"""配置管理 - 本地JSON配置文件，密钥字段加密存储"""

import json
import os
import platform
import threading
from pathlib import Path

from . import __version__
from .crypto_util import decrypt_data, encrypt_data

APP_NAME = "OhMyMeme"
CHAT_RECORD_MEDIA_DIRNAME = "chat-record-media"

# 当前配置文件版本（与软件版本同步，用于数据迁移）
_CONFIG_VERSION = __version__


def resolve_path(path, allow_unresolved=False) -> Path:
    """解析绝对路径；显式兼容虚拟盘时允许退回纯字符串规范化。

    CloudDrive2 等虚拟文件系统可能可正常读写，却不支持 pathlib.resolve()
    依赖的 Windows 最终路径 API。默认仍保持严格解析，避免静默放宽校验。
    """
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve()
    except OSError:
        if not allow_unresolved:
            raise
        return Path(os.path.abspath(os.path.normpath(str(candidate))))


# ~~~ 加密字段列表 ~~~（写入前自动加密，读取时自动解密）
_SECRET_KEYS = {
    "s3_access_key",
    "s3_secret_key",
    "r2_access_key_id",
    "r2_secret_access_key",
    "ftp_password",
    "webdav_password",
    "lan_secret",
    "notion_token",
    "napcat_token",
    "napcat_webui_token",
}

# ~~~ 导入限制 ~~~（原件保留；日常使用副本另行压缩）
_IMPORT_MAX_PX = 2560  # 最长边像素上限（超过 2K）
_IMPORT_MAX_BYTES = 200 * 1024 * 1024  # 原件硬上限（200 MiB）


def _get_config_dir() -> Path:
    """跨平台配置目录"""
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    return Path(base) / APP_NAME


def _get_data_dir() -> Path:
    """跨平台数据/缓存目录"""
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


class Config:
    """应用配置"""

    DEFAULTS = {
        # 版本（用于数据迁移）
        "version": "",
        # 全局设置
        "hotkey": "Ctrl+Alt+N",
        "hotkey_show_at_mouse": False,
        "auto_start": False,
        "silent_start": False,
        "language": "zh-CN",
        # 缓存设置
        "cache_max_size_mb": 500,
        "thumbnail_size": 150,
        "cache_dir": "",  # 自定义表情包存储目录（空=默认 data_dir/cache）
        "virtual_drive_compat": False,  # resolve() 失效的虚拟盘兼容（默认严格）
        # 一次性读取大量原件时串行化并分批暂停，适合 CloudDrive2 等虚拟盘
        "bulk_read_protection": False,
        "bulk_read_batch_size": 8,
        "bulk_read_pause_seconds": 1.0,
        # 当前筛选存在后续页时，后台按限流参数补建日常副本和缩略图
        "bulk_read_prefetch_pages": False,
        # 本地备份
        "backup_dir": "",  # 自定义备份输出目录（空=默认 data_dir/backups）
        # 云端同步
        "sync_auto_fetch_index": False,
        "sync_auto_sync": False,
        "sync_type": "",  # "ftp" | "s3" | "r2" | ""
        "sync_interval_minutes": 60,
        "sync_delete_remote": False,  # 上传时删除远端文件
        "sync_remove_local": False,  # 下载时删除本地多余文件
        "sync_hide_upload_warning": False,  # 不再提醒上传警告
        "sync_threads": 3,  # 同步并发线程数（1-8）
        "show_upload_progress": True,  # 上传时显示进度条
        "show_upload_done": True,  # 上传完毕显示提示
        "show_download_progress": True,  # 下载时显示进度条
        "show_download_done": True,  # 下载完毕显示提示
        # FTP
        "ftp_host": "",
        "ftp_port": 21,
        "ftp_user": "",
        "ftp_password": "",
        "ftp_path": "/",
        # S3
        "s3_endpoint": "",
        "s3_region": "",
        "s3_bucket": "",
        "s3_access_key": "",
        "s3_secret_key": "",
        "s3_path": "",
        "s3_addressing_style": "virtual",
        "s3_signature_version": "s3",
        # R2
        "r2_account_id": "",
        "r2_access_key_id": "",
        "r2_secret_access_key": "",
        "r2_bucket": "",
        "r2_path": "",
        # WebDAV
        "webdav_url": "",
        "webdav_user": "",
        "webdav_password": "",
        "webdav_path": "",
        "webdav_timeout": 30,  # WebDAV 请求超时（秒）
        # 复制设置
        "copy_resize_mode": 1,  # 0不处理；1webp缩放；2转gif；3转gif隐写原图
        "copy_resize_max": 200,  # 缩放后最长边像素
        # 局域网互联
        "lan_port": 17852,  # 局域网服务端口
        "lan_secret": "",  # 互联访问密钥（加密存储）
        # QQ 聊天记录桥接（NapCat / OneBot 11）
        "napcat_enabled": False,
        "napcat_http_url": "http://127.0.0.1:3000",
        "napcat_ws_url": "ws://127.0.0.1:3001",
        "napcat_token": "",
        "napcat_webui_url": "http://127.0.0.1:6099",
        "napcat_webui_token": "",
        "napcat_command": "/omm",
        "napcat_send_mode": "original_native",
        # Telegram 导入
        "tg_tdata_path": "",  # 手动指定的 Telegram tdata 目录
        # Notion 只读导入
        "notion_token": "",
        "notion_database_id": "",
        "notion_data_source_id": "",
        "notion_media_property": "",
        "notion_title_property": "",
        "notion_tags_property": "",
        "notion_tag_mappings": [],
        "notion_collection_property": "",
        "notion_version": "2026-03-11",
        # UI
        "theme": "dark",
        "window_x": -1,
        "window_y": -1,
        "auto_play_gif": True,
        "hover_to_play": False,
        "try_original_image": False,
        "search_description": True,
        "search_image_text": True,
        "show_uncategorized": True,  # 显示「未分组」分组
        "record_recent_use": True,  # 复制时记录最近使用
        "show_startup_animation": True,  # 启动时播放启动动画（关闭时降级 300ms 延时）
    }

    def __init__(self, path: Path = None):
        self._path = path or (_get_config_dir() / "config.json")
        self._data = dict(self.DEFAULTS)
        self._dirty = False
        self._lock = threading.Lock()
        self._load()

    # --- 公开属性访问 ---

    def get(self, key: str, default=None):
        val = self._data.get(key, default)
        if val is not None and key in _SECRET_KEYS:
            dec = decrypt_data(val)
            return dec if dec else val
        return val

    def set(self, key: str, value):
        if key in _SECRET_KEYS and value:
            value = encrypt_data(str(value))
        self._data[key] = value
        self._dirty = True

    def __getattr__(self, key):
        if key.startswith("_"):
            raise AttributeError(key)
        val = self._data.get(key)
        if val is not None and key in _SECRET_KEYS:
            val = decrypt_data(val)
        return val

    def __setattr__(self, key, value):
        if key.startswith("_"):
            super().__setattr__(key, value)
        else:
            self.set(key, value)

    def to_dict(self) -> dict:
        """导出纯文本字典（密钥已解密），用于界面展示"""
        result = {}
        for k, v in self._data.items():
            if v is not None and k in _SECRET_KEYS:
                result[k] = decrypt_data(v) or ""
            else:
                result[k] = v
        return result

    def update_from_dict(self, d: dict):
        """从字典批量更新"""
        for k, v in d.items():
            if k in self.DEFAULTS:
                self.set(k, v)

    def reset(self):
        """恢复出厂默认值"""
        self._data = dict(self.DEFAULTS)
        self._dirty = True

    # --- 持久化 ---

    def _load(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for k in self.DEFAULTS:
                    if k in raw:
                        self._data[k] = raw[k]
                self._migrate(raw)
            except (json.JSONDecodeError, OSError):
                pass

    def _migrate(self, raw):
        """配置文件版本迁移"""
        saved_ver = raw.get("version", "")
        if saved_ver == _CONFIG_VERSION:
            return
        # 0.2.0 及之前：删除 window_width/window_height
        for k in ("window_width", "window_height"):
            self._data.pop(k, None)
        # 旧布尔开关迁移到 copy_resize_mode（旧配置里没有 copy_resize_mode 键）
        if "copy_resize_mode" not in raw and (
            "copy_resize_enabled" in raw or "experimental_stego" in raw
        ):
            if raw.get("experimental_stego"):
                self._data["copy_resize_mode"] = 3
            elif raw.get("copy_resize_enabled") is False:
                self._data["copy_resize_mode"] = 0
        self._data["version"] = _CONFIG_VERSION
        self._dirty = True

    def save(self):
        """持久化到磁盘（加锁 + 原子替换：先写临时文件再 os.replace，
        避免写入中途失败/断电破坏原配置文件）"""
        import tempfile

        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=".config-",
                suffix=".tmp",
                dir=str(self._path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            self._dirty = False

    @property
    def config_dir(self) -> Path:
        return self._path.parent

    @property
    def data_dir(self) -> Path:
        d = _get_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def cache_dir(self) -> Path:
        custom = self.get("cache_dir")
        if custom:
            d = resolve_path(custom, self.get("virtual_drive_compat", False) is True)
        else:
            d = self.data_dir / "cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def thumbnail_dir(self) -> Path:
        d = self.data_dir / "thumbnails"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def derivative_dir(self) -> Path:
        """可再生的日常副本目录，不随原件备份或远程同步。"""
        d = self.data_dir / "derivatives"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def chat_record_media_dir(self) -> Path:
        """聊天记录媒体目录，跟随表情包存储位置迁移与备份。"""
        d = self.cache_dir / CHAT_RECORD_MEDIA_DIRNAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return self.data_dir / "memes.db"


# 全局单例
_config = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
