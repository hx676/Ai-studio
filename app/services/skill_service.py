import json
import hashlib
import functools
import os
import re
import shutil
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Set
from threading import RLock

from fastapi import HTTPException

from app.core.json_store import atomic_write_json, path_lock
from app.core.paths import DATA_DIR
from app.models.agent_skill import SkillCreate, SkillUpdate


CUSTOM_SKILLS_FILE = Path(DATA_DIR) / "skills.json"
SKILL_AGENT_MIGRATION_FILE = Path(DATA_DIR) / "skill-agent-migration.json"
SKILL_AGENT_MIGRATION_LOG = Path(DATA_DIR) / "skill-agent-migration.log"
_MIGRATION_LOCK = RLock()
SKILL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
SKILL_FIELDS = ("id", "name", "description", "agentId", "instructions", "expectJson")


def _atomic_write(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def _serialize_skill_write(operation):
    @functools.wraps(operation)
    def wrapped(*args, **kwargs):
        with path_lock(CUSTOM_SKILLS_FILE):
            return operation(*args, **kwargs)
    return wrapped


def _backup_corrupt_file(path: Path) -> None:
    if not path.exists():
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}.bak"))


def _normalize_skill(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Skill item must be an object")
    skill_id = str(raw.get("id") or "").strip().lower()
    if not SKILL_ID_PATTERN.fullmatch(skill_id):
        raise ValueError("Skill ID 只能包含小写字母、数字、点、下划线和连字符")
    agent_id = str(raw.get("agentId") or "").strip()
    if not agent_id:
        raise ValueError(f"Skill {skill_id} 缺少 Agent")
    instructions = str(raw.get("instructions") or "").strip()
    if not instructions or len(instructions) > 50000:
        raise ValueError(f"Skill {skill_id} 的固定指令不合法")
    return {
        "id": skill_id,
        "name": str(raw.get("name") or skill_id).strip()[:120],
        "description": str(raw.get("description") or "").strip()[:500],
        "agentId": agent_id,
        "instructions": instructions,
        "expectJson": bool(raw.get("expectJson", False)),
    }


def load_custom_skills() -> List[Dict[str, Any]]:
    if not CUSTOM_SKILLS_FILE.exists():
        return []
    try:
        with CUSTOM_SKILLS_FILE.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, list):
            raise ValueError("skills.json must contain a list")
        skills = [_normalize_skill(item) for item in raw]
        ids = [item["id"] for item in skills]
        if len(ids) != len(set(ids)):
            raise ValueError("skills.json contains duplicate Skill IDs")
        return skills
    except Exception:
        _backup_corrupt_file(CUSTOM_SKILLS_FILE)
        _atomic_write(CUSTOM_SKILLS_FILE, [])
        return []


def custom_skill_map() -> Dict[str, Dict[str, Any]]:
    return {item["id"]: item for item in load_custom_skills()}



def _validate_target_agent(agent_id: str) -> None:
    from app.services import agent_service

    agent_service.get_agent(agent_id)


@_serialize_skill_write
def create_skill(payload: SkillCreate, reserved_ids: Set[str] | None = None) -> Dict[str, Any]:
    values = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    requested_id = str(values.pop("id", "") or "").strip().lower()
    skill_id = requested_id or f"skill-{uuid.uuid4().hex[:10]}"
    try:
        created = _normalize_skill({"id": skill_id, **values})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if skill_id in (reserved_ids or set()):
        raise HTTPException(status_code=409, detail=f"Skill ID 与内置 Skill 冲突：{skill_id}")
    skills = load_custom_skills()
    if any(item["id"] == skill_id for item in skills):
        raise HTTPException(status_code=409, detail=f"Skill ID 已存在：{skill_id}")
    _validate_target_agent(created["agentId"])
    skills.append(created)
    _atomic_write(CUSTOM_SKILLS_FILE, skills)
    return deepcopy(created)


@_serialize_skill_write
def update_skill(skill_id: str, payload: SkillUpdate) -> Dict[str, Any]:
    skills = load_custom_skills()
    index = next((idx for idx, item in enumerate(skills) if item["id"] == skill_id), -1)
    if index < 0:
        raise HTTPException(status_code=404, detail=f"自定义 Skill 不存在：{skill_id}")
    values = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    try:
        updated = _normalize_skill({"id": skill_id, **values})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _validate_target_agent(updated["agentId"])
    skills[index] = updated
    _atomic_write(CUSTOM_SKILLS_FILE, skills)
    return deepcopy(updated)


@_serialize_skill_write
def delete_skill(skill_id: str) -> None:
    skills = load_custom_skills()
    index = next((idx for idx, item in enumerate(skills) if item["id"] == skill_id), -1)
    if index < 0:
        raise HTTPException(status_code=404, detail=f"自定义 Skill 不存在：{skill_id}")
    skills.pop(index)
    _atomic_write(CUSTOM_SKILLS_FILE, skills)




def _migration_state() -> Dict[str, Any]:
    if not SKILL_AGENT_MIGRATION_FILE.exists():
        return {"schema_version": 1, "items": {}}
    try:
        with SKILL_AGENT_MIGRATION_FILE.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if isinstance(value, dict) and isinstance(value.get("items"), dict):
            return {"schema_version": 1, "items": value["items"]}
    except Exception:
        _backup_corrupt_file(SKILL_AGENT_MIGRATION_FILE)
    return {"schema_version": 1, "items": {}}


def _preset_agent_id(skill_id: str, occupied: Set[str]) -> str:
    base = f"preset-{skill_id}"[:80].rstrip("._-") or f"preset-{uuid.uuid4().hex[:10]}"
    if base not in occupied:
        return base
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, f"syncanvas-skill:{skill_id}").hex[:8]
    return f"{base[:71].rstrip('._-')}-{suffix}"


def _migration_fingerprint(skill: Dict[str, Any]) -> str:
    source = json.dumps({key: skill.get(key) for key in SKILL_FIELDS}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _migration_prompt(skill: Dict[str, Any], base: Dict[str, Any]) -> str:
    parts = [str(base.get("systemPrompt") or "").strip(), "## 任务预设", skill["instructions"]]
    if skill.get("expectJson"):
        parts.append("请只输出有效 JSON，不要使用 Markdown 代码块。")
    return "\n\n".join(part for part in parts if part)


def _migration_log(message: str) -> None:
    SKILL_AGENT_MIGRATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SKILL_AGENT_MIGRATION_LOG.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{int(time.time() * 1000)} {message}\n")
        handle.flush()
        os.fsync(handle.fileno())


def migrate_custom_skills_to_agents() -> Dict[str, str]:
    """Materialize legacy custom Skills as editable Agent presets once."""
    with _MIGRATION_LOCK:
        skills = load_custom_skills()
        if not skills:
            return {}
        from app.services import agent_service
        from app.models.agent_skill import AgentCreate

        state = _migration_state()
        records = state["items"]
        agents = agent_service.agent_map()
        occupied = set(agents)
        for skill in skills:
            previous = records.get(skill["id"]) if isinstance(records.get(skill["id"]), dict) else {}
            agent_id = str(previous.get("agent_id") or "")
            if agent_id and agent_id in occupied:
                continue
            base = agents.get(skill["agentId"], {})
            expected_prompt = _migration_prompt(skill, base)
            recovered = next((
                item for item in agents.values()
                if item.get("systemPrompt") == expected_prompt
                and item.get("name") == skill["name"]
                and str(item.get("id") or "").startswith(f"preset-{skill['id']}")
            ), None)
            if recovered:
                agent_id = recovered["id"]
                _migration_log(f"recovered skill={skill['id']} agent={agent_id}")
            else:
                if not agent_id:
                    agent_id = _preset_agent_id(skill["id"], occupied)
                created = agent_service.create_agent(AgentCreate(
                    id=agent_id,
                    name=skill["name"],
                    description=skill.get("description") or f"由旧版自定义 Skill {skill['id']} 迁移",
                    modelKind=str(base.get("modelKind") or "text"),
                    temperature=float(base.get("temperature", 0.5)),
                    systemPrompt=expected_prompt,
                ))
                agents[agent_id] = created
                occupied.add(agent_id)
                _migration_log(f"created skill={skill['id']} agent={agent_id}")
            records[skill["id"]] = {
                "agent_id": agent_id,
                "fingerprint": _migration_fingerprint(skill),
                "migrated_at": int(time.time() * 1000),
            }
            # Persist after every item. A crash after Agent creation is repaired
            # by the deterministic prompt match above without duplicating it.
            _atomic_write(SKILL_AGENT_MIGRATION_FILE, state)
        return {
            skill_id: str(item.get("agent_id") or "")
            for skill_id, item in records.items()
            if isinstance(item, dict) and item.get("agent_id")
        }
