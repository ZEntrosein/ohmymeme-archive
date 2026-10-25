"""原件与自适应日常副本回归测试。"""

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, features

from src.config import Config
from src.database import MemeDB
from src.media_derivative import DAILY_COPY_MAX_SIDE, create_daily_copy


class TestMediaDerivative(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.output = self.tmp / "daily"

    def test_static_image_becomes_resized_webp_without_touching_original(self):
        source = self.tmp / "large.png"
        Image.new("RGBA", (2000, 1000), (30, 80, 160, 180)).save(source)
        original = source.read_bytes()

        result = create_daily_copy(
            source, self.output, hashlib.sha256(original).hexdigest()
        )

        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(result["mime_type"], "image/webp")
        daily = self.output / result["filename"]
        self.assertTrue(daily.is_file())
        with Image.open(daily) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertLessEqual(max(image.size), DAILY_COPY_MAX_SIDE)
            self.assertEqual(image.mode, "RGBA")

    def test_large_jpeg_uses_bounded_override_and_restores_pillow_limit(self):
        source = self.tmp / "large-threshold.jpg"
        Image.new("RGB", (4000, 400), (30, 80, 160)).save(source, quality=90)
        original_limit = Image.MAX_IMAGE_PIXELS

        with mock.patch.object(Image, "MAX_IMAGE_PIXELS", 700_000):
            result = create_daily_copy(source, self.output, "large-jpeg-hash")
            self.assertEqual(Image.MAX_IMAGE_PIXELS, 700_000)

        self.assertEqual(Image.MAX_IMAGE_PIXELS, original_limit)
        daily = self.output / result["filename"]
        self.assertTrue(daily.is_file())
        with Image.open(daily) as image:
            self.assertLessEqual(max(image.size), DAILY_COPY_MAX_SIDE)

    def test_jpeg_over_explicit_hard_limit_is_rejected_before_decode(self):
        from src.media_derivative import LARGE_JPEG_MAX_PIXELS

        source = self.tmp / "over-hard-limit.jpg"
        Image.new("RGB", (8, 8), (30, 80, 160)).save(source, quality=90)
        data = bytearray(source.read_bytes())
        patched = False
        for index in range(len(data) - 8):
            if data[index] != 0xFF or data[index + 1] not in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                continue
            data[index + 5 : index + 7] = (65535).to_bytes(2, "big")
            data[index + 7 : index + 9] = (65535).to_bytes(2, "big")
            patched = True
            break
        self.assertTrue(patched)
        source.write_bytes(data)
        self.assertGreater(65535 * 65535, LARGE_JPEG_MAX_PIXELS)

        with self.assertRaisesRegex(ValueError, "JPEG 像素数过大"):
            create_daily_copy(source, self.output, "rejected-jpeg-hash")

    def test_animated_gif_stays_animated_gif(self):
        source = self.tmp / "animated.gif"
        frames = [
            Image.new("RGBA", (64, 64), (255, 0, 0, 255)),
            Image.new("RGBA", (64, 64), (0, 0, 255, 180)),
        ]
        frames[0].save(
            source,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[80, 120],
            loop=0,
        )

        result = create_daily_copy(source, self.output, "gif-hash")

        self.assertEqual(result["mime_type"], "image/gif")
        with Image.open(self.output / result["filename"]) as image:
            self.assertEqual(image.format, "GIF")
            self.assertEqual(image.n_frames, 2)

    def test_single_frame_gif_uses_scalar_duration(self):
        source = self.tmp / "single-frame.gif"
        Image.new("P", (32, 32), 1).save(source, format="GIF", duration=90, loop=0)

        result = create_daily_copy(source, self.output, "single-gif-hash")

        self.assertEqual(result["mime_type"], "image/gif")
        with Image.open(self.output / result["filename"]) as image:
            self.assertEqual(image.format, "GIF")
            self.assertEqual(image.n_frames, 1)

    def test_fallback_copy_uses_detected_format_not_misleading_extension(self):
        from src import media_derivative

        source = self.tmp / "actually-png.jpg"
        Image.new("RGB", (1, 1), (10, 20, 30)).save(source, format="PNG")

        original_saver = media_derivative._save_static_webp

        def save_oversized(image, path):
            original_saver(image, path)
            with open(path, "ab") as output:
                output.write(b"padding" * 100)

        with mock.patch(
            "src.media_derivative._save_static_webp", side_effect=save_oversized
        ):
            result = create_daily_copy(source, self.output, "format/hash")

        self.assertTrue(result["filename"].endswith(".png"))
        self.assertNotIn("/", result["filename"])
        self.assertEqual(result["mime_type"], "image/png")
        with Image.open(self.output / result["filename"]) as image:
            self.assertEqual(image.format, "PNG")

    @unittest.skipUnless(features.check("webp"), "Pillow 未启用 WebP")
    def test_animated_webp_stays_animated_webp(self):
        source = self.tmp / "animated.webp"
        frames = [
            Image.new("RGBA", (64, 64), (255, 0, 0, 255)),
            Image.new("RGBA", (64, 64), (0, 255, 0, 160)),
        ]
        frames[0].save(
            source,
            format="WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=[70, 90],
            loop=0,
            lossless=True,
        )

        result = create_daily_copy(source, self.output, "webp-hash")

        self.assertEqual(result["mime_type"], "image/webp")
        with Image.open(self.output / result["filename"]) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.n_frames, 2)

class TestLargeOriginalImport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.data = self.tmp / "data"
        self.cache = self.tmp / "cache"
        self.cache.mkdir()
        data_patch = mock.patch("src.config._get_data_dir", return_value=self.data)
        data_patch.start()
        self.addCleanup(data_patch.stop)
        self.cfg = Config(self.tmp / "config.json")
        self.cfg.set("cache_dir", str(self.cache))
        self.db = MemeDB(self.tmp / "memes.db")
        self.addCleanup(self.db.close)

    def test_large_dimensions_import_with_limit_off_and_keep_small_daily_copy(self):
        from src.webui import WebUI

        source = self.tmp / "wide-original.png"
        Image.new("RGB", (3000, 40), (30, 90, 150)).save(source)
        original = source.read_bytes()
        ui = WebUI.__new__(WebUI)
        ui._cfg = self.cfg
        with (
            mock.patch("src.webui.get_config", return_value=self.cfg),
            mock.patch("src.webui.get_db", return_value=self.db),
            mock.patch("src.webui.add_memes_to_manifest", return_value=True),
            mock.patch("src.library_events.notify_changed"),
        ):
            self.cfg.set("import_resolution_limit_enabled", True)
            rejected = ui._do_import([str(source)])
            self.assertEqual(rejected["rejected"], 1)
            self.assertEqual(rejected["ids"], [])
            self.cfg.set("import_resolution_limit_enabled", False)
            result = ui._do_import([str(source)])

        self.assertEqual(result["rejected"], 0)
        row = self.db.get_by_id(result["ids"][0])
        self.assertEqual(row["width"], 3000)
        self.assertEqual((self.cache / row["filename"]).read_bytes(), original)
        with Image.open(self.cfg.derivative_dir / row["daily_filename"]) as image:
            self.assertLessEqual(max(image.size), DAILY_COPY_MAX_SIDE)

    def test_single_image_checks_custom_limit_and_size_independently(self):
        from src.webui import _prepare_image_import

        source = self.tmp / "custom-limit.png"
        Image.new("RGB", (3000, 40), (30, 90, 150)).save(source)
        with (
            mock.patch("src.webui.get_config", return_value=self.cfg),
            mock.patch("src.webui.get_db", return_value=self.db),
            mock.patch("src.webui._find_similar_candidates", return_value=[]),
        ):
            self.assertEqual(_prepare_image_import(source)["status"], "ok")
            self.cfg.set("import_resolution_limit_enabled", True)
            self.cfg.set("import_resolution_limit_px", 2999)
            rejected = _prepare_image_import(source)
            self.assertEqual(rejected["status"], "rejected")
            self.assertIn("2999", rejected["error"])
            self.cfg.set("import_resolution_limit_px", 3000)
            self.assertEqual(_prepare_image_import(source)["status"], "ok")
            self.cfg.set("import_resolution_limit_enabled", False)
            with mock.patch("src.webui._IMPORT_MAX_BYTES", 1):
                rejected = _prepare_image_import(source)
                self.assertEqual(rejected["status"], "rejected")
                self.assertIn("原件上限", rejected["error"])

    def test_cache_scan_respects_optional_resolution_limit(self):
        from src.webui import WebUI

        source = self.cache / "wide-scan.png"
        Image.new("RGB", (3000, 40), (30, 90, 150)).save(source)
        ui = WebUI.__new__(WebUI)
        ui._cfg = self.cfg
        with (
            mock.patch("src.webui.get_config", return_value=self.cfg),
            mock.patch("src.webui.get_db", return_value=self.db),
            mock.patch("src.webui.build_manifest"),
        ):
            self.cfg.set("import_resolution_limit_enabled", True)
            self.assertEqual(ui.scan_cache(), 0)
            self.assertTrue(source.is_file())
            self.cfg.set("import_resolution_limit_enabled", False)
            self.assertEqual(ui.scan_cache(), 1)
            self.cfg.set("import_resolution_limit_enabled", True)
            self.assertEqual(ui.scan_cache(), 0)
            self.assertIsNotNone(self.db.get_by_filename(source.name))

    def test_import_keeps_over_20_mib_original_and_builds_small_daily_copy(self):
        from src.webui import WebUI

        source = self.tmp / "padded.png"
        Image.new("RGB", (32, 32), (80, 120, 160)).save(source)
        with source.open("ab") as output:
            output.truncate(20 * 1024 * 1024 + 1)
        source_size = source.stat().st_size

        ui = WebUI.__new__(WebUI)
        ui._cfg = self.cfg
        with (
            mock.patch("src.webui.get_config", return_value=self.cfg),
            mock.patch("src.webui.get_db", return_value=self.db),
            mock.patch("src.webui.add_memes_to_manifest", return_value=True),
            mock.patch("src.webui.request_manifest_rebuild") as rebuild,
            mock.patch("src.webui.build_manifest") as full_rebuild,
            mock.patch("src.library_events.notify_changed") as notify_changed,
        ):
            result = ui._do_import([str(source)], ["大原件"])
            duplicate = ui._do_import([str(source)], ["大原件"])

        self.assertEqual(result["rejected"], 0)
        self.assertEqual(len(result["ids"]), 1)
        self.assertEqual(duplicate["skipped_dup"], 1)
        self.assertEqual(duplicate["duplicate_ids"], result["ids"])
        row = self.db.get_by_id(result["ids"][0])
        self.assertEqual(row["file_size"], source_size)
        self.assertGreater(row["file_size"], 20 * 1024 * 1024)
        self.assertTrue((self.cache / row["filename"]).is_file())
        daily = self.data / "derivatives" / row["daily_filename"]
        self.assertTrue(daily.is_file())
        self.assertLess(daily.stat().st_size, source_size)
        rebuild.assert_not_called()
        full_rebuild.assert_not_called()
        notify_changed.assert_called_once_with("structure")

    def test_import_over_20_mib_can_archive_only_compressed_copy(self):
        from src.webui import WebUI

        source = self.tmp / "compressed-only.png"
        Image.new("RGB", (64, 64), (30, 90, 150)).save(source)
        with source.open("ab") as output:
            output.truncate(20 * 1024 * 1024 + 1)
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()

        ui = WebUI.__new__(WebUI)
        ui._cfg = self.cfg
        with (
            mock.patch("src.webui.get_config", return_value=self.cfg),
            mock.patch("src.webui.get_db", return_value=self.db),
            mock.patch("src.webui.add_memes_to_manifest", return_value=True),
            mock.patch("src.library_events.notify_changed"),
        ):
            result = ui._do_import(
                [str(source)],
                ["仅压缩"],
                large_file_mode="compressed_only",
            )
            duplicate = ui._do_import(
                [str(source)],
                ["仅压缩"],
                large_file_mode="compressed_only",
            )

        self.assertEqual(len(result["ids"]), 1)
        self.assertEqual(duplicate["duplicate_ids"], result["ids"])
        row = self.db.get_by_id(result["ids"][0])
        stored = self.cache / row["filename"]
        self.assertEqual(row["compressed_only"], 1)
        self.assertEqual(row["source_file_hash"], source_hash)
        self.assertNotEqual(row["file_hash"], source_hash)
        self.assertEqual(row["daily_filename"], row["filename"])
        self.assertTrue(stored.is_file())
        self.assertLess(stored.stat().st_size, source.stat().st_size)
        self.assertFalse(
            (self.cfg.derivative_dir / row["daily_filename"]).exists()
        )

    def test_normal_copy_uses_daily_asset_and_original_copy_uses_source(self):
        from src.webui import JsApi, WebUI

        source = self.cache / "original.png"
        Image.new("RGB", (1200, 800), (80, 120, 160)).save(source)
        file_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        daily_result = create_daily_copy(source, self.cfg.derivative_dir, file_hash)
        meme_id = self.db.add_meme(
            source.name,
            file_hash=file_hash,
            daily_filename=daily_result["filename"],
            daily_file_size=daily_result["file_size"],
            daily_mime_type=daily_result["mime_type"],
            daily_profile=daily_result["profile"],
        )
        ui = WebUI.__new__(WebUI)
        ui._cfg = self.cfg
        ui._file_cache = {}
        ui.schedule_hide = mock.Mock()
        api = JsApi.__new__(JsApi)
        api._cfg = self.cfg
        api._db = self.db
        api._webui = ui
        self.cfg.set("copy_resize_mode", 0)

        copied = []
        with mock.patch(
            "src.webui.copy_image_to_clipboard",
            side_effect=lambda path: copied.append(Path(path)) or True,
        ):
            self.assertTrue(api.copy_meme(meme_id)["ok"])
            self.assertTrue(api.copy_meme_original(meme_id)["ok"])

        self.assertEqual(copied[0], self.cfg.derivative_dir / daily_result["filename"])
        self.assertTrue(copied[1].samefile(source))

    def test_delete_removes_original_daily_copy_and_thumbnail(self):
        from src.webui import JsApi, WebUI

        source = self.cache / "delete.png"
        Image.new("RGB", (80, 80), (30, 60, 90)).save(source)
        file_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        daily_result = create_daily_copy(source, self.cfg.derivative_dir, file_hash)
        meme_id = self.db.add_meme(
            source.name,
            file_hash=file_hash,
            daily_filename=daily_result["filename"],
            daily_file_size=daily_result["file_size"],
            daily_mime_type=daily_result["mime_type"],
            daily_profile=daily_result["profile"],
        )
        thumb = self.cfg.thumbnail_dir / f"{meme_id}_150.png"
        thumb.write_bytes(b"thumbnail")
        ui = WebUI.__new__(WebUI)
        ui._cfg = self.cfg
        ui._file_cache = {}
        api = JsApi.__new__(JsApi)
        api._cfg = self.cfg
        api._db = self.db
        api._webui = ui

        with (
            mock.patch("src.webui.remove_memes_from_manifest", return_value=True),
            mock.patch("src.library_events.notify_changed") as notify_changed,
        ):
            self.assertTrue(api.delete_meme(meme_id))

        self.assertFalse(source.exists())
        self.assertFalse((self.cfg.derivative_dir / daily_result["filename"]).exists())
        self.assertFalse(thumb.exists())
        self.assertIsNone(self.db.get_by_id(meme_id))
        notify_changed.assert_called_once_with("structure")

    def test_thumbnail_from_local_daily_copy_skips_virtual_drive_gate(self):
        from src.webui import WebUI

        source = self.cache / "thumbnail-source.png"
        Image.new("RGB", (1200, 800), (20, 70, 120)).save(source)
        file_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        daily_result = create_daily_copy(source, self.cfg.derivative_dir, file_hash)
        meme_id = self.db.add_meme(
            source.name,
            file_hash=file_hash,
            daily_filename=daily_result["filename"],
            daily_file_size=daily_result["file_size"],
            daily_mime_type=daily_result["mime_type"],
            daily_profile=daily_result["profile"],
        )
        ui = WebUI.__new__(WebUI)
        ui._cfg = self.cfg
        ui._file_cache = {}

        with (
            mock.patch("src.webui.get_db", return_value=self.db),
            mock.patch(
                "src.webui.guarded_read",
                side_effect=AssertionError("本地日常副本不应占用虚拟盘读取额度"),
            ),
        ):
            thumbnail = Path(ui._get_thumbnail_path(meme_id, source.name))

        self.assertTrue(thumbnail.is_file())
        self.assertEqual(thumbnail.parent, self.cfg.thumbnail_dir)

    def test_missing_daily_copy_is_rebuilt_lazily_and_old_copy_is_removed(self):
        from src.webui import WebUI

        source = self.cache / "restored.png"
        Image.new("RGB", (1800, 900), (20, 90, 140)).save(source)
        file_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        stale_name = "stale_daily_v0.png"
        stale_path = self.cfg.derivative_dir / stale_name
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_bytes(b"obsolete")
        meme_id = self.db.add_meme(
            source.name,
            file_hash=file_hash,
            daily_filename=stale_name,
            daily_file_size=stale_path.stat().st_size,
            daily_mime_type="image/png",
            daily_profile=0,
        )
        ui = WebUI.__new__(WebUI)
        ui._cfg = self.cfg
        ui._file_cache = {}

        with mock.patch("src.webui.get_db", return_value=self.db):
            daily_path = Path(ui._find_daily_file(self.db.get_by_id(meme_id)))

        row = self.db.get_by_id(meme_id)
        self.assertTrue(daily_path.is_file())
        self.assertEqual(daily_path.name, row["daily_filename"])
        self.assertEqual(row["daily_profile"], 1)
        self.assertFalse(stale_path.exists())
        with Image.open(daily_path) as image:
            self.assertLessEqual(max(image.size), DAILY_COPY_MAX_SIDE)

    def test_old_database_migration_adds_daily_asset_columns(self):
        rows = self.db._get_conn().execute("PRAGMA table_info(memes)").fetchall()
        columns = {row[1] for row in rows}
        self.assertTrue(
            {"daily_filename", "daily_file_size", "daily_mime_type", "daily_profile"}
            <= columns
        )


if __name__ == "__main__":
    unittest.main()
