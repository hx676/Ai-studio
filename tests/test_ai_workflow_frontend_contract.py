import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLASSIC_WORKFLOW = ROOT / "static" / "workflows" / "reference-style-prompt.classic.json"
SMART_WORKFLOW = ROOT / "static" / "workflows" / "reference-style-prompt.smart.json"


class AIWorkflowFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.classic = json.loads(CLASSIC_WORKFLOW.read_text(encoding="utf-8"))
        cls.smart = json.loads(SMART_WORKFLOW.read_text(encoding="utf-8"))
        cls.features = (ROOT / "static" / "js" / "upstream-canvas-features.js").read_text(encoding="utf-8")
        cls.classic_html = (ROOT / "static" / "canvas.html").read_text(encoding="utf-8")
        cls.smart_html = (ROOT / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        cls.manifest = json.loads((ROOT / "custom_nodes" / "syncanvas_agent_skill" / "node.json").read_text(encoding="utf-8"))

    def test_ai_workflow_node_keeps_legacy_ids_but_uses_product_name(self):
        workflow_node = next(item for item in self.manifest["nodes"] if item["id"] == "skill")
        self.assertEqual("AI Workflow", workflow_node["display_name"])
        self.assertEqual("AI 工作流", workflow_node["display_name_zh"])
        self.assertEqual(["skill"], workflow_node["legacy_types"]["classic"])
        self.assertEqual(["smart-skill"], workflow_node["legacy_types"]["smart"])

    def test_both_canvases_expose_the_built_in_example(self):
        self.assertIn('id="canvasWorkflowInsertExample"', self.classic_html)
        self.assertIn('id="smartWorkflowInsertExample"', self.smart_html)
        self.assertIn("reference-style-prompt.classic.json", self.features)
        self.assertIn("reference-style-prompt.smart.json", self.features)
        self.assertIn("WorkflowInsertExample", self.features)

    def test_example_topologies_use_two_built_in_ai_workflows(self):
        expectations = [
            (self.classic, "classic", "image", "prompt", "skill", "generator"),
            (self.smart, "smart", "smart-image", "smart-prompt", "smart-skill", "smart-image"),
        ]
        for workflow, canvas_type, image_type, prompt_type, workflow_type, output_type in expectations:
            with self.subTest(canvas=canvas_type):
                self.assertEqual(canvas_type, workflow["canvas_type"])
                nodes = {item["id"]: item for item in workflow["nodes"]}
                self.assertEqual(image_type, nodes["reference-image"]["type"])
                self.assertEqual(prompt_type, nodes["user-idea"]["type"])
                self.assertEqual(workflow_type, nodes["extract-style"]["type"])
                self.assertEqual(workflow_type, nodes["compose-studio"]["type"])
                self.assertEqual(output_type, nodes["generate-image"]["type"])
                self.assertEqual("extract-style", nodes["extract-style"]["skillId"])
                self.assertEqual("compose-studio", nodes["compose-studio"]["skillId"])
                edges = {(item["from"], item["to"], item.get("targetField", "")) for item in workflow["connections"]}
                self.assertIn(("reference-image", "extract-style", "images"), edges)
                self.assertIn(("extract-style", "compose-studio", "template"), edges)
                self.assertIn(("user-idea", "compose-studio", "userIdea"), edges)
                self.assertIn(("compose-studio", "generate-image", ""), edges)

    def test_import_remaps_ai_workflow_field_bindings(self):
        self.assertIn("node.inputBindings && typeof node.inputBindings === 'object'", self.features)
        self.assertIn("binding.sourceNodeId = idMap.get(binding.sourceNodeId) || binding.sourceNodeId", self.features)
        for workflow in (self.classic, self.smart):
            node_ids = {item["id"] for item in workflow["nodes"]}
            for node in workflow["nodes"]:
                for binding in node.get("inputBindings", {}).values():
                    self.assertIn(binding["sourceNodeId"], node_ids)


if __name__ == "__main__":
    unittest.main()
