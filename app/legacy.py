
# Compatibility implementation module.
#
# This file intentionally keeps the original SynCanvas backend functions and
# globals in one importable module for the first safe split. Routers in
# app/api delegate here so public behavior stays unchanged while the service
# layer is extracted domain by domain.

import json
import uuid
import base64
import hashlib
import urllib.request
import urllib.parse
import urllib.error
import os
import re
import random
import sys
import subprocess
import time
import shutil
import asyncio
import logging
import math
import requests
import zipfile
import mimetypes
from typing import List, Dict, Any, Optional
from threading import Lock
import httpx
from PIL import Image
from io import BytesIO
from fastapi import HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, Response, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

QUIET_ACCESS_PATHS = {
    "/api/queue_status",
    "/api/canvases",
    "/api/canvases/trash",
}
QUIET_ACCESS_PREFIXES = (
    "/api/canvases/",
    "/api/digital-human/task/",
)

from app.services.system_service import QuietAccessLogFilter
from app.services.system_service import ConnectionManager
from app.services.system_service import friendly_validation_error
from app.services.system_service import request_validation_exception_handler
from app.services.system_service import current_app_version
from app.services.system_service import versioned_static_html
from app.services.system_service import sync_static_html_versions
from app.services.system_service import static_html_response
from app.services.system_service import app_info
from app.services.system_service import update_allowed_file
from app.services.system_service import github_json
from app.services.system_service import github_bytes
from app.services.system_service import safe_update_target
from app.services.system_service import schedule_self_restart
from app.services.system_service import update_from_github
from app.services.system_service import list_update_backups
from app.services.system_service import get_update_backups
from app.services.system_service import rollback_update
from app.services.system_service import index
from app.services.system_service import get_history_api
from app.services.system_service import get_queue_status
from app.services.system_service import delete_history


logging.getLogger("uvicorn.access").addFilter(QuietAccessLogFilter())

def digital_human_log(message: str) -> None:
    print(f"[digital-human] {message}", flush=True)



# --- WebSocket 状态管理器 ---

manager = ConnectionManager()
GLOBAL_LOOP = None
APP_VERSION = "2026.05.19"
GITHUB_REPO_URL = ""
GITHUB_VERSION_URL = ""
GITHUB_TREE_URL = ""
GITHUB_RAW_ROOT = ""

async def startup_event():
    global GLOBAL_LOOP
    GLOBAL_LOOP = asyncio.get_running_loop()
    sync_static_html_versions()

async def websocket_endpoint(websocket: WebSocket, client_id: str = None):
    await manager.connect(websocket, client_id)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        await manager.disconnect(websocket, client_id)
    except Exception as e:
        print(f"WS Error: {e}")
        await manager.disconnect(websocket, client_id)

# --- 配置区域 ---

CLIENT_ID = str(uuid.uuid4())
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(BASE_DIR, "workflows")
WORKFLOW_PATH = os.path.join(WORKFLOW_DIR, "Z-Image.json")
STATIC_DIR = os.path.join(BASE_DIR, "static")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
OUTPUT_INPUT_DIR = os.path.join(ASSETS_DIR, "input")
OUTPUT_OUTPUT_DIR = os.path.join(ASSETS_DIR, "output")
ASSET_LIBRARY_DIR = os.path.join(ASSETS_DIR, "library")
PROVIDER_LOGO_DIR = os.path.join(ASSETS_DIR, "provider_logos")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
API_ENV_FILE = os.path.join(BASE_DIR, "API", ".env")
DATA_DIR = os.path.join(BASE_DIR, "data")
CONVERSATION_DIR = os.path.join(DATA_DIR, "conversations")
CANVAS_DIR = os.path.join(DATA_DIR, "canvases")
ASSET_LIBRARY_PATH = os.path.join(DATA_DIR, "asset_library.json")
API_PROVIDERS_FILE = os.path.join(DATA_DIR, "api_providers.json")
GLOBAL_CONFIG_FILE = os.path.join(BASE_DIR, "global_config.json")
DIGITAL_HUMAN_CONFIG_FILE = os.path.join(DATA_DIR, "digital_human_config.json")
DIGITAL_HUMAN_LIBRARY_FILE = os.path.join(DATA_DIR, "digital_human_library.json")
DIGITAL_HUMAN_AUDIO_DIR = os.path.join(OUTPUT_OUTPUT_DIR, "digital-human", "audio")
DIGITAL_HUMAN_VIDEO_DIR = os.path.join(OUTPUT_OUTPUT_DIR, "digital-human", "video")
DIGITAL_HUMAN_INPUT_DIR = os.path.join(OUTPUT_INPUT_DIR, "digital-human")
CANVAS_TRASH_RETENTION_MS = 30 * 24 * 60 * 60 * 1000

QUEUE = []
QUEUE_LOCK = Lock()
HISTORY_LOCK = Lock()
GLOBAL_CONFIG_LOCK = Lock()
DIGITAL_HUMAN_CONFIG_LOCK = Lock()
DIGITAL_HUMAN_LIBRARY_LOCK = Lock()
DIGITAL_HUMAN_TASK_LOCK = Lock()
TTS_SERVICE_LOCK = Lock()
TTS_GENERATION_LOCK = Lock()
HEYGEM_GENERATION_LOCK = Lock()
CONVERSATION_LOCK = Lock()
CANVAS_LOCK = Lock()
LOAD_LOCK = Lock()
NEXT_TASK_ID = 1
UPDATE_LOCK = Lock()
TTS_PROCESS = None
TTS_LAST_ERROR = ""

PROVIDER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{2,40}$")
SUPPORTED_PROVIDER_PROTOCOLS = {"openai", "apimart", "gemini", "volcengine", "runninghub"}
UPLOAD_MAX_BYTES = 500 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
PROVIDER_LOGO_MAX_BYTES = 512 * 1024
PROVIDER_LOGO_MIN_RATIO = 4.0
PROVIDER_LOGO_MAX_RATIO = 6.0
PROVIDER_LOGO_FORMAT_EXT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
RUNNINGHUB_DEFAULT_BASE_URL = "https://www.runninghub.cn"
RUNNINGHUB_DEFAULT_IMAGE_MODELS = [
    "seedream-v5-lite/text-to-image",
    "seedream-v5-lite/image-to-image",
]

def ensure_runtime_config_files():
    """首次运行时提前创建配置目录，避免第一次保存 API Key 时才创建目录/文件。"""
    try:
        os.makedirs(os.path.dirname(API_ENV_FILE), exist_ok=True)
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(DIGITAL_HUMAN_AUDIO_DIR, exist_ok=True)
        os.makedirs(DIGITAL_HUMAN_VIDEO_DIR, exist_ok=True)
        os.makedirs(DIGITAL_HUMAN_INPUT_DIR, exist_ok=True)
        if not os.path.exists(API_ENV_FILE):
            with open(API_ENV_FILE, "a", encoding="utf-8"):
                pass
    except Exception as e:
        print(f"初始化 API 配置目录失败: {e}")

def load_env_file():
    if not os.path.exists(API_ENV_FILE):
        return
    try:
        with open(API_ENV_FILE, 'r', encoding='utf-8-sig') as f:
            for raw_line in f.read().splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    except Exception as e:
        print(f"加载 API/.env 失败: {e}")
ensure_runtime_config_files()
load_env_file()

COMFYUI_INSTANCES = [s.strip() for s in os.getenv("COMFYUI_INSTANCES", "127.0.0.1:8188").split(",") if s.strip()]
COMFYUI_ADDRESS = COMFYUI_INSTANCES[0]

AI_BASE_URL = os.getenv("COMFLY_BASE_URL", "https://ai.comfly.chat").rstrip("/")
AI_API_KEY = os.getenv("COMFLY_API_KEY", "")
MODELSCOPE_API_KEY = os.getenv("MODELSCOPE_API_KEY", "")
MODELSCOPE_CHAT_BASE_URL = "https://api-inference.modelscope.cn/v1"
MODELSCOPE_DEFAULT_IMAGE_MODELS = [
    "Tongyi-MAI/Z-Image-Turbo",
    "Qwen/Qwen-Image-2512",
    "Qwen/Qwen-Image-Edit-2511",
    "black-forest-labs/FLUX.2-klein-9B",
]
MODELSCOPE_DEFAULT_CHAT_MODELS = [
    "Qwen/Qwen3-235B-A22B",
    "Qwen/Qwen3-VL-235B-A22B-Instruct",
    "MiniMax/MiniMax-M2.7:MiniMax",
]
_MODELSCOPE_CONFIGURED_CHAT_MODELS = [m.strip() for m in os.getenv("MODELSCOPE_CHAT_MODELS", "").split(",") if m.strip()]
MODELSCOPE_CHAT_MODELS = list(dict.fromkeys([m for m in [*MODELSCOPE_DEFAULT_CHAT_MODELS, *_MODELSCOPE_CONFIGURED_CHAT_MODELS] if m]))
MODELSCOPE_DEFAULT_IMAGE_MODEL = MODELSCOPE_DEFAULT_IMAGE_MODELS[0]
MODELSCOPE_DEFAULT_CHAT_MODEL = "Qwen/Qwen3-235B-A22B"
MODELSCOPE_DEFAULT_LORAS = [
    {
        "id": "Daniel8152/film",
        "name": "Z-Image Film",
        "target_model": "Tongyi-MAI/Z-Image-Turbo",
        "strength": 0.8,
        "enabled": True,
        "note": "",
    },
    {
        "id": "Daniel8152/Qwen-Image-2512-Film",
        "name": "Qwen Image 2512 Film",
        "target_model": "Qwen/Qwen-Image-2512",
        "strength": 0.8,
        "enabled": True,
        "note": "",
    },
    {
        "id": "Daniel8152/Klein-enhance",
        "name": "Klein enhance",
        "target_model": "black-forest-labs/FLUX.2-klein-9B",
        "strength": 0.8,
        "enabled": True,
        "note": "",
    },
]
MODELSCOPE_DEFAULTS_VERSION = 3
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-2")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "You are a helpful assistant.")
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "30"))
AI_REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "1800"))
IMAGE_POLL_INTERVAL = float(os.getenv("IMAGE_POLL_INTERVAL", "2"))
IMAGE_TASK_TIMEOUT = float(os.getenv("IMAGE_TASK_TIMEOUT", str(AI_REQUEST_TIMEOUT)))
COMFYUI_HISTORY_TIMEOUT = int(float(os.getenv("COMFYUI_HISTORY_TIMEOUT", "1800")))
APIMART_IMAGE_TASK_TIMEOUT = float(os.getenv("APIMART_IMAGE_TASK_TIMEOUT", "1800"))
APIMART_IMAGE_POLL_INTERVAL = float(os.getenv("APIMART_IMAGE_POLL_INTERVAL", "5"))
APIMART_IMAGE_INITIAL_POLL_DELAY = float(os.getenv("APIMART_IMAGE_INITIAL_POLL_DELAY", "10"))
VIDEO_POLL_TIMEOUT = float(os.getenv("VIDEO_POLL_TIMEOUT", "1800"))
ONLINE_IMAGE_PROMPT_MAX_LENGTH = int(os.getenv("ONLINE_IMAGE_PROMPT_MAX_LENGTH", "20000"))
VIDEO_PROMPT_MAX_LENGTH = int(os.getenv("VIDEO_PROMPT_MAX_LENGTH", "4000"))
LLM_MESSAGE_MAX_LENGTH = int(os.getenv("LLM_MESSAGE_MAX_LENGTH", "20000"))

FIELD_LABELS = {
    "prompt": "提示词",
    "message": "文本",
    "system_prompt": "系统提示词",
}




from app.services.provider_service import model_list
from app.services.provider_service import reload_env_globals
from app.services.provider_service import provider_key_env
from app.services.provider_service import mask_secret
from app.services.provider_service import default_api_providers
from app.services.provider_service import merge_default_api_providers
from app.services.provider_service import normalize_model_list
from app.services.provider_service import model_list_from_values
from app.services.provider_service import normalize_ms_loras
from app.services.provider_service import normalize_endpoint_override
from app.services.provider_service import provider_endpoint_url
from app.services.provider_service import runninghub_endpoint_url
from app.services.provider_service import normalize_provider_logo_url
from app.services.provider_service import normalize_provider
from app.services.provider_service import load_api_providers
from app.services.provider_service import save_api_providers
from app.services.provider_service import public_provider
from app.services.provider_service import get_primary_provider_id
from app.services.provider_service import get_api_provider
from app.services.provider_service import get_api_provider_exact
from app.services.provider_service import env_quote
from app.services.provider_service import update_env_values
from app.services.provider_service import upload_provider_logo
from app.services.provider_service import ai_config
from app.services.provider_service import ai_models
from app.services.provider_service import api_providers
from app.services.provider_service import save_providers
from app.services.provider_service import get_global_token
from app.services.provider_service import protocol_from_payload
from app.services.provider_service import upstream_models_url
from app.services.provider_service import upstream_model_headers
from app.services.provider_service import classify_upstream_model
from app.services.provider_service import parse_upstream_models
from app.services.provider_service import test_provider_connection
from app.services.provider_service import probe_async_endpoint
from app.services.provider_service import fetch_models_from_upstream
from app.services.provider_service import fetch_upstream_models_from_payload
from app.services.provider_service import fetch_upstream_models



CHAT_MODELS = model_list("CHAT_MODELS", CHAT_MODEL, ["gpt-4o-mini", "gemini-3.1-flash-image-preview-2k"])
IMAGE_MODELS = model_list("IMAGE_MODELS", IMAGE_MODEL, ["nano-banana-pro"])
VIDEO_MODELS = model_list("VIDEO_MODELS", "veo3-fast", [
    # —— Veo 系列 ——
    "veo2", "veo2-fast", "veo2-pro",
    "veo3", "veo3-fast", "veo3-pro",
    "veo3.1", "veo3.1-fast", "veo3.1-quality", "veo3.1-lite",
    # —— Sora ——
    "sora-2", "sora-2-pro",
    # —— 阿里 通义万相 ——
    "wan2.6-t2v", "wan2.6-i2v",
    "wan2.5-t2v-preview", "wan2.5-i2v-preview",
    "wan2.2-t2v-plus", "wan2.2-i2v-plus", "wan2.2-i2v-flash",
    # —— 火山 豆包 Seedance ——
    "doubao-seedance-2-0-260128",
    "doubao-seedance-2-0-fast-260128",
    "doubao-seedance-1-5-pro-251215",
    "doubao-seedance-1-0-pro-250528",
    "doubao-seedance-1-0-lite-t2v-250428",
    "doubao-seedance-1-0-lite-i2v-250428",
])





















BACKEND_LOCAL_LOAD = {addr: 0 for addr in COMFYUI_INSTANCES}

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(OUTPUT_INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_OUTPUT_DIR, exist_ok=True)
os.makedirs(DIGITAL_HUMAN_AUDIO_DIR, exist_ok=True)
os.makedirs(DIGITAL_HUMAN_VIDEO_DIR, exist_ok=True)
os.makedirs(DIGITAL_HUMAN_INPUT_DIR, exist_ok=True)
os.makedirs(ASSET_LIBRARY_DIR, exist_ok=True)
os.makedirs(PROVIDER_LOGO_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(WORKFLOW_DIR, exist_ok=True)
os.makedirs(CONVERSATION_DIR, exist_ok=True)
os.makedirs(CANVAS_DIR, exist_ok=True)


# --- Pydantic 模型 ---







# 缓存 GitHub Tree API 响应（含 ETag），减少 60 次/h 限流压力
GITHUB_TREE_CACHE: Dict[str, Any] = {"etag": "", "data": None, "expires_at": 0.0}





from app.models.common import UpdateRequest, RollbackRequest






from app.models.common import GenerateRequest, DeleteHistoryRequest, TokenRequest, CloudGenRequest, CloudPollRequest, MsGenerateRequest
from app.models.canvas import AIReference, OnlineImageRequest, CanvasVideoRequest, CanvasLLMRequest, ConversationCreateRequest, CanvasCreateRequest, CanvasSaveRequest, CanvasAssetCheckRequest, CanvasAssetDownloadRequest, AssetLibraryCategoryRequest, AssetLibraryAddRequest, AssetLibraryRenameRequest
from app.models.digital_human import DigitalHumanTTSConfig, DigitalHumanHeyGemConfig, DigitalHumanConfigPayload, DigitalHumanPersonPayload, DigitalHumanPersonVideoPayload, DigitalHumanVoiceMetaPayload, DigitalHumanTTSOptions, DigitalHumanTTSRequest, DigitalHumanGenerateRequest
from app.models.providers import ApiProviderPayload, TestConnectionPayload
from app.models.workflow import WorkflowField, WorkflowConfig, WorkflowUploadRequest, WorkflowRunRequest, ComfyInstancesPayload








CANVAS_TASKS: Dict[str, Dict[str, Any]] = {}
CANVAS_TASK_LOCK = Lock()
DIGITAL_HUMAN_TASKS: Dict[str, Dict[str, Any]] = {}












from app.models.canvas import ChatRequest











# --- 负载均衡 ---

def check_images_exist(backend_addr, images):
    if not images: return True
    for img in images:
        try:
            url = f"http://{backend_addr}/view?filename={urllib.parse.quote(img)}&type=input"
            r = requests.get(url, stream=True, timeout=0.5)
            r.close()
            if r.status_code != 200: return False
        except (requests.RequestException, requests.ConnectionError, requests.Timeout): return False
    return True

def get_best_backend(required_images: List[str] = None):
    best_backend = COMFYUI_INSTANCES[0]
    min_queue_size = float('inf')
    candidates_with_images = []
    candidates_others = []
    backend_stats = {}

    for addr in COMFYUI_INSTANCES:
        try:
            with urllib.request.urlopen(f"http://{addr}/queue", timeout=1) as response:
                data = json.loads(response.read())
                remote_load = len(data.get('queue_running', [])) + len(data.get('queue_pending', []))
                with LOAD_LOCK:
                    local_load = BACKEND_LOCAL_LOAD.get(addr, 0)
                effective_load = max(remote_load, local_load)
                has_images = check_images_exist(addr, required_images)
                backend_stats[addr] = {"load": effective_load, "has_images": has_images}
                if has_images:
                    candidates_with_images.append(addr)
                else:
                    candidates_others.append(addr)
        except Exception as e:
            print(f"Backend {addr} unreachable: {e}")
            continue

    target_candidates = candidates_with_images if candidates_with_images else candidates_others
    if not target_candidates:
        if candidates_others:
            target_candidates = candidates_others
        else:
            return COMFYUI_INSTANCES[0]

    for addr in target_candidates:
        load = backend_stats[addr]["load"]
        if load < min_queue_size:
            min_queue_size = load
            best_backend = addr

    return best_backend

# --- 辅助工具 ---

def download_image(comfy_address, comfy_url_path, prefix="studio_"):
    filename = f"{prefix}{uuid.uuid4().hex[:10]}.png"
    local_path = output_path_for(filename, "output")
    full_url = f"http://{comfy_address}{comfy_url_path}"
    try:
        with urllib.request.urlopen(full_url) as response, open(local_path, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        return output_url_for(filename, "output")
    except Exception as e:
        print(f"下载图片失败: {e}")
        if comfy_url_path.startswith("/view"):
            return comfy_url_path.replace("/view", "/api/view", 1)
        return full_url

def comfy_output_extension(item):
    filename = str((item or {}).get("filename") or "")
    ext = os.path.splitext(filename)[1].lower()
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov", ".m4v", ".gif"}:
        return ext
    fmt = str((item or {}).get("format") or "").lower()
    if "webm" in fmt:
        return ".webm"
    if "quicktime" in fmt or "mov" in fmt:
        return ".mov"
    if "mp4" in fmt or "h264" in fmt or "video" in fmt:
        return ".mp4"
    return ".png"

def is_video_output_item(item):
    ext = comfy_output_extension(item)
    fmt = str((item or {}).get("format") or "").lower()
    return ext in {".mp4", ".webm", ".mov", ".m4v"} or "video" in fmt

def download_comfy_output(comfy_address, item, prefix="studio_"):
    ext = comfy_output_extension(item)
    filename = f"{prefix}{uuid.uuid4().hex[:10]}{ext}"
    local_path = output_path_for(filename, "output")
    subfolder = urllib.parse.quote(str(item.get("subfolder") or ""))
    file_type = urllib.parse.quote(str(item.get("type") or "output"))
    comfy_url_path = f"/view?filename={urllib.parse.quote(str(item['filename']))}&subfolder={subfolder}&type={file_type}"
    full_url = f"http://{comfy_address}{comfy_url_path}"
    try:
        with urllib.request.urlopen(full_url) as response, open(local_path, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        return output_url_for(filename, "output")
    except Exception as e:
        print(f"下载 ComfyUI 输出失败: {e}")
        if comfy_url_path.startswith("/view"):
            return comfy_url_path.replace("/view", "/api/view", 1)
        return full_url

def save_to_history(record):
    with HISTORY_LOCK:
        history = []
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except: pass
        if "timestamp" not in record:
            record["timestamp"] = time.time()
        history.insert(0, record)
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history[:5000], f, ensure_ascii=False, indent=4)

def get_comfy_history(comfy_address, prompt_id):
    try:
        with urllib.request.urlopen(f"http://{comfy_address}/history/{prompt_id}") as response:
            return json.loads(response.read())
    except Exception as e:
        return {}

def safe_user_id(user_id, request: Request):
    candidate = (user_id or "").strip()
    if not candidate and request.client:
        candidate = f"ip-{request.client.host}"
    if not candidate:
        candidate = "anonymous"
    candidate = re.sub(r"[^a-zA-Z0-9_.-]", "-", candidate)[:80].strip(".-")
    return candidate or "anonymous"

def user_dir(user_id):
    path = os.path.join(CONVERSATION_DIR, user_id)
    os.makedirs(path, exist_ok=True)
    return path

def conversation_path(user_id, conversation_id):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", conversation_id or "")
    if not cleaned:
        raise HTTPException(status_code=400, detail="无效的对话 ID")
    return os.path.join(user_dir(user_id), f"{cleaned}.json")

def now_ms():
    return int(time.time() * 1000)

def save_conversation(user_id, conversation):
    with CONVERSATION_LOCK:
        path = conversation_path(user_id, conversation["id"])
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(conversation, f, ensure_ascii=False, indent=2)

def new_conversation(user_id, title="新对话"):
    timestamp = now_ms()
    conversation = {
        "id": uuid.uuid4().hex,
        "title": (title or "新对话")[:80],
        "created_at": timestamp,
        "updated_at": timestamp,
        "messages": [],
    }
    save_conversation(user_id, conversation)
    return conversation

def load_conversation(user_id, conversation_id):
    path = conversation_path(user_id, conversation_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="对话不存在")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def list_conversations(user_id):
    records = []
    for filename in os.listdir(user_dir(user_id)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(user_dir(user_id), filename)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        messages = data.get("messages", [])
        last_message = next((m for m in reversed(messages) if m.get("role") != "system"), None)
        records.append({
            "id": data.get("id"),
            "title": data.get("title", "新对话"),
            "created_at": data.get("created_at", 0),
            "updated_at": data.get("updated_at", 0),
            "last_message": (last_message or {}).get("content", ""),
        })
    return sorted(records, key=lambda item: item["updated_at"], reverse=True)

def canvas_path(canvas_id):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", canvas_id or "")
    if not cleaned:
        raise HTTPException(status_code=400, detail="无效的画布 ID")
    return os.path.join(CANVAS_DIR, f"{cleaned}.json")

def save_canvas(canvas):
    canvas["updated_at"] = now_ms()
    with CANVAS_LOCK:
        with open(canvas_path(canvas["id"]), 'w', encoding='utf-8') as f:
            json.dump(canvas, f, ensure_ascii=False, indent=2)

def normalize_canvas_kind(kind="classic"):
    return "smart" if str(kind or "").strip().lower() == "smart" else "classic"

def new_canvas(title="未命名画布", icon="layers", kind="classic"):
    timestamp = now_ms()
    canvas_kind = normalize_canvas_kind(kind)
    canvas = {
        "id": uuid.uuid4().hex,
        "title": (title or ("智能画布" if canvas_kind == "smart" else "未命名画布"))[:80],
        "icon": (icon or ("sparkles" if canvas_kind == "smart" else "🧩"))[:32],
        "kind": canvas_kind,
        "created_at": timestamp,
        "updated_at": timestamp,
        "nodes": [],
        "connections": [],
        "viewport": {"x": 0, "y": 0, "scale": 1},
    }
    save_canvas(canvas)
    return canvas

def load_canvas(canvas_id):
    path = canvas_path(canvas_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="画布不存在")
    with open(path, 'r', encoding='utf-8') as f:
        canvas = json.load(f)
    if canvas.get("deleted_at"):
        raise HTTPException(status_code=404, detail="画布已在回收站")
    return canvas

def load_canvas_any(canvas_id):
    path = canvas_path(canvas_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="画布不存在")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def canvas_record(data):
    return {
        "id": data.get("id"),
        "title": data.get("title", "未命名画布"),
        "icon": data.get("icon", "🧩"),
        "kind": normalize_canvas_kind(data.get("kind")),
        "created_at": data.get("created_at", 0),
        "updated_at": data.get("updated_at", 0),
        "deleted_at": data.get("deleted_at", 0),
        "node_count": len(data.get("nodes", [])),
    }

def cleanup_expired_canvas_trash():
    cutoff = now_ms() - CANVAS_TRASH_RETENTION_MS
    with CANVAS_LOCK:
        for filename in os.listdir(CANVAS_DIR):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(CANVAS_DIR, filename)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                deleted_at = int(data.get("deleted_at") or 0)
                if deleted_at and deleted_at < cutoff:
                    os.remove(path)
            except Exception:
                continue

def iter_canvas_records(include_deleted=False):
    cleanup_expired_canvas_trash()
    records = []
    for filename in os.listdir(CANVAS_DIR):
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(CANVAS_DIR, filename), 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        is_deleted = bool(data.get("deleted_at"))
        if include_deleted != is_deleted:
            continue
        records.append(canvas_record(data))
    return records

def list_canvases():
    records = iter_canvas_records(include_deleted=False)
    return sorted(records, key=lambda item: item["updated_at"], reverse=True)

def list_deleted_canvases():
    records = iter_canvas_records(include_deleted=True)
    return sorted(records, key=lambda item: item["deleted_at"], reverse=True)

def display_title(text):
    title = re.sub(r"\s+", " ", text or "").strip()
    return title[:24] or "新对话"

def resolve_chat_provider(provider: str, model: str, ms_model: str):
    if provider == "modelscope":
        if not MODELSCOPE_API_KEY:
            raise HTTPException(status_code=400, detail="未配置 MODELSCOPE_API_KEY，请在 API/.env 中填写。")
        base = MODELSCOPE_CHAT_BASE_URL
        hdrs = {"Authorization": f"Bearer {MODELSCOPE_API_KEY}", "Content-Type": "application/json"}
        mdl = selected_model(ms_model or model, MODELSCOPE_CHAT_MODELS[0] if MODELSCOPE_CHAT_MODELS else "MiniMax/MiniMax-M2.7")
        return base, hdrs, mdl
    api_provider = get_api_provider(provider or "")
    base_root = (api_provider.get("base_url") or AI_BASE_URL).rstrip("/")
    if not base_root:
        raise HTTPException(status_code=400, detail=f"{api_provider.get('name') or api_provider['id']} 未配置 Base URL")
    base = base_root if base_root.endswith("/v1") else base_root + "/v1"
    hdrs = api_headers(provider=api_provider)
    default_model = (api_provider.get("chat_models") or [CHAT_MODEL])[0]
    mdl = selected_model(model, default_model)
    return base, hdrs, mdl

def api_headers(json_body=True, provider=None):
    if provider:
        key_env = provider_key_env(provider["id"])
        api_key = os.getenv(key_env, "")
        provider_name = provider.get("name") or provider["id"]
        if not api_key:
            raise HTTPException(status_code=400, detail=f"未配置 {provider_name} 的 API Key，请在 API 平台管理中填写。")
    else:
        api_key = AI_API_KEY
        if not api_key:
            raise HTTPException(status_code=400, detail="未配置 COMFLY_API_KEY，请在 API/.env 中填写。")
    if provider and provider_protocol(provider) == "gemini":
        headers = {"Accept": "application/json", "x-goog-api-key": api_key}
    else:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers

def selected_model(requested, fallback):
    model = (requested or fallback).strip()
    if not model:
        raise HTTPException(status_code=400, detail="模型名称不能为空")
    if len(model) > 240 or any(ord(ch) < 32 or ord(ch) == 127 for ch in model):
        raise HTTPException(status_code=400, detail=f"模型名称不合法：{model}")
    return model

def modelscope_size(value, fallback="1024x1024"):
    size = str(value or fallback).strip().lower().replace("*", "x")
    if re.fullmatch(r"\d{2,5}x\d{2,5}", size):
        return size
    raise HTTPException(status_code=400, detail=f"ModelScope size 格式不正确：{value or fallback}，应为 WxH，例如 1024x1024")

def unwrap_apimart_response(raw):
    """APIMart 将标准 OpenAI 响应包在 {"code":200,"data":{...}} 里；如果检测到就解包。"""
    if isinstance(raw, dict) and "data" in raw and isinstance(raw.get("data"), dict) and "choices" not in raw:
        return raw["data"]
    return raw

def text_from_chat_response(data):
    data = unwrap_apimart_response(data)
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text") or item.get("content") or "")
        return "\n".join(part for part in parts if part)
    return str(content)

def text_delta_from_chat_chunk(data):
    choices = data.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text") or item.get("content") or "")
        return "".join(parts)
    return str(content) if content else ""

def sse_event(data):
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

def extract_image(data):
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content") or {}
            parts = content.get("parts") if isinstance(content, dict) else None
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline = part.get("inlineData") or part.get("inline_data") or {}
                if not isinstance(inline, dict):
                    continue
                value = inline.get("data")
                if value:
                    return {
                        "type": "b64",
                        "value": value,
                        "mime_type": inline.get("mimeType") or inline.get("mime_type") or "image/png",
                    }
    generic = extract_generic_image(data)
    if generic:
        return generic
    if isinstance(data.get("data"), dict) and isinstance(data["data"].get("result"), dict):
        data = data["data"]
    if isinstance(data.get("result"), dict):
        result_images = data["result"].get("images") or []
        if result_images:
            first = result_images[0]
            url = first.get("url")
            if isinstance(url, list) and url:
                return {"type": "url", "value": url[0]}
            if isinstance(url, str) and url:
                return {"type": "url", "value": url}
    if isinstance(data.get("data"), dict) and isinstance(data["data"].get("data"), dict):
        data = data["data"]["data"]
    images = data.get("data") or []
    if not isinstance(images, list) or not images:
        raise HTTPException(status_code=502, detail="生图接口没有返回图片数据")
    first = images[0]
    if first.get("url"):
        return {"type": "url", "value": first["url"]}
    if first.get("b64_json"):
        return {"type": "b64", "value": first["b64_json"]}
    raise HTTPException(status_code=502, detail="无法识别生图接口返回格式")

def image_payload_from_data_url(value):
    match = re.match(r"^data:([^;]+);base64,(.+)$", str(value or ""), re.I | re.S)
    if not match:
        return None
    return {"type": "b64", "value": re.sub(r"\s+", "", match.group(2)), "mime_type": match.group(1) or "image/png"}

def image_payload_from_text(value):
    text = str(value or "").strip()
    if not text:
        return None
    data_match = re.search(r"data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=_-]+", text, re.I)
    if data_match:
        return image_payload_from_data_url(data_match.group(0))
    urls = [m.group(0).rstrip(".,;:!?)]") for m in re.finditer(r"https?://[^\s)<>'\"]+", text, re.I)]
    if not urls:
        return None
    image_url = next((url for url in urls if re.search(r"\.(png|jpe?g|webp|gif|bmp)(\?|$)", url, re.I)), urls[-1])
    return {"type": "url", "value": image_url}

def extract_generic_image(value, depth=0):
    if depth > 12 or value is None:
        return None
    if isinstance(value, str):
        return image_payload_from_text(value)
    if isinstance(value, list):
        for item in value:
            found = extract_generic_image(item, depth + 1)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None
    image_url = value.get("image_url")
    if isinstance(image_url, dict):
        found = image_payload_from_text(image_url.get("url"))
        if found:
            return found
    elif isinstance(image_url, str):
        found = image_payload_from_text(image_url)
        if found:
            return found
    for key in ("url", "output_url", "image"):
        if isinstance(value.get(key), str):
            found = image_payload_from_text(value[key])
            if found:
                return found
    for key in ("b64_json", "base64", "image_base64"):
        if isinstance(value.get(key), str):
            return image_payload_from_data_url(value[key]) or {"type": "b64", "value": value[key], "mime_type": "image/png"}
    inline = value.get("inline_data") or value.get("inlineData")
    if isinstance(inline, dict) and inline.get("data"):
        return {
            "type": "b64",
            "value": inline["data"],
            "mime_type": inline.get("mime_type") or inline.get("mimeType") or "image/png",
        }
    for item in value.values():
        found = extract_generic_image(item, depth + 1)
        if found:
            return found
    return None

def extract_task_id(data):
    if isinstance(data, list):
        for item in data:
            task_id = extract_task_id(item)
            if task_id:
                return task_id
        return None
    if not isinstance(data, dict):
        return None
    for key in ("task_id", "taskId", "taskID", "job_id", "jobId"):
        if data.get(key):
            return str(data[key])
    raw_id = data.get("id")
    if raw_id:
        text = str(raw_id)
        status_text = " ".join(
            str(data.get(key) or "")
            for key in ("status", "state", "task_status", "taskStatus", "message")
        ).lower()
        if text.lower().startswith(("task", "job")) or any(token in status_text for token in ("queued", "running", "pending", "processing")):
            return text
    for key in ("data", "result", "task", "job"):
        nested = data.get(key)
        task_id = extract_task_id(nested)
        if task_id:
            return task_id
    return None

def safe_upstream_summary(value, depth=0):
    if depth > 2:
        return {"type": type(value).__name__}
    if isinstance(value, dict):
        summary = {}
        for key, item in list(value.items())[:24]:
            key_text = str(key)
            if any(token in key_text.lower() for token in ("key", "token", "authorization", "secret")):
                summary[key_text] = "<redacted>"
            else:
                summary[key_text] = safe_upstream_summary(item, depth + 1)
        if len(value) > 24:
            summary["_truncated_keys"] = len(value) - 24
        return summary
    if isinstance(value, list):
        summary = {"type": "list", "len": len(value)}
        if value:
            summary["first"] = safe_upstream_summary(value[0], depth + 1)
        return summary
    if isinstance(value, str):
        if value.startswith("data:image/"):
            prefix = value.split(",", 1)[0]
            return {"type": "data_url", "prefix": prefix, "length": len(value)}
        if len(value) > 240:
            return {"type": "str", "length": len(value), "preview": value[:160]}
        return value
    return value

def image_task_status(data):
    if not isinstance(data, dict):
        return ""
    candidates = [data]
    for key in ("data", "result", "task", "job"):
        item = data.get(key)
        if isinstance(item, dict):
            candidates.append(item)
    for item in candidates:
        for key in ("status", "state", "task_status", "taskStatus"):
            value = item.get(key)
            if value is not None:
                return str(value).strip().upper()
    return ""

def image_task_failure_reason(data):
    if not isinstance(data, dict):
        return ""
    candidates = [data]
    for key in ("data", "result", "task", "job"):
        item = data.get(key)
        if isinstance(item, dict):
            candidates.append(item)
    for item in candidates:
        error = item.get("error") if isinstance(item.get("error"), dict) else {}
        reason = (
            item.get("failure_reason")
            or item.get("fail_reason")
            or item.get("message")
            or item.get("detail")
            or error.get("message")
        )
        if reason:
            return str(reason)
    return ""

def images_api_unsupported(response):
    text = str(getattr(response, "text", "") or "").lower()
    return "images api is not supported" in text or "not supported for this platform" in text

def provider_protocol(provider):
    return str((provider or {}).get("protocol") or "openai").strip().lower()

def is_apimart_provider(provider):
    base_url = str((provider or {}).get("base_url") or "").lower()
    return provider_protocol(provider) == "apimart" or "apimart.ai" in base_url

def is_mindapi_provider(provider):
    base_url = str((provider or {}).get("base_url") or AI_BASE_URL).strip()
    parsed = urllib.parse.urlsplit(base_url)
    return (parsed.netloc or "").lower() in {"mindapi.cc", "www.mindapi.cc"}

def is_gemini_provider(provider):
    return provider_protocol(provider) == "gemini"

def is_volcengine_provider(provider):
    return provider_protocol(provider) == "volcengine"

def is_runninghub_provider(provider):
    return provider_protocol(provider) == "runninghub" or str((provider or {}).get("id") or "").strip().lower() == "runninghub"

async def wait_for_image_task(client, task_id, provider=None):
    base_url = (provider.get("base_url") if provider else AI_BASE_URL).rstrip("/")
    is_apimart = is_apimart_provider(provider)
    if is_apimart:
        task_url = f"{base_url}/tasks/{task_id}" if base_url.endswith("/v1") else f"{base_url}/v1/tasks/{task_id}"
    else:
        task_url = f"{base_url}/images/tasks/{task_id}" if base_url.endswith("/v1") else f"{base_url}/v1/images/tasks/{task_id}"
    timeout = APIMART_IMAGE_TASK_TIMEOUT if is_apimart else IMAGE_TASK_TIMEOUT
    interval = APIMART_IMAGE_POLL_INTERVAL if is_apimart else IMAGE_POLL_INTERVAL
    initial_delay = APIMART_IMAGE_INITIAL_POLL_DELAY if is_apimart else 0
    deadline = time.monotonic() + timeout
    last_payload = {}
    while time.monotonic() < deadline:
        if initial_delay:
            await asyncio.sleep(min(initial_delay, max(0.0, deadline - time.monotonic())))
            initial_delay = 0
            if time.monotonic() >= deadline:
                break
        response = await client.get(task_url, headers=api_headers(provider=provider))
        response.raise_for_status()
        last_payload = response.json()
        task_data = last_payload.get("data") if isinstance(last_payload.get("data"), dict) else last_payload
        status = str(task_data.get("status") or task_data.get("task_status") or "").upper()
        if status in {"SUCCESS", "SUCCEED", "SUCCEEDED", "COMPLETED", "COMPLETE", "DONE", "FINISHED", "OK", "READY"}:
            return last_payload
        if status in {"FAILURE", "FAILED", "FAIL", "ERROR", "ERRORED", "CANCELED", "CANCELLED", "TIMEOUT", "REJECTED", "EXPIRED"}:
            error = task_data.get("error") if isinstance(task_data.get("error"), dict) else {}
            reason = task_data.get("fail_reason") or task_data.get("message") or error.get("message") or last_payload.get("message") or "生图任务失败"
            raise HTTPException(status_code=502, detail=f"生图任务失败：{reason}")
        await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
    raise HTTPException(status_code=504, detail=f"生图任务超时（已等待 {int(timeout)} 秒），task_id={task_id}")

def mindapi_banana_task_endpoint():
    return "https://www.mindapi.cc/pt/v1/api/generate/result"

async def wait_for_mindapi_banana_task(client, task_id, provider=None):
    task_url = mindapi_banana_task_endpoint()
    query_bodies = ({"taskId": task_id}, {"id": task_id}, {"task_id": task_id})
    deadline = time.monotonic() + IMAGE_TASK_TIMEOUT
    last_payload = {}
    while time.monotonic() < deadline:
        response = None
        for body in query_bodies:
            response = await client.post(task_url, headers=banana_api_headers(provider), json=body)
            if response.status_code not in {400, 422}:
                break
        response.raise_for_status()
        last_payload = response.json()
        try:
            extract_image(last_payload)
            return last_payload
        except HTTPException:
            pass
        status = image_task_status(last_payload)
        if status in {"SUCCESS", "SUCCEED", "SUCCEEDED", "COMPLETED", "COMPLETE", "DONE", "FINISHED", "OK", "READY"}:
            print("[BananaImage] task success without image", safe_upstream_summary(last_payload))
            raise HTTPException(status_code=502, detail="Banana task finished but no image was returned.")
        if status in {"FAILURE", "FAILED", "FAIL", "ERROR", "ERRORED", "CANCELED", "CANCELLED", "TIMEOUT", "REJECTED", "EXPIRED"}:
            reason = image_task_failure_reason(last_payload) or "Banana task failed"
            raise HTTPException(status_code=502, detail=reason)
        await asyncio.sleep(min(IMAGE_POLL_INTERVAL, max(0.0, deadline - time.monotonic())))
    print("[BananaImage] task timeout", {"task_id": task_id, "last_payload": safe_upstream_summary(last_payload)})
    raise HTTPException(status_code=504, detail=f"Banana task timed out: task_id={task_id}")

async def wait_for_banana_image_task(client, task_id, provider=None):
    if is_mindapi_provider(provider):
        try:
            return await wait_for_mindapi_banana_task(client, task_id, provider)
        except httpx.HTTPStatusError as exc:
            print(
                "[BananaImage] MindAPI task endpoint failed, fallback to default",
                {"task_id": task_id, "status": exc.response.status_code},
            )
    return await wait_for_image_task(client, task_id, provider)

from app.services.storage_service import output_storage
from app.services.storage_service import output_url_for
from app.services.storage_service import output_path_for
from app.services.storage_service import output_file_from_url
from app.services.storage_service import content_type_for_path
from app.services.storage_service import view_image
from app.services.storage_service import download_output
from app.services.storage_service import read_upload_limited
from app.services.storage_service import save_upload_limited
from app.services.storage_service import upload_image
from app.services.storage_service import upload_ai_reference






from app.services.digital_human_service import digital_human_default_config
from app.services.digital_human_service import merge_dict
from app.services.digital_human_service import load_digital_human_config
from app.services.digital_human_service import save_digital_human_config
from app.services.digital_human_service import normalize_digital_human_config
from app.services.digital_human_service import safe_join_url
from app.services.digital_human_service import normalize_tts_base_url
from app.services.digital_human_service import normalize_service_base_url
from app.services.digital_human_service import tts_port_from_base_url
from app.services.digital_human_service import public_url_for
from app.services.digital_human_service import digital_human_output_url
from app.services.digital_human_service import digital_human_media_url
from app.services.digital_human_service import is_digital_human_media_path_allowed
from app.services.digital_human_service import sanitize_output_filename
from app.services.digital_human_service import digital_human_local_path
from app.services.digital_human_service import list_heygem_video_inputs
from app.services.digital_human_service import digital_human_library_default
from app.services.digital_human_service import digital_human_stable_id
from app.services.digital_human_service import load_digital_human_library_raw
from app.services.digital_human_service import save_digital_human_library
from app.services.digital_human_service import normalize_library_video
from app.services.digital_human_service import normalize_library_person
from app.services.digital_human_service import merge_library_with_disk_videos
from app.services.digital_human_service import load_digital_human_library
from app.services.digital_human_service import find_library_person
from app.services.digital_human_service import find_library_video
from app.services.digital_human_service import enrich_voice_library
from app.services.digital_human_service import digital_human_library_response
from app.services.digital_human_service import assert_deletable_digital_human_media
from app.services.digital_human_service import digital_human_config
from app.services.digital_human_service import save_digital_human_config_api
from app.services.digital_human_service import digital_human_tts_status
from app.services.digital_human_service import digital_human_heygem_status
from app.services.digital_human_service import digital_human_media
from app.services.digital_human_service import digital_human_library
from app.services.digital_human_service import upsert_digital_human_person
from app.services.digital_human_service import add_digital_human_person_video
from app.services.digital_human_service import delete_digital_human_person
from app.services.digital_human_service import delete_digital_human_person_video
from app.services.digital_human_service import patch_digital_human_voice_meta
from app.services.digital_human_service import delete_digital_human_voice
from app.services.digital_human_service import upload_digital_human_asset
from app.services.digital_human_service import digital_human_tts
from app.services.digital_human_service import run_digital_human_task
from app.services.digital_human_service import digital_human_generate
from app.services.digital_human_service import digital_human_task
from app.services.tts_service import tts_status_payload
from app.services.tts_service import check_tts_health
from app.services.tts_service import start_tts_service_process
from app.services.tts_service import ensure_tts_service
from app.services.tts_service import get_gradio_client
from app.services.tts_service import tts_handle_file
from app.services.tts_service import gradio_handle_file
from app.services.tts_service import collect_strings
from app.services.tts_service import tts_voice_library_dir
from app.services.tts_service import tts_voice_embedding_dir
from app.services.tts_service import sanitize_tts_voice_name
from app.services.tts_service import tts_voice_embedding_for_name
from app.services.tts_service import tts_voice_file_for_name
from app.services.tts_service import save_tts_voice_preview_audio
from app.services.tts_service import enrich_tts_voice_item
from app.services.tts_service import normalize_tts_voice_list
from app.services.tts_service import tts_default_reference_audio
from app.services.tts_service import extract_tts_job_id
from app.services.tts_service import iter_tts_rows
from app.services.tts_service import tts_job_status_from_refresh
from app.services.tts_service import extract_tts_audio_candidate
from app.services.tts_service import save_tts_audio_candidate
from app.services.tts_service import save_tts_voice_sync
from app.services.tts_service import save_tts_voice
from app.services.tts_service import clamp_number
from app.services.tts_service import normalize_bool
from app.services.tts_service import normalize_tts_options
from app.services.tts_service import generate_digital_human_tts_sync
from app.services.tts_service import list_tts_voices_sync
from app.services.tts_service import list_tts_voices
from app.services.tts_service import run_subprocess_capture
from app.services.tts_service import generate_digital_human_tts
from app.services.heygem_service import get_heygem_gradio_client
from app.services.heygem_service import heygem_video_candidate_from_text
from app.services.heygem_service import heygem_is_video_candidate
from app.services.heygem_service import heygem_result_urls
from app.services.heygem_service import heygem_output_candidate
from app.services.heygem_service import heygem_resolve_local_result
from app.services.heygem_service import heygem_media_candidate
from app.services.heygem_service import heygem_api_base_url
from app.services.heygem_service import heygem_root_dir
from app.services.heygem_service import heygem_safe_job_code
from app.services.heygem_service import heygem_save_relative_path
from app.services.heygem_service import heygem_log_path
from app.services.heygem_service import heygem_log_cursor
from app.services.heygem_service import heygem_read_log_since
from app.services.heygem_service import heygem_blocking_reason_from_log
from app.services.heygem_service import heygem_progress_payload
from app.services.heygem_service import update_digital_human_task
from app.services.heygem_service import save_heygem_video_result_sync
from app.services.heygem_service import save_heygem_video_result
from app.services.heygem_service import check_heygem_health_sync
from app.services.heygem_service import check_heygem_health
from app.services.heygem_service import generate_heygem_video_sync
from app.services.heygem_service import generate_heygem_video
from app.services.heygem_service import generate_heygem_video_rest_sync
from app.services.heygem_service import generate_heygem_video_monitored
from app.services.heygem_service import heygem_task_id
from app.services.heygem_service import heygem_status
from app.services.heygem_service import submit_heygem_task
from app.services.heygem_service import poll_heygem_task




































TTS_EMO_METHODS = {
    "与音色参考音频相同",
    "使用情感参考音频",
    "使用情感向量控制",
    "使用情感描述文本控制",
}





























HEYGEM_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".m4v")






























def default_asset_library():
    return {
        "categories": [
            {"id": "characters", "name": "角色", "type": "image", "items": []},
            {"id": "scenes", "name": "场景", "type": "image", "items": []},
            {"id": "workflows", "name": "工作流", "type": "workflow", "items": []},
        ],
        "updated_at": now_ms(),
    }

def load_asset_library():
    if not os.path.exists(ASSET_LIBRARY_PATH):
        lib = default_asset_library()
        save_asset_library(lib)
        return lib
    try:
        with open(ASSET_LIBRARY_PATH, "r", encoding="utf-8") as f:
            lib = json.load(f)
    except Exception:
        lib = default_asset_library()
    cats = lib.get("categories") if isinstance(lib.get("categories"), list) else []
    if not any(c.get("type") == "workflow" for c in cats):
        cats.append({"id": "workflows", "name": "工作流", "type": "workflow", "items": []})
    lib["categories"] = cats
    lib["updated_at"] = int(lib.get("updated_at") or now_ms())
    return lib

def save_asset_library(lib):
    lib["updated_at"] = now_ms()
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ASSET_LIBRARY_PATH, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)

def find_asset_category(lib, category_id):
    for cat in lib.get("categories", []):
        if cat.get("id") == category_id:
            return cat
    return None

def sanitize_asset_name(name, fallback="asset"):
    name = re.sub(r'[\\/:*?"<>|]+', "_", str(name or fallback)).strip()
    return name[:120] or fallback


def convert_output_to_jpg(url, quality=88):
    path = output_file_from_url(url)
    if not path:
        return url
    root, ext = os.path.splitext(path)
    if ext.lower() in [".jpg", ".jpeg"]:
        return url
    jpg_path = f"{root}.jpg"
    try:
        with Image.open(path) as img:
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
                img = bg
            else:
                img = img.convert("RGB")
            img.save(jpg_path, "JPEG", quality=quality, optimize=True)
        try:
            root = ASSETS_DIR if os.path.commonpath([os.path.abspath(ASSETS_DIR), os.path.abspath(jpg_path)]) == os.path.abspath(ASSETS_DIR) else OUTPUT_DIR
        except ValueError:
            root = OUTPUT_DIR
        rel = os.path.relpath(jpg_path, root).replace("\\", "/")
        prefix = "/assets" if root == ASSETS_DIR else "/output"
        return f"{prefix}/{rel}"
    except Exception as e:
        print(f"转换 JPG 失败: {e}")
        return url

def reference_to_data_url(ref, max_size=None):
    """把本地输出文件转为 data URL（base64）。max_size 限制最长边像素，避免 payload 过大。"""
    path = output_file_from_url(ref.get("url", ""))
    if not path:
        return ref.get("url", "")
    if max_size:
        try:
            with Image.open(path) as img:
                img.load()
                w, h = img.size
                if max(w, h) > max_size:
                    img.thumbnail((max_size, max_size), Image.LANCZOS)
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                buf = BytesIO()
                fmt = "PNG" if img.mode == "RGBA" else "JPEG"
                img.save(buf, format=fmt, quality=88 if fmt == "JPEG" else None)
                encoded = base64.b64encode(buf.getvalue()).decode("ascii")
                mime = "image/png" if fmt == "PNG" else "image/jpeg"
                return f"data:{mime};base64,{encoded}"
        except Exception as e:
            print(f"reference resize failed, fallback to raw: {e}")
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:{content_type_for_path(path)};base64,{encoded}"

def request_reference_records(reference_images):
    records = []
    for ref in reference_images or []:
        data = ref.dict() if hasattr(ref, "dict") else dict(ref or {})
        if data.get("url") or data.get("data_url"):
            records.append(data)
    return records

def history_reference_records(refs):
    clean = []
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        item = {key: value for key, value in ref.items() if key != "data_url"}
        if item.get("url") or item.get("name"):
            clean.append(item)
    return clean

def compress_data_url_image(value, max_size=1536, jpeg_quality=88):
    if not isinstance(value, str) or not value.startswith("data:image/") or ";base64," not in value:
        return value
    header, encoded = value.split(";base64,", 1)
    try:
        raw = base64.b64decode(encoded)
        with Image.open(BytesIO(raw)) as img:
            img.load()
            if max_size and max(img.size) > max_size:
                img.thumbnail((max_size, max_size), Image.LANCZOS)
            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            if has_alpha:
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                fmt, mime = "PNG", "image/png"
            else:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                fmt, mime = "JPEG", "image/jpeg"
            buf = BytesIO()
            if fmt == "JPEG":
                img.save(buf, format=fmt, quality=jpeg_quality, optimize=True)
            else:
                img.save(buf, format=fmt, optimize=True)
            return f"data:{mime};base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    except Exception as e:
        print(f"data url image compress failed, fallback to raw: {e}")
        return value

def modelscope_image_url(value, max_size=1536):
    if not value:
        return value
    if isinstance(value, str) and (value.startswith("/output/") or value.startswith("/assets/")):
        return reference_to_data_url({"url": value}, max_size=max_size)
    if isinstance(value, str) and value.startswith("data:image/"):
        return compress_data_url_image(value, max_size=max_size)
    return value

def valid_video_image_input(value: str) -> bool:
    if not isinstance(value, str):
        return False
    value = value.strip()
    return (
        value.startswith("http://") or
        value.startswith("https://") or
        value.startswith("asset://") or
        (value.startswith("data:image/") and ";base64," in value)
    )

def valid_apimart_video_image_input(value: str) -> bool:
    if not isinstance(value, str):
        return False
    value = value.strip()
    return value.startswith("http://") or value.startswith("https://") or value.startswith("asset://")

def audio_reference_value(ref) -> str:
    ref_url = str(getattr(ref, "url", "") or "").strip()
    if not ref_url:
        return ""
    if ref_url.startswith("data:audio/") or ref_url.startswith("http://") or ref_url.startswith("https://"):
        return ref_url
    if ref_url.startswith("/output/") or ref_url.startswith("/assets/"):
        path = output_file_from_url(ref_url)
        if not path:
            raise HTTPException(status_code=400, detail=f"Audio input file is missing: {ref_url}")
        with open(path, "rb") as fh:
            encoded = base64.b64encode(fh.read()).decode("ascii")
        return f"data:{content_type_for_path(path)};base64,{encoded}"
    return ref_url

def audio_payload_values(refs):
    values = []
    for ref in refs or []:
        value = audio_reference_value(ref)
        if value:
            values.append(value)
    return values

def is_apimart_veo31_model(model: str) -> bool:
    return str(model or "").strip().lower().startswith("veo3.1")

def apimart_veo31_model(model: str) -> str:
    value = str(model or "").strip().lower()
    aliases = {
        "veo3.1": "veo3.1-fast",
        "veo3.1-pro": "veo3.1-quality",
        "veo3.1-preview": "veo3.1-fast",
    }
    value = aliases.get(value, value or "veo3.1-fast")
    allowed = {"veo3.1-fast", "veo3.1-quality", "veo3.1-lite"}
    return value if value in allowed else "veo3.1-fast"

def apimart_veo31_aspect(aspect: str) -> str:
    value = str(aspect or "16:9").strip()
    return value if value in {"16:9", "9:16"} else "16:9"

def apimart_veo31_resolution(resolution: str) -> str:
    value = str(resolution or "").strip().lower()
    aliases = {"": "720p", "auto": "720p", "480p": "720p", "780p": "720p", "1080": "1080p", "4k": "4k"}
    value = aliases.get(value, value)
    return value if value in {"720p", "1080p", "4k"} else "720p"

def apimart_upload_file_payload(path: str):
    """Return (filename, bytes, content_type), keeping APIMart VEO images under the documented 10MB limit."""
    max_bytes = 9_500_000
    size = os.path.getsize(path)
    if size <= max_bytes:
        with open(path, "rb") as fh:
            return os.path.basename(path), fh.read(), content_type_for_path(path)
    with Image.open(path) as img:
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        quality = 92
        while quality >= 62:
            buf = BytesIO()
            bg.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= max_bytes:
                name = os.path.splitext(os.path.basename(path))[0] + ".jpg"
                return name, data, "image/jpeg"
            quality -= 8
    raise ValueError("图片超过 10MB，且压缩后仍无法满足 VEO3.1 图片限制")

def invalid_video_image_preview(value: str) -> str:
    text = str(value or "")
    if text.startswith("data:"):
        return text.split(";base64,", 1)[0] + ";base64,..."
    return text[:120]

def extract_apimart_asset_url(payload):
    if isinstance(payload, list):
        for item in payload:
            found = extract_apimart_asset_url(item)
            if found:
                return found
        return ""
    if not isinstance(payload, dict):
        return ""
    url_keys = ("url", "asset_url", "assetUrl", "uri", "file_url", "fileUrl")
    for key in url_keys:
        value = str(payload.get(key) or "").strip()
        if valid_apimart_video_image_input(value):
            return value
    id_keys = ("asset_id", "assetId", "file_id", "fileId", "id")
    for key in id_keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value if value.startswith("asset://") else f"asset://{value}"
    for key in ("data", "file", "asset", "result"):
        found = extract_apimart_asset_url(payload.get(key))
        if found:
            return found
    return ""

def apimart_upload_payload_from_bytes(data: bytes, mime: str, name_hint: str = "image"):
    """把内存中的图片字节按 APIMart 的 10MB 限制压缩为可上传 payload。"""
    max_bytes = 9_500_000
    ext = mimetypes.guess_extension(mime or "image/png") or ".png"
    if len(data) <= max_bytes and (mime or "").lower() in ("image/png", "image/jpeg", "image/webp"):
        return f"{name_hint}{ext}", data, (mime or "image/png")
    with Image.open(BytesIO(data)) as img:
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        if has_alpha:
            base = img.convert("RGBA")
            bg = Image.new("RGB", base.size, (255, 255, 255))
            bg.paste(base, mask=base.split()[-1])
            target = bg
        else:
            target = img.convert("RGB")
        quality = 92
        while quality >= 62:
            buf = BytesIO()
            target.save(buf, format="JPEG", quality=quality, optimize=True)
            payload = buf.getvalue()
            if len(payload) <= max_bytes:
                return f"{name_hint}.jpg", payload, "image/jpeg"
            quality -= 8
    raise ValueError("data URL 图片超过 10MB，且压缩后仍无法满足 APIMart 限制")

async def upload_image_for_apimart(client, provider, ref_url: str) -> str:
    """把本地图片转成上游可接受的输入。
    按 APIMart 文档上传到 /v1/uploads/images，拿到可用于生成接口的 http/https URL。
    绝不把 /output/* 或 /assets/* 这类本地路径直接传给上游。
    返回上游可用 URL；返回值以 "ERR:" 开头表示具体失败原因（供前端展示）。"""
    ref_url = str(ref_url or "").strip()
    if not ref_url:
        return "ERR:空地址"
    # 已经是网络 URL 或 asset:// → 直接可用，无需上传
    if ref_url.startswith("http://") or ref_url.startswith("https://") or ref_url.startswith("asset://"):
        return ref_url
    base_url = video_api_root(provider)
    upload_url = f"{base_url}/v1/uploads/images"
    # data URL: 解码后直接上传到 APIMart
    if ref_url.startswith("data:"):
        try:
            if ";base64," not in ref_url:
                return "ERR:不支持的 data URL（缺少 base64 段）"
            header, encoded = ref_url.split(";base64,", 1)
            mime = header.split(":", 1)[1].split(";", 1)[0] if ":" in header else "image/png"
            raw = base64.b64decode(encoded)
            filename, content, ct = apimart_upload_payload_from_bytes(raw, mime, name_hint="canvas_image")
            files = {"file": (filename, content, ct)}
            resp = await client.post(upload_url, headers=api_headers(json_body=False, provider=provider), files=files, timeout=60)
            if resp.status_code in (200, 201):
                rj = resp.json()
                url = extract_apimart_asset_url(rj)
                if valid_apimart_video_image_input(url):
                    return url
                print(f"APIMart 上传 data URL 返回中未找到可用 asset/url: {str(rj)[:300]}")
                return "ERR:APIMart 上传响应未包含可用 URL"
            print(f"APIMart 上传 data URL 失败 ({resp.status_code}): {resp.text[:300]}")
            return f"ERR:APIMart 上传失败({resp.status_code})"
        except ValueError as e:
            return f"ERR:{e}"
        except Exception as e:
            print(f"APIMart 上传 data URL 异常: {e}")
            return f"ERR:上传异常 {e}"
    # 本地 /output/ 或 /assets/ 路径：先确认文件存在再上传
    if ref_url.startswith("/output/") or ref_url.startswith("/assets/"):
        path = output_file_from_url(ref_url)
        if not path:
            print(f"APIMart 上传跳过：本地文件不存在 {ref_url}")
            return "ERR:本地文件不存在或已被删除"
        try:
            filename, content, ct = apimart_upload_file_payload(path)
            files = {"file": (filename, content, ct)}
            resp = await client.post(upload_url, headers=api_headers(json_body=False, provider=provider), files=files, timeout=60)
            if resp.status_code in (200, 201):
                rj = resp.json()
                url = extract_apimart_asset_url(rj)
                if valid_apimart_video_image_input(url):
                    return url
                print(f"APIMart 文件上传返回中未找到可用 asset/url: {str(rj)[:300]}")
                return "ERR:APIMart 上传响应未包含可用 URL"
            print(f"APIMart 文件上传失败 ({resp.status_code}): {resp.text[:300]}")
            return f"ERR:APIMart 上传失败({resp.status_code})"
        except ValueError as e:
            return f"ERR:{e}"
        except Exception as e:
            print(f"APIMart 文件上传异常: {e}")
            return f"ERR:上传异常 {e}"
    return "ERR:不支持的图片来源（仅支持 http/https/asset/data 或本地 /output/ /assets/ 路径）"

async def save_ai_image_to_output(image_data, prefix="online_", category="output"):
    filename = f"{prefix}{uuid.uuid4().hex[:10]}.png"
    path = output_path_for(filename, category)
    if image_data["type"] == "b64":
        mime_type = str(image_data.get("mime_type") or "").lower()
        if "jpeg" in mime_type or "jpg" in mime_type:
            filename = filename[:-4] + ".jpg"
            path = output_path_for(filename, category)
        elif "webp" in mime_type:
            filename = filename[:-4] + ".webp"
            path = output_path_for(filename, category)
        with open(path, "wb") as f:
            f.write(base64.b64decode(image_data["value"]))
        return output_url_for(filename, category)
    value = image_data["value"]
    if value.startswith("/output/") or value.startswith("/assets/"):
        return value
    try:
        timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(value)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if "jpeg" in content_type or "jpg" in content_type:
                filename = filename[:-4] + ".jpg"
                path = output_path_for(filename, category)
            elif "webp" in content_type:
                filename = filename[:-4] + ".webp"
                path = output_path_for(filename, category)
            with open(path, "wb") as f:
                f.write(response.content)
            return output_url_for(filename, category)
    except Exception as e:
        print(f"保存上游图片失败: {e}")
        return value

async def normalize_ai_image_to_size(image_data, target_size):
    target_width, target_height = parse_size_pair(target_size)
    if not target_width or not target_height:
        return image_data
    try:
        if image_data.get("type") == "b64":
            raw = base64.b64decode(image_data["value"])
        else:
            value = image_data.get("value") or ""
            if value.startswith("/output/") or value.startswith("/assets/"):
                path = output_file_from_url(value)
                if not path:
                    return image_data
                with open(path, "rb") as f:
                    raw = f.read()
            elif value.startswith("http://") or value.startswith("https://"):
                timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    response = await client.get(value)
                    response.raise_for_status()
                    raw = response.content
            else:
                return image_data
        with Image.open(BytesIO(raw)) as img:
            img = img.convert("RGB")
            if img.size == (target_width, target_height):
                return image_data
            target_ratio = target_width / max(1, target_height)
            current_ratio = img.size[0] / max(1, img.size[1])
            if current_ratio > target_ratio:
                new_width = max(1, int(round(img.size[1] * target_ratio)))
                left = max(0, (img.size[0] - new_width) // 2)
                img = img.crop((left, 0, left + new_width, img.size[1]))
            elif current_ratio < target_ratio:
                new_height = max(1, int(round(img.size[0] / target_ratio)))
                top = max(0, (img.size[1] - new_height) // 2)
                img = img.crop((0, top, img.size[0], top + new_height))
            img = img.resize((target_width, target_height), Image.LANCZOS)
            buffer = BytesIO()
            img.save(buffer, format="PNG")
        return {"type": "b64", "value": base64.b64encode(buffer.getvalue()).decode("ascii"), "mime_type": "image/png"}
    except Exception as e:
        print(f"resize AI image failed: {e}")
        return image_data

async def save_remote_video_to_output(url, prefix="video_", category="output"):
    if not url:
        return ""
    if url.startswith("/output/") or url.startswith("/assets/"):
        return url
    filename = f"{prefix}{uuid.uuid4().hex[:10]}.mp4"
    path = output_path_for(filename, category)
    try:
        async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type") or "").lower()
            clean_path = urllib.parse.urlparse(url).path
            ext = os.path.splitext(clean_path)[1].lower()
            if ext in {".mp4", ".webm", ".mov"}:
                filename = filename[:-4] + ext
                path = output_path_for(filename, category)
            elif "webm" in content_type:
                filename = filename[:-4] + ".webm"
                path = output_path_for(filename, category)
            elif "quicktime" in content_type or "mov" in content_type:
                filename = filename[:-4] + ".mov"
                path = output_path_for(filename, category)
            with open(path, "wb") as f:
                f.write(response.content)
            return output_url_for(filename, category)
    except Exception as e:
        print(f"保存上游视频失败: {e}")
        return url

async def save_remote_audio_to_output(url, prefix="audio_", category="output"):
    if not url:
        return ""
    if url.startswith("/output/") or url.startswith("/assets/"):
        return url
    filename = f"{prefix}{uuid.uuid4().hex[:10]}.mp3"
    path = output_path_for(filename, category)
    try:
        async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type") or "").lower()
            clean_path = urllib.parse.urlparse(url).path
            ext = os.path.splitext(clean_path)[1].lower()
            if ext in {".wav", ".mp3", ".m4a", ".ogg"}:
                filename = filename[:-4] + ext
                path = output_path_for(filename, category)
            elif "wav" in content_type:
                filename = filename[:-4] + ".wav"
                path = output_path_for(filename, category)
            elif "ogg" in content_type:
                filename = filename[:-4] + ".ogg"
                path = output_path_for(filename, category)
            elif "mp4" in content_type or "m4a" in content_type:
                filename = filename[:-4] + ".m4a"
                path = output_path_for(filename, category)
            with open(path, "wb") as f:
                f.write(response.content)
            return output_url_for(filename, category)
    except Exception as e:
        print(f"save remote audio failed: {e}")
        return url

def parse_size_pair(size):
    match = re.fullmatch(r"\s*(\d+)\s*[xX*]\s*(\d+)\s*", str(size or ""))
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))

GPT_IMAGE2_MAX_EDGE = 3840
GPT_IMAGE2_MAX_PIXELS = 8_294_400
GPT_IMAGE2_MIN_PIXELS = 655_360
IMAGE_ASPECT_OPTIONS = {
    "1:1", "3:2", "2:3", "5:4", "4:5", "4:3", "3:4",
    "16:9", "9:16", "21:9", "9:21", "2:1", "1:2", "3:1", "1:3",
}
IMAGE_RESOLUTION_OPTIONS = {"1K", "2K", "4K"}
GPT_IMAGE2_COMPATIBLE_SIZES = [
    "1024x1024",
    "1024x1536",
    "1536x1024",
    "1024x1792",
    "1792x1024",
]
GPT_IMAGE2_SIZE_TABLE = {
    ("1:1", "1K"): "1024x1024", ("1:1", "2K"): "2048x2048", ("1:1", "4K"): "2880x2880",
    ("16:9", "1K"): "1280x720", ("16:9", "2K"): "2048x1152", ("16:9", "4K"): "3840x2160",
    ("9:16", "1K"): "720x1280", ("9:16", "2K"): "1152x2048", ("9:16", "4K"): "2160x3840",
    ("4:3", "1K"): "1152x864", ("4:3", "2K"): "2304x1728", ("4:3", "4K"): "3264x2448",
    ("3:4", "1K"): "864x1152", ("3:4", "2K"): "1728x2304", ("3:4", "4K"): "2448x3264",
    ("3:2", "1K"): "1536x1024", ("3:2", "2K"): "2048x1360", ("3:2", "4K"): "3504x2336",
    ("2:3", "1K"): "1024x1536", ("2:3", "2K"): "1360x2048", ("2:3", "4K"): "2336x3504",
    ("5:4", "1K"): "1120x896", ("5:4", "2K"): "2240x1792", ("5:4", "4K"): "3200x2560",
    ("4:5", "1K"): "896x1120", ("4:5", "2K"): "1792x2240", ("4:5", "4K"): "2560x3200",
    ("21:9", "1K"): "1456x624", ("21:9", "2K"): "2912x1248", ("21:9", "4K"): "3840x1648",
    ("9:21", "1K"): "624x1456", ("9:21", "2K"): "1248x2912", ("9:21", "4K"): "1648x3840",
    ("1:3", "1K"): "688x2048", ("1:3", "4K"): "1280x3840",
    ("3:1", "1K"): "2048x688", ("3:1", "4K"): "3840x1280",
    ("2:1", "1K"): "1536x768", ("2:1", "2K"): "3072x1536", ("2:1", "4K"): "3840x1920",
    ("1:2", "1K"): "768x1536", ("1:2", "2K"): "1536x3072", ("1:2", "4K"): "1920x3840",
}

def is_gpt_image_2_model(model):
    return re.match(r"^gpt-image-2(?:$|[-_])", str(model or "").strip(), re.I) is not None

def is_nano_banana_model(model):
    return re.match(r"^nano-banana(?:$|[-_])", str(model or "").strip(), re.I) is not None

def normalize_image_aspect_ratio(value, size=""):
    raw = str(value or "").strip().replace(" ", "")
    if raw in IMAGE_ASPECT_OPTIONS:
        return raw
    aspect, _resolution = apimart_size_resolution(size)
    return aspect if aspect in IMAGE_ASPECT_OPTIONS else "1:1"

def normalize_image_resolution(value, size=""):
    raw = str(value or "").strip().upper()
    if raw in IMAGE_RESOLUTION_OPTIONS:
        return raw
    _aspect, resolution = apimart_size_resolution(size)
    raw = str(resolution or "1k").upper()
    return raw if raw in IMAGE_RESOLUTION_OPTIONS else "1K"

def image_size_context(size, aspect_ratio="", resolution=""):
    aspect = normalize_image_aspect_ratio(aspect_ratio, size)
    res = normalize_image_resolution(resolution, size)
    requested_size = str(size or "").strip()
    if not parse_size_pair(requested_size)[0]:
        requested_size = size_from_aspect_resolution(aspect, res)
    return {
        "size": requested_size,
        "aspect_ratio": aspect,
        "resolution": res,
    }

def aspect_ratio_value(aspect_ratio, fallback_size=None):
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", str(aspect_ratio or ""))
    if match:
        return max(1, int(match.group(1))) / max(1, int(match.group(2)))
    if fallback_size:
        return max(1, fallback_size[0]) / max(1, fallback_size[1])
    return 1.0

def gpt_image_2_upstream_size(aspect_ratio, fallback_size=None):
    target_ratio = aspect_ratio_value(aspect_ratio, fallback_size)
    def score(size_text):
        width, height = parse_size_pair(size_text)
        ratio = width / max(1, height)
        return abs(math.log(max(0.01, ratio) / max(0.01, target_ratio)))
    return min(GPT_IMAGE2_COMPATIBLE_SIZES, key=score)

def size_from_aspect_resolution(aspect_ratio, resolution):
    table_size = GPT_IMAGE2_SIZE_TABLE.get((aspect_ratio, resolution))
    if table_size:
        return table_size
    edge = {"1K": 1024, "2K": 2048, "4K": 3840}.get(resolution, 1024)
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", str(aspect_ratio or ""))
    if not match:
        return f"{edge}x{edge}"
    aspect_w = max(1, int(match.group(1)))
    aspect_h = max(1, int(match.group(2)))
    long_edge = max(aspect_w, aspect_h)
    scale = edge / long_edge
    width = max(16, int(round(aspect_w * scale / 16)) * 16)
    height = max(16, int(round(aspect_h * scale / 16)) * 16)
    pixel_limit = GPT_IMAGE2_MAX_PIXELS
    if width * height > pixel_limit:
        shrink = (pixel_limit / max(1, width * height)) ** 0.5
        width = max(16, int((width * shrink) // 16) * 16)
        height = max(16, int((height * shrink) // 16) * 16)
    return f"{width}x{height}"

def gpt_image_2_request_size(size, aspect_ratio="", resolution=""):
    ctx = image_size_context(size, aspect_ratio, resolution)
    requested_size = GPT_IMAGE2_SIZE_TABLE.get((ctx["aspect_ratio"], ctx["resolution"])) or ctx["size"]
    requested_width, requested_height = parse_size_pair(requested_size)
    fallback = (requested_width, requested_height) if requested_width and requested_height else None
    return gpt_image_2_upstream_size(ctx["aspect_ratio"], fallback)

def gpt_image_2_target_size(size, aspect_ratio="", resolution=""):
    ctx = image_size_context(size, aspect_ratio, resolution)
    return GPT_IMAGE2_SIZE_TABLE.get((ctx["aspect_ratio"], ctx["resolution"])) or ctx["size"]

def normalize_gpt_image_2_size(size):
    width, height = parse_size_pair(size)
    if not width or not height:
        return size or "auto"
    if width == height and (width > 2048 or width * height > 4_194_304):
        return "3840x2160"
    ratio = width / height
    if ratio > 3:
        width = height * 3
    elif ratio < 1 / 3:
        height = width * 3
    scale = min(
        1.0,
        GPT_IMAGE2_MAX_EDGE / max(width, height),
        (GPT_IMAGE2_MAX_PIXELS / max(1, width * height)) ** 0.5,
    )
    width = max(16, int((width * scale) // 16) * 16)
    height = max(16, int((height * scale) // 16) * 16)
    if width * height < GPT_IMAGE2_MIN_PIXELS:
        grow = (GPT_IMAGE2_MIN_PIXELS / max(1, width * height)) ** 0.5
        width = int((width * grow + 15) // 16) * 16
        height = int((height * grow + 15) // 16) * 16
    return f"{width}x{height}"

def apimart_size_resolution(size):
    width, height = parse_size_pair(size)
    if not width or not height:
        raw = str(size or "").strip().lower()
        if raw in {"1k", "2k", "4k"}:
            return "1:1", raw
        if re.fullmatch(r"(auto|\d+\s*:\s*\d+)", raw):
            return raw.replace(" ", ""), "1k"
        return "1:1", "1k"
    long_edge = max(width, height)
    pixels = width * height
    if long_edge >= 3000 or pixels > 4_500_000:
        resolution = "4k"
    elif long_edge >= 1800 or pixels > 1_800_000:
        resolution = "2k"
    else:
        resolution = "1k"
    common = [
        (1, 1, "1:1"), (3, 2, "3:2"), (2, 3, "2:3"), (4, 3, "4:3"), (3, 4, "3:4"),
        (5, 4, "5:4"), (4, 5, "4:5"), (16, 9, "16:9"), (9, 16, "9:16"),
        (2, 1, "2:1"), (1, 2, "1:2"), (3, 1, "3:1"), (1, 3, "1:3"),
        (21, 9, "21:9"), (9, 21, "9:21"),
    ]
    ratio = width / height
    best = min(common, key=lambda item: abs(ratio - item[0] / item[1]))
    return best[2], resolution

async def generate_modelscope_provider_image(prompt, size, model, reference_images=None, provider=None):
    clean_token = MODELSCOPE_API_KEY.strip()
    if not clean_token:
        raise HTTPException(status_code=400, detail="未配置 ModelScope API Key，请在 API 设置中填写。")
    width, height = parse_size_pair(size)
    refs = []
    for ref in (reference_images or [])[:4]:
        if not ref.get("url"):
            continue
        # 把参考图压缩为 data URL，避免 base64 payload 过大导致 MS 内部任务失败
        refs.append(modelscope_image_url(ref.get("url", ""), max_size=1536))
    headers = {
        "Authorization": f"Bearer {clean_token}",
        "Content-Type": "application/json",
        "X-ModelScope-Async-Mode": "true",
    }
    payload = {
        "model": selected_model(model, "Tongyi-MAI/Z-Image-Turbo"),
        "prompt": prompt.strip(),
    }
    if width and height:
        payload["width"] = width
        payload["height"] = height
        payload["size"] = f"{width}x{height}"
    if refs:
        payload["image_url"] = refs

    base_root = ((provider or {}).get("base_url") or MODELSCOPE_CHAT_BASE_URL).rstrip("/")
    api_root = base_root if base_root.endswith("/v1") else f"{base_root}/v1"
    async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
        submit_res = await client.post(f"{api_root}/images/generations", headers=headers, json=payload)
        submit_res.raise_for_status()
        raw = submit_res.json()
        task_id = raw.get("task_id")
        if not task_id:
            try:
                return extract_image(raw), raw
            except HTTPException:
                raise HTTPException(status_code=502, detail=f"ModelScope 未返回 task_id：{raw}")

        deadline = time.monotonic() + AI_REQUEST_TIMEOUT
        last_payload = raw
        while time.monotonic() < deadline:
            await asyncio.sleep(IMAGE_POLL_INTERVAL)
            result = await client.get(
                f"{api_root}/tasks/{task_id}",
                headers={**headers, "X-ModelScope-Task-Type": "image_generation"},
            )
            result.raise_for_status()
            data = result.json()
            last_payload = data
            status = str(data.get("task_status") or "").upper()
            if status == "SUCCEED":
                images = data.get("output_images") or []
                if not images:
                    raise HTTPException(status_code=502, detail=f"ModelScope 成功但没有返回图片：{data}")
                return {"type": "url", "value": images[0]}, data
            if status in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED", "TIMEOUT", "REVOKED"}:
                detail = data.get("error_info") or data.get("message") or data.get("detail") or str(data)
                raise HTTPException(status_code=502, detail=f"ModelScope 任务失败：{detail}")
        raise HTTPException(status_code=504, detail=f"ModelScope 生图任务超时：{last_payload}")

def gemini_model_name(model):
    value = selected_model(model, "gemini-3-pro-image-preview").strip()
    return value[len("models/"):] if value.startswith("models/") else value

def gemini_endpoint_url(provider, model):
    model_name = urllib.parse.quote(gemini_model_name(model), safe="")
    return provider_endpoint_url(provider, "image_generation_endpoint", f"/v1beta/models/{model_name}:generateContent")

def gemini_image_config(size):
    width, height = parse_size_pair(size)
    if not width or not height:
        raw = str(size or "").strip().upper()
        if raw in {"1K", "2K", "4K"}:
            return {"aspectRatio": "1:1", "imageSize": raw}
        if re.fullmatch(r"\d+\s*:\s*\d+", raw):
            return {"aspectRatio": raw.replace(" ", ""), "imageSize": "1K"}
        return {"aspectRatio": "1:1", "imageSize": "2K"}
    aspect_ratio, resolution = apimart_size_resolution(size)
    return {"aspectRatio": aspect_ratio, "imageSize": resolution.upper()}

def gemini_reference_part(ref):
    value = reference_to_data_url(ref, max_size=1536)
    if not value:
        return None
    if isinstance(value, str) and value.startswith("data:image/") and ";base64," in value:
        header, encoded = value.split(";base64,", 1)
        mime_type = header.replace("data:", "", 1) or "image/png"
        return {"inlineData": {"mimeType": mime_type, "data": encoded}}
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return {"fileData": {"mimeType": "image/png", "fileUri": value}}
    return None

async def generate_gemini_provider_image(prompt, size, model, reference_images=None, provider=None):
    model_name = gemini_model_name(model)
    endpoint = gemini_endpoint_url(provider, model_name)
    parts = [{"text": prompt.strip()}]
    for ref in (reference_images or [])[:16]:
        part = gemini_reference_part(ref)
        if part:
            parts.append(part)
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": gemini_image_config(size),
        },
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=1800.0, write=120.0, pool=20.0)) as client:
        response = await client.post(endpoint, headers=api_headers(provider=provider), json=body)
        response.raise_for_status()
        raw = response.json()
        return extract_image(raw), raw

def volcengine_endpoint_url(provider):
    return provider_endpoint_url(provider, "image_generation_endpoint", "/api/v3/images/generations")

def volcengine_image_payload(ref):
    value = reference_to_data_url(ref, max_size=1536)
    if not value:
        return None
    return value

async def generate_volcengine_provider_image(prompt, size, model, reference_images=None, provider=None):
    endpoint = volcengine_endpoint_url(provider)
    body = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": "url",
    }
    images = [volcengine_image_payload(ref) for ref in (reference_images or [])[:10]]
    images = [value for value in images if value]
    if images:
        body["image"] = images
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=1800.0, write=120.0, pool=20.0)) as client:
        response = await client.post(endpoint, headers=api_headers(provider=provider), json=body)
        response.raise_for_status()
        raw = response.json()
        return extract_image(raw), raw

def openai_base_url(provider):
    base_url = str((provider or {}).get("base_url") or AI_BASE_URL).strip().rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{(provider or {}).get('name') or (provider or {}).get('id') or 'API'} 鏈厤缃?Base URL")
    return base_url

def openai_path_url(provider, path):
    base_url = openai_base_url(provider)
    if base_url.endswith("/v1") and path.startswith("/v1/"):
        return f"{base_url}{path[3:]}"
    return f"{base_url}{path}"

def banana_image_endpoint(provider):
    if is_mindapi_provider(provider):
        return "https://www.mindapi.cc/pt/v1/api/generate"
    return provider_endpoint_url(provider, "image_generation_endpoint", "/v1/images/generations")

def banana_api_headers(provider):
    headers = api_headers(provider=provider)
    headers["User-Agent"] = "ComfyUI-Lingsi-MindAPI-Node/1.0"
    headers["Connection"] = "close"
    return headers

def banana_reference_summary(value):
    text = str(value or "")
    if text.startswith("data:image/"):
        prefix = text.split(",", 1)[0]
        return {"type": "data_url", "prefix": prefix, "length": len(text)}
    if text.startswith(("http://", "https://")):
        parsed = urllib.parse.urlsplit(text)
        return {"type": "remote_url", "host": parsed.netloc, "path": parsed.path[-48:]}
    return {"type": "other", "length": len(text)}

def banana_reference_to_data_url(ref):
    data_url = str((ref or {}).get("data_url") or "").strip()
    if data_url.startswith("data:image/") and ";base64," in data_url:
        return data_url
    return reference_to_data_url(ref)

def banana_reference_to_base64(ref):
    value = banana_reference_to_data_url(ref)
    if isinstance(value, str) and value.startswith("data:image/") and ";base64," in value:
        return value.split(";base64,", 1)[1].strip()
    return ""

def banana_base64_summary(value):
    text = str(value or "")
    return {"type": "base64", "length": len(text)}

def build_mindapi_banana_body(prompt, model, ctx, images):
    return {
        "model": model,
        "prompt": str(prompt or "").strip(),
        "images": images,
        "aspectRatio": ctx["aspect_ratio"],
        "imageSize": ctx["resolution"],
        "replyType": "json",
    }

async def generate_banana_provider_image(prompt, size, quality, model, reference_images=None, provider=None, aspect_ratio="", resolution=""):
    ctx = image_size_context(size, aspect_ratio, resolution)
    endpoint = banana_image_endpoint(provider)
    mindapi_banana = is_mindapi_provider(provider)
    raw_refs = [ref for ref in (reference_images or [])[:16] if isinstance(ref, dict) and (ref.get("url") or ref.get("data_url"))]
    if mindapi_banana:
        refs = []
        for ref in raw_refs:
            value = banana_reference_to_base64(ref)
            if value:
                refs.append(value)
            else:
                print("[BananaImage] skipped reference", safe_upstream_summary(ref))
        if raw_refs and not refs:
            raise HTTPException(status_code=400, detail="Banana 图生图参考图没有转成 base64，请刷新页面后重新上传参考图。")
        body = build_mindapi_banana_body(prompt, model, ctx, refs)
        reference_summaries = [banana_base64_summary(item) for item in refs]
    else:
        refs = []
        for ref in raw_refs:
            value = banana_reference_to_data_url(ref)
            if value:
                refs.append(value)
            else:
                print("[BananaImage] skipped reference", safe_upstream_summary(ref))
        if raw_refs and not refs:
            raise HTTPException(status_code=400, detail="Reference images were selected, but none could be prepared for Banana.")
        body = {
            "prompt": str(prompt or "").strip(),
            "urls": refs,
            "model": model,
            "aspectRatio": ctx["aspect_ratio"],
            "imageSize": ctx["resolution"],
        }
        reference_summaries = [banana_reference_summary(item) for item in refs]
    request_debug = {
        "endpoint": endpoint,
        "model": model,
        "mindapi_banana": mindapi_banana,
        "has_reference": bool(raw_refs),
        "aspectRatio": body.get("aspectRatio", ""),
        "imageSize": body.get("imageSize", ""),
        "body_keys": list(body.keys()),
        "selected_reference_count": len(raw_refs),
        "image_count": len(refs) if mindapi_banana else 0,
        "url_count": 0 if mindapi_banana else len(refs),
        "reference_summaries": reference_summaries,
    }
    print("[BananaImage] request", request_debug)
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=1800.0, write=120.0, pool=20.0)) as client:
        response = await client.post(endpoint, headers=banana_api_headers(provider), json=body)
        response.raise_for_status()
        raw = response.json()
        if isinstance(raw, dict):
            raw["_banana_request_debug"] = request_debug
        try:
            return extract_image(raw), raw
        except HTTPException as image_error:
            task_id = extract_task_id(raw)
            if not task_id:
                print("[BananaImage] no image or task id", safe_upstream_summary(raw))
                raise image_error
        print("[BananaImage] polling task", {"task_id": task_id, "provider_id": (provider or {}).get("id")})
        task_result = await wait_for_banana_image_task(client, task_id, provider)
        if isinstance(task_result, dict):
            task_result["_banana_request_debug"] = request_debug
        return extract_image(task_result), task_result

def runninghub_api_headers(provider):
    api_key = os.getenv(provider_key_env(provider["id"]), "")
    if not api_key:
        raise HTTPException(status_code=400, detail="未配置 RunningHub API Key，请在 API 设置中填写。")
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json", "Content-Type": "application/json"}

def runninghub_task_endpoint(provider, model):
    model_path = str(model or "").strip().strip("/")
    if not model_path:
        model_path = RUNNINGHUB_DEFAULT_IMAGE_MODELS[0]
    if model_path.startswith("/openapi/"):
        return runninghub_endpoint_url(provider, model_path)
    if model_path.startswith("openapi/"):
        return runninghub_endpoint_url(provider, f"/{model_path}")
    return runninghub_endpoint_url(provider, f"/openapi/v2/{model_path}")

def runninghub_query_status(raw):
    if not isinstance(raw, dict):
        return ""
    values = [
        raw.get("status"),
        raw.get("state"),
        raw.get("taskStatus"),
        raw.get("task_status"),
    ]
    data = raw.get("data")
    if isinstance(data, dict):
        values.extend([data.get("status"), data.get("state"), data.get("taskStatus"), data.get("task_status")])
    for value in values:
        if value is not None:
            return str(value).lower()
    return ""

def runninghub_extract_task_id(raw):
    if not isinstance(raw, dict):
        return ""
    for key in ("taskId", "task_id", "id"):
        if raw.get(key):
            return str(raw[key])
    data = raw.get("data")
    if isinstance(data, dict):
        for key in ("taskId", "task_id", "id"):
            if data.get(key):
                return str(data[key])
    return ""

def runninghub_extract_image(raw):
    if not isinstance(raw, dict):
        raise HTTPException(status_code=502, detail="RunningHub 返回格式不是 JSON 对象")
    containers = [raw]
    data = raw.get("data")
    if isinstance(data, dict):
        containers.append(data)
    for container in containers:
        results = container.get("results") or container.get("result") or container.get("outputs") or container.get("output")
        if isinstance(results, dict):
            results = [results]
        if isinstance(results, list):
            for item in results:
                if isinstance(item, str) and item.startswith(("http://", "https://")):
                    return {"type": "url", "value": item}
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "url" and item.get("value"):
                    return {"type": "url", "value": item["value"]}
                if item.get("type") == "b64" and item.get("value"):
                    return {"type": "b64", "value": item["value"], "mime_type": item.get("mime_type") or "image/png"}
                url = item.get("url") or item.get("fileUrl") or item.get("file_url") or item.get("download_url") or item.get("imageUrl") or item.get("image_url")
                if isinstance(url, list) and url:
                    url = url[0]
                if isinstance(url, str) and url:
                    return {"type": "url", "value": url}
    return extract_image(raw)

async def runninghub_upload_reference(client, provider, ref):
    path = output_file_from_url(ref.get("url", ""))
    if not path:
        value = ref.get("url", "")
        return value if str(value).startswith(("http://", "https://")) else ""
    upload_url = runninghub_endpoint_url(provider, "/openapi/v2/media/upload/binary")
    api_key = os.getenv(provider_key_env(provider["id"]), "")
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    with open(path, "rb") as fh:
        files = {"file": (os.path.basename(path), fh, content_type_for_path(path))}
        response = await client.post(upload_url, headers=headers, files=files, timeout=120)
    response.raise_for_status()
    raw = response.json()
    data = raw.get("data") if isinstance(raw, dict) else None
    candidates = [raw, data] if isinstance(data, dict) else [raw]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        value = item.get("download_url") or item.get("downloadUrl") or item.get("url") or item.get("fileUrl") or item.get("file_url")
        if value:
            return str(value)
    raise HTTPException(status_code=502, detail=f"RunningHub 上传图片未返回 download_url：{raw}")

async def wait_for_runninghub_image_task(client, provider, task_id):
    query_url = runninghub_endpoint_url(provider, "/openapi/v2/query")
    deadline = time.monotonic() + 1800
    last_payload = None
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        response = await client.post(query_url, headers=runninghub_api_headers(provider), json={"taskId": task_id})
        response.raise_for_status()
        raw = response.json()
        last_payload = raw
        status = runninghub_query_status(raw)
        if status in {"success", "succeeded", "completed", "complete", "finished", "finish", "done", "3"}:
            return raw
        if status in {"failed", "fail", "error", "canceled", "cancelled", "4"}:
            raise HTTPException(status_code=502, detail=f"RunningHub 任务失败：{raw}")
        try:
            return {"data": {"results": [runninghub_extract_image(raw)]}}
        except HTTPException:
            pass
    raise HTTPException(status_code=504, detail=f"RunningHub 生图任务超时：{last_payload}")

async def generate_runninghub_provider_image(prompt, size, model, reference_images=None, provider=None):
    endpoint = runninghub_task_endpoint(provider, model)
    width, height = parse_size_pair(size)
    body = {"prompt": prompt}
    if width and height:
        body.update({"width": width, "height": height})
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=1800.0, write=180.0, pool=20.0)) as client:
        image_urls = []
        for ref in (reference_images or [])[:10]:
            url = await runninghub_upload_reference(client, provider, ref)
            if url:
                image_urls.append(url)
        if image_urls:
            body["imageUrls"] = image_urls
        response = await client.post(endpoint, headers=runninghub_api_headers(provider), json=body)
        response.raise_for_status()
        raw = response.json()
        try:
            return runninghub_extract_image(raw), raw
        except HTTPException:
            task_id = runninghub_extract_task_id(raw)
            if not task_id:
                raise HTTPException(status_code=502, detail=f"RunningHub 未返回 taskId 或图片结果：{raw}")
        result = await wait_for_runninghub_image_task(client, provider, task_id)
        return runninghub_extract_image(result), result

async def generate_ai_image(prompt, size, quality, model, reference_images=None, provider_id="comfly", aspect_ratio="", resolution=""):
    provider = get_api_provider(provider_id)
    if provider["id"] == "modelscope":
        return await generate_modelscope_provider_image(prompt, size, model, reference_images, provider)
    if is_runninghub_provider(provider):
        return await generate_runninghub_provider_image(prompt, size, model, reference_images, provider)
    if is_gemini_provider(provider):
        return await generate_gemini_provider_image(prompt, size, model, reference_images, provider)
    if is_volcengine_provider(provider):
        return await generate_volcengine_provider_image(prompt, size, model, reference_images, provider)
    if is_nano_banana_model(model):
        return await generate_banana_provider_image(prompt, size, quality, model, reference_images, provider, aspect_ratio, resolution)
    is_gpt2 = is_gpt_image_2_model(model)
    is_apimart = is_apimart_provider(provider)
    quality = str(quality or "").strip().lower()
    if quality not in {"low", "medium", "high"}:
        quality = ""
    if is_gpt_image_2_model(model) and not is_apimart:
        size = gpt_image_2_request_size(size, aspect_ratio, resolution)
    base_url = (provider.get("base_url") or AI_BASE_URL).rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    gen_url = provider_endpoint_url(provider, "image_generation_endpoint", "/v1/images/generations")
    edit_url = provider_endpoint_url(provider, "image_edit_endpoint", "/v1/images/edits")
    refs = [ref for ref in (reference_images or []) if ref.get("url")]
    mask_refs = [ref for ref in refs if str(ref.get("role") or "").strip().lower() == "mask" or str(ref.get("name") or "").lower().endswith("_mask.png")]
    image_refs = [ref for ref in refs if ref not in mask_refs]
    request_timeout = httpx.Timeout(connect=20.0, read=1800.0, write=120.0, pool=20.0) if (is_gpt2 or is_apimart) else AI_REQUEST_TIMEOUT
    async with httpx.AsyncClient(timeout=request_timeout) as client:
        response = None
        async def post_openai_edits(edit_files=None):
            data = {"model": model, "prompt": prompt, "size": size}
            if quality:
                data["quality"] = quality
            return await client.post(
                edit_url,
                headers=api_headers(json_body=False, provider=provider),
                data=data,
                files=edit_files if edit_files is not None else {},
            )

        if is_apimart:
            apimart_size, resolution = apimart_size_resolution(size)
            # APIMart 的 GPT-Image-2 图生图仍走 /images/generations，
            # 通过 image_urls 传参考图，不使用 OpenAI multipart /images/edits。
            body = {
                "model": model,
                "prompt": prompt,
                "n": 1,
                "size": apimart_size,
                "resolution": resolution,
                "official_fallback": False,
            }
            if image_refs:
                body["image_urls"] = [reference_to_data_url(ref, max_size=1536) for ref in image_refs[:16]]
            response = await client.post(gen_url, headers=api_headers(provider=provider), json=body)
        elif is_gpt2 and not image_refs and not mask_refs:
            body = {"model": model, "prompt": prompt, "size": size}
            if quality:
                body["quality"] = quality
            response = await client.post(gen_url, headers=api_headers(provider=provider), json=body)
            if response.status_code >= 400 and images_api_unsupported(response):
                response = await post_openai_edits()
        elif image_refs:
            # 1) OpenAI 协议的图生图/编辑用 multipart 提交到 /images/edits；
            # GPT-Image-2 参考图不能走 /images/generations JSON，否则部分平台会忽略原图或报 Images API unsupported。
            files = []
            opened = []
            edit_failed_status = None
            edit_failed_text = ""
            try:
                for ref in image_refs[:4]:
                    path = output_file_from_url(ref.get("url", ""))
                    if not path:
                        continue
                    fh = open(path, "rb")
                    opened.append(fh)
                    files.append(("image", (os.path.basename(path), fh, content_type_for_path(path))))
                if mask_refs:
                    mask_path = output_file_from_url(mask_refs[0].get("url", ""))
                    if mask_path:
                        fh = open(mask_path, "rb")
                        opened.append(fh)
                        files.append(("mask", (os.path.basename(mask_path), fh, content_type_for_path(mask_path))))
                try:
                    response = await post_openai_edits(files)
                    if response.status_code >= 400:
                        edit_failed_status = response.status_code
                        edit_failed_text = response.text[:500]
                        response = None
                except httpx.HTTPError as e:
                    edit_failed_status = -1
                    edit_failed_text = str(e)
                    response = None
            finally:
                for fh in opened:
                    fh.close()
            # 2) edits 失败 → 非 GPT-Image-2 可回退到 /images/generations + JSON image:[urls/base64]（grsai 风格）
            if response is None:
                if is_gpt2:
                    raise HTTPException(
                        status_code=502,
                        detail=f"GPT-Image-2 编辑接口 /images/edits 调用失败：{edit_failed_text[:300] or edit_failed_status}。已停止自动重试，避免上游可能已扣费后再次请求。"
                    )
                print(f"/images/edits failed ({edit_failed_status}): {edit_failed_text[:200]} → 回退到 /images/generations + image:[] JSON")
                image_payload = [reference_to_data_url(ref, max_size=1536) for ref in image_refs[:4]]
                body = {
                    "model": model, "prompt": prompt, "size": size,
                    "response_format": "url", "n": 1,
                    "image": image_payload,
                }
                if quality:
                    body["quality"] = quality
                response = await client.post(gen_url, headers=api_headers(provider=provider), json=body)
                if response.status_code >= 400 and images_api_unsupported(response):
                    raise HTTPException(
                        status_code=502,
                        detail=f"编辑接口 /images/edits 调用失败，且该平台不支持 /images/generations：{edit_failed_text[:300] or edit_failed_status}"
                    )
        else:
            body = {"model": model, "prompt": prompt, "size": size, "response_format": "url", "n": 1}
            if quality:
                body["quality"] = quality
            response = await client.post(
                gen_url,
                headers=api_headers(provider=provider),
                json=body,
            )
            if response.status_code >= 400 and images_api_unsupported(response):
                response = await post_openai_edits()
        response.raise_for_status()
        raw = response.json()
        try:
            return extract_image(raw), raw
        except HTTPException:
            task_id = extract_task_id(raw)
            if not task_id:
                raise
        task_result = await wait_for_image_task(client, task_id, provider)
        return extract_image(task_result), task_result

def upstream_message_from_record(item):
    role = item.get("role")
    if role not in {"user", "assistant"} or item.get("type") == "image":
        return None
    refs = item.get("attachments") or []
    if refs and role == "user":
        content = [{"type": "text", "text": item.get("content", "")}]
        for ref in refs[:4]:
            url = reference_to_data_url(ref)
            if url:
                content.append({"type": "image_url", "image_url": {"url": url}})
        return {"role": role, "content": content}
    return {"role": role, "content": item.get("content", "")}

# --- 路由接口 ---






























# --- ModelScope Token (从 env 读取，不再支持通过 UI 修改) ---


# --- 在线生图 (COMFLY) ---












async def build_online_image_result(payload: OnlineImageRequest):
    provider = get_api_provider(payload.provider_id)
    default_model = (provider.get("image_models") or [IMAGE_MODEL])[0]
    model = selected_model(payload.model, default_model)
    refs = request_reference_records(payload.reference_images)
    history_refs = history_reference_records(refs)
    size_ctx = image_size_context(payload.size, payload.aspect_ratio, payload.resolution)
    is_banana = is_nano_banana_model(model)
    try:
        image_data, raw = await generate_ai_image(payload.prompt, payload.size, payload.quality, model, refs, provider["id"], payload.aspect_ratio, payload.resolution)
        if is_gpt_image_2_model(model):
            image_data = await normalize_ai_image_to_size(image_data, gpt_image_2_target_size(payload.size, payload.aspect_ratio, payload.resolution))
        local_url = await save_ai_image_to_output(image_data, prefix="online_")
    except httpx.HTTPStatusError as exc:
        text = exc.response.text or ''
        # 把上游英文错误转成中文友好提示
        friendly = None
        m = re.search(r"longest edge must be less than or equal to (\d+)", text)
        if m:
            limit = m.group(1)
            friendly = f"该模型不支持当前分辨率：最长边超过 {limit}px。请把图片分辨率调低（例如换到 2K 或更小），或更换支持高分辨率的模型。"
        elif "Invalid size" in text or "invalid_value" in text:
            friendly = f"该模型不支持当前尺寸：{payload.size}。请尝试更换分辨率或模型。"
        elif "rate limit" in text.lower() or "429" in text:
            friendly = "请求过于频繁，已被上游限流，请稍后再试。"
        elif "Unauthorized" in text or "401" in text:
            friendly = "API Key 无效或已过期，请到「API 设置」检查 Key。"
        elif "model_not_found" in text or "channel not found" in text:
            friendly = f"上游平台找不到模型「{model}」可用通道。可能该模型未在此账号开通，请换一个已开通的模型。"
        detail = friendly or f"上游生图接口错误：{text[:300]}"
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"请求上游生图接口失败：{exc}") from exc

    result = {
        "prompt": payload.prompt,
        "images": [local_url],
        "timestamp": time.time(),
        "type": "online",
        "model": model,
        "provider_id": provider["id"],
        "provider_name": provider.get("name") or provider["id"],
        "task_id": extract_task_id(raw) if isinstance(raw, dict) else None,
        "request_id": raw.get("id") if isinstance(raw, dict) else None,
        "params": {
            "provider_id": provider["id"],
            "model": model,
            "size": "" if is_banana else payload.size,
            "aspect_ratio": size_ctx["aspect_ratio"],
            "resolution": size_ctx["resolution"],
            "effective_size": size_ctx["size"],
            "quality": payload.quality,
            "reference_images": history_refs,
        },
        "raw_usage": raw.get("usage") if isinstance(raw, dict) else None,
    }
    banana_debug = raw.get("_banana_request_debug") if isinstance(raw, dict) else None
    save_to_history(result)
    if banana_debug:
        result["banana_request_debug"] = banana_debug
    if GLOBAL_LOOP:
        asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(result), GLOBAL_LOOP)
    return result

async def online_image(payload: OnlineImageRequest):
    return await build_online_image_result(payload)

async def zimage_api_image(payload: OnlineImageRequest):
    provider = get_api_provider(payload.provider_id)
    default_model = (provider.get("image_models") or [IMAGE_MODEL])[0]
    model = selected_model(payload.model, default_model)
    is_banana = is_nano_banana_model(model)
    refs = request_reference_records(payload.reference_images)
    history_refs = history_reference_records(refs)
    size_ctx = image_size_context(payload.size, payload.aspect_ratio, payload.resolution)
    try:
        image_data, raw = await generate_ai_image(payload.prompt, payload.size, payload.quality, model, refs, provider["id"], payload.aspect_ratio, payload.resolution)
        if is_gpt_image_2_model(model):
            image_data = await normalize_ai_image_to_size(image_data, gpt_image_2_target_size(payload.size, payload.aspect_ratio, payload.resolution))
        local_url = await save_ai_image_to_output(image_data, prefix="zimage_api_")
    except httpx.HTTPStatusError as exc:
        text = exc.response.text or ""
        raise HTTPException(status_code=exc.response.status_code, detail=f"API 生图接口错误：{text[:300]}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"请求 API 生图接口失败：{exc}") from exc

    result = {
        "prompt": payload.prompt,
        "images": [local_url],
        "timestamp": time.time(),
        "type": "zimage",
        "engine": "api",
        "model": model,
        "provider_id": provider["id"],
        "provider_name": provider.get("name") or provider["id"],
        "provider_logo_url": provider.get("logo_url") or "",
        "task_id": extract_task_id(raw) if isinstance(raw, dict) else None,
        "request_id": raw.get("id") if isinstance(raw, dict) else None,
        "params": {
            "provider_id": provider["id"],
            "model": model,
            "size": "" if is_banana else payload.size,
            "aspect_ratio": size_ctx["aspect_ratio"],
            "resolution": size_ctx["resolution"],
            "effective_size": size_ctx["size"],
            "quality": payload.quality,
            "reference_images": history_refs,
        },
        "raw_usage": raw.get("usage") if isinstance(raw, dict) else None,
    }
    banana_debug = raw.get("_banana_request_debug") if isinstance(raw, dict) else None
    save_to_history(result)
    if banana_debug:
        result["banana_request_debug"] = banana_debug
    if GLOBAL_LOOP:
        asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(result), GLOBAL_LOOP)
    return result

async def run_canvas_image_task(task_id: str, payload: OnlineImageRequest):
    with CANVAS_TASK_LOCK:
        if task_id in CANVAS_TASKS:
            CANVAS_TASKS[task_id]["status"] = "running"
            CANVAS_TASKS[task_id]["updated_at"] = time.time()
    try:
        result = await build_online_image_result(payload)
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({
                "status": "succeeded",
                "result": result,
                "error": "",
                "updated_at": time.time(),
            })
    except Exception as exc:
        detail = getattr(exc, "detail", None) or str(exc)
        status_code = getattr(exc, "status_code", 500)
        with CANVAS_TASK_LOCK:
            CANVAS_TASKS[task_id].update({
                "status": "failed",
                "error": str(detail),
                "status_code": status_code,
                "updated_at": time.time(),
            })

async def create_canvas_image_task(payload: OnlineImageRequest):
    task_id = f"canvas_img_{uuid.uuid4().hex}"
    with CANVAS_TASK_LOCK:
        CANVAS_TASKS[task_id] = {
            "id": task_id,
            "type": "online-image",
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": "",
        }
    asyncio.create_task(run_canvas_image_task(task_id, payload))
    return {"task_id": task_id, "status": "queued"}

async def get_canvas_image_task(task_id: str):
    with CANVAS_TASK_LOCK:
        task = dict(CANVAS_TASKS.get(task_id) or {})
    if not task:
        raise HTTPException(status_code=404, detail="画布任务不存在，可能服务已重启或任务已过期")
    return task

# --- Canvas Video ---

VIDEO_URL_KEYS = (
    "url", "video_url", "videoUrl", "mp4_url", "mp4Url",
    "output", "output_url", "outputUrl", "download_url", "downloadUrl",
    "video", "src", "uri", "preview_url", "previewUrl",
)
AUDIO_URL_KEYS = (
    "audio_url", "audioUrl", "audio", "audio_file", "audioFile",
    "audio_output", "audioOutput", "wav_url", "wavUrl", "mp3_url", "mp3Url",
    "sound_url", "soundUrl", "voice_url", "voiceUrl",
)
AUDIO_URL_CONTAINER_KEYS = ("audios", "audio_urls", "audioUrls", "audio_outputs", "audioOutputs")
AUDIO_GENERIC_CONTAINER_KEYS = ("media", "outputs")

def _looks_like_audio_url(value: str) -> bool:
    if str(value or "").lower().startswith("data:audio/"):
        return True
    clean = urllib.parse.urlparse(str(value or "")).path.lower()
    return os.path.splitext(clean)[1] in {".wav", ".mp3", ".m4a", ".ogg"}

def _collect_video_url(value, urls):
    if not value:
        return
    if isinstance(value, str):
        if value.startswith("http://") or value.startswith("https://") or value.startswith("/output/") or value.startswith("/assets/"):
            urls.append(value)
        return
    if isinstance(value, list):
        for item in value:
            _collect_video_url(item, urls)
        return
    if isinstance(value, dict):
        for key in VIDEO_URL_KEYS:
            if key in value:
                _collect_video_url(value.get(key), urls)

def video_output_urls(raw):
    urls = []
    if not isinstance(raw, dict):
        return urls
    candidates = [raw]
    data = raw.get("data")
    if isinstance(data, dict):
        candidates.append(data)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                candidates.append(item)
    for node in list(candidates):
        result = node.get("result") if isinstance(node, dict) else None
        if isinstance(result, dict):
            candidates.append(result)
        elif isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    candidates.append(item)
    for node in candidates:
        if not isinstance(node, dict):
            continue
        for key in ("videos", "outputs"):
            value = node.get(key)
            if value:
                _collect_video_url(value, urls)
        for key in VIDEO_URL_KEYS:
            if key in node:
                _collect_video_url(node.get(key), urls)
    deduped = []
    for url in urls:
        if isinstance(url, str) and url and url not in deduped:
            deduped.append(url)
    return deduped

def _collect_audio_url(value, urls):
    if not value:
        return
    if isinstance(value, str):
        if value.startswith("http://") or value.startswith("https://") or value.startswith("/output/") or value.startswith("/assets/"):
            urls.append(value)
        return
    if isinstance(value, list):
        for item in value:
            _collect_audio_url(item, urls)
        return
    if isinstance(value, dict):
        for key in AUDIO_URL_KEYS:
            if key in value:
                _collect_audio_url(value.get(key), urls)
        if "url" in value:
            _collect_audio_url(value.get("url"), urls)

def _collect_generic_audio_url(value, urls):
    if not value:
        return
    if isinstance(value, str):
        if _looks_like_audio_url(value):
            _collect_audio_url(value, urls)
        return
    if isinstance(value, list):
        for item in value:
            _collect_generic_audio_url(item, urls)
        return
    if isinstance(value, dict):
        explicit_kind = str(value.get("kind") or value.get("type") or "").strip().lower()
        if explicit_kind == "audio" and value.get("url"):
            _collect_audio_url(value.get("url"), urls)
        if "url" in value and _looks_like_audio_url(value.get("url")):
            _collect_audio_url(value.get("url"), urls)
        for key in AUDIO_URL_KEYS:
            if key in value:
                _collect_audio_url(value.get(key), urls)

def audio_output_urls(raw):
    urls = []
    if not isinstance(raw, dict):
        return urls
    candidates = [raw]
    data = raw.get("data")
    if isinstance(data, dict):
        candidates.append(data)
    elif isinstance(data, list):
        candidates.extend([item for item in data if isinstance(item, dict)])
    for node in list(candidates):
        result = node.get("result") if isinstance(node, dict) else None
        if isinstance(result, dict):
            candidates.append(result)
        elif isinstance(result, list):
            candidates.extend([item for item in result if isinstance(item, dict)])
    for node in candidates:
        if not isinstance(node, dict):
            continue
        for key in AUDIO_URL_CONTAINER_KEYS:
            if key in node:
                _collect_audio_url(node.get(key), urls)
        for key in AUDIO_GENERIC_CONTAINER_KEYS:
            if key in node:
                _collect_generic_audio_url(node.get(key), urls)
        for key in AUDIO_URL_KEYS:
            if key in node:
                _collect_audio_url(node.get(key), urls)
    deduped = []
    for url in urls:
        if isinstance(url, str) and url and url not in deduped:
            deduped.append(url)
    return deduped

def video_api_root(provider):
    base_url = (provider.get("base_url") or AI_BASE_URL).rstrip("/")
    if base_url.endswith("/v1") or base_url.endswith("/v2"):
        base_url = base_url.rsplit("/", 1)[0]
    return base_url

VIDEO_TASK_SUCCESS_STATUSES = {
    "SUCCESS", "SUCCEED", "SUCCEEDED", "COMPLETED", "COMPLETE",
    "DONE", "FINISHED", "FINISH", "OK", "READY",
}
VIDEO_TASK_FAILURE_STATUSES = {
    "FAILURE", "FAILED", "FAIL", "ERROR", "ERRORED",
    "CANCELED", "CANCELLED", "TIMEOUT", "TIMEDOUT", "REJECTED", "EXPIRED",
}

def video_debug_summary(provider, model="", submit_url="", task_id="", status="", urls=None, raw=None):
    return {
        "provider_id": (provider or {}).get("id") or "",
        "provider_name": (provider or {}).get("name") or "",
        "model": model or "",
        "submit_url": submit_url or "",
        "task_id": task_id or "",
        "poll_status": status or "",
        "video_url_count": len(urls or []),
        "raw_summary": safe_upstream_summary(raw) if raw is not None else None,
    }

async def wait_for_video_task(client, provider, task_id):
    base_url = video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    if is_apimart_provider(provider):
        task_path = f"{base_url}/tasks/{task_id}" if base_url.endswith("/v1") else f"{base_url}/v1/tasks/{task_id}"
        task_url = f"{task_path}?language=zh"
    else:
        task_url = f"{base_url}/v2/videos/generations/{task_id}"
    deadline = time.monotonic() + VIDEO_POLL_TIMEOUT
    delay = max(2.0, IMAGE_POLL_INTERVAL)
    last_payload = {}
    while time.monotonic() < deadline:
        await asyncio.sleep(delay)
        response = await client.get(task_url, headers=api_headers(provider=provider))
        response.raise_for_status()
        raw = response.json()
        last_payload = raw
        task_data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        status = str(task_data.get("status") or task_data.get("task_status") or raw.get("status") or raw.get("task_status") or "").upper()
        urls = video_output_urls(raw)
        audio_urls = audio_output_urls(raw)
        print("[CanvasVideo] poll", video_debug_summary(provider, task_id=task_id, status=status, urls=urls, raw=raw))
        if status in VIDEO_TASK_SUCCESS_STATUSES:
            return raw
        # 部分上游不返回标准 status 字段，但已经返回了视频 URL —— 直接当成功处理
        if not status and (urls or audio_urls):
            return raw
        if status in VIDEO_TASK_FAILURE_STATUSES:
            error = task_data.get("error") if isinstance(task_data.get("error"), dict) else {}
            reason = task_data.get("fail_reason") or task_data.get("message") or error.get("message") or raw.get("error") or raw.get("message") or str(raw)
            raise HTTPException(status_code=502, detail=f"视频生成任务失败：{reason}")
        delay = min(delay * 1.6, 12)
    raise HTTPException(status_code=504, detail=f"视频生成任务超时：{last_payload or task_id}")

def apimart_video_size(size):
    value = str(size or "16:9").strip()
    if value == "keep_ratio":
        return "adaptive"
    allowed = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"}
    return value if value in allowed else "16:9"

async def canvas_video(payload: CanvasVideoRequest):
    provider = get_api_provider(payload.provider_id)
    base_url = video_api_root(provider)
    if not base_url:
        raise HTTPException(status_code=400, detail=f"{provider.get('name') or provider['id']} 未配置 Base URL")
    api_key = os.getenv(provider_key_env(provider["id"]), "")
    if not api_key:
        raise HTTPException(status_code=400, detail=f"未配置 {provider.get('name') or provider['id']} 的 API Key，请在 API 设置中填写。")
    is_apimart = is_apimart_provider(provider)
    submit_url = f"{base_url}/videos/generations" if is_apimart and base_url.endswith("/v1") else f"{base_url}/v1/videos/generations" if is_apimart else f"{base_url}/v2/videos/generations"
    requested_model = selected_model(payload.model, "veo3-fast")
    is_veo31 = is_apimart and is_apimart_veo31_model(requested_model)
    try:
        async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:
            audio_payload = audio_payload_values(payload.audios[:3])
            # --- 构造图片载荷 ---
            if is_apimart:
                # APIMart 只接受 http/https 或 asset:// URL，先上传本地图片取回网络 URL
                image_with_roles = []
                invalid_images = []  # 每项为 (原始 URL, 失败原因)
                apimart_model = apimart_veo31_model(requested_model) if is_veo31 else ""
                if apimart_model == "veo3.1-lite" and payload.images:
                    raise HTTPException(status_code=400, detail="veo3.1-lite 不支持图片输入，请改用 veo3.1-fast 或 veo3.1-quality。")
                image_limit = 0 if apimart_model == "veo3.1-lite" else (3 if is_veo31 else 9)
                for ref in payload.images[:image_limit]:
                    if not ref.url:
                        continue
                    role = str(ref.role or "").strip()
                    if not is_veo31 and role in {"first_frame", "last_frame", "reference_image"}:
                        up_url = await upload_image_for_apimart(client, provider, ref.url)
                        if valid_apimart_video_image_input(up_url):
                            image_with_roles.append({"url": up_url, "role": role})
                        else:
                            reason = up_url[4:] if isinstance(up_url, str) and up_url.startswith("ERR:") else "未知错误"
                            invalid_images.append((ref.url, reason))
                image_payload = []
                if not image_with_roles:
                    for ref in payload.images[:image_limit]:
                        if not ref.url:
                            continue
                        up_url = await upload_image_for_apimart(client, provider, ref.url)
                        if valid_apimart_video_image_input(up_url):
                            image_payload.append(up_url)
                        else:
                            reason = up_url[4:] if isinstance(up_url, str) and up_url.startswith("ERR:") else "未知错误"
                            invalid_images.append((ref.url, reason))
                if payload.images and not image_with_roles and not image_payload:
                    first_url, first_reason = invalid_images[0] if invalid_images else ("", "未知错误")
                    sample = invalid_video_image_preview(first_url)
                    raise HTTPException(status_code=400, detail=f"输入图片无法转换为视频接口支持的格式：{sample}\n原因：{first_reason}\n请确认本地文件存在且不超过 10MB；VEO3.1 需要图片是 APIMart 可访问的 http/https / asset:// / data URL。")
                # --- APIMart 请求体 ---
                if is_veo31:
                    model = apimart_model
                    body = {
                        "prompt": payload.prompt,
                        "model": model,
                        "duration": 8,
                        "aspect_ratio": apimart_veo31_aspect(payload.aspect_ratio),
                        "resolution": apimart_veo31_resolution(payload.resolution),
                    }
                    if image_payload and model != "veo3.1-lite":
                        video_images = image_payload[:3]
                        if model == "veo3.1-quality" and len(video_images) > 2:
                            video_images = video_images[:2]
                        body["image_urls"] = video_images
                        if len(video_images) == 2:
                            body["generation_type"] = "frame"
                        elif len(video_images) >= 3 and model != "veo3.1-quality":
                            body["generation_type"] = "reference"
                    if model != "veo3.1-lite":
                        body["official_fallback"] = False
                    if audio_payload:
                        body["audios"] = audio_payload
                        body["audio_url"] = audio_payload[0]
                        body["audio_urls"] = audio_payload
                else:
                    body = {
                        "prompt": payload.prompt,
                        "model": selected_model(payload.model, "doubao-seedance-2.0"),
                        "duration": payload.duration,
                        "size": apimart_video_size(payload.aspect_ratio or payload.size),
                        "resolution": payload.resolution or "480p",
                    }
                    if image_with_roles:
                        body["image_with_roles"] = image_with_roles
                    elif image_payload:
                        body["image_urls"] = image_payload[:9]
                    if payload.videos:
                        body["video_urls"] = [v for v in payload.videos if v][:3]
                    if audio_payload:
                        body["audios"] = audio_payload
                        body["audio_url"] = audio_payload[0]
                        body["audio_urls"] = audio_payload
                    if payload.seed is not None:
                        body["seed"] = payload.seed
                    if payload.return_last_frame:
                        body["return_last_frame"] = True
                    if payload.generate_audio:
                        body["generate_audio"] = True
            else:
                # 非 APIMart：data URL 方式（OpenAI / ComflyAI 接口）
                image_payload = []
                for ref in payload.images[:4]:
                    if ref.url:
                        image_payload.append(reference_to_data_url(ref.dict(), max_size=1536))
                body = {
                    "prompt": payload.prompt,
                    "model": selected_model(payload.model, "veo3-fast"),
                    "duration": payload.duration,
                    "watermark": payload.watermark,
                }
                if payload.aspect_ratio:
                    body["aspect_ratio"] = payload.aspect_ratio
                    body["ratio"] = payload.aspect_ratio
                if payload.size:
                    body["size"] = payload.size
                if payload.resolution:
                    body["resolution"] = payload.resolution
                if image_payload:
                    body["images"] = image_payload
                if payload.videos:
                    body["videos"] = [v for v in payload.videos if v]
                if audio_payload:
                    body["audios"] = audio_payload
                    body["audio_url"] = audio_payload[0]
                    body["audio_urls"] = audio_payload
                if payload.enhance_prompt:
                    body["enhance_prompt"] = True
                if payload.enable_upsample:
                    body["enable_upsample"] = True
                if payload.seed is not None:
                    body["seed"] = payload.seed
                if payload.camerafixed:
                    body["camerafixed"] = True
                if payload.return_last_frame:
                    body["return_last_frame"] = True
                if payload.generate_audio:
                    body["generate_audio"] = True
            # --- 发起视频生成请求 ---
            print("[CanvasVideo] submit", video_debug_summary(provider, model=body.get("model", requested_model), submit_url=submit_url, raw={
                "body_keys": list(body.keys()),
                "image_count": len(body.get("image_urls") or body.get("images") or body.get("image_with_roles") or []),
                "audio_count": len(audio_payload),
                "duration": body.get("duration"),
                "resolution": body.get("resolution"),
                "size": body.get("size") or body.get("aspect_ratio"),
            }))
            response = await client.post(submit_url, headers=api_headers(provider=provider), json=body)
            response.raise_for_status()
            try:
                raw = response.json()
            except Exception:
                # 上游返回了 HTML 错误页面或非 JSON 响应
                resp_text = response.text[:500]
                raise HTTPException(status_code=502, detail=f"上游视频接口返回非 JSON 响应（状态 {response.status_code}）：{resp_text}")
            task_id = extract_task_id(raw) or raw.get("task_id") or raw.get("id")
            result = raw
            if task_id and not (video_output_urls(raw) or audio_output_urls(raw)):
                result = await wait_for_video_task(client, provider, task_id)
            urls = video_output_urls(result)
            audio_urls = audio_output_urls(result)
            debug = video_debug_summary(provider, model=body.get("model", requested_model), submit_url=submit_url, task_id=task_id, urls=urls, raw=result)
            print("[CanvasVideo] result", debug)
            if not urls and not audio_urls:
                raise HTTPException(status_code=502, detail={"message": "视频生成成功但没有解析到视频 URL", "debug": debug})
            local_urls = [await save_remote_video_to_output(url) for url in urls]
            local_audios = [await save_remote_audio_to_output(url) for url in audio_urls]
            paired_audio_count = min(len(local_urls), len(local_audios))
            media = []
            for index, url in enumerate(local_urls):
                item = {"url": url, "kind": "video"}
                if index < paired_audio_count:
                    item["audioUrl"] = local_audios[index]
                media.append(item)
            for url in local_audios[paired_audio_count:]:
                media.append({"url": url, "kind": "audio"})
            return {
                "videos": local_urls,
                "audios": local_audios,
                "media": media,
                "task_id": task_id,
                "raw": result,
                "debug": {**debug, "video_url_count": len(local_urls), "audio_url_count": len(local_audios), "media_count": len(media)},
            }
    except httpx.HTTPStatusError as exc:
        text = exc.response.text
        try:
            requested_model = body.get("model", "") or payload.model or ""
        except NameError:
            requested_model = payload.model or ""
        provider_name = provider.get('name') or provider['id']
        # 1) 模型名不在上游支持范围 → 从错误信息里抽取合法列表展示
        valid_models_match = re.search(r"not in\s*\[([^\]]+)\]", text)
        if valid_models_match:
            valid_models = [m.strip() for m in valid_models_match.group(1).split(",") if m.strip()]
            sample = valid_models[:30]
            more = f"（共 {len(valid_models)} 个，仅显示前 {len(sample)} 个）" if len(valid_models) > len(sample) else ""
            hint = (
                f"上游「{provider_name}」不识别模型「{requested_model}」。\n\n"
                f"上游支持的视频模型清单{more}：\n  {', '.join(sample)}\n\n"
                f"请到「API 设置」里把视频模型改成上面列表中的一个。"
            )
            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc
        # 2) 模型名合法但账号没开通通道
        if "channel not found" in text or "model_not_found" in text:
            hint = (
                f"上游「{provider_name}」识别了模型「{requested_model}」，但你的 API Key 账号下**没有该模型的可用通道**。\n\n"
                f"原因：你的账号没开通这个模型的访问权限（付费/订阅相关）。\n\n"
                f"解决方法：\n"
                f"  1. 登录 {provider.get('base_url') or '上游平台'} 控制台，开通该模型 / 充值；\n"
                f"  2. 或在「API 设置」里把视频模型改成你账号已开通的型号（如 veo3-fast / veo2-fast / sora-2 等）。"
            )
            raise HTTPException(status_code=exc.response.status_code, detail=hint) from exc
        raise HTTPException(status_code=exc.response.status_code, detail=f"上游视频接口错误：{text}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"请求上游视频接口失败：{exc}") from exc

# --- Canvas LLM ---

async def canvas_llm(payload: CanvasLLMRequest):
    chat_base, chat_hdrs, model = resolve_chat_provider(payload.provider, payload.model, payload.ms_model)
    # 判断协议：APIMart 异步 vs 标准 OpenAI
    _llm_provider = get_api_provider(payload.provider) if payload.provider not in ("modelscope",) else {}
    _is_apimart = is_apimart_provider(_llm_provider)
    system_prompt = (payload.system_prompt or "").strip()
    upstream_messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
    for item in payload.messages[-MAX_HISTORY_MESSAGES:]:
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and content:
            upstream_messages.append({"role": role, "content": content})
    # 构造用户消息：有图片时用 OpenAI vision 多模态格式
    if payload.images:
        content_parts = [{"type": "text", "text": payload.message}]
        ok_imgs = 0
        for img in payload.images[:8]:
            if not img or not isinstance(img, str):
                continue
            # 本地 /output/* 或 /assets/* 路径转为 data URL；http(s) 或 data URL 直接用
            if img.startswith("/output/") or img.startswith("/assets/"):
                ref_url = reference_to_data_url({"url": img}, max_size=1024)
            else:
                ref_url = img
            if not ref_url:
                continue
            content_parts.append({"type": "image_url", "image_url": {"url": ref_url}})
            ok_imgs += 1
        print(f"[canvas-llm] model={model} provider={payload.provider} text_len={len(payload.message)} images={ok_imgs}/{len(payload.images)}")
        upstream_messages.append({"role": "user", "content": content_parts})
    else:
        upstream_messages.append({"role": "user", "content": payload.message})
    raw = None
    try:
        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            req_body = {"model": model, "messages": upstream_messages}
            if _is_apimart:
                req_body["stream"] = False   # APIMart 默认流式，强制关闭
            response = await client.post(
                f"{chat_base}/chat/completions",
                headers=chat_hdrs,
                json=req_body,
            )
            response.raise_for_status()
            if not response.content:
                raise HTTPException(status_code=502, detail="上游接口返回了空响应")
            raw = response.json()
    except httpx.HTTPStatusError as exc:
        body = exc.response.text or ""
        raise HTTPException(status_code=exc.response.status_code, detail=f"上游接口错误：{body}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"请求上游接口失败：{exc}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"解析上游响应失败：{exc}") from exc
    try:
        text = text_from_chat_response(raw).strip() if isinstance(raw, dict) else ""
        text = text or "接口返回了空回复。"
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"解析回复内容失败：{exc}") from exc
    raw_data = unwrap_apimart_response(raw) if isinstance(raw, dict) else {}
    return {"text": text, "model": model, "raw_usage": raw_data.get("usage")}

# --- 对话管理 ---

async def conversations(request: Request, x_user_id: str = Header(default="")):
    user_id = safe_user_id(x_user_id, request)
    return {"user_id": user_id, "conversations": list_conversations(user_id)}

async def create_conversation(payload: ConversationCreateRequest, request: Request, x_user_id: str = Header(default="")):
    user_id = safe_user_id(x_user_id, request)
    return {"conversation": new_conversation(user_id, payload.title)}

async def get_conversation(conversation_id: str, request: Request, x_user_id: str = Header(default="")):
    user_id = safe_user_id(x_user_id, request)
    return {"conversation": load_conversation(user_id, conversation_id)}

async def delete_conversation(conversation_id: str, request: Request, x_user_id: str = Header(default="")):
    user_id = safe_user_id(x_user_id, request)
    path = conversation_path(user_id, conversation_id)
    if os.path.exists(path):
        os.remove(path)
    return {"ok": True}

# --- 画布管理 ---

async def canvases():
    return {"canvases": list_canvases()}

async def trashed_canvases():
    return {"canvases": list_deleted_canvases(), "retention_days": 30}

async def create_canvas(payload: CanvasCreateRequest):
    return {"canvas": new_canvas(payload.title, payload.icon, payload.kind)}

async def get_canvas_meta(canvas_id: str):
    canvas = load_canvas(canvas_id)
    return {
        "id": canvas.get("id"),
        "updated_at": canvas.get("updated_at", 0),
        "title": canvas.get("title", "未命名画布"),
        "icon": canvas.get("icon", "layers"),
        "kind": normalize_canvas_kind(canvas.get("kind")),
    }

async def get_canvas(canvas_id: str):
    return {"canvas": load_canvas(canvas_id)}

async def check_canvas_assets(payload: CanvasAssetCheckRequest):
    result = {}
    for url in payload.urls[:3000]:
        text = str(url or "").strip()
        if not text:
            continue
        if text.startswith("/output/") or text.startswith("/assets/"):
            result[text] = bool(output_file_from_url(text))
        else:
            result[text] = True
    return {"exists": result}

async def download_canvas_assets(payload: CanvasAssetDownloadRequest):
    buffer = BytesIO()
    used_names = set()
    count = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for url in payload.urls[:1000]:
            text = str(url or "").strip()
            if not text or not (text.startswith("/output/") or text.startswith("/assets/")):
                continue
            path = output_file_from_url(text)
            if not path or not os.path.isfile(path):
                continue
            base = os.path.basename(path) or f"image-{count + 1}.png"
            name, ext = os.path.splitext(base)
            archive_name = base
            suffix = 2
            while archive_name in used_names:
                archive_name = f"{name}-{suffix}{ext}"
                suffix += 1
            used_names.add(archive_name)
            zf.write(path, archive_name)
            count += 1
    if count <= 0:
        raise HTTPException(status_code=404, detail="没有可下载的本地图片")
    buffer.seek(0)
    filename = re.sub(r'[\\/:*?"<>|]+', "_", payload.filename or "canvas-output-images.zip")
    if not filename.lower().endswith(".zip"):
        filename += ".zip"
    encoded = urllib.parse.quote(filename)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"}
    return Response(buffer.getvalue(), media_type="application/zip", headers=headers)

async def get_asset_library():
    return {"library": load_asset_library()}

async def create_asset_library_category(payload: AssetLibraryCategoryRequest):
    lib = load_asset_library()
    cat_type = "workflow" if str(payload.type or "").lower() == "workflow" else "image"
    category = {"id": f"cat_{uuid.uuid4().hex[:12]}", "name": sanitize_asset_name(payload.name, "新文件夹"), "type": cat_type, "items": []}
    lib.setdefault("categories", []).append(category)
    save_asset_library(lib)
    return {"library": lib, "category": category}

async def rename_asset_library_category(category_id: str, payload: AssetLibraryRenameRequest):
    lib = load_asset_library()
    cat = find_asset_category(lib, category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    cat["name"] = sanitize_asset_name(payload.name, cat.get("name") or "新文件夹")
    save_asset_library(lib)
    return {"library": lib, "category": cat}

async def delete_asset_library_category(category_id: str):
    lib = load_asset_library()
    cat = find_asset_category(lib, category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    if cat.get("type") == "workflow" and category_id == "workflows":
        raise HTTPException(status_code=400, detail="默认工作流分类不能删除")
    lib["categories"] = [c for c in lib.get("categories", []) if c.get("id") != category_id]
    save_asset_library(lib)
    return {"library": lib}

async def add_asset_library_item(payload: AssetLibraryAddRequest):
    lib = load_asset_library()
    cat = find_asset_category(lib, payload.category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    if cat.get("type") != "image":
        raise HTTPException(status_code=400, detail="该分类暂不支持添加图片")
    src = output_file_from_url(payload.url)
    if not src:
        raise HTTPException(status_code=400, detail="只支持保存本地 /assets 或 /output 图片")
    ext = os.path.splitext(src)[1].lower() or ".png"
    if ext not in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
        ext = ".png"
    safe_name = sanitize_asset_name(payload.name or os.path.basename(src), "asset")
    if not os.path.splitext(safe_name)[1]:
        safe_name += ext
    dest_name = f"lib_{uuid.uuid4().hex[:12]}_{safe_name}"
    dest_path = os.path.join(ASSET_LIBRARY_DIR, dest_name)
    shutil.copy2(src, dest_path)
    item = {"id": f"asset_{uuid.uuid4().hex[:12]}", "name": os.path.splitext(safe_name)[0][:120], "url": f"/assets/library/{dest_name}", "created_at": now_ms()}
    cat.setdefault("items", []).append(item)
    save_asset_library(lib)
    return {"library": lib, "item": item}

async def rename_asset_library_item(item_id: str, payload: AssetLibraryRenameRequest):
    lib = load_asset_library()
    for cat in lib.get("categories", []):
        for item in cat.get("items", []):
            if item.get("id") == item_id:
                item["name"] = sanitize_asset_name(payload.name, item.get("name") or "asset")
                save_asset_library(lib)
                return {"library": lib, "item": item}
    raise HTTPException(status_code=404, detail="资产不存在")

async def delete_asset_library_item(item_id: str):
    lib = load_asset_library()
    removed = None
    for cat in lib.get("categories", []):
        keep = []
        for item in cat.get("items", []):
            if item.get("id") == item_id:
                removed = item
            else:
                keep.append(item)
        cat["items"] = keep
    if not removed:
        raise HTTPException(status_code=404, detail="资产不存在")
    save_asset_library(lib)
    return {"library": lib}

async def update_canvas(canvas_id: str, payload: CanvasSaveRequest):
    canvas = load_canvas(canvas_id)
    current_updated_at = int(canvas.get("updated_at") or 0)
    if payload.base_updated_at and current_updated_at and int(payload.base_updated_at) < current_updated_at:
        raise HTTPException(status_code=409, detail={
            "message": "画布已被其他页面更新，已拒绝旧版本覆盖。",
            "canvas": canvas,
            "updated_at": current_updated_at,
        })
    canvas["title"] = (payload.title or canvas.get("title") or "未命名画布")[:80]
    canvas["icon"] = (payload.icon or canvas.get("icon") or "layers")[:32]
    canvas["kind"] = normalize_canvas_kind(canvas.get("kind"))
    canvas["nodes"] = payload.nodes
    canvas["connections"] = payload.connections
    canvas["viewport"] = payload.viewport
    canvas["logs"] = payload.logs[-500:]
    canvas["settings"] = payload.settings or {}
    save_canvas(canvas)
    await manager.broadcast_canvas_updated(canvas_id, int(canvas.get("updated_at") or now_ms()), payload.client_id)
    return {"canvas": canvas}

async def delete_canvas(canvas_id: str):
    canvas = load_canvas_any(canvas_id)
    if not canvas.get("deleted_at"):
        canvas["deleted_at"] = now_ms()
        save_canvas(canvas)
    return {"ok": True}

async def restore_canvas(canvas_id: str):
    canvas = load_canvas_any(canvas_id)
    if canvas.get("deleted_at"):
        canvas.pop("deleted_at", None)
        save_canvas(canvas)
    return {"canvas": canvas}

async def purge_canvas(canvas_id: str):
    path = canvas_path(canvas_id)
    if os.path.exists(path):
        os.remove(path)
    return {"ok": True}

# --- GPT 对话 ---

async def chat(payload: ChatRequest, request: Request, x_user_id: str = Header(default="")):
    user_id = safe_user_id(x_user_id, request)
    conversation = (
        load_conversation(user_id, payload.conversation_id)
        if payload.conversation_id
        else new_conversation(user_id, display_title(payload.message))
    )
    if not conversation.get("messages"):
        conversation["title"] = display_title(payload.message)

    refs = request_reference_records(payload.reference_images)
    history_refs = history_reference_records(refs)
    user_message = {
        "id": uuid.uuid4().hex,
        "role": "user",
        "content": payload.message,
        "created_at": now_ms(),
        "attachments": history_refs,
        "mode": payload.mode,
    }
    conversation["messages"].append(user_message)
    conversation["updated_at"] = now_ms()
    save_conversation(user_id, conversation)

    if payload.mode == "image":
        image_provider_id = payload.provider if payload.provider not in {"modelscope"} else "comfly"
        provider = get_api_provider(image_provider_id)
        default_model = (provider.get("image_models") or [IMAGE_MODEL])[0]
        model = selected_model(payload.image_model or payload.model, default_model)
        try:
            image_data, raw = await generate_ai_image(payload.message, payload.size, payload.quality, model, refs, provider["id"])
            if is_gpt_image_2_model(model):
                image_data = await normalize_ai_image_to_size(image_data, normalize_gpt_image_2_size(payload.size))
            local_url = await save_ai_image_to_output(image_data, prefix="chat_")
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=f"上游生图接口错误：{exc.response.text}") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"请求上游生图接口失败：{exc}") from exc
        assistant_message = {
            "id": uuid.uuid4().hex,
            "role": "assistant",
            "type": "image",
            "content": payload.message,
            "image_url": local_url,
            "created_at": now_ms(),
            "model": model,
            "raw_usage": raw.get("usage") if isinstance(raw, dict) else None,
        }
    else:
        chat_base, chat_hdrs, model = resolve_chat_provider(payload.provider, payload.model, payload.ms_model)
        _conv_provider = get_api_provider(payload.provider) if payload.provider not in ("modelscope",) else {}
        _conv_is_apimart = is_apimart_provider(_conv_provider)
        history = conversation["messages"][-MAX_HISTORY_MESSAGES:]
        upstream_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for item in history:
            msg = upstream_message_from_record(item)
            if msg:
                upstream_messages.append(msg)
        try:
            async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
                conv_req_body = {"model": model, "messages": upstream_messages}
                if _conv_is_apimart:
                    conv_req_body["stream"] = False
                response = await client.post(
                    f"{chat_base}/chat/completions",
                    headers=chat_hdrs,
                    json=conv_req_body,
                )
                response.raise_for_status()
                raw = response.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail=f"上游接口错误：{exc.response.text}") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"请求上游接口失败：{exc}") from exc
        raw_data = unwrap_apimart_response(raw) if isinstance(raw, dict) else raw
        assistant_message = {
            "id": uuid.uuid4().hex,
            "role": "assistant",
            "content": text_from_chat_response(raw).strip() or "接口返回了空回复。",
            "created_at": now_ms(),
            "model": model,
            "raw_usage": raw_data.get("usage") if isinstance(raw_data, dict) else None,
        }

    conversation["messages"].append(assistant_message)
    conversation["updated_at"] = now_ms()
    save_conversation(user_id, conversation)
    return {"conversation": conversation, "message": assistant_message}

async def chat_stream(payload: ChatRequest, request: Request, x_user_id: str = Header(default="")):
    if payload.mode == "image":
        raise HTTPException(status_code=400, detail="图片模式请使用 /api/chat")

    user_id = safe_user_id(x_user_id, request)
    conversation = (
        load_conversation(user_id, payload.conversation_id)
        if payload.conversation_id
        else new_conversation(user_id, display_title(payload.message))
    )
    if not conversation.get("messages"):
        conversation["title"] = display_title(payload.message)

    refs = request_reference_records(payload.reference_images)
    history_refs = history_reference_records(refs)
    user_message = {
        "id": uuid.uuid4().hex,
        "role": "user",
        "content": payload.message,
        "created_at": now_ms(),
        "attachments": history_refs,
        "mode": payload.mode,
    }
    conversation["messages"].append(user_message)
    conversation["updated_at"] = now_ms()
    save_conversation(user_id, conversation)

    chat_base, chat_hdrs, model = resolve_chat_provider(payload.provider, payload.model, payload.ms_model)
    history = conversation["messages"][-MAX_HISTORY_MESSAGES:]
    upstream_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in history:
        msg = upstream_message_from_record(item)
        if msg:
            upstream_messages.append(msg)

    async def stream():
        content_parts = []
        raw_usage = None
        yield sse_event({"type": "meta", "conversation": conversation})
        try:
            async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
                async with client.stream(
                    "POST",
                    f"{chat_base}/chat/completions",
                    headers=chat_hdrs,
                    json={"model": model, "messages": upstream_messages, "stream": True},
                ) as response:
                    if response.status_code >= 400:
                        detail = await response.aread()
                        yield sse_event({"type": "error", "detail": f"上游接口错误：{detail.decode('utf-8', errors='ignore')}"})
                        return
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            break
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(chunk, dict) and chunk.get("usage"):
                            raw_usage = chunk.get("usage")
                        delta = text_delta_from_chat_chunk(chunk)
                        if delta:
                            content_parts.append(delta)
                            yield sse_event({"type": "delta", "delta": delta})
        except httpx.HTTPError as exc:
            yield sse_event({"type": "error", "detail": f"请求上游接口失败：{exc}"})
            return

        assistant_message = {
            "id": uuid.uuid4().hex,
            "role": "assistant",
            "content": "".join(content_parts).strip() or "接口返回了空回复。",
            "created_at": now_ms(),
            "model": model,
            "raw_usage": raw_usage,
        }
        conversation["messages"].append(assistant_message)
        conversation["updated_at"] = now_ms()
        save_conversation(user_id, conversation)
        yield sse_event({"type": "done", "conversation": conversation, "message": assistant_message})

    return StreamingResponse(stream(), media_type="text/event-stream")

# --- 历史记录 ---




# --- ModelScope 角度控制 ---

async def poll_angle_cloud(req: CloudPollRequest):
    base_url = 'https://api-inference.modelscope.cn/'
    clean_token = (req.api_key or MODELSCOPE_API_KEY).strip()
    if not clean_token:
        raise HTTPException(status_code=400, detail="未提供 ModelScope API Key")

    headers = {
        "Authorization": f"Bearer {clean_token}",
        "Content-Type": "application/json",
        "X-ModelScope-Async-Mode": "true"
    }
    task_id = req.task_id
    print(f"Resuming polling for Angle Task: {task_id}")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for i in range(300):
                await asyncio.sleep(2)
                try:
                    result = await client.get(
                        f"{base_url}v1/tasks/{task_id}",
                        headers={**headers, "X-ModelScope-Task-Type": "image_generation"},
                    )
                    data = result.json()
                    status = data.get("task_status")

                    if status == "SUCCEED":
                        img_url = data["output_images"][0]
                        local_path = ""
                        try:
                            async with httpx.AsyncClient() as dl_client:
                                img_res = await dl_client.get(img_url)
                                if img_res.status_code == 200:
                                    filename = f"cloud_angle_{int(time.time())}.png"
                                    file_path = output_path_for(filename, "output")
                                    with open(file_path, "wb") as f:
                                        f.write(img_res.content)
                                    local_path = output_url_for(filename, "output")
                                else:
                                    local_path = img_url
                        except Exception:
                            local_path = img_url

                        record = {"timestamp": time.time(), "prompt": f"Resumed {task_id}", "images": [local_path], "type": "angle"}
                        save_to_history(record)
                        if req.client_id:
                            await manager.send_personal_message({"type": "cloud_status", "status": "SUCCEED", "task_id": task_id}, req.client_id)
                        return {"url": local_path}

                    elif status == "FAILED":
                        if req.client_id:
                            await manager.send_personal_message({"type": "cloud_status", "status": "FAILED", "task_id": task_id}, req.client_id)
                        raise Exception(f"ModelScope task failed: {data}")

                    if i % 5 == 0 and req.client_id:
                        await manager.send_personal_message({
                            "type": "cloud_status", "status": f"{status} ({i}/300)",
                            "task_id": task_id, "progress": i, "total": 300
                        }, req.client_id)

                except Exception as loop_e:
                    print(f"Angle polling error: {loop_e}")
                    continue

            if req.client_id:
                await manager.send_personal_message({"type": "cloud_status", "status": "TIMEOUT", "task_id": task_id}, req.client_id)
            return {"status": "timeout", "task_id": task_id, "message": "Task still pending"}

    except Exception as e:
        print(f"Angle polling error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

async def generate_angle_cloud(req: CloudGenRequest):
    base_url = 'https://api-inference.modelscope.cn/'
    clean_token = (req.api_key or MODELSCOPE_API_KEY).strip()
    if not clean_token:
        raise HTTPException(status_code=400, detail="未提供 ModelScope API Key")

    headers = {
        "Authorization": f"Bearer {clean_token}",
        "Content-Type": "application/json",
        "X-ModelScope-Async-Mode": "true"
    }
    model = selected_model(req.model, "Qwen/Qwen-Image-Edit-2511")
    payload = {
        "model": model,
        "prompt": req.prompt.strip(),
        "image_url": [modelscope_image_url(url, max_size=1536) for url in req.image_urls]
    }
    if req.resolution:
        payload["size"] = modelscope_size(req.resolution)
    if req.loras is not None:
        payload["loras"] = req.loras

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            submit_res = await client.post(f"{base_url}v1/images/generations", headers=headers, json=payload)
            if submit_res.status_code != 200:
                try:
                    detail = submit_res.json()
                except ValueError:
                    detail = submit_res.text
                raise HTTPException(status_code=submit_res.status_code, detail=detail)

            task_id = submit_res.json().get("task_id")
            print(f"Angle Task submitted, ID: {task_id}")

            for i in range(300):
                await asyncio.sleep(2)
                try:
                    result = await client.get(
                        f"{base_url}v1/tasks/{task_id}",
                        headers={**headers, "X-ModelScope-Task-Type": "image_generation"},
                    )
                    data = result.json()
                    status = data.get("task_status")

                    if status == "SUCCEED":
                        img_url = data["output_images"][0]
                        local_path = ""
                        try:
                            async with httpx.AsyncClient() as dl_client:
                                img_res = await dl_client.get(img_url)
                                if img_res.status_code == 200:
                                    filename = f"cloud_angle_{int(time.time())}.png"
                                    file_path = output_path_for(filename, "output")
                                    with open(file_path, "wb") as f:
                                        f.write(img_res.content)
                                    local_path = output_url_for(filename, "output")
                                else:
                                    local_path = img_url
                        except Exception:
                            local_path = img_url

                        record = {"timestamp": time.time(), "prompt": req.prompt, "images": [local_path], "type": "angle"}
                        save_to_history(record)
                        if req.client_id:
                            await manager.send_personal_message({"type": "cloud_status", "status": "SUCCEED", "task_id": task_id}, req.client_id)
                        if GLOBAL_LOOP:
                            asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(record), GLOBAL_LOOP)
                        return {"url": local_path, "task_id": task_id}

                    elif status == "FAILED":
                        if req.client_id:
                            await manager.send_personal_message({"type": "cloud_status", "status": "FAILED", "task_id": task_id}, req.client_id)
                        raise Exception(f"ModelScope task failed: {data}")

                    if i % 5 == 0 and req.client_id:
                        await manager.send_personal_message({
                            "type": "cloud_status", "status": f"{status} ({i}/300)",
                            "task_id": task_id, "progress": i, "total": 300
                        }, req.client_id)

                except Exception as loop_e:
                    print(f"Angle polling error: {loop_e}")
                    continue

            if req.client_id:
                await manager.send_personal_message({"type": "cloud_status", "status": "TIMEOUT", "task_id": task_id}, req.client_id)
            return {"status": "timeout", "task_id": task_id, "message": "Task still pending"}

    except HTTPException:
        raise
    except Exception as e:
        print(f"Angle generation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# --- ModelScope Z-Image 云端生图 ---

async def generate_cloud(req: CloudGenRequest):
    base_url = 'https://api-inference.modelscope.cn/'
    clean_token = (req.api_key or MODELSCOPE_API_KEY).strip()
    if not clean_token:
        raise HTTPException(status_code=400, detail="未提供 ModelScope API Key")

    headers = {
        "Authorization": f"Bearer {clean_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "Tongyi-MAI/Z-Image-Turbo",
        "prompt": req.prompt.strip(),
        "size": modelscope_size(req.resolution),
        "n": 1
    }
    if req.loras is not None:
        payload["loras"] = req.loras

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            submit_res = await client.post(
                f"{base_url}v1/images/generations",
                headers={**headers, "X-ModelScope-Async-Mode": "true"},
                json=payload
            )
            if submit_res.status_code != 200:
                try:
                    detail = submit_res.json()
                except ValueError:
                    detail = submit_res.text
                raise HTTPException(status_code=submit_res.status_code, detail=detail)

            task_id = submit_res.json().get("task_id")
            print(f"Z-Image Task submitted, ID: {task_id}")

            for i in range(200):
                await asyncio.sleep(3)
                try:
                    result = await client.get(
                        f"{base_url}v1/tasks/{task_id}",
                        headers={**headers, "X-ModelScope-Task-Type": "image_generation"},
                    )
                    data = result.json()
                    status = data.get("task_status")

                    if i % 5 == 0:
                        print(f"Task {task_id} status check {i}: {status}")

                    if status == "SUCCEED":
                        img_url = data["output_images"][0]
                        local_path = ""
                        try:
                            async with httpx.AsyncClient() as dl_client:
                                img_res = await dl_client.get(img_url)
                                if img_res.status_code == 200:
                                    filename = f"cloud_{int(time.time())}.png"
                                    file_path = output_path_for(filename, "output")
                                    with open(file_path, "wb") as f:
                                        f.write(img_res.content)
                                    local_path = output_url_for(filename, "output")
                                else:
                                    local_path = img_url
                        except Exception as dl_e:
                            print(f"Download error: {dl_e}")
                            local_path = img_url

                        record = {"timestamp": time.time(), "prompt": req.prompt, "images": [local_path], "type": "cloud"}
                        save_to_history(record)
                        try:
                            await manager.broadcast_new_image(record)
                        except Exception:
                            pass
                        return {"url": local_path}

                    elif status == "FAILED":
                        raise Exception(f"ModelScope task failed: {data}")

                except Exception as loop_e:
                    print(f"Polling error (retrying): {loop_e}")
                    continue

            raise Exception("Cloud generation timeout")

    except HTTPException:
        raise
    except Exception as e:
        print(f"Cloud generation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# --- ModelScope 通用图片生成（支持图生图） ---

async def ms_generate(req: MsGenerateRequest):
    base_url = 'https://api-inference.modelscope.cn/'
    clean_token = (req.api_key or MODELSCOPE_API_KEY).strip()
    if not clean_token:
        raise HTTPException(status_code=400, detail="未配置 ModelScope API Key，请在 API 设置中填写，或重新保存 ModelScope Token。")

    headers = {
        "Authorization": f"Bearer {clean_token}",
        "Content-Type": "application/json",
        "X-ModelScope-Async-Mode": "true"
    }
    payload = {
        "model": req.model,
        "prompt": req.prompt.strip(),
    }
    if req.width and req.height:
        payload["width"] = req.width
        payload["height"] = req.height
        payload["size"] = modelscope_size(req.size or f"{req.width}x{req.height}")
    elif req.size:
        payload["size"] = modelscope_size(req.size)
    if req.image_urls:
        payload["image_url"] = [modelscope_image_url(url, max_size=1536) for url in req.image_urls]
    if req.loras is not None:
        payload["loras"] = req.loras

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            submit_res = await client.post(
                f"{base_url}v1/images/generations",
                headers=headers,
                json=payload
            )
            if submit_res.status_code != 200:
                try:
                    detail = submit_res.json()
                except ValueError:
                    detail = submit_res.text
                raise HTTPException(status_code=submit_res.status_code, detail=detail)

            task_id = submit_res.json().get("task_id")
            print(f"MS Generate Task submitted ({req.model}), ID: {task_id}")

            TERMINAL_FAILED_STATUSES = {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED", "TIMEOUT", "REVOKED"}

            for i in range(300):
                await asyncio.sleep(2)
                try:
                    result = await client.get(
                        f"{base_url}v1/tasks/{task_id}",
                        headers={**headers, "X-ModelScope-Task-Type": "image_generation"},
                    )
                    data = result.json()
                    status = data.get("task_status")
                    print(f"MS Task {task_id} poll {i}: status={status}")

                    if status == "SUCCEED":
                        img_url = data["output_images"][0]
                        local_path = ""
                        try:
                            async with httpx.AsyncClient() as dl_client:
                                img_res = await dl_client.get(img_url)
                                if img_res.status_code == 200:
                                    filename = f"ms_{req.model.replace('/', '_').replace(':', '_')}_{int(time.time())}.png"
                                    file_path = output_path_for(filename, "output")
                                    with open(file_path, "wb") as f:
                                        f.write(img_res.content)
                                    local_path = output_url_for(filename, "output")
                                else:
                                    local_path = img_url
                        except Exception:
                            local_path = img_url

                        record = {
                            "timestamp": time.time(),
                            "prompt": req.prompt,
                            "images": [local_path],
                            "type": "klein",
                            "model": req.model,
                        }
                        save_to_history(record)
                        if GLOBAL_LOOP:
                            asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(record), GLOBAL_LOOP)
                        return {"url": local_path, "task_id": task_id}

                    elif status in TERMINAL_FAILED_STATUSES:
                        error_info = data.get("error_info") or data.get("message") or data.get("detail") or str(data)
                        raise HTTPException(status_code=502, detail=f"MS task {status}: {error_info}")

                except HTTPException:
                    raise
                except Exception as loop_e:
                    print(f"MS polling error: {loop_e}")
                    continue

            raise HTTPException(status_code=504, detail="MS 生图超时")

    except HTTPException:
        raise
    except Exception as e:
        print(f"MS generate error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# --- 本地 ComfyUI 生图 ---

def generate(req: GenerateRequest):
    global NEXT_TASK_ID
    current_task = None
    target_backend = None
    with QUEUE_LOCK:
        task_id = NEXT_TASK_ID
        NEXT_TASK_ID += 1
        current_task = {"task_id": task_id, "client_id": req.client_id}
        QUEUE.append(current_task)

    try:
        required_images = []
        for node_id, node_inputs in req.params.items():
            if isinstance(node_inputs, dict) and "image" in node_inputs:
                image_name = node_inputs["image"]
                if isinstance(image_name, str) and image_name:
                    required_images.append(image_name)

        target_backend = get_best_backend(required_images)
        with LOAD_LOCK:
            BACKEND_LOCAL_LOAD[target_backend] += 1

        for image_name in required_images:
            need_sync = False
            try:
                check_url = f"http://{target_backend}/view?filename={urllib.parse.quote(image_name)}&type=input"
                resp = requests.get(check_url, stream=True, timeout=0.5)
                resp.close()
                if resp.status_code != 200:
                    need_sync = True
            except requests.RequestException:
                need_sync = True

            if need_sync:
                image_content = None
                image_type = "image/png"
                for addr in COMFYUI_INSTANCES:
                    if addr == target_backend: continue
                    try:
                        src_url = f"http://{addr}/view?filename={urllib.parse.quote(image_name)}&type=input"
                        r = requests.get(src_url, timeout=5)
                        if r.status_code == 200:
                            image_content = r.content
                            image_type = r.headers.get("Content-Type", "image/png")
                            break
                    except requests.RequestException: continue

                if image_content:
                    try:
                        files = {'image': (image_name, image_content, image_type)}
                        requests.post(f"http://{target_backend}/upload/image", files=files, timeout=10)
                    except Exception as e:
                        print(f"Sync upload failed: {e}")

        workflow_path = os.path.join(WORKFLOW_DIR, req.workflow_json)
        if not os.path.exists(workflow_path) and req.workflow_json == "Z-Image.json":
            workflow_path = WORKFLOW_PATH
        if not os.path.exists(workflow_path):
            raise Exception(f"Workflow file not found: {req.workflow_json}")

        with open(workflow_path, 'r', encoding='utf-8') as f:
            workflow = json.load(f)

        seed = random.randint(1, 10**15)

        if "23" in workflow and req.prompt:
            workflow["23"]["inputs"]["text"] = req.prompt
        if "144" in workflow:
            workflow["144"]["inputs"]["width"] = req.width
            workflow["144"]["inputs"]["height"] = req.height
        if "22" in workflow:
            workflow["22"]["inputs"]["seed"] = seed
        if "158" in workflow:
            workflow["158"]["inputs"]["noise_seed"] = seed
        for node_id in ["146", "181"]:
            if node_id in workflow and "inputs" in workflow[node_id] and "seed" in workflow[node_id]["inputs"]:
                workflow[node_id]["inputs"]["seed"] = seed
        if "184" in workflow and "inputs" in workflow["184"] and "seed" in workflow["184"]["inputs"]:
            workflow["184"]["inputs"]["seed"] = seed
        if "172" in workflow and "inputs" in workflow["172"] and "seed" in workflow["172"]["inputs"]:
            workflow["172"]["inputs"]["seed"] = seed % 4294967295
        if "14" in workflow and "inputs" in workflow["14"] and "seed" in workflow["14"]["inputs"]:
            workflow["14"]["inputs"]["seed"] = seed

        for node_id, node_inputs in req.params.items():
            if node_id in workflow:
                if "inputs" not in workflow[node_id]:
                    workflow[node_id]["inputs"] = {}
                for input_name, value in node_inputs.items():
                    workflow[node_id]["inputs"][input_name] = value

        p = {"prompt": workflow, "client_id": CLIENT_ID}
        data = json.dumps(p).encode('utf-8')
        try:
            post_req = urllib.request.Request(f"http://{target_backend}/prompt", data=data)
            prompt_id = json.loads(urllib.request.urlopen(post_req, timeout=10).read())['prompt_id']
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            raise Exception(f"HTTP Error {e.code}: {error_body}")

        history_data = None
        for i in range(COMFYUI_HISTORY_TIMEOUT):
            try:
                res = get_comfy_history(target_backend, prompt_id)
                if prompt_id in res:
                    history_data = res[prompt_id]
                    break
            except Exception:
                pass
            time.sleep(1)

        if not history_data:
            raise Exception("ComfyUI 渲染超时")

        local_images = []
        local_videos = []
        local_urls = []
        current_timestamp = time.time()
        if 'outputs' in history_data:
            for node_id in history_data['outputs']:
                node_output = history_data['outputs'][node_id]
                if 'images' in node_output:
                    for img in node_output['images']:
                        prefix = f"{req.type}_{int(current_timestamp)}_"
                        local_path = download_comfy_output(target_backend, img, prefix=prefix)
                        if req.convert_to_jpg:
                            local_path = convert_output_to_jpg(local_path)
                        local_images.append(local_path)
                        local_urls.append(local_path)
                for output_key in ("videos", "gifs", "animated"):
                    for video in node_output.get(output_key, []) or []:
                        if not isinstance(video, dict) or not video.get("filename"):
                            continue
                        prefix = f"{req.type}_{int(current_timestamp)}_"
                        local_path = download_comfy_output(target_backend, video, prefix=prefix)
                        local_videos.append(local_path)
                        local_urls.append(local_path)

        result = {
            "prompt": req.prompt if req.prompt else "Detail Enhance",
            "images": local_images,
            "videos": local_videos,
            "outputs": local_urls,
            "seed": seed,
            "timestamp": current_timestamp,
            "type": req.type,
            "workflow_json": req.workflow_json,
            "task_id": task_id,
            "prompt_id": prompt_id,
            "backend": target_backend,
            "params": req.params
        }
        save_to_history(result)
        if GLOBAL_LOOP:
            asyncio.run_coroutine_threadsafe(manager.broadcast_new_image(result), GLOBAL_LOOP)
        return result

    except Exception as e:
        return {"images": [], "error": str(e)}
    finally:
        if target_backend:
            with LOAD_LOCK:
                if BACKEND_LOCAL_LOAD.get(target_backend, 0) > 0:
                    BACKEND_LOCAL_LOAD[target_backend] -= 1
        if current_task:
            with QUEUE_LOCK:
                if current_task in QUEUE:
                    QUEUE.remove(current_task)

# --- ComfyUI 工作流管理 ---

BUILTIN_WORKFLOWS = {"Z-Image.json", "Z-Image-Enhance.json", "2511.json", "klein-enhance.json", "Flux2-Klein.json", "upscale.json"}
CUSTOM_WORKFLOW_FOLDER = "custom"

from app.services.workflow_service import workflow_path_from_name
from app.services.workflow_service import workflow_config_path
from app.services.workflow_service import is_builtin_workflow
from app.services.workflow_service import get_comfyui_instances
from app.services.workflow_service import save_comfyui_instances
from app.services.workflow_service import list_workflows
from app.services.workflow_service import get_workflow
from app.services.workflow_service import upload_workflow
from app.services.workflow_service import save_workflow_config
from app.services.workflow_service import delete_workflow
from app.services.workflow_service import run_workflow














