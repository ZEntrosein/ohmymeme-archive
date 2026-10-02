"""本地 OneBot 11 连接器。"""

import hashlib
import html
import json
import logging
import os
import queue
import re
import shutil
import socket
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class OneBotError(RuntimeError):
    """OneBot 请求失败。"""


class _NoRedirectHandler(HTTPRedirectHandler):
    """拒绝跟随重定向，防止把本机 Token 带到其他地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_HTTP_OPENER = build_opener(_NoRedirectHandler)
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_FORWARD_RECORDS = 20
_MAX_FORWARD_DEPTH = 4
_MAX_FORWARD_NODES = 5000
_MAX_ARCHIVE_RECORDS = 100
_MAX_ARCHIVE_DEPTH = 12
_MAX_ARCHIVE_NODES = 20000
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_CHAT_RECORD_PLUGIN_TIMEOUT = 10 * 60
_MAX_CHAT_RECORD_PLUGIN_RESPONSE_BYTES = _MAX_ARCHIVE_BYTES
_MAX_NAPCAT_NESTED_FORWARD_DEPTH = 2
_MAX_JSON_CARD_BYTES = 1024 * 1024
_MAX_ARCHIVE_IMAGE_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_MEDIA_BYTES = 512 * 1024 * 1024
_ARCHIVE_MEDIA_KINDS = ("image", "record", "video", "file")
_CHAT_RECORD_NODE_PATH_RE = re.compile(
    r"^n(?:0|[1-9]\d*)(?:/s(?:0|[1-9]\d*)/n(?:0|[1-9]\d*))*$"
)
_WEBUI_CREDENTIAL_TTL = 45 * 60
_WEBUI_CREDENTIAL_CACHE = {}
_WEBUI_CREDENTIAL_LOCK = threading.Lock()

logger = logging.getLogger(__name__)

_CHAT_RECORD_SEND_MODES = {
    "original_native",
    "original_rebuild",
    "archive_rebuild",
    "self_native_relay",
}
_IMAGE_SEND_MODES = {"sticker", "image"}


def _open_request(request, timeout):
    """使用禁止重定向的 opener 发起请求。"""
    return _HTTP_OPENER.open(request, timeout=timeout)


def _normalize_loopback_url(value, schemes):
    """校验并规范化仅绑定本机回环地址的服务 URL。"""
    value = str(value or "").strip()
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise OneBotError("地址格式无效") from exc
    if parsed.scheme not in schemes:
        expected = " 或 ".join(sorted(schemes))
        raise OneBotError(f"地址协议必须是 {expected}")
    if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise OneBotError("为保护账号数据，地址必须使用本机回环地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise OneBotError("地址不能包含账号、查询参数或片段")
    if parsed.path not in ("", "/"):
        raise OneBotError("地址只需填写服务根地址，不要附加接口路径")
    if port is not None and not 1 <= port <= 65535:
        raise OneBotError("端口号无效")
    netloc = parsed.netloc.rstrip("/")
    return urlunparse((parsed.scheme, netloc, "", "", "", ""))


def normalize_http_url(value):
    """规范化 OneBot HTTP 服务地址。"""
    return _normalize_loopback_url(value, {"http"})


def normalize_ws_url(value):
    """规范化 OneBot WebSocket 服务地址。"""
    return _normalize_loopback_url(value, {"ws"})


def _action_url(http_url, action):
    """构造经过白名单校验的 OneBot action URL。"""
    if not re.fullmatch(r"[A-Za-z0-9_]+", str(action or "")):
        raise OneBotError("OneBot 接口名称无效")
    return f"{normalize_http_url(http_url)}/{action}"


def _plugin_api_url(http_url, plugin_id, route):
    """构造 NapCat 插件鉴权路由 URL，只允许固定路径字符。"""
    plugin_id = str(plugin_id or "")
    route = str(route or "").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", plugin_id):
        raise OneBotError("NapCat 插件 ID 无效")
    if not route or not re.fullmatch(r"[A-Za-z0-9._/-]+", route):
        raise OneBotError("NapCat 插件接口路径无效")
    return f"{normalize_http_url(http_url)}/api/Plugin/ext/" f"{plugin_id}/{route}"


def _normalize_token(token):
    """清理复制时带入的首尾空白，并拒绝内部换行注入。"""
    value = str(token or "").strip()
    if len(value) > 4096 or "\r" in value or "\n" in value:
        raise OneBotError("Access Token 格式无效")
    return value


def normalize_token(token):
    """规范化用户在设置页粘贴的 OneBot/WebUI Token。"""
    return _normalize_token(token)


def call_action(http_url, token, action, params=None, timeout=5):
    """调用一个 OneBot 11 HTTP action 并返回 data。"""
    token = _normalize_token(token)
    body = json.dumps(params or {}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        _action_url(http_url, action), data=body, headers=headers, method="POST"
    )
    try:
        with _open_request(request, timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise OneBotError("Access Token 无效或未获授权") from exc
        raise OneBotError(f"NapCat 返回 HTTP {exc.code}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise OneBotError("连接 NapCat 超时") from exc
    except URLError as exc:
        raise OneBotError("无法连接 NapCat，请确认服务地址和网络配置") from exc
    except OSError as exc:
        raise OneBotError("无法连接 NapCat，请确认服务正在运行") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise OneBotError("NapCat 返回数据过大，已停止读取")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OneBotError("NapCat 返回了无法识别的数据") from exc
    if not isinstance(payload, dict):
        raise OneBotError("NapCat 返回格式无效")
    if payload.get("status") != "ok" or payload.get("retcode") not in (0, "0"):
        message = payload.get("wording") or payload.get("message") or "未知错误"
        raise OneBotError(f"OneBot 调用失败：{message}")
    return payload.get("data")


def call_plugin_api(http_url, token, plugin_id, route, timeout=5):
    """调用 NapCat 插件的受 Access Token 保护的 GET 路由。"""
    token = _normalize_token(token)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        _plugin_api_url(http_url, plugin_id, route),
        headers=headers,
        method="GET",
    )
    try:
        with _open_request(request, timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise OneBotError("Access Token 无效或未获授权") from exc
        if exc.code == 404:
            raise OneBotError("NapCat 插件接口不存在") from exc
        raise OneBotError(f"NapCat 插件返回 HTTP {exc.code}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise OneBotError("连接 NapCat 插件接口超时") from exc
    except URLError as exc:
        raise OneBotError("无法连接 NapCat，请确认插件服务正在运行") from exc
    except OSError as exc:
        raise OneBotError("无法连接 NapCat，请确认插件服务正在运行") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise OneBotError("NapCat 插件返回数据过大，已停止读取")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OneBotError("NapCat 插件返回了无法识别的数据") from exc
    if not isinstance(payload, dict):
        raise OneBotError("NapCat 插件返回格式无效")
    return payload


def _webui_login(http_url, token, timeout=5):
    """用 NapCat WebUI 初始 Token 换取短期鉴权凭证。"""
    token = _normalize_token(token)
    if not token:
        raise OneBotError("NapCat WebUI Token 未配置")
    body = json.dumps(
        {"hash": hashlib.sha256((token + ".napcat").encode("utf-8")).hexdigest()},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"{normalize_http_url(http_url)}/api/auth/login",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with _open_request(request, timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise OneBotError("NapCat WebUI Token 无效") from exc
        raise OneBotError(f"NapCat WebUI 登录返回 HTTP {exc.code}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise OneBotError("连接 NapCat WebUI 超时") from exc
    except URLError as exc:
        raise OneBotError("无法连接 NapCat WebUI，请确认地址和端口") from exc
    except OSError as exc:
        raise OneBotError("无法连接 NapCat WebUI，请确认服务正在运行") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise OneBotError("NapCat WebUI 返回数据过大，已停止读取")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OneBotError("NapCat WebUI 登录返回了无法识别的数据") from exc
    if not isinstance(payload, dict):
        raise OneBotError("NapCat WebUI 登录返回格式无效")
    status = payload.get("status")
    code = payload.get("code")
    if (status is not None and status != "ok") or (
        status is None and code not in (0, "0")
    ):
        message = payload.get("message") or payload.get("wording") or "未知错误"
        raise OneBotError(f"NapCat WebUI 登录失败：{message}")
    data = payload.get("data")
    credential = data.get("Credential") if isinstance(data, dict) else None
    if not isinstance(credential, str) or not credential:
        raise OneBotError("NapCat WebUI 登录未返回有效凭证")
    return credential


def _webui_credential(http_url, token, timeout=5):
    normalized_url = normalize_http_url(http_url)
    cache_key = (normalized_url, str(token or ""))
    now = time.monotonic()
    with _WEBUI_CREDENTIAL_LOCK:
        cached = _WEBUI_CREDENTIAL_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return normalized_url, cached[1]
    credential = _webui_login(normalized_url, token, timeout)
    with _WEBUI_CREDENTIAL_LOCK:
        _WEBUI_CREDENTIAL_CACHE[cache_key] = (now + _WEBUI_CREDENTIAL_TTL, credential)
    return normalized_url, credential


def call_webui_plugin_api(
    http_url,
    token,
    plugin_id,
    route,
    timeout=5,
    params=None,
    max_response_bytes=_MAX_RESPONSE_BYTES,
):
    """通过 NapCat WebUI 登录凭证调用受保护的插件 GET 路由。"""
    try:
        response_limit = int(max_response_bytes)
    except (TypeError, ValueError):
        response_limit = _MAX_RESPONSE_BYTES
    response_limit = max(
        1024, min(response_limit, _MAX_CHAT_RECORD_PLUGIN_RESPONSE_BYTES)
    )
    normalized_url = normalize_http_url(http_url)
    cache_key = (normalized_url, str(token or ""))
    request_url = _plugin_api_url(normalized_url, plugin_id, route)
    if params:
        query = {}
        for key, value in params.items():
            if value not in (None, ""):
                query[str(key)] = str(value)
        if query:
            request_url += "?" + urlencode(query)
    raw = None
    for attempt in range(2):
        # NapCat 重启会使旧 Credential 失效。第一次 401/403 时清缓存并
        # 重新登录一次，避免拖拽路径误回退到不可靠的标题识别。
        _, credential = _webui_credential(normalized_url, token, min(timeout, 10))
        request = Request(
            request_url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {credential}",
            },
            method="GET",
        )
        try:
            with _open_request(request, timeout) as response:
                raw = response.read(response_limit + 1)
            break
        except HTTPError as exc:
            if exc.code in (401, 403):
                with _WEBUI_CREDENTIAL_LOCK:
                    _WEBUI_CREDENTIAL_CACHE.pop(cache_key, None)
                if attempt == 0:
                    continue
                raise OneBotError("NapCat WebUI 鉴权凭证已失效") from exc
            if exc.code == 404:
                raise OneBotError("NapCat WebUI 插件接口不存在") from exc
            raise OneBotError(f"NapCat WebUI 插件返回 HTTP {exc.code}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise OneBotError(
                f"NapCat WebUI 插件处理超时（已等待 {int(timeout)} 秒）"
            ) from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise OneBotError(
                    f"NapCat WebUI 插件处理超时（已等待 {int(timeout)} 秒）"
                ) from exc
            raise OneBotError("无法连接 NapCat WebUI，请确认服务正在运行") from exc
        except OSError as exc:
            raise OneBotError("无法连接 NapCat WebUI，请确认服务正在运行") from exc
    if raw is None:
        raise OneBotError("NapCat WebUI 插件请求失败")
    if len(raw) > response_limit:
        raise OneBotError(
            "NapCat WebUI 插件返回数据超过 %.1f MiB，已停止读取"
            % (response_limit / (1024 * 1024))
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OneBotError("NapCat WebUI 插件返回了无法识别的数据") from exc
    if not isinstance(payload, dict):
        raise OneBotError("NapCat WebUI 插件返回格式无效")
    return payload


def consume_chat_record_drag_ticket(webui_url, webui_token, ticket_id):
    """消费桥接插件生成的一次性聊天记录拖拽凭据。"""
    ticket_id = str(ticket_id or "").strip().lower()
    if not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        ticket_id,
    ):
        raise OneBotError("聊天记录拖拽凭据无效")
    payload = call_webui_plugin_api(
        webui_url,
        webui_token,
        "napcat-plugin-ohmymeme-current-peer",
        "consume-chat-record-ticket",
        timeout=_CHAT_RECORD_PLUGIN_TIMEOUT,
        params={"ticket_id": ticket_id},
        max_response_bytes=_MAX_CHAT_RECORD_PLUGIN_RESPONSE_BYTES,
    )
    if not payload.get("ok"):
        raise OneBotError(str(payload.get("error") or "聊天记录拖拽凭据读取失败"))
    source = payload.get("source_message")
    if not isinstance(source, dict):
        raise OneBotError("聊天记录拖拽凭据没有返回来源消息")
    message_type = str(source.get("message_type") or "")
    if message_type not in ("group", "private"):
        raise OneBotError("聊天记录来源会话类型无效")
    _validate_message_id(source.get("message_id"))
    if not re.fullmatch(r"\d{1,32}", str(source.get("self_id") or "")):
        raise OneBotError("聊天记录来源账号无效")
    if not re.fullmatch(
        r"\d{1,64}", str(source.get("real_seq") or source.get("message_seq") or "")
    ):
        raise OneBotError("聊天记录来源序号无效")
    peer_id = (
        source.get("group_id") if message_type == "group" else source.get("target_id")
    )
    if not re.fullmatch(r"\d{1,32}", str(peer_id or "")):
        raise OneBotError("聊天记录来源会话无效")
    if not _message_segments(source.get("message")):
        raise OneBotError("聊天记录来源消息正文为空")
    return source


def _mask_user_id(user_id):
    """对连接测试结果中的 QQ 号做基础脱敏。"""
    value = str(user_id or "")
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def test_connection(http_url, token="", ws_url=""):
    """只读检测 NapCat/OneBot 登录态与版本信息。"""
    normalized_http = normalize_http_url(http_url)
    normalized_ws = normalize_ws_url(ws_url) if ws_url else ""
    login = call_action(normalized_http, token, "get_login_info")
    version = call_action(normalized_http, token, "get_version_info")
    if not isinstance(login, dict) or not isinstance(version, dict):
        raise OneBotError("NapCat 返回的账号或版本信息格式无效")
    return {
        "ok": True,
        "http_url": normalized_http,
        "ws_url": normalized_ws,
        "user_id": _mask_user_id(login.get("user_id")),
        "nickname": str(login.get("nickname") or ""),
        "app_name": str(version.get("app_name") or "OneBot"),
        "app_version": str(version.get("app_version") or ""),
        "protocol_version": str(version.get("protocol_version") or ""),
    }


def _list_from_data(data, keys=()):
    """从 OneBot 的列表或常见对象容器中提取列表。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _message_segments(message):
    """把 OneBot 消息规范为消息段列表。"""
    if isinstance(message, list):
        return [segment for segment in message if isinstance(segment, dict)]
    return []


def _decode_json_card(segment):
    """解析 OneBot json 段，兼容字符串、HTML 转义和双重 JSON 编码。"""
    if not isinstance(segment, dict) or segment.get("type") != "json":
        return None
    data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
    value = data.get("data")
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return None
    if len(value.encode("utf-8", errors="ignore")) > _MAX_JSON_CARD_BYTES:
        return None
    value = html.unescape(value)
    for _ in range(2):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _json_card_forward(segment):
    """从 QQ 多消息 JSON 卡片中提取 NapCat 可读取的转发资源 ID。"""
    card = _decode_json_card(segment)
    if not card or str(card.get("app") or "").lower() != "com.tencent.multimsg":
        return None
    meta = card.get("meta") if isinstance(card.get("meta"), dict) else {}
    detail = meta.get("detail") if isinstance(meta.get("detail"), dict) else {}
    forward_id = detail.get("resid") or detail.get("resId") or detail.get("res_id")
    if forward_id in (None, ""):
        return None
    forward_id = str(forward_id)
    if len(forward_id) > 4096 or any(ord(char) < 32 for char in forward_id):
        return None
    prompt = card.get("prompt") or detail.get("source") or "聊天记录"
    return {
        "type": "forward",
        "data": {"id": forward_id, "prompt": str(prompt)},
    }


def _json_card_preview(segment):
    """读取 JSON 卡片自带的人类可读摘要。

    QQ 已经把用于会话列表和合并转发预览的文案放在
    ``prompt`` 中；其他卡片则可能放在 meta 子项的标题或描述里。
    这里只读摘要字段，完整 JSON 仍保留在归档消息段中。
    """
    card = _decode_json_card(segment)
    if not card:
        return ""
    containers = [card]
    meta = card.get("meta")
    if isinstance(meta, dict):
        containers.extend(value for value in meta.values() if isinstance(value, dict))
    for container in containers:
        for key in ("prompt", "desc", "description", "title", "summary", "name"):
            value = container.get(key)
            if not isinstance(value, str):
                continue
            value = " ".join(value.split()).strip()
            if value:
                return value
    return ""


def _forward_segments(segments):
    """识别标准 forward、JSON 卡片与 NapCat 的内嵌 node 包装。"""
    result = []
    for segment in segments:
        if segment.get("type") == "forward":
            result.append(segment)
            continue
        if segment.get("type") == "node":
            data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
            children = data.get("message")
            if not isinstance(children, list):
                children = data.get("content")
            has_node_children = isinstance(children, list) and any(
                isinstance(child, dict)
                and (
                    child.get("type") == "node"
                    or isinstance(child.get("message"), list)
                    or isinstance(
                        (
                            child.get("data", {}).get("message")
                            if isinstance(child.get("data"), dict)
                            else None
                        ),
                        list,
                    )
                )
                for child in children
            )
            if has_node_children:
                # NapCat 4.18 的 get_forward_msg 会把嵌套转发表示为
                # node.data.message=[node, ...]，而不是 forward.data.content。
                # 转成统一的内联 forward 视图，保留包装节点的发送者与卡片元数据。
                forward_data = dict(data)
                forward_data["content"] = children
                result.append({"type": "forward", "data": forward_data})
                continue
        converted = _json_card_forward(segment)
        if converted:
            result.append(converted)
    return result


def _segment_preview(message, raw_message="", max_length=80):
    """生成带媒体占位符的本地消息预览。"""
    parts = []
    labels = {
        "image": "图片",
        "face": "表情",
        "mface": "表情",
        "record": "语音",
        "video": "视频",
        "file": "文件",
        "forward": "聊天记录",
        "node": "聊天记录",
        "reply": "回复",
    }
    for segment in _message_segments(message):
        kind = str(segment.get("type") or "")
        data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
        if kind == "text":
            parts.append(str(data.get("text") or ""))
        elif kind == "json":
            parts.append(_json_card_preview(segment) or "[json]")
        elif kind:
            parts.append(f"[{labels.get(kind, kind)}]")
    preview = "".join(parts).strip() or str(raw_message or "").strip()
    if max_length and len(preview) > max_length:
        return preview[: max(0, max_length - 3)] + "..."
    return preview


def _chat_type_label(chat_type):
    """把 NapCat 会话类型转换成可读名称。"""
    value = str(chat_type or "")
    return {"1": "好友", "2": "群聊"}.get(value, f"会话 {value}" if value else "会话")


def get_recent_contacts(http_url, token="", count=10):
    """只读获取最近会话，并返回适合设置页展示的脱敏摘要。"""
    try:
        count = max(1, min(int(count), 20))
    except (TypeError, ValueError):
        count = 10
    data = call_action(http_url, token, "get_recent_contact", {"count": count}, 10)
    contacts = _list_from_data(data, ("contacts", "recent_contacts"))
    result = []
    for item in contacts[:count]:
        if not isinstance(item, dict):
            continue
        latest = item.get("lastestMsg")
        latest = latest if isinstance(latest, dict) else {}
        message_id = (
            item.get("msgId") or latest.get("message_id") or latest.get("msgId")
        )
        if message_id in (None, ""):
            continue
        name = (
            item.get("peerName")
            or item.get("remark")
            or item.get("sendMemberName")
            or item.get("sendNickName")
            or "未命名会话"
        )
        peer_id = item.get("peerUin") or latest.get("group_id") or latest.get("user_id")
        result.append(
            {
                "message_id": str(message_id),
                "name": str(name),
                "peer_id": _mask_user_id(peer_id),
                "chat_type": str(item.get("chatType") or ""),
                "chat_type_label": _chat_type_label(item.get("chatType")),
                "time": item.get("msgTime") or latest.get("time") or 0,
                "preview": _segment_preview(
                    latest.get("message"),
                    latest.get("raw_message") or item.get("raw_message"),
                ),
            }
        )
    return {"ok": True, "contacts": result, "count": len(result)}


def _validate_message_id(message_id):
    """校验用户输入的消息标识，避免异常的大型参数。"""
    value = str(message_id or "").strip()
    if not value:
        raise OneBotError("请输入消息 ID")
    if len(value) > 128 or not re.fullmatch(r"[-A-Za-z0-9_:]+", value):
        raise OneBotError("消息 ID 格式无效")
    return value


def _history_probe(http_url, token, message):
    """根据外层消息位置，验证对应历史消息接口是否可读。"""
    message_type = str(message.get("message_type") or "")
    message_seq = message.get("message_seq") or message.get("real_seq")
    if message_type == "group" and message.get("group_id") not in (None, ""):
        action = "get_group_msg_history"
        params = {"group_id": message.get("group_id"), "count": 5}
    elif message_type == "private":
        self_id = str(message.get("self_id") or "")
        peer_id = message.get("target_id")
        if peer_id in (None, "") and str(message.get("user_id") or "") != self_id:
            peer_id = message.get("user_id")
        if peer_id in (None, ""):
            return {
                "status": "unavailable",
                "reason": "无法从该私聊消息确定对方账号",
            }
        action = "get_friend_msg_history"
        params = {"user_id": peer_id, "count": 5}
    else:
        return {"status": "unavailable", "reason": "消息中没有可用的会话位置"}
    if message_seq not in (None, ""):
        params["message_seq"] = message_seq
    try:
        data = call_action(http_url, token, action, params, 10)
        messages = _list_from_data(data, ("messages",))
        return {"status": "ok", "action": action, "message_count": len(messages)}
    except OneBotError as exc:
        return {"status": "error", "action": action, "error": str(exc)}


def _forward_ids(segments):
    """提取消息段中的合并转发资源 ID。"""
    result = []
    for segment in _forward_segments(segments):
        data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
        forward_id = data.get("id")
        if forward_id not in (None, ""):
            result.append(str(forward_id))
    return result


def _diagnose_forward_records(http_url, token, root_segments):
    """限量读取并统计合并转发的嵌套结构。"""
    root_ids = _forward_ids(root_segments)
    if not root_ids:
        return {"status": "not_applicable", "detected": False}

    queue = [(forward_id, 1) for forward_id in root_ids]
    seen = set()
    fetched_count = 0
    errors = []
    segment_types = set()
    node_count = 0
    max_depth = 0
    nested_forward_count = 0
    truncated = False

    def inspect_nodes(nodes, depth):
        """递归统计内嵌节点；没有内嵌内容时把资源 ID 加入队列。"""
        nonlocal node_count, max_depth, nested_forward_count, truncated
        if depth > _MAX_FORWARD_DEPTH:
            truncated = True
            return
        for node in nodes:
            if node_count >= _MAX_FORWARD_NODES:
                truncated = True
                return
            if not isinstance(node, dict):
                continue
            node_count += 1
            max_depth = max(max_depth, depth)
            segments = _message_segments(node.get("message"))
            forward_segments = _forward_segments(segments)
            for segment in segments:
                kind = str(segment.get("type") or "unknown")
                segment_types.add(kind)
            for segment in forward_segments:
                nested_forward_count += 1
                data = (
                    segment.get("data") if isinstance(segment.get("data"), dict) else {}
                )
                content = data.get("content")
                if isinstance(content, list):
                    inspect_nodes(content, depth + 1)
                elif data.get("id") not in (None, ""):
                    queue.append((str(data.get("id")), depth + 1))

    while queue and len(seen) < _MAX_FORWARD_RECORDS:
        forward_id, depth = queue.pop(0)
        if forward_id in seen:
            continue
        if depth > _MAX_FORWARD_DEPTH:
            truncated = True
            continue
        seen.add(forward_id)
        try:
            data = call_action(
                http_url,
                token,
                "get_forward_msg",
                {"message_id": forward_id},
                15,
            )
            fetched_count += 1
            inspect_nodes(_list_from_data(data, ("messages",)), depth)
        except OneBotError as exc:
            errors.append(str(exc))
    if queue:
        truncated = True
    status = "ok" if not errors else ("partial" if node_count else "error")
    return {
        "status": status,
        "detected": True,
        "root_count": len(root_ids),
        "record_count": fetched_count,
        "node_count": node_count,
        "max_depth": max_depth,
        "nested_forward_count": nested_forward_count,
        "segment_types": sorted(segment_types),
        "truncated": truncated,
        "errors": errors[:3],
    }


def diagnose_message(http_url, token="", message_id=""):
    """只读诊断一条消息的历史定位和合并转发结构。"""
    message_id = _validate_message_id(message_id)
    message = call_action(
        http_url, token, "get_msg", {"message_id": message_id}, timeout=10
    )
    if not isinstance(message, dict):
        raise OneBotError("NapCat 返回的消息格式无效")
    segments = _message_segments(message.get("message"))
    sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    sender_name = sender.get("card") or sender.get("nickname") or ""
    return {
        "ok": True,
        "message": {
            "message_id": str(message.get("message_id") or message_id),
            "message_type": str(message.get("message_type") or "unknown"),
            "message_seq": str(
                message.get("message_seq") or message.get("real_seq") or ""
            ),
            "time": message.get("time") or 0,
            "group_name": str(message.get("group_name") or ""),
            "group_id": _mask_user_id(message.get("group_id")),
            "sender_name": str(sender_name),
            "sender_id": _mask_user_id(sender.get("user_id") or message.get("user_id")),
            "segment_types": sorted(
                {str(segment.get("type") or "unknown") for segment in segments}
            ),
            "preview": _segment_preview(
                message.get("message"), message.get("raw_message")
            ),
        },
        "history": _history_probe(http_url, token, message),
        "forward": _diagnose_forward_records(http_url, token, segments),
        "warnings": ["合并转发节点中的会话字段可能不可靠，来源位置以外层消息为准。"],
    }


def normalize_command(command):
    """校验用于 QQ 内触发归档的单词命令。"""
    value = str(command or "").strip()
    if not re.fullmatch(r"/[^\s\x00-\x1f]{1,31}", value):
        raise OneBotError("触发命令必须以 / 开头，且不能包含空格")
    return value


def normalize_send_mode(send_mode):
    """校验聊天记录发送方式。"""
    value = str(send_mode or "original_native").strip()
    if value not in _CHAT_RECORD_SEND_MODES:
        raise OneBotError("聊天记录发送方式无效")
    return value


def normalize_image_send_mode(send_mode):
    """校验图片拖到 QQ 后的发送形式。"""
    value = str(send_mode or "sticker").strip()
    if value not in _IMAGE_SEND_MODES:
        raise OneBotError("图片发送形式无效")
    return value


def parse_command_event(event, command="/omm"):
    """识别自己发出的保存、列表或发送命令。"""
    if not isinstance(event, dict):
        return {"matched": False}
    post_type = str(event.get("post_type") or "")
    if post_type not in ("message", "message_sent"):
        return {"matched": False}
    self_id = str(event.get("self_id") or "")
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = str(sender.get("user_id") or event.get("user_id") or "")
    if post_type != "message_sent" and (not self_id or sender_id != self_id):
        return {"matched": False}

    reply_id = ""
    text_parts = []
    segments = _message_segments(event.get("message"))
    for segment in segments:
        data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
        if segment.get("type") == "text":
            text_parts.append(str(data.get("text") or ""))
        elif segment.get("type") == "reply" and not reply_id:
            reply_id = str(data.get("id") or "")
    text = "".join(text_parts).strip()
    raw_message = str(event.get("raw_message") or "")
    if not reply_id:
        reply_match = re.search(r"\[CQ:reply,id=([^,\]]+)", raw_message)
        if reply_match:
            reply_id = reply_match.group(1)
    if not text and raw_message:
        text = re.sub(r"\[CQ:[^\]]+\]", "", raw_message).strip()

    command = normalize_command(command)
    if text != command and not text.startswith(command + " "):
        return {"matched": False}
    arguments = text[len(command) :].strip().split()
    if arguments and arguments[0].lower() == "list":
        if len(arguments) != 1:
            return {"matched": True, "error": f"用法：{command} list"}
        return {"matched": True, "action": "list"}
    if arguments and arguments[0].lower() == "send":
        if len(arguments) != 2 or not arguments[1].isdigit():
            return {"matched": True, "error": f"用法：{command} send <归档ID>"}
        return {
            "matched": True,
            "action": "send",
            "record_id": int(arguments[1]),
        }
    if len(arguments) > 1:
        return {
            "matched": True,
            "error": f"未知命令。可用：{command}、{command} list、{command} send <ID>",
        }
    explicit_id = arguments[0] if arguments else ""
    target_id = explicit_id or reply_id
    if not target_id:
        return {
            "matched": True,
            "error": f"请回复一条合并转发消息后发送 {command}",
        }
    return {"matched": True, "target_message_id": _validate_message_id(target_id)}


def _collect_archive_nodes(nodes, depth, state, queue_items):
    """统计归档节点，并发现需要继续获取的嵌套转发资源。"""
    if depth > _MAX_ARCHIVE_DEPTH:
        raise OneBotError("聊天记录嵌套层级超过安全上限")
    for node in nodes:
        if not isinstance(node, dict):
            continue
        state["node_count"] += 1
        if state["node_count"] > _MAX_ARCHIVE_NODES:
            raise OneBotError("聊天记录节点数超过安全上限")
        state["max_depth"] = max(state["max_depth"], depth)
        segments = _node_segments(node)
        if not segments and not str(node.get("raw_message") or "").strip():
            state["empty_node_count"] += 1
        forward_segments = _forward_segments(segments)
        for segment in segments:
            kind = str(segment.get("type") or "unknown")
            state["segment_types"].add(kind)
        state["media_count"] += sum(1 for _kind, _data in _iter_segment_media(segments))
        for segment in forward_segments:
            data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
            content = data.get("content")
            if isinstance(content, list):
                state["inline_record_count"] += 1
                _collect_archive_nodes(content, depth + 1, state, queue_items)
            elif data.get("id") not in (None, ""):
                queue_items.append((str(data.get("id")), depth + 1))


def _merge_recovered_forward_data(data, recovered):
    """用插件恢复结果补齐标准 OneBot 正文，不覆盖其已解析的普通消息段。

    NapCat 的标准 OneBot 结果通常比桥接层支持更多消息类型，因此这里仍以
    ``data`` 为主体；桥接层只补充标准结果中缺失的媒体地址、嵌套转发，以及
    reply 段里的 ``omm_quoted_message``。拖拽导入会直接得到这些引用信息，
    ``/omm`` 则依赖此合并步骤，两条入口必须保持相同语义。
    """
    standard = _list_from_data(data, ("messages",))
    restored = _list_from_data(recovered, ("messages",))
    if not restored:
        return data
    merged = []
    for index in range(max(len(standard), len(restored))):
        old = standard[index] if index < len(standard) else None
        new = restored[index] if index < len(restored) else None
        if not isinstance(old, dict):
            if isinstance(new, dict):
                merged.append(new)
            continue
        if not isinstance(new, dict):
            merged.append(old)
            continue
        old_segments = _node_segments(old)
        new_segments = _node_segments(new)
        if not old_segments:
            merged.append(new)
            continue
        # 主 action 的普通段继续使用标准 OneBot 结果（它支持更多消息类型）；
        # 插件只负责把被 NapCat 丢弃的嵌套 forward 接回原位置。
        old_forwards = _forward_segments(old_segments)
        new_forwards = _forward_segments(new_segments)
        additions = new_forwards[len(old_forwards) :]
        combined_segments = []
        recovered_media = {}
        for media_kind in _ARCHIVE_MEDIA_KINDS:
            recovered_media[media_kind] = iter(
                [
                    segment
                    for segment in new_segments
                    if segment.get("type") == media_kind
                    and isinstance(segment.get("data"), dict)
                ]
            )
        recovered_replies = iter(
            [segment for segment in new_segments if segment.get("type") == "reply"]
        )
        recovered_forwards = iter(new_forwards)
        content_changed = False
        for segment in old_segments:
            kind = str(segment.get("type") or "")
            segment_data = (
                segment.get("data") if isinstance(segment.get("data"), dict) else {}
            )
            if kind == "reply":
                replacement = next(recovered_replies, None)
                replacement_data = (
                    replacement.get("data")
                    if isinstance(replacement, dict)
                    and isinstance(replacement.get("data"), dict)
                    else {}
                )
                quoted = replacement_data.get("omm_quoted_message")
                if isinstance(quoted, dict) and quoted:
                    updated_segment = dict(segment)
                    updated_data = dict(segment_data)
                    updated_data["omm_quoted_message"] = quoted
                    updated_segment["data"] = updated_data
                    combined_segments.append(updated_segment)
                    content_changed = True
                else:
                    combined_segments.append(segment)
                continue
            if kind == "forward":
                replacement = next(recovered_forwards, None)
                replacement_data = (
                    replacement.get("data")
                    if isinstance(replacement, dict)
                    and isinstance(replacement.get("data"), dict)
                    else {}
                )
                old_content = segment_data.get("content")
                new_content = replacement_data.get("content")
                if isinstance(old_content, list) and isinstance(new_content, list):
                    nested = _merge_recovered_forward_data(
                        {"messages": old_content}, {"messages": new_content}
                    )
                    merged_content = _list_from_data(nested, ("messages",))
                    if merged_content != old_content:
                        updated_segment = dict(segment)
                        updated_data = dict(segment_data)
                        updated_data["content"] = merged_content
                        updated_segment["data"] = updated_data
                        combined_segments.append(updated_segment)
                        content_changed = True
                    else:
                        combined_segments.append(segment)
                else:
                    combined_segments.append(segment)
                continue
            if kind not in recovered_media or not isinstance(segment.get("data"), dict):
                combined_segments.append(segment)
                continue
            replacement = next(recovered_media[kind], None)
            replacement_data = (
                replacement.get("data") if isinstance(replacement, dict) else {}
            )
            replacement_data = (
                replacement_data if isinstance(replacement_data, dict) else {}
            )
            candidates = [replacement_data.get("url")]
            candidates.extend(replacement_data.get("omm_fallback_urls") or [])
            candidates = [
                str(value).strip()
                for value in candidates
                if str(value or "").lower().startswith(("http://", "https://"))
                and len(str(value)) <= 8192
            ][:5]
            replacement_path = str(replacement_data.get("path") or "").strip()
            if not candidates and not replacement_path:
                combined_segments.append(segment)
                continue
            updated_segment = dict(segment)
            updated_data = dict(segment["data"])
            existing = [
                str(value).strip()
                for value in updated_data.get("omm_fallback_urls") or []
                if str(value or "").lower().startswith(("http://", "https://"))
            ]
            if candidates:
                if kind == "image":
                    updated_data["omm_fallback_urls"] = list(
                        dict.fromkeys(existing + candidates)
                    )[:5]
                else:
                    previous_url = str(updated_data.get("url") or "").strip()
                    updated_data["url"] = candidates[0]
                    fallback = existing + candidates[1:]
                    if previous_url and previous_url != candidates[0]:
                        fallback.append(previous_url)
                    if fallback:
                        updated_data["omm_fallback_urls"] = list(
                            dict.fromkeys(fallback)
                        )[:5]
            if replacement_path:
                updated_data["path"] = replacement_path
            updated_segment["data"] = updated_data
            combined_segments.append(updated_segment)
            content_changed = True
        if not additions and not content_changed:
            merged.append(old)
            continue
        combined = dict(old)
        combined["message"] = combined_segments + additions
        if additions:
            combined["raw_message"] = str(old.get("raw_message") or "") + "[聊天记录]"
        merged.append(combined)
    metadata = {}
    native_payload = recovered.get("native_payload_b64")
    if isinstance(native_payload, str) and native_payload:
        metadata["omm_native_payload_b64"] = native_payload
    if isinstance(data, dict):
        result = dict(data)
        result["messages"] = merged
        result.update(metadata)
        return result
    return {"messages": merged, **metadata}


def _recover_forward_resource(webui_url, webui_token, forward_id, source=None):
    """通过桥接插件读取同一 QQ 长消息资源中的附属 action。"""
    if not webui_url or not webui_token:
        return None
    params = {"res_id": forward_id}
    identity = _persistent_source_identity(source)
    if identity:
        params.update(
            {key: value for key, value in identity.items() if value not in (None, "")}
        )
    payload = call_webui_plugin_api(
        webui_url,
        webui_token,
        "napcat-plugin-ohmymeme-current-peer",
        "recover-forward-resource",
        timeout=_CHAT_RECORD_PLUGIN_TIMEOUT,
        params=params,
        max_response_bytes=_MAX_CHAT_RECORD_PLUGIN_RESPONSE_BYTES,
    )
    if not payload.get("ok"):
        raise OneBotError(str(payload.get("error") or "桥接插件未能恢复多层聊天记录"))
    if not _list_from_data(payload, ("messages",)):
        raise OneBotError("桥接插件没有返回多层聊天记录正文")
    return payload


def _recover_original_message_by_identity(webui_url, webui_token, source):
    """按持久会话位置重新读取外层原消息。

    NapCat 重启后 OneBot message_id 可能失效，但会话 ID + QQ 真实
    msgSeq 仍可以由桥接插件定位。
    """
    identity = _persistent_source_identity(source)
    if not identity:
        raise OneBotError("归档缺少来源会话或真实消息序号，无法重新读取原消息")
    payload = call_webui_plugin_api(
        webui_url,
        webui_token,
        "napcat-plugin-ohmymeme-current-peer",
        "recover-message",
        timeout=_CHAT_RECORD_PLUGIN_TIMEOUT,
        params={
            key: value for key, value in identity.items() if value not in (None, "")
        },
        max_response_bytes=_MAX_CHAT_RECORD_PLUGIN_RESPONSE_BYTES,
    )
    if not payload.get("ok"):
        raise OneBotError(str(payload.get("error") or "无法按持久位置读取原消息"))
    recovered = payload.get("message")
    if not isinstance(recovered, dict) or not _message_segments(
        recovered.get("message")
    ):
        raise OneBotError("桥接插件没有返回外层消息正文")
    recovered = dict(recovered)
    native_payload = payload.get("native_payload_b64")
    if isinstance(native_payload, str) and native_payload:
        recovered["_omm_native_payload_b64"] = native_payload
    native_card = payload.get("native_card")
    if isinstance(native_card, dict):
        recovered["_omm_native_card"] = native_card
    source = source if isinstance(source, dict) else {}
    for key in (
        "self_id",
        "message_type",
        "group_id",
        "group_name",
        "target_id",
        "user_id",
        "sender",
        "real_seq",
        "time",
    ):
        if recovered.get(key) in (None, "") and source.get(key) not in (None, ""):
            recovered[key] = source[key]
    return recovered


def _recover_original_message_by_id(webui_url, webui_token, message_id, source_context):
    """在命令所在会话内按 QQ 原始消息 ID 恢复完整外层消息。"""
    context = source_context if isinstance(source_context, dict) else {}
    message_type = str(context.get("message_type") or "")
    self_id = str(context.get("self_id") or "")
    if message_type == "group":
        peer_id = context.get("group_id")
    elif message_type == "private":
        peer_id = context.get("target_id")
        if peer_id in (None, "") and str(context.get("user_id") or "") != self_id:
            peer_id = context.get("user_id")
    else:
        peer_id = None
    if peer_id in (None, ""):
        raise OneBotError("无法确定被回复消息所属的 QQ 会话")
    payload = call_webui_plugin_api(
        webui_url,
        webui_token,
        "napcat-plugin-ohmymeme-current-peer",
        "recover-message-by-id",
        timeout=_CHAT_RECORD_PLUGIN_TIMEOUT,
        params={
            "message_id": _validate_message_id(message_id),
            "source_type": message_type,
            "source_peer_id": peer_id,
            "source_self_id": self_id,
        },
        max_response_bytes=_MAX_CHAT_RECORD_PLUGIN_RESPONSE_BYTES,
    )
    if not payload.get("ok"):
        raise OneBotError(str(payload.get("error") or "无法按消息标识读取原消息"))
    recovered = payload.get("message")
    if not isinstance(recovered, dict) or not _message_segments(
        recovered.get("message")
    ):
        raise OneBotError("桥接插件没有返回外层消息正文")
    recovered = dict(recovered)
    native_payload = payload.get("native_payload_b64")
    if isinstance(native_payload, str) and native_payload:
        recovered["_omm_native_payload_b64"] = native_payload
    native_card = payload.get("native_card")
    if isinstance(native_card, dict):
        recovered["_omm_native_card"] = native_card
    for key in (
        "self_id",
        "message_type",
        "group_id",
        "group_name",
        "target_id",
        "user_id",
        "sender",
        "time",
    ):
        if recovered.get(key) in (None, "") and context.get(key) not in (None, ""):
            recovered[key] = context[key]
    return recovered


def build_chat_record_package(
    http_url,
    token,
    message_id,
    trigger_event=None,
    webui_url="",
    webui_token="",
    source_message=None,
):
    """读取一条合并转发消息并组装可持久化的完整结构包。"""
    message_id = _validate_message_id(message_id)
    trigger_event = trigger_event if isinstance(trigger_event, dict) else {}
    source = source_message
    source_from_onebot = source is None
    if source_from_onebot:
        try:
            source = call_action(
                http_url, token, "get_msg", {"message_id": message_id}, timeout=10
            )
        except OneBotError as direct_error:
            if not webui_url or not webui_token:
                raise
            try:
                source = _recover_original_message_by_id(
                    webui_url, webui_token, message_id, trigger_event
                )
            except OneBotError as recovery_error:
                raise OneBotError(
                    f"OneBot 无法读取被回复消息；桥接插件按消息标识恢复也失败："
                    f"{recovery_error}"
                ) from direct_error
    if not isinstance(source, dict):
        raise OneBotError("NapCat 返回的消息格式无效")
    source = dict(source)
    if source.get("message_id") in (None, ""):
        source["message_id"] = message_id
    if not source.get("self_id"):
        source["self_id"] = trigger_event.get("self_id")
    if not source.get("message_type"):
        source["message_type"] = trigger_event.get("message_type")
    if not source.get("group_id") and trigger_event.get("group_id"):
        source["group_id"] = trigger_event.get("group_id")
    if not source.get("target_id") and trigger_event.get("target_id"):
        source["target_id"] = trigger_event.get("target_id")
    if (
        source_from_onebot
        and source.get("real_seq") in (None, "")
        and webui_url
        and webui_token
    ):
        # 当前 NapCat 的 get_msg 对回复目标有时只返回消息正文和短 ID，
        # 没有可供 recover-message 使用的真实 msgSeq。/omm 已明确处于目标
        # 会话中，因此按回复段携带的 QQ 原始 msgId 恢复一次完整消息。
        try:
            source = _recover_original_message_by_id(
                webui_url, webui_token, message_id, source
            )
        except OneBotError as exc:
            logger.info("NapCat 命令消息按原始 ID 增强恢复失败: %s", exc)
    recovered_native_payload = str(source.pop("_omm_native_payload_b64", "") or "")
    recovered_native_card = source.pop("_omm_native_card", None)
    archive_bytes = len(
        json.dumps(source, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    if archive_bytes > _MAX_ARCHIVE_BYTES:
        raise OneBotError("聊天记录结构超过 64 MiB，未执行保存")

    root_segments = _message_segments(source.get("message"))
    native_card = (
        recovered_native_card if isinstance(recovered_native_card, dict) else None
    )
    for segment in root_segments:
        card = _decode_json_card(segment)
        if card and str(card.get("app") or "").lower() == "com.tencent.multimsg":
            native_card = card
            break
    root_forwards = _forward_segments(root_segments)
    if (
        not recovered_native_payload
        and any(
            isinstance(
                segment.get("data") if isinstance(segment, dict) else None,
                dict,
            )
            and isinstance(segment["data"].get("content"), list)
            for segment in root_forwards
        )
        and webui_url
        and webui_token
    ):
        # parseMultMsg 会把根记录直接内联成 data.content，此时旧流程不会
        # 进入 forward_records 队列。重新从外层 QQ 原消息读取一次原始
        # 长消息负载，正文仍沿用已经取得的 OneBot 结果。
        try:
            recovered_source = _recover_original_message_by_identity(
                webui_url, webui_token, source
            )
        except OneBotError as exc:
            logger.info("NapCat 根聊天记录原生负载补全失败: %s", exc)
        else:
            recovered_native_payload = str(
                recovered_source.pop("_omm_native_payload_b64", "") or ""
            )
            recovered_card = recovered_source.pop("_omm_native_card", None)
            if isinstance(recovered_card, dict):
                native_card = recovered_card
            # get_msg/parseMultMsg 的正文仍作为主体，但它会把 reply 段压缩为
            # 只有消息 ID。把 recover-message 中已经恢复的引用正文合回内联
            # 节点，使 /omm 与拖拽导入获得相同的被引用媒体。
            recovered_segments = _message_segments(recovered_source.get("message"))
            if recovered_segments:
                merged_source = _merge_recovered_forward_data(
                    {"messages": [{"message": root_segments}]},
                    {"messages": [{"message": recovered_segments}]},
                )
                merged_nodes = _list_from_data(merged_source, ("messages",))
                if merged_nodes:
                    merged_segments = _node_segments(merged_nodes[0])
                    if merged_segments:
                        source["message"] = merged_segments
                        root_segments = merged_segments
                        root_forwards = _forward_segments(root_segments)
    prefetched_records = {}
    if not root_forwards:
        # NapCat 偶尔会让 get_msg 丢失 forward 段，但 get_forward_msg 仍可用
        # 外层消息 ID 正常读取。先直接探测，避免把真实聊天记录误判为普通消息。
        fallback_id = str(source.get("message_id") or message_id)
        try:
            fallback_data = call_action(
                http_url,
                token,
                "get_forward_msg",
                {"message_id": fallback_id},
                20,
            )
        except OneBotError as exc:
            logger.info("NapCat 外层消息 ID 合并转发探测失败: %s", exc)
        else:
            if isinstance(fallback_data, (dict, list)) and _list_from_data(
                fallback_data, ("messages",)
            ):
                root_forwards = [{"type": "forward", "data": {"id": fallback_id}}]
                prefetched_records[fallback_id] = fallback_data
    if not root_forwards:
        segment_types = sorted(
            {str(segment.get("type") or "unknown") for segment in root_segments}
        )
        detected = "、".join(segment_types) if segment_types else "无可识别消息段"
        raise OneBotError(
            f"无法读取被回复消息的合并转发内容（检测到：{detected}）；"
            "请回复外层聊天记录卡片，而不是卡片内的单条消息"
        )

    queue_items = []
    state = {
        "node_count": 0,
        "max_depth": 0,
        "media_count": 0,
        "empty_node_count": 0,
        "inline_record_count": 0,
        "segment_types": set(),
    }
    for segment in root_forwards:
        data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
        if isinstance(data.get("content"), list):
            state["inline_record_count"] += 1
            _collect_archive_nodes(data["content"], 1, state, queue_items)
        elif data.get("id") not in (None, ""):
            queue_items.append((str(data.get("id")), 1))

    records = {}
    recovery_errors = []
    seen = set()
    while queue_items:
        forward_id, depth = queue_items.pop(0)
        if forward_id in seen:
            continue
        if len(seen) >= _MAX_ARCHIVE_RECORDS:
            raise OneBotError("聊天记录包含的转发资源过多，未保存不完整数据")
        if depth > _MAX_ARCHIVE_DEPTH:
            raise OneBotError("聊天记录嵌套层级超过安全上限")
        seen.add(forward_id)
        data = prefetched_records.pop(forward_id, None)
        if data is None:
            data = call_action(
                http_url,
                token,
                "get_forward_msg",
                {"message_id": forward_id},
                20,
            )
        if not isinstance(data, (dict, list)):
            raise OneBotError("NapCat 返回的合并转发格式无效")
        probe_state = {
            "node_count": 0,
            "max_depth": 0,
            "media_count": 0,
            "empty_node_count": 0,
            "inline_record_count": 0,
            "segment_types": set(),
        }
        _collect_archive_nodes(
            _list_from_data(data, ("messages",)), depth, probe_state, []
        )
        needs_structure_recovery = probe_state["empty_node_count"] > 0
        should_hydrate_media = depth == 1
        if (
            (needs_structure_recovery or should_hydrate_media)
            and webui_url
            and webui_token
        ):
            try:
                recovered = _recover_forward_resource(
                    webui_url, webui_token, forward_id, source
                )
                data = _merge_recovered_forward_data(data, recovered)
            except OneBotError as exc:
                if needs_structure_recovery:
                    recovery_errors.append(str(exc))
                    logger.info("NapCat 多层聊天记录 action 恢复失败: %s", exc)
                else:
                    logger.info(
                        "NapCat 聊天记录媒体补全失败，继续使用后备链路: %s", exc
                    )
        archive_bytes += len(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        if archive_bytes > _MAX_ARCHIVE_BYTES:
            raise OneBotError("聊天记录结构超过 64 MiB，未执行保存")
        records[forward_id] = data
        _collect_archive_nodes(
            _list_from_data(data, ("messages",)), depth, state, queue_items
        )

    if state["empty_node_count"]:
        recovery_note = (
            f"；桥接插件恢复失败：{recovery_errors[-1]}" if recovery_errors else ""
        )
        raise OneBotError(
            f"检测到 {state['empty_node_count']} 个未解析的空消息节点；"
            "NapCat 没有返回这些节点的正文。请确认 OneBot 配置中的 "
            "parseMultMsg 已开启；如果已经开启，通常表示原节点已过期、"
            "被撤回，或属于当前 NapCat 版本尚未支持的消息类型"
            f"{recovery_note}"
        )

    root_data = root_forwards[0].get("data")
    root_data = root_data if isinstance(root_data, dict) else {}
    title = str(
        root_data.get("prompt") or source.get("group_name") or "聊天记录"
    ).strip()
    title = title[:77] + "..." if len(title) > 80 else title
    if title == source.get("group_name"):
        title += " · 聊天记录"
    package = {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "source": source,
        "native_card": native_card,
        "trigger": {
            "message_id": trigger_event.get("message_id"),
            "message_type": trigger_event.get("message_type"),
            "group_id": trigger_event.get("group_id"),
            "target_id": trigger_event.get("target_id"),
            "time": trigger_event.get("time"),
        },
        "forward_records": records,
        "summary": {
            "record_count": len(records) + state["inline_record_count"],
            "node_count": state["node_count"],
            "max_depth": state["max_depth"],
            "media_count": state["media_count"],
            "segment_types": sorted(state["segment_types"]),
            "media_localized": False,
        },
    }
    if recovered_native_payload:
        package["omm_native_payload_b64"] = recovered_native_payload
    encoded = json.dumps(package, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > _MAX_ARCHIVE_BYTES:
        raise OneBotError("聊天记录结构超过 64 MiB，未执行保存")
    return package


def archive_chat_record(
    http_url,
    token,
    message_id,
    trigger_event=None,
    db=None,
    webui_url="",
    webui_token="",
    source_message=None,
):
    """读取并把聊天记录包写入本地数据库。"""
    package = build_chat_record_package(
        http_url,
        token,
        message_id,
        trigger_event=trigger_event,
        webui_url=webui_url,
        webui_token=webui_token,
        source_message=source_message,
    )
    from .config import get_config

    if db is None:
        from .database import get_db

        db = get_db()
    source = package.get("source") if isinstance(package, dict) else {}
    source = source if isinstance(source, dict) else {}
    previous = db.get_chat_record_by_source(
        str(source.get("self_id") or ""), str(source.get("message_id") or "")
    )
    if previous:
        previous_folder = str(previous.get("_omm_media_folder") or "").strip()
        if previous_folder:
            package["_omm_media_folder"] = previous_folder
        _reuse_archived_media_paths(previous, package)
        _reuse_archived_native_forward(previous, package)
    cfg = get_config()
    media_root = cfg.chat_record_media_dir
    _localize_archive_media(
        http_url,
        token,
        package,
        media_root,
        storage_root=cfg.cache_dir,
    )
    saved = db.save_chat_record(package)
    _prune_archive_media(package, media_root, storage_root=cfg.cache_dir)
    saved_entry = db.get_chat_record_entry(saved["id"])
    display_name = (
        str(saved_entry.get("name") or "").strip()
        if isinstance(saved_entry, dict)
        else ""
    )
    # 归档由后台 OneBot worker 完成，不经过主窗口点击事件。
    from .library_events import notify_changed

    notify_changed("content")
    return {
        "ok": True,
        "id": saved["id"],
        "created": saved["created"],
        "title": package["title"],
        "name": display_name or package["title"],
        **package["summary"],
    }


def archive_dragged_chat_record(
    http_url,
    token,
    webui_url,
    webui_token,
    ticket_id,
    db=None,
):
    """消费 QQ 拖拽凭据并复用标准聊天记录归档流程。"""
    source = consume_chat_record_drag_ticket(webui_url, webui_token, ticket_id)
    return archive_chat_record(
        http_url,
        token,
        source.get("message_id"),
        trigger_event=source,
        db=db,
        webui_url=webui_url,
        webui_token=webui_token,
        source_message=source,
    )


def migrate_legacy_chat_record_media(db=None, cfg=None):
    """把旧版应用数据目录媒体迁入 cache_dir，并改写为相对路径。"""
    from .config import get_config

    if cfg is None:
        cfg = get_config()
    if db is None:
        from .database import get_db

        db = get_db()
    legacy_root = Path(cfg.data_dir) / "chat-record-media"
    media_root = Path(cfg.chat_record_media_dir)
    try:
        if legacy_root.resolve() == media_root.resolve():
            return {"records": 0, "files": 0}
    except OSError:
        if legacy_root == media_root:
            return {"records": 0, "files": 0}
    total = db.count_chat_records()
    rows = db.list_chat_records(limit=total) if total else []
    migrated_records = 0
    migrated_files = 0
    for row in rows:
        package = db.get_chat_record(int(row["id"]))
        if not package:
            continue
        archive_dir = _archive_media_dir(package, media_root)
        changed = False
        copied = []
        old_sources = []
        for index, (kind, data) in enumerate(_archive_media_entries(package), 1):
            value = str(data.get("local_path") or "").strip()
            if not value:
                continue
            if _archive_media_source(data, cfg=cfg) and not Path(value).is_absolute():
                continue
            try:
                source = Path(value)
                source_resolved = source.resolve()
                legacy_resolved = legacy_root.resolve()
                current_resolved = media_root.resolve()
            except (OSError, ValueError):
                continue
            in_legacy = (
                source_resolved == legacy_resolved
                or legacy_resolved in source_resolved.parents
            )
            in_current = (
                source_resolved == current_resolved
                or current_resolved in source_resolved.parents
            )
            if not source_resolved.is_file() or not (in_legacy or in_current):
                continue
            destination = archive_dir / (
                f"{index:05d}{_media_suffix(source_resolved, data, kind)}"
            )
            try:
                same_file = source_resolved == destination.resolve()
            except OSError:
                same_file = source_resolved == destination
            if not same_file:
                archive_dir.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix(destination.suffix + ".tmp")
                try:
                    shutil.copyfile(source_resolved, temporary)
                    os.replace(temporary, destination)
                except OSError as exc:
                    logger.warning("聊天记录旧媒体迁移失败: %s", exc)
                    try:
                        temporary.unlink()
                    except OSError:
                        pass
                    continue
                copied.append(destination)
                if in_legacy:
                    old_sources.append(source_resolved)
            data["local_path"] = _stored_archive_media_path(destination, cfg.cache_dir)
            migrated_files += 1
            changed = True
        if not changed:
            continue
        try:
            db.save_chat_record(package)
        except Exception:
            for path in copied:
                try:
                    path.unlink()
                except OSError:
                    pass
            logger.exception("聊天记录旧媒体路径更新失败 #%s", row["id"])
            continue
        for path in old_sources:
            try:
                path.unlink()
            except OSError:
                pass
        _prune_archive_media(package, media_root, storage_root=cfg.cache_dir)
        migrated_records += 1
    if legacy_root.is_dir():
        # 仅清理已经搬空的目录；未被数据库引用的旧文件保持原样，避免升级
        # 迁移在无法判断归属时误删用户仍可能需要的媒体。
        for root, dirs, _files in os.walk(legacy_root, topdown=False):
            for name in dirs:
                try:
                    (Path(root) / name).rmdir()
                except OSError:
                    pass
        try:
            legacy_root.rmdir()
        except OSError:
            pass
    return {"records": migrated_records, "files": migrated_files}


def delete_chat_record_archive(record_id, db=None, cfg=None):
    """删除聊天记录及其独占媒体目录。"""
    from .config import get_config

    if cfg is None:
        cfg = get_config()
    if db is None:
        from .database import get_db

        db = get_db()
    package = db.get_chat_record(int(record_id))
    if not package:
        return False
    allowed_roots = [
        Path(cfg.chat_record_media_dir),
        Path(cfg.data_dir) / "chat-record-media",
    ]
    media_files = []
    for _kind, data in _archive_media_entries(package):
        value = str(data.get("local_path") or "").strip()
        if not value:
            continue
        source = _archive_media_source(data, cfg=cfg)
        if not source:
            source = _media_send_source({"local_path": value})
        if not source:
            continue
        try:
            path = Path(source).resolve()
            if any(
                path == root.resolve() or root.resolve() in path.parents
                for root in allowed_roots
            ):
                media_files.append(path)
        except OSError:
            continue
    if not db.delete_chat_record(int(record_id)):
        return False
    for path in media_files:
        try:
            path.unlink()
        except OSError:
            pass
    archive_dir = _archive_media_dir(package, cfg.chat_record_media_dir)
    try:
        shutil.rmtree(archive_dir)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("聊天记录媒体目录删除失败 #%s: %s", record_id, exc)
    for root in allowed_roots:
        try:
            legacy_dir = _archive_media_dir(package, root)
            legacy_dir.rmdir()
            root.rmdir()
        except OSError:
            pass
    return True


def _archive_root_nodes(package):
    """从归档包中找出顶层节点，兼容标准 forward、JSON 卡片与旧包。"""
    if not isinstance(package, dict):
        return []
    records = package.get("forward_records")
    records = records if isinstance(records, dict) else {}
    source = package.get("source")
    source = source if isinstance(source, dict) else {}
    roots = _forward_segments(_message_segments(source.get("message")))
    result = []
    for segment in roots:
        data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
        if isinstance(data.get("content"), list):
            result.extend(data["content"])
            continue
        record = records.get(str(data.get("id") or ""))
        result.extend(_list_from_data(record, ("messages",)))
    if not result and records:
        result = _list_from_data(next(iter(records.values())), ("messages",))
    return [node for node in result if isinstance(node, dict)]


def _node_segments(node):
    """读取 NapCat 返回节点中的消息段。"""
    segments = _message_segments(node.get("message"))
    if segments:
        return segments
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    segments = _message_segments(data.get("message"))
    if segments:
        return segments
    return _message_segments(data.get("content"))


def _quoted_message(segment):
    """读取桥接层随 reply 段保存的被引用原消息。"""
    if not isinstance(segment, dict) or segment.get("type") != "reply":
        return {}
    data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
    quoted = data.get("omm_quoted_message")
    return quoted if isinstance(quoted, dict) else {}


def _quoted_segments(segment):
    quoted = _quoted_message(segment)
    return _message_segments(quoted.get("message"))


def _iter_segment_media(segments, quote_depth=0):
    """按消息顺序遍历正文及回复引用中的媒体段。"""
    for segment in _message_segments(segments):
        kind = str(segment.get("type") or "")
        data = segment.get("data")
        if kind in _ARCHIVE_MEDIA_KINDS and isinstance(data, dict):
            yield kind, data
        if kind == "reply" and quote_depth < 3:
            yield from _iter_segment_media(_quoted_segments(segment), quote_depth + 1)


def _nested_nodes(package, segment):
    """读取一个 forward 段引用的内嵌或已归档节点。"""
    data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
    if isinstance(data.get("content"), list):
        return [node for node in data["content"] if isinstance(node, dict)]
    records = package.get("forward_records")
    records = records if isinstance(records, dict) else {}
    record = records.get(str(data.get("id") or ""))
    return _list_from_data(record, ("messages",))


def _indexed_forward_segments(segments):
    """保留原消息段下标地识别所有 forward 兼容表示。"""
    for segment_index, segment in enumerate(segments):
        for forward_segment in _forward_segments([segment]):
            yield segment_index, forward_segment


def normalize_chat_record_blocked_paths(values):
    """校验并压缩详情页选中的消息节点路径。

    父节点被屏蔽时整个子树都不发送，因此同时保存子路径没有意义。
    """
    if values is None:
        return []
    if not isinstance(values, (list, tuple, set)):
        raise ValueError("聊天记录屏蔽数据无效")
    unique = set()
    for value in values:
        path = str(value or "").strip()
        if not path or len(path) > 256 or not _CHAT_RECORD_NODE_PATH_RE.fullmatch(path):
            raise ValueError("聊天记录屏蔽路径无效")
        unique.add(path)
        if len(unique) > _MAX_ARCHIVE_NODES:
            raise ValueError("聊天记录屏蔽数量超过上限")
    result = []
    for path in sorted(unique, key=lambda item: (item.count("/"), item)):
        if any(path.startswith(f"{parent}/") for parent in result):
            continue
        result.append(path)
    return result


def chat_record_blocked_paths(package):
    """读取归档中的发送屏蔽规则；旧包或损坏值按未设置处理。"""
    package = package if isinstance(package, dict) else {}
    send_filter = package.get("send_filter")
    send_filter = send_filter if isinstance(send_filter, dict) else {}
    try:
        return normalize_chat_record_blocked_paths(
            send_filter.get("blocked_node_paths", [])
        )
    except ValueError:
        return []


def set_chat_record_blocked_paths(package, values):
    """在完整归档包中写入非破坏性的发送屏蔽规则。"""
    if not isinstance(package, dict):
        raise ValueError("聊天记录包格式无效")
    paths = normalize_chat_record_blocked_paths(values)
    send_filter = package.get("send_filter")
    send_filter = dict(send_filter) if isinstance(send_filter, dict) else {}
    if paths:
        send_filter["blocked_node_paths"] = paths
        package["send_filter"] = send_filter
    else:
        send_filter.pop("blocked_node_paths", None)
        if send_filter:
            package["send_filter"] = send_filter
        else:
            package.pop("send_filter", None)
    return paths


def _iter_archive_nodes(package):
    """按详情页与媒体归档共用的顺序遍历聊天节点。

    外部 forward 引用可能在一个归档包中重复出现；与媒体落盘时一样，
    只展开一次，保证详情页生成的媒体序号可以稳定映射到本地副本。
    """
    seen_forward_ids = set()
    visited_nodes = 0

    def walk(nodes, depth, parent_path=""):
        nonlocal visited_nodes
        if depth > _MAX_ARCHIVE_DEPTH:
            return
        for node_index, node in enumerate(nodes):
            if not isinstance(node, dict) or visited_nodes >= _MAX_ARCHIVE_NODES:
                continue
            node_path = (
                f"{parent_path}/n{node_index}" if parent_path else f"n{node_index}"
            )
            visited_nodes += 1
            yield node, depth, node_path
            segments = _node_segments(node)
            for segment_index, segment in _indexed_forward_segments(segments):
                data = (
                    segment.get("data") if isinstance(segment.get("data"), dict) else {}
                )
                child_path = f"{node_path}/s{segment_index}"
                if isinstance(data.get("content"), list):
                    yield from walk(data["content"], depth + 1, child_path)
                    continue
                forward_id = str(data.get("id") or "")
                if not forward_id or forward_id in seen_forward_ids:
                    continue
                seen_forward_ids.add(forward_id)
                yield from walk(_nested_nodes(package, segment), depth + 1, child_path)

    yield from walk(_archive_root_nodes(package), 0)


def chat_record_view(package, media_url_factory=None):
    """把完整归档包转换为详情页可直接展示的扁平消息列表。

    ``media_url_factory`` 可选，用于为已经保存到本地的图片和视频生成详情页
    预览地址。媒体序号包含图片、视频和语音等所有媒体段，以便与归档文件名
    保持一致。
    """
    messages = []
    media_index = 0

    for node, depth, node_path in _iter_archive_nodes(package):
        sender = node.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        segments = _node_segments(node)
        nickname = (
            sender.get("card")
            or sender.get("nickname")
            or data.get("nickname")
            or data.get("name")
            or "未知发送者"
        )
        sender_id = sender.get("user_id") or data.get("user_id") or data.get("uin")
        preview = _segment_preview(segments, node.get("raw_message"), max_length=4000)
        message = {
            "path": node_path,
            "depth": depth,
            "sender": str(nickname),
            "sender_id": str(sender_id or ""),
            "time": node.get("time") or data.get("time") or 0,
            "preview": preview or "[空消息或未解析的嵌套聊天记录]",
            "segment_types": sorted(
                {str(item.get("type") or "unknown") for item in segments}
            ),
        }
        media = []
        quotes = []

        def append_media(kind, segment_data, target, slot, quoted=False):
            nonlocal media_index
            media_index += 1
            if (
                kind in ("image", "video")
                and str(segment_data.get("local_path") or "").strip()
                and callable(media_url_factory)
            ):
                descriptor = {
                    "kind": kind,
                    "index": media_index,
                    "slot": slot,
                    "url": str(media_url_factory(media_index, kind) or ""),
                }
                if quoted:
                    descriptor["quoted"] = True
                label = (
                    segment_data.get("file")
                    or segment_data.get("name")
                    or segment_data.get("path")
                )
                if label:
                    descriptor["alt"] = str(label)
                target.append(descriptor)

        media_slot = 0
        for segment in segments:
            kind = str(segment.get("type") or "")
            segment_data = segment.get("data")
            if kind in _ARCHIVE_MEDIA_KINDS and isinstance(segment_data, dict):
                append_media(kind, segment_data, media, media_slot)
                media_slot += 1
                continue
            if kind != "reply":
                continue
            quoted = _quoted_message(segment)
            quoted_segments = _quoted_segments(segment)
            if not quoted_segments:
                continue
            quoted_media = []
            for quoted_slot, (quoted_kind, quoted_data) in enumerate(
                _iter_segment_media(quoted_segments)
            ):
                append_media(
                    quoted_kind,
                    quoted_data,
                    quoted_media,
                    quoted_slot,
                    quoted=True,
                )
            quote_sender = quoted.get("sender")
            quote_sender = quote_sender if isinstance(quote_sender, dict) else {}
            quote = {
                "sender": str(
                    quote_sender.get("card") or quote_sender.get("nickname") or "原消息"
                ),
                "time": quoted.get("time") or 0,
                "preview": _segment_preview(
                    quoted_segments,
                    quoted.get("raw_message"),
                    max_length=4000,
                )
                or "[未解析的引用内容]",
            }
            if quoted_media:
                quote["media"] = quoted_media
                media.extend(quoted_media)
            quotes.append(quote)
        if media:
            message["media"] = media
        if quotes:
            message["quotes"] = quotes
        messages.append(message)

    return messages


def _media_send_source(data):
    """从接收态媒体段中挑选 NapCat 能重新读取的 URL 或本地文件。"""
    for key in ("local_path", "path", "url", "file"):
        value = str(data.get(key) or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered.startswith(("http://", "https://", "file://", "base64://")):
            return value
        try:
            path = Path(value)
            if path.is_absolute() and path.is_file():
                return str(path)
        except (OSError, ValueError):
            continue
    return ""


def _archive_media_source(data, cfg=None, storage_root=None):
    """解析归档媒体路径；新版相对路径始终相对于当前表情包存储根目录。"""
    value = str(data.get("local_path") or "").strip()
    if not value:
        return ""
    direct = _media_send_source({"local_path": value})
    if direct:
        return direct
    try:
        from .config import CHAT_RECORD_MEDIA_DIRNAME, get_config

        if cfg is None:
            try:
                cfg = get_config()
            except OSError:
                # 传入 storage_root 时解析归档路径不需要读取全局配置；
                # 配置文件暂时不可读不应让备份恢复后的媒体失效。
                cfg = None
        parts = Path(value.replace("\\", "/")).parts
        if (
            not parts
            or parts[0].casefold() != CHAT_RECORD_MEDIA_DIRNAME.casefold()
            or any(part in ("", ".", "..") for part in parts)
        ):
            return ""
        if storage_root is None:
            storage_root = cfg.cache_dir
        root = Path(storage_root)
        path = root.joinpath(*parts)
        # 普通磁盘继续以 resolve 防御符号链接逃逸；开启虚拟盘兼容时，
        # CloudDrive/115 可能不支持最终路径 API，退回纯词法规范化。
        virtual_compat = (
            cfg.get("virtual_drive_compat", False)
            if hasattr(cfg, "get")
            else (
                getattr(cfg, "virtual_drive_compat", False)
                if cfg is not None
                else False
            )
        ) is True
        if virtual_compat:
            normalized_root = Path(os.path.abspath(os.path.normpath(str(root))))
            normalized_path = Path(os.path.abspath(os.path.normpath(str(path))))
            try:
                if os.path.commonpath(
                    (str(normalized_root), str(normalized_path))
                ) != str(normalized_root):
                    return ""
            except ValueError:
                return ""
            path = normalized_path
        else:
            root = root.resolve()
            path = path.resolve()
            if root not in path.parents:
                return ""
        if not path.is_file():
            return ""
        return str(path)
    except (OSError, ValueError):
        return ""


def _archive_media_dir(package, media_root):
    """返回一份聊天记录独占的稳定媒体子目录。"""
    source = package.get("source") if isinstance(package, dict) else {}
    source = source if isinstance(source, dict) else {}
    archive_key = f"{source.get('self_id') or ''}:{source.get('message_id') or ''}"
    folder = str(package.get("_omm_media_folder") or "").strip()
    if (
        not folder
        or folder in (".", "..")
        or Path(folder).name != folder
        or "/" in folder
        or "\\" in folder
    ):
        folder = hashlib.sha256(archive_key.encode("utf-8")).hexdigest()[:24]
    return Path(media_root) / folder


def rename_chat_record_media_folder(package, new_folder, cfg=None):
    """重命名归档媒体目录并同步改写包内路径。

    返回 ``(old_dir, new_dir, moved)``，供调用方在数据库事务失败时回滚。
    路径判断刻意采用词法规范化，不调用 ``resolve()``，兼容 CloudDrive2。
    """
    from .config import get_config

    if cfg is None:
        cfg = get_config()
    if not isinstance(package, dict):
        raise ValueError("聊天记录结构无效")
    folder = str(new_folder or "").strip()
    if (
        not folder
        or folder in (".", "..")
        or Path(folder).name != folder
        or "/" in folder
        or "\\" in folder
    ):
        raise ValueError("媒体文件夹名无效")
    root = Path(cfg.chat_record_media_dir)
    old_dir = _archive_media_dir(package, root)
    new_dir = root / folder
    old_key = os.path.normcase(os.path.abspath(os.path.normpath(str(old_dir))))
    new_key = os.path.normcase(os.path.abspath(os.path.normpath(str(new_dir))))
    moved = False
    same_spelling = str(old_dir) == str(new_dir)
    if not same_spelling:
        if new_dir.exists() and old_key != new_key:
            raise ValueError("该媒体文件夹名已被占用")
        try:
            if old_key == new_key:
                temporary = root / f".omm-chat-rename-{time.time_ns()}"
                os.replace(str(old_dir), str(temporary))
                try:
                    os.replace(str(temporary), str(new_dir))
                except Exception:
                    os.replace(str(temporary), str(old_dir))
                    raise
            else:
                os.replace(str(old_dir), str(new_dir))
            moved = True
        except FileNotFoundError:
            # 没有成功本地化任何媒体的记录本来就不会创建子目录，仍允许
            # 用户先设置目录名，后续重新归档会写入该目录。
            has_local_media = any(
                str(data.get("local_path") or "").strip()
                for _kind, data in _archive_media_entries(package)
            )
            if has_local_media:
                raise FileNotFoundError("找不到聊天记录媒体文件夹")
        except OSError as exc:
            raise OSError(f"媒体文件夹重命名失败：{exc}") from exc

    old_folder = old_dir.name
    for _kind, data in _archive_media_entries(package):
        value = str(data.get("local_path") or "").strip()
        if not value:
            continue
        candidate = Path(value.replace("\\", "/"))
        parts = candidate.parts
        if (
            not candidate.is_absolute()
            and len(parts) >= 3
            and parts[0].casefold() == "chat-record-media"
            and parts[1].casefold() == old_folder.casefold()
        ):
            data["local_path"] = Path(parts[0], folder, *parts[2:]).as_posix()
            continue
        if candidate.is_absolute():
            parent_key = os.path.normcase(
                os.path.abspath(os.path.normpath(str(candidate.parent)))
            )
            if parent_key == old_key:
                data["local_path"] = str(new_dir / candidate.name)
    package["_omm_media_folder"] = folder
    return old_dir, new_dir, moved


def _stored_archive_media_path(path, storage_root):
    """把媒体路径转换成可随 cache_dir 搬迁的相对引用。"""
    path = Path(path)
    if storage_root is None:
        return str(path)
    try:
        return path.relative_to(Path(storage_root)).as_posix()
    except ValueError:
        return str(path)


def _archive_media_entries(package):
    """按聊天节点顺序返回正文及回复引用中的全部媒体段。"""
    result = []
    for node, _depth, _node_path in _iter_archive_nodes(package):
        result.extend(_iter_segment_media(_node_segments(node)))
    return result


def _archive_image_data(package):
    """兼容旧调用：只返回图片数据。"""
    return [data for kind, data in _archive_media_entries(package) if kind == "image"]


def _image_suffix(path, data):
    """从文件名或图片文件头确定本地副本扩展名。"""
    for value in (path.name, str(data.get("file") or "")):
        suffix = Path(value).suffix.lower()
        if suffix in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
            return suffix
    try:
        with path.open("rb") as stream:
            header = stream.read(16)
    except OSError:
        return ".img"
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return ".webp"
    if header.startswith(b"BM"):
        return ".bmp"
    return ".img"


def _media_suffix(path, data, kind):
    """保留媒体原扩展名；图片缺失扩展名时再检查文件头。"""
    if kind == "image":
        return _image_suffix(path, data)
    for value in (
        path.name,
        str(data.get("name") or ""),
        str(data.get("file_name") or ""),
        str(data.get("file") or ""),
    ):
        suffix = Path(value).suffix.lower()
        if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            return suffix
    return {"record": ".silk", "video": ".mp4", "file": ".bin"}.get(kind, ".bin")


def _download_archive_image(http_url, token, data):
    """把当前仍有效的接收态图片解析成 NapCat 本地文件。"""
    cached = _media_send_source({"path": data.get("path")})
    if cached:
        return cached
    urls = [data.get("url")]
    urls.extend(data.get("omm_fallback_urls") or [])
    urls = list(
        dict.fromkeys(
            str(value).strip()
            for value in urls
            if str(value or "").lower().startswith(("http://", "https://"))
            and len(str(value)) <= 8192
        )
    )[:6]
    for url in urls:
        try:
            downloaded = call_action(
                http_url,
                token,
                "download_file",
                {"url": url, "thread_count": 1},
                timeout=30,
            )
        except OneBotError as exc:
            logger.info("NapCat 归档图片 URL 下载失败: %s", exc)
            continue
        source = _media_send_source(downloaded if isinstance(downloaded, dict) else {})
        if source:
            return source
    return _napcat_image_source(http_url, token, data)


def _download_archive_media(http_url, token, kind, data):
    """把媒体段解析成 NapCat 当前可读的本地文件。"""
    if kind == "image":
        return _download_archive_image(http_url, token, data)
    cached = _media_send_source({"path": data.get("path")})
    cached = cached or _media_send_source({"file": data.get("file")})
    if cached and not cached.lower().startswith(("http://", "https://")):
        return cached
    urls = [data.get("url")]
    urls.extend(data.get("omm_fallback_urls") or [])
    if cached and cached.lower().startswith(("http://", "https://")):
        urls.insert(0, cached)
    urls = list(
        dict.fromkeys(
            str(value).strip()
            for value in urls
            if str(value or "").lower().startswith(("http://", "https://"))
            and len(str(value)) <= 8192
        )
    )[:6]
    for url in urls:
        try:
            downloaded = call_action(
                http_url,
                token,
                "download_file",
                {"url": url, "thread_count": 1},
                timeout=120 if kind in ("video", "file") else 30,
            )
        except OneBotError as exc:
            logger.info("NapCat 归档%s URL 下载失败: %s", kind, exc)
            continue
        source = _media_send_source(downloaded if isinstance(downloaded, dict) else {})
        if source:
            return source
    if kind == "record":
        file_id = str(data.get("file") or "").strip()
        if file_id:
            try:
                resolved = call_action(
                    http_url,
                    token,
                    "get_record",
                    {"file": file_id, "out_format": "silk"},
                    timeout=30,
                )
            except OneBotError as exc:
                logger.info("NapCat 归档语音标识还原失败: %s", exc)
            else:
                source = _media_send_source(
                    resolved if isinstance(resolved, dict) else {}
                )
                if source:
                    return source
    return ""


def _localize_archive_media(http_url, token, package, media_root, storage_root=None):
    """保存归档全部媒体本体，并把稳定路径写回结构包。"""
    archive_dir = _archive_media_dir(package, media_root)
    entries = _archive_media_entries(package)
    reserved_names = {
        Path(str(data.get("local_path") or "")).name
        for _kind, data in entries
        if data.get("local_path")
    }
    localized = 0
    for index, (kind, data) in enumerate(entries, 1):
        current = _archive_media_source(data, storage_root=storage_root)
        source_path = current or _download_archive_media(http_url, token, kind, data)
        if not source_path:
            continue
        path = Path(source_path)
        try:
            size = path.stat().st_size
        except OSError:
            continue
        limit = (
            _MAX_ARCHIVE_IMAGE_BYTES if kind == "image" else _MAX_ARCHIVE_MEDIA_BYTES
        )
        if size <= 0 or size > limit:
            logger.info("归档%s大小超出限制: %s", kind, size)
            continue
        archive_dir.mkdir(parents=True, exist_ok=True)
        if os.path.normcase(os.path.abspath(path.parent)) == os.path.normcase(
            os.path.abspath(archive_dir)
        ):
            data["local_path"] = _stored_archive_media_path(path, storage_root)
            localized += 1
            continue
        destination = archive_dir / (f"{index:05d}{_media_suffix(path, data, kind)}")
        try:
            same_file = path.resolve() == destination.resolve()
        except OSError:
            same_file = path == destination
        if same_file:
            data["local_path"] = _stored_archive_media_path(destination, storage_root)
            localized += 1
            continue
        try:
            occupied = destination.name in reserved_names or destination.exists()
        except OSError:
            occupied = True
        if occupied:
            destination = destination.with_name(
                f"{destination.stem}-{os.urandom(8).hex()}{destination.suffix}"
            )
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            shutil.copyfile(path, temporary)
            os.replace(temporary, destination)
        except OSError as exc:
            logger.info("归档%s本地化失败: %s", kind, exc)
            try:
                temporary.unlink()
            except OSError:
                pass
            continue
        data["local_path"] = _stored_archive_media_path(destination, storage_root)
        reserved_names.add(destination.name)
        localized += 1
    summary = package.get("summary") if isinstance(package, dict) else None
    if isinstance(summary, dict):
        summary["media_localized_count"] = localized
        summary["media_localized"] = localized > 0
    return localized


def _localize_archive_images(http_url, token, package, media_root, storage_root=None):
    """兼容旧调用；新版会持久化图片、语音、视频和文件。"""
    return _localize_archive_media(
        http_url, token, package, media_root, storage_root=storage_root
    )


def _prune_archive_media(package, media_root, storage_root=None):
    """删除一份记录子目录中已不再被结构包引用的旧媒体。"""
    archive_dir = _archive_media_dir(package, media_root)
    if not archive_dir.is_dir():
        return 0

    def lexical(path):
        return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))

    archive_key = lexical(archive_dir)
    keep = set()
    for _kind, data in _archive_media_entries(package):
        value = str(data.get("local_path") or "").strip()
        if not value:
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            parts = Path(value.replace("\\", "/")).parts
            if (
                len(parts) < 3
                or parts[0].casefold() != "chat-record-media"
                or any(part in ("", ".", "..") for part in parts)
                or storage_root is None
            ):
                continue
            candidate = Path(storage_root).joinpath(*parts)
        # 清理阶段只需确认“结构包声明保留的是本记录目录内文件”。
        # 不能调用 is_file()/resolve()：虚拟盘刚写完时可能短暂不可见，
        # 否则会把刚归档成功的媒体误删。
        if lexical(candidate.parent) == archive_key:
            keep.add(lexical(candidate))
    removed = 0
    for path in archive_dir.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith(".tmp"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
            continue
        if lexical(path) in keep:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    try:
        archive_dir.rmdir()
    except OSError:
        pass
    return removed


def _reuse_archived_media_paths(archived_package, refreshed_package):
    """按节点和正文/引用位置复用媒体，新增引用不能使正文媒体错位。"""

    def slots(package):
        result = {}

        def read(segments, node_path, quote_path=(), depth=0):
            counters = {}
            reply_index = 0
            for segment in _message_segments(segments):
                kind = str(segment.get("type") or "")
                data = segment.get("data")
                if kind in _ARCHIVE_MEDIA_KINDS and isinstance(data, dict):
                    index = counters.get(kind, 0)
                    counters[kind] = index + 1
                    result[(node_path, quote_path, kind, index)] = data
                if kind == "reply" and depth < 3:
                    read(
                        _quoted_segments(segment),
                        node_path,
                        quote_path + (reply_index,),
                        depth + 1,
                    )
                    reply_index += 1

        for node, _depth, node_path in _iter_archive_nodes(package):
            read(_node_segments(node), node_path)
        return result

    archived = slots(archived_package)
    refreshed = slots(refreshed_package)
    reused = 0
    for key, new in refreshed.items():
        old = archived.get(key)
        if old is None:
            continue
        local_path = _archive_media_source(old)
        if local_path:
            new["local_path"] = old.get("local_path")
            reused += 1
    return reused


def _reuse_archived_native_forward(archived_package, refreshed_package):
    """刷新读取未取得原生负载时，保留归档里已有的可重放负载。"""
    refreshed_payload, _, _, _ = _stored_native_forward_replay(refreshed_package)
    if refreshed_payload:
        return False
    archived_payload, archived_card, _, _ = _stored_native_forward_replay(
        archived_package
    )
    if not archived_payload:
        return False
    refreshed_package["omm_native_payload_b64"] = archived_payload
    if not isinstance(refreshed_package.get("native_card"), dict) and isinstance(
        archived_card, dict
    ):
        refreshed_package["native_card"] = archived_card
    return True


def _reuse_archived_image_paths(archived_package, refreshed_package):
    """兼容旧调用；新版复用全部媒体路径。"""
    return _reuse_archived_media_paths(archived_package, refreshed_package)


def _napcat_image_source(http_url, token, data):
    """让 NapCat 把接收态图片标识还原为当前可读的本地临时文件。"""
    file_id = str(data.get("file") or "").strip()
    if not file_id or _media_send_source({"file": file_id}):
        return ""
    for action in ("download_file_image_stream", "get_image"):
        try:
            resolved = call_action(
                http_url, token, action, {"file": file_id}, timeout=20
            )
        except OneBotError as exc:
            logger.info("NapCat 图片标识还原失败（%s）: %s", action, exc)
            continue
        if isinstance(resolved, dict):
            source = _media_send_source(resolved)
            if source:
                return source
    return ""


def _prepare_sendable_segments(
    segments, degrade_media=False, media_resolver=None, _quote_depth=0
):
    """规范媒体来源；不可用或强制降级时以文字占位保留消息语义。"""
    result = []
    degraded = 0
    media_labels = {
        "image": "图片",
        "record": "语音",
        "video": "视频",
        "file": "文件",
    }
    for segment in segments:
        kind = str(segment.get("type") or "")
        if kind == "reply":
            quoted = _quoted_segments(segment)
            if quoted and _quote_depth < 3:
                quote_content, quote_degraded = _prepare_sendable_segments(
                    quoted,
                    degrade_media=degrade_media,
                    media_resolver=media_resolver,
                    _quote_depth=_quote_depth + 1,
                )
                if quote_content:
                    result.append({"type": "text", "data": {"text": "[引用内容] "}})
                    result.extend(quote_content)
                    result.append({"type": "text", "data": {"text": "\n"}})
                    degraded += quote_degraded
            continue
        if kind in ("forward", "node") or _json_card_forward(segment):
            continue
        data = segment.get("data")
        if not kind or not isinstance(data, dict):
            continue
        if kind == "image":
            # NapCat 接收商城表情时为兼容 OB11 会暂时表示成 image，并把
            # marketFaceElement 的身份字段附在 data 中。重组时还原成 mface，
            # 否则即使本地 GIF 完整，也只会作为普通图片发送。
            emoji_id = str(data.get("emoji_id") or "").strip()
            emoji_key = str(data.get("key") or "").strip()
            try:
                emoji_package_id = int(data.get("emoji_package_id"))
            except (TypeError, ValueError):
                emoji_package_id = -1
            if (
                0 <= emoji_package_id <= 2**31 - 1
                and 0 < len(emoji_id) <= 512
                and 0 < len(emoji_key) <= 4096
            ):
                summary = str(data.get("summary") or "[商城表情]")
                summary = summary.replace("\x00", "")[:256] or "[商城表情]"
                result.append(
                    {
                        "type": "mface",
                        "data": {
                            "emoji_package_id": emoji_package_id,
                            "emoji_id": emoji_id,
                            "key": emoji_key,
                            "summary": summary,
                        },
                    }
                )
                continue
        if kind not in media_labels:
            result.append({"type": kind, "data": dict(data)})
            continue
        source = ""
        if not degrade_media:
            source = _archive_media_source(data)
            source = source or _media_send_source({"path": data.get("path")})
        if not degrade_media and media_resolver is not None:
            source = source or media_resolver(kind, data)
        if (
            not source
            and not degrade_media
            and not (kind == "image" and media_resolver is not None)
        ):
            source = _media_send_source(data)
        if source:
            send_data = {"file": source}
            if kind == "image":
                try:
                    sub_type = int(data.get("sub_type"))
                except (TypeError, ValueError):
                    sub_type = None
                if sub_type is not None and 0 <= sub_type <= 255:
                    send_data["sub_type"] = sub_type
                if data.get("summary") not in (None, ""):
                    summary = str(data.get("summary")).replace("\x00", "")[:256]
                    if summary:
                        send_data["summary"] = summary
            result.append({"type": kind, "data": send_data})
            continue
        degraded += 1
        label = media_labels[kind]
        name = str(data.get("name") or "").strip() if kind == "file" else ""
        suffix = f"：{name}" if name else ""
        result.append(
            {
                "type": "text",
                "data": {"text": f"[{label}{suffix}，资源已失效]"},
            }
        )
    return result, degraded


def _forward_nodes(package, self_id, degrade_media=False, media_resolver=None):
    """生成保留 NapCat 可发送嵌套层级的 OneBot node 树。"""
    degraded_media_count = 0
    built_node_count = 0
    blocked_paths = set(chat_record_blocked_paths(package))

    def make_node(node, content, forward_data=None):
        nonlocal built_node_count
        if built_node_count >= _MAX_ARCHIVE_NODES:
            return None
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        sender = node.get("sender")
        sender = sender if isinstance(sender, dict) else {}
        nickname = (
            sender.get("card")
            or sender.get("nickname")
            or data.get("nickname")
            or data.get("name")
            or "OhMyMeme"
        )
        user_id = (
            sender.get("user_id") or data.get("user_id") or data.get("uin") or self_id
        )
        node_data = {
            "user_id": user_id,
            "nickname": str(nickname),
            "content": content,
        }
        sender_uid = (
            node.get("omm_sender_uid")
            or sender.get("uid")
            or sender.get("user_uid")
            or data.get("omm_sender_uid")
            or data.get("uid")
        )
        if sender_uid not in (None, ""):
            node_data["omm_sender_uid"] = str(sender_uid)
        message_type = str(
            node.get("message_type") or data.get("message_type") or ""
        ).strip()
        if message_type in ("group", "private"):
            node_data["omm_source_type"] = message_type
        if message_type == "group":
            source_peer_id = node.get("group_id") or data.get("group_id")
        else:
            source_peer_id = node.get("target_id") or data.get("target_id")
        if source_peer_id not in (None, ""):
            node_data["omm_source_peer_id"] = str(source_peer_id)
        node_time = node.get("time") or data.get("time")
        if node_time not in (None, "", 0, "0"):
            node_data["time"] = str(node_time)
        if isinstance(forward_data, dict):
            for key in ("source", "news", "summary", "prompt"):
                value = forward_data.get(key)
                if value not in (None, "", [], {}):
                    node_data[key] = value
        built_node_count += 1
        return {"type": "node", "data": node_data}

    def build(nodes, archive_depth, nested_depth, parent_path=""):
        nonlocal degraded_media_count
        result = []
        if archive_depth > _MAX_ARCHIVE_DEPTH:
            return result
        for node_index, node in enumerate(nodes):
            if not isinstance(node, dict) or built_node_count >= _MAX_ARCHIVE_NODES:
                continue
            node_path = (
                f"{parent_path}/n{node_index}" if parent_path else f"n{node_index}"
            )
            if node_path in blocked_paths:
                continue
            segments = _node_segments(node)
            forward_segments = _forward_segments(segments)
            content, degraded = _prepare_sendable_segments(
                segments,
                degrade_media=degrade_media,
                media_resolver=media_resolver,
            )
            degraded_media_count += degraded
            if (
                not content
                and not forward_segments
                and not str(node.get("raw_message") or "").strip()
            ):
                content = [
                    {
                        "type": "text",
                        "data": {"text": "[空消息或未解析的嵌套聊天记录]"},
                    }
                ]
            if content:
                normal_node = make_node(node, content)
                if normal_node:
                    result.append(normal_node)
            for segment_index, segment in _indexed_forward_segments(segments):
                forward_data = (
                    segment.get("data") if isinstance(segment.get("data"), dict) else {}
                )
                nested_nodes = _nested_nodes(package, segment)
                child_path = f"{node_path}/s{segment_index}"
                if nested_depth < _MAX_NAPCAT_NESTED_FORWARD_DEPTH:
                    children = build(
                        nested_nodes,
                        archive_depth + 1,
                        nested_depth + 1,
                        child_path,
                    )
                    if children:
                        wrapper = make_node(node, children, forward_data)
                        if wrapper:
                            result.append(wrapper)
                    continue
                children = build(
                    nested_nodes, archive_depth + 1, nested_depth, child_path
                )
                if not children:
                    continue
                marker = make_node(
                    node,
                    [
                        {
                            "type": "text",
                            "data": {
                                "text": (
                                    "[更深层嵌套记录受 NapCat 层级限制，"
                                    "已在本层展开]"
                                )
                            },
                        }
                    ],
                )
                if marker:
                    result.append(marker)
                result.extend(children)
        return result

    result = build(_archive_root_nodes(package), 0, 0)
    if not result:
        raise OneBotError("归档中没有可重新发送的消息内容")
    return result, degraded_media_count


def _is_media_download_error(exc):
    """判断 NapCat 是否在构建伪造转发节点时下载媒体失败。"""
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "下载文件失败",
            "download file",
            "handleob11filelikemessage",
        )
    )


def _send_destination(event):
    """从命令事件中解析群聊或私聊发送目标。"""
    event = event if isinstance(event, dict) else {}
    if str(event.get("message_type") or "") == "group" and event.get(
        "group_id"
    ) not in (None, ""):
        return "group", event.get("group_id")
    self_id = str(event.get("self_id") or "")
    peer_id = event.get("target_id")
    if peer_id in (None, "") and str(event.get("user_id") or "") != self_id:
        peer_id = event.get("user_id")
    if peer_id in (None, ""):
        raise OneBotError("无法确定要发送到哪个 QQ 会话")
    return "private", peer_id


def _forward_original_message(http_url, token, source_id, event):
    """直接转发仍存在的外层消息，保留 QQ 原生合并转发资源。"""
    destination_type, destination_id = _send_destination(event)
    if destination_type == "group":
        action = "forward_group_single_msg"
        params = {"group_id": destination_id, "message_id": source_id}
    else:
        action = "forward_friend_single_msg"
        params = {"user_id": destination_id, "message_id": source_id}
    return call_action(http_url, token, action, params, 30)


def _persistent_source_identity(source):
    """提取可跨 NapCat 进程重启保存的原消息位置。"""
    source = source if isinstance(source, dict) else {}
    message_type = str(source.get("message_type") or "")
    self_id = str(source.get("self_id") or "")
    if message_type == "group":
        peer_id = source.get("group_id")
    elif message_type == "private":
        peer_id = source.get("target_id")
        if peer_id in (None, "") and str(source.get("user_id") or "") != self_id:
            peer_id = source.get("user_id")
        if peer_id in (None, ""):
            sender = source.get("sender")
            sender = sender if isinstance(sender, dict) else {}
            if str(sender.get("user_id") or "") != self_id:
                peer_id = sender.get("user_id")
    else:
        return None

    # message_seq 是 NapCat 当前进程生成的 OneBot 短 ID；只有 real_seq
    # 才是可在下一次 NapCat 会话中重新定位消息的 QQ 序号。
    real_seq = source.get("real_seq")
    if peer_id in (None, "") or real_seq in (None, ""):
        return None
    sender = source.get("sender")
    sender = sender if isinstance(sender, dict) else {}
    return {
        "source_type": message_type,
        "source_peer_id": peer_id,
        "source_self_id": self_id,
        "source_real_seq": real_seq,
        "source_time": source.get("time"),
        "source_sender_id": sender.get("user_id") or source.get("user_id"),
    }


def chat_record_sources(*packages):
    """合并等价的原生消息位置，只保留定位字段并按最近可用时间排序。"""
    locations = {}
    fields = (
        "self_id",
        "message_id",
        "message_type",
        "group_id",
        "target_id",
        "user_id",
        "real_seq",
        "time",
        "last_success_at",
    )
    for package in packages:
        if not isinstance(package, dict):
            continue
        sources = [package.get("source")]
        stored = package.get("source_locations")
        if isinstance(stored, list):
            sources.extend(stored)
        for source in sources:
            if not isinstance(source, dict):
                continue
            identity = _persistent_source_identity(source)
            if identity:
                key = tuple(
                    str(identity[field])
                    for field in (
                        "source_self_id",
                        "source_type",
                        "source_peer_id",
                        "source_real_seq",
                    )
                )
            elif source.get("message_id") not in (None, ""):
                key = (str(source.get("self_id") or ""), str(source.get("message_id")))
            else:
                continue
            location = {
                field: source[field]
                for field in fields
                if isinstance(source.get(field), (str, int))
            }
            sender = source.get("sender")
            if isinstance(sender, dict) and sender.get("user_id") not in (None, ""):
                if isinstance(sender["user_id"], (str, int)):
                    location["sender"] = {"user_id": sender["user_id"]}
            previous = locations.get(key, {})
            try:
                successful_at = int(location.get("last_success_at") or 0)
            except (TypeError, ValueError):
                successful_at = 0
            location["last_success_at"] = max(
                int(previous.get("last_success_at") or 0),
                successful_at,
            )
            locations.pop(key, None)
            locations[key] = {**previous, **location}
    return sorted(
        reversed(list(locations.values())),
        key=lambda item: int(item.get("last_success_at") or 0),
        reverse=True,
    )


def _remember_chat_record_send(db, record_id, sent, source=None):
    """发送已完成后尽力保存等价副本位置，记录失败不得触发重发。"""
    location = sent.get("source_location") if isinstance(sent, dict) else None
    recorded = False
    try:
        if isinstance(location, dict) and _persistent_source_identity(location):
            recorded = bool(
                db.add_chat_record_source(record_id, location, used_source=source)
            )
        elif source and _persistent_source_identity(source):
            db.add_chat_record_source(record_id, source)
            logger.warning("聊天记录已发送，但未取得副本的真实消息位置 #%s", record_id)
    except Exception as exc:
        logger.warning("聊天记录已发送，但副本位置保存失败 #%s: %s", record_id, exc)
    return recorded


def _forward_original_message_by_identity(webui_url, webui_token, source, event):
    """由桥接插件按真实序号重定位消息并执行 QQ 原生转发。"""
    identity = _persistent_source_identity(source)
    if not identity:
        raise OneBotError(
            "归档缺少来源会话或真实消息序号，无法在 NapCat 重启后重新索引"
        )
    destination_type, destination_id = _send_destination(event)
    try:
        payload = call_webui_plugin_api(
            webui_url,
            webui_token,
            "napcat-plugin-ohmymeme-current-peer",
            "forward-message",
            timeout=35,
            params={
                **identity,
                "destination_type": destination_type,
                "destination_peer_id": destination_id,
            },
        )
    except OneBotError as exc:
        exc.send_started = True
        raise
    if not payload.get("ok"):
        error = OneBotError(str(payload.get("error") or "无法通过持久索引转发原消息"))
        error.send_started = payload.get("retry_safe") is False
        raise error
    return payload


def _send_qqnt_native_rebuilt_forward(
    webui_url,
    webui_token,
    destination_type,
    destination_id,
    nodes,
    native_payload_b64="",
    native_card=None,
    native_source_type="",
    native_source_peer_id="",
    blocked_node_paths=None,
    self_native_relay=False,
):
    """通过临时节点包调用桥接插件，让 QQNT 内核原生生成合并转发。"""
    payload = {
        "schema_version": 1,
        "destination_type": destination_type,
        "destination_peer_id": str(destination_id),
        "messages": nodes,
    }
    if native_payload_b64:
        payload["native_payload_b64"] = native_payload_b64
    if isinstance(native_card, dict):
        payload["native_card"] = native_card
    if native_payload_b64:
        payload["native_source_type"] = str(native_source_type or "")
        payload["native_source_peer_id"] = str(native_source_peer_id or "")
        if blocked_node_paths:
            payload["blocked_node_paths"] = normalize_chat_record_blocked_paths(
                blocked_node_paths
            )
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > _MAX_ARCHIVE_BYTES:
        raise OneBotError("聊天记录重组数据超过 64 MiB，未执行发送")
    descriptor, payload_path = tempfile.mkstemp(
        prefix="ohmymeme-native-forward-", suffix=".json"
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
        response = call_webui_plugin_api(
            webui_url,
            webui_token,
            "napcat-plugin-ohmymeme-current-peer",
            (
                "send-self-native-forward"
                if self_native_relay
                else "send-native-forward"
            ),
            timeout=180,
            params={"payload_path": payload_path},
        )
    finally:
        try:
            Path(payload_path).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.info("原生重组临时文件清理失败: %s", exc)
    if not response.get("ok"):
        raise OneBotError(str(response.get("error") or "QQNT 原生重组发送失败"))
    if self_native_relay and response.get("strategy") != "qqnt_self_native_relay":
        raise OneBotError("NapCat 桥接插件版本过旧，不支持自聊原生转发")
    return response


def _stored_native_forward_replay(package):
    """读取归档中保存的 QQNT 原始长消息负载，不判断筛选规则。"""
    if not isinstance(package, dict):
        return "", None, "", ""
    source = package.get("source")
    source = source if isinstance(source, dict) else {}
    card = package.get("native_card")
    card = card if isinstance(card, dict) else None
    identity = _persistent_source_identity(source) or {}

    # parseMultMsg 可能把根记录直接内联进 source.message，根资源便不会进入
    # forward_records。新版将外层原生负载保存在包顶层；仍兼容旧归档里
    # 跟随根 forward record 保存的格式。
    value = package.get("omm_native_payload_b64")
    if isinstance(value, str) and value:
        return (
            value,
            card,
            str(identity.get("source_type") or ""),
            str(identity.get("source_peer_id") or ""),
        )

    records = package.get("forward_records")
    records = records if isinstance(records, dict) else {}
    root_ids = []
    for segment in _forward_segments(_message_segments(source.get("message"))):
        data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
        if data.get("id") not in (None, ""):
            root_ids.append(str(data["id"]))
    candidates = root_ids + [key for key in records if key not in root_ids]
    for record_id in candidates:
        record = records.get(record_id)
        if not isinstance(record, dict):
            continue
        value = record.get("omm_native_payload_b64")
        if isinstance(value, str) and value:
            return (
                value,
                card,
                str(identity.get("source_type") or ""),
                str(identity.get("source_peer_id") or ""),
            )
    return "", None, "", ""


def _native_forward_replay(package):
    """读取可按当前筛选规则安全重放的 QQNT 原始长消息负载。"""
    # 桥接插件可以在不解码各消息头的前提下，直接移除主 MultiMsg action
    # 中的最外层 msgBody，因此最外层筛选仍可保留其余节点的原生发送者。
    # 嵌套节点涉及附属 action/外部资源，暂时仍须使用筛选后的 nodes 重组。
    if any("/" in path for path in chat_record_blocked_paths(package)):
        return "", None, "", ""
    return _stored_native_forward_replay(package)


def send_chat_record(
    http_url,
    token,
    record_id,
    event,
    db=None,
    send_mode="original_native",
    webui_url="",
    webui_token="",
):
    """按指定方式把聊天记录发送到命令所在会话。"""
    if db is None:
        from .database import get_db

        db = get_db()
    package = db.get_chat_record(int(record_id))
    if not package:
        raise OneBotError(f"找不到聊天记录 #{int(record_id)}")
    send_mode = normalize_send_mode(send_mode)
    source = package.get("source") if isinstance(package.get("source"), dict) else {}
    sources = chat_record_sources(package) or [source]
    if send_mode == "original_native":
        reindexed = False
        current_self_id = str(event.get("self_id") or "").strip()
        errors = []
        for location in sources:
            try:
                if webui_url and webui_token and _persistent_source_identity(location):
                    sent = _forward_original_message_by_identity(
                        webui_url, webui_token, location, event
                    )
                    reindexed = True
                else:
                    account = str(location.get("self_id") or "")
                    if account and current_self_id and account != current_self_id:
                        raise OneBotError("该位置属于其他账号，不能复用短消息 ID")
                    source_id = location.get("message_id")
                    if source_id in (None, ""):
                        raise OneBotError("该位置没有可用消息 ID，请配置 NapCat WebUI")
                    sent = _forward_original_message(http_url, token, source_id, event)
                break
            except OneBotError as exc:
                if getattr(exc, "send_started", False):
                    raise
                errors.append(str(exc))
        else:
            raise OneBotError("所有原生消息位置均不可用：" + "；".join(errors))
        backup_recorded = _remember_chat_record_send(db, record_id, sent, location)
        return {
            "ok": True,
            "id": int(record_id),
            "title": str(package.get("title") or "聊天记录"),
            "node_count": int((package.get("summary") or {}).get("node_count") or 0),
            "source": "original_native",
            "reindexed": reindexed,
            "backup_recorded": backup_recorded,
            "degraded_media_count": 0,
            "message_id": sent.get("message_id") if isinstance(sent, dict) else None,
        }
    if send_mode == "original_rebuild":
        errors = []
        for location in sources:
            try:
                recovered_source = None
                if webui_url and webui_token and _persistent_source_identity(location):
                    recovered_source = _recover_original_message_by_identity(
                        webui_url, webui_token, location
                    )
                elif str(location.get("self_id") or "") not in (
                    "",
                    str(event.get("self_id") or ""),
                ):
                    raise OneBotError("该位置属于其他账号，不能复用短消息 ID")
                refreshed_package = build_chat_record_package(
                    http_url,
                    token,
                    (recovered_source or {}).get("message_id")
                    or location.get("message_id"),
                    trigger_event=location,
                    webui_url=webui_url,
                    webui_token=webui_token,
                    source_message=recovered_source,
                )
                break
            except OneBotError as exc:
                errors.append(str(exc))
        else:
            raise OneBotError("所有消息位置均无法重新读取：" + "；".join(errors))
        blocked_paths = chat_record_blocked_paths(package)
        _reuse_archived_media_paths(package, refreshed_package)
        _reuse_archived_native_forward(package, refreshed_package)
        set_chat_record_blocked_paths(refreshed_package, blocked_paths)
        refreshed_package["source_locations"] = chat_record_sources(
            package, refreshed_package
        )
        package = refreshed_package
        # 模式二取得的原始 QQNT 长消息负载也写回同一归档，使后续模式三
        # 可以完全离线地重放原生结构，而不再伪造发送者身份。
        db.save_chat_record(package, record_id=int(record_id))

    event = event if isinstance(event, dict) else {}
    self_id = event.get("self_id") or source.get("self_id")

    def media_resolver(kind, data):
        return _download_archive_media(http_url, token, kind, data)

    nodes, degraded_media_count = _forward_nodes(
        package, self_id, media_resolver=media_resolver
    )
    destination_type, destination_id = _send_destination(event)
    if send_mode == "self_native_relay":
        if not webui_url or not webui_token:
            raise OneBotError("自聊重建后原生转发需要配置 NapCat WebUI 地址和 Token")
        sent = _send_qqnt_native_rebuilt_forward(
            webui_url,
            webui_token,
            destination_type,
            destination_id,
            nodes,
            self_native_relay=True,
        )
        native_rebuild = True
    elif webui_url and webui_token:
        (
            native_payload_b64,
            native_card,
            native_source_type,
            native_source_peer_id,
        ) = _native_forward_replay(package)
        sent = _send_qqnt_native_rebuilt_forward(
            webui_url,
            webui_token,
            destination_type,
            destination_id,
            nodes,
            native_payload_b64=native_payload_b64,
            native_card=native_card,
            native_source_type=native_source_type,
            native_source_peer_id=native_source_peer_id,
            blocked_node_paths=(
                chat_record_blocked_paths(package) if native_payload_b64 else None
            ),
        )
        native_rebuild = True
    else:
        native_rebuild = False
        if destination_type == "group":
            action = "send_group_forward_msg"
            params = {"group_id": destination_id, "messages": nodes}
        else:
            self_id = str(event.get("self_id") or "")
            action = "send_private_forward_msg"
            params = {"user_id": destination_id, "messages": nodes}
        try:
            sent = call_action(http_url, token, action, params, 30)
        except OneBotError as exc:
            if not _is_media_download_error(exc):
                raise
            logger.info("聊天记录媒体读取失败，改用文字占位重试 #%s", record_id)
            nodes, degraded_media_count = _forward_nodes(
                package, self_id, degrade_media=True
            )
            params["messages"] = nodes
            sent = call_action(http_url, token, action, params, 30)
    backup_recorded = False
    if isinstance(sent, dict) and sent.get("strategy") == "qqnt_native_replay":
        backup_recorded = _remember_chat_record_send(db, record_id, sent)
    return {
        "ok": True,
        "id": int(record_id),
        "title": str(package.get("title") or "聊天记录"),
        "node_count": len(nodes),
        "source": send_mode,
        "backup_recorded": backup_recorded,
        "native_rebuild": native_rebuild,
        "native_replay": (
            bool(
                native_rebuild
                and sent.get("strategy")
                in (
                    "qqnt_native_replay",
                    "qqnt_native_filtered_replay",
                )
            )
            if isinstance(sent, dict)
            else False
        ),
        "native_filtered_replay": (
            bool(
                native_rebuild and sent.get("strategy") == "qqnt_native_filtered_replay"
            )
            if isinstance(sent, dict)
            else False
        ),
        "self_native_relay": (
            bool(native_rebuild and sent.get("strategy") == "qqnt_self_native_relay")
            if isinstance(sent, dict)
            else False
        ),
        "degraded_media_count": degraded_media_count,
        "message_id": sent.get("message_id") if isinstance(sent, dict) else None,
    }


def send_meme_image(http_url, token, path, message_type, peer_id, as_sticker=False):
    """通过 NapCat 向指定 QQ 会话发送本地图片。"""
    source = os.path.abspath(os.fspath(path))
    if not os.path.isfile(source):
        raise OneBotError("表情文件不存在")
    image = {"file": source}
    if as_sticker:
        image.update({"sub_type": 1, "summary": "[表情]"})
    message = [{"type": "image", "data": image}]
    if message_type == "group":
        action = "send_group_msg"
        params = {"group_id": peer_id, "message": message}
    elif message_type == "private":
        action = "send_private_msg"
        params = {"user_id": peer_id, "message": message}
    else:
        raise OneBotError("QQ 会话类型无效")
    sent = call_action(http_url, token, action, params, 30)
    return {
        "ok": True,
        "message_id": sent.get("message_id") if isinstance(sent, dict) else None,
    }


def _send_command_feedback(http_url, token, event, text):
    """把命令处理结果发回原会话；无法确定私聊对象时静默跳过。"""
    message_type = str(event.get("message_type") or "")
    message = [{"type": "text", "data": {"text": str(text)}}]
    if message_type == "group" and event.get("group_id") not in (None, ""):
        call_action(
            http_url,
            token,
            "send_group_msg",
            {"group_id": event.get("group_id"), "message": message},
            10,
        )
        return
    self_id = str(event.get("self_id") or "")
    peer_id = event.get("target_id")
    if peer_id in (None, "") and str(event.get("user_id") or "") != self_id:
        peer_id = event.get("user_id")
    if peer_id not in (None, ""):
        call_action(
            http_url,
            token,
            "send_private_msg",
            {"user_id": peer_id, "message": message},
            10,
        )


def _try_send_command_feedback(http_url, token, event, text):
    """尽力发送命令反馈，反馈失败不能改变已经完成的归档结果。"""
    try:
        _send_command_feedback(http_url, token, event, text)
        return ""
    except Exception as exc:
        logger.warning("NapCat 命令反馈发送失败: %s", exc)
        return str(exc)


def handle_command_event(
    http_url,
    token,
    command,
    event,
    db=None,
    send_mode="original_native",
    webui_url="",
    webui_token="",
):
    """处理一个自身消息事件，并在原会话回复命令结果。"""
    parsed = parse_command_event(event, command)
    if not parsed.get("matched"):
        return {"matched": False}
    if parsed.get("error"):
        feedback_error = _try_send_command_feedback(
            http_url, token, event, parsed["error"]
        )
        return {
            "matched": True,
            "ok": False,
            "error": parsed["error"],
            "feedback_error": feedback_error,
        }
    if parsed.get("action") in ("list", "send") and db is None:
        from .database import get_db

        db = get_db()
    if parsed.get("action") == "list":
        records = db.list_chat_records(limit=10)
        if records:
            lines = ["OhMyMeme：最近保存的聊天记录："]
            for item in records:
                lines.append(
                    f"#{item['id']} {item.get('name') or item['title']}"
                    f"（{item['node_count']} 条消息）"
                )
            lines.append(f"发送：{command} send <ID>")
        else:
            lines = [f"OhMyMeme：还没有保存聊天记录。回复记录后发送 {command}。"]
        feedback_error = _try_send_command_feedback(
            http_url, token, event, "\n".join(lines)
        )
        return {
            "matched": True,
            "ok": True,
            "action": "list",
            "count": len(records),
            "feedback_error": feedback_error,
        }
    if parsed.get("action") == "send":
        try:
            result = send_chat_record(
                http_url,
                token,
                parsed["record_id"],
                event,
                db=db,
                send_mode=send_mode,
                webui_url=webui_url,
                webui_token=webui_token,
            )
        except Exception as exc:
            error = (
                str(exc) if isinstance(exc, (OneBotError, ValueError)) else "内部错误"
            )
            feedback_error = _try_send_command_feedback(
                http_url, token, event, f"OhMyMeme：发送失败：{error}"
            )
            if not isinstance(exc, (OneBotError, ValueError)):
                logger.exception("NapCat 聊天记录发送失败")
            return {
                "matched": True,
                "ok": False,
                "action": "send",
                "error": error,
                "feedback_error": feedback_error,
            }
        source_labels = {
            "original_native": "原消息原生转发",
            "original_rebuild": "原消息重组",
            "archive_rebuild": "本地归档重组",
            "self_native_relay": "自聊重建后原生转发",
        }
        source_label = source_labels.get(result["source"], "本地归档重组")
        if result.get("self_native_relay"):
            native_note = "（发送者统一为当前账号）"
        elif result.get("native_filtered_replay"):
            native_note = "（筛选后原生负载重放）"
        elif result.get("native_replay"):
            native_note = "（QQNT 原生负载重放）"
        elif result.get("native_rebuild"):
            native_note = "（QQNT 原生重组）"
        else:
            native_note = ""
        degraded = int(result.get("degraded_media_count") or 0)
        degraded_note = f"（{degraded} 个失效媒体已转为文字占位）" if degraded else ""
        feedback_error = _try_send_command_feedback(
            http_url,
            token,
            event,
            f"OhMyMeme：已通过{source_label}发送 #{result['id']}"
            f"{native_note}{degraded_note}。",
        )
        return {
            "matched": True,
            "action": "send",
            **result,
            "feedback_error": feedback_error,
        }
    try:
        result = archive_chat_record(
            http_url,
            token,
            parsed["target_message_id"],
            trigger_event=event,
            db=db,
            webui_url=webui_url,
            webui_token=webui_token,
        )
    except Exception as exc:
        error = str(exc) if isinstance(exc, (OneBotError, ValueError)) else "内部错误"
        feedback_error = _try_send_command_feedback(
            http_url, token, event, f"OhMyMeme：保存失败：{error}"
        )
        if not isinstance(exc, (OneBotError, ValueError)):
            logger.exception("NapCat 聊天记录保存失败")
        return {
            "matched": True,
            "ok": False,
            "error": error,
            "feedback_error": feedback_error,
        }
    action = "已保存" if result["created"] else "已更新"
    feedback = (
        f"OhMyMeme：{action}「{result.get('name') or result['title']}」，"
        f"共 {result['node_count']} 条消息。"
    )
    if result["media_count"]:
        media_count = int(result["media_count"])
        localized = int(result.get("media_localized_count") or 0)
        feedback += f" 已记录 {media_count} 个媒体段"
        if localized:
            feedback += f"，其中 {localized} 个媒体已保存到本地"
        if localized < media_count:
            feedback += (
                f"；{media_count - localized} 个媒体未能下载正文，"
                "仅保留可能过期的临时引用"
            )
        feedback += "。"
    feedback_error = _try_send_command_feedback(http_url, token, event, feedback)
    return {
        "matched": True,
        "action": "save",
        **result,
        "feedback_error": feedback_error,
    }


class OneBotCommandListener:
    """监听 NapCat WebSocket 中由自己发送的归档命令。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._worker = None
        self._socket = None
        self._jobs = queue.Queue(maxsize=16)
        self._http_url = ""
        self._ws_url = ""
        self._token = ""
        self._command = "/omm"
        self._send_mode = "original_native"
        self._webui_url = ""
        self._webui_token = ""
        self._connected = False
        self._last_error = ""
        self._last_saved = None
        self._recent_event_ids = []

    def start(
        self,
        http_url,
        ws_url,
        token="",
        command="/omm",
        send_mode="original_native",
        webui_url="",
        webui_token="",
    ):
        """启动监听；重复调用时先停止旧连接。"""
        if not self.stop():
            raise OneBotError("上一条聊天记录仍在保存，请完成后再重载监听")
        try:
            import websocket
        except ImportError as exc:
            raise OneBotError("缺少 websocket-client，无法启动命令监听") from exc
        self._http_url = normalize_http_url(http_url)
        self._ws_url = normalize_ws_url(ws_url)
        self._token = _normalize_token(token)
        self._command = normalize_command(command)
        self._send_mode = normalize_send_mode(send_mode)
        self._webui_url = normalize_http_url(webui_url) if webui_url else ""
        self._webui_token = _normalize_token(webui_token)
        self._stop.clear()
        self._jobs = queue.Queue(maxsize=16)
        with self._lock:
            self._connected = False
            self._last_error = ""
        self._thread = threading.Thread(
            target=self._run, args=(websocket,), daemon=True, name="omm-onebot-events"
        )
        self._worker = threading.Thread(
            target=self._work, daemon=True, name="omm-onebot-archive"
        )
        self._thread.start()
        self._worker.start()
        return self.status()

    def stop(self):
        """停止监听并关闭当前 WebSocket。"""
        self._stop.set()
        current = self._socket
        if current is not None:
            try:
                current.close()
            except Exception:
                pass
        threads = (self._thread, self._worker)
        for thread in threads:
            if (
                thread
                and thread.is_alive()
                and thread is not threading.current_thread()
            ):
                thread.join(timeout=2)
        alive = [thread for thread in threads if thread and thread.is_alive()]
        self._thread = self._thread if self._thread in alive else None
        self._worker = self._worker if self._worker in alive else None
        self._socket = None
        with self._lock:
            self._connected = False
        return not alive

    def _run(self, websocket):
        """维护 WebSocket 连接并把匹配命令交给串行工作线程。"""
        while not self._stop.is_set():
            headers = []
            if self._token:
                headers.append(f"Authorization: Bearer {self._token}")
            try:
                self._socket = websocket.create_connection(
                    self._ws_url,
                    header=headers,
                    timeout=5,
                    enable_multithread=True,
                    redirect_limit=0,
                    http_no_proxy=["127.0.0.1", "localhost", "::1"],
                )
                self._socket.settimeout(1)
                with self._lock:
                    self._connected = True
                    self._last_error = ""
                while not self._stop.is_set():
                    try:
                        raw = self._socket.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not raw:
                        break
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    parsed = parse_command_event(event, self._command)
                    if not parsed.get("matched") or self._is_duplicate(event):
                        continue
                    try:
                        self._jobs.put_nowait(event)
                    except queue.Full:
                        with self._lock:
                            self._last_error = "待处理命令过多，请稍后重试"
            except Exception as exc:
                if not self._stop.is_set():
                    with self._lock:
                        self._last_error = f"WebSocket 连接失败：{exc}"
            finally:
                current = self._socket
                self._socket = None
                if current is not None:
                    try:
                        current.close()
                    except Exception:
                        pass
                with self._lock:
                    self._connected = False
            self._stop.wait(3)

    def _is_duplicate(self, event):
        """过滤同一命令事件的重复上报。"""
        event_id = str(event.get("message_id") or "")
        if not event_id:
            return False
        if event_id in self._recent_event_ids:
            return True
        self._recent_event_ids.append(event_id)
        del self._recent_event_ids[:-100]
        return False

    def _work(self):
        """串行处理命令，避免重复归档同时压垮 NapCat。"""
        while not self._stop.is_set():
            try:
                event = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            result = handle_command_event(
                self._http_url,
                self._token,
                self._command,
                event,
                send_mode=self._send_mode,
                webui_url=self._webui_url,
                webui_token=self._webui_token,
            )
            with self._lock:
                if result.get("ok") and result.get("action") == "save":
                    self._last_saved = {
                        "id": result.get("id"),
                        "title": result.get("name") or result.get("title"),
                        "node_count": result.get("node_count", 0),
                        "time": int(time.time()),
                    }
                    self._last_error = ""
                elif result.get("ok"):
                    self._last_error = ""
                elif result.get("error"):
                    self._last_error = result["error"]

    def status(self):
        """返回不含 Token 的监听状态。"""
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "connected": self._connected,
                "command": self._command,
                "send_mode": self._send_mode,
                "last_error": self._last_error,
                "last_saved": self._last_saved,
            }


_COMMAND_LISTENER = OneBotCommandListener()


def start_command_listener(
    http_url,
    ws_url,
    token="",
    command="/omm",
    send_mode="original_native",
    webui_url="",
    webui_token="",
):
    """启动全局 NapCat 命令监听器。"""
    return _COMMAND_LISTENER.start(
        http_url,
        ws_url,
        token,
        command,
        send_mode,
        webui_url,
        webui_token,
    )


def stop_command_listener():
    """停止全局 NapCat 命令监听器。"""
    _COMMAND_LISTENER.stop()


def get_command_listener_status():
    """返回监听状态和本地归档数量。"""
    status = _COMMAND_LISTENER.status()
    try:
        from .database import get_db

        db = get_db()
        status["saved_count"] = db.count_chat_records()
        status["latest"] = db.get_latest_chat_record()
    except Exception:
        status["saved_count"] = 0
        status["latest"] = None
    return status


def apply_command_listener_config(config=None):
    """根据应用配置启动或停止命令监听。"""
    if config is None:
        from .config import get_config

        config = get_config()
    if not config.get("napcat_enabled", False):
        stop_command_listener()
        return get_command_listener_status()
    return start_command_listener(
        config.get("napcat_http_url", "http://127.0.0.1:3000"),
        config.get("napcat_ws_url", "ws://127.0.0.1:3001"),
        config.get("napcat_token", ""),
        config.get("napcat_command", "/omm"),
        config.get("napcat_send_mode", "original_native"),
        config.get("napcat_webui_url", "http://127.0.0.1:6099"),
        config.get("napcat_webui_token", ""),
    )
