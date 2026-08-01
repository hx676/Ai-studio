from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class NodeEngineInstallRequest(BaseModel):
    install_root: Optional[str] = None
    manifest_url: Optional[str] = None
    source_root: Optional[str] = None
    force: bool = False


class RuntimeProcessRequest(BaseModel):
    wait_seconds: int = Field(default=45, ge=0, le=180)


class RuntimeGraphNode(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    class_type: str = Field(min_length=1, max_length=300)
    widgets: Dict[str, Any] = Field(default_factory=dict)
    input_modes: Dict[str, Literal["widget", "port"]] = Field(default_factory=dict)
    definition_fingerprint: str = Field(default="", max_length=128)


class RuntimeGraphConnection(BaseModel):
    from_node: str = Field(min_length=1, max_length=160)
    from_port: str = Field(min_length=1, max_length=160)
    to_node: str = Field(min_length=1, max_length=160)
    to_port: str = Field(min_length=1, max_length=160)


class RuntimeExternalInput(BaseModel):
    to_node: str = Field(min_length=1, max_length=160)
    to_port: str = Field(min_length=1, max_length=160)
    kind: str = Field(default="json", min_length=1, max_length=40)
    value: Any = None


class RuntimeGraphRunRequest(BaseModel):
    nodes: List[RuntimeGraphNode] = Field(min_length=1, max_length=500)
    connections: List[RuntimeGraphConnection] = Field(default_factory=list, max_length=2000)
    external_inputs: List[RuntimeExternalInput] = Field(default_factory=list, max_length=500)
    target_ids: List[str] = Field(default_factory=list, max_length=100)
    canvas_id: str = Field(default="", max_length=120)
    client_id: str = Field(default="", max_length=160)


class NodeEngineModelImportRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=4000)
    category: str = Field(min_length=1, max_length=80)
    conflict: Literal["skip", "rename", "replace"] = "skip"
    recursive: bool = True


class NodeEngineReadonlyModelPath(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(default="", max_length=120)
    base_path: str = Field(min_length=1, max_length=4000)
    paths: Dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class NodeEngineModelPathsRequest(BaseModel):
    sources: List[NodeEngineReadonlyModelPath] = Field(default_factory=list, max_length=64)


class NodeEngineExtensionInstallRequest(BaseModel):
    source: str = Field(min_length=1, max_length=4000)
    package_id: str = Field(default="", max_length=120)
    install_dependencies: bool = True
    replace: bool = False


class NodeEngineExtensionActionRequest(BaseModel):
    wait_seconds: int = Field(default=90, ge=0, le=180)
