import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLASSIC_HTML = ROOT / "static" / "canvas.html"
SMART_HTML = ROOT / "static" / "smart-canvas.html"
ASSISTANT_JS = ROOT / "static" / "js" / "canvas-assistant.js"
CLASSIC_JS = ROOT / "static" / "js" / "canvas" / "state.js"
SMART_JS = ROOT / "static" / "js" / "smart-canvas" / "state.js"
PANELS_JS = ROOT / "static" / "js" / "upstream-canvas-features.js"
ASSISTANT_CSS = ROOT / "static" / "css" / "canvas-assistant.css"


class CanvasAssistantFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.classic_html = CLASSIC_HTML.read_text(encoding="utf-8")
        cls.smart_html = SMART_HTML.read_text(encoding="utf-8")
        cls.assistant = ASSISTANT_JS.read_text(encoding="utf-8")
        cls.classic = CLASSIC_JS.read_text(encoding="utf-8")
        cls.smart = SMART_JS.read_text(encoding="utf-8")
        cls.panels = PANELS_JS.read_text(encoding="utf-8")
        cls.css = ASSISTANT_CSS.read_text(encoding="utf-8")

    def test_both_canvases_load_the_shared_sidebar_and_safe_markdown_dependencies(self):
        for html in (self.classic_html, self.smart_html):
            self.assertIn('id="canvasAssistantToggle"', html)
            self.assertIn('id="canvasAssistantPanel"', html)
            self.assertIn('/static/js/canvas-assistant.js', html)
            self.assertIn('/static/css/canvas-assistant.css', html)
            self.assertIn('/static/vendor/js/marked-15.0.12.min.js', html)
            self.assertIn('/static/vendor/js/dompurify-3.2.6.min.js', html)

    def test_classic_toolbar_is_grouped_into_two_non_wrapping_rows(self):
        for group in (
            "toolbar-group-input",
            "toolbar-group-ai",
            "toolbar-group-generate",
            "toolbar-group-template",
            "toolbar-group-result",
        ):
            self.assertIn(group, self.classic_html)
        self.assertIn("toolbar-row-primary", self.classic_html)
        self.assertIn("toolbar-row-secondary", self.classic_html)
        self.assertIn("<span>画布精灵</span>", self.classic_html)
        self.assertIn(".toolbar-row { min-width:max-content", (ROOT / "static/css/canvas.css").read_text(encoding="utf-8"))
        self.assertIn(".toolbar .tool-btn span { white-space:nowrap; }", (ROOT / "static/css/canvas.css").read_text(encoding="utf-8"))

    def test_shared_client_uses_canvas_scoped_conversations_and_sse(self):
        self.assertIn("/api/canvases/${encodeURIComponent(canvasId())}/assistant/conversations", self.assistant)
        self.assertIn("/messages/stream", self.assistant)
        self.assertIn("TextDecoder", self.assistant)
        self.assertIn("AbortController", self.assistant)
        self.assertIn("const bootstrap = !message", self.assistant)
        self.assertIn("reference_images:state.refs, bootstrap", self.assistant)
        self.assertIn("select.disabled = state.generating", self.assistant)
        self.assertIn("provider.disabled = state.generating", self.assistant)
        self.assertIn("model.disabled = state.generating", self.assistant)

    def test_sources_are_snapshotted_by_backend_and_locked_in_existing_conversations(self):
        self.assertIn("source.disabled = Boolean(state.conversation)", self.assistant)
        self.assertIn("function selectedSource()", self.assistant)
        self.assertIn("return {kind:kind || 'general', id:parts.join(':') || ''}", self.assistant)
        self.assertIn("state.conversation.source.name", self.assistant)
        self.assertIn("text('快照','snapshot')", self.assistant)

    def test_disabled_providers_are_not_reintroduced_by_modelscope_fallback(self):
        self.assertIn("const configured = config.api_providers || []", self.assistant)
        self.assertIn("!configured.some(item => item.id === 'modelscope')", self.assistant)

    def test_assistant_markdown_is_sanitized_and_user_text_is_escaped(self):
        self.assertIn("DOMPurify.sanitize", self.assistant)
        self.assertIn("FORBID_TAGS:['style','iframe','object','embed','form','input','button','textarea','select','option']", self.assistant)
        self.assertIn("assistant ? safeMarkdown(message.content) : esc(message.content || '')", self.assistant)
        classic_panel = self.classic_html[self.classic_html.index('id="canvasAssistantPanel"'):self.classic_html.index('</aside>', self.classic_html.index('id="canvasAssistantPanel"'))]
        smart_panel = self.smart_html[self.smart_html.index('id="canvasAssistantPanel"'):self.smart_html.index('</aside>', self.smart_html.index('id="canvasAssistantPanel"'))]
        self.assertNotIn("onclick=", classic_panel)
        self.assertNotIn("onclick=", smart_panel)

    def test_send_to_canvas_reuses_existing_prompt_nodes_and_keeps_origin_metadata(self):
        self.assertIn("addPromptNode({x:center.x - 150, y:center.y - 95})", self.classic)
        self.assertIn("node.text = String(content || '')", self.classic)
        self.assertIn("createPromptNode(center.x - 158, center.y - 97)", self.smart)
        self.assertIn("node.text = String(content || '')", self.smart)
        for source in (self.classic, self.smart):
            self.assertIn("assistantOrigin", source)
            self.assertIn("conversationId", source)
            self.assertIn("messageId", source)

    def test_selected_image_collection_is_surface_specific_and_capped(self):
        self.assertIn("canvasAssistantSelectedImages", self.classic)
        self.assertIn("node.type === 'output'", self.classic)
        self.assertIn("node.type === 'group'", self.classic)
        self.assertIn("smartCanvasAssistantSelectedImages", self.smart)
        self.assertIn("node.type === 'smart-image'", self.smart)
        self.assertIn("values.length >= 8", self.classic)
        self.assertIn("values.length >= 8", self.smart)

    def test_visible_labels_follow_the_current_language(self):
        self.assertIn("function applyLabels()", self.assistant)
        self.assertIn("text('画布精灵','Canvas Assistant')", self.assistant)
        self.assertIn("text('输入消息，Enter 发送…','Type a message, Enter to send…')", self.assistant)

    def test_sidebar_is_mutually_exclusive_and_mobile_width_is_supported(self):
        self.assertGreaterEqual(self.panels.count("canvasAssistantPanel"), 2)
        self.assertIn("const panelStateObserver = new MutationObserver", self.assistant)
        self.assertIn("state.open = visible", self.assistant)
        self.assertIn("toggle.setAttribute('aria-expanded',visible?'true':'false')", self.assistant)
        self.assertIn(".canvas-assistant-panel { width:calc(100vw - 24px); }", self.css)

    def test_backend_routes_cover_the_complete_conversation_lifecycle(self):
        from app.main import app

        paths = {(getattr(route, "path", ""), method) for route in app.routes for method in (getattr(route, "methods", None) or set())}
        self.assertIn(("/api/canvas-assistant/sources", "GET"), paths)
        base = "/api/canvases/{canvas_id}/assistant/conversations"
        self.assertIn((base, "GET"), paths)
        self.assertIn((base, "POST"), paths)
        item = base + "/{conversation_id}"
        for method in ("GET", "PATCH", "DELETE"):
            self.assertIn((item, method), paths)
        self.assertIn((item + "/activate", "POST"), paths)
        self.assertIn((item + "/messages/stream", "POST"), paths)


if __name__ == "__main__":
    unittest.main()
