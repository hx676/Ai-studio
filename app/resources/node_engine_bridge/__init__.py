"""SynCanvas result bridge loaded only inside the managed node engine."""

from __future__ import annotations

import json


class AnyType(str):
    def __ne__(self, _value):
        return False


ANY = AnyType("*")


def _serializable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_serializable(item) for item in value[:100]]
    if isinstance(value, dict):
        return {str(key): _serializable(item) for key, item in list(value.items())[:100]}
    return {"type": type(value).__name__, "preview": str(value)[:2000]}


class SynCanvasResult:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"value": (ANY,)}, "hidden": {"run_id": "UNIQUE_ID"}}

    RETURN_TYPES = ()
    FUNCTION = "collect"
    CATEGORY = "SynCanvas/internal"
    OUTPUT_NODE = True

    def collect(self, value, run_id=""):
        payload = json.dumps(_serializable(value), ensure_ascii=False)
        return {"ui": {"syncanvas": [{"run_id": run_id, "value": payload}]}}


NODE_CLASS_MAPPINGS = {"SynCanvasResult": SynCanvasResult}
NODE_DISPLAY_NAME_MAPPINGS = {"SynCanvasResult": "SynCanvas Result"}

