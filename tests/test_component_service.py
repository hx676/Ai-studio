import hashlib
import json
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services import component_service as service


class ComponentServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_root = Path(tempfile.mkdtemp(prefix="syncanvas-components-"))
        self.originals = {
            name: getattr(service, name)
            for name in (
                "BASE_DIR",
                "DATA_DIR",
                "MANIFEST_FILE",
                "DEFAULT_INSTALL_ROOT",
                "LEGACY_TTS_ROOT",
                "LEGACY_HEYGEM_ROOT",
                "COMPONENT_DATA_DIR",
                "STATE_FILE",
                "REGISTRY_FILE",
                "DOWNLOAD_CACHE_DIR",
            )
        }
        service.BASE_DIR = self.temp_root
        service.DATA_DIR = self.temp_root / "data"
        service.MANIFEST_FILE = self.temp_root / "components-manifest.json"
        service.DEFAULT_INSTALL_ROOT = self.temp_root / "components" / "digital-human"
        service.LEGACY_TTS_ROOT = self.temp_root / "index-tts-2"
        service.LEGACY_HEYGEM_ROOT = self.temp_root / "heygem-win-fix" / "heygem-win"
        service.COMPONENT_DATA_DIR = service.DATA_DIR / "components"
        service.STATE_FILE = service.COMPONENT_DATA_DIR / "digital-human-state.json"
        service.REGISTRY_FILE = service.COMPONENT_DATA_DIR / "digital-human-installed.json"
        service.DOWNLOAD_CACHE_DIR = service.DATA_DIR / "component-cache" / "digital-human"
        service._CANCEL_EVENT.clear()
        service._WORKER = None

    def tearDown(self):
        worker = service._WORKER
        if worker is not None and worker.is_alive():
            service._CANCEL_EVENT.set()
            worker.join(timeout=5)
        for name, value in self.originals.items():
            setattr(service, name, value)
        service._WORKER = None
        service._CANCEL_EVENT.clear()
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def _make_artifact(self, artifact_id, files):
        package_dir = self.temp_root / "packages" / "components"
        package_dir.mkdir(parents=True, exist_ok=True)
        archive = package_dir / f"{artifact_id}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
            for name, content in files.items():
                handle.writestr(name, content)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        return archive, digest

    def _write_manifest(self):
        tts_files = {
            "py312/python.exe": b"python",
            "app.py": b"print('tts')",
            "checkpoints/config.yaml": b"model: test",
        }
        heygem_files = {
            "py38/python.exe": b"python",
            "app.py": b"print('heygem')",
            "pretrain_models/model.bin": b"model",
        }
        tts_archive, tts_hash = self._make_artifact("tts", tts_files)
        heygem_archive, heygem_hash = self._make_artifact("heygem", heygem_files)
        manifest = {
            "schema_version": 1,
            "component": {
                "id": "digital-human",
                "display_name": "Digital Human",
                "version": "test-1",
                "download_size": tts_archive.stat().st_size + heygem_archive.stat().st_size,
                "installed_size": 1024,
                "minimum_free_bytes": 1,
                "artifacts": [
                    {
                        "id": "tts",
                        "display_name": "TTS",
                        "version": "test-1",
                        "filename": tts_archive.name,
                        "download_size": tts_archive.stat().st_size,
                        "installed_size": 512,
                        "sha256": tts_hash,
                        "urls": [],
                        "target": "tts",
                        "sentinels": list(tts_files),
                        "preserve_paths": ["assets/bak", "voices", "outputs"],
                    },
                    {
                        "id": "heygem",
                        "display_name": "HeyGem",
                        "version": "test-1",
                        "filename": heygem_archive.name,
                        "download_size": heygem_archive.stat().st_size,
                        "installed_size": 512,
                        "sha256": heygem_hash,
                        "urls": [],
                        "target": "heygem",
                        "sentinels": ["py38/python.exe", "app.py", "pretrain_models"],
                        "preserve_paths": ["save"],
                    },
                ],
            },
        }
        service.MANIFEST_FILE.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def _wait_for_terminal_state(self, timeout=10):
        deadline = time.monotonic() + timeout
        status = {}
        while time.monotonic() < deadline:
            status = service.get_component_status()
            if status["state"] not in service.ACTIVE_STATES:
                return status
            time.sleep(0.05)
        self.fail(f"component install did not finish: {status}")

    def test_local_component_install_reaches_ready(self):
        self._write_manifest()
        initial = service.get_component_status()
        self.assertEqual(initial["state"], "not_installed")
        self.assertTrue(initial["can_install"])

        service.start_component_install()
        status = self._wait_for_terminal_state()

        self.assertEqual(status["state"], "ready", status)
        self.assertTrue((service.DEFAULT_INSTALL_ROOT / "tts" / "py312" / "python.exe").is_file())
        self.assertTrue((service.DEFAULT_INSTALL_ROOT / "heygem" / "pretrain_models" / "model.bin").is_file())
        self.assertTrue(service.REGISTRY_FILE.is_file())

    def test_manual_download_metadata_is_exposed_without_being_used_as_direct_url(self):
        manifest = self._write_manifest()
        shutil.rmtree(self.temp_root / "packages")
        codes = {"tts": "ae7k", "heygem": "i5s8"}
        for artifact in manifest["component"]["artifacts"]:
            artifact["manual_download"] = {
                "provider": "baidu-pan",
                "share_url": f"https://pan.baidu.com/s/{artifact['id']}",
                "extraction_code": codes[artifact["id"]],
                "filename": artifact["filename"],
            }
        service.MANIFEST_FILE.write_text(json.dumps(manifest), encoding="utf-8")

        status = service.get_component_status()

        self.assertFalse(status["can_install"])
        self.assertTrue(status["manual_download_available"])
        self.assertTrue(status["manual_download_required"])
        self.assertTrue(all(not item["direct_download_available"] for item in status["artifacts"]))
        self.assertTrue(all(not item["local_source_available"] for item in status["artifacts"]))
        self.assertEqual(
            {item["id"]: item["manual_download"]["extraction_code"] for item in status["artifacts"]},
            codes,
        )

    def test_invalid_manual_download_url_is_not_exposed(self):
        manifest = self._write_manifest()
        artifact = manifest["component"]["artifacts"][0]
        artifact["manual_download"] = {
            "provider": "baidu-pan",
            "share_url": "javascript:alert(1)",
            "extraction_code": "nope",
            "filename": artifact["filename"],
        }

        normalized = service._normalize_manifest(manifest)

        self.assertNotIn("manual_download", normalized["component"]["artifacts"][0])

    def test_windows_component_is_reported_as_unsupported_on_macos(self):
        manifest = self._write_manifest()
        for artifact in manifest["component"]["artifacts"]:
            artifact["platforms"] = ["win-x64"]
        service.MANIFEST_FILE.write_text(json.dumps(manifest), encoding="utf-8")

        with mock.patch.object(service, "current_platform_tag", return_value="macos-arm64"), mock.patch.object(
            service, "platform_supported", return_value=False
        ):
            status = service.get_component_status()

        self.assertEqual("unsupported", status["state"])
        self.assertFalse(status["supported"])
        self.assertFalse(status["can_install"])
        self.assertTrue(all(not item["platform_supported"] for item in status["artifacts"]))

    def test_invalid_sha256_is_rejected(self):
        manifest = self._write_manifest()
        manifest["component"]["artifacts"][0]["sha256"] = "0" * 64
        service.MANIFEST_FILE.write_text(json.dumps(manifest), encoding="utf-8")

        service.start_component_install()
        status = self._wait_for_terminal_state()

        self.assertEqual(status["state"], "error")
        self.assertIn("SHA256", status["error"])
        self.assertFalse((service.DEFAULT_INSTALL_ROOT / "tts").exists())

    def test_insufficient_disk_space_is_reported(self):
        manifest = self._write_manifest()
        manifest["component"]["minimum_free_bytes"] = 1024
        service.MANIFEST_FILE.write_text(json.dumps(manifest), encoding="utf-8")
        service._replace_state({"state": "queued", "install_root": str(service.DEFAULT_INSTALL_ROOT)})

        with mock.patch.object(
            service,
            "_disk_usage",
            return_value=SimpleNamespace(total=2048, used=2048, free=0),
        ):
            service._install_worker(manifest, service.DEFAULT_INSTALL_ROOT, False)

        state = service._load_state()
        self.assertEqual(state["state"], "error")
        self.assertIn("磁盘空间不足", state["error"])

    def test_repair_preserves_user_voice_files(self):
        self._write_manifest()
        service.start_component_install()
        first_status = self._wait_for_terminal_state()
        self.assertEqual(first_status["state"], "ready", first_status)

        voice_file = service.DEFAULT_INSTALL_ROOT / "tts" / "assets" / "bak" / "user.wav"
        voice_file.parent.mkdir(parents=True, exist_ok=True)
        voice_file.write_bytes(b"user voice")

        service.start_component_install(force=True)
        repair_status = self._wait_for_terminal_state()

        self.assertEqual(repair_status["state"], "ready", repair_status)
        self.assertEqual(voice_file.read_bytes(), b"user voice")

    def test_interrupted_install_is_recoverable(self):
        service._replace_state(
            {
                "state": "downloading",
                "phase": "downloading",
                "progress_percent": 42,
                "error": "old error",
            }
        )

        service.recover_interrupted_component_install()

        state = service._load_state()
        self.assertEqual(state["state"], "interrupted")
        self.assertEqual(state["phase"], "interrupted")
        self.assertEqual(state["progress_percent"], 42)
        self.assertEqual(state["error"], "")

    def test_legacy_layout_is_detected(self):
        self._write_manifest()
        for path in (
            service.LEGACY_TTS_ROOT / "py312" / "python.exe",
            service.LEGACY_TTS_ROOT / "app.py",
            service.LEGACY_TTS_ROOT / "checkpoints" / "config.yaml",
            service.LEGACY_HEYGEM_ROOT / "py38" / "python.exe",
            service.LEGACY_HEYGEM_ROOT / "app.py",
            service.LEGACY_HEYGEM_ROOT / "pretrain_models",
        ):
            if path.suffix:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")
            else:
                path.mkdir(parents=True, exist_ok=True)

        status = service.get_component_status()
        self.assertEqual(status["state"], "ready")
        self.assertEqual(status["installed_source"], "legacy")

    def test_zip_path_traversal_is_rejected(self):
        archive = self.temp_root / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("../outside.txt", "unsafe")
        staging = self.temp_root / "staging"
        staging.mkdir()
        with self.assertRaises(service.ComponentError):
            service._extract_zip(archive, staging, lambda *_: None)
        self.assertFalse((self.temp_root / "outside.txt").exists())


if __name__ == "__main__":
    unittest.main()
