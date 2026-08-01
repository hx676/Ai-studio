import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type

from pydantic import BaseModel

from app.services.skill_schemas import (
    ApiDoctorInput,
    ApiDoctorOutput,
    ComposeDetailInput,
    ComposeOutput,
    ComposePptInput,
    ComposeStudioInput,
    DesignBriefInput,
    DesignBriefOutput,
    DraftDetailCopyInput,
    DraftDetailCopyOutput,
    DraftPptOutlineInput,
    DraftPptOutlineOutput,
    ExtractStyleInput,
    ExtractStyleOutput,
    InpaintPromptInput,
    InpaintPromptOutput,
    ReferenceAnalyzeInput,
    ReferenceAnalyzeOutput,
    UpscaleRepairInput,
    UpscaleRepairOutput,
    dump_model,
)


SkillRunner = Callable[[Dict[str, Any], Any], Awaitable[Dict[str, Any]]]


@dataclass(frozen=True)
class SkillDefinition:
    id: str
    name: str
    description: str
    agents: List[str]
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]
    runner: SkillRunner


def _text(value: Any) -> str:
    return str(value or "").strip()


def _pick(value: Any, *keys: str) -> Any:
    if not isinstance(value, dict):
        return ""
    for key in keys:
        candidate = value.get(key)
        if candidate is not None and candidate != "":
            return candidate
    return ""


def _list(value: Any, limit: int = 100) -> List[str]:
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value[:limit] if _text(item)]


def _obj(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _unique(values: List[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        item = _text(value)
        key = item.casefold()
        if item and key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


async def run_reference_analyze(data: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    images = [_text(item) for item in data.get("images", []) if _text(item)][:4]
    if not images:
        return {"items": []}
    ctx.require_vision_model()

    async def analyze(url: str, index: int) -> Dict[str, Any]:
        try:
            prepared = await ctx.prepare_image(url)
            parsed = await ctx.call_agent(
                "reference-analyzer",
                [
                    {"type": "text", "text": "分析这张参考图，严格按照 JSON schema 输出。"},
                    {"type": "image_url", "image_url": {"url": prepared}},
                ],
                retries=4,
                base_delay_ms=1500,
            )
            return {
                "index": index,
                "description": _text(parsed.get("description"))[:800],
                "style_fingerprint": _text(parsed.get("style_fingerprint"))[:2400],
                "keep_strict": _list(parsed.get("keep_strict"), 8),
                "style_keep_strict": _list(parsed.get("style_keep_strict"), 8),
                "rendering_medium": _text(parsed.get("rendering_medium"))[:200],
                "composition_type": _text(parsed.get("composition_type"))[:150],
                "typography_style": _text(parsed.get("typography_style"))[:200],
                "has_text": bool(parsed.get("has_text")),
                "dominant_colors": _list(parsed.get("dominant_colors"), 5),
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {
                "index": index,
                "description": f"(参考图 {index + 1} 解读失败：{str(exc)[:80]})",
                "keep_strict": [],
            }

    return {"items": await asyncio.gather(*(analyze(url, index) for index, url in enumerate(images)))}


def _find_sections(value: Any) -> Optional[List[Any]]:
    if isinstance(value, list):
        return value or None
    if not isinstance(value, dict):
        return None
    for key in ("pageStyles", "sections", "pages", "items", "data", "result"):
        found = _find_sections(value.get(key))
        if found:
            return found
    for child in value.values():
        found = _find_sections(child)
        if found:
            return found
    return None


def _normalize_section(raw: Any, images: List[str], default_role: str) -> Dict[str, Any]:
    item = _obj(raw)
    raw_index = item.get("source_image_index", item.get("sourceImageIndex", item.get("index", -1)))
    index = raw_index if isinstance(raw_index, int) else -1
    return {
        "id": _short_id(),
        "name": _pick(item, "name", "title") or "Untitled section",
        "role": _pick(item, "role", "type") or default_role,
        "layoutDescription": _pick(item, "layoutDescription", "layout_description", "layout", "description") or "",
        "stylePromptEn": _pick(item, "style_prompt_en", "stylePromptEn", "prompt_en", "promptEn") or "",
        "stylePromptZh": _pick(item, "style_prompt_zh", "stylePromptZh", "prompt_zh", "promptZh") or "",
        "negativePrompt": _pick(item, "negative_prompt", "negativePrompt", "negative") or "",
        "thumbnail": images[index] if 0 <= index < len(images) else None,
    }


def _first_reference(features: Dict[str, Any]) -> Dict[str, Any]:
    refs = features.get("reference_images", features.get("referenceImages", []))
    return _obj(refs[0]) if isinstance(refs, list) and refs else {}


def _normalize_generic_features(features: Any) -> Dict[str, Any]:
    raw = _obj(features)
    summary = _obj(raw.get("global_summary", raw.get("globalSummary")))
    if not summary:
        return raw
    return {
        **raw,
        "palette": raw.get("palette") or summary.get("palette") or [],
        "typography_hint": raw.get("typography_hint") or raw.get("typographyHint") or summary.get("typography_hint") or summary.get("typographyHint") or "",
        "mood": raw.get("mood") or summary.get("mood") or "",
        "composition": raw.get("composition") or summary.get("composition") or "",
        "clean_image_control": raw.get("clean_image_control") or raw.get("cleanImageControl") or summary.get("clean_image_control") or summary.get("cleanImageControl"),
    }


def _clean_controls(value: Any) -> List[str]:
    item = _obj(value)
    return [_text(item.get(key)) for key in ("background", "color", "elements", "text", "edges") if _text(item.get(key))]


def _fallback_style_en(features: Dict[str, Any]) -> str:
    normalized = _normalize_generic_features(features)
    summary = _obj(normalized.get("global_summary", normalized.get("globalSummary")))
    ref = _first_reference(normalized)
    palette = _list(normalized.get("palette") or summary.get("palette"))
    parts = [
        _pick(ref, "style_fingerprint", "styleFingerprint"),
        f"Composition: {normalized.get('composition')}" if normalized.get("composition") else "",
        f"Typography: {normalized.get('typography_hint')}" if normalized.get("typography_hint") else "",
        f"Mood: {normalized.get('mood')}" if normalized.get("mood") else "",
        f"Palette: {', '.join(palette)}" if palette else "",
    ]
    style_keep = _list(ref.get("style_keep_strict", ref.get("styleKeepStrict")))
    if style_keep:
        parts.append(f"Style elements to preserve: {'; '.join(style_keep)}")
    controls = _clean_controls(normalized.get("clean_image_control") or summary.get("clean_image_control"))
    if controls:
        parts.append(f"Clean image control: {'; '.join(controls)}")
    return " ".join(_text(item) for item in parts if _text(item))


def _fallback_style_zh(features: Dict[str, Any]) -> str:
    normalized = _normalize_generic_features(features)
    summary = _obj(normalized.get("global_summary", normalized.get("globalSummary")))
    ref = _first_reference(normalized)
    palette = _list(normalized.get("palette") or summary.get("palette"))
    parts = [
        f"整体氛围：{normalized.get('mood')}" if normalized.get("mood") else "",
        f"构图：{normalized.get('composition')}" if normalized.get("composition") else "",
        f"排版：{normalized.get('typography_hint')}" if normalized.get("typography_hint") else "",
        f"配色：{'、'.join(palette)}" if palette else "",
        f"参考图：{_pick(ref, 'description')}" if _pick(ref, "description") else "",
    ]
    style_keep = _list(ref.get("style_keep_strict", ref.get("styleKeepStrict")))
    if style_keep:
        parts.append(f"需保留的风格元素：{'；'.join(style_keep)}")
    controls = _clean_controls(normalized.get("clean_image_control") or summary.get("clean_image_control"))
    if controls:
        parts.append(f"画面控制：{'；'.join(controls)}")
    return "；".join(_text(item) for item in parts if _text(item))


def _fallback_negative(features: Dict[str, Any]) -> str:
    normalized = _normalize_generic_features(features)
    summary = _obj(normalized.get("global_summary", normalized.get("globalSummary")))
    ref = _first_reference(normalized)
    return ", ".join(_unique([
        *_list(normalized.get("negative_traits", normalized.get("negativeTraits"))),
        *_list(summary.get("negative_traits", summary.get("negativeTraits"))),
        *_list(ref.get("negative_traits", ref.get("negativeTraits"))),
    ]))


async def run_extract_style(data: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    images = [_text(item) for item in data.get("images", []) if _text(item)][:8]
    if not images:
        raise ValueError("至少需要一张参考图")
    ctx.require_vision_model()
    prepared_images = [await ctx.prepare_image(url) for url in images]
    category = data.get("category") or "illustration"
    requirements = _text(data.get("requirements"))
    labels = {"ppt": "PPT 演示页", "poster": "海报", "illustration": "插画", "photography": "摄影", "detail-page": "电商详情页", "comic": "漫画"}
    category_line = f"\n模板目标类型：{labels.get(category, category)}"
    requirement_line = f"\n用户的模板需求：{requirements}" if requirements else ""
    sectioned = {
        "ppt": ("ppt-page-extractor", "PPT 风格模板", "PPT 参考截图", "content"),
        "detail-page": ("detail-section-extractor", "详情页风格模板", "电商详情页参考图", "feature"),
    }
    if category in sectioned:
        agent_id, default_name, prompt_label, default_role = sectioned[category]
        content = [
            {"type": "text", "text": f"分析以下{prompt_label}并输出 pageStyles JSON。{category_line}{requirement_line}\n同类页面只保留一条；source_image_index 从 0 开始。"},
            *[{"type": "image_url", "image_url": {"url": url}} for url in prepared_images],
        ]
        try:
            parsed = await ctx.call_agent(agent_id, content, retries=2, base_delay_ms=1500, timeout_seconds=90)
        except Exception as exc:
            raise ValueError(f"{agent_id} 没有返回可解析的页面结构：{str(exc)[:300]}") from exc
        sections = _find_sections(parsed) or []
        if not sections:
            raise ValueError(f"{agent_id} 没有返回可解析的页面结构")
        page_styles = [_normalize_section(item, images, default_role) for item in sections]
        return {
            "name": _text(data.get("name")) or default_name,
            "category": category,
            "requirements": requirements,
            "features": {"palette": parsed.get("palette", []), "typography": parsed.get("typography", ""), "mood": parsed.get("mood", "")},
            "stylePromptEn": " ".join(item["stylePromptEn"] for item in page_styles[:2] if item["stylePromptEn"]),
            "stylePromptZh": " ".join(item["stylePromptZh"] for item in page_styles[:2] if item["stylePromptZh"]),
            "negativePrompt": page_styles[0].get("negativePrompt", "") if page_styles else "",
            "pageStyles": page_styles,
        }

    ctx.require_text_model()
    features = await ctx.call_agent(
        "vision-analyzer",
        [{"type": "text", "text": f"分析以下参考图并输出 style features JSON。{category_line}{requirement_line}"}, *[{"type": "image_url", "image_url": {"url": url}} for url in prepared_images]],
        retries=2,
        base_delay_ms=1500,
        timeout_seconds=90,
    )
    style: Dict[str, Any] = {}
    try:
        style = await ctx.call_agent("template-distiller", _json({**features, "_category": labels.get(category, category), "_requirements": requirements}), retries=2, base_delay_ms=1000, timeout_seconds=90)
    except Exception as exc:
        ctx.warn(f"风格蒸馏失败，已使用视觉分析兜底：{str(exc)[:160]}")
    normalized = _normalize_generic_features(features)
    return {
        "name": _text(data.get("name")) or "Untitled style",
        "category": category,
        "requirements": requirements,
        "features": normalized,
        "stylePromptEn": _pick(style, "style_prompt_en", "stylePromptEn", "prompt_en", "promptEn") or _pick(normalized, "style_prompt_en", "stylePromptEn") or _fallback_style_en(normalized),
        "stylePromptZh": _pick(style, "style_prompt_zh", "stylePromptZh", "prompt_zh", "promptZh") or _pick(normalized, "style_prompt_zh", "stylePromptZh") or _fallback_style_zh(normalized),
        "negativePrompt": _pick(style, "negative_prompt", "negativePrompt", "negative") or _pick(normalized, "negative_prompt", "negativePrompt", "negative") or _fallback_negative(normalized),
        "pageStyles": [],
    }


async def _references(data: Dict[str, Any], ctx: Any) -> List[Dict[str, Any]]:
    result = await ctx.run_skill("reference-analyze", {"images": data.get("referenceImages", [])})
    return result.get("items", [])


async def run_compose_studio(data: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    ctx.require_text_model()
    template = _obj(data.get("template"))
    model = _text(data.get("targetModel")) or "gpt-image-2"
    refs = await _references(data, ctx)
    payload = {
        "template": {
            "style_prompt_en": template.get("stylePromptEn", ""),
            "style_prompt_zh": template.get("stylePromptZh", ""),
            "negative_prompt": template.get("negativePrompt", ""),
        },
        "user_idea": data.get("userIdea", ""),
        "aspect_ratio": data.get("aspectRatio", "1:1"),
        "target_model": model,
        "prompt_language": "en" if data.get("promptLanguage") == "en" else "zh",
        "reference_images": refs,
    }
    try:
        parsed = await ctx.call_agent("prompt-composer", _json(payload), retries=5, base_delay_ms=1500)
    except Exception as exc:
        parsed = {"prompt": getattr(exc, "raw_excerpt", ""), "negative": template.get("negativePrompt", ""), "notes": ""}
        ctx.mark_fallback()
    return {
        "prompt": _text(parsed.get("prompt")),
        "negative": _text(parsed.get("negative")) or _text(template.get("negativePrompt")),
        "notes": _text(parsed.get("notes")),
        "targetModel": model,
        "aspectRatio": data.get("aspectRatio", "1:1"),
        "reference_images": refs,
    }


def _sanitize_markdown(value: str) -> str:
    text = str(value or "").replace("\r\n", "\n")
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    return text.strip()


def _page_style_payload(page_style: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": page_style.get("name"),
        "role": page_style.get("role"),
        "layoutDescription": page_style.get("layoutDescription"),
        "style_prompt_en": page_style.get("stylePromptEn"),
        "style_prompt_zh": page_style.get("stylePromptZh"),
        "negative_prompt": page_style.get("negativePrompt"),
    }


def _ppt_fallback(data: Dict[str, Any], final_content: str, model: str, refs: List[Dict[str, Any]]) -> str:
    page_style = _obj(data.get("pageStyle"))
    role = _text(page_style.get("role") or data.get("slideRole") or "content")
    language = data.get("promptLanguage", "zh")
    style = _text(page_style.get("stylePromptEn") if language == "en" else page_style.get("stylePromptZh"))
    layout = _text(page_style.get("layoutDescription"))
    ratio = data.get("aspectRatio", "16:9")
    if language == "en":
        return "\n".join(item for item in [
            f"Create one complete PPT slide, aspect ratio {ratio}, for model {model}.",
            f"Page type: {_text(page_style.get('name')) or role} / {role}.",
            f"Style: {style}" if style else "",
            f"Layout: {layout}" if layout else "",
            f"Overall direction: {_text(data.get('userIdea'))}" if _text(data.get("userIdea")) else "",
            f"Content to render as readable text: {final_content}",
            f"Use {len(refs)} reference image(s) as visual anchors." if refs else "",
            "Render a polished presentation page with legible, aligned text faithful to the provided content.",
        ] if item)
    return "\n".join(item for item in [
        f"生成一张完整 PPT 页面，比例 {ratio}，目标模型 {model}。",
        f"页面类型：{_text(page_style.get('name')) or role} / {role}。",
        f"视觉风格：{style}" if style else "",
        f"版式结构：{layout}" if layout else "",
        f"整体想法：{_text(data.get('userIdea'))}" if _text(data.get("userIdea")) else "",
        f"需要渲染为清晰文字的文案：\n{final_content}",
        f"参考 {len(refs)} 张图片的风格、构图和需保留主体。" if refs else "",
        "生成完整精致的演示文稿页面，文字清晰、排版稳定并忠于提供内容。",
    ] if item)


async def run_compose_ppt(data: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    ctx.require_text_model()
    page_style = _obj(data.get("pageStyle")) or None
    model = _text(data.get("targetModel")) or "gpt-image-2"
    fallback_content = f"主题：{_text(data.get('userIdea'))}\n标题：核心信息标题\n要点1：关键价值表达\n要点2：数据或结果支撑\n总结：行动建议或结论"
    final_content = _sanitize_markdown(data.get("userContent", "")) or fallback_content
    refs = data.get("analyzedReferences") or await _references(data, ctx)
    payload = {
        "user_idea": data.get("userIdea", ""),
        "user_content": final_content,
        "aspect_ratio": data.get("aspectRatio", "16:9"),
        "target_model": model,
        "prompt_language": "en" if data.get("promptLanguage") == "en" else "zh",
        "ref_count": len(refs),
        "reference_images": refs,
    }
    agent_id = "ppt-freeform-composer"
    if page_style:
        payload["page_style"] = _page_style_payload(page_style)
        agent_id = "ppt-page-composer"
    else:
        payload["slide_role"] = data.get("slideRole", "content")
    if data.get("designBrief"):
        payload["design_brief"] = data["designBrief"]
    if data.get("pageIndex") is not None and data.get("totalPages") is not None:
        payload["page_position"] = {"current": data["pageIndex"], "total": data["totalPages"]}
    error = ""
    try:
        parsed = await ctx.call_agent(agent_id, _json(payload), retries=5, base_delay_ms=1500)
    except Exception as exc:
        parsed = {"prompt": getattr(exc, "raw_excerpt", ""), "negative": page_style.get("negativePrompt", "") if page_style else "", "notes": ""}
        error = str(exc)
    prompt = _text(parsed.get("prompt"))
    fallback_used = not bool(prompt)
    if fallback_used:
        prompt = _ppt_fallback(data, final_content, model, refs)
        ctx.mark_fallback()
    return {
        "prompt": prompt,
        "negative": _text(parsed.get("negative")) or (_text(page_style.get("negativePrompt")) if page_style else "") or "misspelled text, garbled characters, blurry typography",
        "notes": _text(parsed.get("notes")) or ("已使用基础提示词兜底。" if fallback_used else ""),
        "aspectRatio": data.get("aspectRatio", "16:9"),
        "targetModel": model,
        "pageStyle": {"id": page_style.get("id", ""), "name": page_style.get("name", ""), "role": page_style.get("role", "")} if page_style else None,
        "reference_images": refs,
        "fallbackUsed": fallback_used,
        "composeError": error,
    }


async def run_compose_detail(data: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    ctx.require_text_model()
    page_style = _obj(data.get("pageStyle")) or None
    model = _text(data.get("targetModel")) or "gpt-image-2"
    final_content = _text(data.get("userContent")) or f"主题：{_text(data.get('userIdea'))}\n重点：突出核心卖点与品牌信息\n语气：简洁、可读、适合电商详情页排版"
    refs = await _references(data, ctx)
    payload = {
        "user_idea": data.get("userIdea", ""),
        "user_content": final_content,
        "aspect_ratio": data.get("aspectRatio", "3:4"),
        "target_model": model,
        "prompt_language": "en" if data.get("promptLanguage") == "en" else "zh",
        "ref_count": len(refs),
        "reference_images": refs,
    }
    agent_id = "detail-freeform-composer"
    if page_style:
        payload["page_style"] = _page_style_payload(page_style)
        agent_id = "detail-section-composer"
    else:
        payload["section_role"] = data.get("sectionRole", "feature")
    try:
        parsed = await ctx.call_agent(agent_id, _json(payload), retries=5, base_delay_ms=1500)
    except Exception as exc:
        parsed = {"prompt": getattr(exc, "raw_excerpt", ""), "negative": page_style.get("negativePrompt", "") if page_style else "", "notes": ""}
        ctx.mark_fallback()
    return {
        "prompt": _text(parsed.get("prompt")),
        "negative": _text(parsed.get("negative")) or (_text(page_style.get("negativePrompt")) if page_style else "") or "misspelled text, garbled characters, warped product",
        "notes": _text(parsed.get("notes")),
        "aspectRatio": data.get("aspectRatio", "3:4"),
        "targetModel": model,
        "pageStyle": {"id": page_style.get("id", ""), "name": page_style.get("name", ""), "role": page_style.get("role", "")} if page_style else None,
        "reference_images": refs,
    }


async def run_draft_detail_copy(data: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    ctx.require_text_model()
    payload = {"user_idea": data.get("userIdea", ""), "current_copy": data.get("currentContent", ""), "selected_sections": data.get("selectedSections", []), "style_hint": data.get("styleHint", "")}
    try:
        parsed = await ctx.call_agent("detail-copy-drafter", _json(payload), retries=5, base_delay_ms=1200)
    except Exception as exc:
        parsed = {"content": getattr(exc, "raw_excerpt", ""), "lines": [], "notes": "模型输出异常，已返回原文片段" if getattr(exc, "raw_excerpt", "") else ""}
        ctx.mark_fallback()
    raw_lines = parsed.get("lines") if isinstance(parsed.get("lines"), list) else []
    content = _text(_pick(parsed, "content") or "\n".join(_text(item) for item in raw_lines))
    lines = [_text(item) for item in raw_lines if _text(item)] or [_text(item) for item in content.splitlines() if _text(item)]
    return {"content": content, "lines": lines, "notes": _text(parsed.get("notes"))}


async def run_draft_ppt_outline(data: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    ctx.require_text_model()
    payload = {"user_idea": data.get("userIdea", ""), "selected_pages": data.get("selectedPages", []), "style_hint": data.get("styleHint", ""), "existing_content": data.get("existingContent") or None}
    try:
        parsed = await ctx.call_agent("ppt-outline-drafter", _json(payload), retries=5, base_delay_ms=1200)
    except Exception as exc:
        parsed = {"content": getattr(exc, "raw_excerpt", ""), "outline": [], "notes": "模型输出异常，已返回原文片段" if getattr(exc, "raw_excerpt", "") else ""}
        ctx.mark_fallback()
    raw_outline = parsed.get("outline") if isinstance(parsed.get("outline"), list) else []
    content = _text(_pick(parsed, "content") or "\n".join(_text(item) for item in raw_outline))
    outline = [_text(item) for item in raw_outline if _text(item)] or [_text(item) for item in content.splitlines() if _text(item)]
    return {"content": content, "outline": outline, "notes": _text(parsed.get("notes"))}


async def run_design_brief(data: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    ctx.require_text_model()
    refs = await _references(data, ctx)
    payload = {"user_idea": _text(data.get("userIdea")), "outline": _text(data.get("outlineText")), "total_pages": data.get("totalPages", 1), "aspect_ratio": data.get("aspectRatio", "16:9"), "prompt_language": data.get("promptLanguage", "zh"), "reference_images": refs}
    try:
        brief = await ctx.call_agent("ppt-design-brief", _json(payload), retries=3, base_delay_ms=1500)
        return {"ok": True, "designBrief": brief, "referenceImages": refs, "briefError": ""}
    except Exception as exc:
        ctx.mark_fallback()
        return {"ok": False, "designBrief": None, "referenceImages": refs, "briefError": str(exc)}


async def run_inpaint_prompt(data: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    try:
        parsed = await ctx.call_agent("inpaint-prompt", _json({"original_prompt": data.get("originalPrompt", ""), "region_description": data.get("regionDescription", ""), "edit_instruction": data.get("editInstruction", "")}), retries=5, base_delay_ms=1500)
    except Exception as exc:
        parsed = {"edit_prompt": data.get("editInstruction") or getattr(exc, "raw_excerpt", ""), "preserve": ""}
        ctx.mark_fallback()
    return {"edit_prompt": _text(parsed.get("edit_prompt")), "preserve": _text(parsed.get("preserve"))}


def _normalize_target(value: Any) -> str:
    return "4K" if _text(value).upper() in {"4K", "4096", "3840"} else "2K"


async def run_upscale_repair(data: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    target = _normalize_target(data.get("targetSize"))
    try:
        parsed = await ctx.call_agent("upscale-repair-prompt", _json({"original_prompt": data.get("originalPrompt", ""), "target_size": target, "aspect_ratio": data.get("aspectRatio", "auto"), "extra_notes": data.get("extraNotes", "")}), retries=4, base_delay_ms=1500)
    except Exception:
        original_prompt = _text(data.get("originalPrompt"))
        notes = f" Additional request: {_text(data.get('extraNotes'))}." if _text(data.get("extraNotes")) else ""
        text_guard = (
            " Pay special attention to correct Chinese character glyphs (stroke integrity, no garbled shapes) "
            "and any numerals or latin text; render them exactly and legibly."
            if re.search(r"[\u4e00-\u9fa5]|[\"'“”]|\btext\b|\btitle\b|\bheadline\b", original_prompt, re.IGNORECASE)
            else ""
        )
        parsed = {
            "edit_prompt": (
                f"Upscale this image to {target} resolution while preserving the exact same subject, composition, "
                "layout, color palette, lighting and every visual element unchanged. Sharpen and cleanly re-render "
                "any text, characters, numerals, logos and fine details that were blurred or malformed in the "
                f"low-resolution source; do not invent, remove or relocate any element.{text_guard}{notes}"
            ),
            "preserve": "Keep subject, composition, layout, colors and all visual elements identical to the source.",
            "text_to_restore": "",
        }
        ctx.mark_fallback()
    return {"edit_prompt": _text(parsed.get("edit_prompt")), "preserve": _text(parsed.get("preserve")), "text_to_restore": _text(parsed.get("text_to_restore")), "target_size": target}


def _doctor_heuristic(models: List[str]) -> Dict[str, str]:
    def choose(keys: List[str]) -> str:
        return next((model for model in models if any(key in model.lower() for key in keys)), "")
    return {
        "visionModel": choose(["gpt-4o", "vision", "gemini", "claude", "qwen-vl"]),
        "textModel": choose(["gpt-4o", "gpt-4.1", "claude", "qwen", "glm", "deepseek"]),
        "imageGenModel": choose(["gpt-image", "dall", "seedance", "flux"]),
        "imageEditModel": choose(["nano-banana", "gpt-image", "dall"]),
    }


async def run_api_doctor(data: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    models = [_text(item) for item in data.get("models", []) if _text(item)][:300]
    if not ctx.has_text_model() or not models:
        return {"ok": not bool(data.get("pingError")), "heuristic": True, "baseUrl_fix": data.get("baseUrl", ""), "recommend": _doctor_heuristic(models), "issues": [data["pingError"]] if data.get("pingError") else []}
    payload = {"baseUrl": data.get("baseUrl", ""), "apiKey": "***", "models": models, "pingError": data.get("pingError"), "current": data.get("current", {})}
    try:
        parsed = await ctx.call_agent("api-doctor", _json(payload), retries=3, base_delay_ms=1000)
        return {"ok": True, **parsed}
    except Exception as exc:
        ctx.mark_fallback()
        return {"ok": False, "baseUrl_fix": data.get("baseUrl", ""), "recommend": _doctor_heuristic(models), "issues": [str(exc)], "heuristic": True}


SKILLS: Dict[str, SkillDefinition] = {}


def _register(definition: SkillDefinition) -> None:
    SKILLS[definition.id] = definition


for definition in [
    SkillDefinition("reference-analyze", "参考图解读", "逐张分析参考图并提取风格指纹和保留项。", ["reference-analyzer"], ReferenceAnalyzeInput, ReferenceAnalyzeOutput, run_reference_analyze),
    SkillDefinition("extract-style", "从参考图抽取风格模板", "按类型抽取通用、PPT 或详情页风格模板。", ["vision-analyzer", "template-distiller", "ppt-page-extractor", "detail-section-extractor"], ExtractStyleInput, ExtractStyleOutput, run_extract_style),
    SkillDefinition("compose-studio", "Studio 提示词合成", "模板、用户想法和参考图合成为生图提示词。", ["reference-analyzer", "prompt-composer"], ComposeStudioInput, ComposeOutput, run_compose_studio),
    SkillDefinition("compose-ppt", "PPT 页面合成", "按页面模板或自由模式生成 PPT 页面提示词。", ["reference-analyzer", "ppt-page-composer", "ppt-freeform-composer"], ComposePptInput, ComposeOutput, run_compose_ppt),
    SkillDefinition("compose-detail", "详情页段落合成", "按段落模板或自由模式生成详情页提示词。", ["reference-analyzer", "detail-section-composer", "detail-freeform-composer"], ComposeDetailInput, ComposeOutput, run_compose_detail),
    SkillDefinition("draft-detail-copy", "详情页文案草拟", "根据想法与段落角色生成详情页文案。", ["detail-copy-drafter"], DraftDetailCopyInput, DraftDetailCopyOutput, run_draft_detail_copy),
    SkillDefinition("draft-ppt-outline", "PPT 大纲草拟", "根据主题和页面类型生成 PPT 文案大纲。", ["ppt-outline-drafter"], DraftPptOutlineInput, DraftPptOutlineOutput, run_draft_ppt_outline),
    SkillDefinition("design-brief", "PPT 设计规范生成", "为整套 PPT 生成跨页视觉设计规范。", ["reference-analyzer", "ppt-design-brief"], DesignBriefInput, DesignBriefOutput, run_design_brief),
    SkillDefinition("inpaint-prompt", "改图指令合成", "生成局部修改图片的精炼指令。", ["inpaint-prompt"], InpaintPromptInput, InpaintPromptOutput, run_inpaint_prompt),
    SkillDefinition("upscale-repair", "放大修复指令合成", "生成 2K/4K 放大及文字细节修复指令。", ["upscale-repair-prompt"], UpscaleRepairInput, UpscaleRepairOutput, run_upscale_repair),
    SkillDefinition("api-doctor", "API 诊断", "校正 Base URL 并推荐各模型角色。", ["api-doctor"], ApiDoctorInput, ApiDoctorOutput, run_api_doctor),
]:
    _register(definition)
