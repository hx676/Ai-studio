from typing import List, Literal

from pydantic import BaseModel, Field, model_validator

from app.models.canvas import AIReference, LLM_MESSAGE_MAX_LENGTH


class CanvasAssistantSourceRef(BaseModel):
    kind: Literal["general", "template", "agent"] = "general"
    id: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def validate_source_id(self):
        if self.kind != "general" and not self.id.strip():
            raise ValueError("模板或智能体来源必须提供 ID")
        if self.kind == "general":
            self.id = ""
        return self


class CanvasAssistantConversationCreate(BaseModel):
    source: CanvasAssistantSourceRef = Field(default_factory=CanvasAssistantSourceRef)
    provider_id: str = Field(default="", max_length=80)
    model: str = Field(default="", max_length=240)


class CanvasAssistantConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    provider_id: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=240)


class CanvasAssistantMessageRequest(BaseModel):
    message: str = Field(default="", max_length=LLM_MESSAGE_MAX_LENGTH)
    reference_images: List[AIReference] = Field(default_factory=list, max_length=8)
    bootstrap: bool = False

    @model_validator(mode="after")
    def validate_message(self):
        if not self.bootstrap and not self.message.strip() and not self.reference_images:
            raise ValueError("消息或图片不能为空")
        return self
