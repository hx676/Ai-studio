import json
import io
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app import legacy
from app.services import node_extension_asset_service
from app.services.node_extension_service import NodeExtensionRegistry


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_nodes" / "syncanvas_3d_director"


class DirectorExtensionManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((PACKAGE / "node.json").read_text(encoding="utf-8"))
        cls.index = (PACKAGE / "web" / "index.js").read_text(encoding="utf-8")
        cls.editor = (PACKAGE / "web" / "director-editor.js").read_text(encoding="utf-8")
        cls.styles = (PACKAGE / "web" / "styles.css").read_text(encoding="utf-8")
        cls.features = (PACKAGE / "web" / "director-features.js").read_text(encoding="utf-8")
        cls.host_runtime = (ROOT / "static" / "js" / "node-extensions.js").read_text(encoding="utf-8")
        cls.host_styles = (ROOT / "static" / "css" / "node-extensions.css").read_text(encoding="utf-8")

    def test_manifest_is_bilingual_frontend_node_for_both_canvases(self):
        self.assertEqual("syncanvas.3d-director", self.manifest["id"])
        self.assertEqual("3D 导演台", self.manifest["name_zh"])
        node = self.manifest["nodes"][0]
        self.assertEqual("director-stage", node["id"])
        self.assertEqual(["classic", "smart"], node["surfaces"])
        self.assertEqual("frontend", node["execution"])
        self.assertEqual("director-stage", node["frontend_key"])
        self.assertEqual({"background", "scene_in", "director_note"}, {item["id"] for item in node["inputs"]})
        self.assertEqual({"preview", "depth", "character_mask", "scene", "shot_prompt"}, {item["id"] for item in node["outputs"]})
        self.assertEqual(3, node["version"])
        self.assertEqual("摄像机画面", node["outputs"][0]["name_zh"])
        self.assertEqual(2, node["defaults"]["scene"]["schemaVersion"])

    def test_editor_contains_scene_workflow_and_json_only_state(self):
        for token in (
            "导演视角", "机位视角", "添加角色", "添加群众", "data-mode=\"translate\"",
            "data-mode=\"rotate\"", "data-mode=\"scale\"", "capturePreview", "syncDirectorOutputs",
            "normalizeScene", "context.uploadAsset", "sceneData.objects", "sceneData.cameras",
            "TransformControls", "GLTFLoader", "data-transform-space", "data-toggle-snap",
            "data-timeline-key", "depthUrl", "characterMaskUrl", "data-import-model",
        ):
            self.assertIn(token, self.editor)
        for token in ("POSE_PRESETS", "createRiggedCharacter", "solveCharacterIk", "sampleCameraTrack", "maskColorForId"):
            self.assertIn(token, self.features)
        self.assertIn("previewUrl", self.editor)
        self.assertIn("currentCameraImageUrl", self.index)
        self.assertNotIn("previewUrl || background", self.index)
        self.assertIn("extensionOutputs", self.editor)
        self.assertNotIn("data:image/", self.editor)

    def test_editor_host_and_responsive_layout_are_present(self):
        for token in ("director-editor", "director-viewport", "director-sidebar-left", "director-sidebar-right", "director-add-menu"):
            self.assertIn(token, self.styles)
        self.assertIn("context.openEditor", self.index)
        for token in ("function openEditor(options={})", "function closeEditor(reason='close')", "async function uploadAsset", "activeEditor:null"):
            self.assertIn(token, self.host_runtime)
        self.assertIn("/api/node-extension-assets", self.host_runtime)
        self.assertIn(".extension-editor-backdrop", self.host_styles)
        self.assertIn(".extension-editor-content", self.host_styles)

    def test_v1_scene_is_migrated_without_losing_existing_transforms(self):
        source = {
            "schemaVersion": 1,
            "activeCameraId": "camera-1",
            "objects": [{"id":"actor-old", "type":"character", "position":[1,2,3], "rotation":[0,0,0,1], "scale":[1,1,1]}],
            "cameras": [{"id":"camera-1", "position":[0,2,7], "rotation":[0,0,0,1], "focalLength":35, "aspect":"16:9"}],
            "environment": {"gridVisible":False},
        }
        module_uri = (PACKAGE / "web" / "director-editor.js").resolve().as_uri()
        script = f"import {{normalizeScene}} from {json.dumps(module_uri)}; console.log(JSON.stringify(normalizeScene({json.dumps(source)})));"
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        migrated = json.loads(completed.stdout)
        self.assertEqual(2, migrated["schemaVersion"])
        self.assertEqual([1, 2, 3], migrated["objects"][0]["position"])
        self.assertEqual("standing", migrated["objects"][0]["poseId"])
        self.assertEqual("world", migrated["settings"]["transformSpace"])
        self.assertEqual(5, migrated["timeline"]["duration"])

    def test_camera_image_is_invalidated_after_the_scene_changes(self):
        source = {
            "previewUrl": "/assets/camera.png",
            "scene": {
                "schemaVersion": 2,
                "activeCameraId": "camera-1",
                "objects": [],
                "cameras": [{"id":"camera-1", "position":[0,2,7], "rotation":[0,0,0,1], "focalLength":50, "aspect":"16:9"}],
            },
        }
        module_uri = (PACKAGE / "web" / "director-editor.js").resolve().as_uri()
        script = f"""
            import {{currentCameraImageUrl, normalizeDirectorData}} from {json.dumps(module_uri)};
            const node = {json.dumps({"data": source})};
            normalizeDirectorData(node);
            const before = currentCameraImageUrl(node);
            node.data.scene.cameras[0].position[0] = 1;
            const after = currentCameraImageUrl(node);
            console.log(JSON.stringify({{before, after}}));
        """
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        result = json.loads(completed.stdout)
        self.assertEqual("/assets/camera.png", result["before"])
        self.assertEqual("", result["after"])

    def test_frontend_package_is_discoverable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "custom_nodes"
            root.mkdir()
            shutil.copytree(PACKAGE, root / PACKAGE.name)
            registry = NodeExtensionRegistry(root, Path(temp_dir) / "state.json")
            state = registry.initialize()
            self.assertEqual("loaded", state["packages"][0]["status"])
            self.assertEqual("syncanvas.3d-director/director-stage", state["nodes"][0]["type"])


class DirectorModelUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_assets_dir = legacy.ASSETS_DIR
        self.temp_dir = tempfile.TemporaryDirectory()
        legacy.ASSETS_DIR = self.temp_dir.name

    def tearDown(self):
        legacy.ASSETS_DIR = self.original_assets_dir
        self.temp_dir.cleanup()

    async def test_accepts_valid_glb_2_model(self):
        manifest = json.dumps({"asset":{"version":"2.0"}, "scenes":[{}], "scene":0}, separators=(",", ":")).encode("utf-8")
        manifest += b" " * ((4 - len(manifest) % 4) % 4)
        payload = struct.pack("<4sII", b"glTF", 2, 20 + len(manifest)) + struct.pack("<II", len(manifest), 0x4E4F534A) + manifest
        upload = UploadFile(filename="actor.glb", file=io.BytesIO(payload), headers={"content-type":"model/gltf-binary"})
        result = await node_extension_asset_service.upload_node_extension_asset(upload, "model", "3d-director")
        self.assertTrue(result["url"].startswith("/assets/node-extensions/3d-director/model_"))
        self.assertTrue((Path(self.temp_dir.name) / result["url"].removeprefix("/assets/")).is_file())

    async def test_rejects_gltf_with_external_dependency(self):
        payload = json.dumps({"asset":{"version":"2.0"}, "buffers":[{"uri":"mesh.bin", "byteLength":4}]}).encode("utf-8")
        upload = UploadFile(filename="actor.gltf", file=io.BytesIO(payload), headers={"content-type":"model/gltf+json"})
        with self.assertRaises(HTTPException) as raised:
            await node_extension_asset_service.upload_node_extension_asset(upload, "model", "3d-director")
        self.assertEqual(400, raised.exception.status_code)


if __name__ == "__main__":
    unittest.main()
