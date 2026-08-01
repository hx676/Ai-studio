import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app import upstream_runtime


def upload(name: str, payload: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(payload), filename=name)


def archive(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as output:
        for name, payload in entries.items():
            output.writestr(name, payload)
    return buffer.getvalue()


class WorkflowImportSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="syncanvas-workflow-security-"))
        self.original_assets = upstream_runtime.ASSETS_DIR
        self.original_input = upstream_runtime.OUTPUT_INPUT_DIR
        upstream_runtime.ASSETS_DIR = str(self.temp / "assets")
        upstream_runtime.OUTPUT_INPUT_DIR = str(self.temp / "assets" / "input")
        Path(upstream_runtime.OUTPUT_INPUT_DIR).mkdir(parents=True)

    def tearDown(self):
        upstream_runtime.ASSETS_DIR = self.original_assets
        upstream_runtime.OUTPUT_INPUT_DIR = self.original_input
        shutil.rmtree(self.temp, ignore_errors=True)

    async def test_rejects_traversal_and_undeclared_entries(self):
        workflow = json.dumps({"nodes": [], "connections": [], "resources": []}).encode()
        with self.assertRaises(HTTPException) as traversal:
            await upstream_runtime.import_canvas_workflow(upload("bad.zip", archive({
                "workflow.json": workflow,
                "../escape.txt": b"bad",
            })))
        self.assertEqual(400, traversal.exception.status_code)
        with self.assertRaises(HTTPException) as undeclared:
            await upstream_runtime.import_canvas_workflow(upload("bad.zip", archive({
                "workflow.json": workflow,
                "resources/hidden.txt": b"bad",
            })))
        self.assertEqual(400, undeclared.exception.status_code)

    async def test_rejects_zip_bomb_ratio_and_oversized_graph(self):
        workflow = json.dumps({"nodes": [], "connections": [], "resources": []}).encode()
        with self.assertRaises(HTTPException) as bomb:
            await upstream_runtime.import_canvas_workflow(upload("bomb.zip", archive({
                "workflow.json": workflow,
                "resources/zeros.bin": b"0" * (11 * 1024 * 1024),
            })))
        self.assertEqual(400, bomb.exception.status_code)
        graph = json.dumps({"nodes": [{}] * 5001, "connections": []}).encode()
        with self.assertRaises(HTTPException) as oversized:
            await upstream_runtime.import_canvas_workflow(upload("large.json", graph))
        self.assertEqual(413, oversized.exception.status_code)

    async def test_declared_resource_imports_inside_asset_root(self):
        workflow = {
            "nodes": [{"id": "one", "type": "image", "url": "old://image"}],
            "connections": [],
            "resources": [{"url": "old://image", "archive": "resources/image.png", "name": "image.png"}],
        }
        result = await upstream_runtime.import_canvas_workflow(upload("valid.zip", archive({
            "workflow.json": json.dumps(workflow).encode(),
            "resources/image.png": b"safe-image",
        })))
        mapped = result["resource_map"]["old://image"]
        self.assertTrue(mapped.startswith("/assets/input/workflow_import_"))
        stored = Path(upstream_runtime.ASSETS_DIR) / mapped.removeprefix("/assets/")
        self.assertEqual(b"safe-image", stored.read_bytes())

    def test_canvas_renderers_escape_imported_titles_and_avoid_inline_delete(self):
        classic = Path("static/js/canvas/state.js").read_text(encoding="utf-8")
        smart = Path("static/js/smart-canvas/state.js").read_text(encoding="utf-8")
        self.assertIn("${escapeHtml(title)}", classic)
        self.assertIn("${escapeHtml(title)}", smart)
        self.assertNotIn("onclick=\"deleteNode('", classic)


if __name__ == "__main__":
    unittest.main()
