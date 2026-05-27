import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError

from app import legacy
from app.api import canvas, digital_human, generation, providers, system, workflows

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

app.websocket("/ws/stats")(legacy.websocket_endpoint)

app.include_router(system.router)
app.include_router(digital_human.router)
app.include_router(providers.router)
app.include_router(generation.router)
app.include_router(canvas.router)
app.include_router(workflows.router)
