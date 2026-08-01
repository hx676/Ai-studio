import asyncio

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse

from app.models.canvas_assistant import (
    CanvasAssistantConversationCreate,
    CanvasAssistantConversationUpdate,
    CanvasAssistantMessageRequest,
)
from app.services import canvas_assistant_service as service


router = APIRouter(tags=["canvas-assistant"])


@router.get("/api/canvas-assistant/sources")
async def sources():
    return await asyncio.to_thread(service.list_sources)


@router.get("/api/canvases/{canvas_id}/assistant/conversations")
async def conversations(canvas_id: str):
    return await asyncio.to_thread(service.list_conversations, canvas_id)


@router.post("/api/canvases/{canvas_id}/assistant/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(canvas_id: str, payload: CanvasAssistantConversationCreate):
    return await asyncio.to_thread(service.create_conversation, canvas_id, payload)


@router.get("/api/canvases/{canvas_id}/assistant/conversations/{conversation_id}")
async def get_conversation(canvas_id: str, conversation_id: str):
    return await asyncio.to_thread(service.get_conversation, canvas_id, conversation_id)


@router.patch("/api/canvases/{canvas_id}/assistant/conversations/{conversation_id}")
async def update_conversation(canvas_id: str, conversation_id: str, payload: CanvasAssistantConversationUpdate):
    return await service.update_conversation_exclusive(canvas_id, conversation_id, payload)


@router.delete("/api/canvases/{canvas_id}/assistant/conversations/{conversation_id}")
async def delete_conversation(canvas_id: str, conversation_id: str):
    return await service.delete_conversation_exclusive(canvas_id, conversation_id)


@router.post("/api/canvases/{canvas_id}/assistant/conversations/{conversation_id}/activate")
async def activate_conversation(canvas_id: str, conversation_id: str):
    return await asyncio.to_thread(service.activate_conversation, canvas_id, conversation_id)


@router.post("/api/canvases/{canvas_id}/assistant/conversations/{conversation_id}/messages/stream")
async def stream_message(
    canvas_id: str,
    conversation_id: str,
    payload: CanvasAssistantMessageRequest,
    request: Request,
):
    iterator = await service.stream_message(canvas_id, conversation_id, payload, request)
    return StreamingResponse(iterator, media_type="text/event-stream")
