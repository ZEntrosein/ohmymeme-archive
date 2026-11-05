"""PyWebView 现代化 UI 窗口管理器 + JS API"""

import io
import json
import logging
import math
import os
import platform
import re
import shutil
import socket
import tempfile
import threading
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

# WSL 环境强制软件渲染（必须在导入 webview/GUI 之前设置）
if platform.system() == "Linux":
    try:
        with open("/proc/version", "r", encoding="utf-8") as _f:
            if "microsoft" in _f.read().lower():
                os.environ["MESA_LOADER_DRIVER_OVERRIDE"] = "llvmpipe"
                os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
                os.environ["VK_ICD_FILENAMES"] = ""
    except Exception:
        pass

# 仅核显禁用 WebView2 GPU 合成，独显保持硬件加速避免 CPU 滥用
if platform.system() == "Windows":
    from .platform_util import is_integrated_gpu

    if is_integrated_gpu():
        logging.getLogger(__name__).debug(
            "integrated GPU detected, disable gpu compositing"
        )
        _wv2_args = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        if "--disable-gpu-compositing" not in _wv2_args:
            os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
                f"{_wv2_args} --disable-gpu-compositing".strip()
            )

try:
    from PIL import Image as PILImage

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import webview

    HAS_WEBVIEW = True
except ImportError:
    HAS_WEBVIEW = False

try:
    import bottle

    HAS_BOTTLE = True
except ImportError:
    HAS_BOTTLE = False

from . import (
    __app_name__,
    adb_util,
    backup,
    notion_import,
    onebot,
    qqnt_extract,
    tg_stickers,
    updater,
)
from . import sync as sync_module
from .bulk_read import clamp_batch_size, clamp_pause_seconds, guarded_read
from .category_storage import category_dir, remove_empty_parents, validate_category_name
from .clipboard_util import (
    _is_animated,
    convert_avoid_webp,
    convert_image_mode_1,
    convert_image_mode_2,
    convert_image_mode_3,
    convert_image_mode_4,
    copy_image_to_clipboard,
    copy_text_with_history,
)
from .config import (
    _IMPORT_MAX_BYTES,
    _IMPORT_MAX_PX,
    CHAT_RECORD_MEDIA_DIRNAME,
    get_config,
    get_import_max_px,
    guide_ok,
    normalize_import_max_px,
    normalize_library_genres,
    resolve_path,
    set_guide_ok,
)
from .database import _UNSET, MemeDB, _name_sort_key, get_db, pinyin_matches
from .manifest import add_memes as add_memes_to_manifest
from .manifest import build as build_manifest
from .manifest import index_order, index_path
from .manifest import remove_memes as remove_memes_from_manifest
from .manifest import request_background_rebuild as request_manifest_rebuild
from .manifest import update_meme_name as update_manifest_meme_name
from .manifest import update_meme_structure as update_manifest_meme_structure
from .media_derivative import DAILY_COPY_VERSION, create_daily_copy
from .plugin_manager import get_plugin_manager, route_content_type

logger = logging.getLogger(__name__)

# 内存日志缓冲：固定收集 DEBUG 级日志，供设置页"导出日志"
_LOG_BUFFER = []
_LOG_LOCK = threading.Lock()
_LOG_MAX = 5000


def _normalize_bulk_read_settings(settings: dict) -> dict:
    """复制并规范虚拟盘限流设置，避免保存非法/过大的等待参数。"""
    normalized = dict(settings)
    if "bulk_read_batch_size" in normalized:
        normalized["bulk_read_batch_size"] = clamp_batch_size(
            normalized["bulk_read_batch_size"]
        )
    if "bulk_read_pause_seconds" in normalized:
        normalized["bulk_read_pause_seconds"] = clamp_pause_seconds(
            normalized["bulk_read_pause_seconds"]
        )
    return normalized


# 分页：主窗口单页展示的表情包数量（与前端 index.js MEME_PAGE 保持一致）
MEME_PAGE = 200

# 窗口坐标合法范围：最小化时系统返回 -32000 之类哨兵值，超出即视为无效坐标
_WINDOW_COORD_MIN = -10000
_WINDOW_COORD_MAX = 20000

# 贡献者 SVG 缓存：TTL 1 小时，避免每次打开设置页都请求外网；刷新失败退避
# 重试，避免上游不可用时每个请求都反复触发 10s 慢抓取
_CONTRIBUTORS_TTL = 3600
_CONTRIBUTORS_RETRY_INTERVAL = 60
_CONTRIBUTORS_LOCK = threading.Lock()
_CONTRIBUTORS_CACHE = {"svg": None, "at": 0.0, "next_retry": 0.0}


class _LogBufferHandler(logging.Handler):
    """把 DEBUG 级日志缓存在内存中（限量 5000 条）"""

    def emit(self, record):
        try:
            line = self.format(record)
        except Exception:
            line = record.getMessage()
        with _LOG_LOCK:
            _LOG_BUFFER.append(line)
            if len(_LOG_BUFFER) > _LOG_MAX:
                del _LOG_BUFFER[: len(_LOG_BUFFER) - _LOG_MAX]


def install_log_buffer():
    """挂载内存日志缓冲，根 logger 固定 DEBUG（控制台级别由 main 控制）"""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    handler = _LogBufferHandler()
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(handler)
    return handler


install_log_buffer()

HTML_DIR = Path(__file__).resolve().parent / "webui"
RESOURCES_DIR = Path(__file__).resolve().parent / "resources"


def _contributors_svg():
    """剥离缓存 SVG 的白色背景矩形，适配深色主题后返回"""
    white_rect = '<rect width="100%" height="100%" fill="#ffffff"/>'
    svg = _CONTRIBUTORS_CACHE["svg"].replace(white_rect, "")
    bottle.response.content_type = "image/svg+xml; charset=utf-8"
    return svg


# 启动动画视频边缘主色（OhMyMeme.mp4 边框纯黑，写死避免运行时 ffmpeg 抽帧采样）
_STARTUP_BG_COLOR = "#000000"


# 静态资源扩展名 → 强制 Content-Type：本机 mimetypes/注册表 .js 映射可能为
# text/plain，叠加 nosniff 会被 Chromium 拒执行脚本
_STATIC_MIME_TYPES = {
    ".js": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

# 主窗口启动动画默认视频源（插件可经 startup 覆盖）
_DEFAULT_STARTUP_VIDEO = "/resources/OhMyMeme.mp4"


# 在脚本加载前设置主题与底色，避免独立窗口首次绘制闪烁。
def _theme_html(html, theme):
    theme = "light" if theme == "light" else "dark"
    background = "#f8fafc" if theme == "light" else "#0d0d0f"
    return html.replace(
        "<html ",
        f'<html data-theme="{theme}" '
        f'style="background:{background};color-scheme:{theme}" ',
        1,
    )


def _inject_before(html: str, marker: str, inject: str) -> str:
    """在最后一个 marker 前插入 inject（无 marker 或无内容时原样返回）"""
    if not inject or marker not in html:
        return html
    idx = html.rfind(marker)
    return html[:idx] + inject + "\n" + html[idx:]


def _inject_after(html: str, marker: str, inject: str) -> str:
    """在第一个 marker 后插入 inject"""
    if not inject or marker not in html:
        return html
    idx = html.index(marker)
    return html[: idx + len(marker)] + "\n" + inject + html[idx + len(marker) :]


def _split_asset_tags(tags: str):
    """asset_tags 输出拆成 (link 标签块, script 标签块)"""
    links = [ln for ln in tags.splitlines() if ln.startswith("<link")]
    scripts = [ln for ln in tags.splitlines() if ln.startswith("<script")]
    return "\n".join(links), "\n".join(scripts)


def _make_plugin_route_cb(pid: str, rule: str, fn):
    """插件自定义路由回调：返回 str 或 dict（JSON），Content-Type 按扩展名推断"""
    import json as _json

    def cb():
        try:
            ok, val = get_plugin_manager().call_route(
                pid, fn, dict(bottle.request.params)
            )
        except Exception as e:
            logger.warning(f"plugin route error: {e}")
            bottle.response.status = 500
            return ""
        if not ok:
            bottle.response.status = 404
            return ""
        if isinstance(val, dict):
            bottle.response.content_type = "application/json"
            return _json.dumps(val, ensure_ascii=False)
        ctype = route_content_type(rule)
        if ctype:
            bottle.response.content_type = ctype
        return val if isinstance(val, str) else str(val)

    return cb


# ─── 工具函数  ───


_TEXT_CLIPBOARD_SCRIPT = r"""(() => {
  if (window.__ohmymemeTextCopyInstalled) return;
  window.__ohmymemeTextCopyInstalled = true;
  let pending = Promise.resolve();
  let fallback = false;
  const isTextField = target => target instanceof HTMLInputElement
    || target instanceof HTMLTextAreaElement;
  const privateFields = '[type="password"], '
    + '[autocomplete~="current-password"], [autocomplete~="new-password"]';
  document.addEventListener('copy', event => {
    if (fallback || event.defaultPrevented) return;
    const target = isTextField(event.target)
      ? event.target : event.target === document ? document.activeElement : null;
    if (!isTextField(target) || target.disabled
        || target.matches(privateFields)) return;
    const start = target.selectionStart;
    const end = target.selectionEnd;
    if (typeof start !== 'number' || typeof end !== 'number'
        || start === end) return;
    const bridge = window.pywebview?.api;
    if (typeof bridge?.copy_editor_text !== 'function') return;
    const value = target.value;
    const text = value.slice(start, end);
    if ((text.length + 1) * 2 > 4 * 1024 * 1024
        || text.includes('\u0000')) return;
    event.preventDefault();
    pending = pending.then(() => new Promise(resolve => setTimeout(resolve, 0)));
    pending = pending.then(async () => {
      let copied = false;
      try { copied = await bridge.copy_editor_text(text) === true; } catch (_) {}
      if (!copied && document.activeElement === target && target.value === value
          && target.selectionStart === start && target.selectionEnd === end) {
        fallback = true;
        try { document.execCommand('copy'); } catch (_) {}
        finally { fallback = false; }
      }
    });
  });
})()"""


def _install_text_clipboard_bridge(window):
    """为 Windows 编辑框安装本地剪贴板历史兼容处理。"""
    try:
        window.evaluate_js(_TEXT_CLIPBOARD_SCRIPT)
    except Exception:
        logger.warning("文本剪贴板兼容处理未能安装")


def _copy_editor_text(text, window):
    """使用当前窗口作为原生剪贴板所有者，不改变浏览器隐私模式。"""
    if platform.system() != "Windows" or window is None:
        return False
    try:
        handle = window.native.Handle
        hwnd = handle.ToInt64() if hasattr(handle, "ToInt64") else int(handle)
        return copy_text_with_history(text, hwnd)
    except Exception:
        return False


def _strip_url_modifiers(url: str) -> str:
    """去掉图片 URL 中的 @ 修饰参数，返回原图 URL"""
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    if "@" in parsed.path:
        clean_path = parsed.path[: parsed.path.index("@")]
    else:
        clean_path = parsed.path
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            clean_path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def _safe_serve_filename(name: str) -> bool:
    """校验用于文件服务/DB 查询的文件名，拒绝路径穿越与绝对路径"""
    return (
        bool(name)
        and name not in (".", "..")
        and not name.startswith((".", "/", "\\", "~", ".."))
        and "/" not in name
        and "\\" not in name
    )


def _valid_editable_filename(name: str) -> bool:
    """校验用户输入的单个文件名，兼容 Windows 并拒绝路径与保留名。"""
    if not _safe_serve_filename(name) or len(name) > 255:
        return False
    if name.endswith((" ", ".")) or any(ord(char) < 32 for char in name):
        return False
    if any(char in '<>:"|?*' for char in name):
        return False
    stem = Path(name).stem.rstrip(" .").upper()
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update(f"COM{i}" for i in range(1, 10))
    reserved.update(f"LPT{i}" for i in range(1, 10))
    return bool(stem) and stem not in reserved


def _split_hierarchy_path(value, label: str) -> list:
    """把斜杠路径拆成已清理的层级名称。"""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{label}名称不能为空")
    parts = [part.strip() for part in raw.split("/")]
    if any(not part for part in parts):
        raise ValueError(f"{label}路径中不能有空层级")
    if any(part in (".", "..") for part in parts):
        raise ValueError(f"{label}路径包含无效层级")
    if any(len(part) > 100 for part in parts):
        raise ValueError(f"{label}每级名称最多 100 个字符")
    return parts


def _normalize_hierarchy_delete_mode(value) -> str:
    mode = str(value or "ask").strip().lower()
    return mode if mode in ("ask", "promote", "cascade") else "ask"


def _normalize_large_meme_import_mode(value) -> str:
    """规范化超过 20 MiB 的普通媒体导入策略。"""
    mode = str(value or "ask").strip().lower()
    return mode if mode in ("ask", "keep_original", "compressed_only") else "ask"


# 动图缩略图帧数上限：超限抽帧并按步长放大帧延时，控制动画 WebP 体积
_THUMB_MAX_FRAMES = 100


def _animated_thumb_frames(img, size):
    """动图逐帧缩放到 size 内，抽帧限幅后返回 (frames, durations) 供动画 WebP 编码"""
    n = img.n_frames
    step = max(1, math.ceil(n / _THUMB_MAX_FRAMES))
    frames, durations = [], []
    has_alpha = False
    i = 0
    while i < n:
        img.seek(i)
        frame = img.copy()
        frame.thumbnail((size, size), PILImage.LANCZOS)
        alpha = frame.mode in ("RGBA", "LA", "PA") or (
            frame.mode == "P" and "transparency" in frame.info
        )
        if frame.mode not in ("RGB", "RGBA"):
            frame = frame.convert("RGBA" if alpha else "RGB")
        has_alpha = has_alpha or alpha or "A" in frame.getbands()
        durations.append(max(20, int(img.info.get("duration") or 100)) * step)
        frames.append(frame)
        i += step
    if has_alpha:
        frames = [f if f.mode == "RGBA" else f.convert("RGBA") for f in frames]
    else:
        frames = [f if f.mode == "RGB" else f.convert("RGB") for f in frames]
    return frames, durations


def _migrate_thumbnails(thumb_dir) -> int:
    """升级迁移：清除旧命名缩略图（{id}_{size}.png 等），新命名按需懒重建，幂等"""
    removed = 0
    try:
        if not thumb_dir.is_dir():
            return 0
        for f in thumb_dir.iterdir():
            if f.is_file() and not re.fullmatch(r"[0-9a-f]{64}\.webp", f.name):
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
    except OSError:
        return removed
    if removed:
        logger.info(f"缩略图迁移: 清除旧命名文件 {removed} 个")
    return removed


# 云端直接使用：内存清单状态（刷新线程与查询线程共享；missing 按本地集合懒重算）
_cloud_lock = threading.Lock()
_cloud_state = {
    "manifest": None,
    "missing": [],
    "local": frozenset(),
    "loaded": False,
    "refreshing": False,
}
_cloud_inflight = set()
_cloud_inflight_lock = threading.Lock()

# 主窗口排序模式：manifest=清单序（默认）/manifest_desc 反向/name_asc|name_desc 显示名
_SORT_MODES = ("manifest", "manifest_desc", "name_asc", "name_desc")

# 本地清单顺序缓存（按文件 mtime+size 失效），避免每次排序查询都重解析 JSON
_local_manifest_cache = {"key": None, "order": []}
_local_manifest_lock = threading.Lock()


def _local_manifest_order():
    """本地清单 memes 文件名顺序（不可用时空列表）"""
    try:
        st = index_path().stat()
    except OSError:
        return []
    key = (st.st_mtime_ns, st.st_size)
    with _local_manifest_lock:
        if _local_manifest_cache["key"] == key:
            return _local_manifest_cache["order"]
    order = index_order()
    with _local_manifest_lock:
        _local_manifest_cache["key"] = key
        _local_manifest_cache["order"] = order
    return order


def _cloud_ensure_loaded():
    # 冷启动：先读 cloud-index.json 缓存出图，网络刷新由后台线程负责
    with _cloud_lock:
        if _cloud_state["loaded"]:
            return
    data = sync_module.load_cloud_manifest()
    if isinstance(data, dict):
        sync_module._apply_remote_categories(data)
        sync_module._apply_remote_collections(data)
    with _cloud_lock:
        if not _cloud_state["loaded"]:
            _cloud_state["manifest"] = data if isinstance(data, dict) else None
            local = frozenset(get_db().get_all_filenames())
            _cloud_state["missing"] = sync_module.cloud_missing(data, local)
            _cloud_state["local"] = local
            _cloud_state["loaded"] = True


def _cloud_reset():
    # 清空云态并删除本地清单缓存（开关切换/云后端变更时调用）
    with _cloud_lock:
        _cloud_state.update(
            manifest=None,
            missing=[],
            local=frozenset(),
            loaded=False,
            refreshing=False,
        )
    try:
        path = get_config().data_dir / sync_module.CLOUD_INDEX_FILENAME
        if path.exists():
            path.unlink()
    except OSError:
        pass
    _sync_status_reset()


# 顶栏同步状态：内存缓存远端清单 + 后台刷新，供主窗口低开销轮询
_sync_status_lock = threading.Lock()
_sync_status_state = {
    "manifest": None,
    "cache_loaded": False,
    "checked": False,
    "refreshing": False,
    "error": "",
    "updated_at": 0.0,
}
_sync_status_gen = 0


def _sync_status_reset():
    # 云配置变更：作废在途刷新并清空清单缓存
    global _sync_status_gen
    with _sync_status_lock:
        _sync_status_gen += 1
        _sync_status_state.update(
            manifest=None,
            cache_loaded=False,
            checked=False,
            refreshing=False,
            error="",
            updated_at=0.0,
        )


def _sync_status_invalidate():
    # 同步完成后远端清单已变化：作废在途刷新并标记待重拉（保留旧清单供离线时展示）
    global _sync_status_gen
    with _sync_status_lock:
        _sync_status_gen += 1
        _sync_status_state.update(
            checked=False,
            refreshing=False,
            error="",
        )


def _sync_status_set_manifest(manifest):
    # 复用已有拉取结果（启动自动同步/云清单刷新），避免重复请求远端
    with _sync_status_lock:
        _sync_status_state.update(
            manifest=manifest if isinstance(manifest, dict) else None,
            cache_loaded=True,
            checked=True,
            error="",
            updated_at=time.time(),
        )


def _sync_status_ensure_cache_locked():
    # 首次查询先读 cloud-index.json 缓存出值，网络刷新由后台线程完成
    if _sync_status_state["cache_loaded"]:
        return
    _sync_status_state["cache_loaded"] = True
    data = sync_module.load_cloud_manifest()
    if isinstance(data, dict):
        _sync_status_state["manifest"] = data


def _start_sync_status_refresh(force=False) -> bool:
    # 后台拉取远端清单（refreshing 去重；已检查过则仅 force 重拉）
    global _sync_status_gen
    with _sync_status_lock:
        if _sync_status_state["refreshing"]:
            return False
        if _sync_status_state["checked"] and not force:
            return False
        _sync_status_state["refreshing"] = True
        _sync_status_state["error"] = ""
        gen = _sync_status_gen
    threading.Thread(target=_sync_status_worker, args=(gen,), daemon=True).start()
    return True


def _sync_status_worker(gen):
    # 拉取远端清单并写入状态；代次不匹配（配置重置/同步完成）时丢弃结果
    try:
        data = sync_module.download_index()
        with _sync_status_lock:
            if gen != _sync_status_gen:
                return
            _sync_status_state.update(
                manifest=data if isinstance(data, dict) else None,
                cache_loaded=True,
                checked=True,
                refreshing=False,
                error="",
                updated_at=time.time(),
            )
    except Exception as e:
        logger.info("sync status refresh failed: %s", e)
        with _sync_status_lock:
            if gen != _sync_status_gen:
                return
            _sync_status_state.update(
                checked=True,
                refreshing=False,
                error=str(e),
                updated_at=time.time(),
            )


def _sync_status_snapshot(force=False) -> dict:
    # 组装顶栏所需状态：配置/清单差集/后台传输进度
    cfg = get_config()
    if not str(cfg.get("sync_type", "") or "").strip():
        return {"configured": False}
    with _sync_status_lock:
        _sync_status_ensure_cache_locked()
        need_refresh = force or not _sync_status_state["checked"]
    if need_refresh:
        _start_sync_status_refresh(force=force)
    with _sync_status_lock:
        manifest = _sync_status_state["manifest"]
        checked = _sync_status_state["checked"]
        refreshing = _sync_status_state["refreshing"]
        error = _sync_status_state["error"]
        checked_at = _sync_status_state["updated_at"]
    local = set(get_db().get_all_filenames() or [])
    remote_names = set()
    remote_ready = set()
    if isinstance(manifest, dict):
        for entry in manifest.get("memes") or []:
            if not isinstance(entry, dict):
                continue
            fname = entry.get("filename")
            if not sync_module._safe_remote_fname(fname):
                continue
            remote_names.add(fname)
            if sync_module._safe_sha(entry.get("sha256")):
                remote_ready.add(fname)
    local_missing = len(remote_ready - local) if manifest is not None else 0
    cloud_missing = len(local - remote_names) if manifest is not None else 0
    if manifest is not None:
        manifest_state = "ok"
    elif refreshing or not checked:
        manifest_state = "loading"
    elif error:
        manifest_state = "error"
    else:
        manifest_state = "missing"
    progress = sync_module.get_sync_progress()
    status = str(progress.get("status", "idle") or "idle")
    active = status in ("uploading", "downloading", "deleting")

    def _int(value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    files_done = _int(progress.get("files_done"))
    files_total = _int(progress.get("files_total"))
    return {
        "configured": True,
        "manifest_state": manifest_state,
        "refreshing": refreshing,
        "checked": checked,
        "error": error,
        "checked_at": checked_at,
        "local_count": len(local),
        "remote_count": len(remote_names),
        "local_missing": local_missing,
        "cloud_missing": cloud_missing,
        "synced": manifest is not None and local_missing == 0 and cloud_missing == 0,
        "transfer": {
            "active": active,
            "status": status,
            "direction": progress.get("direction", ""),
            "files_done": files_done,
            "files_total": files_total,
            "remaining": max(files_total - files_done, 0) if files_total else 0,
            "bytes_done": _int(progress.get("bytes_done")),
            "bytes_total": _int(progress.get("bytes_total")),
            "progress": _int(progress.get("progress")),
            "speed": _int(progress.get("speed")),
            "current_file": progress.get("current_file", "") or "",
        },
    }


def _cloud_view():
    # 可见云条目：本地文件名集合变化时重算差集，否则复用缓存
    with _cloud_lock:
        manifest = _cloud_state["manifest"]
        cached = _cloud_state["missing"]
        cached_local = _cloud_state["local"]
    if not manifest:
        return []
    local = frozenset(get_db().get_all_filenames())
    if local == cached_local:
        return cached
    missing = sync_module.cloud_missing(manifest, local)
    with _cloud_lock:
        if _cloud_state["manifest"] is manifest:
            _cloud_state["missing"] = missing
            _cloud_state["local"] = local
    return missing


def _start_cloud_refresh(fetched=None) -> bool:
    # 启动云端清单刷新线程（refreshing 中去重，未开云端直接返回）
    if not get_config().get("cloud_direct", False):
        return False
    with _cloud_lock:
        if _cloud_state["refreshing"]:
            return False
        _cloud_state["refreshing"] = True
    threading.Thread(target=_cloud_refresh_worker, args=(fetched,), daemon=True).start()
    return True


_thumb_autopush_lock = threading.Lock()
_thumb_autopush_running = False


def _start_thumb_autopush(worker) -> bool:
    # 启动缩略图静默补传线程（单飞去重，异常仅告警不外抛）
    global _thumb_autopush_running
    with _thumb_autopush_lock:
        if _thumb_autopush_running:
            return False
        _thumb_autopush_running = True

    def _run():
        global _thumb_autopush_running
        try:
            worker()
        except Exception as e:
            logger.warning("cloud thumb autopush failed: %s", e)
        finally:
            with _thumb_autopush_lock:
                _thumb_autopush_running = False

    threading.Thread(target=_run, daemon=True).start()
    return True


def _cloud_refresh_worker(fetched=None):
    # 后台拉云端清单 → 写缓存 → 差集 → 预取缩略图 → 通知前端
    try:
        data = fetched
        if data is None:
            data = sync_module.download_index()
        if isinstance(data, dict):
            sync_module.save_cloud_manifest(data)
            sync_module._apply_remote_categories(data)
            sync_module._apply_remote_collections(data)
            local = frozenset(get_db().get_all_filenames())
            missing = sync_module.cloud_missing(data, local)
            with _cloud_lock:
                _cloud_state.update(
                    manifest=data,
                    missing=missing,
                    local=local,
                    loaded=True,
                )
            _sync_status_set_manifest(data)
            cfg = get_config()
            if cfg.get("cloud_direct", False):
                try:
                    ready = sync_module.prefetch_thumbs(missing, cfg.thumbnail_dir)
                    logger.info(f"cloud prefetch: {ready}/{len(missing)}")
                except Exception as e:
                    logger.warning(f"cloud prefetch failed: {e}")
            _notify_cloud_ready()
        else:
            # 拉取失败：保留既有缓存，下次启动或保存设置时重试
            logger.info("cloud refresh: no manifest fetched")
    except Exception as e:
        logger.warning(f"cloud refresh failed: {e}")
    finally:
        with _cloud_lock:
            _cloud_state["refreshing"] = False


def _notify_cloud_ready():
    # 通知主窗口云端数据就绪（前端刷新搜索/计数/分组/标签）
    try:
        if HAS_WEBVIEW and webview.windows:
            webview.windows[0].evaluate_js("window.onCloudReady&&window.onCloudReady()")
    except Exception:
        pass


def _flatten_manifest_collection(node) -> list:
    # 清单分组节点子树展平：本组 filenames 在前，递归子组接后
    out = []
    for fname in node.get("filenames") or []:
        if isinstance(fname, str):
            out.append(fname)
    for child in node.get("children") or []:
        if isinstance(child, dict):
            out.extend(_flatten_manifest_collection(child))
    return out


def _find_manifest_collection(node_list, target, prefix=""):
    # 按全路径（父/子）在清单分组树中定位节点
    if not isinstance(node_list, list):
        return None
    for node in node_list:
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or "")
        path = f"{prefix}/{name}" if prefix else name
        if path == target:
            return node
        found = _find_manifest_collection(node.get("children") or [], target, path)
        if found is not None:
            return found
    return None


_THUMB_FETCH_LOCK = threading.Lock()
_THUMB_FETCH_QUEUE = []
_THUMB_FETCH_QUEUED = set()
_THUMB_FETCH_WAKE = threading.Event()
_THUMB_FETCH_RUNNING = [False]


def _enqueue_thumb_fetch(sha):
    # 云缩略图缺失时按需入队后台补拉（下载期间单飞防重，整批完成通知前端重载）
    with _THUMB_FETCH_LOCK:
        if sha in _THUMB_FETCH_QUEUED:
            return
        _THUMB_FETCH_QUEUED.add(sha)
        _THUMB_FETCH_QUEUE.append(sha)
        if not _THUMB_FETCH_RUNNING[0]:
            _THUMB_FETCH_RUNNING[0] = True
            threading.Thread(target=_thumb_fetch_worker, daemon=True).start()
    _THUMB_FETCH_WAKE.set()


def _thumb_fetch_worker():
    # 后台逐个预取队列中的云缩略图；整批完成后通知前端（滚动到缺图处即自愈，无需重启）
    fetched = False
    try:
        while True:
            _THUMB_FETCH_WAKE.wait()
            while True:
                with _THUMB_FETCH_LOCK:
                    if not _THUMB_FETCH_QUEUE:
                        _THUMB_FETCH_WAKE.clear()
                        break
                    sha = _THUMB_FETCH_QUEUE.pop(0)
                try:
                    n = sync_module.prefetch_thumbs(
                        [{"sha256": sha}], get_config().thumbnail_dir
                    )
                    if n:
                        fetched = True
                except Exception as e:
                    logger.warning(f"on-demand thumb {sha[:12]} failed: {e}")
                with _THUMB_FETCH_LOCK:
                    _THUMB_FETCH_QUEUED.discard(sha)
            if fetched:
                fetched = False
                _notify_cloud_ready()
    except Exception as e:
        logger.warning(f"thumb fetch worker failed: {e}")
    finally:
        with _THUMB_FETCH_LOCK:
            _THUMB_FETCH_RUNNING[0] = False


def _host_allowed(host: str, port: int) -> bool:
    """仅接受本地回环 Host，阻断 DNS rebinding / 跨站直连"""
    host = (host or "").strip()
    if not host:
        return False
    base = host.split(":")[0] if ":" in host else host
    if base not in ("127.0.0.1", "localhost"):
        return False
    if ":" in host and host.rsplit(":", 1)[-1] != str(port):
        return False
    return True


def _check_connectivity() -> dict:
    """检查互联网连接，返回 {ok, latency}"""
    import socket as _socket

    hosts = [("baidu.com", 80), ("www.baidu.com", 443)]
    for host, port in hosts:
        try:
            t0 = time.time()
            s = _socket.create_connection((host, port), timeout=3)
            s.close()
            latency = int((time.time() - t0) * 1000)
            return {"ok": True, "latency": f"{latency}ms"}
        except Exception:
            continue
    return {"ok": False, "latency": ""}


def _storage_dir_validation(new_dir, old_dir, protected=(), allow_unresolved=False):
    # 校验自定义存储目录，返回 (ok, error)
    if not new_dir or not isinstance(new_dir, str):
        return False, "目录不能为空"
    if not os.path.isabs(new_dir):
        return False, "请选择绝对路径"
    try:
        new = resolve_path(new_dir, allow_unresolved)
        old = resolve_path(old_dir, allow_unresolved)
    except OSError:
        return False, "路径无效"
    if new == old:
        return False, "与当前目录相同"
    if old in new.parents:
        return False, "不能选择当前目录的子目录"
    if new in old.parents:
        return False, "不能选择当前目录的上级目录"
    for p in protected:
        try:
            p = resolve_path(p, allow_unresolved)
        except OSError:
            continue
        if new == p or new in p.parents or p in new.parents:
            return False, "不能选择应用数据/缩略图目录或其上下级目录"
    return True, ""


def _probe_storage_dir(path):
    """验证目录具备缓存所需的写入、原子替换、读取和删除能力。"""
    source = None
    committed = None
    try:
        fd, source = tempfile.mkstemp(prefix=".ohmymeme-probe-", dir=str(path))
        with os.fdopen(fd, "wb") as file:
            file.write(b"ohmymeme-storage-probe")
            file.flush()
        committed = source + ".committed"
        os.replace(source, committed)
        source = None
        with open(committed, "rb") as file:
            if file.read() != b"ohmymeme-storage-probe":
                return False, "目标目录读写校验失败"
        os.unlink(committed)
        committed = None
        return True, ""
    except OSError as exc:
        return False, f"目标目录不支持必要的文件操作: {exc}"
    finally:
        for item in (source, committed):
            if item:
                try:
                    os.unlink(item)
                except OSError:
                    pass


def _find_hotkey_window_position(cursor, work_area, width, height):
    """按固定候选顺序选择完整落入工作区的窗口位置"""
    cursor_x, cursor_y = cursor
    left, top, right, bottom = work_area
    candidates = (
        (cursor_x, cursor_y),
        (right - width, cursor_y),
        (cursor_x, bottom - height),
        (right - width, bottom - height),
    )
    for x, y in candidates:
        if left <= x and top <= y and x + width <= right and y + height <= bottom:
            return x, y
    return None


def _enable_windows_taskbar_minimize(window) -> bool:
    """为无边框 Win32 窗口补回任务栏最小化所需的原生样式。"""
    if platform.system() != "Windows" or window is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        native = getattr(window, "native", None)
        handle = getattr(native, "Handle", None)
        if handle is None:
            return False
        try:
            hwnd = handle.ToInt64()
        except AttributeError:
            hwnd = int(handle)

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_style = user32.GetWindowLongPtrW
        set_style = user32.SetWindowLongPtrW
        get_style.argtypes = (wintypes.HWND, ctypes.c_int)
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        set_style.restype = ctypes.c_ssize_t
        user32.SetWindowPos.argtypes = (
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        )
        user32.SetWindowPos.restype = wintypes.BOOL

        gwl_style = -16
        ws_sysmenu = 0x00080000
        ws_minimizebox = 0x00020000
        style = get_style(hwnd, gwl_style)
        updated = style | ws_sysmenu | ws_minimizebox
        if updated != style:
            ctypes.set_last_error(0)
            previous = set_style(hwnd, gwl_style, updated)
            if previous == 0 and ctypes.get_last_error():
                raise ctypes.WinError(ctypes.get_last_error())
            # 应用新的非客户区样式，但不改变位置、大小、层级或激活状态。
            flags = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020
            if not user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, flags):
                raise ctypes.WinError(ctypes.get_last_error())
        return True
    except Exception:
        logger.exception("enable Windows taskbar minimize failed")
        return False


def _window_drag_update(win_x, win_y, origin, last_move, dx, dy):
    # 窗口拖动一步计算：返回 (origin, last_move, target)。
    # origin 为空时记录拖动起点并返回（本次不动）；8ms 节流期内丢弃；
    # 否则按起点+总偏移移动到绝对目标（自愈，无累积滞后）。
    now = time.monotonic()
    if origin is None:
        return (win_x - dx, win_y - dy), now, None
    if now - last_move < 0.008:
        return origin, last_move, None
    return origin, now, (origin[0] + dx, origin[1] + dy)


class JsApi:
    """暴露给前端的 JS API"""

    def copy_editor_text(self, text):
        """复制普通编辑框文本并允许进入本地剪贴板历史。"""
        return _copy_editor_text(text, self._webui._window)

    def __init__(self, webui):
        self._webui = webui
        self._cfg = get_config()
        self._db = get_db()
        self._drag_origin = None
        self._drag_last_move = 0.0
        self._page_cache_lock = threading.Lock()
        self._page_cache_generation = 0
        self._page_cache_thread = None
        self._random_meme_ids = None

    def _dialog(self, kind, **kw):
        """主窗口上下文打开文件对话框（owner 为主窗口，无需焦点恢复）"""
        win = webview.windows[0] if webview.windows else None
        if win is None:
            return None
        try:
            return win.create_file_dialog(kind, **kw)
        except Exception:
            return None

    def _cloud_base(self) -> list:
        """可见云条目（无关键字/标签过滤）；云端直接使用关闭时返回空"""
        cfg = getattr(self, "_cfg", None)
        if cfg is None or not cfg.get("cloud_direct", False):
            return []
        _cloud_ensure_loaded()
        return _cloud_view()

    def _collection_path(self, collection_id, by_id=None) -> str:
        """分组 id → 全路径名（顶级/子级），供云条目按分组过滤"""
        if by_id is None:
            by_id = {r[0]: r for r in self._db.get_collections()}
        parts = []
        cur = collection_id
        seen = set()
        while cur is not None and cur in by_id and cur not in seen:
            seen.add(cur)
            _, name, parent_id, _ = by_id[cur]
            parts.append(str(name))
            cur = parent_id
        return "/".join(reversed(parts))

    def _cloud_filtered(
        self, keyword, tags, collection_id, category_id=None, genre=None
    ) -> list:
        """按关键字/标签/分组筛选云条目（最近使用 -3 不混云）"""
        if collection_id in (-3, -5) or genre == "chat_record":
            return []
        items = self._cloud_base()
        if not items:
            return []
        kw = (keyword or "").strip().lower()
        tag_set = {
            (category.casefold(), name.casefold())
            for category, name in MemeDB._normalize_tags(tags)
        }
        direct_category_id = (
            -category_id - 4 if category_id is not None and category_id < -4 else None
        )
        target_category_id = direct_category_id or category_id
        category_parts = (
            self._db.get_category_path_names(target_category_id)
            if target_category_id is not None and target_category_id > 0
            else None
        )
        if (
            target_category_id is not None
            and target_category_id > 0
            and not category_parts
        ):
            return []
        hidden_id, _hidden_ids, excluded_ids = self._hidden_collection_state(
            collection_id
        )
        hidden_path = self._collection_path(hidden_id)
        path_prefix = None
        if collection_id is not None and collection_id > 0:
            path_prefix = self._collection_path(collection_id)
            if not path_prefix:
                return []
        out = []
        for it in items:
            is_gif = it["filename"].lower().endswith(".gif")
            if genre in ("image", "gif") and is_gif != (genre == "gif"):
                continue
            if excluded_ids and any(
                cp == hidden_path or cp.startswith(hidden_path + "/")
                for cp in it["collections"]
            ):
                continue
            item_parts = it.get("category_path") or []
            if category_id == -4 and item_parts:
                continue
            if category_parts and item_parts[: len(category_parts)] != category_parts:
                continue
            if direct_category_id is not None and item_parts != category_parts:
                continue
            if collection_id == -2 and not it["favorited"]:
                continue
            if collection_id == -4 and it["collections"]:
                continue
            records = it.get("tag_records")
            item_tags = {
                (category.casefold(), name.casefold())
                for category, name in MemeDB._normalize_tags(
                    records if isinstance(records, list) else it["tags"]
                )
            }
            if tag_set and not tag_set.issubset(item_tags):
                continue
            if kw:
                hay = (
                    it["name"] + " " + it["filename"] + " " + " ".join(it["tags"])
                ).lower()
                if kw not in hay and not pinyin_matches(
                    kw, it["name"], it["filename"], *it["tags"]
                ):
                    continue
            if path_prefix and not any(
                cp == path_prefix or cp.startswith(path_prefix + "/")
                for cp in it["collections"]
            ):
                continue
            out.append(it)
        return out

    def _cloud_order(self, collection_id):
        """当前云清单的展示顺序（文件名列表）；分组取子树序，其余取全局 memes 序"""
        with _cloud_lock:
            manifest = _cloud_state["manifest"]
        if not isinstance(manifest, dict):
            return None
        if collection_id is not None and collection_id > 0:
            path = self._collection_path(collection_id)
            node = (
                _find_manifest_collection(manifest.get("collections") or [], path)
                if path
                else None
            )
            if node is None:
                return []
            return _flatten_manifest_collection(node)
        order = []
        for m in manifest.get("memes") or []:
            if isinstance(m, dict) and isinstance(m.get("filename"), str):
                order.append(m["filename"])
        return order

    def _merge_manifest_order(self, local_rows, cloud_rows, order) -> list:
        """本地与云行按清单序穿插；不在清单的本地行保持原查询序排最前"""
        pos = {fname: i for i, fname in enumerate(order)}
        extras = [r for r in local_rows if r["filename"] not in pos]
        paired = [(pos[r["filename"]], r) for r in local_rows if r["filename"] in pos]
        cloud_out = [self._cloud_row(it) for it in cloud_rows]
        paired.extend(
            (pos[c["filename"]], c) for c in cloud_out if c["filename"] in pos
        )
        paired.sort(key=lambda t: t[0])
        rest = [c for c in cloud_out if c["filename"] not in pos]
        return extras + [r for _, r in paired] + rest

    def _sort_rows(self, rows, order, mode):
        """按 manifest/名称模式排序行（本地+云混合）；order 为空时保持原查询序"""
        if mode in ("manifest", "manifest_desc"):
            if not order:
                return rows
            pos = {fname: i for i, fname in enumerate(order)}
            extras = [r for r in rows if r["filename"] not in pos]
            paired = [(pos[r["filename"]], r) for r in rows if r["filename"] in pos]
            paired.sort(key=lambda t: t[0], reverse=(mode == "manifest_desc"))
            if mode == "manifest_desc":
                return [r for _, r in paired] + extras
            return extras + [r for _, r in paired]
        return sorted(
            rows,
            key=lambda r: _name_sort_key(r.get("name") or r["filename"]),
            reverse=(mode == "name_desc"),
        )

    def _cloud_row(self, it) -> dict:
        """云条目 → 前端行（无 id，cloud 标记，file_hash 为 sha256）"""
        fname = it["filename"].lower()
        return {
            "filename": it["filename"],
            "name": it["name"],
            "file_hash": it["sha256"],
            "from_stego": 0,
            "width": 0,
            "height": 0,
            "mime_type": "",
            "is_gif": fname.endswith(".gif"),
            "is_animated": False,
            "favorited": it["favorited"],
            "auto_play_gif": self._cfg.get("auto_play_gif", True),
            "hover_to_play": self._cfg.get("hover_to_play", False),
            "cloud": True,
            "genre": "gif" if fname.endswith(".gif") else "image",
            "compressed_only": bool(it.get("compressed_only")),
        }

    def search_memes(
        self,
        keyword="",
        tags=None,
        collection_id=None,
        offset=0,
        limit=200,
        category_id=None,
        genre=None,
        sort_mode="",
    ):
        """搜索表情，支持 offset/limit 分页"""
        if isinstance(category_id, str):
            sort_mode, category_id = category_id, None
        mode = sort_mode if sort_mode in _SORT_MODES else ""
        if collection_id == -5 or (collection_id == -3 and mode == "manifest"):
            mode = ""
        cloud = self._cloud_filtered(keyword, tags, collection_id, category_id, genre)
        order = self._cloud_order(collection_id) if cloud else None
        if mode in ("manifest", "manifest_desc") and not order:
            if collection_id is None or collection_id in (-2, -4):
                order = _local_manifest_order() or None
        merging = bool(cloud) and order is not None
        fetch_all = merging or bool(mode)
        rows = self._search_meme_rows(
            keyword,
            tags,
            collection_id,
            0 if fetch_all else offset,
            -1 if fetch_all else limit,
            category_id,
            genre,
        )
        favorited_ids = set()
        try:
            conn = self._db._get_conn()
            fav_rows = conn.execute("SELECT meme_id FROM favorites").fetchall()
            favorited_ids = {r[0] for r in fav_rows}
        except Exception:
            pass
        collection_ids_map = self._db.get_meme_collection_ids_map(
            [row["id"] for row in rows]
        )
        recent_ids = set(self._db.get_recent_meme_ids([row["id"] for row in rows]))
        result = []
        for row in rows:
            fname = row["filename"].lower()
            mime_type = str(row.get("mime_type") or "").lower()
            is_gif = mime_type.endswith("gif") or fname.endswith(".gif")
            if is_gif:
                is_animated = True
            elif fname.endswith(".webp"):
                is_animated = self._local_daily_is_animated(row)
            else:
                is_animated = False
            oname = row.get("original_name", "") or os.path.splitext(row["filename"])[0]
            result.append(
                {
                    "id": row["id"],
                    "filename": row["filename"],
                    "name": oname,
                    "file_hash": row.get("file_hash", ""),
                    "compressed_only": bool(row.get("compressed_only")),
                    "from_stego": row.get("from_stego", 0),
                    "width": row.get("width", 0),
                    "height": row.get("height", 0),
                    "mime_type": row.get("mime_type", ""),
                    "is_gif": is_gif,
                    "is_animated": is_animated,
                    "favorited": row["id"] in favorited_ids,
                    "auto_play_gif": self._cfg.get("auto_play_gif", True),
                    "hover_to_play": self._cfg.get("hover_to_play", False),
                    "genre": "gif" if is_gif else "image",
                    "category_id": row.get("category_id"),
                    "collection_ids": collection_ids_map.get(row["id"], []),
                    "recently_used": row["id"] in recent_ids,
                }
            )
        if mode:
            combined = result + [self._cloud_row(item) for item in cloud]
            if mode == "manifest_desc" and not order:
                order = [item["filename"] for item in combined]
            combined = self._sort_rows(combined, order, mode)
            return combined[offset:] if limit < 0 else combined[offset : offset + limit]
        if merging:
            # 清单序穿插合并后统一切片（本地全量已取回）
            merged = self._merge_manifest_order(result, cloud, order)
            return merged[offset:] if limit < 0 else merged[offset : offset + limit]
        if cloud:
            # 清单不可用回退：本地在前原序，云条目接尾分页跨两段切片
            local_total = self.count_memes(
                keyword, tags, collection_id, category_id, genre
            ) - len(cloud)
            start = max(0, offset - local_total)
            end = len(cloud) if limit < 0 else offset + limit - local_total
            if end > 0:
                result.extend(self._cloud_row(it) for it in cloud[start:end])
        return result

    def _local_daily_is_animated(self, row) -> bool:
        """仅从本地日常副本判断 WebP 动画，列表查询不触碰网盘原件。"""
        daily_name = row.get("daily_filename", "") or ""
        if (
            not daily_name
            or not _safe_serve_filename(daily_name)
            or row.get("daily_profile") != DAILY_COPY_VERSION
        ):
            return False
        path = self._cfg.derivative_dir / daily_name
        return _is_animated(str(path)) if path.is_file() else False

    def _hidden_collection_state(self, selected_collection_id=None):
        """返回系统隐藏分组、其子树及当前查询应排除的 ID。"""
        hidden_id = self._db.ensure_hidden_collection()
        hidden_ids = self._get_collection_ids_recursive(hidden_id)
        cfg = getattr(self, "_cfg", None)
        enabled = (
            cfg.get("hidden_collection_enabled", True) is not False
            if cfg is not None
            else True
        )
        try:
            selected_id = int(selected_collection_id)
        except (TypeError, ValueError):
            selected_id = None
        inside_hidden = selected_id in hidden_ids
        excluded_ids = hidden_ids if enabled and not inside_hidden else []
        return hidden_id, hidden_ids, excluded_ids

    def _get_random_meme_ids(self, excluded_ids, refresh=False):
        """保留当前随机页的 ID，只有显式重抽才替换。"""
        self._ensure_page_cache_state()
        with self._page_cache_lock:
            if refresh or getattr(self, "_random_meme_ids", None) is None:
                rows = self._db.search(
                    limit=MEME_PAGE,
                    exclude_collection_ids=excluded_ids,
                    random_order=True,
                )
                self._random_meme_ids = [row["id"] for row in rows]
            return list(self._random_meme_ids)

    def _search_meme_rows(
        self,
        keyword="",
        tags=None,
        collection_id=None,
        offset=0,
        limit=200,
        category_id=None,
        genre=None,
    ):
        """返回分页数据库行，不探测图片内容，供网格与后台缓存预热共用。"""
        if genre == "chat_record":
            return []
        if tags is not None and len(tags) == 0:
            tags = None
        fav_only = collection_id == -2
        recent_only = collection_id == -3
        uncategorized = collection_id == -4
        random_only = collection_id == -5
        category_uncategorized = category_id == -4
        cid = (
            None
            if (fav_only or recent_only or uncategorized or random_only)
            else collection_id
        )
        catid = self._category_filter_ids(category_id)
        include_description = self._cfg.get("search_description", True) is not False
        include_ocr_text = self._cfg.get("search_image_text", True) is not False
        _hidden_id, _hidden_ids, excluded_ids = self._hidden_collection_state(
            collection_id
        )
        random_ids = self._get_random_meme_ids(excluded_ids) if random_only else None
        if recent_only:
            rows = self._db.get_recent(
                limit,
                offset,
                genre=genre,
                exclude_collection_ids=excluded_ids,
            )
        else:
            if cid is not None and cid > 0:
                cid = self._get_collection_ids_recursive(cid)
            rows = self._db.search(
                keyword=keyword,
                tags=tags,
                collection_id=cid,
                favorite_only=fav_only,
                uncategorized_only=uncategorized,
                include_description=include_description,
                include_ocr_text=include_ocr_text,
                offset=0 if random_only else offset,
                limit=MEME_PAGE if random_only else limit,
                category_id=catid,
                uncategorized_category=category_uncategorized,
                genre=genre,
                exclude_collection_ids=excluded_ids,
                meme_ids=random_ids,
            )
        if random_only:
            positions = {meme_id: index for index, meme_id in enumerate(random_ids)}
            rows.sort(key=lambda row: positions[row["id"]])
            rows = rows[offset : offset + limit]
        return rows

    def count_memes(
        self,
        keyword="",
        tags=None,
        collection_id=None,
        category_id=None,
        genre=None,
    ) -> int:
        """统计符合搜索条件（关键字/标签/分组/收藏/最近使用）的表情总数，供分页"""
        if genre == "chat_record":
            return 0
        if collection_id == -5:
            return len(
                self._search_meme_rows(
                    keyword, tags, collection_id, 0, MEME_PAGE, category_id, genre
                )
            )
        if tags is not None and len(tags) == 0:
            tags = None
        fav_only = collection_id == -2
        recent_only = collection_id == -3
        uncategorized = collection_id == -4
        category_uncategorized = category_id == -4
        _hidden_id, _hidden_ids, excluded_ids = self._hidden_collection_state(
            collection_id
        )
        if recent_only:
            return self._db.count_recent(
                genre=genre, exclude_collection_ids=excluded_ids
            )
        cid = None if (fav_only or recent_only or uncategorized) else collection_id
        catid = self._category_filter_ids(category_id)
        include_description = self._cfg.get("search_description", True) is not False
        include_ocr_text = self._cfg.get("search_image_text", True) is not False
        if cid is not None and cid > 0:
            cid = self._get_collection_ids_recursive(cid)
        total = self._db.count(
            keyword=keyword,
            tags=tags,
            collection_id=cid,
            favorite_only=fav_only,
            uncategorized_only=uncategorized,
            include_description=include_description,
            include_ocr_text=include_ocr_text,
            category_id=catid,
            uncategorized_category=category_uncategorized,
            genre=genre,
            exclude_collection_ids=excluded_ids,
        )
        return total + len(
            self._cloud_filtered(keyword, tags, collection_id, category_id, genre)
        )

    @staticmethod
    def _library_genre_filter(genre):
        """将多选体裁及旧单选参数转换为聊天/表情查询条件。"""
        valid = ("image", "gif", "chat_record")
        if isinstance(genre, (list, tuple)):
            selected = [item for item in valid if item in genre or "all" in genre]
        else:
            selected = [genre] if genre in valid else valid
        meme_genres = [item for item in selected if item != "chat_record"]
        meme_genre = (
            None if len(meme_genres) == 2 else next(iter(meme_genres), "chat_record")
        )
        return "chat_record" in selected, meme_genre

    def search_library(
        self,
        keyword="",
        tags=None,
        collection_id=None,
        offset=0,
        limit=200,
        category_id=None,
        genre="all",
        refresh_random=False,
        sort_mode="",
    ) -> dict:
        """统一分页搜索图片、GIF 和聊天记录。"""
        # 把取消旧预热与查询合并到同一次 pywebview 调用中。若由前端先发
        # cancel 再发 search，繁忙时两次桥接可能互相排队，导致刷新挂起。
        self.cancel_page_cache_warmup()
        chat_selected, meme_genre = self._library_genre_filter(genre)
        offset = max(0, int(offset or 0))
        limit = max(1, min(int(limit or MEME_PAGE), 1000))
        tags = tags or []
        _hidden_id, _hidden_ids, excluded_ids = self._hidden_collection_state(
            collection_id
        )
        if collection_id == -5 and refresh_random:
            self._get_random_meme_ids(excluded_ids, refresh=True)
        chat_allowed = chat_selected and collection_id not in (
            -2,
            -3,
            -5,
        )
        chat_collection_id = None
        chat_uncategorized = collection_id == -4
        if chat_allowed and collection_id is not None and collection_id > 0:
            chat_collection_id = self._get_collection_ids_recursive(collection_id)
        chat_category_id = (
            self._category_filter_ids(category_id) if chat_allowed else None
        )
        chat_category_uncategorized = category_id == -4
        chat_count = (
            self._db.count_chat_records(
                keyword,
                tags=tags,
                collection_id=chat_collection_id,
                category_id=chat_category_id,
                uncategorized_category=chat_category_uncategorized,
                uncategorized_only=chat_uncategorized,
                exclude_collection_ids=excluded_ids,
            )
            if chat_allowed
            else 0
        )
        chat_records = []
        if chat_allowed and offset < chat_count:
            chat_records = self._db.list_chat_records(
                keyword,
                min(limit, chat_count - offset),
                offset,
                tags=tags,
                collection_id=chat_collection_id,
                category_id=chat_category_id,
                uncategorized_category=chat_category_uncategorized,
                uncategorized_only=chat_uncategorized,
                exclude_collection_ids=excluded_ids,
            )
            for item in chat_records:
                item["genre"] = "chat_record"

        remaining = limit - len(chat_records)
        meme_count = 0
        memes = []
        if meme_genre != "chat_record":
            meme_count = self.count_memes(
                keyword, tags, collection_id, category_id, meme_genre
            )
            if remaining > 0:
                meme_offset = max(0, offset - chat_count)
                memes = self.search_memes(
                    keyword,
                    tags,
                    collection_id,
                    meme_offset,
                    remaining,
                    category_id,
                    meme_genre,
                    sort_mode,
                )
        return {
            "memes": memes,
            "chat_records": chat_records,
            "chat_total": chat_count,
            "total": chat_count + meme_count,
            "offset": offset,
            "limit": limit,
        }

    def count_library(
        self,
        keyword="",
        tags=None,
        collection_id=None,
        category_id=None,
        genre="all",
    ) -> int:
        """统计统一内容库结果，供桥接调用和测试。"""
        result = self.search_library(
            keyword, tags, collection_id, 0, 1, category_id, genre
        )
        return int(result["total"])

    def _ensure_page_cache_state(self):
        """兼容测试/迁移中通过 ``__new__`` 构造的轻量 JsApi。"""
        if not hasattr(self, "_page_cache_lock"):
            self._page_cache_lock = threading.Lock()
            self._page_cache_generation = 0
            self._page_cache_thread = None

    def _page_cache_is_current(self, generation: int) -> bool:
        self._ensure_page_cache_state()
        with self._page_cache_lock:
            return generation == self._page_cache_generation

    def cancel_page_cache_warmup(self) -> dict:
        """使正在运行的后续页预热任务在下一个检查点退出。"""
        self._ensure_page_cache_state()
        with self._page_cache_lock:
            self._page_cache_generation += 1
        return {"ok": True}

    def start_page_cache_warmup(
        self,
        keyword="",
        tags=None,
        collection_id=None,
        current_offset=0,
        page_size=200,
        total=0,
        category_id=None,
        genre="all",
        sort_mode="",
    ) -> dict:
        """后台补建当前查询后续页的日常副本和缩略图。"""
        self._ensure_page_cache_state()
        with self._page_cache_lock:
            self._page_cache_generation += 1
            generation = self._page_cache_generation

        enabled = self._cfg.get("bulk_read_protection", False) is True
        prefetch = self._cfg.get("bulk_read_prefetch_pages", False) is True
        if not enabled or not prefetch:
            return {"ok": True, "started": False}
        _chat_selected, meme_genre = self._library_genre_filter(genre)
        if meme_genre == "chat_record":
            return {"ok": True, "started": False}
        page_size = max(1, min(int(page_size or MEME_PAGE), 1000))
        next_offset = max(0, int(current_offset or 0)) + page_size
        total = max(0, int(total or 0))
        if next_offset >= total:
            return {"ok": True, "started": False}

        args = (
            generation,
            str(keyword or ""),
            list(tags or []),
            collection_id,
            next_offset,
            page_size,
            total,
            category_id,
            list(genre) if isinstance(genre, (list, tuple)) else genre,
            sort_mode,
        )
        worker = threading.Thread(
            target=self._run_page_cache_warmup,
            args=args,
            name="OhMyMemePageCacheWarmup",
            daemon=True,
        )
        with self._page_cache_lock:
            self._page_cache_thread = worker
        worker.start()
        return {"ok": True, "started": True}

    def _run_page_cache_warmup(
        self,
        generation,
        keyword,
        tags,
        collection_id,
        next_offset,
        page_size,
        total,
        category_id,
        genre,
        sort_mode="",
    ):
        """串行预热；新搜索会递增 generation，让旧任务尽快退出。"""
        try:
            # 先给当前页的可见请求留出时间；每 100ms 检查一次取消状态。
            for _ in range(10):
                if not self._page_cache_is_current(generation):
                    return
                time.sleep(0.1)
            _hidden_id, _hidden_ids, excluded_ids = self._hidden_collection_state(
                collection_id
            )
            chat_selected, meme_genre = self._library_genre_filter(genre)
            if meme_genre == "chat_record":
                return
            chat_allowed = chat_selected and collection_id not in (-2, -3, -5)
            chat_collection_id = None
            chat_uncategorized = collection_id == -4
            if chat_allowed and collection_id is not None and collection_id > 0:
                chat_collection_id = self._get_collection_ids_recursive(collection_id)
            chat_category_id = (
                self._category_filter_ids(category_id) if chat_allowed else None
            )
            chat_category_uncategorized = category_id == -4
            chat_count = (
                self._db.count_chat_records(
                    keyword,
                    tags=tags,
                    collection_id=chat_collection_id,
                    category_id=chat_category_id,
                    uncategorized_category=chat_category_uncategorized,
                    uncategorized_only=chat_uncategorized,
                    exclude_collection_ids=excluded_ids,
                )
                if chat_allowed
                else 0
            )
            meme_offset = max(0, int(next_offset) - chat_count)
            meme_total = self.count_memes(
                keyword, tags, collection_id, category_id, meme_genre
            )
            while meme_offset < meme_total:
                if not self._page_cache_is_current(generation):
                    return
                search_rows = (
                    self.search_memes
                    if sort_mode in _SORT_MODES
                    else self._search_meme_rows
                )
                options = {"sort_mode": sort_mode} if sort_mode in _SORT_MODES else {}
                rows = search_rows(
                    keyword,
                    tags,
                    collection_id,
                    meme_offset,
                    page_size,
                    category_id,
                    meme_genre,
                    **options,
                )
                if not rows:
                    break
                for row in rows:
                    if not self._page_cache_is_current(generation):
                        return
                    if row.get("cloud"):
                        continue
                    try:
                        self._webui._get_thumbnail_path(
                            int(row["id"]),
                            row["filename"],
                            read_priority="background",
                        )
                    except Exception as cache_error:
                        logger.warning(
                            "page cache warmup failed %s: %s",
                            row.get("filename", ""),
                            cache_error,
                        )
                    # 主动让出执行机会，避免后台任务连续抢在当前页请求前面。
                    time.sleep(0.02)
                meme_offset += len(rows)
            logger.info(
                "page cache warmup finished: offset=%s total=%s",
                next_offset,
                total,
            )
        except Exception:
            logger.exception("page cache warmup failed")

    def get_chat_record_details(self, record_id: int) -> dict:
        """返回聊天记录来源、统计与可读消息列表。"""
        entry = self._db.get_chat_record_entry(int(record_id))
        if not entry:
            return {"ok": False, "error": "聊天记录不存在"}
        package = entry.pop("package")
        summary = package.get("summary")
        summary = summary if isinstance(summary, dict) else {}
        source = package.get("source")
        source = source if isinstance(source, dict) else {}
        blocked_message_paths = onebot.chat_record_blocked_paths(package)
        cfg = getattr(self, "_cfg", None)
        napcat_command = (
            cfg.get("napcat_command", "/omm") if cfg is not None else "/omm"
        )
        return {
            "ok": True,
            **entry,
            "genre": "chat_record",
            "genre_label": "聊天记录",
            "peer_label": {
                "group": "群聊",
                "private": "私聊",
            }.get(entry.get("message_type"), "未知会话"),
            "captured_at": package.get("captured_at") or "",
            "source_locations": [
                onebot._persistent_source_identity(location)
                for location in onebot.chat_record_sources(package)
                if onebot._persistent_source_identity(location)
            ],
            "media_count": int(summary.get("media_count") or 0),
            "max_depth": int(summary.get("max_depth") or 0),
            "sender_name": str(
                (source.get("sender") or {}).get("nickname")
                if isinstance(source.get("sender"), dict)
                else ""
            ),
            "messages": onebot.chat_record_view(
                package,
                media_url_factory=lambda index, _kind: (
                    f"/api/chat-record-media/{int(record_id)}/{int(index)}"
                ),
            ),
            "blocked_message_paths": blocked_message_paths,
            "blocked_message_count": len(blocked_message_paths),
            "tags": self._db.get_chat_tags(entry["id"]),
            "tag_records": self._db.get_chat_tag_records(entry["id"]),
            "all_tags": self._db.get_all_tags(),
            "all_tag_records": self._db.get_all_tag_records(),
            "collections": self._db.get_chat_collections(entry["id"]),
            "all_collections": self._db.get_all_collection_paths(),
            "category": self._db.get_chat_category(entry["id"]),
            "all_categories": self._build_category_options(),
            "send_command": (f"{napcat_command} send {int(record_id)}"),
        }

    def update_chat_record_details(self, record_id: int, data) -> dict:
        """保存聊天记录名称、媒体目录、分类信息和选择性发送规则。"""
        old_media_dir = None
        new_media_dir = None
        media_dir_moved = False
        try:
            if not isinstance(data, dict):
                return {"ok": False, "error": "详情数据无效"}
            record_id = int(record_id)
            entry = self._db.get_chat_record_entry(record_id)
            if not entry:
                return {"ok": False, "error": "聊天记录不存在"}
            name = str(
                data.get("name", entry.get("name") or entry.get("title")) or ""
            ).strip()
            if not name:
                return {"ok": False, "error": "名称不能为空"}
            if len(name) > 200 or any(ord(char) < 32 for char in name):
                return {"ok": False, "error": "名称包含无效字符或超过 200 个字符"}
            media_folder = str(
                data.get("media_folder", entry.get("media_folder") or "") or ""
            ).strip()
            if not _valid_editable_filename(media_folder):
                return {"ok": False, "error": "媒体文件夹名包含无效字符或保留名称"}
            existing_folder = self._db.get_chat_record_by_media_folder(media_folder)
            if existing_folder and int(existing_folder["id"]) != record_id:
                return {"ok": False, "error": "该媒体文件夹名已被其他聊天记录使用"}
            raw_tags = data.get("tags", self._db.get_chat_tag_records(record_id))
            if not isinstance(raw_tags, list):
                return {"ok": False, "error": "标签数据无效"}
            tags = [
                tag
                for tag in raw_tags
                if (isinstance(tag, dict) and str(tag.get("name") or "").strip())
                or (isinstance(tag, str) and tag.strip())
            ]
            raw_collection_ids = data.get("collection_ids")
            if raw_collection_ids is not None and not isinstance(
                raw_collection_ids, list
            ):
                return {"ok": False, "error": "分组数据无效"}
            collection_ids = None
            if raw_collection_ids is not None:
                try:
                    collection_ids = list(
                        dict.fromkeys(int(value) for value in raw_collection_ids)
                    )
                except (TypeError, ValueError):
                    return {"ok": False, "error": "分组数据无效"}
                if any(value <= 0 for value in collection_ids):
                    return {"ok": False, "error": "分组数据无效"}
            category_supplied = "category_id" in data
            category_id = _UNSET
            if category_supplied:
                raw_category_id = data.get("category_id")
                if raw_category_id in (None, "", 0, "0"):
                    category_id = None
                else:
                    try:
                        category_id = int(raw_category_id)
                    except (TypeError, ValueError):
                        return {"ok": False, "error": "类别数据无效"}
                    if category_id <= 0 or not self._db.category_exists(category_id):
                        return {"ok": False, "error": "类别不存在"}
            update_kwargs = {
                "collection_ids": collection_ids,
                "category_id": category_id,
                "name": name,
            }
            if "blocked_message_paths" in data:
                try:
                    blocked_message_paths = onebot.normalize_chat_record_blocked_paths(
                        data.get("blocked_message_paths")
                    )
                except ValueError as exc:
                    return {"ok": False, "error": str(exc)}
                valid_paths = {
                    item["path"] for item in onebot.chat_record_view(entry["package"])
                }
                if any(path not in valid_paths for path in blocked_message_paths):
                    return {"ok": False, "error": "聊天记录屏蔽节点不存在"}
                update_kwargs["blocked_message_paths"] = blocked_message_paths
            if media_folder != str(entry.get("media_folder") or ""):
                old_media_dir, new_media_dir, media_dir_moved = (
                    onebot.rename_chat_record_media_folder(
                        entry["package"], media_folder, cfg=self._cfg
                    )
                )
                update_kwargs["media_folder"] = media_folder
                update_kwargs["package"] = entry["package"]
            updated = self._db.update_chat_record_details(
                record_id, tags, **update_kwargs
            )
            if not updated:
                return {"ok": False, "error": "聊天记录不存在"}
            try:
                from .library_events import notify_changed

                notify_changed("structure")
            except Exception:
                pass
            updated_package = self._db.get_chat_record(record_id) or {}
            blocked_message_paths = onebot.chat_record_blocked_paths(updated_package)
            return {
                "ok": True,
                "name": name,
                "media_folder": media_folder,
                "tags": self._db.get_chat_tags(record_id),
                "tag_records": self._db.get_chat_tag_records(record_id),
                "all_tags": self._db.get_all_tags(),
                "all_tag_records": self._db.get_all_tag_records(),
                "collections": self._db.get_chat_collections(record_id),
                "all_collections": self._db.get_all_collection_paths(),
                "category": self._db.get_chat_category(record_id),
                "all_categories": self._build_category_options(),
                "blocked_message_paths": blocked_message_paths,
                "blocked_message_count": len(blocked_message_paths),
            }
        except (ValueError, OSError) as exc:
            if (
                media_dir_moved
                and old_media_dir is not None
                and new_media_dir is not None
            ):
                try:
                    os.replace(str(new_media_dir), str(old_media_dir))
                except OSError:
                    logger.exception("聊天记录媒体文件夹回滚失败")
            return {"ok": False, "error": str(exc)}
        except Exception:
            if (
                media_dir_moved
                and old_media_dir is not None
                and new_media_dir is not None
            ):
                try:
                    os.replace(str(new_media_dir), str(old_media_dir))
                except OSError:
                    logger.exception("聊天记录媒体文件夹回滚失败")
            logger.exception("update_chat_record_details error")
            return {"ok": False, "error": "保存失败"}

    def delete_chat_record(self, record_id: int) -> dict:
        """删除一份聊天记录及其独占媒体目录。"""
        try:
            deleted = onebot.delete_chat_record_archive(
                int(record_id), db=self._db, cfg=self._cfg
            )
            if deleted:
                try:
                    from .library_events import notify_changed

                    notify_changed("structure")
                except Exception:
                    pass
            return {
                "ok": deleted,
                "error": "" if deleted else "聊天记录不存在",
            }
        except (TypeError, ValueError):
            return {"ok": False, "error": "聊天记录 ID 无效"}
        except Exception as exc:
            logger.exception("delete chat record failed")
            return {"ok": False, "error": str(exc)}

    def locate_meme(self, meme_id, page_size=MEME_PAGE):
        """定位表情在默认「全部」列表中的序号与页码（与 search_memes 默认视图同序）

        云清单合并顺序必须与 search_memes 的默认分支保持一致：改那里的
        `_cloud_filtered`/`_cloud_order`/`_merge_manifest_order` 时同步改这里。
        """
        try:
            mid = int(meme_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "无效的表情 id"}
        if mid <= 0:
            return {"ok": False, "error": "无效的表情 id"}
        try:
            size = int(page_size)
        except (TypeError, ValueError):
            size = MEME_PAGE
        size = size if size > 0 else MEME_PAGE
        mode = self._cfg.get("sort_mode", "")
        mode = mode if mode in _SORT_MODES else ""
        rows = self.search_memes("", None, None, 0, -1, sort_mode=mode)
        for idx, r in enumerate(rows):
            if r.get("id") == mid:
                return {
                    "ok": True,
                    "id": mid,
                    "index": idx,
                    "page": idx // size + 1,
                    "index_in_page": idx % size,
                    "total": len(rows),
                    "page_size": size,
                }
        return {"ok": False, "id": mid, "error": "表情不在默认列表中"}

    def get_tags(self) -> list:
        """标签列表（云端直接使用开启时并入云端独有标签）"""
        local = self._db.get_all_tags()
        if not self._cfg.get("cloud_direct", False):
            return local
        cloud = self._cloud_filtered("", None, None)
        extra = {t for it in cloud for t in it["tags"] if t}
        if not extra:
            return local
        return sorted(set(local) | extra)

    def get_tag_records(self) -> list:
        """返回含分类信息的标签列表；get_tags 保留给旧版前端。"""
        records = self._db.get_all_tag_records()
        by_key = {
            (str(tag.get("category") or "").casefold(), tag["name"].casefold()): tag
            for tag in records
        }
        for item in self._cloud_filtered("", None, None):
            tags = item.get("tag_records")
            for category, name in MemeDB._normalize_tags(
                tags if isinstance(tags, list) else item["tags"]
            ):
                by_key.setdefault(
                    (category.casefold(), name.casefold()),
                    {"name": name, "category": category},
                )
        return sorted(
            by_key.values(), key=lambda tag: (tag.get("category", ""), tag["name"])
        )

    def get_meme_tags(self, meme_id):
        """返回某表情的标签列表"""
        try:
            return self._db.get_meme_tags(meme_id) or []
        except Exception as e:
            logger.error(f"get_meme_tags error: {e}")
            return []

    def get_meme_tag_records(self, meme_id):
        try:
            return self._db.get_meme_tag_records(meme_id) or []
        except Exception as e:
            logger.error(f"get_meme_tag_records error: {e}")
            return []

    def get_chat_tags(self, record_id):
        try:
            return self._db.get_chat_tags(int(record_id)) or []
        except Exception as e:
            logger.error(f"get_chat_tags error: {e}")
            return []

    def get_chat_tag_records(self, record_id):
        try:
            return self._db.get_chat_tag_records(int(record_id)) or []
        except Exception as e:
            logger.error(f"get_chat_tag_records error: {e}")
            return []

    def set_chat_tags(self, record_id, tags):
        try:
            if not isinstance(tags, list):
                return False
            ok = self._db.set_chat_tags(int(record_id), tags)
            if ok:
                try:
                    from .library_events import notify_changed

                    notify_changed("content")
                except Exception:
                    pass
            return bool(ok)
        except Exception as e:
            logger.error(f"set_chat_tags error: {e}")
            return False

    def rename_tag(self, old_name, new_name, category="", new_category=None):
        """全局修改标签名称/种类，目标存在时合并标签。"""
        try:
            if not isinstance(old_name, str) or not isinstance(new_name, str):
                return {"ok": False, "error": "标签名称无效"}
            result = self._db.rename_tag(
                old_name, new_name, category=category, new_category=new_category
            )
            if result is None:
                return {"ok": False, "error": "标签不存在或名称为空"}
            return {
                "ok": True,
                "name": result["name"],
                "category": result["category"],
                "merged": result["merged"],
                "all_tags": self._db.get_all_tags(),
                "all_tag_records": self._db.get_all_tag_records(),
            }
        except Exception as e:
            logger.error(f"rename_tag error: {e}")
            return {"ok": False, "error": "标签编辑失败"}

    def get_meme_details(self, meme_id):
        """按需返回单张表情的可编辑详情和全图库浏览位置"""
        try:
            row = self._db.get_by_id(int(meme_id))
            if not row:
                return None
            navigation = self._db.get_meme_navigation(row["id"]) or {}
            name = row.get("original_name", "") or os.path.splitext(row["filename"])[0]
            return {
                "id": row["id"],
                "filename": row["filename"],
                "name": name,
                "width": row.get("width", 0),
                "height": row.get("height", 0),
                "file_size": row.get("file_size", 0),
                "compressed_only": bool(row.get("compressed_only")),
                "mime_type": row.get("mime_type", ""),
                "created_at": row.get("created_at", ""),
                "updated_at": row.get("updated_at", ""),
                # 中性旧字段保留兼容：优先人工内容，无人工内容时回退 AI。
                "description": (
                    row.get("manual_description", "")
                    or row.get("ai_description", "")
                    or ""
                ),
                "ocr_text": (
                    row.get("manual_ocr_text", "") or row.get("ai_ocr_text", "") or ""
                ),
                "manual_description": row.get("manual_description", "") or "",
                "manual_ocr_text": row.get("manual_ocr_text", "") or "",
                "ai_description": row.get("ai_description", "") or "",
                "ai_ocr_text": row.get("ai_ocr_text", "") or "",
                "tags": self._db.get_meme_tags(row["id"]),
                "tag_records": self._db.get_meme_tag_records(row["id"]),
                "all_tags": self._db.get_all_tags(),
                "all_tag_records": self._db.get_all_tag_records(),
                "collections": self._db.get_meme_collections(row["id"]),
                "all_collections": self._db.get_all_collection_paths(),
                "category": self._db.get_meme_category(row["id"]),
                "all_categories": self._build_category_options(),
                "previous_id": navigation.get("previous_id"),
                "next_id": navigation.get("next_id"),
                "position": navigation.get("position", 1),
                "total": navigation.get("total", 1),
            }
        except Exception as e:
            logger.error(f"get_meme_details error: {e}")
            return None

    def update_meme_details(self, meme_id, data):
        """保存标题、文件名、标签、类别及人工/AI 图片文字；路径同步到磁盘。"""
        old_path = None
        new_path = None
        file_moved = False
        try:
            if not isinstance(data, dict):
                return {"ok": False, "error": "详情数据无效"}
            meme_id = int(meme_id)
            row = self._db.get_by_id(meme_id)
            if not row:
                return {"ok": False, "error": "表情包不存在"}
            raw_tags = data.get("tags", [])
            if not isinstance(raw_tags, list):
                return {"ok": False, "error": "标签数据无效"}
            tags = [
                tag
                for tag in raw_tags
                if (isinstance(tag, dict) and str(tag.get("name") or "").strip())
                or (isinstance(tag, str) and tag.strip())
            ]
            raw_collection_ids = data.get("collection_ids")
            if raw_collection_ids is not None and not isinstance(
                raw_collection_ids, list
            ):
                return {"ok": False, "error": "分组数据无效"}
            collection_ids = None
            if raw_collection_ids is not None:
                collection_ids = list(
                    dict.fromkeys(int(value) for value in raw_collection_ids)
                )
                if any(value <= 0 for value in collection_ids):
                    return {"ok": False, "error": "分组数据无效"}
            category_supplied = "category_id" in data
            target_category_id = row.get("category_id")
            if category_supplied:
                raw_category_id = data.get("category_id")
                if raw_category_id in (None, "", 0, "0"):
                    target_category_id = None
                else:
                    try:
                        target_category_id = int(raw_category_id)
                    except (TypeError, ValueError):
                        return {"ok": False, "error": "类别数据无效"}
                    if target_category_id <= 0 or not self._db.category_exists(
                        target_category_id
                    ):
                        return {"ok": False, "error": "类别不存在"}
            old_collection_ids = {
                item["id"] for item in self._db.get_meme_collections(meme_id)
            }
            has_split_text = any(
                key in data
                for key in (
                    "manual_description",
                    "manual_ocr_text",
                    "ai_description",
                    "ai_ocr_text",
                )
            )
            if has_split_text:
                manual_description = str(
                    data.get("manual_description", row.get("manual_description", ""))
                    or ""
                )
                manual_ocr_text = str(
                    data.get("manual_ocr_text", row.get("manual_ocr_text", "")) or ""
                )
                ai_description = str(
                    data.get("ai_description", row.get("ai_description", "")) or ""
                )
                ai_ocr_text = str(
                    data.get("ai_ocr_text", row.get("ai_ocr_text", "")) or ""
                )
            else:
                # 旧版调用方的 description/ocr_text 原本直接编辑 AI 字段。
                manual_description = str(row.get("manual_description", "") or "")
                manual_ocr_text = str(row.get("manual_ocr_text", "") or "")
                ai_description = str(
                    data.get("description", row.get("ai_description", "")) or ""
                )
                ai_ocr_text = str(
                    data.get("ocr_text", row.get("ai_ocr_text", "")) or ""
                )
            current_title = (
                row.get("original_name", "") or os.path.splitext(row["filename"])[0]
            )
            title_supplied = "title" in data
            filename_supplied = "filename" in data
            title = str(data.get("title", current_title) or "").strip()
            filename = str(data.get("filename", row["filename"]) or "").strip()
            if not title:
                return {"ok": False, "error": "标题不能为空"}
            if not _valid_editable_filename(filename):
                return {"ok": False, "error": "文件名包含无效字符或保留名称"}
            if Path(filename).suffix.lower() != Path(row["filename"]).suffix.lower():
                return {"ok": False, "error": "不能修改文件扩展名"}

            existing = self._db.get_by_filename(filename)
            if existing and existing["id"] != meme_id:
                return {"ok": False, "error": "该文件名已被其他表情包使用"}

            if filename != row["filename"] or target_category_id != row.get(
                "category_id"
            ):
                try:
                    webui = getattr(self, "_webui", None)
                    mover = getattr(webui, "_move_meme_file", None)
                    if callable(mover):
                        old_path, new_path = mover(row, filename, target_category_id)
                    elif webui is None and not hasattr(self, "_cfg"):
                        # 允许脱离窗口的轻量 DB 调用方（例如迁移/测试适配器）
                        # 只更新元数据；真实 WebUI 始终走上面的物理移动分支。
                        old_path = new_path = None
                    else:
                        source = self._find_meme_file(row["filename"])
                        if not source:
                            raise FileNotFoundError("找不到原始图片文件")
                        old_path = Path(source)
                        new_path = old_path.with_name(filename)
                        same_path = old_path == new_path
                        if new_path.exists() and not same_path:
                            try:
                                same_path = os.path.samefile(old_path, new_path)
                            except OSError:
                                pass
                        if new_path.exists() and not same_path:
                            raise ValueError("目标位置已存在同名文件")
                        os.replace(str(old_path), str(new_path))
                    file_moved = (
                        old_path is not None
                        and new_path is not None
                        and old_path != new_path
                    )
                except (FileNotFoundError, ValueError) as move_error:
                    return {"ok": False, "error": str(move_error)}

            ok = self._db.update_meme_details(
                meme_id,
                tags,
                ai_description,
                ai_ocr_text,
                title=title if title_supplied else None,
                filename=filename if filename_supplied else None,
                collection_ids=collection_ids,
                manual_description=manual_description,
                manual_ocr_text=manual_ocr_text,
                category_id=target_category_id if category_supplied else _UNSET,
            )
            if not ok:
                raise ValueError("表情包不存在")
            collections_changed = (
                collection_ids is not None and set(collection_ids) != old_collection_ids
            )
            category_changed = category_supplied and target_category_id != row.get(
                "category_id"
            )
            manifest_structure_changed = (
                filename != row["filename"] or collections_changed or category_changed
            )
            if manifest_structure_changed:
                if not update_manifest_meme_structure(meme_id, row["filename"]):
                    # 完整重建可能正在扫描虚拟盘；只排队修复，不阻塞保存响应。
                    request_manifest_rebuild()
            elif title != current_title and not update_manifest_meme_name(
                row["filename"], title
            ):
                # 首次运行、锁繁忙或清单损坏时在后台恢复一致性。
                request_manifest_rebuild()
            if file_moved:
                file_cache = getattr(self._webui, "_file_cache", None)
                if isinstance(file_cache, dict):
                    file_cache.pop(row["filename"], None)
                    file_cache.pop(filename, None)
                    file_cache[filename] = str(new_path)
            return {
                "ok": True,
                "title": title,
                "filename": filename,
                "all_tags": self._db.get_all_tags(),
                "all_tag_records": self._db.get_all_tag_records(),
                "collections": self._db.get_meme_collections(meme_id),
                "all_collections": self._db.get_all_collection_paths(),
                "category": self._db.get_meme_category(meme_id),
                "all_categories": self._build_category_options(),
            }
        except Exception as e:
            if file_moved and old_path is not None and new_path is not None:
                try:
                    if new_path.exists() and not old_path.exists():
                        old_path.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(new_path, old_path)
                except OSError:
                    logger.exception("failed to roll back meme filename")
            logger.error(f"update_meme_details error: {e}")
            error = str(e) if isinstance(e, ValueError) else "保存失败"
            return {"ok": False, "error": error}

    def set_meme_tags(self, meme_id, tags):
        """覆盖式设置某表情的标签"""
        try:
            self._db.set_meme_tags(meme_id, tags or [])
            build_manifest()
            return True
        except Exception as e:
            logger.error(f"set_meme_tags error: {e}")
            return False

    def set_chat_category(self, record_id, category_id=None):
        try:
            if category_id in ("", 0, "0"):
                category_id = None
            elif category_id is not None:
                category_id = int(category_id)
            ok = self._db.set_chat_category(int(record_id), category_id)
            if ok:
                try:
                    from .library_events import notify_changed

                    notify_changed("structure")
                except Exception:
                    pass
            return bool(ok)
        except Exception as e:
            logger.error(f"set_chat_category error: {e}")
            return False

    def get_init_data(self) -> dict:
        """批返回初始化所需数据，减少 JS bridge 往返"""
        sort_mode = self._cfg.get("sort_mode", "manifest")
        memes = self.search_memes("", None, None, 0, MEME_PAGE, sort_mode=sort_mode)
        collections = self.get_collections()
        categories = self.get_categories()
        fields = get_plugin_manager().init_fields()
        return {
            "theme": self._cfg.get("theme", "dark"),
            "sort_mode": sort_mode,
            "memes": memes,
            "tags": self.get_tags(),
            "tag_records": self.get_tag_records(),
            "collections": collections,
            "categories": categories,
            "library_genres": normalize_library_genres(self._cfg.get("library_genres")),
            "show_startup_animation": self._cfg.get("show_startup_animation", True),
            "hover_zoom": self._cfg.get("hover_zoom", True),
            "window_resize_enabled": self._cfg.get("window_resize_enabled", False),
            "startup_bg_color": fields.get("startup_bg_color") or _STARTUP_BG_COLOR,
            "startup_video_src": fields.get("startup_video_src")
            or _DEFAULT_STARTUP_VIDEO,
            "startup_media_duration_ms": fields.get("startup_media_duration_ms") or 0,
            "startup_media_type": fields.get("startup_media_type") or "",
            "plugin_buttons": fields.get("plugin_buttons") or [],
            "plugin_button_constraints": fields.get("plugin_button_constraints")
            or {"hide": [], "order": {}},
            "guide_ok": guide_ok(),
        }

    def set_library_genres(self, genres):
        """仅保存体裁选择，不触发扫描、同步或窗口刷新。"""
        if not isinstance(genres, list) or any(
            genre not in ("image", "gif", "chat_record") for genre in genres
        ):
            return {"ok": False, "error": "无效的体裁选择"}
        try:
            self._cfg.set("library_genres", genres)
            self._cfg.save()
            return {"ok": True}
        except OSError:
            logger.exception("failed to save library genres")
            return {"ok": False, "error": "体裁选择保存失败"}

    def complete_guide(self) -> dict:
        """设置向导完成：写入配置 guide=ok 标记"""
        set_guide_ok()
        return {"ok": True}

    def get_meme_path(self, meme_id: int) -> str:
        """返回日常副本路径（供拖拽到外部应用），不存在返回空串。"""
        row = self._db.get_by_id(meme_id)
        if not row:
            return ""
        return self._daily_meme_path(row)

    def get_meme_paths(self, meme_ids: list) -> dict:
        """批量返回表情本地文件路径 {id: path}，供拖拽到外部应用"""
        out = {}
        for mid in meme_ids:
            try:
                row = self._db.get_by_id(int(mid))
                if row:
                    p = self._daily_meme_path(row)
                    if p:
                        out[int(mid)] = p
            except Exception:
                continue
        return out

    def start_native_drag(self, meme_id: int) -> bool:
        """用 WinForms DoDragDrop 启动原生文件拖拽（QQ/微信可接收真实文件）"""
        row = self._db.get_by_id(meme_id)
        if not row:
            return False
        p = self._daily_meme_path(row)
        if not p:
            return False
        try:
            from .native_drag import start_native_drag as _start

            ok = bool(_start(p))
            if ok:
                self._record_recent_meme(meme_id)
                self._webui.schedule_hide()
            return ok
        except Exception:
            return False

    def start_meme_drag(self, meme_id: int) -> dict:
        """拖出图片；落到 QQ 时可改由 NapCat 按表情包发送。"""
        row = self._db.get_by_id(meme_id)
        if not row:
            return {"ok": False, "error": "表情不存在"}
        path = self._daily_meme_path(row)
        if not path:
            return {"ok": False, "error": "本地文件不存在"}
        send_mode = self._cfg.get("napcat_image_send_mode", "sticker")
        intercept_qq = send_mode == "sticker"
        try:
            from .native_drag import start_meme_drag
            from .qq_drag import resolve_qq_target

            dropped = start_meme_drag(path, intercept_qq=intercept_qq)
            if dropped.get("cancelled"):
                return {"ok": False, "cancelled": True}
            if not dropped.get("ok"):
                return {
                    "ok": False,
                    "error": dropped.get("error") or "拖拽失败",
                }
            qq_target = dropped.get("qq_target")
            if qq_target:
                http_url = self._cfg.get("napcat_http_url", "http://127.0.0.1:3000")
                token = self._cfg.get("napcat_token", "")
                target = resolve_qq_target(
                    http_url,
                    token,
                    qq_target.get("window_handle"),
                    qq_target.get("process_name"),
                    qq_target.get("x"),
                    qq_target.get("y"),
                    self._cfg.get("napcat_webui_url", "http://127.0.0.1:6099"),
                    self._cfg.get("napcat_webui_token", ""),
                )
                onebot.send_meme_image(
                    http_url,
                    token,
                    path,
                    target["message_type"],
                    target["peer_id"],
                    as_sticker=True,
                )
                recent_recorded = self._record_recent_meme(meme_id)
                self._webui.schedule_hide()
                return {
                    "ok": True,
                    "recent_recorded": recent_recorded,
                    "sent_as_sticker": True,
                    "target_label": target.get("label") or "当前 QQ 会话",
                    "target_source": target.get("source") or "uia",
                }
            recent_recorded = self._record_recent_meme(meme_id)
            self._webui.schedule_hide()
            return {
                "ok": True,
                "recent_recorded": recent_recorded,
                "file_delivered": True,
            }
        except (onebot.OneBotError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            logger.exception("meme native drag failed")
            return {"ok": False, "error": "拖拽失败，请检查 NapCat 与 QQ"}

    def start_chat_record_drag(self, record_id: int) -> dict:
        """拖到 QQ 当前会话后直接通过 NapCat 发送，不投递命令文本。"""
        try:
            record_id = int(record_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "聊天记录 ID 无效"}
        if not self._db.get_chat_record_entry(record_id):
            return {"ok": False, "error": "聊天记录不存在"}
        try:
            from .native_drag import start_chat_record_target_drag
            from .qq_drag import resolve_qq_target

            dropped = start_chat_record_target_drag(record_id)
            if dropped.get("cancelled"):
                return {"ok": False, "cancelled": True}
            if not dropped.get("ok"):
                return {
                    "ok": False,
                    "error": dropped.get("error") or "拖拽发送失败",
                }
            http_url = self._cfg.get("napcat_http_url", "http://127.0.0.1:3000")
            token = self._cfg.get("napcat_token", "")
            target = resolve_qq_target(
                http_url,
                token,
                dropped.get("window_handle"),
                dropped.get("process_name"),
                dropped.get("x"),
                dropped.get("y"),
                self._cfg.get("napcat_webui_url", "http://127.0.0.1:6099"),
                self._cfg.get("napcat_webui_token", ""),
            )
            event = {"message_type": target["message_type"]}
            if target["message_type"] == "group":
                event["group_id"] = target["peer_id"]
            else:
                event["target_id"] = target["peer_id"]
            result = onebot.send_chat_record(
                http_url,
                token,
                record_id,
                event,
                db=self._db,
                send_mode=self._cfg.get("napcat_send_mode", "original_native"),
                webui_url=self._cfg.get("napcat_webui_url", "http://127.0.0.1:6099"),
                webui_token=self._cfg.get("napcat_webui_token", ""),
            )
            self._webui.schedule_hide()
            return {
                "ok": True,
                "target_label": target.get("label") or "当前 QQ 会话",
                "target_source": target.get("source") or "uia",
                "source": result.get("source"),
                "native_rebuild": bool(result.get("native_rebuild")),
                "native_replay": bool(result.get("native_replay")),
                "native_filtered_replay": bool(result.get("native_filtered_replay")),
                "self_native_relay": bool(result.get("self_native_relay")),
                "degraded_media_count": int(result.get("degraded_media_count") or 0),
            }
        except (onebot.OneBotError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            logger.exception("chat record native drag failed")
            return {"ok": False, "error": "拖拽发送失败，请检查 NapCat 与 QQ"}

    def copy_meme(self, meme_id):
        # 复制表情到剪贴板；copy_resize_mode: 0不处理 1webp缩放 2转gif
        # 3转gif隐写原图 4仅改后缀为gif
        row = self._db.get_by_id(meme_id)
        if not row:
            return {"ok": False, "status": "copy_failed"}
        path = self._daily_meme_path(row)
        if not path:
            return {"ok": False, "status": "copy_failed"}
        resize_mode = int(self._cfg.get("copy_resize_mode", 1) or 0)
        resize_max = int(self._cfg.get("copy_resize_max", 200) or 200)
        avoid_webp = bool(self._cfg.get("copy_avoid_webp", False))
        match resize_mode:
            case 1:
                path = convert_image_mode_1(path, resize_max, avoid_webp) or path
            case 2:
                path = convert_image_mode_2(path, resize_max) or path
            case 3:
                path = convert_image_mode_3(path, resize_max) or path
            case 4:
                path = convert_image_mode_4(path, resize_max) or path
        # 复制微信等应用会把 WebP 当文件，故兜底把残余 WebP 转为 GIF/JPG
        # 模式 4 要求字节完全不变，不参与该兜底
        if avoid_webp and resize_mode != 4:
            path = convert_avoid_webp(path, resize_max)
        ok = copy_image_to_clipboard(path)
        if not ok:
            return {"ok": False, "status": "copy_failed"}
        recent_recorded = self._record_recent_meme(meme_id)
        self._webui.schedule_hide()
        return {
            "ok": True,
            "status": "copied",
            "recent_recorded": recent_recorded,
        }

    def _record_recent_meme(self, meme_id: int) -> bool:
        """按用户设置记录一次成功使用；记录失败不反转已经完成的复制/发送。"""
        if not self._cfg.get("record_recent_use", True):
            return False
        try:
            self._db.record_use(int(meme_id))
            return True
        except Exception:
            logger.exception("failed to record recent meme use: %s", meme_id)
            return False

    def _daily_meme_path(self, row) -> str:
        finder = getattr(self._webui, "_find_daily_file", None)
        if callable(finder):
            try:
                path = finder(row, read_priority="critical")
                if path:
                    return path
            except Exception:
                pass
        with guarded_read(priority="critical"):
            finder = getattr(self._webui, "_find_meme_file_for_row", None)
            return (
                finder(row)
                if callable(finder)
                else self._find_meme_file(row["filename"])
            )

    def copy_meme_original(self, meme_id):
        """复制未经日常压缩处理的原件。"""
        row = self._db.get_by_id(meme_id)
        if not row:
            return {"ok": False, "status": "copy_failed"}
        with guarded_read(priority="critical"):
            path = self._webui._find_meme_file_for_row(row)
            if not path:
                return {"ok": False, "status": "copy_failed"}
            copied = copy_image_to_clipboard(path)
        if not copied:
            return {"ok": False, "status": "copy_failed"}
        recent_recorded = self._record_recent_meme(meme_id)
        self._webui.schedule_hide()
        return {
            "ok": True,
            "status": "copied_original",
            "recent_recorded": recent_recorded,
        }

    def toggle_favorite(self, meme_id: int) -> bool:
        return self._db.toggle_favorite(meme_id)

    def is_favorite(self, meme_id: int) -> bool:
        return self._db.is_favorite(meme_id)

    def rename_meme(self, meme_id: int, new_name: str) -> bool:
        if not new_name:
            return False
        try:
            row = self._db.get_by_id(meme_id)
            if not row:
                return False
            self._db.update_meme(meme_id, original_name=new_name)
            if not update_manifest_meme_name(row["filename"], new_name):
                request_manifest_rebuild()
            return True
        except Exception as e:
            logging.getLogger(__name__).error(f"rename error: {e}")
            return False

    def _delete_meme_files(self, meme_id) -> bool:
        """删除磁盘原件、日常副本、缩略图和缓存条目。"""
        import os

        row = self._db.get_by_id(meme_id)
        if not row:
            return False
        file_path = self._webui._find_meme_file_for_row(row)
        if file_path:
            try:
                os.remove(file_path)
            except Exception:
                pass
        daily_name = row.get("daily_filename", "") or ""
        if daily_name and not row.get("compressed_only"):
            daily_path = self._cfg.derivative_dir / daily_name
            try:
                daily_path.unlink()
            except OSError:
                pass
        thumb_dir = self._cfg.thumbnail_dir
        fhash = row.get("file_hash", "")
        if fhash:
            thumb = thumb_dir / f"{fhash}.webp"
            if thumb.exists():
                try:
                    thumb.unlink()
                except Exception:
                    pass
        for f in thumb_dir.glob(f"{meme_id}_*"):
            try:
                f.unlink()
            except Exception:
                pass
        if hasattr(self._webui, "_file_cache"):
            self._webui._file_cache.pop(row["filename"], None)
        return True

    def delete_meme(self, meme_id: int) -> bool:
        row = self._db.get_by_id(meme_id)
        if not row:
            return False
        if not self._delete_meme_files(meme_id):
            return False
        self._db.delete_meme(meme_id)
        if not remove_memes_from_manifest([row["filename"]]):
            request_manifest_rebuild()
        try:
            from .library_events import notify_changed

            notify_changed("structure")
        except Exception:
            pass
        return True

    def delete_memes(self, meme_ids: list) -> dict:
        """批量删除，返回 {ok, deleted}"""
        ids = list(dict.fromkeys(int(x) for x in (meme_ids or [])))
        filenames = []
        deleted = 0
        for mid in ids:
            row = self._db.get_by_id(mid)
            if self._delete_meme_files(mid):
                deleted += 1
                if row:
                    filenames.append(row["filename"])
        if deleted:
            self._db.delete_memes(ids)
            if not remove_memes_from_manifest(filenames):
                request_manifest_rebuild()
            try:
                from .library_events import notify_changed

                notify_changed("structure")
            except Exception:
                pass
        return {"ok": True, "deleted": deleted}

    # 递归获取分组及其所有子分组的 ID 列表
    def _get_collection_ids_recursive(self, collection_id):
        ids = [collection_id]
        children = self._db.get_child_collections(collection_id)
        for child in children:
            ids.extend(self._get_collection_ids_recursive(child["id"]))
        return ids

    # 构建嵌套分组树并统计各分组成员数
    def _build_collection_tree(self, parent_id=None, hidden_state=None):
        if hidden_state is None:
            hidden_state = self._hidden_collection_state()
        hidden_id, hidden_ids, excluded_ids = hidden_state
        raw = self._db.get_collections()
        result = []
        for cid, name, pid, _ in raw:
            if pid != parent_id:
                continue
            children = self._build_collection_tree(
                parent_id=cid, hidden_state=hidden_state
            )
            all_ids = self._get_collection_ids_recursive(cid)
            count_excluded_ids = [] if cid in hidden_ids else excluded_ids
            cnt = self._db.count_collection_content(
                all_ids, exclude_collection_ids=count_excluded_ids
            ) + len(self._cloud_filtered("", None, cid))
            item = {
                "id": cid,
                "name": name,
                "count": cnt,
                "system_hidden": cid == hidden_id,
            }
            if children:
                item["children"] = children
            result.append(item)
        return result

    # --- 单归属类别 ---

    def _category_filter_ids(self, category_id):
        """解析真实类别子树或仅匹配父类别的虚拟“未分类”入口。"""
        if category_id is None or category_id == -4:
            return None
        if category_id < -4:
            return [-category_id - 4]
        return self._get_category_ids_recursive(category_id)

    def _get_category_ids_recursive(self, category_id):
        ids = [int(category_id)]
        children = {}
        for cid, _name, parent_id, _order in self._db.get_categories():
            children.setdefault(parent_id, []).append(cid)
        cursor = 0
        while cursor < len(ids):
            ids.extend(children.get(ids[cursor], []))
            cursor += 1
        return ids

    def _build_category_tree(
        self, parent_id=None, excluded_ids=None, include_direct=False
    ):
        if excluded_ids is None:
            _hidden_id, _hidden_ids, excluded_ids = self._hidden_collection_state()
        raw = self._db.get_categories()
        result = []
        for cid, name, pid, _order in raw:
            if pid != parent_id:
                continue
            children = self._build_category_tree(
                cid, excluded_ids=excluded_ids, include_direct=include_direct
            )
            all_ids = self._get_category_ids_recursive(cid)
            item = {
                "id": cid,
                "name": name,
                "path": self._db.get_category_path(cid),
                "count": self._db.count_category_content(
                    all_ids, exclude_collection_ids=excluded_ids
                )
                + len(self._cloud_filtered("", None, None, cid)),
            }
            if children:
                if include_direct:
                    direct_id = -cid - 4
                    children.insert(
                        0,
                        {
                            "id": direct_id,
                            "name": "未分类",
                            "path": item["path"] + "/未分类",
                            "direct_category_id": cid,
                            "count": self._db.count_category_content(
                                [cid], exclude_collection_ids=excluded_ids
                            )
                            + len(self._cloud_filtered("", None, None, direct_id)),
                        },
                    )
                item["children"] = children
            result.append(item)
        return result

    def _build_category_options(self, include_direct=False):
        _hidden_id, _hidden_ids, excluded_ids = self._hidden_collection_state()
        result = [
            {
                "id": -1,
                "name": "全部",
                "path": "全部",
                "count": self._db.count(exclude_collection_ids=excluded_ids)
                + self._db.count_chat_records(exclude_collection_ids=excluded_ids)
                + len(self._cloud_filtered("", None, None)),
            }
        ]
        result.append(
            {
                "id": -4,
                "name": "未分类",
                "path": "未分类",
                "count": self._db.count_uncategorized_content(
                    exclude_collection_ids=excluded_ids
                )
                + len(self._cloud_filtered("", None, None, -4)),
            }
        )
        return result + self._build_category_tree(
            excluded_ids=excluded_ids, include_direct=include_direct
        )

    def get_categories(self) -> list:
        return self._build_category_options(
            include_direct=self._cfg.get("show_category_uncategorized", False) is True
        )

    def search_categories(self, keyword: str = "") -> list:
        kw = (keyword or "").strip().lower()
        out = []

        def walk(items, depth=0):
            for item in items:
                if item.get("id", 0) > 0:
                    if (
                        not kw
                        or kw in item["name"].lower()
                        or kw in item.get("path", "").lower()
                    ):
                        out.append(
                            {
                                "id": item["id"],
                                "name": item["name"],
                                "path": item.get("path", item["name"]),
                                "depth": depth,
                            }
                        )
                walk(item.get("children", []) or [], depth + 1)

        walk(self._build_category_tree())
        return out[:50]

    def create_category(self, name: str, parent_id=None) -> dict:
        try:
            parent = None if parent_id in (None, "", 0, "0") else int(parent_id)
            if parent is not None and not self._db.category_exists(parent):
                return {"ok": False, "error": "父类别不存在"}
            parts = [
                validate_category_name(part)
                for part in _split_hierarchy_path(name, "类别")
            ]
            created = False
            cid = parent
            for part in parts:
                existing = next(
                    (
                        row[0]
                        for row in self._db.get_categories()
                        if row[2] == cid and row[1].casefold() == part.casefold()
                    ),
                    None,
                )
                if existing is None:
                    created = True
                    cid = self._db.create_category(part, cid)
                else:
                    cid = existing
            try:
                category_dir(
                    (getattr(self, "_cfg", None) or get_config()).cache_dir,
                    self._db.get_category_path_names(cid),
                ).mkdir(parents=True, exist_ok=True)
            except OSError as directory_error:
                logger.warning("category directory create failed: %s", directory_error)
            if created:
                request_manifest_rebuild()
            return {
                "ok": True,
                "id": cid,
                "created": created,
                "path": self._db.get_category_path(cid),
                "categories": self._build_category_options(),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def create_subcategory(self, name: str, parent_id: int) -> dict:
        return self.create_category(name, parent_id)

    def rename_category(self, category_id: int, new_name: str) -> dict:
        try:
            category_id = int(category_id)
            old_parts = self._db.get_category_path_names(category_id)
            if not old_parts:
                return {"ok": False, "error": "类别不存在"}
            parent_id = self._db.get_category(category_id).get("parent_id")
            new_name = validate_category_name(new_name)
            parent_parts = self._db.get_category_path_names(parent_id)
            moved_dirs = self._webui._rename_category_directory(
                old_parts, parent_parts, new_name
            )
            try:
                self._db.rename_category(category_id, new_name)
            except Exception:
                if moved_dirs:
                    old_dir, new_dir = moved_dirs
                    if new_dir.exists() and not old_dir.exists():
                        os.replace(str(new_dir), str(old_dir))
                raise
            build_manifest()
            return {"ok": True, "categories": self._build_category_options()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @contextmanager
    def _move_category_storage(self, category_id, parent_id):
        """整目录移动类别；提交失败时恢复路径，不遍历或重读图片。"""
        root = self._cfg.cache_dir
        old_parts = self._db.get_category_path_names(category_id)
        parent_parts = self._db.get_category_path_names(parent_id)
        old_dir = category_dir(root, old_parts)
        new_dir = category_dir(root, parent_parts + [old_parts[-1]])
        for parts in (old_parts, parent_parts + [old_parts[-1]]):
            for length in range(1, len(parts) + 1):
                path = category_dir(root, parts[:length])
                if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                    raise ValueError("类别路径包含链接目录，不能安全移动")
        if new_dir.exists():
            raise ValueError("目标类别目录已存在，不能合并或覆盖")
        if not old_dir.exists():
            if self._db.count(
                category_id=self._get_category_ids_recursive(category_id)
            ):
                raise ValueError("类别原件目录不可用，请检查存储盘后重试")
            yield
            return
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(old_dir), str(new_dir))
        storage_dir = new_dir
        try:
            yield
        except Exception:
            try:
                os.replace(str(new_dir), str(old_dir))
                storage_dir = old_dir
            except OSError as rollback_error:
                logger.exception("category move directory rollback failed")
                raise OSError(
                    f"数据库提交失败且目录回滚失败，原件仍保存在：{new_dir}"
                ) from rollback_error
            raise
        finally:
            file_cache = getattr(self._webui, "_file_cache", None)
            if file_cache is not None:
                for filename, cached_path in list(file_cache.items()):
                    try:
                        relative = Path(cached_path).relative_to(old_dir)
                    except ValueError:
                        continue
                    file_cache[filename] = str(storage_dir / relative)

    def move_hierarchy_node(self, kind, node_id, parent_id=None) -> dict:
        """移动类别或分组的整棵子树，不改变成员归属。"""
        try:
            guard = _IMPORT_LOCK if kind == "category" else nullcontext()
            read_guard = (
                guarded_read(priority="critical")
                if kind == "category"
                else nullcontext()
            )
            with guard, read_guard:
                storage_move = (
                    self._move_category_storage(node_id, parent_id)
                    if kind == "category"
                    else None
                )
                result = self._db.move_hierarchy_node(
                    kind, node_id, parent_id, storage_move=storage_move
                )
            if result["changed"]:
                try:
                    request_manifest_rebuild()
                except Exception:
                    logger.exception("hierarchy moved but manifest request failed")
            return {"ok": True, **result}
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            logger.exception("hierarchy move failed")
            return {"ok": False, "error": "移动失败，未更改层级"}

    def get_hierarchy_delete_mode(self) -> str:
        return _normalize_hierarchy_delete_mode(
            self._cfg.get("hierarchy_delete_mode", "ask")
        )

    def set_hierarchy_delete_mode(self, mode: str) -> dict:
        normalized = _normalize_hierarchy_delete_mode(mode)
        if normalized != str(mode or "").strip().lower():
            return {"ok": False, "error": "删除策略无效"}
        self._cfg.set("hierarchy_delete_mode", normalized)
        self._cfg.save()
        return {"ok": True, "mode": normalized}

    def get_large_meme_import_mode(self) -> str:
        """返回超过 20 MiB 的普通媒体导入策略。"""
        return _normalize_large_meme_import_mode(
            self._cfg.get("large_meme_import_mode", "ask")
        )

    def set_large_meme_import_mode(self, mode: str) -> dict:
        """保存超过 20 MiB 的普通媒体导入策略。"""
        normalized = _normalize_large_meme_import_mode(mode)
        if normalized != str(mode or "").strip().lower():
            return {"ok": False, "error": "大文件导入策略无效"}
        self._cfg.set("large_meme_import_mode", normalized)
        self._cfg.save()
        return {"ok": True, "mode": normalized}

    def resolve_large_meme_import(self, token: str, mode: str, remember=False) -> dict:
        """按用户选择继续一次超过 20 MiB 的普通媒体导入。"""
        normalized = _normalize_large_meme_import_mode(mode)
        if normalized not in ("keep_original", "compressed_only"):
            return {"ok": False, "error": "请选择有效的大文件导入方式"}
        item = _pop_pending_large_import(token)
        if not item:
            return {"ok": False, "error": "决策已过期，请重新导入"}
        if remember:
            self._cfg.set("large_meme_import_mode", normalized)
            self._cfg.save()
        if item.get("kind") == "job":
            started = start_import_job(
                self._webui,
                item.get("paths") or [],
                item.get("names"),
                item.get("make_collection", False),
                item.get("folder_name", ""),
                normalized,
            )
            if not started:
                return {"ok": False, "error": "已有导入在进行中"}
            return {"ok": True, "async": True}
        try:
            path = (item.get("paths") or [""])[0]
            names = item.get("names") or []
            return self._webui._import_with_similar_decision(
                path,
                names[0] if names else "",
                large_file_mode=normalized,
            )
        finally:
            _cleanup_pending_large_item(item)

    def cancel_large_meme_import(self, token: str) -> dict:
        """取消并清理一次待决策的大文件导入。"""
        item = _pop_pending_large_import(token)
        if item:
            _cleanup_pending_large_item(item)
        return {"ok": True}

    def delete_category(self, category_id: int, mode: str = "promote") -> dict:
        moves = []
        db_committed = False
        promoted_directory_paths = []
        try:
            category_id = int(category_id)
            mode = str(mode or "").strip().lower()
            if mode not in ("promote", "cascade"):
                return {"ok": False, "error": "请选择有效的类别删除方式"}
            category = self._db.get_category(category_id)
            if not category:
                return {"ok": False, "error": "类别不存在"}
            parts = self._db.get_category_path_names(category_id)
            parent_parts = self._db.get_category_path_names(category.get("parent_id"))
            if mode == "promote":
                for descendant_id in self._db.get_category_descendants(category_id):
                    if descendant_id == category_id:
                        continue
                    descendant_parts = self._db.get_category_path_names(descendant_id)
                    relative_parts = descendant_parts[len(parts) :]
                    if relative_parts:
                        promoted_directory_paths.append(
                            [*parent_parts, *relative_parts]
                        )
            moves = self._webui._delete_category_directory(
                parts, parent_parts, mode=mode
            )
            result = self._db.delete_category(category_id, mode=mode)
            db_committed = True
            try:
                self._webui._cleanup_category_directory(parts)
            except OSError as cleanup_error:
                logger.warning("category directory cleanup failed: %s", cleanup_error)
            for promoted_parts in promoted_directory_paths:
                try:
                    category_dir(self._cfg.cache_dir, promoted_parts).mkdir(
                        parents=True, exist_ok=True
                    )
                except OSError as directory_error:
                    logger.warning(
                        "promoted category directory create failed: %s",
                        directory_error,
                    )
            request_manifest_rebuild()
            return {
                "ok": True,
                "mode": mode,
                "parent_id": result.get("parent_id"),
                "deleted_ids": result.get("deleted_ids", [category_id]),
                "categories": self._build_category_options(),
            }
        except Exception as e:
            if moves and not db_committed:
                try:
                    self._webui._restore_category_directory_moves(moves)
                except Exception:
                    logger.exception("category directory rollback failed")
            return {"ok": False, "error": str(e)}

    def get_collections(self) -> list:
        _hidden_id, _hidden_ids, excluded_ids = self._hidden_collection_state()
        top = self._build_collection_tree()
        sys_cols = [
            {
                "id": -4,
                "name": "未分组",
                "count": self._db.count_ungrouped_content(
                    exclude_collection_ids=excluded_ids
                )
                + len(self._cloud_filtered("", None, -4)),
            }
        ]
        sys_cols.extend(
            [
                {
                    "id": -2,
                    "name": "收藏夹",
                    "count": self._db.count(
                        favorite_only=True, exclude_collection_ids=excluded_ids
                    )
                    + len(self._cloud_filtered("", None, -2)),
                },
                {
                    "id": -3,
                    "name": "最近使用",
                    "count": self._db.count_recent(exclude_collection_ids=excluded_ids),
                },
                {
                    "id": -5,
                    "name": "随机",
                    "count": min(
                        MEME_PAGE,
                        self._db.count(exclude_collection_ids=excluded_ids),
                    ),
                },
            ]
        )
        return sys_cols + top

    def get_child_collections(self, parent_id: int) -> list:
        return self._db.get_child_collections(parent_id)

    def search_collections(self, keyword: str = "") -> list:
        """按名称搜索已有分组（顶层 + 子分组），供添加分组弹窗下拉框"""
        kw = (keyword or "").strip().lower()
        out = []
        for item in self._flatten_collections():
            if not kw or kw in item["name"].lower():
                out.append(
                    {"id": item["id"], "name": item["name"], "depth": item["depth"]}
                )
        return out[:20]

    def get_collection_members(self, collection_id: int) -> list:
        """返回分组内表情成员，供添加分组弹窗右侧栏展示"""
        try:
            return self._db.search(collection_id=collection_id, limit=5000) or []
        except Exception:
            return []

    def _flatten_collections(self) -> list:
        """展平分组树（含子分组），带 depth"""
        out = []

        def walk(items, depth):
            for c in items:
                if c.get("id", 0) > 0:
                    out.append({"id": c["id"], "name": c["name"], "depth": depth})
                for ch in c.get("children", []) or []:
                    walk([ch], depth + 1)

        walk(self._build_collection_tree(), 0)
        return out

    def add_to_collection(self, meme_id: int, name: str) -> bool:
        cid, _created = self._create_collection_path(name)
        if cid < 0:
            return False
        self._db.add_to_collection(meme_id, cid)
        return True

    def add_to_existing_collection(self, meme_id: int, collection_id: int) -> bool:
        try:
            self._db.add_to_collection(meme_id, collection_id)
            return True
        except Exception:
            return False

    def batch_add_to_collection(
        self, meme_ids: list, collection_id: int = 0, name: str = ""
    ) -> dict:
        """批量加入分组（追加语义）；collection_id<=0 时按 name 创建/复用顶层分组"""
        ids = list(dict.fromkeys(int(x) for x in (meme_ids or [])))
        try:
            cid = int(collection_id or 0)
            if cid <= 0:
                cid, _created = self._create_collection_path(name)
                if cid < 0:
                    return {"ok": False}
            added = self._db.add_memes_to_collection(ids, cid)
            if added:
                build_manifest()
            return {"ok": True, "added": added}
        except Exception:
            return {"ok": False}

    def batch_set_meme_category(self, meme_ids, category_id=None, name=""):
        """批量设置单归属类别并移动原件，提交前失败则回滚所有移动。"""
        moves = []
        committed = False
        try:
            ids = list(dict.fromkeys(int(value) for value in (meme_ids or [])))
            rows = [self._db.get_by_id(meme_id) for meme_id in ids]
            if not ids or any(row is None for row in rows):
                return {"ok": False, "error": "选中的表情包已不存在，请刷新后重试"}
            if name and category_id is None:
                created = self.create_category(name)
                if not created.get("ok"):
                    return created
                category_id = created["id"]
            else:
                category_id = (
                    None if category_id in (None, "", 0, "0") else int(category_id)
                )
            if category_id is not None and (
                category_id <= 0 or not self._db.category_exists(category_id)
            ):
                return {"ok": False, "error": "类别不存在"}
            changed = [row for row in rows if row.get("category_id") != category_id]
            for row in changed:
                old_path, new_path = self._webui._move_meme_file(
                    row, row["filename"], category_id
                )
                if old_path != new_path:
                    moves.append((row["filename"], old_path, new_path))
            self._db.set_memes_category([row["id"] for row in changed], category_id)
            committed = True
            file_cache = getattr(self._webui, "_file_cache", None)
            if isinstance(file_cache, dict):
                for filename, _old_path, new_path in moves:
                    file_cache[filename] = str(new_path)
            if changed:
                try:
                    request_manifest_rebuild()
                except Exception:
                    logger.exception("batch category manifest refresh failed")
            return {"ok": True, "count": len(rows), "category_id": category_id}
        except Exception as exc:
            logger.exception("batch category assignment failed")
            return {"ok": False, "error": str(exc) or "设置类别失败"}
        finally:
            if not committed:
                file_cache = getattr(getattr(self, "_webui", None), "_file_cache", None)
                for filename, old_path, new_path in reversed(moves):
                    try:
                        if old_path.exists():
                            raise OSError("回滚位置已存在文件")
                        old_path.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(new_path, old_path)
                        if isinstance(file_cache, dict):
                            file_cache[filename] = str(old_path)
                    except OSError:
                        logger.exception("failed to roll back batch category file")

    def batch_move_to_collection(
        self, meme_ids: list, from_id: int, to_id: int = 0, name: str = ""
    ) -> dict:
        """批量从 from 分组（含子分组）移动到 to 分组；to_id<=0 时按 name 建目标"""
        ids = list(dict.fromkeys(int(x) for x in (meme_ids or [])))
        try:
            # 分组视图递归展示子分组成员，移出时需从整棵子树移除才算真正"移走"
            from_ids = self._get_collection_ids_recursive(int(from_id))
            cid = int(to_id or 0)
            if cid <= 0:
                cid, _created = self._create_collection_path(name)
                if cid < 0:
                    return {"ok": False}
            # create_collection 会复用同名顶层分组（可能是源分组自身），解析后统一校验：
            # 移入源分组自身或其子分组 = 移出后又加回递归视图，等于没移动，拒绝
            if cid in from_ids:
                return {"ok": False, "error": "不能移动到当前分组自身或其子分组"}
            moved = self._db.move_memes_to_collection(ids, from_ids, cid)
            # moved==0 时成员关系仍可能变化（成员已在目标、仅从源移除），manifest 需重建
            if ids:
                build_manifest()
            return {"ok": True, "moved": moved}
        except Exception:
            return {"ok": False}

    def batch_add_tags(self, meme_ids: list, tags: list) -> dict:
        """批量合并追加标签（不清空各表情已有标签），返回 {ok, count}"""
        try:
            ids = list(dict.fromkeys(int(x) for x in (meme_ids or [])))
            count = self._db.add_tags_to_memes(ids, list(tags or []))
            build_manifest()
            return {"ok": True, "count": count}
        except Exception:
            return {"ok": False}

    def set_collection_members(self, collection_id: int, meme_ids: list) -> bool:
        """批量设置分组内成员（先清空再写入），供添加分组弹窗确定时保存右侧列表"""
        try:
            self._db.set_collection_members(collection_id, meme_ids)
            build_manifest()
            return True
        except Exception:
            return False

    def set_collection_members_new(self, name: str, meme_ids: list) -> dict:
        """创建新分组并批量设置成员，返回 {ok, id}"""
        try:
            cid, created = self._create_collection_path(name)
            if not created:
                return {"ok": False, "error": "同名分组已存在，请从下拉框选择已有分组"}
            if cid < 0:
                return {"ok": False}
            self._db.set_collection_members(cid, meme_ids)
            build_manifest()
            return {"ok": True, "id": cid}
        except Exception:
            return {"ok": False}

    def reorder_memes(self, meme_ids: list) -> bool:
        try:
            self._db.reorder_memes(meme_ids)
            build_manifest()
            return True
        except Exception:
            return False

    def reorder_collections(self, collection_ids: list) -> bool:
        try:
            self._db.reorder_collections(collection_ids)
            build_manifest()
            return True
        except Exception:
            return False

    def reorder_collection_members(self, collection_id: int, meme_ids: list) -> bool:
        try:
            self._db.reorder_collection_members(collection_id, meme_ids)
            build_manifest()
            return True
        except Exception:
            return False

    def delete_collection(self, collection_id: int, mode: str = "cascade") -> dict:
        try:
            collection_id = int(collection_id)
            if collection_id == -5:
                return {"ok": False, "error": "不能删除随机入口"}
            mode = str(mode or "").strip().lower()
            if mode not in ("promote", "cascade"):
                return {"ok": False, "error": "请选择有效的分组删除方式"}
            result = self._db.delete_collection(collection_id, mode=mode)
            request_manifest_rebuild()
            return {
                "ok": True,
                "mode": mode,
                "parent_id": result.get("parent_id"),
                "deleted_ids": result.get("deleted_ids", [collection_id]),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def rename_collection(self, collection_id: int, new_name: str) -> bool:
        if not new_name or collection_id == -5:
            return False
        try:
            self._db.rename_collection(collection_id, new_name)
            build_manifest()
            return True
        except Exception:
            return False

    def _create_collection_path(self, name, parent_id=None):
        """从指定父节点开始创建或复用一条分组路径。"""
        parts = _split_hierarchy_path(name, "分组")
        parent = None if parent_id in (None, "", 0, "0") else int(parent_id)
        if parent is not None and not any(
            row[0] == parent for row in self._db.get_collections()
        ):
            raise ValueError("父分组不存在")
        created = False
        cid = parent
        for part in parts:
            existed = self._db.collection_exists(part, cid)
            cid = self._db.create_collection(part, parent_id=cid)
            if cid < 0:
                raise ValueError("创建分组失败")
            created = created or not existed
        return cid, created

    def create_collection(self, name: str, parent_id=None) -> dict:
        """创建或复用分组路径，不立即修改任何内容的归属。"""
        try:
            cid, created = self._create_collection_path(name, parent_id)
            all_collections = self._db.get_all_collection_paths()
            item = next(
                (record for record in all_collections if record["id"] == cid),
                {"id": cid, "name": str(name), "path": str(name)},
            )
            if created:
                request_manifest_rebuild()
            return {
                "ok": True,
                "id": cid,
                "created": created,
                "collection": item,
                "all_collections": all_collections,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def create_subcollection(self, name: str, parent_id: int) -> dict:
        return self.create_collection(name, parent_id)

    def record_meme_use(self, meme_id: int) -> bool:
        """显式标记已有表情为最近使用；不受自动记录开关影响。"""
        try:
            meme_id = int(meme_id)
            if not self._db.get_by_id(meme_id):
                return False
            self._db.record_use(meme_id)
            return True
        except Exception:
            return False

    def record_meme_uses(self, meme_ids: list) -> dict:
        """批量显式标记已有表情，供重复导入定位使用。"""
        recorded = []
        seen = set()
        for value in meme_ids or []:
            try:
                meme_id = int(value)
            except (TypeError, ValueError):
                continue
            if meme_id in seen:
                continue
            seen.add(meme_id)
            if self.record_meme_use(meme_id):
                recorded.append(meme_id)
        return {"ok": bool(recorded), "ids": recorded}

    def remove_from_recent(self, meme_id: int) -> bool:
        try:
            self._db.remove_from_recent(meme_id)
            return True
        except Exception:
            return False

    def clear_recent(self) -> bool:
        try:
            self._db.clear_recent()
            return True
        except Exception:
            return False

    def remove_from_collection(self, meme_id: int, collection_id: int) -> bool:
        self._db.remove_from_collection(meme_id, collection_id)
        return True

    def add_to_chat_collection(self, record_id: int, collection_id: int) -> bool:
        try:
            self._db.add_to_chat_collection(record_id, collection_id)
            try:
                from .library_events import notify_changed

                notify_changed("structure")
            except Exception:
                pass
            return True
        except Exception:
            return False

    def assign_library_item(
        self, item_type: str, item_id: int, target_type: str, target_id: int
    ) -> dict:
        """把图片或聊天记录拖放到分组或类别。"""
        old_path = None
        new_path = None
        file_moved = False
        category_committed = False

        def rollback_moved_file():
            if (
                category_committed
                or not file_moved
                or old_path is None
                or new_path is None
            ):
                return
            try:
                if new_path.exists() and not old_path.exists():
                    old_path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(new_path, old_path)
            except OSError:
                logger.exception("failed to roll back dragged meme category")

        try:
            item_type = str(item_type or "")
            target_type = str(target_type or "")
            item_id = int(item_id)
            target_id = int(target_id)
            if item_type not in ("meme", "chat_record"):
                return {"ok": False, "error": "内容类型无效"}
            if target_type not in ("collection", "category"):
                return {"ok": False, "error": "目标类型无效"}

            def refresh_meme_manifest(row):
                try:
                    updated = update_manifest_meme_structure(item_id, row["filename"])
                except Exception:
                    logger.exception("drag assignment manifest update failed")
                    updated = False
                if not updated:
                    request_manifest_rebuild()

            if target_type == "collection":
                if target_id <= 0 or not any(
                    row[0] == target_id for row in self._db.get_collections()
                ):
                    return {"ok": False, "error": "只能拖到自定义分组"}
                if item_type == "meme":
                    row = self._db.get_by_id(item_id)
                    if not row:
                        return {"ok": False, "error": "表情包不存在"}
                    self._db.add_to_collection(item_id, target_id)
                    refresh_meme_manifest(row)
                else:
                    self._db.add_to_chat_collection(item_id, target_id)
                    request_manifest_rebuild()
                operation = "added"
            else:
                category_id = None if target_id == -4 else target_id
                if category_id is not None and (
                    category_id <= 0 or not self._db.category_exists(category_id)
                ):
                    return {"ok": False, "error": "类别不存在"}
                if item_type == "chat_record":
                    if not self._db.set_chat_category(item_id, category_id):
                        return {"ok": False, "error": "聊天记录不存在"}
                    request_manifest_rebuild()
                else:
                    row = self._db.get_by_id(item_id)
                    if not row:
                        return {"ok": False, "error": "表情包不存在"}
                    if row.get("category_id") != category_id:
                        old_path, new_path = self._webui._move_meme_file(
                            row, row["filename"], category_id
                        )
                        file_moved = old_path != new_path
                        if not self._db.set_meme_category(item_id, category_id):
                            raise ValueError("表情包不存在")
                        category_committed = True
                        if file_moved:
                            file_cache = getattr(self._webui, "_file_cache", None)
                            if isinstance(file_cache, dict):
                                file_cache[row["filename"]] = str(new_path)
                    refresh_meme_manifest(row)
                operation = "moved"

            try:
                from .library_events import notify_changed

                notify_changed("structure")
            except Exception:
                pass
            return {"ok": True, "operation": operation}
        except (OSError, ValueError) as exc:
            rollback_moved_file()
            return {"ok": False, "error": str(exc)}
        except Exception:
            rollback_moved_file()
            logger.exception("assign library item failed")
            return {"ok": False, "error": "修改归属失败"}

    def remove_from_chat_collection(self, record_id: int, collection_id: int) -> bool:
        try:
            self._db.remove_from_chat_collection(record_id, collection_id)
            try:
                from .library_events import notify_changed

                notify_changed("structure")
            except Exception:
                pass
            return True
        except Exception:
            return False

    def log(self, msg, level="info"):
        """供前端输出调试日志到终端"""
        getattr(logger, level, logger.info)(msg)

    def rescan_cache(self) -> bool:
        """启动单例后台扫描；不要让 pywebview RPC 持续等待整库读取。"""
        return self._webui.start_cache_scan()

    # 非阻塞检查更新：新鲜缓存即返，首次/过期/force 触发后台检查返回 pending
    def check_update(self, debug=False, force=False) -> dict:
        info = updater.update_info(force=bool(debug) or bool(force))
        if debug or self._webui._update_debug:
            info["has_update"] = True
        return info

    def open_url(self, url: str) -> bool:
        """用系统默认浏览器打开外链（更新跳商店/关于页），仅允许 http(s)"""
        from urllib.parse import urlparse

        if urlparse(url or "").scheme not in ("http", "https"):
            return False
        try:
            if platform.system() == "Windows":
                os.startfile(url)
            elif platform.system() == "Darwin":
                import subprocess

                subprocess.Popen(["open", url])
            else:
                import subprocess

                subprocess.Popen(["xdg-open", url])
            return True
        except Exception:
            return False

    def start_download(self, url: str) -> bool:
        return updater.start_download(url)

    def get_download_progress(self) -> dict:
        return updater.get_download_progress()

    def run_downloaded_installer(self) -> bool:
        path = updater.last_downloaded_path()
        ok = updater.run_downloaded_installer()
        if ok:
            self._webui._schedule_quit(path)
        return ok

    def rerun_installer(self) -> bool:
        """重新运行已下载的安装包（启动失败后不必重新下载）"""
        path = updater.last_downloaded_path()
        if not path:
            return False
        ok = updater.run_installer(path)
        if ok:
            self._webui._schedule_quit(path)
        return ok

    def download_update(self, url: str) -> dict:
        """同步下载（旧版，保留兼容）"""
        path = updater.download_release(url)
        if not path:
            return {"ok": False, "error": "download failed"}
        ok = updater.run_installer(path)
        return {"ok": ok, "error": "" if ok else "run installer failed"}

    def check_connectivity(self) -> dict:
        return _check_connectivity()

    def download_original_image(self, url: str) -> dict:
        """下载浏览器来源的原始图片，或导入本地文件"""
        s = url.strip()
        # 本地文件：file:// URI 或裸绝对路径
        local_path = None
        if s.startswith("file://"):
            from urllib.parse import unquote, urlparse

            local_path = unquote(urlparse(s).path)
            # Windows: file:///C:/path → /C:/path → 去掉前导 /
            if len(local_path) > 3 and local_path[2] == ":":
                local_path = local_path.lstrip("/")
        elif s.startswith("/") and os.path.isfile(s):
            local_path = s
        elif len(s) > 2 and s[1] == ":" and os.path.isfile(s):
            # Windows 裸路径: C:\Users\...
            local_path = s
        if local_path:
            return self._import_with_similar_decision(
                local_path, os.path.basename(local_path)
            )

        clean_url = _strip_url_modifiers(s)

        # 网络图片原图下载
        if not self._cfg.get("try_original_image", False):
            return {"ok": False, "error": "功能未启用"}
        conn = _check_connectivity()
        if not conn["ok"]:
            return {"ok": False, "error": "无网络连接"}
        import tempfile
        from urllib.error import URLError
        from urllib.parse import urlparse
        from urllib.request import urlopen

        # 从 URL 路径推断扩展名
        parsed_path = urlparse(clean_url).path
        _, ext = os.path.splitext(parsed_path)
        ext = ext.lower() if ext else ""
        allowed_img_ext = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

        # URL 路径无扩展名时，从响应 Content-Type 推断
        need_type = not ext or ext not in allowed_img_ext

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".download")
        tmp_path = tmp.name
        tmp.close()
        try:
            with urlopen(clean_url, timeout=15) as resp:
                length = resp.headers.get("Content-Length", "")
                if length and int(length) > _IMPORT_MAX_BYTES:
                    return {
                        "ok": False,
                        "error": "图片超过 %d MiB 原件上限"
                        % (_IMPORT_MAX_BYTES // (1024 * 1024)),
                    }
                if need_type:
                    ct = resp.headers.get("Content-Type", "")
                    content_type = ct.split(";")[0].strip()
                    type_map = {
                        "image/gif": ".gif",
                        "image/png": ".png",
                        "image/jpeg": ".jpg",
                        "image/webp": ".webp",
                        "image/bmp": ".bmp",
                    }
                    ext = type_map.get(content_type, ".png")
                total = 0
                with open(tmp_path, "wb") as f:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > _IMPORT_MAX_BYTES:
                            return {
                                "ok": False,
                                "error": "图片超过 %d MiB 原件上限"
                                % (_IMPORT_MAX_BYTES // (1024 * 1024)),
                            }
                        f.write(chunk)
            # 重命名为正确扩展名
            final_path = tmp_path + ext
            os.rename(tmp_path, final_path)
            r = self._import_with_similar_decision(
                final_path, os.path.splitext(os.path.basename(clean_url))[0] or None
            )
            return r
        except URLError as e:
            return {"ok": False, "error": f"下载失败: {e.reason}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            try:
                os.unlink(tmp_path + ext)
            except Exception:
                pass

    def _import_with_similar_decision(self, path, oname=""):
        """单图导入决策：哈希命中提示已存在；内容近似转相似（委托 WebUI 统一逻辑）"""
        return self._webui._import_with_similar_decision(path, oname)

    def resolve_similar_import(self, token: str, action: str) -> dict:
        """响应用户对相似图的导入决策（action: discard/keep_old/keep_new/keep_both）"""
        item = _pop_pending_similar(token)
        if not item:
            return {"ok": False, "error": "决策已过期，请重新导入"}
        path = item["path"]
        action = action or "keep_new"
        cands = item.get("candidates") or []
        best_id = (
            min(cands, key=lambda c: c.get("distance", 99))["id"] if cands else None
        )
        if action in ("discard", "keep_old"):
            # 丢弃/仅保留旧图：删掉待决策临时文件，不导入
            try:
                os.unlink(path)
            except OSError:
                pass
            return {
                "ok": True,
                "action": action,
                "imported": False,
                "existing_id": best_id if action == "keep_old" else 0,
            }
        # 取最相似候选（distance 最小）作为"旧图"，用于 keep_new 替换
        try:
            r = self._webui._do_import(
                [path],
                [item.get("oname")] if item.get("oname") else None,
                read_priority="critical",
                large_file_mode=item.get("large_file_mode"),
            )
        except Exception as e:
            logger.error("resolve similar import error: %s", e)
            return {"ok": False, "error": "导入失败"}
        finally:
            try:
                os.unlink(path)  # 清理待决策临时文件
            except OSError:
                pass
        ids = r.get("ids") or []
        if ids:
            replaced = False
            if action == "keep_new" and best_id:
                # keep_new：导入新图并替换旧图——完整删除链（文件+缩略图+缓存+DB）
                try:
                    replaced = self.delete_meme(best_id)
                except Exception as e:
                    logger.error("keep_new 替换旧图失败: %s", e)
            return {
                "ok": True,
                "id": ids[0],
                "imported": True,
                "replaced": replaced,
            }
        if r.get("rejected"):
            return {"ok": False, "error": "文件超过大小/分辨率限制，已跳过"}
        return {"ok": False, "error": "导入失败"}

    def get_sync_progress(self) -> dict:
        return sync_module.get_sync_progress()

    def get_sync_status(self, force: bool = False) -> dict:
        """顶栏同步状态：是否配置云端、清单差集、后台传输进度（force 重拉清单）"""
        try:
            return _sync_status_snapshot(bool(force))
        except Exception as e:
            logger.warning("get_sync_status error: %s", e)
            return {
                "configured": bool(get_config().get("sync_type", "")),
                "manifest_state": "error",
                "error": str(e),
                "transfer": {"active": False, "status": "idle"},
            }

    def sync_push(self) -> dict:
        try:
            self._webui.ensure_local_thumbs()
            r = sync_module.push()
            r["ok"] = True
            return r
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "failed_files": sync_module.get_sync_progress().get("failed_items", []),
            }
        finally:
            _sync_status_invalidate()

    def sync_pull(self) -> dict:
        try:
            r = sync_module.pull()
            r["ok"] = True
            return r
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "failed_files": sync_module.get_sync_progress().get("failed_items", []),
            }
        finally:
            _sync_status_invalidate()

    def run_auto_sync(self) -> dict:
        """启动时自动同步：根据配置拉取远端索引和/或全量同步"""
        result = {"fetched": False, "synced": False, "error": ""}
        sync_type = self._cfg.get("sync_type", "")
        if not sync_type:
            return result
        fetched = None
        try:
            if self._cfg.get("sync_auto_fetch_index", False):
                from .sync import download_index

                fetched = download_index()
                result["fetched"] = fetched is not None
                if isinstance(fetched, dict):
                    _sync_status_set_manifest(fetched)
            if self._cfg.get("cloud_direct", False):
                # 云端直接使用：复用本次 fetch 结果（fetch 关闭则线程内自拉）
                _start_cloud_refresh(fetched)
                if self._cfg.get("cloud_thumb_auto_push", True):
                    # 启动静默补传：生成本地缺失缩略图并差集上传云端
                    _start_thumb_autopush(self._auto_push_thumbs)
            if self._cfg.get("sync_auto_sync", False):
                from .sync import pull

                r = pull()
                result["synced"] = r.get("downloaded", 0) > 0
        except Exception as e:
            result["error"] = str(e)
        return result

    def _auto_push_thumbs(self):
        """启动静默补传：本地缺失缩略图先补齐，再差集上传云端"""
        if not self._cfg.get("cloud_thumb_auto_push", True):
            return
        self._webui.ensure_local_thumbs()
        n = sync_module.auto_push_thumbs()
        if n:
            logger.info("cloud thumb auto-push: uploaded %d", n)

    def cloud_refresh(self) -> bool:
        """手动触发云端清单刷新与缩略图预取（刷新按钮；开关/refreshing 中去重）"""
        return _start_cloud_refresh()

    def cloud_download(self, filename: str) -> dict:
        """下载云端缺失表情：校验→去重入库→自动复制；标签/分组/收藏异步后补"""
        import hashlib

        if not self._cfg.get("cloud_direct", False):
            return {"ok": False, "status": "disabled"}
        if not self._cfg.get("sync_type", ""):
            return {"ok": False, "status": "no_sync"}
        entry = next(
            (it for it in self._cloud_base() if it["filename"] == filename), None
        )
        if entry is None:
            return {"ok": False, "status": "not_found"}
        with _cloud_inflight_lock:
            if filename in _cloud_inflight:
                return {"ok": False, "status": "busy"}
            _cloud_inflight.add(filename)
        cfg = self._cfg
        sha = entry["sha256"]
        ext = os.path.splitext(filename)[1].lower() or ".png"
        tmp = None
        try:
            fd, tmp_name = tempfile.mkstemp(prefix="cloud-", suffix=ext)
            os.close(fd)
            tmp = Path(tmp_name)
            remote_root = sync_module._remote_root(cfg).rstrip("/")
            category_parts = sync_module._safe_category_parts(
                entry.get("category_path")
            )
            relative_path = "/".join([*category_parts, filename])
            remote_path = f"{remote_root}/{sync_module.REMOTE_MEME_DIR}/{relative_path}"
            if not sync_module.download_single(remote_path, tmp):
                return {"ok": False, "status": "download_failed"}
            h = hashlib.sha256()
            with open(tmp, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            if h.hexdigest() != sha:
                return {"ok": False, "status": "sha_mismatch"}
            fsize = tmp.stat().st_size
            if fsize > _IMPORT_MAX_BYTES:
                return {"ok": False, "status": "too_large"}
            w = hgt = 0
            if HAS_PIL:
                try:
                    with PILImage.open(tmp) as img:
                        w, hgt = img.size
                except Exception:
                    return {"ok": False, "status": "invalid_image"}
                max_px = get_import_max_px(cfg)
                if max_px and max(w, hgt) > max_px:
                    return {"ok": False, "status": "too_large"}
            src_phash = _perceptual_hash_path(tmp)
            with _IMPORT_LOCK:
                existing = self._db.get_by_hash(sha)
                if existing:
                    meme_id = existing["id"]
                    try:
                        tmp.unlink()
                        tmp = None
                    except OSError:
                        pass
                else:
                    category_id = sync_module._ensure_db_category_path(
                        self._db, category_parts
                    )
                    dst = sync_module._entry_local_path(cfg.cache_dir, entry)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if dst.exists():
                        return {"ok": False, "status": "file_exists"}
                    with guarded_read(priority="critical"):
                        shutil.move(str(tmp), str(dst))
                    tmp = None
                    meme_id = self._db.add_meme(
                        filename=filename,
                        file_hash=sha,
                        width=w,
                        height=hgt,
                        file_size=fsize,
                        mime_type=f"image/{ext[1:]}",
                        original_name=entry["name"],
                        perceptual_hash=src_phash,
                        category_id=category_id,
                        source_file_hash=entry.get("source_sha256") or "",
                        compressed_only=int(bool(entry.get("compressed_only"))),
                    )
            with _cloud_lock:
                _cloud_state["missing"] = [
                    m for m in _cloud_state["missing"] if m["filename"] != filename
                ]
            threading.Thread(
                target=self._cloud_backfill, args=(meme_id, entry), daemon=True
            ).start()
            copied = False
            try:
                copied = bool(self.copy_meme(meme_id).get("ok"))
            except Exception as e:
                logger.warning(f"cloud copy failed: {e}")
            return {
                "ok": True,
                "status": "copied" if copied else "imported",
                "id": meme_id,
            }
        finally:
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            with _cloud_inflight_lock:
                _cloud_inflight.discard(filename)

    def _cloud_backfill(self, meme_id, entry):
        """异步补齐云端元数据：标签/分组/收藏 + 重建 manifest + 通知前端刷新"""
        try:
            records = entry.get("tag_records")
            tags = records if isinstance(records, list) else entry.get("tags") or []
            if tags:
                self._db.add_tags_to_memes([meme_id], tags)
            for path in entry.get("collections") or []:
                cid = None
                for seg in str(path).split("/"):
                    if not seg:
                        continue
                    cid = self._db.create_collection(seg, parent_id=cid)
                    if cid is None or cid < 0:
                        break
                if cid and cid > 0:
                    self._db.add_to_collection(meme_id, cid)
            if entry.get("favorited"):
                self._db.add_favorite(meme_id)
            build_manifest()
            _notify_cloud_ready()
        except Exception as e:
            logger.warning(f"cloud backfill failed: {e}")

    def sync_test(self) -> str:
        try:
            from .sync import sync_test as _test

            return _test()
        except Exception as e:
            return str(e)

    def import_memes(self) -> bool:
        # 通过系统文件对话框选择导入（后台执行，避免大数量导入阻塞界面）
        result = self._dialog(
            webview.FileDialog.OPEN,
            allow_multiple=True,
            file_types=("图片文件 (*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp)",),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        paths = list(result)
        large_mode = _normalize_large_meme_import_mode(
            self._cfg.get("large_meme_import_mode", "ask")
        )
        large_count = _large_import_count(paths)
        if large_mode == "ask" and large_count:
            token = _register_pending_large_import(paths, kind="job")
            return {
                "ok": False,
                "large_file_pending": True,
                "token": token,
                "count": large_count,
                "threshold_mb": _LARGE_MEME_IMPORT_BYTES // (1024 * 1024),
            }
        if not start_import_job(self._webui, paths, None, False, "", large_mode):
            return {"ok": False, "error": "已有导入在进行中"}
        return {"ok": True, "async": True}

    def import_folder(self, make_collection=True) -> dict:
        """选择文件夹并导入其中全部图片；make_collection 时以文件夹名创建分组"""
        result = self._dialog(webview.FileDialog.FOLDER)
        if not result:
            return {"ok": False, "cancelled": True}
        folder = result[0] if isinstance(result, (tuple, list)) else result
        try:
            files = []
            names = []
            for root, _, fnames in os.walk(folder):
                for fn in sorted(fnames):
                    ext = os.path.splitext(fn)[1].lower()
                    if ext not in _ALLOWED_IMPORT_EXT:
                        continue
                    files.append(os.path.join(root, fn))
                    names.append(os.path.splitext(fn)[0])
            if not files:
                return {"ok": False, "error": "文件夹中没有支持的图片"}
            folder_name = os.path.basename(os.path.normpath(folder))
            large_mode = _normalize_large_meme_import_mode(
                self._cfg.get("large_meme_import_mode", "ask")
            )
            large_count = _large_import_count(files)
            if large_mode == "ask" and large_count:
                token = _register_pending_large_import(
                    files,
                    names,
                    make_collection,
                    folder_name,
                    kind="job",
                )
                return {
                    "ok": False,
                    "large_file_pending": True,
                    "token": token,
                    "count": large_count,
                    "threshold_mb": _LARGE_MEME_IMPORT_BYTES // (1024 * 1024),
                }
            if not start_import_job(
                self._webui,
                files,
                names,
                make_collection,
                folder_name,
                large_mode,
            ):
                return {"ok": False, "error": "已有导入在进行中"}
            return {"ok": True, "async": True, "total": len(files)}
        except Exception as e:
            logger.error(f"import_folder failed: {e}")
            return {"ok": False, "error": str(e)}

    def get_import_progress(self):
        # 后台导入进度快照（前端轮询用）
        return get_import_progress()

    def cancel_import_job(self):
        # 请求取消当前后台导入
        return cancel_import_job()

    def import_from_clipboard(self) -> dict:
        import hashlib
        import tempfile

        from PIL import ImageGrab

        try:
            clip = ImageGrab.grabclipboard()
        except Exception:
            return {"ok": False, "error": "读取剪贴板失败"}
        if clip is None:
            return {"ok": False, "error": "剪贴板中没有图片"}
        try:
            if isinstance(clip, list):
                paths = [p for p in clip if os.path.isfile(p)]
                if not paths:
                    return {"ok": False, "error": "剪贴板中没有图片文件"}
                large_mode = _normalize_large_meme_import_mode(
                    self._cfg.get("large_meme_import_mode", "ask")
                )
                large_count = _large_import_count(paths)
                if large_mode == "ask" and large_count:
                    token = _register_pending_large_import(paths, kind="job")
                    return {
                        "ok": False,
                        "large_file_pending": True,
                        "token": token,
                        "count": large_count,
                        "threshold_mb": _LARGE_MEME_IMPORT_BYTES // (1024 * 1024),
                    }
                r = self._webui._do_import(paths, large_file_mode=large_mode)
                ids = r.get("ids") or []
                duplicate_ids = r.get("duplicate_ids") or []
                rejected = r.get("rejected", 0)
                if ids:
                    row = self._db.get_by_id(ids[0])
                    orig = row["original_name"] if row else ""
                    return {
                        "ok": True,
                        "id": ids[0],
                        "name": orig or "未命名",
                        "rejected": rejected,
                        "duplicate_ids": duplicate_ids,
                    }
                return {
                    "ok": True,
                    "id": 0,
                    "name": "未命名",
                    "rejected": rejected,
                    "duplicate": bool(duplicate_ids),
                    "duplicate_ids": duplicate_ids,
                    "existing_id": duplicate_ids[0] if duplicate_ids else 0,
                }
            img = clip
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp_path = tmp.name
            tmp.close()
            try:
                img.save(tmp_path, "PNG")
                sha256 = hashlib.sha256()
                with open(tmp_path, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        sha256.update(chunk)
                existing = self._db.get_by_hash(sha256.hexdigest())
                if existing:
                    return {
                        "ok": False,
                        "duplicate": True,
                        "existing_id": existing["id"],
                        "duplicate_ids": [existing["id"]],
                        "error": "该图片已存在",
                    }
                r = self._webui._do_import([tmp_path], [""])
                ids = r.get("ids") or []
                duplicate_ids = r.get("duplicate_ids") or []
                rejected = r.get("rejected", 0)
                if ids:
                    row = self._db.get_by_id(ids[0])
                    orig = row["original_name"] if row else ""
                    return {
                        "ok": True,
                        "id": ids[0],
                        "name": orig or "未命名",
                        "rejected": rejected,
                        "duplicate_ids": duplicate_ids,
                    }
                return {
                    "ok": True,
                    "id": 0,
                    "name": "未命名",
                    "rejected": rejected,
                    "duplicate": bool(duplicate_ids),
                    "duplicate_ids": duplicate_ids,
                    "existing_id": duplicate_ids[0] if duplicate_ids else 0,
                }
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def open_settings(self):
        return self._webui.open_settings()

    def lan_confirm_device(self, approved: bool) -> dict:
        from . import lan

        lan.confirm_device(bool(approved))
        return {"ok": True}

    def plugin_call(self, plugin_id: str, method: str, args=None) -> dict:
        """分发主窗口插件方法（manifest main_api 声明的逻辑）"""
        return get_plugin_manager().call_main(str(plugin_id), str(method), args or [])

    def plugin_button_click(self, plugin_id: str, key: str) -> dict:
        """主窗口顶栏插件按钮点击分发"""
        return get_plugin_manager().call_button(str(plugin_id), str(key))

    def get_settings(self) -> dict:
        d = self._cfg.to_dict()
        from .platform_util import is_auto_start_enabled

        return {
            "theme": d.get("theme", "dark"),
            "hotkey": d.get("hotkey", "Ctrl+Alt+N"),
            "hotkey_show_at_mouse": d.get("hotkey_show_at_mouse", False),
            "auto_play_gif": d.get("auto_play_gif", True),
            "try_original_image": d.get("try_original_image", False),
            "auto_start": is_auto_start_enabled(),
            "silent_start": d.get("silent_start", False),
            "sync_auto_fetch_index": d.get("sync_auto_fetch_index", False),
            "sync_auto_sync": d.get("sync_auto_sync", False),
            "sync_type": d.get("sync_type", ""),
            "sync_delete_remote": d.get("sync_delete_remote", False),
            "sync_remove_local": d.get("sync_remove_local", False),
            "sync_hide_upload_warning": d.get("sync_hide_upload_warning", False),
            "ftp_host": d.get("ftp_host", ""),
            "ftp_port": d.get("ftp_port", 21),
            "ftp_user": d.get("ftp_user", ""),
            "ftp_password": d.get("ftp_password", ""),
            "ftp_path": d.get("ftp_path", "/"),
            "s3_endpoint": d.get("s3_endpoint", ""),
            "s3_region": d.get("s3_region", ""),
            "s3_bucket": d.get("s3_bucket", ""),
            "s3_access_key": d.get("s3_access_key", ""),
            "s3_secret_key": d.get("s3_secret_key", ""),
            "s3_path": d.get("s3_path", ""),
            "r2_account_id": d.get("r2_account_id", ""),
            "r2_access_key_id": d.get("r2_access_key_id", ""),
            "r2_secret_access_key": d.get("r2_secret_access_key", ""),
            "r2_bucket": d.get("r2_bucket", ""),
            "r2_path": d.get("r2_path", ""),
            "webdav_url": d.get("webdav_url", ""),
            "webdav_user": d.get("webdav_user", ""),
            "webdav_password": d.get("webdav_password", ""),
            "webdav_path": d.get("webdav_path", ""),
            "webdav_allow_insecure": d.get("webdav_allow_insecure", False),
            "show_upload_progress": d.get("show_upload_progress", True),
            "show_upload_done": d.get("show_upload_done", True),
            "show_download_progress": d.get("show_download_progress", True),
            "show_download_done": d.get("show_download_done", True),
            "show_uncategorized": d.get("show_uncategorized", True),
            "show_category_uncategorized": d.get("show_category_uncategorized") is True,
            "hidden_collection_enabled": d.get("hidden_collection_enabled", True),
            "hierarchy_delete_mode": _normalize_hierarchy_delete_mode(
                d.get("hierarchy_delete_mode", "ask")
            ),
            "large_meme_import_mode": _normalize_large_meme_import_mode(
                d.get("large_meme_import_mode", "ask")
            ),
            "import_resolution_limit_enabled": d.get("import_resolution_limit_enabled")
            is True,
            "import_resolution_limit_px": normalize_import_max_px(
                d.get("import_resolution_limit_px", _IMPORT_MAX_PX)
            ),
            "record_recent_use": d.get("record_recent_use", True),
            "search_description": d.get("search_description", True),
            "search_image_text": d.get("search_image_text", True),
            "show_startup_animation": d.get("show_startup_animation", True),
            "qq_drag_cleanup_on_start": d.get("qq_drag_cleanup_on_start", True),
            "virtual_drive_compat": d.get("virtual_drive_compat", False),
            "bulk_read_protection": d.get("bulk_read_protection", False),
            "bulk_read_batch_size": clamp_batch_size(d.get("bulk_read_batch_size", 8)),
            "bulk_read_pause_seconds": clamp_pause_seconds(
                d.get("bulk_read_pause_seconds", 1.0)
            ),
            "bulk_read_prefetch_pages": d.get("bulk_read_prefetch_pages", False),
            "notion_token": d.get("notion_token", ""),
            "notion_database_id": d.get("notion_database_id", ""),
            "notion_data_source_id": d.get("notion_data_source_id", ""),
            "notion_media_property": d.get("notion_media_property", ""),
            "notion_title_property": d.get("notion_title_property", ""),
            "notion_tags_property": d.get("notion_tags_property", ""),
            "notion_tag_mappings": d.get("notion_tag_mappings", []),
            "notion_collection_property": d.get("notion_collection_property", ""),
            "notion_version": d.get("notion_version", "2026-03-11"),
            "hover_zoom": d.get("hover_zoom", True),
            "copy_avoid_webp": d.get("copy_avoid_webp", False),
            "disable_auto_hide": d.get("disable_auto_hide", False),
            "window_resize_enabled": d.get("window_resize_enabled", False),
        }

    def notion_test_connection(self, settings) -> dict:
        """测试 Notion 连接并返回数据库属性。"""
        try:
            return notion_import.test_notion_connection(settings)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def start_notion_import(self, settings) -> dict:
        """启动主窗口中的 Notion 数据库只读导入。"""
        return notion_import.start_notion_import(self._webui, settings)

    def get_notion_import_progress(self) -> dict:
        """获取 Notion 导入进度。"""
        return notion_import.get_notion_progress()

    def cancel_notion_import(self):
        """取消 Notion 导入。"""
        notion_import.cancel_notion_import()

    def save_sort_mode(self, mode) -> dict:
        """保存主窗口排序模式（_SORT_MODES 之一）"""
        if mode not in _SORT_MODES:
            return {"ok": False, "error": "invalid sort mode"}
        self._cfg.set("sort_mode", mode)
        self._cfg.save()
        return {"ok": True}

    def save_settings(self, settings: dict):
        if isinstance(settings, dict):
            settings = dict(settings)
            if "hidden_collection_enabled" in settings:
                settings["hidden_collection_enabled"] = (
                    settings["hidden_collection_enabled"] is not False
                )
            if "hierarchy_delete_mode" in settings:
                settings["hierarchy_delete_mode"] = _normalize_hierarchy_delete_mode(
                    settings["hierarchy_delete_mode"]
                )
            if "large_meme_import_mode" in settings:
                settings["large_meme_import_mode"] = _normalize_large_meme_import_mode(
                    settings["large_meme_import_mode"]
                )
            settings = _normalize_bulk_read_settings(settings)
            if "auto_start" in settings:
                from .platform_util import set_auto_start

                set_auto_start(settings["auto_start"])
            prev_cloud = bool(self._cfg.get("cloud_direct", False))
            prev_sync_type = self._cfg.get("sync_type", "")
            self._cfg.update_from_dict(settings)
            self._cfg.save()
            if "cloud_direct" in settings or "sync_type" in settings:
                cloud = bool(self._cfg.get("cloud_direct", False))
                sync_changed = (
                    "sync_type" in settings
                    and settings.get("sync_type", "") != prev_sync_type
                )
                if cloud != prev_cloud or sync_changed:
                    _cloud_reset()
                    if cloud:
                        _start_cloud_refresh()
            if "hotkey" in settings:
                self._webui._on_hotkey_change(settings["hotkey"])

    def reset_settings(self) -> dict:
        prev_cache_dir = self._cfg.get("cache_dir", "")
        prev_virtual_drive_compat = self._cfg.get("virtual_drive_compat", False) is True
        self._cfg.reset()
        if prev_cache_dir:
            self._cfg.set("cache_dir", prev_cache_dir)
            self._cfg.set("virtual_drive_compat", prev_virtual_drive_compat)
        self._cfg.save()
        hotkey = self._cfg.get("hotkey", "Ctrl+Alt+N")
        self._webui._on_hotkey_change(hotkey)
        from .platform_util import set_auto_start

        set_auto_start(False)
        _cloud_reset()
        return {
            "theme": self._cfg.get("theme", "dark"),
            "hotkey": hotkey,
            "hotkey_show_at_mouse": self._cfg.get("hotkey_show_at_mouse", False),
            "auto_play_gif": self._cfg.get("auto_play_gif", True),
            "copy_resize_mode": self._cfg.get("copy_resize_mode", 1),
            "auto_start": False,
            "silent_start": False,
            "show_category_uncategorized": False,
            "hidden_collection_enabled": True,
            "sync_auto_fetch_index": False,
            "sync_auto_sync": False,
            "sync_type": "",
            "sync_delete_remote": False,
            "sync_remove_local": False,
            "sync_hide_upload_warning": False,
            "ftp_host": "",
            "ftp_port": 21,
            "ftp_user": "",
            "ftp_password": "",
            "ftp_path": "/",
            "s3_endpoint": "",
            "s3_region": "",
            "s3_bucket": "",
            "s3_access_key": "",
            "s3_secret_key": "",
            "s3_path": "",
            "r2_account_id": "",
            "r2_access_key_id": "",
            "r2_secret_access_key": "",
            "r2_bucket": "",
            "r2_path": "",
            "webdav_url": "",
            "webdav_user": "",
            "webdav_password": "",
            "webdav_path": "",
            "webdav_allow_insecure": False,
            "show_upload_progress": True,
            "show_upload_done": True,
            "show_download_progress": True,
            "show_download_done": True,
            "hierarchy_delete_mode": "ask",
            "large_meme_import_mode": "ask",
            "import_resolution_limit_enabled": False,
            "import_resolution_limit_px": _IMPORT_MAX_PX,
            "search_description": True,
            "search_image_text": True,
            "show_startup_animation": True,
            "qq_drag_cleanup_on_start": True,
            "virtual_drive_compat": self._cfg.get("virtual_drive_compat", False),
            "bulk_read_protection": False,
            "bulk_read_batch_size": 8,
            "bulk_read_pause_seconds": 1.0,
            "bulk_read_prefetch_pages": False,
            "hover_zoom": True,
            "window_resize_enabled": False,
        }

    def resize_window(self, width: int, height: int):
        """拖动边框调整主窗口大小（window_resize_enabled 开启时前端调用）"""
        w = self._webui._window
        if not w:
            return
        try:
            nw = max(480, min(8000, int(width)))
            nh = max(360, min(6000, int(height)))
            w.resize(nw, nh)
        except Exception:
            pass

    def move_window(self, dx: int, dy: int):
        w = self._webui._window
        if not w:
            return
        try:
            origin, last, target = _window_drag_update(
                w.x, w.y, self._drag_origin, self._drag_last_move, dx, dy
            )
            self._drag_origin, self._drag_last_move = origin, last
            if target:
                w.move(*target)
        except Exception:
            pass

    def stop_window_drag(self):
        self._drag_origin = None

    def start_window_drag(self, button: int, root_x: int, root_y: int) -> bool:
        """Linux 用 GTK begin_move_drag 合成器拖动；其他平台走增量回退"""
        self._drag_origin = None
        if platform.system() != "Linux":
            return False
        w = self._webui._window
        if not w:
            return False
        try:
            from gi.repository import Gdk, GLib

            native = getattr(w, "native", None)
            if native is None:
                return False
            # Gdk.CURRENT_TIME(0)：GDK 文档明确允许未知时间时用它，X11 下回填最近
            # 一次真实输入事件时间，Wayland 下该参数不参与合成器拖动
            GLib.idle_add(
                native.begin_move_drag, button, root_x, root_y, Gdk.CURRENT_TIME
            )
            return True
        except Exception:
            return False

    def hide_window(self):
        self._webui.hide()

    def minimize_window(self):
        """最小化到任务栏；与隐藏到托盘保持不同语义。"""
        return self._webui.minimize()

    def _find_meme_file(self, filename: str) -> str:
        cache_dir = self._cfg.cache_dir
        for root, dirs, files in os.walk(cache_dir):
            if Path(root) == Path(cache_dir):
                dirs[:] = [
                    item
                    for item in dirs
                    if item.casefold() != CHAT_RECORD_MEDIA_DIRNAME.casefold()
                ]
            if filename in files:
                return os.path.join(root, filename)
        return ""


# ─── QQNT 提取驱动（后台线程 + 状态，供设置页向导轮询） ───

_QQNT_STATE = {
    "status": "idle",  # idle|running|done|cancelled|error
    "progress": 0,
    "message": "",
    "error": "",
    "log": [],
    "result": None,
}
_QQNT_LOCK = threading.Lock()
_QQNT_CANCEL = False


def _set_qqnt(**kw):
    with _QQNT_LOCK:
        _QQNT_STATE.update(**kw)


def _append_qqnt_log(msg):
    with _QQNT_LOCK:
        _QQNT_STATE["log"] = (_QQNT_STATE["log"] + [msg])[-100:]


def get_qqnt_progress() -> dict:
    with _QQNT_LOCK:
        return dict(_QQNT_STATE)


def cancel_qqnt_extract():
    global _QQNT_CANCEL
    _QQNT_CANCEL = True


def start_qqnt_extract(
    qq_number: str,
    output_dir: str,
    image_only: bool = False,
    overwrite: bool = False,
    ini_path: str = None,
    userdata_save_path: str = None,
) -> bool:
    global _QQNT_CANCEL
    _QQNT_CANCEL = False
    _set_qqnt(
        status="running", progress=0, message="准备中", error="", log=[], result=None
    )
    threading.Thread(
        target=_qqnt_worker,
        args=(
            qq_number,
            output_dir,
            image_only,
            overwrite,
            ini_path,
            userdata_save_path,
        ),
        daemon=True,
    ).start()
    return True


def _qqnt_worker(
    qq_number, output_dir, image_only, overwrite, ini_path, userdata_save_path
):
    """后台执行 QQNT 表情提取：调用 qqnt_extract 并转发进度/错误到 _QQNT_STATE"""

    def on_progress(done, total, src, dst):
        pct = int(done * 100 / total) if total else 0
        _set_qqnt(progress=pct, message="复制中 %d/%d" % (done, total))

    def on_error(src, msg):
        _append_qqnt_log("失败: %s (%s)" % (src, msg))

    def on_log(msg):
        _append_qqnt_log(msg)

    try:
        result = qqnt_extract.extract_qq_emojis(
            qq_number,
            output_dir,
            userdata_save_path=userdata_save_path,
            ini_path=ini_path or qqnt_extract.DEFAULT_INI_PATH,
            image_only=image_only,
            overwrite=overwrite,
            should_stop=lambda: _QQNT_CANCEL,
            on_progress=on_progress,
            on_error=on_error,
            on_log=on_log,
        )
        if _QQNT_CANCEL:
            _set_qqnt(status="cancelled", message="已取消", result=result)
        else:
            _set_qqnt(status="done", progress=100, message="提取完成", result=result)
    except Exception as e:
        logger.error("qqnt extract error: %s", e)
        _set_qqnt(status="error", message="提取失败", error=str(e))


# ─── 存储位置迁移进度（后台线程 + 轮询） ───
_STORAGE_MIGRATE_STATE = {
    "status": "idle",  # idle|running|done|error|cancelled
    "progress": 0,
    "message": "",
    "current": "",
    "moved": 0,
    "total": 0,
    "failed": [],
    "error": "",
    "cancel_requested": False,
}
_STORAGE_MIGRATE_LOCK = threading.Lock()
_STORAGE_MIGRATE_THREAD = None

# ─── 本地备份/恢复进度（后台线程 + 轮询） ───
_BACKUP_STATE = {
    "kind": "",  # create|restore
    "status": "idle",  # idle|running|done|error
    "done": 0,
    "total": 0,
    "error": "",
    "result": None,
}
_BACKUP_LOCK = threading.Lock()


def _set_backup_state(**kw):
    with _BACKUP_LOCK:
        _BACKUP_STATE.update(**kw)


def get_backup_progress() -> dict:
    import copy

    with _BACKUP_LOCK:
        return copy.deepcopy(_BACKUP_STATE)


def start_backup_thread(kind: str, zip_path: str = "") -> bool:
    """启动备份/恢复后台线程；已有任务运行时返回 False"""
    if kind not in ("create", "restore"):
        return False
    with _BACKUP_LOCK:
        if _BACKUP_STATE["status"] == "running":
            return False
        _BACKUP_STATE.update(
            kind=kind, status="running", done=0, total=0, error="", result=None
        )
    threading.Thread(target=_backup_worker, args=(kind, zip_path), daemon=True).start()
    return True


def _backup_worker(kind: str, zip_path: str):
    cfg = get_config()
    db = get_db()
    cb = _backup_progress_tick
    try:
        if kind == "create":
            result = backup.create_backup(
                backup.get_backup_dir(cfg),
                cfg.data_dir,
                cfg.cache_dir,
                db,
                cb,
                allow_unresolved=cfg.get("virtual_drive_compat", False) is True,
            )  # create_backup 内部校验 backup_dir 与 cache_dir 的嵌套关系
        else:
            # 恢复全程持有导入互斥锁：_do_import/同步 pull 走同一把锁，
            # 恢复期间的写入请求阻塞等待，避免 add_meme 与整库替换并发
            with _IMPORT_LOCK:
                result = backup.restore_backup(
                    zip_path, cfg.data_dir, cfg.cache_dir, db, cb
                )
                if result.get("ok"):
                    build_manifest()
    except Exception as e:
        logger.warning(f"备份任务({kind})失败: {e}")
        _set_backup_state(status="error", error=str(e))
        return
    if result.get("ok"):
        _set_backup_state(status="done", result=result)
    else:
        _set_backup_state(status="error", error=result.get("error", "恢复失败"))


def _backup_progress_tick(done, total):
    _set_backup_state(done=done, total=total)


def _set_storage_migrate(**kw):
    with _STORAGE_MIGRATE_LOCK:
        _STORAGE_MIGRATE_STATE.update(**kw)


def get_storage_migration_progress() -> dict:
    import copy

    with _STORAGE_MIGRATE_LOCK:
        return copy.deepcopy(_STORAGE_MIGRATE_STATE)


def cancel_storage_migration():
    with _STORAGE_MIGRATE_LOCK:
        if _STORAGE_MIGRATE_STATE["status"] == "running":
            _STORAGE_MIGRATE_STATE["cancel_requested"] = True
    return {"ok": True}


def _storage_migrate_manifest_path() -> Path:
    """迁移清单文件路径（崩溃后续跑依据）"""
    return get_config().data_dir / "storage_migration.json"


def _write_storage_migration_manifest(old: Path, new: Path, virtual_drive_compat=None):
    import json

    try:
        with open(_storage_migrate_manifest_path(), "w", encoding="utf-8") as f:
            data = {"old": str(old), "new": str(new)}
            if virtual_drive_compat is not None:
                data["virtual_drive_compat"] = bool(virtual_drive_compat)
            json.dump(data, f)
    except OSError:
        pass


def _clear_storage_migration_manifest():
    try:
        _storage_migrate_manifest_path().unlink(missing_ok=True)
    except OSError:
        pass


def resume_pending_storage_migration():
    """启动自愈：存在未完成迁移清单则后台幂等续迁，成功前不阻塞启动"""
    import json

    manifest = _storage_migrate_manifest_path()
    try:
        if not manifest.is_file():
            return
        with open(manifest, "r", encoding="utf-8") as f:
            data = json.load(f)
        old, new = Path(data["old"]), Path(data["new"])
        virtual_drive_compat = data.get("virtual_drive_compat")
    except (OSError, ValueError, KeyError) as e:
        logger.warning("storage migration manifest invalid: %s", e)
        _clear_storage_migration_manifest()
        return
    if not start_storage_migration_thread(
        new,
        old_override=old,
        virtual_drive_compat=virtual_drive_compat,
    ):
        return

    def _watch():
        global _STORAGE_MIGRATE_THREAD
        thread = _STORAGE_MIGRATE_THREAD
        if thread is not None:
            thread.join()
        if _STORAGE_MIGRATE_STATE["status"] != "done":
            # 目标盘不可用等：放弃自愈并清除清单，保持旧配置
            logger.warning("storage migration resume failed, keeping old cache_dir")
            _clear_storage_migration_manifest()

    threading.Thread(target=_watch, daemon=True).start()


def start_storage_migration_thread(
    new_dir: Path,
    old_override: Path = None,
    virtual_drive_compat=None,
):
    """后台迁移 cache_dir 文件到 new_dir，避免阻塞桥接与 UI"""
    global _STORAGE_MIGRATE_THREAD
    old = old_override or get_config().cache_dir
    with _STORAGE_MIGRATE_LOCK:
        if _STORAGE_MIGRATE_STATE["status"] == "running":
            return False
        _STORAGE_MIGRATE_STATE.update(
            status="running",
            progress=0,
            message="准备迁移",
            current="",
            moved=0,
            total=0,
            failed=[],
            error="",
            cancel_requested=False,
        )
    _write_storage_migration_manifest(old, new_dir, virtual_drive_compat)
    _STORAGE_MIGRATE_THREAD = threading.Thread(
        target=_storage_migrate_worker,
        args=(old, new_dir, virtual_drive_compat),
        daemon=True,
    )
    _STORAGE_MIGRATE_THREAD.start()
    return True


def _storage_migrate_worker(old: Path, new: Path, virtual_drive_compat=None):
    """三阶段迁移：复制（源只读）→ 切换配置 → 清理源；全程幂等可续跑

    阶段1 只写新目录，崩溃/取消只需清理本次新副本（源完好无分裂）；
    阶段2 写配置是唯一切换点，此后新目录已完整；阶段3 删源失败仅残留
    旧目录冗余文件（rescan 只扫 cache_dir，无害）。

    文件复制采用"迁移临时文件 + 原子提交"：先写入 dst 同目录的
    .migrating 临时文件，完成后 os.replace 原子提交到 dst，避免写入中途
    崩溃在最终路径留下半写文件（恢复时只需删除临时文件，不回滚已提交文件）。
    """
    import shutil

    copied_pairs = []
    created_dsts = []  # 本次运行实际新建的目标（取消/失败时清理，不动既有文件）
    config_switched = False  # 阶段2 配置已切换时禁止清理新目录文件
    migrating_suffix = ".migrating"
    try:
        # 阶段一：扫描 + 复制（幂等：dst 已存在且内容一致视为已复制）
        plan = []
        for root, dirs, files in os.walk(str(old)):
            rel = os.path.relpath(root, str(old))
            for d in list(dirs):
                if d == "thumbnails":
                    dirs.remove(d)
            for name in files:
                src = os.path.join(root, name)
                dst = (new if rel == "." else new / rel) / name
                plan.append((src, dst))
        total = len(plan)
        _set_storage_migrate(total=total, message="复制文件到新目录...")
        conflicts = []
        for src, dst in plan:
            with _STORAGE_MIGRATE_LOCK:
                cancel_requested = _STORAGE_MIGRATE_STATE["cancel_requested"]
            if cancel_requested:
                break
            try:
                with guarded_read():
                    src_size = os.path.getsize(src)
            except OSError:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp_dst = dst.parent / (dst.name + migrating_suffix)
            # 清理上次中断留下的同路径临时文件（恢复入口）
            if tmp_dst.exists():
                tmp_dst.unlink()
            if dst.exists():
                if dst.stat().st_size == src_size and _file_sha256(src) == _file_sha256(
                    str(dst)
                ):
                    copied_pairs.append((src, dst))
                    _set_storage_migrate(
                        progress=int(len(copied_pairs) * 100 / total) if total else 100,
                        moved=len(copied_pairs),
                        current=os.path.basename(src),
                    )
                    continue
                conflicts.append(dst)
                continue
            try:
                with guarded_read():
                    with open(src, "rb") as in_f, open(tmp_dst, "wb") as out_f:
                        shutil.copyfileobj(in_f, out_f)
            except Exception:
                # 写入中途失败：仅删除本次临时文件，最终路径未触及
                if tmp_dst.exists():
                    tmp_dst.unlink()
                raise
            # 原子提交到最终路径（os.replace 同文件系统原子；不覆盖既有目标）
            if dst.exists():
                # 提交瞬间目标出现：校验内容，一致则视为已复制否则冲突
                tmp_dst.unlink()
                if dst.stat().st_size == src_size and _file_sha256(src) == _file_sha256(
                    str(dst)
                ):
                    copied_pairs.append((src, dst))
                    _set_storage_migrate(
                        progress=int(len(copied_pairs) * 100 / total) if total else 100,
                        moved=len(copied_pairs),
                        current=os.path.basename(src),
                    )
                    continue
                conflicts.append(dst)
                continue
            try:
                os.replace(str(tmp_dst), str(dst))
            except OSError:
                # 提交瞬间目标出现（极小窗口）：回退到内容校验
                if (
                    dst.exists()
                    and dst.stat().st_size == src_size
                    and _file_sha256(src) == _file_sha256(str(dst))
                ):
                    tmp_dst.unlink()
                    copied_pairs.append((src, dst))
                    _set_storage_migrate(
                        progress=int(len(copied_pairs) * 100 / total) if total else 100,
                        moved=len(copied_pairs),
                        current=os.path.basename(src),
                    )
                    continue
                tmp_dst.unlink()
                conflicts.append(dst)
                continue
            copied_pairs.append((src, dst))
            created_dsts.append(dst)
            _set_storage_migrate(
                progress=int(len(copied_pairs) * 100 / total) if total else 100,
                moved=len(copied_pairs),
                current=os.path.basename(src),
            )
        with _STORAGE_MIGRATE_LOCK:
            cancel_requested = _STORAGE_MIGRATE_STATE["cancel_requested"]
        if conflicts:
            for d in created_dsts:
                try:
                    d.unlink()
                except OSError:
                    pass
            _set_storage_migrate(
                status="error",
                message="目标目录已存在同名但内容不同的文件，未迁移",
                error=f"目标已存在内容不同的同名文件: {conflicts[0]} 等 "
                f"{len(conflicts)} 个",
            )
            _clear_storage_migration_manifest()
            return
        if cancel_requested:
            for d in created_dsts:
                try:
                    d.unlink()
                except OSError:
                    pass
            _set_storage_migrate(status="cancelled", message="已取消，目录保持原状")
            _clear_storage_migration_manifest()
            return
        # 阶段二：切换配置（唯一危险点，此后新目录已完整）
        cfg = get_config()
        cfg.set("cache_dir", str(new))
        if virtual_drive_compat is not None:
            cfg.set("virtual_drive_compat", bool(virtual_drive_compat))
        cfg.save()
        # 依据磁盘上实际持久化的 cache_dir 判断是否已切换（原子保存保证一致，
        # 但异常路径下以磁盘为准，避免误判为未切换而删除新目录）
        try:
            import json as _json

            on_disk = _json.loads(get_config()._path.read_text(encoding="utf-8"))
            config_switched = on_disk.get("cache_dir") == str(new)
        except Exception:
            config_switched = False
        # 阶段三：清理源文件（失败仅残留，不阻断不回滚）
        failed = []
        for src, _dst in copied_pairs:
            try:
                os.unlink(src)
            except OSError as e:
                failed.append(
                    {"name": os.path.basename(src), "path": src, "error": str(e)}
                )
        _set_storage_migrate(
            status="done",
            progress=100,
            message="迁移完成",
            current="",
            failed=failed,
        )
        _clear_storage_migration_manifest()
        try:
            if len(webview.windows) > 0:
                webview.windows[0].evaluate_js("refreshMemes();")
        except Exception:
            pass
    except Exception as e:
        logger.error("storage migrate error: %s", e)
        # 配置未切换时源未动，仅清理本次新建副本即回到原状（既有文件不动）；
        # 配置已切换则新目录已完整，禁止删除新目录文件
        if not config_switched:
            for d in created_dsts:
                try:
                    d.unlink()
                except OSError:
                    pass
        _set_storage_migrate(status="error", message="迁移失败", error=str(e))
        _clear_storage_migration_manifest()


class SettingsApi:
    """暴露给设置窗口的 JS API（仅设置相关方法）"""

    def copy_editor_text(self, text):
        """复制普通编辑框文本并允许进入本地剪贴板历史。"""
        return _copy_editor_text(text, self._webui._settings_window)

    def __init__(self, webui):
        self._webui = webui
        self._cfg = get_config()
        self._drag_origin = None
        self._drag_last_move = 0.0

    def check_connectivity(self) -> dict:
        return _check_connectivity()

    def napcat_test_connection(
        self, http_url: str, ws_url: str = "", token: str = ""
    ) -> dict:
        """只读测试 NapCat 的 OneBot 登录态和版本接口。"""
        try:
            return onebot.test_connection(http_url, token, ws_url)
        except onebot.OneBotError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            logger.exception("NapCat 连接测试失败")
            return {"ok": False, "error": "连接测试失败，请检查 NapCat 配置"}

    def napcat_apply_listener(
        self,
        enabled: bool,
        http_url: str,
        ws_url: str,
        token: str = "",
        command: str = "/omm",
        send_mode: str = "original_native",
        webui_url: str = "http://127.0.0.1:6099",
        webui_token: str = "",
    ) -> dict:
        """立即应用 QQ 命令监听配置。"""
        try:
            token = onebot.normalize_token(token)
            webui_token = onebot.normalize_token(webui_token)
            command = onebot.normalize_command(command)
            send_mode = onebot.normalize_send_mode(send_mode)
            http_url = onebot.normalize_http_url(http_url)
            ws_url = onebot.normalize_ws_url(ws_url)
            webui_url = onebot.normalize_http_url(webui_url or "http://127.0.0.1:6099")
            self._cfg.update_from_dict(
                {
                    "napcat_enabled": bool(enabled),
                    "napcat_http_url": http_url,
                    "napcat_ws_url": ws_url,
                    "napcat_token": token,
                    "napcat_webui_url": webui_url,
                    "napcat_webui_token": webui_token,
                    "napcat_command": command,
                    "napcat_send_mode": send_mode,
                }
            )
            self._cfg.save()
            if not enabled:
                onebot.stop_command_listener()
                return {"ok": True, **onebot.get_command_listener_status()}
            status = onebot.start_command_listener(
                http_url,
                ws_url,
                token,
                command,
                send_mode,
                webui_url,
                webui_token,
            )
            return {"ok": True, **status}
        except onebot.OneBotError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            logger.exception("NapCat 命令监听应用失败")
            return {"ok": False, "error": "命令监听启动失败，请检查 NapCat 日志"}

    def napcat_get_listener_status(self) -> dict:
        """返回不含 Token 的 QQ 命令监听和归档状态。"""
        return onebot.get_command_listener_status()

    def lan_start(self, port: int = None, secret: str = None) -> dict:
        from . import lan

        p = int(port or self._cfg.get("lan_port", 17852))
        s = secret if secret is not None else self._cfg.get("lan_secret", "")
        ok = lan.start(p, s)
        return {"ok": ok, "status": lan.get_status()}

    def lan_stop(self) -> dict:
        from . import lan

        lan.stop()
        return {"ok": True, "status": lan.get_status()}

    def lan_get_status(self) -> dict:
        from . import lan

        return lan.get_status()

    def lan_get_ip(self) -> str:
        from . import lan

        return lan.get_lan_ip()

    def lan_qr(self) -> dict:
        """生成连接二维码（IP + 端口 + 密钥，仅局域网服务运行时）"""
        import base64
        import json

        import qrcode

        from . import lan

        st = lan.get_status()
        if st.get("status") != "running":
            return {"ok": False, "error": "局域网服务未运行"}
        ip = lan.get_lan_ip()
        if not ip:
            return {"ok": False, "error": "未获取到局域网 IP"}
        # 用握手实际使用的密钥（设置页可能用未保存的输入值启动服务）
        secret = lan.runtime_secret()
        if secret is None:
            secret = self._cfg.get("lan_secret", "")
        payload = json.dumps(
            {
                "v": 1,
                "app": "ohmymeme",
                "ip": ip,
                "port": st.get("port"),
                "secret": secret,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        img = qrcode.make(payload, box_size=8, border=2)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return {"ok": True, "qr": "data:image/png;base64," + b64}

    def lan_set_allow_secret_config(self, enabled: bool) -> dict:
        from . import lan

        lan.set_allow_secret_config(bool(enabled))
        return {
            "ok": True,
            "allow_secret_config": lan.get_status()["allow_secret_config"],
        }

    def plugin_list(self) -> list:
        """插件列表（manifest 元信息 + 状态 + 权限清单）"""
        return get_plugin_manager().list_info()

    def plugin_set_enabled(self, plugin_id: str, enabled: bool) -> dict:
        """切换插件启用状态（禁用立即生效，启用需重启）"""
        return get_plugin_manager().set_enabled(str(plugin_id), bool(enabled))

    def plugin_get_settings(self, plugin_id: str) -> dict:
        """读取插件自有设置"""
        return get_plugin_manager().get_settings(str(plugin_id))

    def plugin_save_settings(self, plugin_id: str, data: dict) -> dict:
        """保存插件自有设置（成功后通知前端刷新插件样式）"""
        result = get_plugin_manager().save_settings(str(plugin_id), data or {})
        if result.get("ok"):
            import json as _json

            get_plugin_manager().evaluate_js(
                "window.ommPluginRefresh&&window.ommPluginRefresh(%s)"
                % _json.dumps(str(plugin_id))
            )
        return result

    def plugin_open_dir(self) -> dict:
        """在系统文件管理器打开插件安装目录"""
        return get_plugin_manager().open_plugins_dir()

    def plugin_call(self, plugin_id: str, method: str, args=None) -> dict:
        """分发设置窗口插件方法（manifest settings_api 声明的逻辑）"""
        return get_plugin_manager().call_settings(
            str(plugin_id), str(method), args or []
        )

    def lan_confirm_device(self, approved: bool) -> dict:
        from . import lan

        lan.confirm_device(bool(approved))
        return {"ok": True}

    def get_settings(self) -> dict:
        d = self._cfg.to_dict()
        from .platform_util import is_auto_start_enabled

        return {
            "theme": d.get("theme", "dark"),
            "hotkey": d.get("hotkey", "Ctrl+Alt+N"),
            "hotkey_show_at_mouse": d.get("hotkey_show_at_mouse", False),
            "auto_play_gif": d.get("auto_play_gif", True),
            "try_original_image": d.get("try_original_image", False),
            "copy_resize_mode": int(d.get("copy_resize_mode", 1) or 0),
            "cache_dir": str(self._cfg.cache_dir),
            "virtual_drive_compat": d.get("virtual_drive_compat", False),
            "lan_port": d.get("lan_port", 17852),
            "lan_secret": d.get("lan_secret", ""),
            "napcat_enabled": d.get("napcat_enabled", False),
            "napcat_http_url": d.get("napcat_http_url", "http://127.0.0.1:3000"),
            "napcat_ws_url": d.get("napcat_ws_url", "ws://127.0.0.1:3001"),
            "napcat_token": d.get("napcat_token", ""),
            "napcat_webui_url": d.get("napcat_webui_url", "http://127.0.0.1:6099"),
            "napcat_webui_token": d.get("napcat_webui_token", ""),
            "napcat_command": d.get("napcat_command", "/omm"),
            "napcat_send_mode": d.get("napcat_send_mode", "original_native"),
            "napcat_image_send_mode": d.get("napcat_image_send_mode", "sticker"),
            "qq_drag_cleanup_on_start": d.get("qq_drag_cleanup_on_start", True),
            "auto_start": is_auto_start_enabled(),
            "silent_start": d.get("silent_start", False),
            "sync_auto_fetch_index": d.get("sync_auto_fetch_index", False),
            "sync_auto_sync": d.get("sync_auto_sync", False),
            "sync_type": d.get("sync_type", ""),
            "sync_delete_remote": d.get("sync_delete_remote", False),
            "sync_remove_local": d.get("sync_remove_local", False),
            "sync_hide_upload_warning": d.get("sync_hide_upload_warning", False),
            "ftp_host": d.get("ftp_host", ""),
            "ftp_port": d.get("ftp_port", 21),
            "ftp_user": d.get("ftp_user", ""),
            "ftp_password": d.get("ftp_password", ""),
            "ftp_path": d.get("ftp_path", "/"),
            "s3_endpoint": d.get("s3_endpoint", ""),
            "s3_region": d.get("s3_region", ""),
            "s3_bucket": d.get("s3_bucket", ""),
            "s3_access_key": d.get("s3_access_key", ""),
            "s3_secret_key": d.get("s3_secret_key", ""),
            "s3_path": d.get("s3_path", ""),
            "r2_account_id": d.get("r2_account_id", ""),
            "r2_access_key_id": d.get("r2_access_key_id", ""),
            "r2_secret_access_key": d.get("r2_secret_access_key", ""),
            "r2_bucket": d.get("r2_bucket", ""),
            "r2_path": d.get("r2_path", ""),
            "webdav_url": d.get("webdav_url", ""),
            "webdav_user": d.get("webdav_user", ""),
            "webdav_password": d.get("webdav_password", ""),
            "webdav_path": d.get("webdav_path", ""),
            "webdav_allow_insecure": d.get("webdav_allow_insecure", False),
            "show_upload_progress": d.get("show_upload_progress", True),
            "show_upload_done": d.get("show_upload_done", True),
            "show_download_progress": d.get("show_download_progress", True),
            "show_download_done": d.get("show_download_done", True),
            "show_uncategorized": d.get("show_uncategorized", True),
            "show_category_uncategorized": d.get("show_category_uncategorized") is True,
            "hidden_collection_enabled": d.get("hidden_collection_enabled", True),
            "hierarchy_delete_mode": _normalize_hierarchy_delete_mode(
                d.get("hierarchy_delete_mode", "ask")
            ),
            "large_meme_import_mode": _normalize_large_meme_import_mode(
                d.get("large_meme_import_mode", "ask")
            ),
            "import_resolution_limit_enabled": d.get("import_resolution_limit_enabled")
            is True,
            "import_resolution_limit_px": normalize_import_max_px(
                d.get("import_resolution_limit_px", _IMPORT_MAX_PX)
            ),
            "record_recent_use": d.get("record_recent_use", True),
            "search_description": d.get("search_description", True),
            "search_image_text": d.get("search_image_text", True),
            "show_startup_animation": d.get("show_startup_animation", True),
            "bulk_read_protection": d.get("bulk_read_protection", False),
            "bulk_read_batch_size": clamp_batch_size(d.get("bulk_read_batch_size", 8)),
            "bulk_read_pause_seconds": clamp_pause_seconds(
                d.get("bulk_read_pause_seconds", 1.0)
            ),
            "bulk_read_prefetch_pages": d.get("bulk_read_prefetch_pages", False),
            "tg_tdata_path": d.get("tg_tdata_path", ""),
            "hover_to_play": d.get("hover_to_play", False),
            "hover_zoom": d.get("hover_zoom", True),
            "copy_avoid_webp": d.get("copy_avoid_webp", False),
            "disable_auto_hide": d.get("disable_auto_hide", False),
            "manifest_include_tags": d.get("manifest_include_tags", True),
            "manifest_include_collections": d.get("manifest_include_collections", True),
            "manifest_include_favorites": d.get("manifest_include_favorites", True),
            "cloud_direct": d.get("cloud_direct", True),
            "cloud_thumb_auto_push": d.get("cloud_thumb_auto_push", True),
            "window_resize_enabled": d.get("window_resize_enabled", False),
        }

    def _safe_refresh(self, js_function: str) -> dict:
        """执行前端刷新函数，并在异常时记录日志"""
        try:
            if len(webview.windows) > 0:
                webview.windows[0].evaluate_js(f"{js_function}();")
        except Exception:
            logger.exception("前端刷新函数 %s 执行失败", js_function)
        return {"ok": True}

    def refresh_memes(self):
        """设置窗口同步完成后刷新主窗口表情列表"""
        return self._safe_refresh("refreshMemes")

    def refresh_tags(self):
        """设置窗口同步完成后刷新主窗口标签栏"""
        return self._safe_refresh("refreshTags")

    def refresh_collections(self):
        """设置窗口同步完成后刷新主窗口分组树"""
        return self._safe_refresh("refreshCollections")

    def open_guide(self) -> bool:
        """设置页入口：同步打开向导并显示主窗口。

        两步各限时执行（后台线程 join 超时兜底），任一步挂起也不阻塞本方法返回，
        避免前端 Promise 永不 resolve 表现为「按钮无响应」。同步执行保证与随后的
        设置窗口关闭不并发，防止窗口操作竞态导致 UI 冻结。
        """
        win = getattr(self._webui, "_window", None)
        if win is None:
            return False

        def _call(fn, name: str, timeout: float = 5.0) -> bool:
            box: dict = {}

            def _run():
                try:
                    box["ok"] = fn()
                except Exception as e:
                    logger.warning("open_guide %s failed: %s", name, e)
                    box["ok"] = False

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout)
            if t.is_alive():
                logger.warning("open_guide %s timed out (%ss)", name, timeout)
                return False
            return box.get("ok", False) is not False

        ok = _call(lambda: win.evaluate_js("window.showGuide && showGuide();"), "eval")
        _call(self._webui.show, "show")
        return ok

    def open_env_check(self) -> bool:
        """设置页入口：打开环境检测窗口（独立子进程，非阻塞）"""
        try:
            from .env_check import spawn_ui

            return spawn_ui()
        except Exception as e:
            logger.warning("open_env_check failed: %s", e)
            return False

    def save_settings(self, settings: dict):
        if isinstance(settings, dict):
            settings = dict(settings)
            hidden_collection_changed = "hidden_collection_enabled" in settings and (
                settings["hidden_collection_enabled"] is not False
            ) != (self._cfg.get("hidden_collection_enabled", True) is not False)
            category_sidebar_changed = "show_category_uncategorized" in settings and (
                settings["show_category_uncategorized"] is True
            ) != (self._cfg.get("show_category_uncategorized", False) is True)
            if "hidden_collection_enabled" in settings:
                settings["hidden_collection_enabled"] = (
                    settings["hidden_collection_enabled"] is not False
                )
            if "hierarchy_delete_mode" in settings:
                settings["hierarchy_delete_mode"] = _normalize_hierarchy_delete_mode(
                    settings["hierarchy_delete_mode"]
                )
            if "large_meme_import_mode" in settings:
                settings["large_meme_import_mode"] = _normalize_large_meme_import_mode(
                    settings["large_meme_import_mode"]
                )
            settings = _normalize_bulk_read_settings(settings)
            napcat_changed = any(
                key in settings
                for key in (
                    "napcat_enabled",
                    "napcat_http_url",
                    "napcat_ws_url",
                    "napcat_token",
                    "napcat_webui_url",
                    "napcat_webui_token",
                    "napcat_command",
                    "napcat_send_mode",
                )
            )
            if "napcat_webui_url" in settings:
                try:
                    settings["napcat_webui_url"] = onebot.normalize_http_url(
                        settings["napcat_webui_url"] or "http://127.0.0.1:6099"
                    )
                except onebot.OneBotError as exc:
                    return {"ok": False, "error": f"NapCat WebUI 地址无效：{exc}"}
            for token_key in ("napcat_token", "napcat_webui_token"):
                if token_key not in settings:
                    continue
                try:
                    settings[token_key] = onebot.normalize_token(settings[token_key])
                except onebot.OneBotError as exc:
                    return {"ok": False, "error": str(exc)}
            if "napcat_command" in settings:
                try:
                    settings["napcat_command"] = onebot.normalize_command(
                        settings["napcat_command"]
                    )
                except onebot.OneBotError as exc:
                    return {"ok": False, "error": str(exc)}
            if "napcat_send_mode" in settings:
                try:
                    settings["napcat_send_mode"] = onebot.normalize_send_mode(
                        settings["napcat_send_mode"]
                    )
                except onebot.OneBotError as exc:
                    return {"ok": False, "error": str(exc)}
            if "napcat_image_send_mode" in settings:
                try:
                    settings["napcat_image_send_mode"] = (
                        onebot.normalize_image_send_mode(
                            settings["napcat_image_send_mode"]
                        )
                    )
                except onebot.OneBotError as exc:
                    return {"ok": False, "error": str(exc)}
            previous_hotkey = self._cfg.get("hotkey", "Ctrl+Alt+N")
            if "auto_start" in settings and settings["auto_start"] != self._cfg.get(
                "auto_start", False
            ):
                from .platform_util import set_auto_start

                set_auto_start(settings["auto_start"])
            prev_cloud = bool(self._cfg.get("cloud_direct", False))
            prev_sync_type = self._cfg.get("sync_type", "")
            self._cfg.update_from_dict(settings)
            self._cfg.save()
            # 云端直接使用开关/云后端变更：重置云态与缓存，开启时立即重拉
            if "cloud_direct" in settings or "sync_type" in settings:
                cloud = bool(self._cfg.get("cloud_direct", False))
                sync_changed = (
                    "sync_type" in settings
                    and settings.get("sync_type", "") != prev_sync_type
                )
                if cloud != prev_cloud or sync_changed:
                    _cloud_reset()
                    if cloud:
                        _start_cloud_refresh()
            self._refresh_main_settings()
            if "hotkey" in settings and settings["hotkey"] != previous_hotkey:
                self._webui._on_hotkey_change(settings["hotkey"])
            if napcat_changed:
                try:
                    onebot.apply_command_listener_config(self._cfg)
                except onebot.OneBotError as exc:
                    return {"ok": False, "error": str(exc)}
                except Exception:
                    logger.exception("NapCat 命令监听重载失败")
                    return {"ok": False, "error": "命令监听重载失败"}
            if hidden_collection_changed or category_sidebar_changed:
                try:
                    if webview.windows:
                        webview.windows[0].evaluate_js(
                            "window.refreshLibrary && "
                            "window.refreshLibrary('structure');"
                        )
                except Exception:
                    logger.debug("主窗口结构刷新失败", exc_info=True)
            return {"ok": True, "theme": self._cfg.get("theme", "dark")}
        return {"ok": False, "error": "invalid_settings"}

    # 将已保存的界面设置直接推送到主窗口。
    def _refresh_main_settings(self):
        import json

        settings = {
            "theme": self._cfg.get("theme", "dark"),
            "hover_zoom": self._cfg.get("hover_zoom", True),
            "window_resize_enabled": self._cfg.get("window_resize_enabled", False),
        }
        try:
            if webview.windows:
                webview.windows[0].evaluate_js(f"refreshMemes({json.dumps(settings)});")
        except Exception:
            logger.debug("主窗口设置刷新失败", exc_info=True)

    def reset_settings(self) -> dict:
        prev_cache_dir = self._cfg.get("cache_dir", "")
        category_sidebar_changed = (
            self._cfg.get("show_category_uncategorized", False) is True
        )
        self._cfg.reset()
        if prev_cache_dir:
            self._cfg.set("cache_dir", prev_cache_dir)
        self._cfg.save()
        hotkey = self._cfg.get("hotkey", "Ctrl+Alt+N")
        self._webui._on_hotkey_change(hotkey)
        from .platform_util import set_auto_start

        set_auto_start(False)
        onebot.stop_command_listener()
        _cloud_reset()
        self._refresh_main_settings()
        if category_sidebar_changed:
            try:
                if webview.windows:
                    webview.windows[0].evaluate_js(
                        "window.refreshLibrary && window.refreshLibrary('structure');"
                    )
            except Exception:
                logger.debug("主窗口结构刷新失败", exc_info=True)
        return {
            "theme": self._cfg.get("theme", "dark"),
            "hotkey": hotkey,
            "hotkey_show_at_mouse": self._cfg.get("hotkey_show_at_mouse", False),
            "auto_play_gif": self._cfg.get("auto_play_gif", True),
            "copy_resize_mode": self._cfg.get("copy_resize_mode", 1),
            "auto_start": False,
            "silent_start": False,
            "show_category_uncategorized": False,
            "hidden_collection_enabled": True,
            "sync_auto_fetch_index": False,
            "sync_auto_sync": False,
            "sync_type": "",
            "sync_delete_remote": False,
            "sync_remove_local": False,
            "sync_hide_upload_warning": False,
            "ftp_host": "",
            "ftp_port": 21,
            "ftp_user": "",
            "ftp_password": "",
            "ftp_path": "/",
            "s3_endpoint": "",
            "s3_region": "",
            "s3_bucket": "",
            "s3_access_key": "",
            "s3_secret_key": "",
            "s3_path": "",
            "r2_account_id": "",
            "r2_access_key_id": "",
            "r2_secret_access_key": "",
            "r2_bucket": "",
            "r2_path": "",
            "webdav_url": "",
            "webdav_user": "",
            "webdav_password": "",
            "webdav_path": "",
            "webdav_allow_insecure": False,
            "show_upload_progress": True,
            "show_upload_done": True,
            "show_download_progress": True,
            "show_download_done": True,
            "hierarchy_delete_mode": "ask",
            "large_meme_import_mode": "ask",
            "import_resolution_limit_enabled": False,
            "import_resolution_limit_px": _IMPORT_MAX_PX,
            "record_recent_use": True,
            "search_description": True,
            "search_image_text": True,
            "show_startup_animation": True,
            "virtual_drive_compat": False,
            "bulk_read_protection": False,
            "bulk_read_batch_size": 8,
            "bulk_read_pause_seconds": 1.0,
            "bulk_read_prefetch_pages": False,
            "napcat_enabled": False,
            "napcat_http_url": "http://127.0.0.1:3000",
            "napcat_ws_url": "ws://127.0.0.1:3001",
            "napcat_token": "",
            "napcat_webui_url": "http://127.0.0.1:6099",
            "napcat_webui_token": "",
            "napcat_command": "/omm",
            "napcat_send_mode": "original_native",
            "napcat_image_send_mode": "sticker",
            "qq_drag_cleanup_on_start": True,
            "tg_tdata_path": self._cfg.get("tg_tdata_path", ""),
            "hover_to_play": self._cfg.get("hover_to_play", False),
            "hover_zoom": True,
            "copy_avoid_webp": self._cfg.get("copy_avoid_webp", False),
            "disable_auto_hide": False,
            "manifest_include_tags": True,
            "manifest_include_collections": True,
            "manifest_include_favorites": True,
            "cloud_direct": True,
            "cloud_thumb_auto_push": True,
            "window_resize_enabled": False,
        }

    def resize_window(self, width: int, height: int, persist: bool = False):
        """拖动边框调整设置窗口大小；persist=True 时同时持久化尺寸"""
        w = self._webui._settings_window
        if not w:
            return
        try:
            nw = max(600, min(8000, int(width)))
            nh = max(480, min(6000, int(height)))
            w.resize(nw, nh)
            if persist:
                self._cfg.set("settings_window_width", nw)
                self._cfg.set("settings_window_height", nh)
                self._cfg.save()
        except Exception:
            pass

    def reset_main_window_size(self) -> dict:
        """恢复主窗口默认大小 960x640 并清除持久化尺寸"""
        try:
            self._cfg.set("window_width", 0)
            self._cfg.set("window_height", 0)
            self._cfg.save()
            w = self._webui._window
            if w:
                w.resize(960, 640)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def move_window(self, dx: int, dy: int):
        w = self._webui._settings_window
        if not w:
            return
        try:
            origin, last, target = _window_drag_update(
                w.x, w.y, self._drag_origin, self._drag_last_move, dx, dy
            )
            self._drag_origin, self._drag_last_move = origin, last
            if target:
                w.move(*target)
        except Exception:
            pass

    def stop_window_drag(self):
        self._drag_origin = None

    def start_window_drag(self, button: int, root_x: int, root_y: int) -> bool:
        """Linux 用 GTK begin_move_drag 合成器拖动；其他平台走增量回退"""
        self._drag_origin = None
        if platform.system() != "Linux":
            return False
        w = self._webui._settings_window
        if not w:
            return False
        try:
            from gi.repository import Gdk, GLib

            native = getattr(w, "native", None)
            if native is None:
                return False
            # Gdk.CURRENT_TIME(0)：GDK 文档明确允许未知时间时用它，X11 下回填最近
            # 一次真实输入事件时间，Wayland 下该参数不参与合成器拖动
            GLib.idle_add(
                native.begin_move_drag, button, root_x, root_y, Gdk.CURRENT_TIME
            )
            return True
        except Exception:
            return False

    def start_qq_import(self) -> dict:
        adb_util.start_qq_import()
        return {"ok": True}

    def get_qq_import_progress(self) -> dict:
        return adb_util.get_qq_progress()

    def save_qq_zip(self) -> dict:
        """把生成的 QQ ZIP 通过另存为对话框保存到用户选择的位置"""
        st = adb_util.get_qq_progress()
        if st["status"] != "done" or not st["zip_path"]:
            return {"ok": False, "error": "no zip ready"}
        result = self._dialog(
            webview.FileDialog.SAVE,
            allow_multiple=False,
            file_types=("ZIP 文件 (*.zip)",),
        )
        if not result:
            return {"ok": False, "error": "cancelled"}
        import shutil

        dst = result[0] if isinstance(result, (tuple, list)) else result
        if not dst.lower().endswith(".zip"):
            dst += ".zip"
        src = st["zip_path"]
        shutil.copy2(src, dst)
        try:
            os.unlink(src)
        except OSError:
            pass
        adb_util.reset_qq_import()
        return {"ok": True, "path": dst}

    def open_adb_folder(self) -> bool:
        try:
            adb_util.open_adb_folder()
            return True
        except Exception:
            return False

    def export_logs(self) -> dict:
        """导出本次运行收集的日志（DEBUG 级）到用户选择的位置"""
        result = self._dialog(
            webview.FileDialog.SAVE,
            allow_multiple=False,
            save_filename="OhMyMeme-logs.txt",
            file_types=("文本文件 (*.txt)",),
        )
        if not result:
            return {"ok": False, "error": "cancelled"}
        dst = result[0] if isinstance(result, (tuple, list)) else result
        if not dst.lower().endswith(".txt"):
            dst += ".txt"
        with _LOG_LOCK:
            lines = list(_LOG_BUFFER)
        if not lines:
            return {"ok": False, "error": "no logs"}
        try:
            with open(dst, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "path": dst, "count": len(lines)}

    def open_adb_help(self) -> bool:
        try:
            adb_util.open_adb_help()
            return True
        except Exception:
            return False

    def open_url(self, url: str) -> bool:
        """用系统默认浏览器打开外链（关于页 GitHub/QQ 群），仅允许 http(s)"""
        from urllib.parse import urlparse

        if urlparse(url or "").scheme not in ("http", "https"):
            return False
        try:
            if platform.system() == "Windows":
                os.startfile(url)
            elif platform.system() == "Darwin":
                import subprocess

                subprocess.Popen(["open", url])
            else:
                import subprocess

                subprocess.Popen(["xdg-open", url])
            return True
        except Exception:
            return False

    def cancel_qq_import(self):
        adb_util.cancel_qq_import()

    def pick_tg_tdata(self) -> dict:
        """手动选择 Telegram Desktop tdata 目录（校验并持久化）"""
        result = self._dialog(webview.FileDialog.FOLDER)
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (tuple, list)) else result
        if not tg_stickers.is_valid_tdata(path):
            return {
                "ok": False,
                "error": "所选目录不是有效的 tdata 目录（未找到 key_datas）",
            }
        self._cfg.set("tg_tdata_path", path)
        self._cfg.save()
        return {"ok": True, "path": path}

    def start_tg_import(self, tdata_path=None, passcode="", convert_webm=True) -> dict:
        """启动 Telegram 缓存导入，已有任务时返回 {"ok": False, "error"}"""
        if not tdata_path:
            tdata_path = self._cfg.get("tg_tdata_path", "") or None
        started = tg_stickers.start_tg_import(
            self._webui, tdata_path, passcode, convert_webm
        )
        if not started:
            return {"ok": False, "error": "已有导入任务正在进行"}
        return {"ok": True}

    def get_tg_import_progress(self) -> dict:
        return tg_stickers.get_tg_progress()

    def cancel_tg_import(self):
        tg_stickers.cancel_tg_import()

    def start_douyin_import(self, cookie: str) -> dict:
        """启动抖音表情包下载导入（全部下载）"""
        try:
            from . import douyin
        except ImportError as e:
            return {"ok": False, "error": f"缺少依赖: {e}"}

        started = douyin.start_douyin_import(self._webui, cookie)
        if not started:
            return {"ok": False, "error": "已有导入任务正在进行"}
        return {"ok": True}

    def get_douyin_import_progress(self) -> dict:
        from . import douyin

        return douyin.get_douyin_progress()

    def cancel_douyin_import(self):
        from . import douyin

        douyin.cancel_douyin_import()

    def pick_wechat_root(self):
        """手动选择微信文件根目录"""
        result = self._dialog(webview.FileDialog.FOLDER)
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (tuple, list)) else result
        if not os.path.isdir(path):
            return {"ok": False, "error": "所选目录不存在"}
        return {"ok": True, "path": path}

    def inspect_wechat_environment(self, user_root=None):
        """检测微信环境"""
        from . import wechat_probe

        return wechat_probe.inspect_wechat_environment(user_root)

    def list_wechat_stickers(self, user_root, account_path=None):
        """列出可导入的微信表情"""
        from . import wechat_probe

        return wechat_probe.list_wechat_stickers(user_root, account_path)

    def start_wechat_import(self, user_root=None, download=True, account_path=None):
        """启动微信表情包导入，已有任务时返回 {"ok": False}"""
        from . import wechat_probe

        started = wechat_probe.start_wechat_import(
            self._webui, user_root, download, account_path
        )
        if not started:
            return {"ok": False, "error": "已有导入任务正在进行"}
        return {"ok": True}

    def get_wechat_import_progress(self):
        """获取微信导入进度"""
        from . import wechat_probe

        return wechat_probe.get_wechat_progress()

    def cancel_wechat_import(self):
        """取消微信导入"""
        from . import wechat_probe

        wechat_probe.cancel_wechat_import()

    def qqnt_check_env(self) -> dict:
        """检查 QQNT 提取环境，返回 get_extract_status 结果"""
        return qqnt_extract.get_extract_status(
            ini_path=self._cfg.get("qqnt_ini_path") or qqnt_extract.DEFAULT_INI_PATH,
            userdata_save_path=self._cfg.get("qqnt_userdata_path") or None,
            fetch_nicknames=True,
        )

    def qqnt_pick_ini(self) -> dict:
        """选择 UserDataInfo.ini，保存到配置并返回环境状态"""
        result = self._dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=("INI Files (*.ini);;All Files (*)",),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (tuple, list)) else result
        self._cfg.set("qqnt_ini_path", path)
        self._cfg.set("qqnt_userdata_path", "")
        self._cfg.save()
        return self.qqnt_check_env()

    def qqnt_pick_userdata(self) -> dict:
        """选择用户数据目录，保存到配置并返回环境状态"""
        result = self._dialog(webview.FileDialog.FOLDER)
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (tuple, list)) else result
        self._cfg.set("qqnt_userdata_path", path)
        self._cfg.save()
        return self.qqnt_check_env()

    def qqnt_pick_base(self) -> dict:
        """选择保存基础目录"""
        result = self._dialog(webview.FileDialog.FOLDER)
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (tuple, list)) else result
        return {"ok": True, "base": path}

    def get_storage_info(self):
        """返回存储目录信息（供设置页展示）"""
        cfg = self._cfg
        cache = cfg.cache_dir
        count = 0
        total = 0
        try:
            if cache.exists():
                for root, dirs, files in os.walk(str(cache)):
                    for d in list(dirs):
                        if d == "thumbnails":
                            dirs.remove(d)
                    for name in files:
                        with guarded_read():
                            count += 1
                            try:
                                total += (Path(root) / name).stat().st_size
                            except OSError:
                                pass
        except OSError:
            pass
        return {
            "cache_dir": str(cache),
            "data_dir": str(cfg.data_dir),
            "custom": bool(cfg.get("cache_dir", "")),
            "file_count": count,
            "total_size": total,
        }

    def _dialog(self, kind, **kw):
        """以设置窗口为 owner 打开文件对话框，返回原始 result（None 表示取消/失败）。

        对话框关闭后自动恢复设置窗口前台焦点，避免主窗口抢焦遮挡设置页。
        kind: webview.FileDialog.FOLDER / OPEN / SAVE
        其余关键字透传给 create_file_dialog。
        """
        win = self._webui._settings_window
        if win is None:
            win = webview.windows[0] if webview.windows else None
        if win is None:
            return None
        try:
            result = win.create_file_dialog(kind, **kw)
        except Exception:
            return None
        # 对话框关闭后焦点可能回到主窗口，恢复设置窗口到前台
        try:
            self._webui.focus_settings_window()
        except Exception:
            pass
        return result

    def pick_storage_dir(self):
        """选择新的表情包存储目录（只返回路径，不立即生效）"""
        result = self._dialog(webview.FileDialog.FOLDER)
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (tuple, list)) else result
        return {"ok": True, "path": path}

    def plugin_pick_file(self, plugin_id: str, media: str = "any") -> dict:
        """为插件打开文件选择对话框（插件设置区块用，media=video/image/any）"""
        pid = str(plugin_id)
        info = get_plugin_manager().list_info()
        if not any(p.get("id") == pid and p.get("status") == "loaded" for p in info):
            return {"ok": False, "error": "plugin_disabled"}
        types = {
            "video": ("Video (*.mp4;*.webm;*.mov;*.mkv;*.avi)",),
            "image": ("Images (*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp;*.avif)",),
            "any": (
                "Media (*.mp4;*.webm;*.mov;*.gif;*.png;*.jpg;*.jpeg;*.webp;*.bmp)",
                "All files (*.*)",
            ),
        }.get(str(media), ("All files (*.*)",))
        result = self._dialog(webview.FileDialog.OPEN, file_types=types)
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (tuple, list)) else result
        return {"ok": True, "path": str(path)}

    def apply_storage_dir(self, path, move_files=False, virtual_drive_compat=None):
        """应用新的表情包存储目录；move_files=True 时后台迁移现有文件"""
        compat = (
            bool(virtual_drive_compat)
            if virtual_drive_compat is not None
            else self._cfg.get("virtual_drive_compat", False) is True
        )
        old_custom = self._cfg.get("cache_dir", "")
        try:
            old = (
                resolve_path(old_custom, compat) if old_custom else self._cfg.cache_dir
            )
        except OSError:
            return {
                "ok": False,
                "error": "当前存储目录无法解析，请开启“增强虚拟盘兼容性”后重试",
            }
        protected = (self._cfg.data_dir, self._cfg.thumbnail_dir)
        ok, err = _storage_dir_validation(
            path, str(old), protected, allow_unresolved=compat
        )
        if not ok:
            return {"ok": False, "error": err}
        ok, err = backup.validate_backup_dir(
            path,
            backup.get_backup_dir(self._cfg),
            allow_unresolved=compat,
        )
        if not ok:
            return {"ok": False, "error": f"新存储目录与备份目录冲突: {err}"}
        try:
            new = resolve_path(path, compat)
        except OSError:
            return {"ok": False, "error": "路径无效"}
        try:
            new.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"ok": False, "error": f"创建目录失败: {e}"}
        if compat:
            ok, err = _probe_storage_dir(new)
            if not ok:
                return {"ok": False, "error": err}
        elif not os.access(new, os.W_OK):
            return {"ok": False, "error": "目标目录不可写"}
        if not move_files:
            self._cfg.set("cache_dir", str(new))
            if virtual_drive_compat is not None:
                self._cfg.set("virtual_drive_compat", compat)
            self._cfg.save()
            self._invalidate_cache_refresh()
            return {"ok": True, "cache_dir": str(new), "moved": 0, "failed": []}
        started = start_storage_migration_thread(
            new,
            old_override=old,
            virtual_drive_compat=(compat if virtual_drive_compat is not None else None),
        )
        if not started:
            return {"ok": False, "error": "有迁移任务正在运行"}
        # 后台迁移进行中，前端轮询 get_storage_migration_progress()
        return {"ok": True, "async": True, "cache_dir": str(new)}

    def _invalidate_cache_refresh(self):
        fc = getattr(self._webui, "_file_cache", None)
        if fc is not None:
            fc.clear()
        try:
            if len(webview.windows) > 0:
                webview.windows[0].evaluate_js("refreshMemes();")
        except Exception:
            pass

    def get_storage_migration_progress(self) -> dict:
        return get_storage_migration_progress()

    def cancel_storage_migration(self) -> dict:
        return cancel_storage_migration()

    # ─── 本地备份与恢复 ───

    def backup_get_info(self) -> dict:
        """备份目录与已有备份列表"""
        d = backup.get_backup_dir(self._cfg)
        return {
            "ok": True,
            "backup_dir": str(d),
            "custom": bool(self._cfg.get("backup_dir", "")),
            "list": backup.list_backups(d),
        }

    def backup_pick_dir(self) -> dict:
        """选择备份输出目录并立即生效"""
        result = self._dialog(webview.FileDialog.FOLDER)
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (tuple, list)) else result
        new = Path(path).resolve()
        try:
            new.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"ok": False, "error": f"创建目录失败: {e}"}
        if not os.access(new, os.W_OK):
            return {"ok": False, "error": "目标目录不可写"}
        ok, err = backup.validate_backup_dir(
            str(new),
            self._cfg.cache_dir,
            allow_unresolved=self._cfg.get("virtual_drive_compat", False) is True,
        )
        if not ok:
            return {"ok": False, "error": err}
        self._cfg.set("backup_dir", str(new))
        self._cfg.save()
        return {"ok": True, "backup_dir": str(new)}

    def backup_create(self) -> dict:
        if start_backup_thread("create"):
            return {"ok": True, "async": True}
        return {"ok": False, "error": "有备份/恢复任务正在进行"}

    def backup_delete(self, name: str) -> dict:
        if backup.delete_backup(backup.get_backup_dir(self._cfg), name):
            return {"ok": True}
        return {"ok": False, "error": "删除失败：无效的备份文件名或文件不存在"}

    def backup_pick_zip(self) -> dict:
        """选择要恢复的备份 ZIP 文件"""
        result = self._dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=("备份包 (*.zip)",),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        path = result[0] if isinstance(result, (tuple, list)) else result
        return {"ok": True, "path": path}

    def backup_restore(self, path: str) -> dict:
        """从备份 ZIP 恢复；仅允许空库，拒绝时返回当前条数"""
        if (
            get_db().has_any_data()
        ):  # 含 stego 载体行与空分组/标签，任何业务数据都视为非空
            return {
                "ok": False,
                "error": "当前数据库已有表情/分组/标签等数据，恢复仅在空库时可用。"
                "如需保留现有数据，请先手动备份或使用远端同步。",
            }
        if not Path(path).is_file():
            return {"ok": False, "error": "备份文件不存在"}
        if start_backup_thread("restore", path):
            return {"ok": True, "async": True}
        return {"ok": False, "error": "有备份/恢复任务正在进行"}

    def backup_progress(self) -> dict:
        return get_backup_progress()

    def qqnt_default_dir(self, base: str, qq_number: str) -> dict:
        """按账号生成默认输出目录（昵称+QQ号）"""
        try:
            d = qqnt_extract.get_default_output_dir(
                base, qq_number, fetch_nickname=True
            )
        except Exception:
            return {"ok": False}
        return {"ok": True, "dir": d}

    def qqnt_start(
        self,
        qq_number: str,
        output_dir: str,
        image_only: bool = False,
        overwrite: bool = False,
    ) -> dict:
        ok = start_qqnt_extract(
            qq_number,
            output_dir,
            image_only=image_only,
            overwrite=overwrite,
            ini_path=self._cfg.get("qqnt_ini_path") or None,
            userdata_save_path=self._cfg.get("qqnt_userdata_path") or None,
        )
        return {"ok": ok}

    def qqnt_get_progress(self) -> dict:
        return get_qqnt_progress()

    def qqnt_cancel(self):
        cancel_qqnt_extract()

    def qqnt_open_dir(self, path: str) -> bool:
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":
                import subprocess

                subprocess.Popen(["open", path])
            else:
                import subprocess

                subprocess.Popen(["xdg-open", path])
            return True
        except Exception:
            return False

    def import_memes(self) -> dict:
        result = self._dialog(
            webview.FileDialog.OPEN,
            allow_multiple=True,
            file_types=("图片文件 (*.png;*.jpg;*.jpeg;*.gif;*.webp;*.bmp)",),
        )
        if not result:
            return {"ok": False, "cancelled": True}
        r = self._webui._do_import(result)
        return {
            "ok": True,
            "imported": len(r.get("ids") or []),
            "rejected": r.get("rejected", 0),
        }

    def close_settings(self):
        self._webui.close_settings()

    def get_current_version(self) -> str:
        from . import __version__

        return __version__

    # 非阻塞检查更新：新鲜缓存即返，首次/过期/force 触发后台检查返回 pending
    def check_update(self, debug=False, force=False) -> dict:
        info = updater.update_info(force=bool(debug) or bool(force))
        if debug or self._webui._update_debug:
            info["has_update"] = True
        return info

    def start_download(self, url: str) -> bool:
        return updater.start_download(url)

    def get_download_progress(self) -> dict:
        return updater.get_download_progress()

    def run_downloaded_installer(self) -> bool:
        path = updater.last_downloaded_path()
        ok = updater.run_downloaded_installer()
        if ok:
            self._webui._schedule_quit(path)
        return ok

    def rerun_installer(self) -> bool:
        """重新运行已下载的安装包（启动失败后不必重新下载）"""
        path = updater.last_downloaded_path()
        if not path:
            return False
        ok = updater.run_installer(path)
        if ok:
            self._webui._schedule_quit(path)
        return ok

    def download_update(self, url: str) -> dict:
        path = updater.download_release(url)
        if not path:
            return {"ok": False, "error": "download failed"}
        ok = updater.run_installer(path)
        return {"ok": ok, "error": "" if ok else "run installer failed"}

    def get_sync_progress(self) -> dict:
        return sync_module.get_sync_progress()

    def sync_push(self, delete_remote: bool = None) -> dict:
        try:
            self._webui.ensure_local_thumbs()
            r = sync_module.push(delete_remote=delete_remote)
            r["ok"] = True
            return r
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "failed_files": sync_module.get_sync_progress().get("failed_items", []),
            }
        finally:
            _sync_status_invalidate()

    def sync_pull(self, remove_local: bool = None) -> dict:
        try:
            r = sync_module.pull(remove_local=remove_local)
            r["ok"] = True
            # 刷新主窗口数据
            try:
                if len(webview.windows) > 0:
                    webview.windows[0].evaluate_js(
                        "refreshMemes();refreshTags();refreshCollections();refreshCategories();"
                    )
            except Exception:
                pass
            return r
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "failed_files": sync_module.get_sync_progress().get("failed_items", []),
            }
        finally:
            _sync_status_invalidate()

    def sync_test(self) -> str:
        try:
            from .sync import sync_test as _test

            return _test()
        except Exception as e:
            return str(e)

    def delete_all_local(self) -> dict:
        """删除本地所有表情包"""
        try:
            import shutil

            db = get_db()
            db.delete_all()
            cache = self._cfg.cache_dir
            if cache.exists():
                # 类别原件现在位于 cache_dir 下的多级目录；清空时递归移除
                # 所有缓存子项，但保留存储根目录本身供后续导入使用。
                for child in cache.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
            cache.mkdir(parents=True, exist_ok=True)
            thumbs = self._cfg.thumbnail_dir
            if thumbs.exists():
                for f in thumbs.iterdir():
                    if f.is_file():
                        f.unlink()
            from .manifest import build as build_manifest

            build_manifest()
            try:
                if len(webview.windows) > 0:
                    webview.windows[0].evaluate_js(
                        "refreshMemes();refreshTags();refreshCollections();refreshCategories();"
                    )
            except Exception:
                pass
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def delete_all_cloud(self) -> dict:
        """删除云端所有表情包"""
        try:
            return sync_module.delete_all_remote()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_remote_orphans(self, delete: bool = False) -> dict:
        """扫描云端孤儿文件；delete=True 时物理删除"""
        try:
            return sync_module.cleanup_remote_orphans(delete=delete)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def check_sync_status(self) -> dict:
        """比较本地与云端同步状态"""
        try:
            from .sync import download_index

            manifest = download_index()
            if not manifest:
                return {"ok": False, "error": "无法获取远端索引"}
            _sync_status_set_manifest(manifest)
            db = get_db()
            local_rows = db.search(keyword="", tags=None, limit=999999)
            local_count = len(local_rows)
            local_filenames = {r["filename"] for r in local_rows}
            remote_memes = manifest.get("memes", [])
            remote_count = len(remote_memes)
            remote_filenames = {m["filename"] for m in remote_memes}
            local_extra = local_filenames - remote_filenames
            local_missing = remote_filenames - local_filenames
            if not local_extra and not local_missing:
                return {
                    "ok": True,
                    "synced": True,
                    "local_count": local_count,
                    "remote_count": remote_count,
                }
            return {
                "ok": True,
                "synced": False,
                "local_count": local_count,
                "remote_count": remote_count,
                "local_extra": len(local_extra),
                "local_missing": len(local_missing),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}


def _file_sha256(path, read_priority="foreground"):
    """计算文件 SHA-256"""
    import hashlib

    sha = hashlib.sha256()
    with guarded_read(priority=read_priority):
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
    return sha.hexdigest()


# 感知哈希去重（pHash）：内容近似但 SHA-256 不同时，判定汉明距离阈值
_PHASH_SIMILAR_DIST = 12  # 64 位感知哈希的汉明距离阈值，≤此值视为近似
_PHASH_CACHE = {}  # 文件路径 → (mtime, size, hash)：避免对大库重复全解码
_PHASH_CACHE_LOCK = threading.Lock()
_PHASH_BACKFILL_LOCK = threading.Lock()
_PHASH_BACKFILLING = False  # 当前是否有后台回填线程在跑（防重入）
_IMPORT_LOCK = threading.Lock()  # 串行化 _do_import 关键区，防并发导入同图重复
_LARGE_MEME_IMPORT_BYTES = 20 * 1024 * 1024
_CHAT_RECORD_TICKET_BYTES = 64 * 1024

# 后台导入进度（文件夹/文件对话框/剪贴板等交互式多文件导入）
_IMPORT_JOB_STATE = {
    "status": "idle",  # idle|running|done|error|cancelled
    "progress": 0,
    "message": "",
    "current": "",
    "done": 0,
    "total": 0,
    "imported": 0,
    "rejected": 0,
    "skipped_dup": 0,
    "duplicate_ids": [],
    "error": "",
    "token": None,  # 任务代次：旧 worker 令牌不匹配时不覆盖新任务状态
}
_IMPORT_JOB_LOCK = threading.Lock()
_IMPORT_JOB_CANCEL = False
_ALLOWED_IMPORT_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _set_import_job(**kw):
    with _IMPORT_JOB_LOCK:
        _IMPORT_JOB_STATE.update(**kw)


def _set_import_job_current(my_token, **kw):
    """仅当 my_token 仍是当前任务令牌时更新状态（旧 worker 在开新任务后不再覆盖）"""
    with _IMPORT_JOB_LOCK:
        if _IMPORT_JOB_STATE.get("token") != my_token:
            return False
        _IMPORT_JOB_STATE.update(**kw)
        return True


def get_import_progress() -> dict:
    with _IMPORT_JOB_LOCK:
        return dict(_IMPORT_JOB_STATE)


def cancel_import_job():
    global _IMPORT_JOB_CANCEL
    with _IMPORT_JOB_LOCK:
        if _IMPORT_JOB_STATE["status"] == "running":
            _IMPORT_JOB_CANCEL = True
    return {"ok": True}


def _import_job_progress_cb(my_token, done, total, current):
    """_do_import 逐文件回调：更新进度（仅限当前任务令牌）；取消时返回 False 中断"""
    cancel = False
    with _IMPORT_JOB_LOCK:
        cancel = _IMPORT_JOB_CANCEL
    pct = int(done * 100 / total) if total else 100
    _set_import_job_current(
        my_token,
        progress=pct,
        done=done,
        total=total,
        current=current,
        message=f"导入中 {done}/{total}",
        status="cancelled" if cancel else "running",
    )
    return False if cancel else None


def _import_job_worker(
    webui,
    files,
    names,
    make_collection,
    folder_name,
    my_token,
    large_file_mode,
):
    """后台执行交互式导入（folder/文件对话框/剪贴板），逐文件报进度"""
    global _IMPORT_JOB_CANCEL
    db = get_db()

    def cb(done, total, current):
        return _import_job_progress_cb(my_token, done, total, current)

    try:
        if _IMPORT_JOB_CANCEL:
            _set_import_job_current(my_token, status="cancelled")
            return
        r = webui._do_import(files, names, cb, large_file_mode=large_file_mode)
        ids = r.get("ids") or []
        imported = len(ids)
        rejected = r.get("rejected", 0)
        skipped_dup = r.get("skipped_dup", 0)
        duplicate_ids = r.get("duplicate_ids") or []
        with _IMPORT_JOB_LOCK:
            was_cancel = _IMPORT_JOB_CANCEL
            is_current = _IMPORT_JOB_STATE.get("token") == my_token
        if not is_current:
            # 本任务已不是当前令牌（被新任务取代），立刻返回，
            # 避免旧 worker 执行 create_collection/add_to_collection 等副作用污染新任务
            return
        if not was_cancel:
            # 正常完成：进度满格、done 为全部
            _set_import_job_current(
                my_token,
                imported=imported,
                rejected=rejected,
                skipped_dup=skipped_dup,
                duplicate_ids=duplicate_ids,
                progress=100,
                done=len(files),
                total=len(files),
            )
        else:
            # 取消：保留 progress_cb 已更新的实际 done/total，不强制 100%
            _set_import_job_current(
                my_token,
                imported=imported,
                rejected=rejected,
                skipped_dup=skipped_dup,
                duplicate_ids=duplicate_ids,
            )
            _set_import_job_current(my_token, status="cancelled", message="导入已取消")
            return
        collection_id = None
        if make_collection and ids and folder_name:
            collection_id = db.create_collection(folder_name)
            if collection_id > 0:
                for mid in ids:
                    db.add_to_collection(mid, collection_id)

                build_manifest()
        _set_import_job_current(
            my_token,
            status="done",
            progress=100,
            message="导入完成",
            collection_id=collection_id,
        )
    except Exception as e:
        logger.error("import job error: %s", e)
        _set_import_job_current(
            my_token, status="error", error=str(e), message="导入失败"
        )
    finally:
        # 仅当仍是当前任务（token 匹配）时才重置取消标志；
        # 旧 worker 收尾不清新任务的取消标志，保证 token 代次隔离
        with _IMPORT_JOB_LOCK:
            if _IMPORT_JOB_STATE.get("token") == my_token:
                _IMPORT_JOB_CANCEL = False


def start_import_job(
    webui,
    files,
    names,
    make_collection=False,
    folder_name="",
    large_file_mode=None,
):
    """启动后台导入，立即返回；前端轮询 get_import_progress()"""
    import secrets

    global _IMPORT_JOB_CANCEL
    with _IMPORT_JOB_LOCK:
        if _IMPORT_JOB_STATE["status"] == "running":
            return False
        token = secrets.token_hex(6)
        _IMPORT_JOB_CANCEL = False
        _IMPORT_JOB_STATE.update(
            token=token,
            status="running",
            progress=0,
            message="开始导入",
            current="",
            done=0,
            total=len(files),
            imported=0,
            rejected=0,
            skipped_dup=0,
            duplicate_ids=[],
            error="",
        )
    threading.Thread(
        target=_import_job_worker,
        args=(
            webui,
            files,
            names,
            make_collection,
            folder_name,
            token,
            large_file_mode,
        ),
        daemon=True,
    ).start()
    return True


# pHash 的一次性 DCT 基（模块加载时预计算，跨图复用）：_PHASH_DCT_COS[k][x]、
# _PHASH_DCT_NORM[k]（k=0 用 1.0，否则 sqrt(0.5)）
_PHASH_DCT_COS = [
    [math.cos((2 * x + 1) * k * math.pi / (2 * 32)) for x in range(32)]
    for k in range(8)
]
_PHASH_DCT_NORM = [1.0 if k == 0 else math.sqrt(0.5) for k in range(8)]


def _phash_path_cached(fpath, read_priority="foreground"):
    """带 (mtime, size) 校验的 phash 缓存，文件未变时避免重复解码"""
    with guarded_read(priority=read_priority):
        try:
            st = os.stat(fpath)
            key = (st.st_mtime, st.st_size)
        except OSError:
            return 0
        with _PHASH_CACHE_LOCK:
            cached = _PHASH_CACHE.get(fpath)
            if cached and cached[0] == key:
                return cached[1]
        h = _perceptual_hash_path(fpath, read_priority=read_priority)
    if h:
        with _PHASH_CACHE_LOCK:
            if len(_PHASH_CACHE) >= 4096:
                _PHASH_CACHE.clear()  # 简单防膨胀：超过容量整体清空重建
            _PHASH_CACHE[fpath] = (key, h)
    return h


def _gray_pixels(img):
    """取 32x32 灰度像素列表（兼容新旧 Pillow：get_flattened_data/getdata）"""
    from PIL import Image

    small = img.convert("L").resize((32, 32), Image.LANCZOS)
    getter = getattr(small, "get_flattened_data", None)
    if getter is not None:
        return list(getter())
    return list(small.getdata())


def _perceptual_hash(img) -> int:
    """计算 PIL 图像的 64 位感知哈希（pHash，8x8 可分离 DCT）；比均值哈希判别力更强"""
    try:
        px = _gray_pixels(img)
        rows = [[0.0] * 32 for _ in range(8)]
        for k in range(8):
            ck = _PHASH_DCT_COS[k]
            for x in range(32):
                ckx = ck[x]
                row = rows[k]
                base = x * 32
                for y in range(32):
                    row[y] += px[base + y] * ckx
        dct = [[0.0] * 8 for _ in range(8)]
        for k in range(8):
            ck = _PHASH_DCT_NORM[k]
            for j in range(8):
                s = 0.0
                cl = _PHASH_DCT_COS[j]
                for y in range(32):
                    s += rows[k][y] * cl[y]
                dct[k][j] = ck * _PHASH_DCT_NORM[j] * s / 4.0
        coeffs = [dct[k][j] for k in range(8) for j in range(8)]
        median = sorted(coeffs)[len(coeffs) // 2]
        h = 0
        for i, c in enumerate(coeffs):
            if c > median:
                h |= 1 << i
        return h
    except Exception:
        return 0


def _perceptual_hash_path(path, read_priority="foreground") -> int:
    """对图片文件计算感知哈希，失败返回 0（表示不可用）"""
    if not HAS_PIL:
        return 0
    try:
        with guarded_read(priority=read_priority):
            with PILImage.open(path) as img:
                # 动画取首帧
                if getattr(img, "n_frames", 1) > 1:
                    img.seek(0)
                return _perceptual_hash(img)
    except Exception:
        return 0


def _phash_hamming(a: int, b: int) -> int:
    """两个 64 位哈希的汉明距离"""
    return bin(a ^ b).count("1")


# 等待用户选择大文件归档方式的普通媒体导入项。
_PENDING_LARGE_IMPORT = {}
_PENDING_LARGE_IMPORT_LOCK = threading.Lock()
_PENDING_LARGE_IMPORT_TTL = 300


def _large_import_count(paths) -> int:
    """统计超过交互阈值的文件；无法读取大小时按非大文件处理。"""
    count = 0
    for path in paths or []:
        try:
            if os.path.getsize(path) > _LARGE_MEME_IMPORT_BYTES:
                count += 1
        except OSError:
            pass
    return count


def _cleanup_pending_large_item(item):
    """清理待决策项拥有的临时文件。"""
    for path in item.get("cleanup_paths") or []:
        try:
            os.unlink(path)
        except OSError:
            pass


def _register_pending_large_import(
    paths,
    names=None,
    make_collection=False,
    folder_name="",
    kind="job",
    cleanup_paths=None,
) -> str:
    """保存一次大文件导入上下文并返回短期令牌。"""
    import secrets

    token = secrets.token_hex(16)
    now = time.time()
    with _PENDING_LARGE_IMPORT_LOCK:
        _PENDING_LARGE_IMPORT[token] = {
            "paths": list(paths or []),
            "names": list(names) if names else None,
            "make_collection": bool(make_collection),
            "folder_name": folder_name or "",
            "kind": kind,
            "cleanup_paths": list(cleanup_paths or []),
            "ts": now,
        }
        for key, value in list(_PENDING_LARGE_IMPORT.items()):
            if now - value.get("ts", 0) > _PENDING_LARGE_IMPORT_TTL:
                _cleanup_pending_large_item(value)
                del _PENDING_LARGE_IMPORT[key]
    return token


def _hold_large_import_file(path, oname="") -> dict:
    """复制单个临时/拖放文件，确保对话框显示期间源文件仍然可用。"""
    import shutil
    import tempfile

    ext = os.path.splitext(path)[1] or ".png"
    fd, held = tempfile.mkstemp(prefix="ohmm_large_", suffix=ext)
    os.close(fd)
    try:
        shutil.copy2(path, held)
    except Exception:
        try:
            os.unlink(held)
        except OSError:
            pass
        raise
    token = _register_pending_large_import(
        [held],
        [oname] if oname else None,
        kind="single",
        cleanup_paths=[held],
    )
    return {
        "ok": False,
        "large_file_pending": True,
        "token": token,
        "count": 1,
        "threshold_mb": _LARGE_MEME_IMPORT_BYTES // (1024 * 1024),
    }


def _pop_pending_large_import(token: str) -> dict:
    """取出仍有效的大文件导入决策项。"""
    with _PENDING_LARGE_IMPORT_LOCK:
        item = _PENDING_LARGE_IMPORT.pop(token, None)
    if item and time.time() - item.get("ts", 0) > _PENDING_LARGE_IMPORT_TTL:
        _cleanup_pending_large_item(item)
        return None
    return item


# 等待用户决策的相似图导入项（token → 待导入信息），避免 tmp 被清理
_PENDING_SIMILAR = {}
_PENDING_SIMILAR_LOCK = threading.Lock()
_PENDING_SIMILAR_TTL = 300  # 秒，超时自动丢弃


def _register_pending_similar(
    path: str, oname: str, candidates: list, large_file_mode=None
) -> str:
    import secrets

    token = secrets.token_hex(16)
    with _PENDING_SIMILAR_LOCK:
        _PENDING_SIMILAR[token] = {
            "path": path,
            "oname": oname,
            "candidates": candidates,
            "large_file_mode": large_file_mode,
            "ts": time.time(),
        }
        # 清理过期项
        now = time.time()
        for k, v in list(_PENDING_SIMILAR.items()):
            if now - v["ts"] > _PENDING_SIMILAR_TTL:
                try:
                    os.unlink(v["path"])
                except OSError:
                    pass
                del _PENDING_SIMILAR[k]
    return token


def _pop_pending_similar(token: str) -> dict:
    with _PENDING_SIMILAR_LOCK:
        item = _PENDING_SIMILAR.pop(token, None)
        if item and time.time() - item.get("ts", 0) > _PENDING_SIMILAR_TTL:
            # 已过期：删除临时文件并视为不存在
            try:
                os.unlink(item["path"])
            except OSError:
                pass
            item = None
    return item


def _iter_cache_images(cache_dir, read_priority="foreground"):
    """遍历缓存目录下所有在库图片，跳过应用管理的专用目录。"""
    try:
        for root, dirs, files in os.walk(str(cache_dir)):
            for d in list(dirs):
                is_chat_media = (
                    Path(root) == Path(cache_dir)
                    and d.casefold() == CHAT_RECORD_MEDIA_DIRNAME.casefold()
                )
                if d.casefold() == "thumbnails" or is_chat_media:
                    dirs.remove(d)
            for name in files:
                if _detect_image_ext(
                    os.path.join(root, name), read_priority=read_priority
                ):
                    yield name, os.path.join(root, name)
    except OSError:
        return


def _build_cache_index(read_priority="foreground"):
    """一次性构建 库中 filename → 绝对路径 映射（供批量回填，避免逐行 walk）"""
    idx = {}
    cfg = get_config()
    for fname, fpath in _iter_cache_images(
        Path(str(cfg.cache_dir)), read_priority=read_priority
    ):
        idx[fname] = fpath
    return idx


def _resolve_cache_file(filename, index=None):
    """根据 DB 中的 filename 定位缓存文件绝对路径（一级优先，退化用 index/遍历）"""
    if index is not None:
        return index.get(filename) or ""
    cfg = get_config()
    cache_dir = cfg.cache_dir
    direct = cache_dir / filename
    if direct.is_file():
        return str(direct)
    for _fname, fpath in _iter_cache_images(Path(str(cache_dir))):
        if _fname == filename:
            return fpath
    return ""


def _backfill_phash_background(rows, index):
    """后台线程：为 phash 为空的旧库行补算并写回 DB（不阻塞导入）"""
    global _PHASH_BACKFILLING
    db = get_db()
    try:
        if index is None:
            index = _build_cache_index(read_priority="background")
        for row in rows:
            fname = row.get("filename")
            fpath = (
                index.get(fname) if index is not None else _resolve_cache_file(fname)
            )
            if not fpath:
                continue
            try:
                phash = _phash_path_cached(fpath, read_priority="background")
            except Exception:
                continue
            # 无条件写入（set_perceptual_hash 内部处理 0→"0" 占位）
            db.set_perceptual_hash(row["id"], phash)
    finally:
        with _PHASH_BACKFILL_LOCK:
            _PHASH_BACKFILLING = False


def _ensure_backfill_daemon(rows, index):
    """若当前无后台回填在跑，启动一个（防重入）；已跑则跳过"""
    global _PHASH_BACKFILLING
    if not rows:
        return
    with _PHASH_BACKFILL_LOCK:
        if _PHASH_BACKFILLING:
            return
        _PHASH_BACKFILLING = True
    try:
        threading.Thread(
            target=_backfill_phash_background,
            args=(rows, index),
            daemon=True,
        ).start()
    except Exception:
        with _PHASH_BACKFILL_LOCK:
            _PHASH_BACKFILLING = False


def _find_similar_candidates(img_path, read_priority="foreground"):
    """基于 DB 感知哈希的全库比对：只解码新图，存量直接读 DB（微秒级）

    返回候选列表（全库比对，无截断）。phash 为空的旧库行惰性回填（算一次写回 DB）。
    """
    cfg = get_config()
    db = get_db()
    if not cfg.cache_dir or not HAS_PIL:
        return []
    target_hash = _phash_path_cached(img_path, read_priority=read_priority)
    if target_hash == 0:
        return []
    rows = db.get_all_phash()
    matches = []
    missing = [r for r in rows if not r.get("perceptual_hash")]
    # 旧库回填始终在后台完成。不能为了本次单图导入同步遍历整个虚拟盘，
    # 否则精确哈希已经完成后仍会给 CD2 队列灌入整库读取请求。
    if missing:
        _ensure_backfill_daemon(missing, None)
    for row in rows:
        fname = row.get("filename")
        phash = row.get("perceptual_hash")
        if not phash:
            continue  # 未回填（已交给后台），本次跳过
        try:
            phash = int(phash, 16)
        except (TypeError, ValueError):
            continue
        if not phash:
            continue  # 占位 "0"（无感知内容）跳过比较
        dist = _phash_hamming(target_hash, phash)
        if dist <= _PHASH_SIMILAR_DIST:
            matches.append(
                {
                    "id": row["id"],
                    "filename": fname,
                    "name": row.get("original_name") or fname,
                    "file_hash": row.get("file_hash", ""),
                    "distance": dist,
                }
            )
    matches.sort(key=lambda x: x["distance"])
    # DB 比对为微秒级整数运算，全库比对无截断漏检
    return matches[:5]


def _prepare_image_import(path, read_priority="critical") -> dict:
    """单图导入前的去重决策：返回 hash_dup / similar / ok / rejected"""
    db = get_db()
    w = h = 0
    with guarded_read(priority=read_priority):
        try:
            fsize = os.path.getsize(path)
        except OSError:
            fsize = 0
        if HAS_PIL:
            try:
                with PILImage.open(path) as img:
                    w, h = img.size
            except Exception:
                pass
    if fsize > _IMPORT_MAX_BYTES:
        return {
            "status": "rejected",
            "error": "图片超过 %d MiB 原件上限，已跳过"
            % (_IMPORT_MAX_BYTES // (1024 * 1024)),
        }
    max_px = get_import_max_px(get_config())
    if max_px and max(w, h) > max_px:
        return {
            "status": "rejected",
            "error": f"图片最长边 {max(w, h)}px，超过设置的 {max_px}px 上限，已跳过",
        }
    fhash = _file_sha256(path, read_priority=read_priority)
    row = db.get_by_hash(fhash)
    if row:
        return {
            "status": "hash_dup",
            "hash": fhash,
            "existing_id": row["id"],
        }
    candidates = _find_similar_candidates(path, read_priority=read_priority)
    if candidates:
        return {"status": "similar", "candidates": candidates}
    return {"status": "ok", "hash": fhash}


def _detect_image_ext(path, read_priority="foreground"):
    """读取文件头魔数识别真实扩展名（QQ 保存常为 .jpg，实为 png/webp 等），未知返回空串"""  # noqa: E501
    try:
        with guarded_read(priority=read_priority):
            with open(path, "rb") as f:
                return adb_util._detect_ext(f.read(16))
    except OSError:
        return ""


def _try_decode_stego(gif_path, read_priority="foreground"):
    """实验性：检测 GIF 隐写并解码还原原图到临时文件；非隐写/失败返回 None"""
    try:
        with guarded_read(priority=read_priority):
            with open(gif_path, "rb") as f:
                if b"STG3" not in f.read():
                    return None
        from .gif_stego import decode as stego_decode
    except Exception as e:
        logger.warning(f"_try_decode_stego detect: {e}")
        return None
    try:
        import glob
        import tempfile
        import uuid

        base = os.path.join(tempfile.gettempdir(), f"ohmm_dec_{uuid.uuid4().hex}")
        with guarded_read(priority=read_priority):
            stego_decode(gif_path, base, quiet=True)
        cands = [
            c
            for c in glob.glob(base + "*")
            if os.path.isfile(c) and os.path.getsize(c) > 0
        ]
        return cands[0] if cands else None
    except Exception as e:
        logger.warning(f"_try_decode_stego decode: {e}")
        return None


class WebUI:
    """PyWebView UI 管理器"""

    def __init__(self, update_debug: bool = False, silent_start: bool = False):
        self._cfg = get_config()
        self._window = None
        self._settings_window = None
        self._port = self._find_free_port()
        self._bottle_thread = None
        self._api = JsApi(self)
        self._settings_api = SettingsApi(self)
        self._visible = False
        self._started = False
        self._pending_hide = False
        self._hotkey_session = False
        self._last_toggle_ms = 0
        self._last_tray_toggle_ms = 0
        self._window_state_lock = threading.Lock()
        self._library_refresh_lock = threading.Lock()
        self._library_refresh_timer = None
        self._library_refresh_kind = "content"
        self._cache_scan_lock = threading.Lock()
        self._cache_scan_thread = None
        self._on_hotkey_change_cb = None
        self._update_debug = update_debug
        self._silent_start = silent_start
        from .library_events import set_change_callback

        set_change_callback(self._on_library_changed)

    def _on_library_changed(self, kind="content"):
        """合并短时间内的后台入库通知，避免连续触发重复查询。"""
        with self._library_refresh_lock:
            if kind == "structure":
                self._library_refresh_kind = "structure"
            if self._library_refresh_timer is not None:
                return
            timer = threading.Timer(0.12, self._flush_library_refresh)
            timer.daemon = True
            self._library_refresh_timer = timer
            timer.start()

    def _flush_library_refresh(self):
        with self._library_refresh_lock:
            kind = self._library_refresh_kind
            self._library_refresh_kind = "content"
            self._library_refresh_timer = None
        window = self._window
        if window is None:
            return
        try:
            window.evaluate_js(
                "window.refreshLibrary && window.refreshLibrary(%r)" % kind
            )
        except Exception as exc:
            logger.debug("library refresh notification failed: %s", exc)

    def _init_lan(self):
        from . import lan

        lan.set_confirm_callback(self._lan_confirm_cb)

    def set_on_hotkey_change(self, cb):
        self._on_hotkey_change_cb = cb

    def _lan_confirm_cb(self, device: dict):
        """LAN 设备连接确认：转到设置窗口弹窗，等待 JS 回传结果"""
        import json

        from . import lan

        win = self._settings_window
        if win is None:
            if not self._create_settings_window():
                lan.confirm_device(False)
                return
            win = self._settings_window
        try:
            self.focus_settings_window()
            js = "window.showLanDeviceConfirm && showLanDeviceConfirm(%s)" % json.dumps(
                device, ensure_ascii=False
            )
            win.evaluate_js(js)
        except Exception as e:
            # 推送失败不立即拒绝：get_status 轮询 pending_confirm 兜底展示
            logger.warning(f"lan confirm dialog push error: {e}")

    # --- 窗口控制（从任何线程调用安全）---

    def _get_hotkey_window_position(self):
        """获取 Windows 光标所在显示器工作区内的热键窗口位置"""
        if platform.system() != "Windows":
            return None
        try:
            import ctypes
            from ctypes import wintypes

            class POINT(ctypes.Structure):
                _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))

            class RECT(ctypes.Structure):
                _fields_ = (
                    ("left", wintypes.LONG),
                    ("top", wintypes.LONG),
                    ("right", wintypes.LONG),
                    ("bottom", wintypes.LONG),
                )

            class MONITORINFO(ctypes.Structure):
                _fields_ = (
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", wintypes.DWORD),
                )

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetCursorPos.argtypes = (ctypes.POINTER(POINT),)
            user32.GetCursorPos.restype = wintypes.BOOL
            user32.MonitorFromPoint.argtypes = (POINT, wintypes.DWORD)
            user32.MonitorFromPoint.restype = wintypes.HMONITOR
            user32.GetMonitorInfoW.argtypes = (
                wintypes.HMONITOR,
                ctypes.POINTER(MONITORINFO),
            )
            user32.GetMonitorInfoW.restype = wintypes.BOOL

            cursor = POINT()
            if not user32.GetCursorPos(ctypes.byref(cursor)):
                raise ctypes.WinError(ctypes.get_last_error())
            monitor = user32.MonitorFromPoint(cursor, 2)
            if not monitor:
                raise ctypes.WinError(ctypes.get_last_error())
            monitor_info = MONITORINFO()
            monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
            if not user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
                raise ctypes.WinError(ctypes.get_last_error())

            work = monitor_info.rcWork
            return _find_hotkey_window_position(
                (cursor.x, cursor.y),
                (work.left, work.top, work.right, work.bottom),
                self._window.width,
                self._window.height,
            )
        except Exception as e:
            logger.warning("hotkey window position error: %s", e)
            return None

    # 显示主窗口并清理非热键会话状态
    def show(self):
        """显示主窗口（线程安全：持锁完成状态更新 + 原生窗口操作）"""
        if self._window is None:
            return
        with self._window_state_lock:
            self._visible = True
            self._hotkey_session = False
            self._pending_hide = False
            self._show_on_gui()
            win = self._window
        self._focus_search_async(win)

    def _show_on_gui(self):
        """必须在 _window_state_lock 内调用（状态与窗口操作原子化）"""
        win = self._window
        if win is None:
            return
        # 先撤销最小化：Show/Activate 不重置 WindowState，Win+D/显示桌面/任务栏
        # 最小化后只 show 会让窗口"以最小化状态显示"，屏幕上永远看不到内容
        try:
            shown = getattr(getattr(win, "events", None), "shown", None)
            if hasattr(win, "restore") and (shown is None or shown.is_set()):
                win.restore()
        except Exception as e:
            logger.debug("restore window: %s", e)
        # 置顶一下提升 z-order，随即复位不长期置顶；失败绝不能阻断后续 show
        try:
            win.on_top = True
            win.on_top = False
        except Exception as e:
            logger.debug("raise window z-order: %s", e)
        try:
            if callable(getattr(win, "show", None)):
                win.show()
        except Exception as e:
            logger.warning("show window error: %s", e)
        try:
            if callable(getattr(win, "focus", None)):
                win.focus()
        except Exception as e:
            logger.debug("focus window: %s", e)
        self._ensure_window_on_screen(win)

    # 窗口至少要有这么大的可见区域才算「在屏内」，否则移回主屏（1px 交集不算可见）
    _MIN_VISIBLE_W = 200
    _MIN_VISIBLE_H = 120
    _MIN_VISIBLE_RATIO = 0.3

    def _ensure_window_on_screen(self, win=None):
        """窗口可见区域过小时移回主屏（换显示器/RDP/DPI 后常见）"""
        win = win or self._window
        if win is None:
            return
        try:
            events = getattr(win, "events", None)
            shown = getattr(events, "shown", None)
            if shown is not None and not shown.is_set():
                return
            screens = getattr(webview, "screens", None) if HAS_WEBVIEW else None
            screens = screens() if callable(screens) else screens
            if not screens:
                return
            x, y = int(win.x), int(win.y)
            width, height = int(win.width), int(win.height)
        except Exception as e:
            logger.debug("window geometry read: %s", e)
            return
        if width <= 0 or height <= 0:
            return
        need_w = min(self._MIN_VISIBLE_W, width * self._MIN_VISIBLE_RATIO)
        need_h = min(self._MIN_VISIBLE_H, height * self._MIN_VISIBLE_RATIO)
        for screen in screens:
            sx, sy = int(screen.x), int(screen.y)
            sw, sh = int(screen.width), int(screen.height)
            visible_w = min(x + width, sx + sw) - max(x, sx)
            visible_h = min(y + height, sy + sh) - max(y, sy)
            if visible_w >= need_w and visible_h >= need_h:
                return
        primary = screens[0]
        tx = int(primary.x) + max(0, (int(primary.width) - width) // 2)
        ty = int(primary.y) + max(0, (int(primary.height) - height) // 2)
        try:
            win.move(tx, ty)
            logger.warning("window off-screen (%s,%s), moved to (%s,%s)", x, y, tx, ty)
        except Exception as e:
            logger.warning("move off-screen window failed: %s", e)

    def _focus_search_async(self, win=None):
        """搜索框聚焦放独立线程：JS 求值挂起时不占用窗口状态锁与托盘/热键线程"""
        win = win or self._window
        if win is None:
            return

        def _run():
            try:
                win.evaluate_js(
                    "focusSearch();window.refreshRecent&&window.refreshRecent()"
                )
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _on_window_minimized(self):
        """系统最小化（Win+D/显示桌面）后同步可见状态，使托盘/热键一次即可还原"""
        with self._window_state_lock:
            self._visible = False
            self._hotkey_session = False

    def hide(self):
        """隐藏主窗口（线程安全：持锁完成状态更新 + 原生窗口操作）"""
        if self._window is None:
            return
        with self._window_state_lock:
            self._visible = False
            self._hotkey_session = False
            self._pending_hide = False
            self._hide_on_gui()

    def _hide_on_gui(self):
        """必须在 _window_state_lock 内调用（状态与窗口操作原子化）"""
        try:
            self._save_window_position()
            if callable(self._window.hide):
                self._window.hide()
        except Exception:
            pass

    def minimize(self) -> bool:
        """原生最小化到任务栏；窗口仍属于可见会话，可由任务栏恢复。"""
        if self._window is None:
            return False
        with self._window_state_lock:
            try:
                self._save_window_position()
                minimize = getattr(self._window, "minimize", None)
                if not callable(minimize):
                    return False
                minimize()
                return True
            except Exception:
                logger.exception("minimize window failed")
                return False

    def toggle(self):
        with self._window_state_lock:
            visible = self._visible
        if visible:
            self.hide()
        else:
            self.show()

    _TRAY_DEBOUNCE_S = 0.4

    def toggle_safe(self):
        """托盘切换：去抖避免左键双击先显示后立刻隐藏"""
        if not self._window:
            return
        now = time.monotonic()
        with self._window_state_lock:
            if now - self._last_tray_toggle_ms < self._TRAY_DEBOUNCE_S:
                return
            self._last_tray_toggle_ms = now
        self.toggle()

    _HOTKEY_DEBOUNCE_S = 0.25

    def toggle_hotkey_safe(self):
        """按热键专用定位规则安全切换窗口（去抖 + 持锁 + 状态与窗口操作原子化）"""
        if not self._window:
            return
        now = time.monotonic()
        with self._window_state_lock:
            if now - self._last_toggle_ms < self._HOTKEY_DEBOUNCE_S:
                return
            self._last_toggle_ms = now
            visible = self._visible
            if visible:
                self._visible = False
                self._hotkey_session = False
                self._hide_on_gui()
                return
            if self._cfg.get("hotkey_show_at_mouse", False):
                try:
                    position = self._get_hotkey_window_position()
                    if position is not None:
                        self._window.move(*position)
                except Exception:
                    pass
            self._visible = True
            self._show_on_gui()
            # 在锁内设置 _hotkey_session，避免 show 与 schedule_hide 之间的竞争窗口
            self._hotkey_session = True
            win = self._window
        # 聚焦搜索框放到锁外：evaluate_js 可能阻塞，不能占着窗口状态锁
        self._focus_search_async(win)

    def schedule_hide(self):
        if self._cfg.get("disable_auto_hide", False):
            return False
        with self._window_state_lock:
            if not self._hotkey_session:
                return False
            self._pending_hide = True
        if self._window:
            self._run_on_gui(0.1, self._process_pending_hide)
        return True

    def _process_pending_hide(self):
        with self._window_state_lock:
            if not self._pending_hide:
                return
            self._pending_hide = False
        self.hide()

    def _run_on_gui(self, delay: float, func):
        """延时在 GUI 线程执行（pywebview Window 无 after 方法）"""
        if threading.current_thread() is threading.main_thread() and delay <= 0:
            func()
            return
        t = threading.Timer(delay, func)
        t.daemon = True
        t.start()

    def _schedule_quit(self, watch_path: str = ""):
        """等安装程序进程确认已启动后再结束当前进程（未启动则保留运行以便重试）"""

        def _force_kill():
            if watch_path and not updater.wait_installer_started(watch_path):
                logger.warning("installer not started, keep running: %s", watch_path)
                return
            time.sleep(1.5)
            if os.name == "nt":
                try:
                    import ctypes

                    ctypes.windll.kernel32.TerminateProcess(
                        ctypes.windll.kernel32.GetCurrentProcess(), 0
                    )
                except Exception:
                    pass
            os._exit(0)

        threading.Thread(target=_force_kill, daemon=True).start()

    @property
    def is_visible(self) -> bool:
        return self._visible

    # --- 缩略图 ---

    def _existing_daily_path(self, row) -> str:
        """只检查本地日常副本；绝不定位或读取虚拟盘原件。"""
        if not row:
            return ""
        if row.get("compressed_only"):
            try:
                path = self._category_file_path(
                    row.get("category_id"), row.get("filename", "")
                )
                return str(path) if path.is_file() else ""
            except (OSError, ValueError):
                return ""
        daily_name = row.get("daily_filename", "") or ""
        if (
            not daily_name
            or not _safe_serve_filename(daily_name)
            or row.get("daily_profile") != DAILY_COPY_VERSION
        ):
            return ""
        daily_path = self._cfg.derivative_dir / daily_name
        return str(daily_path) if daily_path.is_file() else ""

    def _get_thumbnail_path(
        self,
        meme_id,
        filename: str,
        size: int = 150,
        read_priority: str = "foreground",
    ) -> str:
        cache_dir = self._cfg.thumbnail_dir
        db = get_db()
        is_hash = sync_module._safe_sha(meme_id)
        row = None
        if not is_hash:
            getter = getattr(db, "get_by_id", None)
            row = getter(meme_id) if callable(getter) else None
        file_hash = meme_id if is_hash else (row or {}).get("file_hash", "")
        cache_name = (
            f"{file_hash}.webp"
            if sync_module._safe_sha(file_hash)
            else f"{meme_id}_{size}.webp"
        )
        thumb_path = cache_dir / cache_name
        if thumb_path.exists():
            return str(thumb_path)
        if is_hash:
            getter = getattr(db, "get_by_hash", None)
            row = getter(meme_id) if callable(getter) else None
        meme_path = self._existing_daily_path(row)
        local_daily = bool(meme_path)
        if not meme_path and row and row.get("id"):
            meme_path = self._find_daily_file(row, read_priority=read_priority)
        if not meme_path and (not row or not row.get("id")):
            with guarded_read(priority=read_priority):
                meme_path = self._find_meme_file(filename)
        if not meme_path or not HAS_PIL:
            return ""
        try:
            read_guard = (
                nullcontext() if local_daily else guarded_read(priority=read_priority)
            )
            with read_guard:
                with PILImage.open(meme_path) as img:
                    animated = (
                        bool(getattr(img, "is_animated", False))
                        and int(getattr(img, "n_frames", 1)) > 1
                    )
                    buf = io.BytesIO()
                    if animated:
                        # 动图逐帧生成动画 WebP；失败回退静态首帧，不留空
                        try:
                            frames, durations = _animated_thumb_frames(img, size)
                            frames[0].save(
                                buf,
                                "WEBP",
                                save_all=True,
                                append_images=frames[1:],
                                duration=durations,
                                loop=img.info.get("loop", 0) or 0,
                                quality=85,
                            )
                        except Exception as e:
                            logger.warning(f"animated thumb fallback {filename}: {e}")
                            buf = io.BytesIO()
                            animated = False
                    if not animated:
                        img.seek(0)
                        img.thumbnail((size, size), PILImage.LANCZOS)
                        has_alpha = img.mode in ("RGBA", "LA", "PA") or (
                            img.mode == "P" and "transparency" in img.info
                        )
                        if img.mode not in ("RGB", "RGBA"):
                            img = img.convert("RGBA" if has_alpha else "RGB")
                        img.save(buf, "WEBP", quality=85)
            cache_dir.mkdir(parents=True, exist_ok=True)
            # 并发请求同一缩略图时避免交错写同一文件：先写临时文件再原子替换
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._cfg.thumbnail_dir), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(buf.getvalue())
                os.replace(tmp_path, thumb_path)
            except Exception:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise
            return str(thumb_path)
        except Exception as e:
            logger.warning(f"thumb error {filename}: {e}")
            return ""

    def _thumb_is_stale(self, thumb_path, filename: str, row=None) -> bool:
        """源为动图但缩略图是静态单帧（动画版上线前的旧产物）→ 过期需重建"""
        if not HAS_PIL:
            return False
        ext = os.path.splitext(filename)[1].lower()
        if ext not in (".gif", ".webp"):
            return False
        try:
            with PILImage.open(thumb_path) as t:
                if int(getattr(t, "n_frames", 1)) > 1:
                    return False
            src = self._existing_daily_path(row)
            local_daily = bool(src)
            if not src and row and row.get("id"):
                src = self._find_daily_file(row, read_priority="background")
            read_guard = (
                nullcontext() if local_daily else guarded_read(priority="background")
            )
            with read_guard:
                if not src and not row:
                    src = self._find_meme_file(filename)
                if not src:
                    return False
                with PILImage.open(src) as s:
                    return (
                        bool(getattr(s, "is_animated", False))
                        and int(getattr(s, "n_frames", 1)) > 1
                    )
        except Exception:
            return False

    def ensure_local_thumbs(self) -> int:
        """云端直接使用：push/启动前预生成缺失与过期缩略图（cloud_direct 关时空操作）"""
        if not self._cfg.get("cloud_direct", False):
            return 0
        db = get_db()
        thumb_dir = self._cfg.thumbnail_dir
        generated = 0
        offset = 0
        try:
            while True:
                rows = db.get_all(offset=offset, limit=500)
                if not rows:
                    break
                for row in rows:
                    fhash = row.get("file_hash", "")
                    if not fhash:
                        continue
                    thumb = thumb_dir / f"{fhash}.webp"
                    stale = thumb.exists() and self._thumb_is_stale(
                        thumb, row["filename"], row if row.get("id") else None
                    )
                    if stale:
                        try:
                            thumb.unlink()
                        except OSError:
                            pass
                    if thumb.exists():
                        continue
                    if self._get_thumbnail_path(
                        fhash, row["filename"], read_priority="background"
                    ):
                        generated += 1
                if len(rows) < 500:
                    break
                offset += len(rows)
        except Exception as e:
            logger.warning("ensure_local_thumbs failed: %s", e)
        if generated:
            logger.info("cloud_direct: pre-generated %d thumbnails", generated)
        return generated

    def _find_meme_file(self, filename: str) -> str:
        if not _safe_serve_filename(filename):
            return ""
        cache_dir = self._cfg.cache_dir
        direct = cache_dir / filename
        if direct.is_file():
            return str(direct)
        if not hasattr(self, "_file_cache"):
            self._file_cache = {}
        if filename in self._file_cache:
            cached = self._file_cache[filename]
            if os.path.exists(cached):
                return cached
        for root, dirs, files in os.walk(cache_dir):
            if Path(root) == Path(cache_dir):
                dirs[:] = [
                    item
                    for item in dirs
                    if item.casefold() != CHAT_RECORD_MEDIA_DIRNAME.casefold()
                ]
            if filename in files:
                full = os.path.join(root, filename)
                self._file_cache[filename] = full
                return full
        return ""

    def _category_file_path(self, category_id, filename: str) -> Path:
        if not _safe_serve_filename(filename):
            raise ValueError("文件名无效")
        db = get_db()
        parts = db.get_category_path_names(category_id)
        return category_dir(self._cfg.cache_dir, parts) / filename

    def _find_meme_file_for_row(self, row) -> str:
        if not row:
            return ""
        try:
            expected = self._category_file_path(row.get("category_id"), row["filename"])
            if expected.is_file():
                return str(expected)
        except (KeyError, ValueError):
            return ""
        return self._find_meme_file(row.get("filename", ""))

    def _find_interrupted_rename_file(self, row, target: Path) -> str:
        """按大小和 SHA-256 找回已移动但尚未入库的原件。"""
        expected_hash = row.get("file_hash", "") or ""
        if not expected_hash:
            return ""
        if target.is_file():
            try:
                if _file_sha256(target) == expected_hash:
                    return str(target)
            except OSError:
                pass

        expected_size = int(row.get("file_size", 0) or 0)
        if expected_size <= 0:
            return ""
        old_parent = self._category_file_path(
            row.get("category_id"), row.get("filename", "")
        ).parent
        search_dirs = [target.parent]
        if old_parent != target.parent:
            search_dirs.append(old_parent)
        db = getattr(getattr(self, "_api", None), "_db", None)
        matches = []
        seen = set()
        for directory in search_dirs:
            try:
                entries = os.scandir(directory)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    candidate = Path(entry.path)
                    candidate_key = os.path.normcase(os.path.abspath(str(candidate)))
                    if candidate_key in seen:
                        continue
                    seen.add(candidate_key)
                    try:
                        if (
                            not entry.is_file()
                            or candidate.suffix.lower() != target.suffix.lower()
                            or entry.stat().st_size != expected_size
                        ):
                            continue
                    except OSError:
                        continue
                    if db is not None:
                        owner = db.get_by_filename(candidate.name)
                        if owner and owner.get("id") != row.get("id"):
                            continue
                    try:
                        if _file_sha256(candidate) != expected_hash:
                            continue
                    except OSError:
                        continue
                    matches.append(candidate)
                    if len(matches) > 1:
                        return ""
        if matches:
            logger.info("recovered interrupted rename source: %s", matches[0].name)
            return str(matches[0])
        return ""

    def _move_meme_file(self, row, filename: str, category_id):
        """把原件移动到类别对应目录并返回 (旧路径, 新路径)。"""
        # 保存详情属于用户正在等待的关键操作：排在可见页加载和后台预热之前。
        # 整段覆盖源文件定位、校验和移动，避免定位阶段先向虚拟盘灌入请求。
        with guarded_read(priority="critical"):
            source_name = row.get("filename", "")
            target = self._category_file_path(category_id, filename)
            source_value = self._find_meme_file_for_row(row)
            if not source_value:
                source_value = self._find_interrupted_rename_file(row, target)
            if not source_value:
                raise FileNotFoundError("找不到原始图片文件")
            source = Path(source_value)
            allow_unresolved = self._cfg.get("virtual_drive_compat", False) is True
            same = resolve_path(source, allow_unresolved) == resolve_path(
                target, allow_unresolved
            )
            if same:
                return source, target
            if target.exists():
                try:
                    if os.path.samefile(source, target):
                        return source, target
                except OSError:
                    pass
                raise ValueError("目标位置已存在同名文件")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(source), str(target))
            remove_empty_parents(source.parent, self._cfg.cache_dir)
            if hasattr(self, "_file_cache"):
                self._file_cache.pop(source_name, None)
                self._file_cache.pop(filename, None)
            return source, target

    def _rename_category_directory(self, old_parts, parent_parts, new_name):
        old_dir = category_dir(self._cfg.cache_dir, old_parts)
        new_dir = category_dir(self._cfg.cache_dir, list(parent_parts) + [new_name])
        if old_dir == new_dir or not old_dir.exists():
            return None
        if new_dir.exists():
            raise ValueError("目标类别目录已存在")
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(old_dir), str(new_dir))
        return old_dir, new_dir

    def _delete_category_directory(self, parts, parent_parts, mode="promote"):
        """按删除策略把类别目录中的原件移动到父目录，返回可回滚移动表。"""
        if mode not in ("promote", "cascade"):
            raise ValueError("无效的类别删除方式")
        source_dir = category_dir(self._cfg.cache_dir, parts)
        if not source_dir.exists():
            return []
        target_dir = category_dir(self._cfg.cache_dir, parent_parts)
        moves = []
        for path in source_dir.rglob("*"):
            if path.is_file():
                relative = (
                    Path(path.name)
                    if mode == "cascade"
                    else path.relative_to(source_dir)
                )
                target = target_dir / relative
                moves.append((path, target))
        target_keys = set()
        for _source, target in moves:
            key = str(target).casefold()
            if key in target_keys or target.exists():
                raise ValueError("删除类别会覆盖目标目录中的同名文件")
            target_keys.add(key)
        moved = []
        try:
            for source, target in moves:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(source), str(target))
                moved.append((source, target))
                if hasattr(self, "_file_cache"):
                    self._file_cache[source.name] = str(target)
        except Exception:
            self._restore_category_directory_moves(moved)
            raise
        return moved

    def _cleanup_category_directory(self, parts):
        """数据库提交后移除已清空的原类别目录。"""
        source_dir = category_dir(self._cfg.cache_dir, parts)
        if not source_dir.exists():
            return
        for path in sorted(source_dir.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            source_dir.rmdir()
        except OSError:
            pass

    def _restore_category_directory_moves(self, moves):
        """数据库删除失败时，把已经移动的类别原件恢复到原路径。"""
        for source, target in reversed(list(moves or [])):
            if not target.exists() or source.exists():
                continue
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(target), str(source))
            if hasattr(self, "_file_cache"):
                self._file_cache[source.name] = str(source)

    def _find_daily_file(self, row, read_priority: str = "foreground") -> str:
        """返回日常副本；旧数据或缓存丢失时从原件自动重建。"""
        if not row:
            return ""
        if row.get("compressed_only"):
            return self._find_meme_file_for_row(row)
        daily_name = row.get("daily_filename", "") or ""
        existing = self._existing_daily_path(row)
        if existing:
            return existing
        source = ""
        try:
            with guarded_read(priority=read_priority):
                # 定位原件本身就会触发虚拟盘 is_file/os.walk 请求，必须与真正
                # 解码放在同一优先级闸门内，不能让并发缩略图请求提前灌满网盘队列。
                source = self._find_meme_file_for_row(row)
                if not source:
                    return ""
                result = create_daily_copy(
                    source,
                    self._cfg.derivative_dir,
                    row.get("file_hash", "") or Path(source).stem,
                )
            get_db().set_daily_asset(
                row["id"],
                result["filename"],
                result["file_size"],
                result["mime_type"],
                result["profile"],
            )
            if (
                daily_name
                and daily_name != result["filename"]
                and _safe_serve_filename(daily_name)
            ):
                try:
                    (self._cfg.derivative_dir / daily_name).unlink(missing_ok=True)
                except OSError as cleanup_error:
                    logger.warning(
                        "old daily copy cleanup failed %s: %s",
                        daily_name,
                        cleanup_error,
                    )
            return str(self._cfg.derivative_dir / result["filename"])
        except Exception as e:
            logger.warning("daily copy failed %s: %s", row.get("filename", ""), e)
            return source

    def _get_chat_record_media_path(self, record_id, media_index) -> str:
        """返回聊天记录中可安全预览的本地图片或视频路径。

        归档包中的 ``local_path`` 只允许指向聊天记录媒体目录，且只公开
        浏览器可直接预览的图片与视频格式；语音和一般文件不会通过这个
        路由暴露给详情页。
        """
        try:
            record_id = int(record_id)
            media_index = int(media_index)
        except (TypeError, ValueError):
            return ""
        if record_id <= 0 or media_index <= 0:
            return ""
        try:
            package = get_db().get_chat_record(record_id)
            if not package:
                return ""
            entries = onebot._archive_media_entries(package)
            if media_index > len(entries):
                return ""
            kind, data = entries[media_index - 1]
            if kind not in ("image", "video") or not isinstance(data, dict):
                return ""
            storage_root = self._cfg.cache_dir
            source = onebot._archive_media_source(
                data, cfg=self._cfg, storage_root=storage_root
            )
            if not source:
                return ""
            path = Path(source)
            ext = path.suffix.lower()
            allowed = {
                "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"},
                "video": {".mp4", ".webm", ".mov", ".m4v", ".ogv"},
            }
            if ext not in allowed[kind]:
                return ""
            try:
                if path.is_symlink():
                    return ""
            except OSError:
                # 某些挂载盘不支持 lstat；词法根目录校验仍会继续生效。
                pass

            roots = (
                Path(storage_root) / CHAT_RECORD_MEDIA_DIRNAME,
                Path(self._cfg.data_dir) / CHAT_RECORD_MEDIA_DIRNAME,
            )

            def normalize_for_compare(value):
                value = os.path.abspath(os.path.normpath(str(value)))
                try:
                    value = os.path.realpath(value)
                except OSError:
                    # 虚拟盘可能不支持最终路径 API；保留纯词法路径比较。
                    pass
                return os.path.normcase(value)

            def within(candidate, root):
                candidate = normalize_for_compare(candidate)
                root = normalize_for_compare(root)
                try:
                    return os.path.commonpath((candidate, root)) == root
                except ValueError:
                    return False

            if not any(within(path, root) for root in roots):
                return ""
            return str(path) if path.is_file() else ""
        except (OSError, ValueError, TypeError):
            return ""

    def _import_chat_record_ticket(self, raw) -> dict:
        """消费 QQ 桥接拖出的凭据并走标准聊天记录归档流程。"""
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            return {"ok": False, "error": "聊天记录拖拽凭据为空"}
        if len(raw) > _CHAT_RECORD_TICKET_BYTES:
            return {"ok": False, "error": "聊天记录拖拽凭据超过安全上限"}
        try:
            descriptor = json.loads(bytes(raw).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"ok": False, "error": "聊天记录拖拽凭据格式无效"}
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("type") != "ohmymeme.qq-chat-record-drag"
            or descriptor.get("schema_version") != 1
        ):
            return {"ok": False, "error": "这不是 OhMyMeme 聊天记录拖拽凭据"}
        ticket_id = str(descriptor.get("ticket_id") or "").strip()
        try:
            result = onebot.archive_dragged_chat_record(
                self._cfg.get("napcat_http_url", "http://127.0.0.1:3000"),
                self._cfg.get("napcat_token", ""),
                self._cfg.get("napcat_webui_url", "http://127.0.0.1:6099"),
                self._cfg.get("napcat_webui_token", ""),
                ticket_id,
                db=get_db(),
            )
        except (onebot.OneBotError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {**result, "chat_record": True}

    def _do_import(
        self,
        file_paths,
        names=None,
        progress_cb=None,
        read_priority="foreground",
        large_file_mode=None,
    ):
        import shutil

        cfg = get_config()
        large_file_mode = _normalize_large_meme_import_mode(
            large_file_mode
            if large_file_mode is not None
            else cfg.get("large_meme_import_mode", "ask")
        )
        # 非交互导入器无法弹出决策框；ask 在这里采用保留原件的无损策略。
        if large_file_mode == "ask":
            large_file_mode = "keep_original"
        db = get_db()
        cache_dir = cfg.cache_dir
        derivative_dir = getattr(
            cfg, "derivative_dir", Path(cfg.data_dir) / "derivatives"
        )
        derivative_dir.mkdir(parents=True, exist_ok=True)
        imported = 0
        rejected = 0
        skipped_dup = 0
        duplicate_ids = []
        imported_ids = []
        total = len(file_paths)
        done = 0
        stop_import = False
        for i, src in enumerate(file_paths):
            try:
                base_name = (
                    names[i]
                    if names and i < len(names)
                    else os.path.splitext(os.path.basename(src))[0]
                )
                # 隐写 GIF 无论开关与否都只入库解码还原的原图（开关仅控制复制输出）
                restored = None
                if _detect_image_ext(src, read_priority=read_priority) == ".gif":
                    restored = _try_decode_stego(src, read_priority=read_priority)
                if restored:
                    items = [(restored, base_name, None, 1)]
                else:
                    items = [(src, base_name, None, 0)]
                for path, oname, stego_of_hash, from_stego in items:
                    w = h = 0
                    try:
                        fsize = os.path.getsize(path)
                    except OSError:
                        fsize = 0
                    if HAS_PIL:
                        try:
                            with guarded_read(priority=read_priority):
                                with PILImage.open(path) as img:
                                    w, h = img.size
                        except Exception:
                            pass
                    max_px = get_import_max_px(cfg)
                    if fsize > _IMPORT_MAX_BYTES or (max_px and max(w, h) > max_px):
                        rejected += 1
                        logger.info(
                            "import rejected (over limit): %s", os.path.basename(path)
                        )
                        continue
                    fhash = _file_sha256(path, read_priority=read_priority)
                    ext = (
                        _detect_image_ext(path, read_priority=read_priority)
                        or os.path.splitext(path)[1]
                        or ".png"
                    )
                    # 感知哈希计算较重（解码+DCT），放锁外避免持锁阻塞其他导入
                    src_phash = _perceptual_hash_path(path, read_priority=read_priority)
                    # 去重检查 + 落盘 + 入库 在同一临界区：并发导入同图时不产生重复记录
                    with _IMPORT_LOCK:
                        existing = db.get_by_hash(fhash)
                        if existing:
                            skipped_dup += 1
                            duplicate_ids.append(int(existing["id"]))
                            continue
                        dst = cache_dir / f"{fhash[:16]}{ext}"
                        daily = None
                        try:
                            with guarded_read(priority=read_priority):
                                daily = create_daily_copy(path, derivative_dir, fhash)
                            compressed_only = bool(
                                fsize > _LARGE_MEME_IMPORT_BYTES
                                and large_file_mode == "compressed_only"
                            )
                            stored_hash = fhash
                            stored_width = w
                            stored_height = h
                            stored_size = fsize
                            stored_mime = f"image/{ext[1:]}" if ext else "image/png"
                            daily_filename = daily["filename"]
                            if compressed_only:
                                daily_src = derivative_dir / daily["filename"]
                                stored_hash = _file_sha256(
                                    daily_src, read_priority=read_priority
                                )
                                compressed_existing = db.get_by_hash(stored_hash)
                                if compressed_existing:
                                    skipped_dup += 1
                                    duplicate_ids.append(int(compressed_existing["id"]))
                                    try:
                                        daily_src.unlink()
                                    except OSError:
                                        pass
                                    continue
                                stored_ext = daily_src.suffix or ext or ".webp"
                                dst = cache_dir / f"{stored_hash[:16]}{stored_ext}"
                                shutil.copy2(daily_src, dst)
                                try:
                                    daily_src.unlink()
                                except OSError:
                                    pass
                                stored_width = int(daily.get("width", w) or w)
                                stored_height = int(daily.get("height", h) or h)
                                stored_size = int(dst.stat().st_size)
                                stored_mime = daily.get("mime_type") or stored_mime
                                daily_filename = dst.name
                            else:
                                with guarded_read(priority=read_priority):
                                    shutil.copy2(path, dst)
                            db.add_meme(
                                filename=dst.name,
                                file_hash=stored_hash,
                                source_file_hash=(fhash if compressed_only else ""),
                                compressed_only=compressed_only,
                                width=stored_width,
                                height=stored_height,
                                file_size=stored_size,
                                mime_type=stored_mime,
                                original_name=oname,
                                stego_of_hash=stego_of_hash,
                                from_stego=from_stego,
                                perceptual_hash=src_phash,
                                daily_filename=daily_filename,
                                daily_file_size=(
                                    stored_size
                                    if compressed_only
                                    else daily["file_size"]
                                ),
                                daily_mime_type=daily["mime_type"],
                                daily_profile=daily["profile"],
                            )
                        except Exception:
                            try:
                                dst.unlink()
                            except OSError:
                                pass
                            if daily:
                                try:
                                    (derivative_dir / daily["filename"]).unlink()
                                except OSError:
                                    pass
                            raise
                        row = db.get_by_hash(fhash)
                        if row:
                            imported_ids.append(row["id"])
                        imported += 1
                if restored:
                    try:
                        os.unlink(restored)
                    except OSError:
                        pass
            except Exception as e:
                logger.error(f"import {src}: {e}")
            finally:
                # 每个源文件无论 导入/去重跳过/拒绝/异常 都推进进度并检查取消
                done += 1
                if progress_cb:
                    try:
                        if progress_cb(done, total, os.path.basename(src)) is False:
                            stop_import = True
                    except Exception:
                        pass
            if stop_import:
                break  # 取消：progress_cb 返回 False 时中断导入
        if imported:
            # 完整清单重建会对整库原件逐个 exists/stat，在虚拟盘上等同于
            # 再走一轮缓存队列。新条目优先增量写本地 JSON；如果旧重建正
            # 持锁或清单不可用则只安排后台低优先级重建，绝不阻塞导入响应。
            if not add_memes_to_manifest(imported_ids):
                request_manifest_rebuild()
            # 所有导入入口在数据库提交和清单更新后发布统一通知；structure
            # 同时刷新「未分组/未分类」等侧栏计数。
            try:
                from .library_events import notify_changed

                notify_changed("structure")
            except Exception:
                pass
        logger.info("导入完成: %s 个", imported)
        return {
            "ids": imported_ids,
            "rejected": rejected,
            "skipped_dup": skipped_dup,
            "duplicate_ids": list(dict.fromkeys(duplicate_ids)),
        }

    def _import_with_similar_decision(self, path, oname="", large_file_mode=None):
        """单图导入决策：哈希命中提示已存在；内容近似转相似（URL 拖放与文件上传复用）"""
        try:
            prep = _prepare_image_import(path)
        except Exception as e:
            logger.error("import decision error: %s", e)
            return {"ok": False, "error": "导入失败"}
        resolved_large_mode = _normalize_large_meme_import_mode(
            large_file_mode
            if large_file_mode is not None
            else self._cfg.get("large_meme_import_mode", "ask")
        )
        if prep.get("status") not in ("hash_dup", "rejected"):
            try:
                is_large = os.path.getsize(path) > _LARGE_MEME_IMPORT_BYTES
            except OSError:
                is_large = False
            if is_large and resolved_large_mode == "ask":
                try:
                    return _hold_large_import_file(path, oname)
                except OSError:
                    return {"ok": False, "error": "无法暂存待导入的大文件"}
        if prep.get("status") == "ok":
            r = self._do_import(
                [path],
                [oname] if oname else None,
                read_priority="critical",
                large_file_mode=resolved_large_mode,
            )
            ids = r.get("ids") or []
            if ids:
                return {"ok": True, "id": ids[0]}
            if r.get("skipped_dup"):
                duplicate_ids = r.get("duplicate_ids") or []
                if not duplicate_ids and prep.get("hash"):
                    existing = get_db().get_by_hash(prep["hash"])
                    if existing:
                        duplicate_ids = [existing["id"]]
                return {
                    "ok": False,
                    "duplicate": True,
                    "existing_id": duplicate_ids[0] if duplicate_ids else 0,
                    "duplicate_ids": duplicate_ids,
                    "error": "该图片已存在，无需重复导入",
                }
            if r.get("rejected"):
                return {
                    "ok": False,
                    "rejected": r["rejected"],
                    "error": "文件超过大小/分辨率限制，已跳过",
                }
            return {"ok": False, "error": "导入失败"}
        if prep.get("status") == "rejected":
            return {
                "ok": False,
                "rejected": 1,
                "error": prep.get("error") or "导入失败",
            }
        if prep.get("status") == "hash_dup":
            existing_id = prep.get("existing_id", 0)
            return {
                "ok": False,
                "duplicate": True,
                "existing_id": existing_id,
                "duplicate_ids": [existing_id] if existing_id else [],
                "error": "该图片已存在，无需重复导入",
            }
        # similar：先把文件复制到独立临时目录，避免调用方 finally 清理源文件
        import tempfile

        cands = prep.get("candidates") or []
        hold = None
        try:
            ext = os.path.splitext(path)[1] or ".png"
            fd, hold = tempfile.mkstemp(prefix="ohmm_simil_", suffix=ext)
            os.close(fd)
            import shutil

            shutil.copy2(path, hold)
        except OSError:
            if hold:
                try:
                    os.unlink(hold)
                except OSError:
                    pass
            return {"ok": False, "error": "导入失败"}
        token = _register_pending_similar(hold, oname, cands, resolved_large_mode)
        return {
            "ok": False,
            "similar_pending": True,
            "token": token,
            "candidates": cands,
        }

    def ensure_import_collection(self, ids, group_name, parent_id=None):
        """把导入 ids 加入固定名分组（同名复用，不存在则建）；返回 collection_id"""
        if not ids or not group_name:
            return -1
        db = get_db()
        cid = db.create_collection(group_name, parent_id=parent_id)
        if cid > 0:
            for mid in ids:
                db.add_to_collection(mid, cid)
            from .manifest import build as build_manifest

            build_manifest()
        return cid

    def _on_hotkey_change(self, new_hotkey: str):
        if self._on_hotkey_change_cb:
            self._on_hotkey_change_cb(new_hotkey)

    # --- Bottle 路由 ---

    def _setup_bottle(self):
        app = bottle.Bottle()

        @app.hook("before_request")
        def _guard_cross_origin():
            # 仅接受本地回环 Host，阻断 DNS rebinding 与外部直连
            if not _host_allowed(bottle.request.headers.get("Host", ""), self._port):
                bottle.abort(403, "Forbidden")
            if bottle.request.method == "POST":
                origin = bottle.request.headers.get("Origin", "")
                if origin:
                    allowed = (
                        f"http://127.0.0.1:{self._port}",
                        f"http://localhost:{self._port}",
                    )
                    if origin not in allowed:
                        bottle.abort(403, "Forbidden")
                if bottle.request.headers.get("Sec-Fetch-Site", "") == "cross-site":
                    bottle.abort(403, "Forbidden")

        @app.hook("after_request")
        def _set_security_headers():
            bottle.response.headers["X-Content-Type-Options"] = "nosniff"
            bottle.response.headers["Referrer-Policy"] = "no-referrer"
            bottle.response.headers["X-Frame-Options"] = "DENY"
            if bottle.request.path.startswith("/api/"):
                bottle.response.headers["Cache-Control"] = "no-store"

        @app.route("/")
        def index():
            vue_html = HTML_DIR / "vue.html"
            # 仅当 vue.html 与构建产物都存在时才走 Vue 前端，否则回退旧 index.html
            if vue_html.exists() and (HTML_DIR / "dist" / "ohmymeme.js").exists():
                tags = get_plugin_manager().asset_tags("main")
                try:
                    html = _theme_html(
                        vue_html.read_text(encoding="utf-8"), self._cfg.get("theme")
                    )
                    html = _inject_before(html, "</body>", tags)
                    bottle.response.content_type = "text/html; charset=utf-8"
                    return html
                except OSError as e:
                    logger.warning(f"vue.html 读取失败回退静态: {e}")
                return bottle.static_file("vue.html", root=str(HTML_DIR))
            html_path = HTML_DIR / "index.html"
            if html_path.exists():
                try:
                    html = _theme_html(
                        html_path.read_text(encoding="utf-8"), self._cfg.get("theme")
                    )
                    bottle.response.content_type = "text/html; charset=utf-8"
                    return html
                except OSError as e:
                    logger.warning(f"index.html 读取失败: {e}")
                    return bottle.HTTPError(500, "页面读取失败")
            return f"<h1>{__app_name__}</h1><p>index.html not found</p>"

        @app.route("/settings/")
        def settings_page():
            html_path = HTML_DIR / "settings.html"
            if html_path.exists():
                links, scripts = _split_asset_tags(
                    get_plugin_manager().asset_tags("settings")
                )
                sections = get_plugin_manager().settings_sections()
                try:
                    html = _theme_html(
                        html_path.read_text(encoding="utf-8"), self._cfg.get("theme")
                    )
                    html = _inject_before(html, "</head>", links)
                    html = _inject_after(html, "<!-- plugin-sections -->", sections)
                    # 插件脚本在 Vue 入口之后注入（defer 文档序）
                    html = _inject_before(html, "</body>", scripts)
                    bottle.response.content_type = "text/html; charset=utf-8"
                    return html
                except OSError as e:
                    logger.warning(f"settings.html 读取失败回退静态: {e}")
                return bottle.static_file("settings.html", root=str(HTML_DIR))
            return "<h1>设置</h1><p>settings.html not found</p>"

        @app.route("/api/contributors")
        def serve_contributors():
            # 代理贡献者 SVG：剥离白色背景矩形，适配深色主题；TTL 缓存 + 单飞
            # 刷新（锁内抓取），失败退避 60s 并回退旧缓存
            now = time.time()
            if _CONTRIBUTORS_CACHE["svg"] is None or now >= (
                _CONTRIBUTORS_CACHE["at"] + _CONTRIBUTORS_TTL
            ):
                with _CONTRIBUTORS_LOCK:
                    # 取锁后刷新时间并重查退避期：等待中的并发请求不再重复抓取
                    now = time.time()
                    if _CONTRIBUTORS_CACHE["svg"] is None or now >= (
                        _CONTRIBUTORS_CACHE["at"] + _CONTRIBUTORS_TTL
                    ):
                        if now < _CONTRIBUTORS_CACHE["next_retry"]:
                            if _CONTRIBUTORS_CACHE["svg"] is None:
                                bottle.response.status = 502
                                return ""
                            return _contributors_svg()
                        try:
                            from urllib.request import Request, urlopen

                            url = "https://contributor.starsfire.top/TNTXZ/OhMyMeme"
                            req = Request(url, headers={"User-Agent": "OhMyMeme"})
                            with urlopen(req, timeout=10) as resp:
                                svg = resp.read().decode("utf-8", "replace")
                            _CONTRIBUTORS_CACHE["svg"] = svg
                            _CONTRIBUTORS_CACHE["at"] = now
                        except Exception:
                            # 失败一律退避（含无缓存冷启动），防并发请求反复触发网络重试
                            _CONTRIBUTORS_CACHE["next_retry"] = (
                                now + _CONTRIBUTORS_RETRY_INTERVAL
                            )
                            if _CONTRIBUTORS_CACHE["svg"] is None:
                                bottle.response.status = 502
                                return ""
            return _contributors_svg()

        @app.route("/api/thumb/<sha256>")
        def serve_thumb(sha256):
            if not re.fullmatch(r"[0-9a-f]{64}", sha256):
                bottle.response.status = 404
                return ""
            path = ""
            thumb_path = self._cfg.thumbnail_dir / f"{sha256}.webp"
            if thumb_path.exists():
                path = str(thumb_path)
            else:
                row = get_db().get_by_hash(sha256)
                if row:
                    path = self._get_thumbnail_path(sha256, row["filename"])
            if path:
                return bottle.static_file(
                    os.path.basename(path),
                    root=os.path.dirname(path),
                    mimetype="image/webp",
                )
            # 云缺失行缩略图：后台补拉（完成经 onCloudReady 重载），本请求先404
            try:
                if self._cfg.get("cloud_direct", False):
                    _cloud_ensure_loaded()
                    if any(it.get("sha256") == sha256 for it in _cloud_view()):
                        _enqueue_thumb_fetch(sha256)
            except Exception:
                pass
            bottle.response.status = 404
            return ""

        @app.route("/api/original/<meme_id>/<filename>")
        def serve_original(meme_id, filename):
            row = get_db().get_by_id(int(meme_id))
            if not row or row["filename"] != filename:
                bottle.response.status = 404
                return ""
            path = self._existing_daily_path(row)
            if not path:
                path = self._find_daily_file(row)
            if path:
                ext = os.path.splitext(path)[1].lower()
                ctype = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                    ".bmp": "image/bmp",
                }.get(ext, "application/octet-stream")
                return bottle.static_file(
                    os.path.basename(path), root=os.path.dirname(path), mimetype=ctype
                )
            bottle.response.status = 404
            return ""

        @app.route("/api/chat-record-media/<record_id>/<media_index>")
        def serve_chat_record_media(record_id, media_index):
            path = self._get_chat_record_media_path(record_id, media_index)
            if not path:
                bottle.response.status = 404
                return ""
            ext = os.path.splitext(path)[1].lower()
            ctype = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
                ".mp4": "video/mp4",
                ".webm": "video/webm",
                ".mov": "video/quicktime",
                ".m4v": "video/x-m4v",
                ".ogv": "video/ogg",
            }.get(ext)
            if not ctype:
                bottle.response.status = 404
                return ""
            bottle.response.headers["Content-Disposition"] = "inline"
            return bottle.static_file(
                os.path.basename(path), root=os.path.dirname(path), mimetype=ctype
            )

        @app.route("/api/upload/", method="POST")
        def upload_memes():
            try:
                import json

                data = json.loads(bottle.request.body.read())
                files = data.get("files", []) if isinstance(data, dict) else data
                allowed = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
                paths, names = [], []
                if not isinstance(files, list):
                    return {"ok": False, "error": "上传数据格式无效"}
                chat_items = [
                    item
                    for item in files
                    if isinstance(item, dict)
                    and os.path.splitext(str(item.get("name") or ""))[1].lower()
                    == ".ommchat"
                ]
                if chat_items:
                    if len(files) != 1 or len(chat_items) != 1:
                        return {
                            "ok": False,
                            "error": "聊天记录凭据不能与图片一起导入",
                        }
                    import base64

                    try:
                        raw = base64.b64decode(
                            str(chat_items[0].get("data") or ""), validate=True
                        )
                    except (ValueError, TypeError):
                        return {"ok": False, "error": "聊天记录凭据编码无效"}
                    return self._import_chat_record_ticket(raw)
                for item in files:
                    if not isinstance(item, dict):
                        continue
                    oname = item.get("name", "")
                    b64 = item.get("data", "")
                    ext = os.path.splitext(oname)[1].lower()
                    if ext not in allowed or not b64:
                        continue
                    import base64

                    raw = base64.b64decode(b64)
                    if len(raw) > _IMPORT_MAX_BYTES:
                        bottle.response.status = 413
                        return {
                            "ok": False,
                            "error": "图片超过 %d MiB 原件上限"
                            % (_IMPORT_MAX_BYTES // (1024 * 1024)),
                        }
                    fd, tmp = tempfile.mkstemp(prefix="ohmm_upload_", suffix=ext)
                    with os.fdopen(fd, "wb") as f:
                        f.write(raw)
                    paths.append(tmp)
                    base = os.path.splitext(oname)[0]
                    names.append(base)
                if paths:
                    if len(paths) == 1:
                        # 单张上传走相似/哈希决策，能触发重复与近似检测
                        r = self._import_with_similar_decision(
                            paths[0], names[0] if names else ""
                        )
                        for p in paths:
                            try:
                                os.unlink(p)
                            except Exception:
                                pass
                        return r
                    self._do_import(paths, names)
                    for p in paths:
                        try:
                            os.unlink(p)
                        except Exception:
                            pass
                return {"ok": True}
            except Exception as e:
                logger.error(f"upload error: {e}")
                bottle.response.status = 500
                return {"ok": False, "error": str(e)}

        @app.route("/resources/<filepath:path>")
        def serve_resources(filepath):
            # 启动动画等内置资源（src/resources），防止路径穿越
            name = os.path.basename(filepath)
            if not name or name != filepath.replace("\\", "/").split("/")[-1]:
                bottle.abort(404, "Not Found")
            return bottle.static_file(name, root=str(RESOURCES_DIR))

        # 插件资产与自定义路由：须注册在兜底静态路由之前（Bottle 按注册序匹配）
        @app.route("/plugins/<plugin_id>/<relpath:path>")
        def serve_plugin_asset(plugin_id, relpath):
            resolved = get_plugin_manager().resolve_asset(plugin_id, relpath)
            if not resolved:
                bottle.abort(404, "Not Found")
            path, ctype = resolved
            return bottle.static_file(
                os.path.basename(path),
                root=os.path.dirname(path),
                mimetype=ctype or "application/octet-stream",
            )

        for _pid, _rule, _fn in get_plugin_manager().custom_routes():
            app.route(
                _rule,
                method=["GET", "POST"],
                callback=_make_plugin_route_cb(_pid, _rule, _fn),
            )

        @app.route("/<filepath:path>")
        def static_files(filepath):
            # 按扩展名强制 MIME，规避本机 .js 映射被改写成 text/plain 时
            # 叠加 nosniff 导致 Chromium 拒执行脚本；未知类型走 bottle 自动检测
            ctype = _STATIC_MIME_TYPES.get(os.path.splitext(filepath)[1].lower())
            if ctype:
                return bottle.static_file(filepath, root=str(HTML_DIR), mimetype=ctype)
            return bottle.static_file(filepath, root=str(HTML_DIR))

        # 多线程 wsgiref：默认单线程下慢请求（外网抓取/缩略图生成）会阻塞
        # 其他路由，导致设置页资源排队、JS 监听未注册期间窗口无法交互
        from socketserver import ThreadingMixIn
        from wsgiref.simple_server import WSGIServer

        class _ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
            daemon_threads = True
            # wsgiref 默认 backlog=5：懒加载缩略图突发连接易被丢弃导致整行图片加载失败
            request_queue_size = 128

        bottle.run(
            app,
            host="127.0.0.1",
            port=self._port,
            quiet=True,
            server_class=_ThreadedWSGIServer,
        )

    # --- 缓存扫描 ---

    def start_cache_scan(self) -> bool:
        """异步启动缓存扫描；已有任务运行时直接复用，不重复排队。"""
        with self._cache_scan_lock:
            if self._cache_scan_thread is not None:
                return True
            thread = threading.Thread(
                target=self._run_cache_scan,
                name="cache-scan",
                daemon=True,
            )
            self._cache_scan_thread = thread
            thread.start()
        return True

    def _run_cache_scan(self):
        try:
            added = self.scan_cache()
            if added:
                try:
                    from .library_events import notify_changed

                    notify_changed("structure")
                except Exception:
                    pass
        except Exception:
            logger.exception("cache scan failed")
        finally:
            with self._cache_scan_lock:
                self._cache_scan_thread = None

    def scan_cache(self):
        """扫描本地缓存目录，将已有文件自动注册到数据库"""
        import hashlib

        cache_dir = self._cfg.cache_dir
        db = get_db()
        allowed_ext = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
        added = 0
        if not cache_dir.exists():
            logger.debug(f"scan_cache: cache dir not found {cache_dir}")
            return 0
        for root, dirs, files in os.walk(cache_dir):
            if Path(root) == Path(cache_dir):
                dirs[:] = [
                    item
                    for item in dirs
                    if item.casefold() != CHAT_RECORD_MEDIA_DIRNAME.casefold()
                ]
            try:
                relative_parts = Path(root).relative_to(cache_dir).parts
            except ValueError:
                relative_parts = ()
            if any(part.lower() == "thumbnails" for part in relative_parts):
                continue
            try:
                category_id = db.ensure_category_path(relative_parts)
            except ValueError:
                logger.warning("scan_cache skip invalid category path: %s", root)
                continue
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in allowed_ext:
                    continue
                fpath = os.path.join(root, fname)
                if "thumbnails" in fpath.lower():
                    continue
                # 跳过由 WebP 动图自动生成的 GIF（同名 .webp 存在即为生成物）
                stem = os.path.splitext(fname)[0]
                if ext == ".gif" and os.path.isfile(os.path.join(root, stem + ".webp")):
                    continue
                if db.get_by_filename(fname):
                    continue
                try:
                    with guarded_read():
                        fsize = os.path.getsize(fpath)
                        w = h = 0
                        if HAS_PIL:
                            try:
                                with PILImage.open(fpath) as img:
                                    w, h = img.size
                            except Exception:
                                pass
                        max_px = get_import_max_px(self._cfg)
                        if fsize > _IMPORT_MAX_BYTES or (max_px and max(w, h) > max_px):
                            logger.info("scan_cache skip (over limit): %s", fname)
                            continue
                        sha256 = hashlib.sha256()
                        with open(fpath, "rb") as f:
                            for chunk in iter(lambda: f.read(65536), b""):
                                sha256.update(chunk)
                        fhash = sha256.hexdigest()
                        phash = _perceptual_hash_path(fpath)
                    if db.get_by_hash(fhash):
                        continue
                    mime = f"image/{ext[1:]}" if ext else "image/png"
                    oname = os.path.splitext(fname)[0]
                    db.add_meme(
                        filename=fname,
                        file_hash=fhash,
                        width=w,
                        height=h,
                        file_size=fsize,
                        mime_type=mime,
                        original_name=oname,
                        perceptual_hash=phash,
                        category_id=category_id,
                    )
                    added += 1
                except Exception as e:
                    logger.warning(f"scan_cache skip {fname}: {e}")
        if added:
            logger.info(f"缓存扫描完成: 新增 {added} 个文件")
        build_manifest()
        return added

    # --- 启动 ---

    def start(self) -> bool:
        if not HAS_WEBVIEW:
            logger.error("pywebview not installed")
            return False
        if not HAS_BOTTLE:
            logger.error("bottle not installed")
            return False

        _migrate_thumbnails(self._cfg.thumbnail_dir)
        threading.Thread(target=self.ensure_local_thumbs, daemon=True).start()

        # 启动 Bottle 服务器
        self._bottle_thread = threading.Thread(target=self._setup_bottle, daemon=True)
        self._bottle_thread.start()
        time.sleep(0.3)

        url = f"http://127.0.0.1:{self._port}/"
        wx, wy = self._saved_window_position()
        width, height = self._resolve_window_size()
        self._window = webview.create_window(
            __app_name__,
            url,
            js_api=self._api,
            width=width,
            height=height,
            x=wx,
            y=wy,
            resizable=True,
            frameless=True,
            easy_drag=False,
            hidden=self._silent_start,
            background_color=(
                "#f8fafc" if self._cfg.get("theme") == "light" else "#0d0d0f"
            ),
        )
        if platform.system() == "Windows":
            self._window.events.loaded += lambda: _install_text_clipboard_bridge(
                self._window
            )
            # frameless=True 会让 WinForms 去掉系统最小化样式；窗口显示后补回，
            # 使任务栏按钮在窗口位于前台时可以再次点击并最小化。
            self._window.events.shown += lambda: _enable_windows_taskbar_minimize(
                self._window
            )
        self._visible = not self._silent_start
        try:
            # 首次显示后校正屏幕外坐标；被系统最小化时同步可见状态
            self._window.events.shown += lambda: self._ensure_window_on_screen()
            self._window.events.minimized += self._on_window_minimized
        except Exception as e:
            logger.debug("bind window events: %s", e)

        self._started = True
        # 注册 LAN 设备确认回调（窗口创建完成后）
        self._init_lan()
        # start() blocks - 在调用线程运行 GUI 循环
        gui = "gtk" if platform.system() == "Linux" else None
        webview.start(debug=False, http_server=False, gui=gui)
        return True

    def open_settings(self):
        return self._create_settings_window()

    def _create_settings_window(self):
        try:
            if self._settings_window is not None:
                self._save_settings_window_size()
                try:
                    self._settings_window.destroy()
                except Exception:
                    pass
                self._settings_window = None

            sw = int(self._cfg.get("settings_window_width", 0) or 0)
            sh = int(self._cfg.get("settings_window_height", 0) or 0)
            sw = sw if 600 <= sw <= 8000 else 720
            sh = sh if 480 <= sh <= 6000 else 560
            settings_url = f"http://127.0.0.1:{self._port}/settings/"
            sx = sy = None
            if self._window is not None:
                try:
                    mx, my = self._window.x, self._window.y
                    mw, mh = self._window.width, self._window.height
                    if mx is not None and my is not None and mw and mh:
                        sx = mx + (mw - sw) // 2
                        sy = my + (mh - sh) // 2
                except Exception:
                    sx = sy = None
            self._settings_window = webview.create_window(
                f"设置 - {__app_name__}",
                settings_url,
                js_api=self._settings_api,
                width=sw,
                height=sh,
                x=sx,
                y=sy,
                resizable=True,
                frameless=True,
                easy_drag=False,
                background_color=(
                    "#f8fafc" if self._cfg.get("theme") == "light" else "#0d0d0f"
                ),
            )
            if platform.system() == "Windows":
                window = self._settings_window
                window.events.loaded += lambda: _install_text_clipboard_bridge(window)
            return True
        except Exception as e:
            logger.warning(f"create settings window error: {e}")
            return False

    def _save_settings_window_size(self):
        """设置窗口销毁前持久化尺寸（下次打开沿用）"""
        w = self._settings_window
        if not w:
            return
        try:
            if w.width and w.height:
                self._cfg.set("settings_window_width", int(w.width))
                self._cfg.set("settings_window_height", int(w.height))
                self._cfg.save()
        except Exception:
            pass

    def focus_settings_window(self):
        """把设置窗口重新拉到前台（对话框关闭后恢复焦点用）"""
        win = self._settings_window
        if win is None:
            return
        try:
            win.on_top = True  # 置顶一下提升 z-order，随即复位不长期置顶
            win.on_top = False
            if callable(win.show):
                win.show()
            if callable(win.focus):
                win.focus()
        except Exception:
            pass

    def close_settings(self):
        if self._settings_window:
            self._save_settings_window_size()
            try:
                self._settings_window.destroy()
            except Exception as e:
                logger.warning(f"destroy settings error: {e}")
            self._settings_window = None
        # 关闭设置页不停止局域网服务：手机发现/传输通常在设置页关闭后进行，
        # 停服由开关关闭、恢复默认或程序退出负责（main.shutdown 兜底 lan.stop）

    def _resolve_window_size(self):
        """主窗口尺寸：插件 window 覆盖 > 持久化尺寸 > 默认 960x640"""
        try:
            overrides = get_plugin_manager().window_params()
        except Exception:
            overrides = {}

        def _norm(value, fallback, lo, hi):
            try:
                value = int(value)
            except (TypeError, ValueError):
                return fallback
            return value if lo <= value <= hi else fallback

        width = _norm(overrides.get("width"), None, 480, 8000)
        height = _norm(overrides.get("height"), None, 360, 6000)
        if width is None:
            width = _norm(self._cfg.get("window_width", 0), 960, 480, 8000)
        if height is None:
            height = _norm(self._cfg.get("window_height", 0), 640, 360, 6000)
        return width, height

    def _saved_window_position(self):
        """读取持久化窗口坐标；哨兵值交给系统摆放，负坐标合法（副屏在主屏左侧/上方）"""
        try:
            wx = int(self._cfg.get("window_x", -1))
            wy = int(self._cfg.get("window_y", -1))
        except (TypeError, ValueError):
            return None, None
        # 最小化时系统返回 -32000 之类哨兵坐标，写盘后下次启动会在屏幕外
        if not (_WINDOW_COORD_MIN < wx <= _WINDOW_COORD_MAX):
            return None, None
        if not (_WINDOW_COORD_MIN < wy <= _WINDOW_COORD_MAX):
            return None, None
        return wx, wy

    def _save_window_position(self):
        if not self._window:
            return
        try:
            # 尺寸与坐标分开判断：最小化态下坐标是哨兵值，但尺寸仍应保存
            if self._window.width and self._window.height:
                self._cfg.set("window_width", int(self._window.width))
                self._cfg.set("window_height", int(self._window.height))
            wx = self._window.x
            wy = self._window.y
            # 最小化/隐藏态下拿到的哨兵坐标不写盘，否则下次启动窗口不可见
            if (
                wx is not None
                and wy is not None
                and _WINDOW_COORD_MIN < wx <= _WINDOW_COORD_MAX
                and _WINDOW_COORD_MIN < wy <= _WINDOW_COORD_MAX
            ):
                self._cfg.set("window_x", wx)
                self._cfg.set("window_y", wy)
                # 标记坐标确实写过：否则下次加载无法区分 -1 是旧哨兵还是副屏上的合法坐标
                self._cfg.set("window_pos_set", True)
            self._cfg.save()
        except Exception:
            pass

    def stop(self):
        from .library_events import set_change_callback

        set_change_callback(None)
        with self._library_refresh_lock:
            timer = self._library_refresh_timer
            self._library_refresh_timer = None
        if timer is not None:
            timer.cancel()
        self._save_window_position()
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None
        if self._settings_window:
            try:
                self._settings_window.destroy()
            except Exception:
                pass
            self._settings_window = None

    @staticmethod
    def _find_free_port() -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port
