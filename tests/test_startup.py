"""启动流程测试 - pytest风格"""

import os
import re
import subprocess
import sys
import tempfile
import threading
import time
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

_SETTINGS_BODY = Path(__file__).resolve().parent.parent / "src" / "vue-src" / "settings" / "settings-body.html"
_SETTINGS_LEGACY = Path(__file__).resolve().parent.parent / "src" / "vue-src" / "settings" / "legacy.js"


def _wait_calls(target, count, timeout=2.0):
    """等待后台窗口切换线程写入期望调用次数（托盘/热键回调改为异步执行）"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(target.calls) < count:
        time.sleep(0.01)


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
            {"shown": _FakeEvent(), "loaded": _FakeEvent()},
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


def test_clipboard(tmp_path, monkeypatch):
    """验证复制分发，不触碰真实剪贴板或把应用图标写入历史。"""
    from src import clipboard_util

    clipboard = mock.Mock()
    monkeypatch.setattr(clipboard_util, "HAS_PYPERCLIP", True)
    monkeypatch.setattr(clipboard_util, "pyperclip", clipboard, raising=False)
    assert copy_text("test") is True
    clipboard.copy.assert_called_once_with("test")
    for name in ("_copy_image_windows", "_copy_image_macos", "_copy_image_linux"):
        monkeypatch.setattr(clipboard_util, name, mock.Mock(return_value=True))
    image_path = tmp_path / "test-image.png"
    image_path.write_bytes(b"mock image")
    assert copy_image_to_clipboard(str(image_path)) is True


def test_native_text_clipboard_formats_and_failure_cleanup(monkeypatch):
    """原生格式和内存所有权全部用假 Win32 API 验证。"""
    import ctypes

    from src.clipboard_util import copy_text_with_history

    for failure in (
        None,
        "register",
        "allocate",
        "lock",
        "open",
        "empty",
        49153,
        49154,
        13,
    ):
        user32 = mock.Mock()
        kernel32 = mock.Mock()
        buffers = {}
        transferred = []
        formats = {
            "CanUploadToCloudClipboard": 49153,
            "CanIncludeInClipboardHistory": 49154,
        }
        user32.RegisterClipboardFormatW.side_effect = lambda name: (
            0 if failure == "register" else formats[name]
        )
        user32.OpenClipboard.return_value = failure != "open"
        user32.EmptyClipboard.return_value = failure != "empty"

        def allocate(flags, size):
            handle = len(buffers) + 1
            if failure == "allocate" and handle == 2:
                return 0
            buffers[handle] = ctypes.create_string_buffer(size)
            return handle

        def lock(handle):
            if failure == "lock" and handle == 2:
                return 0
            return ctypes.addressof(buffers[handle])

        def set_data(fmt, handle):
            if failure == fmt:
                return 0
            transferred.append(handle)
            return handle

        kernel32.GlobalAlloc.side_effect = allocate
        kernel32.GlobalLock.side_effect = lock
        user32.SetClipboardData.side_effect = set_data
        with monkeypatch.context() as patcher:
            patcher.setattr(
                ctypes,
                "WinDLL",
                lambda name, **kwargs: user32 if name == "user32" else kernel32,
                raising=False,
            )
            result = copy_text_with_history("灰头土脸", 0x123456789)
        assert result is (failure is None)
        freed = {call.args[0] for call in kernel32.GlobalFree.call_args_list}
        assert freed == set(buffers) - set(transferred)
        if user32.OpenClipboard.called and failure != "open":
            user32.CloseClipboard.assert_called_once_with()
        else:
            user32.CloseClipboard.assert_not_called()
        if failure is None:
            user32.OpenClipboard.assert_called_once_with(0x123456789)
            assert [
                call.args[0] for call in user32.SetClipboardData.call_args_list
            ] == [
                49153,
                49154,
                13,
            ]
            assert int.from_bytes(bytes(buffers[1]), "little") == 0
            assert int.from_bytes(bytes(buffers[2]), "little") == 1
            assert bytes(buffers[3]).decode("utf-16-le") == "灰头土脸\x00"


def test_native_text_clipboard_invalid_input_never_opens_clipboard(monkeypatch):
    import ctypes

    from src.clipboard_util import copy_text_with_history

    load = mock.Mock(side_effect=AssertionError("不得访问真实 Win32 API"))
    monkeypatch.setattr(ctypes, "WinDLL", load, raising=False)
    for value in (None, "", "a\x00b", "\ud800", "x" * (2 * 1024 * 1024)):
        assert copy_text_with_history(value, 123) is False
    assert copy_text_with_history("text", None) is False
    load.assert_not_called()


def test_editor_text_copy_uses_the_correct_window(monkeypatch):
    import inspect
    from types import SimpleNamespace

    from src import webui

    monkeypatch.setattr(webui.platform, "system", lambda: "Windows")
    write = mock.Mock(return_value=True)
    monkeypatch.setattr(webui, "copy_text_with_history", write)
    ui = SimpleNamespace(
        _window=SimpleNamespace(native=SimpleNamespace(Handle=101)),
        _settings_window=SimpleNamespace(
            native=SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 202))
        ),
    )
    for api_class, owner in ((webui.JsApi, 101), (webui.SettingsApi, 202)):
        api = api_class.__new__(api_class)
        api._webui = ui
        assert api.copy_editor_text("灰头土脸") is True
        write.assert_called_with("灰头土脸", owner)
    monkeypatch.setattr(webui.platform, "system", lambda: "Linux")
    assert webui._copy_editor_text("text", ui._window) is False
    assert write.call_count == 2
    assert "events.loaded" in inspect.getsource(webui.WebUI.start)
    assert "events.loaded" in inspect.getsource(webui.WebUI._create_settings_window)


def test_editor_text_copy_bridge_preserves_private_fields_and_fallback():
    import shutil

    import pytest

    from src.webui import _TEXT_CLIPBOARD_SCRIPT

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "jsdom").is_dir():
        pytest.skip("编辑框复制测试需要 Node 和 jsdom")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { JSDOM } = require('jsdom');
const dom = new JSDOM(`<input id="text" value="前缀灰头土脸后缀">
  <textarea id="textarea">多行\n文本</textarea>
  <input id="password" type="password" value="private">
  <input id="revealed" autocomplete="section-login current-password" value="private">
  <input id="readonly" readonly value="readonly"><div id="body">页面文字</div>`,
  {runScripts:'outside-only'});
const win = dom.window, doc = win.document, calls = [], fallback = [];
let success = true;
win.pywebview = {api:{copy_editor_text:async text => {calls.push(text);return success;}}};
const source = process.argv[1];
win.eval(source); win.eval(source);
const copy = (id,start,end) => {
  const input = doc.getElementById(id); input.focus();
  if(input.setSelectionRange) input.setSelectionRange(start,end);
  const e = new win.Event('copy',{bubbles:true,cancelable:true});
  input.dispatchEvent(e); return e;
};
doc.execCommand = name => {
  fallback.push(name);
  assert.equal(copy('text',2,6).defaultPrevented,false);
  return true;
};
const flush = async () => {
  for (let i=0;i<4;i++) await new Promise(resolve=>setTimeout(resolve,0));
};
(async()=>{
  assert.equal(copy('text',2,6).defaultPrevented,true);
  await flush(); assert.deepEqual(calls,['灰头土脸']);
  assert.equal(doc.getElementById('text').selectionStart,2);
  assert.equal(copy('body',0,1).defaultPrevented,false);
  assert.equal(copy('password',0,7).defaultPrevented,false);
  assert.equal(copy('revealed',0,7).defaultPrevented,false);
  assert.equal(copy('text',2,2).defaultPrevented,false);
  assert.equal(copy('body',0,1).defaultPrevented,false);
  await flush(); assert.equal(calls.length,1);
  assert.equal(copy('textarea',0,5).defaultPrevented,true);
  assert.equal(copy('readonly',0,8).defaultPrevented,true);
  await flush(); assert.deepEqual(calls,['灰头土脸','多行\n文本','readonly']);
  const input=doc.getElementById('text');
  input.value='first'; copy('text',0,5);
  input.value='second'; copy('text',0,6); await flush();
  assert.deepEqual(calls.slice(-2),['first','second']);
  input.value='前缀灰头土脸后缀';
  success=false; copy('text',2,6); await flush();
  assert.deepEqual(fallback,['copy']);
  copy('text',2,6); doc.getElementById('text').value='new edit'; await flush();
  assert.deepEqual(fallback,['copy']);
  win.pywebview=null;
  assert.equal(copy('text',0,3).defaultPrevented,false);
  dom.window.close();
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", script, _TEXT_CLIPBOARD_SCRIPT],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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
    assert created[0][1]["width"] == 960
    assert created[0][1]["height"] == 640
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
    app._toggle_busy = False
    app._toggle_started_at = 0.0
    app._toggle_lock = threading.Lock()

    app._on_hotkey()
    _wait_calls(app._webui, 1)
    assert app._webui.calls == ["hotkey"]

    app._on_tray_show()
    _wait_calls(app._webui, 2)

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
    assert 'src="/index.js"' in html
    assert 'href="/index.css"' in html
    shell = (HTML_DIR / "settings.html").read_text(encoding="utf-8")
    assert 'src="/dist/ohmymeme.js"' in shell
    assert 'id="settings-mount"' in shell
    assert "<!-- plugin-sections -->" in shell
    settings_html = _SETTINGS_BODY.read_text(encoding="utf-8")
    assert 'id="s-hotkey-show-at-mouse"' in settings_html
    assert 'id="s-napcat-image-send-mode"' in settings_html
    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")
    assert settings_js.count("s.hotkey_show_at_mouse === true") == 2
    assert "hotkey_show_at_mouse," in settings_js
    assert "napcat_image_send_mode" in settings_js


def test_manifest_include_tags_settings_contract():
    """设置页「将标签写入同步清单」开关（HTML 复选框 + JS 读写 + 后端配置键）"""
    from src.config import Config
    from src.webui import HTML_DIR, SettingsApi

    assert Config.DEFAULTS.get("manifest_include_tags") is True
    settings_html = _SETTINGS_BODY.read_text(encoding="utf-8")
    assert 'id="s-manifest-include-tags"' in settings_html
    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")
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
    settings_html = _SETTINGS_BODY.read_text(encoding="utf-8")
    assert 'id="s-manifest-include-favorites"' in settings_html
    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")
    assert "s.manifest_include_favorites !== false" in settings_js
    assert "manifest_include_favorites," in settings_js
    import inspect

    src = inspect.getsource(SettingsApi.get_settings)
    assert '"manifest_include_favorites"' in src
    reset_src = inspect.getsource(SettingsApi.reset_settings)
    assert '"manifest_include_favorites": True' in reset_src


def test_import_resolution_limit_settings_contract(tmp_path):
    import inspect

    from src.config import get_import_max_px
    from src.webui import HTML_DIR, JsApi, SettingsApi

    settings_html = _SETTINGS_BODY.read_text(encoding="utf-8")
    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")
    assert 'id="s-import-resolution-limit-enabled"' in settings_html
    assert 'id="s-import-resolution-limit-px"' in settings_html
    assert settings_js.count("s.import_resolution_limit_enabled === true") == 2
    assert "import_resolution_limit_enabled, import_resolution_limit_px," in settings_js
    assert "pixels.disabled = !enabled" in settings_js
    for api_class in (JsApi, SettingsApi):
        get_source = inspect.getsource(api_class.get_settings)
        assert '"import_resolution_limit_enabled"' in get_source
        assert '"import_resolution_limit_px"' in get_source
        assert '"import_resolution_limit_enabled": False' in inspect.getsource(
            api_class.reset_settings
        )

    cfg = Config(tmp_path / "config.json")
    api = SettingsApi.__new__(SettingsApi)
    api._cfg = cfg
    assert api.save_settings(
        {"import_resolution_limit_enabled": True, "import_resolution_limit_px": "4096"}
    ) == {"ok": True, "theme": "dark"}
    assert get_import_max_px(Config(tmp_path / "config.json")) == 4096
    assert api.save_settings({"import_resolution_limit_enabled": False}) == {"ok": True, "theme": "dark"}
    assert get_import_max_px(Config(tmp_path / "config.json")) == 0


def test_category_direct_settings_contract_and_refresh(tmp_path, monkeypatch):
    import inspect
    from types import SimpleNamespace

    from src import webui as webui_module
    from src.webui import HTML_DIR, JsApi, SettingsApi

    cfg = Config(tmp_path / "config.json")
    assert cfg.get("show_category_uncategorized") is False
    html = _SETTINGS_BODY.read_text(encoding="utf-8")
    javascript = _SETTINGS_LEGACY.read_text(encoding="utf-8")
    assert 'id="s-show-category-uncategorized"' in html
    assert javascript.count("s.show_category_uncategorized === true") == 2
    assert "show_category_uncategorized," in javascript
    window = mock.Mock()
    monkeypatch.setattr(webui_module, "webview", SimpleNamespace(windows=[window]))
    monkeypatch.setattr(webui_module, "_cloud_reset", lambda: None)
    monkeypatch.setattr(webui_module.onebot, "stop_command_listener", lambda: None)
    monkeypatch.setattr("src.platform_util.set_auto_start", lambda enabled: None)
    for api_class in (JsApi, SettingsApi):
        api = api_class.__new__(api_class)
        api._cfg = cfg
        api._webui = SimpleNamespace(_on_hotkey_change=lambda key: None)
        assert api.get_settings()["show_category_uncategorized"] is False
        assert '"show_category_uncategorized": False' in inspect.getsource(
            api_class.reset_settings
        )
        api.save_settings({"show_category_uncategorized": True})
        assert (
            Config(tmp_path / "config.json").get("show_category_uncategorized") is True
        )
        assert api.get_settings()["show_category_uncategorized"] is True
        if api_class is SettingsApi:
            assert "refreshLibrary('structure')" in window.evaluate_js.call_args.args[0]
        reset = api.reset_settings()
        assert reset["show_category_uncategorized"] is False
        assert (
            Config(tmp_path / "config.json").get("show_category_uncategorized") is False
        )
        if api_class is SettingsApi:
            assert "refreshLibrary('structure')" in window.evaluate_js.call_args.args[0]


def test_qq_drag_cleanup_settings_contract():
    """启动清理开关能回显、保存及恢复默认，且仅在应用启动时触发。"""
    import inspect

    from src.main import OhMyMemeApp
    from src.webui import HTML_DIR, JsApi, SettingsApi

    assert Config.DEFAULTS["qq_drag_cleanup_on_start"] is True
    settings_html = _SETTINGS_BODY.read_text(encoding="utf-8")
    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")
    assert 'id="s-qq-drag-cleanup-on-start"' in settings_html
    assert settings_js.count("s.qq_drag_cleanup_on_start !== false") == 2
    assert "qq_drag_cleanup_on_start:" in settings_js
    for api_class in (JsApi, SettingsApi):
        assert '"qq_drag_cleanup_on_start"' in inspect.getsource(api_class.get_settings)
        assert '"qq_drag_cleanup_on_start": True' in inspect.getsource(
            api_class.reset_settings
        )
    assert "_start_qq_drag_temp_cleanup(self._cfg)" in inspect.getsource(
        OhMyMemeApp.run
    )


def test_qq_drag_cleanup_startup_gate_and_background_thread():
    from src.main import _start_qq_drag_temp_cleanup
    from src.onebot import cleanup_qq_drag_temp_files

    cfg = mock.Mock()
    with mock.patch("src.main.threading.Thread") as thread:
        cfg.get.return_value = False
        _start_qq_drag_temp_cleanup(cfg)
        thread.assert_not_called()
        cfg.get.return_value = True
        _start_qq_drag_temp_cleanup(cfg)
        thread.assert_called_once_with(
            target=cleanup_qq_drag_temp_files,
            name="qq-drag-temp-cleanup",
            daemon=True,
        )
        thread.return_value.start.assert_called_once_with()
    with mock.patch(
        "src.main.threading.Thread", side_effect=RuntimeError("unavailable")
    ):
        _start_qq_drag_temp_cleanup(cfg)


def test_qq_drag_cleanup_setting_saved_without_running_cleanup(tmp_path, monkeypatch):
    from src import onebot
    from src.webui import SettingsApi

    cfg = Config(tmp_path / "config.json")
    api = SettingsApi.__new__(SettingsApi)
    api._cfg = cfg
    cleanup = mock.Mock()
    monkeypatch.setattr(onebot, "cleanup_qq_drag_temp_files", cleanup)
    assert api.save_settings({"qq_drag_cleanup_on_start": False}) == {"ok": True, "theme": "dark"}
    assert Config(tmp_path / "config.json").get("qq_drag_cleanup_on_start") is False
    cleanup.assert_not_called()


def test_manifest_include_collections_settings_contract():
    """设置页「将分组写入同步清单」开关（HTML + JS 读写 + 后端配置键 + 关闭确认）"""
    import inspect

    from src.config import Config
    from src.webui import SettingsApi

    assert Config.DEFAULTS.get("manifest_include_collections") is True
    settings_html = _SETTINGS_BODY.read_text(encoding="utf-8")
    assert 'id="s-manifest-include-collections"' in settings_html
    assert "confirmManifestToggle(this, '分组')" in settings_html
    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")
    assert "s.manifest_include_collections !== false" in settings_js
    assert "manifest_include_collections:" in settings_js
    assert "关闭后将无法云同步" in settings_js
    src = inspect.getsource(SettingsApi.get_settings)
    assert '"manifest_include_collections"' in src
    reset_src = inspect.getsource(SettingsApi.reset_settings)
    assert '"manifest_include_collections": True' in reset_src


def test_webdav_allow_insecure_settings_contract():
    """WebDAV 自签名证书开关 + 测试连接确认分支（HTML + JS + 后端配置键）"""
    import inspect

    from src.config import Config
    from src.webui import SettingsApi

    assert Config.DEFAULTS.get("webdav_allow_insecure") is False
    settings_html = _SETTINGS_BODY.read_text(encoding="utf-8")
    assert 'id="s-webdav-insecure"' in settings_html
    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")
    assert "WEBDAV_SSL_UNTRUSTED" in settings_js
    assert "继续使用" in settings_js
    assert "webdav_allow_insecure" in settings_js
    src = inspect.getsource(SettingsApi.get_settings)
    assert '"webdav_allow_insecure"' in src
    reset_src = inspect.getsource(SettingsApi.reset_settings)
    assert '"webdav_allow_insecure": False' in reset_src


def test_copy_mode_4_settings_contract():
    """提供仅改后缀选项，同时保留定制版默认复制行为。"""
    import inspect

    from src.clipboard_util import convert_image_mode_4
    from src.config import Config
    from src.webui import JsApi, SettingsApi

    assert Config.DEFAULTS.get("copy_resize_mode") == 1
    settings_html = _SETTINGS_BODY.read_text(encoding="utf-8")
    assert '<option value="4">仅修改后缀为 GIF' in settings_html
    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")
    assert "s.copy_resize_mode ?? 1" in settings_js
    assert "case 4:" in inspect.getsource(JsApi.copy_meme)
    assert "resize_mode != 4" in inspect.getsource(JsApi.copy_meme)
    assert "convert_image_mode_4" in inspect.getsource(JsApi.copy_meme)
    assert callable(convert_image_mode_4)
    assert '"copy_resize_mode": int(d.get("copy_resize_mode", 1) or 0)' in (
        inspect.getsource(SettingsApi.get_settings)
    )


def test_cloud_thumb_auto_push_settings_contract():
    """设置页「启动时自动补传缺失的云端缩略图」开关（HTML 复选框 + JS 读写 + 后端配置键）"""
    import inspect

    from src.config import Config
    from src.webui import HTML_DIR, SettingsApi

    assert Config.DEFAULTS.get("cloud_direct") is True
    assert Config.DEFAULTS.get("cloud_thumb_auto_push") is True
    settings_html = _SETTINGS_BODY.read_text(encoding="utf-8")
    assert 'id="s-cloud-thumb-push"' in settings_html
    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")
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
    settings_html = _SETTINGS_BODY.read_text(encoding="utf-8")
    assert 'id="s-disable-auto-hide"' in settings_html
    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")
    assert settings_js.count("s.disable_auto_hide === true") == 2
    assert "disable_auto_hide," in settings_js
    src = inspect.getsource(SettingsApi.get_settings)
    assert '"disable_auto_hide"' in src
    reset_src = inspect.getsource(SettingsApi.reset_settings)
    assert '"disable_auto_hide": False' in reset_src


def test_window_resize_settings_contract():
    """设置页「允许调整窗口大小」开关（HTML 复选框 + JS 读写 + 8 向边框拖拽 + resize API）"""
    import inspect

    from src.config import Config
    from src.webui import HTML_DIR, JsApi, SettingsApi, WebUI

    assert Config.DEFAULTS.get("window_resize_enabled") is False
    assert Config.DEFAULTS.get("settings_window_width") == 0
    settings_html = _SETTINGS_BODY.read_text(encoding="utf-8")
    assert 'id="s-window-resize"' in settings_html
    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")
    assert "s.window_resize_enabled === true" in settings_js
    assert "window_resize_enabled," in settings_js
    assert "applyWindowResizeHandles" in settings_js
    vue_dir = HTML_DIR.parent / "vue-src"
    main_css = (vue_dir / "style.css").read_text(encoding="utf-8")
    assert "win-resize-handle" in main_css
    app_vue = (vue_dir / "App.vue").read_text(encoding="utf-8")
    assert "win-resize-layer" in app_vue
    assert "syncWinResizeHandles" in app_vue
    src = inspect.getsource(SettingsApi.get_settings)
    assert '"window_resize_enabled"' in src
    reset_src = inspect.getsource(SettingsApi.reset_settings)
    assert '"window_resize_enabled": False' in reset_src
    js_src = inspect.getsource(JsApi.get_settings)
    assert '"window_resize_enabled"' in js_src
    assert "window_resize_enabled" in inspect.getsource(JsApi.get_init_data)
    assert hasattr(JsApi, "resize_window")
    assert hasattr(SettingsApi, "resize_window")
    assert hasattr(SettingsApi, "reset_main_window_size")
    assert "resetMainWindowSize()" in settings_html
    assert "reset_main_window_size" in settings_js
    assert hasattr(SettingsApi, "plugin_pick_file")
    webui_src = inspect.getsource(WebUI._create_settings_window)
    assert "resizable=True" in webui_src
    assert "settings_window_width" in inspect.getsource(WebUI._save_settings_window_size)


def test_resize_window_api_clamps():
    """resize_window 尺寸钳制（主窗 480-8000x360-6000，设置窗 600-8000x480-6000）"""
    from src.webui import JsApi, SettingsApi

    class FakeWin:
        def __init__(self):
            self.size = None

        def resize(self, w, h):
            self.size = (w, h)

    class FakeWebui:
        pass

    fw = FakeWebui()
    fw._window = FakeWin()
    api = JsApi.__new__(JsApi)
    api._webui = fw
    api.resize_window(100, 50)
    assert fw._window.size == (480, 360)
    api.resize_window(99999, 99999)
    assert fw._window.size == (8000, 6000)
    api.resize_window(1280, 800)
    assert fw._window.size == (1280, 800)

    fw2 = FakeWebui()
    fw2._settings_window = FakeWin()
    sa = SettingsApi.__new__(SettingsApi)
    sa._webui = fw2
    sa.resize_window(100, 50, False)
    assert fw2._settings_window.size == (600, 480)
    sa.resize_window(99999, 99999, False)
    assert fw2._settings_window.size == (8000, 6000)
    sa.resize_window(720, 560, False)
    assert fw2._settings_window.size == (720, 560)


def test_cloud_direct_confirm_buttons_contract():
    """首次配置云端的弹窗按钮为「开启/关闭」（showConfirm 第 3/4 参），Esc/遮罩返回 null 不改动"""
    from src.webui import HTML_DIR

    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")
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

    settings_html = _SETTINGS_BODY.read_text(encoding="utf-8")
    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")
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

    html = _SETTINGS_BODY.read_text(encoding="utf-8")
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
    settings_html = _SETTINGS_BODY.read_text(encoding="utf-8")
    settings_js = _SETTINGS_LEGACY.read_text(encoding="utf-8")

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


def test_library_genres_persist_and_restore_from_config(tmp_path):
    from src.webui import JsApi

    config_path = tmp_path / "genres.json"
    api = JsApi.__new__(JsApi)
    api._cfg = Config(config_path)
    api.search_memes = mock.Mock(return_value=[])
    api.get_collections = mock.Mock(return_value=[])
    api._build_category_options = mock.Mock(return_value=[])
    api.get_tags = mock.Mock(return_value=[])
    api.get_tag_records = mock.Mock(return_value=[])
    all_genres = ["image", "gif", "chat_record"]

    with mock.patch("src.webui.guide_ok", return_value=True):
        assert api.get_init_data()["library_genres"] == all_genres
        assert not config_path.exists()
        for selected, expected in (
            (["gif"], ["gif"]),
            (["chat_record", "image", "image"], ["image", "chat_record"]),
            ([], []),
            (all_genres, all_genres),
        ):
            assert api.set_library_genres(selected) == {"ok": True}
            api._cfg = Config(config_path)
            assert api._cfg.get("library_genres") == expected
            assert api.get_init_data()["library_genres"] == expected

        for invalid in (None, "gif", {}, [None], ["video"], [{}]):
            assert api.set_library_genres(invalid)["ok"] is False
            assert Config(config_path).get("library_genres") == all_genres

        with mock.patch.object(Config, "save", side_effect=OSError("read-only")):
            assert api.set_library_genres([])["ok"] is False
        assert Config(config_path).get("library_genres") == all_genres
        assert api.set_library_genres(["image"])["ok"] is True
        api._cfg.reset()
        api._cfg.save()
        assert Config(config_path).get("library_genres") == all_genres


def test_library_genres_normalize_old_or_invalid_config(tmp_path):
    import json

    from src.config import normalize_library_genres

    config_path = tmp_path / "genres.json"
    for saved, expected in (
        ({}, ["image", "gif", "chat_record"]),
        ({"library_genres": None}, ["image", "gif", "chat_record"]),
        ({"library_genres": "gif"}, ["image", "gif", "chat_record"]),
        ({"library_genres": []}, []),
        ({"library_genres": ["gif", "gif", "video"]}, ["gif"]),
    ):
        config_path.write_text(json.dumps(saved), encoding="utf-8")
        cfg = Config(config_path)
        assert normalize_library_genres(cfg.get("library_genres")) == expected
    cfg.set("library_genres", ["chat_record", "image", "unknown"])
    cfg.save()
    assert Config(config_path).get("library_genres") == ["image", "chat_record"]


def test_library_genres_frontend_restore_and_ordered_saves():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "esbuild").is_dir():
        pytest.skip("体裁偏好测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const esbuild = require('esbuild');
const queries = [];
const warmups = [];
const saves = [];
let persisted;
let blockSave;
let rejectNext = false;
global.pywebview = { api: {
  get_init_data: async () => ({ memes: [], library_genres: persisted }),
  search_library: async (...args) => {
    queries.push(args);
    return { memes: [], chat_records: [], chat_total: 0, total: 0 };
  },
  start_page_cache_warmup: async (...args) => { warmups.push(args); },
  set_library_genres: async genres => {
    saves.push([...genres]);
    if (blockSave) await blockSave;
    if (rejectNext) { rejectNext = false; return { ok: false }; }
    persisted = [...genres];
    return { ok: true };
  },
} };
global.window = { pywebview: global.pywebview };
  global.document = new (require('jsdom').JSDOM)('<!doctype html><html></html>').window.document;
const filename = path.resolve('src/vue-src/composables/useMemes.ts');
const compiled = esbuild.buildSync({
  entryPoints: [filename], bundle: true, platform: 'node', format: 'cjs',
  external: ['vue'], write: false,
}).outputFiles[0].text;
function newSession() {
  const instance = new Module(filename, module);
  instance.filename = filename;
  instance.paths = Module._nodeModulePaths(path.dirname(filename));
  instance._compile(compiled, filename);
  return instance.exports.useMemes();
}
const settle = () => new Promise(resolve => setImmediate(resolve));
const all = ['image', 'gif', 'chat_record'];
(async () => {
  for (const saved of [undefined, null, 'gif', [], ['gif'], ['chat_record', 'image']]) {
    persisted = saved;
    const session = newSession();
    const before = saves.length;
    await session.loadInitData();
    await settle();
    const expected = Array.isArray(saved) ? all.filter(genre => saved.includes(genre)) : all;
    assert.deepEqual([...session.state.activeGenres], expected);
    assert.deepEqual(queries.at(-1)[6], expected);
    assert.deepEqual(warmups.at(-1)[7], expected);
    assert.equal(saves.length, before, 'restoring must not write back defaults');
  }
  persisted = all;
  const session = newSession();
  await session.loadInitData();
  let release;
  blockSave = new Promise(resolve => { release = resolve; });
  session.toggleGenre('image');
  session.toggleGenre('gif');
  session.toggleGenre('chat_record');
  await settle();
  assert.equal(saves.length, 1, 'rapid toggles must serialize writes');
  assert.deepEqual(queries.at(-1)[6], [], 'search must not wait for preference writes');
  release();
  blockSave = null;
  await settle();
  assert.deepEqual(saves, [['gif', 'chat_record'], ['chat_record'], []]);
  assert.deepEqual(persisted, []);
  const restarted = newSession();
  await restarted.loadInitData();
  assert.deepEqual([...restarted.state.activeGenres], []);
  assert.deepEqual(queries.at(-1)[6], []);
  assert.equal(saves.length, 3);
  restarted.setActiveCollection(-5);
  await settle();
  assert.deepEqual(persisted, all, 'existing reset behavior is saved too');
  const afterReset = newSession();
  await afterReset.loadInitData();
  assert.deepEqual([...afterReset.state.activeGenres], all);
  rejectNext = true;
  afterReset.toggleGenre('image');
  afterReset.toggleGenre('gif');
  await settle();
  assert.deepEqual(persisted, ['chat_record'], 'a failed save must not block later saves');
  afterReset.state.page = 2;
  afterReset.state.searchQuery = 'temporary';
  afterReset.state.activeTags.add('temporary');
  const before = saves.length;
  await settle();
  assert.equal(saves.length, before, 'do not persist transient search or navigation');
  const finalSession = newSession();
  await finalSession.loadInitData();
  assert.deepEqual([...finalSession.state.activeGenres], ['chat_record']);
  assert.equal(finalSession.state.page, 1);
  assert.equal(finalSession.state.searchQuery, '');
  assert.equal(finalSession.state.activeTags.size, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_genre_restore_waits_for_registered_bridge_in_actual_app():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "vite").is_dir():
        pytest.skip("启动恢复集成测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { setTimeout: delay } = require('node:timers/promises');
const failures = [];
process.on('unhandledRejection', error => failures.push(String(error)));
(async () => {
  const { build } = await import('vite');
  const bundle = await build({ logLevel: 'silent', build: { write: false, minify: false } });
  const code = bundle.output.find(item => item.type === 'chunk').code;
  let saved = ['image', 'gif'];
  for (const initial of ['empty', 'absent', 'partial', 'ready']) {
    const dom = new JSDOM('<div id="app-mount"></div>', {
      runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/',
    });
    const win = dom.window;
    const calls = [], errors = [];
    win.console.error = (...args) => errors.push(args.map(String));
    win.console.warn = (...args) => errors.push(args.map(String));
    win.matchMedia = () => ({ matches: true, addEventListener() {}, removeEventListener() {} });
    win.fetch = async () => ({ ok: true, json: async () => ({}) });
    const methods = {
      get_init_data: async () => {
        calls.push(['init']);
        return { library_genres: [...saved], memes: [], tags: [], collections: [],
          categories: [], guide_ok: true, show_startup_animation: false };
      },
      search_library: async (...args) => {
        calls.push(['search', [...args[6]]]);
        return { memes: [], chat_records: [], chat_total: 0, total: 0 };
      },
      set_library_genres: async genres => { saved = [...genres]; return { ok: true }; },
      start_page_cache_warmup: async () => ({}), check_update: async () => ({}),
      rescan_cache: async () => ({}), run_auto_sync: async () => ({}),
      get_tag_records: async () => [], get_categories: async () => [], get_collections: async () => [],
    };
    if (initial !== 'absent') win.pywebview = { api: initial === 'ready' ? methods : {} };
    if (initial === 'partial') win.pywebview.api.search_library = methods.search_library;
    try {
      win.eval(code);
      await delay(20);
      if (initial !== 'ready') {
        assert.equal(calls.length, 0, 'initialization must wait for actual registered methods');
        win.pywebview ||= { api: {} };
        Object.assign(win.pywebview.api, methods);
        win.dispatchEvent(new win.Event('pywebviewready'));
      }
      for (let i = 0; i < 100 && !calls.some(call => call[0] === 'search'); i++) await delay(10);
      await delay(10);
      assert.equal(calls[0]?.[0], 'init', initial);
      assert.deepEqual(calls.find(call => call[0] === 'search')?.[1], ['image', 'gif'], initial);
      const selected = () => [...win.document.querySelectorAll('.genre-tag')]
        .map(element => element.getAttribute('aria-checked'));
      assert.deepEqual(selected(), ['true', 'true', 'false'], initial);
      if (initial === 'empty') {
        const checkbox = win.document.querySelectorAll('.genre-tag')[2];
        checkbox.click();
        await delay(10);
        assert.deepEqual(saved, ['image', 'gif', 'chat_record']);
        checkbox.click();
        await delay(10);
        assert.deepEqual(saved, ['image', 'gif']);
      }
      assert.deepEqual(errors, [], initial);
      assert.deepEqual(failures, [], initial);
    } finally {
      win.document.querySelector('#app-mount').__vue_app__?.unmount();
      win.close();
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_duplicate_import_keeps_genres_and_saved_preference():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "esbuild").is_dir():
        pytest.skip("重复导入筛选测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Module = require('node:module');
const { ref } = require('vue');
const esbuild = require('esbuild');
const filename = path.resolve('src/vue-src/composables/useMemes.ts');
const compiled = esbuild.buildSync({ entryPoints: [filename], bundle: true,
  platform: 'node', format: 'cjs', external: ['vue'], write: false }).outputFiles[0].text;
const app = fs.readFileSync('src/vue-src/App.vue', 'utf8');
const handlers = ['duplicateIds', 'revealExistingImports', 'handleSimilarImport', 'handleLargeMemeImport']
  .map(name => app.match(new RegExp('(?:async )?function ' + name + '\\([^]*?\\n\\}'))[0]).join('\n');
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  for (const selected of [['image', 'gif'], ['gif'], ['chat_record'], []]) {
    let persisted = [...selected], recordOk = true;
    const writes = [], searches = [], records = [];
    global.pywebview = { api: {
      get_init_data: async () => ({ memes: [], library_genres: persisted }),
      set_library_genres: async genres => { writes.push([...genres]); persisted = [...genres]; return { ok: true }; },
      search_library: async (...args) => {
        searches.push(args);
        return { memes: [], chat_records: [], total: 0, chat_total: 0 };
      },
      start_page_cache_warmup: async () => ({}),
      get_tag_records: async () => [], get_collections: async () => [], get_categories: async () => [],
      record_meme_uses: async ids => { records.push(ids); return { ok: recordOk, ids }; },
      resolve_similar_import: async () => ({ ok: true, existing_id: 7 }),
      resolve_large_meme_import: async () => ({ duplicate: true, duplicate_ids: [7] }),
    } };
    global.window = { pywebview: global.pywebview };
  global.document = new (require('jsdom').JSDOM)('<!doctype html><html></html>').window.document;
    const moduleInstance = new Module(filename, module);
    moduleInstance.filename = filename;
    moduleInstance.paths = Module._nodeModulePaths(path.dirname(filename));
    moduleInstance._compile(compiled, filename);
    const use = moduleInstance.exports.useMemes();
    await use.loadInitData();
    const { state } = use;
    const context = vm.createContext({ ...use, api: async (method, ...args) => global.pywebview.api[method](...args),
      ALL_GENRES: ['image', 'gif', 'chat_record'], sidebarTab: ref('categories'), showToast() {},
      similarImportDialog: { value: { open: async () => 'keep_old' } },
      largeMemeImportDialog: { value: { open: async () => ({ mode: 'keep_original', remember: false }) } },
    });
    vm.runInContext(esbuild.transformSync(handlers, { loader: 'ts' }).code, context);
    for (const action of ['exact', 'similar', 'large']) {
      state.activeCategory = 42;
      state.activeCollection = null;
      state.searchQuery = 'old search';
      state.activeTags.add('old tag');
      if (action === 'exact') await context.revealExistingImports([7, 7, 8]);
      if (action === 'similar') await context.handleSimilarImport({ token: 'similar', candidates: [] });
      if (action === 'large') await context.handleLargeMemeImport({ token: 'large' });
      await settle();
      assert.deepEqual([...state.activeGenres], selected, action + ': checkbox state must not change');
      assert.deepEqual(persisted, selected, action + ': stored preference must not change');
      assert.equal(writes.length, 0, action + ': duplicate navigation must not save a new preference');
      assert.deepEqual(searches.at(-1)[6], selected);
      assert.equal(state.activeCollection, -3);
      assert.equal(state.activeCategory, null);
      assert.equal(state.searchQuery, '');
      assert.equal(state.activeTags.size, 0);
    }
    assert.deepEqual([...records[0]], [7, 8]);
    recordOk = false;
    state.activeCollection = 99;
    await context.revealExistingImports([7]);
    assert.equal(state.activeCollection, 99, 'failed recording must not navigate');
    assert.deepEqual([...state.activeGenres], selected);
    assert.deepEqual(persisted, selected);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_sort_mode_restores_and_reaches_library_and_warmup_without_resetting_genres():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "esbuild").is_dir():
        pytest.skip("排序状态回归测试需要 Node 和前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const esbuild = require('esbuild');
const filename = path.resolve('src/vue-src/composables/useMemes.ts');
const compiled = esbuild.buildSync({ entryPoints: [filename], bundle: true,
  platform: 'node', format: 'cjs', external: ['vue'], write: false }).outputFiles[0].text;
let savedSort = 'name_desc', releaseFirst;
const searches = [], warmups = [], writes = [], genreWrites = [];
global.document = new (require('jsdom').JSDOM)('<!doctype html><html></html>').window.document;
global.pywebview = { api: {
  get_init_data: async () => ({ sort_mode: savedSort, library_genres: ['gif'],
    memes: [], categories: [], collections: [], tags: [] }),
  search_library: async (...args) => {
    searches.push(args); return { memes: [], chat_records: [], chat_total: 0, total: 500 };
  },
  start_page_cache_warmup: async (...args) => { warmups.push(args); return {}; },
  set_library_genres: async value => { genreWrites.push(value); return { ok: true }; },
  save_sort_mode: async mode => {
    writes.push(mode);
    if (writes.length === 1) await new Promise(resolve => { releaseFirst = resolve; });
    savedSort = mode;
    return { ok: true };
  },
} };
global.window = { pywebview: global.pywebview };
function session() {
  const instance = new Module(filename, module);
  instance.filename = filename;
  instance.paths = Module._nodeModulePaths(path.dirname(filename));
  instance._compile(compiled, filename);
  return instance.exports.useMemes();
}
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const use = session();
  await use.loadInitData();
  assert.equal(use.state.sortMode, 'name_desc');
  assert.equal(searches.at(-1)[8], 'name_desc');
  assert.equal(warmups.at(-1)[8], 'name_desc');
  assert.deepEqual(searches.at(-1)[6], ['gif']);
  use.state.activeCategory = -12;
  use.state.activeCollection = 42;
  use.state.searchQuery = 'kept';
  const first = use.setSortMode('name_asc');
  const last = use.setSortMode('manifest_desc');
  await settle();
  assert.deepEqual(writes, ['name_asc']);
  releaseFirst();
  await Promise.all([first, last]);
  assert.deepEqual(writes, ['name_asc', 'manifest_desc']);
  assert.equal(savedSort, 'manifest_desc');
  assert.equal(searches.at(-1)[8], 'manifest_desc');
  assert.equal(warmups.at(-1)[8], 'manifest_desc');
  assert.deepEqual([...use.state.activeGenres], ['gif']);
  assert.equal(use.state.activeCategory, -12);
  assert.equal(use.state.activeCollection, 42);
  assert.equal(use.state.searchQuery, 'kept');
  assert.equal(genreWrites.length, 0);
  await use.setSortMode('invalid');
  assert.equal(writes.length, 2);
  const reopened = session();
  await reopened.loadInitData();
  assert.equal(reopened.state.sortMode, 'manifest_desc');
  assert.deepEqual([...reopened.state.activeGenres], ['gif']);
  assert.equal(reopened.canReorder(), false);
  await reopened.setSortMode('manifest');
  assert.equal(reopened.canReorder(), true);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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


def test_sidebar_tabs_preserve_content_and_navigation():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "esbuild").is_dir():
        pytest.skip("侧栏前端交互测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Module = require('node:module');
const { ref, computed } = require('vue');
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
const { state, setActiveCollection, setActiveCategory, search } =
  moduleInstance.exports.useMemes();
const app = fs.readFileSync('src/vue-src/App.vue', 'utf8');
const start = app.indexOf('const sidebarTab =');
const end = app.indexOf('const tagGroups =', start);
assert.ok(start >= 0 && end > start);
const names = ['switchSidebarTab', 'onSidebarSelect', 'onBreadcrumbClick',
  'onFolderCardClick', 'onFolderCardContext'];
const handlers = names.map(name => {
  const match = app.match(new RegExp('function ' + name + '\\([^]*?\\n\\}'));
  assert.ok(match, name);
  return match[0];
}).join('\n');
const menus = [];
const context = { ref, computed, state, setActiveCollection, setActiveCategory, search,
  onCategoryRightClick: (...args) => menus.push(['category', ...args]),
  ctx: { show: (...args) => menus.push(['collection', ...args]) },
};
vm.createContext(context);
vm.runInContext(esbuild.transformSync(app.slice(start, end) + handlers, {
  loader: 'ts',
}).code, context);
const value = name => vm.runInContext(name + '.value', context);
const evaluate = source => vm.runInContext(source, context);
const settle = () => new Promise(resolve => setImmediate(resolve));
const event = { preventDefault() {}, stopPropagation() {} };
function populate(kind, id) {
  state.activeCollection = kind === 'collections' ? id : null;
  state.activeCategory = kind === 'categories' ? id : null;
  state.page = 4;
  state.pageCount = 7;
  state.total = 1201;
  state.chatTotal = 1;
  state.searchQuery = '保留关键词';
  state.activeTags = new Set(['风格\u001f可爱']);
  state.activeGenres = ['gif', 'chat_record'];
  state.selectedIds = new Set([101]);
  state.memes = [{ id: 101, filename: 'kept.gif' }];
  state.chatRecords = [{ id: 201, title: '保留聊天记录' }];
  state.loading = true;
}
(async () => {
  state.collections = [
    { id: -3, name: '最近使用' }, { id: -5, name: '随机' },
    { id: 10, name: '分组父级', children: [{ id: 11, name: '子分组' }] },
  ];
  state.categories = [
    { id: -1, name: '全部' }, { id: -4, name: '未分类' },
    { id: 10, name: '类别父级', children: [{ id: 11, name: '子类别' }] },
  ];
  for (const [kind, id] of [
    ['collections', 10], ['categories', 10], ['collections', -5],
    ['collections', -3], ['categories', -4], ['collections', null],
  ]) {
    populate(kind, id);
    evaluate('sidebarTab.value = ' + JSON.stringify(kind));
    evaluate("hoveredSidebarNode.value = { kind: 'collection', id: 10 }");
    const before = JSON.stringify(state);
    const memes = state.memes, records = state.chatRecords, selection = state.selectedIds;
    const folders = value('folderCards'), crumbs = value('breadcrumb');
    const callCount = calls.length;
    const other = kind === 'collections' ? 'categories' : 'collections';
    context.switchSidebarTab(other);
    await settle();
    assert.equal(value('sidebarTab'), other);
    assert.equal(value('hoveredSidebarNode'), null);
    assert.equal(value('sidebarItems'), other === 'categories' ? state.categories : state.collections);
    assert.equal(JSON.stringify(state), before);
    assert.equal(state.memes, memes);
    assert.equal(state.chatRecords, records);
    assert.equal(state.selectedIds, selection);
    assert.equal(value('folderCards'), folders);
    assert.equal(value('breadcrumb'), crumbs);
    assert.equal(calls.length, callCount);
    assert.equal(value('activeSidebarId'), id == null && other === 'categories' ? -1 : null);
    context.switchSidebarTab(kind);
    context.switchSidebarTab(kind);
    await settle();
    assert.equal(JSON.stringify(state), before);
    assert.equal(calls.length, callCount);
  }
  for (const kind of ['collections', 'categories']) {
    populate(kind, 10);
    evaluate('sidebarTab.value = ' + JSON.stringify(kind));
    context.switchSidebarTab(kind === 'collections' ? 'categories' : 'collections');
    assert.equal(value('breadcrumb').at(-1).name, kind === 'categories' ? '类别父级' : '分组父级');
    assert.equal(value('folderCards')[0].name, kind === 'categories' ? '子类别' : '子分组');
    context.onFolderCardContext(event, 11, 'child');
    assert.equal(menus.at(-1)[0], kind === 'categories' ? 'category' : 'collection');
    context.onFolderCardClick(11);
    await settle();
    assert.equal(calls.at(-1)[2], kind === 'collections' ? 11 : null);
    assert.equal(calls.at(-1)[5], kind === 'categories' ? 11 : null);
    context.onBreadcrumbClick(10);
    await settle();
    assert.equal(calls.at(-1)[2], kind === 'collections' ? 10 : null);
    assert.equal(calls.at(-1)[5], kind === 'categories' ? 10 : null);
    context.onBreadcrumbClick(null);
    await settle();
    assert.equal(state.activeCategory, null);
    assert.equal(state.activeCollection, null);
    populate(kind, 10);
    context.onSidebarSelect(11);
    await settle();
    assert.equal(calls.at(-1)[2], kind === 'categories' ? 11 : null);
    assert.equal(calls.at(-1)[5], kind === 'collections' ? 11 : null);
    assert.equal(state.page, 1);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_hierarchy_pointer_drag_confirms_and_preserves_other_interactions():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "esbuild").is_dir():
        pytest.skip("层级拖动测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Module = require('node:module');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<aside id="sidebar"><header id="root" data-hierarchy-root="category"></header><div id="tree"></div></aside>');
for (const name of ['window', 'document', 'HTMLElement', 'Element', 'SVGElement', 'Node']) global[name] = dom.window[name];
const { ref, reactive, computed, createApp, h, nextTick } = require('vue');
const { parse, compileScript } = require('@vue/compiler-sfc');
const esbuild = require('esbuild');
const app = fs.readFileSync('src/vue-src/App.vue', 'utf8');
const state = reactive({ categories: [], collections: [] });
const sidebarTab = ref('categories'), sidebarCollapsed = ref(false);
let confirmation;
let result = { ok: true };
const prompts = [], calls = [], refreshes = [], toasts = [], clicks = [];
const context = vm.createContext({
  ref, state, sidebarTab, sidebarCollapsed, document,
  sidebarItems: computed(() => sidebarTab.value === 'categories' ? state.categories : state.collections),
  clearHoverPreview() {},
  confirmAsk: (title, message) => { prompts.push([title, message]); return new Promise(resolve => { confirmation = resolve; }); },
  api: async (...args) => { calls.push(args); return result; },
  refreshLibraryView: async kind => { refreshes.push(kind); },
  showToast: message => { toasts.push(message); },
  ctx: { visible: { value: false } }, hoverPreview: { value: null },
  selectMode: { value: false }, sortEnabled: { value: false },
});
const extract = name => app.match(new RegExp('(?:async )?function ' + name + '\\([^]*?\\n\\}'))[0];
const start = app.indexOf('let hierarchyDrag:');
const end = app.indexOf('function beginLibraryDrag', start);
const helpers = ['findCollectionNode', 'subtreeIds', 'onDocPointerMove', 'onDocPointerUp',
  'onDocPointerCancel', 'onDocKeydown', 'onSidebarSwipeDown', 'onSidebarSwipeMove',
  'onSidebarSwipeEnd', 'onSwipeClickCapture'].map(extract).join('\n');
vm.runInContext(esbuild.transformSync(app.slice(start, end) + '\nlet swipeStart = null; let swipeClickGuard = false;\n' + helpers, { loader: 'ts' }).code, context);
const value = name => vm.runInContext(name, context);
const descriptor = parse(fs.readFileSync('src/vue-src/components/CollectionTreeNode.vue', 'utf8'), { filename: 'CollectionTreeNode.vue' }).descriptor;
const componentCode = compileScript(descriptor, { id: 'hierarchy-test', inlineTemplate: true }).content;
const filename = path.resolve('src/vue-src/components/CollectionTreeNode.vue');
const compiled = new Module(filename, module);
compiled.filename = filename;
compiled.paths = Module._nodeModulePaths(path.dirname(filename));
compiled._compile(esbuild.transformSync(componentCode, { loader: 'ts', format: 'cjs' }).code, filename);
const component = compiled.exports.default;
let hit;
document.elementFromPoint = () => hit;
document.addEventListener('pointerdown', context.onSidebarSwipeDown);
document.addEventListener('click', context.onSwipeClickCapture, true);
const nodes = [
  { id: 1, name: 'source', children: [{ id: 2, name: 'child', children: [{ id: 3, name: 'grandchild' }] }] },
  { id: 4, name: 'target' },
  { id: -4, name: '未分类' },
  { id: -5, name: '未分类', direct_category_id: 1 },
  { id: 5, name: '隐藏', system_hidden: true },
];
state.categories = nodes;
state.collections = nodes;
const mounted = createApp({ setup() { return () => state.categories.map(item => h(component, {
  key: item.id, node: item, activeId: null, depth: 0, collapsed: false,
  dropKind: sidebarTab.value === 'categories' ? 'category' : 'collection',
  dropTargetKey: value('hierarchyDropTargetKey.value'), hierarchyDragId: value('hierarchyDragId.value'),
  onNodePointerdown: context.onHierarchyPointerDown,
  onSelect: id => clicks.push(id),
})); } });
mounted.mount('#tree');
const row = id => document.querySelector(`[data-hierarchy-id="${id}"]`);
const down = (id, target = row(id).querySelector('.tree-label'), type = 'mouse') => {
  const event = new dom.window.MouseEvent('pointerdown', { bubbles: true, cancelable: true, button: 0, clientX: 20, clientY: 20 });
  Object.defineProperties(event, { pointerId: { value: 8 }, pointerType: { value: type } });
  target.dispatchEvent(event);
};
const move = (id, distance = 12) => {
  hit = id === null ? document.getElementById('root') : row(id);
  const event = { pointerId: 8, clientX: 20 + distance, clientY: 20, preventDefault() {} };
  context.onDocPointerMove(event);
  return event;
};
const click = id => row(id).dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 }));
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  down(1); move(4, 4);
  assert.equal(value('hierarchyDrag.active'), false);
  await context.onDocPointerUp({ pointerId: 8, clientX: 24, clientY: 20, preventDefault() {} });
  click(1);
  assert.deepEqual(clicks, [1]);
  assert.equal(prompts.length, 0);
  down(1);
  const released = move(4);
  assert.equal(value('hierarchyDropTargetKey.value'), 'category:4');
  context.onSidebarSwipeMove({ clientX: -80, clientY: 20 });
  assert.equal(sidebarCollapsed.value, false, 'node drag must not collapse sidebar');
  const pending = context.onDocPointerUp(released);
  await settle();
  click(1);
  assert.deepEqual(clicks, [1], 'post-drag click is suppressed');
  assert.equal(prompts.length, 1);
  assert.ok(prompts[0][1].includes('子类别树'));
  assert.equal(calls.length, 0, 'cannot write before confirmation');
  confirmation(false); await pending;
  assert.equal(calls.length, 0);
  assert.equal(refreshes.length, 0);
  down(1); const confirmed = context.onDocPointerUp(move(4));
  await settle(); confirmation(true); await confirmed;
  assert.deepEqual(calls[0], ['move_hierarchy_node', 'category', 1, 4]);
  assert.deepEqual(refreshes, ['structure']);
  assert.equal(value('hierarchyDrag'), null);
  down(1); move(2); assert.equal(value('hierarchyDropTargetKey.value'), '');
  move(3); assert.equal(value('hierarchyDropTargetKey.value'), '');
  move(1); assert.equal(value('hierarchyDropTargetKey.value'), '');
  move(-4); assert.equal(value('hierarchyDropTargetKey.value'), '');
  move(-5); assert.equal(value('hierarchyDropTargetKey.value'), '');
  move(null); assert.equal(value('hierarchyDropTargetKey.value'), '', 'already at root');
  const escaped = { key: 'Escape', preventDefault() {} };
  context.onDocKeydown(escaped);
  assert.equal(value('hierarchyDrag'), null);
  down(2); const rootMove = context.onDocPointerUp(move(null));
  await settle(); confirmation(true); await rootMove;
  assert.deepEqual(calls.at(-1), ['move_hierarchy_node', 'category', 2, null]);
  down(2); move(1); assert.equal(value('hierarchyDropTargetKey.value'), '', 'same parent is a no-op');
  vm.runInContext('clearHierarchyDrag()', context);
  for (const id of [-4, -5, 5]) {
    down(id); assert.equal(value('hierarchyDrag'), null);
  }
  down(1, row(1).querySelector('.tree-toggle'));
  assert.equal(value('hierarchyDrag'), null);
  down(1, row(1).querySelector('button'));
  assert.equal(value('hierarchyDrag'), null);
  sidebarCollapsed.value = true;
  down(1); assert.equal(value('hierarchyDrag'), null);
  sidebarCollapsed.value = false;
  down(1, undefined, 'touch'); assert.equal(value('hierarchyDrag'), null);
  sidebarTab.value = 'collections'; await nextTick();
  document.getElementById('root').dataset.hierarchyRoot = 'collection';
  down(1); move(4);
  assert.equal(value('hierarchyDropTargetKey.value'), 'collection:4');
  row(4).dataset.hierarchyKind = 'category'; move(4);
  assert.equal(value('hierarchyDropTargetKey.value'), '', 'cross-kind drop is invalid');
  row(4).dataset.hierarchyKind = 'collection';
  result = { ok: false, error: '同名冲突' };
  const failed = context.onDocPointerUp(move(4));
  await settle(); confirmation(true); await failed;
  assert.equal(toasts.at(-1), '同名冲突');
  assert.deepEqual(calls.at(-1), ['move_hierarchy_node', 'collection', 1, 4]);
  assert.equal(refreshes.length, 2, 'failed move must not refresh or alter navigation');
  mounted.unmount();
  dom.window.close();
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_category_direct_frontend_navigation_and_controls():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "esbuild").is_dir():
        pytest.skip("类别未分类入口测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const Module = require('node:module');
const vue = require('vue');
const { ref, computed, createSSRApp } = vue;
const { renderToString } = require('@vue/server-renderer');
const { parse, compileScript } = require('@vue/compiler-sfc');
const esbuild = require('esbuild');
const queries = [];
let categories;
global.pywebview = { api: {
  search_library: async (...args) => {
    queries.push(args);
    return { memes: [], chat_records: [], chat_total: 0, total: 0 };
  },
  start_page_cache_warmup: async () => ({}),
  get_categories: async () => categories,
} };
const filename = path.resolve('src/vue-src/composables/useMemes.ts');
const compiled = esbuild.buildSync({ entryPoints: [filename], bundle: true,
  platform: 'node', format: 'cjs', external: ['vue'], write: false }).outputFiles[0].text;
function loadModule(code, file) {
  const instance = new Module(file, module);
  instance.filename = file;
  instance.paths = Module._nodeModulePaths(path.dirname(file));
  instance._compile(code, file);
  return instance.exports;
}
const { state, setActiveCollection, setActiveCategory, refreshCategories } =
  loadModule(compiled, filename).useMemes();
const app = fs.readFileSync('src/vue-src/App.vue', 'utf8');
const start = app.indexOf('const sidebarTab =');
const end = app.indexOf('const tagGroups =', start);
const names = ['onSidebarSelect', 'onBreadcrumbClick', 'onCategoryRightClick'];
const handlers = names.map(name => app.match(new RegExp('function ' + name + '\\([^]*?\\n\\}'))[0]).join('\n');
const menus = [];
const context = { ref, computed, state, setActiveCollection, setActiveCategory,
  ctx: { show: (...args) => menus.push(args) } };
vm.createContext(context);
vm.runInContext(esbuild.transformSync(app.slice(start, end) + handlers, { loader: 'ts' }).code, context);
const value = name => vm.runInContext(name + '.value', context);
const evaluate = source => vm.runInContext(source, context);
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const direct = { id: -14, name: '未分类', count: 2, direct_category_id: 10 };
  categories = [{ id: -4, name: '未分类' },
    { id: 10, name: '父类别', children: [direct, { id: 11, name: '子类别' }] }];
  state.categories = categories;
  evaluate("sidebarTab.value = 'categories'");
  state.searchQuery = 'keep';
  state.activeTags.add('keep');
  state.activeGenres = ['gif', 'chat_record'];
  context.onSidebarSelect(-14);
  await settle();
  assert.equal(state.activeCategory, -14);
  assert.equal(queries.at(-1)[5], -14);
  assert.equal(state.searchQuery, 'keep');
  assert.ok(state.activeTags.has('keep'));
  assert.deepEqual([...state.activeGenres], ['gif', 'chat_record']);
  assert.equal(value('activeTopLevel'), 10);
  assert.equal(value('activeSidebarId'), -14);
  assert.deepEqual(Array.from(value('breadcrumb'), item => item.name), ['全部', '父类别', '未分类']);
  assert.equal(value('folderCards').length, 0);
  context.onSidebarNodeHover(-14);
  assert.ok(context.isMemeInHoveredSidebarNode({ category_id: 10 }));
  assert.ok(!context.isMemeInHoveredSidebarNode({ category_id: 11 }));
  assert.ok(!context.isMemeInHoveredSidebarNode({ category_id: null }));
  const event = { preventDefault() {}, stopPropagation() {} };
  context.onCategoryRightClick(event, -14, '未分类');
  assert.equal(menus.length, 0);
  context.onBreadcrumbClick(10);
  await settle();
  assert.equal(state.activeCategory, 10);
  assert.equal(value('folderCards')[0].id, -14);
  context.onSidebarSelect(-14);
  await settle();
  categories = [{ id: -4, name: '未分类' }, { id: 10, name: '父类别', children: [{ id: 11, name: '子类别' }] }];
  await refreshCategories();
  assert.equal(state.activeCategory, 10, 'switch off returns to the real parent');
  assert.equal(queries.at(-1)[5], 10);
  state.activeCategory = -14;
  categories = [];
  await refreshCategories();
  assert.equal(state.activeCategory, null, 'deleted parent returns to all');
  const componentFile = path.resolve('src/vue-src/components/CollectionTreeNode.vue');
  const descriptor = parse(fs.readFileSync(componentFile, 'utf8'), { filename: componentFile }).descriptor;
  const componentCode = compileScript(descriptor, { id: 'direct-tree-test', inlineTemplate: true }).content;
  const component = loadModule(esbuild.transformSync(componentCode, { loader: 'ts', format: 'cjs' }).code, componentFile).default;
  const render = (node, options = {}) => renderToString(createSSRApp(component, {
    node, activeId: -14, depth: 1, collapsed: false, dropKind: 'category', dropTargetKey: '',
    ...options,
  }));
  const virtualHtml = await render(direct);
  assert.ok(!virtualHtml.includes('tree-more'));
  assert.ok(!virtualHtml.includes('data-library-drop-kind'));
  assert.ok(virtualHtml.includes('aria-selected="true"'));
  const realHtml = await render({ id: 10, name: '父类别', count: 3 });
  assert.ok(realHtml.includes('tree-more'));
  assert.ok(realHtml.includes('data-library-drop-kind="category"'));
  const { JSDOM } = require('jsdom');
  const fullName = '这是一个超出侧栏显示宽度的完整名称 <&" 示例';
  for (const dropKind of ['category', 'collection']) {
    for (const collapsed of [false, true]) {
      const html = await render({ id: 10, name: fullName, count: 3 }, { dropKind, collapsed });
      const dom = new JSDOM(html);
      assert.equal(dom.window.document.querySelector('.tree-row').title, fullName);
      assert.ok(!dom.window.document.querySelector('.tree-icon').hasAttribute('title'), 'icon must not block the row tooltip');
      dom.window.close();
    }
  }
  const virtualDom = new JSDOM(virtualHtml);
  assert.ok(virtualDom.window.document.querySelector('.tree-row').title.startsWith('未分类\n'));
  virtualDom.window.close();
  const randomDom = new JSDOM(await render({ id: -5, name: '随机' }, { dropKind: 'collection' }));
  assert.ok(randomDom.window.document.querySelector('.tree-row').title.includes('再次点击可换一批'));
  randomDom.window.close();
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_theme_html_uses_saved_theme_before_scripts():
    from src.webui import _theme_html

    html = '<html lang="zh-CN"><head></head><body></body></html>'
    light = _theme_html(html, "light")
    assert 'data-theme="light"' in light
    assert 'background:#f8fafc;color-scheme:light' in light
    assert 'lang="zh-CN"' in light
    for value in ("dark", None, '<script>alert(1)</script>'):
        dark = _theme_html(html, value)
        assert 'data-theme="dark"' in dark
        assert 'background:#0d0d0f;color-scheme:dark' in dark
        assert '<script>' not in dark


def test_theme_settings_persist_refresh_and_reset(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    import src.platform_util as platform_util
    import src.webui as webui

    monkeypatch.setattr(platform_util, "is_auto_start_enabled", lambda: False)
    monkeypatch.setattr(platform_util, "set_auto_start", lambda enabled: None)
    monkeypatch.setattr(webui, "_cloud_reset", lambda: None)
    window = Mock()
    monkeypatch.setattr(
        webui, "webview", SimpleNamespace(windows=[window]), raising=False
    )
    cfg = Config(tmp_path / "config.json")
    main = webui.JsApi.__new__(webui.JsApi)
    main._cfg = cfg
    settings = webui.SettingsApi.__new__(webui.SettingsApi)
    settings._cfg = cfg
    settings._webui = SimpleNamespace(_on_hotkey_change=Mock())
    assert main.get_settings()["theme"] == "dark"
    assert settings.get_settings()["theme"] == "dark"

    assert settings.save_settings({"theme": "light", "hotkey": "Ctrl+Alt+N"}) == {
        "ok": True,
        "theme": "light",
    }
    settings._webui._on_hotkey_change.assert_not_called()
    assert Config(tmp_path / "config.json").get("theme") == "light"
    assert main.get_settings()["theme"] == "light"
    assert settings.get_settings()["theme"] == "light"
    assert '"theme": "light"' in window.evaluate_js.call_args.args[0]

    assert settings.reset_settings()["theme"] == "dark"
    assert Config(tmp_path / "config.json").get("theme") == "dark"
    assert main.get_settings()["theme"] == "dark"


# 执行真实前端函数，验证并发保存、失败恢复与刷新乱序。
def test_theme_frontend_races():
    script = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const source = fs.readFileSync('src/vue-src/settings/legacy.js', 'utf8');
const elements = new Proxy({}, {get: (o, k) => o[k] ||= {value: '', checked: false}});
const calls = [], themes = [], toasts = [];
let resolveSave;
const context = vm.createContext({
  console, document: {getElementById: id => elements[id]},
  collectSyncSettings: () => ({}), collectNapCatSettings: () => ({}),
  refreshNapCatListenerStatus: () => {}, validateSync: () => true,
  api: (method, values) => {
    calls.push([method, values]);
    return new Promise(resolve => {resolveSave = resolve});
  },
  applyTheme: value => themes.push(value), showToast: value => toasts.push(value),
  applyWindowResizeHandles: () => {}, _lastCloudDirect: false,
  _settingsDirty: true, winResizeEnabled: false,
});
vm.runInContext(source.slice(source.indexOf('let settingsQueue'), source.indexOf('// 打开设置向导')), context);
(async () => {
  elements['s-theme'].value = 'light';
  const first = context.saveSettings();
  elements['s-theme'].value = 'dark';
  const second = context.saveSettings();
  await Promise.resolve();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].theme, 'light');
  resolveSave({ok: true, theme: 'light'});
  await first;
  await Promise.resolve();
  assert.equal(calls.length, 2);
  assert.equal(calls[1][1].theme, 'dark');
  resolveSave(null);
  await second;
  assert.deepEqual(themes, ['light']);
  assert.ok(toasts.at(-1).includes('失败'));
  const third = context.saveSettings();
  await Promise.resolve();
  resolveSave({ok: true, theme: 'dark'});
  await third;
  assert.deepEqual(themes, ['light', 'dark']);

  const app = fs.readFileSync('src/vue-src/App.vue', 'utf8');
  let refresh = app.slice(app.indexOf('  let settingsRefreshGen'), app.indexOf('  window.refreshTags ='));
  refresh = refresh.replaceAll(': any', '').replace('(settings?)', '(settings)');
  const pending = [];
  context.window = {pywebview: {api: {get_settings: () => new Promise(r => pending.push(r))}}};
  context.state = {};
  context.syncBadge = {value: null};
  context.refreshLibraryView = context.search = context.syncWinResizeHandles = () => {};
  vm.runInContext(refresh, context);
  context.window.refreshMemes();
  context.window.refreshMemes();
  pending[1]({theme: 'light'});
  await Promise.resolve();
  pending[0]({theme: 'dark'});
  await Promise.resolve();
  assert.equal(themes.at(-1), 'light');
  context.window.refreshMemes();
  context.window.refreshMemes({theme: 'dark'});
  pending[2]({theme: 'light'});
  await Promise.resolve();
  assert.equal(themes.at(-1), 'dark');
})().catch(e => {console.error(e); process.exitCode = 1});
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
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


def test_batch_category_picker_and_refresh():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "esbuild").is_dir():
        pytest.skip("类别多选前端测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<div id="app"></div><input id="cb-name">');
for (const name of ['window', 'document', 'HTMLElement', 'Element', 'SVGElement', 'Node']) global[name] = dom.window[name];
const { createApp, nextTick } = require('vue');
const calls = [];
global.pywebview = { api: {
  get_categories: async () => { calls.push('categories'); return [
    { id: -1, name: '全部' }, { id: -4, name: '未分类' },
    { id: 10, name: '父类别', children: [
      { id: -14, name: '未分类', direct_category_id: 10 },
      { id: 11, name: '子类别' },
    ] },
  ]; },
  get_collections: async () => { calls.push('collections'); return [{ id: 20, name: '分组' }]; },
  get_collection_members: async () => { throw new Error('Picker must not load members'); },
  search_memes: async () => { throw new Error('Picker must not load library'); },
} };
const filename = path.resolve('src/vue-src/composables/useCollectionBuilder.ts');
const result = require('esbuild').buildSync({ entryPoints: [filename], bundle: true,
  platform: 'node', format: 'cjs', external: ['vue'], write: false });
const compiled = new Module(filename, module);
compiled.paths = Module._nodeModulePaths(path.dirname(filename));
compiled._compile(result.outputFiles[0].text, filename);
let cb;
const app = createApp({ setup() { cb = compiled.exports.useCollectionBuilder(); return () => null; } });
app.mount('#app');
(async () => {
  const confirmed = [];
  await cb.openPick(r => confirmed.push(r), 'category', [101, 102]);
  assert.deepEqual(calls, ['categories']);
  assert.deepEqual(cb.collectionOptions.value.map(o => o.id), [0, 10, 11]);
  cb.collectionName.value = '父类别';
  await nextTick();
  assert.deepEqual(cb.filteredCollectionOptions.value.map(o => o.id), [10, 11]);
  cb.selectCollection(cb.collectionOptions.value[2]);
  await nextTick();
  cb.confirm();
  assert.deepEqual(confirmed[0], { name: '父类别/子类别', memeIds: [101, 102], existingId: 11 });
  await cb.openPick(r => confirmed.push(r), 'category', [101, 102]);
  cb.selectCollection(cb.collectionOptions.value[0]);
  await nextTick();
  cb.confirm();
  assert.equal(confirmed[1].existingId, 0);
  await cb.openPick(r => confirmed.push(r), 'category', [101, 102]);
  cb.collectionName.value = '新父级/新子级';
  cb.createNew();
  await nextTick();
  cb.confirm();
  assert.equal(confirmed[2].existingId, null);
  assert.equal(confirmed[2].name, '新父级/新子级');
  await cb.openPick(r => confirmed.push(r), 'category', [101]);
  cb.close();
  assert.equal(confirmed.length, 3);
  await cb.openPick(r => confirmed.push(r), 'add', [101]);
  assert.deepEqual(cb.collectionOptions.value.map(o => o.id), [20]);
  assert.equal(calls.at(-1), 'collections');
  cb.close();
  app.unmount();
  dom.window.close();

  const source = fs.readFileSync('src/vue-src/App.vue', 'utf8');
  assert.match(source, /@click="batchSetCategory">设置类别/);
  const block = source.match(/function batchSetCategory\(\) \{[\s\S]*?\n\}/)[0];
  let picker;
  const apiCalls = [];
  const events = [];
  const context = vm.createContext({ state: { selectedIds: new Set([101, 102]) },
    cb: { openPick: (callback, mode, ids) => { picker = { callback, mode, ids }; } },
    api: async (...args) => { apiCalls.push(args); return { ok: true, count: 2 }; },
    showToast: value => events.push(value),
    refreshLibraryView: async kind => events.push(`refresh:${kind}`),
  });
  vm.runInContext(block, context);
  context.batchSetCategory();
  assert.equal(picker.mode, 'category');
  context.state.selectedIds.clear();
  await picker.callback({ existingId: 0, name: '未分类' });
  assert.deepEqual([...apiCalls[0][1]], [101, 102]);
  assert.equal(apiCalls[0][0], 'batch_set_meme_category');
  assert.equal(apiCalls[0][2], 0);
  assert.equal(apiCalls[0][3], '');
  assert.equal(events.at(-1), 'refresh:structure');
  context.state.selectedIds.add(101);
  context.batchSetCategory();
  await picker.callback({ existingId: null, name: '新父级/新子级' });
  assert.equal(apiCalls.at(-1)[2], null);
  assert.equal(apiCalls.at(-1)[3], '新父级/新子级');
  context.api = async () => ({ ok: false, error: '移动失败' });
  events.length = 0;
  await picker.callback({ existingId: 11, name: '父类别/子类别' });
  assert.deepEqual(events, ['移动失败']);
  picker = null;
  context.state.selectedIds.clear();
  context.batchSetCategory();
  assert.equal(picker, null);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_escape_closes_details_before_hiding_main_window():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "esbuild").is_dir():
        pytest.skip("Esc 分层操作测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {JSDOM}=require('jsdom');
const {transformSync}=require('esbuild');
const dom=new JSDOM('<main id="main" tabindex="0"></main><div id="meme"><input></div><div id="chat"><input></div>');
const document=dom.window.document;
const read=name=>fs.readFileSync(`src/vue-src/${name}`,'utf8');
const extract=(source,name)=>source.match(new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n\\}`))[0];
const state={meme:false,chat:false,blocked:false,hidden:0,closed:[]};
const close=kind=>{state.closed.push(kind);if(!state.blocked)state[kind]=false;};
const ctx={visible:{value:false},hide(){this.visible.value=false;}};
const hoverPreview={value:false},selectMode={value:false},sortEnabled={value:false};
const appContext=vm.createContext({ctx,hoverPreview,selectMode,sortEnabled,hierarchyDrag:null,
  syncBadge:{value:null},
  memeDetails:{value:{isOpen:()=>state.meme,close:()=>close('meme')}},
  chatRecordDetails:{value:{isOpen:()=>state.chat,close:()=>close('chat')}},
  hideWindow:()=>state.hidden++,clearHoverPreview:()=>hoverPreview.value=false,
  toggleSelect:()=>selectMode.value=false,toggleSort:()=>sortEnabled.value=false,
});
vm.runInContext(transformSync(extract(read('App.vue'),'onDocKeydown'),{loader:'ts'}).code,appContext);
document.addEventListener('keydown',appContext.onDocKeydown);
for(const [name,kind]of[['MemeDetails','meme'],['ChatRecordDetails','chat']]){
  const source=read(`components/${name}.vue`);
  assert.match(source,/isOpen: \(\) => visible.value/);
  const box=document.getElementById(kind);
  const context=vm.createContext({boxEl:{value:box},close:()=>close(kind),
    focusNextDetailInput:()=>false,trapTabFocus:()=>{},isTextEditing:()=>true,
  });
  vm.runInContext(transformSync(extract(source,'onKeydown'),{loader:'ts'}).code,context);
  box.addEventListener('keydown',context.onKeydown);
}
const key=(target,options={})=>{const e=new dom.window.KeyboardEvent('keydown',
  {key:'Escape',bubbles:true,cancelable:true,...options});target.dispatchEvent(e);return e;};
for(const kind of ['meme','chat']){
  state[kind]=true;
  const input=document.querySelector(`#${kind} input`);
  input.focus();assert.equal(key(input).defaultPrevented,true);
  assert.equal(state[kind],false);assert.equal(state.hidden,0);
  key(document.body,{repeat:true});assert.equal(state.hidden,0,'Held Esc hid the main window');
  state[kind]=true;key(document.body);
  assert.equal(state[kind],false);assert.equal(state.hidden,0,'Unfocused detail leaked Esc to main');
  state[kind]=true;key(input,{repeat:true});assert.equal(state[kind],true);
  key(input,{isComposing:true});assert.equal(state[kind],true);
  key(document.body,{isComposing:true});assert.equal(state[kind],true);
  state[kind]=false;
}
state.meme=true;state.blocked=true;key(document.body);
assert.equal(state.meme,true);assert.equal(state.hidden,0,'A busy detail caused main to hide');
state.blocked=false;
ctx.visible.value=true;key(document.body);
assert.equal(ctx.visible.value,false);assert.equal(state.meme,true);
key(document.body);assert.equal(state.meme,false);
const handled=new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true});
handled.preventDefault();document.body.dispatchEvent(handled);assert.equal(state.hidden,0);
key(document.body,{keyCode:229});assert.equal(state.hidden,0);
hoverPreview.value=true;key(document.body);assert.equal(hoverPreview.value,false);assert.equal(state.hidden,0);
selectMode.value=true;key(document.body);assert.equal(selectMode.value,false);assert.equal(state.hidden,0);
sortEnabled.value=true;key(document.body);assert.equal(sortEnabled.value,false);assert.equal(state.hidden,0);
key(document.getElementById('main'));assert.equal(state.hidden,1,'Fresh main-page Esc must still hide the window');
key(document.body,{repeat:true});assert.equal(state.hidden,1);
dom.window.close();
"""
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_subpages_suspend_global_drop_import_without_blocking_text_edits():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "esbuild").is_dir():
        pytest.skip("子页面拖放测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const dom = new JSDOM(`<div id="main"></div><div id="modal" role="dialog" aria-modal="true">
  <textarea id="text"></textarea><input id="input"><input id="readonly" readonly>
  <input id="disabled" disabled><input id="checkbox" type="checkbox">
  <div id="editable" contenteditable="true"><span id="nested">文本</span></div>
  <button id="button">关闭</button></div>`);
const document = dom.window.document;
dom.window.HTMLElement.prototype.getClientRects = function() {
  return this.hidden || this.closest('[hidden]') || dom.window.getComputedStyle(this).display==='none' ? [] : [{}];
};
Object.defineProperty(document.getElementById('editable'),'isContentEditable',{value:true});
const source = fs.readFileSync('src/vue-src/App.vue','utf8');
const names = ['ignoreImportDrag','onDragEnter','onDragOver','onDragLeave','onDrop'];
const functions = names.map(name => {
  const block = source.match(new RegExp(`(?:async )?function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n\\}`));
  assert.ok(block,`Missing ${name}`);return block[0];
}).join('\n');
const script = require('esbuild').transformSync(functions,{loader:'ts',target:'es2020'}).code;
const calls = [];
const dragOver = {value:true};
const context = vm.createContext({
  document, Element:dom.window.Element, HTMLInputElement:dom.window.HTMLInputElement,
  getComputedStyle:dom.window.getComputedStyle.bind(dom.window), dragOver,
  nativeDragActive:false, dragCounter:3,
  window:{pywebview:{api:{download_original_image:async uri=>{calls.push(['import',uri]);return {ok:true};}}}},
  finishMemeImport:async()=>calls.push(['finished']),
  showToast:message=>calls.push(['toast',message]),
});
vm.runInContext(script,context);
const event = (target,types=['text/plain']) => ({
  target, defaultPrevented:false,
  preventDefault(){this.defaultPrevented=true;},
  dataTransfer:{types,getData(){calls.push(['read']);return 'https://example.test/meme.png';},
    get files(){throw new Error('Blocked drops must not read files');}},
});
(async()=>{
  for(const name of ['onDragEnter','onDragOver','onDragLeave','onDrop']){
    for(const id of ['text','input','nested']){
      const e=event(document.getElementById(id));await context[name](e);
      assert.equal(e.defaultPrevented,false,`${name} prevented native text editing`);
      assert.equal(dragOver.value,false);assert.equal(context.dragCounter,0);
    }
    for(const id of ['main','modal','readonly','disabled','checkbox','button']){
      const e=event(document.getElementById(id));await context[name](e);
      assert.equal(e.defaultPrevented,true,`${name} allowed navigation outside an editor`);
    }
  }
  for(const types of [['Files'],['Files','text/plain'],['text/uri-list','Files']]){
    const e=event(document.getElementById('text'),types);await context.onDrop(e);
    assert.equal(e.defaultPrevented,true,'File drops must not navigate or import');
  }
  assert.deepEqual(calls,[],'Subpage drag events reached the import pipeline');
  document.getElementById('modal').remove();
  for(const props of [
    {className:'import-overlay'},{className:'imp-overlay'},{className:'upd-overlay'},
    {className:'ctx-overlay'},{className:'sync-warning-overlay'},
    {id:'sync-progress-overlay'},{id:'sync-done-overlay'},{id:'startup-anim'},
  ]){
    const overlay=Object.assign(document.createElement('div'),props);document.body.append(overlay);
    await context.onDrop(event(document.getElementById('main')));
    assert.deepEqual(calls,[]);overlay.remove();
  }
  for(const style of ['display:none','visibility:hidden']){
    const hidden=document.createElement('div');hidden.setAttribute('aria-modal','true');
    hidden.style.cssText=style;document.body.append(hidden);
    const e=event(document.getElementById('main'));await context.onDrop(e);
    assert.equal(e.defaultPrevented,true);assert.equal(calls.filter(call=>call[0]==='import').length,1);
    calls.length=0;hidden.remove();
  }
  const enter=event(document.getElementById('main'));context.onDragEnter(enter);
  assert.equal(enter.defaultPrevented,true);assert.equal(dragOver.value,true);
  context.onDragLeave(event(document.getElementById('main')));assert.equal(dragOver.value,false);
  await context.onDrop(event(document.getElementById('main')));
  assert.ok(calls.some(call=>call[0]==='import'));
  assert.ok(calls.some(call=>call[0]==='finished'));
  assert.equal(dragOver.value,false);assert.equal(context.dragCounter,0);
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>dom.window.close());
"""
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_details_enter_navigation_and_save_shortcuts():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "esbuild").is_dir():
        pytest.skip("详情键盘测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const { JSDOM } = require('jsdom');
const dom = new JSDOM(`<div id="box"><input id="first"><input disabled>
  <input readonly><input hidden><input type="hidden"><input type="checkbox">
  <fieldset disabled><input></fieldset><div inert><input></div>
  <textarea id="text"></textarea><input id="combo" role="combobox">
  <select id="select"><option>父类别</option></select><input id="last">
  <button id="save">保存</button></div>`);
global.HTMLElement = dom.window.HTMLElement;
dom.window.HTMLElement.prototype.getClientRects = function() { return this.hidden ? [] : [{}]; };
const filename = path.resolve('src/vue-src/utils/api.ts');
const result = require('esbuild').buildSync({ entryPoints:[filename], bundle:true,
  platform:'node', format:'cjs', write:false });
const compiled = new Module(filename, module);
compiled.paths = Module._nodeModulePaths(path.dirname(filename));
compiled._compile(result.outputFiles[0].text, filename);
const { focusNextDetailInput, handleDetailSaveShortcut } = compiled.exports;
const document = dom.window.document;
const box = document.getElementById('box');
let saved = 0, localEnters = 0;
box.addEventListener('keydown', e => handleDetailSaveShortcut(e, () => saved++), true);
box.addEventListener('keydown', e => focusNextDetailInput(box, e));
document.getElementById('combo').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); localEnters++; }
});
const key = (id, options = {}) => {
  const field = document.getElementById(id); field.focus();
  const event = new dom.window.KeyboardEvent('keydown', {key:'Enter',bubbles:true,cancelable:true,...options});
  field.dispatchEvent(event); return event;
};
assert.equal(key('first').defaultPrevented,true);
assert.equal(document.activeElement.id,'text');
assert.equal(key('text',{shiftKey:true}).defaultPrevented,false);
assert.equal(document.activeElement.id,'text');
key('text');assert.equal(document.activeElement.id,'combo');
key('combo');assert.equal(localEnters,1);assert.equal(document.activeElement.id,'combo');
key('combo',{ctrlKey:true});assert.equal(saved,1);assert.equal(localEnters,1);
key('text',{ctrlKey:true});assert.equal(saved,2);assert.equal(document.activeElement.id,'text');
key('text',{metaKey:true});assert.equal(saved,3);
key('first',{ctrlKey:true,key:'s'});assert.equal(saved,4);
key('first',{ctrlKey:true,repeat:true});assert.equal(saved,4);
assert.equal(key('first',{isComposing:true}).defaultPrevented,false);
assert.equal(document.activeElement.id,'first');
assert.equal(key('combo',{isComposing:true,ctrlKey:true}).defaultPrevented,false);
assert.equal(saved,4);assert.equal(localEnters,1);
assert.equal(key('first',{keyCode:229}).defaultPrevented,false);
key('last');assert.equal(document.activeElement.id,'last');assert.equal(saved,4);
assert.equal(key('save').defaultPrevented,false);
assert.equal(key('select').defaultPrevented,false);
assert.equal(key('first',{altKey:true}).defaultPrevented,false);
for (const [name, save] of [['MemeDetails','saveChanges'],['ChatRecordDetails','saveManagement']]) {
  const source = fs.readFileSync(`src/vue-src/components/${name}.vue`,'utf8');
  assert.ok(source.includes(`@keydown.capture="handleDetailSaveShortcut($event, ${save})"`));
  assert.ok(source.includes('focusNextDetailInput(boxEl.value, e)'));
  assert.ok(source.includes('Ctrl+Enter'));
}
dom.window.close();
"""
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_chat_record_readonly_text_is_selectable_without_changing_controls():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "esbuild").is_dir():
        pytest.skip("聊天文字拖选测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const { JSDOM } = require('jsdom');
const { parse, compileScript, compileStyle } = require('@vue/compiler-sfc');
const dom = new JSDOM('<div id="app"></div>');
for (const name of ['window', 'document', 'HTMLElement', 'Element', 'SVGElement', 'Node']) global[name] = dom.window[name];
const { createApp, nextTick } = require('vue');
const filename = path.resolve('src/vue-src/components/ChatRecordDetails.vue');
const { descriptor } = parse(fs.readFileSync(filename, 'utf8'), { filename });
const id = 'data-v-chat-selection-test';
const compiledStyle = compileStyle({ filename, id, scoped: true, source: descriptor.styles[0].content });
assert.deepEqual(compiledStyle.errors, []);
const style = document.createElement('style');
style.textContent = 'body { user-select: none; }\n' + compiledStyle.code;
document.head.appendChild(style);
const compiled = require('esbuild').buildSync({
  stdin: { contents: compileScript(descriptor, { id, inlineTemplate: true }).content,
    sourcefile: filename, resolveDir: path.dirname(filename), loader: 'ts' },
  bundle: true, platform: 'node', format: 'cjs', external: ['vue'], write: false,
}).outputFiles[0].text;
const moduleInstance = new Module(filename, module);
moduleInstance.paths = Module._nodeModulePaths(path.dirname(filename));
moduleInstance._compile(compiled, filename);
const component = moduleInstance.exports.default;
component.__scopeId = id;
global.pywebview = { api: { get_chat_record_details: async () => ({
  ok: true, id: 1, name: '可复制的记录名称', media_folder: 'archive', title: '记录',
  genre_label: '聊天记录', node_count: 2, max_depth: 2, media_count: 0,
  peer_label: '来源群聊', peer_id: '123', source_message_id: '456', updated_at: '2026-10-20',
  source_locations: [{ source_self_id: '1', source_type: 'group', source_peer_id: '123', source_real_seq: '456' }],
  send_command: '/omm send 1', all_tags: [], all_tag_records: [], tags: [], tag_records: [],
  all_categories: [], all_collections: [], collections: [], blocked_message_paths: [],
  messages: [{ path: 'n0', depth: 0, sender: '发言者', time: 1, preview: '第一行消息\n第二行消息',
    quotes: [{ sender: '原发言者', time: 1, preview: '被引用的文字' }] },
    { path: 'n1', depth: 1, sender: '另一人', time: 1, preview: '嵌套正文' }],
}) } };
const mounted = createApp(component);
const details = mounted.mount('#app');
(async () => {
  try {
    await details.open(1); await nextTick();
    const selectable = ['.chat-details-header h2', '.chat-details-meta dt', '.chat-details-meta dd',
      '.chat-source-location-list span', '.chat-command-help > span', '.chat-command-help code',
      '.chat-message-identity strong', '.chat-message-identity time', '.chat-quoted-head span',
      '.chat-quoted-head time', '.chat-message > p', '.chat-quoted-message p', '.nested-label'];
    for (const selector of selectable) {
      const elements = [...document.querySelectorAll(selector)];
      assert.ok(elements.length, selector);
      for (const element of elements) {
        assert.equal(window.getComputedStyle(element).userSelect, 'text', selector);
        assert.equal(window.getComputedStyle(element).cursor, 'text', selector);
      }
    }
    const paragraph = document.querySelector('.chat-message > p');
    const range = document.createRange(); range.selectNodeContents(paragraph);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    assert.equal(window.getSelection().toString(), '第一行消息\n第二行消息');
    const copy = new window.KeyboardEvent('keydown', { key: 'c', ctrlKey: true, bubbles: true, cancelable: true });
    paragraph.dispatchEvent(copy);
    assert.equal(copy.defaultPrevented, false, 'Ctrl+C must retain native copy');
    const pointer = (type, target) => {
      const event = new window.MouseEvent(type, { bubbles: true, button: 0 });
      Object.defineProperties(event, { isPrimary: { value: true }, pointerId: { value: 1 } });
      target.dispatchEvent(event);
    };
    const overlay = document.querySelector('.chat-details-overlay');
    pointer('pointerdown', paragraph); pointer('pointerup', overlay); overlay.click();
    assert.ok(details.isOpen(), 'text selection released outside must not dismiss details');
    const toggle = document.querySelector('.chat-block-toggle');
    assert.equal(window.getComputedStyle(toggle).userSelect, 'none');
    const checkbox = toggle.querySelector('input');
    checkbox.click(); await nextTick();
    assert.equal(checkbox.checked, true, 'blocking control still works');
    for (const element of document.querySelectorAll('button, .chat-option, .chat-field')) {
      assert.notEqual(window.getComputedStyle(element).userSelect, 'text', 'controls must not be opted into text selection');
    }
  } finally {
    mounted.unmount(); dom.window.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", script],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_details_backdrop_ignores_selection_drags_but_allows_outside_clicks():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "esbuild").is_dir():
        pytest.skip("详情遮罩交互测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const filename = path.resolve('src/vue-src/utils/api.ts');
const result = require('esbuild').buildSync({ entryPoints: [filename], bundle: true,
  platform: 'node', format: 'cjs', write: false });
const compiled = new Module(filename, module);
compiled.paths = Module._nodeModulePaths(path.dirname(filename));
compiled._compile(result.outputFiles[0].text, filename);
const overlay = {}, inside = {};
let closed = 0;
const guard = compiled.exports.createBackdropClickGuard(() => closed++);
const event = (target, overrides = {}) => ({ target, currentTarget: overlay,
  pointerId: 1, button: 0, isPrimary: true,
  preventDefault() { throw new Error('Text selection must not be prevented'); },
  stopPropagation() { throw new Error('Normal controls must not be intercepted'); },
  ...overrides });
const gesture = (start, end, click = overlay) => {
  guard.onPointerDown(event(start));
  guard.onPointerUp(event(end));
  guard.onClick(event(click));
};
gesture(inside, overlay);
assert.equal(closed, 0, 'Selecting inside and releasing outside must not close');
gesture(overlay, inside);
assert.equal(closed, 0, 'Releasing inside must not close even if click targets the overlay');
gesture(inside, inside, inside);
assert.equal(closed, 0, 'Normal input clicks must not close');
gesture(overlay, overlay);
assert.equal(closed, 1, 'Normal outside clicks must still close');
guard.onClick(event(overlay));
assert.equal(closed, 1, 'A stale or unmatched click must not close');
guard.onPointerDown(event(overlay, { button: 2 }));
guard.onPointerUp(event(overlay, { button: 2 }));
guard.onClick(event(overlay));
assert.equal(closed, 1, 'Right-click must not close');
guard.onPointerDown(event(overlay, { isPrimary: false }));
guard.onPointerUp(event(overlay));
guard.onClick(event(overlay));
assert.equal(closed, 1, 'A secondary pointer must not close');
guard.onPointerDown(event(overlay));
guard.onPointerUp(event(overlay, { pointerId: 2 }));
guard.onClick(event(overlay));
assert.equal(closed, 1, 'Mismatched pointer IDs must not close');
guard.onPointerDown(event(overlay));
guard.reset();
guard.onPointerUp(event(overlay));
guard.onClick(event(overlay));
assert.equal(closed, 1, 'Cancelled or previous-dialog gestures must not close');
gesture(inside, overlay);
gesture(overlay, overlay);
assert.equal(closed, 2, 'A ignored drag must not swallow the following valid click');
for (const name of ['MemeDetails', 'ChatRecordDetails']) {
  const source = fs.readFileSync(`src/vue-src/components/${name}.vue`, 'utf8');
  assert.match(source, /const backdrop = createBackdropClickGuard\(close\)/);
  assert.match(source, /@pointerdown\.capture="backdrop.onPointerDown"/);
  assert.match(source, /@pointerup\.capture="backdrop.onPointerUp"/);
  assert.match(source, /@pointercancel\.capture="backdrop.reset"/);
  assert.match(source, /@click="backdrop.onClick"/);
  assert.doesNotMatch(source, /@click\.self="close"/);
  assert.match(source, /async function open\([^)]*\) \{\s*backdrop.reset\(\)/);
}
"""
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_details_actions_stay_outside_scrolling_content():
    import shutil

    import pytest

    root = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "@vue" / "compiler-sfc").is_dir():
        pytest.skip("详情操作栏结构测试需要 Node 和已安装的前端依赖")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { parse } = require('@vue/compiler-sfc');
const { parse: parseTemplate } = require('@vue/compiler-dom');
for (const [filename, boxClass, contentClass, footerClass, saveHandler] of [
  ['MemeDetails', 'details-box', 'details-content', 'details-actions', 'saveChanges'],
  ['ChatRecordDetails', 'chat-details-box', 'chat-details-content', 'chat-management-actions', 'saveManagement'],
]) {
  const source = fs.readFileSync(`src/vue-src/components/${filename}.vue`, 'utf8');
  const { descriptor, errors } = parse(source);
  assert.deepEqual(errors, []);
  const ast = parseTemplate(descriptor.template.content);
  const hasClass = (node, name) => node.props?.some(prop =>
    prop.type === 6 && prop.name === 'class' && prop.value?.content.split(/\s+/).includes(name));
  const find = (node, name) => hasClass(node, name) ? node :
    node.children?.map(child => find(child, name)).find(Boolean);
  const box = find(ast, boxClass);
  const footer = find(ast, footerClass);
  const content = find(ast, contentClass);
  assert.ok(box.children.includes(footer), `${filename}: footer must be a direct dialog child`);
  assert.ok(box.children.indexOf(footer) > box.children.indexOf(content));
  assert.equal(find(content, footerClass), undefined);
  const buttons = footer.children.filter(node => node.tag === 'button');
  assert.equal(buttons.length, 2);
  const handler = node => node.props.find(prop => prop.type === 7 &&
    prop.name === 'on' && prop.arg?.content === 'click')?.exp?.content;
  assert.deepEqual(buttons.map(handler), ['close', saveHandler]);
  assert.ok(buttons.every(node => node.props.some(prop => prop.type === 7 &&
    prop.name === 'bind' && prop.arg?.content === 'disabled' && prop.exp.content.includes('saving'))));
  const css = descriptor.styles.map(style => style.content).join('\n');
  const footerCss = css.match(new RegExp(`\\.${footerClass}\\s*\\{([^}]+)\\}`))[1];
  assert.match(footerCss, /flex-shrink:\s*0/);
  assert.match(footerCss, /justify-content:\s*flex-end/);
  assert.doesNotMatch(css, new RegExp(`\\.${boxClass}\\s*\\{[^}]*overflow-y:\\s*auto`));
}
"""
    result = subprocess.run(
        [node, "-e", script], cwd=root, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


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


# Vue 构建缺失时，实际回退路由仍下发已保存的主题。
def test_theme_legacy_route(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    import src.webui as webui

    (tmp_path / "index.html").write_text(
        '<html lang="zh"><head></head><body>fallback</body></html>', encoding="utf-8"
    )
    monkeypatch.setattr(webui, "HTML_DIR", tmp_path)
    runner = Mock()
    monkeypatch.setattr(webui.bottle, "run", runner)
    ui = webui.WebUI.__new__(webui.WebUI)
    ui._port = 17853
    ui._cfg = SimpleNamespace(get=lambda key, default=None: "light")
    ui._setup_bottle()
    app = runner.call_args.args[0]
    route = next(route for route in app.routes if route.rule == "/")
    html = route.callback()
    assert 'data-theme="light"' in html
    assert "fallback" in html
    assert webui.bottle.response.content_type == "text/html; charset=utf-8"
