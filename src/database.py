"""SQLite元数据管理 - 零依赖"""

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .category_storage import validate_category_name
from .config import CHAT_RECORD_MEDIA_DIRNAME, get_config

_lazy_pinyin = None
_UNSET = object()


def _default_chat_media_folder(account_id, source_message_id) -> str:
    """为旧记录和新归档生成稳定、互不冲突的默认媒体目录名。"""
    key = f"{account_id or ''}:{source_message_id or ''}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


# 备份恢复候选库预校验所需的表与列（backup.prepare_restore_source 使用）
_RESTORE_REQUIRED_TABLES = (
    "memes",
    "tags",
    "meme_tags",
    "collections",
    "meme_collections",
    "favorites",
    "recent_uses",
)
# 恢复候选库必需的外键定义：(子表列, 父表, 父表列, ON DELETE 行为)，
# 缺失会导致恢复后级联删除失效、产生孤儿记录
_RESTORE_REQUIRED_FOREIGN_KEYS = {
    "meme_tags": {
        ("meme_id", "memes", "id", "CASCADE"),
        ("tag_id", "tags", "id", "CASCADE"),
    },
    "meme_collections": {
        ("meme_id", "memes", "id", "CASCADE"),
        ("collection_id", "collections", "id", "CASCADE"),
    },
    "favorites": {("meme_id", "memes", "id", "CASCADE")},
    "recent_uses": {("meme_id", "memes", "id", "CASCADE")},
    "collections": {("parent_id", "collections", "id", "CASCADE")},
    "categories": {("parent_id", "categories", "id", "CASCADE")},
    "memes": {("category_id", "categories", "id", "SET NULL")},
    "notion_sources": {("meme_id", "memes", "id", "CASCADE")},
    "chat_records": {("category_id", "categories", "id", "SET NULL")},
    "chat_tags": {
        ("chat_id", "chat_records", "id", "CASCADE"),
        ("tag_id", "tags", "id", "CASCADE"),
    },
    "chat_collections": {
        ("chat_id", "chat_records", "id", "CASCADE"),
        ("collection_id", "collections", "id", "CASCADE"),
    },
}
_RESTORE_REQUIRED_COLUMNS = {
    "memes": {
        "id",
        "filename",
        "file_hash",
        "original_name",
        "width",
        "height",
        "file_size",
        "mime_type",
        "sort_order",
        "stego_of_hash",
        "from_stego",
        "perceptual_hash",
        "ai_description",
        "ai_ocr_text",
        "manual_description",
        "manual_ocr_text",
        "category_id",
        "created_at",
        "updated_at",
    },
    "tags": {"id", "name", "category"},
    "meme_tags": {"meme_id", "tag_id"},
    "collections": {"id", "name", "parent_id", "sort_order"},
    "categories": {"id", "name", "parent_id", "sort_order"},
    "meme_collections": {"meme_id", "collection_id", "sort_order"},
    "favorites": {"meme_id", "added_at"},
    "recent_uses": {"meme_id", "used_at"},
    "notion_sources": {
        "id",
        "meme_id",
        "data_source_id",
        "page_id",
        "property_id",
        "file_index",
        "file_name",
        "page_last_edited_time",
        "created_at",
        "updated_at",
    },
    "chat_records": {
        "id",
        "account_id",
        "source_message_id",
        "source_message_seq",
        "message_type",
        "peer_id",
        "category_id",
        "title",
        "name",
        "media_folder",
        "node_count",
        "record_count",
        "package_json",
        "created_at",
        "updated_at",
    },
    "chat_tags": {"chat_id", "tag_id"},
    "chat_collections": {"chat_id", "collection_id", "sort_order"},
}


# 名称自然排序键：数字段按整数（1,2,10），其余段按拼音（pypinyin 缺失时退回原串小写）
def _name_sort_key(name):
    global _lazy_pinyin
    key = []
    for part in re.split(r"(\d+)", str(name)):
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
            continue
        if _lazy_pinyin is None:
            try:
                from pypinyin import lazy_pinyin as _lp

                _lazy_pinyin = _lp
            except Exception:
                _lazy_pinyin = False
        if _lazy_pinyin:
            text = "".join(_lazy_pinyin(part)).lower()
        else:
            text = part.lower()
        key.append((1, text))
    return key


class MemeDB:
    """SQLite元数据存储，线程安全"""

    def __init__(self, db_path: Path = None):
        if db_path is None:
            db_path = get_config().db_path
        self._db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self._db_path), timeout=5.0, check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _init_db(self):
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT    NOT NULL,
                file_hash   TEXT    NOT NULL DEFAULT '',
                source_file_hash TEXT NOT NULL DEFAULT '',
                compressed_only INTEGER DEFAULT 0,
                original_name TEXT  NOT NULL DEFAULT '',
                width       INTEGER DEFAULT 0,
                height      INTEGER DEFAULT 0,
                file_size   INTEGER DEFAULT 0,
                mime_type   TEXT    DEFAULT 'image/png',
                sort_order  INTEGER DEFAULT 0,
                stego_of_hash TEXT DEFAULT NULL,
                from_stego  INTEGER DEFAULT 0,
                perceptual_hash TEXT DEFAULT NULL,
                ai_description TEXT NOT NULL DEFAULT '',
                ai_ocr_text TEXT NOT NULL DEFAULT '',
                manual_description TEXT NOT NULL DEFAULT '',
                manual_ocr_text TEXT NOT NULL DEFAULT '',
                category_id INTEGER DEFAULT NULL
                            REFERENCES categories(id) ON DELETE SET NULL,
                daily_filename TEXT NOT NULL DEFAULT '',
                daily_file_size INTEGER DEFAULT 0,
                daily_mime_type TEXT NOT NULL DEFAULT '',
                daily_profile INTEGER DEFAULT 0,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS tags (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                name     TEXT NOT NULL COLLATE NOCASE,
                category TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
                UNIQUE (category, name)
            );

            CREATE TABLE IF NOT EXISTS meme_tags (
                meme_id INTEGER NOT NULL REFERENCES memes(id) ON DELETE CASCADE,
                tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (meme_id, tag_id)
            );

            CREATE TABLE IF NOT EXISTS collections (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL COLLATE NOCASE,
                parent_id   INTEGER DEFAULT NULL
                              REFERENCES collections(id) ON DELETE CASCADE,
                sort_order  INTEGER DEFAULT 0,
                is_hidden   INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS meme_collections (
                meme_id       INTEGER NOT NULL REFERENCES memes(id) ON DELETE CASCADE,
                collection_id INTEGER NOT NULL
                              REFERENCES collections(id) ON DELETE CASCADE,
                sort_order    INTEGER DEFAULT 0,
                PRIMARY KEY (meme_id, collection_id)
            );

            CREATE TABLE IF NOT EXISTS categories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL COLLATE NOCASE,
                parent_id   INTEGER DEFAULT NULL
                            REFERENCES categories(id) ON DELETE CASCADE,
                sort_order  INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS favorites (
                meme_id   INTEGER PRIMARY KEY REFERENCES memes(id) ON DELETE CASCADE,
                added_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS recent_uses (
                meme_id   INTEGER NOT NULL REFERENCES memes(id) ON DELETE CASCADE,
                used_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                PRIMARY KEY (meme_id)
            );

            CREATE TABLE IF NOT EXISTS notion_sources (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                meme_id     INTEGER NOT NULL REFERENCES memes(id) ON DELETE CASCADE,
                data_source_id TEXT NOT NULL,
                page_id     TEXT NOT NULL,
                property_id TEXT NOT NULL,
                file_index  INTEGER NOT NULL,
                file_name   TEXT NOT NULL DEFAULT '',
                page_last_edited_time TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE (data_source_id, page_id, property_id, file_index, file_name)
            );

            CREATE TABLE IF NOT EXISTS chat_records (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id  TEXT NOT NULL,
                source_message_id TEXT NOT NULL,
                source_message_seq TEXT NOT NULL DEFAULT '',
                message_type TEXT NOT NULL DEFAULT '',
                peer_id     TEXT NOT NULL DEFAULT '',
                category_id INTEGER DEFAULT NULL
                            REFERENCES categories(id) ON DELETE SET NULL,
                title       TEXT NOT NULL DEFAULT '',
                name        TEXT NOT NULL DEFAULT '',
                media_folder TEXT NOT NULL DEFAULT '',
                node_count  INTEGER NOT NULL DEFAULT 0,
                record_count INTEGER NOT NULL DEFAULT 0,
                package_json TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE (account_id, source_message_id)
            );

            CREATE TABLE IF NOT EXISTS chat_tags (
                chat_id INTEGER NOT NULL REFERENCES chat_records(id) ON DELETE CASCADE,
                tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (chat_id, tag_id)
            );

            CREATE TABLE IF NOT EXISTS chat_collections (
                chat_id INTEGER NOT NULL
                    REFERENCES chat_records(id) ON DELETE CASCADE,
                collection_id INTEGER NOT NULL
                    REFERENCES collections(id) ON DELETE CASCADE,
                sort_order    INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, collection_id)
            );

            CREATE INDEX IF NOT EXISTS idx_memes_hash ON memes(file_hash);
            CREATE INDEX IF NOT EXISTS idx_memes_name ON memes(filename);
            CREATE INDEX IF NOT EXISTS idx_recent_uses_at ON recent_uses(used_at);
            CREATE INDEX IF NOT EXISTS idx_notion_sources_meme
                ON notion_sources(meme_id);
            CREATE INDEX IF NOT EXISTS idx_chat_records_updated
                ON chat_records(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_chat_tags_tag
                ON chat_tags(tag_id);
            CREATE INDEX IF NOT EXISTS idx_chat_collections_collection
                ON chat_collections(collection_id);
        """
        )
        self._migrate(conn)

    def _migrate(self, conn):
        """迁移旧表：添加可能缺失的列并补建依赖新列的索引（幂等，可重复执行）"""
        # 来源映射单独建表，使旧数据库与旧备份恢复后都能自动获得该能力。
        conn.executescript(
            """
                CREATE TABLE IF NOT EXISTS categories (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT NOT NULL COLLATE NOCASE,
                    parent_id   INTEGER DEFAULT NULL
                                REFERENCES categories(id) ON DELETE CASCADE,
                    sort_order  INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS notion_sources (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    meme_id     INTEGER NOT NULL
                                      REFERENCES memes(id) ON DELETE CASCADE,
                    data_source_id TEXT NOT NULL,
                    page_id     TEXT NOT NULL,
                    property_id TEXT NOT NULL,
                    file_index  INTEGER NOT NULL,
                    file_name   TEXT NOT NULL DEFAULT '',
                    page_last_edited_time TEXT NOT NULL DEFAULT '',
                    created_at  TEXT NOT NULL
                                      DEFAULT (datetime('now','localtime')),
                    updated_at  TEXT NOT NULL
                                      DEFAULT (datetime('now','localtime')),
                    UNIQUE (data_source_id, page_id, property_id,
                            file_index, file_name)
                );
                CREATE INDEX IF NOT EXISTS idx_notion_sources_meme
                    ON notion_sources(meme_id);
                CREATE TABLE IF NOT EXISTS chat_records (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id  TEXT NOT NULL,
                    source_message_id TEXT NOT NULL,
                    source_message_seq TEXT NOT NULL DEFAULT '',
                    message_type TEXT NOT NULL DEFAULT '',
                    peer_id     TEXT NOT NULL DEFAULT '',
                    category_id INTEGER DEFAULT NULL
                                REFERENCES categories(id) ON DELETE SET NULL,
                    title       TEXT NOT NULL DEFAULT '',
                    name        TEXT NOT NULL DEFAULT '',
                    media_folder TEXT NOT NULL DEFAULT '',
                    node_count  INTEGER NOT NULL DEFAULT 0,
                    record_count INTEGER NOT NULL DEFAULT 0,
                    package_json TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                                DEFAULT (datetime('now','localtime')),
                    updated_at  TEXT NOT NULL
                                DEFAULT (datetime('now','localtime')),
                    UNIQUE (account_id, source_message_id)
                );
                CREATE TABLE IF NOT EXISTS chat_tags (
                    chat_id INTEGER NOT NULL
                        REFERENCES chat_records(id) ON DELETE CASCADE,
                    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    PRIMARY KEY (chat_id, tag_id)
                );
                CREATE TABLE IF NOT EXISTS chat_collections (
                    chat_id INTEGER NOT NULL
                        REFERENCES chat_records(id) ON DELETE CASCADE,
                    collection_id INTEGER NOT NULL
                        REFERENCES collections(id) ON DELETE CASCADE,
                    sort_order    INTEGER DEFAULT 0,
                    PRIMARY KEY (chat_id, collection_id)
                );
                CREATE INDEX IF NOT EXISTS idx_chat_records_updated
                    ON chat_records(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_chat_tags_tag
                    ON chat_tags(tag_id);
                CREATE INDEX IF NOT EXISTS idx_chat_collections_collection
                    ON chat_collections(collection_id);
            """
        )
        # 早期版本把 OneBot 进程内短 message_id 误写入摘要序号列。
        # package_json 中的 real_seq 才能跨 NapCat 重启重新定位原消息；
        # 启动与备份恢复时都把已有记录的摘要修正为真实序号。
        seq_updates = []
        for row in conn.execute(
            "SELECT id, source_message_seq, package_json FROM chat_records"
        ).fetchall():
            try:
                package = json.loads(row[2])
                source = package.get("source") if isinstance(package, dict) else None
                real_seq = source.get("real_seq") if isinstance(source, dict) else None
            except (TypeError, json.JSONDecodeError):
                continue
            if real_seq not in (None, "") and str(real_seq) != str(row[1] or ""):
                seq_updates.append((str(real_seq), int(row[0])))
        if seq_updates:
            conn.executemany(
                "UPDATE chat_records SET source_message_seq=? WHERE id=?",
                seq_updates,
            )
        tag_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(tags)").fetchall()
        }
        if "category" not in tag_columns:
            # 旧表在 name 上有单列 UNIQUE，无法用 ALTER TABLE 移除。保留原 id
            # 重建父表，使同名标签可以存在于不同分类，meme_tags 的关联不变。
            foreign_keys_enabled = bool(
                conn.execute("PRAGMA foreign_keys").fetchone()[0]
            )
            conn.commit()
            conn.execute("PRAGMA foreign_keys=OFF")
            try:
                conn.executescript(
                    """
                    BEGIN;
                    CREATE TABLE tags_new (
                        id       INTEGER PRIMARY KEY AUTOINCREMENT,
                        name     TEXT NOT NULL COLLATE NOCASE,
                        category TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
                        UNIQUE (category, name)
                    );
                    INSERT INTO tags_new (id, name, category)
                        SELECT id, name, '' FROM tags;
                    DROP TABLE tags;
                    ALTER TABLE tags_new RENAME TO tags;
                    COMMIT;
                    """
                )
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise
            finally:
                conn.execute(
                    f"PRAGMA foreign_keys={'ON' if foreign_keys_enabled else 'OFF'}"
                )
        migrates = [
            ("memes", "sort_order", "INTEGER DEFAULT 0"),
            ("memes", "stego_of_hash", "TEXT DEFAULT NULL"),
            ("memes", "from_stego", "INTEGER DEFAULT 0"),
            ("memes", "perceptual_hash", "TEXT DEFAULT NULL"),
            ("memes", "ai_description", "TEXT NOT NULL DEFAULT ''"),
            ("memes", "ai_ocr_text", "TEXT NOT NULL DEFAULT ''"),
            ("memes", "manual_description", "TEXT NOT NULL DEFAULT ''"),
            ("memes", "manual_ocr_text", "TEXT NOT NULL DEFAULT ''"),
            (
                "memes",
                "category_id",
                "INTEGER DEFAULT NULL REFERENCES categories(id) ON DELETE SET NULL",
            ),
            ("memes", "daily_filename", "TEXT NOT NULL DEFAULT ''"),
            ("memes", "daily_file_size", "INTEGER DEFAULT 0"),
            ("memes", "daily_mime_type", "TEXT NOT NULL DEFAULT ''"),
            ("memes", "daily_profile", "INTEGER DEFAULT 0"),
            ("memes", "source_file_hash", "TEXT NOT NULL DEFAULT ''"),
            ("memes", "compressed_only", "INTEGER DEFAULT 0"),
            (
                "collections",
                "parent_id",
                "INTEGER DEFAULT NULL REFERENCES collections(id) ON DELETE CASCADE",
            ),
            ("collections", "sort_order", "INTEGER DEFAULT 0"),
            ("collections", "is_hidden", "INTEGER NOT NULL DEFAULT 0"),
            ("meme_collections", "sort_order", "INTEGER DEFAULT 0"),
            (
                "chat_records",
                "category_id",
                "INTEGER DEFAULT NULL REFERENCES categories(id) ON DELETE SET NULL",
            ),
            ("chat_records", "name", "TEXT NOT NULL DEFAULT ''"),
            ("chat_records", "media_folder", "TEXT NOT NULL DEFAULT ''"),
            ("chat_collections", "sort_order", "INTEGER DEFAULT 0"),
        ]
        for tbl, col, col_def in migrates:
            try:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {col_def}")
            except sqlite3.OperationalError:
                pass  # 列已存在
        # 以下索引依赖迁移新增的列，必须放在迁移之后创建；否则旧库尚未
        # 补齐 stego_of_hash/category_id 时，CREATE INDEX 会中断启动。
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memes_stego ON memes(stego_of_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memes_category ON memes(category_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memes_source_hash "
            "ON memes(source_file_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_records_category "
            "ON chat_records(category_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_collections_hidden_root "
            "ON collections(is_hidden) WHERE is_hidden=1"
        )
        # 旧聊天记录此前只有来源摘要 title，媒体目录名则按来源哈希隐式计算。
        # 在迁移阶段固化两者，之后用户修改不会被重新归档覆盖。
        rows = conn.execute(
            "SELECT id, account_id, source_message_id, title, name, media_folder "
            "FROM chat_records"
        ).fetchall()
        for row in rows:
            name = str(row[4] or row[3] or "聊天记录").strip() or "聊天记录"
            media_folder = str(row[5] or "").strip() or _default_chat_media_folder(
                row[1], row[2]
            )
            if name != str(row[4] or "") or media_folder != str(row[5] or ""):
                conn.execute(
                    "UPDATE chat_records SET name=?, media_folder=? WHERE id=?",
                    (name, media_folder, int(row[0])),
                )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_records_media_folder "
            "ON chat_records(media_folder COLLATE NOCASE)"
        )
        conn.commit()

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    def backup_to(self, path: str):
        """WAL 一致快照：经 sqlite backup API 把当前库导出到目标文件"""
        with self._lock:
            dest = sqlite3.connect(path)
            try:
                self._get_conn().backup(dest)
                dest.commit()
            finally:
                dest.close()

    def has_any_data(self) -> bool:
        """是否存在任何业务数据（memes/分组/标签/收藏，含 stego 载体行）

        供备份恢复的空库判定。
        """
        conn = self._get_conn()
        for tbl in (
            "memes",
            "collections",
            "categories",
            "tags",
            "favorites",
            "chat_records",
        ):
            if conn.execute(f"SELECT 1 FROM {tbl} LIMIT 1").fetchone():
                return True
        return False

    def prepare_restore_source(self, path: str):
        """候选备份库预校验：integrity_check + 全部表/必需列存在性 + 隔离连接上
        预迁移 + 孤儿外键检查。

        在 staging 文件的独立连接上操作，不触碰活动库；失败抛 ValueError。
        """
        conn = sqlite3.connect(path)
        # Row 按列名取值：foreign_key_list 的 on_delete 列序存在版本歧义
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise ValueError(f"integrity_check: {row[0] if row else 'unknown'}")
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            missing_tables = [t for t in _RESTORE_REQUIRED_TABLES if t not in tables]
            if missing_tables:
                raise ValueError("缺少必需的数据表: " + ", ".join(missing_tables))
            self._migrate(conn)
            for tbl, cols in _RESTORE_REQUIRED_COLUMNS.items():
                have = {
                    r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()
                }
                missing_cols = cols - have
                if missing_cols:
                    raise ValueError(
                        f"表 {tbl} 缺少必需列: " + ", ".join(sorted(missing_cols))
                    )
            # 外键定义必须存在且带 ON DELETE CASCADE（缺失则级联删除失效）
            for tbl, required in _RESTORE_REQUIRED_FOREIGN_KEYS.items():
                actual = set()
                for r in conn.execute(f"PRAGMA foreign_key_list({tbl})").fetchall():
                    parent = r["table"]
                    col_from = r["from"]
                    col_to = r["to"]
                    on_delete = r["on_delete"]
                    if col_to is None:  # 隐式引用父表主键
                        pk = [
                            c[1]
                            for c in conn.execute(
                                f"PRAGMA table_info({parent})"
                            ).fetchall()
                            if c[5] > 0
                        ]
                        col_to = pk[0] if pk else None
                    actual.add((col_from, parent, col_to, on_delete))
                missing_fks = required - actual
                if missing_fks:
                    raise ValueError(f"表 {tbl} 缺少必需的外键定义")
            fk_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk_issues:
                raise ValueError(f"存在 {len(fk_issues)} 条孤儿外键记录")
            conn.commit()
        finally:
            conn.close()

    def restore_from(self, path: str):
        """用备份库原子替换当前库内容（backup API，绕过 SQL 层），并补齐旧版本缺失列

        获取 _lock 后、替换前复查空库：所有 MemeDB 写入（含未走 _IMPORT_LOCK 的
        rescan）都在同一把锁上串行，检查与替换因此原子，绕过前置检查的竞态窗口
        不可能得手；非空时抛 ValueError 交由 restore_backup 回滚缓存文件。
        """
        with self._lock:
            if self.has_any_data():
                raise ValueError("恢复期间检测到新数据写入，已中止")
            src = sqlite3.connect(path)
            try:
                src.backup(self._get_conn())
            finally:
                src.close()
            conn = self._get_conn()
            self._migrate(conn)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()

    # --- 增删改 ---

    def add_meme(
        self,
        filename: str,
        file_hash: str = "",
        source_file_hash: str = "",
        compressed_only: int = 0,
        width: int = 0,
        height: int = 0,
        file_size: int = 0,
        mime_type: str = "image/png",
        original_name: str = "",
        tags: List[Any] = None,
        stego_of_hash: str = None,
        from_stego: int = 0,
        perceptual_hash: int = None,
        ai_description: str = "",
        ai_ocr_text: str = "",
        manual_description: str = "",
        manual_ocr_text: str = "",
        daily_filename: str = "",
        daily_file_size: int = 0,
        daily_mime_type: str = "",
        daily_profile: int = 0,
        category_id: Any = None,
    ) -> int:
        with self._lock:
            conn = self._get_conn()
            if perceptual_hash:
                ph_hex = hex(perceptual_hash)
            elif perceptual_hash == 0:
                ph_hex = "0"  # 已计算但无感知内容：占位，避免反复回填
            else:
                ph_hex = None
            cur = conn.execute(
                """INSERT INTO memes
                   (filename, file_hash, source_file_hash, compressed_only,
                    width, height, file_size, mime_type, original_name,
                    stego_of_hash, from_stego,
                    perceptual_hash, ai_description, ai_ocr_text,
                    manual_description, manual_ocr_text,
                    category_id,
                    daily_filename, daily_file_size, daily_mime_type, daily_profile)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?)""",
                (
                    filename,
                    file_hash,
                    source_file_hash or "",
                    1 if compressed_only else 0,
                    width,
                    height,
                    file_size,
                    mime_type,
                    original_name,
                    stego_of_hash,
                    from_stego,
                    ph_hex,
                    ai_description or "",
                    ai_ocr_text or "",
                    manual_description or "",
                    manual_ocr_text or "",
                    (
                        int(category_id)
                        if category_id is not None and category_id is not _UNSET
                        else None
                    ),
                    daily_filename or "",
                    int(daily_file_size or 0),
                    daily_mime_type or "",
                    int(daily_profile or 0),
                ),
            )
            meme_id = cur.lastrowid
            conn.commit()  # 先提交meme插入，确保FOREIGN KEY约束通过
            if tags:
                self._set_tags(conn, meme_id, tags)
                conn.commit()
            return meme_id

    def delete_memes(self, meme_ids: List[int]):
        """批量删除表情（单锁单事务），一次性清理孤儿标签"""
        if not meme_ids:
            return
        with self._lock:
            conn = self._get_conn()
            placeholders = ",".join("?" for _ in meme_ids)
            conn.execute(f"DELETE FROM memes WHERE id IN ({placeholders})", meme_ids)
            self._prune_orphan_tags(conn)
            conn.commit()

    def delete_meme(self, meme_id: int):
        self.delete_memes([meme_id])

    def get_notion_source(
        self,
        data_source_id: str,
        page_id: str,
        property_id: str,
        file_index: int,
        file_name: str,
        page_last_edited_time: str,
    ) -> Optional[dict]:
        """按稳定来源与页面版本查找已导入的 Notion 文件。"""
        if not page_last_edited_time:
            return None
        conn = self._get_conn()
        row = conn.execute(
            """SELECT ns.meme_id, ns.page_last_edited_time,
                      m.filename, m.file_hash
               FROM notion_sources ns
               JOIN memes m ON m.id = ns.meme_id
               WHERE ns.data_source_id=? AND ns.page_id=?
                 AND ns.property_id=? AND ns.file_index=? AND ns.file_name=?
                 AND ns.page_last_edited_time=?
               LIMIT 1""",
            (
                data_source_id,
                page_id,
                property_id,
                int(file_index),
                file_name,
                page_last_edited_time,
            ),
        ).fetchone()
        return dict(row) if row else None

    def upsert_notion_source(
        self,
        meme_id: int,
        data_source_id: str,
        page_id: str,
        property_id: str,
        file_index: int,
        file_name: str,
        page_last_edited_time: str,
    ):
        """记录 Notion 文件到本地表情的映射，页面修改后更新版本。"""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO notion_sources
                   (meme_id, data_source_id, page_id, property_id,
                    file_index, file_name, page_last_edited_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(data_source_id, page_id, property_id,
                               file_index, file_name)
                   DO UPDATE SET meme_id=excluded.meme_id,
                       page_last_edited_time=excluded.page_last_edited_time,
                       updated_at=datetime('now','localtime')""",
                (
                    int(meme_id),
                    data_source_id,
                    page_id,
                    property_id,
                    int(file_index),
                    file_name,
                    page_last_edited_time,
                ),
            )
            conn.commit()

    def save_chat_record(self, package: dict, record_id: int = None) -> dict:
        """保存完整聊天记录包；重复保存同一外层消息时更新原记录。"""
        if not isinstance(package, dict):
            raise ValueError("聊天记录包格式无效")
        source = package.get("source")
        summary = package.get("summary")
        if not isinstance(source, dict) or not isinstance(summary, dict):
            raise ValueError("聊天记录包缺少来源或摘要")
        account_id = str(source.get("self_id") or "")
        message_id = str(source.get("message_id") or "")
        if not account_id or not message_id:
            raise ValueError("聊天记录包缺少账号或消息 ID")
        if str(source.get("message_type") or "") == "group":
            peer_id = source.get("group_id")
        else:
            peer_id = source.get("target_id")
            if str(peer_id or "") in ("", account_id):
                peer_id = source.get("user_id")
            if str(peer_id or "") in ("", account_id):
                sender = source.get("sender")
                sender = sender if isinstance(sender, dict) else {}
                peer_id = sender.get("user_id")
        source_title = str(package.get("title") or "聊天记录").strip() or "聊天记录"
        with self._lock:
            conn = self._get_conn()
            columns = (
                "id, name, media_folder, package_json, account_id, source_message_id"
            )
            if record_id is None:
                existing = conn.execute(
                    f"SELECT {columns} FROM chat_records "
                    "WHERE account_id=? AND source_message_id=?",
                    (account_id, message_id),
                ).fetchone()
            else:
                existing = conn.execute(
                    f"SELECT {columns} FROM chat_records WHERE id=?",
                    (int(record_id),),
                ).fetchone()
                if existing is None:
                    raise ValueError("聊天记录不存在")
                account_id, message_id = existing[4], existing[5]
            from .onebot import chat_record_sources

            package["source_locations"] = chat_record_sources(
                json.loads(existing[3]) if existing is not None else {}, package
            )
            name = (
                str(existing[1] or "").strip() if existing is not None else ""
            ) or source_title
            media_folder = (
                str(existing[2] or "").strip() if existing is not None else ""
            ) or _default_chat_media_folder(account_id, message_id)
            package["_omm_media_folder"] = media_folder
            package_json = json.dumps(
                package, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            )
            conn.execute(
                """INSERT INTO chat_records
                   (account_id, source_message_id, source_message_seq,
                    message_type, peer_id, title, name, media_folder,
                    node_count, record_count, package_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(account_id, source_message_id) DO UPDATE SET
                       source_message_seq=excluded.source_message_seq,
                       message_type=excluded.message_type,
                       peer_id=excluded.peer_id,
                       title=excluded.title,
                       name=CASE WHEN chat_records.name='' THEN excluded.name
                                 ELSE chat_records.name END,
                       media_folder=CASE
                           WHEN chat_records.media_folder='' THEN excluded.media_folder
                           ELSE chat_records.media_folder END,
                       node_count=excluded.node_count,
                       record_count=excluded.record_count,
                       package_json=excluded.package_json,
                       updated_at=datetime('now','localtime')""",
                (
                    account_id,
                    message_id,
                    str(source.get("real_seq") or source.get("message_seq") or ""),
                    str(source.get("message_type") or ""),
                    str(peer_id or ""),
                    source_title,
                    name,
                    media_folder,
                    int(summary.get("node_count") or 0),
                    int(summary.get("record_count") or 0),
                    package_json,
                ),
            )
            row = conn.execute(
                "SELECT id FROM chat_records "
                "WHERE account_id=? AND source_message_id=?",
                (account_id, message_id),
            ).fetchone()
            conn.commit()
        return {"id": row[0], "created": existing is None}

    def add_chat_record_source(
        self, record_id: int, source: dict, used_source: dict = None
    ) -> bool:
        """原子追加或更新原生副本位置，不覆盖正文及用户管理信息。"""
        from .onebot import chat_record_sources

        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT package_json FROM chat_records WHERE id=?", (int(record_id),)
            ).fetchone()
            if row is None:
                return False
            package = json.loads(row[0])
            location = dict(source)
            location["last_success_at"] = int(time.time() * 1000)
            used = dict(used_source) if used_source else {}
            used["last_success_at"] = location["last_success_at"] - 1
            package["source_locations"] = chat_record_sources(
                package, {"source": used}, {"source": location}
            )
            try:
                conn.execute(
                    "UPDATE chat_records SET package_json=? WHERE id=?",
                    (
                        json.dumps(package, ensure_ascii=False, separators=(",", ":")),
                        int(record_id),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return True

    def get_chat_record(self, record_id: int) -> Optional[dict]:
        """读取一个完整聊天记录包。"""
        row = (
            self._get_conn()
            .execute(
                "SELECT package_json, media_folder FROM chat_records WHERE id=?",
                (int(record_id),),
            )
            .fetchone()
        )
        if not row:
            return None
        package = json.loads(row[0])
        package["_omm_media_folder"] = str(row[1] or "")
        return package

    def get_chat_record_by_source(
        self, account_id: str, source_message_id: str
    ) -> Optional[dict]:
        """按账号与外层消息 ID 读取已有聊天记录包。"""
        row = (
            self._get_conn()
            .execute(
                "SELECT package_json, media_folder FROM chat_records "
                "WHERE account_id=? AND source_message_id=?",
                (str(account_id), str(source_message_id)),
            )
            .fetchone()
        )
        if not row:
            return None
        package = json.loads(row[0])
        package["_omm_media_folder"] = str(row[1] or "")
        return package

    def get_chat_record_entry(self, record_id: int) -> Optional[dict]:
        """读取聊天记录摘要及完整结构包。"""
        row = (
            self._get_conn()
            .execute("SELECT * FROM chat_records WHERE id=?", (int(record_id),))
            .fetchone()
        )
        if not row:
            return None
        result = dict(row)
        result["package"] = json.loads(result.pop("package_json"))
        result["package"]["_omm_media_folder"] = str(result.get("media_folder") or "")
        return result

    def get_chat_record_by_media_folder(self, media_folder: str) -> Optional[dict]:
        row = (
            self._get_conn()
            .execute(
                "SELECT id, name, media_folder FROM chat_records "
                "WHERE media_folder=? COLLATE NOCASE LIMIT 1",
                (str(media_folder),),
            )
            .fetchone()
        )
        return dict(row) if row else None

    def _chat_record_where(
        self,
        keyword: str = "",
        tags=None,
        collection_id=None,
        category_id=None,
        uncategorized_category: bool = False,
        uncategorized_only: bool = False,
        exclude_collection_ids=None,
    ):
        """构造聊天记录的统一筛选条件。

        聊天记录使用独立关系表，但标签、分组和类别本身与表情共享，
        因此这里与 ``search`` 的语义保持一致，供列表、统计和统一库调用。
        """
        where = []
        params = []
        if keyword:
            kw = f"%{keyword}%"
            where.append(
                "(c.name LIKE ? OR c.media_folder LIKE ? OR c.title LIKE ? OR "
                "c.peer_id LIKE ? OR c.source_message_id LIKE ? OR c.id IN ("
                "SELECT ct.chat_id FROM chat_tags ct JOIN tags t ON t.id=ct.tag_id "
                "WHERE t.name LIKE ?))"
            )
            params.extend([kw, kw, kw, kw, kw, kw])

        normalized_tags = self._normalize_tags(tags)
        if normalized_tags:
            conditions = " OR ".join(
                "(t.category=? COLLATE NOCASE AND t.name=? COLLATE NOCASE)"
                for _ in normalized_tags
            )
            where.append(
                "c.id IN (SELECT ct.chat_id FROM chat_tags ct "
                "JOIN tags t ON t.id=ct.tag_id WHERE "
                f"{conditions} GROUP BY ct.chat_id "
                "HAVING COUNT(DISTINCT t.id)=?)"
            )
            for category, name in normalized_tags:
                params.extend([category, name])
            params.append(len(normalized_tags))

        if collection_id is not None:
            try:
                if isinstance(collection_id, list):
                    collection_ids = [int(value) for value in collection_id]
                else:
                    collection_ids = [int(collection_id)]
            except (TypeError, ValueError):
                return ["1=0"], []
            collection_ids = [value for value in collection_ids if value > 0]
            if collection_ids:
                placeholders = ",".join("?" for _ in collection_ids)
                where.append(
                    "c.id IN (SELECT cc.chat_id FROM chat_collections cc "
                    f"WHERE cc.collection_id IN ({placeholders}))"
                )
                params.extend(collection_ids)
            else:
                return ["1=0"], []
        if uncategorized_only:
            where.append(
                "NOT EXISTS (SELECT 1 FROM chat_collections cc "
                "WHERE cc.chat_id=c.id)"
            )

        excluded_ids = list(
            dict.fromkeys(int(value) for value in (exclude_collection_ids or []))
        )
        if excluded_ids:
            placeholders = ",".join("?" for _ in excluded_ids)
            where.append(
                "NOT EXISTS (SELECT 1 FROM chat_collections hidden_cc "
                "WHERE hidden_cc.chat_id=c.id "
                f"AND hidden_cc.collection_id IN ({placeholders}))"
            )
            params.extend(excluded_ids)

        if uncategorized_category:
            where.append("c.category_id IS NULL")
        elif category_id is not None:
            category_ids = (
                category_id if isinstance(category_id, list) else [category_id]
            )
            try:
                category_ids = [int(value) for value in category_ids]
            except (TypeError, ValueError):
                return ["1=0"], []
            category_ids = [value for value in category_ids if value > 0]
            if category_ids:
                placeholders = ",".join("?" for _ in category_ids)
                where.append(f"c.category_id IN ({placeholders})")
                params.extend(category_ids)
            else:
                return ["1=0"], []
        return where, params

    def list_chat_records(
        self,
        keyword: str = "",
        limit: int = 100,
        offset: int = 0,
        tags=None,
        collection_id=None,
        category_id=None,
        uncategorized_category: bool = False,
        uncategorized_only: bool = False,
        exclude_collection_ids=None,
    ) -> List[dict]:
        """按更新时间分页读取聊天记录摘要，并支持统一标签筛选。"""
        where, params = self._chat_record_where(
            keyword,
            tags=tags,
            collection_id=collection_id,
            category_id=category_id,
            uncategorized_category=uncategorized_category,
            uncategorized_only=uncategorized_only,
            exclude_collection_ids=exclude_collection_ids,
        )
        sql = (
            "SELECT c.id, c.title, c.name, c.media_folder, c.node_count, "
            "c.record_count, c.message_type, "
            "c.peer_id, c.source_message_id, c.source_message_seq, "
            "c.category_id, c.created_at, c.updated_at FROM chat_records c"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY c.updated_at DESC, c.id DESC LIMIT ? OFFSET ?"
        params.extend([max(0, int(limit)), max(0, int(offset))])
        rows = self._get_conn().execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def count_chat_records(
        self,
        keyword: str = "",
        tags=None,
        collection_id=None,
        category_id=None,
        uncategorized_category: bool = False,
        uncategorized_only: bool = False,
        exclude_collection_ids=None,
    ) -> int:
        """返回符合统一筛选条件的已保存聊天记录数量。"""
        where, params = self._chat_record_where(
            keyword,
            tags=tags,
            collection_id=collection_id,
            category_id=category_id,
            uncategorized_category=uncategorized_category,
            uncategorized_only=uncategorized_only,
            exclude_collection_ids=exclude_collection_ids,
        )
        sql = "SELECT COUNT(*) FROM chat_records c"
        if where:
            sql += " WHERE " + " AND ".join(where)
        row = self._get_conn().execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def delete_chat_record(self, record_id: int) -> bool:
        """删除一份聊天记录归档。"""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("DELETE FROM chat_records WHERE id=?", (int(record_id),))
            if cur.rowcount:
                self._prune_orphan_tags(conn)
            conn.commit()
            return cur.rowcount > 0

    def get_latest_chat_record(self) -> Optional[dict]:
        """返回最近保存记录的轻量摘要。"""
        row = (
            self._get_conn()
            .execute(
                """SELECT id, title, name, media_folder, node_count,
                          record_count, updated_at
               FROM chat_records ORDER BY updated_at DESC, id DESC LIMIT 1"""
            )
            .fetchone()
        )
        return dict(row) if row else None

    def update_meme(self, meme_id: int, **kwargs):
        allowed = {
            "filename",
            "file_hash",
            "source_file_hash",
            "compressed_only",
            "width",
            "height",
            "file_size",
            "mime_type",
            "original_name",
            "stego_of_hash",
            "from_stego",
            "ai_description",
            "ai_ocr_text",
            "manual_description",
            "manual_ocr_text",
            "daily_filename",
            "daily_file_size",
            "daily_mime_type",
            "daily_profile",
            # perceptual_hash 不在此列：写统一走 set_perceptual_hash（hex 序列化）
        }
        sets = []
        vals = []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return
        sets.append("updated_at=datetime('now','localtime')")
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                f"UPDATE memes SET {', '.join(sets)} WHERE id=?", (*vals, meme_id)
            )
            conn.commit()

    # --- 标签 ---

    def _prune_orphan_tags(self, conn):
        """清理未被表情或聊天记录使用的孤儿标签"""
        conn.execute(
            """DELETE FROM tags WHERE id NOT IN (
                SELECT DISTINCT tag_id FROM meme_tags
                UNION
                SELECT DISTINCT tag_id FROM chat_tags
            )"""
        )

    @staticmethod
    def _normalize_tag(tag: Any) -> Optional[Tuple[str, str]]:
        """把旧字符串标签和新版结构化标签统一为 (category, name)。"""
        if isinstance(tag, dict):
            name = str(tag.get("name") or "").strip()
            category = str(tag.get("category") or "").strip()
        else:
            name = str(tag or "").strip()
            category = ""
        return (category, name) if name else None

    @classmethod
    def _normalize_tags(cls, tags) -> List[Tuple[str, str]]:
        normalized = []
        seen = set()
        for tag in tags or []:
            value = cls._normalize_tag(tag)
            if value is None:
                continue
            key = (value[0].casefold(), value[1].casefold())
            if key not in seen:
                normalized.append(value)
                seen.add(key)
        return normalized

    @staticmethod
    def _tag_record(row) -> dict:
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "category": row["category"] or "",
        }

    def _set_tags(self, conn, meme_id: int, tags):
        conn.execute("DELETE FROM meme_tags WHERE meme_id=?", (meme_id,))
        for category, name in self._normalize_tags(tags):
            # INSERT OR IGNORE 后 lastrowid 不可靠（已有tag时返回上次真实插入的rowid）
            conn.execute(
                "INSERT OR IGNORE INTO tags (name, category) VALUES (?, ?)",
                (name, category),
            )
            row = conn.execute(
                "SELECT id FROM tags WHERE name=? AND category=?", (name, category)
            ).fetchone()
            if row is None:
                continue
            tag_id = row[0]
            conn.execute(
                "INSERT OR IGNORE INTO meme_tags (meme_id, tag_id) VALUES (?, ?)",
                (meme_id, tag_id),
            )
        self._prune_orphan_tags(conn)

    def set_meme_tags(self, meme_id: int, tags):
        with self._lock:
            conn = self._get_conn()
            self._set_tags(conn, meme_id, tags)
            conn.commit()

    def _set_chat_tags(self, conn, chat_id: int, tags):
        conn.execute("DELETE FROM chat_tags WHERE chat_id=?", (chat_id,))
        for category, name in self._normalize_tags(tags):
            conn.execute(
                "INSERT OR IGNORE INTO tags (name, category) VALUES (?, ?)",
                (name, category),
            )
            row = conn.execute(
                "SELECT id FROM tags WHERE name=? AND category=?", (name, category)
            ).fetchone()
            if row is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO chat_tags (chat_id, tag_id) VALUES (?, ?)",
                    (chat_id, row[0]),
                )
        self._prune_orphan_tags(conn)

    def set_chat_tags(self, chat_id: int, tags):
        with self._lock:
            conn = self._get_conn()
            if (
                conn.execute(
                    "SELECT 1 FROM chat_records WHERE id=?", (int(chat_id),)
                ).fetchone()
                is None
            ):
                return False
            self._set_chat_tags(conn, int(chat_id), tags)
            conn.commit()
            return True

    def set_daily_asset(
        self,
        meme_id: int,
        filename: str,
        file_size: int,
        mime_type: str,
        profile: int,
    ):
        """记录可再生日常副本，不改变业务更新时间和排序。"""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """UPDATE memes
                   SET daily_filename=?, daily_file_size=?,
                       daily_mime_type=?, daily_profile=?
                   WHERE id=?""",
                (
                    filename or "",
                    int(file_size or 0),
                    mime_type or "",
                    int(profile or 0),
                    meme_id,
                ),
            )
            conn.commit()

    def rename_tag(
        self,
        old_name: str,
        new_name: str,
        category: str = "",
        new_category: str = None,
    ) -> Optional[dict]:
        """全局修改标签名称/种类；目标已存在时合并全部关联。"""
        old_name = old_name.strip()
        new_name = new_name.strip()
        category = str(category or "").strip()
        target_category = (
            category if new_category is None else str(new_category or "").strip()
        )
        if not old_name or not new_name:
            return None
        with self._lock:
            conn = self._get_conn()
            source = conn.execute(
                "SELECT id, name, category FROM tags WHERE name=? AND category=?",
                (old_name, category),
            ).fetchone()
            if source is None:
                return None
            try:
                target = conn.execute(
                    "SELECT id, name, category FROM tags "
                    "WHERE name=? AND category=?",
                    (new_name, target_category),
                ).fetchone()
                if target is not None and target["id"] != source["id"]:
                    conn.execute(
                        """INSERT OR IGNORE INTO meme_tags (meme_id, tag_id)
                           SELECT meme_id, ? FROM meme_tags WHERE tag_id=?""",
                        (target["id"], source["id"]),
                    )
                    conn.execute(
                        """INSERT OR IGNORE INTO chat_tags (chat_id, tag_id)
                           SELECT chat_id, ? FROM chat_tags WHERE tag_id=?""",
                        (target["id"], source["id"]),
                    )
                    conn.execute("DELETE FROM tags WHERE id=?", (source["id"],))
                    result = {
                        "name": target["name"],
                        "category": target["category"],
                        "merged": True,
                    }
                else:
                    conn.execute(
                        "UPDATE tags SET name=?, category=? WHERE id=?",
                        (new_name, target_category, source["id"]),
                    )
                    result = {
                        "name": new_name,
                        "category": target_category,
                        "merged": False,
                    }
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    def update_meme_details(
        self,
        meme_id: int,
        tags,
        ai_description: str,
        ai_ocr_text: str,
        title: str = None,
        filename: str = None,
        collection_ids: List[int] = None,
        manual_description: str = None,
        manual_ocr_text: str = None,
        category_id: Any = _UNSET,
    ) -> bool:
        """在同一事务内保存详情文字和标签，目标不存在时不产生孤儿标签。"""
        with self._lock:
            conn = self._get_conn()
            meme_row = conn.execute(
                "SELECT compressed_only FROM memes WHERE id=?", (meme_id,)
            ).fetchone()
            if meme_row is None:
                return False
            try:
                fields = [
                    "ai_description=?",
                    "ai_ocr_text=?",
                    "updated_at=datetime('now','localtime')",
                ]
                values = [ai_description, ai_ocr_text]
                if manual_description is not None:
                    fields.append("manual_description=?")
                    values.append(manual_description)
                if manual_ocr_text is not None:
                    fields.append("manual_ocr_text=?")
                    values.append(manual_ocr_text)
                if title is not None:
                    fields.append("original_name=?")
                    values.append(title)
                if filename is not None:
                    fields.append("filename=?")
                    values.append(filename)
                    if bool(meme_row["compressed_only"]):
                        fields.append("daily_filename=?")
                        values.append(filename)
                if category_id is not _UNSET:
                    # None is a valid value here: it explicitly means「未分类」.
                    fields.append("category_id=?")
                    values.append(category_id)
                values.append(meme_id)
                conn.execute(
                    f"UPDATE memes SET {', '.join(fields)} WHERE id=?",
                    values,
                )
                self._set_tags(conn, meme_id, tags)
                if collection_ids is not None:
                    target_ids = list(
                        dict.fromkeys(int(value) for value in collection_ids)
                    )
                    if target_ids:
                        placeholders = ",".join("?" for _ in target_ids)
                        valid_ids = {
                            row[0]
                            for row in conn.execute(
                                f"SELECT id FROM collections "
                                f"WHERE id IN ({placeholders})",
                                target_ids,
                            ).fetchall()
                        }
                        if valid_ids != set(target_ids):
                            raise ValueError("collection not found")
                    current = {
                        row[0]: row[1]
                        for row in conn.execute(
                            "SELECT collection_id, sort_order "
                            "FROM meme_collections WHERE meme_id=?",
                            (meme_id,),
                        ).fetchall()
                    }
                    for collection_id in set(current) - set(target_ids):
                        conn.execute(
                            "DELETE FROM meme_collections "
                            "WHERE meme_id=? AND collection_id=?",
                            (meme_id, collection_id),
                        )
                    for collection_id in set(target_ids) - set(current):
                        next_order = conn.execute(
                            "SELECT COALESCE(MAX(sort_order), -1) + 1 "
                            "FROM meme_collections WHERE collection_id=?",
                            (collection_id,),
                        ).fetchone()[0]
                        conn.execute(
                            "INSERT INTO meme_collections "
                            "(meme_id, collection_id, sort_order) VALUES (?, ?, ?)",
                            (meme_id, collection_id, next_order),
                        )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def get_meme_tags(self, meme_id: int) -> List[str]:
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT t.name FROM tags t
               JOIN meme_tags mt ON mt.tag_id = t.id
               WHERE mt.meme_id = ?""",
            (meme_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_meme_tag_records(self, meme_id: int) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT t.id, t.name, t.category FROM tags t
               JOIN meme_tags mt ON mt.tag_id = t.id
               WHERE mt.meme_id = ?
               ORDER BY t.category, t.name""",
            (meme_id,),
        ).fetchall()
        return [self._tag_record(row) for row in rows]

    def get_chat_tags(self, chat_id: int) -> List[str]:
        rows = (
            self._get_conn()
            .execute(
                """SELECT t.name FROM tags t
               JOIN chat_tags ct ON ct.tag_id=t.id
               WHERE ct.chat_id=? ORDER BY t.category, t.name""",
                (int(chat_id),),
            )
            .fetchall()
        )
        return [row[0] for row in rows]

    def get_chat_tag_records(self, chat_id: int) -> List[dict]:
        rows = (
            self._get_conn()
            .execute(
                """SELECT t.id, t.name, t.category FROM tags t
               JOIN chat_tags ct ON ct.tag_id=t.id
               WHERE ct.chat_id=? ORDER BY t.category, t.name""",
                (int(chat_id),),
            )
            .fetchall()
        )
        return [self._tag_record(row) for row in rows]

    def update_chat_record_details(
        self,
        chat_id: int,
        tags,
        collection_ids: List[int] = None,
        category_id: Any = _UNSET,
        blocked_message_paths: Any = _UNSET,
        name: Any = _UNSET,
        media_folder: Any = _UNSET,
        package: Any = _UNSET,
    ) -> bool:
        """在同一事务内保存聊天记录管理信息和发送屏蔽规则。"""
        chat_id = int(chat_id)
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT package_json, name, media_folder FROM chat_records WHERE id=?",
                (chat_id,),
            ).fetchone()
            if row is None:
                return False
            try:
                if name is not _UNSET:
                    conn.execute(
                        "UPDATE chat_records SET name=? WHERE id=?",
                        (str(name), chat_id),
                    )
                if media_folder is not _UNSET:
                    duplicate = conn.execute(
                        "SELECT 1 FROM chat_records "
                        "WHERE media_folder=? COLLATE NOCASE AND id<>?",
                        (str(media_folder), chat_id),
                    ).fetchone()
                    if duplicate:
                        raise ValueError("media folder already exists")
                    conn.execute(
                        "UPDATE chat_records SET media_folder=? WHERE id=?",
                        (str(media_folder), chat_id),
                    )
                if category_id is not _UNSET:
                    if (
                        category_id is not None
                        and conn.execute(
                            "SELECT 1 FROM categories WHERE id=?", (int(category_id),)
                        ).fetchone()
                        is None
                    ):
                        raise ValueError("category not found")
                    conn.execute(
                        "UPDATE chat_records SET category_id=? WHERE id=?",
                        (category_id, chat_id),
                    )
                package_changed = package is not _UNSET
                if package is _UNSET:
                    stored_package = json.loads(row[0])
                elif not isinstance(package, dict):
                    raise ValueError("chat record package invalid")
                else:
                    stored_package = package
                    from .onebot import chat_record_sources

                    stored_package["source_locations"] = chat_record_sources(
                        json.loads(row[0]), stored_package
                    )
                if media_folder is not _UNSET:
                    stored_package["_omm_media_folder"] = str(media_folder)
                    package_changed = True
                if blocked_message_paths is not _UNSET:
                    from .onebot import set_chat_record_blocked_paths

                    set_chat_record_blocked_paths(stored_package, blocked_message_paths)
                    package_changed = True
                if package_changed:
                    package_json = json.dumps(
                        stored_package,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    conn.execute(
                        "UPDATE chat_records SET package_json=? WHERE id=?",
                        (package_json, chat_id),
                    )
                self._set_chat_tags(conn, chat_id, tags)
                if collection_ids is not None:
                    target_ids = list(
                        dict.fromkeys(int(value) for value in collection_ids)
                    )
                    if any(value <= 0 for value in target_ids):
                        raise ValueError("collection not found")
                    if target_ids:
                        placeholders = ",".join("?" for _ in target_ids)
                        valid_ids = {
                            row[0]
                            for row in conn.execute(
                                "SELECT id FROM collections "
                                f"WHERE id IN ({placeholders})",
                                target_ids,
                            ).fetchall()
                        }
                        if valid_ids != set(target_ids):
                            raise ValueError("collection not found")
                    current = {
                        row[0]: row[1]
                        for row in conn.execute(
                            "SELECT collection_id, sort_order FROM chat_collections "
                            "WHERE chat_id=?",
                            (chat_id,),
                        ).fetchall()
                    }
                    for collection_id in set(current) - set(target_ids):
                        conn.execute(
                            "DELETE FROM chat_collections WHERE chat_id=? "
                            "AND collection_id=?",
                            (chat_id, collection_id),
                        )
                    for collection_id in set(target_ids) - set(current):
                        next_order = conn.execute(
                            "SELECT COALESCE(MAX(sort_order), -1) + 1 "
                            "FROM chat_collections WHERE collection_id=?",
                            (collection_id,),
                        ).fetchone()[0]
                        conn.execute(
                            "INSERT INTO chat_collections "
                            "(chat_id, collection_id, sort_order) VALUES (?, ?, ?)",
                            (chat_id, collection_id, next_order),
                        )
                conn.execute(
                    "UPDATE chat_records SET updated_at=datetime('now','localtime') "
                    "WHERE id=?",
                    (chat_id,),
                )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def get_all_tags(self) -> List[str]:
        conn = self._get_conn()
        return [
            r[0] for r in conn.execute("SELECT name FROM tags ORDER BY name").fetchall()
        ]

    def get_all_tag_records(self) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, name, category FROM tags ORDER BY category, name"
        ).fetchall()
        return [self._tag_record(row) for row in rows]

    def get_meme_navigation(self, meme_id: int) -> Optional[dict]:
        """按全图库默认顺序返回目标表情的前后项和当前位置。"""
        conn = self._get_conn()
        row = conn.execute(
            """WITH ordered AS (
                   SELECT id,
                          LAG(id) OVER (
                              ORDER BY sort_order ASC, id DESC
                          ) AS previous_id,
                          LEAD(id) OVER (
                              ORDER BY sort_order ASC, id DESC
                          ) AS next_id,
                          ROW_NUMBER() OVER (
                              ORDER BY sort_order ASC, id DESC
                          ) AS position,
                          COUNT(*) OVER () AS total
                   FROM memes
                   WHERE stego_of_hash IS NULL OR stego_of_hash = ''
               )
               SELECT previous_id, next_id, position, total
               FROM ordered WHERE id=?""",
            (meme_id,),
        ).fetchone()
        return dict(row) if row else None

    def _existing_meme_ids(self, conn, ids: List[int]) -> List[int]:
        """过滤出实际存在的表情 id（外键开启时对缺失 id 写子表会整批失败）"""
        placeholders = ",".join("?" for _ in ids)
        return [
            r[0]
            for r in conn.execute(
                f"SELECT id FROM memes WHERE id IN ({placeholders})", ids
            ).fetchall()
        ]

    def add_tags_to_memes(self, meme_ids: List[int], tags) -> int:
        """批量合并追加标签（不清空各表情已有标签），返回实际存在的表情数"""
        normalized_tags = self._normalize_tags(tags)
        ids = list(dict.fromkeys(int(x) for x in (meme_ids or [])))
        if not normalized_tags or not ids:
            return 0
        with self._lock:
            conn = self._get_conn()
            try:
                valid_ids = self._existing_meme_ids(conn, ids)
                if not valid_ids:
                    return 0
                for category, name in normalized_tags:
                    # INSERT OR IGNORE 后 lastrowid 不可靠（同 _set_tags）
                    conn.execute(
                        "INSERT OR IGNORE INTO tags (name, category) VALUES (?, ?)",
                        (name, category),
                    )
                    row = conn.execute(
                        "SELECT id FROM tags WHERE name=? AND category=?",
                        (name, category),
                    ).fetchone()
                    if row is None:
                        continue
                    for mid in valid_ids:
                        conn.execute(
                            "INSERT OR IGNORE INTO meme_tags "
                            "(meme_id, tag_id) VALUES (?, ?)",
                            (mid, row[0]),
                        )
                conn.commit()
                return len(valid_ids)
            except Exception:
                conn.rollback()
                raise

    # --- 收藏 ---

    def toggle_favorite(self, meme_id: int) -> bool:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT 1 FROM favorites WHERE meme_id=?", (meme_id,)
            ).fetchone()
            if row:
                conn.execute("DELETE FROM favorites WHERE meme_id=?", (meme_id,))
                fav = False
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO favorites (meme_id) VALUES (?)", (meme_id,)
                )
                fav = True
            conn.commit()
            return fav

    def is_favorite(self, meme_id: int) -> bool:
        conn = self._get_conn()
        return (
            conn.execute(
                "SELECT 1 FROM favorites WHERE meme_id=?", (meme_id,)
            ).fetchone()
            is not None
        )

    # --- 单归属类别 ---

    def _category_rows(self, conn):
        return conn.execute(
            "SELECT id, name, parent_id, sort_order FROM categories"
        ).fetchall()

    def get_categories(self) -> List[Tuple[int, str, int, int]]:
        conn = self._get_conn()
        rows = self._category_rows(conn)
        return [
            (row[0], row[1], row[2], row[3])
            for row in sorted(rows, key=lambda r: (r[3], _name_sort_key(r[1])))
        ]

    def get_category(self, category_id: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id, name, parent_id, sort_order FROM categories WHERE id=?",
            (category_id,),
        ).fetchone()
        return dict(row) if row else None

    def category_exists(self, category_id: int) -> bool:
        return self.get_category(category_id) is not None

    def create_category(self, name: str, parent_id: int = None) -> int:
        name = validate_category_name(name)
        if (
            parent_id is None
            and name.casefold() == CHAT_RECORD_MEDIA_DIRNAME.casefold()
        ):
            raise ValueError("该名称由聊天记录媒体目录保留")
        with self._lock:
            conn = self._get_conn()
            if (
                parent_id is not None
                and conn.execute(
                    "SELECT 1 FROM categories WHERE id=?", (parent_id,)
                ).fetchone()
                is None
            ):
                raise ValueError("category parent not found")
            if parent_id is None:
                row = conn.execute(
                    "SELECT id FROM categories WHERE name=? AND parent_id IS NULL",
                    (name,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id FROM categories WHERE name=? AND parent_id=?",
                    (name, parent_id),
                ).fetchone()
            if row:
                return row[0]
            conn.execute(
                "INSERT INTO categories (name, parent_id) VALUES (?, ?)",
                (name, parent_id),
            )
            conn.commit()
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def ensure_category_path(self, parts) -> Optional[int]:
        """按名称路径逐级创建类别并返回末级 id；空路径表示未分类。"""
        parent_id = None
        for part in parts or ():
            parent_id = self.create_category(part, parent_id)
        return parent_id

    def set_meme_category(self, meme_id: int, category_id: int = None) -> bool:
        with self._lock:
            conn = self._get_conn()
            if (
                conn.execute("SELECT 1 FROM memes WHERE id=?", (meme_id,)).fetchone()
                is None
            ):
                return False
            if (
                category_id is not None
                and conn.execute(
                    "SELECT 1 FROM categories WHERE id=?", (category_id,)
                ).fetchone()
                is None
            ):
                raise ValueError("category not found")
            conn.execute(
                "UPDATE memes SET category_id=?, "
                "updated_at=datetime('now','localtime') "
                "WHERE id=?",
                (category_id, meme_id),
            )
            conn.commit()
            return True

    def set_chat_category(self, chat_id: int, category_id: int = None) -> bool:
        with self._lock:
            conn = self._get_conn()
            if (
                conn.execute(
                    "SELECT 1 FROM chat_records WHERE id=?", (int(chat_id),)
                ).fetchone()
                is None
            ):
                return False
            if (
                category_id is not None
                and conn.execute(
                    "SELECT 1 FROM categories WHERE id=?", (int(category_id),)
                ).fetchone()
                is None
            ):
                raise ValueError("category not found")
            conn.execute(
                "UPDATE chat_records SET category_id=?, "
                "updated_at=datetime('now','localtime') WHERE id=?",
                (category_id, int(chat_id)),
            )
            conn.commit()
            return True

    def rename_category(self, category_id: int, new_name: str):
        new_name = validate_category_name(new_name)
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT parent_id FROM categories WHERE id=?", (category_id,)
            ).fetchone()
            if row is None:
                raise ValueError("category not found")
            parent_id = row[0]
            if (
                parent_id is None
                and new_name.casefold() == CHAT_RECORD_MEDIA_DIRNAME.casefold()
            ):
                raise ValueError("该名称由聊天记录媒体目录保留")
            if parent_id is None:
                duplicate = conn.execute(
                    "SELECT 1 FROM categories "
                    "WHERE name=? AND parent_id IS NULL AND id<>?",
                    (new_name, category_id),
                ).fetchone()
            else:
                duplicate = conn.execute(
                    "SELECT 1 FROM categories WHERE name=? AND parent_id=? AND id<>?",
                    (new_name, parent_id, category_id),
                ).fetchone()
            if duplicate:
                raise ValueError("同级类别已存在同名项")
            conn.execute(
                "UPDATE categories SET name=? WHERE id=?", (new_name, category_id)
            )
            conn.commit()

    def delete_category(self, category_id: int, mode: str = "promote") -> dict:
        """删除类别层级；内容归入父级，子类别可提升或随子树删除。"""
        if mode not in ("promote", "cascade"):
            raise ValueError("invalid category delete mode")
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT parent_id FROM categories WHERE id=?", (category_id,)
            ).fetchone()
            if row is None:
                raise ValueError("category not found")
            parent_id = row[0]
            descendant_ids = self.get_category_descendants(category_id)
            try:
                if mode == "promote":
                    children = conn.execute(
                        "SELECT id, name FROM categories WHERE parent_id=?",
                        (category_id,),
                    ).fetchall()
                    if parent_id is None:
                        siblings = conn.execute(
                            "SELECT name FROM categories "
                            "WHERE parent_id IS NULL AND id<>?",
                            (category_id,),
                        ).fetchall()
                    else:
                        siblings = conn.execute(
                            "SELECT name FROM categories "
                            "WHERE parent_id=? AND id<>?",
                            (parent_id, category_id),
                        ).fetchall()
                    occupied = {str(item[0]).casefold() for item in siblings}
                    for child in children:
                        key = str(child[1]).casefold()
                        if key in occupied:
                            raise ValueError(
                                f"上一级已存在同名类别“{child[1]}”，无法提升子类别"
                            )
                        occupied.add(key)
                    conn.execute(
                        "UPDATE memes SET category_id=?, "
                        "updated_at=datetime('now','localtime') WHERE category_id=?",
                        (parent_id, category_id),
                    )
                    conn.execute(
                        "UPDATE chat_records SET category_id=?, "
                        "updated_at=datetime('now','localtime') WHERE category_id=?",
                        (parent_id, category_id),
                    )
                    conn.execute(
                        "UPDATE categories SET parent_id=? WHERE parent_id=?",
                        (parent_id, category_id),
                    )
                else:
                    subtree = (
                        "WITH RECURSIVE subtree(id) AS ("
                        "SELECT id FROM categories WHERE id=? UNION ALL "
                        "SELECT c.id FROM categories c JOIN subtree s "
                        "ON c.parent_id=s.id) "
                    )
                    conn.execute(
                        subtree + "UPDATE memes SET category_id=?, "
                        "updated_at=datetime('now','localtime') "
                        "WHERE category_id IN (SELECT id FROM subtree)",
                        (category_id, parent_id),
                    )
                    conn.execute(
                        subtree + "UPDATE chat_records SET category_id=?, "
                        "updated_at=datetime('now','localtime') "
                        "WHERE category_id IN (SELECT id FROM subtree)",
                        (category_id, parent_id),
                    )
                conn.execute("DELETE FROM categories WHERE id=?", (category_id,))
                conn.commit()
                return {
                    "parent_id": parent_id,
                    "deleted_ids": (
                        descendant_ids if mode == "cascade" else [int(category_id)]
                    ),
                }
            except Exception:
                conn.rollback()
                raise

    def get_category_descendants(self, category_id: int) -> List[int]:
        ids = [int(category_id)]
        cursor = 0
        rows = self.get_categories()
        children = {}
        for cid, _name, parent_id, _order in rows:
            children.setdefault(parent_id, []).append(cid)
        while cursor < len(ids):
            ids.extend(children.get(ids[cursor], []))
            cursor += 1
        return ids

    def get_category_path_names(self, category_id: int) -> List[str]:
        if category_id is None:
            return []
        conn = self._get_conn()
        by_id = {
            row[0]: {"name": row[1], "parent_id": row[2]}
            for row in self._category_rows(conn)
        }
        parts = []
        current = category_id
        visited = set()
        while current is not None and current not in visited:
            visited.add(current)
            node = by_id.get(current)
            if node is None:
                break
            parts.append(node["name"])
            current = node["parent_id"]
        return list(reversed(parts))

    def get_category_path(self, category_id: int) -> str:
        return " / ".join(self.get_category_path_names(category_id))

    def get_all_category_paths(self) -> List[dict]:
        return [
            {
                "id": cid,
                "name": name,
                "path": self.get_category_path(cid),
                "parent_id": parent_id,
            }
            for cid, name, parent_id, _order in self.get_categories()
        ]

    def get_meme_category(self, meme_id: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT category_id FROM memes WHERE id=?", (meme_id,)
        ).fetchone()
        if not row or row[0] is None:
            return None
        category = self.get_category(row[0])
        if not category:
            return None
        return {
            "id": category["id"],
            "name": category["name"],
            "path": self.get_category_path(category["id"]),
            "parent_id": category["parent_id"],
        }

    def get_chat_category(self, chat_id: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT category_id FROM chat_records WHERE id=?", (int(chat_id),)
        ).fetchone()
        if not row or row[0] is None:
            return None
        category = self.get_category(row[0])
        if not category:
            return None
        return {
            "id": category["id"],
            "name": category["name"],
            "path": self.get_category_path(category["id"]),
            "parent_id": category["parent_id"],
        }

    def count_uncategorized(self) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) FROM memes WHERE category_id IS NULL "
            "AND (stego_of_hash IS NULL OR stego_of_hash='')"
        ).fetchone()
        return row[0] if row else 0

    def count_collection_content(
        self, collection_ids, exclude_collection_ids=None
    ) -> int:
        """统计分组子树中表情与聊天记录的总数。"""
        ids = list(dict.fromkeys(int(value) for value in (collection_ids or [])))
        if not ids:
            return 0
        return self.count(
            collection_id=ids, exclude_collection_ids=exclude_collection_ids
        ) + self.count_chat_records(
            collection_id=ids, exclude_collection_ids=exclude_collection_ids
        )

    def count_category_content(self, category_ids, exclude_collection_ids=None) -> int:
        """统计类别子树中表情与聊天记录的总数。"""
        ids = list(dict.fromkeys(int(value) for value in (category_ids or [])))
        if not ids:
            return 0
        return self.count(
            category_id=ids, exclude_collection_ids=exclude_collection_ids
        ) + self.count_chat_records(
            category_id=ids, exclude_collection_ids=exclude_collection_ids
        )

    def count_uncategorized_content(self, exclude_collection_ids=None) -> int:
        return self.count(
            uncategorized_category=True,
            exclude_collection_ids=exclude_collection_ids,
        ) + self.count_chat_records(
            uncategorized_category=True,
            exclude_collection_ids=exclude_collection_ids,
        )

    def count_ungrouped_content(self, exclude_collection_ids=None) -> int:
        return self.count(
            uncategorized_only=True,
            exclude_collection_ids=exclude_collection_ids,
        ) + self.count_chat_records(
            uncategorized_only=True,
            exclude_collection_ids=exclude_collection_ids,
        )

    # --- 收藏集 ---

    def create_collection(self, name: str, parent_id: int = None) -> int:
        """创建分组；同名(parent_id)已存在则直接返回其 id（不产生重复空分组）"""
        with self._lock:
            conn = self._get_conn()
            if parent_id is not None:
                row = conn.execute(
                    "SELECT id FROM collections WHERE name=? AND parent_id=?",
                    (name, parent_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id FROM collections WHERE name=? AND parent_id IS NULL",
                    (name,),
                ).fetchone()
            if row:
                return row[0]
            if parent_id is not None:
                conn.execute(
                    "INSERT INTO collections (name, parent_id) VALUES (?, ?)",
                    (name, parent_id),
                )
            else:
                conn.execute("INSERT INTO collections (name) VALUES (?)", (name,))
            conn.commit()
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def add_to_collection(self, meme_id: int, collection_id: int):
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT OR IGNORE INTO meme_collections "
                "(meme_id, collection_id) VALUES (?, ?)",
                (meme_id, collection_id),
            )
            conn.commit()

    def add_to_chat_collection(self, chat_id: int, collection_id: int):
        with self._lock:
            conn = self._get_conn()
            if (
                conn.execute(
                    "SELECT 1 FROM chat_records WHERE id=?", (int(chat_id),)
                ).fetchone()
                is None
            ):
                raise ValueError("chat record not found")
            if (
                conn.execute(
                    "SELECT 1 FROM collections WHERE id=?", (int(collection_id),)
                ).fetchone()
                is None
            ):
                raise ValueError("collection not found")
            conn.execute(
                "INSERT OR IGNORE INTO chat_collections (chat_id, collection_id) "
                "VALUES (?, ?)",
                (int(chat_id), int(collection_id)),
            )
            conn.commit()

    def remove_from_chat_collection(self, chat_id: int, collection_id: int):
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM chat_collections WHERE chat_id=? AND collection_id=?",
                (int(chat_id), int(collection_id)),
            )
            conn.commit()

    def add_memes_to_collection(self, meme_ids: List[int], collection_id: int) -> int:
        """批量加入分组（追加语义，单事务），返回实际新增的关联数（重复加入不计）"""
        ids = list(dict.fromkeys(int(x) for x in (meme_ids or [])))
        if not ids:
            return 0
        with self._lock:
            conn = self._get_conn()
            try:
                valid_ids = self._existing_meme_ids(conn, ids)
                if not valid_ids:
                    return 0
                if (
                    conn.execute(
                        "SELECT 1 FROM collections WHERE id=?", (collection_id,)
                    ).fetchone()
                    is None
                ):
                    raise ValueError(f"collection {collection_id} not found")
                added = 0
                for mid in valid_ids:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO meme_collections "
                        "(meme_id, collection_id) VALUES (?, ?)",
                        (mid, collection_id),
                    )
                    added += max(cur.rowcount, 0)
                conn.commit()
                return added
            except Exception:
                conn.rollback()
                raise

    def move_memes_to_collection(
        self, meme_ids: List[int], from_ids: List[int], to_id: int
    ) -> int:
        """批量把实际属于 from_ids（源分组子树）成员的表情移入 to_id（单事务）

        返回实际新增的目标关联数（已在目标分组的重复关联不计）；
        不属于源子树的表情不受删除或移动影响
        """
        ids = list(dict.fromkeys(int(x) for x in (meme_ids or [])))
        froms = list(dict.fromkeys(int(x) for x in (from_ids or [])))
        if not ids or not froms:
            return 0
        with self._lock:
            conn = self._get_conn()
            try:
                valid_ids = self._existing_meme_ids(conn, ids)
                if not valid_ids:
                    return 0
                if (
                    conn.execute(
                        "SELECT 1 FROM collections WHERE id=?", (to_id,)
                    ).fetchone()
                    is None
                ):
                    raise ValueError(f"collection {to_id} not found")
                # 仅纳入实际属于源子树的成员
                ph_from = ",".join("?" for _ in froms)
                ph_mid = ",".join("?" for _ in valid_ids)
                member_rows = conn.execute(
                    f"SELECT DISTINCT meme_id FROM meme_collections "
                    f"WHERE collection_id IN ({ph_from}) AND meme_id IN ({ph_mid})",
                    [*froms, *valid_ids],
                ).fetchall()
                members = [r[0] for r in member_rows]
                if not members:
                    return 0
                ph_members = ",".join("?" for _ in members)
                conn.execute(
                    f"DELETE FROM meme_collections WHERE collection_id IN ({ph_from}) "
                    f"AND meme_id IN ({ph_members})",
                    [*froms, *members],
                )
                moved = 0
                for mid in members:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO meme_collections "
                        "(meme_id, collection_id) VALUES (?, ?)",
                        (mid, to_id),
                    )
                    moved += max(cur.rowcount, 0)
                # 级联清理源子树内变空的分组：只删无成员且无子分组的叶子空组，
                # 逐层迭代直到无删除（支持子组先空、父组后空）；仅限 from_ids 范围，
                # 子树外分组不受影响
                while True:
                    deleted = conn.execute(
                        f"DELETE FROM collections WHERE id IN ({ph_from}) "
                        "AND is_hidden=0 "
                        "AND id NOT IN ("
                        "SELECT DISTINCT collection_id FROM meme_collections) "
                        "AND id NOT IN ("
                        "SELECT DISTINCT collection_id FROM chat_collections) "
                        "AND id NOT IN ("
                        "SELECT DISTINCT parent_id FROM collections "
                        "WHERE parent_id IS NOT NULL)",
                        froms,
                    ).rowcount
                    if deleted == 0:
                        break
                conn.commit()
                return moved
            except Exception:
                conn.rollback()
                raise

    def collection_exists(self, name: str, parent_id: int = None) -> bool:
        """检查同名分组是否已存在"""
        conn = self._get_conn()
        if parent_id is not None:
            row = conn.execute(
                "SELECT 1 FROM collections WHERE name=? AND parent_id=?",
                (name, parent_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM collections WHERE name=? AND parent_id IS NULL",
                (name,),
            ).fetchone()
        return row is not None

    def remove_from_collection(self, meme_id: int, collection_id: int):
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM meme_collections WHERE meme_id=? AND collection_id=?",
                (meme_id, collection_id),
            )
            conn.commit()

    def set_collection_members(self, collection_id: int, meme_ids: List[int]):
        """批量设置分组内成员（清空后按序写入，保留 sort_order）"""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM meme_collections WHERE collection_id=?", (collection_id,)
            )
            for i, mid in enumerate(meme_ids or []):
                conn.execute(
                    "INSERT OR IGNORE INTO meme_collections "
                    "(meme_id, collection_id, sort_order) VALUES (?, ?, ?)",
                    (mid, collection_id, i),
                )
            conn.commit()

    def get_collections(self) -> List[Tuple[int, str, int, int]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, name, parent_id, sort_order FROM collections "
            "ORDER BY sort_order ASC"
        ).fetchall()
        # 自然排序回退：sort_order 相同（未拖拽）时数字按数值、中文按拼音
        return [
            (r[0], r[1], r[2], r[3])
            for r in sorted(rows, key=lambda r: (r[3], _name_sort_key(r[1])))
        ]

    def ensure_hidden_collection(self) -> int:
        """返回不可删除的“隐藏”系统根分组，旧同名根分组会原地升级。"""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT id FROM collections WHERE is_hidden=1 LIMIT 1"
            ).fetchone()
            if row is not None:
                return int(row[0])
            row = conn.execute(
                "SELECT id FROM collections "
                "WHERE parent_id IS NULL AND name=? COLLATE NOCASE LIMIT 1",
                ("隐藏",),
            ).fetchone()
            if row is not None:
                hidden_id = int(row[0])
                conn.execute(
                    "UPDATE collections SET name=?, is_hidden=1 WHERE id=?",
                    ("隐藏", hidden_id),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO collections (name, is_hidden) VALUES (?, 1)",
                    ("隐藏",),
                )
                hidden_id = int(cur.lastrowid)
            conn.commit()
            return hidden_id

    def get_hidden_collection_id(self) -> Optional[int]:
        row = (
            self._get_conn()
            .execute("SELECT id FROM collections WHERE is_hidden=1 LIMIT 1")
            .fetchone()
        )
        return int(row[0]) if row is not None else None

    def is_hidden_collection_root(self, collection_id: int) -> bool:
        row = (
            self._get_conn()
            .execute(
                "SELECT is_hidden FROM collections WHERE id=?", (int(collection_id),)
            )
            .fetchone()
        )
        return bool(row and row[0])

    def _collection_paths(self, conn, collection_ids) -> List[dict]:
        """为给定分组 id 构造完整层级路径，并防御异常循环父级。"""
        rows = conn.execute("SELECT id, name, parent_id FROM collections").fetchall()
        by_id = {
            row["id"]: {
                "id": row["id"],
                "name": row["name"],
                "parent_id": row["parent_id"],
            }
            for row in rows
        }
        result = []
        for collection_id in collection_ids:
            item = by_id.get(collection_id)
            if item is None:
                continue
            parts = []
            current = collection_id
            visited = set()
            while current is not None and current not in visited:
                visited.add(current)
                node = by_id.get(current)
                if node is None:
                    break
                parts.append(node["name"])
                current = node["parent_id"]
            result.append(
                {
                    "id": item["id"],
                    "name": item["name"],
                    "path": " / ".join(reversed(parts)),
                }
            )
        return sorted(result, key=lambda item: _name_sort_key(item["path"]))

    def get_all_collection_paths(self) -> List[dict]:
        """返回全部分组及完整层级路径，供详情页选择器使用。"""
        conn = self._get_conn()
        collection_ids = [
            row[0] for row in conn.execute("SELECT id FROM collections").fetchall()
        ]
        return self._collection_paths(conn, collection_ids)

    def get_meme_collections(self, meme_id: int) -> List[dict]:
        """返回表情直接所属的全部分组及完整层级路径。"""
        conn = self._get_conn()
        collection_ids = [
            row[0]
            for row in conn.execute(
                "SELECT collection_id FROM meme_collections WHERE meme_id=?",
                (meme_id,),
            ).fetchall()
        ]
        return self._collection_paths(conn, collection_ids)

    def get_meme_collection_ids_map(self, meme_ids) -> Dict[int, List[int]]:
        """批量返回表情直接所属的分组 ID，供列表页即时标示归属。"""
        ids = list(dict.fromkeys(int(value) for value in (meme_ids or [])))
        result = {meme_id: [] for meme_id in ids}
        if not ids:
            return result
        placeholders = ",".join("?" for _ in ids)
        rows = (
            self._get_conn()
            .execute(
                "SELECT meme_id, collection_id FROM meme_collections "
                f"WHERE meme_id IN ({placeholders}) "
                "ORDER BY meme_id, sort_order, collection_id",
                ids,
            )
            .fetchall()
        )
        for meme_id, collection_id in rows:
            result[int(meme_id)].append(int(collection_id))
        return result

    def get_chat_collections(self, chat_id: int) -> List[dict]:
        """返回聊天记录直接所属的全部分组及完整层级路径。"""
        conn = self._get_conn()
        collection_ids = [
            row[0]
            for row in conn.execute(
                "SELECT collection_id FROM chat_collections WHERE chat_id=? "
                "ORDER BY sort_order, collection_id",
                (int(chat_id),),
            ).fetchall()
        ]
        return self._collection_paths(conn, collection_ids)

    def get_child_collections(self, parent_id: int) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, name, sort_order FROM collections WHERE parent_id=? "
            "ORDER BY sort_order ASC",
            (parent_id,),
        ).fetchall()
        return [
            {"id": r[0], "name": r[1]}
            for r in sorted(rows, key=lambda r: (r[2], _name_sort_key(r[1])))
        ]

    def get_collection_depth(self, cid: int) -> int:
        depth = 0
        cur = cid
        conn = self._get_conn()
        while cur is not None:
            row = conn.execute(
                "SELECT parent_id FROM collections WHERE id=?", (cur,)
            ).fetchone()
            if row is None or row[0] is None:
                break
            cur = row[0]
            depth += 1
        return depth

    def delete_all(self):
        """删除所有表情包及相关数据"""
        with self._lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM chat_records")
            conn.execute("DELETE FROM notion_sources")
            conn.execute("DELETE FROM favorites")
            conn.execute("DELETE FROM meme_collections")
            conn.execute("DELETE FROM meme_tags")
            conn.execute("DELETE FROM memes")
            conn.execute("DELETE FROM collections")
            conn.execute("DELETE FROM categories")
            conn.execute("DELETE FROM tags")
            conn.commit()

    def delete_collection(self, collection_id: int, mode: str = "cascade") -> dict:
        """删除分组层级；内容归入父级，子分组可提升或随子树删除。"""
        if mode not in ("promote", "cascade"):
            raise ValueError("invalid collection delete mode")
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT parent_id, is_hidden FROM collections WHERE id=?",
                (collection_id,),
            ).fetchone()
            if row is None:
                raise ValueError("collection not found")
            if row[1]:
                raise ValueError("“隐藏”是系统分组，不能删除")
            parent_id = row[0]
            all_rows = conn.execute(
                "SELECT id, name, parent_id FROM collections"
            ).fetchall()
            children_by_parent = {}
            for item in all_rows:
                children_by_parent.setdefault(item[2], []).append(item[0])
            descendant_ids = [int(collection_id)]
            cursor = 0
            while cursor < len(descendant_ids):
                descendant_ids.extend(
                    children_by_parent.get(descendant_ids[cursor], [])
                )
                cursor += 1
            try:
                if mode == "promote":
                    children = [
                        item for item in all_rows if item[2] == int(collection_id)
                    ]
                    siblings = [
                        item
                        for item in all_rows
                        if item[2] == parent_id and item[0] != int(collection_id)
                    ]
                    occupied = {str(item[1]).casefold() for item in siblings}
                    for child in children:
                        key = str(child[1]).casefold()
                        if key in occupied:
                            raise ValueError(
                                f"上一级已存在同名分组“{child[1]}”，无法提升子分组"
                            )
                        occupied.add(key)
                    source_sql = "collection_id=?"
                    source_params = (collection_id,)
                    conn.execute(
                        "UPDATE collections SET parent_id=? WHERE parent_id=?",
                        (parent_id, collection_id),
                    )
                else:
                    source_sql = (
                        "collection_id IN (WITH RECURSIVE subtree(id) AS ("
                        "SELECT id FROM collections WHERE id=? UNION ALL "
                        "SELECT c.id FROM collections c JOIN subtree s "
                        "ON c.parent_id=s.id) SELECT id FROM subtree)"
                    )
                    source_params = (collection_id,)
                if parent_id is not None:
                    conn.execute(
                        "INSERT OR IGNORE INTO meme_collections "
                        "(meme_id, collection_id, sort_order) "
                        "SELECT meme_id, ?, sort_order FROM meme_collections "
                        f"WHERE {source_sql}",
                        (parent_id, *source_params),
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO chat_collections "
                        "(chat_id, collection_id, sort_order) "
                        "SELECT chat_id, ?, sort_order FROM chat_collections "
                        f"WHERE {source_sql}",
                        (parent_id, *source_params),
                    )
                conn.execute("DELETE FROM collections WHERE id=?", (collection_id,))
                conn.commit()
                return {
                    "parent_id": parent_id,
                    "deleted_ids": (
                        descendant_ids if mode == "cascade" else [int(collection_id)]
                    ),
                }
            except Exception:
                conn.rollback()
                raise

    def rename_collection(self, collection_id: int, new_name: str):
        with self._lock:
            conn = self._get_conn()
            if self.is_hidden_collection_root(collection_id):
                raise ValueError("“隐藏”是系统分组，不能重命名")
            conn.execute(
                "UPDATE collections SET name=? WHERE id=?", (new_name, collection_id)
            )
            conn.commit()

    # --- 搜索 ---

    @staticmethod
    def _genre_condition(genre: str, table_prefix: str = "") -> Optional[str]:
        """返回不可编辑体裁筛选条件；GIF 按真实 GIF MIME/扩展名判定。"""
        filename = f"{table_prefix}filename"
        mime_type = f"{table_prefix}mime_type"
        gif = (
            f"(LOWER(COALESCE({mime_type}, '')) = 'image/gif' "
            f"OR LOWER({filename}) LIKE '%.gif')"
        )
        if genre == "gif":
            return gif
        if genre == "image":
            return f"NOT {gif}"
        return None

    def search(
        self,
        keyword: str = "",
        tags: List[Any] = None,
        collection_id: int = None,
        favorite_only: bool = False,
        uncategorized_only: bool = False,
        offset: int = 0,
        limit: int = 100,
        include_description: bool = True,
        include_ocr_text: bool = True,
        category_id=None,
        uncategorized_category: bool = False,
        genre: str = None,
        exclude_collection_ids=None,
        meme_ids=None,
        random_order: bool = False,
    ) -> List[dict]:
        conn = self._get_conn()
        where = ["(m.stego_of_hash IS NULL OR m.stego_of_hash = '')"]
        params = []
        if meme_ids is not None:
            placeholders = ",".join("?" for _ in meme_ids)
            where.append(f"m.id IN ({placeholders})" if meme_ids else "1=0")
            params.extend(meme_ids)

        if keyword:
            kw = f"%{keyword}%"
            keyword_parts = ["m.filename LIKE ?", "m.original_name LIKE ?"]
            keyword_params = [kw, kw]
            if include_description:
                keyword_parts.extend(
                    ["m.manual_description LIKE ?", "m.ai_description LIKE ?"]
                )
                keyword_params.extend([kw, kw])
            if include_ocr_text:
                keyword_parts.extend(
                    ["m.manual_ocr_text LIKE ?", "m.ai_ocr_text LIKE ?"]
                )
                keyword_params.extend([kw, kw])
            keyword_parts.append(
                "m.id IN (SELECT mt.meme_id FROM meme_tags mt "
                "JOIN tags t ON t.id = mt.tag_id WHERE t.name LIKE ?)"
            )
            keyword_params.append(kw)
            where.append("(" + " OR ".join(keyword_parts) + ")")
            params.extend(keyword_params)

        normalized_tags = self._normalize_tags(tags)
        if normalized_tags:
            tag_conditions = " OR ".join(
                "(t.category=? COLLATE NOCASE AND t.name=? COLLATE NOCASE)"
                for _ in normalized_tags
            )
            where.append(
                f"""m.id IN (
                SELECT mt.meme_id FROM meme_tags mt
                JOIN tags t ON t.id = mt.tag_id
                WHERE {tag_conditions}
                GROUP BY mt.meme_id HAVING COUNT(DISTINCT t.id) = ?
            )"""
            )
            for category, name in normalized_tags:
                params.extend([category, name])
            params.append(len(normalized_tags))

        if collection_id is not None:
            if isinstance(collection_id, list):
                placeholders = ",".join("?" for _ in collection_id)
                where.append(
                    f"""m.id IN (
                    SELECT mc.meme_id FROM meme_collections mc
                    WHERE mc.collection_id IN ({placeholders})
                )"""
                )
                params.extend(collection_id)
            else:
                where.append(
                    "m.id IN ("
                    "SELECT mc.meme_id FROM meme_collections mc "
                    "WHERE mc.collection_id = ?)"
                )
                params.append(collection_id)

        if uncategorized_category:
            where.append("m.category_id IS NULL")
        elif category_id is not None:
            category_ids = (
                category_id if isinstance(category_id, list) else [category_id]
            )
            if category_ids:
                placeholders = ",".join("?" for _ in category_ids)
                where.append(f"m.category_id IN ({placeholders})")
                params.extend(category_ids)

        if favorite_only:
            where.append("m.id IN (SELECT meme_id FROM favorites)")

        if uncategorized_only:
            where.append(
                """NOT EXISTS (
                SELECT 1 FROM meme_collections mc WHERE mc.meme_id = m.id
            )"""
            )

        excluded_ids = list(
            dict.fromkeys(int(value) for value in (exclude_collection_ids or []))
        )
        if excluded_ids:
            placeholders = ",".join("?" for _ in excluded_ids)
            where.append(
                "NOT EXISTS (SELECT 1 FROM meme_collections hidden_mc "
                "WHERE hidden_mc.meme_id=m.id "
                f"AND hidden_mc.collection_id IN ({placeholders}))"
            )
            params.extend(excluded_ids)

        genre_condition = self._genre_condition(genre, "m.")
        if genre_condition:
            where.append(genre_condition)

        sql = "SELECT m.* FROM memes m"
        if where:
            sql += " WHERE " + " AND ".join(where)

        if random_order:
            sql += " ORDER BY RANDOM() LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif collection_id is not None:
            # 按主分组的 meme_collections.sort_order 排序（拖拽排序结果）
            primary_cid = (
                collection_id[0] if isinstance(collection_id, list) else collection_id
            )
            sql += """ ORDER BY (
                SELECT mc.sort_order FROM meme_collections mc
                WHERE mc.meme_id = m.id AND mc.collection_id = ?
            ), m.id LIMIT ? OFFSET ?"""
            params.extend([primary_cid, limit, offset])
        else:
            sql += " ORDER BY m.sort_order ASC, m.updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count(
        self,
        keyword: str = "",
        tags: List[Any] = None,
        collection_id: int = None,
        favorite_only: bool = False,
        uncategorized_only: bool = False,
        include_description: bool = True,
        include_ocr_text: bool = True,
        category_id=None,
        uncategorized_category: bool = False,
        genre: str = None,
        exclude_collection_ids=None,
    ) -> int:
        conn = self._get_conn()
        where = ["(stego_of_hash IS NULL OR stego_of_hash = '')"]
        params = []
        if keyword:
            kw = f"%{keyword}%"
            keyword_parts = ["filename LIKE ?", "original_name LIKE ?"]
            keyword_params = [kw, kw]
            if include_description:
                keyword_parts.extend(
                    ["manual_description LIKE ?", "ai_description LIKE ?"]
                )
                keyword_params.extend([kw, kw])
            if include_ocr_text:
                keyword_parts.extend(["manual_ocr_text LIKE ?", "ai_ocr_text LIKE ?"])
                keyword_params.extend([kw, kw])
            keyword_parts.append(
                "memes.id IN (SELECT mt.meme_id FROM meme_tags mt "
                "JOIN tags t ON t.id = mt.tag_id WHERE t.name LIKE ?)"
            )
            keyword_params.append(kw)
            where.append("(" + " OR ".join(keyword_parts) + ")")
            params.extend(keyword_params)
        normalized_tags = self._normalize_tags(tags)
        if normalized_tags:
            tag_conditions = " OR ".join(
                "(t.category=? COLLATE NOCASE AND t.name=? COLLATE NOCASE)"
                for _ in normalized_tags
            )
            where.append(
                f"""id IN (
                SELECT mt.meme_id FROM meme_tags mt
                JOIN tags t ON t.id = mt.tag_id
                WHERE {tag_conditions}
                GROUP BY mt.meme_id HAVING COUNT(DISTINCT t.id) = ?
            )"""
            )
            for category, name in normalized_tags:
                params.extend([category, name])
            params.append(len(normalized_tags))
        if collection_id is not None:
            if isinstance(collection_id, list):
                placeholders = ",".join("?" for _ in collection_id)
                where.append(
                    f"""id IN (
                    SELECT meme_id FROM meme_collections
                    WHERE collection_id IN ({placeholders})
                )"""
                )
                params.extend(collection_id)
            else:
                where.append(
                    """id IN (
                    SELECT meme_id FROM meme_collections WHERE collection_id = ?
                )"""
                )
                params.append(collection_id)

        if uncategorized_category:
            where.append("category_id IS NULL")
        elif category_id is not None:
            category_ids = (
                category_id if isinstance(category_id, list) else [category_id]
            )
            if category_ids:
                placeholders = ",".join("?" for _ in category_ids)
                where.append(f"category_id IN ({placeholders})")
                params.extend(category_ids)
        if favorite_only:
            where.append("id IN (SELECT meme_id FROM favorites)")
        if uncategorized_only:
            where.append(
                """NOT EXISTS (
                SELECT 1 FROM meme_collections WHERE meme_id = memes.id
            )"""
            )
        excluded_ids = list(
            dict.fromkeys(int(value) for value in (exclude_collection_ids or []))
        )
        if excluded_ids:
            placeholders = ",".join("?" for _ in excluded_ids)
            where.append(
                "NOT EXISTS (SELECT 1 FROM meme_collections hidden_mc "
                "WHERE hidden_mc.meme_id=memes.id "
                f"AND hidden_mc.collection_id IN ({placeholders}))"
            )
            params.extend(excluded_ids)
        genre_condition = self._genre_condition(genre)
        if genre_condition:
            where.append(genre_condition)
        sql = "SELECT COUNT(*) FROM memes"
        if where:
            sql += " WHERE " + " AND ".join(where)
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else 0

    def get_by_hash(self, file_hash: str) -> Optional[dict]:
        if not file_hash:
            return None
        conn = self._get_conn()
        row = conn.execute(
            """SELECT * FROM memes
               WHERE file_hash=? OR source_file_hash=? LIMIT 1""",
            (file_hash, file_hash),
        ).fetchone()
        return dict(row) if row else None

    def get_by_stego_of(self, file_hash: str) -> Optional[dict]:
        """查找携带指定原图哈希的隐写 GIF 表情"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM memes WHERE stego_of_hash=? LIMIT 1", (file_hash,)
        ).fetchone()
        return dict(row) if row else None

    def get_by_id(self, meme_id: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM memes WHERE id=?", (meme_id,)).fetchone()
        return dict(row) if row else None

    def get_by_filename(self, filename: str) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM memes WHERE filename=? LIMIT 1", (filename,)
        ).fetchone()
        return dict(row) if row else None

    def get_all(self, offset: int = 0, limit: int = 100) -> List[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM memes ORDER BY sort_order ASC, updated_at DESC "
            "LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_phash(self) -> List[dict]:
        """返回所有可见 meme 的感知哈希快照，供全库相似度比对（排除隐写载体）"""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT id, filename, original_name, perceptual_hash, stego_of_hash "
                "FROM memes WHERE (stego_of_hash IS NULL OR stego_of_hash = '') "
                "ORDER BY sort_order ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def set_perceptual_hash(self, meme_id: int, phash: int):
        """写入/更新某 meme 的感知哈希（hex 文本存储，惰性回填用）"""
        if phash:
            ph_hex = hex(phash)
        elif phash == 0:
            ph_hex = "0"  # 已计算但无感知内容：占位，避免反复回填
        else:
            ph_hex = None
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE memes SET perceptual_hash=? WHERE id=?", (ph_hex, meme_id)
            )
            conn.commit()

    def reorder_memes(self, meme_ids: List[int]):
        with self._lock:
            conn = self._get_conn()
            for i, mid in enumerate(meme_ids):
                conn.execute("UPDATE memes SET sort_order=? WHERE id=?", (i, mid))
            conn.commit()

    def reorder_collections(self, collection_ids: List[int]):
        with self._lock:
            conn = self._get_conn()
            for i, cid in enumerate(collection_ids):
                conn.execute("UPDATE collections SET sort_order=? WHERE id=?", (i, cid))
            conn.commit()

    def reorder_collection_members(self, collection_id: int, meme_ids: List[int]):
        with self._lock:
            conn = self._get_conn()
            for i, mid in enumerate(meme_ids):
                conn.execute(
                    "UPDATE meme_collections SET sort_order=? "
                    "WHERE meme_id=? AND collection_id=?",
                    (i, mid, collection_id),
                )
            conn.commit()

    def record_use(self, meme_id: int):
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO recent_uses (meme_id, used_at) "
                "VALUES (?, strftime('%Y-%m-%d %H:%M:%f','now','localtime'))",
                (meme_id,),
            )
            conn.commit()

    def remove_from_recent(self, meme_id: int):
        with self._lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM recent_uses WHERE meme_id=?", (meme_id,))
            conn.commit()

    def clear_recent(self):
        with self._lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM recent_uses")
            conn.commit()

    def get_recent_meme_ids(self, meme_ids) -> List[int]:
        """批量返回给定表情中存在于最近使用列表的 ID。"""
        ids = list(dict.fromkeys(int(value) for value in (meme_ids or [])))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = (
            self._get_conn()
            .execute(
                f"SELECT meme_id FROM recent_uses WHERE meme_id IN ({placeholders})",
                ids,
            )
            .fetchall()
        )
        return [int(row[0]) for row in rows]

    def get_recent(
        self,
        limit: int = 50,
        offset: int = 0,
        genre: str = None,
        exclude_collection_ids=None,
    ) -> List[dict]:
        """按最近使用时间分页查询表情（used_at 相同时以 meme_id 稳定排序）"""
        conn = self._get_conn()
        where = ["(m.stego_of_hash IS NULL OR m.stego_of_hash = '')"]
        genre_condition = self._genre_condition(genre, "m.")
        if genre_condition:
            where.append(genre_condition)
        params = []
        excluded_ids = list(
            dict.fromkeys(int(value) for value in (exclude_collection_ids or []))
        )
        if excluded_ids:
            placeholders = ",".join("?" for _ in excluded_ids)
            where.append(
                "NOT EXISTS (SELECT 1 FROM meme_collections hidden_mc "
                "WHERE hidden_mc.meme_id=m.id "
                f"AND hidden_mc.collection_id IN ({placeholders}))"
            )
            params.extend(excluded_ids)
        params.extend([limit, offset])
        rows = conn.execute(
            "SELECT m.* FROM memes m "
            "JOIN recent_uses r ON r.meme_id = m.id "
            "WHERE " + " AND ".join(where) + " "
            "ORDER BY r.used_at DESC, r.meme_id DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def count_recent(self, genre: str = None, exclude_collection_ids=None) -> int:
        """统计最近使用表情总数"""
        conn = self._get_conn()
        where = ["(m.stego_of_hash IS NULL OR m.stego_of_hash = '')"]
        genre_condition = self._genre_condition(genre, "m.")
        if genre_condition:
            where.append(genre_condition)
        params = []
        excluded_ids = list(
            dict.fromkeys(int(value) for value in (exclude_collection_ids or []))
        )
        if excluded_ids:
            placeholders = ",".join("?" for _ in excluded_ids)
            where.append(
                "NOT EXISTS (SELECT 1 FROM meme_collections hidden_mc "
                "WHERE hidden_mc.meme_id=m.id "
                f"AND hidden_mc.collection_id IN ({placeholders}))"
            )
            params.extend(excluded_ids)
        row = conn.execute(
            "SELECT COUNT(*) FROM memes m "
            "JOIN recent_uses r ON r.meme_id = m.id "
            "WHERE " + " AND ".join(where),
            params,
        ).fetchone()
        return row[0] if row else 0


# 全局单例
_db = None


def get_db() -> MemeDB:
    global _db
    if _db is None:
        _db = MemeDB()
    return _db
