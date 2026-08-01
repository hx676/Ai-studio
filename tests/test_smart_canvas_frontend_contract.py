import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_JS = ROOT / "static" / "js" / "smart-canvas" / "state.js"
SMART_CSS = ROOT / "static" / "css" / "smart-canvas.css"
SMART_HTML = ROOT / "static" / "smart-canvas.html"
AGENT_SKILL_JS = ROOT / "custom_nodes" / "syncanvas_agent_skill" / "web" / "agent-skill-canvas.js"
AGENT_SKILL_CSS = ROOT / "custom_nodes" / "syncanvas_agent_skill" / "web" / "agent-skill-nodes.css"
UPSTREAM_FEATURES_JS = ROOT / "static" / "js" / "upstream-canvas-features.js"


class SmartCanvasFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state_source = STATE_JS.read_text(encoding="utf-8")
        cls.style_source = SMART_CSS.read_text(encoding="utf-8")
        cls.html_source = SMART_HTML.read_text(encoding="utf-8")
        cls.agent_skill_source = AGENT_SKILL_JS.read_text(encoding="utf-8")
        cls.agent_skill_style_source = AGENT_SKILL_CSS.read_text(encoding="utf-8")
        cls.feature_source = UPSTREAM_FEATURES_JS.read_text(encoding="utf-8")

    def test_running_nodes_receive_the_shared_state_class(self):
        self.assertIn("${(node.running || isPending) ? 'node-running' : ''}", self.state_source)

    def test_running_state_has_a_visible_animated_perimeter(self):
        self.assertIn(".image-node.node-running::after", self.style_source)
        self.assertIn("outline: 3px solid var(--node-run-core)", self.style_source)
        self.assertIn("--node-run-core: #16a34a", self.style_source)
        self.assertIn("animation: smart-node-run-ring", self.style_source)
        self.assertIn("@keyframes smart-node-run-ring", self.style_source)
        self.assertNotIn(".node-running::after { content:none", self.style_source)

    def test_running_animation_respects_reduced_motion(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.style_source)

    def test_agent_skill_running_state_has_a_direct_green_fallback(self):
        self.assertIn("wrap.classList.toggle('is-running', Boolean(node.running))", self.agent_skill_source)
        self.assertIn('nodeEl.dataset.running = node.running', self.state_source)
        self.assertIn('.image-node.smart-agent-node[data-running="true"]', self.agent_skill_style_source)
        self.assertIn("outline: 4px solid #16a34a", self.agent_skill_style_source)
        self.assertIn("animation: ai-node-running-frame", self.agent_skill_style_source)
        self.assertRegex(self.html_source, r"node-extensions\.css\?v=[^\"]+")
        self.assertRegex(self.html_source, r"node-extensions\.js\?v=[^\"]+")

    def test_agent_extension_supports_dark_theme_without_horizontal_overflow(self):
        self.assertIn(".studio-theme-dark .ai-node-select", self.agent_skill_style_source)
        self.assertIn(".theme-dark .ai-node-output", self.agent_skill_style_source)
        self.assertIn(".agent-node .node-body", self.agent_skill_style_source)
        self.assertIn("overflow: hidden", self.agent_skill_style_source)
        self.assertIn("overflow: auto", self.agent_skill_style_source)

    def test_template_nodes_are_creatable_and_serializable(self):
        self.assertIn("data-create-type=\"template-store\"", self.html_source)
        self.assertIn("data-create-type=\"template-call\"", self.html_source)
        self.assertIn("function createTemplateStoreNode", self.state_source)
        self.assertIn("function createTemplateCallNode", self.state_source)
        self.assertIn("function serializableSmartNode", self.state_source)
        self.assertIn("delete copy.structuredOutput", self.state_source)

    def test_extension_create_menu_resolves_smart_legacy_aliases(self):
        self.assertIn("agent:'smart-agent'", self.state_source)
        self.assertIn("skill:'smart-skill'", self.state_source)
        self.assertIn("'template-store':'smart-template-store'", self.state_source)
        self.assertIn("'template-call':'smart-template-call'", self.state_source)
        self.assertIn("definition(smartLegacyTypes[type], 'smart')", self.state_source)

    def test_create_menu_opens_from_right_click_instead_of_double_click(self):
        self.assertIn("shell.ondblclick = null", self.state_source)
        self.assertIn("shell.oncontextmenu = e =>", self.state_source)
        self.assertIn("e.preventDefault()", self.state_source)
        self.assertIn("e.stopPropagation()", self.state_source)
        self.assertIn("openCreateMenu(e)", self.state_source)
        self.assertNotIn("shell.ondblclick = e =>", self.state_source)

    def test_template_assets_can_create_bound_call_nodes(self):
        self.assertIn("data-asset-tab=\"template\"", self.html_source)
        self.assertIn("application/x-smart-asset", self.state_source)
        self.assertIn("asset?.kind === 'template'", self.state_source)
        self.assertIn("createTemplateCallNode", self.state_source)
        self.assertIn("event.data?.type === 'asset_library_updated'", self.state_source)
        self.assertIn("loadAssetLibrary().catch", self.state_source)

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
        self.assertRegex(self.html_source, r"upstream-canvas-features\.js\?v=[^\"]+")

    def test_applied_prompt_template_has_a_persistent_blue_title(self):
        self.assertIn('class="prompt-template-token"', self.feature_source)
        self.assertIn('data-template-prompt="${esc(text)}"', self.feature_source)
        self.assertNotIn("</span><br>${promptHtml}", self.feature_source)
        self.assertRegex(self.html_source, r"smart-canvas\.css\?v=[^\"]+")
        self.assertIn(".prompt-template-token", self.style_source)
        self.assertIn("color:#1d4ed8", self.style_source)
        self.assertIn("mention-image-token|prompt-template-token", self.state_source)

    def test_prompt_template_title_is_not_sent_to_the_model(self):
        self.assertIn("promptInput.querySelectorAll('.prompt-template-token')", self.state_source)
        self.assertIn("token.dataset.templatePrompt", self.state_source)
        self.assertIn("token.style.display = 'none'", self.state_source)
        self.assertIn("token.removeAttribute('style')", self.state_source)
        self.assertIn("[...templatePrompts, visibleText]", self.state_source)

    def test_first_node_selection_does_not_overwrite_its_saved_prompt(self):
        self.assertIn("if(switchedNode && activeComposerSubject?.id) savePromptDraftForCurrent()", self.state_source)
        self.assertRegex(self.html_source, r"smart-canvas/state\.js\?v=[^\"]+")

    def test_composer_tracks_the_selected_image_during_drag(self):
        self.assertIn("function positionComposerForNode(node)", self.state_source)
        self.assertIn("if(composerNode?.type === 'smart-image') positionComposerForNode(composerNode)", self.state_source)
        self.assertIn("positionComposerForNode(node)", self.state_source)

    def test_selected_image_is_included_as_a_generation_reference(self):
        self.assertIn("...outputImagesForNode(node, true)", self.state_source)
        self.assertIn("...inputImagesFor(node)", self.state_source)
        self.assertIn("reference_images:refs", self.state_source)
        self.assertRegex(self.html_source, r"smart-canvas/state\.js\?v=[^\"]+")

    def test_image_edit_entry_is_visible_on_image_nodes(self):
        self.assertIn('class="mini-x image-edit-open"', self.state_source)
        self.assertIn("openImageEditor(id, Number(btn.dataset.imageIndex || 0))", self.state_source)
        self.assertIn(".image-wrap .image-edit-open", self.style_source)

    def test_connection_style_is_configurable_and_persisted(self):
        for style in ("curve", "orthogonal", "straight", "hidden"):
            self.assertIn(f'data-connection-style="{style}"', self.html_source)
        self.assertIn("connectionStyle:'curve'", self.state_source)
        self.assertIn("function connectionPathData", self.state_source)
        self.assertIn("if(style === 'orthogonal')", self.state_source)
        self.assertIn("H${midX} V${ty} H${tx}", self.state_source)
        self.assertIn("if(style === 'straight' || style === 'hidden')", self.state_source)
        self.assertIn("connection-style-hidden", self.state_source)
        self.assertIn("normalizeCanvasAppearanceSettings(normalizeSmartSettingsEngines(settings))", self.state_source)


if __name__ == "__main__":
    unittest.main()
