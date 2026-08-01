import asyncio
import json
import os
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from app import legacy
from app.core.json_store import atomic_write_json
from app.core.paths import DATA_DIR
from app.core.run_retention import prune_run_history
from app.core.security import redact_sensitive_text
from app.models.agent_skill import AIRunRequest, AgentRunRequest
from app.services import agent_service, provider_service, skill_service
from app.services.skill_definitions import SKILLS, SkillDefinition
from app.services.skill_schemas import CustomSkillInput, CustomSkillOutput, dump_model, model_schema, validate_model


SETTINGS_FILE = Path(DATA_DIR) / "ai_runtime_settings.json"
RUN_DIR = Path(DATA_DIR) / "ai-runs"
MAX_CONCURRENCY = max(1, int(os.getenv("AI_RUN_MAX_CONCURRENCY", "4")))
MAX_QUEUED_RUNS = max(1, int(os.getenv("AI_RUN_MAX_QUEUE", "100")))
MAX_PERSISTED_TEXT = 200_000
ALLOWED_PROTOCOLS = {"openai", "apimart"}
TERMINAL_STATES = {"succeeded", "failed", "cancelled", "interrupted"}
SKILL_ENGLISH_NAMES = {
    "reference-analyze": "Reference Image Analysis",
    "extract-style": "Extract Style Template",
    "compose-studio": "Studio Prompt Composer",
    "compose-ppt": "PPT Page Composer",
    "compose-detail": "Detail Section Composer",
    "draft-detail-copy": "Detail Copy Draft",
    "draft-ppt-outline": "PPT Outline Draft",
    "design-brief": "PPT Design Brief",
    "inpaint-prompt": "Edit Instruction Composer",
    "upscale-repair": "Upscale & Repair Instructions",
    "api-doctor": "API Diagnostics",
}


class AgentCallError(RuntimeError):
    def __init__(self, message: str, raw_excerpt: str = "", status_code: int = 502):
        super().__init__(message)
        self.raw_excerpt = raw_excerpt[:2000]
        self.status_code = status_code


def _now_ms() -> int:
    return int(time.time() * 1000)


def _atomic_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def _clean_model_name(value: Any) -> str:
    model = str(value or "").strip()
    if len(model) > 240 or any(ord(char) < 32 or ord(char) == 127 for char in model):
        raise HTTPException(status_code=422, detail="模型名称不合法")
    return model


def _chat_providers() -> List[Dict[str, Any]]:
    return [
        provider for provider in provider_service.load_api_providers()
        if provider.get("enabled", True) and provider.get("chat_models")
        and provider_service.provider_protocol(provider) in ALLOWED_PROTOCOLS
    ]


def _provider(provider_id: str) -> Dict[str, Any]:
    providers = provider_service.load_api_providers()
    target = next((item for item in providers if item.get("id") == provider_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"API Provider 不存在：{provider_id}")
    protocol = provider_service.provider_protocol(target)
    if protocol not in ALLOWED_PROTOCOLS:
        raise HTTPException(status_code=409, detail=f"Provider {target.get('name') or provider_id} 不支持智能体/AI 工作流 Chat 协议")
    if not target.get("enabled", True):
        raise HTTPException(status_code=409, detail=f"Provider {target.get('name') or provider_id} 已停用")
    return target


def default_runtime_settings() -> Dict[str, str]:
    providers = _chat_providers()
    primary = next((item for item in providers if item.get("primary")), None)
    provider = primary or (providers[0] if providers else None)
    models = list(provider.get("chat_models") or []) if provider else []
    return {
        "provider_id": str(provider.get("id") or "") if provider else "",
        "text_model": str(models[0] if models else ""),
        "vision_model": str(models[0] if models else ""),
    }


def load_runtime_settings() -> Dict[str, str]:
    defaults = default_runtime_settings()
    if not SETTINGS_FILE.exists():
        return defaults
    try:
        with SETTINGS_FILE.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            return defaults
        return {
            "provider_id": str(raw.get("provider_id") or defaults["provider_id"]),
            "text_model": _clean_model_name(raw.get("text_model") or defaults["text_model"]),
            "vision_model": _clean_model_name(raw.get("vision_model") or defaults["vision_model"]),
        }
    except Exception:
        return defaults


def save_runtime_settings(value: Dict[str, Any]) -> Dict[str, str]:
    provider_id = str(value.get("provider_id") or "").strip()
    provider = _provider(provider_id) if provider_id else None
    models = list(provider.get("chat_models") or []) if provider else []
    text_model = _clean_model_name(value.get("text_model"))
    vision_model = _clean_model_name(value.get("vision_model"))
    if provider and text_model and text_model not in models:
        raise HTTPException(status_code=422, detail="文本模型不在所选 Provider 的聊天模型列表中")
    if provider and vision_model and vision_model not in models:
        raise HTTPException(status_code=422, detail="视觉模型不在所选 Provider 的聊天模型列表中")
    result = {"provider_id": provider_id, "text_model": text_model, "vision_model": vision_model}
    _atomic_json(SETTINGS_FILE, result)
    return result


def resolve_settings(request: AIRunRequest) -> Dict[str, str]:
    saved = load_runtime_settings()
    provider_id = str(request.provider_id or saved.get("provider_id") or "").strip()
    provider = _provider(provider_id) if provider_id else None
    models = list(provider.get("chat_models") or []) if provider else []
    text_model = _clean_model_name(request.text_model or saved.get("text_model") or (models[0] if models else ""))
    vision_model = _clean_model_name(request.vision_model or saved.get("vision_model") or (models[0] if models else ""))
    if provider and text_model and text_model not in models:
        raise HTTPException(status_code=422, detail=f"文本模型不属于 Provider：{text_model}")
    if provider and vision_model and vision_model not in models:
        raise HTTPException(status_code=422, detail=f"视觉模型不属于 Provider：{vision_model}")
    return {"provider_id": provider_id, "text_model": text_model, "vision_model": vision_model}


def used_by_agent() -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for skill in SKILLS.values():
        for agent_id in skill.agents:
            result.setdefault(agent_id, []).append(skill.id)
    for skill in skill_service.load_custom_skills():
        result.setdefault(skill["agentId"], []).append(skill["id"])
    return result


def list_skill_metadata() -> List[Dict[str, Any]]:
    migrated = skill_service.migrate_custom_skills_to_agents()
    result = []
    for skill in SKILLS.values():
        result.append({
            "id": skill.id,
            "name": skill.name,
            "nameEn": SKILL_ENGLISH_NAMES.get(skill.id, skill.name),
            "description": skill.description,
            "agents": list(skill.agents),
            "inputSchema": model_schema(skill.input_model),
            "outputSchema": model_schema(skill.output_model),
            "builtIn": True,
            "editable": False,
            "kind": "builtin",
        })
    for skill in skill_service.load_custom_skills():
        result.append({
            **deepcopy(skill),
            "agents": [skill["agentId"]],
            "inputSchema": model_schema(CustomSkillInput),
            "outputSchema": model_schema(CustomSkillOutput),
            "builtIn": False,
            "editable": True,
            "kind": "legacy",
            "hidden": True,
            "migratedAgentId": migrated.get(skill["id"], ""),
        })
    return result


def _try_json(value: Any) -> Any:
    if value is None:
        return None
    text = str(value)
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    body = fenced.group(1) if fenced else text
    try:
        return json.loads(body)
    except Exception:
        pass
    for opening, closing in (("{", "}"), ("[", "]")):
        start, end = body.find(opening), body.rfind(closing)
        if start >= 0 and end > start:
            try:
                return json.loads(body[start:end + 1])
            except Exception:
                pass
    return None


def _summary_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        for key in ("prompt", "edit_prompt", "content", "style_prompt_zh", "stylePromptZh", "text", "notes"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    try:
        return json.dumps(output, ensure_ascii=False, indent=2)
    except Exception:
        return str(output or "")


def _scrub_persisted(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith("data:"):
            return "[base64 image omitted]"
        return value[:MAX_PERSISTED_TEXT]
    if isinstance(value, list):
        return [_scrub_persisted(item) for item in value[:1000]]
    if isinstance(value, dict):
        return {
            str(key)[:120]: _scrub_persisted(item)
            for key, item in value.items()
            if str(key).lower() not in {"apikey", "api_key", "authorization", "raw", "raw_response"}
        }
    return value


class SkillContext:
    def __init__(self, settings: Dict[str, str]):
        self.settings = settings
        self.warnings: List[str] = []
        self.fallback_used = False
        self.last_model = ""

    def warn(self, message: str) -> None:
        if message and message not in self.warnings:
            self.warnings.append(message[:500])

    def mark_fallback(self) -> None:
        self.fallback_used = True

    def has_text_model(self) -> bool:
        return bool(self.settings.get("provider_id") and self.settings.get("text_model"))

    def require_text_model(self) -> None:
        if not self.has_text_model():
            raise ValueError("未配置智能体/AI 工作流文本模型")

    def require_vision_model(self) -> None:
        if not self.settings.get("provider_id") or not self.settings.get("vision_model"):
            raise ValueError("未配置智能体/AI 工作流视觉模型")

    async def prepare_image(self, value: str) -> str:
        url = str(value or "").strip()
        if not url:
            raise ValueError("图片地址为空")
        if url.startswith("data:") or url.startswith("http://") or url.startswith("https://"):
            return url
        if url.startswith("/output/") or url.startswith("/assets/"):
            prepared = await asyncio.to_thread(legacy.reference_to_data_url, {"url": url}, 1536)
            if not prepared:
                raise ValueError(f"无法读取本地图片：{url}")
            return prepared
        raise ValueError("图片必须是 /output、/assets、HTTP URL 或 Data URL")

    async def call_agent(
        self,
        agent_id: str,
        user: Any,
        expect_json: bool = True,
        retries: int = 5,
        base_delay_ms: int = 1500,
        timeout_seconds: Optional[float] = None,
    ) -> Any:
        agent = agent_service.get_agent(agent_id)
        model_kind = agent.get("modelKind") or "text"
        model = self.settings.get("vision_model" if model_kind == "vision" else "text_model", "")
        if not model:
            raise AgentCallError(f"{agent_id} 缺少{'视觉' if model_kind == 'vision' else '文本'}模型", status_code=409)
        provider_id = self.settings.get("provider_id", "")
        if not provider_id:
            raise AgentCallError("未配置智能体/AI 工作流 Provider", status_code=409)
        provider = _provider(provider_id)
        base, headers, resolved_model = legacy.resolve_chat_provider(provider_id, model, model if provider_id == "modelscope" else "")
        self.last_model = resolved_model
        messages = [
            {"role": "system", "content": agent.get("systemPrompt", "")},
            {"role": "user", "content": user},
        ]
        request_body: Dict[str, Any] = {
            "model": resolved_model,
            "temperature": agent.get("temperature", 0.5),
            "messages": messages,
        }
        if expect_json:
            request_body["response_format"] = {"type": "json_object"}
        if legacy.is_apimart_provider(provider):
            request_body["stream"] = False
        timeout = timeout_seconds or legacy.AI_REQUEST_TIMEOUT

        async def request(include_response_format: bool) -> str:
            body = dict(request_body)
            if not include_response_format:
                body.pop("response_format", None)
            last_error: Optional[Exception] = None
            for attempt in range(max(1, retries)):
                try:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        response = await client.post(f"{base}/chat/completions", headers=headers, json=body)
                    response.raise_for_status()
                    raw = response.json()
                    text = legacy.text_from_chat_response(raw).strip()
                    if not text:
                        raise AgentCallError(f"{agent_id} 返回空内容")
                    return text
                except asyncio.CancelledError:
                    raise
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    if exc.response.status_code < 500 and exc.response.status_code not in {408, 409, 429}:
                        break
                except Exception as exc:
                    last_error = exc
                if attempt + 1 < max(1, retries):
                    await asyncio.sleep((base_delay_ms / 1000) * (2 ** min(attempt, 3)))
            if isinstance(last_error, httpx.HTTPStatusError):
                body_text = last_error.response.text[:2000]
                raise AgentCallError(f"{agent_id} 上游错误 {last_error.response.status_code}", body_text, last_error.response.status_code)
            raise AgentCallError(f"{agent_id} 调用失败：{last_error}", str(last_error or ""))

        try:
            raw_text = await request(True)
        except AgentCallError:
            if not (expect_json and model_kind == "vision"):
                raise
            self.warn(f"{agent_id} 的视觉模型不接受 response_format，已自动降级")
            raw_text = await request(False)
        if not expect_json:
            return raw_text
        parsed = _try_json(raw_text)
        if parsed is None:
            raise AgentCallError(f"{agent_id} 未返回可解析 JSON", raw_text)
        return parsed

    async def run_skill(self, skill_id: str, raw_input: Dict[str, Any]) -> Dict[str, Any]:
        definition = SKILLS.get(skill_id)
        if definition:
            try:
                validated_input = dump_model(validate_model(definition.input_model, raw_input))
            except ValidationError as exc:
                raise ValueError(f"AI 工作流输入不合法：{exc}") from exc
            output = await definition.runner(validated_input, self)
            try:
                return dump_model(validate_model(definition.output_model, output))
            except ValidationError as exc:
                raise ValueError(f"AI 工作流输出不符合契约：{exc}") from exc

        custom = skill_service.custom_skill_map().get(skill_id)
        if not custom:
            raise ValueError(f"AI 工作流不存在：{skill_id}")
        try:
            validated_input = dump_model(validate_model(CustomSkillInput, raw_input))
        except ValidationError as exc:
            raise ValueError(f"旧版 Skill 输入不合法：{exc}") from exc

        images = [str(item).strip() for item in validated_input.pop("images", []) if str(item).strip()][:8]
        message = str(validated_input.pop("message", "") or "").strip()
        payload = {key: value for key, value in validated_input.items() if value not in (None, "", [], {})}
        parts = [custom["instructions"]]
        if message:
            parts.append(f"用户输入：\n{message}")
        if payload:
            parts.append("补充上下文：\n" + json.dumps(payload, ensure_ascii=False, indent=2))
        prompt = "\n\n".join(parts)
        user: Any = prompt
        if images:
            user = [{"type": "text", "text": prompt}]
            for url in images:
                user.append({"type": "image_url", "image_url": {"url": await self.prepare_image(url)}})
        output = await self.call_agent(custom["agentId"], user, expect_json=custom["expectJson"])
        normalized = output if isinstance(output, dict) else {"text": str(output or "")}
        try:
            return dump_model(validate_model(CustomSkillOutput, normalized))
        except ValidationError as exc:
            raise ValueError(f"旧版 Skill 输出不符合契约：{exc}") from exc


class AIRunManager:
    def __init__(self) -> None:
        self.records: Dict[str, Dict[str, Any]] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.semaphore: Optional[asyncio.Semaphore] = None

    def _semaphore(self) -> asyncio.Semaphore:
        if self.semaphore is None:
            self.semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        return self.semaphore

    def _persist(self, record: Dict[str, Any]) -> None:
        _atomic_json(RUN_DIR / f"{record['run_id']}.json", _scrub_persisted(record))

    def recover(self) -> None:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        for path in RUN_DIR.glob("*.json"):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    record = json.load(handle)
                if record.get("status") not in TERMINAL_STATES:
                    record["status"] = "interrupted"
                    record["error"] = "主应用重启，运行已中断"
                    record["completed_at"] = _now_ms()
                    if record.get("started_at"):
                        record["duration_ms"] = record["completed_at"] - record["started_at"]
                    _atomic_json(path, record)
                self.records[record["run_id"]] = record
            except Exception:
                continue
        prune_run_history(RUN_DIR, self.records, TERMINAL_STATES)

    def get(self, run_id: str) -> Dict[str, Any]:
        record = self.records.get(run_id)
        if not record:
            path = RUN_DIR / f"{run_id}.json"
            if path.exists():
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        record = json.load(handle)
                    self.records[run_id] = record
                except Exception:
                    record = None
        if not record:
            raise HTTPException(status_code=404, detail="运行记录不存在")
        return deepcopy(record)

    def submit(self, kind: str, target_id: str, request: AIRunRequest, expect_json: bool = False) -> Dict[str, Any]:
        pending = sum(1 for record in self.records.values() if record.get("status") not in TERMINAL_STATES)
        if pending >= MAX_QUEUED_RUNS:
            raise HTTPException(status_code=429, detail=f"AI 任务队列已满（最多 {MAX_QUEUED_RUNS} 个）")
        if kind == "agent":
            agent_service.get_agent(target_id)
        else:
            definition = SKILLS.get(target_id)
            custom = skill_service.custom_skill_map().get(target_id)
            if not definition and not custom:
                raise HTTPException(status_code=404, detail=f"AI 工作流不存在：{target_id}")
            try:
                validate_model(definition.input_model if definition else CustomSkillInput, request.input)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=json.loads(exc.json())) from exc
        settings = resolve_settings(request)
        run_id = uuid.uuid4().hex
        record = {
            "run_id": run_id,
            "kind": kind,
            "target_id": target_id,
            "status": "queued",
            "canvas_id": request.canvas_id,
            "node_id": request.node_id,
            "created_at": _now_ms(),
            "started_at": None,
            "completed_at": None,
            "duration_ms": None,
            "output_text": "",
            "output": None,
            "model": "",
            "warnings": [],
            "fallback_used": False,
            "error": "",
        }
        self.records[run_id] = record
        self._persist(record)
        task = asyncio.create_task(self._execute(run_id, kind, target_id, deepcopy(request.input), settings, expect_json))
        self.tasks[run_id] = task
        task.add_done_callback(lambda _task, rid=run_id: self.tasks.pop(rid, None))
        return deepcopy(record)

    async def _execute(self, run_id: str, kind: str, target_id: str, raw_input: Dict[str, Any], settings: Dict[str, str], expect_json: bool) -> None:
        record = self.records[run_id]
        try:
            async with self._semaphore():
                record["status"] = "running"
                record["started_at"] = _now_ms()
                self._persist(record)
                context = SkillContext(settings)
                if kind == "skill":
                    output = await context.run_skill(target_id, raw_input)
                else:
                    message = str(raw_input.get("message") or raw_input.get("text") or "").strip()
                    images = [str(item) for item in raw_input.get("images", []) if str(item).strip()][:8]
                    if not message and not images:
                        raise ValueError("Agent 输入不能为空")
                    user: Any = message
                    if images:
                        user = [{"type": "text", "text": message or "请分析这些图片。"}]
                        for url in images:
                            user.append({"type": "image_url", "image_url": {"url": await context.prepare_image(url)}})
                    result = await context.call_agent(target_id, user, expect_json=expect_json)
                    output = result if isinstance(result, dict) else {"text": result}
                record["output"] = _scrub_persisted(output)
                record["output_text"] = _summary_text(output)[:MAX_PERSISTED_TEXT]
                record["model"] = context.last_model
                record["warnings"] = context.warnings
                record["fallback_used"] = context.fallback_used or bool(isinstance(output, dict) and output.get("fallbackUsed"))
                record["status"] = "succeeded"
        except asyncio.CancelledError:
            record["status"] = "cancelled"
            record["error"] = "运行已取消"
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = redact_sensitive_text(exc)[:5000]
        finally:
            record["completed_at"] = _now_ms()
            if record.get("started_at"):
                record["duration_ms"] = record["completed_at"] - record["started_at"]
            self._persist(record)
            self.tasks.pop(run_id, None)
            prune_run_history(RUN_DIR, self.records, TERMINAL_STATES)

    def cancel(self, run_id: str) -> Dict[str, Any]:
        record = self.records.get(run_id)
        if not record:
            return self.get(run_id)
        if record.get("status") in TERMINAL_STATES:
            return deepcopy(record)
        task = self.tasks.get(run_id)
        record["status"] = "cancelled"
        record["error"] = "运行已取消"
        record["completed_at"] = _now_ms()
        if record.get("started_at"):
            record["duration_ms"] = record["completed_at"] - record["started_at"]
        self._persist(record)
        if task:
            task.cancel()
        return deepcopy(record)


run_manager = AIRunManager()


def submit_agent_run(agent_id: str, request: AgentRunRequest) -> Dict[str, Any]:
    return run_manager.submit("agent", agent_id, request, expect_json=request.expect_json)


def submit_skill_run(skill_id: str, request: AIRunRequest) -> Dict[str, Any]:
    return run_manager.submit("skill", skill_id, request)
