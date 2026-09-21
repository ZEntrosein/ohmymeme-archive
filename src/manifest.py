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
        if not filenames and not children:
            db.delete_collection(cid)
            continue
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


def _write_manifest(data: dict) -> bool:
    """原子写入本地清单；调用方必须持有 ``_MANIFEST_LOCK``。"""
    path = _index_path()
    try:
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


def _build_unlocked() -> List[Dict]:
    """从数据库重建完整索引并写入磁盘"""
    db = get_db()
    rows = db.search(keyword="", tags=None, limit=999999)

    memes = []
    cache_dir = get_config().cache_dir

    for r in rows:
        fname = r["filename"]
        path_getter = getattr(db, "get_category_path_names", None)
        category_parts = (
            path_getter(r.get("category_id"))
            if callable(path_getter)
            else []
        )
        storage_path = "/".join([*category_parts, fname])
        fpath = cache_dir.joinpath(*category_parts, fname)
        mtime = ""
        with guarded_read():
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
    _write_manifest(data)

    return memes


def build() -> List[Dict]:
    """串行重建完整索引，避免与增量元数据更新互相覆盖。"""
    with _MANIFEST_LOCK:
        return _build_unlocked()


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

    清单不存在、损坏或未包含目标文件时返回 False，由调用方决定是否退回
    完整重建。使用与 build 相同的锁，避免并发重建覆盖刚写入的新标题。
    """
    filename = str(filename or "")
    if not filename:
        return False
    with _MANIFEST_LOCK:
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
