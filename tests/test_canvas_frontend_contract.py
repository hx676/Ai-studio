import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_JS = ROOT / "static" / "js" / "canvas" / "state.js"
CANVAS_HTML = ROOT / "static" / "canvas.html"
CANVAS_CSS = ROOT / "static" / "css" / "canvas.css"
INDEX_HTML = ROOT / "static" / "index.html"
SMART_STATE_JS = ROOT / "static" / "js" / "smart-canvas" / "state.js"
SMART_HTML = ROOT / "static" / "smart-canvas.html"
CANVAS_LIST_JS = ROOT / "static" / "js" / "canvas-list.js"
CANVAS_LIST_CSS = ROOT / "static" / "css" / "canvas-list.css"


class CanvasFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = STATE_JS.read_text(encoding="utf-8")
        cls.html_source = CANVAS_HTML.read_text(encoding="utf-8")
        cls.style_source = CANVAS_CSS.read_text(encoding="utf-8")
        cls.index_source = INDEX_HTML.read_text(encoding="utf-8")
        cls.smart_source = SMART_STATE_JS.read_text(encoding="utf-8")
        cls.smart_html_source = SMART_HTML.read_text(encoding="utf-8")
        cls.canvas_list_source = CANVAS_LIST_JS.read_text(encoding="utf-8")
        cls.canvas_list_style = CANVAS_LIST_CSS.read_text(encoding="utf-8")

    def function_block(self, start, end):
        return self.source.split(start, 1)[1].split(end, 1)[0]

    def test_single_generate_button_does_not_run_connected_workflow(self):
        block = self.function_block(
            "async function runCanvasGenerate(nodeId)",
            "function computeCascadeOrder(targetId)",
        )
        self.assertIn("runCascadeNodeByType(node, {cascade:false})", block)
        self.assertIn("runMsGenNode(nodeId, {cascade:false})", block)
        self.assertNotIn("computeConnectedWorkflowOrder", block)
        self.assertNotIn("runOneCascadePass", block)

    def test_cascade_button_keeps_connected_workflow_execution(self):
        self.assertIn("runNodeCascade(btn.dataset.loopCascade)", self.source)
        self.assertIn("runNodeCascade(nodeId)", self.source)
        self.assertIn("const order = computeCascadeOrder(nodeId)", self.source)

    def test_template_nodes_are_available_in_the_classic_canvas(self):
        self.assertIn('onclick="addTemplateStoreNode()"', self.html_source)
        self.assertIn('onclick="addTemplateCallNode()"', self.html_source)
        self.assertIn("if(type === 'template-store') return addTemplateStoreNode(point)", self.source)
        self.assertIn("if(type === 'template-call') return addTemplateCallNode(point)", self.source)
        self.assertIn(".template-store-node", self.style_source)
        self.assertIn(".template-call-node", self.style_source)

    def test_template_nodes_participate_in_connections_and_cascade_runs(self):
        connect_block = self.function_block("function canConnect(fromId, toId)", "function sanitizeConnections()")
        run_block = self.function_block("function runCascadeNodeByType(node, opts={})", "async function runCascadeNodeWithLoopContext")
        self.assertIn("if(to.type === 'template-store')", connect_block)
        self.assertIn("['template-call','template-store'].includes(from.type)", connect_block)
        self.assertIn("node.type === 'template-call'", run_block)
        self.assertIn("node.type === 'template-store'", run_block)
        self.assertIn("'template-call','template-store'", self.source)

    def test_saved_template_calls_reload_latest_template_data(self):
        open_block = self.function_block("async function openCanvas(id)", "function applyRemoteCanvasData(remote)")
        self.assertIn("ensureCanvasTemplateLibrary().catch", open_block)
        self.assertIn("refreshTemplateCallNode(node, {quiet:true})", open_block)
        serializable_block = self.function_block("function serializableCanvasNode(node)", "function serializableCanvasNodes")
        self.assertIn("delete copy.structuredOutput", serializable_block)
        self.assertIn("delete copy._templateImages", serializable_block)
        self.assertIn("event.data?.type === 'asset_library_updated'", self.source)
        self.assertIn("ensureCanvasTemplateLibrary(true)", self.source)

    def test_template_store_unwraps_agent_result_envelopes(self):
        source_block = self.function_block("function canvasTemplateObjectFromNode(source)", "function canvasTemplateSources(node)")
        self.assertIn("source?.structuredOutput", source_block)
        self.assertIn("source?.outputText", source_block)
        self.assertIn("'template','output','result','data','json','text'", source_block)
        self.assertIn("isCanvasDistilledTemplate(parsed)", source_block)

    def test_direct_node_runs_refresh_upstream_template_calls(self):
        blocks = [
            self.function_block("async function runAgentSkillNode(nodeId, opts={})", "async function cancelAgentSkillNode"),
            self.function_block("async function runGenerator(genId, opts={})", "async function runVideoNode"),
            self.function_block("async function runVideoNode(nodeId, opts={})", "async function runLLMNode"),
            self.function_block("async function runLLMNode(nodeId, opts={})", "function isTerminalGenerator"),
        ]
        for block in blocks:
            self.assertIn("await refreshTemplateCallsForNode", block)
        refresh_block = self.function_block("async function refreshTemplateCallNode(node, options={})", "async function runTemplateStoreNode")
        self.assertIn("throw new Error(node.templateError)", refresh_block)

    def test_template_call_picker_renders_thumbnails_and_keyboard_selection(self):
        picker_block = self.function_block("function templateCallBodyHtml(node)", "function selectCanvasTemplateAsset")
        bind_block = self.function_block("function bindTemplateNodeControls(el, node)", "async function refreshTemplateCallNode")
        self.assertIn("template-picker-item", picker_block)
        self.assertIn("canvasTemplateAssetThumbnail(item)", picker_block)
        self.assertIn('role="listbox"', picker_block)
        self.assertNotIn('template-call-preview', picker_block)
        self.assertIn("option.onkeydown", bind_block)
        self.assertIn("ArrowDown", bind_block)

    def test_template_call_renders_loaded_template_content(self):
        content_block = self.function_block("function templateCallContentHtml(template)", "function canvasTemplateObjectFromNode")
        picker_block = self.function_block("function templateCallBodyHtml(node)", "function selectCanvasTemplateAsset")
        self.assertIn("'stylePromptZh', 'style_prompt_zh'", content_block)
        self.assertIn("'negativePrompt', 'negative_prompt', 'negative'", content_block)
        self.assertIn("JSON.stringify(template, null, 2)", content_block)
        self.assertIn("templateCallContentHtml(node.structuredOutput)", picker_block)
        self.assertIn(".template-call-content", self.style_source)

    def test_template_store_creates_new_assets_unless_update_is_explicit(self):
        store_block = self.function_block("async function runTemplateStoreNode(nodeId, options={})", "async function refreshTemplateCallsForNode")
        bind_block = self.function_block("function bindTemplateNodeControls(el, node)", "async function refreshTemplateCallNode")
        self.assertIn("Boolean(options.update && node.templateId)", store_block)
        self.assertIn("data-template-update", bind_block)
        self.assertIn("{update:true}", bind_block)

    def test_semantic_output_changes_refresh_all_direct_dependents(self):
        dependent_block = self.function_block("function refreshConnectedDependents(sourceNodeIds=[])", "function setCanvasTemplateLibraryFromResponse")
        self.assertIn("sourceIds.has(connection.from)", dependent_block)
        self.assertIn(".map(connection => connection.to)", dependent_block)
        self.assertNotIn("template-store", dependent_block)

        run_refresh_block = self.function_block("function refreshRunNodes(node, out=null)", "function captureOutputScrolls")
        self.assertIn("refreshConnectedDependents(ids)", run_refresh_block)

        agent_block = self.function_block("async function runAgentSkillNode(nodeId, opts={})", "async function cancelAgentSkillNode")
        llm_block = self.function_block("async function runLLMNode(nodeId, opts={})", "function isTerminalGenerator")
        template_call_block = self.function_block("async function refreshTemplateCallNode(node, options={})", "async function runTemplateStoreNode")
        template_store_block = self.function_block("async function runTemplateStoreNode(nodeId, options={})", "async function refreshTemplateCallsForNode")
        for block in [agent_block, llm_block, template_call_block, template_store_block]:
            self.assertIn("refreshConnectedDependents([node.id])", block)

        llm_chat_block = self.function_block("async function runLLMChat(nodeId)", "function deleteNode")
        output_bind_block = self.function_block("function bindOutputWrap(wrap, node)", "function outputDomKeyForItem")
        self.assertIn("refreshConnectedDependents([node.id])", llm_chat_block)
        self.assertIn("refreshConnectedDependents([node.id])", output_bind_block)

    def test_project_workspace_is_the_single_canvas_entry(self):
        self.assertNotIn('data-route="canvas"', self.index_source)
        self.assertNotIn('id="frame-canvas"', self.index_source)
        self.assertNotIn("'canvas': { page:'canvas' }", self.index_source)
        self.assertIn("'canvas': 'projects'", self.index_source)

        return_block = self.function_block("async function returnToCanvasManager()", "function requestDeleteCanvas")
        self.assertIn("window.location.href = projectWorkspaceUrl()", return_block)
        self.assertIn("/static/canvas-list.html", self.source)
        self.assertIn("/static/canvas-list.html", self.smart_source)
        self.assertNotIn("window.location.href = '/static/canvas.html", self.smart_source)

    def test_workspace_route_switch_does_not_crossfade_old_iframes(self):
        self.assertIn("visibility: hidden;", self.index_source)
        self.assertIn("visibility: visible;", self.index_source)
        self.assertIn("transition: none;", self.index_source)
        self.assertNotIn("transition: all 0.5s var(--fluid-ease);", self.index_source)

    def test_canvas_pages_stay_covered_until_target_state_is_restored(self):
        self.assertIn('class="canvas-booting"', self.html_source)
        self.assertIn("html.canvas-booting .shell { visibility:hidden", self.html_source)
        self.assertIn("document.documentElement.classList.remove('canvas-booting')", self.source)
        self.assertIn('class="smart-canvas-booting"', self.smart_html_source)
        self.assertIn("html.smart-canvas-booting .shell { visibility:hidden", self.smart_html_source)
        self.assertIn("document.documentElement.classList.remove('smart-canvas-booting')", self.smart_source)

    def test_direct_classic_canvas_does_not_render_legacy_picker_during_boot(self):
        boot_block = self.function_block("window.onload = async () =>", "};")
        self.assertNotIn("loadCanvasList(false)", boot_block)
        self.assertIn("await openCanvas(openId)", boot_block)
        self.assertIn("await finishCanvasBoot()", boot_block)

    def test_project_card_is_covered_immediately_while_canvas_navigates(self):
        self.assertIn("document.documentElement.classList.add('canvas-opening')", self.canvas_list_source)
        self.assertIn("if(canvasOpening) return", self.canvas_list_source)
        self.assertIn("html.canvas-opening .workspace { visibility:hidden", self.canvas_list_style)

    def test_new_project_cards_choose_a_nearby_free_position(self):
        self.assertIn("function nearestFreeCardPosition(preferred)", self.canvas_list_source)
        self.assertIn("worldPt = nearestFreeCardPosition(worldPt)", self.canvas_list_source)
        self.assertIn("if(available(base)) return base", self.canvas_list_source)

    def test_canvas_navigation_uses_the_current_release_cache_key(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        sources = [self.canvas_list_source, self.source, self.smart_source]
        self.assertTrue(all(f"v={version}" in source for source in sources))
        self.assertTrue(all("2026.07.31.1" not in source for source in sources))

    def test_extension_create_menus_stay_inside_the_viewport(self):
        self.assertIn("max-height:calc(100vh-24px)", self.style_source.replace(" ", ""))
        self.assertIn("overflow-y:auto", self.style_source.replace(" ", ""))
        smart_style = (ROOT / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8").replace(" ", "")
        self.assertIn("max-height:calc(100vh-28px)", smart_style)
        self.assertIn("overflow-y:auto", smart_style)
        self.assertIn("const h = Math.min(480, window.innerHeight - 28)", self.smart_source)


if __name__ == "__main__":
    unittest.main()
