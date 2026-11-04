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
_IMPORT_MAX_PX = 2560  # 可选最长边限制的默认阈值
_IMPORT_MAX_BYTES = 200 * 1024 * 1024  # 原件硬上限（200 MiB）


def normalize_import_max_px(value):
    """规范化可选的导入最长边阈值。"""
    try:
        if isinstance(value, bool):
            return _IMPORT_MAX_PX
        return max(1, min(65535, int(value)))
    except (TypeError, ValueError, OverflowError):
        return _IMPORT_MAX_PX


def get_import_max_px(cfg=None):
    """返回当前导入最长边上限，0 表示不限制。"""
    cfg = cfg if cfg is not None else get_config()
    if cfg.get("import_resolution_limit_enabled", False) is not True:
        return 0
    return normalize_import_max_px(
        cfg.get("import_resolution_limit_px", _IMPORT_MAX_PX)
    )


def normalize_library_genres(value):
    """规范化主窗口体裁选择，保留全部取消的空列表。"""
    genres = ["image", "gif", "chat_record"]
    if not isinstance(value, list):
        return genres
    return [genre for genre in genres if genre in value]


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
        # 向导
        "guide": "",  # 设置向导完成标记（"ok"）
        # 全局设置
        "hotkey": "Ctrl+Alt+N",
        "hotkey_show_at_mouse": False,
        "disable_auto_hide": False,  # 关闭自动隐藏（复制/拖拽成功后保持窗口可见）
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
        # 超过 20 MiB 的普通媒体：ask 每次询问；keep_original 保留原件；
        # compressed_only 仅归档日常压缩版。聊天记录媒体不经过此设置。
        "large_meme_import_mode": "ask",
        "import_resolution_limit_enabled": False,
        "import_resolution_limit_px": _IMPORT_MAX_PX,
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
        "manifest_include_tags": True,  # 将标签写入 meme-index.json 清单
        "manifest_include_collections": True,  # 将分组写入 meme-index.json 清单
        "manifest_include_favorites": True,  # 将收藏夹写入 meme-index.json 清单
        "cloud_direct": True,  # 云端直接使用（缺失表情点击下载；默认开启）
        "cloud_thumb_auto_push": True,  # 启动时静默检测云端缺失缩略图并后台上传
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
        "webdav_allow_insecure": False,  # 忽略 WebDAV 自签名/不受信任证书
        # 复制设置
        # 0不处理；1webp缩放；2转gif；3转gif隐写原图；4仅改后缀为gif
        "copy_resize_mode": 1,
        "copy_resize_max": 200,  # 缩放后最长边像素
        "copy_avoid_webp": False,  # 复制时避免 WebP（动图转 GIF，静态转 JPG）
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
        "napcat_image_send_mode": "sticker",
        "qq_drag_cleanup_on_start": True,  # 启动时清理超过一天的拖拽凭证和占位图
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
        "library_genres": ["image", "gif", "chat_record"],
        "window_x": None,  # None=未保存，交给系统居中（负坐标合法，不能用 -1 当哨兵）
        "window_y": None,
        "window_pos_set": False,  # 是否已由 _save_window_position 真正写过坐标
        "window_width": 0,  # 主窗口宽度持久化（0=默认 960）
        "window_height": 0,  # 主窗口高度持久化（0=默认 640）
        "window_resize_enabled": False,  # 允许拖窗口边框调整大小（主/设置窗口）
        "sort_mode": "manifest",  # 主窗口排序（manifest/name，可带 _desc）
        "settings_window_width": 0,  # 设置窗口宽度持久化（0=默认 720）
        "settings_window_height": 0,  # 设置窗口高度持久化（0=默认 560）
        "auto_play_gif": True,
        "hover_to_play": False,
        "hover_zoom": True,  # 悬停卡片放大预览整图
        "try_original_image": False,
        "search_description": True,
        "search_image_text": True,
        "show_uncategorized": True,  # 显示「未分组」分组
        "show_category_uncategorized": False,
        # “隐藏”系统分组及其子级内容只在该子树中显示
        "hidden_collection_enabled": True,
        # 删除分组/类别层级：ask 每次询问；promote 提升子级；cascade 删除子树
        "hierarchy_delete_mode": "ask",
        "record_recent_use": True,  # 复制时记录最近使用
        "show_startup_animation": True,  # 启动时播放启动动画（关闭时降级 300ms 延时）
        # 插件
        "plugin_dirs": [],  # 额外插件扫描目录（开发直连或集合）
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
        if key == "import_resolution_limit_px":
            value = normalize_import_max_px(value)
        elif key in ("import_resolution_limit_enabled", "show_category_uncategorized"):
            value = value is True
        elif key == "library_genres":
            value = normalize_library_genres(value)
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
        """恢复出厂默认值（保留向导完成标记）"""
        guide = self._data.get("guide", "")
        self._data = dict(self.DEFAULTS)
        if guide:
            self._data["guide"] = guide
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
                # 老配置没有 window_pos_set：旧默认值 -1 是「未保存」哨兵，负坐标
                # 放开后它会通过范围校验，清成 None 让窗口回到系统居中；
                # 新版写过坐标时标志为真，-1 是副屏上的合法坐标，原样保留
                if not self._data.get("window_pos_set"):
                    if self._data.get("window_x") == -1:
                        self._data["window_x"] = None
                    if self._data.get("window_y") == -1:
                        self._data["window_y"] = None
                self._migrate(raw)
            except (json.JSONDecodeError, OSError):
                pass

    def _migrate(self, raw):
        """配置文件版本迁移"""
        saved_ver = raw.get("version", "")
        if "napcat_image_send_mode" not in raw and raw.get("napcat_gif_send_mode") in (
            "sticker",
            "image",
        ):
            self._data["napcat_image_send_mode"] = raw["napcat_gif_send_mode"]
            self._dirty = True
        if saved_ver == _CONFIG_VERSION:
            return
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


def guide_ok() -> bool:
    """设置向导是否已完成（config.json 的 guide 标记）"""
    cfg = get_config()
    v = cfg.get("guide")
    if v == "ok":
        return True
    # 旧版写在 meme-index.json 清单里：读到即迁移到配置文件
    from .manifest import load as load_manifest

    legacy = load_manifest().get("guide")
    if legacy:
        cfg.set("guide", legacy)
        cfg.save()
        v = legacy
    return v == "ok"


def set_guide_ok():
    """标记设置向导已完成（写入 config.json）"""
    cfg = get_config()
    cfg.set("guide", "ok")
    cfg.save()
