"""OhMyMeme 核心模块测试"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
import zipfile
from urllib.error import HTTPError
from pathlib import Path

# 确保 src 在导入路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import __app_name__, __version__, onebot, qq_drag
from src.config import Config
from src.crypto_util import decrypt_data, encrypt_data
from src.database import MemeDB


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertIsNotNone(re.match(r"\d+\.\d+\.\d+", __version__))
        self.assertEqual(__app_name__, "OhMyMeme")


class TestCrypto(unittest.TestCase):
    def test_encrypt_decrypt(self):
        original = "test_secret_key_123!@#"
        enc = encrypt_data(original)
        self.assertNotEqual(enc, original)
        dec = decrypt_data(enc)
        self.assertEqual(dec, original)

    def test_empty(self):
        self.assertEqual(decrypt_data(""), "")
        self.assertEqual(encrypt_data(""), "")

    def test_invalid(self):
        self.assertEqual(decrypt_data("invalid!@#base64!!"), "")


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.config_path = self.tmp_dir / "config.json"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_defaults(self):
        cfg = Config(self.config_path)
        self.assertEqual(cfg.get("hotkey"), "Ctrl+Alt+N")
        self.assertIs(cfg.get("hotkey_show_at_mouse"), False)
        self.assertEqual(cfg.get("sync_auto_fetch_index"), False)
        self.assertEqual(cfg.get("sync_auto_sync"), False)
        self.assertEqual(cfg.get("sync_type"), "")
        self.assertEqual(cfg.get("ftp_host"), "")
        self.assertEqual(cfg.get("cache_max_size_mb"), 500)
        self.assertIs(cfg.get("virtual_drive_compat"), False)
        self.assertIs(cfg.get("bulk_read_protection"), False)
        self.assertEqual(cfg.get("bulk_read_batch_size"), 8)
        self.assertEqual(cfg.get("bulk_read_pause_seconds"), 1.0)
        self.assertIs(cfg.get("bulk_read_prefetch_pages"), False)
        self.assertIs(cfg.get("search_description"), True)
        self.assertIs(cfg.get("search_image_text"), True)
        self.assertIs(cfg.get("napcat_enabled"), False)
        self.assertEqual(cfg.get("napcat_http_url"), "http://127.0.0.1:3000")
        self.assertEqual(cfg.get("napcat_ws_url"), "ws://127.0.0.1:3001")
        self.assertEqual(cfg.get("napcat_webui_url"), "http://127.0.0.1:6099")
        self.assertEqual(cfg.get("napcat_command"), "/omm")
        self.assertEqual(cfg.get("napcat_send_mode"), "original_native")
        self.assertEqual(cfg.get("napcat_image_send_mode"), "sticker")
        self.assertIs(cfg.get("qq_drag_cleanup_on_start"), True)
        self.assertIs(cfg.get("import_resolution_limit_enabled"), False)
        self.assertEqual(cfg.get("import_resolution_limit_px"), 2560)

    def test_import_resolution_limit_persists_and_resets(self):
        from src.config import get_import_max_px

        self.config_path.write_text('{"hotkey": "Ctrl+Shift+X"}', encoding="utf-8")
        cfg = Config(self.config_path)
        self.assertEqual(get_import_max_px(cfg), 0)
        cfg.update_from_dict(
            {"import_resolution_limit_enabled": True, "import_resolution_limit_px": 4096}
        )
        cfg.save()
        reloaded = Config(self.config_path)
        self.assertEqual(get_import_max_px(reloaded), 4096)
        reloaded.set("import_resolution_limit_enabled", False)
        self.assertEqual(get_import_max_px(reloaded), 0)
        self.assertEqual(reloaded.get("import_resolution_limit_px"), 4096)
        reloaded.reset()
        self.assertEqual(get_import_max_px(reloaded), 0)
        self.assertEqual(reloaded.get("import_resolution_limit_px"), 2560)

    def test_import_resolution_limit_normalizes_invalid_thresholds(self):
        from src.config import get_import_max_px

        cfg = Config(self.config_path)
        cfg.set("import_resolution_limit_enabled", True)
        for value, expected in (
            ("4096", 4096),
            (0, 1),
            (-1, 1),
            (100000, 65535),
            (None, 2560),
            ("invalid", 2560),
            (True, 2560),
            (float("inf"), 2560),
        ):
            with self.subTest(value=value):
                cfg.set("import_resolution_limit_px", value)
                self.assertEqual(cfg.get("import_resolution_limit_px"), expected)
                self.assertEqual(get_import_max_px(cfg), expected)

    def test_qq_drag_cleanup_setting_persists_and_resets(self):
        cfg = Config(self.config_path)
        cfg.update_from_dict({"qq_drag_cleanup_on_start": False})
        cfg.save()
        reloaded = Config(self.config_path)
        self.assertIs(reloaded.get("qq_drag_cleanup_on_start"), False)
        reloaded.reset()
        self.assertIs(reloaded.get("qq_drag_cleanup_on_start"), True)

    def test_set_get(self):
        cfg = Config(self.config_path)
        cfg.set("hotkey", "Ctrl+Shift+X")
        self.assertEqual(cfg.get("hotkey"), "Ctrl+Shift+X")

    def test_legacy_gif_send_mode_migrates_to_all_images(self):
        from src import __version__

        self.config_path.write_text(
            json.dumps(
                {"version": __version__, "napcat_gif_send_mode": "image"}
            ),
            encoding="utf-8",
        )

        cfg = Config(self.config_path)

        self.assertEqual(cfg.get("napcat_image_send_mode"), "image")

    def test_s3_path_persists_through_bulk_update(self):
        cfg = Config(self.config_path)
        cfg.update_from_dict({"s3_path": "memes"})
        cfg.save()

        self.assertEqual(Config(self.config_path).get("s3_path"), "memes")

    def test_encrypted_secret(self):
        cfg = Config(self.config_path)
        cfg.set("s3_secret_key", "my_secret")
        saved = cfg._data["s3_secret_key"]
        # 应该是加密后的字符串
        self.assertNotEqual(saved, "my_secret")
        self.assertTrue(len(saved) > 20)

    def test_notion_token_is_encrypted(self):
        cfg = Config(self.config_path)
        cfg.set("notion_token", "ntn_test_secret")
        self.assertNotEqual(cfg._data["notion_token"], "ntn_test_secret")
        self.assertEqual(cfg.get("notion_token"), "ntn_test_secret")

    def test_napcat_token_is_encrypted(self):
        cfg = Config(self.config_path)
        cfg.set("napcat_token", "napcat_test_secret")
        self.assertNotEqual(cfg._data["napcat_token"], "napcat_test_secret")
        self.assertEqual(cfg.get("napcat_token"), "napcat_test_secret")

    def test_napcat_webui_token_is_encrypted(self):
        cfg = Config(self.config_path)
        cfg.set("napcat_webui_token", "webui_test_secret")
        self.assertNotEqual(cfg._data["napcat_webui_token"], "webui_test_secret")
        self.assertEqual(cfg.get("napcat_webui_token"), "webui_test_secret")

    def test_save_load(self):
        cfg = Config(self.config_path)
        cfg.set("hotkey", "Ctrl+Alt+N")
        cfg.set("s3_access_key", "AKID123")
        cfg.save()

        cfg2 = Config(self.config_path)
        self.assertEqual(cfg2.get("hotkey"), "Ctrl+Alt+N")
        self.assertEqual(cfg2.get("s3_access_key"), "AKID123")

    def test_hotkey_show_at_mouse_old_config_defaults_false(self):
        self.config_path.write_text('{"hotkey": "Ctrl+Shift+X"}', encoding="utf-8")

        cfg = Config(self.config_path)

        self.assertIs(cfg.get("hotkey_show_at_mouse"), False)

    def test_hotkey_show_at_mouse_persists_and_resets(self):
        cfg = Config(self.config_path)
        cfg.set("hotkey_show_at_mouse", True)
        cfg.save()

        reloaded = Config(self.config_path)
        self.assertIs(reloaded.get("hotkey_show_at_mouse"), True)

        reloaded.reset()
        reloaded.save()

        reset = Config(self.config_path)
        self.assertIs(reset.get("hotkey_show_at_mouse"), False)

    def test_manifest_include_tags_default_on(self):
        cfg = Config(self.config_path)
        self.assertIs(cfg.get("manifest_include_tags"), True)
        self.assertIs(Config.DEFAULTS.get("manifest_include_tags"), True)

        cfg.set("manifest_include_tags", False)
        cfg.save()
        self.assertIs(Config(self.config_path).get("manifest_include_tags"), False)

    def test_manifest_include_favorites_default_on(self):
        cfg = Config(self.config_path)
        self.assertIs(cfg.get("manifest_include_favorites"), True)
        self.assertIs(Config.DEFAULTS.get("manifest_include_favorites"), True)

        cfg.set("manifest_include_favorites", False)
        cfg.save()
        self.assertIs(
            Config(self.config_path).get("manifest_include_favorites"), False
        )

    def test_removed_auto_paste_setting_is_not_retained(self):
        self.config_path.write_text('{"auto_paste_meme": true}', encoding="utf-8")

        cfg = Config(self.config_path)

        self.assertNotIn("auto_paste_meme", cfg.to_dict())
        cfg.save()
        self.assertNotIn(
            "auto_paste_meme", self.config_path.read_text(encoding="utf-8")
        )

    def test_property_access(self):
        cfg = Config(self.config_path)
        cfg.auto_start = True
        self.assertTrue(cfg.auto_start)

    def test_cache_dir_default_empty(self):
        cfg = Config(self.config_path)
        self.assertEqual(cfg.get("cache_dir"), "")

    def test_cache_dir_custom(self):
        cfg = Config(self.config_path)
        custom = self.tmp_dir / "my_memes"
        cfg.set("cache_dir", str(custom))
        self.assertEqual(cfg.cache_dir, custom)
        self.assertTrue(custom.exists())
        cfg.save()
        cfg2 = Config(self.config_path)
        self.assertEqual(cfg2.cache_dir, custom)

    def test_cache_dir_resolve(self):
        cfg = Config(self.config_path)
        raw = self.tmp_dir / "a" / ".." / "my_memes"
        cfg.set("cache_dir", str(raw))
        self.assertEqual(cfg.cache_dir, (self.tmp_dir / "my_memes").resolve())
        self.assertTrue((self.tmp_dir / "my_memes").exists())

    def test_chat_record_media_dir_follows_cache_dir(self):
        cfg = Config(self.config_path)
        cfg.set("cache_dir", str(self.tmp_dir / "meme-library"))

        self.assertEqual(
            cfg.chat_record_media_dir,
            cfg.cache_dir / "chat-record-media",
        )
        self.assertTrue(cfg.chat_record_media_dir.is_dir())

    def test_cache_dir_virtual_drive_fallback_requires_opt_in(self):
        cfg = Config(self.config_path)
        custom = self.tmp_dir / "virtual_memes"
        cfg.set("cache_dir", str(custom))

        with mock.patch("src.config.Path.resolve", side_effect=OSError("unsupported")):
            with self.assertRaises(OSError):
                _ = cfg.cache_dir

        cfg.set("virtual_drive_compat", True)
        with mock.patch("src.config.Path.resolve", side_effect=OSError("unsupported")):
            self.assertEqual(cfg.cache_dir, custom)
        self.assertTrue(custom.exists())


class TestOneBot(unittest.TestCase):
    def test_qq_drag_cleanup_only_removes_old_owned_credentials_and_icons(self):
        now = 2000000000
        prefix = "ohmymeme-market-face-"
        uuid = "d33dc498-b7c1-4b65-ad36-b21d4197ae37"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expired = [
                root / f"{prefix}{uuid}.ommchat",
                root / f"{prefix}{uuid}-drag-icon.png",
            ]
            retained = [
                root / f"{prefix}{uuid}.png",
                root / f"{prefix}{uuid}.gif",
                root / f"{prefix}not-a-uuid.ommchat",
                root / f"{prefix}{uuid}.ommchat.bak",
                root / "other.ommchat",
                root / "ohmymeme-native-forward-test.json",
            ]
            for path in expired + retained:
                path.write_bytes(b"test")
                os.utime(path, (now - 86401, now - 86401))
            nested = root / "nested"
            nested.mkdir()
            nested_file = nested / f"{prefix}{uuid}.ommchat"
            nested_file.write_bytes(b"nested")
            os.utime(nested_file, (now - 86401, now - 86401))
            matching_directory = root / (
                f"{prefix}40f1227d-7897-4e1c-85b8-1c3f15b1c4a2.ommchat"
            )
            matching_directory.mkdir()
            with (
                mock.patch("src.onebot.tempfile.gettempdir", return_value=folder),
                mock.patch("src.onebot.time.time", return_value=now),
            ):
                result = onebot.cleanup_qq_drag_temp_files()
            self.assertEqual(result, {"removed": 2, "bytes": 8, "failed": 0})
            self.assertTrue(all(not path.exists() for path in expired))
            self.assertTrue(all(path.exists() for path in retained))
            self.assertTrue(nested_file.exists())
            self.assertTrue(matching_directory.is_dir())

    def test_qq_drag_cleanup_retains_recent_and_exactly_one_day_old_files(self):
        now = 2000000000
        names = [
            "d33dc498-b7c1-4b65-ad36-b21d4197ae37",
            "40f1227d-7897-4e1c-85b8-1c3f15b1c4a2",
            "96b1802e-3645-4e8d-bb6a-c00d0cc35400",
        ]
        with tempfile.TemporaryDirectory() as folder:
            for uuid, age in zip(names, [86400, 86399, -60]):
                path = Path(folder) / f"ohmymeme-market-face-{uuid}.ommchat"
                path.write_bytes(b"test")
                os.utime(path, (now - age, now - age))
            with (
                mock.patch("src.onebot.tempfile.gettempdir", return_value=folder),
                mock.patch("src.onebot.time.time", return_value=now),
            ):
                result = onebot.cleanup_qq_drag_temp_files()
            self.assertEqual(result, {"removed": 0, "bytes": 0, "failed": 0})
            self.assertEqual(len(list(Path(folder).iterdir())), 3)

    def test_qq_drag_cleanup_continues_after_a_locked_file(self):
        now = 2000000000
        unlink = Path.unlink
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            locked = (
                root
                / "ohmymeme-market-face-d33dc498-b7c1-4b65-ad36-b21d4197ae37.ommchat"
            )
            removable = (
                root
                / "ohmymeme-market-face-40f1227d-7897-4e1c-85b8-1c3f15b1c4a2-drag-icon.png"
            )
            for path in [locked, removable]:
                path.write_bytes(b"test")
                os.utime(path, (now - 86401, now - 86401))

            def delete(path, *args, **kwargs):
                if path.name == locked.name:
                    raise PermissionError("locked")
                return unlink(path, *args, **kwargs)

            with (
                mock.patch("src.onebot.tempfile.gettempdir", return_value=folder),
                mock.patch("src.onebot.time.time", return_value=now),
                mock.patch("src.onebot.Path.unlink", autospec=True, side_effect=delete),
            ):
                result = onebot.cleanup_qq_drag_temp_files()
            self.assertEqual(result, {"removed": 1, "bytes": 4, "failed": 1})
            self.assertTrue(locked.exists())
            self.assertFalse(removable.exists())

    def test_qq_drag_cleanup_skips_reparse_points(self):
        entry = mock.Mock()
        entry.name = "ohmymeme-market-face-d33dc498-b7c1-4b65-ad36-b21d4197ae37.ommchat"
        entry.is_file.return_value = True
        entry.stat.return_value.st_file_attributes = 0x400
        with tempfile.TemporaryDirectory() as folder:
            with (
                mock.patch("src.onebot.tempfile.gettempdir", return_value=folder),
                mock.patch("src.onebot.os.scandir") as scan,
                mock.patch("src.onebot.Path.unlink") as delete,
            ):
                scan.return_value.__enter__.return_value = [entry]
                result = onebot.cleanup_qq_drag_temp_files()
            delete.assert_not_called()
            entry.stat.assert_called_once_with(follow_symlinks=False)
            self.assertEqual(result["removed"], 0)

    def test_qq_drag_cleanup_handles_unreadable_temp_directory(self):
        with mock.patch("src.onebot.os.scandir", side_effect=PermissionError("denied")):
            result = onebot.cleanup_qq_drag_temp_files()
        self.assertEqual(result, {"removed": 0, "bytes": 0, "failed": 1})

    def test_send_meme_image_marks_image_as_sticker(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "meme.gif"
            image.write_bytes(b"GIF89a")
            with mock.patch(
                "src.onebot.call_action", return_value={"message_id": 42}
            ) as action:
                result = onebot.send_meme_image(
                    "http://127.0.0.1:3000",
                    "token",
                    image,
                    "group",
                    "123",
                    as_sticker=True,
                )

        self.assertEqual(result["message_id"], 42)
        self.assertEqual(action.call_args.args[2], "send_group_msg")
        self.assertEqual(
            action.call_args.args[3],
            {
                "group_id": "123",
                "message": [
                    {
                        "type": "image",
                        "data": {
                            "file": str(image.absolute()),
                            "sub_type": 1,
                            "summary": "[表情]",
                        },
                    }
                ],
            },
        )

    def test_send_meme_image_can_keep_normal_image_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "meme.gif"
            image.write_bytes(b"GIF89a")
            with mock.patch(
                "src.onebot.call_action", return_value={"message_id": 43}
            ) as action:
                onebot.send_meme_image(
                    "http://127.0.0.1:3000",
                    "token",
                    image,
                    "private",
                    "456",
                )

        self.assertEqual(action.call_args.args[2], "send_private_msg")
        self.assertEqual(
            action.call_args.args[3]["message"][0]["data"],
            {"file": str(image.absolute())},
        )

    def test_rebuilt_chat_record_preserves_animated_sticker_image_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "animated.gif"
            image.write_bytes(b"GIF89a")
            segments, degraded = onebot._prepare_sendable_segments(
                [
                    {
                        "type": "image",
                        "data": {
                            "local_path": str(image),
                            "sub_type": 1,
                            "summary": "[动画表情]",
                        },
                    }
                ]
            )

        self.assertEqual(degraded, 0)
        self.assertEqual(
            segments,
            [
                {
                    "type": "image",
                    "data": {
                        "file": str(image),
                        "sub_type": 1,
                        "summary": "[动画表情]",
                    },
                }
            ],
        )

    def test_rebuilt_chat_record_restores_market_face_instead_of_plain_image(self):
        resolver = mock.Mock(side_effect=AssertionError("商城表情不应按图片下载"))
        segments, degraded = onebot._prepare_sendable_segments(
            [
                {
                    "type": "image",
                    "data": {
                        "file": "market-face.gif",
                        "emoji_package_id": 12,
                        "emoji_id": "abcdef",
                        "key": "market-key",
                        "summary": "[商城表情]",
                    },
                }
            ],
            degrade_media=True,
            media_resolver=resolver,
        )

        resolver.assert_not_called()
        self.assertEqual(degraded, 0)
        self.assertEqual(
            segments,
            [
                {
                    "type": "mface",
                    "data": {
                        "emoji_package_id": 12,
                        "emoji_id": "abcdef",
                        "key": "market-key",
                        "summary": "[商城表情]",
                    },
                }
            ],
        )

    def test_token_normalization_trims_paste_whitespace_but_rejects_internal_newlines(self):
        self.assertEqual(onebot.normalize_token("  token-value\r\n"), "token-value")
        with self.assertRaises(onebot.OneBotError):
            onebot.normalize_token("first-line\nsecond-line")

    def test_only_accepts_loopback_service_urls(self):
        self.assertEqual(
            onebot.normalize_http_url("http://127.0.0.1:3000/"),
            "http://127.0.0.1:3000",
        )
        self.assertEqual(
            onebot.normalize_ws_url("ws://localhost:3001"),
            "ws://localhost:3001",
        )
        with self.assertRaises(onebot.OneBotError):
            onebot.normalize_http_url("http://192.168.1.10:3000")
        with self.assertRaises(onebot.OneBotError):
            onebot.normalize_http_url("http://127.0.0.1:3000/get_login_info")

    def test_connection_reads_login_and_version_without_exposing_full_qq(self):
        seen = []

        def fake_urlopen(request, timeout=0):
            seen.append((request, timeout))
            response = mock.MagicMock()
            response.__enter__.return_value = response
            if request.full_url.endswith("/get_login_info"):
                data = {"user_id": 123456789, "nickname": "测试账号"}
            else:
                data = {
                    "app_name": "NapCat.Onebot",
                    "app_version": "4.18.28",
                    "protocol_version": "v11",
                }
            payload = {"status": "ok", "retcode": 0, "data": data}
            response.read.return_value = json.dumps(payload).encode("utf-8")
            return response

        with mock.patch("src.onebot._open_request", side_effect=fake_urlopen):
            result = onebot.test_connection(
                "http://127.0.0.1:3000",
                "test-token",
                "ws://127.0.0.1:3001",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["nickname"], "测试账号")
        self.assertEqual(result["user_id"], "12*****89")
        self.assertEqual(result["app_name"], "NapCat.Onebot")
        self.assertEqual(result["protocol_version"], "v11")
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0][0].get_header("Authorization"), "Bearer test-token")

    def test_call_action_preserves_list_data(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {"status": "ok", "retcode": 0, "data": [{"msgId": "12"}]}
        ).encode("utf-8")
        with mock.patch("src.onebot._open_request", return_value=response):
            data = onebot.call_action(
                "http://127.0.0.1:3000", "token", "get_recent_contact"
            )
        self.assertEqual(data, [{"msgId": "12"}])

    def test_call_plugin_api_reads_authenticated_json_route(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {
                "ok": True,
                "available": True,
                "target": {
                    "message_type": "group",
                    "peer_id": "123",
                },
            }
        ).encode("utf-8")
        with mock.patch("src.onebot._open_request", return_value=response) as opener:
            data = onebot.call_plugin_api(
                "http://127.0.0.1:3000",
                "secret",
                "napcat-plugin-ohmymeme-current-peer",
                "current-peer",
            )

        self.assertTrue(data["available"])
        request = opener.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "http://127.0.0.1:3000/api/Plugin/ext/"
            "napcat-plugin-ohmymeme-current-peer/current-peer",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")

    def test_call_webui_plugin_api_logs_in_and_uses_credential(self):
        login_response = mock.MagicMock()
        login_response.__enter__.return_value = login_response
        login_response.read.return_value = json.dumps(
            {"code": 0, "data": {"Credential": "signed-credential"}}
        ).encode("utf-8")
        plugin_response = mock.MagicMock()
        plugin_response.__enter__.return_value = plugin_response
        plugin_response.read.return_value = json.dumps(
            {"ok": True, "available": False}
        ).encode("utf-8")

        with mock.patch(
            "src.onebot._open_request",
            side_effect=[login_response, plugin_response],
        ) as opener:
            data = onebot.call_webui_plugin_api(
                "http://127.0.0.1:6099",
                "webui-secret",
                "napcat-plugin-ohmymeme-current-peer",
                "current-peer",
            )

        self.assertFalse(data["available"])
        self.assertEqual(
            opener.call_args_list[0].args[0].full_url,
            "http://127.0.0.1:6099/api/auth/login",
        )
        self.assertEqual(
            opener.call_args_list[1].args[0].get_header("Authorization"),
            "Bearer signed-credential",
        )

    def test_call_webui_plugin_api_reauthenticates_after_stale_credential(self):
        def response(payload):
            value = mock.MagicMock()
            value.__enter__.return_value = value
            value.read.return_value = json.dumps(payload).encode("utf-8")
            return value

        stale = HTTPError(
            "http://127.0.0.1:6099/api/Plugin/ext/test/current-peer",
            401,
            "Unauthorized",
            {},
            None,
        )
        onebot._WEBUI_CREDENTIAL_CACHE.clear()
        with mock.patch(
            "src.onebot._open_request",
            side_effect=[
                response({"code": 0, "data": {"Credential": "old"}}),
                stale,
                response({"code": 0, "data": {"Credential": "new"}}),
                response({"ok": True, "available": True}),
            ],
        ) as opener:
            data = onebot.call_webui_plugin_api(
                "http://127.0.0.1:6099",
                "webui-secret",
                "test",
                "current-peer",
            )

        self.assertTrue(data["available"])
        self.assertEqual(opener.call_count, 4)
        self.assertEqual(
            opener.call_args_list[-1].args[0].get_header("Authorization"),
            "Bearer new",
        )

    def test_call_webui_plugin_api_encodes_query_parameters(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"ok": True}).encode("utf-8")
        onebot._WEBUI_CREDENTIAL_CACHE[("http://127.0.0.1:6099", "secret")] = (
            time.monotonic() + 60,
            "credential",
        )

        with mock.patch("src.onebot._open_request", return_value=response) as opener:
            onebot.call_webui_plugin_api(
                "http://127.0.0.1:6099",
                "secret",
                "test",
                "forward-message",
                params={"source_real_seq": 123, "label": "空 格"},
                max_response_bytes=1024,
            )

        self.assertEqual(
            opener.call_args.args[0].full_url,
            "http://127.0.0.1:6099/api/Plugin/ext/test/forward-message?"
            "source_real_seq=123&label=%E7%A9%BA+%E6%A0%BC",
        )
        response.read.assert_called_once_with(1025)

    def test_consumes_and_validates_chat_record_drag_ticket(self):
        ticket_id = "12345678-1234-4123-8123-123456789abc"
        source = {
            "message_id": 42,
            "message_type": "group",
            "group_id": 778899,
            "self_id": 112233,
            "real_seq": 9876,
            "message": [{"type": "forward", "data": {"id": "resource"}}],
        }
        with mock.patch(
            "src.onebot.call_webui_plugin_api",
            return_value={"ok": True, "source_message": source},
        ) as bridge:
            result = onebot.consume_chat_record_drag_ticket(
                "http://127.0.0.1:6099", "secret", ticket_id
            )

        self.assertIs(result, source)
        bridge.assert_called_once_with(
            "http://127.0.0.1:6099",
            "secret",
            "napcat-plugin-ohmymeme-current-peer",
            "consume-chat-record-ticket",
            timeout=onebot._CHAT_RECORD_PLUGIN_TIMEOUT,
            params={"ticket_id": ticket_id},
            max_response_bytes=onebot._MAX_CHAT_RECORD_PLUGIN_RESPONSE_BYTES,
        )

    def test_dragged_chat_record_reuses_standard_archive_pipeline(self):
        source = {
            "message_id": 42,
            "message_type": "group",
            "group_id": 778899,
            "self_id": 112233,
            "real_seq": 9876,
            "message": [{"type": "forward", "data": {"id": "resource"}}],
        }
        with (
            mock.patch(
                "src.onebot.consume_chat_record_drag_ticket", return_value=source
            ) as consume,
            mock.patch(
                "src.onebot.archive_chat_record", return_value={"ok": True, "id": 3}
            ) as archive,
        ):
            result = onebot.archive_dragged_chat_record(
                "http://127.0.0.1:3000",
                "onebot-secret",
                "http://127.0.0.1:6099",
                "webui-secret",
                "12345678-1234-4123-8123-123456789abc",
                db="database",
            )

        self.assertEqual(result, {"ok": True, "id": 3})
        consume.assert_called_once_with(
            "http://127.0.0.1:6099",
            "webui-secret",
            "12345678-1234-4123-8123-123456789abc",
        )
        archive.assert_called_once_with(
            "http://127.0.0.1:3000",
            "onebot-secret",
            42,
            trigger_event=source,
            db="database",
            webui_url="http://127.0.0.1:6099",
            webui_token="webui-secret",
            source_message=source,
        )

    def test_webui_imports_chat_record_ticket_and_marks_response(self):
        from src.webui import WebUI

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(Path(tmp) / "config.json")
            cfg.set("napcat_http_url", "http://127.0.0.1:3000")
            cfg.set("napcat_token", "onebot-secret")
            cfg.set("napcat_webui_url", "http://127.0.0.1:6099")
            cfg.set("napcat_webui_token", "webui-secret")
            webui = WebUI.__new__(WebUI)
            webui._cfg = cfg
            descriptor = json.dumps(
                {
                    "type": "ohmymeme.qq-chat-record-drag",
                    "schema_version": 1,
                    "ticket_id": "12345678-1234-4123-8123-123456789abc",
                }
            ).encode("utf-8")
            with (
                mock.patch("src.webui.get_db", return_value="database"),
                mock.patch(
                    "src.onebot.archive_dragged_chat_record",
                    return_value={"ok": True, "id": 7, "created": True},
                ) as archive,
            ):
                result = webui._import_chat_record_ticket(descriptor)

        self.assertEqual(
            result,
            {"ok": True, "id": 7, "created": True, "chat_record": True},
        )
        archive.assert_called_once_with(
            "http://127.0.0.1:3000",
            "onebot-secret",
            "http://127.0.0.1:6099",
            "webui-secret",
            "12345678-1234-4123-8123-123456789abc",
            db="database",
        )

    def test_rejects_token_header_injection(self):
        with self.assertRaises(onebot.OneBotError):
            onebot.call_action(
                "http://127.0.0.1:3000",
                "safe\r\nX-Injected: yes",
                "get_login_info",
            )

    def test_recent_contacts_returns_masked_local_summary(self):
        payload = [
            {
                "chatType": 2,
                "peerUin": "123456789",
                "peerName": "测试群",
                "msgId": "98765",
                "msgTime": 1700000000,
                "lastestMsg": {
                    "message": [
                        {"type": "text", "data": {"text": "你好"}},
                        {"type": "image", "data": {"file": "private.jpg"}},
                    ]
                },
            }
        ]
        with mock.patch("src.onebot.call_action", return_value=payload) as action:
            result = onebot.get_recent_contacts("http://127.0.0.1:3000", "secret", 10)

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["contacts"][0]["peer_id"], "12*****89")
        self.assertEqual(result["contacts"][0]["chat_type_label"], "群聊")
        self.assertEqual(result["contacts"][0]["preview"], "你好[图片]")
        action.assert_called_once_with(
            "http://127.0.0.1:3000",
            "secret",
            "get_recent_contact",
            {"count": 10},
            10,
        )

    def test_message_diagnosis_reads_history_and_nested_forward(self):
        actions = []

        def fake_action(http_url, token, action, params=None, timeout=5):
            actions.append((action, params, timeout))
            if action == "get_msg":
                return {
                    "self_id": 111111,
                    "message_id": 9988,
                    "message_seq": 77,
                    "message_type": "group",
                    "group_id": 123456789,
                    "group_name": "测试群",
                    "sender": {"user_id": 222222, "nickname": "Alice"},
                    "message": [{"type": "forward", "data": {"id": "forward-root"}}],
                }
            if action == "get_group_msg_history":
                return {"messages": [{"message_id": 9988}]}
            if params["message_id"] == "forward-root":
                return {
                    "messages": [
                        {
                            "message": [
                                {"type": "text", "data": {"text": "第一层"}},
                                {
                                    "type": "forward",
                                    "data": {"id": "forward-child"},
                                },
                            ]
                        }
                    ]
                }
            return {
                "messages": [
                    {"message": [{"type": "image", "data": {"file": "a.jpg"}}]}
                ]
            }

        with mock.patch("src.onebot.call_action", side_effect=fake_action):
            result = onebot.diagnose_message("http://127.0.0.1:3000", "secret", "9988")

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"]["group_id"], "12*****89")
        self.assertEqual(result["history"]["status"], "ok")
        self.assertEqual(result["history"]["message_count"], 1)
        self.assertEqual(result["forward"]["status"], "ok")
        self.assertEqual(result["forward"]["record_count"], 2)
        self.assertEqual(result["forward"]["node_count"], 2)
        self.assertEqual(result["forward"]["max_depth"], 2)
        self.assertEqual(
            result["forward"]["segment_types"], ["forward", "image", "text"]
        )
        self.assertEqual(
            {item[0] for item in actions},
            {"get_msg", "get_group_msg_history", "get_forward_msg"},
        )
        self.assertFalse(any(item[0].startswith("send_") for item in actions))

    def test_json_card_preview_uses_prompt_instead_of_segment_type(self):
        card = {
            "app": "com.tencent.qqshare",
            "prompt": "发现超好看的装扮",
            "meta": {"detail": {"title": "备用标题"}},
        }
        segment = {
            "type": "json",
            "data": {"data": json.dumps(json.dumps(card, ensure_ascii=False))},
        }

        self.assertEqual(
            onebot._segment_preview([segment]),
            "发现超好看的装扮",
        )

    def test_forward_diagnosis_stops_on_duplicate_record(self):
        def fake_action(http_url, token, action, params=None, timeout=5):
            if action == "get_msg":
                return {
                    "message_id": 1,
                    "message_type": "unknown",
                    "message": [{"type": "forward", "data": {"id": "loop"}}],
                }
            return {
                "messages": [{"message": [{"type": "forward", "data": {"id": "loop"}}]}]
            }

        with mock.patch("src.onebot.call_action", side_effect=fake_action) as action:
            result = onebot.diagnose_message("http://127.0.0.1:3000", "secret", "1")

        self.assertEqual(result["forward"]["record_count"], 1)
        self.assertEqual(action.call_count, 2)

    def test_command_event_requires_own_message_and_reply(self):
        event = {
            "post_type": "message_sent",
            "self_id": 123,
            "user_id": 123,
            "message": [
                {"type": "reply", "data": {"id": "7788"}},
                {"type": "text", "data": {"text": " /omm "}},
            ],
        }
        self.assertEqual(
            onebot.parse_command_event(event),
            {"matched": True, "target_message_id": "7788"},
        )
        event["post_type"] = "message"
        event["user_id"] = 456
        self.assertEqual(onebot.parse_command_event(event), {"matched": False})

        no_reply = {
            "post_type": "message_sent",
            "self_id": 123,
            "message": [{"type": "text", "data": {"text": "/omm"}}],
        }
        parsed = onebot.parse_command_event(no_reply)
        self.assertTrue(parsed["matched"])
        self.assertIn("回复", parsed["error"])

    def test_command_event_supports_explicit_message_id(self):
        event = {
            "post_type": "message_sent",
            "self_id": 123,
            "message": [{"type": "text", "data": {"text": "/omm 8899"}}],
        }
        self.assertEqual(
            onebot.parse_command_event(event),
            {"matched": True, "target_message_id": "8899"},
        )

    def test_command_event_supports_list_and_send(self):
        event = {
            "post_type": "message_sent",
            "self_id": 123,
            "message": [{"type": "text", "data": {"text": "/omm list"}}],
        }
        self.assertEqual(
            onebot.parse_command_event(event),
            {"matched": True, "action": "list"},
        )
        event["message"][0]["data"]["text"] = "/omm send 42"
        self.assertEqual(
            onebot.parse_command_event(event),
            {"matched": True, "action": "send", "record_id": 42},
        )

    def test_send_chat_record_rebuilds_archive_when_original_is_unavailable(self):
        package = {
            "title": "测试记录",
            "native_card": {
                "app": "com.tencent.multimsg",
                "meta": {"detail": {"resid": "old"}},
            },
            "source": {
                "self_id": 123,
                "message_id": 99,
                "message_type": "group",
                "group_id": 987,
                "message": [{"type": "forward", "data": {"id": "root"}}],
            },
            "forward_records": {
                "root": {
                    "omm_native_payload_b64": "bmF0aXZl",
                    "messages": [
                        {
                            "sender": {"user_id": 456, "nickname": "Alice"},
                            "message": [{"type": "text", "data": {"text": "你好"}}],
                        }
                    ],
                }
            },
        }
        db = mock.MagicMock()
        db.get_chat_record.return_value = package
        event = {
            "self_id": 123,
            "message_type": "group",
            "group_id": 789,
        }
        with mock.patch(
            "src.onebot.build_chat_record_package",
            side_effect=onebot.OneBotError("已过期"),
        ):

            def fake_action(http_url, token, action, params=None, timeout=5):
                if action == "forward_group_single_msg":
                    raise onebot.OneBotError("原消息已失效")
                return {"message_id": 100}

            with mock.patch(
                "src.onebot.call_action", side_effect=fake_action
            ) as action:
                result = onebot.send_chat_record(
                    "http://127.0.0.1:3000",
                    "token",
                    7,
                    event,
                    db=db,
                    send_mode="archive_rebuild",
                )

        self.assertEqual(result["source"], "archive_rebuild")
        self.assertEqual(result["message_id"], 100)
        self.assertEqual(action.call_args_list[-1].args[2], "send_group_forward_msg")
        params = action.call_args_list[-1].args[3]
        self.assertEqual(params["group_id"], 789)
        self.assertEqual(params["messages"][0]["data"]["nickname"], "Alice")
        self.assertEqual(
            params["messages"][0]["data"]["content"][0]["data"]["text"],
            "你好",
        )

    def test_send_chat_record_uses_qqnt_native_rebuild_when_webui_is_available(self):
        package = {
            "title": "测试记录",
            "native_card": {
                "app": "com.tencent.multimsg",
                "meta": {"detail": {"resid": "old"}},
            },
            "source": {
                "self_id": 123,
                "message_id": 99,
                "message_type": "group",
                "group_id": 987,
                "real_seq": 9988,
                "time": 1710000099,
                "sender": {"user_id": 654},
                "message": [{"type": "forward", "data": {"id": "root"}}],
            },
            "forward_records": {
                "root": {
                    "omm_native_payload_b64": "bmF0aXZl",
                    "messages": [
                        {
                            "sender": {
                                "user_id": 456,
                                "uid": "uid-456",
                                "nickname": "Alice",
                            },
                            "message_type": "group",
                            "group_id": 987,
                            "time": 1710000000,
                            "message": [{"type": "text", "data": {"text": "你好"}}],
                        }
                    ],
                }
            },
        }
        db = mock.MagicMock()
        db.get_chat_record.return_value = package
        captured = {}

        def fake_plugin(http_url, token, plugin_id, route, timeout=5, params=None):
            payload_path = Path(params["payload_path"])
            captured.update(json.loads(payload_path.read_text(encoding="utf-8")))
            # 桥接插件读取后会立即删除临时文件。
            payload_path.unlink()
            return {
                "ok": True,
                "message_id": "native-card-id",
                "strategy": "qqnt_native_replay",
            }

        with mock.patch(
            "src.onebot.call_webui_plugin_api", side_effect=fake_plugin
        ) as plugin:
            with mock.patch("src.onebot.call_action") as action:
                result = onebot.send_chat_record(
                    "http://127.0.0.1:3000",
                    "token",
                    7,
                    {
                        "self_id": 123,
                        "message_type": "private",
                        "target_id": 789,
                    },
                    db=db,
                    send_mode="archive_rebuild",
                    webui_url="http://127.0.0.1:6099",
                    webui_token="webui-token",
                )

        action.assert_not_called()
        self.assertEqual(plugin.call_args.args[3], "send-native-forward")
        self.assertEqual(captured["destination_type"], "private")
        self.assertEqual(captured["destination_peer_id"], "789")
        self.assertEqual(captured["messages"][0]["data"]["nickname"], "Alice")
        self.assertEqual(captured["messages"][0]["data"]["omm_sender_uid"], "uid-456")
        self.assertEqual(captured["messages"][0]["data"]["omm_source_type"], "group")
        self.assertEqual(captured["messages"][0]["data"]["omm_source_peer_id"], "987")
        self.assertEqual(captured["messages"][0]["data"]["time"], "1710000000")
        self.assertEqual(captured["native_payload_b64"], "bmF0aXZl")
        self.assertEqual(captured["native_card"]["app"], "com.tencent.multimsg")
        self.assertEqual(captured["native_source_type"], "group")
        self.assertEqual(captured["native_source_peer_id"], "987")
        self.assertEqual(result["message_id"], "native-card-id")
        self.assertTrue(result["native_rebuild"])
        self.assertTrue(result["native_replay"])

    def test_archive_rebuild_reads_inline_root_native_payload_from_package(self):
        package = {
            "title": "内联根记录",
            "omm_native_payload_b64": "aW5saW5lLW5hdGl2ZQ==",
            "native_card": {"app": "com.tencent.multimsg"},
            "source": {
                "self_id": 123,
                "message_id": 99,
                "message_type": "group",
                "group_id": 987,
                "real_seq": 9988,
                "message": [{
                    "type": "forward",
                    "data": {"content": [{
                        "sender": {"user_id": 456, "nickname": "Alice"},
                        "message": [{"type": "text", "data": {"text": "你好"}}],
                    }]},
                }],
            },
            "forward_records": {},
        }
        db = mock.MagicMock()
        db.get_chat_record.return_value = package
        captured = {}

        def fake_plugin(http_url, token, plugin_id, route, timeout=5, params=None):
            payload_path = Path(params["payload_path"])
            captured.update(json.loads(payload_path.read_text(encoding="utf-8")))
            payload_path.unlink()
            return {"ok": True, "strategy": "qqnt_native_replay"}

        with mock.patch(
            "src.onebot.call_webui_plugin_api", side_effect=fake_plugin
        ):
            result = onebot.send_chat_record(
                "http://127.0.0.1:3000",
                "token",
                7,
                {"self_id": 123, "message_type": "group", "group_id": 789},
                db=db,
                send_mode="archive_rebuild",
                webui_url="http://127.0.0.1:6099",
                webui_token="webui-token",
            )

        self.assertEqual(captured["native_payload_b64"], "aW5saW5lLW5hdGl2ZQ==")
        self.assertEqual(captured["native_card"]["app"], "com.tencent.multimsg")
        self.assertTrue(result["native_replay"])

    def test_archive_rebuild_filters_root_nodes_inside_native_payload(self):
        package = {
            "title": "带屏蔽规则的记录",
            "source": {
                "self_id": 123,
                "message_id": 99,
                "message_type": "group",
                "group_id": 987,
                "message": [{"type": "forward", "data": {"id": "root"}}],
            },
            "send_filter": {"blocked_node_paths": ["n1"]},
            "forward_records": {
                "root": {
                    "omm_native_payload_b64": "bmF0aXZl",
                    "messages": [
                        {
                            "sender": {"user_id": 456, "nickname": "Alice"},
                            "message": [{"type": "text", "data": {"text": "保留"}}],
                        },
                        {
                            "sender": {"user_id": 789, "nickname": "Ad"},
                            "message": [{"type": "text", "data": {"text": "广告"}}],
                        },
                    ],
                }
            },
        }
        db = mock.MagicMock()
        db.get_chat_record.return_value = package
        captured = {}

        def fake_plugin(http_url, token, plugin_id, route, timeout=5, params=None):
            payload_path = Path(params["payload_path"])
            captured.update(json.loads(payload_path.read_text(encoding="utf-8")))
            payload_path.unlink()
            return {
                "ok": True,
                "message_id": "filtered-card-id",
                "strategy": "qqnt_native_filtered_replay",
            }

        with mock.patch(
            "src.onebot.call_webui_plugin_api", side_effect=fake_plugin
        ):
            result = onebot.send_chat_record(
                "http://127.0.0.1:3000",
                "token",
                7,
                {"self_id": 123, "message_type": "group", "group_id": 789},
                db=db,
                send_mode="archive_rebuild",
                webui_url="http://127.0.0.1:6099",
                webui_token="webui-token",
            )

        self.assertEqual(captured["native_payload_b64"], "bmF0aXZl")
        self.assertEqual(captured["blocked_node_paths"], ["n1"])
        self.assertEqual(len(captured["messages"]), 1)
        self.assertEqual(captured["messages"][0]["data"]["nickname"], "Alice")
        self.assertEqual(
            captured["messages"][0]["data"]["content"][0]["data"]["text"],
            "保留",
        )
        self.assertTrue(result["native_replay"])
        self.assertTrue(result["native_filtered_replay"])

    def test_archive_rebuild_uses_node_rebuild_for_nested_filters(self):
        package = {
            "title": "带内层屏蔽规则的记录",
            "source": {
                "self_id": 123,
                "message_id": 99,
                "message_type": "group",
                "group_id": 987,
                "message": [{"type": "forward", "data": {"id": "root"}}],
            },
            "send_filter": {"blocked_node_paths": ["n0/s0/n1"]},
            "forward_records": {
                "root": {
                    "omm_native_payload_b64": "bmF0aXZl",
                    "messages": [{
                        "sender": {"user_id": 456, "nickname": "Alice"},
                        "message": [{
                            "type": "forward",
                            "data": {"content": [
                                {
                                    "sender": {"user_id": 789, "nickname": "Bob"},
                                    "message": [{"type": "text", "data": {"text": "保留"}}],
                                },
                                {
                                    "sender": {"user_id": 999, "nickname": "Ad"},
                                    "message": [{"type": "text", "data": {"text": "广告"}}],
                                },
                            ]},
                        }],
                    }],
                },
            },
        }
        db = mock.MagicMock()
        db.get_chat_record.return_value = package
        captured = {}

        def fake_plugin(http_url, token, plugin_id, route, timeout=5, params=None):
            payload_path = Path(params["payload_path"])
            captured.update(json.loads(payload_path.read_text(encoding="utf-8")))
            payload_path.unlink()
            return {"ok": True, "strategy": "qqnt_native_rebuild"}

        with mock.patch(
            "src.onebot.call_webui_plugin_api", side_effect=fake_plugin
        ):
            result = onebot.send_chat_record(
                "http://127.0.0.1:3000",
                "token",
                7,
                {"self_id": 123, "message_type": "group", "group_id": 789},
                db=db,
                send_mode="archive_rebuild",
                webui_url="http://127.0.0.1:6099",
                webui_token="webui-token",
            )

        self.assertNotIn("native_payload_b64", captured)
        children = captured["messages"][0]["data"]["content"]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["data"]["nickname"], "Bob")
        self.assertFalse(result["native_replay"])
        self.assertFalse(result["native_filtered_replay"])

    def test_send_chat_record_uses_self_native_relay_without_saved_payload(self):
        package = {
            "title": "桌面兼容记录",
            "source": {
                "self_id": 123,
                "message_id": 99,
                "message": [{"type": "forward", "data": {"id": "root"}}],
            },
            "send_filter": {"blocked_node_paths": ["n1"]},
            "forward_records": {
                "root": {
                    "omm_native_payload_b64": "dW51c2Vk",
                    "messages": [
                        {
                            "sender": {"user_id": 456, "nickname": "Alice"},
                            "message": [{"type": "text", "data": {"text": "你好"}}],
                        },
                        {
                            "sender": {"user_id": 789, "nickname": "Ad"},
                            "message": [{"type": "text", "data": {"text": "广告"}}],
                        },
                    ],
                }
            },
        }
        db = mock.MagicMock()
        db.get_chat_record.return_value = package
        captured = {}

        def fake_plugin(http_url, token, plugin_id, route, timeout=5, params=None):
            payload_path = Path(params["payload_path"])
            captured.update(json.loads(payload_path.read_text(encoding="utf-8")))
            payload_path.unlink()
            return {
                "ok": True,
                "message_id": "self-card-id",
                "strategy": "qqnt_self_native_relay",
            }

        with mock.patch(
            "src.onebot.call_webui_plugin_api", side_effect=fake_plugin
        ) as plugin:
            result = onebot.send_chat_record(
                "http://127.0.0.1:3000",
                "token",
                7,
                {
                    "self_id": 123,
                    "message_type": "group",
                    "group_id": 789,
                },
                db=db,
                send_mode="self_native_relay",
                webui_url="http://127.0.0.1:6099",
                webui_token="webui-token",
            )

        self.assertEqual(plugin.call_args.args[3], "send-self-native-forward")
        self.assertNotIn("self_native_relay", captured)
        self.assertNotIn("native_payload_b64", captured)
        self.assertEqual(captured["destination_type"], "group")
        self.assertEqual(captured["destination_peer_id"], "789")
        self.assertEqual(len(captured["messages"]), 1)
        self.assertEqual(captured["messages"][0]["data"]["nickname"], "Alice")
        self.assertEqual(result["source"], "self_native_relay")
        self.assertTrue(result["native_rebuild"])
        self.assertTrue(result["self_native_relay"])
        self.assertFalse(result["native_replay"])

    def test_self_native_relay_requires_webui_credentials(self):
        package = {
            "source": {
                "self_id": 123,
                "message_id": 99,
                "message": [{"type": "forward", "data": {"id": "root"}}],
            },
            "forward_records": {
                "root": {
                    "messages": [
                        {
                            "sender": {"user_id": 456, "nickname": "Alice"},
                            "message": [{"type": "text", "data": {"text": "你好"}}],
                        }
                    ]
                }
            },
        }
        db = mock.MagicMock()
        db.get_chat_record.return_value = package

        with self.assertRaisesRegex(onebot.OneBotError, "WebUI"):
            onebot.send_chat_record(
                "http://127.0.0.1:3000",
                "token",
                7,
                {
                    "self_id": 123,
                    "message_type": "group",
                    "group_id": 789,
                },
                db=db,
                send_mode="self_native_relay",
            )

    def test_forward_nodes_preserves_nested_forward_tree(self):
        package = {
            "source": {"message": [{"type": "forward", "data": {"id": "root"}}]},
            "forward_records": {
                "root": {
                    "messages": [
                        {
                            "sender": {"user_id": 456, "nickname": "Alice"},
                            "message": [
                                {"type": "text", "data": {"text": "外层说明"}},
                                {
                                    "type": "forward",
                                    "data": {
                                        "id": "child",
                                        "prompt": "[聊天记录] Bob：内层内容",
                                    },
                                },
                            ],
                        }
                    ]
                },
                "child": {
                    "messages": [
                        {
                            "sender": {"user_id": 789, "nickname": "Bob"},
                            "message": [{"type": "text", "data": {"text": "内层内容"}}],
                        }
                    ]
                },
            },
        }

        nodes, degraded = onebot._forward_nodes(package, 123)

        self.assertEqual(degraded, 0)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]["data"]["content"][0]["data"]["text"], "外层说明")
        nested_wrapper = nodes[1]
        self.assertEqual(nested_wrapper["data"]["nickname"], "Alice")
        self.assertEqual(nested_wrapper["data"]["prompt"], "[聊天记录] Bob：内层内容")
        self.assertEqual(nested_wrapper["data"]["content"][0]["type"], "node")
        self.assertEqual(
            nested_wrapper["data"]["content"][0]["data"]["nickname"], "Bob"
        )
        self.assertEqual(
            nested_wrapper["data"]["content"][0]["data"]["content"][0]["data"]["text"],
            "内层内容",
        )

    def test_forward_nodes_omit_selected_root_and_nested_messages(self):
        package = {
            "source": {
                "message": [{
                    "type": "forward",
                    "data": {
                        "content": [
                            {
                                "sender": {"nickname": "Alice"},
                                "message": [{"type": "text", "data": {"text": "保留"}}],
                            },
                            {
                                "sender": {"nickname": "Ad"},
                                "message": [{"type": "text", "data": {"text": "广告"}}],
                            },
                            {
                                "sender": {"nickname": "Forwarder"},
                                "message": [{
                                    "type": "forward",
                                    "data": {
                                        "content": [
                                            {
                                                "sender": {"nickname": "Bob"},
                                                "message": [{"type": "text", "data": {"text": "内层保留"}}],
                                            },
                                            {
                                                "sender": {"nickname": "Nested Ad"},
                                                "message": [{"type": "text", "data": {"text": "内层广告"}}],
                                            },
                                        ]
                                    },
                                }],
                            },
                        ]
                    },
                }],
            },
            "forward_records": {},
            "send_filter": {
                "blocked_node_paths": ["n1", "n2/s0/n1"],
            },
        }

        view = onebot.chat_record_view(package)
        self.assertEqual(
            [item["path"] for item in view],
            ["n0", "n1", "n2", "n2/s0/n0", "n2/s0/n1"],
        )
        nodes, degraded = onebot._forward_nodes(package, 123)

        texts = []

        def collect(value):
            if isinstance(value, dict):
                if value.get("type") == "text":
                    texts.append(value.get("data", {}).get("text"))
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(nodes)
        self.assertEqual(degraded, 0)
        self.assertIn("保留", texts)
        self.assertIn("内层保留", texts)
        self.assertNotIn("广告", texts)
        self.assertNotIn("内层广告", texts)

    def test_blocking_parent_removes_its_nested_subtree(self):
        package = {
            "source": {
                "message": [{
                    "type": "forward",
                    "data": {"content": [{
                        "sender": {"nickname": "Alice"},
                        "message": [{
                            "type": "forward",
                            "data": {"content": [{
                                "sender": {"nickname": "Bob"},
                                "message": [{"type": "text", "data": {"text": "子内容"}}],
                            }]},
                        }],
                    }]},
                }],
            },
            "send_filter": {"blocked_node_paths": ["n0"]},
        }

        with self.assertRaisesRegex(onebot.OneBotError, "没有可重新发送"):
            onebot._forward_nodes(package, 123)

    def test_forward_nodes_flattens_only_beyond_napcat_nested_limit(self):
        def nested_node(name, depth):
            if depth == 0:
                message = [{"type": "text", "data": {"text": "最深内容"}}]
            else:
                message = [
                    {
                        "type": "forward",
                        "data": {"content": [nested_node(f"L{depth - 1}", depth - 1)]},
                    }
                ]
            return {"sender": {"nickname": name}, "message": message}

        package = {
            "source": {
                "message": [
                    {
                        "type": "forward",
                        "data": {"content": [nested_node("L3", 3)]},
                    }
                ]
            },
            "forward_records": {},
        }

        nodes, _ = onebot._forward_nodes(package, 123)

        texts = []

        def collect(value):
            if isinstance(value, dict):
                if value.get("type") == "text":
                    texts.append(value.get("data", {}).get("text"))
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(nodes)
        self.assertIn("最深内容", texts)
        self.assertIn("[更深层嵌套记录受 NapCat 层级限制，已在本层展开]", texts)

    def test_send_chat_record_forwards_existing_outer_message_natively(self):
        package = {
            "title": "原生记录",
            "source": {"self_id": 123, "message_id": 99},
            "send_filter": {"blocked_node_paths": ["n0"]},
            "summary": {"node_count": 2},
        }
        db = mock.MagicMock()
        db.get_chat_record.return_value = package
        event = {
            "self_id": 123,
            "message_type": "private",
            "target_id": 456,
        }
        with mock.patch(
            "src.onebot.call_action", return_value={"message_id": 100}
        ) as action:
            result = onebot.send_chat_record(
                "http://127.0.0.1:3000", "token", 7, event, db=db
            )

        self.assertEqual(result["source"], "original_native")
        action.assert_called_once_with(
            "http://127.0.0.1:3000",
            "token",
            "forward_friend_single_msg",
            {"user_id": 456, "message_id": 99},
            30,
        )

    def test_send_chat_record_reindexes_native_message_after_napcat_restart(self):
        package = {
            "title": "可持久重定位的记录",
            "source": {
                "self_id": 123,
                "message_id": -987,
                "message_type": "group",
                "group_id": 555,
                "real_seq": 7788,
                "time": 1710000000,
                "user_id": 111222,
            },
            "summary": {"node_count": 2},
        }
        db = mock.MagicMock()
        db.get_chat_record.return_value = package
        event = {"self_id": 123, "message_type": "group", "group_id": 999}

        with mock.patch(
            "src.onebot.call_action",
            side_effect=onebot.OneBotError("无法找到消息-987"),
        ):
            with mock.patch(
                "src.onebot.call_webui_plugin_api",
                return_value={"ok": True, "recovered": True},
            ) as plugin:
                result = onebot.send_chat_record(
                    "http://127.0.0.1:3000",
                    "onebot-token",
                    7,
                    event,
                    db=db,
                    webui_url="http://127.0.0.1:6099",
                    webui_token="webui-token",
                )

        self.assertTrue(result["reindexed"])
        self.assertEqual(result["source"], "original_native")
        self.assertEqual(
            plugin.call_args.kwargs["params"],
            {
                "source_type": "group",
                "source_peer_id": 555,
                "source_self_id": "123",
                "source_real_seq": 7788,
                "source_time": 1710000000,
                "source_sender_id": 111222,
                "destination_type": "group",
                "destination_peer_id": 999,
            },
        )

    def test_cross_account_native_forward_skips_short_id_and_flips_private_peer(self):
        package = {
            "title": "B 账号导入的私聊记录",
            "source": {
                "self_id": 20000,
                "message_id": -987,
                "message_type": "private",
                "target_id": 10000,
                "real_seq": 7788,
                "time": 1710000000,
                "sender": {"user_id": 20000},
            },
            "summary": {"node_count": 2},
        }
        db = mock.MagicMock()
        db.get_chat_record.return_value = package
        event = {"self_id": 10000, "message_type": "group", "group_id": 999}

        with mock.patch("src.onebot.call_action") as action:
            with mock.patch(
                "src.onebot.call_webui_plugin_api",
                return_value={"ok": True, "recovered": True},
            ) as plugin:
                result = onebot.send_chat_record(
                    "http://127.0.0.1:3000",
                    "onebot-token",
                    7,
                    event,
                    db=db,
                    webui_url="http://127.0.0.1:6099",
                    webui_token="webui-token",
                )

        action.assert_not_called()
        self.assertTrue(result["reindexed"])
        self.assertEqual(
            plugin.call_args.kwargs["params"],
            {
                "source_type": "private",
                "source_peer_id": 10000,
                "source_self_id": "20000",
                "source_real_seq": 7788,
                "source_time": 1710000000,
                "source_sender_id": 20000,
                "destination_type": "group",
                "destination_peer_id": 999,
            },
        )

    def test_send_chat_record_can_force_original_rebuild(self):
        archived = {
            "title": "旧归档",
            "omm_native_payload_b64": "cHJlc2VydmVkLW5hdGl2ZQ==",
            "native_card": {"app": "com.tencent.multimsg"},
            "source": {"self_id": 123, "message_id": 99},
            "send_filter": {"blocked_node_paths": ["n1"]},
        }
        refreshed = {
            "title": "刷新后的记录",
            "source": {
                "self_id": 123,
                "message_id": 99,
                "message": [{"type": "forward", "data": {"id": "root"}}],
            },
            "forward_records": {
                "root": {
                    "messages": [
                        {
                            "sender": {"user_id": 456, "nickname": "Alice"},
                            "message": [{"type": "text", "data": {"text": "刷新内容"}}],
                        },
                        {
                            "sender": {"user_id": 789, "nickname": "Ad"},
                            "message": [{"type": "text", "data": {"text": "刷新广告"}}],
                        },
                    ]
                }
            },
        }
        db = mock.MagicMock()
        db.get_chat_record.return_value = archived
        event = {"self_id": 123, "message_type": "group", "group_id": 789}
        with mock.patch(
            "src.onebot.build_chat_record_package", return_value=refreshed
        ) as rebuild:
            with mock.patch(
                "src.onebot.call_action", return_value={"message_id": 100}
            ) as action:
                result = onebot.send_chat_record(
                    "http://127.0.0.1:3000",
                    "token",
                    7,
                    event,
                    db=db,
                    send_mode="original_rebuild",
                )

        rebuild.assert_called_once()
        self.assertEqual(
            refreshed["send_filter"]["blocked_node_paths"], ["n1"]
        )
        self.assertEqual(
            refreshed["omm_native_payload_b64"], "cHJlc2VydmVkLW5hdGl2ZQ=="
        )
        self.assertEqual(refreshed["native_card"]["app"], "com.tencent.multimsg")
        db.save_chat_record.assert_called_once_with(refreshed, record_id=7)
        self.assertEqual(result["source"], "original_rebuild")
        self.assertEqual(action.call_args.args[2], "send_group_forward_msg")
        sent_nodes = action.call_args.args[3]["messages"]
        self.assertEqual(len(sent_nodes), 1)
        self.assertEqual(sent_nodes[0]["data"]["nickname"], "Alice")

    def test_original_rebuild_recovers_source_by_persistent_identity_after_restart(self):
        archived = {
            "title": "旧归档",
            "source": {
                "self_id": 123,
                "message_id": -99,
                "message_type": "group",
                "group_id": 456,
                "real_seq": 7788,
                "time": 1710000000,
                "user_id": 111,
            },
        }
        recovered_source = {
            "message_id": "native-outer",
            "message": [{"type": "forward", "data": {"id": "root"}}],
        }
        refreshed = {
            "title": "刷新后",
            "source": recovered_source,
            "forward_records": {
                "root": {
                    "messages": [{
                        "sender": {"nickname": "Alice"},
                        "message": [{"type": "text", "data": {"text": "内容"}}],
                    }]
                }
            },
        }
        db = mock.MagicMock()
        db.get_chat_record.return_value = archived
        build_calls = []

        def fake_build(*args, **kwargs):
            build_calls.append((args, kwargs))
            if kwargs.get("source_message") is None:
                raise onebot.OneBotError("OneBot 调用失败：消息不存在")
            self.assertEqual(kwargs["source_message"]["message_id"], "native-outer")
            self.assertEqual(kwargs["source_message"]["real_seq"], 7788)
            return refreshed

        with (
            mock.patch("src.onebot.build_chat_record_package", side_effect=fake_build),
            mock.patch(
                "src.onebot.call_webui_plugin_api",
                return_value={
                    "ok": True,
                    "message": recovered_source,
                    "native_payload_b64": "bmF0aXZl",
                },
            ) as plugin,
            mock.patch(
                "src.onebot._send_qqnt_native_rebuilt_forward",
                return_value={"ok": True, "strategy": "qqnt_native_rebuild"},
            ),
        ):
            result = onebot.send_chat_record(
                "http://127.0.0.1:3000",
                "token",
                7,
                {"self_id": 123, "message_type": "group", "group_id": 789},
                db=db,
                send_mode="original_rebuild",
                webui_url="http://127.0.0.1:6099",
                webui_token="webui-token",
            )

        self.assertEqual(result["source"], "original_rebuild")
        self.assertEqual(len(build_calls), 1)
        self.assertEqual(plugin.call_args.args[3], "recover-message")
        self.assertEqual(plugin.call_args.kwargs["params"]["source_real_seq"], 7788)
        self.assertEqual(plugin.call_args.kwargs["params"]["source_self_id"], "123")
        db.save_chat_record.assert_called_once_with(refreshed, record_id=7)

    def test_native_send_records_destination_and_reuses_it_after_source_is_gone(self):
        with tempfile.TemporaryDirectory() as folder:
            db = MemeDB(Path(folder) / "memes.db")
            try:
                original = {
                    "self_id": "123", "message_id": -99, "message_type": "group",
                    "group_id": "456", "real_seq": "100", "time": "1710000000",
                    "sender": {"user_id": "321"},
                }
                saved = db.save_chat_record({
                    "title": "记录", "source": original, "summary": {"node_count": 2},
                })
                replica = {
                    "self_id": "123", "message_id": "9876543210",
                    "message_type": "group", "group_id": "789", "real_seq": "200",
                    "time": "1710000001", "sender": {"user_id": "123"},
                }
                event = {"self_id": "123", "message_type": "group", "group_id": "789"}
                with mock.patch("src.onebot.call_webui_plugin_api", return_value={
                    "ok": True, "source_location": replica,
                }) as plugin:
                    result = onebot.send_chat_record(
                        "http://127.0.0.1:3000", "", saved["id"], event, db=db,
                        webui_url="http://127.0.0.1:6099", webui_token="token",
                    )
                self.assertTrue(result["backup_recorded"])
                self.assertEqual(len(onebot.chat_record_sources(db.get_chat_record(saved["id"]))), 2)
                with mock.patch("src.onebot.call_webui_plugin_api", return_value={"ok": True}) as plugin:
                    onebot.send_chat_record(
                        "http://127.0.0.1:3000", "", saved["id"], event, db=db,
                        webui_url="http://127.0.0.1:6099", webui_token="token",
                    )
                self.assertEqual(plugin.call_args.kwargs["params"]["source_real_seq"], "200")
                self.assertEqual(db.count_chat_records(), 1)
            finally:
                db.close()

    def test_native_locations_are_equal_and_lookup_failure_tries_next(self):
        original = {"self_id": 123, "message_id": -99, "message_type": "group",
                    "group_id": 456, "real_seq": 100}
        backup = {"self_id": 123, "message_id": 999, "message_type": "group",
                  "group_id": 789, "real_seq": 200, "last_success_at": 1}
        db = mock.MagicMock()
        db.get_chat_record.return_value = {"source": original, "source_locations": [backup]}
        with mock.patch("src.onebot.call_webui_plugin_api", side_effect=[
            {"ok": False, "retry_safe": True, "error": "已清除"}, {"ok": True},
        ]) as plugin:
            onebot.send_chat_record(
                "http://127.0.0.1:3000", "", 7,
                {"self_id": 123, "message_type": "group", "group_id": 900}, db=db,
                webui_url="http://127.0.0.1:6099", webui_token="token",
            )
        self.assertEqual([call.kwargs["params"]["source_real_seq"]
                          for call in plugin.call_args_list], [200, 100])

    def test_native_send_does_not_retry_after_dispatch_or_transport_timeout(self):
        db = mock.MagicMock()
        source = {"self_id": 123, "message_id": 99, "message_type": "group",
                  "group_id": 456, "real_seq": 100}
        db.get_chat_record.return_value = {"source": source, "source_locations": [
            {**source, "group_id": 789, "real_seq": 200},
        ]}
        for response in (
            {"ok": False, "retry_safe": False, "error": "发送状态不确定"},
            onebot.OneBotError("连接超时"),
        ):
            with mock.patch("src.onebot.call_webui_plugin_api") as plugin:
                if isinstance(response, Exception):
                    plugin.side_effect = response
                else:
                    plugin.return_value = response
                with self.assertRaises(onebot.OneBotError):
                    onebot.send_chat_record(
                        "http://127.0.0.1:3000", "", 7,
                        {"self_id": 123, "message_type": "group", "group_id": 900}, db=db,
                        webui_url="http://127.0.0.1:6099", webui_token="token",
                    )
                self.assertEqual(plugin.call_count, 1)

    def test_filtered_replay_does_not_become_an_equivalent_original(self):
        db = mock.MagicMock()
        db.get_chat_record.return_value = {
            "source": {"self_id": 123, "message_id": 99},
            "send_filter": {"blocked_node_paths": ["n1"]},
        }
        location = {"self_id": 123, "message_id": 999, "message_type": "group",
                    "group_id": 789, "real_seq": 200}
        with (
            mock.patch("src.onebot._forward_nodes", return_value=([{}], 0)),
            mock.patch("src.onebot._send_qqnt_native_rebuilt_forward", return_value={
                "ok": True, "strategy": "qqnt_native_filtered_replay", "source_location": location,
            }),
        ):
            result = onebot.send_chat_record(
                "http://127.0.0.1:3000", "", 7,
                {"self_id": 123, "message_type": "group", "group_id": 789}, db=db,
                send_mode="archive_rebuild", webui_url="http://127.0.0.1:6099",
                webui_token="token",
            )
        self.assertFalse(result["backup_recorded"])
        db.add_chat_record_source.assert_not_called()

    def test_backup_database_failure_does_not_change_successful_send(self):
        db = mock.MagicMock()
        source = {"self_id": 123, "message_id": 99, "message_type": "group",
                  "group_id": 456, "real_seq": 100}
        db.get_chat_record.return_value = {"source": source}
        db.add_chat_record_source.side_effect = sqlite3.OperationalError("disk full")
        with mock.patch("src.onebot.call_webui_plugin_api", return_value={
            "ok": True, "source_location": {**source, "real_seq": 200},
        }) as plugin:
            result = onebot.send_chat_record(
                "http://127.0.0.1:3000", "", 7,
                {"self_id": 123, "message_type": "group", "group_id": 789}, db=db,
                webui_url="http://127.0.0.1:6099", webui_token="token",
            )
        self.assertTrue(result["ok"])
        self.assertFalse(result["backup_recorded"])
        self.assertEqual(plugin.call_count, 1)

    def test_original_rebuild_tries_backup_and_updates_existing_record(self):
        source = {"self_id": 123, "message_id": 99, "message_type": "group",
                  "group_id": 456, "real_seq": 100}
        backup = {**source, "message_id": 999, "group_id": 789, "real_seq": 200}
        db = mock.MagicMock()
        db.get_chat_record.return_value = {"source": source, "source_locations": [backup],
                                          "summary": {"node_count": 2}}
        refreshed = {"title": "刷新", "source": {**backup, "message_id": 1000},
                     "summary": {"node_count": 2}}
        with (
            mock.patch("src.onebot._recover_original_message_by_identity", side_effect=[
                onebot.OneBotError("副本已清除"), source,
            ]) as recover,
            mock.patch("src.onebot.build_chat_record_package", return_value=refreshed),
            mock.patch("src.onebot._forward_nodes", return_value=([{}], 0)),
            mock.patch("src.onebot._send_qqnt_native_rebuilt_forward", return_value={"ok": True}),
        ):
            onebot.send_chat_record(
                "http://127.0.0.1:3000", "", 7,
                {"self_id": 123, "message_type": "group", "group_id": 789}, db=db,
                send_mode="original_rebuild", webui_url="http://127.0.0.1:6099",
                webui_token="token",
            )
        self.assertEqual([call.args[2]["real_seq"] for call in recover.call_args_list], [200, 100])
        db.save_chat_record.assert_called_once_with(refreshed, record_id=7)
        self.assertEqual({item["real_seq"] for item in refreshed["source_locations"]}, {100, 200})

    def test_send_chat_record_rejects_unknown_mode(self):
        db = mock.MagicMock()
        db.get_chat_record.return_value = {
            "title": "记录",
            "source": {"self_id": 123, "message_id": 99},
        }
        with self.assertRaisesRegex(onebot.OneBotError, "发送方式无效"):
            onebot.send_chat_record(
                "http://127.0.0.1:3000",
                "token",
                7,
                {"self_id": 123, "message_type": "private", "target_id": 456},
                db=db,
                send_mode="automatic",
            )

    def test_send_chat_record_uses_media_url_and_degrades_failed_download(self):
        package = {
            "title": "含图片记录",
            "source": {
                "self_id": 123,
                "message_id": 99,
                "message": [{"type": "forward", "data": {"id": "root"}}],
            },
            "forward_records": {
                "root": {
                    "messages": [
                        {
                            "sender": {"user_id": 456, "nickname": "Alice"},
                            "message": [
                                {
                                    "type": "image",
                                    "data": {
                                        "file": "receiver-side-image-id.jpg",
                                        "url": "https://example.test/fresh.jpg",
                                    },
                                }
                            ],
                        }
                    ]
                }
            },
        }
        db = mock.MagicMock()
        db.get_chat_record.return_value = package
        event = {
            "self_id": 123,
            "message_type": "group",
            "group_id": 789,
        }
        attempts = []

        def fake_action(http_url, token, action, params=None, timeout=5):
            if action == "forward_group_single_msg":
                raise onebot.OneBotError("原消息已失效")
            if action == "download_file":
                attempts.append((action, params))
                raise onebot.OneBotError("下载文件失败: Bad Request")
            if action in ("download_file_image_stream", "get_image"):
                raise onebot.OneBotError("图片标识已失效")
            attempts.append((action, params))
            return {"message_id": 100}

        with mock.patch(
            "src.onebot.build_chat_record_package",
            side_effect=onebot.OneBotError("原消息失效"),
        ):
            with mock.patch("src.onebot.call_action", side_effect=fake_action):
                result = onebot.send_chat_record(
                    "http://127.0.0.1:3000",
                    "token",
                    7,
                    event,
                    db=db,
                    send_mode="archive_rebuild",
                )

        self.assertEqual(
            [item[0] for item in attempts], ["download_file", "send_group_forward_msg"]
        )
        self.assertEqual(
            attempts[1][1]["messages"][0]["data"]["content"][0]["data"]["text"],
            "[图片，资源已失效]",
        )
        self.assertEqual(result["degraded_media_count"], 1)
        self.assertEqual(result["message_id"], 100)

    def test_send_chat_record_resolves_received_image_id_to_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "forward-image.jpg"
            image_path.write_bytes(b"image")
            package = {
                "title": "含可还原图片的记录",
                "source": {
                    "self_id": 123,
                    "message_id": 99,
                    "message": [{"type": "forward", "data": {"id": "root"}}],
                },
                "forward_records": {
                    "root": {
                        "messages": [
                            {
                                "sender": {"user_id": 456, "nickname": "Alice"},
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {
                                            "file": "receiver-side-image-id.jpg",
                                            "url": "https://expired.test/image.jpg",
                                        },
                                    }
                                ],
                            }
                        ]
                    }
                },
            }
            db = mock.MagicMock()
            db.get_chat_record.return_value = package
            event = {
                "self_id": 123,
                "message_type": "group",
                "group_id": 789,
            }
            sent_params = None

            def fake_action(http_url, token, action, params=None, timeout=5):
                nonlocal sent_params
                if action == "forward_group_single_msg":
                    raise onebot.OneBotError("原消息已失效")
                if action == "download_file":
                    raise onebot.OneBotError("图片 URL 已失效")
                if action == "download_file_image_stream":
                    return {"file": str(image_path)}
                if action == "send_group_forward_msg":
                    sent_params = params
                    return {"message_id": 100}
                raise AssertionError(action)

            with mock.patch(
                "src.onebot.build_chat_record_package",
                side_effect=onebot.OneBotError("原消息内容已失效"),
            ):
                with mock.patch("src.onebot.call_action", side_effect=fake_action):
                    result = onebot.send_chat_record(
                        "http://127.0.0.1:3000",
                        "token",
                        7,
                        event,
                        db=db,
                        send_mode="archive_rebuild",
                    )

        image = sent_params["messages"][0]["data"]["content"][0]
        self.assertEqual(image, {"type": "image", "data": {"file": str(image_path)}})
        self.assertEqual(result["degraded_media_count"], 0)

    def test_send_chat_record_degrades_only_the_unresolvable_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "available.jpg"
            image_path.write_bytes(b"image")
            package = {
                "title": "部分媒体仍有效",
                "source": {
                    "self_id": 123,
                    "message_id": 99,
                    "message": [{"type": "forward", "data": {"id": "root"}}],
                },
                "forward_records": {
                    "root": {
                        "messages": [
                            {
                                "sender": {"user_id": 456, "nickname": "Alice"},
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {
                                            "file": "expired-image",
                                            "url": "https://expired.test/image.jpg",
                                        },
                                    },
                                    {
                                        "type": "image",
                                        "data": {"path": str(image_path)},
                                    },
                                ],
                            }
                        ]
                    }
                },
            }
            db = mock.MagicMock()
            db.get_chat_record.return_value = package
            sent_params = None

            def fake_action(http_url, token, action, params=None, timeout=5):
                nonlocal sent_params
                if action == "download_file":
                    raise onebot.OneBotError("图片 URL 已失效")
                if action in ("download_file_image_stream", "get_image"):
                    raise onebot.OneBotError("图片标识已失效")
                if action == "send_group_forward_msg":
                    sent_params = params
                    return {"message_id": 100}
                raise AssertionError(action)

            with mock.patch("src.onebot.call_action", side_effect=fake_action):
                result = onebot.send_chat_record(
                    "http://127.0.0.1:3000",
                    "token",
                    7,
                    {"self_id": 123, "message_type": "group", "group_id": 789},
                    db=db,
                    send_mode="archive_rebuild",
                )

        content = sent_params["messages"][0]["data"]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[0]["data"]["text"], "[图片，资源已失效]")
        self.assertEqual(
            content[1], {"type": "image", "data": {"file": str(image_path)}}
        )
        self.assertEqual(result["degraded_media_count"], 1)

    def test_archive_images_are_persisted_and_reused_by_refreshed_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloaded = root / "napcat-temp.jpg"
            downloaded.write_bytes(b"\xff\xd8\xffimage")
            archived = {
                "source": {"self_id": 123, "message_id": 99},
                "forward_records": {
                    "root": {
                        "messages": [
                            {
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {
                                            "file": "receiver-image-id",
                                            "url": "https://example.test/image",
                                        },
                                    }
                                ]
                            }
                        ]
                    }
                },
                "summary": {"media_count": 1, "media_localized": False},
            }
            with mock.patch(
                "src.onebot._download_archive_image",
                return_value=str(downloaded),
            ):
                count = onebot._localize_archive_images(
                    "http://127.0.0.1:3000",
                    "token",
                    archived,
                    root / "media",
                )

            local_path = Path(
                archived["forward_records"]["root"]["messages"][0]["message"][0][
                    "data"
                ]["local_path"]
            )
            self.assertEqual(count, 1)
            self.assertTrue(local_path.is_file())
            self.assertEqual(local_path.read_bytes(), downloaded.read_bytes())
            self.assertTrue(archived["summary"]["media_localized"])
            self.assertEqual(archived["summary"]["media_localized_count"], 1)

            refreshed = {
                "forward_records": {
                    "root": {
                        "messages": [
                            {
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {"file": "new-receiver-image-id"},
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
            reused = onebot._reuse_archived_image_paths(archived, refreshed)

            new_data = refreshed["forward_records"]["root"]["messages"][0]["message"][
                0
            ]["data"]
            self.assertEqual(reused, 1)
            self.assertEqual(new_data["local_path"], str(local_path))

    def test_new_quoted_media_does_not_shift_or_overwrite_existing_media(self):
        from copy import deepcopy

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.jpg"
            second = root / "second.jpg"
            quoted = root / "quoted.jpg"
            first.write_bytes(b"\xff\xd8\xfffirst")
            second.write_bytes(b"\xff\xd8\xffsecond")
            quoted.write_bytes(b"\xff\xd8\xffquoted")
            package = {
                "source": {
                    "self_id": 123,
                    "message_id": 99,
                    "message": [{"type": "forward", "data": {"id": "root"}}],
                },
                "forward_records": {
                    "root": {
                        "messages": [
                            {
                                "message": [
                                    {"type": "reply", "data": {"id": "quote"}},
                                    {"type": "image", "data": {"path": str(first)}},
                                    {"type": "image", "data": {"path": str(second)}},
                                ]
                            }
                        ]
                    }
                },
            }
            media_root = root / "media"
            self.assertEqual(
                onebot._localize_archive_media("", "", package, media_root), 2
            )
            original_paths = [
                Path(data["local_path"])
                for _, data in onebot._archive_media_entries(package)
            ]
            refreshed = deepcopy(package)
            segments = refreshed["forward_records"]["root"]["messages"][0]["message"]
            for segment in segments[1:]:
                segment["data"].pop("local_path")
                segment["data"].pop("path")
            segments[0]["data"]["omm_quoted_message"] = {
                "message": [{"type": "image", "data": {"path": str(quoted)}}]
            }
            self.assertEqual(onebot._reuse_archived_media_paths(package, refreshed), 2)
            self.assertNotIn(
                "local_path",
                segments[0]["data"]["omm_quoted_message"]["message"][0]["data"],
            )
            self.assertEqual(
                onebot._localize_archive_media("", "", refreshed, media_root), 3
            )
            entries = onebot._archive_media_entries(refreshed)
            self.assertEqual(
                [Path(data["local_path"]).read_bytes() for _, data in entries],
                [quoted.read_bytes(), first.read_bytes(), second.read_bytes()],
            )
            self.assertEqual(
                [path.read_bytes() for path in original_paths],
                [first.read_bytes(), second.read_bytes()],
            )

    def test_archive_video_and_voice_are_persisted_with_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "downloaded.jpg"
            video = root / "downloaded.mp4"
            voice = root / "downloaded.silk"
            image.write_bytes(b"\xff\xd8\xffimage")
            video.write_bytes(b"video")
            voice.write_bytes(b"voice")
            package = {
                "source": {
                    "self_id": 123,
                    "message_id": 99,
                    "message": [{"type": "forward", "data": {"id": "root"}}],
                },
                "forward_records": {
                    "root": {
                        "messages": [
                            {
                                "message": [
                                    {"type": "image", "data": {"file": "pic"}},
                                    {"type": "video", "data": {"file": "clip.mp4"}},
                                    {"type": "record", "data": {"file": "voice.silk"}},
                                ]
                            }
                        ]
                    }
                },
                "summary": {"media_count": 3},
            }
            sources = {
                "image": str(image),
                "video": str(video),
                "record": str(voice),
            }
            with mock.patch(
                "src.onebot._download_archive_media",
                side_effect=lambda _url, _token, kind, _data: sources[kind],
            ):
                count = onebot._localize_archive_media(
                    "http://127.0.0.1:3000",
                    "token",
                    package,
                    root / "media",
                )

            entries = onebot._archive_media_entries(package)
            self.assertEqual(count, 3)
            self.assertEqual(
                [kind for kind, _data in entries], ["image", "video", "record"]
            )
            self.assertEqual(
                [Path(data["local_path"]).suffix for _kind, data in entries],
                [".jpg", ".mp4", ".silk"],
            )
            self.assertTrue(
                all(Path(data["local_path"]).is_file() for _kind, data in entries)
            )
            self.assertEqual(package["summary"]["media_localized_count"], 3)
            self.assertTrue(package["summary"]["media_localized"])

    def test_archive_images_use_cache_relative_per_record_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "memes"
            media_root = cache / "chat-record-media"
            downloaded = root / "napcat-temp.png"
            downloaded.write_bytes(b"\x89PNG\r\n\x1a\nimage")
            package = {
                "source": {
                    "self_id": 123,
                    "message_id": 99,
                    "message": [{"type": "forward", "data": {"id": "root"}}],
                },
                "forward_records": {
                    "root": {
                        "messages": [
                            {
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {"file": "receiver-image-id"},
                                    }
                                ]
                            }
                        ]
                    }
                },
                "summary": {"media_count": 1},
            }
            with mock.patch(
                "src.onebot._download_archive_image", return_value=str(downloaded)
            ):
                count = onebot._localize_archive_images(
                    "http://127.0.0.1:3000",
                    "token",
                    package,
                    media_root,
                    storage_root=cache,
                )

            data = package["forward_records"]["root"]["messages"][0]["message"][0][
                "data"
            ]
            parts = Path(data["local_path"]).parts
            self.assertEqual(count, 1)
            self.assertEqual(parts[0], "chat-record-media")
            self.assertEqual(len(parts), 3)
            self.assertTrue((cache / data["local_path"]).is_file())
            self.assertTrue(
                os.path.samefile(
                    onebot._archive_media_source(data, storage_root=cache),
                    cache / data["local_path"],
                )
            )

    def test_prune_archive_media_keeps_declared_file_when_lookup_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            package = {
                "source": {"self_id": 123, "message_id": 99},
                "forward_records": {
                    "root": {
                        "messages": [
                            {
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {
                                            "local_path": "chat-record-media/declared/00001.jpg"
                                        },
                                    }
                                ]
                            }
                        ]
                    }
                },
            }
            archive_dir = onebot._archive_media_dir(
                package, cache / "chat-record-media"
            )
            # 使用真实记录哈希目录，模拟虚拟盘刚写完时 is_file/resolve 暂时失败。
            data = package["forward_records"]["root"]["messages"][0]["message"][0][
                "data"
            ]
            data["local_path"] = (
                Path("chat-record-media") / archive_dir.name / "00001.jpg"
            ).as_posix()
            archive_dir.mkdir(parents=True)
            image = archive_dir / "00001.jpg"
            image.write_bytes(b"image")
            stale = archive_dir / "stale.jpg"
            stale.write_bytes(b"stale")

            with mock.patch("src.onebot._archive_media_source", return_value=""):
                removed = onebot._prune_archive_media(
                    package,
                    cache / "chat-record-media",
                    storage_root=cache,
                )

            self.assertTrue(image.is_file())
            self.assertFalse(stale.exists())
            self.assertEqual(removed, 1)

    def test_legacy_chat_record_media_is_migrated_and_deleted_with_record(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "app-data"
            cache = root / "meme-library"
            legacy = data_dir / "chat-record-media" / "legacy-record"
            legacy.mkdir(parents=True)
            old_image = legacy / "00001.jpg"
            old_image.write_bytes(b"\xff\xd8\xffimage")
            package = {
                "schema_version": 1,
                "title": "旧聊天记录",
                "source": {
                    "self_id": 123,
                    "message_id": 456,
                    "message_type": "group",
                    "group_id": 789,
                    "message": [{"type": "forward", "data": {"id": "root"}}],
                },
                "forward_records": {
                    "root": {
                        "messages": [
                            {
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {"local_path": str(old_image)},
                                    }
                                ]
                            }
                        ]
                    }
                },
                "summary": {"node_count": 1, "record_count": 1},
            }
            db = MemeDB(root / "test.db")
            try:
                saved = db.save_chat_record(package)
                cfg = SimpleNamespace(
                    data_dir=data_dir,
                    cache_dir=cache,
                    chat_record_media_dir=cache / "chat-record-media",
                )
                result = onebot.migrate_legacy_chat_record_media(db=db, cfg=cfg)
                migrated = db.get_chat_record(saved["id"])
                data = migrated["forward_records"]["root"]["messages"][0]["message"][0][
                    "data"
                ]
                migrated_path = cache / data["local_path"]

                self.assertEqual(result, {"records": 1, "files": 1})
                self.assertFalse(Path(data["local_path"]).is_absolute())
                self.assertTrue(migrated_path.is_file())
                self.assertFalse(old_image.exists())
                self.assertTrue(
                    onebot.delete_chat_record_archive(saved["id"], db=db, cfg=cfg)
                )
                self.assertFalse(migrated_path.parent.exists())
                self.assertIsNone(db.get_chat_record(saved["id"]))
            finally:
                db.close()

    def test_archive_images_finds_parse_mult_msg_inline_content(self):
        package = {
            "source": {
                "message": [
                    {
                        "type": "forward",
                        "data": {
                            "content": [
                                {
                                    "message": [
                                        {
                                            "type": "forward",
                                            "data": {
                                                "content": [
                                                    {
                                                        "message": [
                                                            {
                                                                "type": "image",
                                                                "data": {
                                                                    "file": "inline-image",
                                                                    "url": "https://example.test/inline.jpg",
                                                                },
                                                            }
                                                        ]
                                                    }
                                                ]
                                            },
                                        }
                                    ]
                                }
                            ]
                        },
                    }
                ]
            },
            "forward_records": {},
        }

        images = onebot._archive_image_data(package)

        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["file"], "inline-image")

    def test_archive_image_download_prefers_url_before_internal_file_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            downloaded = Path(tmp) / "downloaded.jpg"
            downloaded.write_bytes(b"image")
            actions = []

            def fake_action(http_url, token, action, params=None, timeout=5):
                actions.append(action)
                if action == "download_file":
                    return {"file": str(downloaded)}
                raise AssertionError(action)

            with mock.patch("src.onebot.call_action", side_effect=fake_action):
                result = onebot._download_archive_image(
                    "http://127.0.0.1:3000",
                    "token",
                    {
                        "file": "receiver-side-image-id",
                        "url": "https://example.test/image.jpg",
                    },
                )

        self.assertEqual(result, str(downloaded))
        self.assertEqual(actions, ["download_file"])

    def test_build_chat_record_package_keeps_complete_nested_records(self):
        def fake_action(http_url, token, action, params=None, timeout=5):
            if action == "get_msg":
                return {
                    "self_id": 123,
                    "message_id": 99,
                    "message_seq": 100,
                    "message_type": "group",
                    "group_id": 456,
                    "group_name": "示例群",
                    "message": [
                        {
                            "type": "forward",
                            "data": {"id": "root", "prompt": "三条聊天记录"},
                        }
                    ],
                }
            if params["message_id"] == "root":
                return {
                    "messages": [
                        {
                            "sender": {"nickname": "Alice"},
                            "message": [
                                {"type": "text", "data": {"text": "你好"}},
                                {"type": "forward", "data": {"id": "child"}},
                            ],
                        }
                    ]
                }
            return {
                "messages": [
                    {
                        "sender": {"nickname": "Bob"},
                        "message": [
                            {"type": "image", "data": {"url": "https://x/a.jpg"}}
                        ],
                    }
                ]
            }

        with mock.patch("src.onebot.call_action", side_effect=fake_action):
            package = onebot.build_chat_record_package(
                "http://127.0.0.1:3000", "token", "99"
            )

        self.assertEqual(package["title"], "三条聊天记录")
        self.assertEqual(set(package["forward_records"]), {"root", "child"})
        self.assertEqual(package["summary"]["record_count"], 2)
        self.assertEqual(package["summary"]["node_count"], 2)
        self.assertEqual(package["summary"]["max_depth"], 2)
        self.assertEqual(package["summary"]["media_count"], 1)
        self.assertFalse(package["summary"]["media_localized"])

    def test_build_chat_record_package_keeps_inline_multilevel_records(self):
        def fake_action(http_url, token, action, params=None, timeout=5):
            if action == "get_msg":
                return {
                    "message_id": 99,
                    "message": [{"type": "forward", "data": {"id": "root"}}],
                }
            return {
                "messages": [
                    {
                        "sender": {"nickname": "Alice"},
                        "message": [
                            {"type": "text", "data": {"text": "第一层"}},
                            {
                                "type": "forward",
                                "data": {
                                    "content": [
                                        {
                                            "sender": {"nickname": "Bob"},
                                            "message": [
                                                {
                                                    "type": "text",
                                                    "data": {"text": "第二层"},
                                                },
                                                {
                                                    "type": "forward",
                                                    "data": {
                                                        "content": [
                                                            {
                                                                "sender": {
                                                                    "nickname": "Carol"
                                                                },
                                                                "message": [
                                                                    {
                                                                        "type": "text",
                                                                        "data": {
                                                                            "text": "第三层"
                                                                        },
                                                                    }
                                                                ],
                                                            }
                                                        ]
                                                    },
                                                },
                                            ],
                                        }
                                    ]
                                },
                            },
                        ],
                    }
                ]
            }

        with mock.patch("src.onebot.call_action", side_effect=fake_action):
            package = onebot.build_chat_record_package(
                "http://127.0.0.1:3000", "token", "99"
            )

        self.assertEqual(package["summary"]["record_count"], 3)
        self.assertEqual(package["summary"]["node_count"], 3)
        self.assertEqual(package["summary"]["max_depth"], 3)
        self.assertEqual(
            [item["preview"] for item in onebot.chat_record_view(package)],
            ["第一层[聊天记录]", "第二层[聊天记录]", "第三层"],
        )

    def test_build_chat_record_package_reads_napcat_node_data_message(self):
        def fake_action(http_url, token, action, params=None, timeout=5):
            if action == "get_msg":
                return {
                    "message_id": 99,
                    "message": [{"type": "forward", "data": {"id": "root"}}],
                }
            return {
                "messages": [
                    {
                        "type": "node",
                        "data": {
                            "user_id": "1",
                            "nickname": "Alice",
                            "message": [
                                {"type": "text", "data": {"text": "第一层"}},
                                {
                                    "type": "node",
                                    "data": {
                                        "user_id": "1",
                                        "nickname": "Alice",
                                        "message": [
                                            {
                                                "type": "node",
                                                "data": {
                                                    "user_id": "2",
                                                    "nickname": "Bob",
                                                    "message": [
                                                        {
                                                            "type": "image",
                                                            "data": {
                                                                "file": "nested.jpg"
                                                            },
                                                        }
                                                    ],
                                                },
                                            }
                                        ],
                                    },
                                },
                            ],
                        },
                    }
                ]
            }

        with mock.patch("src.onebot.call_action", side_effect=fake_action):
            package = onebot.build_chat_record_package(
                "http://127.0.0.1:3000", "token", "99"
            )

        self.assertEqual(package["summary"]["record_count"], 2)
        self.assertEqual(package["summary"]["node_count"], 2)
        self.assertEqual(package["summary"]["max_depth"], 2)
        self.assertEqual(package["summary"]["media_count"], 1)
        self.assertEqual(
            [item["preview"] for item in onebot.chat_record_view(package)],
            ["第一层[聊天记录]", "[图片]"],
        )
        self.assertEqual(
            [item["file"] for item in onebot._archive_image_data(package)],
            ["nested.jpg"],
        )

    def test_build_chat_record_package_rejects_unresolved_empty_nodes(self):
        def fake_action(http_url, token, action, params=None, timeout=5):
            if action == "get_msg":
                return {
                    "message_id": 99,
                    "message": [{"type": "forward", "data": {"id": "root"}}],
                }
            return {
                "messages": [
                    {
                        "sender": {"nickname": "Alice"},
                        "message": [],
                        "raw_message": "",
                    }
                ]
            }

        with mock.patch("src.onebot.call_action", side_effect=fake_action):
            with self.assertRaisesRegex(onebot.OneBotError, "parseMultMsg"):
                onebot.build_chat_record_package("http://127.0.0.1:3000", "token", "99")

    def test_build_chat_record_package_recovers_nested_actions_with_plugin(self):
        def fake_action(http_url, token, action, params=None, timeout=5):
            if action == "get_msg":
                return {
                    "message_id": 99,
                    "self_id": 10000,
                    "message_type": "group",
                    "group_id": 20000,
                    "real_seq": 77,
                    "time": 1710000001,
                    "sender": {"user_id": 30000},
                    "message": [{"type": "forward", "data": {"id": "root"}}],
                }
            return {
                "messages": [
                    {
                        "sender": {"nickname": "Alice"},
                        "message": [
                            {
                                "type": "image",
                                "data": {"file": "outer.jpg", "url": "https://old/x"},
                            }
                        ],
                    },
                    {
                        "sender": {"nickname": "Alice"},
                        "message": [],
                        "raw_message": "",
                    },
                ]
            }

        recovered = {
            "ok": True,
            "native_payload_b64": "bmF0aXZlLXByb3Rv",
            "messages": [
                {
                    "sender": {"nickname": "Alice"},
                    "message": [
                        {
                            "type": "image",
                            "data": {
                                "file": "outer.jpg",
                                "url": "https://fallback/x",
                                "omm_fallback_urls": ["https://signed/x"],
                            },
                        }
                    ],
                },
                {
                    "sender": {"nickname": "Alice"},
                    "message": [
                        {
                            "type": "forward",
                            "data": {
                                "id": "omm-action:child",
                                "content": [
                                    {
                                        "sender": {"nickname": "Bob"},
                                        "message": [
                                            {"type": "text", "data": {"text": "内层"}}
                                        ],
                                    }
                                ],
                            },
                        }
                    ],
                },
            ],
        }

        with (
            mock.patch("src.onebot.call_action", side_effect=fake_action),
            mock.patch(
                "src.onebot.call_webui_plugin_api", return_value=recovered
            ) as plugin,
        ):
            package = onebot.build_chat_record_package(
                "http://127.0.0.1:3000",
                "token",
                "99",
                webui_url="http://127.0.0.1:6099",
                webui_token="webui-token",
            )

        plugin.assert_called_once()
        self.assertEqual(plugin.call_args.args[3], "recover-forward-resource")
        self.assertEqual(
            plugin.call_args.kwargs["params"],
            {
                "res_id": "root",
                "source_type": "group",
                "source_peer_id": 20000,
                "source_self_id": "10000",
                "source_real_seq": 77,
                "source_time": 1710000001,
                "source_sender_id": 30000,
            },
        )
        self.assertEqual(package["summary"]["node_count"], 3)
        self.assertEqual(
            package["forward_records"]["root"]["omm_native_payload_b64"],
            "bmF0aXZlLXByb3Rv",
        )
        self.assertEqual(package["summary"]["max_depth"], 2)
        self.assertEqual(
            [item["preview"] for item in onebot.chat_record_view(package)],
            ["[图片]", "[聊天记录]", "内层"],
        )
        image = onebot._archive_image_data(package)[0]
        self.assertEqual(image["url"], "https://old/x")
        self.assertEqual(
            image["omm_fallback_urls"],
            ["https://fallback/x", "https://signed/x"],
        )

    def test_build_inline_root_captures_native_payload_from_outer_message(self):
        source = {
            "message_id": 99,
            "self_id": 10000,
            "message_type": "group",
            "group_id": 20000,
            "real_seq": 77,
            "time": 1710000001,
            "sender": {"user_id": 30000},
            "message": [{
                "type": "forward",
                "data": {"content": [{
                    "sender": {"user_id": 30000, "nickname": "Alice"},
                    "message": [{"type": "text", "data": {"text": "内联内容"}}],
                }]},
            }],
        }
        native_card = {
            "app": "com.tencent.multimsg",
            "meta": {"detail": {"resid": "old-resource"}},
        }

        with (
            mock.patch("src.onebot.call_action", return_value=source),
            mock.patch(
                "src.onebot.call_webui_plugin_api",
                return_value={
                    "ok": True,
                    "message": source,
                    "native_payload_b64": "aW5saW5lLXByb3Rv",
                    "native_card": native_card,
                },
            ) as plugin,
        ):
            package = onebot.build_chat_record_package(
                "http://127.0.0.1:3000",
                "token",
                "99",
                webui_url="http://127.0.0.1:6099",
                webui_token="webui-token",
            )

        self.assertEqual(plugin.call_args.args[3], "recover-message")
        self.assertEqual(package["omm_native_payload_b64"], "aW5saW5lLXByb3Rv")
        self.assertEqual(package["native_card"], native_card)
        self.assertNotIn("_omm_native_payload_b64", package["source"])
        self.assertEqual(package["summary"]["node_count"], 1)

    def test_build_inline_root_merges_quoted_media_from_recovered_message(self):
        plain_source = {
            "message_id": 99,
            "self_id": 10000,
            "message_type": "group",
            "group_id": 20000,
            "real_seq": 77,
            "time": 1710000001,
            "sender": {"user_id": 30000},
            "message": [{
                "type": "forward",
                "data": {"content": [{
                    "sender": {"nickname": "Bob"},
                    "message": [
                        {"type": "reply", "data": {"id": "quoted-1"}},
                        {"type": "text", "data": {"text": "看这里"}},
                    ],
                }]},
            }],
        }
        recovered_source = {
            **plain_source,
            "message": [{
                "type": "forward",
                "data": {"content": [{
                    "sender": {"nickname": "Bob"},
                    "message": [
                        {
                            "type": "reply",
                            "data": {
                                "id": "quoted-1",
                                "omm_quoted_message": {
                                    "sender": {"nickname": "Alice"},
                                    "message": [{
                                        "type": "video",
                                        "data": {
                                            "file": "quoted.mp4",
                                            "path": "C:/cache/quoted.mp4",
                                        },
                                    }],
                                },
                            },
                        },
                        {"type": "text", "data": {"text": "看这里"}},
                    ],
                }]},
            }],
        }

        with (
            mock.patch("src.onebot.call_action", return_value=plain_source),
            mock.patch(
                "src.onebot.call_webui_plugin_api",
                return_value={
                    "ok": True,
                    "message": recovered_source,
                    "native_payload_b64": "aW5saW5lLXByb3Rv",
                },
            ),
        ):
            package = onebot.build_chat_record_package(
                "http://127.0.0.1:3000",
                "token",
                "99",
                webui_url="http://127.0.0.1:6099",
                webui_token="webui-token",
            )

        node = package["source"]["message"][0]["data"]["content"][0]
        quote = node["message"][0]["data"]["omm_quoted_message"]
        self.assertEqual(quote["sender"]["nickname"], "Alice")
        self.assertEqual(quote["message"][0]["type"], "video")
        self.assertEqual(package["summary"]["media_count"], 1)
        self.assertEqual(
            onebot._archive_media_entries(package)[0][0],
            "video",
        )

    def test_command_import_recovers_missing_real_seq_by_message_id(self):
        plain_source = {
            "message_id": "7690600602430115722",
            "self_id": 10000,
            "message_type": "private",
            "target_id": 20000,
            "message": [{"type": "forward", "data": {"id": "root"}}],
        }
        recovered_source = {
            **plain_source,
            "real_seq": "7788",
            "message": [{
                "type": "forward",
                "data": {"content": [{
                    "sender": {"nickname": "Bob"},
                    "message": [{
                        "type": "reply",
                        "data": {
                            "id": "quoted-1",
                            "omm_quoted_message": {
                                "sender": {"nickname": "Alice"},
                                "message": [{
                                    "type": "image",
                                    "data": {
                                        "file": "quoted.jpg",
                                        "path": "C:/cache/quoted.jpg",
                                    },
                                }],
                            },
                        },
                    }],
                }]},
            }],
        }

        with (
            mock.patch("src.onebot.call_action", return_value=plain_source),
            mock.patch(
                "src.onebot.call_webui_plugin_api",
                return_value={
                    "ok": True,
                    "message": recovered_source,
                    "native_payload_b64": "bmF0aXZl",
                },
            ) as plugin,
        ):
            package = onebot.build_chat_record_package(
                "http://127.0.0.1:3000",
                "token",
                plain_source["message_id"],
                trigger_event={
                    "self_id": 10000,
                    "message_type": "private",
                    "target_id": 20000,
                },
                webui_url="http://127.0.0.1:6099",
                webui_token="webui-token",
            )

        self.assertEqual(plugin.call_args.args[3], "recover-message-by-id")
        self.assertEqual(
            plugin.call_args.kwargs["params"],
            {
                "message_id": plain_source["message_id"],
                "source_type": "private",
                "source_peer_id": 20000,
                "source_self_id": "10000",
            },
        )
        self.assertEqual(package["source"]["real_seq"], "7788")
        self.assertEqual(package["summary"]["media_count"], 1)
        self.assertEqual(
            onebot._archive_media_entries(package)[0][0],
            "image",
        )

    def test_command_import_recovers_by_id_when_get_msg_mapping_expired(self):
        recovered_source = {
            "message_id": "7690600602430115722",
            "self_id": 10000,
            "message_type": "group",
            "group_id": 20000,
            "real_seq": "7788",
            "message": [{
                "type": "forward",
                "data": {"content": [{
                    "message": [{"type": "text", "data": {"text": "仍可恢复"}}],
                }]},
            }],
        }
        with (
            mock.patch(
                "src.onebot.call_action",
                side_effect=onebot.OneBotError("OneBot 调用失败：消息不存在"),
            ),
            mock.patch(
                "src.onebot.call_webui_plugin_api",
                return_value={
                    "ok": True,
                    "message": recovered_source,
                    "native_payload_b64": "bmF0aXZl",
                },
            ) as plugin,
        ):
            package = onebot.build_chat_record_package(
                "http://127.0.0.1:3000",
                "token",
                recovered_source["message_id"],
                trigger_event={
                    "self_id": 10000,
                    "message_type": "group",
                    "group_id": 20000,
                },
                webui_url="http://127.0.0.1:6099",
                webui_token="webui-token",
            )

        self.assertEqual(plugin.call_args.args[3], "recover-message-by-id")
        self.assertEqual(package["source"]["real_seq"], "7788")
        self.assertEqual(package["summary"]["node_count"], 1)

    def test_build_chat_record_package_hydrates_complete_media_with_plugin(self):
        def fake_action(http_url, token, action, params=None, timeout=5):
            if action == "get_msg":
                return {
                    "message_id": 99,
                    "self_id": 10000,
                    "message_type": "group",
                    "group_id": 20000,
                    "real_seq": 77,
                    "time": 1710000001,
                    "sender": {"user_id": 30000},
                    "message": [{"type": "forward", "data": {"id": "root"}}],
                }
            return {
                "messages": [
                    {
                        "sender": {"nickname": "Alice"},
                        "message": [
                            {"type": "image", "data": {"file": "image.jpg"}},
                            {
                                "type": "video",
                                "data": {
                                    "file": "video.mp4",
                                    "url": "https://old/video",
                                },
                            },
                        ],
                    }
                ]
            }

        recovered = {
            "ok": True,
            "messages": [
                {
                    "sender": {"nickname": "Alice"},
                    "message": [
                        {
                            "type": "image",
                            "data": {"file": "image.jpg", "path": "C:/cache/image.jpg"},
                        },
                        {
                            "type": "video",
                            "data": {
                                "file": "video.mp4",
                                "path": "C:/cache/video.mp4",
                                "url": "https://fresh/video",
                            },
                        },
                    ],
                }
            ],
        }
        with (
            mock.patch("src.onebot.call_action", side_effect=fake_action),
            mock.patch(
                "src.onebot.call_webui_plugin_api", return_value=recovered
            ) as plugin,
        ):
            package = onebot.build_chat_record_package(
                "http://127.0.0.1:3000",
                "token",
                "99",
                webui_url="http://127.0.0.1:6099",
                webui_token="webui-token",
            )

        plugin.assert_called_once()
        media = package["forward_records"]["root"]["messages"][0]["message"]
        image = media[0]
        video = media[1]
        self.assertEqual(image["data"]["path"], "C:/cache/image.jpg")
        self.assertEqual(video["data"]["path"], "C:/cache/video.mp4")
        self.assertEqual(video["data"]["url"], "https://fresh/video")
        self.assertEqual(video["data"]["omm_fallback_urls"], ["https://old/video"])

    def test_download_archive_image_tries_fallback_urls(self):
        attempts = []
        with tempfile.TemporaryDirectory() as tmp:
            downloaded = Path(tmp) / "image.jpg"
            downloaded.write_bytes(b"image")

            def fake_action(http_url, token, action, params=None, timeout=5):
                attempts.append(params["url"])
                if params["url"] == "https://fallback/x":
                    return {"file": str(downloaded)}
                raise onebot.OneBotError("下载失败")

            with mock.patch("src.onebot.call_action", side_effect=fake_action):
                result = onebot._download_archive_image(
                    "http://127.0.0.1:3000",
                    "token",
                    {
                        "url": "https://primary/x",
                        "omm_fallback_urls": ["https://fallback/x"],
                    },
                )

        self.assertEqual(result, str(downloaded))
        self.assertEqual(attempts, ["https://primary/x", "https://fallback/x"])

    def test_existing_empty_nodes_show_and_send_as_placeholder(self):
        package = {
            "source": {"message": [{"type": "forward", "data": {"id": "root"}}]},
            "forward_records": {
                "root": {
                    "messages": [
                        {
                            "sender": {"nickname": "Alice"},
                            "message": [],
                            "raw_message": "",
                        }
                    ]
                }
            },
        }

        view = onebot.chat_record_view(package)
        nodes, degraded = onebot._forward_nodes(package, 123)

        self.assertEqual(view[0]["preview"], "[空消息或未解析的嵌套聊天记录]")
        self.assertEqual(degraded, 0)
        self.assertEqual(
            nodes[0]["data"]["content"][0]["data"]["text"],
            "[空消息或未解析的嵌套聊天记录]",
        )

    def test_chat_record_view_exposes_saved_image_media_urls_in_archive_order(self):
        package = {
            "source": {"message": [{"type": "forward", "data": {"id": "root"}}]},
            "forward_records": {
                "root": {
                    "messages": [
                        {
                            "sender": {"nickname": "Alice"},
                            "message": [
                                {
                                    "type": "image",
                                    "data": {
                                        "file": "first.jpg",
                                        "local_path": "chat-record-media/r/00001.jpg",
                                    },
                                },
                                {
                                    "type": "video",
                                    "data": {
                                        "file": "clip.mp4",
                                        "local_path": "chat-record-media/r/00002.mp4",
                                    },
                                },
                            ],
                        },
                        {
                            "sender": {"nickname": "Bob"},
                            "message": [
                                {
                                    "type": "image",
                                    "data": {
                                        "file": "second.gif",
                                        "local_path": "chat-record-media/r/00003.gif",
                                    },
                                }
                            ],
                        },
                    ]
                }
            },
        }

        view = onebot.chat_record_view(
            package,
            media_url_factory=lambda index, _kind: f"/api/chat-record-media/7/{index}",
        )

        self.assertEqual(view[0]["media"][0]["index"], 1)
        self.assertEqual(view[0]["media"][0]["url"], "/api/chat-record-media/7/1")
        self.assertEqual(view[0]["media"][0]["alt"], "first.jpg")
        self.assertEqual(view[1]["media"][0]["index"], 3)
        self.assertEqual(view[1]["media"][0]["url"], "/api/chat-record-media/7/3")

    def test_reply_quoted_media_is_archived_previewed_and_rebuilt(self):
        quoted_image = {
            "file": "quoted.jpg",
            "local_path": "chat-record-media/r/00001.jpg",
        }
        package = {
            "source": {"message": [{"type": "forward", "data": {"id": "root"}}]},
            "forward_records": {
                "root": {
                    "messages": [
                        {
                            "sender": {"nickname": "Bob"},
                            "message": [
                                {
                                    "type": "reply",
                                    "data": {
                                        "id": "source-message",
                                        "omm_quoted_message": {
                                            "sender": {"nickname": "Alice"},
                                            "time": 1710000000,
                                            "message": [
                                                {
                                                    "type": "image",
                                                    "data": quoted_image,
                                                }
                                            ],
                                        },
                                    },
                                },
                                {"type": "text", "data": {"text": "看这里"}},
                            ],
                        }
                    ]
                }
            },
        }

        entries = onebot._archive_media_entries(package)
        view = onebot.chat_record_view(
            package,
            media_url_factory=lambda index, _kind: f"/media/{index}",
        )
        rebuilt, degraded = onebot._prepare_sendable_segments(
            package["forward_records"]["root"]["messages"][0]["message"],
            degrade_media=True,
        )

        self.assertEqual(entries, [("image", quoted_image)])
        self.assertEqual(view[0]["quotes"][0]["sender"], "Alice")
        self.assertEqual(view[0]["quotes"][0]["media"][0]["index"], 1)
        self.assertTrue(view[0]["quotes"][0]["media"][0]["quoted"])
        self.assertEqual(degraded, 1)
        self.assertEqual(rebuilt[0]["data"]["text"], "[引用内容] ")
        self.assertIn("资源已失效", rebuilt[1]["data"]["text"])
        self.assertEqual(rebuilt[-1]["data"]["text"], "看这里")

    def test_chat_record_media_path_serves_saved_images_and_videos_under_root(self):
        from types import SimpleNamespace

        from src.webui import WebUI

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            media = cache / "chat-record-media" / "record"
            media.mkdir(parents=True)
            image = media / "00001.gif"
            image.write_bytes(b"GIF89a")
            video = media / "00002.mp4"
            video.write_bytes(b"video")
            package = {
                "source": {
                    "self_id": 123,
                    "message_id": 456,
                    "message": [{"type": "forward", "data": {"id": "root"}}],
                },
                "forward_records": {
                    "root": {
                        "messages": [
                            {
                                "message": [
                                    {
                                        "type": "image",
                                        "data": {
                                            "local_path": "chat-record-media/record/00001.gif"
                                        },
                                    },
                                    {
                                        "type": "video",
                                        "data": {
                                            "local_path": "chat-record-media/record/00002.mp4"
                                        },
                                    },
                                ]
                            }
                        ]
                    }
                },
                "summary": {"node_count": 1, "record_count": 1, "media_count": 2},
            }
            db = MemeDB(root / "memes.db")
            try:
                saved = db.save_chat_record(package)
                cfg = SimpleNamespace(
                    cache_dir=cache,
                    data_dir=root / "data",
                    get=lambda _key, default=None: default,
                )
                ui = WebUI.__new__(WebUI)
                ui._cfg = cfg
                with mock.patch("src.webui.get_db", return_value=db):
                    result = ui._get_chat_record_media_path(saved["id"], 1)
                    video_result = ui._get_chat_record_media_path(saved["id"], 2)
                self.assertTrue(os.path.samefile(result, image))
                self.assertTrue(os.path.samefile(video_result, video))
            finally:
                db.close()

    def test_build_chat_record_package_probes_outer_id_when_forward_segment_is_lost(
        self,
    ):
        actions = []

        def fake_action(http_url, token, action, params=None, timeout=5):
            actions.append((action, params))
            if action == "get_msg":
                return {
                    "message_id": 99,
                    "message_type": "group",
                    "group_id": 456,
                    "message": [
                        {
                            "type": "json",
                            "data": {"data": '{"app":"com.tencent.multimsg"}'},
                        }
                    ],
                }
            return {
                "messages": [
                    {
                        "sender": {"nickname": "Alice"},
                        "message": [{"type": "text", "data": {"text": "你好"}}],
                    }
                ]
            }

        with mock.patch("src.onebot.call_action", side_effect=fake_action):
            package = onebot.build_chat_record_package(
                "http://127.0.0.1:3000", "token", "99"
            )

        self.assertEqual(set(package["forward_records"]), {"99"})
        self.assertEqual(package["summary"]["node_count"], 1)
        self.assertEqual(
            actions,
            [
                ("get_msg", {"message_id": "99"}),
                ("get_forward_msg", {"message_id": "99"}),
            ],
        )

    def test_build_chat_record_package_reads_multimsg_json_resid(self):
        actions = []
        card = {
            "app": "com.tencent.multimsg",
            "prompt": "[聊天记录] Alice：你好",
            "meta": {"detail": {"resid": "forward-resource+/="}},
        }

        def fake_action(http_url, token, action, params=None, timeout=5):
            actions.append((action, params))
            if action == "get_msg":
                return {
                    "message_id": 99,
                    "message_type": "group",
                    "group_id": 456,
                    "message": [
                        {
                            "type": "json",
                            "data": {"data": json.dumps(card, ensure_ascii=False)},
                        }
                    ],
                }
            return {
                "messages": [
                    {
                        "sender": {"nickname": "Alice"},
                        "message": [{"type": "text", "data": {"text": "你好"}}],
                    }
                ]
            }

        with mock.patch("src.onebot.call_action", side_effect=fake_action):
            package = onebot.build_chat_record_package(
                "http://127.0.0.1:3000", "token", "99"
            )

        self.assertEqual(package["title"], "[聊天记录] Alice：你好")
        self.assertEqual(package["native_card"], card)
        self.assertEqual(set(package["forward_records"]), {"forward-resource+/="})
        self.assertEqual(package["summary"]["node_count"], 1)
        self.assertEqual(
            actions,
            [
                ("get_msg", {"message_id": "99"}),
                (
                    "get_forward_msg",
                    {"message_id": "forward-resource+/="},
                ),
            ],
        )

    def test_command_handler_reports_save_result_to_original_group(self):
        event = {
            "post_type": "message_sent",
            "self_id": 123,
            "message_type": "group",
            "group_id": 456,
            "message": [
                {"type": "reply", "data": {"id": "99"}},
                {"type": "text", "data": {"text": "/omm"}},
            ],
        }
        archive_result = {
            "ok": True,
            "id": 1,
            "created": True,
            "title": "测试记录",
            "node_count": 3,
            "record_count": 1,
            "max_depth": 1,
            "media_count": 0,
            "segment_types": ["text"],
            "media_localized": False,
        }
        with mock.patch(
            "src.onebot.archive_chat_record", return_value=archive_result
        ) as archive:
            with mock.patch("src.onebot.call_action", return_value={}) as action:
                result = onebot.handle_command_event(
                    "http://127.0.0.1:3000", "token", "/omm", event
                )

        self.assertTrue(result["ok"])
        archive.assert_called_once()
        self.assertEqual(action.call_args.args[2], "send_group_msg")
        self.assertEqual(action.call_args.args[3]["group_id"], 456)

    def test_feedback_failure_does_not_undo_saved_record(self):
        event = {
            "post_type": "message_sent",
            "self_id": 123,
            "message_type": "group",
            "group_id": 456,
            "message": [
                {"type": "reply", "data": {"id": "99"}},
                {"type": "text", "data": {"text": "/omm"}},
            ],
        }
        saved = {
            "ok": True,
            "id": 1,
            "created": True,
            "title": "已落库",
            "node_count": 1,
            "record_count": 1,
            "max_depth": 1,
            "media_count": 0,
            "segment_types": ["text"],
            "media_localized": False,
        }
        with mock.patch("src.onebot.archive_chat_record", return_value=saved):
            with mock.patch(
                "src.onebot.call_action", side_effect=onebot.OneBotError("发送失败")
            ):
                result = onebot.handle_command_event(
                    "http://127.0.0.1:3000", "token", "/omm", event
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["feedback_error"], "发送失败")


class TestLibraryEvents(unittest.TestCase):
    def tearDown(self):
        from src.library_events import set_change_callback

        set_change_callback(None)

    def test_notification_reaches_registered_bound_method(self):
        from src.library_events import notify_changed, set_change_callback

        class Receiver:
            def __init__(self):
                self.kinds = []

            def receive(self, kind):
                self.kinds.append(kind)

        receiver = Receiver()
        set_change_callback(receiver.receive)

        self.assertTrue(notify_changed("content"))
        self.assertTrue(notify_changed("structure"))
        self.assertEqual(receiver.kinds, ["content", "structure"])

    def test_notification_without_registered_window_is_ignored(self):
        from src.library_events import notify_changed, set_change_callback

        set_change_callback(None)

        self.assertFalse(notify_changed("content"))

    def test_chat_archive_notifies_library_after_database_commit(self):
        from src import library_events

        package = {
            "title": "新聊天记录",
            "source": {"self_id": "1", "message_id": "2"},
            "summary": {"node_count": 1, "media_count": 0},
        }
        db = mock.MagicMock()
        db.get_chat_record_by_source.return_value = None
        db.save_chat_record.return_value = {"id": 7, "created": True}
        cfg = mock.MagicMock()

        with (
            mock.patch("src.onebot.build_chat_record_package", return_value=package),
            mock.patch("src.config.get_config", return_value=cfg),
            mock.patch("src.onebot._localize_archive_media"),
            mock.patch("src.onebot._prune_archive_media"),
            mock.patch.object(library_events, "notify_changed") as notify,
        ):
            result = onebot.archive_chat_record(
                "http://127.0.0.1:3000", "token", "2", db=db
            )

        self.assertTrue(result["ok"])
        notify.assert_called_once_with("content")


class TestBulkRead(unittest.TestCase):
    def test_guarded_read_pauses_after_one_batch(self):
        from src import bulk_read

        gate = bulk_read._BulkReadGate()
        started = bulk_read.time.monotonic()
        with (
            mock.patch.object(bulk_read, "_GATE", gate),
            mock.patch.object(bulk_read, "enabled", return_value=True),
        ):
            for _ in range(bulk_read.BULK_READ_BATCH_SIZE):
                with bulk_read.guarded_read():
                    pass
        self.assertEqual(gate._count, 0)
        self.assertGreaterEqual(
            gate._cooldown_until,
            started + bulk_read.BULK_READ_PAUSE_SECONDS,
        )

    def test_guarded_read_uses_configurable_batch_and_pause(self):
        from src import bulk_read

        gate = bulk_read._BulkReadGate()
        started = bulk_read.time.monotonic()
        with (
            mock.patch.object(bulk_read, "_GATE", gate),
            mock.patch.object(bulk_read, "enabled", return_value=True),
            mock.patch.object(bulk_read, "limits", return_value=(3, 0.25)),
        ):
            for _ in range(3):
                with bulk_read.guarded_read():
                    pass
        self.assertEqual(gate._count, 0)
        self.assertGreaterEqual(gate._cooldown_until, started + 0.25)

    def test_critical_read_bypasses_existing_cooldown(self):
        from src import bulk_read

        gate = bulk_read._BulkReadGate()
        gate._cooldown_until = bulk_read.time.monotonic() + 30
        with mock.patch.object(bulk_read, "limits", return_value=(1000, 0)):
            started = bulk_read.time.monotonic()
            with gate.operation("critical"):
                pass
        self.assertLess(bulk_read.time.monotonic() - started, 0.2)

    def test_waiting_critical_read_runs_before_queued_background_read(self):
        from src import bulk_read

        gate = bulk_read._BulkReadGate()
        active_entered = threading.Event()
        release_active = threading.Event()
        order = []

        def run_active():
            with gate.operation("background"):
                order.append("active")
                active_entered.set()
                release_active.wait(2)

        def run_waiter(name, priority):
            with gate.operation(priority):
                order.append(name)

        with mock.patch.object(bulk_read, "limits", return_value=(1000, 0)):
            active = threading.Thread(target=run_active)
            background = threading.Thread(
                target=run_waiter, args=("background", "background")
            )
            critical = threading.Thread(
                target=run_waiter, args=("critical", "critical")
            )
            active.start()
            self.assertTrue(active_entered.wait(1))
            background.start()
            critical.start()
            deadline = bulk_read.time.monotonic() + 1
            while bulk_read.time.monotonic() < deadline:
                with gate._condition:
                    if gate._waiting[0] and gate._waiting[2]:
                        break
                bulk_read.time.sleep(0.005)
            release_active.set()
            active.join(1)
            critical.join(1)
            background.join(1)

        self.assertEqual(order, ["active", "critical", "background"])

    def test_limit_values_are_clamped(self):
        from src import bulk_read

        self.assertEqual(bulk_read.clamp_batch_size(0), 1)
        self.assertEqual(bulk_read.clamp_batch_size(5000), 1000)
        self.assertEqual(bulk_read.clamp_batch_size("invalid"), 8)
        self.assertEqual(bulk_read.clamp_pause_seconds(-2), 0.0)
        self.assertEqual(bulk_read.clamp_pause_seconds(99), 60.0)
        self.assertEqual(bulk_read.clamp_pause_seconds("invalid"), 1.0)

    def test_settings_normalization_clamps_only_bulk_read_numbers(self):
        from src.webui import _normalize_bulk_read_settings

        normalized = _normalize_bulk_read_settings(
            {
                "bulk_read_batch_size": 0,
                "bulk_read_pause_seconds": 999,
                "bulk_read_prefetch_pages": True,
                "theme": "dark",
            }
        )

        self.assertEqual(normalized["bulk_read_batch_size"], 1)
        self.assertEqual(normalized["bulk_read_pause_seconds"], 60.0)
        self.assertTrue(normalized["bulk_read_prefetch_pages"])
        self.assertEqual(normalized["theme"], "dark")


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.index_path = self.tmp_dir / "meme-index.json"

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_collection_tree_preserves_explicit_empty_paths(self):
        from src import manifest

        db = mock.MagicMock()
        db.get_collections.return_value = [
            (1, "角色", None, 0),
            (2, "爱丽丝", 1, 0),
            (3, "问候", 2, 0),
        ]
        db.search.return_value = []

        result = manifest._build_collection_tree(db)

        self.assertEqual(
            result,
            [
                {
                    "name": "角色",
                    "filenames": [],
                    "children": [
                        {
                            "name": "爱丽丝",
                            "filenames": [],
                            "children": [
                                {"name": "问候", "filenames": []}
                            ],
                        }
                    ],
                }
            ],
        )
        db.delete_collection.assert_not_called()

    def test_update_meme_name_rewrites_only_local_manifest_metadata(self):
        from src import manifest

        original = {
            "version": 4,
            "memes": [
                {
                    "filename": "first.png",
                    "name": "旧标题",
                    "sha256": "first-hash",
                    "file_size": 123,
                    "mtime": "456",
                    "category_path": ["角色"],
                    "storage_path": "角色/first.png",
                },
                {"filename": "second.png", "name": "保持不变"},
            ],
            "collections": [{"name": "收藏", "filenames": ["first.png"]}],
            "categories": [{"name": "角色"}],
        }
        self.index_path.write_text(
            json.dumps(original, ensure_ascii=False), encoding="utf-8"
        )

        with (
            mock.patch.object(manifest, "_index_path", return_value=self.index_path),
            mock.patch.object(
                manifest, "guarded_read", side_effect=AssertionError("不应访问原件")
            ),
        ):
            updated = manifest.update_meme_name("first.png", "新标题")

        self.assertTrue(updated)
        result = json.loads(self.index_path.read_text(encoding="utf-8"))
        self.assertEqual(result["memes"][0]["name"], "新标题")
        self.assertEqual(result["memes"][0]["mtime"], "456")
        self.assertEqual(result["memes"][0]["storage_path"], "角色/first.png")
        self.assertEqual(result["memes"][1], original["memes"][1])
        self.assertEqual(result["collections"], original["collections"])
        self.assertFalse(self.index_path.with_name("meme-index.json.tmp").exists())

    def test_update_meme_name_requests_fallback_when_entry_is_missing(self):
        from src import manifest

        self.index_path.write_text(
            json.dumps({"version": 4, "memes": []}), encoding="utf-8"
        )
        with mock.patch.object(manifest, "_index_path", return_value=self.index_path):
            self.assertFalse(manifest.update_meme_name("missing.png", "新标题"))

    def test_incremental_updates_do_not_wait_for_full_rebuild_lock(self):
        from src import manifest

        lock_acquired = threading.Event()
        release_lock = threading.Event()

        def hold_manifest_lock():
            with manifest._MANIFEST_LOCK:
                lock_acquired.set()
                release_lock.wait(2)

        holder = threading.Thread(target=hold_manifest_lock)
        holder.start()
        self.assertTrue(lock_acquired.wait(1))
        try:
            started = time.monotonic()
            self.assertFalse(manifest.update_meme_name("busy.png", "新标题"))
            self.assertFalse(manifest.update_meme_structure(7, "busy.png"))
            self.assertLess(time.monotonic() - started, 0.2)
        finally:
            release_lock.set()
            holder.join(1)
        self.assertFalse(holder.is_alive())

    def test_update_meme_structure_refreshes_metadata_without_reading_originals(self):
        from src import manifest

        original = {
            "version": 4,
            "memes": [
                {
                    "filename": "old.png",
                    "name": "旧标题",
                    "sha256": "old-hash",
                    "file_size": 100,
                    "mtime": "456",
                    "category_path": [],
                    "storage_path": "old.png",
                    "future_field": "保留",
                },
                {"filename": "untouched.png", "name": "保持不变"},
            ],
            "collections": [],
            "categories": [],
        }
        self.index_path.write_text(
            json.dumps(original, ensure_ascii=False), encoding="utf-8"
        )
        db = mock.MagicMock()
        db.get_by_id.return_value = {
            "id": 7,
            "filename": "new.png",
            "original_name": "新标题",
            "file_hash": "new-hash",
            "file_size": 321,
            "category_id": 9,
        }
        db.get_category_path_names.return_value = ["角色", "爱丽丝"]
        db.get_tags_map.return_value = {"new.png": ["可爱"]}
        db.get_tag_records_map.return_value = {
            "new.png": [{"name": "可爱", "category": "风格"}]
        }
        db.search.return_value = [{"filename": "new.png"}]
        collections = [{"name": "收藏", "filenames": ["new.png"]}]
        categories = [{"name": "角色", "children": [{"name": "爱丽丝"}]}]

        with (
            mock.patch.object(manifest, "_index_path", return_value=self.index_path),
            mock.patch.object(manifest, "get_db", return_value=db),
            mock.patch.object(
                manifest, "_build_collection_tree", return_value=collections
            ),
            mock.patch.object(
                manifest, "_build_category_tree", return_value=categories
            ),
            mock.patch.object(
                manifest, "guarded_read", side_effect=AssertionError("不应访问原件")
            ),
        ):
            updated = manifest.update_meme_structure(7, "old.png")

        self.assertTrue(updated)
        result = json.loads(self.index_path.read_text(encoding="utf-8"))
        entry = result["memes"][0]
        self.assertEqual(entry["filename"], "new.png")
        self.assertEqual(entry["name"], "新标题")
        self.assertEqual(entry["sha256"], "new-hash")
        self.assertEqual(entry["file_size"], 321)
        self.assertEqual(entry["mtime"], "456")
        self.assertEqual(entry["category_path"], ["角色", "爱丽丝"])
        self.assertEqual(entry["storage_path"], "角色/爱丽丝/new.png")
        self.assertEqual(entry["future_field"], "保留")
        self.assertEqual(entry["tags"], ["可爱"])
        self.assertEqual(entry["tag_records"], [{"name": "可爱", "category": "风格"}])
        self.assertEqual(result["favorite"], ["new.png"])
        self.assertEqual(result["memes"][1], original["memes"][1])
        self.assertEqual(result["collections"], collections)
        self.assertEqual(result["categories"], categories)

    def test_update_meme_structure_requests_fallback_when_entry_is_missing(self):
        from src import manifest

        self.index_path.write_text(
            json.dumps({"version": 4, "memes": []}), encoding="utf-8"
        )
        db = mock.MagicMock()
        db.get_by_id.return_value = {"id": 7, "filename": "new.png"}
        with (
            mock.patch.object(manifest, "_index_path", return_value=self.index_path),
            mock.patch.object(manifest, "get_db", return_value=db),
        ):
            self.assertFalse(manifest.update_meme_structure(7, "old.png"))


class TestQQDragTarget(unittest.TestCase):
    def test_renderer_title_from_exact_drop_window_is_mapped_to_group(self):
        def onebot_action(_url, _token, action, **_kwargs):
            if action == "get_group_list":
                return [{"group_id": 123, "group_name": "目标群"}]
            if action == "get_friend_list":
                return []
            raise AssertionError(action)

        with (
            mock.patch.object(
                qq_drag,
                "call_webui_plugin_api",
                return_value={
                    "ok": True,
                    "available": True,
                    "confidence": "renderer_title",
                    "title_candidates": [{"name": "目标群", "score": 300}],
                },
            ) as plugin_api,
            mock.patch.object(qq_drag, "call_action", side_effect=onebot_action),
            mock.patch.object(
                qq_drag,
                "_qq_header_candidates_with_timeout",
                side_effect=AssertionError("渲染层标题匹配后不应回退到 UIA"),
            ),
        ):
            target = qq_drag.resolve_qq_target(
                "http://127.0.0.1:3000",
                "secret",
                12345,
                "QQ",
            )

        self.assertEqual(target["message_type"], "group")
        self.assertEqual(target["peer_id"], "123")
        self.assertEqual(target["source"], "napcat_renderer")
        self.assertEqual(
            plugin_api.call_args.kwargs["params"], {"window_handle": 12345}
        )

    def test_plugin_target_is_preferred_over_ui_automation(self):
        with (
            mock.patch.object(
                qq_drag,
                "call_webui_plugin_api",
                return_value={
                    "ok": True,
                    "available": True,
                    "confidence": "active_tab",
                    "target": {
                        "message_type": "private",
                        "peer_id": "456",
                        "label": "备注名",
                    },
                },
            ),
            mock.patch.object(
                qq_drag,
                "_qq_header_candidates_with_timeout",
                side_effect=AssertionError("不应回退到 UIA"),
            ),
        ):
            target = qq_drag.resolve_qq_target(
                "http://127.0.0.1:3000",
                "secret",
                123,
                "QQ",
            )

        self.assertEqual(target["message_type"], "private")
        self.assertEqual(target["peer_id"], "456")
        self.assertEqual(target["source"], "napcat_plugin")

    def test_plugin_target_rejects_unconfirmed_stale_peer(self):
        with mock.patch.object(
            qq_drag,
            "call_webui_plugin_api",
            return_value={
                "ok": True,
                "available": True,
                "target": {
                    "message_type": "group",
                    "peer_id": "123",
                    "label": "可能已经过期的群",
                },
            },
        ):
            error = {}
            target = qq_drag._plugin_target("http://127.0.0.1:6099", "secret", error)

        self.assertIsNone(target)
        self.assertIn("未经", error["error"])

    def test_qq_header_uses_point_hits_instead_of_descendant_scan(self):
        from types import SimpleNamespace

        bounds = SimpleNamespace(X=100, Y=200, Width=1000, Height=700)
        title_bounds = SimpleNamespace(X=390, Y=250, Width=180, Height=34)
        root = SimpleNamespace(Current=SimpleNamespace(BoundingRectangle=bounds))
        title = SimpleNamespace(
            Current=SimpleNamespace(
                Name="测试群",
                BoundingRectangle=title_bounds,
                ControlType=SimpleNamespace(ProgrammaticName="ControlType.Button"),
            )
        )

        class FakeAutomationElement:
            point_calls = 0

            @staticmethod
            def FromHandle(_handle):
                return root

            @classmethod
            def FromPoint(cls, _point):
                cls.point_calls += 1
                return title

        walker = SimpleNamespace(GetParent=lambda _element: None)
        fake_tree_walker = SimpleNamespace(ControlViewWalker=walker)

        with mock.patch.object(
            qq_drag,
            "_load_uia",
            return_value=(
                lambda value: value,
                lambda x, y: (x, y),
                FakeAutomationElement,
                fake_tree_walker,
            ),
        ):
            candidates = qq_drag._qq_header_candidates(123)

        self.assertEqual([item["name"] for item in candidates], ["测试群"])
        self.assertLessEqual(FakeAutomationElement.point_calls, 15)

    def test_qq_header_read_has_a_timeout(self):
        def slow_read(_window_handle, _progress=None):
            time.sleep(0.1)
            return []

        with mock.patch.object(qq_drag, "_qq_header_candidates", side_effect=slow_read):
            with self.assertRaisesRegex(RuntimeError, "超时"):
                qq_drag._qq_header_candidates_with_timeout(123, timeout=0.01)

    def test_selects_unique_group_from_qq_header(self):
        target, error = qq_drag._choose_target(
            [{"name": "测试群", "score": 15}],
            [{"group_id": 123, "group_name": "测试群"}],
            [{"user_id": 456, "nickname": "Alice", "remark": ""}],
        )

        self.assertEqual(error, "")
        self.assertEqual(target["message_type"], "group")
        self.assertEqual(target["peer_id"], "123")
        self.assertEqual(target["label"], "测试群")

    def test_selects_friend_remark_when_qq_appends_status_text(self):
        target, error = qq_drag._choose_target(
            [{"name": "测试小号 在线", "score": 15}],
            [],
            [{"user_id": 456, "nickname": "原昵称", "remark": "测试小号"}],
        )

        self.assertEqual(error, "")
        self.assertEqual(target["message_type"], "private")
        self.assertEqual(target["peer_id"], "456")
        self.assertEqual(target["label"], "测试小号")

    def test_rejects_ambiguous_partial_contact_match(self):
        target, error = qq_drag._choose_target(
            [{"name": "Alice 和 Bob", "score": 15}],
            [],
            [
                {"user_id": 1, "nickname": "Alice", "remark": ""},
                {"user_id": 2, "nickname": "Bob", "remark": ""},
            ],
        )

        self.assertIsNone(target)
        self.assertIn("读取到：Alice 和 Bob", error)

    def test_rejects_same_name_group_and_friend(self):
        target, error = qq_drag._choose_target(
            [{"name": "同名会话", "score": 15}],
            [{"group_id": 123, "group_name": "同名会话"}],
            [{"user_id": 456, "nickname": "同名会话", "remark": ""}],
        )

        self.assertIsNone(target)
        self.assertIn("无法", error)

    def test_rejects_two_equally_likely_header_contacts(self):
        target, error = qq_drag._choose_target(
            [
                {"name": "Alice", "score": 15},
                {"name": "Bob", "score": 15},
            ],
            [],
            [
                {"user_id": 1, "nickname": "Alice", "remark": ""},
                {"user_id": 2, "nickname": "Bob", "remark": ""},
            ],
        )

        self.assertIsNone(target)
        self.assertIn("多个会话", error)


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_dir / "test.db"
        self.db = MemeDB(self.db_path)

    def tearDown(self):
        self.db.close()
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_chat_record_locations_survive_refresh_and_stale_detail_edits(self):
        source = {"self_id": 123, "message_id": 99, "message_type": "group",
                  "group_id": 456, "real_seq": 100}
        saved = self.db.save_chat_record({
            "title": "原记录", "source": source, "summary": {"node_count": 2},
        })
        record_id = saved["id"]
        self.db.update_chat_record_details(record_id, [], name="自定义名称")
        stale = self.db.get_chat_record(record_id)
        folder = stale["_omm_media_folder"]
        backup = {**source, "message_id": 999, "group_id": 789, "real_seq": 200}
        self.assertTrue(self.db.add_chat_record_source(record_id, backup))
        self.assertTrue(self.db.add_chat_record_source(record_id, {**backup, "message_id": 998}))
        self.db.update_chat_record_details(record_id, [], package=stale)
        refreshed = {"title": "重新读取", "source": backup, "summary": {"node_count": 2}}
        self.assertEqual(self.db.save_chat_record(refreshed, record_id=record_id),
                         {"id": record_id, "created": False})
        entry = self.db.get_chat_record_entry(record_id)
        self.assertEqual(entry["name"], "自定义名称")
        self.assertEqual(entry["media_folder"], folder)
        locations = onebot.chat_record_sources(entry["package"])
        self.assertEqual({item["real_seq"] for item in locations}, {100, 200})
        self.assertEqual(len(locations), 2)
        self.assertEqual(self.db.count_chat_records(), 1)
        from src.webui import JsApi

        api = JsApi.__new__(JsApi)
        api._db = self.db
        self.assertEqual(len(api.get_chat_record_details(record_id)["source_locations"]), 2)

    def _save_test_chat(self, message_id: int):
        return self.db.save_chat_record(
            {
                "schema_version": 1,
                "title": f"测试记录 {message_id}",
                "source": {
                    "self_id": 1,
                    "message_id": message_id,
                    "message_type": "private",
                    "user_id": 2,
                },
                "forward_records": {},
                "summary": {"node_count": 1, "record_count": 1},
            }
        )

    def test_hierarchy_move_preserves_subtrees_members_and_ids(self):
        for kind in ("category", "collection"):
            create = getattr(self.db, f"create_{kind}")
            parent, destination = create("原父级"), create("目标父级")
            node = create("待移动", parent)
            child = create("子级", node)
            meme = self.db.add_meme(f"{kind}.png")
            chat = self._save_test_chat(410 if kind == "category" else 411)["id"]
            if kind == "category":
                self.db.set_meme_category(meme, child)
                self.db.set_chat_category(chat, node)
            else:
                self.db.add_to_collection(meme, child)
                self.db.add_to_chat_collection(chat, node)
            result = self.db.move_hierarchy_node(kind, node, destination)
            self.assertTrue(result["changed"])
            get_nodes = (
                self.db.get_categories
                if kind == "category"
                else self.db.get_collections
            )
            rows = {item[0]: item for item in get_nodes()}
            self.assertEqual(rows[node][2], destination)
            self.assertEqual(rows[child][2], node)
            self.assertEqual(rows[parent][2], None)
            self.assertEqual(
                self.db.move_hierarchy_node(kind, node, destination),
                {"changed": False, "parent_id": destination},
            )
            self.assertTrue(self.db.move_hierarchy_node(kind, node)["changed"])
            if kind == "category":
                self.assertEqual(self.db.get_meme_category(meme)["id"], child)
                self.assertEqual(self.db.get_chat_category(chat)["id"], node)
                self.assertEqual(
                    self.db.get_category_path_names(child), ["待移动", "子级"]
                )
            else:
                self.assertEqual(self.db.get_meme_collections(meme)[0]["id"], child)
                self.assertEqual(self.db.get_chat_collections(chat)[0]["id"], node)

    def test_hierarchy_move_rejects_cycles_conflicts_and_virtual_nodes(self):
        for kind in ("category", "collection"):
            create = getattr(self.db, f"create_{kind}")
            source, destination = create("source"), create("destination")
            child = create("child", source)
            create("SOURCE", destination)
            get_nodes = (
                self.db.get_categories
                if kind == "category"
                else self.db.get_collections
            )
            before = get_nodes()
            for node, target in (
                (source, source),
                (source, child),
                (source, destination),
                (source, -4),
                (source, -1),
                (source, 999999),
                (-4, source),
                (-source - 4, source),
                (999999, None),
                (True, source),
                (source, False),
                (source, "2"),
            ):
                with self.subTest(kind=kind, node=node, target=target):
                    with self.assertRaises(ValueError):
                        self.db.move_hierarchy_node(kind, node, target)
                    self.assertEqual(get_nodes(), before)
            duplicate = create("source", child)
            with self.assertRaises(ValueError):
                self.db.move_hierarchy_node(kind, duplicate, None)
        with self.assertRaises(ValueError):
            self.db.move_hierarchy_node("invalid", 1, 2)
        hidden = self.db.ensure_hidden_collection()
        with self.assertRaises(ValueError):
            self.db.move_hierarchy_node("collection", hidden, destination)
        normal = self.db.create_collection("允许放入隐藏")
        self.assertTrue(
            self.db.move_hierarchy_node("collection", normal, hidden)["changed"]
        )
        self.assertTrue(self.db.move_hierarchy_node("collection", normal)["changed"])
        reserved = self.db.create_category("chat-record-media", source)
        with self.assertRaises(ValueError):
            self.db.move_hierarchy_node("category", reserved)

    def test_hierarchy_opposite_concurrent_moves_cannot_create_cycle(self):
        from concurrent.futures import ThreadPoolExecutor

        for kind in ("category", "collection"):
            create = getattr(self.db, f"create_{kind}")
            first, second = create("first"), create("second")
            barrier = threading.Barrier(2)

            def move(source, target):
                barrier.wait(timeout=5)
                try:
                    self.db.move_hierarchy_node(kind, source, target)
                    return True
                except ValueError:
                    return False
                finally:
                    self.db.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(move, first, second)
                b = pool.submit(move, second, first)
                self.assertEqual(
                    sorted([a.result(timeout=10), b.result(timeout=10)]), [False, True]
                )

    def _hierarchy_move_api(self):
        from types import SimpleNamespace

        from src.webui import JsApi

        cache = self.tmp_dir / "hierarchy-move-cache"
        cache.mkdir(exist_ok=True)
        cfg = Config(self.tmp_dir / "hierarchy-move-config.json")
        cfg.set("cache_dir", str(cache))
        api = JsApi.__new__(JsApi)
        api._db, api._cfg = self.db, cfg
        api._webui = SimpleNamespace(_file_cache={})
        return api, cache

    def test_hierarchy_category_api_moves_whole_directory_without_reading_images(self):
        from contextlib import nullcontext

        api, cache = self._hierarchy_move_api()
        source = self.db.create_category("source")
        child = self.db.create_category("child", source)
        target = self.db.create_category("target")
        path = cache / "source" / "child" / "original.gif"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"GIF89a-test")
        (cache / "source" / "untracked.txt").write_text("keep", encoding="utf-8")
        media = cache / "chat-record-media" / "archive" / "image.png"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"chat-media")
        mid = self.db.add_meme(path.name, category_id=child)
        chat = self._save_test_chat(412)["id"]
        self.db.set_chat_category(chat, child)
        api._webui._file_cache.update({path.name: str(path), "other.png": "unrelated"})

        with (
            mock.patch("src.webui.guarded_read", return_value=nullcontext()) as gate,
            mock.patch("src.webui.request_manifest_rebuild") as rebuild,
            mock.patch(
                "src.webui.build_manifest",
                side_effect=AssertionError("no foreground rebuild"),
            ),
            mock.patch("src.webui.os.walk", side_effect=AssertionError("no scanning")),
            mock.patch(
                "src.webui._file_sha256", side_effect=AssertionError("no hashing")
            ),
            mock.patch(
                "builtins.open", side_effect=AssertionError("no file content reads")
            ),
        ):
            result = api.move_hierarchy_node("category", source, target)
        self.assertTrue(result["ok"], result)
        gate.assert_called_once_with(priority="critical")
        rebuild.assert_called_once_with()
        moved = cache / "target" / "source" / "child" / path.name
        self.assertFalse(path.exists())
        self.assertEqual(moved.read_bytes(), b"GIF89a-test")
        self.assertEqual(
            (cache / "target" / "source" / "untracked.txt").read_text(), "keep"
        )
        self.assertEqual(media.read_bytes(), b"chat-media")
        self.assertEqual(self.db.get_by_id(mid)["category_id"], child)
        self.assertEqual(self.db.get_chat_category(chat)["id"], child)
        self.assertEqual(api._webui._file_cache[path.name], str(moved))
        self.assertEqual(api._webui._file_cache["other.png"], "unrelated")
        api._cfg.set("virtual_drive_compat", True)
        with (
            mock.patch.object(Path, "resolve", side_effect=OSError("WinError 1005")),
            mock.patch("src.webui.request_manifest_rebuild"),
        ):
            result = api.move_hierarchy_node("category", source)
        self.assertTrue(result["ok"], result)
        self.assertTrue(path.exists())

    def test_hierarchy_category_move_rolls_back_if_database_update_fails(self):
        api, cache = self._hierarchy_move_api()
        source = self.db.create_category("source")
        target = self.db.create_category("target")
        path = cache / "source" / "original.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"keep")
        self.db.add_meme(path.name, category_id=source)
        api._webui._file_cache[path.name] = str(path)
        conn = self.db._get_conn()
        conn.execute(
            "CREATE TEMP TRIGGER reject_hierarchy BEFORE UPDATE OF parent_id ON categories "
            "BEGIN SELECT RAISE(ABORT, 'test update failure'); END"
        )
        with mock.patch("src.webui.request_manifest_rebuild") as rebuild:
            result = api.move_hierarchy_node("category", source, target)
        self.assertFalse(result["ok"])
        self.assertIsNone(self.db.get_category(source)["parent_id"])
        self.assertEqual(path.read_bytes(), b"keep")
        self.assertFalse((cache / "target" / "source").exists())
        self.assertEqual(api._webui._file_cache[path.name], str(path))
        rebuild.assert_not_called()
        conn.execute("DROP TRIGGER reject_hierarchy")

    def test_hierarchy_category_move_rejects_directory_conflict_and_missing_drive(self):
        api, cache = self._hierarchy_move_api()
        source = self.db.create_category("source")
        target = self.db.create_category("target")
        path = cache / "source" / "original.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"original")
        self.db.add_meme(path.name, category_id=source)
        conflict = cache / "target" / "source" / "occupied.png"
        conflict.parent.mkdir(parents=True)
        conflict.write_bytes(b"occupied")
        with mock.patch("src.webui.request_manifest_rebuild") as rebuild:
            result = api.move_hierarchy_node("category", source, target)
            self.assertFalse(result["ok"])
            self.assertEqual(path.read_bytes(), b"original")
            self.assertEqual(conflict.read_bytes(), b"occupied")
            self.assertIsNone(self.db.get_category(source)["parent_id"])
            with mock.patch("src.webui.os.replace", side_effect=OSError("move failed")):
                result = api.move_hierarchy_node("category", target, source)
            self.assertFalse(result["ok"])
            self.assertIsNone(self.db.get_category(target)["parent_id"])
            missing = self.db.create_category("missing")
            self.db.add_meme("unavailable.png", category_id=missing)
            result = api.move_hierarchy_node("category", missing, target)
            self.assertFalse(result["ok"])
            self.assertIn("目录不可用", result["error"])
            self.assertIsNone(self.db.get_category(missing)["parent_id"])
            rebuild.assert_not_called()

    def test_hierarchy_category_move_allows_empty_but_rejects_linked_paths(self):
        api, cache = self._hierarchy_move_api()
        source = self.db.create_category("empty")
        target = self.db.create_category("target")
        with mock.patch("src.webui.request_manifest_rebuild"):
            result = api.move_hierarchy_node("category", source, target)
            self.assertTrue(result["ok"], result)
            self.assertEqual(self.db.get_category(source)["parent_id"], target)
        path = cache / "linked"
        path.mkdir()
        linked = self.db.create_category("linked")
        with (
            mock.patch.object(Path, "is_symlink", return_value=True),
            mock.patch("src.webui.os.replace") as replace,
            mock.patch("src.webui.request_manifest_rebuild") as rebuild,
        ):
            result = api.move_hierarchy_node("category", linked, target)
        self.assertFalse(result["ok"])
        self.assertIn("链接目录", result["error"])
        self.assertIsNone(self.db.get_category(linked)["parent_id"])
        replace.assert_not_called()
        rebuild.assert_not_called()

    def test_hierarchy_category_failed_rollback_reports_actual_file_location(self):
        api, cache = self._hierarchy_move_api()
        source = self.db.create_category("source")
        target = self.db.create_category("target")
        path = cache / "source" / "original.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"preserved")
        api._webui._file_cache[path.name] = str(path)
        self.db._get_conn().execute(
            "CREATE TEMP TRIGGER reject_hierarchy BEFORE UPDATE OF parent_id ON categories "
            "BEGIN SELECT RAISE(ABORT, 'test update failure'); END"
        )
        original_replace = os.replace
        calls = []

        def replace(source_path, target_path):
            calls.append((source_path, target_path))
            if len(calls) > 1:
                raise OSError("rollback denied")
            original_replace(source_path, target_path)

        with mock.patch("src.webui.os.replace", side_effect=replace):
            result = api.move_hierarchy_node("category", source, target)
        moved = cache / "target" / "source" / path.name
        self.assertFalse(result["ok"])
        self.assertIn("回滚失败", result["error"])
        self.assertIn(str(moved.parent), result["error"])
        self.assertEqual(moved.read_bytes(), b"preserved")
        self.assertIsNone(self.db.get_category(source)["parent_id"])
        self.assertEqual(api._webui._file_cache[path.name], str(moved))

    def test_hierarchy_collection_move_never_touches_original_files(self):
        api, cache = self._hierarchy_move_api()
        source = self.db.create_collection("source")
        target = self.db.create_collection("target")
        with (
            mock.patch(
                "src.webui.guarded_read", side_effect=AssertionError("no throttle")
            ),
            mock.patch(
                "src.webui.os.replace", side_effect=AssertionError("no file moves")
            ),
            mock.patch("src.webui.request_manifest_rebuild") as rebuild,
        ):
            result = api.move_hierarchy_node("collection", source, target)
            self.assertTrue(result["ok"], result)
            same = api.move_hierarchy_node("collection", source, target)
            self.assertTrue(same["ok"])
            self.assertFalse(same["changed"])
            rebuild.assert_called_once_with()

    def test_category_delete_modes_promote_or_remove_subtree(self):
        parent = self.db.create_category("父类别")
        node = self.db.create_category("待删除", parent)
        child = self.db.create_category("保留子级", node)
        direct_meme = self.db.add_meme("category-direct.png")
        child_meme = self.db.add_meme("category-child.png")
        direct_chat = self._save_test_chat(101)
        child_chat = self._save_test_chat(102)
        self.db.set_meme_category(direct_meme, node)
        self.db.set_meme_category(child_meme, child)
        self.db.set_chat_category(direct_chat["id"], node)
        self.db.set_chat_category(child_chat["id"], child)

        promoted = self.db.delete_category(node, mode="promote")

        self.assertEqual(promoted["parent_id"], parent)
        self.assertEqual(promoted["deleted_ids"], [node])
        self.assertIsNone(self.db.get_category(node))
        self.assertEqual(self.db.get_category(child)["parent_id"], parent)
        self.assertEqual(self.db.get_meme_category(direct_meme)["id"], parent)
        self.assertEqual(self.db.get_meme_category(child_meme)["id"], child)
        self.assertEqual(self.db.get_chat_category(direct_chat["id"])["id"], parent)
        self.assertEqual(self.db.get_chat_category(child_chat["id"])["id"], child)

        cascade_node = self.db.create_category("删除子树", parent)
        cascade_child = self.db.create_category("一并删除", cascade_node)
        cascade_meme = self.db.add_meme("category-cascade.png")
        cascade_chat = self._save_test_chat(103)
        self.db.set_meme_category(cascade_meme, cascade_child)
        self.db.set_chat_category(cascade_chat["id"], cascade_node)

        cascaded = self.db.delete_category(cascade_node, mode="cascade")

        self.assertEqual(set(cascaded["deleted_ids"]), {cascade_node, cascade_child})
        self.assertIsNone(self.db.get_category(cascade_node))
        self.assertIsNone(self.db.get_category(cascade_child))
        self.assertEqual(self.db.get_meme_category(cascade_meme)["id"], parent)
        self.assertEqual(self.db.get_chat_category(cascade_chat["id"])["id"], parent)

    def test_collection_delete_modes_promote_or_remove_subtree(self):
        parent = self.db.create_collection("父分组")
        node = self.db.create_collection("待删除", parent)
        child = self.db.create_collection("保留子级", node)
        direct_meme = self.db.add_meme("collection-direct.png")
        child_meme = self.db.add_meme("collection-child.png")
        direct_chat = self._save_test_chat(201)
        child_chat = self._save_test_chat(202)
        self.db.add_to_collection(direct_meme, node)
        self.db.add_to_collection(child_meme, child)
        self.db.add_to_chat_collection(direct_chat["id"], node)
        self.db.add_to_chat_collection(child_chat["id"], child)

        promoted = self.db.delete_collection(node, mode="promote")

        self.assertEqual(promoted["parent_id"], parent)
        self.assertEqual(promoted["deleted_ids"], [node])
        self.assertEqual(
            next(item for item in self.db.get_collections() if item[0] == child)[2],
            parent,
        )
        self.assertEqual(
            [item["id"] for item in self.db.get_meme_collections(direct_meme)],
            [parent],
        )
        self.assertEqual(
            [item["id"] for item in self.db.get_meme_collections(child_meme)],
            [child],
        )
        self.assertEqual(
            [item["id"] for item in self.db.get_chat_collections(direct_chat["id"])],
            [parent],
        )
        self.assertEqual(
            [item["id"] for item in self.db.get_chat_collections(child_chat["id"])],
            [child],
        )

        cascade_node = self.db.create_collection("删除子树", parent)
        cascade_child = self.db.create_collection("一并删除", cascade_node)
        cascade_meme = self.db.add_meme("collection-cascade.png")
        cascade_chat = self._save_test_chat(203)
        self.db.add_to_collection(cascade_meme, cascade_child)
        self.db.add_to_chat_collection(cascade_chat["id"], cascade_node)

        cascaded = self.db.delete_collection(cascade_node, mode="cascade")

        self.assertEqual(set(cascaded["deleted_ids"]), {cascade_node, cascade_child})
        remaining_ids = {item[0] for item in self.db.get_collections()}
        self.assertNotIn(cascade_node, remaining_ids)
        self.assertNotIn(cascade_child, remaining_ids)
        self.assertEqual(
            [item["id"] for item in self.db.get_meme_collections(cascade_meme)],
            [parent],
        )
        self.assertEqual(
            [item["id"] for item in self.db.get_chat_collections(cascade_chat["id"])],
            [parent],
        )

    def test_category_delete_api_moves_files_for_each_mode(self):
        from src.webui import JsApi, WebUI

        cache = self.tmp_dir / "category-delete-cache"
        cfg = Config(self.tmp_dir / "category-delete-config.json")
        cfg.set("cache_dir", str(cache))
        webui = WebUI.__new__(WebUI)
        webui._cfg = cfg
        webui._file_cache = {}
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg
        api._webui = webui

        parent = self.db.create_category("父类别")
        promote_node = self.db.create_category("提升节点", parent)
        promote_child = self.db.create_category("保留子级", promote_node)
        empty_grandchild = self.db.create_category("空孙级", promote_child)
        direct = cache / "父类别" / "提升节点" / "direct.png"
        nested = cache / "父类别" / "提升节点" / "保留子级" / "nested.png"
        direct.parent.mkdir(parents=True)
        nested.parent.mkdir(parents=True)
        (cache / "父类别" / "提升节点" / "保留子级" / "空孙级").mkdir()
        direct.write_bytes(b"direct")
        nested.write_bytes(b"nested")
        direct_id = self.db.add_meme("direct.png")
        nested_id = self.db.add_meme("nested.png")
        self.db.set_meme_category(direct_id, promote_node)
        self.db.set_meme_category(nested_id, promote_child)

        with mock.patch("src.webui.request_manifest_rebuild") as rebuild:
            promoted = api.delete_category(promote_node, "promote")

        self.assertTrue(promoted["ok"])
        self.assertTrue((cache / "父类别" / "direct.png").is_file())
        self.assertTrue((cache / "父类别" / "保留子级" / "nested.png").is_file())
        self.assertTrue((cache / "父类别" / "保留子级" / "空孙级").is_dir())
        self.assertEqual(self.db.get_category(promote_child)["parent_id"], parent)
        self.assertEqual(
            self.db.get_category(empty_grandchild)["parent_id"], promote_child
        )
        rebuild.assert_called_once_with()

        cascade_node = self.db.create_category("删除节点", parent)
        cascade_child = self.db.create_category("删除子级", cascade_node)
        cascade_direct = cache / "父类别" / "删除节点" / "cascade-direct.png"
        cascade_nested = (
            cache / "父类别" / "删除节点" / "删除子级" / "cascade-nested.png"
        )
        cascade_direct.parent.mkdir(parents=True)
        cascade_nested.parent.mkdir(parents=True)
        cascade_direct.write_bytes(b"direct")
        cascade_nested.write_bytes(b"nested")
        cascade_direct_id = self.db.add_meme("cascade-direct.png")
        cascade_nested_id = self.db.add_meme("cascade-nested.png")
        self.db.set_meme_category(cascade_direct_id, cascade_node)
        self.db.set_meme_category(cascade_nested_id, cascade_child)

        with mock.patch("src.webui.request_manifest_rebuild") as rebuild:
            cascaded = api.delete_category(cascade_node, "cascade")

        self.assertTrue(cascaded["ok"])
        self.assertTrue((cache / "父类别" / "cascade-direct.png").is_file())
        self.assertTrue((cache / "父类别" / "cascade-nested.png").is_file())
        self.assertFalse((cache / "父类别" / "删除节点").exists())
        self.assertIsNone(self.db.get_category(cascade_child))
        rebuild.assert_called_once_with()

    def test_root_subtree_deletion_keeps_content_but_clears_assignment(self):
        category = self.db.create_category("根类别")
        subcategory = self.db.create_category("子类别", category)
        category_meme = self.db.add_meme("root-category.png")
        self.db.set_meme_category(category_meme, subcategory)

        collection = self.db.create_collection("根分组")
        subcollection = self.db.create_collection("子分组", collection)
        collection_meme = self.db.add_meme("root-collection.png")
        self.db.add_to_collection(collection_meme, subcollection)

        self.db.delete_category(category, mode="cascade")
        self.db.delete_collection(collection, mode="cascade")

        self.assertIsNone(self.db.get_by_id(category_meme)["category_id"])
        self.assertEqual(self.db.get_meme_collections(collection_meme), [])
        self.assertIsNotNone(self.db.get_by_id(category_meme))
        self.assertIsNotNone(self.db.get_by_id(collection_meme))

    def test_hierarchy_delete_mode_defaults_to_ask_and_can_be_saved(self):
        from src.webui import JsApi, WebUI

        cfg = Config(self.tmp_dir / "delete-mode-config.json")
        api = JsApi.__new__(JsApi)
        api._cfg = cfg

        self.assertEqual(api.get_hierarchy_delete_mode(), "ask")
        self.assertTrue(api.set_hierarchy_delete_mode("cascade")["ok"])
        self.assertEqual(api.get_hierarchy_delete_mode(), "cascade")
        self.assertFalse(api.set_hierarchy_delete_mode("invalid")["ok"])
        self.assertEqual(api.get_hierarchy_delete_mode(), "cascade")

    def test_recent_usage_automatic_setting_and_explicit_marking(self):
        from src.webui import JsApi

        meme_id = self.db.add_meme("recent-setting.png")
        cfg = Config(self.tmp_dir / "recent-setting-config.json")
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg

        self.assertTrue(api._record_recent_meme(meme_id))
        self.assertEqual(
            [row["id"] for row in self.db.get_recent()],
            [meme_id],
        )
        self.db.clear_recent()
        cfg.set("record_recent_use", False)
        self.assertFalse(api._record_recent_meme(meme_id))
        self.assertEqual(self.db.get_recent(), [])
        # 重复导入后的显式定位是用户要求的动作，不受自动记录开关影响。
        self.assertEqual(
            api.record_meme_uses([meme_id, meme_id, "invalid"]),
            {"ok": True, "ids": [meme_id]},
        )
        self.assertEqual([row["id"] for row in self.db.get_recent()], [meme_id])

    def test_recent_usage_frontend_refresh_contract(self):
        root = Path(__file__).resolve().parent.parent
        app = (root / "src" / "vue-src" / "App.vue").read_text(
            encoding="utf-8"
        )
        composable = (
            root / "src" / "vue-src" / "composables" / "useMemes.ts"
        ).read_text(encoding="utf-8")
        backend = (root / "src" / "webui.py").read_text(encoding="utf-8")

        recent_block = composable.split(
            "async function refreshRecentUsage", 1
        )[1].split("async function reorderMemes", 1)[0]
        self.assertIn("await refreshCollections()", recent_block)
        self.assertIn("state.activeCollection === -3", recent_block)
        self.assertIn("await search()", recent_block)
        self.assertIn("if (result?.ok) await refreshRecentUsage()", app)
        self.assertIn("void refreshRecentUsage()", app)
        self.assertIn("window.refreshRecent =", app)
        self.assertIn("window.refreshRecent&&window.refreshRecent()", backend)
        self.assertIn("async function revealExistingImports", app)
        self.assertIn("state.activeCollection = -3", app)
        self.assertIn("record_meme_uses", app)

    def test_chat_record_save_is_complete_and_idempotent(self):
        package = {
            "schema_version": 1,
            "title": "群聊记录",
            "source": {
                "self_id": 123,
                "message_id": 456,
                "message_seq": 7,
                "real_seq": 7007,
                "message_type": "group",
                "group_id": 789,
            },
            "forward_records": {
                "root": {
                    "messages": [
                        {"message": [{"type": "text", "data": {"text": "你好"}}]}
                    ]
                }
            },
            "summary": {"node_count": 1, "record_count": 1},
        }

        first = self.db.save_chat_record(package)
        package["summary"]["node_count"] = 2
        second = self.db.save_chat_record(package)

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.db.count_chat_records(), 1)
        self.assertEqual(
            self.db.get_chat_record(first["id"])["forward_records"],
            package["forward_records"],
        )
        latest = self.db.get_latest_chat_record()
        self.assertEqual(latest["title"], "群聊记录")
        self.assertEqual(latest["node_count"], 2)
        self.assertEqual(
            self.db.list_chat_records(limit=1)[0]["source_message_seq"],
            "7007",
        )
        conn = self.db._get_conn()
        conn.execute(
            "UPDATE chat_records SET source_message_seq='7' WHERE id=?",
            (first["id"],),
        )
        conn.commit()
        self.db._migrate(conn)
        self.assertEqual(
            self.db.list_chat_records(limit=1)[0]["source_message_seq"],
            "7007",
        )
        self.db.delete_all()
        self.assertEqual(self.db.count_chat_records(), 0)

    def test_chat_record_name_and_media_folder_are_editable_and_persistent(self):
        from src.webui import JsApi, WebUI

        package = {
            "schema_version": 1,
            "title": "原始聊天摘要",
            "source": {
                "self_id": 123,
                "message_id": 654,
                "message_type": "private",
                "user_id": 456,
            },
            "forward_records": {
                "root": {
                    "messages": [
                        {
                            "message": [
                                {
                                    "type": "image",
                                    "data": {},
                                }
                            ]
                        }
                    ]
                }
            },
            "summary": {"node_count": 1, "record_count": 1},
        }
        saved = self.db.save_chat_record(package)
        entry = self.db.get_chat_record_entry(saved["id"])
        old_folder = entry["media_folder"]
        cache = self.tmp_dir / "chat-name-cache"
        media = cache / "chat-record-media" / old_folder / "00001.gif"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"GIF89a-test")
        stored = self.db.get_chat_record(saved["id"])
        stored["forward_records"]["root"]["messages"][0]["message"][0][
            "data"
        ]["local_path"] = f"chat-record-media/{old_folder}/00001.gif"
        self.db.save_chat_record(stored)

        cfg = Config(self.tmp_dir / "chat-name-config.json")
        cfg.set("cache_dir", str(cache))
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg
        result = api.update_chat_record_details(
            saved["id"],
            {
                "name": "人工命名的聊天记录",
                "media_folder": "项目讨论记录",
                "tags": [],
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "人工命名的聊天记录")
        self.assertEqual(result["media_folder"], "项目讨论记录")
        self.assertFalse(media.parent.exists())
        renamed = cache / "chat-record-media" / "项目讨论记录" / "00001.gif"
        self.assertTrue(renamed.is_file())
        changed = self.db.get_chat_record_entry(saved["id"])
        self.assertEqual(changed["name"], "人工命名的聊天记录")
        self.assertEqual(changed["media_folder"], "项目讨论记录")
        changed_data = changed["package"]["forward_records"]["root"][
            "messages"
        ][0]["message"][0]["data"]
        self.assertEqual(
            changed_data["local_path"],
            "chat-record-media/项目讨论记录/00001.gif",
        )
        webui = WebUI.__new__(WebUI)
        webui._cfg = cfg
        with mock.patch("src.webui.get_db", return_value=self.db):
            preview_path = webui._get_chat_record_media_path(saved["id"], 1)
        self.assertTrue(os.path.samefile(preview_path, renamed))
        self.assertEqual(
            self.db.list_chat_records(keyword="人工命名")[0]["id"], saved["id"]
        )
        self.assertEqual(
            self.db.list_chat_records(keyword="项目讨论记录")[0]["id"], saved["id"]
        )

        refreshed = self.db.get_chat_record(saved["id"])
        refreshed["title"] = "重新读取的来源摘要"
        self.db.save_chat_record(refreshed)
        persisted = self.db.get_chat_record_entry(saved["id"])
        self.assertEqual(persisted["title"], "重新读取的来源摘要")
        self.assertEqual(persisted["name"], "人工命名的聊天记录")
        self.assertEqual(persisted["media_folder"], "项目讨论记录")

    def test_genre_filters_and_unified_library_keep_system_genres_separate(self):
        from types import SimpleNamespace

        from src.webui import JsApi

        image_id = self.db.add_meme("still.png", mime_type="image/png")
        gif_id = self.db.add_meme("moving.gif", mime_type="image/gif")
        package = {
            "schema_version": 1,
            "title": "项目讨论",
            "source": {
                "self_id": 123,
                "message_id": 456,
                "message_type": "group",
                "group_id": 789,
                "message": [{"type": "forward", "data": {"id": "root"}}],
            },
            "forward_records": {
                "root": {
                    "messages": [
                        {
                            "sender": {"nickname": "Alice"},
                            "message": [{"type": "text", "data": {"text": "确认方案"}}],
                        }
                    ]
                }
            },
            "summary": {
                "node_count": 1,
                "record_count": 1,
                "max_depth": 1,
                "media_count": 0,
            },
        }
        saved = self.db.save_chat_record(package)

        self.assertEqual(
            [row["id"] for row in self.db.search(genre="image")], [image_id]
        )
        self.assertEqual(
            [row["id"] for row in self.db.search(genre="gif")],
            [gif_id],
        )
        self.assertEqual(self.db.count(genre="image"), 1)
        self.assertEqual(self.db.count_chat_records("项目"), 1)

        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = Config(self.tmp_dir / "genre-config.json")
        api._webui = SimpleNamespace(_find_meme_file_for_row=lambda row: None)
        all_items = api.search_library(genre="all")
        self.assertEqual(all_items["total"], 3)
        self.assertEqual(
            [item["id"] for item in all_items["chat_records"]], [saved["id"]]
        )
        self.assertEqual(api.search_library(genre="gif")["memes"][0]["id"], gif_id)
        details = api.get_chat_record_details(saved["id"])
        self.assertTrue(details["ok"])
        self.assertEqual(details["genre_label"], "聊天记录")
        self.assertEqual(details["messages"][0]["preview"], "确认方案")
        self.assertEqual(details["send_command"], "/omm send 1")

    def test_library_genre_multiselect_unions_and_paginates_all_combinations(self):
        from itertools import combinations
        from types import SimpleNamespace

        from src.webui import JsApi

        image = self.db.add_meme("multiselect.png", tags=["共同"])
        gif = self.db.add_meme("multiselect.gif", mime_type="image/gif", tags=["共同"])
        chats = [self._save_test_chat(9201), self._save_test_chat(9202)]
        group = self.db.create_collection("多选范围")
        child = self.db.create_collection("子分组", group)
        category = self.db.create_category("多选类别")
        for meme_id in (image, gif):
            self.db.add_to_collection(meme_id, child)
            self.db.set_meme_category(meme_id, category)
        for chat in chats:
            self.db.add_to_chat_collection(chat["id"], child)
            self.db.set_chat_category(chat["id"], category)
            self.db.update_chat_record_details(chat["id"], ["共同"])
        hidden = self.db.ensure_hidden_collection()
        hidden_meme = self.db.add_meme("excluded.gif", mime_type="image/gif")
        self.db.add_to_collection(hidden_meme, hidden)
        hidden_chat = self._save_test_chat(9203)
        self.db.add_to_chat_collection(hidden_chat["id"], hidden)
        self.db.add_meme("carrier.gif", stego_of_hash="source")
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = Config(self.tmp_dir / "multiselect-config.json")
        api._webui = SimpleNamespace()

        for size in range(4):
            for selected in combinations(("image", "gif", "chat_record"), size):
                genres = list(selected)
                with self.subTest(genres=genres):
                    expected_memes = set()
                    if "image" in genres:
                        expected_memes.add(image)
                    if "gif" in genres:
                        expected_memes.add(gif)
                    expected_chats = (
                        {chat["id"] for chat in chats}
                        if "chat_record" in genres
                        else set()
                    )
                    result = api.search_library(genre=genres)
                    total = len(expected_memes) + len(expected_chats)
                    self.assertEqual(result["total"], total)
                    self.assertEqual(result["chat_total"], len(expected_chats))
                    self.assertEqual(
                        {row["id"] for row in result["memes"]}, expected_memes
                    )
                    self.assertEqual(
                        {row["id"] for row in result["chat_records"]}, expected_chats
                    )
                    self.assertEqual(api.count_library(genre=genres), total)
                    ordered = [
                        ("chat", row["id"]) for row in result["chat_records"]
                    ] + [("meme", row["id"]) for row in result["memes"]]
                    paged = []
                    for offset in range(0, total + 2, 2):
                        page = api.search_library(genre=genres, offset=offset, limit=2)
                        self.assertEqual(page["total"], total)
                        paged.extend(
                            ("chat", row["id"]) for row in page["chat_records"]
                        )
                        paged.extend(("meme", row["id"]) for row in page["memes"])
                    self.assertEqual(paged, ordered)
                    scoped = api.search_library(
                        genre=genres,
                        tags=["共同"],
                        collection_id=group,
                        category_id=category,
                    )
                    self.assertEqual(scoped["total"], total)
                    legacy = api.search_library(genre="all" if size == 3 else genres)
                    self.assertEqual(legacy["total"], total)

        self.db.toggle_favorite(gif)
        self.db.record_use(gif)
        for special in (-2, -3, -5):
            result = api.search_library(
                collection_id=special, genre=["gif", "chat_record"]
            )
            self.assertEqual([row["id"] for row in result["memes"]], [gif])
            self.assertEqual(result["chat_records"], [])
            self.assertEqual(
                api.search_library(collection_id=special, genre=[])["total"], 0
            )
        self.assertEqual(
            api.search_library(genre=["image", "chat_record"], keyword="multiselect")[
                "total"
            ],
            1,
        )

    def test_multiselect_page_cache_warmup_matches_later_library_pages(self):
        from types import SimpleNamespace

        from src.webui import JsApi

        for index in range(5):
            self.db.add_meme(f"prefetch-image-{index}.png")
            self.db.add_meme(f"prefetch-gif-{index}.gif", mime_type="image/gif")
        for index in range(3):
            self._save_test_chat(9300 + index)
        warmed = []
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = Config(self.tmp_dir / "multiselect-prefetch-config.json")
        api._cfg.set("bulk_read_protection", True)
        api._cfg.set("bulk_read_prefetch_pages", True)
        api._webui = SimpleNamespace(
            _get_thumbnail_path=lambda meme_id, filename, read_priority: warmed.append(
                meme_id
            )
        )

        class ImmediateThread:
            def __init__(self, target, args, **_kwargs):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        with (
            mock.patch("src.webui.threading.Thread", ImmediateThread),
            mock.patch("src.webui.time.sleep"),
        ):
            for genres in (
                ["image", "chat_record"],
                ["gif", "chat_record"],
                ["image", "gif"],
                ["image", "gif", "chat_record"],
                ["chat_record"],
                [],
            ):
                with self.subTest(genres=genres):
                    warmed.clear()
                    total = api.count_library(genre=genres)
                    expected = []
                    for offset in range(2, total, 2):
                        expected.extend(
                            row["id"]
                            for row in api.search_library(
                                genre=genres, offset=offset, limit=2
                            )["memes"]
                        )
                    result = api.start_page_cache_warmup(
                        page_size=2, total=total, genre=genres
                    )
                    self.assertEqual(warmed, expected)
                    self.assertEqual(result["started"], bool(expected))

    def test_chat_record_details_persist_selective_send_filters(self):
        from src.webui import JsApi

        package = {
            "schema_version": 1,
            "title": "可筛选记录",
            "source": {
                "self_id": 123,
                "message_id": 456,
                "message_type": "group",
                "group_id": 789,
                "message": [{"type": "forward", "data": {"id": "root"}}],
            },
            "forward_records": {
                "root": {
                    "messages": [
                        {
                            "sender": {"nickname": "Alice"},
                            "message": [{"type": "text", "data": {"text": "保留"}}],
                        },
                        {
                            "sender": {"nickname": "Ad"},
                            "message": [{"type": "text", "data": {"text": "广告"}}],
                        },
                    ]
                }
            },
            "summary": {
                "node_count": 2,
                "record_count": 1,
                "max_depth": 1,
                "media_count": 0,
            },
        }
        saved = self.db.save_chat_record(package)
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = Config(self.tmp_dir / "chat-filter-config.json")

        before = api.get_chat_record_details(saved["id"])
        self.assertEqual(
            [message["path"] for message in before["messages"]], ["n0", "n1"]
        )
        result = api.update_chat_record_details(
            saved["id"],
            {
                "tags": [],
                "blocked_message_paths": ["n1"],
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["blocked_message_paths"], ["n1"])
        self.assertEqual(result["blocked_message_count"], 1)
        stored = self.db.get_chat_record(saved["id"])
        self.assertEqual(
            stored["send_filter"]["blocked_node_paths"], ["n1"]
        )
        after = api.get_chat_record_details(saved["id"])
        self.assertEqual(after["blocked_message_paths"], ["n1"])

        invalid = api.update_chat_record_details(
            saved["id"],
            {"tags": [], "blocked_message_paths": ["../../other"]},
        )
        self.assertFalse(invalid["ok"])
        self.assertEqual(
            self.db.get_chat_record(saved["id"])["send_filter"][
                "blocked_node_paths"
            ],
            ["n1"],
        )

    def test_page_cache_warmup_processes_only_later_filtered_pages(self):
        from types import SimpleNamespace

        from src.webui import JsApi

        group_id = self.db.create_collection("预热分组")
        for index in range(5):
            meme_id = self.db.add_meme(
                f"warm-{index}.png", original_name=f"目标 {index}"
            )
            self.db.add_to_collection(meme_id, group_id)
        self.db.add_meme("outside.png", original_name="其他")

        warmed = []
        cfg = Config(self.tmp_dir / "warmup-config.json")
        cfg.set("bulk_read_protection", True)
        cfg.set("bulk_read_prefetch_pages", True)
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg
        api._webui = SimpleNamespace(
            _get_thumbnail_path=lambda meme_id, filename, read_priority: warmed.append(
                (meme_id, filename, read_priority)
            )
        )

        class ImmediateThread:
            def __init__(self, target, args, **_kwargs):
                self.target = target
                self.args = args

            def start(self):
                self.target(*self.args)

        all_rows = api._search_meme_rows("目标", [], group_id, 0, 99)
        expected = [(row["id"], row["filename"], "background") for row in all_rows[2:]]
        with (
            mock.patch("src.webui.threading.Thread", ImmediateThread),
            mock.patch("src.webui.time.sleep"),
        ):
            result = api.start_page_cache_warmup(
                "目标", [], group_id, 0, 2, len(all_rows)
            )

        self.assertTrue(result["started"])
        self.assertEqual(warmed, expected)

    def test_webp_list_search_never_probes_virtual_drive_original(self):
        from types import SimpleNamespace

        from src.webui import JsApi

        meme_id = self.db.add_meme(
            "static.webp",
            mime_type="image/webp",
            original_name="本地副本缺失",
        )
        cfg = Config(self.tmp_dir / "webp-search-config.json")
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg
        api._webui = SimpleNamespace(
            _find_meme_file_for_row=mock.Mock(
                side_effect=AssertionError("列表查询不应读取网盘原件")
            )
        )

        rows = api.search_memes(limit=10)

        self.assertEqual([row["id"] for row in rows], [meme_id])
        self.assertFalse(rows[0]["is_animated"])
        api._webui._find_meme_file_for_row.assert_not_called()

    def test_meme_list_includes_sidebar_membership_metadata_in_one_batch(self):
        from types import SimpleNamespace

        from src.webui import JsApi

        first_id = self.db.add_meme("first.png", mime_type="image/png")
        second_id = self.db.add_meme("second.png", mime_type="image/png")
        category_id = self.db.create_category("角色")
        first_collection = self.db.create_collection("常用")
        second_collection = self.db.create_collection("爱丽丝", first_collection)
        self.db.set_meme_category(first_id, category_id)
        self.db.add_memes_to_collection([first_id], first_collection)
        self.db.add_memes_to_collection([first_id], second_collection)
        self.db.record_use(first_id)

        membership = self.db.get_meme_collection_ids_map(
            [first_id, second_id, first_id]
        )
        self.assertEqual(
            membership,
            {
                first_id: [first_collection, second_collection],
                second_id: [],
            },
        )

        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = Config(self.tmp_dir / "sidebar-hover-config.json")
        api._webui = SimpleNamespace()
        rows = {row["id"]: row for row in api.search_memes(limit=10)}

        self.assertEqual(rows[first_id]["category_id"], category_id)
        self.assertTrue(rows[first_id]["recently_used"])
        self.assertEqual(
            rows[first_id]["collection_ids"],
            [first_collection, second_collection],
        )
        self.assertIsNone(rows[second_id]["category_id"])
        self.assertEqual(rows[second_id]["collection_ids"], [])
        self.assertFalse(rows[second_id]["recently_used"])

    def test_sidebar_virtual_roots_put_ungrouped_first_and_uncategorized_after_all(
        self,
    ):
        from src.webui import JsApi

        self.db.add_meme("sidebar-all.png", mime_type="image/png")
        self._save_test_chat(9081)
        self.db.create_category("角色")
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = Config(self.tmp_dir / "category-all-config.json")

        categories = api.get_categories()
        collections = api.get_collections()

        self.assertEqual(
            [(item["id"], item["name"]) for item in categories[:2]],
            [(-1, "全部"), (-4, "未分类")],
        )
        self.assertEqual(categories[0]["count"], 2)
        self.assertEqual(categories[1]["count"], 2)
        self.assertEqual(collections[0]["id"], -4)
        self.assertEqual(collections[0]["name"], "未分组")
        self.assertTrue(any(item["name"] == "角色" for item in categories))

        api._cfg.set("show_uncategorized", False)
        self.assertEqual(
            [(item["id"], item["name"]) for item in api.get_categories()[:2]],
            [(-1, "全部"), (-4, "未分类")],
        )
        self.assertEqual(api.get_collections()[0]["id"], -4)
        self.assertEqual(api.get_collections()[0]["count"], 2)
        self.assertEqual(
            api.get_init_data()["collections"], api.get_collections()
        )
        self.assertEqual(api.search_library(collection_id=-4)["total"], 2)

    def test_random_page_samples_one_page_and_only_rerolls_explicitly(self):
        from types import SimpleNamespace

        from src.webui import MEME_PAGE, JsApi

        ids = [
            self.db.add_meme(f"random-{index}.png", mime_type="image/png")
            for index in range(MEME_PAGE + 12)
        ]
        hidden_root = self.db.ensure_hidden_collection()
        hidden_child = self.db.create_collection("随机排除", hidden_root)
        self.db.add_memes_to_collection(ids[-2:], hidden_child)
        self.db.add_meme("random-carrier.gif", stego_of_hash="source-hash")
        self._save_test_chat(9083)
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = Config(self.tmp_dir / "random-page-config.json")
        api._webui = SimpleNamespace()

        with mock.patch.object(self.db, "search", wraps=self.db.search) as query:
            self.assertEqual(api.get_init_data()["collections"], api.get_collections())
            self.assertFalse(
                any(call.kwargs.get("random_order") for call in query.call_args_list)
            )
            first = api.search_library(collection_id=-5)
            first_ids = [row["id"] for row in first["memes"]]
            self.assertEqual(first["total"], MEME_PAGE)
            self.assertEqual(len(set(first_ids)), MEME_PAGE)
            self.assertTrue(set(first_ids).issubset(set(ids[:-2])))
            self.assertEqual(first["chat_records"], [])
            self.assertEqual(first["chat_total"], 0)

            api.search_library()
            api.search_library(collection_id=-4)
            api.get_collections()
            self.db.toggle_favorite(first_ids[0])
            returned = api.search_library(collection_id=-5)
            self.assertEqual([row["id"] for row in returned["memes"]], first_ids)
            self.assertTrue(returned["memes"][0]["favorited"])
            self.assertEqual(
                sum(
                    bool(call.kwargs.get("random_order"))
                    for call in query.call_args_list
                ),
                1,
            )
            api.search_library(collection_id=-5, refresh_random=True)
            self.assertEqual(
                sum(
                    bool(call.kwargs.get("random_order"))
                    for call in query.call_args_list
                ),
                2,
            )

        sampled = api.search_library(collection_id=-5)["memes"]
        self.db.delete_meme(sampled[0]["id"])
        self.db.add_to_collection(sampled[1]["id"], hidden_child)
        remaining = api.search_library(collection_id=-5)
        self.assertEqual(remaining["total"], MEME_PAGE - 2)
        self.assertEqual(
            [row["id"] for row in remaining["memes"]],
            [row["id"] for row in sampled[2:]],
        )
        self.assertEqual(
            api.search_library(collection_id=-5, refresh_random=True)["total"],
            MEME_PAGE,
        )

    def test_random_page_handles_empty_small_filtered_and_hidden_libraries(self):
        from types import SimpleNamespace

        from src.webui import JsApi

        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = Config(self.tmp_dir / "random-small-config.json")
        api._webui = SimpleNamespace()
        self.assertEqual(api.search_library(collection_id=-5)["total"], 0)
        image = self.db.add_meme("small.png", mime_type="image/png")
        gif = self.db.add_meme("small.gif", mime_type="image/gif")
        self.db.set_meme_tags(gif, ["动画"])
        hidden = self.db.add_meme("private.png", mime_type="image/png")
        self.db.add_to_collection(hidden, self.db.ensure_hidden_collection())
        self.assertEqual(api.search_library(collection_id=-5)["total"], 0)

        result = api.search_library(collection_id=-5, refresh_random=True)
        ids = [row["id"] for row in result["memes"]]
        self.assertEqual(set(ids), {image, gif})
        self.assertEqual(result["total"], 2)
        self.assertEqual(
            api.search_library(collection_id=-5, offset=1, limit=1)["memes"][0]["id"],
            ids[1],
        )
        self.assertEqual(api.search_library(collection_id=-5, offset=2)["memes"], [])
        for filters in ({"genre": "gif"}, {"tags": ["动画"]}, {"keyword": "small.gif"}):
            filtered = api.search_library(collection_id=-5, **filters)
            self.assertEqual(filtered["total"], 1)
            self.assertEqual([row["id"] for row in filtered["memes"]], [gif])
        self.assertEqual(
            api.search_library(collection_id=-5, genre="chat_record")["total"], 0
        )
        self.assertEqual(
            [row["id"] for row in api.search_library(collection_id=-5)["memes"]], ids
        )

        api._cfg.set("hidden_collection_enabled", False)
        result = api.search_library(collection_id=-5, refresh_random=True)
        self.assertEqual({row["id"] for row in result["memes"]}, {image, gif, hidden})
        api._cfg.set("hidden_collection_enabled", True)
        self.assertEqual(api.search_library(collection_id=-5)["total"], 2)
        self.assertEqual(
            next(row for row in api.get_collections() if row["id"] == -5)["count"], 2
        )
        self.assertFalse(api.delete_collection(-5)["ok"])
        self.assertFalse(api.rename_collection(-5, "别的名字"))

    def test_hidden_collection_root_is_stable_and_protected(self):
        existing = self.db.create_collection("隐藏")
        hidden_id = self.db.ensure_hidden_collection()

        self.assertEqual(hidden_id, existing)
        self.assertEqual(self.db.ensure_hidden_collection(), hidden_id)
        self.assertTrue(self.db.is_hidden_collection_root(hidden_id))
        with self.assertRaisesRegex(ValueError, "不能重命名"):
            self.db.rename_collection(hidden_id, "别的名字")
        with self.assertRaisesRegex(ValueError, "不能删除"):
            self.db.delete_collection(hidden_id)

        child_id = self.db.create_collection("可删除子分组", hidden_id)
        result = self.db.delete_collection(child_id)
        self.assertIn(child_id, result["deleted_ids"])
        self.assertTrue(self.db.is_hidden_collection_root(hidden_id))

    def test_hidden_collection_filters_all_views_until_effect_is_disabled(self):
        from types import SimpleNamespace

        from src.webui import JsApi

        visible_id = self.db.add_meme(
            "visible.png", mime_type="image/png", original_name="共同关键词"
        )
        hidden_id = self.db.add_meme(
            "hidden.png", mime_type="image/png", original_name="共同关键词"
        )
        dual_id = self.db.add_meme(
            "dual.png", mime_type="image/png", original_name="共同关键词"
        )
        hidden_root = self.db.ensure_hidden_collection()
        hidden_child = self.db.create_collection("私密", hidden_root)
        normal_group = self.db.create_collection("普通")
        self.db.add_memes_to_collection([hidden_id, dual_id], hidden_child)
        self.db.add_memes_to_collection([visible_id, dual_id], normal_group)
        self.db.toggle_favorite(hidden_id)
        self.db.record_use(hidden_id)
        hidden_chat = self._save_test_chat(9082)
        self.db.add_to_chat_collection(hidden_chat["id"], hidden_child)

        cfg = Config(self.tmp_dir / "hidden-collection-config.json")
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg
        api._webui = SimpleNamespace()

        self.assertEqual(
            [row["id"] for row in api.search_memes(keyword="共同关键词")],
            [visible_id],
        )
        self.assertEqual(
            [row["id"] for row in api.search_memes(collection_id=normal_group)],
            [visible_id],
        )
        self.assertEqual(api.search_memes(collection_id=-2), [])
        self.assertEqual(api.search_memes(collection_id=-3), [])
        self.assertEqual(
            {row["id"] for row in api.search_memes(collection_id=hidden_root)},
            {hidden_id, dual_id},
        )
        self.assertEqual(
            {row["id"] for row in api.search_memes(collection_id=hidden_child)},
            {hidden_id, dual_id},
        )
        self.assertEqual(api.search_library()["chat_records"], [])
        self.assertEqual(
            [row["id"] for row in api.search_library(
                collection_id=hidden_root
            )["chat_records"]],
            [hidden_chat["id"]],
        )
        collections = api.get_collections()
        hidden_node = next(item for item in collections if item["id"] == hidden_root)
        normal_node = next(item for item in collections if item["id"] == normal_group)
        self.assertTrue(hidden_node["system_hidden"])
        self.assertEqual(hidden_node["count"], 3)
        self.assertEqual(normal_node["count"], 1)
        self.assertEqual(api.get_categories()[0]["count"], 1)

        cfg.set("hidden_collection_enabled", False)
        self.assertEqual(
            {row["id"] for row in api.search_memes(keyword="共同关键词")},
            {visible_id, hidden_id, dual_id},
        )
        self.assertEqual(
            {row["id"] for row in api.search_memes(collection_id=normal_group)},
            {visible_id, dual_id},
        )
        self.assertEqual(
            [row["id"] for row in api.search_library()["chat_records"]],
            [hidden_chat["id"]],
        )
        normal_node = next(
            item for item in api.get_collections() if item["id"] == normal_group
        )
        self.assertEqual(normal_node["count"], 2)
        self.assertEqual(api.get_categories()[0]["count"], 4)

    def test_page_cache_warmup_disabled_cancels_previous_generation(self):
        from types import SimpleNamespace

        from src.webui import JsApi

        cfg = Config(self.tmp_dir / "warmup-disabled-config.json")
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg
        api._webui = SimpleNamespace()
        api._ensure_page_cache_state()
        previous_generation = api._page_cache_generation

        result = api.start_page_cache_warmup(total=500)

        self.assertFalse(result["started"])
        self.assertGreater(api._page_cache_generation, previous_generation)
        self.assertFalse(api._page_cache_is_current(previous_generation))

    def test_chat_record_table_is_restored_by_migration(self):
        candidate = self.tmp_dir / "old-chat-backup.db"
        self.db.backup_to(str(candidate))
        conn = sqlite3.connect(candidate)
        try:
            conn.execute("DROP TABLE chat_records")
            conn.commit()
        finally:
            conn.close()

        self.db.prepare_restore_source(str(candidate))
        conn = sqlite3.connect(candidate)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='chat_records'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)

    def test_add_meme(self):
        mid = self.db.add_meme(
            "test.png",
            file_hash="abc123",
            width=100,
            height=200,
            ai_description="一只挥手的小猫",
            ai_ocr_text="你好",
        )
        self.assertIsNotNone(mid)
        meme = self.db.get_by_id(mid)
        self.assertEqual(meme["filename"], "test.png")
        self.assertEqual(meme["file_hash"], "abc123")
        self.assertEqual(meme["ai_description"], "一只挥手的小猫")
        self.assertEqual(meme["ai_ocr_text"], "你好")

    def test_delete_meme(self):
        mid = self.db.add_meme("test.png")
        self.db.delete_meme(mid)
        self.assertIsNone(self.db.get_by_id(mid))

    def test_meme_details_includes_tags_and_ai_metadata(self):
        from src.webui import JsApi

        mid = self.db.add_meme(
            "detail.png",
            width=320,
            height=180,
            original_name="详情测试",
            tags=["问候", "角色"],
            ai_description="角色正在挥手",
            ai_ocr_text="你好",
        )
        api = JsApi.__new__(JsApi)
        api._db = self.db
        parent = self.db.create_collection("角色")
        child = self.db.create_collection("爱丽丝", parent_id=parent)
        other = self.db.create_collection("问候场景")
        self.db.add_to_collection(mid, child)
        self.db.add_to_collection(mid, other)

        detail = api.get_meme_details(mid)
        self.assertEqual(detail["name"], "详情测试")
        self.assertEqual(detail["description"], "角色正在挥手")
        self.assertEqual(detail["ocr_text"], "你好")
        self.assertEqual(detail["manual_description"], "")
        self.assertEqual(detail["manual_ocr_text"], "")
        self.assertEqual(detail["ai_description"], "角色正在挥手")
        self.assertEqual(detail["ai_ocr_text"], "你好")
        self.assertEqual(set(detail["tags"]), {"问候", "角色"})
        self.assertEqual(set(detail["all_tags"]), {"问候", "角色"})
        self.assertIsNone(detail["previous_id"])
        self.assertIsNone(detail["next_id"])
        self.assertEqual(detail["position"], 1)
        self.assertEqual(detail["total"], 1)
        self.assertEqual(
            {item["path"] for item in detail["collections"]},
            {"角色 / 爱丽丝", "问候场景"},
        )
        self.assertEqual(
            {item["path"] for item in detail["all_collections"]},
            {"角色", "角色 / 爱丽丝", "问候场景"},
        )

    def test_update_meme_details_saves_user_text_and_tags(self):
        from src.webui import JsApi

        mid = self.db.add_meme(
            "editable.png",
            tags=["旧标签"],
            ai_description="旧描述",
            ai_ocr_text="旧文字",
        )
        api = JsApi.__new__(JsApi)
        api._db = self.db

        result = api.update_meme_details(
            mid,
            {
                "tags": ["新标签", "角色"],
                "description": "用户修改后的描述",
                "ocr_text": "用户校正后的图片文字",
            },
        )

        self.assertTrue(result["ok"])
        detail = api.get_meme_details(mid)
        self.assertEqual(detail["description"], "用户修改后的描述")
        self.assertEqual(detail["ocr_text"], "用户校正后的图片文字")
        self.assertEqual(set(detail["tags"]), {"新标签", "角色"})
        self.assertNotIn("旧标签", self.db.get_all_tags())

    def test_manual_and_ai_text_fields_are_independently_editable(self):
        from src.webui import JsApi

        mid = self.db.add_meme(
            "split-text.png",
            ai_description="原 AI 描述",
            ai_ocr_text="原 AI 文字",
        )
        api = JsApi.__new__(JsApi)
        api._db = self.db

        result = api.update_meme_details(
            mid,
            {
                "tags": [],
                "manual_description": "人工描述",
                "manual_ocr_text": "人工文字",
                "ai_description": "修正后的 AI 描述",
                "ai_ocr_text": "",
            },
        )

        self.assertTrue(result["ok"])
        detail = api.get_meme_details(mid)
        self.assertEqual(detail["manual_description"], "人工描述")
        self.assertEqual(detail["manual_ocr_text"], "人工文字")
        self.assertEqual(detail["ai_description"], "修正后的 AI 描述")
        self.assertEqual(detail["ai_ocr_text"], "")
        self.assertEqual(detail["description"], "人工描述")
        self.assertEqual(detail["ocr_text"], "人工文字")

    def test_update_meme_details_edits_collection_memberships(self):
        from src.webui import JsApi

        mid = self.db.add_meme("group-edit.png")
        old_group = self.db.create_collection("旧分组")
        parent = self.db.create_collection("角色")
        new_group = self.db.create_collection("爱丽丝", parent_id=parent)
        self.db.add_to_collection(mid, old_group)
        api = JsApi.__new__(JsApi)
        api._db = self.db

        with (
            mock.patch(
                "src.webui.update_manifest_meme_structure", return_value=True
            ) as incremental,
            mock.patch("src.webui.build_manifest") as build,
        ):
            result = api.update_meme_details(
                mid,
                {
                    "tags": [],
                    "description": "",
                    "ocr_text": "",
                    "collection_ids": [new_group],
                },
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["collections"],
            [{"id": new_group, "name": "爱丽丝", "path": "角色 / 爱丽丝"}],
        )
        self.assertEqual(self.db.search(collection_id=old_group), [])
        self.assertEqual(
            [row["id"] for row in self.db.search(collection_id=new_group)], [mid]
        )
        incremental.assert_called_once_with(mid, "group-edit.png")
        build.assert_not_called()

    def test_drag_assignment_adds_meme_and_chat_record_to_collection(self):
        from src.webui import JsApi

        meme_id = self.db.add_meme("drag-group.png", tags=["保留标签"])
        saved = self.db.save_chat_record(
            {
                "schema_version": 1,
                "title": "拖拽聊天记录",
                "source": {
                    "self_id": 1,
                    "message_id": 2,
                    "message_type": "private",
                    "user_id": 3,
                },
                "forward_records": {},
                "summary": {"node_count": 1, "record_count": 1},
            }
        )
        collection_id = self.db.create_collection("拖放分组")
        api = JsApi.__new__(JsApi)
        api._db = self.db

        with (
            mock.patch(
                "src.webui.update_manifest_meme_structure", return_value=True
            ) as incremental,
            mock.patch("src.webui.request_manifest_rebuild") as rebuild,
        ):
            meme_result = api.assign_library_item(
                "meme", meme_id, "collection", collection_id
            )
            chat_result = api.assign_library_item(
                "chat_record", saved["id"], "collection", collection_id
            )

        self.assertTrue(meme_result["ok"])
        self.assertTrue(chat_result["ok"])
        self.assertEqual(
            [item["id"] for item in self.db.get_meme_collections(meme_id)],
            [collection_id],
        )
        self.assertEqual(
            [item["id"] for item in self.db.get_chat_collections(saved["id"])],
            [collection_id],
        )
        self.assertEqual(self.db.get_meme_tags(meme_id), ["保留标签"])
        incremental.assert_called_once_with(meme_id, "drag-group.png")
        rebuild.assert_called_once_with()

    def test_drag_assignment_moves_meme_file_and_switches_categories(self):
        from types import SimpleNamespace

        from src.webui import JsApi

        old_path = self.tmp_dir / "drag-category.png"
        new_path = self.tmp_dir / "角色" / "drag-category.png"
        meme_id = self.db.add_meme("drag-category.png", tags=["保留标签"])
        saved = self.db.save_chat_record(
            {
                "schema_version": 1,
                "title": "类别聊天记录",
                "source": {
                    "self_id": 1,
                    "message_id": 4,
                    "message_type": "private",
                    "user_id": 5,
                },
                "forward_records": {},
                "summary": {"node_count": 1, "record_count": 1},
            }
        )
        category_id = self.db.create_category("角色")
        mover = mock.Mock(return_value=(old_path, new_path))
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._webui = SimpleNamespace(
            _move_meme_file=mover,
            _file_cache={"drag-category.png": str(old_path)},
        )

        with (
            mock.patch(
                "src.webui.update_manifest_meme_structure", return_value=True
            ),
            mock.patch("src.webui.request_manifest_rebuild") as rebuild,
        ):
            meme_result = api.assign_library_item(
                "meme", meme_id, "category", category_id
            )
            chat_result = api.assign_library_item(
                "chat_record", saved["id"], "category", category_id
            )

        self.assertTrue(meme_result["ok"])
        self.assertTrue(chat_result["ok"])
        self.assertEqual(self.db.get_by_id(meme_id)["category_id"], category_id)
        self.assertEqual(
            self.db.get_chat_record_entry(saved["id"])["category_id"], category_id
        )
        self.assertEqual(self.db.get_meme_tags(meme_id), ["保留标签"])
        mover.assert_called_once()
        self.assertEqual(api._webui._file_cache["drag-category.png"], str(new_path))
        rebuild.assert_called_once_with()

    def _batch_category_api(self):
        from src.webui import JsApi, WebUI

        cfg = Config(self.tmp_dir / "batch-category-config.json")
        cfg.set("cache_dir", str(self.tmp_dir / "batch-category-cache"))
        cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        ui = WebUI.__new__(WebUI)
        ui._db = self.db
        ui._cfg = cfg
        ui._file_cache = {}
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg
        api._webui = ui
        db_patch = mock.patch("src.webui.get_db", return_value=self.db)
        db_patch.start()
        self.addCleanup(db_patch.stop)
        return api, cfg.cache_dir

    def test_batch_category_moves_files_and_preserves_groups_and_tags(self):
        api, cache = self._batch_category_api()
        parent = self.db.create_category("角色")
        category = self.db.create_category("爱丽丝", parent)
        group = self.db.create_collection("保留分组")
        ids = []
        for name in ("batch-first.png", "batch-second.gif", "batch-unselected.png"):
            (cache / name).write_bytes(name.encode())
            mid = self.db.add_meme(name, tags=["保留标签"])
            self.db.add_to_collection(mid, group)
            ids.append(mid)
        with (
            mock.patch("src.webui.request_manifest_rebuild") as rebuild,
            mock.patch("src.webui.build_manifest") as build,
        ):
            result = api.batch_set_meme_category([ids[0], ids[1], ids[0]], category)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        for mid in ids[:2]:
            row = self.db.get_by_id(mid)
            target = cache / "角色" / "爱丽丝" / row["filename"]
            self.assertTrue(target.is_file())
            self.assertFalse((cache / row["filename"]).exists())
            self.assertEqual(row["category_id"], category)
            self.assertEqual(self.db.get_meme_tags(mid), ["保留标签"])
            self.assertEqual(self.db.get_meme_collections(mid)[0]["id"], group)
            self.assertEqual(api._webui._file_cache[row["filename"]], str(target))
        self.assertIsNone(self.db.get_by_id(ids[2])["category_id"])
        self.assertTrue((cache / "batch-unselected.png").is_file())
        rebuild.assert_called_once_with()
        build.assert_not_called()
        with mock.patch("src.webui.request_manifest_rebuild"):
            cleared = api.batch_set_meme_category(ids[:2], 0)
        self.assertTrue(cleared["ok"])
        for mid in ids[:2]:
            row = self.db.get_by_id(mid)
            self.assertIsNone(row["category_id"])
            self.assertTrue((cache / row["filename"]).is_file())

    def test_batch_category_creates_path_and_reuses_existing_category(self):
        api, cache = self._batch_category_api()
        mid = self.db.add_meme("batch-new.png")
        (cache / "batch-new.png").write_bytes(b"image")
        with mock.patch("src.webui.request_manifest_rebuild"):
            created = api.batch_set_meme_category([mid], None, "新父级/新子级")
            reused = api.batch_set_meme_category([mid], None, "新父级/新子级")
        self.assertTrue(created["ok"])
        self.assertEqual(created["category_id"], reused["category_id"])
        self.assertEqual(len(self.db.get_categories()), 2)
        self.assertTrue((cache / "新父级" / "新子级" / "batch-new.png").is_file())

    def test_batch_category_rolls_back_files_on_move_or_commit_failure(self):
        api, cache = self._batch_category_api()
        category = self.db.create_category("目标")
        first = self.db.add_meme("batch-rollback-first.png")
        second = self.db.add_meme("batch-rollback-second.png")
        first_path = cache / "batch-rollback-first.png"
        second_path = cache / "batch-rollback-second.png"
        first_path.write_bytes(b"first")
        with mock.patch("src.webui.request_manifest_rebuild") as rebuild:
            missing = api.batch_set_meme_category([first, second], category)
        self.assertFalse(missing["ok"])
        self.assertEqual(first_path.read_bytes(), b"first")
        self.assertIsNone(self.db.get_by_id(first)["category_id"])
        self.assertIsNone(self.db.get_by_id(second)["category_id"])
        rebuild.assert_not_called()
        second_path.write_bytes(b"second")
        with (
            mock.patch.object(
                self.db, "set_memes_category", side_effect=RuntimeError("提交失败")
            ),
            mock.patch("src.webui.request_manifest_rebuild") as rebuild,
        ):
            failed = api.batch_set_meme_category([first, second], category)
        self.assertFalse(failed["ok"])
        self.assertEqual(first_path.read_bytes(), b"first")
        self.assertEqual(second_path.read_bytes(), b"second")
        self.assertIsNone(self.db.get_by_id(first)["category_id"])
        self.assertIsNone(self.db.get_by_id(second)["category_id"])
        rebuild.assert_not_called()

    def test_batch_category_collision_preserves_existing_file(self):
        api, cache = self._batch_category_api()
        category = self.db.create_category("目标")
        mid = self.db.add_meme("batch-collision.png")
        source = cache / "batch-collision.png"
        source.write_bytes(b"source")
        target = cache / "目标" / "batch-collision.png"
        target.parent.mkdir()
        target.write_bytes(b"existing")
        with mock.patch("src.webui.request_manifest_rebuild"):
            result = api.batch_set_meme_category([mid], category)
        self.assertFalse(result["ok"])
        self.assertEqual(source.read_bytes(), b"source")
        self.assertEqual(target.read_bytes(), b"existing")
        self.assertIsNone(self.db.get_by_id(mid)["category_id"])

    def test_batch_category_database_failure_rolls_back_entire_transaction(self):
        first = self.db.add_meme("batch-sql-first.png")
        second = self.db.add_meme("batch-sql-second.png")
        category = self.db.create_category("目标")
        conn = self.db._get_conn()
        conn.execute(
            f"CREATE TEMP TRIGGER fail_batch_category BEFORE UPDATE OF category_id "
            f"ON memes WHEN NEW.id={second} "
            "BEGIN SELECT RAISE(ABORT, 'test commit failure'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.set_memes_category([first, second], category)
        self.assertIsNone(self.db.get_by_id(first)["category_id"])
        self.assertIsNone(self.db.get_by_id(second)["category_id"])
        conn.execute("DROP TRIGGER fail_batch_category")

    def test_batch_category_rejects_missing_items_and_invalid_targets(self):
        api, cache = self._batch_category_api()
        mid = self.db.add_meme("batch-invalid.png")
        source = cache / "batch-invalid.png"
        source.write_bytes(b"image")
        with mock.patch("src.webui.request_manifest_rebuild") as rebuild:
            for target in (-4, -1, 99999, "invalid"):
                self.assertFalse(api.batch_set_meme_category([mid], target)["ok"])
            self.assertFalse(
                api.batch_set_meme_category([mid, 99999], None, "不应创建")["ok"]
            )
            self.assertFalse(api.batch_set_meme_category([], None, "不应创建")["ok"])
        self.assertTrue(source.is_file())
        self.assertEqual(self.db.get_categories(), [])
        rebuild.assert_not_called()
        with self.assertRaises(ValueError):
            self.db.set_memes_category([mid, 99999], None)
        self.assertIsNone(self.db.get_by_id(mid)["category_id"])

    def test_create_collection_api_does_not_assign_meme_until_details_save(self):
        from src.webui import JsApi

        meme_id = self.db.add_meme("collection-create.png")
        api = JsApi.__new__(JsApi)
        api._db = self.db

        with (
            mock.patch("src.webui.build_manifest") as build,
            mock.patch("src.webui.request_manifest_rebuild") as rebuild,
        ):
            parent = api.create_collection("角色")
            child = api.create_collection("爱丽丝", parent["id"])

        self.assertTrue(parent["ok"])
        self.assertTrue(child["ok"])
        self.assertEqual(child["collection"]["path"], "角色 / 爱丽丝")
        self.assertEqual(self.db.get_meme_collections(meme_id), [])
        # 创建节点本身不需要为整库同步清单而阻塞界面。
        build.assert_not_called()
        self.assertEqual(rebuild.call_count, 2)

    def test_create_collection_api_creates_and_reuses_deep_paths(self):
        from src.webui import JsApi

        api = JsApi.__new__(JsApi)
        api._db = self.db
        with mock.patch("src.webui.request_manifest_rebuild") as rebuild:
            result = api.create_collection("角色 / 爱丽丝 / 问候")
            repeated = api.create_collection("角色/爱丽丝/问候")
            relative = api.create_collection("早安/挥手", result["id"])

        self.assertTrue(result["ok"])
        self.assertTrue(result["created"])
        self.assertEqual(result["collection"]["path"], "角色 / 爱丽丝 / 问候")
        self.assertEqual(repeated["id"], result["id"])
        self.assertFalse(repeated["created"])
        self.assertEqual(
            relative["collection"]["path"],
            "角色 / 爱丽丝 / 问候 / 早安 / 挥手",
        )
        self.assertEqual(len(self.db.get_collections()), 5)
        self.assertEqual(rebuild.call_count, 2)

    def test_create_category_api_creates_root_and_relative_paths(self):
        from src.webui import JsApi

        cache = self.tmp_dir / "category-path-cache"
        cfg = Config(self.tmp_dir / "category-path-config.json")
        cfg.set("cache_dir", str(cache))
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg

        with mock.patch("src.webui.request_manifest_rebuild") as rebuild:
            root_path = api.create_category("来源 / QQ / 群聊")
            relative = api.create_category("公告/活动", root_path["id"])
            repeated = api.create_category("来源/QQ/群聊")
            casefolded = api.create_category("来源/qq/群聊")

        self.assertTrue(root_path["ok"])
        self.assertTrue(root_path["created"])
        self.assertEqual(root_path["path"], "来源 / QQ / 群聊")
        self.assertEqual(
            relative["path"], "来源 / QQ / 群聊 / 公告 / 活动"
        )
        self.assertEqual(repeated["id"], root_path["id"])
        self.assertFalse(repeated["created"])
        self.assertEqual(casefolded["id"], root_path["id"])
        self.assertFalse(casefolded["created"])
        self.assertTrue((cache / "来源" / "QQ" / "群聊" / "公告" / "活动").is_dir())
        self.assertEqual(rebuild.call_count, 2)

    def test_create_category_api_does_not_assign_meme_and_schedules_manifest(
        self,
    ):
        from src.webui import JsApi

        meme_id = self.db.add_meme("category-create.png")
        cache = self.tmp_dir / "category-create-cache"
        cfg = Config(self.tmp_dir / "category-create-config.json")
        cfg.set("cache_dir", str(cache))
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg

        with mock.patch("src.webui.request_manifest_rebuild") as rebuild:
            parent = api.create_category("角色")
            child = api.create_category("爱丽丝", parent["id"])

        self.assertTrue(parent["ok"])
        self.assertTrue(child["ok"])
        self.assertIsNone(self.db.get_by_id(meme_id)["category_id"])
        self.assertTrue((cache / "角色" / "爱丽丝").is_dir())
        child_record = self.db.get_category(child["id"])
        self.assertEqual(child_record["parent_id"], parent["id"])
        self.assertEqual(rebuild.call_count, 2)

    def test_hierarchy_creation_rejects_empty_or_dot_segments(self):
        from src.webui import JsApi

        cfg = Config(self.tmp_dir / "invalid-path-config.json")
        cfg.set("cache_dir", str(self.tmp_dir / "invalid-path-cache"))
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg

        self.assertFalse(api.create_collection("角色//问候")["ok"])
        self.assertFalse(api.create_category("角色/../问候")["ok"])
        self.assertEqual(self.db.get_collections(), [])
        self.assertEqual(self.db.get_categories(), [])

    def test_update_meme_details_renames_title_and_physical_file(self):
        from types import SimpleNamespace

        from src.webui import JsApi

        cache = self.tmp_dir / "meme-files"
        cache.mkdir()
        old_path = cache / "old-name.png"
        old_path.write_bytes(b"image-bytes")
        mid = self.db.add_meme("old-name.png", original_name="旧标题")
        cfg = Config(self.tmp_dir / "rename-config.json")
        cfg.set("cache_dir", str(cache))
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg
        api._webui = SimpleNamespace(_file_cache={"old-name.png": str(old_path)})

        with (
            mock.patch(
                "src.webui.update_manifest_meme_structure", return_value=True
            ) as incremental,
            mock.patch("src.webui.build_manifest") as build,
        ):
            result = api.update_meme_details(
                mid,
                {
                    "title": "新标题",
                    "filename": "new-name.png",
                    "tags": [],
                    "description": "描述",
                    "ocr_text": "文字",
                },
            )

        self.assertTrue(result["ok"])
        self.assertFalse(old_path.exists())
        self.assertEqual((cache / "new-name.png").read_bytes(), b"image-bytes")
        row = self.db.get_by_id(mid)
        self.assertEqual(row["original_name"], "新标题")
        self.assertEqual(row["filename"], "new-name.png")
        self.assertNotIn("old-name.png", api._webui._file_cache)
        self.assertTrue(
            os.path.samefile(
                api._webui._file_cache["new-name.png"], cache / "new-name.png"
            )
        )
        incremental.assert_called_once_with(mid, "old-name.png")
        build.assert_not_called()

    def test_update_meme_details_structure_schedules_rebuild_when_manifest_is_missing(
        self,
    ):
        from src.webui import JsApi

        mid = self.db.add_meme("structure-fallback.png")
        group = self.db.create_collection("新分组")
        api = JsApi.__new__(JsApi)
        api._db = self.db

        with (
            mock.patch(
                "src.webui.update_manifest_meme_structure", return_value=False
            ) as incremental,
            mock.patch("src.webui.request_manifest_rebuild") as rebuild,
            mock.patch("src.webui.build_manifest") as build,
        ):
            result = api.update_meme_details(
                mid,
                {
                    "filename": "structure-fallback.png",
                    "tags": [],
                    "collection_ids": [group],
                },
            )

        self.assertTrue(result["ok"])
        incremental.assert_called_once_with(mid, "structure-fallback.png")
        rebuild.assert_called_once_with()
        build.assert_not_called()

    def test_update_meme_details_title_only_updates_manifest_incrementally(self):
        from src.webui import JsApi

        mid = self.db.add_meme("title-only.png", original_name="旧标题")
        api = JsApi.__new__(JsApi)
        api._db = self.db

        with (
            mock.patch(
                "src.webui.update_manifest_meme_name", return_value=True
            ) as incremental,
            mock.patch("src.webui.build_manifest") as build,
        ):
            result = api.update_meme_details(
                mid,
                {"title": "新标题", "filename": "title-only.png", "tags": []},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(self.db.get_by_id(mid)["original_name"], "新标题")
        incremental.assert_called_once_with("title-only.png", "新标题")
        build.assert_not_called()

    def test_update_meme_details_title_only_schedules_rebuild_when_manifest_is_missing(
        self,
    ):
        from src.webui import JsApi

        mid = self.db.add_meme("missing-manifest.png", original_name="旧标题")
        api = JsApi.__new__(JsApi)
        api._db = self.db

        with (
            mock.patch("src.webui.update_manifest_meme_name", return_value=False),
            mock.patch("src.webui.request_manifest_rebuild") as rebuild,
            mock.patch("src.webui.build_manifest") as build,
        ):
            result = api.update_meme_details(
                mid,
                {"title": "新标题", "filename": "missing-manifest.png", "tags": []},
            )

        self.assertTrue(result["ok"])
        rebuild.assert_called_once_with()
        build.assert_not_called()

    def test_legacy_rename_meme_uses_incremental_manifest_update(self):
        from src.webui import JsApi

        mid = self.db.add_meme("legacy-title.png", original_name="旧标题")
        api = JsApi.__new__(JsApi)
        api._db = self.db

        with (
            mock.patch(
                "src.webui.update_manifest_meme_name", return_value=True
            ) as incremental,
            mock.patch("src.webui.build_manifest") as build,
        ):
            result = api.rename_meme(mid, "新标题")

        self.assertTrue(result)
        self.assertEqual(self.db.get_by_id(mid)["original_name"], "新标题")
        incremental.assert_called_once_with("legacy-title.png", "新标题")
        build.assert_not_called()

    def test_update_meme_details_renames_on_unresolvable_virtual_drive(self):
        from src.webui import JsApi, WebUI

        cache = self.tmp_dir / "virtual-rename"
        cache.mkdir()
        old_path = cache / "old-name.png"
        old_path.write_bytes(b"image-bytes")
        mid = self.db.add_meme("old-name.png", file_hash="stored-hash")
        cfg = Config(self.tmp_dir / "virtual-rename-config.json")
        cfg.set("cache_dir", str(cache))
        cfg.set("virtual_drive_compat", True)
        webui = WebUI.__new__(WebUI)
        webui._cfg = cfg
        webui._file_cache = {}
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg
        api._webui = webui
        webui._api = api

        with (
            mock.patch("src.webui.get_db", return_value=self.db),
            mock.patch("src.webui.update_manifest_meme_structure", return_value=True),
            mock.patch("src.webui.build_manifest"),
            mock.patch.object(
                Path,
                "resolve",
                side_effect=OSError("virtual drive does not support resolve"),
            ),
        ):
            result = api.update_meme_details(
                mid,
                {"title": "新标题", "filename": "new-name.png", "tags": []},
            )

        self.assertTrue(result["ok"])
        self.assertFalse(old_path.exists())
        self.assertEqual((cache / "new-name.png").read_bytes(), b"image-bytes")
        self.assertEqual(self.db.get_by_id(mid)["filename"], "new-name.png")

    def test_update_meme_details_recovers_completed_file_move(self):
        from src.webui import JsApi, WebUI

        cache = self.tmp_dir / "interrupted-rename"
        cache.mkdir()
        content = b"previously-moved-image"
        moved_path = cache / "new-name.png"
        moved_path.write_bytes(content)
        mid = self.db.add_meme(
            "old-name.png", file_hash=hashlib.sha256(content).hexdigest()
        )
        cfg = Config(self.tmp_dir / "interrupted-rename-config.json")
        cfg.set("cache_dir", str(cache))
        webui = WebUI.__new__(WebUI)
        webui._cfg = cfg
        webui._file_cache = {}
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg
        api._webui = webui
        webui._api = api

        with (
            mock.patch("src.webui.get_db", return_value=self.db),
            mock.patch("src.webui.update_manifest_meme_structure", return_value=True),
            mock.patch("src.webui.build_manifest"),
        ):
            result = api.update_meme_details(
                mid,
                {"title": "新标题", "filename": moved_path.name, "tags": []},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(moved_path.read_bytes(), content)
        self.assertEqual(self.db.get_by_id(mid)["filename"], moved_path.name)

    def test_update_meme_details_finds_previous_interrupted_rename_by_hash(self):
        from src.webui import JsApi, WebUI

        cache = self.tmp_dir / "interrupted-rename-search"
        cache.mkdir()
        content = b"previously-moved-image-with-an-unknown-name"
        displaced_path = cache / "first-attempt.png"
        displaced_path.write_bytes(content)
        mid = self.db.add_meme(
            "old-name.png",
            file_hash=hashlib.sha256(content).hexdigest(),
            file_size=len(content),
        )
        cfg = Config(self.tmp_dir / "interrupted-rename-search-config.json")
        cfg.set("cache_dir", str(cache))
        webui = WebUI.__new__(WebUI)
        webui._cfg = cfg
        webui._file_cache = {}
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg
        api._webui = webui
        webui._api = api

        with (
            mock.patch("src.webui.get_db", return_value=self.db),
            mock.patch("src.webui.update_manifest_meme_structure", return_value=True),
            mock.patch("src.webui.build_manifest"),
        ):
            result = api.update_meme_details(
                mid,
                {"title": "新标题", "filename": "second-attempt.png", "tags": []},
            )

        final_path = cache / "second-attempt.png"
        self.assertTrue(result["ok"])
        self.assertFalse(displaced_path.exists())
        self.assertEqual(final_path.read_bytes(), content)
        self.assertEqual(self.db.get_by_id(mid)["filename"], final_path.name)

    def test_update_meme_details_rejects_extension_change_and_rolls_back_file(self):
        from types import SimpleNamespace

        from src.webui import JsApi

        cache = self.tmp_dir / "rollback-files"
        cache.mkdir()
        old_path = cache / "original.png"
        old_path.write_bytes(b"image-bytes")
        mid = self.db.add_meme("original.png", original_name="标题")
        cfg = Config(self.tmp_dir / "rollback-config.json")
        cfg.set("cache_dir", str(cache))
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg
        api._webui = SimpleNamespace(_file_cache={})

        invalid = api.update_meme_details(
            mid,
            {"title": "标题", "filename": "changed.jpg", "tags": []},
        )
        self.assertFalse(invalid["ok"])
        self.assertIn("扩展名", invalid["error"])

        with mock.patch.object(
            self.db, "update_meme_details", side_effect=RuntimeError("db failed")
        ):
            failed = api.update_meme_details(
                mid,
                {"title": "标题", "filename": "changed.png", "tags": []},
            )
        self.assertFalse(failed["ok"])
        self.assertTrue(old_path.exists())
        self.assertFalse((cache / "changed.png").exists())
        self.assertEqual(self.db.get_by_id(mid)["filename"], "original.png")

    def test_rename_tag_updates_every_meme(self):
        from src.webui import JsApi

        first = self.db.add_meme("first.png", tags=["爱丽丝", "问候"])
        second = self.db.add_meme("second.png", tags=["爱丽丝"])
        api = JsApi.__new__(JsApi)
        api._db = self.db

        result = api.rename_tag("爱丽丝", "Alice")

        self.assertTrue(result["ok"])
        self.assertFalse(result["merged"])
        self.assertEqual(result["name"], "Alice")
        self.assertIn("Alice", self.db.get_meme_tags(first))
        self.assertIn("Alice", self.db.get_meme_tags(second))
        self.assertNotIn("爱丽丝", self.db.get_all_tags())

    def test_rename_tag_merges_existing_target_without_duplicate_links(self):
        from src.webui import JsApi

        first = self.db.add_meme("first.png", tags=["角色", "人物"])
        second = self.db.add_meme("second.png", tags=["角色"])
        api = JsApi.__new__(JsApi)
        api._db = self.db

        result = api.rename_tag("角色", "人物")

        self.assertTrue(result["ok"])
        self.assertTrue(result["merged"])
        self.assertEqual(self.db.get_meme_tags(first), ["人物"])
        self.assertEqual(self.db.get_meme_tags(second), ["人物"])
        self.assertNotIn("角色", result["all_tags"])

    def test_meme_details_navigation_uses_global_library_order(self):
        from src.webui import JsApi

        first = self.db.add_meme("first.png")
        second = self.db.add_meme("second.png")
        third = self.db.add_meme("third.png")
        hidden = self.db.add_meme("carrier.gif", stego_of_hash="source-hash")
        self.db.reorder_memes([third, first, hidden, second])
        api = JsApi.__new__(JsApi)
        api._db = self.db

        start = api.get_meme_details(third)
        middle = api.get_meme_details(first)
        end = api.get_meme_details(second)

        self.assertIsNone(start["previous_id"])
        self.assertEqual(start["next_id"], first)
        self.assertEqual((start["position"], start["total"]), (1, 3))
        self.assertEqual((middle["previous_id"], middle["next_id"]), (third, second))
        self.assertEqual((middle["position"], middle["total"]), (2, 3))
        self.assertEqual(end["previous_id"], first)
        self.assertIsNone(end["next_id"])

    def test_ai_metadata_columns_are_restored_by_migration(self):
        self.db.close()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("ALTER TABLE memes DROP COLUMN ai_description")
            conn.execute("ALTER TABLE memes DROP COLUMN ai_ocr_text")
            conn.commit()
        finally:
            conn.close()

        self.db = MemeDB(self.db_path)
        columns = {
            row[1]
            for row in self.db._get_conn()
            .execute("PRAGMA table_info(memes)")
            .fetchall()
        }
        self.assertIn("ai_description", columns)
        self.assertIn("ai_ocr_text", columns)

    def test_manual_metadata_migration_keeps_existing_values_as_ai(self):
        meme_id = self.db.add_meme(
            "legacy-ai.png",
            ai_description="旧图片描述",
            ai_ocr_text="旧图片文字",
        )
        self.db.close()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("ALTER TABLE memes DROP COLUMN manual_description")
            conn.execute("ALTER TABLE memes DROP COLUMN manual_ocr_text")
            conn.commit()
        finally:
            conn.close()

        self.db = MemeDB(self.db_path)
        row = self.db.get_by_id(meme_id)
        self.assertEqual(row["ai_description"], "旧图片描述")
        self.assertEqual(row["ai_ocr_text"], "旧图片文字")
        self.assertEqual(row["manual_description"], "")
        self.assertEqual(row["manual_ocr_text"], "")

    def test_category_column_and_index_are_added_after_legacy_schema_migration(self):
        self.db.close()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DROP INDEX IF EXISTS idx_memes_category")
            conn.execute("ALTER TABLE memes DROP COLUMN category_id")
            conn.commit()
        finally:
            conn.close()

        self.db = MemeDB(self.db_path)
        columns = {
            row[1]
            for row in self.db._get_conn()
            .execute("PRAGMA table_info(memes)")
            .fetchall()
        }
        indexes = {
            row[1]
            for row in self.db._get_conn()
            .execute("PRAGMA index_list(memes)")
            .fetchall()
        }
        self.assertIn("category_id", columns)
        self.assertIn("idx_memes_category", indexes)

    def test_legacy_tags_are_migrated_to_uncategorized_without_losing_links(self):
        meme_id = self.db.add_meme("legacy.png", tags=["旧标签"])
        self.db.close()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(
                """
                PRAGMA foreign_keys=OFF;
                CREATE TABLE legacy_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE
                );
                INSERT INTO legacy_tags (id, name) SELECT id, name FROM tags;
                DROP TABLE tags;
                ALTER TABLE legacy_tags RENAME TO tags;
                """
            )
            conn.commit()
        finally:
            conn.close()

        self.db = MemeDB(self.db_path)

        self.assertEqual(
            self.db.get_meme_tag_records(meme_id),
            [{"id": 1, "name": "旧标签", "category": ""}],
        )
        self.assertEqual(
            self.db._get_conn().execute("PRAGMA foreign_key_check").fetchall(), []
        )

    def test_notion_source_mapping_tracks_version_and_cascades(self):
        mid = self.db.add_meme("notion.png", file_hash="notion-hash")
        args = ("source-id", "page-id", "property-id", 0, "image.png")
        self.db.upsert_notion_source(mid, *args, "version-1")

        source = self.db.get_notion_source(*args, "version-1")
        self.assertEqual(source["meme_id"], mid)
        self.assertEqual(source["filename"], "notion.png")
        self.assertIsNone(self.db.get_notion_source(*args, "version-2"))

        self.db.upsert_notion_source(mid, *args, "version-2")
        self.assertEqual(
            self.db.get_notion_source(*args, "version-2")["file_hash"],
            "notion-hash",
        )
        self.db.delete_meme(mid)
        self.assertIsNone(self.db.get_notion_source(*args, "version-2"))

    def test_notion_source_table_is_restored_by_migration(self):
        candidate = self.tmp_dir / "old-backup.db"
        self.db.backup_to(str(candidate))
        conn = sqlite3.connect(candidate)
        try:
            conn.execute("DROP TABLE notion_sources")
            conn.commit()
        finally:
            conn.close()

        self.db.prepare_restore_source(str(candidate))
        conn = sqlite3.connect(candidate)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='notion_sources'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)

    def test_delete_memes(self):
        m1 = self.db.add_meme("a.png", tags=["shared", "only1"])
        m2 = self.db.add_meme("b.png", tags=["shared"])
        m3 = self.db.add_meme("c.png", tags=["only2"])
        cid = self.db.create_collection("g")
        self.db.add_to_collection(m1, cid)
        self.db.toggle_favorite(m2)
        self.db.delete_memes([m1, m2])
        self.assertIsNone(self.db.get_by_id(m1))
        self.assertIsNone(self.db.get_by_id(m2))
        self.assertIsNotNone(self.db.get_by_id(m3))
        # 孤儿标签一次性修剪
        self.assertEqual(set(self.db.get_all_tags()), {"only2"})
        # 外键级联清理分组成员关系
        rows = (
            self.db._get_conn()
            .execute("SELECT 1 FROM meme_collections WHERE meme_id=?", (m1,))
            .fetchall()
        )
        self.assertEqual(len(rows), 0)
        self.assertFalse(self.db.is_favorite(m2))

    def test_delete_memes_empty_list(self):
        self.db.delete_memes([])  # 空列表不炸

    def test_search(self):
        self.db.add_meme("cat.png", tags=["cat", "funny"])
        self.db.add_meme("dog.png", tags=["dog"])
        self.db.add_meme("cat_dog.png", tags=["cat", "dog"])

        results = self.db.search(keyword="cat")
        self.assertEqual(len(results), 2)

        results = self.db.search(tags=["cat"])
        self.assertEqual(len(results), 2)

        results = self.db.search(keyword="dog")
        self.assertEqual(len(results), 2)

    def test_search_keyword_matches_tag(self):
        self.db.add_meme("img1.png", tags=["小猫"])
        self.db.add_meme("img2.png", tags=["小狗"])
        # 搜索词命中标签名也能搜到
        results = self.db.search(keyword="小猫")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["filename"], "img1.png")
        self.assertEqual(self.db.count(keyword="小狗"), 1)
        self.assertEqual(self.db.count(keyword="不存在的标签"), 0)

    def test_search_keyword_matches_description_and_image_text(self):
        first = self.db.add_meme(
            "first.png",
            ai_description="爱丽丝正在向镜头挥手",
            ai_ocr_text="早上好",
        )
        second = self.db.add_meme(
            "second.png",
            ai_description="空旷的街道",
            ai_ocr_text="禁止通行",
        )

        self.assertEqual(
            [row["id"] for row in self.db.search(keyword="爱丽丝")], [first]
        )
        self.assertEqual(
            [row["id"] for row in self.db.search(keyword="禁止")], [second]
        )
        self.assertEqual(self.db.count(keyword="镜头挥手"), 1)
        self.assertEqual(self.db.count(keyword="禁止通行"), 1)
        self.assertEqual(
            self.db.search(keyword="爱丽丝", include_description=False), []
        )
        self.assertEqual(
            self.db.count(keyword="镜头挥手", include_description=False), 0
        )
        self.assertEqual(self.db.search(keyword="禁止", include_ocr_text=False), [])
        self.assertEqual(self.db.count(keyword="禁止通行", include_ocr_text=False), 0)

        self.db.update_meme_details(first, [], "用户修改后的独特描述", "校正文字")
        self.assertEqual(
            [row["id"] for row in self.db.search(keyword="独特描述")], [first]
        )
        self.assertEqual(self.db.count(keyword="校正文字"), 1)

    def test_search_matches_manual_and_ai_text_with_same_scope_switches(self):
        mid = self.db.add_meme(
            "mixed-search.png",
            manual_description="人工描述关键词",
            manual_ocr_text="人工文字关键词",
            ai_description="AI描述关键词",
            ai_ocr_text="AI文字关键词",
        )

        for keyword in (
            "人工描述关键词",
            "人工文字关键词",
            "AI描述关键词",
            "AI文字关键词",
        ):
            self.assertEqual(
                [row["id"] for row in self.db.search(keyword=keyword)], [mid]
            )
        self.assertEqual(
            self.db.search(keyword="人工描述关键词", include_description=False), []
        )
        self.assertEqual(
            self.db.search(keyword="人工文字关键词", include_ocr_text=False), []
        )

    def test_search_api_respects_description_and_image_text_settings(self):
        from src.webui import JsApi

        self.db.add_meme(
            "search-scope.png",
            ai_description="仅描述命中",
            ai_ocr_text="仅文字命中",
        )
        cfg = Config(self.tmp_dir / "search-config.json")
        api = JsApi.__new__(JsApi)
        api._db = self.db
        api._cfg = cfg

        cfg.set("search_description", False)
        self.assertEqual(api.search_memes(keyword="仅描述命中"), [])
        self.assertEqual(api.count_memes(keyword="仅描述命中"), 0)
        self.assertEqual(len(api.search_memes(keyword="仅文字命中")), 1)

        cfg.set("search_description", True)
        cfg.set("search_image_text", False)
        self.assertEqual(len(api.search_memes(keyword="仅描述命中")), 1)
        self.assertEqual(api.search_memes(keyword="仅文字命中"), [])
        self.assertEqual(api.count_memes(keyword="仅文字命中"), 0)

    def test_add_tags_to_memes_merge(self):
        mid1 = self.db.add_meme("a.png", tags=["old"])
        mid2 = self.db.add_meme("b.png")
        count = self.db.add_tags_to_memes([mid1, mid2], ["new", " old "])
        self.assertEqual(count, 2)
        self.assertEqual(set(self.db.get_meme_tags(mid1)), {"old", "new"})
        self.assertEqual(set(self.db.get_meme_tags(mid2)), {"old", "new"})
        self.assertEqual(set(self.db.get_all_tags()), {"old", "new"})
        # 空参数不产生变更
        self.assertEqual(self.db.add_tags_to_memes([], ["x"]), 0)
        self.assertEqual(self.db.add_tags_to_memes([mid1], []), 0)
        self.assertEqual(self.db.add_tags_to_memes([mid1], ["  "]), 0)

    def test_add_tags_to_memes_missing_ids(self):
        # 混合有效与不存在的 id：只对存在的表情生效，不抛异常、不部分写入
        mid = self.db.add_meme("a.png")
        count = self.db.add_tags_to_memes([mid, 99999], ["t1"])
        self.assertEqual(count, 1)
        self.assertEqual(set(self.db.get_meme_tags(mid)), {"t1"})
        self.assertEqual(set(self.db.get_all_tags()), {"t1"})
        # 全部 id 不存在：返回 0 且不产生任何标签
        self.assertEqual(self.db.add_tags_to_memes([88888, 99999], ["t2"]), 0)
        self.assertEqual(set(self.db.get_all_tags()), {"t1"})

    def test_add_memes_to_collection_batch(self):
        mid1 = self.db.add_meme("a.png")
        mid2 = self.db.add_meme("b.png")
        cid = self.db.create_collection("g")
        # 混合有效与不存在的 id：只对存在的表情生效
        count = self.db.add_memes_to_collection([mid1, mid2, 99999], cid)
        self.assertEqual(count, 2)
        self.assertEqual(len(self.db.search(collection_id=cid)), 2)
        # 重复加入同一分组：不产生重复关联，计数为 0
        self.assertEqual(self.db.add_memes_to_collection([mid1], cid), 0)
        self.assertEqual(len(self.db.search(collection_id=cid)), 2)
        # 目标分组不存在：抛异常且不产生部分写入
        with self.assertRaises(ValueError):
            self.db.add_memes_to_collection([mid1], 424242)
        self.assertEqual(len(self.db.search(collection_id=cid)), 2)

    def test_move_memes_to_collection_scope_and_dedup(self):
        mid1 = self.db.add_meme("a.png")  # 属于源分组
        mid2 = self.db.add_meme("b.png")  # 不属于源分组，仅属于其他分组
        mid3 = self.db.add_meme("c.png")  # 属于源分组且已在目标分组
        src = self.db.create_collection("src")
        other = self.db.create_collection("other")
        target = self.db.create_collection("t")
        self.db.add_to_collection(mid1, src)
        self.db.add_to_collection(mid3, src)
        self.db.add_to_collection(mid3, target)
        self.db.add_to_collection(mid2, other)

        moved = self.db.move_memes_to_collection([mid1, mid2, mid3], [src], target)
        # 仅 mid1 实际新增目标关联；mid3 重复加入不计；mid2 非源成员不受影响
        self.assertEqual(moved, 1)
        self.assertEqual(self.db.search(collection_id=src), [])
        self.assertEqual(len(self.db.search(collection_id=target)), 2)
        self.assertEqual(len(self.db.search(collection_id=other)), 1)

    def test_move_cascades_empty_group_cleanup(self):
        mid1 = self.db.add_meme("a.png")
        mid2 = self.db.add_meme("b.png")
        parent = self.db.create_collection("p")
        child = self.db.create_collection("c", parent_id=parent)
        keep = self.db.create_collection("keep")  # 源子树外的空分组
        target = self.db.create_collection("t")
        self.db.add_to_collection(mid1, child)
        self.db.add_to_collection(mid2, parent)

        moved = self.db.move_memes_to_collection([mid1, mid2], [parent, child], target)
        self.assertEqual(moved, 2)
        # 源子树内 child 先空、parent 后空，级联清理；子树外的空分组保留
        ids = {c[0] for c in self.db.get_collections()}
        self.assertEqual(ids, {target, keep})

    def test_move_memes_to_collection_batch(self):
        mid1 = self.db.add_meme("a.png")
        mid2 = self.db.add_meme("b.png")  # 仅属于子分组
        parent = self.db.create_collection("p")
        child = self.db.create_collection("c", parent_id=parent)
        self.db.add_to_collection(mid1, parent)
        self.db.add_to_collection(mid2, child)
        target = self.db.create_collection("t")
        # 从源子树整体移出：仅属于子分组的成员也会被移走
        count = self.db.move_memes_to_collection(
            [mid1, mid2, 99999], [parent, child], target
        )
        self.assertEqual(count, 2)
        self.assertEqual(self.db.search(collection_id=parent), [])
        self.assertEqual(self.db.search(collection_id=child), [])
        self.assertEqual(len(self.db.search(collection_id=target)), 2)
        # 目标分组不存在：抛异常且不产生部分写入
        with self.assertRaises(ValueError):
            self.db.move_memes_to_collection([mid1], [parent], 424242)
        self.assertEqual(len(self.db.search(collection_id=target)), 2)

    def test_tags(self):
        mid = self.db.add_meme("test.png", tags=["a", "b", "c"])
        tags = self.db.get_meme_tags(mid)
        self.assertEqual(set(tags), {"a", "b", "c"})

        self.db.set_meme_tags(mid, ["x", "y"])
        tags = self.db.get_meme_tags(mid)
        self.assertEqual(set(tags), {"x", "y"})

    def test_categorized_tags_allow_same_name_and_filter_independently(self):
        first = self.db.add_meme("first.png")
        second = self.db.add_meme("second.png")
        self.db.set_meme_tags(
            first,
            [
                {"category": "情绪", "name": "开心"},
                {"category": "角色", "name": "猫"},
            ],
        )
        self.db.set_meme_tags(
            second,
            [{"category": "主题", "name": "开心"}],
        )

        records = self.db.get_all_tag_records()
        self.assertEqual(
            {(item["category"], item["name"]) for item in records},
            {("情绪", "开心"), ("角色", "猫"), ("主题", "开心")},
        )
        self.assertEqual(
            [
                item["id"]
                for item in self.db.search(tags=[{"category": "情绪", "name": "开心"}])
            ],
            [first],
        )
        self.assertEqual(
            [
                item["id"]
                for item in self.db.search(tags=[{"category": "主题", "name": "开心"}])
            ],
            [second],
        )

    def test_rename_categorized_tag_does_not_touch_same_name_in_other_category(self):
        first = self.db.add_meme("first.png")
        second = self.db.add_meme("second.png")
        self.db.set_meme_tags(first, [{"category": "情绪", "name": "开心"}])
        self.db.set_meme_tags(second, [{"category": "主题", "name": "开心"}])

        result = self.db.rename_tag("开心", "快乐", category="情绪")

        self.assertEqual(result["category"], "情绪")
        self.assertEqual(self.db.get_meme_tag_records(first)[0]["name"], "快乐")
        self.assertEqual(self.db.get_meme_tag_records(second)[0]["name"], "开心")

    def test_edit_tag_category_merges_existing_target(self):
        first = self.db.add_meme("first.png")
        second = self.db.add_meme("second.png")
        self.db.set_meme_tags(first, [{"category": "旧种类", "name": "猫"}])
        self.db.set_meme_tags(second, [{"category": "新种类", "name": "猫"}])

        result = self.db.rename_tag(
            "猫",
            "猫",
            category="旧种类",
            new_category="新种类",
        )

        self.assertTrue(result["merged"])
        self.assertEqual(result["category"], "新种类")
        self.assertEqual(self.db.get_meme_tag_records(first)[0]["category"], "新种类")
        self.assertEqual(self.db.get_meme_tag_records(second)[0]["category"], "新种类")
        self.assertEqual(
            [
                (item["category"], item["name"])
                for item in self.db.get_all_tag_records()
            ],
            [("新种类", "猫")],
        )

    def test_tags_prune_orphans(self):
        mid1 = self.db.add_meme("a.png", tags=["shared", "only1"])
        mid2 = self.db.add_meme("b.png", tags=["shared"])
        # mid1 换成新标签：only1 成为孤儿被清理，shared 仍被 mid2 使用
        self.db.set_meme_tags(mid1, ["other"])
        self.assertEqual(set(self.db.get_all_tags()), {"shared", "other"})
        # 删除 mid2：shared 成为孤儿被清理
        self.db.delete_meme(mid2)
        self.assertEqual(self.db.get_all_tags(), ["other"])

    def test_get_tags_map(self):
        self.db.add_meme("a.png", tags=["y", "x"])
        self.db.add_meme("b.png")
        m = self.db.get_tags_map()
        self.assertEqual(m, {"a.png": ["x", "y"]})

    def test_apply_remote_tags_union(self):
        from src.sync import _apply_remote_tags

        mid = self.db.add_meme("a.png", tags=["local"])
        self.db.add_meme("b.png")
        remote = {
            "memes": [
                {"filename": "a.png", "tags": ["remote"]},
                {"filename": "b.png", "tags": ["btag"]},
                {"filename": "missing.png", "tags": ["ghost"]},
                {"filename": "../evil.png", "tags": ["evil"]},
            ]
        }
        with mock.patch("src.sync.get_db", return_value=self.db):
            _apply_remote_tags(remote)
        # 并集：本地标签保留、远端标签并入
        self.assertEqual(set(self.db.get_meme_tags(mid)), {"local", "remote"})
        self.assertEqual(set(self.db.get_meme_tags(2)), {"btag"})
        self.assertEqual(self.db.get_all_tags(), ["btag", "local", "remote"])

    def test_apply_remote_tags_falls_back_to_tag_map(self):
        from src.sync import _apply_remote_tags

        mid = self.db.add_meme("a.png")
        remote = {
            "memes": [{"filename": "a.png"}],
            "tag_map": {"a.png": ["oldstyle"]},
        }
        with mock.patch("src.sync.get_db", return_value=self.db):
            _apply_remote_tags(remote)
        self.assertEqual(self.db.get_meme_tags(mid), ["oldstyle"])

    def test_apply_remote_tags_missing_keys_skips(self):
        from src.sync import _apply_remote_tags

        mid = self.db.add_meme("a.png", tags=["local"])
        with mock.patch("src.sync.get_db", return_value=self.db):
            _apply_remote_tags({"memes": [{"filename": "a.png"}]})
            _apply_remote_tags({"memes": [{"filename": "a.png", "tags": []}]})
            _apply_remote_tags({"memes": [{"filename": "a.png", "tags": ["", 42]}]})
        # 条目无 tags 且无 tag_map、空列表、非字符串项均不改动本地标签
        self.assertEqual(self.db.get_meme_tags(mid), ["local"])
        self.assertEqual(self.db.get_all_tags(), ["local"])

    def test_apply_remote_favorites_union(self):
        from src.sync import _apply_remote_favorites

        mid_a = self.db.add_meme("a.png")
        mid_c = self.db.add_meme("c.png")
        self.db.toggle_favorite(mid_c)  # 本地已收藏、远端列表没有 → 保留
        remote = {"favorite": ["a.png", "missing.png", "../evil.png"]}
        with mock.patch("src.sync.get_db", return_value=self.db):
            _apply_remote_favorites(remote)
            _apply_remote_favorites(remote)  # 幂等：重复合并不产生副作用
        self.assertTrue(self.db.is_favorite(mid_a))
        self.assertTrue(self.db.is_favorite(mid_c))

    def test_apply_remote_favorites_missing_key_noop(self):
        from src.sync import _apply_remote_favorites

        mid = self.db.add_meme("a.png")
        mid_b = self.db.add_meme("b.png")
        self.db.toggle_favorite(mid)
        with mock.patch("src.sync.get_db", return_value=self.db):
            _apply_remote_favorites({})
            _apply_remote_favorites({"favorite": "a.png"})
            _apply_remote_favorites({"favorite": [42]})
        # 键缺失/非 list/仅非法项均不改动本地收藏
        self.assertTrue(self.db.is_favorite(mid))
        self.assertFalse(self.db.is_favorite(mid_b))

    def test_favorites(self):
        mid = self.db.add_meme("test.png")
        self.assertFalse(self.db.is_favorite(mid))
        self.db.toggle_favorite(mid)
        self.assertTrue(self.db.is_favorite(mid))
        self.db.toggle_favorite(mid)
        self.assertFalse(self.db.is_favorite(mid))

    def test_collections(self):
        mid = self.db.add_meme("test.png")
        cid = self.db.create_collection("my_collection")
        self.db.add_to_collection(mid, cid)

        results = self.db.search(collection_id=cid)
        self.assertEqual(len(results), 1)

        collections = self.db.get_collections()
        self.assertEqual(len(collections), 1)

    def test_collection_member_reorder(self):
        # 分组内拖拽排序：按 meme_collections.sort_order 生效
        mid1 = self.db.add_meme("a.png")
        mid2 = self.db.add_meme("b.png")
        mid3 = self.db.add_meme("c.png")
        cid = self.db.create_collection("group")
        for m in (mid1, mid2, mid3):
            self.db.add_to_collection(m, cid)

        self.db.reorder_collection_members(cid, [mid3, mid1, mid2])
        results = self.db.search(collection_id=cid)
        self.assertEqual([r["id"] for r in results], [mid3, mid1, mid2])

        # 子分组独立排序，不影响父分组
        sub = self.db.create_collection("sub", cid)
        self.db.add_to_collection(mid2, sub)
        self.db.add_to_collection(mid3, sub)
        self.db.reorder_collection_members(sub, [mid3, mid2])
        parent_results = self.db.search(collection_id=cid)
        self.assertEqual([r["id"] for r in parent_results], [mid3, mid1, mid2])
        sub_results = self.db.search(collection_id=sub)
        self.assertEqual([r["id"] for r in sub_results], [mid3, mid2])

    def test_global_reorder_independent_of_collection(self):
        # 全局拖拽排序不受分组排序影响
        mid1 = self.db.add_meme("a.png")
        mid2 = self.db.add_meme("b.png")
        cid = self.db.create_collection("group")
        self.db.add_to_collection(mid1, cid)
        self.db.add_to_collection(mid2, cid)
        self.db.reorder_collection_members(cid, [mid2, mid1])
        self.db.reorder_memes([mid1, mid2])
        results = self.db.search()
        self.assertEqual([r["id"] for r in results], [mid1, mid2])

    def test_hash_dedup(self):
        mid1 = self.db.add_meme("a.png", file_hash="hash1")
        mid2 = self.db.add_meme("b.png", file_hash="hash1")
        # 允许重复hash入库，但上层应用应去重
        self.assertIsNotNone(mid1)
        self.assertIsNotNone(mid2)

    def test_empty_search(self):
        results = self.db.search()
        self.assertEqual(len(results), 0)
        self.assertEqual(self.db.count(), 0)


class TestHotkeyWatchdog(unittest.TestCase):
    """GlobalHotkey 键盘监听线程守护：自动重注册逻辑（mock keyboard 模块）"""

    def test_windows_message_loop_uses_real_buffer_and_exits_on_quit(self):
        import ctypes
        from ctypes.wintypes import MSG
        from types import SimpleNamespace

        from src.hotkey import _patch_windows_keyboard_message_loop

        received = []
        results = iter([1, 2, 0])

        def get_message(pointer, *args):
            self.assertTrue(ctypes.cast(pointer, ctypes.c_void_p).value)
            message = ctypes.cast(pointer, ctypes.POINTER(MSG)).contents
            result = next(results)
            message.message = 0x0012 if result == 0 else 0x0400
            received.append(message.message)
            return result

        backend = SimpleNamespace(
            listen=lambda callback: self.fail("不应调用旧消息循环"),
            prepare_intercept=mock.Mock(),
            GetMessage=get_message,
            TranslateMessage=mock.Mock(),
            DispatchMessage=mock.Mock(),
        )
        keyboard = SimpleNamespace(_os_keyboard=backend)
        callback = mock.Mock()
        with mock.patch("src.hotkey.sys.platform", "win32"):
            _patch_windows_keyboard_message_loop(keyboard)
            patched_listen = backend.listen
            _patch_windows_keyboard_message_loop(keyboard)
            self.assertIs(backend.listen, patched_listen)
            backend.listen(callback)
        backend.prepare_intercept.assert_called_once_with(callback)
        self.assertEqual(received, [0x0400, 0x0400, 0x0012])
        self.assertEqual(backend.TranslateMessage.call_count, 2)
        self.assertEqual(backend.DispatchMessage.call_count, 2)

    def test_windows_message_loop_reports_api_failure_without_dispatching(self):
        import ctypes
        from types import SimpleNamespace

        from src.hotkey import _patch_windows_keyboard_message_loop

        backend = SimpleNamespace(
            listen=lambda callback: None,
            prepare_intercept=mock.Mock(),
            GetMessage=mock.Mock(return_value=-1),
            TranslateMessage=mock.Mock(),
            DispatchMessage=mock.Mock(),
        )
        with (
            mock.patch("src.hotkey.sys.platform", "win32"),
            mock.patch.object(ctypes, "get_last_error", return_value=87, create=True),
            mock.patch.object(ctypes, "WinError", return_value=OSError("invalid message"), create=True) as error,
        ):
            _patch_windows_keyboard_message_loop(SimpleNamespace(_os_keyboard=backend))
            with self.assertRaisesRegex(OSError, "invalid message"):
                backend.listen(mock.Mock())
        error.assert_called_once_with(87)
        backend.TranslateMessage.assert_not_called()
        backend.DispatchMessage.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "需要 Windows 消息队列")
    def test_windows_message_loop_native_quit_without_installing_hooks(self):
        import ctypes
        from ctypes import wintypes
        from types import SimpleNamespace

        from src.hotkey import _patch_windows_keyboard_message_loop

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        pointer = ctypes.POINTER(wintypes.MSG)
        get_message = user32.GetMessageW
        get_message.argtypes = [pointer, wintypes.HWND, wintypes.UINT, wintypes.UINT]
        get_message.restype = wintypes.BOOL
        peek_message = user32.PeekMessageW
        peek_message.argtypes = [pointer, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        peek_message.restype = wintypes.BOOL
        post_message = user32.PostThreadMessageW
        post_message.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        post_message.restype = wintypes.BOOL
        ready = threading.Event()
        errors = []

        def prepare(_callback):
            peek_message(ctypes.byref(wintypes.MSG()), None, 0, 0, 0)
            ready.set()

        backend = SimpleNamespace(
            listen=lambda callback: None,
            prepare_intercept=prepare,
            GetMessage=get_message,
            TranslateMessage=mock.Mock(),
            DispatchMessage=mock.Mock(),
        )
        _patch_windows_keyboard_message_loop(SimpleNamespace(_os_keyboard=backend))

        def run():
            try:
                backend.listen(lambda event: None)
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        try:
            self.assertTrue(ready.wait(3))
            self.assertTrue(post_message(worker.native_id, 0x8000, 0, 0))
            self.assertTrue(post_message(worker.native_id, 0x0012, 0, 0))
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            backend.TranslateMessage.assert_called_once()
            backend.DispatchMessage.assert_called_once()
        finally:
            if worker.is_alive():
                post_message(worker.native_id, 0x0012, 0, 0)
                worker.join(3)

    def test_windows_message_loop_leaves_other_backends_untouched(self):
        from types import SimpleNamespace

        from src.hotkey import _patch_windows_keyboard_message_loop

        original = mock.Mock()
        backend = SimpleNamespace(listen=original)
        keyboard = SimpleNamespace(_os_keyboard=backend)
        with mock.patch("src.hotkey.sys.platform", "linux"):
            _patch_windows_keyboard_message_loop(keyboard)
        self.assertIs(backend.listen, original)
        with mock.patch("src.hotkey.sys.platform", "win32"):
            _patch_windows_keyboard_message_loop(keyboard)
            _patch_windows_keyboard_message_loop(SimpleNamespace())
        self.assertIs(backend.listen, original)

    def _fake_keyboard_module(self, should_raise=False, hook_raises=False):
        class FakeModule(object):
            add_raises = False
            hook_raises = False
            add_calls = 0
            remove_calls = 0

            @classmethod
            def add_hotkey(cls, *a, **kw):
                cls.add_calls += 1
                if cls.add_raises:
                    raise RuntimeError("inject-fail")

            @classmethod
            def remove_hotkey(cls, *a, **kw):
                cls.remove_calls += 1

            @classmethod
            def hook(cls, *a, **kw):
                if cls.hook_raises:
                    raise RuntimeError("hook-fail")

            @classmethod
            def unhook(cls, *a, **kw):
                pass

        FakeModule.add_raises = should_raise
        FakeModule.hook_raises = hook_raises
        return FakeModule

    @mock.patch.dict("sys.modules", {"keyboard": None}, clear=False)
    def test_file_logger_disabled_under_pytest(self):
        """pytest 下禁用热键文件日志，测试夹具错误不得写入真实 hotkey.log。"""
        from src import hotkey

        self.assertIs(hotkey._get_file_logger(), hotkey.logger)

    def _fresh_hotkey(self):
        from src.hotkey import GlobalHotkey

        hk = GlobalHotkey()
        hk._hotkey = "Ctrl+Alt+N"
        hk._safe_callback = lambda: None
        hk._backend = "keyboard"
        hk._watchdog_stop = threading.Event()
        hk._hook_last_seen = 0.0
        hk._last_probe_at = 0.0
        hk._probe_pending_at = None
        hk._hook_observer = lambda event: None  # 模拟观察者已注册
        return hk

    def test_hook_health_check_probe_timeout_detects_dead_hook(self):
        """探针注入后超时未见任何键盘事件判死；用户按键即视为存活。"""
        from src.hotkey import KEYBOARD_PROBE_TIMEOUT

        hk = self._fresh_hotkey()
        calls = []
        hk._inject_probe_key = lambda: calls.append(1)

        # 第一轮：注入探针，待确认
        self.assertFalse(hk._hook_health_check(now=100.0))
        self.assertEqual(len(calls), 1)
        self.assertEqual(hk._probe_pending_at, 100.0)
        # 用户按键（或探针事件）上报 → 存活
        hk._hook_last_seen = 100.5
        self.assertFalse(hk._hook_health_check(now=105.0))
        self.assertIsNone(hk._probe_pending_at)
        # 距上次探针满间隔再次注入
        self.assertFalse(hk._hook_health_check(now=130.0))
        self.assertEqual(len(calls), 2)
        # 注入后超时无任何事件 → 判死
        hk._hook_last_seen = 0.0
        self.assertTrue(hk._hook_health_check(now=130.0 + KEYBOARD_PROBE_TIMEOUT))
        self.assertIsNone(hk._probe_pending_at)

    def test_probe_blocker_swallows_pending_probe_and_refreshes(self):
        """pending 窗口内的 F15 探针被阻塞钩子吞掉并刷新心跳，防止泄漏到前台（issue #95）。"""
        import types

        hk = self._fresh_hotkey()
        blocker = hk._make_probe_blocker()

        # 无 pending：F15（真实宏键盘按键）正常放行
        hk._probe_pending_at = None
        self.assertTrue(blocker(types.SimpleNamespace(name="f15", scan_code=-126)))

        # pending 窗口内：注入探针（scan_code=-126）被吞并刷新 _hook_last_seen 完成确认
        hk._probe_pending_at = 100.0
        hk._hook_last_seen = 0.0
        self.assertFalse(blocker(types.SimpleNamespace(name="f15", scan_code=-126)))
        self.assertGreaterEqual(hk._hook_last_seen, 100.0)

        # pending 窗口内：真实按键事件放行
        self.assertTrue(blocker(types.SimpleNamespace(name="a", scan_code=30)))
        self.assertTrue(blocker(types.SimpleNamespace(name="ctrl", scan_code=29)))

    def test_probe_blocker_passes_on_malformed_event(self):
        """事件属性访问异常时阻塞钩子回退放行，绝不拦截正常按键。"""
        hk = self._fresh_hotkey()
        blocker = hk._make_probe_blocker()
        hk._probe_pending_at = 100.0

        class BrokenEvent(object):
            def __getattr__(self, name):
                raise RuntimeError("boom")

        self.assertTrue(blocker(BrokenEvent()))

    @mock.patch.dict("sys.modules", {"keyboard": None}, clear=False)
    @mock.patch("sys.platform", "win32")
    def test_try_keyboard_registers_and_unregisters_probe_blocker(self):
        """_try_keyboard 注册探针阻塞钩子，unregister 时移除。"""
        from src.hotkey import GlobalHotkey

        fake_mod = self._fake_keyboard_module()
        hk = GlobalHotkey()
        try:
            with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
                ok = hk._try_keyboard("Ctrl+Alt+N", lambda: None)
                self.assertTrue(ok)
                self.assertIsNotNone(hk._probe_blocker)

                hk.unregister()
                self.assertIsNone(hk._probe_blocker)
        finally:
            hk.unregister()

    def test_restart_keyboard_listener_reinstalls_hook(self):
        """钩子失效重启：结束旧线程→重装钩子→重挂热键；成功后清 pending。"""
        fake_mod = self._fake_keyboard_module()
        fake_mod.add_calls = 0
        fake_mod.remove_calls = 0

        class FakeThread(object):
            def is_alive(self):
                return False

        class FakeListener(object):
            listening = True
            listening_thread = FakeThread()
            started = False

            def start_if_necessary(self):
                self.started = True

        hk = self._fresh_hotkey()
        hk._reregister_pending = True  # 此前一次重启失败遗留
        hk._kill_listening_thread = lambda listener: None

        listener = FakeListener()
        with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
            ok = hk._restart_keyboard_listener(listener)

        self.assertTrue(ok)
        self.assertFalse(hk._reregister_pending)
        self.assertEqual(fake_mod.remove_calls, 1)
        self.assertTrue(listener.started)
        self.assertEqual(fake_mod.add_calls, 1)

    def test_restart_keyboard_listener_keeps_healthy_processing_thread(self):
        """处理线程健康时仅替换监听线程，不产生重复的事件消费者。"""
        fake_mod = self._fake_keyboard_module()
        fake_mod.add_calls = 0
        fake_mod.remove_calls = 0

        class FakeThread(object):
            def __init__(self, alive):
                self._alive = alive

            def is_alive(self):
                return self._alive

        class FakeListener(object):
            listening = True
            listening_thread = FakeThread(False)  # kill 之后的残留状态
            processing_thread = FakeThread(True)
            started = False
            listened = False

            def start_if_necessary(self):
                self.started = True

            def listen(self):
                self.listened = True

        hk = self._fresh_hotkey()
        hk._kill_listening_thread = lambda listener: None
        original_pt = FakeListener.processing_thread

        listener = FakeListener()
        with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
            ok = hk._restart_keyboard_listener(listener)
            if listener.listening_thread is not None:
                listener.listening_thread.join(timeout=2)

        self.assertTrue(ok)
        self.assertFalse(listener.started)  # 未走 start_if_necessary
        self.assertTrue(listener.listening)
        self.assertTrue(listener.listened)  # 新监听线程已启动
        # 原处理线程对象被保留（未新建重复消费者）
        self.assertIs(listener.processing_thread, original_pt)
        self.assertEqual(fake_mod.add_calls, 1)

    @mock.patch.dict("sys.modules", {"keyboard": None}, clear=False)
    @mock.patch("sys.platform", "win32")
    def test_try_keyboard_hook_failure_degrades_gracefully(self):
        """hook 观察者注册失败：保留 keyboard 后端仅降级心跳，不回落 pynput
        造成双后端重复触发。"""
        from src.hotkey import GlobalHotkey

        fake_mod = self._fake_keyboard_module(hook_raises=True)
        hk = GlobalHotkey()
        try:
            with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
                ok = hk._try_keyboard("Ctrl+Alt+N", lambda: None)

            self.assertTrue(ok)
            self.assertEqual(hk._backend, "keyboard")
            self.assertIsNone(hk._hook_observer)
            self.assertEqual(fake_mod.add_calls, 1)
        finally:
            hk.unregister()

    def test_hook_health_check_disabled_without_observer(self):
        """降级模式（观察者未注册）下不注入探针、不判死，避免误判重启循环。"""
        hk = self._fresh_hotkey()
        hk._hook_observer = None
        calls = []
        hk._inject_probe_key = lambda: calls.append(1)

        self.assertFalse(hk._hook_health_check(now=1000.0))
        self.assertEqual(calls, [])
        self.assertIsNone(hk._probe_pending_at)

    def test_reregister_listening_only_death_starts_listen_thread(self):
        """仅监听线程死亡：保留处理线程，单独启动新监听线程（不重建存活线程）。"""
        fake_mod = self._fake_keyboard_module()
        fake_mod.add_calls = 0
        fake_mod.remove_calls = 0

        class FakeThread(object):
            def __init__(self, alive):
                self._alive = alive

            def is_alive(self):
                return self._alive

        class FakeListener(object):
            listening = True
            listening_thread = FakeThread(False)  # 仅监听线程死亡
            processing_thread = FakeThread(True)  # 处理线程存活
            restarted = False
            listened = False

            def start_if_necessary(self):
                self.restarted = True

            def listen(self):
                self.listened = True

        hk = self._fresh_hotkey()
        original_pt = FakeListener.processing_thread

        listener = FakeListener()
        with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
            ok = hk._reregister_keyboard(listener)
            listener.listening_thread.join(timeout=2)

        self.assertTrue(ok)
        self.assertFalse(listener.restarted)  # 未全量重启
        self.assertTrue(listener.listened)  # 单独启动了新监听线程
        self.assertIs(listener.processing_thread, original_pt)  # 处理线程保留
        self.assertEqual(fake_mod.add_calls, 1)

    def test_reregister_processing_only_death_starts_process_thread(self):
        """仅处理线程死亡：保留监听线程（钩子仍在），单独启动新处理线程。"""
        fake_mod = self._fake_keyboard_module()
        fake_mod.add_calls = 0
        fake_mod.remove_calls = 0

        class FakeThread(object):
            def __init__(self, alive):
                self._alive = alive

            def is_alive(self):
                return self._alive

        class FakeListener(object):
            listening = True
            listening_thread = FakeThread(True)  # 监听线程存活（钩子仍在）
            processing_thread = FakeThread(False)  # 仅处理线程死亡
            restarted = False
            processed = False

            def start_if_necessary(self):
                self.restarted = True

            def process(self):
                self.processed = True

        hk = self._fresh_hotkey()
        original_lt = FakeListener.listening_thread

        listener = FakeListener()
        with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
            ok = hk._reregister_keyboard(listener)
            listener.processing_thread.join(timeout=2)

        self.assertTrue(ok)
        self.assertFalse(listener.restarted)  # 未全量重启（避免双钩子）
        self.assertTrue(listener.processed)  # 单独启动了新处理线程
        self.assertIs(listener.listening_thread, original_lt)  # 监听线程未重建
        self.assertEqual(fake_mod.add_calls, 1)

    def test_watchdog_tick_retries_pending_reregistration(self):
        """add_hotkey 失败置 pending 后，看门狗轮询必须重试（否则热键永久失效）。"""
        fake_mod = self._fake_keyboard_module()
        fake_mod.add_calls = 0
        fake_mod.remove_calls = 0

        class FakeThread(object):
            def is_alive(self):
                return True

        class FakeListener(object):
            listening = True
            listening_thread = FakeThread()  # 线程已补齐：pending 场景的常态
            processing_thread = FakeThread()

        hk = self._fresh_hotkey()
        hk._reregister_pending = True  # 上轮 add_hotkey 失败遗留

        with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
            hk._watchdog_tick(FakeListener(), hk._watchdog_gen)

        self.assertFalse(hk._reregister_pending)  # 重试成功
        self.assertEqual(fake_mod.add_calls, 1)

    def test_watchdog_tick_healthy_state_is_noop(self):
        """线程存活、无 pending、观察者存活时单轮检查不做任何动作。"""
        fake_mod = self._fake_keyboard_module()
        fake_mod.add_calls = 0

        class FakeThread(object):
            def is_alive(self):
                return True

        class FakeListener(object):
            listening = True
            listening_thread = FakeThread()
            processing_thread = FakeThread()

        hk = self._fresh_hotkey()
        hk._reregister_pending = False
        # 模拟刚注入过探针：间隔未满，不应再次注入
        hk._last_probe_at = time.monotonic()
        probe_calls = []
        hk._inject_probe_key = lambda: probe_calls.append(1)

        with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
            hk._watchdog_tick(FakeListener(), hk._watchdog_gen)

        self.assertEqual(fake_mod.add_calls, 0)
        # 探针在间隔未满时不注入（刚注册不久）
        self.assertEqual(probe_calls, [])

    def test_restart_keyboard_listener_failure_sets_pending(self):
        """旧监听线程无法退出时中止重启（避免双钩子），置 pending 待下轮重试。"""
        fake_mod = self._fake_keyboard_module()
        fake_mod.add_calls = 0
        fake_mod.remove_calls = 0

        class FakeThread(object):
            def is_alive(self):
                return True

        class FakeListener(object):
            listening = True
            listening_thread = FakeThread()

            def start_if_necessary(self):
                raise AssertionError("不应重装钩子")

        hk = self._fresh_hotkey()

        def refuse(listener):
            raise RuntimeError("旧监听线程未能退出")

        hk._kill_listening_thread = refuse

        with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
            ok = hk._restart_keyboard_listener(FakeListener())

        self.assertFalse(ok)
        self.assertTrue(hk._reregister_pending)
        self.assertEqual(fake_mod.add_calls, 0)

    @mock.patch.dict("sys.modules", {"keyboard": None}, clear=False)
    def test_reregister_restarts_listener_and_reattaches(self):
        from src.hotkey import GlobalHotkey

        fake_mod = self._fake_keyboard_module()
        fake_mod.add_calls = 0
        fake_mod.remove_calls = 0

        class FakeListener(object):
            listening = True
            start_calls = 0

            def start_if_necessary(self):
                self.start_calls += 1

        listener = FakeListener()

        hk = GlobalHotkey()
        hk._hotkey = "Ctrl+Alt+N"
        hk._safe_callback = lambda: None
        hk._backend = "keyboard"
        hk._watchdog_stop = threading.Event()  # 模拟守护已启动

        with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
            ok = hk._reregister_keyboard(listener)

        self.assertTrue(ok)
        self.assertFalse(hk._reregister_pending)
        self.assertEqual(fake_mod.remove_calls, 1)
        self.assertEqual(listener.start_calls, 1)
        self.assertFalse(listener.listening)
        self.assertEqual(fake_mod.add_calls, 1)

    @mock.patch.dict("sys.modules", {"keyboard": None}, clear=False)
    def test_reregister_add_failure_sets_pending_and_not_success(self):
        """重注册 add_hotkey 失败：返回 False、置 pending，绝不记录成功/停止重试。"""
        from src.hotkey import GlobalHotkey

        fake_mod = self._fake_keyboard_module(should_raise=True)
        fake_mod.add_calls = 0
        fake_mod.remove_calls = 0

        class FakeListener(object):
            listening = True
            start_calls = 0

            def start_if_necessary(self):
                self.start_calls += 1

        hk = GlobalHotkey()
        hk._hotkey = "Ctrl+Alt+N"
        hk._safe_callback = lambda: None
        hk._backend = "keyboard"
        hk._watchdog_stop = threading.Event()

        with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
            ok = hk._reregister_keyboard(FakeListener())

        self.assertFalse(ok)
        self.assertTrue(hk._reregister_pending)  # 守护下轮会继续重试

    @mock.patch.dict("sys.modules", {"keyboard": None}, clear=False)
    def test_reregister_after_unregister_does_not_add(self):
        """注销后重注册不得重新挂热键（同锁 + 停止标志/回调清空兜底）。"""
        from src.hotkey import GlobalHotkey

        fake_mod = self._fake_keyboard_module()
        fake_mod.add_calls = 0
        fake_mod.remove_calls = 0

        class FakeListener(object):
            listening = True

            def start_if_necessary(self):
                pass

        hk = GlobalHotkey()
        hk._hotkey = "Ctrl+Alt+N"
        hk._safe_callback = lambda: None
        hk._backend = "keyboard"

        with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
            # 1) 已注销：watchdog_stop 置位后 unregister 清空回调
            hk._watchdog_stop = threading.Event()
            hk.unregister()  # stop.set() + _safe_callback=None
            # 2) 守护线程若此刻想重注册，应直接返回 False 且不再 add
            ok = hk._reregister_keyboard(FakeListener())

        self.assertFalse(ok)
        self.assertEqual(fake_mod.add_calls, 0)  # 注销后不得重新挂热键

    @mock.patch.dict("sys.modules", {"keyboard": None}, clear=False)
    def test_stale_generation_cannot_touch_after_reregister(self):
        """注销后重新注册：旧代次 watchdog 的重注册操作不得作用到新生命周期。"""
        from src.hotkey import GlobalHotkey

        fake_mod = self._fake_keyboard_module()
        fake_mod.add_calls = 0

        class FakeListener(object):
            listening = True

            def start_if_necessary(self):
                pass

        hk = GlobalHotkey()
        hk._backend = "keyboard"
        hk._hotkey = "Ctrl+Alt+N"

        with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
            # 第一次注册（代次 0）→ 注销（代次 -> 1）→ 立即重新注册（新代次 1）
            hk._watchdog_stop = threading.Event()
            hk.unregister()
            old_gen = 0  # 旧 watchdog 捕获的代次
            # 重新注册：赋新回调并启动新守护（当前代次已是 1）
            hk._safe_callback = lambda: None
            self.assertEqual(hk._watchdog_gen, 1)
            # 旧代次 watchdog 调重注册：应被拒，不得 add
            stale = hk._reregister_keyboard(FakeListener(), gen=old_gen)
            self.assertFalse(stale)
            self.assertEqual(fake_mod.add_calls, 0)
            # 新代次（当前 gen）可以正常重注册
            fresh = hk._reregister_keyboard(FakeListener(), gen=hk._watchdog_gen)
            self.assertTrue(fresh)
            self.assertEqual(fake_mod.add_calls, 1)

    @mock.patch.dict("sys.modules", {"keyboard": None}, clear=False)
    def test_unregister_stops_watchdog(self):
        from src.hotkey import GlobalHotkey

        fake_mod = self._fake_keyboard_module()
        fake_mod.add_calls = 0

        hk = GlobalHotkey()
        hk._backend = "keyboard"
        hk._hotkey = "Ctrl+Alt+N"
        hk._safe_callback = lambda: None

        with mock.patch.dict("sys.modules", {"keyboard": fake_mod}):
            stop = mock.MagicMock()
            hk._watchdog_stop = stop
            hk.unregister()

        # watchdog 应被停止，safe_callback 清空，remove_hotkey 被调用
        stop.set.assert_called_once()
        self.assertIsNone(hk._watchdog_stop)
        self.assertIsNone(hk._safe_callback)
        self.assertEqual(fake_mod.remove_calls, 1)


class TestBackup(unittest.TestCase):
    """本地备份：ZIP 创建/列表/删除/恢复（PC 间整库迁移）"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = MemeDB(self.tmp / "memes.db")
        self.cache = self.tmp / "cache"
        self.cache.mkdir()
        self.backup_dir = self.tmp / "backups"

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_list_delete(self):
        from src import backup

        (self.cache / "abc123.png").write_bytes(b"pngdata")
        (self.cache / "thumbnails").mkdir()
        (self.cache / "thumbnails" / "thumb.png").write_bytes(b"thumb")
        r = backup.create_backup(self.backup_dir, self.tmp, self.cache, self.db)
        self.assertTrue(r["ok"])
        self.assertEqual(r["file_count"], 1)  # thumbnails 不入包
        lst = backup.list_backups(self.backup_dir)
        self.assertEqual(len(lst), 1)
        self.assertEqual(lst[0]["name"], r["path"])
        self.assertTrue(backup.delete_backup(self.backup_dir, lst[0]["name"]))
        self.assertEqual(backup.list_backups(self.backup_dir), [])
        # 路径穿越/非法名拒绝
        self.assertFalse(backup.delete_backup(self.backup_dir, "../evil.zip"))
        self.assertFalse(backup.delete_backup(self.backup_dir, "other.zip"))

    def test_restore_preserves_metadata(self):
        from src import backup

        mid = self.db.add_meme("abc123.png", tags=["cat"], width=10, height=10)
        self.db.toggle_favorite(mid)
        cid = self.db.create_collection("col")
        self.db.add_to_collection(mid, cid)
        (self.cache / "abc123.png").write_bytes(b"pngdata")
        r = backup.create_backup(self.backup_dir, self.tmp, self.cache, self.db)

        # 第二台设备：全新库与空缓存目录
        db2 = MemeDB(self.tmp / "memes2.db")
        try:
            cache2 = self.tmp / "cache2"
            cache2.mkdir()
            r2 = backup.restore_backup(
                self.backup_dir / r["path"], self.tmp, cache2, db2
            )
            self.assertTrue(r2["ok"])
            self.assertEqual(db2.count(), 1)
            m = db2.search()[0]
            self.assertEqual(m["filename"], "abc123.png")
            self.assertEqual(set(db2.get_meme_tags(m["id"])), {"cat"})
            self.assertTrue(db2.is_favorite(m["id"]))
            self.assertEqual(len(db2.search(collection_id=cid)), 1)
            self.assertTrue((cache2 / "abc123.png").exists())
        finally:
            db2.close()

    def test_backup_restores_chat_record_media_with_relative_path(self):
        from src import backup

        media = self.cache / "chat-record-media" / "record-folder" / "00001.jpg"
        media.parent.mkdir(parents=True)
        media.write_bytes(b"\xff\xd8\xffimage")
        package = {
            "schema_version": 1,
            "title": "含图片记录",
            "source": {
                "self_id": 123,
                "message_id": 456,
                "message_type": "group",
                "group_id": 789,
                "message": [{"type": "forward", "data": {"id": "root"}}],
            },
            "forward_records": {
                "root": {
                    "messages": [
                        {
                            "message": [
                                {
                                    "type": "image",
                                    "data": {
                                        "local_path": (
                                            "chat-record-media/record-folder/00001.jpg"
                                        )
                                    },
                                }
                            ]
                        }
                    ]
                }
            },
            "summary": {"node_count": 1, "record_count": 1},
        }
        saved = self.db.save_chat_record(package)
        result = backup.create_backup(self.backup_dir, self.tmp, self.cache, self.db)

        db2 = MemeDB(self.tmp / "memes2.db")
        try:
            cache2 = self.tmp / "cache2"
            cache2.mkdir()
            restored = backup.restore_backup(
                self.backup_dir / result["path"], self.tmp, cache2, db2
            )
            restored_package = db2.get_chat_record(saved["id"])
            data = restored_package["forward_records"]["root"]["messages"][0][
                "message"
            ][0]["data"]

            self.assertTrue(restored["ok"])
            expected_path = package["forward_records"]["root"]["messages"][0][
                "message"
            ][0]["data"]["local_path"]
            self.assertEqual(data["local_path"], expected_path)
            self.assertTrue(
                os.path.samefile(
                    onebot._archive_media_source(data, storage_root=cache2),
                    cache2 / data["local_path"],
                )
            )
        finally:
            db2.close()

    def test_restore_keeps_orphans_and_overwrites_same_name(self):
        from src import backup

        (self.cache / "aaa111.png").write_bytes(b"newcontent")
        r = backup.create_backup(self.backup_dir, self.tmp, self.cache, self.db)

        db2 = MemeDB(self.tmp / "memes2.db")
        try:
            cache2 = self.tmp / "cache2"
            cache2.mkdir()
            (cache2 / "bbb222.png").write_bytes(b"orphan")  # 孤儿文件
            (cache2 / "aaa111.png").write_bytes(b"old")  # 同名旧内容
            r2 = backup.restore_backup(
                self.backup_dir / r["path"], self.tmp, cache2, db2
            )
            self.assertTrue(r2["ok"])
            # 孤儿保留、同名被备份内容覆盖
            self.assertEqual((cache2 / "bbb222.png").read_bytes(), b"orphan")
            self.assertEqual((cache2 / "aaa111.png").read_bytes(), b"newcontent")
        finally:
            db2.close()

    def test_restore_rejects_nonempty_db(self):
        from src import backup

        src_cache = self.tmp / "src_cache"
        src_cache.mkdir()
        (src_cache / "aaa111.png").write_bytes(b"data")
        db2 = MemeDB(self.tmp / "src.db")
        try:
            db2.add_meme("aaa111.png")
            r = backup.create_backup(self.backup_dir, self.tmp, src_cache, db2)
        finally:
            db2.close()

        self.db.add_meme("existing.png")  # 目标库非空
        cache2 = self.tmp / "cache2"
        cache2.mkdir()
        r2 = backup.restore_backup(
            self.backup_dir / r["path"], self.tmp, cache2, self.db
        )
        self.assertFalse(r2["ok"])
        self.assertIn("中止", r2["error"])
        self.assertEqual(list(cache2.iterdir()), [])  # 未落任何文件
        self.assertFalse((self.tmp / "backup_restore_tmp").exists())  # staging 已清理

    def test_has_any_data_includes_stego_carriers(self):
        """仅含 stego 载体行的库 count() 为 0，但 has_any_data 为真，拒绝恢复。"""
        from src import backup

        mid = self.db.add_meme("carrier.gif")
        self.db.update_meme(mid, stego_of_hash="deadbeef")
        self.assertEqual(self.db.count(), 0)  # search/count 层隐藏载体
        self.assertTrue(self.db.has_any_data())

        src_cache = self.tmp / "src_cache"
        src_cache.mkdir()
        (src_cache / "aaa111.png").write_bytes(b"data")
        db2 = MemeDB(self.tmp / "src.db")
        try:
            db2.add_meme("aaa111.png")
            r = backup.create_backup(self.backup_dir, self.tmp, src_cache, db2)
        finally:
            db2.close()

        cache2 = self.tmp / "cache2"
        cache2.mkdir()
        r2 = backup.restore_backup(
            self.backup_dir / r["path"], self.tmp, cache2, self.db
        )
        self.assertFalse(r2["ok"])  # 仅载体库不得被恢复覆盖
        self.assertEqual(list(cache2.iterdir()), [])

    def test_restore_from_rechecks_empty_and_preserves_data(self):
        """模拟竞态：前置空库检查后又有写入，restore_from 锁内复查拒绝且记录保留。"""
        from src import backup

        src_cache = self.tmp / "src_cache"
        src_cache.mkdir()
        (src_cache / "aaa111.png").write_bytes(b"data")
        db2 = MemeDB(self.tmp / "src.db")
        try:
            db2.add_meme("aaa111.png")
            r = backup.create_backup(self.backup_dir, self.tmp, src_cache, db2)
        finally:
            db2.close()

        self.db.add_meme("sneaky.png")  # 前置检查之后才出现的写入
        real_has_any_data = self.db.has_any_data
        calls = {"n": 0}

        def fake_has_any_data():
            calls["n"] += 1
            if calls["n"] == 1:
                return False  # 首次（restore_backup 前置检查）谎报为空
            return real_has_any_data()  # restore_from 锁内复查取真实值

        with mock.patch.object(self.db, "has_any_data", side_effect=fake_has_any_data):
            r = backup.restore_backup(
                self.backup_dir / r["path"], self.tmp, self.cache, self.db
            )
        self.assertFalse(r["ok"])
        self.assertIn("中止", r["error"])
        self.assertEqual(self.db.count(), 1)  # 竞态写入的记录仍被保留
        self.assertEqual(calls["n"], 2)  # 锁内复查确实执行过

    def test_validate_backup_dir_rejects_cache_nesting(self):
        """备份目录与 cache_dir 相同/互为嵌套时拒绝，防止备份递归自包含。"""
        from src import backup

        cache = self.tmp / "cache"  # setUp 已创建
        nested = cache / "backups"
        other = self.tmp / "elsewhere"
        self.assertEqual(
            backup.validate_backup_dir(nested, cache),
            (False, "备份目录不能与表情包存储目录相同或互为嵌套"),
        )
        self.assertEqual(backup.validate_backup_dir(cache, cache)[0], False)
        # cache_dir 嵌套在备份目录内同样拒绝
        self.assertEqual(backup.validate_backup_dir(other, other / "cache")[0], False)
        # 平级目录放行
        self.assertEqual(backup.validate_backup_dir(other, cache)[0], True)
        self.assertEqual(
            backup.validate_backup_dir(self.tmp / "backups", cache)[0], True
        )

    def test_prepare_restore_source_rejects_missing_tables(self):
        """候选库缺失关系表（meme_tags/favorites 等）时拒绝恢复。"""
        partial = self.tmp / "partial.db"
        conn = sqlite3.connect(partial)
        try:
            conn.executescript(
                "CREATE TABLE memes (id INTEGER PRIMARY KEY, filename TEXT);"
                "CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);"
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(ValueError) as ctx:
            self.db.prepare_restore_source(str(partial))
        self.assertIn("缺少必需的数据表", str(ctx.exception))
        self.assertIn("meme_tags", str(ctx.exception))

    def test_restore_rejects_missing_foreign_key_definitions(self):
        """表与列齐全但外键定义被删除的候选库：预校验拒绝，restore 流程不接受。"""
        from src import backup

        nofk = self.tmp / "nofk.db"
        conn = sqlite3.connect(nofk)
        try:
            # 与正式 schema 相同的表和列，但全部 REFERENCES 被移除
            conn.executescript(
                """
                CREATE TABLE memes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    file_hash TEXT NOT NULL DEFAULT '',
                    original_name TEXT NOT NULL DEFAULT '',
                    width INTEGER DEFAULT 0,
                    height INTEGER DEFAULT 0,
                    file_size INTEGER DEFAULT 0,
                    mime_type TEXT DEFAULT 'image/png',
                    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                );
                CREATE TABLE meme_tags (
                    meme_id INTEGER NOT NULL,
                    tag_id INTEGER NOT NULL,
                    PRIMARY KEY (meme_id, tag_id)
                );
                CREATE TABLE collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    parent_id INTEGER DEFAULT NULL,
                    sort_order INTEGER DEFAULT 0
                );
                CREATE TABLE meme_collections (
                    meme_id INTEGER NOT NULL,
                    collection_id INTEGER NOT NULL,
                    sort_order INTEGER DEFAULT 0,
                    PRIMARY KEY (meme_id, collection_id)
                );
                CREATE TABLE favorites (
                    meme_id INTEGER PRIMARY KEY,
                    added_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
                );
                CREATE TABLE recent_uses (
                    meme_id INTEGER PRIMARY KEY,
                    used_at TEXT NOT NULL
                );
            """
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(ValueError) as ctx:
            self.db.prepare_restore_source(str(nofk))
        self.assertIn("缺少必需的外键定义", str(ctx.exception))

        # 端到端：restore_backup 不接受该候选库
        self.backup_dir.mkdir()
        bad = self.backup_dir / "OhMyMeme-backup-nofk.zip"
        with zipfile.ZipFile(bad, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("backup.json", json.dumps({"file_count": 0}))
            zf.write(nofk, "db/memes.db")
        r = backup.restore_backup(bad, self.tmp, self.cache, self.db)
        self.assertFalse(r["ok"])
        self.assertIn("缺少必需的外键定义", r["error"])
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_prepare_restore_source_rejects_fk_orphans(self):
        """候选库存在孤儿外键记录（引用被删分组）时拒绝恢复。"""
        cid = None
        src = self.tmp / "candidate.db"
        db2 = MemeDB(self.tmp / "src.db")
        try:
            mid = db2.add_meme("a.png")
            cid = db2.create_collection("c")
            db2.add_to_collection(mid, cid)
            db2.backup_to(str(src))
        finally:
            db2.close()
        # 默认连接外键关闭，可删除被引用行制造孤儿 meme_collections 记录
        conn = sqlite3.connect(src)
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DELETE FROM collections WHERE id=?", (cid,))
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(ValueError) as ctx:
            self.db.prepare_restore_source(str(src))
        self.assertIn("孤儿外键", str(ctx.exception))

    def test_restore_rejects_oversized_or_unsupported_members(self):
        """ZIP 安全限制：不支持的压缩类型 / 超大成员拒绝恢复。"""
        from src import backup

        self.backup_dir.mkdir()
        # BZIP2 压缩类型
        bad = self.backup_dir / "OhMyMeme-backup-a.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("backup.json", json.dumps({"file_count": 1}))
            zf.writestr("db/memes.db", b"db")
            zi = zipfile.ZipInfo("files/aaa111.png")
            zf.writestr(zi, b"data", compress_type=zipfile.ZIP_BZIP2)
        r = backup.restore_backup(bad, self.tmp, self.cache, self.db)
        self.assertFalse(r["ok"])
        self.assertIn("压缩类型", r["error"])
        # 超大成员：调低限制常量后由 file_size 校验拦截
        big = self.backup_dir / "OhMyMeme-backup-b.zip"
        with zipfile.ZipFile(big, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("backup.json", json.dumps({"file_count": 1}))
            zf.writestr("db/memes.db", b"db")
            zf.writestr("files/aaa111.png", b"data")
        with mock.patch.object(backup, "RESTORE_MAX_MEMBER_BYTES", 2):
            r2 = backup.restore_backup(big, self.tmp, self.cache, self.db)
        self.assertFalse(r2["ok"])
        self.assertIn("超大文件", r2["error"])
        self.assertEqual(list(self.cache.iterdir()), [])  # 未落任何文件

    def test_restore_rejects_invalid_candidate_db(self):
        """文件数正确但库文件是垃圾字节：预校验拒绝，缓存零落盘。"""
        from src import backup

        self.backup_dir.mkdir()
        bad = self.backup_dir / "OhMyMeme-backup-c.zip"
        with zipfile.ZipFile(bad, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("backup.json", json.dumps({"file_count": 1}))
            zf.writestr("db/memes.db", b"not a real database")
            zf.writestr("files/aaa111.png", b"data")
        r = backup.restore_backup(bad, self.tmp, self.cache, self.db)
        self.assertFalse(r["ok"])
        self.assertIn("数据库无效", r["error"])
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertFalse((self.tmp / "backup_restore_tmp").exists())

    def test_restore_rejects_invalid_package(self):
        from src import backup

        self.backup_dir.mkdir()
        bad = self.backup_dir / "OhMyMeme-backup-x.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("other.txt", "hi")
        r = backup.restore_backup(bad, self.tmp, self.cache, self.db)
        self.assertFalse(r["ok"])

        notzip = self.backup_dir / "OhMyMeme-backup-y.zip"
        notzip.write_bytes(b"junk")
        with self.assertRaises(zipfile.BadZipFile):
            backup.restore_backup(notzip, self.tmp, self.cache, self.db)

    def test_restore_rejects_count_mismatch(self):
        from src import backup

        self.backup_dir.mkdir()
        bad = self.backup_dir / "OhMyMeme-backup-z.zip"
        with zipfile.ZipFile(bad, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("backup.json", json.dumps({"file_count": 5, "created_at": ""}))
            zf.writestr("db/memes.db", b"not a real db")
            zf.writestr("files/aaa111.png", b"data")
        r = backup.restore_backup(bad, self.tmp, self.cache, self.db)
        self.assertFalse(r["ok"])
        self.assertIn("不完整", r["error"])


if __name__ == "__main__":
    unittest.main()
