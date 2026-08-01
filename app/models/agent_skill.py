from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AgentUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    modelKind: Literal["text", "vision"] = "text"
    temperature: float = Field(default=0.5, ge=0.0, le=2.0)
    systemPrompt: str = Field(min_length=1, max_length=50000)


class AgentCreate(AgentUpdate):
    id: str = Field(default="", max_length=80)


class AgentImportRequest(BaseModel):
    agents: List[Dict[str, Any]] = Field(default_factory=list)


class SkillUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    agentId: str = Field(min_length=1, max_length=80)
    instructions: str = Field(min_length=1, max_length=50000)
    expectJson: bool = False


class SkillCreate(SkillUpdate):
    id: str = Field(default="", max_length=80)


class AIRuntimeSettingsUpdate(BaseModel):
    provider_id: str = Field(default="", max_length=80)
    text_model: str = Field(default="", max_length=240)
    vision_model: str = Field(default="", max_length=240)


class AIRunRequest(BaseModel):
    input: Dict[str, Any] = Field(default_factory=dict)
    provider_id: str = Field(default="", max_length=80)
    text_model: str = Field(default="", max_length=240)
    vision_model: str = Field(default="", max_length=240)
    canvas_id: str = Field(default="", max_length=120)
    node_id: str = Field(default="", max_length=120)


class AgentRunRequest(AIRunRequest):
    expect_json: bool = False


class AIRunRecord(BaseModel):
    run_id: str
    kind: Literal["agent", "skill"]
    target_id: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]
    canvas_id: str = ""
    node_id: str = ""
    created_at: int
    started_at: Optional[int] = None
    completed_at: Optional[int] = None
    duration_ms: Optional[int] = None
    output_text: str = ""
    output: Optional[Any] = None
    model: str = ""
    warnings: List[str] = Field(default_factory=list)
    fallback_used: bool = False
    error: str = ""
