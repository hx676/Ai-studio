from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterable, List

import httpx
from fastapi import HTTPException, Request

from app import legacy
from app.core.json_store import atomic_write_json, path_lock, read_json_resilient
from app.core.paths import DATA_DIR
from app.core.security import redact_sensitive_text
from app.models.canvas_assistant import (
    CanvasAssistantConversationCreate,
    CanvasAssistantConversationUpdate,
    CanvasAssistantMessageRequest,
)
from app.services import agent_service, template_asset_service


ROOT = Path(DATA_DIR) / "canvas-assistant"
ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,120}$")
MAX_MESSAGES = 200
CONTEXT_MESSAGES = 30
MAX_REFERENCES = 8
LOCAL_REFERENCE_PREFIXES = ("/assets/", "/output/", "/api/storage-files/")
BOOTSTRAP_MESSAGE = (
    "Start the conversation now. Follow the initial step in the system instructions exactly, "
    "do not skip ahead, and wait for the user's next input when required."
)

_STREAM_LOCKS: Dict[str, asyncio.Lock] = {}
_STREAM_LOCKS_GUARD = asyncio.Lock()


def now_ms() -> int:
    return int(time.time() * 1000)


def _model_dump(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value or {})


def _safe_id(value: str, label: str) -> str:
    cleaned = str(value or "").strip()
    if not ID_RE.fullmatch(cleaned):
        raise HTTPException(status_code=400, detail=f"无效的{label} ID")
    return cleaned


def _canvas_root(canvas_id: str) -> Path:
    safe = _safe_id(canvas_id, "画布")
    legacy.load_canvas(safe)
    return ROOT / safe


def _index_path(canvas_id: str) -> Path:
    return _canvas_root(canvas_id) / "index.json"


def _conversation_path(canvas_id: str, conversation_id: str) -> Path:
    return _canvas_root(canvas_id) / f"{_safe_id(conversation_id, '会话')}.json"


def _default_index() -> Dict[str, Any]:
    return {"active_conversation_id": "", "conversation_ids": []}


def _load_index(canvas_id: str) -> Dict[str, Any]:
    path = _index_path(canvas_id)
    value = read_json_resilient(path, _default_index())
    if not isinstance(value, dict):
        value = _default_index()
    value["conversation_ids"] = [
        item for item in value.get("conversation_ids", []) if isinstance(item, str) and ID_RE.fullmatch(item)
    ]
    active = str(value.get("active_conversation_id") or "")
    value["active_conversation_id"] = active if active in value["conversation_ids"] else ""
    return value


def _save_index(canvas_id: str, value: Dict[str, Any]) -> None:
    atomic_write_json(_index_path(canvas_id), value)


def _load_conversation(canvas_id: str, conversation_id: str) -> Dict[str, Any]:
    path = _conversation_path(canvas_id, conversation_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="画布精灵会话不存在")
    value = read_json_resilient(path, {})
    if not isinstance(value, dict) or not value:
        raise HTTPException(status_code=500, detail="画布精灵会话数据已损坏，已保留备份")
    if str(value.get("canvas_id") or "") != canvas_id:
        raise HTTPException(status_code=404, detail="画布精灵会话不属于当前画布")
    return value


def _save_conversation(canvas_id: str, value: Dict[str, Any]) -> None:
    value["updated_at"] = now_ms()
    atomic_write_json(_conversation_path(canvas_id, value["id"]), value)


def _template_prompt(template: Dict[str, Any]) -> str:
    for key in (
        "stylePromptZh",
        "style_prompt_zh",
        "style_prompt_cn",
        "stylePromptEn",
        "style_prompt_en",
        "stylePrompt",
        "style_prompt",
        "systemPrompt",
        "system_prompt",
        "prompt",
        "instructions",
    ):
        value = template.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise HTTPException(status_code=422, detail="所选模板没有可用于对话的提示词")


def _fingerprint(kind: str, source_id: str, prompt: str, version: Any = "") -> str:
    raw = f"{kind}\n{source_id}\n{version}\n{prompt}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _source_snapshot(kind: str, source_id: str) -> Dict[str, Any]:
    if kind == "general":
        prompt = str(legacy.SYSTEM_PROMPT or "You are a helpful assistant.").strip()
        return {
            "kind": "general",
            "id": "",
            "name": "通用助手",
            "description": "使用当前 API 对话模型",
            "system_prompt": prompt,
            "temperature": 0.5,
            "fingerprint": _fingerprint("general", "", prompt),
        }
    if kind == "agent":
        agent = agent_service.get_agent(_safe_id(source_id, "智能体"))
        prompt = str(agent.get("systemPrompt") or "").strip()
        return {
            "kind": "agent",
            "id": agent["id"],
            "name": agent.get("name") or agent["id"],
            "description": agent.get("description") or "",
            "system_prompt": prompt,
            "temperature": float(agent.get("temperature", 0.5)),
            "fingerprint": _fingerprint("agent", agent["id"], prompt, agent.get("temperature")),
        }
    if kind == "template":
        result = template_asset_service.get_template_asset(_safe_id(source_id, "模板"))
        item = result.get("item") or {}
        prompt = _template_prompt(result.get("template") or {})
        return {
            "kind": "template",
            "id": str(item.get("id") or source_id),
            "name": str(item.get("name") or "模板"),
            "description": "模板提示词",
            "system_prompt": prompt,
            "temperature": 0.5,
            "fingerprint": _fingerprint("template", source_id, prompt, item.get("updated_at")),
        }
    raise HTTPException(status_code=422, detail="不支持的画布精灵来源")


def _public_source(source: Dict[str, Any]) -> Dict[str, Any]:
    return {key: source.get(key) for key in ("kind", "id", "name", "description", "fingerprint")}


def _public_message(message: Dict[str, Any]) -> Dict[str, Any] | None:
    if message.get("hidden"):
        return None
    return {
        key: message.get(key)
        for key in ("id", "role", "content", "attachments", "created_at", "model", "status", "error")
        if message.get(key) not in (None, "", [])
    }


def public_conversation(value: Dict[str, Any]) -> Dict[str, Any]:
    messages = [_public_message(item) for item in value.get("messages", [])]
    return {
        "id": value.get("id"),
        "canvas_id": value.get("canvas_id"),
        "title": value.get("title") or "新对话",
        "source": _public_source(value.get("source") or {}),
        "provider_id": value.get("provider_id") or "",
        "model": value.get("model") or "",
        "started": bool(value.get("started")),
        "created_at": value.get("created_at") or 0,
        "updated_at": value.get("updated_at") or 0,
        "messages": [item for item in messages if item],
    }


def _summary(value: Dict[str, Any]) -> Dict[str, Any]:
    public = public_conversation(value)
    public.pop("messages", None)
    visible = [item for item in value.get("messages", []) if not item.get("hidden")]
    public["last_message"] = str((visible[-1] if visible else {}).get("content") or "")[:240]
    public["message_count"] = len(visible)
    return public


def list_sources() -> Dict[str, Any]:
    templates: List[Dict[str, Any]] = []
    library = legacy.load_asset_library()
    libraries: Iterable[Dict[str, Any]] = library.get("libraries") or [library]
    for lib in libraries:
        for category in lib.get("categories", []):
            if category.get("type") != "template":
                continue
            for item in category.get("items", []):
                if item.get("kind") == "template" and item.get("id"):
                    templates.append({
                        "kind": "template",
                        "id": item["id"],
                        "name": item.get("name") or "模板",
                        "description": category.get("name") or "模板",
                        "thumbnail_url": item.get("thumbnail_url") or "",
                        "updated_at": item.get("updated_at") or 0,
                    })
    agents = [
        {
            "kind": "agent",
            "id": item["id"],
            "name": item.get("name") or item["id"],
            "description": item.get("description") or "",
            "model_kind": item.get("modelKind") or "text",
        }
        for item in agent_service.list_agents()
    ]
    return {
        "sources": [
            {"kind": "general", "id": "", "name": "通用助手", "description": "使用当前 API 对话模型"},
            *templates,
            *agents,
        ]
    }


def list_conversations(canvas_id: str) -> Dict[str, Any]:
    index = _load_index(canvas_id)
    records = []
    valid_ids = []
    for conversation_id in index["conversation_ids"]:
        try:
            value = _load_conversation(canvas_id, conversation_id)
        except HTTPException:
            continue
        records.append(_summary(value))
        valid_ids.append(conversation_id)
    records.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)
    active = index.get("active_conversation_id") if index.get("active_conversation_id") in valid_ids else (records[0]["id"] if records else "")
    if valid_ids != index["conversation_ids"] or active != index.get("active_conversation_id"):
        index.update({"conversation_ids": valid_ids, "active_conversation_id": active})
        _save_index(canvas_id, index)
    return {"active_conversation_id": active, "conversations": records}


def create_conversation(canvas_id: str, payload: CanvasAssistantConversationCreate) -> Dict[str, Any]:
    canvas_id = _safe_id(canvas_id, "画布")
    root = _canvas_root(canvas_id)
    root.mkdir(parents=True, exist_ok=True)
    values = _model_dump(payload)
    source_ref = values.get("source") or {"kind": "general", "id": ""}
    source = _source_snapshot(str(source_ref.get("kind") or "general"), str(source_ref.get("id") or ""))
    created = now_ms()
    conversation = {
        "id": uuid.uuid4().hex,
        "canvas_id": canvas_id,
        "title": source.get("name") or "新对话",
        "source": source,
        "provider_id": str(values.get("provider_id") or ""),
        "model": str(values.get("model") or ""),
        "started": False,
        "created_at": created,
        "updated_at": created,
        "messages": [],
    }
    index_path = _index_path(canvas_id)
    with path_lock(index_path):
        _save_conversation(canvas_id, conversation)
        index = _load_index(canvas_id)
        index["conversation_ids"] = [conversation["id"], *[item for item in index["conversation_ids"] if item != conversation["id"]]]
        index["active_conversation_id"] = conversation["id"]
        _save_index(canvas_id, index)
    return {"conversation": public_conversation(conversation), "active_conversation_id": conversation["id"]}


def get_conversation(canvas_id: str, conversation_id: str) -> Dict[str, Any]:
    return {"conversation": public_conversation(_load_conversation(canvas_id, conversation_id))}


def update_conversation(canvas_id: str, conversation_id: str, payload: CanvasAssistantConversationUpdate) -> Dict[str, Any]:
    path = _conversation_path(canvas_id, conversation_id)
    with path_lock(path):
        value = _load_conversation(canvas_id, conversation_id)
        changes = _model_dump(payload)
        for key in ("title", "provider_id", "model"):
            if changes.get(key) is not None:
                value[key] = str(changes[key]).strip()
        _save_conversation(canvas_id, value)
    return {"conversation": public_conversation(value)}


def activate_conversation(canvas_id: str, conversation_id: str) -> Dict[str, Any]:
    value = _load_conversation(canvas_id, conversation_id)
    index_path = _index_path(canvas_id)
    with path_lock(index_path):
        index = _load_index(canvas_id)
        if conversation_id not in index["conversation_ids"]:
            index["conversation_ids"].insert(0, conversation_id)
        index["active_conversation_id"] = conversation_id
        _save_index(canvas_id, index)
    return {"active_conversation_id": conversation_id, "conversation": public_conversation(value)}


def delete_conversation(canvas_id: str, conversation_id: str) -> Dict[str, Any]:
    path = _conversation_path(canvas_id, conversation_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="画布精灵会话不存在")
    index_path = _index_path(canvas_id)
    with path_lock(index_path):
        index = _load_index(canvas_id)
        try:
            path.unlink()
        except OSError as exc:
            raise HTTPException(status_code=500, detail="无法删除画布精灵会话") from exc
        index["conversation_ids"] = [item for item in index["conversation_ids"] if item != conversation_id]
        if index.get("active_conversation_id") == conversation_id:
            index["active_conversation_id"] = index["conversation_ids"][0] if index["conversation_ids"] else ""
        _save_index(canvas_id, index)
    return {"ok": True, "active_conversation_id": index["active_conversation_id"]}


async def _stream_lock(canvas_id: str, conversation_id: str) -> asyncio.Lock:
    key = f"{canvas_id}:{conversation_id}"
    async with _STREAM_LOCKS_GUARD:
        return _STREAM_LOCKS.setdefault(key, asyncio.Lock())


async def _release_stream_lock(canvas_id: str, conversation_id: str, lock: asyncio.Lock) -> None:
    if lock.locked():
        lock.release()
    key = f"{canvas_id}:{conversation_id}"
    async with _STREAM_LOCKS_GUARD:
        if _STREAM_LOCKS.get(key) is lock and not lock.locked():
            _STREAM_LOCKS.pop(key, None)


async def update_conversation_exclusive(
    canvas_id: str,
    conversation_id: str,
    payload: CanvasAssistantConversationUpdate,
) -> Dict[str, Any]:
    canvas_id = _safe_id(canvas_id, "画布")
    conversation_id = _safe_id(conversation_id, "会话")
    lock = await _stream_lock(canvas_id, conversation_id)
    if lock.locked():
        raise HTTPException(status_code=409, detail="生成回复时不能修改当前会话")
    await lock.acquire()
    try:
        return await asyncio.to_thread(update_conversation, canvas_id, conversation_id, payload)
    finally:
        await _release_stream_lock(canvas_id, conversation_id, lock)


async def delete_conversation_exclusive(canvas_id: str, conversation_id: str) -> Dict[str, Any]:
    canvas_id = _safe_id(canvas_id, "画布")
    conversation_id = _safe_id(conversation_id, "会话")
    lock = await _stream_lock(canvas_id, conversation_id)
    if lock.locked():
        raise HTTPException(status_code=409, detail="请先停止生成，再删除当前会话")
    await lock.acquire()
    try:
        return await asyncio.to_thread(delete_conversation, canvas_id, conversation_id)
    finally:
        await _release_stream_lock(canvas_id, conversation_id, lock)


async def _upstream_message(item: Dict[str, Any]) -> Dict[str, Any] | None:
    role = item.get("role")
    if role not in {"user", "assistant"}:
        return None
    text = str(item.get("content") or "")
    refs = item.get("attachments") or []
    if role == "user" and refs:
        content: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        for ref in refs[:MAX_REFERENCES]:
            url = await asyncio.to_thread(legacy.reference_to_data_url, ref, 1536)
            if not str(url or "").startswith("data:image/"):
                raise HTTPException(status_code=400, detail=f"参考图不存在或无法读取：{ref.get('name') or ref.get('url') or 'image'}")
            content.append({"type": "image_url", "image_url": {"url": url}})
        return {"role": role, "content": content}
    return {"role": role, "content": text}


async def _upstream_messages(value: Dict[str, Any]) -> List[Dict[str, Any]]:
    source = value.get("source") or {}
    messages: List[Dict[str, Any]] = [{"role": "system", "content": str(source.get("system_prompt") or legacy.SYSTEM_PROMPT)}]
    for item in value.get("messages", [])[-CONTEXT_MESSAGES:]:
        if item.get("status") == "error":
            continue
        converted = await _upstream_message(item)
        if converted:
            messages.append(converted)
    return messages


def _history_references(payload: CanvasAssistantMessageRequest) -> List[Dict[str, Any]]:
    result = []
    seen = set()
    for raw in payload.reference_images[:MAX_REFERENCES]:
        item = _model_dump(raw)
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        if not url.startswith(LOCAL_REFERENCE_PREFIXES):
            raise HTTPException(status_code=400, detail="画布精灵只接受 SynCanvas 本地图片，不能保存 Base64 或外部 URL")
        seen.add(url)
        result.append({key: item.get(key) for key in ("url", "name", "role", "mime") if item.get(key)})
    return result


async def stream_message(
    canvas_id: str,
    conversation_id: str,
    payload: CanvasAssistantMessageRequest,
    request: Request,
) -> AsyncIterator[str]:
    canvas_id = _safe_id(canvas_id, "画布")
    conversation_id = _safe_id(conversation_id, "会话")
    lock = await _stream_lock(canvas_id, conversation_id)
    if lock.locked():
        raise HTTPException(status_code=409, detail="该画布精灵会话正在生成回复")
    await lock.acquire()

    async def generate() -> AsyncIterator[str]:
        value: Dict[str, Any] | None = None
        try:
            value = await asyncio.to_thread(_load_conversation, canvas_id, conversation_id)
            if len(value.get("messages", [])) + 2 > MAX_MESSAGES:
                yield legacy.sse_event({"type": "error", "detail": "当前会话已达到 200 条消息，请新建会话"})
                return
            data = _model_dump(payload)
            bootstrap = bool(data.get("bootstrap"))
            if bootstrap and value.get("started"):
                yield legacy.sse_event({"type": "error", "detail": "当前会话已经开始"})
                return
            content = BOOTSTRAP_MESSAGE if bootstrap else str(data.get("message") or "").strip()
            user_message = {
                "id": uuid.uuid4().hex,
                "role": "user",
                "content": content,
                "attachments": [] if bootstrap else _history_references(payload),
                "hidden": bootstrap,
                "created_at": now_ms(),
            }
            value.setdefault("messages", []).append(user_message)
            value["started"] = True
            await asyncio.to_thread(_save_conversation, canvas_id, value)
            yield legacy.sse_event({"type": "meta", "conversation": public_conversation(value)})

            provider_id = str(value.get("provider_id") or "")
            requested_model = str(value.get("model") or "")
            chat_base, chat_headers, model = legacy.resolve_chat_provider(provider_id, requested_model, requested_model)
            upstream_messages = await _upstream_messages(value)
            body: Dict[str, Any] = {"model": model, "messages": upstream_messages, "stream": True}
            temperature = (value.get("source") or {}).get("temperature")
            if temperature is not None:
                body["temperature"] = float(temperature)

            content_parts: List[str] = []
            raw_usage: Any = None
            async with httpx.AsyncClient(timeout=legacy.AI_REQUEST_TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    f"{chat_base}/chat/completions",
                    headers=chat_headers,
                    json=body,
                ) as response:
                    if response.status_code >= 400:
                        detail = (await response.aread()).decode("utf-8", errors="replace")
                        yield legacy.sse_event({"type": "error", "detail": redact_sensitive_text(f"上游对话接口错误：{detail}")})
                        return
                    async for line in response.aiter_lines():
                        if await request.is_disconnected():
                            return
                        if not line:
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            break
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(chunk, dict) and chunk.get("usage"):
                            raw_usage = chunk.get("usage")
                        delta = legacy.text_delta_from_chat_chunk(chunk)
                        if delta:
                            content_parts.append(delta)
                            yield legacy.sse_event({"type": "delta", "delta": delta})
            if await request.is_disconnected():
                return
            assistant_message = {
                "id": uuid.uuid4().hex,
                "role": "assistant",
                "content": "".join(content_parts).strip() or "接口返回了空回复。",
                "created_at": now_ms(),
                "model": model,
                "status": "done",
                "raw_usage": raw_usage,
            }
            value["model"] = model
            value.setdefault("messages", []).append(assistant_message)
            await asyncio.to_thread(_save_conversation, canvas_id, value)
            yield legacy.sse_event({
                "type": "done",
                "conversation": public_conversation(value),
                "message": _public_message(assistant_message),
            })
        except asyncio.CancelledError:
            raise
        except HTTPException as exc:
            yield legacy.sse_event({"type": "error", "detail": redact_sensitive_text(str(exc.detail))})
        except httpx.HTTPError as exc:
            yield legacy.sse_event({"type": "error", "detail": redact_sensitive_text(f"请求上游对话接口失败：{exc}")})
        except Exception as exc:
            yield legacy.sse_event({"type": "error", "detail": redact_sensitive_text(f"画布精灵运行失败：{exc}")})
        finally:
            await _release_stream_lock(canvas_id, conversation_id, lock)

    return generate()
