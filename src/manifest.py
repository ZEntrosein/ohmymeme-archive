"""远端同步索引清单维护"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Dict, List

from .bulk_read import guarded_read
from .config import get_config
from .database import get_db

logger = logging.getLogger(__name__)

INDEX_FILENAME = "meme-index.json"
_MANIFEST_LOCK = threading.RLock()
_REBUILD_STATE_LOCK = threading.Lock()
_REBUILD_RUNNING = False
_REBUILD_REQUESTED = False


def _index_path() -> Path:
    return get_config().data_dir / INDEX_FILENAME


def _build_collection_tree(db, parent_id=None) -> list:
    raw = db.get_collections()
    items = []
    for cid, cname, pid, _ in raw:
        if pid != parent_id:
            continue
        member_rows = db.search(collection_id=cid, limit=999999)
        filenames = [mr["filename"] for mr in member_rows]
        children = _build_collection_tree(db, parent_id=cid)
        item = {"name": cname, "filenames": filenames}
        if children:
            item["children"] = children
        items.append(item)
    return items


def _build_category_tree(db, parent_id=None) -> list:
    """类别树使用名称路径同步，保留空类别，避免不同设备 id 不一致。"""
    getter = getattr(db, "get_categories", None)
    if not callable(getter):
        return []
    raw = getter()
    items = []
    for cid, cname, pid, _ in raw:
        if pid != parent_id:
            continue
        children = _build_category_tree(db, parent_id=cid)
        item = {"name": cname}
        if children:
            item["children"] = children
        items.append(item)
    return items


def _write_manifest(data: dict, metadata_filenames=None) -> bool:
    """原子写入本地清单；调用方必须持有 ``_MANIFEST_LOCK``。"""
    path = _index_path()
    try:
        if metadata_filenames is not None:
            cfg = get_config()
            db = get_db()
            include_tags = bool(cfg.get("manifest_include_tags", True))
            tags_map = db.get_tags_map() if include_tags else {}
            records_getter = getattr(db, "get_tag_records_map", None)
            records_map = (
                records_getter() if include_tags and callable(records_getter) else {}
            )
            for entry in data.get("memes", []):
                if not isinstance(entry, dict):
                    continue
                if (
                    metadata_filenames is not True
                    and entry.get("filename") not in metadata_filenames
                ):
                    continue
                if include_tags:
                    entry["tags"] = tags_map.get(entry.get("filename"), [])
                    if callable(records_getter):
                        entry["tag_records"] = records_map.get(
                            entry.get("filename"), []
                        )
                else:
                    entry.pop("tags", None)
                    entry.pop("tag_records", None)
            if cfg.get("manifest_include_favorites", True):
                data["favorite"] = [
                    row["filename"]
                    for row in db.search(favorite_only=True, limit=999999)
                ]
            else:
                data.pop("favorite", None)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        logger.debug(
            "manifest written: %s memes, %s collections",
            len(data.get("memes", [])),
            len(data.get("collections", [])),
        )
        return True
    except Exception as e:
        logger.warning("manifest write failed: %s", e)
        return False


def _build_unlocked(read_priority="foreground") -> List[Dict]:
    """从数据库重建完整索引并写入磁盘"""
    db = get_db()
    rows = db.search(keyword="", tags=None, limit=999999)

    memes = []
    cache_dir = get_config().cache_dir

    for r in rows:
        fname = r["filename"]
        path_getter = getattr(db, "get_category_path_names", None)
        category_parts = (
            path_getter(r.get("category_id")) if callable(path_getter) else []
        )
        storage_path = "/".join([*category_parts, fname])
        fpath = cache_dir.joinpath(*category_parts, fname)
        mtime = ""
        with guarded_read(priority=read_priority):
            if fpath.exists():
                try:
                    mtime = str(int(fpath.stat().st_mtime))
                except Exception:
                    pass
        memes.append(
            {
                "filename": fname,
                "name": r.get("original_name", os.path.splitext(fname)[0]),
                "sha256": r.get("file_hash", ""),
                "source_sha256": r.get("source_file_hash", ""),
                "compressed_only": bool(r.get("compressed_only")),
                "file_size": r.get("file_size", 0),
                "mtime": mtime,
                "category_path": category_parts,
                "storage_path": storage_path,
            }
        )

    collections = _build_collection_tree(db)

    categories = _build_category_tree(db)
    data = {
        "version": 4,
        "memes": memes,
        "collections": collections,
        "categories": categories,
    }
    _write_manifest(data, metadata_filenames=True)

    return memes


def build(read_priority="foreground") -> List[Dict]:
    """串行重建完整索引，避免与增量元数据更新互相覆盖。"""
    with _MANIFEST_LOCK:
        return _build_unlocked(read_priority=read_priority)


def _background_rebuild_worker():
    global _REBUILD_REQUESTED, _REBUILD_RUNNING
    while True:
        with _REBUILD_STATE_LOCK:
            if not _REBUILD_REQUESTED:
                _REBUILD_RUNNING = False
                return
            _REBUILD_REQUESTED = False
        try:
            build(read_priority="background")
        except Exception:
            logger.exception("background manifest rebuild failed")


def request_background_rebuild() -> bool:
    """合并请求并在后台重建清单，调用线程永不等待虚拟盘扫描。"""
    global _REBUILD_REQUESTED, _REBUILD_RUNNING
    with _REBUILD_STATE_LOCK:
        _REBUILD_REQUESTED = True
        if _REBUILD_RUNNING:
            return False
        _REBUILD_RUNNING = True
    try:
        thread = threading.Thread(
            target=_background_rebuild_worker,
            name="manifest-rebuild",
            daemon=True,
        )
        thread.start()
        return True
    except Exception:
        with _REBUILD_STATE_LOCK:
            _REBUILD_RUNNING = False
        logger.exception("start background manifest rebuild failed")
        return False


def add_memes(meme_ids) -> bool:
    """把新入库条目增量写入清单，不读取原件或等待在途全库重建。

    若清单锁正被旧的完整重建占用、清单缺失或内容损坏，立即返回 False；
    调用方可安排后台重建，但不得让导入响应等待该锁。
    """
    try:
        ids = [int(value) for value in meme_ids]
    except (TypeError, ValueError):
        return False
    if not ids or not _MANIFEST_LOCK.acquire(blocking=False):
        return False
    try:
        path = _index_path()
        if not path.exists():
            return False
        data = _load_unlocked()
        memes = data.get("memes")
        if not isinstance(memes, list):
            return False
        db = get_db()
        new_items = []
        new_filenames = set()
        path_getter = getattr(db, "get_category_path_names", None)
        for meme_id in ids:
            row = db.get_by_id(meme_id)
            if not row:
                continue
            filename = str(row.get("filename") or "")
            if not filename:
                continue
            category_parts = (
                path_getter(row.get("category_id")) if callable(path_getter) else []
            )
            new_filenames.add(filename)
            new_items.append(
                {
                    "filename": filename,
                    "name": row.get("original_name", os.path.splitext(filename)[0]),
                    "sha256": row.get("file_hash", ""),
                    "source_sha256": row.get("source_file_hash", ""),
                    "compressed_only": bool(row.get("compressed_only")),
                    "file_size": row.get("file_size", 0),
                    # 新文件无需再触碰虚拟盘取 stat；同步以 sha256/size 为准。
                    "mtime": "",
                    "category_path": category_parts,
                    "storage_path": "/".join([*category_parts, filename]),
                }
            )
        if len(new_items) != len(ids):
            return False
        data["version"] = 4
        data["memes"] = new_items + [
            item
            for item in memes
            if not isinstance(item, dict) or item.get("filename") not in new_filenames
        ]
        return _write_manifest(data, metadata_filenames=new_filenames)
    finally:
        _MANIFEST_LOCK.release()


def remove_memes(filenames) -> bool:
    """从清单增量移除表情并刷新结构树，不读取任何原件。"""
    names = {str(value or "") for value in (filenames or []) if str(value or "")}
    if not names or not _MANIFEST_LOCK.acquire(blocking=False):
        return False
    try:
        path = _index_path()
        if not path.exists():
            return False
        data = _load_unlocked()
        memes = data.get("memes")
        if not isinstance(memes, list):
            return False
        remaining = [
            item
            for item in memes
            if not isinstance(item, dict) or item.get("filename") not in names
        ]
        if len(remaining) == len(memes):
            return False
        db = get_db()
        data["version"] = 4
        data["memes"] = remaining
        data["collections"] = _build_collection_tree(db)
        data["categories"] = _build_category_tree(db)
        return _write_manifest(data, metadata_filenames=set())
    finally:
        _MANIFEST_LOCK.release()


def _load_unlocked() -> Dict:
    """加载索引文件，不存在时返回空结构"""
    path = _index_path()
    if not path.exists():
        return {"version": 4, "memes": [], "collections": [], "categories": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version", 2) < 3:
            # 旧版 v2 扁平分组 → 新版 v3 嵌套：原地转换
            if isinstance(data.get("collections"), list):
                new_colls = []
                for c in data["collections"]:
                    if isinstance(c, dict) and "name" in c:
                        new_colls.append(
                            {
                                "name": c["name"],
                                "filenames": c.get("filenames", []),
                            }
                        )
                data["collections"] = new_colls
            data["version"] = 3
        if not isinstance(data.get("categories"), list):
            data["categories"] = []
        data.setdefault("version", 4)
        return data
    except Exception as e:
        logger.warning(f"manifest load failed: {e}")
        return {"version": 4, "memes": [], "collections": [], "categories": []}


def load() -> Dict:
    """读取一次原子快照。"""
    with _MANIFEST_LOCK:
        return _load_unlocked()


def update_meme_name(filename: str, name: str) -> bool:
    """只更新清单中的本地标题，不访问原件或挂载盘。

    清单锁正被完整重建占用、清单不存在、损坏或未包含目标文件时立即返回
    False，由调用方安排后台重建。这样详情保存不会等待一次全库扫描结束。
    """
    filename = str(filename or "")
    if not filename or not _MANIFEST_LOCK.acquire(blocking=False):
        return False
    try:
        path = _index_path()
        if not path.exists():
            return False
        data = _load_unlocked()
        memes = data.get("memes")
        if not isinstance(memes, list):
            return False
        for item in memes:
            if isinstance(item, dict) and item.get("filename") == filename:
                item["name"] = str(name or "")
                return _write_manifest(data)
        return False
    finally:
        _MANIFEST_LOCK.release()


def update_meme_structure(meme_id: int, previous_filename: str = "") -> bool:
    """增量刷新单张表情及分组/类别结构，不读取任何原件。

    详情页修改分组、类别或文件名时，数据库已经是新的权威状态。这里复用
    清单中原有的 ``mtime``，从数据库更新目标条目，并重建纯数据库驱动的
    分组与类别树。清单锁繁忙、清单缺失、损坏或找不到目标条目时立即返回
    False，让调用方安排后台重建，不阻塞保存响应。
    """
    try:
        meme_id = int(meme_id)
    except (TypeError, ValueError):
        return False
    if not _MANIFEST_LOCK.acquire(blocking=False):
        return False
    try:
        path = _index_path()
        if not path.exists():
            return False
        data = _load_unlocked()
        memes = data.get("memes")
        if not isinstance(memes, list):
            return False
        db = get_db()
        row = db.get_by_id(meme_id)
        if not row:
            return False
        filename = str(row.get("filename") or "")
        candidates = {filename}
        if previous_filename:
            candidates.add(str(previous_filename))
        target_index = next(
            (
                index
                for index, item in enumerate(memes)
                if isinstance(item, dict) and item.get("filename") in candidates
            ),
            None,
        )
        if target_index is None:
            return False
        current = dict(memes[target_index])
        path_getter = getattr(db, "get_category_path_names", None)
        category_parts = (
            path_getter(row.get("category_id")) if callable(path_getter) else []
        )
        current.update(
            {
                "filename": filename,
                "name": row.get("original_name", os.path.splitext(filename)[0]),
                "sha256": row.get("file_hash", ""),
                "file_size": row.get("file_size", 0),
                "mtime": str(current.get("mtime", "")),
                "category_path": category_parts,
                "storage_path": "/".join([*category_parts, filename]),
            }
        )
        memes[target_index] = current
        data["version"] = 4
        data["collections"] = _build_collection_tree(db)
        data["categories"] = _build_category_tree(db)
        return _write_manifest(data, metadata_filenames={filename})
    finally:
        _MANIFEST_LOCK.release()
