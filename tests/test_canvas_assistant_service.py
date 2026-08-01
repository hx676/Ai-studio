import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.models.canvas_assistant import (
    CanvasAssistantConversationCreate,
    CanvasAssistantConversationUpdate,
    CanvasAssistantMessageRequest,
    CanvasAssistantSourceRef,
)
from app.services import canvas_assistant_service as service


class _ConnectedRequest:
    async def is_disconnected(self):
        return False


class _DisconnectedRequest:
    async def is_disconnected(self):
        return True


class _FakeResponse:
    status_code = 200

    async def aread(self):
        return b""

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"第一段"}}]}'
        yield 'data: {"choices":[{"delta":{"content":"\\n第二段"}}],"usage":{"total_tokens":12}}'
        yield "data: [DONE]"


class _FakeStreamContext:
    def __init__(self, owner, method, url, **kwargs):
        owner.calls.append({"method": method, "url": url, **kwargs})

    async def __aenter__(self):
        return _FakeResponse()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FakeAsyncClient:
    calls = []

    def __init__(self, *args, **kwargs):
        self.options = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def stream(self, method, url, **kwargs):
        return _FakeStreamContext(type(self), method, url, **kwargs)


class _FakeErrorResponse(_FakeResponse):
    status_code = 502

    async def aread(self):
        return b"authorization: Bearer upstream-secret-token"


class _FakeErrorStreamContext(_FakeStreamContext):
    async def __aenter__(self):
        return _FakeErrorResponse()


class _FakeErrorAsyncClient(_FakeAsyncClient):
    def stream(self, method, url, **kwargs):
        return _FakeErrorStreamContext(type(self), method, url, **kwargs)


def _events(chunks):
    result = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                result.append(json.loads(line[6:]))
    return result


class CanvasAssistantServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="syncanvas-assistant-")
        self.root = Path(self.temp_dir.name)
        self.root_patch = patch.object(service, "ROOT", self.root / "canvas-assistant")
        self.canvas_patch = patch.object(service.legacy, "load_canvas", side_effect=lambda canvas_id: {"id": canvas_id})
        self.root_patch.start()
        self.canvas_patch.start()
        service._STREAM_LOCKS.clear()

    def tearDown(self):
        service._STREAM_LOCKS.clear()
        self.canvas_patch.stop()
        self.root_patch.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def create_payload(kind="general", source_id="", provider_id="provider-a", model="model-a"):
        return CanvasAssistantConversationCreate(
            source=CanvasAssistantSourceRef(kind=kind, id=source_id),
            provider_id=provider_id,
            model=model,
        )

    def test_conversations_are_isolated_per_canvas_and_active_state_persists(self):
        first = service.create_conversation("canvas-a", self.create_payload())["conversation"]
        second = service.create_conversation("canvas-a", self.create_payload())["conversation"]
        other = service.create_conversation("canvas-b", self.create_payload())["conversation"]

        listed = service.list_conversations("canvas-a")
        self.assertEqual(second["id"], listed["active_conversation_id"])
        self.assertEqual({first["id"], second["id"]}, {item["id"] for item in listed["conversations"]})
        self.assertEqual([other["id"]], [item["id"] for item in service.list_conversations("canvas-b")["conversations"]])

        service.activate_conversation("canvas-a", first["id"])
        self.assertEqual(first["id"], service.list_conversations("canvas-a")["active_conversation_id"])
        with self.assertRaises(HTTPException) as wrong_canvas:
            service.get_conversation("canvas-b", first["id"])
        self.assertEqual(404, wrong_canvas.exception.status_code)

        updated = service.update_conversation(
            "canvas-a",
            first["id"],
            CanvasAssistantConversationUpdate(title="产品视觉总监", provider_id="provider-b", model="model-b"),
        )["conversation"]
        self.assertEqual("产品视觉总监", updated["title"])
        self.assertEqual("provider-b", updated["provider_id"])
        self.assertEqual("model-b", updated["model"])

        deleted = service.delete_conversation("canvas-a", first["id"])
        self.assertTrue(deleted["ok"])
        self.assertEqual(second["id"], deleted["active_conversation_id"])

    def test_template_and_agent_prompts_are_snapshotted_and_never_returned(self):
        template = {
            "item": {"id": "template-1", "name": "电商视觉全案", "updated_at": 10},
            "template": {"stylePromptZh": "严格执行 Step 0，然后等待用户。"},
        }
        agent = {
            "id": "agent-1",
            "name": "视觉总监",
            "description": "多轮策划",
            "systemPrompt": "先诊断，再等待确认。",
            "temperature": 0.2,
        }
        with patch.object(service.template_asset_service, "get_template_asset", return_value=template), patch.object(
            service.agent_service, "get_agent", return_value=agent
        ):
            template_conversation = service.create_conversation(
                "canvas-a", self.create_payload("template", "template-1")
            )["conversation"]
            agent_conversation = service.create_conversation(
                "canvas-a", self.create_payload("agent", "agent-1")
            )["conversation"]

        for public in (template_conversation, agent_conversation):
            self.assertNotIn("system_prompt", public["source"])
            self.assertIn("fingerprint", public["source"])

        template_path = service.ROOT / "canvas-a" / f"{template_conversation['id']}.json"
        agent_path = service.ROOT / "canvas-a" / f"{agent_conversation['id']}.json"
        stored_template = json.loads(template_path.read_text(encoding="utf-8"))
        stored_agent = json.loads(agent_path.read_text(encoding="utf-8"))
        self.assertEqual("严格执行 Step 0，然后等待用户。", stored_template["source"]["system_prompt"])
        self.assertEqual("先诊断，再等待确认。", stored_agent["source"]["system_prompt"])
        self.assertEqual(0.2, stored_agent["source"]["temperature"])

        with patch.object(service.template_asset_service, "get_template_asset", side_effect=HTTPException(404, "deleted")):
            restored = service.get_conversation("canvas-a", template_conversation["id"])["conversation"]
        self.assertEqual("电商视觉全案", restored["source"]["name"])

    def test_sources_include_general_templates_and_agents_without_full_prompts(self):
        library = {
            "libraries": [{
                "categories": [{
                    "id": "templates",
                    "name": "模板",
                    "type": "template",
                    "items": [{"id": "template-1", "name": "视觉模板", "kind": "template"}],
                }]
            }]
        }
        agents = [{"id": "agent-1", "name": "视觉总监", "description": "desc", "modelKind": "vision"}]
        with patch.object(service.legacy, "load_asset_library", return_value=library), patch.object(
            service.agent_service, "list_agents", return_value=agents
        ):
            result = service.list_sources()["sources"]
        self.assertEqual({"general", "template", "agent"}, {item["kind"] for item in result})
        self.assertFalse(any("systemPrompt" in item or "system_prompt" in item for item in result))

    def test_template_without_prompt_is_rejected(self):
        result = {"item": {"id": "template-1", "name": "空模板"}, "template": {"features": ["grid"]}}
        with patch.object(service.template_asset_service, "get_template_asset", return_value=result):
            with self.assertRaises(HTTPException) as missing:
                service.create_conversation("canvas-a", self.create_payload("template", "template-1"))
        self.assertEqual(422, missing.exception.status_code)

    def test_reference_history_rejects_base64_and_external_urls(self):
        for url in ("data:image/png;base64,AAAA", "https://example.com/product.png"):
            payload = CanvasAssistantMessageRequest(message="analyze", reference_images=[{"url": url}])
            with self.assertRaises(HTTPException) as invalid:
                service._history_references(payload)
            self.assertEqual(400, invalid.exception.status_code)


class CanvasAssistantStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="syncanvas-assistant-stream-")
        self.root_patch = patch.object(service, "ROOT", Path(self.temp_dir.name) / "canvas-assistant")
        self.canvas_patch = patch.object(service.legacy, "load_canvas", side_effect=lambda canvas_id: {"id": canvas_id})
        self.root_patch.start()
        self.canvas_patch.start()
        service._STREAM_LOCKS.clear()
        _FakeAsyncClient.calls.clear()

    async def asyncTearDown(self):
        service._STREAM_LOCKS.clear()
        self.canvas_patch.stop()
        self.root_patch.stop()
        self.temp_dir.cleanup()

    async def test_bootstrap_and_followup_stream_persist_visible_messages_and_local_urls(self):
        created = service.create_conversation(
            "canvas-a",
            CanvasAssistantConversationCreate(
                source=CanvasAssistantSourceRef(kind="general"),
                provider_id="provider-a",
                model="model-a",
            ),
        )["conversation"]
        resolve = ("https://chat.example/v1", {"Authorization": "Bearer secret"}, "resolved-model")
        with patch.object(service.legacy, "resolve_chat_provider", return_value=resolve), patch.object(
            service.httpx, "AsyncClient", _FakeAsyncClient
        ), patch.object(service.legacy, "reference_to_data_url", return_value="data:image/png;base64,AAAA"):
            bootstrap = await service.stream_message(
                "canvas-a", created["id"], CanvasAssistantMessageRequest(bootstrap=True), _ConnectedRequest()
            )
            bootstrap_events = _events([chunk async for chunk in bootstrap])
            followup = await service.stream_message(
                "canvas-a",
                created["id"],
                CanvasAssistantMessageRequest(
                    message="这是产品图，请开始诊断",
                    reference_images=[{"url": "/assets/input/product.png", "name": "product.png"}],
                ),
                _ConnectedRequest(),
            )
            followup_events = _events([chunk async for chunk in followup])

        self.assertEqual(["meta", "delta", "delta", "done"], [item["type"] for item in bootstrap_events])
        self.assertEqual(["meta", "delta", "delta", "done"], [item["type"] for item in followup_events])
        public = service.get_conversation("canvas-a", created["id"])["conversation"]
        self.assertEqual(["assistant", "user", "assistant"], [item["role"] for item in public["messages"]])
        self.assertEqual("/assets/input/product.png", public["messages"][1]["attachments"][0]["url"])
        self.assertNotIn("base64", json.dumps(public, ensure_ascii=False))

        stored = json.loads((service.ROOT / "canvas-a" / f"{created['id']}.json").read_text(encoding="utf-8"))
        self.assertTrue(stored["messages"][0]["hidden"])
        self.assertEqual(service.BOOTSTRAP_MESSAGE, stored["messages"][0]["content"])
        sent = _FakeAsyncClient.calls[-1]["json"]["messages"]
        image_message = next(item for item in sent if isinstance(item.get("content"), list))
        self.assertEqual("data:image/png;base64,AAAA", image_message["content"][1]["image_url"]["url"])
        self.assertNotIn(f"canvas-a:{created['id']}", service._STREAM_LOCKS)

    async def test_same_conversation_rejects_a_second_concurrent_stream(self):
        created = service.create_conversation(
            "canvas-a", CanvasAssistantConversationCreate(source=CanvasAssistantSourceRef(kind="general"))
        )["conversation"]
        lock = await service._stream_lock("canvas-a", created["id"])
        await lock.acquire()
        try:
            with self.assertRaises(HTTPException) as busy:
                await service.stream_message(
                    "canvas-a", created["id"], CanvasAssistantMessageRequest(message="hello"), _ConnectedRequest()
                )
            self.assertEqual(409, busy.exception.status_code)
        finally:
            lock.release()

    async def test_update_and_delete_are_rejected_while_the_conversation_is_streaming(self):
        created = service.create_conversation(
            "canvas-a", CanvasAssistantConversationCreate(source=CanvasAssistantSourceRef(kind="general"))
        )["conversation"]
        lock = await service._stream_lock("canvas-a", created["id"])
        await lock.acquire()
        try:
            with self.assertRaises(HTTPException) as update_busy:
                await service.update_conversation_exclusive(
                    "canvas-a", created["id"], CanvasAssistantConversationUpdate(title="should not win")
                )
            with self.assertRaises(HTTPException) as delete_busy:
                await service.delete_conversation_exclusive("canvas-a", created["id"])
            self.assertEqual(409, update_busy.exception.status_code)
            self.assertEqual(409, delete_busy.exception.status_code)
        finally:
            lock.release()

    async def test_exclusive_update_and_delete_release_and_prune_lifecycle_locks(self):
        created = service.create_conversation(
            "canvas-a", CanvasAssistantConversationCreate(source=CanvasAssistantSourceRef(kind="general"))
        )["conversation"]
        updated = await service.update_conversation_exclusive(
            "canvas-a", created["id"], CanvasAssistantConversationUpdate(title="updated")
        )
        self.assertEqual("updated", updated["conversation"]["title"])
        self.assertNotIn(f"canvas-a:{created['id']}", service._STREAM_LOCKS)
        deleted = await service.delete_conversation_exclusive("canvas-a", created["id"])
        self.assertTrue(deleted["ok"])
        self.assertNotIn(f"canvas-a:{created['id']}", service._STREAM_LOCKS)

    async def test_disconnect_closes_stream_without_saving_partial_assistant_reply(self):
        created = service.create_conversation(
            "canvas-a", CanvasAssistantConversationCreate(source=CanvasAssistantSourceRef(kind="general"))
        )["conversation"]
        resolve = ("https://chat.example/v1", {"Authorization": "Bearer secret"}, "resolved-model")
        with patch.object(service.legacy, "resolve_chat_provider", return_value=resolve), patch.object(
            service.httpx, "AsyncClient", _FakeAsyncClient
        ):
            iterator = await service.stream_message(
                "canvas-a", created["id"], CanvasAssistantMessageRequest(message="stop this"), _DisconnectedRequest()
            )
            events = _events([chunk async for chunk in iterator])
        self.assertEqual(["meta"], [item["type"] for item in events])
        public = service.get_conversation("canvas-a", created["id"])["conversation"]
        self.assertEqual(["user"], [item["role"] for item in public["messages"]])
        self.assertNotIn(f"canvas-a:{created['id']}", service._STREAM_LOCKS)

    async def test_upstream_error_is_redacted_and_does_not_save_a_fake_reply(self):
        created = service.create_conversation(
            "canvas-a", CanvasAssistantConversationCreate(source=CanvasAssistantSourceRef(kind="general"))
        )["conversation"]
        resolve = ("https://chat.example/v1", {"Authorization": "Bearer request-secret"}, "resolved-model")
        with patch.object(service.legacy, "resolve_chat_provider", return_value=resolve), patch.object(
            service.httpx, "AsyncClient", _FakeErrorAsyncClient
        ):
            iterator = await service.stream_message(
                "canvas-a", created["id"], CanvasAssistantMessageRequest(message="trigger error"), _ConnectedRequest()
            )
            events = _events([chunk async for chunk in iterator])
        self.assertEqual(["meta", "error"], [item["type"] for item in events])
        self.assertNotIn("upstream-secret-token", events[-1]["detail"])
        self.assertIn("REDACTED", events[-1]["detail"])
        public = service.get_conversation("canvas-a", created["id"])["conversation"]
        self.assertEqual(["user"], [item["role"] for item in public["messages"]])

    async def test_message_limit_returns_sse_error_before_contacting_provider(self):
        created = service.create_conversation(
            "canvas-a", CanvasAssistantConversationCreate(source=CanvasAssistantSourceRef(kind="general"))
        )["conversation"]
        path = service.ROOT / "canvas-a" / f"{created['id']}.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["messages"] = [
            {"id": f"m-{index}", "role": "user" if index % 2 == 0 else "assistant", "content": "x", "created_at": index}
            for index in range(service.MAX_MESSAGES - 1)
        ]
        service._save_conversation("canvas-a", stored)
        with patch.object(service.legacy, "resolve_chat_provider") as provider:
            iterator = await service.stream_message(
                "canvas-a", created["id"], CanvasAssistantMessageRequest(message="one more"), _ConnectedRequest()
            )
            events = _events([chunk async for chunk in iterator])
        self.assertEqual("error", events[0]["type"])
        self.assertIn("新建会话", events[0]["detail"])
        provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
