from typing import Any, Dict

from pydantic import BaseModel, Field


class NodeExtensionUpdateRequest(BaseModel):
    enabled: bool


class NodeExtensionDependencyInstallRequest(BaseModel):
    confirmed: bool = False


class NodeExtensionApplyRequest(BaseModel):
    restart_delay: int = Field(default=3, ge=1, le=30)


class NodeRunCreateRequest(BaseModel):
    node_type: str = Field(min_length=3, max_length=180)
    node_version: int = Field(default=1, ge=1)
    state: Dict[str, Any] = Field(default_factory=dict)
    inputs: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)
    canvas_id: str = Field(default="", max_length=120)
    node_id: str = Field(default="", max_length=120)
