import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import legacy
from app.api import agent_skill, canvas, canvas_assistant, components, digital_human, generation, image_batches, node_extension_assets, node_extensions, providers, runtime_nodes, system, template_assets, workflows
from app.services import component_service, digital_human_service, image_batch_service, node_engine_component_service, node_engine_service, node_extension_service, skill_runtime, skill_service
from app import upstream_bridge
from app.core.security import browser_write_allowed, configured_origins, install_log_redaction, redact_sensitive_value, request_host_allowed


install_log_redaction()


@asynccontextmanager
async def lifespan(_: FastAPI):
    legacy.GLOBAL_LOOP = asyncio.get_running_loop()
    legacy.sync_static_html_versions()
    await upstream_bridge.initialize_upstream_runtime()
    component_service.recover_interrupted_component_install()
    node_engine_component_service.recover_interrupted_install()
    digital_human_service.start_digital_human_gpu_idle_reaper()
    skill_service.migrate_custom_skills_to_agents()
    skill_runtime.run_manager.recover()
    image_batch_service.recover_interrupted_chat_image_messages()
    node_extension_service.initialize_node_extensions()
    node_engine_service.initialize()
    try:
        yield
    finally:
        await node_engine_service.shutdown()


app = FastAPI(lifespan=lifespan)
MAX_JSON_REQUEST_BYTES = 64 * 1024 * 1024
_DATA_MUTATION_LOCKS = {
    "canvas": asyncio.Lock(),
    "assets": asyncio.Lock(),
    "agents": asyncio.Lock(),
    "providers": asyncio.Lock(),
    "workflows": asyncio.Lock(),
}


def _mutation_domain(path: str) -> str:
    if path.startswith(("/api/canvases", "/api/projects")):
        return "canvas"
    if path.startswith((
        "/api/asset-library",
        "/api/asset-url-library",
        "/api/prompt-libraries",
        "/api/shared-folders",
        "/api/template-assets",
    )):
        return "assets"
    if path.startswith(("/api/agents", "/api/skills")):
        return "agents"
    if path.startswith("/api/providers"):
        return "providers"
    if path.startswith("/api/workflows"):
        return "workflows"
    return ""

# CORS 配置：支持本地开发和配置的域名
# 默认允许本地开发，如果设置了 ALLOWED_ORIGINS 环境变量则使用该值
allowed_origins = configured_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_browser_origin(request: Request, call_next):
    if not request_host_allowed(request):
        return JSONResponse(status_code=400, content={"detail": "仅允许通过本机地址访问 SynCanvas"})
    if not browser_write_allowed(request):
        return JSONResponse(status_code=403, content={"detail": "拒绝跨来源写请求"})
    content_type = request.headers.get("content-type", "").casefold()
    content_length = request.headers.get("content-length", "")
    if "application/json" in content_type and content_length.isdigit() and int(content_length) > MAX_JSON_REQUEST_BYTES:
        return JSONResponse(status_code=413, content={"detail": "JSON 请求超过 64 MiB"})
    domain = _mutation_domain(request.url.path) if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"} else ""
    if domain:
        async with _DATA_MUTATION_LOCKS[domain]:
            return await call_next(request)
    return await call_next(request)

app.mount("/static", StaticFiles(directory=legacy.STATIC_DIR), name="static")
app.mount("/output", StaticFiles(directory=legacy.OUTPUT_DIR), name="output")
app.mount("/assets", StaticFiles(directory=legacy.ASSETS_DIR), name="assets")

app.add_exception_handler(RequestValidationError, legacy.request_validation_exception_handler)


@app.exception_handler(HTTPException)
async def redacted_http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": redact_sensitive_value(exc.detail)},
        headers=exc.headers,
    )

app.websocket("/ws/stats")(legacy.websocket_endpoint)

app.include_router(system.router)
app.include_router(agent_skill.router)
app.include_router(components.router)
app.include_router(digital_human.router)
app.include_router(providers.router)
app.include_router(generation.router)
app.include_router(image_batches.router)
app.include_router(canvas.router)
app.include_router(canvas_assistant.router)
app.include_router(workflows.router)
app.include_router(template_assets.router)
app.include_router(node_extension_assets.router)
app.include_router(node_extensions.router)
app.include_router(runtime_nodes.router)

UPSTREAM_SYNC = upstream_bridge.install_upstream_routes(app)


@app.get("/api/upstream-sync")
async def upstream_sync_status():
    return UPSTREAM_SYNC
