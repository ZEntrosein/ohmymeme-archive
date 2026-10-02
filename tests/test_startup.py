"""启动流程测试 - pytest风格"""

import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["OHMYMEME_TEST"] = "1"

from src.clipboard_util import copy_image_to_clipboard, copy_text
from src.config import Config
from src.crypto_util import decrypt_data, encrypt_data
from src.database import MemeDB
from src.hotkey import GlobalHotkey
from src.tray import _create_default_icon


class _FakeConfig:
    def __init__(self, hotkey_show_at_mouse):
        self.hotkey_show_at_mouse = hotkey_show_at_mouse
        self.disable_auto_hide = False
        self.saved = {}
        self.thumbnail_dir = Path(tempfile.mkdtemp(prefix="ohmm_fake_thumbs_"))

    def get(self, key, default=None):
        if key == "hotkey_show_at_mouse":
            return self.hotkey_show_at_mouse
        if key == "disable_auto_hide":
            return self.disable_auto_hide
        return default

    def set(self, key, value):
        self.saved[key] = value

    def save(self):
        pass


class _FakeWindow:
    def __init__(self):
        self.width = 700
        self.height = 500
        self.x = 10
        self.y = 20
        self.on_top = False
        self.calls = []
        self.events = type(
            "FakeEvents",
            (),
            {"shown": _FakeEvent()},
        )()

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def show(self):
        self.calls.append(("show",))

    def focus(self):
        self.calls.append(("focus",))

    def hide(self):
        self.calls.append(("hide",))

    def minimize(self):
        self.calls.append(("minimize",))


class _FakeEvent:
    def __init__(self):
        self.callbacks = []

    def __iadd__(self, callback):
        self.callbacks.append(callback)
        return self


class _FakeMemeDB:
    def get_by_id(self, meme_id):
        return {"filename": f"meme-{meme_id}.png"}

    def record_use(self, meme_id):
        pass


def _fake_webui(hotkey_show_at_mouse, visible=False):
    from src.webui import WebUI

    ui = WebUI()
    ui._cfg = _FakeConfig(hotkey_show_at_mouse)
    ui._window = _FakeWindow()
    ui._visible = visible
    return ui


def test_config_io(tmp_path):
    cfg = Config(tmp_path / "config.json")
    cfg.set("hotkey", "Ctrl+Shift+X")
    cfg.set("s3_secret_key", "test_secret_123")
    cfg.set("auto_start", True)
    cfg.save()

    cfg2 = Config(tmp_path / "config.json")
    assert cfg2.get("hotkey") == "Ctrl+Shift+X"
    assert cfg2.get("s3_secret_key") == "test_secret_123"
    assert cfg2.get("auto_start")


def test_database_operations(tmp_path):
    db = MemeDB(tmp_path / "test.db")
    mid = db.add_meme("test.png", file_hash="abc", width=100, height=200, tags=["test"])
    assert mid is not None
    assert db.count() == 1
    assert db.get_by_hash("abc") is not None
    results = db.search(keyword="test")
    assert len(results) == 1
    assert not db.is_favorite(mid)
    db.toggle_favorite(mid)
    assert db.is_favorite(mid)
    tags = db.get_meme_tags(mid)
    assert "test" in tags
    db.close()


def test_hotkey_init():
    hk = GlobalHotkey()
    result = hk.register("Ctrl+Alt+N", lambda: None)
    assert result
    hk.unregister()


def test_tray_icon():
    img = _create_default_icon()
    assert img is not None
    assert img.size == (64, 64)
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    assert len(buf.getvalue()) > 0


def _tray_menu_texts(icon):
    return [i.text for i in icon.menu]


def _stub_pystray_if_headless(monkeypatch):
    """headless CI（无 DISPLAY）导入 pystray 在模块初始化即连 X 失败，注入最小 stub；
    本机（Windows/macOS/有显示的 Linux）直接用真实 pystray"""
    try:
        import pystray  # noqa: F401

        return
    except Exception:
        pass
    import types

    from src import tray as tray_mod

    class FakeMenuItem:
        def __init__(self, text, action, default=False, enabled=True):
            self.text = text
            self.action = action
            self.default = default
            self.enabled = enabled

    class FakeMenu:
        def __init__(self, *items):
            self._items = items

        def __iter__(self):
            return iter(self._items)

    FakeMenu.SEPARATOR = FakeMenuItem(None, None)

    class FakeIcon:
        def __init__(self, name, icon=None, title=None, menu=None):
            self.name = name
            self.icon = icon
            self.title = title
            self.menu = menu

        def run(self):
            pass

        def stop(self):
            pass

    stub = types.ModuleType("pystray")
    stub.MenuItem = FakeMenuItem
    stub.Menu = FakeMenu
    stub.Icon = FakeIcon
    monkeypatch.setitem(sys.modules, "pystray", stub)
    monkeypatch.setattr(tray_mod, "_pystray_available", None)


def test_tray_menu_labels_zh(monkeypatch):
    _stub_pystray_if_headless(monkeypatch)
    from src.tray import TrayManager

    tm = TrayManager(on_show=lambda: None, on_quit=lambda: None)
    assert tm._build_icon(_create_default_icon(), "zh")
    texts = _tray_menu_texts(tm._icon)
    assert "显示/隐藏" in texts
    assert "退出" in texts


def test_tray_menu_labels_en(monkeypatch):
    _stub_pystray_if_headless(monkeypatch)
    from src.tray import TrayManager

    tm = TrayManager(on_show=lambda: None, on_quit=lambda: None)
    assert tm._build_icon(_create_default_icon(), "en")
    texts = _tray_menu_texts(tm._icon)
    assert "Show/Hide" in texts
    assert "Quit" in texts


def test_tray_menu_fallback_to_en(monkeypatch):
    _stub_pystray_if_headless(monkeypatch)
    from src import tray as tray_mod

    calls = []

    def fake_build(self, image, lang):
        calls.append(lang)
        if lang == "zh":
            return False
        self._icon = None
        self._lang = lang
        return True

    monkeypatch.setattr(tray_mod.TrayManager, "_build_icon", fake_build)
    tm = tray_mod.TrayManager()
    assert tm.start() is True
    tm.stop()
    assert calls == ["zh", "en"]
    assert tm._lang == "en"


def test_crypto():
    secrets = ["my_secret_key", "AKID1234567890", "s3cr3t!@#$%"]
    for s in secrets:
        enc = encrypt_data(s)
        dec = decrypt_data(enc)
        assert dec == s


def test_clipboard():
    result = copy_text("test")
    assert isinstance(result, bool)
    icon_path = (
        Path(__file__).resolve().parent.parent / "src" / "resources" / "icon.png"
    )
    if icon_path.exists():
        result = copy_image_to_clipboard(str(icon_path))
        assert isinstance(result, bool)


def test_webui_import():
    from src.webui import JsApi, WebUI

    w = WebUI()
    assert w._port > 0
    assert w._window is None
    assert not w.is_visible
    assert hasattr(w, "toggle_safe")
    assert hasattr(w, "show")
    assert hasattr(w, "hide")
    # JsApi
    api = JsApi(w)
    assert hasattr(api, "search_memes")
    assert hasattr(api, "get_tags")
    assert hasattr(api, "copy_meme")
    assert hasattr(api, "import_memes")


def test_find_hotkey_window_position_candidate_order():
    from src.webui import _find_hotkey_window_position

    work_area = (0, 0, 100, 100)
    assert _find_hotkey_window_position((10, 10), work_area, 30, 20) == (10, 10)
    assert _find_hotkey_window_position((80, 10), work_area, 30, 20) == (70, 10)
    assert _find_hotkey_window_position((10, 90), work_area, 30, 20) == (10, 80)
    assert _find_hotkey_window_position((80, 90), work_area, 30, 20) == (70, 80)


def test_find_hotkey_window_position_edge_equality_allowed():
    from src.webui import _find_hotkey_window_position

    assert _find_hotkey_window_position((70, 80), (0, 0, 100, 100), 30, 20) == (
        70,
        80,
    )


def test_find_hotkey_window_position_none_when_window_cannot_fit():
    from src.webui import _find_hotkey_window_position

    work_area = (-100, -50, 100, 50)
    assert _find_hotkey_window_position((0, 0), work_area, 201, 50) is None
    assert _find_hotkey_window_position((0, 0), work_area, 100, 101) is None


def test_toggle_hotkey_safe_moves_then_shows_hidden_window(monkeypatch):
    ui = _fake_webui(True)
    monkeypatch.setattr(ui, "_get_hotkey_window_position", lambda: (40, 50))

    ui.toggle_hotkey_safe()

    assert ui._window.calls == [("move", 40, 50), ("show",), ("focus",)]
    assert ui._visible
    assert ui._hotkey_session


def test_hide_clears_hotkey_session():
    ui = _fake_webui(True)
    ui.toggle_hotkey_safe()

    ui.hide()

    assert not ui._hotkey_session


def test_minimize_keeps_window_visible_for_taskbar_restore():
    ui = _fake_webui(True, visible=True)

    assert ui.minimize()

    assert ui._window.calls == [("minimize",)]
    assert ui._visible is True


def test_background_library_notifications_are_coalesced(monkeypatch):
    from src.webui import WebUI

    timers = []

    class FakeTimer:
        def __init__(self, delay, callback):
            self.delay = delay
            self.callback = callback
            self.daemon = False
            self.started = False

        def start(self):
            self.started = True
            timers.append(self)

    ui = WebUI.__new__(WebUI)
    ui._library_refresh_lock = threading.Lock()
    ui._library_refresh_timer = None
    ui._library_refresh_kind = "content"
    ui._window = mock.Mock()
    monkeypatch.setattr("src.webui.threading.Timer", FakeTimer)

    ui._on_library_changed("content")
    ui._on_library_changed("structure")

    assert len(timers) == 1
    assert timers[0].delay == 0.12
    timers[0].callback()
    ui._window.evaluate_js.assert_called_once_with(
        "window.refreshLibrary && window.refreshLibrary('structure')"
    )


def test_tray_toggle_show_does_not_mark_hotkey_session():
    ui = _fake_webui(True)

    ui.toggle_safe()

    assert ui._visible
    assert not ui._hotkey_session


def test_ordinary_show_clears_existing_hotkey_session():
    ui = _fake_webui(True)
    ui.toggle_hotkey_safe()

    ui.show()

    assert not ui._hotkey_session


def test_schedule_hide_only_hides_hotkey_session():
    ui = _fake_webui(True, visible=True)
    ui.schedule_hide()
    ui._process_pending_hide()
    assert ui._visible is True

    ui = _fake_webui(True)
    ui.toggle_hotkey_safe()
    ui.schedule_hide()
    ui._process_pending_hide()
    assert ui._visible is False


def test_schedule_hide_respects_disable_auto_hide():
    ui = _fake_webui(True)
    ui._cfg.disable_auto_hide = True
    ui.toggle_hotkey_safe()
    assert ui.schedule_hide() is False
    ui._process_pending_hide()
    assert ui._visible is True


def test_toggle_hotkey_safe_hides_visible_window_without_placement(monkeypatch):
    ui = _fake_webui(True, visible=True)

    def fail_if_called():
        raise AssertionError("visible hotkey toggle must not calculate placement")

    monkeypatch.setattr(ui, "_get_hotkey_window_position", fail_if_called)

    ui.toggle_hotkey_safe()

    assert ui._window.calls == [("hide",)]
    assert not ui._visible


def test_toggle_hotkey_safe_disabled_shows_without_placement(monkeypatch):
    ui = _fake_webui(False)

    def fail_if_called():
        raise AssertionError("disabled placement must not be calculated")

    monkeypatch.setattr(ui, "_get_hotkey_window_position", fail_if_called)

    ui.toggle_hotkey_safe()

    assert ui._window.calls == [("show",), ("focus",)]
    assert ui._visible


def test_toggle_hotkey_safe_placement_exception_still_shows(monkeypatch):
    ui = _fake_webui(True)

    def fail_placement():
        raise RuntimeError("placement unavailable")

    monkeypatch.setattr(ui, "_get_hotkey_window_position", fail_placement)

    ui.toggle_hotkey_safe()

    assert ui._window.calls == [("show",), ("focus",)]
    assert ui._visible


def test_successful_native_drag_requests_hide_only_for_hotkey_session(monkeypatch):
    import src.native_drag as native_drag
    from src.webui import JsApi

    ui = _fake_webui(True)
    ui._api = JsApi(ui)
    ui._api._db = _FakeMemeDB()
    monkeypatch.setattr(ui._api, "_daily_meme_path", lambda row: row["filename"])
    monkeypatch.setattr(native_drag, "start_native_drag", lambda path: True)
    monkeypatch.setattr(ui, "_run_on_gui", lambda delay, func: func())

    ui.show()
    assert ui._api.start_native_drag(1)
    assert ui._visible is True

    ui.hide()
    ui.toggle_hotkey_safe()
    assert ui._api.start_native_drag(1)
    assert ui._visible is False


def test_chat_record_drag_sends_directly_to_resolved_qq_group(monkeypatch):
    import src.native_drag as native_drag
    import src.qq_drag as qq_drag
    import src.webui as webui_module
    from src.webui import JsApi

    ui = type("DragWebUI", (), {})()
    ui.schedule_hide = mock.Mock()
    api = JsApi.__new__(JsApi)
    api._webui = ui
    api._cfg = _FakeConfig(False)
    api._cfg.saved.update(
        {
            "napcat_http_url": "http://127.0.0.1:3000",
            "napcat_token": "token",
            "napcat_send_mode": "archive_rebuild",
        }
    )
    api._cfg.get = lambda key, default=None: api._cfg.saved.get(key, default)
    api._db = type(
        "ChatDB",
        (),
        {"get_chat_record_entry": lambda self, record_id: {"id": record_id}},
    )()
    monkeypatch.setattr(
        native_drag,
        "start_chat_record_target_drag",
        lambda record_id: {
            "ok": True,
            "window_handle": 123,
            "process_name": "QQ",
        },
    )
    monkeypatch.setattr(
        qq_drag,
        "resolve_qq_target",
        lambda *args: {
            "message_type": "group",
            "peer_id": "456",
            "label": "测试群",
        },
    )
    sent = {}

    def fake_send(
        http_url,
        token,
        record_id,
        event,
        db=None,
        send_mode=None,
        webui_url="",
        webui_token="",
    ):
        sent.update(
            {
                "http_url": http_url,
                "token": token,
                "record_id": record_id,
                "event": event,
                "db": db,
                "send_mode": send_mode,
            }
        )
        return {"source": send_mode, "degraded_media_count": 0}

    monkeypatch.setattr(webui_module.onebot, "send_chat_record", fake_send)

    result = api.start_chat_record_drag(7)

    assert result["ok"]
    assert result["target_label"] == "测试群"
    assert sent["event"] == {"message_type": "group", "group_id": "456"}
    assert sent["send_mode"] == "archive_rebuild"
    ui.schedule_hide.assert_called_once()


def test_static_image_drag_to_qq_sends_napcat_sticker(monkeypatch):
    import src.native_drag as native_drag
    import src.qq_drag as qq_drag
    import src.webui as webui_module
    from src.webui import JsApi

    ui = type("DragWebUI", (), {})()
    ui.schedule_hide = mock.Mock()
    api = JsApi.__new__(JsApi)
    api._webui = ui
    api._cfg = _FakeConfig(False)
    api._cfg.saved.update(
        {
            "napcat_http_url": "http://127.0.0.1:3000",
            "napcat_token": "token",
            "napcat_image_send_mode": "sticker",
        }
    )
    api._cfg.get = lambda key, default=None: api._cfg.saved.get(key, default)
    used = []
    api._db = type(
        "ImageDB",
        (),
        {
            "get_by_id": lambda self, meme_id: {
                "id": meme_id,
                "filename": "a.png",
            },
            "record_use": lambda self, meme_id: used.append(meme_id),
        },
    )()
    monkeypatch.setattr(api, "_daily_meme_path", lambda row: row["filename"])
    native_meme_drag = mock.Mock(
        side_effect=lambda path, intercept_qq=False: {
            "ok": True,
            "qq_target": {
                "window_handle": 123,
                "process_name": "QQ",
                "x": 1,
                "y": 2,
            },
            "intercept_qq": intercept_qq,
        },
    )
    monkeypatch.setattr(native_drag, "start_meme_drag", native_meme_drag)
    monkeypatch.setattr(
        qq_drag,
        "resolve_qq_target",
        lambda *args: {
            "message_type": "group",
            "peer_id": "456",
            "label": "测试群",
            "source": "napcat_plugin",
        },
    )
    sent = mock.Mock(return_value={"ok": True})
    monkeypatch.setattr(webui_module.onebot, "send_meme_image", sent)

    result = api.start_meme_drag(7)

    assert result["ok"]
    assert result["recent_recorded"]
    assert result["sent_as_sticker"]
    assert result["target_label"] == "测试群"
    native_meme_drag.assert_called_once_with("a.png", intercept_qq=True)
    sent.assert_called_once_with(
        "http://127.0.0.1:3000",
        "token",
        "a.png",
        "group",
        "456",
        as_sticker=True,
    )
    assert used == [7]
    ui.schedule_hide.assert_called_once()


def test_successful_copy_requests_hide_only_for_hotkey_session(monkeypatch):
    import src.webui as webui_module
    from src.webui import JsApi

    ui = _fake_webui(True)
    ui._api = JsApi(ui)
    ui._api._db = _FakeMemeDB()
    monkeypatch.setattr(ui._api, "_daily_meme_path", lambda row: row["filename"])
    monkeypatch.setattr(webui_module, "copy_image_to_clipboard", lambda path: True)
    monkeypatch.setattr(ui, "_run_on_gui", lambda delay, func: func())

    ui.show()
    result = ui._api.copy_meme(1)
    assert result["ok"]
    assert result["status"] == "copied"
    assert ui._visible is True

    ui.hide()
    ui.toggle_hotkey_safe()
    assert ui._api.copy_meme(1)
    assert ui._visible is False


def _start_fake_webui(monkeypatch, silent_start):
    import types

    import src.webui as webui_module

    created = []

    def create_window(*args, **kwargs):
        window = _FakeWindow()
        created.append((window, kwargs))
        return window

    monkeypatch.setattr(webui_module, "HAS_WEBVIEW", True)
    monkeypatch.setattr(webui_module, "HAS_BOTTLE", True)
    monkeypatch.setattr(
        webui_module,
        "webview",
        types.SimpleNamespace(
            create_window=create_window,
            start=lambda **kwargs: None,
        ),
    )
    monkeypatch.setattr(webui_module.time, "sleep", lambda _: None)

    from src.webui import WebUI

    ui = WebUI(silent_start=silent_start)
    ui._cfg = _FakeConfig(True)
    monkeypatch.setattr(ui, "_setup_bottle", lambda: None)
    monkeypatch.setattr(ui, "_init_lan", lambda: None)
    assert ui.start()
    return ui, created


def test_webui_start_normal_visibility_hides_without_placement(monkeypatch):
    ui, created = _start_fake_webui(monkeypatch, silent_start=False)
    assert created[0][1]["hidden"] is False
    assert ui._visible is True

    def fail_if_called():
        raise AssertionError("visible hotkey toggle must not calculate placement")

    monkeypatch.setattr(ui, "_get_hotkey_window_position", fail_if_called)
    ui.toggle_hotkey_safe()

    assert ui._window.calls == [("hide",)]
    assert ui._visible is False


def test_webui_start_silent_visibility_allows_hotkey_placement(monkeypatch):
    ui, created = _start_fake_webui(monkeypatch, silent_start=True)
    assert created[0][1]["hidden"] is True
    assert ui._visible is False
    monkeypatch.setattr(ui, "_get_hotkey_window_position", lambda: (40, 50))

    ui.toggle_hotkey_safe()

    assert ui._window.calls == [("move", 40, 50), ("show",), ("focus",)]
    assert ui._visible is True


def test_app_routes_hotkey_and_tray_to_separate_zero_argument_methods():
    import inspect

    from src.main import OhMyMemeApp

    class FakeWebUI:
        def __init__(self):
            self.calls = []

        def toggle_hotkey_safe(self):
            self.calls.append("hotkey")

        def toggle_safe(self):
            self.calls.append("tray")

    app = OhMyMemeApp.__new__(OhMyMemeApp)
    app._webui = FakeWebUI()

    app._on_hotkey()
    assert app._webui.calls == ["hotkey"]

    app._on_tray_show()

    assert app._webui.calls == ["hotkey", "tray"]
    assert len(inspect.signature(app._on_hotkey).parameters) == 0
    assert len(inspect.signature(app._on_tray_show).parameters) == 0


def test_webui_html_exists():
    from src.webui import HTML_DIR

    assert HTML_DIR.exists()
    assert (HTML_DIR / "index.html").exists()
    html = (HTML_DIR / "index.html").read_text(encoding="utf-8")
    assert "OhMyMeme" in html
    assert "search" in html
    # 前端样式/脚本已从 HTML 拆分为独立静态文件
    assert (HTML_DIR / "index.css").exists()
    assert (HTML_DIR / "index.js").exists()
    assert (HTML_DIR / "settings.html").exists()
    assert (HTML_DIR / "settings.css").exists()
    assert (HTML_DIR / "settings.js").exists()
    assert 'src="/index.js"' in html
    assert 'href="/index.css"' in html
    settings_html = (HTML_DIR / "settings.html").read_text(encoding="utf-8")
    assert 'src="/settings.js"' in settings_html
    assert 'href="/settings.css"' in settings_html
    assert 'id="s-hotkey-show-at-mouse"' in settings_html
    assert 'id="s-napcat-image-send-mode"' in settings_html
    settings_js = (HTML_DIR / "settings.js").read_text(encoding="utf-8")
    assert settings_js.count("s.hotkey_show_at_mouse === true") == 2
    assert "hotkey_show_at_mouse," in settings_js
    assert "napcat_image_send_mode" in settings_js


def test_manifest_include_tags_settings_contract():
    """设置页「将标签写入同步清单」开关（HTML 复选框 + JS 读写 + 后端配置键）"""
    from src.config import Config
    from src.webui import HTML_DIR, SettingsApi

    assert Config.DEFAULTS.get("manifest_include_tags") is True
    settings_html = (HTML_DIR / "settings.html").read_text(encoding="utf-8")
    assert 'id="s-manifest-include-tags"' in settings_html
    settings_js = (HTML_DIR / "settings.js").read_text(encoding="utf-8")
    assert "s.manifest_include_tags !== false" in settings_js
    assert "manifest_include_tags," in settings_js
    import inspect

    src = inspect.getsource(SettingsApi.get_settings)
    assert '"manifest_include_tags"' in src
    reset_src = inspect.getsource(SettingsApi.reset_settings)
    assert '"manifest_include_tags": True' in reset_src


def test_manifest_include_favorites_settings_contract():
    """设置页「将收藏夹写入同步清单」开关（HTML 复选框 + JS 读写 + 后端配置键）"""
    from src.config import Config
    from src.webui import HTML_DIR, SettingsApi

    assert Config.DEFAULTS.get("manifest_include_favorites") is True
    settings_html = (HTML_DIR / "settings.html").read_text(encoding="utf-8")
    assert 'id="s-manifest-include-favorites"' in settings_html
    settings_js = (HTML_DIR / "settings.js").read_text(encoding="utf-8")
    assert "s.manifest_include_favorites !== false" in settings_js
    assert "manifest_include_favorites," in settings_js
    import inspect

    src = inspect.getsource(SettingsApi.get_settings)
    assert '"manifest_include_favorites"' in src
    reset_src = inspect.getsource(SettingsApi.reset_settings)
    assert '"manifest_include_favorites": True' in reset_src


def test_cloud_thumb_auto_push_settings_contract():
    """设置页「启动时自动补传缺失的云端缩略图」开关（HTML 复选框 + JS 读写 + 后端配置键）"""
    import inspect

    from src.config import Config
    from src.webui import HTML_DIR, SettingsApi

    assert Config.DEFAULTS.get("cloud_direct") is True
    assert Config.DEFAULTS.get("cloud_thumb_auto_push") is True
    settings_html = (HTML_DIR / "settings.html").read_text(encoding="utf-8")
    assert 'id="s-cloud-thumb-push"' in settings_html
    settings_js = (HTML_DIR / "settings.js").read_text(encoding="utf-8")
    assert "s.cloud_thumb_auto_push !== false" in settings_js
    assert "cloud_thumb_auto_push:" in settings_js
    src = inspect.getsource(SettingsApi.get_settings)
    assert '"cloud_thumb_auto_push"' in src
    reset_src = inspect.getsource(SettingsApi.reset_settings)
    assert '"cloud_thumb_auto_push": True' in reset_src
    assert '"cloud_direct": True' in reset_src


def test_disable_auto_hide_settings_contract():
    """设置页「关闭自动隐藏」开关（HTML 复选框 + JS 读写 + 后端配置键）"""
    import inspect

    from src.config import Config
    from src.webui import HTML_DIR, SettingsApi

    assert Config.DEFAULTS.get("disable_auto_hide") is False
    settings_html = (HTML_DIR / "settings.html").read_text(encoding="utf-8")
    assert 'id="s-disable-auto-hide"' in settings_html
    settings_js = (HTML_DIR / "settings.js").read_text(encoding="utf-8")
    assert settings_js.count("s.disable_auto_hide === true") == 2
    assert "disable_auto_hide," in settings_js
    src = inspect.getsource(SettingsApi.get_settings)
    assert '"disable_auto_hide"' in src
    reset_src = inspect.getsource(SettingsApi.reset_settings)
    assert '"disable_auto_hide": False' in reset_src


def test_cloud_direct_confirm_buttons_contract():
    """首次配置云端的弹窗按钮为「开启/关闭」（showConfirm 第 3/4 参），Esc/遮罩返回 null 不改动"""
    from src.webui import HTML_DIR

    settings_js = (HTML_DIR / "settings.js").read_text(encoding="utf-8")
    assert re.search(
        r"'云端直接使用'[\s\S]{0,400}?'开启'\s*,\s*'关闭'", settings_js
    ), "云端直接使用确认弹窗必须用「开启/关闭」按钮"
    show_confirm = re.search(
        r"function showConfirm\([\s\S]*?\n}", settings_js
    ).group(0)
    assert "okText = '确定'" in show_confirm
    assert "cancelText = '取消'" in show_confirm
    assert "resolve(null)" in show_confirm  # Esc/遮罩 = 不改动


def test_about_links_static_contract():
    """关于页 GitHub/QQ 群外链按钮（HTML 按钮 + JS URL 常量 + open_url 接口）"""
    from src.webui import HTML_DIR

    settings_html = (HTML_DIR / "settings.html").read_text(encoding="utf-8")
    settings_js = (HTML_DIR / "settings.js").read_text(encoding="utf-8")
    assert "openAboutUrl('github')" in settings_html
    assert "openAboutUrl('qq')" in settings_html
    assert "https://github.com/TNTXZ/OhMyMeme" in settings_js
    assert "qm.qq.com/cgi-bin/qm/qr" in settings_js
    assert "api('open_url'" in settings_js


def test_open_url_rejects_non_http():
    """open_url 仅允许 http(s)，file/javascript 等 scheme 一律拒绝"""
    from src.webui import SettingsApi

    assert SettingsApi.open_url(None, "file:///etc/passwd") is False
    assert SettingsApi.open_url(None, "javascript:alert(1)") is False
    assert SettingsApi.open_url(None, "") is False
    assert SettingsApi.open_url(None, None) is False


def test_open_url_dispatches_to_default_browser(monkeypatch):
    """https 链接交给系统默认浏览器打开"""
    import os

    import src.webui as webui_module

    calls = []
    monkeypatch.setattr(webui_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(os, "startfile", lambda u: calls.append(u), raising=False)
    ok = webui_module.SettingsApi.open_url(None, "https://github.com/TNTXZ/OhMyMeme")
    assert ok is True
    assert calls == ["https://github.com/TNTXZ/OhMyMeme"]


def test_wechat_dialog_user_agreement_warning():
    """微信导入对话框须常驻用户协议警告（合规提示，防被删）"""
    from src.webui import HTML_DIR

    html = (HTML_DIR / "settings.html").read_text(encoding="utf-8")
    seg = html.split('id="wechat-config"', 1)
    assert len(seg) == 2
    warn_seg = seg[1].split('id="wechat-progress"', 1)[0]
    assert "该功能可能不符合微信用户协议，请谨慎使用！" in warn_seg


def test_sorting_visual_feedback_static_contract():
    root = Path(__file__).resolve().parent.parent
    index_js = (root / "src" / "webui" / "index.js").read_text(encoding="utf-8")
    index_css = (root / "src" / "webui" / "index.css").read_text(encoding="utf-8")
    initial_state = index_js.split("function ", 1)[0]

    assert re.search(
        r"\bcollections\s*=\s*\[\]", initial_state
    ), "the initial view must initialize without collections"
    assert re.search(
        r"\bactiveCollection\s*=\s*null\b", initial_state
    ), "the initial view must remain the all-memes view"

    grid_wrap = re.search(r"#grid-wrap\s*\{(?:(?!\}).)*?\}", index_css, re.DOTALL)
    assert grid_wrap, "grid wrapper styles must remain locally inspectable"
    assert re.search(r"overflow-y\s*:\s*scroll", grid_wrap.group(0))
    assert re.search(r"overflow-x\s*:\s*hidden", grid_wrap.group(0))

    meme_grid = re.search(r"#meme-grid\s*\{(?:(?!\}).)*?\}", index_css, re.DOTALL)
    assert meme_grid, "meme grid styles must remain locally inspectable"
    grid_padding = re.search(r"padding\s*:\s*([^;]+)", meme_grid.group(0))
    assert grid_padding, "meme grid must reserve space for sorting outlines"
    padding_values = [
        float(value)
        for value in re.findall(r"(?:^|\s)(\d+(?:\.\d+)?)px", grid_padding.group(1))
    ]
    assert len(padding_values) in (1, 2, 3, 4)
    if len(padding_values) == 1:
        padding_top = padding_right = padding_bottom = padding_left = padding_values[0]
    elif len(padding_values) == 2:
        padding_top = padding_bottom = padding_values[0]
        padding_right = padding_left = padding_values[1]
    elif len(padding_values) == 3:
        padding_top, padding_right, padding_bottom = padding_values
        padding_left = padding_right
    else:
        padding_top, padding_right, padding_bottom, padding_left = padding_values
    assert padding_top >= 6
    assert padding_right >= 6
    assert padding_left >= 6
    assert padding_bottom >= 6

    card_rule = re.search(r"\.meme-card\s*\{(?:(?!\}).)*?\}", index_css, re.DOTALL)
    assert card_rule, "meme card base styles must remain locally inspectable"
    assert re.search(r"outline\s*:\s*0\s+solid\s+transparent", card_rule.group(0))
    assert re.search(r"outline-offset\s*:\s*3px", card_rule.group(0))
    transition = re.search(r"transition\s*:\s*([^;]+)", card_rule.group(0))
    assert transition, "meme card transitions must remain locally inspectable"
    transition_value = transition.group(1)
    for property_name in ("transform", "outline-color", "outline-width"):
        assert re.search(rf"\b{property_name}\s+var\(--transition\)", transition_value)

    render_grid = re.search(
        r"function renderGrid\(\)\s*\{(?:(?!\n\}).)*\n\}", index_js, re.DOTALL
    )
    assert render_grid, "renderGrid must remain locally inspectable"
    render_grid_body = render_grid.group(0)
    assert re.search(r"const\s+sortEnabled\s*=\s*canReorderMemes\(\)", render_grid_body)
    remove_sort = re.search(
        r"grid\.classList\.remove\(\s*['\"]sort-enabled['\"]\s*\)",
        render_grid_body,
    )
    clear_grid = re.search(r"grid\.innerHTML\s*=\s*['\"]['\"]", render_grid_body)
    assert remove_sort and clear_grid
    assert remove_sort.start() < clear_grid.start()

    animation = re.search(
        r"requestAnimationFrame\(\s*\(\)\s*=>\s*\{(?P<body>.*?)\}\s*\)\s*;",
        render_grid_body,
        re.DOTALL,
    )
    assert animation, "sorting state must be applied in requestAnimationFrame"
    animation_body = animation.group("body")
    assert re.search(
        r"if\s*\(\s*renderToken\s*!==\s*gridRenderToken\s*\)\s*return\s*;",
        animation_body,
    ), "stale render callbacks must be ignored"
    assert re.search(
        r"grid\.classList\.toggle\(\s*['\"]sort-enabled['\"]\s*,\s*sortEnabled\s*\)",
        animation_body,
    )

    can_reorder = re.search(
        r"function canReorderMemes\(\)\s*\{(?:(?!\n\}).)*\n\}", index_js, re.DOTALL
    )
    assert can_reorder, "sorting eligibility must remain locally inspectable"
    can_reorder_body = can_reorder.group(0)
    assert re.search(
        r"if\s*\(\s*q\s*\|\|\s*activeTags\s*\.\s*size\s*>\s*0\s*\)\s*"
        r"return\s+false\s*;",
        can_reorder_body,
    )
    assert re.search(
        r"if\s*\(\s*!\s*dragSortEnabled\s*\)\s*return\s+false\s*;",
        can_reorder_body,
    )
    assert re.search(
        r"return\s+activeCollection\s*===\s*null\s*\|\|\s*"
        r"activeCollection\s*===\s*-4\s*\|\|\s*"
        r"activeCollection\s*>\s*0\s*;",
        can_reorder_body,
    )
    assert re.search(
        r"activeCollection\s*>\s*0[\s\S]*?api\(\s*['\"]reorder_collection_members['\"]\s*,\s*activeCollection",
        index_js,
    ), (
        "sortable collections must persist their member order through the "
        "active collection"
    )
    assert re.search(
        r"api\(\s*['\"]reorder_memes['\"]\s*,\s*memes\.map\([^)]*\.id\s*\)",
        index_js,
    ), "the all-memes view must persist its global order through reorder_memes"

    normal_card_selector = (
        "#meme-grid.sort-enabled .meme-card:not(.folder-card):not(.dragging)"
    )
    sorting_rule = re.search(
        re.escape(normal_card_selector) + r"\s*\{(?:(?!\}).)*?\}",
        index_css,
        re.DOTALL,
    )
    assert (
        sorting_rule
    ), "sorting feedback must target only ordinary, non-dragging cards"
    assert re.search(r"transform\s*:\s*scale\(0\.95\)", sorting_rule.group(0))
    assert re.search(
        r"outline\s*:\s*3px\s+solid\s+var\(--border-light\)", sorting_rule.group(0)
    )
    assert re.search(r"outline-offset\s*:\s*[^;]+", sorting_rule.group(0))

    shake_selector = (
        "#meme-grid.sort-enabled:not(.drag-active) "
        ".meme-card:not(.folder-card):not(.dragging):not(.sort-enter)"
    )
    shake_rule = re.search(
        re.escape(shake_selector) + r"\s*\{(?:(?!\}).)*?\}",
        index_css,
        re.DOTALL,
    )
    assert shake_rule, "sorting shake must exclude drag, FLIP, folder, and entry states"
    assert re.search(r"animation\s*:\s*sort-shake\s+[^;]+", shake_rule.group(0))
    assert "transform" not in shake_rule.group(0), (
        "sorting shake must use the independent rotate property so it cannot replace "
        "drag or FLIP transforms"
    )
    shake_keyframes = re.search(
        r"@keyframes\s+sort-shake\s*\{(?P<body>.*?)\n\}",
        index_css,
        re.DOTALL,
    )
    assert shake_keyframes, "sorting shake must define named keyframes"
    assert re.search(r"rotate\s*:\s*-?0\.75deg", shake_keyframes.group("body"))
    assert re.search(r"rotate\s*:\s*0\.75deg", shake_keyframes.group("body"))
    reduced_motion = re.search(
        r"@media\s*\(prefers-reduced-motion\s*:\s*reduce\)\s*\{(?P<body>.*?)\n\}",
        index_css,
        re.DOTALL,
    )
    assert reduced_motion, "sorting shake must respect reduced-motion preferences"
    assert shake_selector in reduced_motion.group("body")
    assert re.search(r"animation\s*:\s*none", reduced_motion.group("body"))
    assert re.search(r"rotate\s*:\s*0deg", reduced_motion.group("body"))

    toggle_sort = re.search(
        r"function toggleDragSort\(\)\s*\{(?P<body>.*?)\n\}",
        index_js,
        re.DOTALL,
    )
    assert toggle_sort, "toolbar sort toggle must remain locally inspectable"
    toggle_sort_body = toggle_sort.group("body")
    assert re.search(r"if\s*\(\s*dragSortEnabled\s*\)", toggle_sort_body)
    enable_branch = re.search(
        r"if\s*\(\s*dragSortEnabled\s*\)\s*\{(?P<body>.*?)\}",
        toggle_sort_body,
        re.DOTALL,
    )
    assert enable_branch, "enabling sort must have a distinct branch"
    assert re.search(r"refreshMemes\s*\(\s*\)", enable_branch.group("body"))

    disable_branch = toggle_sort_body[enable_branch.end() :]
    assert not re.search(r"refreshMemes\s*\(\s*\)", disable_branch)
    assert not re.search(r"grid\.innerHTML\s*=", disable_branch)
    assert re.search(r"\+\+\s*gridRenderToken", disable_branch)
    disable_animation = re.search(
        r"requestAnimationFrame\(\s*\(\)\s*=>\s*\{(?P<body>.*?)\}\s*\)",
        disable_branch,
        re.DOTALL,
    )
    assert disable_animation, "disabling sort must defer exit feedback removal"
    disable_animation_body = disable_animation.group("body")
    assert re.search(
        r"if\s*\(\s*\w+\s*!==\s*gridRenderToken\s*\|\|\s*dragSortEnabled\s*\)\s*return\s*;",
        disable_animation_body,
    )
    assert re.search(
        r"grid\.classList\.remove\(\s*['\"]sort-enabled['\"]\s*\)",
        disable_animation_body,
    )

    sort_enter_selector = (
        "#meme-grid.sort-enabled .meme-card.sort-enter:not(.folder-card):not(.dragging)"
    )
    sort_enter_rule = re.search(
        re.escape(sort_enter_selector) + r"\s*\{(?:(?!\}).)*?\}",
        index_css,
        re.DOTALL,
    )
    assert (
        sort_enter_rule
    ), "pagination entry feedback must have a matching CSS baseline"
    assert re.search(r"transform\s*:\s*scale\(1\)", sort_enter_rule.group(0))
    assert re.search(r"outline\s*:\s*0\s+solid\s+transparent", sort_enter_rule.group(0))
    assert re.search(r"outline-offset\s*:\s*3px", sort_enter_rule.group(0))

    load_more = re.search(
        r"async function loadMoreMemes\(\)\s*\{.*?\n\}\s*\n\s*"
        r"async function refreshTags",
        index_js,
        re.DOTALL,
    )
    assert load_more, "pagination loader must remain locally inspectable"
    load_more_body = load_more.group(0)
    assert re.search(r"if\s*\(\s*sortEnabled\s*\)\s*cards\.forEach\(", load_more_body)
    assert re.search(
        r"cards\.forEach\(\s*card\s*=>\s*card\.classList\.add\(\s*['\"]sort-enter['\"]\s*\)\s*\)",
        load_more_body,
    )
    assert re.search(
        r"requestAnimationFrame\(\s*\(\)\s*=>\s*\{\s*cards\.forEach\(\s*card\s*=>\s*card\.classList\.remove\(\s*['\"]sort-enter['\"]\s*\)\s*\)\s*;?\s*\}\s*\)",
        load_more_body,
        re.DOTALL,
    )
    layout_read = (
        r"(?:getBoundingClientRect\(\)|offset(?:Width|Height)|client(?:Width|Height))"
    )
    pagination_append = re.search(
        r"cards\.forEach\(\s*card\s*=>\s*grid\.appendChild\(\s*card\s*\)\s*\)",
        load_more_body,
    )
    pagination_animation = re.search(r"requestAnimationFrame\(", load_more_body)
    assert pagination_append and pagination_animation
    assert re.search(
        layout_read,
        load_more_body[pagination_append.end() : pagination_animation.start()],
    ), "pagination sort baseline must commit layout before requestAnimationFrame"

    drag_scales = [
        float(scale)
        for scale in re.findall(
            r"d\.card\.style\.transform\s*=\s*['\"][^'\"]*?translate\([^)]*\)\s*"
            r"scale\((0?\.\d+)\)",
            index_js,
        )
    ]
    assert drag_scales == [0.90, 0.90]
    assert re.search(
        r"c\.style\.transform\s*=\s*[^;]+?scale\(0\.95\)",
        index_js,
    ), "FLIP displacement must preserve the sorting-mode baseline scale"
    assert not re.search(r"scale\([^)]*\)\s*scale\(", index_js)
    assert (
        "#meme-grid.drag-active .meme-card:not(.folder-card):not(.dragging)"
        in index_css
    )

    grid_metrics = re.search(
        r"function gridMetrics\(\)\s*\{(?:(?!\n\}).)*\n\}", index_js, re.DOTALL
    )
    assert grid_metrics, "grid slot metrics must remain locally inspectable"
    assert "offsetWidth" in grid_metrics.group(0)
    assert "offsetHeight" in grid_metrics.group(0), (
        "grid slot geometry must use untransformed layout dimensions so sorting scale "
        "cannot shift drag insertion slots"
    )
    grid_metrics_body = grid_metrics.group(0)
    assert re.search(r"getComputedStyle\(\s*grid\s*\)", grid_metrics_body)
    assert re.search(r"padding(?:Left|Right)", grid_metrics_body)
    assert re.search(
        r"(?:grid\.clientWidth|(?:gRect|gridRect)\.width)\s*-\s*"
        r"[^;]*padding(?:Left|left)[^;]*padding(?:Right|right)",
        grid_metrics_body,
    ), "grid columns must use usable width after computed horizontal padding"

    grid_slot = re.search(
        r"function gridSlotIndex\(x, y\)\s*\{(?:(?!\n\}).)*\n\}",
        index_js,
        re.DOTALL,
    )
    assert grid_slot, "grid slot index must remain locally inspectable"
    grid_slot_body = grid_slot.group(0)
    assert "originX" in grid_metrics_body and "originY" in grid_metrics_body
    assert re.search(r"x\s*-\s*originX", grid_slot_body)
    assert re.search(r"y\s*-\s*originY", grid_slot_body)
    assert not re.search(r"gRect\.left|gRect\.top", grid_slot_body)
    initial_append = re.search(
        r"memes\.forEach\(\s*m\s*=>\s*grid\.appendChild\(\s*renderMemeCard\(\s*m\s*\)\s*\)\s*\)",
        render_grid_body,
    )
    initial_animation = re.search(r"requestAnimationFrame\(", render_grid_body)
    assert initial_append and initial_animation
    assert re.search(
        layout_read,
        render_grid_body[initial_append.end() : initial_animation.start()],
    ), "initial sort baseline must commit layout before requestAnimationFrame"


def test_grid_slot_hit_testing_stays_aligned_when_layout_moves_and_scrolls():
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["node", root / "tests" / "fixtures" / "grid_slot_probe.cjs"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "grid slot behavior: PASS"


def test_webui_safe_serve_filename():
    from src.webui import _safe_serve_filename

    for name in ("a.png", "表情.webp", "a b.gif", "abc123.gif"):
        assert _safe_serve_filename(name)
    for name in (
        "../evil.png",
        "..",
        ".",
        "dir/file.png",
        "/etc/passwd",
        "\\win\\x.png",
        "~/x.png",
        "",
        ".hidden",
    ):
        assert not _safe_serve_filename(name)


def test_webui_host_allowed():
    from src.webui import _host_allowed

    assert _host_allowed("127.0.0.1", 12345)
    assert _host_allowed("127.0.0.1:12345", 12345)
    assert _host_allowed("localhost:12345", 12345)
    assert not _host_allowed("evil.example.com", 12345)
    assert not _host_allowed("evil.example.com:12345", 12345)
    assert not _host_allowed("127.0.0.1:9999", 12345)
    assert not _host_allowed("", 12345)


def test_storage_dir_validation(tmp_path):
    from src.webui import _storage_dir_validation

    old = tmp_path / "old"
    old.mkdir()
    (old / "sub").mkdir()
    assert _storage_dir_validation(None, str(old))[0] is False
    assert _storage_dir_validation("", str(old))[0] is False
    assert _storage_dir_validation("rel/path", str(old))[0] is False
    assert _storage_dir_validation(str(old), str(old))[0] is False
    assert _storage_dir_validation(str(old / "sub"), str(old))[0] is False
    assert _storage_dir_validation(str(tmp_path), str(old))[0] is False
    assert _storage_dir_validation(str(tmp_path / "new"), str(old))[0] is True

    data = tmp_path / "data"
    assert _storage_dir_validation(str(data), str(old), (data,))[0] is False
    assert _storage_dir_validation(str(data / "x"), str(old), (data,))[0] is False
    assert _storage_dir_validation(str(tmp_path), str(old), (data,))[0] is False
    assert _storage_dir_validation(str(tmp_path / "ok"), str(old), (data,))[0] is True


def test_storage_dir_virtual_drive_compat_is_opt_in(monkeypatch, tmp_path):
    from src.webui import _probe_storage_dir, _storage_dir_validation

    old = tmp_path / "old"
    old.mkdir()
    new = tmp_path / "virtual"

    def fail_resolve(*_args, **_kwargs):
        raise OSError("virtual filesystem does not support resolve")

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    assert _storage_dir_validation(str(new), str(old))[0] is False
    assert _storage_dir_validation(str(new), str(old), allow_unresolved=True)[0] is True

    new.mkdir()
    assert _probe_storage_dir(new) == (True, "")
    assert list(new.iterdir()) == []


def test_virtual_drive_compat_settings_contract():
    root = Path(__file__).resolve().parent.parent
    settings_html = (root / "src" / "webui" / "settings.html").read_text(
        encoding="utf-8"
    )
    settings_js = (root / "src" / "webui" / "settings.js").read_text(encoding="utf-8")

    assert 'id="s-virtual-drive-compat"' in settings_html
    assert 'id="s-bulk-read-protection"' in settings_html
    assert 'id="s-bulk-read-prefetch-pages"' in settings_html
    assert 'id="s-bulk-read-batch-size"' in settings_html
    assert 'id="s-bulk-read-pause-seconds"' in settings_html
    assert "s.virtual_drive_compat === true" in settings_js
    assert (
        "show_startup_animation, virtual_drive_compat, bulk_read_protection"
        in settings_js
    )
    assert (
        "bulk_read_batch_size, bulk_read_pause_seconds, bulk_read_prefetch_pages"
        in settings_js
    )
    assert "s.bulk_read_protection === true" in settings_js
    assert "s.bulk_read_prefetch_pages === true" in settings_js
    assert "toggleBulkReadOptions()" in settings_js
    assert "pendingStorageDir, move, compat" in settings_js


def test_genre_multiselect_and_random_sidebar_interactions():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "esbuild").is_dir():
        pytest.skip("随机页前端交互测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Module = require('node:module');
const esbuild = require('esbuild');
const calls = [];
global.pywebview = { api: {
  search_library: async (...args) => {
    calls.push(args);
    return { memes: [], chat_records: [], chat_total: 0, total: 0 };
  },
  start_page_cache_warmup: async () => ({}),
} };
const filename = path.resolve('src/vue-src/composables/useMemes.ts');
const compiled = esbuild.buildSync({
  entryPoints: [filename], bundle: true, platform: 'node', format: 'cjs',
  external: ['vue'], write: false,
}).outputFiles[0].text;
const moduleInstance = new Module(filename, module);
moduleInstance.filename = filename;
moduleInstance.paths = Module._nodeModulePaths(path.dirname(filename));
moduleInstance._compile(compiled, filename);
const { state, setActiveCollection, setActiveCategory, toggleGenre, search, canReorder } =
  moduleInstance.exports.useMemes();
const app = fs.readFileSync('src/vue-src/App.vue', 'utf8');
const handler = app.match(/function onSidebarSelect\(id: number\) \{[\s\S]*?\n\}/)[0];
const javascript = esbuild.transformSync(handler, { loader: 'ts' }).code;
const context = { sidebarTab: { value: 'collections' }, state,
  setActiveCollection, setActiveCategory };
vm.createContext(context);
vm.runInContext(javascript, context);
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  assert.deepEqual([...state.activeGenres], ['image', 'gif', 'chat_record']);
  state.activeCollection = 42;
  state.searchQuery = 'kept search';
  state.activeTags.add('kept tag');
  state.page = 4;
  toggleGenre('gif');
  await settle();
  assert.deepEqual([...state.activeGenres], ['image', 'chat_record']);
  assert.deepEqual(calls.at(-1)[6], ['image', 'chat_record']);
  assert.equal(state.page, 1);
  assert.equal(state.activeCollection, 42);
  assert.equal(state.searchQuery, 'kept search');
  assert.ok(state.activeTags.has('kept tag'));
  toggleGenre('image');
  await settle();
  assert.deepEqual([...state.activeGenres], ['chat_record']);
  assert.equal(state.activeCollection, 42);
  assert.equal(canReorder(), false);
  toggleGenre('chat_record');
  await settle();
  assert.deepEqual(calls.at(-1)[6], []);
  assert.equal(canReorder(), false);
  state.activeCollection = null;
  state.activeCategory = 21;
  toggleGenre('gif');
  await settle();
  assert.deepEqual(calls.at(-1)[6], ['gif']);
  assert.equal(state.activeCategory, 21);
  toggleGenre('image');
  await settle();
  assert.deepEqual(calls.at(-1)[6], ['image', 'gif']);
  toggleGenre('chat_record');
  await settle();
  assert.deepEqual(calls.at(-1)[6], ['image', 'gif', 'chat_record']);
  state.searchQuery = 'old search';
  state.activeTags.add('old tag');
  state.activeGenres = ['chat_record'];
  context.onSidebarSelect(-5);
  await settle();
  assert.equal(state.activeCollection, -5);
  assert.equal(state.searchQuery, '');
  assert.equal(state.activeTags.size, 0);
  assert.deepEqual([...state.activeGenres], ['image', 'gif', 'chat_record']);
  assert.equal(canReorder(), false);
  assert.equal(calls.at(-1)[7], false);

  context.onSidebarSelect(-5);
  await settle();
  assert.equal(state.activeCollection, -5);
  assert.equal(calls.at(-1)[7], true);

  context.onSidebarSelect(-4);
  await settle();
  context.onSidebarSelect(-5);
  await settle();
  assert.equal(calls.at(-1)[7], false);
  setActiveCategory(null);
  await settle();
  context.onSidebarSelect(-5);
  await settle();
  assert.equal(calls.at(-1)[7], false);
  setActiveCollection(-5);
  await settle();
  assert.equal(state.activeCollection, -5);
  assert.equal(calls.at(-1)[7], false);
  await search(false);
  assert.equal(calls.at(-1)[7], false);
  toggleGenre('gif');
  await settle();
  assert.equal(state.activeCollection, -5);
  assert.equal(calls.at(-1)[7], false);
  assert.equal(calls.filter(args => args[7] === true).length, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_later_page_cache_warmup_follows_current_query_contract():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "vue-src" / "composables" / "useMemes.ts").read_text(
        encoding="utf-8"
    )
    backend = (root / "src" / "webui.py").read_text(encoding="utf-8")

    assert "'start_page_cache_warmup'" in source
    assert "state.searchQuery" in source
    assert "state.activeCollection" in source
    assert "state.activeCategory" in source
    assert "state.activeGenres" in source
    assert "'cancel_page_cache_warmup'" not in source
    assert "self.cancel_page_cache_warmup()" in backend
    assert "warmLaterPages(offset, selectedTags)" in source


def test_drag_import_uses_local_refresh_without_diagnostic_fallbacks():
    root = Path(__file__).resolve().parent.parent
    app_source = (root / "src" / "vue-src" / "App.vue").read_text(encoding="utf-8")
    meme_source = (root / "src" / "vue-src" / "composables" / "useMemes.ts").read_text(
        encoding="utf-8"
    )
    backend = (root / "src" / "webui.py").read_text(encoding="utf-8")

    assert "function presentImportedMeme" not in meme_source
    assert "pendingImportedMemes" not in meme_source
    finish_block = app_source.split("async function finishMemeImport", 1)[1].split(
        "async function onDrop", 1
    )[0]
    assert "await refreshLibraryView('structure')" in finish_block
    assert "window.location.reload" not in finish_block
    assert "schedule_main_page_reload" not in backend
    assert "get_meme_card" not in backend
    assert '"/api/import-presented/"' not in backend
    assert "get_library_revision" not in backend


def test_delete_waits_for_structure_refresh_and_backend_publishes_change():
    root = Path(__file__).resolve().parent.parent
    app_source = (root / "src" / "vue-src" / "App.vue").read_text(encoding="utf-8")
    backend = (root / "src" / "webui.py").read_text(encoding="utf-8")

    single_delete = app_source.split("case 'delete':", 1)[1].split(
        "case 'rename-collection':", 1
    )[0]
    batch_delete = app_source.split("async function batchDelete", 1)[1].split(
        "function batchAddToCollection", 1
    )[0]
    assert "await refreshLibraryView('structure')" in single_delete
    assert "await refreshLibraryView('structure')" in batch_delete
    assert "remove_memes_from_manifest" in backend
    assert 'notify_changed("structure")' in backend


def test_cache_rescan_runs_as_single_background_job():
    from src.webui import WebUI

    ui = WebUI.__new__(WebUI)
    ui._cache_scan_lock = threading.Lock()
    ui._cache_scan_thread = None
    started = threading.Event()
    release = threading.Event()

    def scan_cache():
        started.set()
        release.wait(2)
        return 0

    ui.scan_cache = scan_cache
    self_notify = mock.patch("src.library_events.notify_changed")
    with self_notify as notify_changed:
        assert ui.start_cache_scan()
        assert started.wait(1)
        first_thread = ui._cache_scan_thread
        assert ui.start_cache_scan()
        assert ui._cache_scan_thread is first_thread
        release.set()
        first_thread.join(2)
        assert ui._cache_scan_thread is None
        notify_changed.assert_not_called()


def test_meme_details_close_discards_unsaved_changes():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "vue-src" / "components" / "MemeDetails.vue").read_text(
        encoding="utf-8"
    )
    close_block = source.split("function close() {", 1)[1].split(
        "function isTextEditing", 1
    )[0]

    assert "saveChanges" not in close_block
    assert "dirty.value = false" in close_block
    assert "放弃修改并关闭" in source

    actions = source.split('<div class="details-actions">', 1)[1].split("</div>", 1)[0]
    close_button = actions.index('ref="closeEl" class="btn btn-secondary"')
    save_button = actions.index('class="btn btn-primary"')
    assert close_button < save_button


def test_meme_details_can_create_and_select_collections_and_categories():
    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "vue-src" / "components" / "MemeDetails.vue").read_text(
        encoding="utf-8"
    )
    app_source = (root / "src" / "vue-src" / "App.vue").read_text(encoding="utf-8")

    assert "'create_collection'" in source
    assert "'create_category'" in source
    assert "collectionCreateParentId" in source
    assert "categoryCreateParentId" in source
    assert "顶层分组" in source
    assert "顶层类别" in source
    assert "创建并选中" in source
    assert "表情的归属需点击保存更改才生效" in source
    assert "emit('structure-created')" in source
    assert '@structure-created="onMemeStructureCreated"' in app_source
