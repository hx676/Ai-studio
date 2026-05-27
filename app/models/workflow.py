from typing import Any, Dict, List, Optional
from pydantic import BaseModel

class WorkflowField(BaseModel):
    id: str
    node: str = ""
    input: str = ""
    name: str = ""
    type: str = "text"
    default: Any = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    options: List[str] = []
    random_enabled: bool = False

class WorkflowConfig(BaseModel):
    title: str = ""
    fields: List[WorkflowField] = []
    mini_cards: Dict[str, Any] = {}

class WorkflowUploadRequest(BaseModel):
    name: str
    workflow: Dict[str, Any]

class WorkflowRunRequest(BaseModel):
    fields: Dict[str, Any] = {}
    config: WorkflowConfig
    client_id: str = ""

class ComfyInstancesPayload(BaseModel):
    instances: List[str] = []
