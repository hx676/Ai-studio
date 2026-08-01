import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.models.runtime_nodes import NodeEngineModelImportRequest, NodeEngineModelPathsRequest
from app.services import node_engine_asset_service as service
from app.services import node_engine_component_service as component_service


class NodeEngineAssetServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "node-engine"
        replacements = {
            "DATA_ROOT": self.root,
            "MODELS_DIR": self.root / "models",
            "CUSTOM_NODES_DIR": self.root / "custom_nodes",
            "DISABLED_CUSTOM_NODES_DIR": self.root / "disabled_custom_nodes",
            "TASKS_DIR": self.root / "tasks",
            "MODEL_IMPORT_DIR": self.root / "tasks" / "model-imports",
            "EXTENSION_TASK_DIR": self.root / "tasks" / "extensions",
            "MODEL_REGISTRY_FILE": self.root / "model-registry.json",
            "MODEL_PATHS_FILE": self.root / "model-paths.json",
            "EXTENSION_REGISTRY_FILE": self.root / "extension-registry.json",
            "EXTRA_PATHS_FILE": self.root / "extra_model_paths.yaml",
            "EXTENSION_STAGING_DIR": self.root / ".extension-staging",
        }
        self.patchers = [patch.object(service, name, value) for name, value in replacements.items()]
        for patcher in self.patchers:
            patcher.start()
        for path in (
            service.MODELS_DIR,
            service.CUSTOM_NODES_DIR,
            service.DISABLED_CUSTOM_NODES_DIR,
            service.MODEL_IMPORT_DIR,
            service.EXTENSION_TASK_DIR,
            service.EXTENSION_STAGING_DIR,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.manager = service.ModelImportManager()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temporary.cleanup()

    def wait_for_import(self, task_id, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.manager.get(task_id)
            if record["status"] in service.TERMINAL_STATES:
                return record
            time.sleep(0.02)
        self.fail(f"model import did not finish: {task_id}")

    def submit_file(self, source, *, conflict="skip"):
        record = self.manager.submit(NodeEngineModelImportRequest(
            source_path=str(source),
            category="checkpoints",
            conflict=conflict,
            recursive=True,
        ))
        return self.wait_for_import(record["task_id"])

    def test_model_import_copies_and_verifies_sha256(self):
        source = Path(self.temporary.name) / "source" / "model.safetensors"
        source.parent.mkdir()
        source.write_bytes(b"syncanvas-model-data")

        record = self.submit_file(source)

        self.assertEqual(record["status"], "succeeded")
        self.assertEqual(record["progress"], 1.0)
        self.assertEqual(len(record["imported"]), 1)
        target = service.MODELS_DIR / "checkpoints" / "model.safetensors"
        self.assertEqual(target.read_bytes(), source.read_bytes())
        registry = json.loads(service.MODEL_REGISTRY_FILE.read_text(encoding="utf-8"))
        self.assertEqual(registry["files"]["checkpoints/model.safetensors"]["sha256"], record["imported"][0]["sha256"])

    def test_duplicate_hash_is_recorded_without_second_copy(self):
        first = Path(self.temporary.name) / "first" / "one.safetensors"
        second = Path(self.temporary.name) / "second" / "two.safetensors"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"same-model")
        second.write_bytes(b"same-model")

        self.assertEqual(self.submit_file(first)["status"], "succeeded")
        duplicate = self.submit_file(second)

        self.assertEqual(duplicate["status"], "succeeded")
        self.assertEqual(len(duplicate["duplicates"]), 1)
        self.assertFalse((service.MODELS_DIR / "checkpoints" / "two.safetensors").exists())

    def test_rename_conflict_preserves_both_models(self):
        first = Path(self.temporary.name) / "first" / "weights.safetensors"
        second = Path(self.temporary.name) / "second" / "weights.safetensors"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"first")
        second.write_bytes(b"second")

        self.assertEqual(self.submit_file(first)["status"], "succeeded")
        renamed = self.submit_file(second, conflict="rename")

        self.assertEqual(renamed["status"], "succeeded")
        self.assertEqual((service.MODELS_DIR / "checkpoints" / "weights.safetensors").read_bytes(), b"first")
        self.assertEqual((service.MODELS_DIR / "checkpoints" / "weights-2.safetensors").read_bytes(), b"second")

    def test_readonly_model_path_cannot_escape_source_root(self):
        base = Path(self.temporary.name) / "external-models"
        outside = Path(self.temporary.name) / "outside"
        base.mkdir()
        outside.mkdir()
        request = NodeEngineModelPathsRequest(sources=[{
            "id": "external",
            "name": "External",
            "base_path": str(base),
            "paths": {"checkpoints": "../outside"},
            "enabled": True,
        }])

        with self.assertRaises(HTTPException) as caught:
            service.set_model_paths(request)

        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("超出源目录", str(caught.exception.detail))

    def test_extension_zip_rejects_path_traversal(self):
        archive_path = Path(self.temporary.name) / "unsafe.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("package/__init__.py", "NODE_CLASS_MAPPINGS = {}")
            archive.writestr("../escape.py", "bad = True")

        with self.assertRaises(ValueError):
            service._extract_extension_zip(archive_path, service.EXTENSION_STAGING_DIR / "unsafe")

        self.assertFalse((self.root.parent / "escape.py").exists())

    def test_local_extension_copy_excludes_models_and_repository_metadata(self):
        source = Path(self.temporary.name) / "extension-source"
        (source / ".git").mkdir(parents=True)
        (source / "models").mkdir()
        (source / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}", encoding="utf-8")
        (source / "models" / "large.bin").write_bytes(b"not-copied")
        (source / ".git" / "config").write_text("ignored", encoding="utf-8")
        staging = service.EXTENSION_STAGING_DIR / "copy"

        package_root = service._prepare_extension_source(str(source), staging)

        self.assertEqual(package_root, staging)
        self.assertTrue((staging / "__init__.py").is_file())
        self.assertFalse((staging / "models").exists())
        self.assertFalse((staging / ".git").exists())

    def test_recover_marks_running_import_interrupted(self):
        service.MODEL_IMPORT_DIR.mkdir(parents=True, exist_ok=True)
        task_file = service.MODEL_IMPORT_DIR / "pending.json"
        task_file.write_text(json.dumps({"task_id": "pending", "status": "running"}), encoding="utf-8")

        manager = service.ModelImportManager()
        manager.recover()

        recovered = manager.get("pending")
        self.assertEqual(recovered["status"], "interrupted")
        self.assertIn("重启", recovered["error"])


class NodeEngineManagerFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.settings = (root / "static/settings.html").read_text(encoding="utf-8")
        cls.page = (root / "static/node-engine.html").read_text(encoding="utf-8")
        cls.manager = (root / "static/js/node-engine.js").read_text(encoding="utf-8")
        cls.styles = (root / "static/css/node-engine.css").read_text(encoding="utf-8")
        cls.documentation = (root / "docs/NODE_ENGINE.md").read_text(encoding="utf-8")

    def test_settings_exposes_node_engine_manager(self):
        self.assertIn('data-settings-section="node-engine"', self.settings)
        self.assertIn('/static/node-engine.html', self.settings)

    def test_manager_uses_isolated_model_and_extension_apis(self):
        self.assertIn("/api/node-engine/models/import", self.manager)
        self.assertIn("/api/node-engine/model-paths", self.manager)
        self.assertIn("/api/node-engine/extensions/install", self.manager)
        self.assertIn("/api/node-engine/extensions/${encodeURIComponent(id)}/", self.manager)

    def test_manager_defaults_to_canvas_utility_nodes(self):
        self.assertIn('data-engine-tab="nodes"', self.page)
        self.assertIn('<option value="utility">画布实用</option>', self.page)
        self.assertIn("scope:byId('nodeScope').value", self.manager)
        self.assertIn("模型（高级）", self.page)

    def test_nonempty_tables_hide_empty_state(self):
        self.assertIn(".empty-state[hidden]", self.styles)

    def test_dependency_install_requires_explicit_confirmation(self):
        self.assertIn("window.confirm('扩展依赖将安装到节点引擎的独立 Python 环境", self.manager)

    def test_documentation_records_isolation_and_gpl_distribution_gate(self):
        self.assertIn("Process isolation is fault isolation, not a security sandbox", self.documentation)
        self.assertIn("GPL Distribution Gate", self.documentation)


class NodeEngineManifestTests(unittest.TestCase):
    def test_downloadable_gpl_artifact_requires_exact_source_version(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "component": {
                    "id": "node-engine",
                    "source_url": "https://github.com/comfyanonymous/ComfyUI",
                    "source_version": "",
                    "artifact": {
                        "sha256": "a" * 64,
                        "urls": ["https://example.invalid/node-engine.zip"],
                    },
                }
            }), encoding="utf-8")
            with patch.object(component_service, "MANIFEST_FILE", manifest_path):
                with self.assertRaises(component_service.NodeEngineComponentError) as caught:
                    component_service._load_manifest()
        self.assertIn("确切源码地址和上游版本", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
