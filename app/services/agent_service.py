import json
import functools
import re
import shutil
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List

from fastapi import HTTPException

from app.core.json_store import atomic_write_json, path_lock
from app.core.paths import DATA_DIR
from app.models.agent_skill import AgentCreate, AgentUpdate


RESOURCE_DIR = Path(__file__).resolve().parents[1] / "resources"
DEFAULT_AGENTS_FILE = RESOURCE_DIR / "agents.defaults.json"
SEED_AGENTS_FILE = RESOURCE_DIR / "agents.seed.json"
AGENTS_FILE = Path(DATA_DIR) / "agents.json"
AGENT_FIELDS = ("id", "name", "description", "modelKind", "temperature", "systemPrompt")
AGENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")


def _read_agent_resource(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, list):
        raise RuntimeError(f"Agent resource is not a list: {path}")
    return [_normalize_agent(item) for item in raw]


def _normalize_agent(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Agent item must be an object")
    agent_id = str(raw.get("id") or "").strip()
    if not AGENT_ID_PATTERN.fullmatch(agent_id):
        raise ValueError("Agent ID 只能包含小写字母、数字、点、下划线和连字符")
    model_kind = str(raw.get("modelKind") or "text").strip().lower()
    if model_kind not in {"text", "vision"}:
        raise ValueError(f"Agent {agent_id} has invalid modelKind")
    prompt = str(raw.get("systemPrompt") or "")
    if not prompt or len(prompt) > 50000:
        raise ValueError(f"Agent {agent_id} has invalid systemPrompt")
    try:
        temperature = float(raw.get("temperature", 0.5))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Agent {agent_id} has invalid temperature") from exc
    if temperature < 0 or temperature > 2:
        raise ValueError(f"Agent {agent_id} has invalid temperature")
    return {
        "id": agent_id,
        "name": str(raw.get("name") or agent_id).strip()[:120],
        "description": str(raw.get("description") or "").strip()[:500],
        "modelKind": model_kind,
        "temperature": temperature,
        "systemPrompt": prompt,
    }


def default_agents() -> List[Dict[str, Any]]:
    return _read_agent_resource(DEFAULT_AGENTS_FILE)


def seed_agents() -> List[Dict[str, Any]]:
    return _read_agent_resource(SEED_AGENTS_FILE)


def _atomic_write(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def _serialize_agent_write(operation):
    @functools.wraps(operation)
    def wrapped(*args, **kwargs):
        with path_lock(AGENTS_FILE):
            return operation(*args, **kwargs)
    return wrapped


def _backup_corrupt_file(path: Path) -> None:
    if not path.exists():
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}.bak")
    shutil.copy2(path, backup)


def load_agents() -> List[Dict[str, Any]]:
    if not AGENTS_FILE.exists():
        agents = seed_agents()
        _atomic_write(AGENTS_FILE, agents)
        return agents
    try:
        with AGENTS_FILE.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, list):
            raise ValueError("agents.json must contain a list")
        agents = [_normalize_agent(item) for item in raw]
        ids = [item["id"] for item in agents]
        if len(ids) != len(set(ids)):
            raise ValueError("agents.json contains duplicate Agent IDs")

        # New built-ins are merged on upgrade while user-created Agents remain untouched.
        current = {item["id"]: item for item in agents}
        seeds = {item["id"]: item for item in seed_agents()}
        defaults = default_agents()
        built_in_ids = {item["id"] for item in defaults}
        ordered = [deepcopy(current.get(item["id"]) or seeds.get(item["id"]) or item) for item in defaults]
        ordered.extend(item for item in agents if item["id"] not in built_in_ids)
        if ordered != agents:
            _atomic_write(AGENTS_FILE, ordered)
        return ordered
    except Exception:
        _backup_corrupt_file(AGENTS_FILE)
        agents = seed_agents()
        _atomic_write(AGENTS_FILE, agents)
        return agents


def agent_map() -> Dict[str, Dict[str, Any]]:
    return {item["id"]: item for item in load_agents()}


def get_agent(agent_id: str) -> Dict[str, Any]:
    agent = agent_map().get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent 不存在：{agent_id}")
    return deepcopy(agent)


def list_agents(used_by: Dict[str, Iterable[str]] | None = None) -> List[Dict[str, Any]]:
    links = used_by or {}
    built_in_ids = {item["id"] for item in default_agents()}
    result = []
    for item in load_agents():
        record = deepcopy(item)
        record["builtIn"] = item["id"] in built_in_ids
        record["editable"] = True
        record["usedBy"] = sorted(set(links.get(item["id"], [])))
        record["unbound"] = not bool(record["usedBy"])
        result.append(record)
    return result


@_serialize_agent_write
def create_agent(payload: AgentCreate) -> Dict[str, Any]:
    values = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    requested_id = str(values.pop("id", "") or "").strip().lower()
    agent_id = requested_id or f"agent-{uuid.uuid4().hex[:10]}"
    try:
        created = _normalize_agent({"id": agent_id, **values})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    agents = load_agents()
    if any(item["id"] == agent_id for item in agents):
        raise HTTPException(status_code=409, detail=f"Agent ID 已存在：{agent_id}")
    agents.append(created)
    _atomic_write(AGENTS_FILE, agents)
    return deepcopy(created)


@_serialize_agent_write
def update_agent(agent_id: str, payload: AgentUpdate) -> Dict[str, Any]:
    agents = load_agents()
    index = next((idx for idx, item in enumerate(agents) if item["id"] == agent_id), -1)
    if index < 0:
        raise HTTPException(status_code=404, detail=f"Agent 不存在：{agent_id}")
    values = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    updated = {"id": agent_id, **values}
    agents[index] = _normalize_agent(updated)
    _atomic_write(AGENTS_FILE, agents)
    return deepcopy(agents[index])


@_serialize_agent_write
def reset_agent(agent_id: str) -> Dict[str, Any]:
    defaults = {item["id"]: item for item in default_agents()}
    if agent_id not in defaults:
        if agent_id in agent_map():
            raise HTTPException(status_code=409, detail="自定义 Agent 没有内置默认值")
        raise HTTPException(status_code=404, detail=f"Agent 不存在：{agent_id}")
    agents = load_agents()
    index = next((idx for idx, item in enumerate(agents) if item["id"] == agent_id), -1)
    if index < 0:
        raise HTTPException(status_code=404, detail=f"Agent 不存在：{agent_id}")
    agents[index] = deepcopy(defaults[agent_id])
    _atomic_write(AGENTS_FILE, agents)
    return deepcopy(agents[index])


@_serialize_agent_write
def delete_agent(agent_id: str, used_by: Dict[str, Iterable[str]] | None = None) -> None:
    if agent_id in {item["id"] for item in default_agents()}:
        raise HTTPException(status_code=409, detail="内置 Agent 不能删除，可以恢复默认值")
    agents = load_agents()
    index = next((idx for idx, item in enumerate(agents) if item["id"] == agent_id), -1)
    if index < 0:
        raise HTTPException(status_code=404, detail=f"Agent 不存在：{agent_id}")
    dependencies = sorted(set((used_by or {}).get(agent_id, [])))
    if dependencies:
        raise HTTPException(status_code=409, detail=f"智能体正被 AI 工作流使用：{', '.join(dependencies)}")
    agents.pop(index)
    _atomic_write(AGENTS_FILE, agents)


@_serialize_agent_write
def import_agents(raw_agents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    defaults = default_agents()
    known_ids = {item["id"] for item in defaults}
    if not isinstance(raw_agents, list) or not raw_agents:
        raise HTTPException(status_code=400, detail="导入文件中没有 Agent")
    try:
        normalized = [_normalize_agent(item) for item in raw_agents]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ids = [item["id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="导入文件包含重复 Agent ID")
    if not known_ids.issubset(set(ids)):
        missing = sorted(known_ids - set(ids))
        detail = f"Agent 集合不兼容；缺少内置 Agent：{', '.join(missing)}"
        raise HTTPException(status_code=422, detail=detail)
    normalized_map = {item["id"]: item for item in normalized}
    ordered = [normalized_map[default["id"]] for default in defaults]
    ordered.extend(item for item in normalized if item["id"] not in known_ids)
    _atomic_write(AGENTS_FILE, ordered)
    return deepcopy(ordered)


def export_agents() -> List[Dict[str, Any]]:
    return [{key: deepcopy(item[key]) for key in AGENT_FIELDS} for item in load_agents()]
