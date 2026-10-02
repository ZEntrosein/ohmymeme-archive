"""解析拖拽松手时的 QQ 发送目标，优先使用 NapCat 桥接插件并回退 UIA。"""

import glob
import os
import platform
import queue
import re
import threading
from collections import defaultdict

from .onebot import OneBotError, call_action, call_webui_plugin_api

CURRENT_PEER_PLUGIN_ID = "napcat-plugin-ohmymeme-current-peer"


def _normalize_name(value):
    return " ".join(str(value or "").split()).casefold()


def _contact_index(groups, friends):
    index = defaultdict(set)
    labels = {}
    for item in groups or []:
        if not isinstance(item, dict) or item.get("group_id") in (None, ""):
            continue
        name = str(item.get("group_name") or "").strip()
        key = _normalize_name(name)
        if key:
            target = ("group", str(item["group_id"]))
            index[key].add(target)
            labels[target] = name
    for item in friends or []:
        if not isinstance(item, dict) or item.get("user_id") in (None, ""):
            continue
        target = ("private", str(item["user_id"]))
        label = str(item.get("remark") or item.get("nickname") or "").strip()
        labels[target] = label
        for value in (item.get("remark"), item.get("nickname")):
            key = _normalize_name(value)
            if key:
                index[key].add(target)
    return index, labels


def _choose_target(element_candidates, groups, friends):
    """从聊天区顶部候选名称中选择唯一联系人；纯函数，便于测试。"""
    index, labels = _contact_index(groups, friends)
    scored = defaultdict(int)
    seen_names = []
    for candidate in element_candidates:
        if not isinstance(candidate, dict):
            continue
        key = _normalize_name(candidate.get("name"))
        if not key:
            continue
        seen_names.append(str(candidate.get("name") or "").strip())
        targets = index.get(key, set())
        match_bonus = 100
        if not targets:
            # QQNT 的无障碍名称可能把在线状态、群人数或原昵称拼在备注后面。
            # 仅当所有“包含关系”的别名最终指向同一个会话时才接受，避免
            # 短备注、同名好友或群名造成误发。
            contained = set()
            for alias, alias_targets in index.items():
                if len(alias) < 2:
                    continue
                if alias in key or key in alias:
                    contained.update(alias_targets)
            targets = contained
            match_bonus = 50
        if len(targets) != 1:
            continue
        target = next(iter(targets))
        scored[target] = max(
            scored[target],
            int(candidate.get("score") or 0) + match_bonus,
        )
    if not scored:
        readable = []
        for name in seen_names:
            compact = " ".join(name.split())
            if compact and compact not in readable:
                readable.append(compact[:40])
            if len(readable) >= 6:
                break
        detail = (
            f"；读取到：{' / '.join(readable)}" if readable else "；未读取到标题文本"
        )
        return None, f"无法从 QQ 当前聊天标题识别好友或群{detail}"
    best_score = max(scored.values())
    best = [target for target, score in scored.items() if score == best_score]
    if len(best) != 1:
        return None, "QQ 当前聊天标题匹配到多个会话，已取消发送"
    target = best[0]
    return {
        "message_type": target[0],
        "peer_id": target[1],
        "label": labels.get(target) or "当前 QQ 会话",
    }, ""


def _plugin_target(webui_url, webui_token, error_out=None, window_handle=None):
    """优先读取 NapCat 伴随插件提供的真实 OneBot 目标。"""
    try:
        response = call_webui_plugin_api(
            webui_url,
            webui_token,
            CURRENT_PEER_PLUGIN_ID,
            "current-peer",
            timeout=3,
            params={"window_handle": window_handle},
        )
    except OneBotError as exc:
        # 插件尚未安装、尚未捕获会话或旧版 NapCat 不支持路由时，
        # 交给旧的 UIA 标题降级路径处理。
        if isinstance(error_out, dict):
            error_out["error"] = str(exc)
        return None
    if not isinstance(response, dict) or response.get("available") is not True:
        if isinstance(error_out, dict):
            error_out["error"] = str(
                response.get("error")
                if isinstance(response, dict) and response.get("error")
                else "插件未返回可用会话"
            )
        return None
    if response.get("confidence") == "renderer_title":
        candidates = []
        for item in response.get("title_candidates") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or len(name) > 200:
                continue
            candidates.append({"name": name, "score": int(item.get("score") or 0)})
        if candidates:
            return {
                "element_candidates": candidates,
                "source": "napcat_renderer",
            }
        if isinstance(error_out, dict):
            error_out["error"] = "插件未从拖拽落点的 QQ 窗口读取到标题"
        return None
    if response.get("confidence") != "active_tab":
        if isinstance(error_out, dict):
            error_out["error"] = "插件返回的目标未经当前会话切换事件确认"
        return None
    target = response.get("target")
    if not isinstance(target, dict):
        if isinstance(error_out, dict):
            error_out["error"] = "插件返回的会话目标格式无效"
        return None
    message_type = str(target.get("message_type") or "")
    peer_id = str(target.get("peer_id") or "")
    if message_type not in ("group", "private") or not peer_id.isdigit():
        if isinstance(error_out, dict):
            error_out["error"] = "插件返回的会话 ID 无效"
        return None
    return {
        "message_type": message_type,
        "peer_id": peer_id,
        "label": str(target.get("label") or "当前 QQ 会话"),
        "source": "napcat_plugin",
    }


def _load_uia():
    if platform.system() != "Windows":
        raise RuntimeError("UI Automation 仅支持 Windows")
    import clr

    windows = os.environ.get("WINDIR", r"C:\Windows")
    roots = [
        os.path.join(windows, "assembly", "GAC_MSIL", "UIAutomationTypes"),
        os.path.join(windows, "assembly", "GAC_MSIL", "UIAutomationClient"),
        os.path.join(windows, "assembly", "GAC_MSIL", "WindowsBase"),
    ]
    assemblies = {}
    for root in roots:
        name = os.path.basename(root)
        paths = glob.glob(os.path.join(root, "*", f"{name}.dll"))
        if not paths:
            raise RuntimeError(f"缺少 {name}")
        assemblies[name] = paths[0]
    clr.AddReference(assemblies["UIAutomationTypes"])
    clr.AddReference(assemblies["UIAutomationClient"])
    clr.AddReference(assemblies["WindowsBase"])
    from System import IntPtr
    from System.Windows import Point
    from System.Windows.Automation import (
        AutomationElement,
        TreeWalker,
    )

    return (
        IntPtr,
        Point,
        AutomationElement,
        TreeWalker,
    )


def _qq_header_candidates(window_handle, progress=None):
    progress = progress if isinstance(progress, dict) else {}
    progress.update({"phase": "加载 UI Automation", "completed": 0, "total": 15})
    (
        IntPtr,
        Point,
        AutomationElement,
        TreeWalker,
    ) = _load_uia()
    progress["phase"] = "读取 QQ 窗口"
    root = AutomationElement.FromHandle(IntPtr(int(window_handle)))
    current = root.Current
    bounds = current.BoundingRectangle
    if bounds.Width <= 0 or bounds.Height <= 0:
        return []
    chat_left = bounds.X + bounds.Width * 0.25
    header_bottom = bounds.Y + min(180, bounds.Height * 0.28)

    # FindAll(Descendants) 会让 QQNT 遍历整个消息区的 Chromium 可访问性树，
    # 在较长会话中可能几十秒不返回。即便 ElementFromPoint 不遍历消息树，
    # 跨进程调用上百次也可能累计超过超时，因此只探测 QQNT 标题按钮通常
    # 覆盖的 15 个点；每个点最多读取自身和一个父控件。
    x_offsets = (28, 88, 148, 218, 298)
    y_offsets = (48, 68, 88)
    candidates = {}
    progress["phase"] = "命中标题区域"

    def collect(element, base_score):
        for depth in range(2):
            if element is None:
                return
            try:
                item = element.Current
                name = str(item.Name or "").strip()
                rect = item.BoundingRectangle
                control = str(item.ControlType.ProgrammaticName)
                if (
                    name
                    and rect.Width > 0
                    and rect.Height > 0
                    and rect.X >= chat_left
                    and bounds.Y <= rect.Y <= header_bottom
                    and control in ("ControlType.Button", "ControlType.Text")
                    and not re.fullmatch(r"[\s\W_]+", name)
                ):
                    score = base_score - depth
                    if control == "ControlType.Button":
                        score += 3
                    if rect.Width <= 420:
                        score += 2
                    previous = candidates.get(name)
                    if previous is None or score > previous:
                        candidates[name] = score
                element = TreeWalker.ControlViewWalker.GetParent(element)
            except Exception:
                return

    for y_offset in y_offsets:
        y = bounds.Y + y_offset
        if y > header_bottom:
            break
        for x_offset in x_offsets:
            x = chat_left + x_offset
            if x >= bounds.X + bounds.Width - 20:
                continue
            try:
                collect(AutomationElement.FromPoint(Point(x, y)), 20)
            except Exception:
                pass
            finally:
                progress["completed"] += 1
    return [{"name": name, "score": score} for name, score in candidates.items()]


def _qq_header_candidates_with_timeout(window_handle, timeout=8):
    """防止 QQNT 的 UI Automation 提供程序偶发无限阻塞拖拽线程。"""
    result = queue.Queue(maxsize=1)
    progress = {"phase": "准备读取", "completed": 0, "total": 15}

    def read_candidates():
        try:
            result.put((True, _qq_header_candidates(window_handle, progress)))
        except Exception as exc:
            result.put((False, exc))

    threading.Thread(
        target=read_candidates,
        name="ohmymeme-qq-uia",
        daemon=True,
    ).start()
    try:
        ok, value = result.get(timeout=timeout)
    except queue.Empty as exc:
        raise RuntimeError(
            "读取 QQ 标题超时"
            f"（{progress.get('phase')}，"
            f"{progress.get('completed', 0)}/{progress.get('total', 15)}）"
        ) from exc
    if not ok:
        raise value
    return value


def resolve_qq_target(
    http_url,
    token,
    window_handle,
    process_name,
    drop_x=None,
    drop_y=None,
    plugin_http_url="http://127.0.0.1:6099",
    plugin_token="",
):
    """验证 QQ 窗口，优先读取 WebUI 插件目标，再回退标题匹配。"""
    if str(process_name or "").casefold() != "qq":
        raise OneBotError("请拖到 QQ 聊天窗口")
    plugin_error = {}
    plugin_target = _plugin_target(
        plugin_http_url,
        plugin_token,
        plugin_error,
        window_handle=window_handle,
    )
    if plugin_target:
        if plugin_target.get("peer_id"):
            return plugin_target
        element_candidates = plugin_target.get("element_candidates") or []
        groups = call_action(http_url, token, "get_group_list", timeout=15)
        friends = call_action(http_url, token, "get_friend_list", timeout=15)
        target, error = _choose_target(element_candidates, groups, friends)
        if target:
            target["source"] = "napcat_renderer"
            return target
        plugin_error["error"] = error
    try:
        candidates = _qq_header_candidates_with_timeout(window_handle)
    except Exception as exc:
        detail = str(exc)
        if plugin_error.get("error"):
            detail += f"；NapCat 当前会话插件：{plugin_error['error']}"
        raise OneBotError(f"无法读取 QQ 当前聊天标题：{detail}") from exc
    if not plugin_target:
        groups = call_action(http_url, token, "get_group_list", timeout=15)
        friends = call_action(http_url, token, "get_friend_list", timeout=15)
    target, error = _choose_target(candidates, groups, friends)
    if not target:
        if plugin_error.get("error"):
            error = f"{error}；NapCat 当前会话插件：{plugin_error['error']}"
        raise OneBotError(error)
    return target
