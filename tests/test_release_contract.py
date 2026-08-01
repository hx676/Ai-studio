import json
import unittest
from pathlib import Path

from tools import release_preflight


ROOT = Path(__file__).resolve().parents[1]


class ReleaseContractTests(unittest.TestCase):
    def test_release_script_contains_required_source_and_metadata(self):
        script = (ROOT / "tools" / "build_modular_release.ps1").read_text(encoding="utf-8")
        for required in (
            '"custom_nodes"',
            '"CLI"',
            '"LICENSE"',
            '"node-engine-manifest.json"',
            '"requirements.lock"',
            '"package-lock.json"',
            '"tailwind.config.js"',
            "release_smoke_test.py",
            "Assert-ReleaseManifest",
            "release_preflight.py",
            "Write-Utf8NoBomFile",
            "ReleaseTimestamp",
            "launcherFileVersion",
            "--artifacts-path",
            "--self-contained true",
            '"packages\\components"',
            '"static\\vendor\\css\\tailwind.css"',
            "manual_download",
            "independently versioned",
            "must declare platforms",
        ):
            self.assertIn(required, script)

    def test_macos_packaging_pipeline_is_present(self):
        script = (ROOT / "tools" / "build_macos_dmg.ps1").read_text(encoding="utf-8")
        launcher = (ROOT / "macos" / "SynCanvas").read_text(encoding="utf-8")
        native = (ROOT / "tools" / "build_macos_dmg_native.sh").read_text(encoding="utf-8")
        self.assertIn("macos-universal", script)
        self.assertIn("dmg-bootstrap", script)
        self.assertIn("release_smoke_test.py", script)
        self.assertIn("Application Support/SynCanvas", launcher)
        self.assertIn("requirements.lock", launcher)
        self.assertIn("codesign", native)
        self.assertIn("notarytool", native)

    def test_node_engine_source_offer_is_complete(self):
        manifest = json.loads((ROOT / "node-engine-manifest.json").read_text(encoding="utf-8"))
        component = manifest["component"]
        self.assertEqual("GPL-3.0", component["license"])
        self.assertTrue(component["source_url"].startswith("https://"))
        self.assertTrue(component["source_version"])
        self.assertTrue(component["source_offer_url"].startswith("https://"))

    def test_native_feature_packages_exist(self):
        for package in (
            "syncanvas_agent_skill",
            "syncanvas_image_compare",
            "syncanvas_output_folder",
            "syncanvas_runtime_node",
            "syncanvas_templates",
        ):
            self.assertTrue((ROOT / "custom_nodes" / package / "node.json").is_file())

    def test_launcher_publish_is_single_file_and_self_contained(self):
        project = (ROOT / "launcher" / "SynCanvasLauncher.csproj").read_text(encoding="utf-8")
        self.assertIn("<SelfContained>true</SelfContained>", project)
        self.assertIn("<PublishSingleFile>true</PublishSingleFile>", project)
        self.assertIn("<RuntimeIdentifier>win-x64</RuntimeIdentifier>", project)
        self.assertIn("publish-artifacts\\**", project)
        supervisor = (ROOT / "launcher" / "SupervisorClient.cs").read_text(encoding="utf-8")
        self.assertIn('psi.Environment["PYTHONDONTWRITEBYTECODE"] = "1"', supervisor)

    def test_release_runtime_does_not_write_source_bytecode_caches(self):
        supervisor = (ROOT / "tools" / "service_supervisor_parts" / "cli.py").read_text(encoding="utf-8")
        smoke = (ROOT / "tools" / "release_smoke_test.py").read_text(encoding="utf-8")
        self.assertIn('env.setdefault("PYTHONDONTWRITEBYTECODE", "1")', supervisor)
        self.assertIn('"PYTHONDONTWRITEBYTECODE": "1"', smoke)

    def test_release_builder_uses_a_curated_root_file_list(self):
        script = (ROOT / "tools" / "build_modular_release.ps1").read_text(encoding="utf-8")
        self.assertNotIn('foreach ($dir in @("python", "packages"))', script)
        self.assertNotIn("Get-ChildItem -LiteralPath $SourceRoot -File | Where-Object", script)
        self.assertNotIn('"PROJECT_SELF_CHECK.md"', script)
        self.assertNotIn('"get-pip.py"', script)
        self.assertIn("Reset-StagedMutableDirectories $coreStage", script)
        self.assertIn('Get-ChildItem -LiteralPath $SourceRoot -File -Filter "*SynCanvas.bat"', script)

    def test_source_release_preflight_passes(self):
        result = release_preflight.audit_source(ROOT)
        self.assertEqual([], result["errors"])

    def test_windows_installer_preserves_user_owned_directories(self):
        script = (ROOT / "installer" / "SynCanvas.iss").read_text(encoding="utf-8")
        self.assertIn("DefaultDirName={localappdata}\\Programs\\SynCanvas", script)
        self.assertIn("Flags: uninsneveruninstall", script)
        self.assertIn("PrepareToInstall", script)
        self.assertIn("service_supervisor.py", script)
        self.assertIn('Name: "english"', script)
        self.assertIn("LicenseFile=..\\LICENSE", script)
        wrapper = (ROOT / "tools" / "build_windows_installer.ps1").read_text(encoding="utf-8")
        self.assertIn("release_preflight.py", wrapper)
        self.assertIn("SynCanvas-Setup-$Version-win-x64.exe", wrapper)
        self.assertIn("Unsafe installer staging path", wrapper)

    def test_browser_compiler_is_replaced_by_built_css(self):
        self.assertTrue((ROOT / "static" / "vendor" / "css" / "tailwind.css").is_file())
        for name in ("index.html", "canvas.html", "api-settings.html"):
            source = (ROOT / "static" / name).read_text(encoding="utf-8")
            self.assertNotIn("tailwindcss-cdn.js", source)


if __name__ == "__main__":
    unittest.main()
