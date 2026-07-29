import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_JS = ROOT / "static" / "js" / "smart-canvas" / "state.js"
SMART_CSS = ROOT / "static" / "css" / "smart-canvas.css"
SMART_HTML = ROOT / "static" / "smart-canvas.html"
UPSTREAM_FEATURES_JS = ROOT / "static" / "js" / "upstream-canvas-features.js"


class SmartCanvasFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state_source = STATE_JS.read_text(encoding="utf-8")
        cls.style_source = SMART_CSS.read_text(encoding="utf-8")
        cls.html_source = SMART_HTML.read_text(encoding="utf-8")
        cls.feature_source = UPSTREAM_FEATURES_JS.read_text(encoding="utf-8")

    def test_running_nodes_receive_the_shared_state_class(self):
        self.assertIn("${node.running ? 'node-running' : ''}", self.state_source)

    def test_running_state_has_a_visible_animated_perimeter(self):
        self.assertIn(".image-node.node-running::after", self.style_source)
        self.assertIn("animation: smart-node-run-ring", self.style_source)
        self.assertIn("@keyframes smart-node-run-ring", self.style_source)
        self.assertNotIn(".node-running::after { content:none", self.style_source)

    def test_running_animation_respects_reduced_motion(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.style_source)

    def test_template_nodes_are_creatable_and_serializable(self):
        self.assertIn("data-create-type=\"template-store\"", self.html_source)
        self.assertIn("data-create-type=\"template-call\"", self.html_source)
        self.assertIn("function createTemplateStoreNode", self.state_source)
        self.assertIn("function createTemplateCallNode", self.state_source)
        self.assertIn("function serializableSmartNode", self.state_source)
        self.assertIn("delete copy.structuredOutput", self.state_source)

    def test_template_assets_can_create_bound_call_nodes(self):
        self.assertIn("data-asset-tab=\"template\"", self.html_source)
        self.assertIn("application/x-smart-asset", self.state_source)
        self.assertIn("asset?.kind === 'template'", self.state_source)
        self.assertIn("createTemplateCallNode", self.state_source)

    def test_template_calls_refresh_before_downstream_runs(self):
        self.assertIn("async function refreshTemplateCallsFor", self.state_source)
        self.assertIn("await refreshTemplateCallsFor(node)", self.state_source)
        self.assertIn("await refreshTemplateCallNode(aiNode", self.state_source)

    def test_template_nodes_have_stable_layout_and_error_state(self):
        self.assertIn("node?.type === 'smart-template-store'", self.state_source)
        self.assertIn("node?.type === 'smart-template-call'", self.state_source)
        self.assertIn(".image-node.template-store-node", self.style_source)
        self.assertIn(".image-node.template-call-node.node-error", self.style_source)

    def test_prompt_template_panel_does_not_clear_canvas_selection(self):
        self.assertIn("function stopCanvasInteractionLeak", self.feature_source)
        self.assertIn("['pointerdown','mousedown','click','dblclick','wheel']", self.feature_source)
        self.assertIn("panel?.addEventListener(type, stopCanvasInteractionLeak)", self.feature_source)

    def test_prompt_template_updates_the_smart_composer_through_its_input_event(self):
        self.assertIn("const input = byId('promptInput')", self.feature_source)
        self.assertIn("input.innerHTML = smartPromptTemplateHtml(template, text, mode)", self.feature_source)
        self.assertIn("input.dispatchEvent(new Event('input', {bubbles:true}))", self.feature_source)
        self.assertIn("upstream-canvas-features.js?v=2026.07.29.2", self.html_source)

    def test_applied_prompt_template_has_a_persistent_blue_title(self):
        self.assertIn('class="prompt-template-token"', self.feature_source)
        self.assertIn(".prompt-template-token", self.style_source)
        self.assertIn("color:#1d4ed8", self.style_source)
        self.assertIn("mention-image-token|prompt-template-token", self.state_source)

    def test_prompt_template_title_is_not_sent_to_the_model(self):
        self.assertIn("promptInput.querySelectorAll('.prompt-template-token')", self.state_source)
        self.assertIn("token.style.display = 'none'", self.state_source)
        self.assertIn("token.removeAttribute('style')", self.state_source)

    def test_first_node_selection_does_not_overwrite_its_saved_prompt(self):
        self.assertIn("if(switchedNode && activeComposerSubject?.id) savePromptDraftForCurrent()", self.state_source)
        self.assertIn("smart-canvas/state.js?v=2026.07.29.3", self.html_source)


if __name__ == "__main__":
    unittest.main()
