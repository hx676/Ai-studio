from fastapi import APIRouter, Header, Request, status

from app.models.image_batch import ChatImageBatchRequest, ZImageBatchRequest
from app.services import image_batch_service as service


router = APIRouter()


@router.post("/api/zimage-batches", status_code=status.HTTP_202_ACCEPTED)
async def create_zimage_batch(payload: ZImageBatchRequest):
    return await service.create_zimage_batch(payload)


@router.post("/api/chat/image-batches", status_code=status.HTTP_202_ACCEPTED)
async def create_chat_image_batch(
    payload: ChatImageBatchRequest,
    request: Request,
    x_user_id: str = Header(default=""),
):
    return await service.create_chat_image_batch(payload, request, x_user_id)


@router.get("/api/image-batches/{batch_id}")
async def get_image_batch(batch_id: str, request: Request, x_user_id: str = Header(default="")):
    return await service.get_image_batch(batch_id, request, x_user_id)


@router.post(
    "/api/conversations/{conversation_id}/messages/{message_id}/retry-image",
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_chat_image_message(
    conversation_id: str,
    message_id: str,
    request: Request,
    x_user_id: str = Header(default=""),
):
    return await service.retry_chat_image_message(conversation_id, message_id, request, x_user_id)
