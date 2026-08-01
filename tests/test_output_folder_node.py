import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_nodes" / "syncanvas_output_folder"
MANIFEST = PACKAGE / "node.json"
FRONTEND = PACKAGE / "web" / "index.js"
CLASSIC_STATE = ROOT / "static" / "js" / "canvas" / "state.js"
SMART_STATE = ROOT / "static" / "js" / "smart-canvas" / "state.js"


class OutputFolderNodeManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.node = cls.manifest["nodes"][0]

    def test_package_is_enabled_for_both_canvas_surfaces(self):
        self.assertEqual("syncanvas.output-folder", self.manifest["id"])
        self.assertEqual("输出到文件夹", self.manifest["name_zh"])
        self.assertTrue(self.manifest["enabled_by_default"])
        self.assertEqual(["classic", "smart"], self.node["surfaces"])
        self.assertEqual("frontend", self.node["execution"])

    def test_media_input_accepts_batches_and_path_output_is_text(self):
        media = self.node["inputs"][0]
        self.assertEqual("files", media["id"])
        self.assertEqual(["image", "audio", "video"], media["types"])
        self.assertTrue(media["multiple"])
        self.assertTrue(media["required"])
        self.assertEqual(["text"], self.node["outputs"][0]["types"])

    def test_frontend_only_package_has_no_python_dependencies(self):
        self.assertIn("Frontend-only", (PACKAGE / "__init__.py").read_text(encoding="utf-8"))
        self.assertIn("no Python dependencies", (PACKAGE / "requirements.txt").read_text(encoding="utf-8"))


class OutputFolderNodeFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND.read_text(encoding="utf-8")
        cls.classic = CLASSIC_STATE.read_text(encoding="utf-8")
        cls.smart = SMART_STATE.read_text(encoding="utf-8")

    def test_export_requires_an_explicit_browser_folder_choice(self):
        self.assertIn("globalThis.showDirectoryPicker", self.frontend)
        self.assertIn("mode:'readwrite'", self.frontend)
        self.assertIn("directory.requestPermission({mode:'readwrite'})", self.frontend)
        self.assertNotIn("/api/", self.frontend)

    def test_export_streams_each_blob_to_a_file_handle(self):
        self.assertIn("await directory.getFileHandle(name, {create:true})", self.frontend)
        self.assertIn("await handle.createWritable()", self.frontend)
        self.assertIn("await writable.write(blob)", self.frontend)
        self.assertIn("await writable.close()", self.frontend)
        self.assertIn("for (let index = 0; index < media.length; index += 1)", self.frontend)

    def test_export_enforces_file_count_and_size_limits(self):
        self.assertIn("const MAX_FILES = 500", self.frontend)
        self.assertIn("const MAX_FILE_BYTES = 500 * 1024 * 1024", self.frontend)
        self.assertIn("const MAX_TOTAL_BYTES = 1024 * 1024 * 1024", self.frontend)
        self.assertIn("declared > MAX_FILE_BYTES", self.frontend)
        self.assertIn("totalBytes + blob.size > MAX_TOTAL_BYTES", self.frontend)

    def test_export_handles_conflicts_and_does_not_persist_directory_handles(self):
        self.assertIn("conflictMode === 'overwrite'", self.frontend)
        self.assertIn("await fileExists(directory, candidate)", self.frontend)
        self.assertIn("lastFolderName", self.frontend)
        self.assertNotIn("directoryHandle:", self.frontend)
        self.assertNotIn("absolutePath", self.frontend)

    def test_both_canvas_input_collectors_preserve_audio_and_video_kinds(self):
        for source in (self.classic, self.smart):
            self.assertIn("source.audio || source.audios", source)
            self.assertIn("source.videos || source.generatedVideos", source)
            self.assertIn("items.push({kind:'audio'", source)
            self.assertIn("items.push({kind:'video'", source)


if __name__ == "__main__":
    unittest.main()
