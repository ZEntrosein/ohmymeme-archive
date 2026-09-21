"""从 Notion 数据库只读导入表情包。"""

import hashlib
import http.client
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import _IMPORT_MAX_BYTES, CHAT_RECORD_MEDIA_DIRNAME, get_config
from .database import get_db
from .manifest import build as build_manifest

logger = logging.getLogger(__name__)

_NOTION_API = "https://api.notion.com/v1"
_NOTION_STATE = {
    "status": "idle",
    "phase": "",
    "message": "",
    "progress": 0,
    "total": 0,
    "done": 0,
    "imported": 0,
    "duplicates": 0,
    "unchanged": 0,
    "rejected": 0,
    "failed": 0,
    "changed": False,
    "error": "",
}
_NOTION_LOCK = threading.Lock()
_NOTION_CANCEL = False


class NotionImportError(RuntimeError):
    """Notion 导入错误。"""


class NotionNetworkError(NotionImportError):
    """可重试的临时网络错误。"""


def normalize_notion_id(raw):
    """从 Notion URL 或文本中提取 32 位 ID。"""
    value = urllib.parse.unquote(str(raw or "").strip())
    if not value:
        return ""
    path = urllib.parse.urlparse(value).path if "://" in value else value
    matches = re.findall(r"[0-9a-fA-F]{32}", path.replace("-", ""))
    if not matches:
        matches = re.findall(r"[0-9a-fA-F]{32}", value.replace("-", ""))
    return matches[-1].lower() if matches else ""


def _update_state(**values):
    """原子更新导入进度。"""
    with _NOTION_LOCK:
        _NOTION_STATE.update(**values)


def get_notion_progress():
    """返回 Notion 导入进度快照。"""
    with _NOTION_LOCK:
        return dict(_NOTION_STATE)


def cancel_notion_import():
    """请求取消当前 Notion 导入。"""
    global _NOTION_CANCEL
    _NOTION_CANCEL = True


def _cancelled():
    """返回当前任务是否已请求取消。"""
    return _NOTION_CANCEL


class NotionClient:
    """最小 Notion API 客户端。"""

    def __init__(self, token, version="2026-03-11", opener=None):
        self.token = str(token or "").strip()
        self.version = str(version or "2026-03-11").strip()
        self.opener = opener or urllib.request.urlopen

    def request(self, method, path, payload=None):
        """发送带重试的 Notion JSON 请求。"""
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            _NOTION_API + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": self.version,
                "Content-Type": "application/json",
            },
        )
        last_error = None
        for attempt in range(4):
            try:
                with self.opener(request, timeout=30) as response:
                    body = response.read()
                return json.loads(body.decode("utf-8")) if body else {}
            except urllib.error.HTTPError as exc:
                try:
                    detail = json.loads(exc.read().decode("utf-8")).get("message", "")
                except Exception:
                    detail = ""
                last_error = NotionImportError(
                    f"Notion 请求失败（HTTP {exc.code}）"
                    + (f"：{detail}" if detail else "")
                )
                if exc.code != 429 and exc.code < 500:
                    raise last_error from exc
                retry_after = exc.headers.get("Retry-After", "")
                try:
                    delay = min(float(retry_after), 5.0)
                except (TypeError, ValueError):
                    delay = min(0.5 * (2**attempt), 5.0)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = NotionImportError(f"Notion 网络请求失败：{exc}")
                delay = min(0.5 * (2**attempt), 5.0)
            if attempt < 3:
                time.sleep(delay)
        raise last_error or NotionImportError("Notion 请求失败")

    def resolve_data_source(self, database_id, data_source_id=""):
        """解析数据库唯一的数据源 ID。"""
        configured = normalize_notion_id(data_source_id)
        if configured:
            return configured
        database = normalize_notion_id(database_id)
        if not database:
            raise NotionImportError("请填写有效的 Notion 数据库 URL 或 ID")
        result = self.request("GET", f"/databases/{database}")
        sources = [
            normalize_notion_id(item.get("id"))
            for item in result.get("data_sources", [])
            if isinstance(item, dict) and item.get("id")
        ]
        sources = [item for item in sources if item]
        if len(sources) != 1:
            raise NotionImportError(
                "无法唯一确定 data source，请在设置中填写 Data source ID"
            )
        return sources[0]

    def schema(self, data_source_id):
        """读取数据源结构。"""
        return self.request("GET", f"/data_sources/{data_source_id}")

    def retrieve_page(self, page_id):
        """读取单个页面。"""
        page_id = normalize_notion_id(page_id)
        if not page_id:
            raise NotionImportError("Relation 中包含无效页面 ID")
        return self.request("GET", f"/pages/{page_id}")

    def retrieve_relation_ids(self, page_id, property_id):
        """分页读取被页面对象截断的 Relation 属性。"""
        page_id = normalize_notion_id(page_id)
        if not page_id or not property_id:
            return []
        property_id = urllib.parse.quote(
            urllib.parse.unquote(str(property_id)), safe=""
        )
        result_ids = []
        cursor = None
        while True:
            query = "?page_size=100"
            if cursor:
                query += "&start_cursor=" + urllib.parse.quote(str(cursor), safe="")
            result = self.request(
                "GET", f"/pages/{page_id}/properties/{property_id}{query}"
            )
            for item in result.get("results") or []:
                relation = item.get("relation") if isinstance(item, dict) else None
                relation_id = normalize_notion_id((relation or {}).get("id"))
                if relation_id:
                    result_ids.append(relation_id)
            if not result.get("has_more"):
                return list(dict.fromkeys(result_ids))
            cursor = result.get("next_cursor")
            if not cursor:
                raise NotionImportError("Notion Relation 分页缺少 next_cursor")

    def query_pages(self, data_source_id):
        """分页读取数据源全部页面。"""
        pages = []
        cursor = None
        while True:
            if _cancelled():
                return pages
            payload = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            result = self.request(
                "POST", f"/data_sources/{data_source_id}/query", payload
            )
            pages.extend(
                item for item in result.get("results", []) if isinstance(item, dict)
            )
            if not result.get("has_more"):
                return pages
            cursor = result.get("next_cursor")
            if not cursor:
                raise NotionImportError("Notion 查询分页缺少 next_cursor")


def _rich_text(value):
    """拼接 Notion rich text/title 的纯文本。"""
    return "".join(
        str(item.get("plain_text") or "")
        for item in value or []
        if isinstance(item, dict)
    ).strip()


def _property_text(prop):
    """把常见 Notion 属性转换为文本。"""
    if not isinstance(prop, dict):
        return ""
    kind = prop.get("type")
    if kind in ("title", "rich_text"):
        return _rich_text(prop.get(kind))
    if kind in ("select", "status"):
        value = prop.get(kind) or {}
        return str(value.get("name") or "").strip()
    if kind == "url":
        return str(prop.get("url") or "").strip()
    return ""


def _property_labels(prop):
    """把选择或文本属性转换为去重名称列表。"""
    if not isinstance(prop, dict):
        return []
    kind = prop.get("type")
    if kind == "multi_select":
        values = [
            str(item.get("name") or "").strip()
            for item in prop.get("multi_select", [])
            if isinstance(item, dict)
        ]
    else:
        text = _property_text(prop)
        values = re.split(r"[,，;；\n]", text) if text else []
    result = []
    seen = set()
    for value in values:
        value = value.strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _relation_ids(prop):
    """提取 Relation 属性中的页面 ID。"""
    if not isinstance(prop, dict) or prop.get("type") != "relation":
        return []
    result = []
    seen = set()
    for item in prop.get("relation") or []:
        if not isinstance(item, dict):
            continue
        page_id = normalize_notion_id(item.get("id"))
        if page_id and page_id not in seen:
            seen.add(page_id)
            result.append(page_id)
    return result


def _auto_title(page):
    """读取页面中的标题属性。"""
    for prop in (page.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            text = _property_text(prop)
            if text:
                return text
    return ""


def _tag_mappings(settings):
    """读取多标签列映射；旧版单列设置自动迁移为同名分类。"""
    result = []
    seen = set()
    raw_mappings = (settings or {}).get("notion_tag_mappings")
    if isinstance(raw_mappings, list):
        for item in raw_mappings:
            if not isinstance(item, dict):
                continue
            property_name = str(item.get("property") or "").strip()
            category = str(item.get("category") or property_name).strip()
            key = (category.casefold(), property_name.casefold())
            if property_name and key not in seen:
                result.append({"property": property_name, "category": category})
                seen.add(key)
    if not result:
        property_name = str((settings or {}).get("notion_tags_property") or "").strip()
        if property_name:
            result.append({"property": property_name, "category": property_name})
    return result


def extract_page_media(page, settings, relation_names=None):
    """把一个 Notion 页面转换为待导入媒体记录。"""
    properties = page.get("properties") or {}
    media_name = str(settings.get("notion_media_property") or "").strip()
    media_prop = properties.get(media_name) or {}
    if media_prop.get("type") != "files":
        return []

    title_name = str(settings.get("notion_title_property") or "").strip()
    title = _property_text(properties.get(title_name)) if title_name else ""
    title = title or _auto_title(page)
    groups_name = str(settings.get("notion_collection_property") or "").strip()
    relation_names = relation_names or {}
    groups_prop = properties.get(groups_name) if groups_name else None
    tags = []
    tag_seen = set()
    for mapping in _tag_mappings(settings):
        tags_prop = properties.get(mapping["property"])
        labels = _property_labels(tags_prop)
        labels.extend(
            relation_names[page_id]
            for page_id in _relation_ids(tags_prop)
            if relation_names.get(page_id)
        )
        for label in labels:
            key = (mapping["category"].casefold(), label.casefold())
            if key not in tag_seen:
                tags.append({"category": mapping["category"], "name": label})
                tag_seen.add(key)
    groups = _property_labels(groups_prop)
    groups.extend(
        relation_names[page_id]
        for page_id in _relation_ids(groups_prop)
        if relation_names.get(page_id)
    )
    groups = list(dict.fromkeys(groups))

    records = []
    files = media_prop.get("files") or []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "file":
            url = (item.get("file") or {}).get("url")
        elif kind == "external":
            url = (item.get("external") or {}).get("url")
        else:
            url = ""
        if not url:
            continue
        file_name = str(item.get("name") or "").strip()
        base_name = title or os.path.splitext(file_name)[0]
        base_name = base_name or str(page.get("id") or "Notion")
        if len(files) > 1:
            base_name = f"{base_name}-{index + 1}"
        records.append(
            {
                "url": str(url),
                "name": base_name,
                "tags": list(tags),
                "collections": list(groups),
                "page_id": normalize_notion_id(page.get("id"))
                or str(page.get("id") or ""),
                "property_id": urllib.parse.unquote(
                    str(media_prop.get("id") or media_name)
                ),
                "file_index": index,
                "file_name": file_name,
                "file_type": kind,
                "page_last_edited_time": str(page.get("last_edited_time") or ""),
            }
        )
    return records


def _source_file_exists(filename):
    """确认来源映射指向的本地缓存文件仍然存在。"""
    filename = str(filename or "")
    if not filename or os.path.basename(filename) != filename:
        return False
    cache_dir = get_config().cache_dir
    if os.path.isfile(cache_dir / filename):
        return True
    for root, dirs, files in os.walk(cache_dir):
        if os.path.normcase(os.path.abspath(root)) == os.path.normcase(
            os.path.abspath(cache_dir)
        ):
            dirs[:] = [
                item
                for item in dirs
                if item.casefold() != CHAT_RECORD_MEDIA_DIRNAME.casefold()
            ]
        if filename in files:
            return os.path.isfile(os.path.join(root, filename))
    return False


def _unchanged_source(db, data_source_id, record):
    """返回未变化且本地文件仍存在的 Notion 托管来源。"""
    if record.get("file_type") != "file" or not record.get("page_last_edited_time"):
        return None
    source = db.get_notion_source(
        data_source_id,
        record["page_id"],
        record["property_id"],
        record["file_index"],
        record["file_name"],
        record["page_last_edited_time"],
    )
    if source and _source_file_exists(source.get("filename")):
        return source
    return None


def _remember_source(db, data_source_id, record, meme_id):
    """保存稳定的 Notion 托管文件来源，不保存会过期的签名 URL。"""
    if (
        record.get("file_type") != "file"
        or not record.get("page_last_edited_time")
        or not meme_id
    ):
        return
    db.upsert_notion_source(
        meme_id,
        data_source_id,
        record["page_id"],
        record["property_id"],
        record["file_index"],
        record["file_name"],
        record["page_last_edited_time"],
    )


def _file_sha256(path):
    """计算已下载媒体的内容哈希，用于找到重复图片对应的本地记录。"""
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _restore_cached_file(path, row):
    """哈希记录仍在但缓存文件丢失时，用本次下载内容修复本地缓存。"""
    filename = str((row or {}).get("filename") or "")
    if not filename or os.path.basename(filename) != filename:
        return False
    target = get_config().cache_dir / filename
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return True
    except OSError:
        logger.exception("Failed to restore missing Notion cache file: %s", filename)
        return False


def _apply_record_metadata(db, meme_id, record, notion_collection):
    """把 Notion 标签和分组应用到已定位的本地表情。"""
    if record["tags"]:
        db.add_tags_to_memes([meme_id], record["tags"])
    if notion_collection is None:
        notion_collection = db.create_collection("Notion")
    db.add_to_collection(meme_id, notion_collection)
    for group in record["collections"]:
        group_id = db.create_collection(group, parent_id=notion_collection)
        db.add_to_collection(meme_id, group_id)
    return notion_collection


def resolve_relation_names(client, pages, settings):
    """批量解析标签和分组 Relation 页面标题。"""
    names = set()
    tag_property_names = [item["property"] for item in _tag_mappings(settings)]
    groups_name = str(settings.get("notion_collection_property") or "").strip()
    for page in pages:
        properties = page.get("properties") or {}
        for property_name in (*tag_property_names, groups_name):
            if property_name:
                prop = properties.get(property_name) or {}
                relation_ids = _relation_ids(prop)
                if prop.get("has_more") and prop.get("id"):
                    relation_ids = client.retrieve_relation_ids(
                        page.get("id"), prop.get("id")
                    )
                    prop["relation"] = [{"id": item} for item in relation_ids]
                    prop["has_more"] = False
                names.update(relation_ids)
    result = {}
    for index, page_id in enumerate(sorted(names), start=1):
        if _cancelled():
            return result
        _update_state(
            phase="relations",
            message=f"正在解析关联标签 {index}/{len(names)}",
        )
        page = client.retrieve_page(page_id)
        title = _auto_title(page)
        if title:
            result[page_id] = title
    return result


def _validate_public_url(url):
    """只允许指向公网地址的 HTTPS URL。"""
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise NotionImportError("媒体 URL 必须是公网 HTTPS 地址")
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise NotionNetworkError(f"暂时无法解析媒体地址：{parsed.hostname}") from exc
    for item in addresses:
        address = ipaddress.ip_address(item[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise NotionImportError("媒体 URL 指向非公网地址，已拒绝下载")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """逐跳校验下载重定向。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _image_extension(path):
    """按文件头识别 OhMyMeme 支持的图片格式。"""
    with open(path, "rb") as file:
        head = file.read(16)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return ".webp"
    if head.startswith(b"BM"):
        return ".bmp"
    return ""


def _remove_partial_download(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _wait_download_retry(delay, cancel_check=None):
    """可取消的退避等待，取消响应延迟不超过 100ms。"""
    deadline = time.monotonic() + max(float(delay), 0.0)
    while True:
        if cancel_check and cancel_check():
            raise InterruptedError("cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.1))


def download_media(url, path, cancel_check=None, opener=None):
    """安全下载 Notion 媒体；临时网络故障最多自动重试 3 次。"""
    opener = opener or urllib.request.build_opener(_SafeRedirectHandler()).open
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "OhMyMeme/Notion", "Accept": "image/*"},
    )
    retryable_http = {408, 425, 429, 500, 502, 503, 504}
    last_error = None
    retried = False
    for attempt in range(4):
        total = 0
        try:
            _validate_public_url(url)
            with opener(request, timeout=30) as response, open(path, "wb") as output:
                length = response.headers.get("Content-Length", "")
                if length and int(length) > _IMPORT_MAX_BYTES:
                    raise NotionImportError(
                        "图片超过 %d MiB 原件上限"
                        % (_IMPORT_MAX_BYTES // (1024 * 1024))
                    )
                while True:
                    if cancel_check and cancel_check():
                        raise InterruptedError("cancelled")
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _IMPORT_MAX_BYTES:
                        raise NotionImportError(
                            "图片超过 %d MiB 原件上限"
                            % (_IMPORT_MAX_BYTES // (1024 * 1024))
                        )
                    output.write(chunk)
            break
        except InterruptedError:
            _remove_partial_download(path)
            raise
        except urllib.error.HTTPError as exc:
            _remove_partial_download(path)
            if exc.code not in retryable_http:
                raise NotionImportError(f"媒体下载失败（HTTP {exc.code}）") from exc
            last_error = exc
            retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
            try:
                delay = min(float(retry_after), 5.0)
            except (TypeError, ValueError):
                delay = min(0.5 * (2**attempt), 5.0)
        except (
            NotionNetworkError,
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            ssl.SSLError,
            http.client.HTTPException,
        ) as exc:
            _remove_partial_download(path)
            last_error = exc
            delay = min(0.5 * (2**attempt), 5.0)
        except NotionImportError:
            _remove_partial_download(path)
            raise
        except Exception:
            _remove_partial_download(path)
            raise

        if attempt >= 3:
            raise NotionImportError(
                f"媒体下载网络异常，已重试 3 次：{last_error}"
            ) from last_error
        retried = True
        _update_state(message=f"网络异常，正在重试下载（{attempt + 2}/4）")
        logger.warning("Notion media download retry %s/4: %s", attempt + 2, last_error)
        _wait_download_retry(delay, cancel_check)

    if not _image_extension(path):
        _remove_partial_download(path)
        raise NotionImportError("媒体不是支持的 PNG/JPG/GIF/WebP/BMP 图片")
    if retried:
        _update_state(message="网络已恢复，正在导入图片...")
    return path


def _validated_settings(settings):
    """清洗并验证 Notion 导入配置。"""
    result = {
        key: str((settings or {}).get(key) or "").strip()
        for key in (
            "notion_token",
            "notion_database_id",
            "notion_data_source_id",
            "notion_media_property",
            "notion_title_property",
            "notion_tags_property",
            "notion_collection_property",
            "notion_version",
        )
    }
    result["notion_tag_mappings"] = _tag_mappings(settings or {})
    result["notion_version"] = result["notion_version"] or "2026-03-11"
    if not result["notion_token"]:
        raise NotionImportError("请填写 Notion Integration Token")
    if not (
        normalize_notion_id(result["notion_database_id"])
        or normalize_notion_id(result["notion_data_source_id"])
    ):
        raise NotionImportError("请填写有效的数据库 URL、数据库 ID 或 Data source ID")
    return result


def test_notion_connection(settings, client_factory=NotionClient):
    """测试连接并返回可用于映射的属性列表。"""
    config = _validated_settings(settings)
    client = client_factory(config["notion_token"], config["notion_version"])
    source_id = client.resolve_data_source(
        config["notion_database_id"], config["notion_data_source_id"]
    )
    schema = client.schema(source_id)
    properties = schema.get("properties") or {}
    result = []
    for name, prop in properties.items():
        if isinstance(prop, dict):
            result.append({"name": str(name), "type": str(prop.get("type") or "")})
    result.sort(key=lambda item: item["name"].casefold())
    return {"ok": True, "data_source_id": source_id, "properties": result}


def start_notion_import(webui, settings, client_factory=NotionClient):
    """在后台启动只读 Notion 数据库导入。"""
    global _NOTION_CANCEL
    try:
        config = _validated_settings(settings)
    except NotionImportError as exc:
        return {"ok": False, "error": str(exc)}

    with _NOTION_LOCK:
        if _NOTION_STATE["status"] == "running":
            return {"ok": False, "error": "已有 Notion 导入任务正在进行"}
        _NOTION_CANCEL = False
        _NOTION_STATE.update(
            status="running",
            phase="connecting",
            message="正在连接 Notion...",
            progress=0,
            total=0,
            done=0,
            imported=0,
            duplicates=0,
            unchanged=0,
            rejected=0,
            failed=0,
            changed=False,
            error="",
        )

    def _run():
        imported = 0
        duplicates = 0
        unchanged = 0
        rejected = 0
        failed = 0
        manifest_changed = False

        def flush_manifest():
            """把已完成条目的元数据写入同步清单，包括取消/失败路径。"""
            if not manifest_changed:
                return
            try:
                build_manifest()
            except Exception:
                logger.exception("Failed to finalize partial Notion import manifest")

        try:
            client = client_factory(config["notion_token"], config["notion_version"])
            source_id = client.resolve_data_source(
                config["notion_database_id"], config["notion_data_source_id"]
            )
            schema = client.schema(source_id)
            media_name = config["notion_media_property"]
            media_schema = (schema.get("properties") or {}).get(media_name) or {}
            if media_schema.get("type") != "files":
                raise NotionImportError(
                    f"属性“{media_name or '未填写'}”不是 Files & media 类型"
                )

            _update_state(phase="querying", message="正在读取数据库页面...")
            pages = client.query_pages(source_id)
            relation_names = resolve_relation_names(client, pages, config)
            records = []
            for page in pages:
                records.extend(extract_page_media(page, config, relation_names))
            total = len(records)
            _update_state(total=total, message=f"找到 {total} 张图片")
            if _cancelled():
                flush_manifest()
                _update_state(
                    status="cancelled",
                    message="已取消，已导入的部分已保留",
                    changed=manifest_changed,
                )
                return
            if not records:
                _update_state(
                    status="done", progress=100, message="数据库中没有可导入图片"
                )
                return

            db = get_db()
            notion_collection = None
            with tempfile.TemporaryDirectory(prefix="ohmm_notion_") as tmp_dir:
                for index, record in enumerate(records, start=1):
                    if _cancelled():
                        flush_manifest()
                        _update_state(
                            status="cancelled",
                            message="已取消，已导入的部分已保留",
                            changed=manifest_changed,
                        )
                        return
                    path = os.path.join(tmp_dir, f"{index}.download")
                    source = _unchanged_source(db, source_id, record)
                    if source:
                        unchanged += 1
                        notion_collection = _apply_record_metadata(
                            db, source["meme_id"], record, notion_collection
                        )
                        manifest_changed = True
                        _update_state(
                            phase="skipping",
                            message=f"未变化，跳过下载：{record['name']}",
                        )
                    else:
                        _update_state(
                            phase="downloading",
                            message=f"正在下载 {record['name']}",
                        )
                        try:
                            download_media(record["url"], path, _cancelled)
                            result = webui._do_import([path], [record["name"]])
                            ids = result.get("ids") or []
                            duplicate_count = int(result.get("skipped_dup") or 0)
                            duplicates += duplicate_count
                            rejected += int(result.get("rejected") or 0)
                            imported += len(ids)
                            meme_id = ids[0] if ids else None
                            if not meme_id and duplicate_count:
                                row = db.get_by_hash(_file_sha256(path))
                                if row and (
                                    _source_file_exists(row.get("filename"))
                                    or _restore_cached_file(path, row)
                                ):
                                    meme_id = row["id"]
                            if meme_id:
                                notion_collection = _apply_record_metadata(
                                    db, meme_id, record, notion_collection
                                )
                                _remember_source(db, source_id, record, meme_id)
                                manifest_changed = True
                        except InterruptedError:
                            flush_manifest()
                            _update_state(
                                status="cancelled",
                                message="已取消，已导入的部分已保留",
                                changed=manifest_changed,
                            )
                            return
                        except NotionImportError as exc:
                            failed += 1
                            logger.warning("Notion media skipped: %s", exc)
                        except Exception:
                            failed += 1
                            logger.exception("Notion media import failed")
                        finally:
                            try:
                                os.unlink(path)
                            except OSError:
                                pass
                    progress = round(index * 100 / total)
                    _update_state(
                        done=index,
                        progress=progress,
                        imported=imported,
                        duplicates=duplicates,
                        unchanged=unchanged,
                        rejected=rejected,
                        failed=failed,
                        changed=manifest_changed,
                    )
            flush_manifest()
            _update_state(
                status="done",
                phase="done",
                progress=100,
                changed=manifest_changed,
                message=(
                    f"导入完成：新增 {imported}，未变化 {unchanged}，"
                    f"重复 {duplicates}，"
                    f"拒绝 {rejected}，失败 {failed}"
                ),
            )
        except Exception as exc:
            logger.exception("Notion import failed")
            flush_manifest()
            _update_state(
                status="error",
                phase="error",
                error=str(exc),
                message="导入失败，已完成的部分已保留",
                changed=manifest_changed,
            )

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True}
