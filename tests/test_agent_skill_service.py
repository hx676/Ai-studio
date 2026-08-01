import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from app.main import app
from app.models.agent_skill import AIRunRequest, AgentCreate, AgentUpdate, SkillCreate, SkillUpdate
from app.services import agent_service, skill_runtime, skill_service
from app.services.skill_definitions import SKILLS


class AgentServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_root = Path(tempfile.mkdtemp(prefix="syncanvas-agents-"))
        self.original_file = agent_service.AGENTS_FILE
        agent_service.AGENTS_FILE = self.temp_root / "agents.json"

    def tearDown(self):
        agent_service.AGENTS_FILE = self.original_file
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def test_seed_contains_all_agents_and_current_prompt_overrides(self):
        agents = agent_service.load_agents()
        self.assertEqual(17, len(agents))
        defaults = {item["id"]: item for item in agent_service.default_agents()}
        current = {item["id"]: item for item in agents}
        self.assertNotEqual(defaults["ppt-page-composer"]["systemPrompt"], current["ppt-page-composer"]["systemPrompt"])
        self.assertNotEqual(defaults["ppt-freeform-composer"]["systemPrompt"], current["ppt-freeform-composer"]["systemPrompt"])

    def test_update_reset_and_import_are_atomic(self):
        original = agent_service.get_agent("upscaler")
        updated = agent_service.update_agent("upscaler", AgentUpdate(
            name="放大测试",
            description="test",
            modelKind="text",
            temperature=0.7,
            systemPrompt="Return a useful answer.",
        ))
        self.assertEqual("放大测试", updated["name"])
        reset = agent_service.reset_agent("upscaler")
        self.assertEqual(original["id"], reset["id"])
        self.assertEqual(agent_service.default_agents()[6]["systemPrompt"], reset["systemPrompt"])
        imported = agent_service.import_agents(agent_service.seed_agents())
        self.assertEqual(17, len(imported))
        self.assertFalse(agent_service.AGENTS_FILE.with_suffix(".json.tmp").exists())

    def test_corrupt_file_recovers_to_seed_and_keeps_backup(self):
        agent_service.AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        agent_service.AGENTS_FILE.write_text("not json", encoding="utf-8")
        agents = agent_service.load_agents()
        self.assertEqual(17, len(agents))
        self.assertTrue(list(self.temp_root.glob("agents.corrupt-*.json.bak")))

    def test_custom_agents_survive_reload_and_missing_builtins_are_migrated(self):
        custom = agent_service.create_agent(AgentCreate(
            id="my-helper",
            name="My helper",
            description="custom",
            modelKind="text",
            temperature=0.4,
            systemPrompt="Help with the task.",
        ))
        stored = json.loads(agent_service.AGENTS_FILE.read_text(encoding="utf-8"))
        stored = [item for item in stored if item["id"] != "upscaler"]
        agent_service.AGENTS_FILE.write_text(json.dumps(stored), encoding="utf-8")

        migrated = agent_service.load_agents()
        self.assertEqual(18, len(migrated))
        self.assertIn(custom["id"], {item["id"] for item in migrated})
        self.assertIn("upscaler", {item["id"] for item in migrated})
        listed = next(item for item in agent_service.list_agents() if item["id"] == custom["id"])
        self.assertFalse(listed["builtIn"])

    def test_custom_agent_delete_and_builtin_protection(self):
        created = agent_service.create_agent(AgentCreate(
            name="Temporary",
            modelKind="text",
            temperature=0.5,
            systemPrompt="Temporary helper.",
        ))
        with self.assertRaisesRegex(Exception, "正被 AI 工作流使用"):
            agent_service.delete_agent(created["id"], {created["id"]: ["custom-skill"]})
        agent_service.delete_agent(created["id"])
        self.assertNotIn(created["id"], {item["id"] for item in agent_service.load_agents()})
        with self.assertRaisesRegex(Exception, "内置 Agent 不能删除"):
            agent_service.delete_agent("upscaler")


class CustomSkillServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_root = Path(tempfile.mkdtemp(prefix="syncanvas-skills-"))
        self.original_agents_file = agent_service.AGENTS_FILE
        self.original_skills_file = skill_service.CUSTOM_SKILLS_FILE
        self.original_migration_file = skill_service.SKILL_AGENT_MIGRATION_FILE
        self.original_migration_log = skill_service.SKILL_AGENT_MIGRATION_LOG
        agent_service.AGENTS_FILE = self.temp_root / "agents.json"
        skill_service.CUSTOM_SKILLS_FILE = self.temp_root / "skills.json"
        skill_service.SKILL_AGENT_MIGRATION_FILE = self.temp_root / "skill-agent-migration.json"
        skill_service.SKILL_AGENT_MIGRATION_LOG = self.temp_root / "skill-agent-migration.log"

    async def asyncTearDown(self):
        agent_service.AGENTS_FILE = self.original_agents_file
        skill_service.CUSTOM_SKILLS_FILE = self.original_skills_file
        skill_service.SKILL_AGENT_MIGRATION_FILE = self.original_migration_file
        skill_service.SKILL_AGENT_MIGRATION_LOG = self.original_migration_log
        shutil.rmtree(self.temp_root, ignore_errors=True)

    async def test_custom_skill_crud_metadata_and_execution(self):
        created = skill_service.create_skill(SkillCreate(
            id="copy-polisher",
            name="文案润色",
            description="Polish copy",
            agentId="prompt-composer",
            instructions="润色用户提供的文案。",
            expectJson=False,
        ), set(SKILLS))
        self.assertEqual("copy-polisher", created["id"])
        updated = skill_service.update_skill("copy-polisher", SkillUpdate(
            name="文案精修",
            description="Polish copy carefully",
            agentId="prompt-composer",
            instructions="精修用户提供的文案，保持原意。",
            expectJson=True,
        ))
        self.assertTrue(updated["expectJson"])

        metadata = next(item for item in skill_runtime.list_skill_metadata() if item["id"] == "copy-polisher")
        self.assertFalse(metadata["builtIn"])
        self.assertTrue(metadata["hidden"])
        self.assertEqual("legacy", metadata["kind"])
        self.assertIn("message", metadata["inputSchema"]["properties"])
        context = skill_runtime.SkillContext({"provider_id": "test", "text_model": "text", "vision_model": "vision"})
        with mock.patch.object(context, "call_agent", new=mock.AsyncMock(return_value={"result": "done"})) as call:
            output = await context.run_skill("copy-polisher", {"message": "original", "context": {"tone": "formal"}})
        self.assertEqual("done", output["result"])
        self.assertIn("精修用户提供的文案", call.await_args.args[1])
        self.assertTrue(call.await_args.kwargs["expect_json"])

        skill_service.delete_skill("copy-polisher")
        self.assertEqual([], skill_service.load_custom_skills())

    async def test_legacy_custom_skill_migrates_once_to_editable_agent_preset(self):
        skill_service.create_skill(SkillCreate(
            id="copy-polisher",
            name="文案润色",
            description="迁移测试",
            agentId="prompt-composer",
            instructions="保留原意并润色文案。",
            expectJson=True,
        ), set(SKILLS))
        original_skills = skill_service.CUSTOM_SKILLS_FILE.read_text(encoding="utf-8")

        first = skill_service.migrate_custom_skills_to_agents()
        migrated_id = first["copy-polisher"]
        migrated = agent_service.get_agent(migrated_id)
        self.assertEqual("文案润色", migrated["name"])
        self.assertIn(agent_service.get_agent("prompt-composer")["systemPrompt"], migrated["systemPrompt"])
        self.assertIn("保留原意并润色文案。", migrated["systemPrompt"])
        self.assertIn("只输出有效 JSON", migrated["systemPrompt"])

        count_after_first = len(agent_service.load_agents())
        second = skill_service.migrate_custom_skills_to_agents()
        self.assertEqual(first, second)
        self.assertEqual(count_after_first, len(agent_service.load_agents()))
        self.assertEqual(original_skills, skill_service.CUSTOM_SKILLS_FILE.read_text(encoding="utf-8"))
        self.assertTrue(skill_service.SKILL_AGENT_MIGRATION_FILE.exists())

    async def test_skill_ids_cannot_shadow_builtins(self):
        with self.assertRaisesRegex(Exception, "内置 Skill 冲突"):
            skill_service.create_skill(SkillCreate(
                id="api-doctor",
                name="Shadow",
                agentId="api-doctor",
                instructions="Do something.",
            ), set(SKILLS))


class FakeContext:
    def __init__(self):
        self.fallback_used = False
        self.warnings = []

    def require_text_model(self):
        return None

    def require_vision_model(self):
        return None

    def has_text_model(self):
        return True

    def mark_fallback(self):
        self.fallback_used = True

    def warn(self, message):
        self.warnings.append(message)

    async def prepare_image(self, value):
        return value

    async def run_skill(self, skill_id, value):
        if skill_id == "reference-analyze":
            return {"items": []}
        return await SKILLS[skill_id].runner(value, self)

    async def call_agent(self, agent_id, user, **kwargs):
        if agent_id == "prompt-composer":
            return {"prompt": "final prompt", "negative": "bad text", "notes": "ok"}
        if agent_id == "ppt-design-brief":
            return {"palette": ["#000000"], "layout": "grid"}
        if agent_id == "api-doctor":
            return {"baseUrl_fix": "https://example.com/v1", "recommend": {"textModel": "gpt-4o"}, "issues": []}
        if agent_id == "upscale-repair-prompt":
            return {"edit_prompt": "upscale", "preserve": "everything", "text_to_restore": "title"}
        if agent_id == "inpaint-prompt":
            return {"edit_prompt": "replace sky", "preserve": "subject"}
        if agent_id == "detail-copy-drafter":
            return {"content": "A\nB", "lines": ["A", "B"], "notes": ""}
        if agent_id == "ppt-outline-drafter":
            return {"content": "# A", "outline": ["A"], "notes": ""}
        if agent_id in {"ppt-page-composer", "ppt-freeform-composer", "detail-section-composer", "detail-freeform-composer"}:
            return {"prompt": "page prompt", "negative": "bad", "notes": ""}
        if agent_id == "reference-analyzer":
            return {"description": "image", "style_fingerprint": "clean vector", "keep_strict": []}
        if agent_id == "vision-analyzer":
            return {"palette": ["#fff"], "mood": "clean", "composition": "centered"}
        if agent_id == "template-distiller":
            return {"style_prompt_en": "clean vector", "style_prompt_zh": "简洁矢量", "negative_prompt": "noise"}
        if agent_id in {"ppt-page-extractor", "detail-section-extractor"}:
            return {"pageStyles": [{"name": "Cover", "role": "cover", "style_prompt_en": "clean", "source_image_index": 0}]}
        raise AssertionError(f"unexpected agent: {agent_id}")


class SkillContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_registry_contains_all_eleven_skills_and_real_schemas(self):
        metadata = skill_runtime.list_skill_metadata()
        self.assertEqual(11, len(metadata))
        self.assertEqual(11, len(SKILLS))
        compose = next(item for item in metadata if item["id"] == "compose-studio")
        self.assertIn("userIdea", compose["inputSchema"]["properties"])
        self.assertIn("prompt", compose["outputSchema"]["properties"])

    async def test_representative_skill_outputs_match_contracts(self):
        ctx = FakeContext()
        studio = await SKILLS["compose-studio"].runner({"userIdea": "test", "aspectRatio": "1:1"}, ctx)
        self.assertEqual("final prompt", studio["prompt"])
        brief = await SKILLS["design-brief"].runner({"userIdea": "deck", "totalPages": 3}, ctx)
        self.assertTrue(brief["ok"])
        repair = await SKILLS["upscale-repair"].runner({"targetSize": "4096"}, ctx)
        self.assertEqual("4K", repair["target_size"])
        doctor = await SKILLS["api-doctor"].runner({"baseUrl": "https://example.com", "models": ["gpt-4o"]}, ctx)
        self.assertTrue(doctor["ok"])

    async def test_all_skills_accept_minimal_contract_input(self):
        ctx = FakeContext()
        samples = {
            "reference-analyze": {"images": []},
            "extract-style": {"images": ["data:image/png;base64,AA=="], "category": "illustration"},
            "compose-studio": {"userIdea": "idea"},
            "compose-ppt": {"userContent": "title"},
            "compose-detail": {"userContent": "copy"},
            "draft-detail-copy": {"userIdea": "product"},
            "draft-ppt-outline": {"userIdea": "deck"},
            "design-brief": {"userIdea": "deck"},
            "inpaint-prompt": {"editInstruction": "change sky"},
            "upscale-repair": {"targetSize": "2K"},
            "api-doctor": {"baseUrl": "https://example.com", "models": ["gpt-4o"]},
        }
        for skill_id, value in samples.items():
            with self.subTest(skill=skill_id):
                result = await SKILLS[skill_id].runner(value, ctx)
                self.assertIsInstance(result, dict)

    async def test_upscale_fallback_preserves_text_repair_rule(self):
        ctx = FakeContext()

        async def fail_agent(*args, **kwargs):
            raise RuntimeError("upstream unavailable")

        ctx.call_agent = fail_agent
        result = await SKILLS["upscale-repair"].runner({
            "originalPrompt": "海报 title 文字",
            "targetSize": "4096",
            "extraNotes": "keep logo",
        }, ctx)
        self.assertEqual("4K", result["target_size"])
        self.assertIn("correct Chinese character glyphs", result["edit_prompt"])
        self.assertIn("Additional request: keep logo", result["edit_prompt"])
        self.assertTrue(ctx.fallback_used)


class RunManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_root = Path(tempfile.mkdtemp(prefix="syncanvas-ai-runs-"))
        self.original_run_dir = skill_runtime.RUN_DIR
        skill_runtime.RUN_DIR = self.temp_root / "runs"

    async def asyncTearDown(self):
        skill_runtime.RUN_DIR = self.original_run_dir
        shutil.rmtree(self.temp_root, ignore_errors=True)

    async def test_async_run_succeeds_and_persists_sanitized_output(self):
        manager = skill_runtime.AIRunManager()

        async def fake_run_skill(context, skill_id, raw_input):
            await asyncio.sleep(0.01)
            return {"content": "done", "image": "data:image/png;base64,secret"}

        request = AIRunRequest(input={"baseUrl": "", "models": []})
        with mock.patch.object(skill_runtime.SkillContext, "run_skill", new=fake_run_skill):
            created = manager.submit("skill", "api-doctor", request)
            await manager.tasks[created["run_id"]]
        record = manager.get(created["run_id"])
        self.assertEqual("succeeded", record["status"])
        persisted = json.loads((skill_runtime.RUN_DIR / f"{created['run_id']}.json").read_text("utf-8"))
        self.assertEqual("[base64 image omitted]", persisted["output"]["image"])

    async def test_agent_run_exposes_clean_output_text(self):
        manager = skill_runtime.AIRunManager()

        async def fake_call_agent(context, agent_id, user, **kwargs):
            return "agent answer"

        request = AIRunRequest(input={"message": "hello"})
        with mock.patch.object(skill_runtime.SkillContext, "call_agent", new=fake_call_agent):
            created = manager.submit("agent", "upscaler", request)
            await manager.tasks[created["run_id"]]
        record = manager.get(created["run_id"])
        self.assertEqual("succeeded", record["status"])
        self.assertEqual("agent answer", record["output_text"])
        self.assertEqual({"text": "agent answer"}, record["output"])

    async def test_cancel_and_recover_interrupted_runs(self):
        manager = skill_runtime.AIRunManager()

        async def slow_run(context, skill_id, raw_input):
            await asyncio.sleep(60)
            return {"content": "late"}

        request = AIRunRequest(input={"baseUrl": "", "models": []})
        with mock.patch.object(skill_runtime.SkillContext, "run_skill", new=slow_run):
            created = manager.submit("skill", "api-doctor", request)
            cancelled = manager.cancel(created["run_id"])
            self.assertEqual("cancelled", cancelled["status"])
            task = manager.tasks.get(created["run_id"])
            if task:
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual("cancelled", manager.get(created["run_id"])["status"])

        interrupted = {
            "run_id": "old-run", "kind": "skill", "target_id": "api-doctor", "status": "running",
            "created_at": 1, "started_at": 2, "completed_at": None, "output": None,
        }
        skill_runtime.RUN_DIR.mkdir(parents=True, exist_ok=True)
        (skill_runtime.RUN_DIR / "old-run.json").write_text(json.dumps(interrupted), encoding="utf-8")
        recovered = skill_runtime.AIRunManager()
        recovered.recover()
        self.assertEqual("interrupted", recovered.get("old-run")["status"])

    async def test_concurrency_overflow_queues_and_runs_are_isolated(self):
        manager = skill_runtime.AIRunManager()
        release = asyncio.Event()
        started = []

        async def controlled_run(context, skill_id, raw_input):
            started.append(raw_input["baseUrl"])
            await release.wait()
            if raw_input["baseUrl"] == "fail":
                raise RuntimeError("isolated failure")
            return {"content": raw_input["baseUrl"]}

        requests = [
            AIRunRequest(input={"baseUrl": "fail" if index == 0 else f"run-{index}", "models": []})
            for index in range(skill_runtime.MAX_CONCURRENCY + 2)
        ]
        with mock.patch.object(skill_runtime.SkillContext, "run_skill", new=controlled_run):
            created = [manager.submit("skill", "api-doctor", request) for request in requests]
            await asyncio.sleep(0.02)
            statuses = [manager.get(item["run_id"])["status"] for item in created]
            self.assertEqual(skill_runtime.MAX_CONCURRENCY, statuses.count("running"))
            self.assertEqual(2, statuses.count("queued"))
            release.set()
            await asyncio.gather(*(manager.tasks[item["run_id"]] for item in created))

        final = [manager.get(item["run_id"]) for item in created]
        self.assertEqual(1, sum(item["status"] == "failed" for item in final))
        self.assertEqual(len(final) - 1, sum(item["status"] == "succeeded" for item in final))


class AgentSkillApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_root = Path(tempfile.mkdtemp(prefix="syncanvas-ai-api-"))
        self.original_agents_file = agent_service.AGENTS_FILE
        self.original_settings_file = skill_runtime.SETTINGS_FILE
        self.original_skills_file = skill_service.CUSTOM_SKILLS_FILE
        self.original_migration_file = skill_service.SKILL_AGENT_MIGRATION_FILE
        self.original_migration_log = skill_service.SKILL_AGENT_MIGRATION_LOG
        self.original_run_dir = skill_runtime.RUN_DIR
        self.original_run_manager = skill_runtime.run_manager
        agent_service.AGENTS_FILE = self.temp_root / "agents.json"
        skill_service.CUSTOM_SKILLS_FILE = self.temp_root / "skills.json"
        skill_service.SKILL_AGENT_MIGRATION_FILE = self.temp_root / "skill-agent-migration.json"
        skill_service.SKILL_AGENT_MIGRATION_LOG = self.temp_root / "skill-agent-migration.log"
        skill_runtime.SETTINGS_FILE = self.temp_root / "settings.json"
        skill_runtime.RUN_DIR = self.temp_root / "runs"
        skill_runtime.run_manager = skill_runtime.AIRunManager()
        self.provider = {
            "id": "test-provider",
            "name": "Test Provider",
            "protocol": "openai",
            "enabled": True,
            "primary": True,
            "chat_models": ["text-1", "vision-1"],
        }
        self.provider_patch = mock.patch.object(skill_runtime.provider_service, "load_api_providers", return_value=[self.provider])
        self.provider_patch.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.provider_patch.stop()
        agent_service.AGENTS_FILE = self.original_agents_file
        skill_runtime.SETTINGS_FILE = self.original_settings_file
        skill_service.CUSTOM_SKILLS_FILE = self.original_skills_file
        skill_service.SKILL_AGENT_MIGRATION_FILE = self.original_migration_file
        skill_service.SKILL_AGENT_MIGRATION_LOG = self.original_migration_log
        skill_runtime.RUN_DIR = self.original_run_dir
        skill_runtime.run_manager = self.original_run_manager
        shutil.rmtree(self.temp_root, ignore_errors=True)

    async def test_agent_routes_and_runtime_settings(self):
        response = await self.client.get("/api/agents")
        self.assertEqual(200, response.status_code)
        self.assertEqual(17, len(response.json()["agents"]))

        exported = await self.client.get("/api/agents/export")
        self.assertEqual(200, exported.status_code)
        self.assertIn("syncanvas-agents.json", exported.headers["content-disposition"])
        payload = exported.json()

        upscaler = next(item for item in payload if item["id"] == "upscaler")
        updated = {**upscaler, "name": "API route test", "temperature": 0.8}
        updated.pop("id")
        response = await self.client.put("/api/agents/upscaler", json=updated)
        self.assertEqual(200, response.status_code)
        self.assertEqual("API route test", response.json()["agent"]["name"])

        response = await self.client.post("/api/agents/upscaler/reset")
        self.assertEqual(200, response.status_code)
        self.assertEqual("upscaler", response.json()["agent"]["id"])

        response = await self.client.post("/api/agents/import", json={"agents": payload})
        self.assertEqual(200, response.status_code)
        self.assertEqual(17, response.json()["imported"])

        response = await self.client.put("/api/ai-runtime/settings", json={
            "provider_id": "test-provider",
            "text_model": "text-1",
            "vision_model": "vision-1",
        })
        self.assertEqual(200, response.status_code)
        self.assertEqual("vision-1", response.json()["vision_model"])

    async def test_custom_agents_remain_editable_and_custom_skill_writes_are_retired(self):
        agent_payload = {
            "name": "Custom Agent", "description": "api test", "modelKind": "text",
            "temperature": 0.5, "systemPrompt": "Answer clearly.",
        }
        response = await self.client.post("/api/agents", json=agent_payload)
        self.assertEqual(201, response.status_code)
        agent_id = response.json()["agent"]["id"]

        skill_payload = {
            "name": "Custom Skill", "description": "api test", "agentId": agent_id,
            "instructions": "Follow this reusable instruction.", "expectJson": False,
        }
        response = await self.client.post("/api/skills", json=skill_payload)
        self.assertEqual(410, response.status_code)
        self.assertIn("智能体", response.json()["detail"])
        self.assertEqual(410, (await self.client.put("/api/skills/legacy-skill", json=skill_payload)).status_code)
        self.assertEqual(410, (await self.client.delete("/api/skills/legacy-skill")).status_code)
        self.assertEqual(204, (await self.client.delete(f"/api/agents/{agent_id}")).status_code)

    async def test_async_run_poll_and_http_cancel(self):
        request = {
            "input": {"baseUrl": "https://example.com", "models": []},
            "provider_id": "test-provider",
            "text_model": "text-1",
            "vision_model": "vision-1",
            "canvas_id": "canvas-test",
            "node_id": "node-test",
        }
        response = await self.client.post("/api/skills/api-doctor/runs", json=request)
        self.assertEqual(202, response.status_code)
        run_id = response.json()["run_id"]
        for _ in range(20):
            record = (await self.client.get(f"/api/ai-runs/{run_id}")).json()
            if record["status"] in skill_runtime.TERMINAL_STATES:
                break
            await asyncio.sleep(0.01)
        self.assertEqual("succeeded", record["status"])
        self.assertTrue(record["output"]["heuristic"])

        async def slow_run(context, skill_id, raw_input):
            await asyncio.sleep(60)
            return {"content": "late"}

        with mock.patch.object(skill_runtime.SkillContext, "run_skill", new=slow_run):
            response = await self.client.post("/api/skills/api-doctor/runs", json=request)
            self.assertEqual(202, response.status_code)
            cancel_id = response.json()["run_id"]
            cancelled = await self.client.delete(f"/api/ai-runs/{cancel_id}")
            self.assertEqual(200, cancelled.status_code)
            self.assertEqual("cancelled", cancelled.json()["status"])
            await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
