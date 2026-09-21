import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from src import notion_import


class FakeClient:
    def __init__(self, token, version):
        self.token = token
        self.version = version

    def resolve_data_source(self, database_id, data_source_id=""):
        return "b" * 32

    def schema(self, data_source_id):
        return {
            "properties": {
                "名称": {"type": "title"},
                "图片": {"type": "files"},
                "标签": {"type": "multi_select"},
                "分组": {"type": "select"},
            }
        }

    def query_pages(self, data_source_id):
        return [
            {
                "id": "page-1",
                "last_edited_time": "2026-09-19T08:00:00.000Z",
                "properties": {
                    "名称": {
                        "type": "title",
                        "title": [{"plain_text": "开心"}],
                    },
                    "图片": {
                        "id": "media-property",
                        "type": "files",
                        "files": [
                            {
                                "name": "happy.png",
                                "type": "file",
                                "file": {"url": "https://example.com/happy.png"},
                            }
                        ],
                    },
                    "标签": {
                        "type": "multi_select",
                        "multi_select": [{"name": "开心"}, {"name": "猫"}],
                    },
                    "分组": {"type": "select", "select": {"name": "常用"}},
                },
            }
        ]


class FakeDB:
    def __init__(self):
        self.collections = {}
        self.members = []
        self.tags = []
        self.sources = {}
        self.hash_row = None

    def create_collection(self, name, parent_id=None):
        key = (name, parent_id)
        if key not in self.collections:
            self.collections[key] = len(self.collections) + 1
        return self.collections[key]

    def add_to_collection(self, meme_id, collection_id):
        self.members.append((meme_id, collection_id))

    def add_tags_to_memes(self, meme_ids, tags):
        self.tags.append((list(meme_ids), list(tags)))
        return len(meme_ids)

    def get_notion_source(
        self,
        data_source_id,
        page_id,
        property_id,
        file_index,
        file_name,
        page_last_edited_time,
    ):
        key = (data_source_id, page_id, property_id, file_index, file_name)
        source = self.sources.get(key)
        if source and source["page_last_edited_time"] == page_last_edited_time:
            return dict(source)
        return None

    def upsert_notion_source(
        self,
        meme_id,
        data_source_id,
        page_id,
        property_id,
        file_index,
        file_name,
        page_last_edited_time,
    ):
        key = (data_source_id, page_id, property_id, file_index, file_name)
        self.sources[key] = {
            "meme_id": meme_id,
            "filename": "cached.png",
            "page_last_edited_time": page_last_edited_time,
        }

    def get_by_hash(self, file_hash):
        return self.hash_row


class FakeWebUI:
    def __init__(self):
        self.calls = 0

    def _do_import(self, paths, names=None, progress_cb=None):
        self.calls += 1
        return {"ids": [7], "rejected": 0, "skipped_dup": 0}


class TestNotionImport(unittest.TestCase):
    def setUp(self):
        notion_import._NOTION_CANCEL = False
        notion_import._update_state(status="idle")

    def test_normalize_notion_url_prefers_path_id(self):
        database_id = "11111111222233334444555555555555"
        view_id = "aaaaaaaaaaaabbbbccccdddddddddddd"
        url = f"https://www.notion.so/workspace/Test-{database_id}?v={view_id}"
        self.assertEqual(notion_import.normalize_notion_id(url), database_id)

    def test_restore_cached_file_repairs_missing_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.download"
            cache = root / "cache"
            source.write_bytes(b"same-image")

            class FakeConfig:
                cache_dir = cache

            with patch.object(notion_import, "get_config", return_value=FakeConfig()):
                restored = notion_import._restore_cached_file(
                    source, {"filename": "existing.png"}
                )

            self.assertTrue(restored)
            self.assertEqual((cache / "existing.png").read_bytes(), b"same-image")

    def test_download_media_retries_transient_network_error(self):
        class FakeResponse:
            headers = {}

            def __init__(self):
                self.chunks = [b"\x89PNG\r\n\x1a\n", b""]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                return self.chunks.pop(0)

        calls = 0

        def opener(_request, timeout=30):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise urllib.error.URLError("temporary SSL EOF")
            return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.download"
            with (
                patch.object(notion_import, "_validate_public_url"),
                patch.object(notion_import, "_wait_download_retry"),
            ):
                result = notion_import.download_media(
                    "https://example.com/image.png", path, opener=opener
                )

            self.assertEqual(result, path)
            self.assertEqual(calls, 3)
            self.assertTrue(path.exists())

    def test_download_media_reports_retry_exhaustion_without_partial_file(self):
        calls = 0

        def opener(_request, timeout=30):
            nonlocal calls
            calls += 1
            raise urllib.error.URLError("temporary SSL EOF")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "image.download"
            with (
                patch.object(notion_import, "_validate_public_url"),
                patch.object(notion_import, "_wait_download_retry"),
                self.assertRaisesRegex(notion_import.NotionImportError, "已重试 3 次"),
            ):
                notion_import.download_media(
                    "https://example.com/image.png", path, opener=opener
                )

            self.assertEqual(calls, 4)
            self.assertFalse(path.exists())

    def test_extract_page_media_maps_title_tags_and_group(self):
        page = FakeClient("", "").query_pages("")[0]
        settings = {
            "notion_media_property": "图片",
            "notion_title_property": "名称",
            "notion_tags_property": "标签",
            "notion_collection_property": "分组",
        }
        records = notion_import.extract_page_media(page, settings)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["name"], "开心")
        self.assertEqual(
            records[0]["tags"],
            [
                {"category": "标签", "name": "开心"},
                {"category": "标签", "name": "猫"},
            ],
        )
        self.assertEqual(records[0]["collections"], ["常用"])
        self.assertEqual(records[0]["property_id"], "media-property")
        self.assertEqual(records[0]["file_index"], 0)
        self.assertEqual(
            records[0]["page_last_edited_time"], "2026-09-19T08:00:00.000Z"
        )

    def test_test_connection_returns_schema(self):
        result = notion_import.test_notion_connection(
            {
                "notion_token": "token",
                "notion_data_source_id": "b" * 32,
            },
            client_factory=FakeClient,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["data_source_id"], "b" * 32)
        self.assertEqual(
            {item["name"] for item in result["properties"]},
            {"名称", "图片", "标签", "分组"},
        )

    def test_relation_titles_map_to_tags(self):
        tag_id = "c" * 32
        page = FakeClient("", "").query_pages("")[0]
        page["properties"]["标签"] = {
            "type": "relation",
            "relation": [{"id": tag_id}],
        }

        class RelationClient:
            def retrieve_page(self, page_id):
                self.page_id = page_id
                return {
                    "properties": {
                        "名称": {
                            "type": "title",
                            "title": [{"plain_text": "关系标签"}],
                        }
                    }
                }

        settings = {
            "notion_media_property": "图片",
            "notion_title_property": "名称",
            "notion_tags_property": "标签",
        }
        client = RelationClient()
        names = notion_import.resolve_relation_names(client, [page], settings)
        records = notion_import.extract_page_media(page, settings, names)
        self.assertEqual(client.page_id, tag_id)
        self.assertEqual(
            records[0]["tags"],
            [{"category": "标签", "name": "关系标签"}],
        )

    def test_multiple_tag_columns_keep_separate_categories(self):
        page = FakeClient("", "").query_pages("")[0]
        page["properties"]["情绪"] = {
            "type": "multi_select",
            "multi_select": [{"name": "开心"}],
        }
        settings = {
            "notion_media_property": "图片",
            "notion_tag_mappings": [
                {"category": "内容", "property": "标签"},
                {"category": "情绪", "property": "情绪"},
            ],
        }

        records = notion_import.extract_page_media(page, settings)

        self.assertEqual(
            records[0]["tags"],
            [
                {"category": "内容", "name": "开心"},
                {"category": "内容", "name": "猫"},
                {"category": "情绪", "name": "开心"},
            ],
        )

    def test_query_pages_follows_cursor(self):
        class PagedClient(notion_import.NotionClient):
            def __init__(self):
                super().__init__("token")
                self.calls = []

            def request(self, method, path, payload=None):
                self.calls.append((method, path, payload))
                if len(self.calls) == 1:
                    return {
                        "results": [{"id": "one"}],
                        "has_more": True,
                        "next_cursor": "cursor",
                    }
                return {"results": [{"id": "two"}], "has_more": False}

        client = PagedClient()
        pages = client.query_pages("source")
        self.assertEqual([page["id"] for page in pages], ["one", "two"])
        self.assertEqual(client.calls[1][2]["start_cursor"], "cursor")

    def test_relation_property_pagination(self):
        class RelationClient(notion_import.NotionClient):
            def __init__(self):
                super().__init__("token")
                self.paths = []

            def request(self, method, path, payload=None):
                self.paths.append(path)
                if len(self.paths) == 1:
                    return {
                        "results": [{"relation": {"id": "c" * 32}}],
                        "has_more": True,
                        "next_cursor": "next",
                    }
                return {
                    "results": [{"relation": {"id": "d" * 32}}],
                    "has_more": False,
                }

        client = RelationClient()
        result = client.retrieve_relation_ids("a" * 32, "tag%5Cid")
        self.assertEqual(result, ["c" * 32, "d" * 32])
        self.assertIn("start_cursor=next", client.paths[1])

    def test_background_import_applies_tags_and_collections(self):
        db = FakeDB()
        webui = FakeWebUI()

        def fake_download(url, path, cancel_check=None, opener=None):
            with open(path, "wb") as file:
                file.write(b"\x89PNG\r\n\x1a\n")
            return path

        settings = {
            "notion_token": "token",
            "notion_data_source_id": "b" * 32,
            "notion_media_property": "图片",
            "notion_title_property": "名称",
            "notion_tags_property": "标签",
            "notion_collection_property": "分组",
        }
        with (
            patch.object(notion_import, "get_db", return_value=db),
            patch.object(notion_import, "build_manifest"),
            patch.object(notion_import, "download_media", side_effect=fake_download),
        ):
            result = notion_import.start_notion_import(
                webui, settings, client_factory=FakeClient
            )
            self.assertTrue(result["ok"])
            deadline = time.time() + 2
            while time.time() < deadline:
                state = notion_import.get_notion_progress()
                if state["status"] != "running":
                    break
                time.sleep(0.01)

        state = notion_import.get_notion_progress()
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["imported"], 1)
        self.assertEqual(
            db.tags,
            [
                (
                    [7],
                    [
                        {"category": "标签", "name": "开心"},
                        {"category": "标签", "name": "猫"},
                    ],
                )
            ],
        )
        root = db.collections[("Notion", None)]
        child = db.collections[("常用", root)]
        self.assertIn((7, root), db.members)
        self.assertIn((7, child), db.members)
        self.assertEqual(webui.calls, 1)
        self.assertEqual(len(db.sources), 1)

    def test_unchanged_notion_file_skips_download(self):
        db = FakeDB()
        webui = FakeWebUI()
        db.upsert_notion_source(
            7,
            "b" * 32,
            "page-1",
            "media-property",
            0,
            "happy.png",
            "2026-09-19T08:00:00.000Z",
        )
        settings = {
            "notion_token": "token",
            "notion_data_source_id": "b" * 32,
            "notion_media_property": "图片",
            "notion_title_property": "名称",
            "notion_tags_property": "标签",
            "notion_collection_property": "分组",
        }
        with (
            patch.object(notion_import, "get_db", return_value=db),
            patch.object(notion_import, "build_manifest"),
            patch.object(notion_import, "_source_file_exists", return_value=True),
            patch.object(notion_import, "download_media") as download,
        ):
            result = notion_import.start_notion_import(
                webui, settings, client_factory=FakeClient
            )
            self.assertTrue(result["ok"])
            deadline = time.time() + 2
            while time.time() < deadline:
                state = notion_import.get_notion_progress()
                if state["status"] != "running":
                    break
                time.sleep(0.01)

        state = notion_import.get_notion_progress()
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["unchanged"], 1)
        self.assertEqual(webui.calls, 0)
        download.assert_not_called()

    def test_cancel_keeps_partial_import_and_flushes_manifest(self):
        db = FakeDB()

        class TwoRecordClient(FakeClient):
            def query_pages(self, data_source_id):
                page = super().query_pages(data_source_id)[0]
                second = {
                    **page,
                    "id": "page-2",
                    "properties": {
                        **page["properties"],
                        "名称": {
                            "type": "title",
                            "title": [{"plain_text": "第二张"}],
                        },
                        "图片": {
                            "id": "media-property",
                            "type": "files",
                            "files": [
                                {
                                    "name": "second.png",
                                    "type": "file",
                                    "file": {"url": "https://example.com/second.png"},
                                }
                            ],
                        },
                    },
                }
                return [page, second]

        class CancelAfterFirstWebUI(FakeWebUI):
            def _do_import(self, paths, names=None, progress_cb=None):
                result = super()._do_import(paths, names, progress_cb)
                notion_import.cancel_notion_import()
                return result

        webui = CancelAfterFirstWebUI()

        def fake_download(url, path, cancel_check=None, opener=None):
            with open(path, "wb") as file:
                file.write(b"\x89PNG\r\n\x1a\n")
            return path

        settings = {
            "notion_token": "token",
            "notion_data_source_id": "b" * 32,
            "notion_media_property": "图片",
            "notion_title_property": "名称",
            "notion_tags_property": "标签",
            "notion_collection_property": "分组",
        }
        with (
            patch.object(notion_import, "get_db", return_value=db),
            patch.object(notion_import, "build_manifest") as build,
            patch.object(notion_import, "download_media", side_effect=fake_download),
        ):
            result = notion_import.start_notion_import(
                webui, settings, client_factory=TwoRecordClient
            )
            self.assertTrue(result["ok"])
            deadline = time.time() + 2
            while time.time() < deadline:
                state = notion_import.get_notion_progress()
                if state["status"] != "running":
                    break
                time.sleep(0.01)

        state = notion_import.get_notion_progress()
        self.assertEqual(state["status"], "cancelled")
        self.assertEqual(state["imported"], 1)
        self.assertTrue(state["changed"])
        self.assertEqual(webui.calls, 1)
        build.assert_called_once_with()

    def test_existing_hash_creates_source_mapping_for_next_run(self):
        db = FakeDB()
        db.hash_row = {"id": 9, "filename": "existing.png"}

        class DuplicateWebUI(FakeWebUI):
            def _do_import(self, paths, names=None, progress_cb=None):
                self.calls += 1
                return {"ids": [], "rejected": 0, "skipped_dup": 1}

        webui = DuplicateWebUI()

        def fake_download(url, path, cancel_check=None, opener=None):
            with open(path, "wb") as file:
                file.write(b"same-image")
            return path

        settings = {
            "notion_token": "token",
            "notion_data_source_id": "b" * 32,
            "notion_media_property": "图片",
            "notion_title_property": "名称",
        }
        with (
            patch.object(notion_import, "get_db", return_value=db),
            patch.object(notion_import, "build_manifest"),
            patch.object(notion_import, "_source_file_exists", return_value=True),
            patch.object(notion_import, "download_media", side_effect=fake_download),
        ):
            result = notion_import.start_notion_import(
                webui, settings, client_factory=FakeClient
            )
            self.assertTrue(result["ok"])
            deadline = time.time() + 2
            while time.time() < deadline:
                state = notion_import.get_notion_progress()
                if state["status"] != "running":
                    break
                time.sleep(0.01)

        state = notion_import.get_notion_progress()
        self.assertEqual(state["duplicates"], 1)
        self.assertEqual(len(db.sources), 1)
        self.assertEqual(next(iter(db.sources.values()))["meme_id"], 9)


if __name__ == "__main__":
    unittest.main()
