import json
import shutil
import tempfile
import unittest
from pathlib import Path

from app.services.node_extension_service import NodeExtensionRegistry


class ImageCompareExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.package = cls.root / "custom_nodes" / "syncanvas_image_compare"
        cls.manifest = json.loads((cls.package / "node.json").read_text(encoding="utf-8"))
        cls.adapter = (cls.package / "web" / "index.js").read_text(encoding="utf-8")
        cls.styles = (cls.package / "web" / "styles.css").read_text(encoding="utf-8")
        cls.classic = (cls.root / "static" / "js" / "canvas" / "state.js").read_text(encoding="utf-8")
        cls.smart = (cls.root / "static" / "js" / "smart-canvas" / "state.js").read_text(encoding="utf-8")

    def test_manifest_declares_two_required_image_inputs_and_no_outputs(self):
        self.assertEqual("syncanvas.image-compare", self.manifest["id"])
        node = self.manifest["nodes"][0]
        self.assertEqual("compare", node["id"])
        self.assertEqual("frontend", node["execution"])
        self.assertEqual(["classic", "smart"], node["surfaces"])
        self.assertEqual([], node["outputs"])
        self.assertEqual(
            [
                {
                    "id": "a",
                    "name": "A",
                    "name_zh": "图片 A",
                    "types": ["image"],
                    "required": True,
                },
                {
                    "id": "b",
                    "name": "B",
                    "name_zh": "图片 B",
                    "types": ["image"],
                    "required": True,
                },
            ],
            node["inputs"],
        )
        self.assertEqual({"position": 50}, node["defaults"])

    def test_frontend_adapter_renders_and_persists_the_compare_slider(self):
        self.assertIn('type="range"', self.adapter)
        self.assertIn('aria-label="图像比对位置"', self.adapter)
        self.assertIn("inputs.a", self.adapter)
        self.assertIn("inputs.b", self.adapter)
        self.assertIn("--compare-position", self.adapter)
        self.assertIn("node.data = {...(node.data || {}), position}", self.adapter)
        self.assertIn("save?.()", self.adapter)
        for transient in ("images", "inputNodeIds", "promptDraftHtml", "promptDraftText"):
            self.assertIn(f"'{transient}'", self.adapter)

    def test_both_canvases_render_manifest_ports_and_save_port_ids(self):
        self.assertIn("function extensionPortMarkup", self.classic)
        self.assertIn('data-port="${escapeAttr(id)}"', self.classic)
        self.assertIn("port.dataset.direction", self.classic)
        self.assertIn("connections.push({id:uid('c'), from:fromId, to:toId, fromPort, toPort})", self.classic)
        self.assertIn("(c.toPort || 'in') === toPort", self.classic)

        self.assertIn("function smartPortMarkup", self.smart)
        self.assertIn("function smartNearestPortId", self.smart)
        self.assertIn('data-direction="${direction}"', self.smart)
        self.assertIn("fromDirection:portDirection", self.smart)
        self.assertIn("connectInputNode(fromId, toId, fromPort, toPort)", self.smart)
        self.assertIn("(c.toPort || 'in') === toPort", self.smart)

    def test_styles_cover_dark_theme_and_contain_node_content(self):
        self.assertIn(".theme-dark .image-compare-node", self.styles)
        self.assertIn(".studio-theme-dark .image-compare-node", self.styles)
        self.assertIn("overflow: hidden", self.styles)
        self.assertIn("min-width: 0", self.styles)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", self.styles)

    def test_frontend_only_package_is_discoverable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_nodes = Path(temp_dir) / "custom_nodes"
            custom_nodes.mkdir()
            shutil.copytree(self.package, custom_nodes / self.package.name)
            registry = NodeExtensionRegistry(custom_nodes, Path(temp_dir) / "state.json")
            state = registry.initialize()
            self.assertEqual("loaded", state["packages"][0]["status"])
            self.assertEqual("syncanvas.image-compare/compare", state["nodes"][0]["type"])
            self.assertEqual("frontend", state["nodes"][0]["execution"])


if __name__ == "__main__":
    unittest.main()
