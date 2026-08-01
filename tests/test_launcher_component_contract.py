import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ONE_CLICK = ROOT / "一键启动 SynCanvas.bat"
RUN_BAT = ROOT / "run.bat"
PREFLIGHT = ROOT / "tools" / "runtime_preflight.py"
SUPERVISOR = ROOT / "tools" / "service_supervisor_parts" / "cli.py"
CLIENT = ROOT / "launcher" / "SupervisorClient.cs"
WINDOW_CODE = ROOT / "launcher" / "MainWindow.xaml.cs"
WINDOW_XAML = ROOT / "launcher" / "MainWindow.xaml"


class LauncherComponentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.one_click = ONE_CLICK.read_text(encoding="utf-8")
        cls.run_bat = RUN_BAT.read_text(encoding="utf-8")
        cls.preflight = PREFLIGHT.read_text(encoding="utf-8")
        cls.supervisor = SUPERVISOR.read_text(encoding="utf-8")
        cls.client = CLIENT.read_text(encoding="utf-8")
        cls.window_code = WINDOW_CODE.read_text(encoding="utf-8")
        cls.window_xaml = WINDOW_XAML.read_text(encoding="utf-8")

    def test_one_click_always_uses_the_current_launcher_pipeline(self):
        self.assertIn('call "%~dp0run.bat"', self.one_click)
        self.assertNotIn('start "" "%~dp0SynCanvasLauncher.exe"', self.one_click)

    def test_main_runtime_dependencies_are_checked_and_repaired(self):
        self.assertIn("runtime_preflight.py", self.run_bat)
        self.assertIn("--check", self.run_bat)
        self.assertIn("--repair", self.run_bat)
        self.assertIn('"qrcode"', self.preflight)
        self.assertIn('"gradio_client"', self.preflight)
        self.assertIn('"-r"', self.preflight)

    def test_native_launcher_only_starts_and_stops_main(self):
        self.assertIn("--start-once main", self.client)
        self.assertIn("--stop main", self.client)
        self.assertIn("--project-backend-pids main", self.client)
        self.assertIn('service.Key == "main"', self.client)

    def test_backend_pid_query_can_filter_component_processes(self):
        self.assertIn("def project_backend_process_items(keys:", self.supervisor)
        self.assertIn("key not in key_set", self.supervisor)
        self.assertIn("print_project_backend_pids(args.services or None)", self.supervisor)
        self.assertIn("数字人组件已安装，当前按需待命", self.supervisor)
        self.assertIn('not exited.get("stop_reason")', self.supervisor)

    def test_optional_components_do_not_drive_launcher_start_stop_state(self):
        self.assertIn("s.Required &&", self.window_code)
        self.assertIn("if (!service.Required)", self.window_code)
        self.assertIn("由数字人页面按需启动", self.window_code)
        self.assertIn("TTS 与 HeyGem 组件不会被停止", self.window_code)

    def test_component_paths_are_not_manually_configured_in_launcher(self):
        for field in (
            "TtsBaseUrlBox",
            "TtsRootBox",
            "TtsPythonBox",
            "HeyGemBaseUrlBox",
            "HeyGemApiUrlBox",
            "HeyGemRootBox",
        ):
            self.assertNotIn(field, self.window_xaml)
            self.assertNotIn(field, self.window_code)
        self.assertIn("数字人组件 (TTS + HeyGem)", self.window_xaml)


if __name__ == "__main__":
    unittest.main()
