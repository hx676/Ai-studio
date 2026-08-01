import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.routing import APIRoute

from app.api.node_extensions import node_extension_web_asset
from app.main import app
from app.models.node_extensions import NodeRunCreateRequest
from app.services.node_extension_service import NodeExtensionRegistry, NodeRunManager


def write_extension(
    root: Path,
    package_id: str = "test.nodes",
    *,
    broken: bool = False,
    node_version: int = 1,
    with_migration: bool = False,
) -> Path:
    directory = root / package_id.replace(".", "_")
    (directory / "web").mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "id": package_id,
        "name": "Test Nodes",
        "version": "1.0.0",
        "enabled_by_default": True,
        "web_directory": "web",
        "requirements": "requirements.txt",
        "nodes": [{
            "id": "echo",
            "display_name": "Echo",
            "version": node_version,
            "surfaces": ["classic", "smart"],
            "legacy_types": {"classic": ["legacy-echo"]},
            "inputs": [{"id": "text", "types": ["text"]}],
            "outputs": [{"id": "text", "types": ["text"]}],
            "defaults": {"prefix": "test"},
            "size": {"classic": {"width": 300, "height": 220}},
            "execution": "python",
            "backend_class": "echo",
        }],
    }
    (directory / "node.json").write_text(json.dumps(manifest), encoding="utf-8")
    migration_source = """
def migrate_v1(state):
    state['prefix'] = state.pop('legacy_prefix', state.get('prefix', ''))
    return state
""" if with_migration else ""
    source = "raise RuntimeError('broken import')\n" if broken else f"""
{migration_source}
class Echo:
    STATE_MIGRATIONS = {{1: migrate_v1}} if {'True' if with_migration else 'False'} else {{}}
    async def execute(self, context, state, inputs):
        value = inputs.get('text', state.get('prefix', ''))
        if isinstance(value, dict):
            value = value.get('value', '')
        return {{'outputs': {{'text': {{'kind': 'text', 'value': str(value)}}}}}}
NODE_CLASS_MAPPINGS = {{'echo': Echo}}
NODE_DISPLAY_NAME_MAPPINGS = {{'echo': 'Echo'}}
WEB_DIRECTORY = './web'
"""
    (directory / "__init__.py").write_text(source, encoding="utf-8")
    (directory / "requirements.txt").write_text("", encoding="utf-8")
    (directory / "web" / "index.js").write_text("export function register() {}\n", encoding="utf-8")
    (directory / "web" / "styles.css").write_text(".echo {}\n", encoding="utf-8")
    return directory


class NodeExtensionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "custom_nodes"
        self.root.mkdir()
        self.state = Path(self.temp.name) / "data" / "node_extensions.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_discovers_imports_and_resolves_legacy_alias(self):
        write_extension(self.root)
        registry = NodeExtensionRegistry(self.root, self.state)
        state = registry.initialize()
        self.assertEqual(["test.nodes/echo"], [node["type"] for node in state["nodes"]])
        self.assertEqual("test.nodes/echo", registry.resolve_node("legacy-echo", "classic")["type"])
        self.assertEqual("loaded", state["packages"][0]["status"])

    def test_enable_changes_are_staged_without_unloading_active_python(self):
        write_extension(self.root)
        registry = NodeExtensionRegistry(self.root, self.state)
        registry.initialize()
        changed = registry.set_enabled("test.nodes", False)
        self.assertTrue(changed["restart_required"])
        self.assertEqual("disabled", changed["packages"][0]["status"])
        self.assertIsNotNone(registry.resolve_node("test.nodes/echo"))
        restored = registry.set_enabled("test.nodes", True)
        self.assertFalse(restored["restart_required"])
        self.assertIsNotNone(registry.handler_for("test.nodes/echo")[1])

    def test_localized_package_node_and_port_labels_are_exposed(self):
        directory = write_extension(self.root)
        manifest_path = directory / "node.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update({"name_zh": "测试节点", "description_zh": "测试扩展说明"})
        manifest["nodes"][0].update({"display_name_zh": "回显", "description_zh": "回显输入内容"})
        manifest["nodes"][0]["inputs"][0]["name_zh"] = "文本"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        state = NodeExtensionRegistry(self.root, self.state).initialize()
        package = state["packages"][0]
        node = state["nodes"][0]
        self.assertEqual("测试节点", package["name_zh"])
        self.assertEqual("测试扩展说明", package["description_zh"])
        self.assertEqual("回显", node["display_name_zh"])
        self.assertEqual("回显输入内容", node["description_zh"])
        self.assertEqual("文本", node["inputs"][0]["name_zh"])

    def test_broken_extension_does_not_block_other_packages(self):
        write_extension(self.root, "test.good")
        write_extension(self.root, "test.broken", broken=True)
        registry = NodeExtensionRegistry(self.root, self.state)
        state = registry.initialize()
        packages = {item["id"]: item for item in state["packages"]}
        self.assertEqual("loaded", packages["test.good"]["status"])
        self.assertEqual("error", packages["test.broken"]["status"])
        self.assertIn("broken import", packages["test.broken"]["error"])

    def test_manifest_rejects_path_escape_and_duplicate_nodes(self):
        directory = write_extension(self.root)
        manifest_path = directory / "node.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["web_directory"] = "../outside"
        manifest["nodes"].append(dict(manifest["nodes"][0]))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        registry = NodeExtensionRegistry(self.root, self.state)
        state = registry.initialize()
        self.assertEqual("invalid", state["packages"][0]["status"])
        self.assertTrue("Duplicate node id" in state["packages"][0]["error"] or "escapes" in state["packages"][0]["error"])

    def test_web_assets_are_confined_to_web_directory(self):
        write_extension(self.root)
        registry = NodeExtensionRegistry(self.root, self.state)
        registry.initialize()
        self.assertEqual("index.js", registry.web_asset_path("test.nodes", "index.js").name)
        with self.assertRaises(Exception):
            registry.web_asset_path("test.nodes", "../__init__.py")

    def test_public_api_routes_are_registered(self):
        paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
        self.assertIn("/api/node-extensions", paths)
        self.assertIn("/api/node-extensions/rescan", paths)
        self.assertIn("/api/node-runs", paths)


class NodeExtensionWebAssetTests(unittest.IsolatedAsyncioTestCase):
    async def test_nested_frontend_modules_are_not_served_from_stale_cache(self):
        with patch(
            "app.api.node_extensions.service.registry.web_asset_path",
            return_value=Path(__file__),
        ):
            response = await node_extension_web_asset("syncanvas.agent-skill", "agent-skill-canvas.js")
        self.assertEqual("no-store", response.headers.get("cache-control"))


class NodeExtensionFrontendLocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.registry_script = (root / "static/js/node-extensions.js").read_text(encoding="utf-8")
        cls.manager_script = (root / "static/js/node-extension-manager.js").read_text(encoding="utf-8")

    def test_manager_and_canvas_registry_prefer_chinese_manifest_fields(self):
        self.assertIn("function localizedField(item, key)", self.manager_script)
        self.assertIn("localizedField(node, 'display_name')", self.manager_script)
        self.assertIn("localizedField(item, 'name')", self.manager_script)
        self.assertIn("localizedField(item, 'description')", self.manager_script)
        self.assertIn("function rebuildLocalizedRegistry()", self.registry_script)
        self.assertIn("display_name:localizedField(definition, 'display_name')", self.registry_script)
        self.assertIn("name:localizedField(port, 'name')", self.registry_script)
        self.assertIn("window.addEventListener('studio-lang-change'", self.registry_script)


class NodeRunManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / "custom_nodes"
        root.mkdir()
        write_extension(root)
        self.registry = NodeExtensionRegistry(root, Path(self.temp.name) / "state.json")
        self.registry.initialize()
        self.manager = NodeRunManager(self.registry, Path(self.temp.name) / "runs")

    async def asyncTearDown(self):
        for task in list(self.manager.tasks.values()):
            task.cancel()
        await asyncio.gather(*self.manager.tasks.values(), return_exceptions=True)
        self.temp.cleanup()

    async def test_runs_extension_and_normalizes_outputs(self):
        record = self.manager.submit(NodeRunCreateRequest(
            node_type="test.nodes/echo",
            state={"prefix": "fallback"},
            inputs={"text": {"kind": "text", "value": "hello"}},
            canvas_id="canvas-1",
            node_id="node-1",
        ))
        await self.manager.tasks[record["run_id"]]
        completed = self.manager.get(record["run_id"])
        self.assertEqual("succeeded", completed["status"])
        self.assertEqual("hello", completed["result"]["output_text"])
        self.assertEqual("text", completed["result"]["outputs"]["text"][0]["kind"])

    async def test_runs_stepwise_state_migration(self):
        for task in list(self.manager.tasks.values()):
            task.cancel()
        await asyncio.gather(*self.manager.tasks.values(), return_exceptions=True)
        root = Path(self.temp.name) / "migrating_nodes"
        root.mkdir()
        write_extension(root, node_version=2, with_migration=True)
        self.registry = NodeExtensionRegistry(root, Path(self.temp.name) / "migrating-state.json")
        self.registry.initialize()
        self.manager = NodeRunManager(self.registry, Path(self.temp.name) / "migrating-runs")
        record = self.manager.submit(NodeRunCreateRequest(
            node_type="test.nodes/echo",
            node_version=1,
            state={"legacy_prefix": "migrated"},
        ))
        await self.manager.tasks[record["run_id"]]
        completed = self.manager.get(record["run_id"])
        self.assertEqual("succeeded", completed["status"])
        self.assertEqual(2, completed["node_version"])
        self.assertEqual(1, completed["source_node_version"])
        self.assertEqual("migrated", completed["result"]["output_text"])

    async def test_rejects_future_state_version(self):
        with self.assertRaisesRegex(Exception, "newer than supported"):
            self.manager.submit(NodeRunCreateRequest(
                node_type="test.nodes/echo",
                node_version=2,
                state={},
            ))


class NodeExtensionFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.runtime = (root / "static/js/node-extensions.js").read_text(encoding="utf-8")
        cls.classic = (root / "static/js/canvas/state.js").read_text(encoding="utf-8")
        cls.smart = (root / "static/js/smart-canvas/state.js").read_text(encoding="utf-8")
        cls.settings = (root / "static/node-extensions.html").read_text(encoding="utf-8")
        cls.agent_module = (root / "custom_nodes/syncanvas_agent_skill/web/index.js").read_text(encoding="utf-8")
        cls.agent_manifest = json.loads((root / "custom_nodes/syncanvas_agent_skill/node.json").read_text(encoding="utf-8"))

    def test_shared_runtime_handles_registry_execution_and_missing_nodes(self):
        self.assertIn("window.SynCanvasNodeExtensions = api", self.runtime)
        self.assertIn("/api/node-runs", self.runtime)
        self.assertIn("extensionMissing", self.runtime)
        self.assertIn("data-extension-run", self.runtime)

    def test_both_canvas_surfaces_use_the_shared_registry(self):
        self.assertIn("addRegisteredExtensionNode", self.classic)
        self.assertIn("SynCanvasNodeExtensions.canConnect(from, to, 'classic')", self.classic)
        self.assertIn("createRegisteredExtensionNode", self.smart)
        self.assertIn("SynCanvasNodeExtensions.canConnect(sourceNode, targetNode, 'smart')", self.smart)
        self.assertIn("fromPort:connection.fromPort || 'out'", self.classic)
        self.assertIn("fromPort:connection.fromPort || 'out'", self.smart)

    def test_agent_extension_versions_its_nested_frontend_module(self):
        version = self.agent_manifest["version"]
        self.assertIn(f"agent-skill-canvas.js?v={version}", self.agent_module)

    def test_extension_settings_exposes_rescan_and_apply(self):
        self.assertIn('id="rescanBtn"', self.settings)
        self.assertIn('id="applyBtn"', self.settings)

    def test_agent_is_a_canonical_versioned_extension_node(self):
        agent = next(node for node in self.agent_manifest["nodes"] if node["id"] == "agent")
        self.assertEqual(2, agent["version"])
        self.assertTrue(next(port for port in agent["inputs"] if port["id"] == "text")["multiple"])
        self.assertIn("migrate: migrateAgent", self.agent_module)
        self.assertIn("serialize: serializeAgent", self.agent_module)
        self.assertIn("node.type = definition.type", self.agent_module)
        self.assertIn("node.data = data", self.agent_module)
        self.assertNotIn("api.registerNode('agent', { legacy: true", self.agent_module)

    def test_both_surfaces_create_agent_through_registry(self):
        self.assertIn("return addRegisteredExtensionNode('agent', p);", self.classic)
        self.assertIn("if(type === 'agent') return addAgentNode(menuPoint);", self.classic)
        self.assertIn("return createRegisteredExtensionNode('smart-agent', x, y, options);", self.smart)
        self.assertIn("connection.toPort || 'in'", self.classic)
        self.assertIn("connection.toPort || 'in'", self.smart)

    def test_agent_migration_preserves_legacy_provider(self):
        self.assertIn("data.aiProvider, node.aiProvider", self.agent_module)


if __name__ == "__main__":
    unittest.main()
