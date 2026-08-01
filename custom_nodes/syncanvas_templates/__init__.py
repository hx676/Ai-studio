from typing import Any, Dict

from app.models.template_assets import TemplateAssetCreateRequest, TemplateAssetUpdateRequest
from app.services import template_asset_service


def _values(inputs: Dict[str, Any], key: str) -> list[Any]:
    raw = inputs.get(key)
    items = raw if isinstance(raw, list) else ([] if raw is None else [raw])
    return [item.get("value") if isinstance(item, dict) and "value" in item else item for item in items]


def _prompt(template: Dict[str, Any]) -> str:
    for key in (
        "stylePromptZh",
        "style_prompt_zh",
        "stylePromptEn",
        "style_prompt_en",
        "stylePrompt",
        "style_prompt",
        "prompt",
    ):
        value = template.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _outputs(template: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "outputs": {
            "text": {"kind": "text", "value": _prompt(template)},
        }
    }


class TemplateStoreNode:
    async def execute(self, context, state: Dict[str, Any], inputs: Dict[str, Any]):
        values = _values(inputs, "template")
        template = values[0] if values else state.get("template")
        if not isinstance(template, dict):
            raise ValueError("Template input must be a JSON object")
        images = [str(value) for value in _values(inputs, "images") if str(value).strip()][:8]
        template_id = str(state.get("templateId") or state.get("template_id") or "").strip()
        common = {
            "name": str(state.get("name") or "新模板"),
            "template": template,
            "thumbnail_url": images[0] if images else "",
            "reference_image_urls": images[1:] if images else [],
            "source_canvas_id": context.canvas_id,
            "source_node_id": context.node_id,
        }
        if template_id:
            response = template_asset_service.update_template_asset(template_id, TemplateAssetUpdateRequest(**common))
        else:
            response = template_asset_service.create_template_asset(TemplateAssetCreateRequest(**common))
        asset = response.get("item", response.get("asset", response))
        stored = response.get("template", template)
        result = _outputs(stored)
        result["asset"] = asset
        return result


class TemplateCallNode:
    async def execute(self, context, state: Dict[str, Any], inputs: Dict[str, Any]):
        template_id = str(state.get("templateId") or state.get("template_id") or "").strip()
        if not template_id:
            raise ValueError("Template call node requires templateId")
        response = template_asset_service.get_template_asset(template_id)
        template = response.get("template", {})
        return _outputs(template)


NODE_CLASS_MAPPINGS = {"store": TemplateStoreNode, "call": TemplateCallNode}
NODE_DISPLAY_NAME_MAPPINGS = {"store": "存模板", "call": "调用模板"}
WEB_DIRECTORY = "./web"
