import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List

from fastapi import HTTPException

from app.models.common import GenerateRequest
from app.models.workflow import ComfyInstancesPayload, WorkflowConfig, WorkflowRunRequest, WorkflowUploadRequest
from app.services.provider_service import update_env_values


def legacy_module():
    from app import legacy

    return legacy


def __getattr__(name):
    return getattr(legacy_module(), name)


BUILTIN_WORKFLOWS = {"Z-Image.json", "Z-Image-Enhance.json", "2511.json", "klein-enhance.json", "Flux2-Klein.json", "upscale.json"}
CUSTOM_WORKFLOW_FOLDER = "custom"
LEGACY_CUSTOM_WORKFLOW_FOLDER = "自定义"
WORKFLOW_NAME_RE = re.compile(rf"^(?:(?:{CUSTOM_WORKFLOW_FOLDER}|{LEGACY_CUSTOM_WORKFLOW_FOLDER})/)?[a-zA-Z0-9_一-龥.\-]+\.json$")

def workflow_dir() -> str:
    return legacy_module().WORKFLOW_DIR


def current_comfyui_instances() -> List[str]:
    return list(getattr(legacy_module(), "COMFYUI_INSTANCES", None) or ["127.0.0.1:8188"])


def normalize_workflow_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def generate(req):
    return legacy_module().generate(req)


def normalize_comfyui_instance(value: str) -> str:
    s = str(value or "").strip()
    s = re.sub(r"^https?://", "", s, flags=re.IGNORECASE).rstrip("/")
    if not s:
        return ""
    if ":" not in s:
        raise HTTPException(status_code=400, detail=f"地址缺少端口号：{value}（应为 host:port，例如 127.0.0.1:8188）")
    host, _, port = s.rpartition(":")
    if not host or not port.isdigit():
        raise HTTPException(status_code=400, detail=f"地址不合法：{value}（应为 host:port，例如 127.0.0.1:8188）")
    return s


def normalize_comfyui_instances(values: List[str]) -> List[str]:
    cleaned = []
    for item in values:
        s = normalize_comfyui_instance(item)
        if s and s not in cleaned:
            cleaned.append(s)
    if not cleaned:
        raise HTTPException(status_code=400, detail="至少保留一个 ComfyUI 后端地址")
    return cleaned


def sync_comfyui_instances(cleaned: List[str]) -> None:
    lg = legacy_module()
    old_load = getattr(lg, "BACKEND_LOCAL_LOAD", {}) or {}
    next_load = {addr: int(old_load.get(addr, 0) or 0) for addr in cleaned}
    lg.COMFYUI_INSTANCES = list(cleaned)
    lg.COMFYUI_ADDRESS = cleaned[0]
    lg.BACKEND_LOCAL_LOAD = next_load

    try:
        from app.services import provider_service

        provider_service.COMFYUI_INSTANCES = list(cleaned)
        if hasattr(provider_service, "_AI_CONFIG_RESPONSE_CACHE"):
            provider_service._AI_CONFIG_RESPONSE_CACHE["expires"] = 0.0
    except Exception:
        pass


def workflow_path_from_name(name: str) -> str:
    if not WORKFLOW_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid workflow name")
    root = workflow_dir()
    path = os.path.abspath(os.path.join(root, *name.split("/")))
    workflow_root = os.path.abspath(root)
    if os.path.commonpath([workflow_root, path]) != workflow_root:
        raise HTTPException(status_code=400, detail="Invalid workflow name")
    return path

def workflow_config_path(name: str) -> str:
    return workflow_path_from_name(name).replace(".json", ".config.json")

def is_builtin_workflow(name: str) -> bool:
    return "/" not in name and os.path.basename(name) in BUILTIN_WORKFLOWS

def get_comfyui_instances():
    return {"instances": current_comfyui_instances()}

def save_comfyui_instances(payload: ComfyInstancesPayload):
    cleaned = normalize_comfyui_instances(payload.instances)
    # 写入 env 文件
    try:
        update_env_values({"COMFYUI_INSTANCES": ",".join(cleaned)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入 env 失败：{e}")
    sync_comfyui_instances(cleaned)
    return {"instances": current_comfyui_instances()}


def comfyui_status():
    instances = get_comfyui_instances()["instances"]
    items = []
    for addr in instances:
        started = time.monotonic()
        item = {
            "address": addr,
            "base_url": f"http://{addr}",
            "ok": False,
            "healthy": False,
            "queue_running": 0,
            "queue_pending": 0,
            "queue_total": 0,
            "latency_ms": None,
            "error": "",
        }
        try:
            req = urllib.request.Request(f"http://{addr}/queue", headers={"User-Agent": "SynCanvas-ComfyUI-Status"})
            with urllib.request.urlopen(req, timeout=2) as response:
                status = getattr(response, "status", 200)
                raw = response.read()
            if not (200 <= status < 400):
                item["error"] = f"HTTP {status}"
            else:
                payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
                running = payload.get("queue_running") or []
                pending = payload.get("queue_pending") or []
                item.update(
                    {
                        "ok": True,
                        "healthy": True,
                        "queue_running": len(running) if isinstance(running, list) else 0,
                        "queue_pending": len(pending) if isinstance(pending, list) else 0,
                    }
                )
                item["queue_total"] = item["queue_running"] + item["queue_pending"]
        except urllib.error.HTTPError as exc:
            item["error"] = f"HTTP {exc.code}"
        except Exception as exc:
            item["error"] = str(exc)
        item["latency_ms"] = int((time.monotonic() - started) * 1000)
        items.append(item)
    return {"ok": any(item["ok"] for item in items), "instances": items}

def list_workflows():
    root_dir = workflow_dir()
    if not os.path.isdir(root_dir):
        return {"workflows": []}
    items = []
    for root, dirs, files in os.walk(root_dir):
        if os.path.abspath(root) == os.path.abspath(root_dir):
            dirs[:] = [d for d in dirs if d in {CUSTOM_WORKFLOW_FOLDER, LEGACY_CUSTOM_WORKFLOW_FOLDER}]
        for fn in sorted(files):
            if not fn.endswith(".json") or fn.endswith(".config.json"):
                continue
            rel = os.path.relpath(os.path.join(root, fn), root_dir).replace("\\", "/")
            if is_builtin_workflow(rel):
                continue
            cfg = {}
            cfg_path = workflow_config_path(rel)
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f) or {}
                except Exception:
                    cfg = {}
            items.append({
                "name": rel,
                "title": cfg.get("title") or fn.replace(".json", ""),
                "builtin": False,
                "field_count": len(cfg.get("fields") or []),
            })
    items.sort(key=lambda item: (0 if item["name"].startswith(f"{CUSTOM_WORKFLOW_FOLDER}/") else 1, item["title"]))
    return {"workflows": items}

def get_workflow(name: str):
    if not WORKFLOW_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid workflow name")
    workflow_path = workflow_path_from_name(name)
    if not os.path.exists(workflow_path):
        raise HTTPException(status_code=404, detail="Workflow not found")
    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)
    cfg = {"title": name.replace(".json", ""), "fields": []}
    cfg_path = workflow_config_path(name)
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f) or cfg
        except Exception:
            pass
    return {"name": name, "workflow": workflow, "config": cfg, "builtin": is_builtin_workflow(name)}

def upload_workflow(payload: WorkflowUploadRequest):
    name = os.path.basename(payload.name.strip())
    if not name.endswith(".json"):
        name = name + ".json"
    if not WORKFLOW_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="工作流名称不合法，请使用中文/英文/数字/_-.")
    if not isinstance(payload.workflow, dict) or not payload.workflow:
        raise HTTPException(status_code=400, detail="工作流 JSON 为空")
    # 简单校验：是 API 格式（节点 id 为 key，含 class_type）
    sample = next(iter(payload.workflow.values()), None)
    if not isinstance(sample, dict) or "class_type" not in sample:
        raise HTTPException(status_code=400, detail="不是有效的 ComfyUI API 工作流 JSON（需包含 class_type）")
    custom_dir = os.path.join(workflow_dir(), CUSTOM_WORKFLOW_FOLDER)
    os.makedirs(custom_dir, exist_ok=True)
    stored_name = f"{CUSTOM_WORKFLOW_FOLDER}/{name}"
    path = workflow_path_from_name(stored_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload.workflow, f, ensure_ascii=False, indent=2)
    return {"name": stored_name}

def save_workflow_config(name: str, payload: WorkflowConfig):
    if not WORKFLOW_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid workflow name")
    workflow_path = workflow_path_from_name(name)
    if not os.path.exists(workflow_path):
        raise HTTPException(status_code=404, detail="Workflow not found")
    cfg_path = workflow_config_path(name)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(payload.dict(), f, ensure_ascii=False, indent=2)
    return {"config": payload.dict()}

def delete_workflow(name: str):
    if not WORKFLOW_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid workflow name")
    if is_builtin_workflow(name):
        raise HTTPException(status_code=400, detail="内置工作流不可删除")
    workflow_path = workflow_path_from_name(name)
    cfg_path = workflow_config_path(name)
    if not os.path.exists(workflow_path):
        raise HTTPException(status_code=404, detail="Workflow not found")
    os.remove(workflow_path)
    if os.path.exists(cfg_path):
        os.remove(cfg_path)
    return {"ok": True}

def run_workflow(name: str, payload: WorkflowRunRequest):
    if not WORKFLOW_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid workflow name")
    if not os.path.exists(workflow_path_from_name(name)):
        raise HTTPException(status_code=404, detail="Workflow not found")
    # 根据 config 的字段把值映射成 params 节点覆盖
    params: Dict[str, Dict[str, Any]] = {}
    for field in payload.config.fields:
        if not field.node or not field.input:
            continue
        if field.id in payload.fields:
            value = payload.fields[field.id]
            # 类型转换
            if field.type in ("number", "slider"):
                try:
                    value = float(value) if (field.step and field.step < 1) else int(float(value))
                except Exception:
                    pass
            elif field.type == "boolean":
                value = normalize_workflow_bool(value, False)
            elif field.type == "dropdown":
                # 下拉值如果看起来是数字（如 "1024" / "2048" / "0.8"），自动转成 int/float
                if isinstance(value, str):
                    s = value.strip()
                    try:
                        if s and ('.' in s or 'e' in s.lower()):
                            value = float(s)
                        elif s and (s.lstrip('-').isdigit()):
                            value = int(s)
                    except (ValueError, TypeError):
                        pass
            params.setdefault(field.node, {})[field.input] = value
    req = GenerateRequest(
        prompt="",
        workflow_json=name,
        params=params,
        type="workflow-test",
        client_id=payload.client_id or str(uuid.uuid4()),
    )
    return generate(req)
