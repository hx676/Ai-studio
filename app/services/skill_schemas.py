from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SkillModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class CustomSkillInput(SkillModel):
    message: str = Field(default="", max_length=40000)
    images: List[str] = Field(default_factory=list, max_length=8)
    context: Dict[str, Any] = Field(default_factory=dict)


class CustomSkillOutput(SkillModel):
    text: str = ""


class ReferenceAnalyzeInput(SkillModel):
    images: List[str] = Field(default_factory=list, max_length=4)


class ReferenceItem(SkillModel):
    index: int
    description: str = ""
    style_fingerprint: str = ""
    keep_strict: List[str] = Field(default_factory=list)
    style_keep_strict: List[str] = Field(default_factory=list)
    rendering_medium: str = ""
    composition_type: str = ""
    typography_style: str = ""
    has_text: bool = False
    dominant_colors: List[str] = Field(default_factory=list)


class ReferenceAnalyzeOutput(SkillModel):
    items: List[ReferenceItem] = Field(default_factory=list)


class ExtractStyleInput(SkillModel):
    images: List[str] = Field(default_factory=list, min_length=1, max_length=8)
    name: str = Field(default="", max_length=120)
    category: Literal["ppt", "poster", "illustration", "photography", "detail-page", "comic"] = "illustration"
    requirements: str = Field(default="", max_length=4000)


class PageStyle(SkillModel):
    id: str = ""
    name: str = ""
    role: str = ""
    layoutDescription: str = ""
    stylePromptEn: str = ""
    stylePromptZh: str = ""
    negativePrompt: str = ""
    thumbnail: Optional[str] = None


class ExtractStyleOutput(SkillModel):
    name: str
    category: str
    requirements: str = ""
    features: Dict[str, Any] = Field(default_factory=dict)
    stylePromptEn: str = ""
    stylePromptZh: str = ""
    negativePrompt: str = ""
    pageStyles: List[PageStyle] = Field(default_factory=list)


class ComposeStudioInput(SkillModel):
    template: Dict[str, Any] = Field(default_factory=dict)
    userIdea: str = Field(default="", max_length=20000)
    aspectRatio: str = Field(default="1:1", max_length=40)
    targetModel: str = Field(default="", max_length=240)
    referenceImages: List[str] = Field(default_factory=list, max_length=4)
    promptLanguage: Literal["zh", "en"] = "zh"


class ComposeOutput(SkillModel):
    prompt: str = ""
    negative: str = ""
    notes: str = ""
    targetModel: str = ""
    aspectRatio: str = ""
    reference_images: List[ReferenceItem] = Field(default_factory=list)
    pageStyle: Optional[Dict[str, Any]] = None
    fallbackUsed: bool = False
    composeError: str = ""


class ComposePptInput(SkillModel):
    pageStyle: Optional[Dict[str, Any]] = None
    slideRole: str = Field(default="content", max_length=80)
    userIdea: str = Field(default="", max_length=20000)
    userContent: str = Field(default="", max_length=40000)
    aspectRatio: str = Field(default="16:9", max_length=40)
    targetModel: str = Field(default="", max_length=240)
    referenceImages: List[str] = Field(default_factory=list, max_length=4)
    promptLanguage: Literal["zh", "en"] = "zh"
    designBrief: Optional[Dict[str, Any]] = None
    pageIndex: Optional[int] = Field(default=None, ge=1)
    totalPages: Optional[int] = Field(default=None, ge=1, le=500)
    analyzedReferences: Optional[List[Dict[str, Any]]] = None


class ComposeDetailInput(SkillModel):
    pageStyle: Optional[Dict[str, Any]] = None
    sectionRole: str = Field(default="feature", max_length=80)
    userIdea: str = Field(default="", max_length=20000)
    userContent: str = Field(default="", max_length=40000)
    aspectRatio: str = Field(default="3:4", max_length=40)
    targetModel: str = Field(default="", max_length=240)
    referenceImages: List[str] = Field(default_factory=list, max_length=4)
    promptLanguage: Literal["zh", "en"] = "zh"


class DraftDetailCopyInput(SkillModel):
    userIdea: str = Field(default="", max_length=20000)
    currentContent: str = Field(default="", max_length=40000)
    selectedSections: List[str] = Field(default_factory=list, max_length=100)
    styleHint: str = Field(default="", max_length=10000)


class DraftDetailCopyOutput(SkillModel):
    content: str = ""
    lines: List[str] = Field(default_factory=list)
    notes: str = ""


class DraftPptOutlineInput(SkillModel):
    userIdea: str = Field(default="", max_length=20000)
    selectedPages: List[str] = Field(default_factory=list, max_length=100)
    styleHint: str = Field(default="", max_length=10000)
    existingContent: str = Field(default="", max_length=40000)


class DraftPptOutlineOutput(SkillModel):
    content: str = ""
    outline: List[str] = Field(default_factory=list)
    notes: str = ""


class DesignBriefInput(SkillModel):
    userIdea: str = Field(default="", max_length=20000)
    outlineText: str = Field(default="", max_length=40000)
    totalPages: int = Field(default=1, ge=1, le=500)
    aspectRatio: str = Field(default="16:9", max_length=40)
    promptLanguage: Literal["zh", "en"] = "zh"
    referenceImages: List[str] = Field(default_factory=list, max_length=4)


class DesignBriefOutput(SkillModel):
    ok: bool
    designBrief: Optional[Dict[str, Any]] = None
    referenceImages: List[ReferenceItem] = Field(default_factory=list)
    briefError: str = ""


class InpaintPromptInput(SkillModel):
    originalPrompt: str = Field(default="", max_length=30000)
    regionDescription: str = Field(default="", max_length=10000)
    editInstruction: str = Field(min_length=1, max_length=20000)


class InpaintPromptOutput(SkillModel):
    edit_prompt: str = ""
    preserve: str = ""


class UpscaleRepairInput(SkillModel):
    originalPrompt: str = Field(default="", max_length=30000)
    targetSize: str = Field(default="2K", max_length=20)
    aspectRatio: str = Field(default="auto", max_length=40)
    extraNotes: str = Field(default="", max_length=10000)


class UpscaleRepairOutput(SkillModel):
    edit_prompt: str
    preserve: str
    text_to_restore: str = ""
    target_size: Literal["2K", "4K"]


class ApiDoctorInput(SkillModel):
    baseUrl: str = Field(default="", max_length=1000)
    models: List[str] = Field(default_factory=list, max_length=300)
    pingError: Optional[str] = Field(default=None, max_length=5000)
    current: Dict[str, Any] = Field(default_factory=dict)


class ApiDoctorOutput(SkillModel):
    ok: bool
    baseUrl_fix: str = ""
    recommend: Dict[str, Any] = Field(default_factory=dict)
    issues: List[str] = Field(default_factory=list)
    heuristic: bool = False


def model_schema(model: type[BaseModel]) -> Dict[str, Any]:
    if hasattr(model, "model_json_schema"):
        return model.model_json_schema()
    return model.schema()


def validate_model(model: type[BaseModel], value: Any) -> BaseModel:
    if hasattr(model, "model_validate"):
        return model.model_validate(value)
    return model.parse_obj(value)


def dump_model(value: BaseModel) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()
