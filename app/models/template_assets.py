from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TemplateAssetCreateRequest(BaseModel):
    library_id: str = ""
    category_id: str = ""
    name: str = "模板"
    template: Dict[str, Any]
    thumbnail_url: str = ""
    reference_image_urls: List[str] = Field(default_factory=list)
    source_canvas_id: str = ""
    source_node_id: str = ""
    source_skill_id: str = ""
    source_metadata: Dict[str, Any] = Field(default_factory=dict)


class TemplateAssetUpdateRequest(BaseModel):
    library_id: Optional[str] = None
    category_id: Optional[str] = None
    name: Optional[str] = None
    template: Optional[Dict[str, Any]] = None
    thumbnail_url: Optional[str] = None
    reference_image_urls: Optional[List[str]] = None
    source_canvas_id: Optional[str] = None
    source_node_id: Optional[str] = None
    source_skill_id: Optional[str] = None
    source_metadata: Optional[Dict[str, Any]] = None
