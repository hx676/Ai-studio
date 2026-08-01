from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse

from app.models.agent_skill import (
    AIRunRequest,
    AIRuntimeSettingsUpdate,
    AgentCreate,
    AgentImportRequest,
    AgentRunRequest,
    AgentUpdate,
    SkillCreate,
    SkillUpdate,
)
from app.services import agent_service, skill_runtime, skill_service


router = APIRouter()


@router.get("/api/agents")
async def list_agents():
    skill_service.migrate_custom_skills_to_agents()
    return {"agents": agent_service.list_agents(skill_runtime.used_by_agent())}


@router.get("/api/agents/export")
async def export_agents():
    response = JSONResponse(content=agent_service.export_agents())
    response.headers["Content-Disposition"] = 'attachment; filename="syncanvas-agents.json"'
    return response


@router.post("/api/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(payload: AgentCreate):
    return {"agent": agent_service.create_agent(payload)}


@router.post("/api/agents/import")
async def import_agents(payload: AgentImportRequest):
    agents = agent_service.import_agents(payload.agents)
    return {"agents": agent_service.list_agents(skill_runtime.used_by_agent()), "imported": len(agents)}


@router.put("/api/agents/{agent_id}")
async def update_agent(agent_id: str, payload: AgentUpdate):
    return {"agent": agent_service.update_agent(agent_id, payload)}


@router.post("/api/agents/{agent_id}/reset")
async def reset_agent(agent_id: str):
    return {"agent": agent_service.reset_agent(agent_id)}


@router.delete("/api/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str):
    agent_service.delete_agent(agent_id, skill_runtime.used_by_agent())
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/agents/{agent_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def run_agent(agent_id: str, payload: AgentRunRequest):
    return skill_runtime.submit_agent_run(agent_id, payload)


@router.get("/api/skills")
async def list_skills():
    return {"skills": skill_runtime.list_skill_metadata()}


@router.post("/api/skills", status_code=status.HTTP_201_CREATED)
async def create_skill(payload: SkillCreate):
    return JSONResponse(status_code=410, content={"detail": "自定义 Skill 已合并到智能体，请创建智能体预设"})


@router.put("/api/skills/{skill_id}")
async def update_skill(skill_id: str, payload: SkillUpdate):
    return JSONResponse(status_code=410, content={"detail": "自定义 Skill 已合并到智能体；AI 工作流由内置代码提供，不能修改"})


@router.delete("/api/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(skill_id: str):
    return JSONResponse(status_code=410, content={"detail": "自定义 Skill 已合并到智能体；旧数据会保留用于兼容历史画布"})


@router.post("/api/skills/{skill_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def run_skill(skill_id: str, payload: AIRunRequest):
    return skill_runtime.submit_skill_run(skill_id, payload)


@router.get("/api/ai-runtime/settings")
async def get_runtime_settings():
    return skill_runtime.load_runtime_settings()


@router.put("/api/ai-runtime/settings")
async def put_runtime_settings(payload: AIRuntimeSettingsUpdate):
    values = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    return skill_runtime.save_runtime_settings(values)


@router.get("/api/ai-runs/{run_id}")
async def get_run(run_id: str):
    return skill_runtime.run_manager.get(run_id)


@router.delete("/api/ai-runs/{run_id}")
async def cancel_run(run_id: str):
    return skill_runtime.run_manager.cancel(run_id)
