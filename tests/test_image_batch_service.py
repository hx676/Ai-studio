import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app import legacy
from app.models.image_batch import ChatImageBatchRequest, ZImageBatchRequest
from app.services import image_batch_service as service


class _Request:
    client = None


async def _wait_for_batch(batch_id: str, timeout: float = 2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        with service._BATCH_LOCK:
            batch = service._BATCHES.get(batch_id)
            if batch and batch.get("status") in service.IMAGE_BATCH_TERMINAL_STATUSES:
                return service._public_batch_locked(batch)
        await asyncio.sleep(0.005)
    raise AssertionError(f"batch did not finish: {batch_id}")


class ImageBatchServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_root = Path(tempfile.mkdtemp(prefix="syncanvas-image-batch-"))
        self.original_conversation_dir = legacy.CONVERSATION_DIR
        legacy.CONVERSATION_DIR = str(self.temp_root / "conversations")
        Path(legacy.CONVERSATION_DIR).mkdir(parents=True, exist_ok=True)
        with service._BATCH_LOCK:
            service._BATCHES.clear()
        service._ACTIVE_TASKS.clear()

    async def asyncTearDown(self):
        tasks = list(service._ACTIVE_TASKS.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        service._ACTIVE_TASKS.clear()
        with service._BATCH_LOCK:
            service._BATCHES.clear()
        legacy.CONVERSATION_DIR = self.original_conversation_dir

    async def test_zimage_batch_starts_four_items_concurrently(self):
        release = asyncio.Event()
        all_started = asyncio.Event()
        started = []

        async def controlled_generate(payload, batch_meta=None):
            started.append(batch_meta["batch_index"])
            if len(started) == 4:
                all_started.set()
            await release.wait()
            index = batch_meta["batch_index"]
            return {
                "record_id": batch_meta["record_id"],
                "images": [f"/output/result-{index}.png"],
                "timestamp": index + 1,
                "type": "zimage",
            }

        with patch.object(legacy, "build_zimage_image_result", new=controlled_generate):
            created = await service.create_zimage_batch(ZImageBatchRequest(prompt="same prompt", count=4))
            await asyncio.wait_for(all_started.wait(), timeout=1)
            self.assertCountEqual([0, 1, 2, 3], started)
            release.set()
            batch = await _wait_for_batch(created["batch_id"])

        self.assertEqual("succeeded", batch["status"])
        self.assertEqual(4, batch["succeeded_count"])
        self.assertEqual([0, 1, 2, 3], [item["index"] for item in batch["items"]])

    async def test_zimage_batch_keeps_successes_when_one_item_fails(self):
        async def partial_generate(payload, batch_meta=None):
            index = batch_meta["batch_index"]
            if index == 2:
                raise RuntimeError("authorization: Bearer upstream-secret-token")
            return {
                "record_id": batch_meta["record_id"],
                "images": [f"/output/result-{index}.png"],
                "timestamp": index + 1,
                "type": "zimage",
            }

        with patch.object(legacy, "build_zimage_image_result", new=partial_generate):
            created = await service.create_zimage_batch(ZImageBatchRequest(prompt="same prompt", count=4))
            batch = await _wait_for_batch(created["batch_id"])

        self.assertEqual("partial", batch["status"])
        self.assertEqual(3, batch["succeeded_count"])
        self.assertEqual(1, batch["failed_count"])
        failed = next(item for item in batch["items"] if item["status"] == "failed")
        self.assertNotIn("upstream-secret-token", failed["error"])

    async def test_batch_count_is_restricted_to_one_through_four(self):
        for count in (0, 5):
            with self.subTest(count=count), self.assertRaises(ValidationError):
                ZImageBatchRequest(prompt="test", count=count)

    async def test_chat_batch_persists_ordered_independent_image_messages(self):
        calls = 0
        release = asyncio.Event()
        all_started = asyncio.Event()

        async def generate(prompt, size, quality, model, refs, provider_id):
            nonlocal calls
            calls += 1
            index = calls
            if calls == 3:
                all_started.set()
            await release.wait()
            if index == 2:
                raise RuntimeError("one image failed")
            return {"type": "url", "value": f"https://example.com/{index}.png"}, {"usage": {"total_tokens": index}}

        async def save_image(image_data, prefix="chat_"):
            return "/output/" + image_data["value"].rsplit("/", 1)[-1]

        provider = {"id": "test-provider", "name": "Test", "image_models": ["image-model"]}
        payload = ChatImageBatchRequest(
            message="same chat prompt",
            provider="test-provider",
            image_model="image-model",
            count=3,
        )
        with (
            patch.object(legacy, "get_api_provider", return_value=provider),
            patch.object(legacy, "generate_ai_image", new=generate),
            patch.object(legacy, "save_ai_image_to_output", new=save_image),
        ):
            created = await service.create_chat_image_batch(payload, _Request(), "user-1")
            await asyncio.wait_for(all_started.wait(), timeout=1)
            release.set()
            batch = await _wait_for_batch(created["batch"]["batch_id"])

        conversation = legacy.load_conversation("user-1", created["conversation"]["id"])
        self.assertEqual("partial", batch["status"])
        self.assertEqual(["user", "assistant", "assistant", "assistant"], [item["role"] for item in conversation["messages"]])
        assistants = conversation["messages"][1:]
        self.assertEqual([0, 1, 2], [item["batch_index"] for item in assistants])
        self.assertEqual(["succeeded", "failed", "succeeded"], [item["image_status"] for item in assistants])
        self.assertTrue(all("data_url" not in str(item.get("retry_snapshot")) for item in assistants))

    async def test_failed_chat_message_retries_in_place(self):
        async def fail_generate(*args, **kwargs):
            raise RuntimeError("temporary failure")

        provider = {"id": "test-provider", "name": "Test", "image_models": ["image-model"]}
        payload = ChatImageBatchRequest(
            message="retry prompt",
            provider="test-provider",
            image_model="image-model",
            count=1,
        )
        with (
            patch.object(legacy, "get_api_provider", return_value=provider),
            patch.object(legacy, "generate_ai_image", new=fail_generate),
        ):
            created = await service.create_chat_image_batch(payload, _Request(), "user-2")
            await _wait_for_batch(created["batch"]["batch_id"])

        conversation_id = created["conversation"]["id"]
        failed_conversation = legacy.load_conversation("user-2", conversation_id)
        failed_message = failed_conversation["messages"][-1]
        original_message_count = len(failed_conversation["messages"])

        async def success_generate(*args, **kwargs):
            return {"type": "url", "value": "https://example.com/retry.png"}, {}

        async def save_image(*args, **kwargs):
            return "/output/retry.png"

        with (
            patch.object(legacy, "generate_ai_image", new=success_generate),
            patch.object(legacy, "save_ai_image_to_output", new=save_image),
        ):
            retried = await service.retry_chat_image_message(
                conversation_id,
                failed_message["id"],
                _Request(),
                "user-2",
            )
            await _wait_for_batch(retried["batch"]["batch_id"])

        final_conversation = legacy.load_conversation("user-2", conversation_id)
        self.assertEqual(original_message_count, len(final_conversation["messages"]))
        final_message = next(item for item in final_conversation["messages"] if item["id"] == failed_message["id"])
        self.assertEqual("succeeded", final_message["image_status"])
        self.assertEqual("/output/retry.png", final_message["image_url"])

    async def test_recovery_marks_running_chat_images_interrupted(self):
        conversation = legacy.new_conversation("user-3", "Recovery")

        def add_pending(current):
            current["messages"].append(
                {
                    "id": "pending-image",
                    "role": "assistant",
                    "type": "image",
                    "image_status": "running",
                    "retry_snapshot": {"prompt": "retry"},
                }
            )

        legacy.mutate_conversation("user-3", conversation["id"], add_pending)
        self.assertEqual(1, service.recover_interrupted_chat_image_messages())
        recovered = legacy.load_conversation("user-3", conversation["id"])
        self.assertEqual("interrupted", recovered["messages"][-1]["image_status"])


if __name__ == "__main__":
    unittest.main()
