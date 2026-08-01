import json
from typing import Any, Dict

from app.models.agent_skill import AIRunRequest
from app.services import skill_runtime


def _values(inputs: Dict[str, Any], key: str) -> list[Any]:
    raw = inputs.get(key)
    items = raw if isinstance(raw, list) else ([] if raw is None else [raw])
    result = []
    for item in items:
        if isinstance(item, dict) and "value" in item:
            result.append(item["value"])
        else:
            result.append(item)
    return result


def _first(inputs: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        values = _values(inputs, key)
        if values:
            return values[0]
    return default


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _summary_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        for key in ("content", "text", "prompt", "description", "summary"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return json.dumps(output, ensure_ascii=False) if output is not None else ""


def _sanitize_output(value: Any) -> Any:
    if isinstance(value, str):
        return None if value.lstrip().lower().startswith("data:") else value
    if isinstance(value, list):
        return [clean for item in value if (clean := _sanitize_output(item)) is not None]
    if isinstance(value, dict):
        sensitive = {"authorization", "api_key", "apikey", "password", "access_token", "refresh_token"}
        return {
            key: clean
            for key, item in value.items()
            if str(key).lower() not in sensitive
            if (clean := _sanitize_output(item)) is not None
        }
    return value


def _result(output: Any) -> Dict[str, Any]:
    safe_output = _sanitize_output(output)
    structured = safe_output if isinstance(safe_output, (dict, list)) else {"text": str(safe_output or "")}
    text = _summary_text(safe_output)
    images = structured.get("images", []) if isinstance(structured, dict) else []
    if isinstance(structured, dict) and isinstance(structured.get("image"), str):
        images = [structured["image"], *images]
    return {
        "outputs": {
            "text": {"kind": "text", "value": text},
            "structured": {"kind": "json", "value": structured},
            "images": [{"kind": "image", "value": item} for item in images if isinstance(item, str)],
        }
    }


def _migrate_agent_v1_to_v2(state: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "agentId": str(state.get("agentId") or state.get("agent_id") or ""),
        "providerId": str(state.get("providerId") or state.get("provider_id") or state.get("aiProvider") or ""),
        "textModel": str(state.get("textModel") or state.get("text_model") or ""),
        "visionModel": str(state.get("visionModel") or state.get("vision_model") or ""),
        "message": str(state.get("message") or state.get("userInput") or ""),
        "expectJson": bool(state.get("expectJson", state.get("expect_json", False))),
    }


class AgentNode:
    STATE_MIGRATIONS = {1: _migrate_agent_v1_to_v2}

    async def execute(self, context, state: Dict[str, Any], inputs: Dict[str, Any]):
        agent_id = str(state.get("agentId") or state.get("agent_id") or _first(inputs, "agent_id", default="")).strip()
        if not agent_id:
            raise ValueError("Agent node requires agentId")
        text_parts = [_text_value(state.get("message", ""))]
        text_parts.extend(_text_value(value) for value in _values(inputs, "text"))
        message = "\n\n".join(value for value in text_parts if value).strip()
        images = [str(value) for value in _values(inputs, "images") if str(value).strip()][:8]
        if not message and not images:
            raise ValueError("Agent input cannot be empty")
        context.progress(0.05, "Preparing Agent input")
        request = AIRunRequest(
            input={"message": message, "images": images},
            provider_id=str(state.get("providerId") or state.get("provider_id") or ""),
            text_model=str(state.get("textModel") or state.get("text_model") or ""),
            vision_model=str(state.get("visionModel") or state.get("vision_model") or ""),
            canvas_id=context.canvas_id,
            node_id=context.node_id,
        )
        runtime = skill_runtime.SkillContext(skill_runtime.resolve_settings(request))
        user: Any = message
        if images:
            user = [{"type": "text", "text": message or "Analyze these images."}]
            for url in images:
                user.append({"type": "image_url", "image_url": {"url": await runtime.prepare_image(url)}})
        context.progress(0.2, "Running Agent")
        output = await runtime.call_agent(agent_id, user, expect_json=bool(state.get("expectJson", False)))
        context.progress(0.95, "Finalizing output")
        return _result(output)


class SkillNode:
    async def execute(self, context, state: Dict[str, Any], inputs: Dict[str, Any]):
        skill_id = str(state.get("skillId") or state.get("skill_id") or _first(inputs, "skill_id", default="")).strip()
        if not skill_id:
            raise ValueError("AI 工作流节点缺少 skillId")
        raw_input = _first(inputs, "input", "structured", default={})
        if not isinstance(raw_input, dict):
            raw_input = {"text": str(raw_input or "")}
        request = AIRunRequest(
            input=raw_input,
            provider_id=str(state.get("providerId") or state.get("provider_id") or ""),
            text_model=str(state.get("textModel") or state.get("text_model") or ""),
            vision_model=str(state.get("visionModel") or state.get("vision_model") or ""),
            canvas_id=context.canvas_id,
            node_id=context.node_id,
        )
        runtime = skill_runtime.SkillContext(skill_runtime.resolve_settings(request))
        return _result(await runtime.run_skill(skill_id, raw_input))


NODE_CLASS_MAPPINGS = {"agent": AgentNode, "skill": SkillNode}
NODE_DISPLAY_NAME_MAPPINGS = {"agent": "Agent", "skill": "AI Workflow"}
WEB_DIRECTORY = "./web"
