import os
import unittest
from unittest.mock import patch

from app import legacy, upstream_runtime
from app.services import provider_service


class FakeResponse:
    def __init__(self, status_code, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeAsyncClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.calls = []
        self.__class__.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if len(self.calls) == 1:
            return FakeResponse(
                400,
                '{"error":{"code":"MODEL_PRICE_ERROR","message":"image_modalities precharge pricing_mode"}}',
            )
        return FakeResponse(200, payload={"data": [{"url": "https://example.com/result.png"}]})


class FakeGeminiFallbackClient(FakeAsyncClient):
    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if len(self.calls) == 1:
            return FakeResponse(405, "<html><h1>405 Not Allowed</h1></html>")
        return FakeResponse(200, payload={
            "candidates": [{
                "content": {
                    "parts": [{"inlineData": {"mimeType": "image/png", "data": "dGVzdA=="}}]
                }
            }]
        })


class UpstreamApimartHelperTests(unittest.TestCase):
    def test_deprecated_async_image_endpoint_is_ignored(self):
        for module in (provider_service, upstream_runtime):
            with self.subTest(module=module.__name__):
                self.assertTrue(module.is_deprecated_openai_image_async_endpoint("/v1/images/generations/async/"))
                self.assertTrue(
                    module.is_deprecated_openai_image_async_endpoint(
                        "https://api.example.com/v1/images/generations/async?legacy=1"
                    )
                )
                self.assertEqual(
                    "",
                    module.normalize_endpoint_override("/v1/images/generations/async", "文生图端口"),
                )
                self.assertEqual(
                    "https://api.example.com/v1/images/generations",
                    module.provider_endpoint_url(
                        {
                            "base_url": "https://api.example.com/v1",
                            "image_generation_endpoint": "/v1/images/generations/async",
                        },
                        "image_generation_endpoint",
                        "/v1/images/generations",
                    ),
                )

    def test_apimart_model_rules_and_pricing_detection_match(self):
        for module in (legacy, upstream_runtime):
            with self.subTest(module=module.__name__):
                self.assertEqual("1K", module.apimart_image_resolution_for_model("nano-banana-ext", "4K"))
                self.assertEqual("4K", module.apimart_image_resolution_for_model("nano-banana-pro-ext", "4k"))
                self.assertTrue(module.apimart_model_supports_official_fallback("nano-banana-pro-ext"))
                self.assertFalse(module.apimart_model_supports_official_fallback("nano-banana-ext-official"))
                self.assertTrue(
                    module.apimart_image_modalities_pricing_error(
                        '{"error":{"code":"MODEL_PRICE_ERROR","message":"image_modalities"}}'
                    )
                )
                self.assertFalse(
                    module.apimart_image_modalities_pricing_error(
                        '{"error":{"code":"MODEL_PRICE_ERROR","message":"text_modalities"}}'
                    )
                )

    def test_apimart_gemini_override_uses_bearer_auth(self):
        provider = {
            "id": "apimart",
            "name": "APIMART",
            "protocol": "gemini",
            "base_url": "https://api.apimart.ai/v1",
        }
        with patch.dict(os.environ, {"API_PROVIDER_APIMART_KEY": "test-secret"}, clear=False):
            for module in (legacy, upstream_runtime):
                with self.subTest(module=module.__name__):
                    headers = module.api_headers(provider=provider)
                    self.assertEqual("Bearer test-secret", headers.get("Authorization"))
                    self.assertNotIn("x-goog-api-key", headers)


class UpstreamApimartRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_pricing_error_retries_once_with_official_fallback(self):
        provider = {
            "id": "apimart",
            "name": "APIMART",
            "protocol": "gemini",
            "base_url": "https://api.apimart.ai/v1",
            "image_generation_endpoint": "",
            "image_edit_endpoint": "",
        }
        FakeAsyncClient.instances = []
        with (
            patch.object(legacy, "get_api_provider", return_value=provider),
            patch.object(legacy.httpx, "AsyncClient", FakeAsyncClient),
            patch.dict(os.environ, {"API_PROVIDER_APIMART_KEY": "test-secret"}, clear=False),
        ):
            image, raw = await legacy.generate_ai_image(
                "test prompt",
                "4096x4096",
                "",
                "nano-banana-pro-ext",
                provider_id="apimart",
            )

        self.assertEqual({"type": "url", "value": "https://example.com/result.png"}, image)
        self.assertEqual("https://example.com/result.png", raw["data"][0]["url"])
        calls = FakeAsyncClient.instances[0].calls
        self.assertEqual(2, len(calls))
        first_body = calls[0][1]["json"]
        retry_body = calls[1][1]["json"]
        self.assertEqual("4K", first_body["resolution"])
        self.assertNotIn("official_fallback", first_body)
        self.assertIs(True, retry_body["official_fallback"])

    async def test_gemini_native_405_falls_back_to_openai_images(self):
        provider = {
            "id": "custom-gemini",
            "name": "Custom Gemini",
            "protocol": "gemini",
            "base_url": "https://api.example.com",
            "image_generation_endpoint": "",
        }
        for module in (legacy, upstream_runtime):
            with self.subTest(module=module.__name__):
                FakeGeminiFallbackClient.instances = []
                with (
                    patch.object(module.httpx, "AsyncClient", FakeGeminiFallbackClient),
                    patch.dict(os.environ, {"API_PROVIDER_CUSTOM_GEMINI_KEY": "test-secret"}, clear=False),
                ):
                    image, raw = await module.generate_gemini_provider_image(
                        "test prompt",
                        "1024x1024",
                        "gemini-3-pro-image",
                        provider=provider,
                    )

                self.assertEqual(
                    {"type": "b64", "value": "dGVzdA==", "mime_type": "image/png"},
                    image,
                )
                self.assertEqual("dGVzdA==", raw["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
                calls = FakeGeminiFallbackClient.instances[0].calls
                self.assertEqual(2, len(calls))
                self.assertEqual(
                    "https://api.example.com/v1beta/models/gemini-3-pro-image:generateContent",
                    calls[0][0],
                )
                self.assertEqual("test-secret", calls[0][1]["headers"].get("x-goog-api-key"))
                self.assertEqual(
                    "https://api.example.com/v1/models/gemini-3-pro-image:generateContent",
                    calls[1][0],
                )
                self.assertEqual("test-secret", calls[1][1]["headers"].get("x-goog-api-key"))
                self.assertEqual(
                    [{"role": "user", "parts": [{"text": "test prompt"}]}],
                    calls[1][1]["json"]["contents"],
                )

    async def test_explicit_gemini_endpoint_does_not_silently_fallback(self):
        provider = {
            "id": "custom-gemini",
            "name": "Custom Gemini",
            "protocol": "gemini",
            "base_url": "https://api.example.com",
            "image_generation_endpoint": "/custom/generate",
        }
        for module in (legacy, upstream_runtime):
            with self.subTest(module=module.__name__):
                FakeGeminiFallbackClient.instances = []
                with (
                    patch.object(module.httpx, "AsyncClient", FakeGeminiFallbackClient),
                    patch.dict(os.environ, {"API_PROVIDER_CUSTOM_GEMINI_KEY": "test-secret"}, clear=False),
                ):
                    with self.assertRaises(AssertionError):
                        await module.generate_gemini_provider_image(
                            "test prompt",
                            "1024x1024",
                            "gemini-3-pro-image",
                            provider=provider,
                        )
                calls = FakeGeminiFallbackClient.instances[0].calls
                self.assertEqual(1, len(calls))
                self.assertEqual("https://api.example.com/custom/generate", calls[0][0])


if __name__ == "__main__":
    unittest.main()
