import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_nodes" / "syncanvas_templates"
MANIFEST = json.loads((PACKAGE / "node.json").read_text(encoding="utf-8"))
CLASSIC = (ROOT / "static" / "js" / "canvas" / "state.js").read_text(encoding="utf-8")
SMART = (ROOT / "static" / "js" / "smart-canvas" / "state.js").read_text(encoding="utf-8")


def load_template_module():
    name = "syncanvas_templates_contract_test"
    spec = importlib.util.spec_from_file_location(name, PACKAGE / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(name, None)


class TemplateExtensionNodeTests(unittest.TestCase):
    def test_store_and_call_expose_only_one_prompt_output(self):
        self.assertEqual("1.1.0", MANIFEST["version"])
        for node in MANIFEST["nodes"]:
            self.assertEqual(
                [{"id": "text", "name": "Prompt", "name_zh": "提示词", "types": ["text"]}],
                node["outputs"],
            )

    def test_backend_result_contains_only_the_prompt_port(self):
        module = load_template_module()
        result = module._outputs({"style_prompt_zh": "干净的商业摄影风格"})
        self.assertEqual(["text"], list(result["outputs"]))
        self.assertEqual("干净的商业摄影风格", result["outputs"]["text"]["value"])
        english = module._outputs({"style_prompt_en": "clean commercial photography"})
        self.assertEqual("clean commercial photography", english["outputs"]["text"]["value"])

    def test_old_template_output_ports_migrate_to_text(self):
        self.assertIn("templateOutputIds.has(connection.from) ? {...connection, fromPort:'text'}", CLASSIC)
        self.assertIn("templateOutputIds.has(connection.from) ? {...connection, fromPort:'text'}", SMART)

    def test_template_references_are_not_forwarded_to_downstream_nodes(self):
        self.assertIn("if(!promptOnlyTemplate) (source.images || source.referenceImages || [])", CLASSIC)
        self.assertIn("if(!promptOnlyTemplate) (source?.images || source?.referenceImages || [])", CLASSIC)
        self.assertIn("if(!promptOnlyTemplate) (source.images || source.referenceImages || source._templateImages || [])", SMART)
        self.assertIn("if(!promptOnlyTemplate) (source?.images || source?.referenceImages || source?._templateImages || [])", SMART)

    def test_smart_template_aliases_render_manifest_ports(self):
        self.assertIn("const templateAlias = ['smart-template-store','smart-template-call'].includes(node?.type)", SMART)
        self.assertIn("node?.type === definition.type || templateAlias", SMART)


if __name__ == "__main__":
    unittest.main()
