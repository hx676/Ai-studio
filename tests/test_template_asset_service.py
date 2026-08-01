import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from app import upstream_runtime
from app.api import template_assets as template_assets_api
from app.models.template_assets import TemplateAssetCreateRequest, TemplateAssetUpdateRequest
from app.services import template_asset_service


class TemplateAssetServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.originals = {}
        replacements = {
            "ASSETS_DIR": str(self.root / "assets"),
            "ASSET_LIBRARY_DIR": str(self.root / "assets" / "library"),
            "DATA_DIR": str(self.root / "data"),
            "ASSET_LIBRARY_PATH": str(self.root / "data" / "asset_library.json"),
            "OUTPUT_DIR": str(self.root / "output"),
            "OUTPUT_OUTPUT_DIR": str(self.root / "output"),
            "OUTPUT_INPUT_DIR": str(self.root / "assets" / "input"),
            "GLOBAL_LOOP": None,
        }
        for name, value in replacements.items():
            self.originals[name] = getattr(upstream_runtime, name)
            setattr(upstream_runtime, name, value)
        (self.root / "assets" / "library").mkdir(parents=True)
        (self.root / "assets" / "input").mkdir(parents=True)
        (self.root / "output").mkdir(parents=True)
        (self.root / "data").mkdir(parents=True)
        self.image_path = self.root / "assets" / "input" / "reference.png"
        Image.new("RGB", (40, 30), (36, 112, 164)).save(self.image_path)

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(upstream_runtime, name, value)
        self.temp_dir.cleanup()

    @staticmethod
    def template(version=1):
        return {
            "name": "海报模板",
            "features": ["清晰网格", "高对比标题"],
            "stylePromptZh": f"现代编辑风格 v{version}",
            "pageStyles": {"cover": {"layout": "center"}},
            "unknownField": {"keep": True},
        }

    def create_request(self, **overrides):
        data = {
            "name": "海报模板",
            "template": self.template(),
            "thumbnail_url": "/assets/input/reference.png",
            "reference_image_urls": ["/assets/input/reference.png"],
            "source_canvas_id": "canvas-1",
            "source_node_id": "skill-1",
        }
        data.update(overrides)
        return TemplateAssetCreateRequest(**data)

    def test_old_library_normalization_adds_template_category_without_changing_assets(self):
        old_item = {"id": "asset_old", "name": "旧图", "url": "/assets/input/reference.png", "kind": "image"}
        old = {
            "active_library_id": "default",
            "libraries": [{"id": "default", "name": "默认资产库", "categories": [{"id": "images", "name": "图片", "type": "image", "items": [old_item]}]}],
        }
        normalized = upstream_runtime.normalize_asset_library(old)
        categories = normalized["libraries"][0]["categories"]
        self.assertTrue(any(category.get("type") == "template" and category.get("default") for category in categories))
        self.assertEqual(categories[0]["items"][0]["id"], "asset_old")

    def test_create_read_update_move_and_delete_template(self):
        created = template_asset_service.create_template_asset(self.create_request())
        item = created["item"]
        template_id = item["id"]
        self.assertRegex(template_id, r"^tmpl_[a-f0-9]{12}$")
        self.assertEqual(item["kind"], "template")
        self.assertTrue(item["thumbnail_url"].startswith(f"/assets/library/templates/{template_id}/"))
        self.assertTrue((template_asset_service.template_directory(template_id) / "template.json").is_file())
        self.assertTrue(template_asset_service.get_template_asset(template_id)["template"]["unknownField"]["keep"])

        lib = upstream_runtime.load_asset_library()
        default_library = upstream_runtime.find_asset_library(lib, "default")
        target_category = {"id": "template_custom", "name": "商业模板", "type": "template", "items": []}
        default_library["categories"].append(target_category)
        upstream_runtime.save_asset_library(lib)

        updated = template_asset_service.update_template_asset(
            template_id,
            TemplateAssetUpdateRequest(name="新版海报", category_id="template_custom", template=self.template(2)),
        )
        self.assertEqual(updated["item"]["id"], template_id)
        self.assertEqual(updated["item"]["created_at"], item["created_at"])
        self.assertEqual(updated["item"]["category_id"], "template_custom")
        self.assertEqual(updated["template"]["stylePromptZh"], "现代编辑风格 v2")
        self.assertEqual(template_asset_service.get_template_asset(template_id)["template"]["unknownField"], {"keep": True})

        deleted = template_asset_service.delete_template_asset(template_id)
        self.assertEqual(deleted["deleted"], template_id)
        self.assertFalse(template_asset_service.template_directory(template_id).exists())
        with self.assertRaises(HTTPException) as missing:
            template_asset_service.get_template_asset(template_id)
        self.assertEqual(missing.exception.status_code, 404)

    def test_update_images_keeps_id_and_replaces_controlled_files(self):
        created = template_asset_service.create_template_asset(self.create_request(reference_image_urls=[]))
        template_id = created["item"]["id"]
        second = self.root / "assets" / "input" / "second.jpg"
        Image.new("RGB", (24, 24), (196, 67, 57)).save(second)
        updated = template_asset_service.update_template_asset(
            template_id,
            TemplateAssetUpdateRequest(
                thumbnail_url="/assets/input/second.jpg",
                reference_image_urls=["/assets/input/reference.png"],
            ),
        )
        self.assertEqual(updated["item"]["id"], template_id)
        self.assertTrue(updated["item"]["thumbnail_url"].endswith("thumbnail.jpg"))
        self.assertEqual(len(updated["item"]["reference_image_urls"]), 1)
        self.assertEqual(len(list(template_asset_service.template_directory(template_id).glob("thumbnail.*"))), 1)

    def test_rejects_untrusted_paths_sensitive_fields_and_large_json(self):
        with self.assertRaises(HTTPException) as untrusted:
            template_asset_service.create_template_asset(self.create_request(thumbnail_url="https://example.com/ref.png"))
        self.assertEqual(untrusted.exception.status_code, 400)

        with self.assertRaises(HTTPException) as sensitive:
            template_asset_service.create_template_asset(self.create_request(template={"features": [], "api_key": "secret"}))
        self.assertEqual(sensitive.exception.status_code, 400)
        self.assertIn("敏感字段", sensitive.exception.detail)

        with self.assertRaises(HTTPException) as oversized:
            template_asset_service.create_template_asset(self.create_request(template={"features": ["x" * (2 * 1024 * 1024)]}))
        self.assertEqual(oversized.exception.status_code, 413)

    def test_reference_images_are_deduplicated_and_capped_at_eight(self):
        urls = []
        for index in range(10):
            path = self.root / "assets" / "input" / f"ref-{index}.png"
            Image.new("RGB", (8, 8), (index, index, index)).save(path)
            urls.append(f"/assets/input/ref-{index}.png")
        created = template_asset_service.create_template_asset(
            self.create_request(thumbnail_url="", reference_image_urls=urls + [urls[0]])
        )
        self.assertEqual(len(created["item"]["reference_image_urls"]), 8)
        index_data = json.loads(Path(upstream_runtime.ASSET_LIBRARY_PATH).read_text(encoding="utf-8"))
        serialized = json.dumps(index_data, ensure_ascii=False)
        self.assertNotIn("stylePromptZh", serialized)


class TemplateAssetApiAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_disk_persistence_is_offloaded_from_the_event_loop(self):
        event_loop_thread = threading.get_ident()

        def create_in_worker(payload):
            return {"worker_thread": threading.get_ident(), "payload": payload}

        marker = object()
        with patch.object(template_asset_service, "create_template_asset", side_effect=create_in_worker):
            result = await template_assets_api.create_template(marker)

        self.assertIs(result["payload"], marker)
        self.assertNotEqual(result["worker_thread"], event_loop_thread)


if __name__ == "__main__":
    unittest.main()
