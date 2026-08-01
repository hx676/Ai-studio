import os
import io
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx
from uvicorn.logging import AccessFormatter

import main as entrypoint
from app.core.security import install_log_redaction, redact_sensitive_text, safe_print, websocket_origin_allowed
from app.main import app
from app.services import provider_service


class LocalBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_rejects_untrusted_host_and_cross_origin_write(self):
        response = await self.client.get("/api/upstream-sync", headers={"host": "evil.example"})
        self.assertEqual(400, response.status_code)
        response = await self.client.post(
            "/api/does-not-exist",
            headers={"origin": "https://evil.example", "host": "127.0.0.1:3000"},
        )
        self.assertEqual(403, response.status_code)
        response = await self.client.post(
            "/api/does-not-exist",
            headers={"origin": "http://127.0.0.1:3000", "host": "127.0.0.1:3000"},
        )
        self.assertEqual(404, response.status_code)

    def test_main_host_is_loopback_only(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SYNCANVAS_MAIN_HOST", None)
            self.assertEqual("127.0.0.1", entrypoint.main_host())
        with mock.patch.dict(os.environ, {"SYNCANVAS_MAIN_HOST": "0.0.0.0"}):
            with self.assertRaises(RuntimeError):
                entrypoint.main_host()

    def test_websocket_origin_and_host(self):
        self.assertTrue(websocket_origin_allowed(SimpleNamespace(headers={"host": "127.0.0.1:3000"})))
        self.assertFalse(websocket_origin_allowed(SimpleNamespace(headers={
            "host": "127.0.0.1:3000", "origin": "https://evil.example"
        })))
        self.assertFalse(websocket_origin_allowed(SimpleNamespace(headers={"host": "evil.example"})))


class SecretHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_status_never_returns_token(self):
        original_key = provider_service.MODELSCOPE_API_KEY
        original_file = provider_service.GLOBAL_CONFIG_FILE
        with tempfile.TemporaryDirectory(prefix="syncanvas-token-") as temp:
            provider_service.MODELSCOPE_API_KEY = "ms-secret-value"
            provider_service.GLOBAL_CONFIG_FILE = str(Path(temp) / "missing.json")
            try:
                result = await provider_service.get_global_token()
            finally:
                provider_service.MODELSCOPE_API_KEY = original_key
                provider_service.GLOBAL_CONFIG_FILE = original_file
        self.assertEqual({"configured": True}, result)
        self.assertNotIn("ms-secret-value", str(result))

    def test_redacts_headers_assignments_and_urls(self):
        raw = "Authorization: Bearer abcdefghijkl api_key=topsecret modelscope_api_token=modelsecret https://x.test/a?token=urlsecret"
        redacted = redact_sensitive_text(raw)
        self.assertNotIn("abcdefghijkl", redacted)
        self.assertNotIn("topsecret", redacted)
        self.assertNotIn("modelsecret", redacted)
        self.assertNotIn("urlsecret", redacted)
        self.assertIn("REDACTED", redacted)

    def test_print_and_logging_redact_nested_secret_values(self):
        install_log_redaction()
        output = io.StringIO()
        safe_print({"modelscope_api_token": "print-secret", "ok": "visible"}, file=output)
        self.assertNotIn("print-secret", output.getvalue())
        self.assertIn("visible", output.getvalue())

        record = logging.getLogRecordFactory()(
            "syncanvas-test",
            logging.INFO,
            __file__,
            1,
            "api_key=%s",
            ("logging-secret",),
            None,
        )
        rendered = record.getMessage()
        self.assertNotIn("logging-secret", rendered)
        self.assertIn("REDACTED", rendered)

    def test_log_redaction_preserves_uvicorn_access_formatter_arguments(self):
        install_log_redaction()
        record = logging.getLogRecordFactory()(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:3000", "GET", "/api/test?token=url-secret", "1.1", 200),
            None,
        )
        self.assertEqual(len(record.args), 5)
        rendered = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(record)
        self.assertNotIn("url-secret", rendered)
        self.assertIn("REDACTED", rendered)


if __name__ == "__main__":
    unittest.main()
