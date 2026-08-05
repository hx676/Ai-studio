"""Progressive 1-4 image batches shared by AI Image and AI Chat."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from threading import RLock
from typing import Any, Dict

from fastapi import Header, HTTPException, Request

from app import legacy
from app.core.security import redact_sensitive_text
from app.models.canvas import OnlineImageRequest
from app.models.image_batch import ChatImageBatchRequest, ZImageBatchRequest


IMAGE_BATCH_MAX_COUNT = 4
IMAGE_BATCH_TERMINAL_LIMIT = 200
IMAGE_BATCH_RETENTION_SECONDS = 30 * 60
IMAGE_BATCH_TERMINAL_STATUSES = {"succeeded", "partial", "failed"}
IMAGE_BATCH_ITEM_TERMINAL_STATUSES = {"succeeded", "failed"}

_BATCH_LOCK = RLock()
_BATCHES: Dict[str, Dict[str, Any]] = {}
_ACTIVE_TASKS: Dict[str, asyncio.Task] = {}


def _now() -> float:
    return time.time()


def _error_detail(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("detail") or str(detail)
    return redact_sensitive_text(detail or str(exc) or exc.__class__.__name__)[:1000]


def _refresh_batch_status_locked(batch: Dict[str, Any]) -> None:
    items = batch.get("items") or []
    succeeded = sum(1 for item in items if item.get("status") == "succeeded")
    failed = sum(1 for item in items if item.get("status") == "failed")
    active = any(item.get("status") in {"pending", "running"} for item in items)
    batch["succeeded_count"] = succeeded
    batch["failed_count"] = failed
    batch["updated_at"] = _now()
    if active:
        batch["status"] = "running"
        batch.pop("ended_at", None)
    elif succeeded == len(items):
        batch["status"] = "succeeded"
        batch["ended_at"] = _now()
    elif succeeded:
        batch["status"] = "partial"
        batch["ended_at"] = _now()
    else:
        batch["status"] = "failed"
        batch["ended_at"] = _now()


def _prune_batches_locked() -> None:
    now = _now()
    expired = [
        batch_id
        for batch_id, batch in _BATCHES.items()
        if batch.get("status") in IMAGE_BATCH_TERMINAL_STATUSES
        and now - float(batch.get("ended_at") or batch.get("updated_at") or now) > IMAGE_BATCH_RETENTION_SECONDS
    ]
    for batch_id in expired:
        _BATCHES.pop(batch_id, None)

    terminal = sorted(
        (
            (batch_id, float(batch.get("ended_at") or batch.get("updated_at") or 0))
            for batch_id, batch in _BATCHES.items()
            if batch.get("status") in IMAGE_BATCH_TERMINAL_STATUSES
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    for batch_id, _ in terminal[IMAGE_BATCH_TERMINAL_LIMIT:]:
        _BATCHES.pop(batch_id, None)


def _public_batch_locked(batch: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "batch_id": batch["batch_id"],
        "surface": batch["surface"],
        "status": batch["status"],
        "requested_count": batch["requested_count"],
        "succeeded_count": batch.get("succeeded_count", 0),
        "failed_count": batch.get("failed_count", 0),
        "conversation_id": batch.get("conversation_id") or "",
        "created_at": batch["created_at"],
        "updated_at": batch["updated_at"],
        "ended_at": batch.get("ended_at"),
        "items": [
            {
                "id": item["id"],
                "index": item["index"],
                "status": item["status"],
                "image_url": item.get("image_url") or "",
                "error": item.get("error") or "",
                "message_id": item.get("message_id") or "",
                "record_id": item.get("record_id") or "",
                "result": item.get("result"),
                "started_at": item.get("started_at"),
                "ended_at": item.get("ended_at"),
            }
            for item in sorted(batch.get("items") or [], key=lambda current: current["index"])
        ],
    }


def _new_batch(surface: str, count: int, *, user_id: str = "", conversation_id: str = "") -> Dict[str, Any]:
    batch_id = f"img_batch_{uuid.uuid4().hex}"
    now = _now()
    batch = {
        "batch_id": batch_id,
        "surface": surface,
        "status": "running",
        "requested_count": count,
        "succeeded_count": 0,
        "failed_count": 0,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "created_at": now,
        "updated_at": now,
        "items": [
            {
                "id": f"{batch_id}_{index}",
                "index": index,
                "status": "pending",
                "image_url": "",
                "error": "",
            }
            for index in range(count)
        ],
    }
    with _BATCH_LOCK:
        _prune_batches_locked()
        _BATCHES[batch_id] = batch
    return batch


def _update_item(batch_id: str, index: int, **changes: Any) -> None:
    with _BATCH_LOCK:
        batch = _BATCHES.get(batch_id)
        if not batch:
            return
        item = next((entry for entry in batch["items"] if entry["index"] == index), None)
        if not item:
            return
        item.update(changes)
        _refresh_batch_status_locked(batch)
        _prune_batches_locked()


def _register_task(batch_id: str, index: int, coroutine) -> None:
    key = f"{batch_id}:{index}"
    task = asyncio.create_task(coroutine)
    _ACTIVE_TASKS[key] = task

    def cleanup(_: asyncio.Task) -> None:
        _ACTIVE_TASKS.pop(key, None)

    task.add_done_callback(cleanup)


async def _run_zimage_item(batch_id: str, index: int, payload: OnlineImageRequest, count: int) -> None:
    _update_item(batch_id, index, status="running", started_at=_now(), error="")
    record_id = f"zimage_{uuid.uuid4().hex}"
    try:
        result = await legacy.build_zimage_image_result(
            payload,
            {
                "record_id": record_id,
                "batch_id": batch_id,
                "batch_index": index,
                "batch_count": count,
            },
        )
        image_url = str((result.get("images") or [""])[0] or "")
        if not image_url:
            raise HTTPException(status_code=502, detail="上游没有返回图片")
        _update_item(
            batch_id,
            index,
            status="succeeded",
            image_url=image_url,
            record_id=record_id,
            result=result,
            ended_at=_now(),
            error="",
        )
    except Exception as exc:
        _update_item(
            batch_id,
            index,
            status="failed",
            error=_error_detail(exc),
            ended_at=_now(),
        )


def _chat_message_retry_snapshot(payload: ChatImageBatchRequest, provider_id: str, model: str, refs) -> Dict[str, Any]:
    return {
        "prompt": payload.message,
        "provider": provider_id,
        "image_model": model,
        "size": payload.size,
        "quality": payload.quality,
        "reference_images": legacy.history_reference_records(refs),
    }


def _patch_chat_message(user_id: str, conversation_id: str, message_id: str, changes: Dict[str, Any]):
    def mutate(conversation):
        message = next((item for item in conversation.get("messages", []) if item.get("id") == message_id), None)
        if not message:
            raise HTTPException(status_code=404, detail="图片消息不存在")
        message.update(changes)

    return legacy.mutate_conversation(user_id, conversation_id, mutate)


async def _run_chat_item(
    batch_id: str,
    index: int,
    *,
    user_id: str,
    conversation_id: str,
    message_id: str,
    prompt: str,
    provider_id: str,
    model: str,
    size: str,
    quality: str,
    refs,
) -> None:
    _update_item(batch_id, index, status="running", started_at=_now(), error="", message_id=message_id)
    try:
        _patch_chat_message(user_id, conversation_id, message_id, {"image_status": "running", "error": ""})
        image_data, raw = await legacy.generate_ai_image(prompt, size, quality, model, refs, provider_id)
        if legacy.is_gpt_image_2_model(model):
            image_data = await legacy.normalize_ai_image_to_size(image_data, legacy.normalize_gpt_image_2_size(size))
        image_url = await legacy.save_ai_image_to_output(image_data, prefix="chat_")
        if not image_url:
            raise HTTPException(status_code=502, detail="上游没有返回图片")
        changes = {
            "image_status": "succeeded",
            "image_url": image_url,
            "error": "",
            "raw_usage": raw.get("usage") if isinstance(raw, dict) else None,
            "completed_at": legacy.now_ms(),
        }
        _patch_chat_message(user_id, conversation_id, message_id, changes)
        _update_item(
            batch_id,
            index,
            status="succeeded",
            image_url=image_url,
            message_id=message_id,
            record_id=message_id,
            result={"image_url": image_url, "message_id": message_id},
            ended_at=_now(),
            error="",
        )
    except Exception as exc:
        detail = _error_detail(exc)
        try:
            _patch_chat_message(
                user_id,
                conversation_id,
                message_id,
                {"image_status": "failed", "image_url": "", "error": detail, "completed_at": legacy.now_ms()},
            )
        except Exception:
            pass
        _update_item(
            batch_id,
            index,
            status="failed",
            message_id=message_id,
            error=detail,
            ended_at=_now(),
        )


async def create_zimage_batch(payload: ZImageBatchRequest):
    count = int(payload.count)
    single_payload = OnlineImageRequest(**payload.model_dump(exclude={"count"}))
    batch = _new_batch("zimage", count)
    for item in batch["items"]:
        _register_task(batch["batch_id"], item["index"], _run_zimage_item(batch["batch_id"], item["index"], single_payload, count))
    with _BATCH_LOCK:
        return _public_batch_locked(batch)


async def create_chat_image_batch(
    payload: ChatImageBatchRequest,
    request: Request,
    x_user_id: str = Header(default=""),
):
    user_id = legacy.safe_user_id(x_user_id, request)
    conversation = (
        legacy.load_conversation(user_id, payload.conversation_id)
        if payload.conversation_id
        else legacy.new_conversation(user_id, legacy.display_title(payload.message))
    )
    conversation_id = conversation["id"]
    refs = legacy.request_reference_records(payload.reference_images)
    history_refs = legacy.history_reference_records(refs)
    image_provider_id = payload.provider if payload.provider not in {"modelscope"} else "comfly"
    provider = legacy.get_api_provider(image_provider_id)
    default_model = (provider.get("image_models") or [legacy.IMAGE_MODEL])[0]
    model = legacy.selected_model(payload.image_model, default_model)
    batch = _new_batch("chat", int(payload.count), user_id=user_id, conversation_id=conversation_id)
    retry_snapshot = _chat_message_retry_snapshot(payload, provider["id"], model, refs)
    user_message = {
        "id": uuid.uuid4().hex,
        "role": "user",
        "content": payload.message,
        "created_at": legacy.now_ms(),
        "attachments": history_refs,
        "mode": "image",
        "batch_id": batch["batch_id"],
    }
    assistant_messages = []
    for item in batch["items"]:
        message_id = uuid.uuid4().hex
        item["message_id"] = message_id
        assistant_messages.append(
            {
                "id": message_id,
                "role": "assistant",
                "type": "image",
                "content": payload.message,
                "image_url": "",
                "image_status": "pending",
                "error": "",
                "created_at": legacy.now_ms(),
                "model": model,
                "provider": provider["id"],
                "size": payload.size,
                "batch_id": batch["batch_id"],
                "batch_index": item["index"],
                "batch_count": int(payload.count),
                "retry_snapshot": retry_snapshot,
            }
        )

    def append_messages(current):
        if not current.get("messages"):
            current["title"] = legacy.display_title(payload.message)
        current.setdefault("messages", []).append(user_message)
        current["messages"].extend(assistant_messages)

    conversation = legacy.mutate_conversation(user_id, conversation_id, append_messages)
    for item in batch["items"]:
        _register_task(
            batch["batch_id"],
            item["index"],
            _run_chat_item(
                batch["batch_id"],
                item["index"],
                user_id=user_id,
                conversation_id=conversation_id,
                message_id=item["message_id"],
                prompt=payload.message,
                provider_id=provider["id"],
                model=model,
                size=payload.size,
                quality=payload.quality,
                refs=refs,
            ),
        )
    with _BATCH_LOCK:
        public = _public_batch_locked(batch)
    return {"batch": public, "conversation": conversation}


async def get_image_batch(batch_id: str, request: Request, x_user_id: str = Header(default="")):
    with _BATCH_LOCK:
        _prune_batches_locked()
        batch = _BATCHES.get(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="图片批次不存在或已过期")
        if batch.get("surface") == "chat":
            user_id = legacy.safe_user_id(x_user_id, request)
            if batch.get("user_id") != user_id:
                raise HTTPException(status_code=404, detail="图片批次不存在或已过期")
        return _public_batch_locked(batch)


async def retry_chat_image_message(
    conversation_id: str,
    message_id: str,
    request: Request,
    x_user_id: str = Header(default=""),
):
    user_id = legacy.safe_user_id(x_user_id, request)
    holder: Dict[str, Any] = {}
    batch = _new_batch("chat", 1, user_id=user_id, conversation_id=conversation_id)

    def prepare_retry(conversation):
        message = next((item for item in conversation.get("messages", []) if item.get("id") == message_id), None)
        if not message:
            raise HTTPException(status_code=404, detail="图片消息不存在")
        if message.get("image_status") not in {"failed", "interrupted"}:
            raise HTTPException(status_code=409, detail="只有失败或中断的图片可以重试")
        snapshot = message.get("retry_snapshot")
        if not isinstance(snapshot, dict) or not snapshot.get("prompt"):
            raise HTTPException(status_code=409, detail="该图片缺少可重试参数")
        holder["snapshot"] = snapshot
        message.update(
            {
                "image_status": "pending",
                "image_url": "",
                "error": "",
                "batch_id": batch["batch_id"],
                "batch_index": 0,
                "batch_count": 1,
            }
        )

    try:
        conversation = legacy.mutate_conversation(user_id, conversation_id, prepare_retry)
    except Exception:
        with _BATCH_LOCK:
            _BATCHES.pop(batch["batch_id"], None)
        raise
    snapshot = holder["snapshot"]
    refs = legacy.request_reference_records(snapshot.get("reference_images") or [])
    batch["items"][0]["message_id"] = message_id
    _register_task(
        batch["batch_id"],
        0,
        _run_chat_item(
            batch["batch_id"],
            0,
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            prompt=str(snapshot.get("prompt") or ""),
            provider_id=str(snapshot.get("provider") or "comfly"),
            model=str(snapshot.get("image_model") or legacy.IMAGE_MODEL),
            size=str(snapshot.get("size") or "1024x1024"),
            quality=str(snapshot.get("quality") or "auto"),
            refs=refs,
        ),
    )
    with _BATCH_LOCK:
        public = _public_batch_locked(batch)
    return {"batch": public, "conversation": conversation}


def recover_interrupted_chat_image_messages() -> int:
    recovered = 0
    if not os.path.isdir(legacy.CONVERSATION_DIR):
        return 0
    with legacy.CONVERSATION_LOCK:
        for user_name in os.listdir(legacy.CONVERSATION_DIR):
            directory = os.path.join(legacy.CONVERSATION_DIR, user_name)
            if not os.path.isdir(directory):
                continue
            for filename in os.listdir(directory):
                if not filename.endswith(".json"):
                    continue
                path = os.path.join(directory, filename)
                conversation = legacy.read_json_resilient(path, {})
                if not isinstance(conversation, dict):
                    continue
                changed = False
                for message in conversation.get("messages", []):
                    if message.get("type") == "image" and message.get("image_status") in {"pending", "running"}:
                        message["image_status"] = "interrupted"
                        message["error"] = "服务重启导致生成中断，可点击重试。"
                        changed = True
                        recovered += 1
                if changed:
                    conversation["updated_at"] = legacy.now_ms()
                    legacy.atomic_write_json(path, conversation)
    return recovered
