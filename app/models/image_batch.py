from typing import List

from pydantic import BaseModel, Field

from app.models.canvas import AIReference, OnlineImageRequest


class ZImageBatchRequest(OnlineImageRequest):
    count: int = Field(default=1, ge=1, le=4)


class ChatImageBatchRequest(BaseModel):
    conversation_id: str = ""
    message: str = Field(min_length=1, max_length=20000)
    provider: str = "comfly"
    image_model: str = ""
    size: str = "1024x1024"
    quality: str = "auto"
    reference_images: List[AIReference] = []
    count: int = Field(default=1, ge=1, le=4)
