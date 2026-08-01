import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.routing import APIRoute

from app import upstream_runtime
from app.main import UPSTREAM_SYNC, app
from app.models.canvas import CanvasCreateRequest


def routes_for(path, method):
    return [
        route
        for route in app.router.routes
        if isinstance(route, APIRoute)
        and route.path == path
        and method.upper() in (route.methods or set())
    ]


class UpstreamBridgeRouteTests(unittest.TestCase):
    def test_sync_baseline_and_route_counts(self):
        self.assertTrue(UPSTREAM_SYNC["commit"].startswith("96c0085"))
        self.assertEqual("2026.07.28.1", UPSTREAM_SYNC["version"])
        self.assertGreaterEqual(UPSTREAM_SYNC["installed_count"], 100)
        self.assertGreaterEqual(UPSTREAM_SYNC["replaced_count"], 7)

    def test_local_routes_win_except_compatible_asset_library(self):
        canvas_routes = routes_for("/api/canvases", "GET")
        self.assertEqual(1, len(canvas_routes))
        self.assertNotEqual("app.upstream_runtime", canvas_routes[0].endpoint.__module__)

        asset_routes = routes_for("/api/asset-library", "GET")
        self.assertEqual(1, len(asset_routes))
        self.assertEqual("app.upstream_runtime", asset_routes[0].endpoint.__module__)

    def test_updater_routes_are_not_imported(self):
        self.assertFalse(routes_for("/api/check-update", "GET"))
        self.assertFalse(routes_for("/api/update-connectivity", "GET"))

    def test_jimeng_cli_management_routes_are_available(self):
        self.assertTrue(routes_for("/api/jimeng/status", "GET"))
        self.assertTrue(routes_for("/api/jimeng/install/start", "POST"))
        self.assertTrue(routes_for("/api/jimeng/login/start", "POST"))
        self.assertTrue(routes_for("/api/jimeng/login/qr", "GET"))

    def test_jimeng_login_fields_are_parsed_without_treating_page_as_image(self):
        text = (
            "verification_uri: https://jimeng.example/cli-auth?user_code=abc123\n"
            "user_code: abc123\n"
            "expires_at: 2099-07-28T14:51:34+08:00"
        )
        fields = upstream_runtime.jimeng_login_response_fields(text)
        self.assertEqual("https://jimeng.example/cli-auth?user_code=abc123", fields["verification_url"])
        self.assertEqual("abc123", fields["user_code"])
        self.assertEqual("/api/jimeng/login/qr?v=0", fields["qr_image_url"])
        self.assertEqual(fields["qr_image_url"], fields["qr_url"])

    def test_canvas_create_metadata_keeps_project_fields(self):
        request = CanvasCreateRequest(
            title="Project board",
            project="demo",
            board_x=120.5,
            board_y=-32,
        )
        self.assertEqual("demo", request.project)
        self.assertEqual(120.5, request.board_x)
        self.assertEqual(-32, request.board_y)


class UpstreamBridgeInterfaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_sync_and_asset_library_compatibility_shapes(self):
        sync = await self.client.get("/api/upstream-sync")
        self.assertEqual(200, sync.status_code)
        self.assertTrue(sync.json()["commit"].startswith("96c0085"))

        assets = await self.client.get("/api/asset-library")
        self.assertEqual(200, assets.status_code)
        library = assets.json()["library"]
        self.assertIsInstance(library.get("libraries"), list)
        self.assertIsInstance(library.get("categories"), list)

    async def test_prompt_libraries_and_workflow_import(self):
        prompts = await self.client.get("/api/prompt-libraries")
        self.assertEqual(200, prompts.status_code)
        self.assertIsInstance(prompts.json()["library"].get("libraries"), list)

        workflow = {
            "format": "syncanvas-canvas-workflow",
            "version": 1,
            "nodes": [{"id": "prompt_1", "type": "prompt", "text": "hello"}],
            "connections": [],
        }
        imported = await self.client.post(
            "/api/canvas-workflows/import",
            files={"file": ("workflow.json", json.dumps(workflow).encode("utf-8"), "application/json")},
        )
        self.assertEqual(200, imported.status_code)
        data = imported.json()
        self.assertEqual("prompt_1", data["nodes"][0]["id"])
        self.assertEqual([], data["connections"])

    async def test_metadata_update_invalidates_stale_full_canvas_save(self):
        from app import legacy

        with tempfile.TemporaryDirectory(prefix="syncanvas-canvas-conflict-") as temp:
            canvas_dir = str(Path(temp) / "canvases")
            Path(canvas_dir).mkdir(parents=True)
            with (
                patch.object(legacy, "CANVAS_DIR", canvas_dir),
                patch.object(upstream_runtime, "CANVAS_DIR", canvas_dir),
            ):
                created_response = await self.client.post("/api/canvases", json={"title": "before"})
                self.assertEqual(200, created_response.status_code)
                created = created_response.json()["canvas"]

                changed = await self.client.post(
                    f"/api/canvases/{created['id']}/meta",
                    json={"title": "metadata wins"},
                )
                self.assertEqual(200, changed.status_code)
                self.assertGreater(changed.json()["canvas"]["updated_at"], created["updated_at"])

                stale = await self.client.put(
                    f"/api/canvases/{created['id']}",
                    json={
                        "title": "stale overwrite",
                        "nodes": [{"id": "stale"}],
                        "connections": [],
                        "viewport": {},
                        "base_updated_at": created["updated_at"],
                    },
                )
                self.assertEqual(409, stale.status_code)
                current = await self.client.get(f"/api/canvases/{created['id']}")
                self.assertEqual("metadata wins", current.json()["canvas"]["title"])
                self.assertEqual([], current.json()["canvas"]["nodes"])

    async def test_jimeng_qr_endpoint_returns_png_and_rejects_empty_state(self):
        original = dict(upstream_runtime.JIMENG_LOGIN_SESSION)
        try:
            upstream_runtime.JIMENG_LOGIN_SESSION.update({
                "stdout": (
                    "verification_uri: https://jimeng.example/cli-auth?user_code=abc123\n"
                    "user_code: abc123\n"
                    "expires_at: 2099-07-28T14:51:34+08:00"
                ),
                "stderr": "",
                "started_at": 123,
            })
            response = await self.client.get("/api/jimeng/login/qr")
            self.assertEqual(200, response.status_code)
            self.assertEqual("image/png", response.headers["content-type"])
            self.assertTrue(response.content.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertIn("no-store", response.headers["cache-control"])

            upstream_runtime.JIMENG_LOGIN_SESSION.update({"stdout": "", "stderr": ""})
            missing = await self.client.get("/api/jimeng/login/qr")
            self.assertEqual(404, missing.status_code)
            self.assertIn("重新点击扫码登录", missing.json()["detail"])

            upstream_runtime.JIMENG_LOGIN_SESSION.update({
                "stdout": (
                    "verification_uri: https://jimeng.example/cli-auth?user_code=expired\n"
                    "expires_at: 2020-07-28T14:51:34+08:00"
                ),
                "stderr": "",
            })
            expired = await self.client.get("/api/jimeng/login/qr")
            self.assertEqual(410, expired.status_code)
            self.assertIn("已过期", expired.json()["detail"])
        finally:
            upstream_runtime.JIMENG_LOGIN_SESSION.clear()
            upstream_runtime.JIMENG_LOGIN_SESSION.update(original)

    async def test_jimeng_login_keeps_device_flow_process_running(self):
        original = dict(upstream_runtime.JIMENG_LOGIN_SESSION)
        proc = SimpleNamespace(returncode=None, stdout=None, stderr=None)
        captured = {}

        def build_command(args, exe=None):
            captured["args"] = list(args)
            return [exe or "dreamina", *args]

        def close_reader(coro):
            coro.close()
            return None

        try:
            with (
                patch.object(upstream_runtime, "jimeng_cli_executable", return_value="dreamina"),
                patch.object(upstream_runtime, "jimeng_command", side_effect=build_command),
                patch.object(upstream_runtime.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)),
                patch.object(upstream_runtime.asyncio, "create_task", side_effect=close_reader),
                patch.object(upstream_runtime.asyncio, "sleep", AsyncMock()),
            ):
                result = await upstream_runtime.jimeng_login_start()

            self.assertEqual(["login"], captured["args"])
            self.assertTrue(result["running"])
        finally:
            upstream_runtime.JIMENG_LOGIN_SESSION.clear()
            upstream_runtime.JIMENG_LOGIN_SESSION.update(original)


if __name__ == "__main__":
    unittest.main()
