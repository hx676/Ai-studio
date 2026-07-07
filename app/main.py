import asyncio
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError

from app import legacy
from app.api import canvas, digital_human, generation, providers, system, workflows
from app.services import digital_human_service

app = FastAPI()

# CORS 配置：支持本地开发和配置的域名
# 默认允许本地开发，如果设置了 ALLOWED_ORIGINS 环境变量则使用该值
_allowed_origins = os.getenv("ALLOWED_ORIGINS", "")
if _allowed_origins:
    allowed_origins = [o.strip() for o in _allowed_origins.split(",") if o.strip()]
else:
    allowed_origins = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=legacy.STATIC_DIR), name="static")
app.mount("/output", StaticFiles(directory=legacy.OUTPUT_DIR), name="output")
app.mount("/assets", StaticFiles(directory=legacy.ASSETS_DIR), name="assets")

app.add_exception_handler(RequestValidationError, legacy.request_validation_exception_handler)

@app.on_event("startup")
async def startup_event():
    legacy.GLOBAL_LOOP = asyncio.get_running_loop()
    legacy.sync_static_html_versions()
    digital_human_service.start_digital_human_gpu_idle_reaper()

app.websocket("/ws/stats")(legacy.websocket_endpoint)

app.include_router(system.router)
app.include_router(digital_human.router)
app.include_router(providers.router)
app.include_router(generation.router)
app.include_router(canvas.router)
app.include_router(workflows.router)
