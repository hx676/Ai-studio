from typing import Any, Dict


class UppercaseNode:
    async def execute(self, context, state: Dict[str, Any], inputs: Dict[str, Any]):
        raw = inputs.get("text", state.get("text", ""))
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        if isinstance(raw, dict) and "value" in raw:
            raw = raw["value"]
        return {"outputs": {"text": {"kind": "text", "value": str(raw or "").upper()}}}


NODE_CLASS_MAPPINGS = {"uppercase": UppercaseNode}
NODE_DISPLAY_NAME_MAPPINGS = {"uppercase": "Uppercase"}
WEB_DIRECTORY = "./web"
