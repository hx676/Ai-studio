import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import uuid
from typing import List

from fastapi import File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app import legacy
from app.models.digital_human import (
    DigitalHumanConfigPayload,
    DigitalHumanGenerateRequest,
    DigitalHumanPersonPayload,
    DigitalHumanPersonVideoPayload,
    DigitalHumanPersonVideosPayload,
    DigitalHumanTTSRequest,
    DigitalHumanVoiceMetaPayload,
)
from app.services.storage_service import content_type_for_path, output_file_from_url, save_upload_limited
from app.services.file_safety import safe_filename_stem, sanitize_output_filename as safe_output_filename
from app.services.tts_service import (
    enrich_tts_voice_item,
    ensure_tts_service,
    generate_digital_human_tts,
    list_tts_voices,
    normalize_bool,
    save_tts_voice,
    save_tts_voice_preview_audio,
    sanitize_tts_voice_name,
    stop_tts_for_gpu_handoff,
    tts_voice_embedding_for_name,
    tts_voice_file_for_name,
    tts_voice_library_dir,
)

try:
    import psutil
except Exception:
    psutil = None

try:
    from PIL import Image, ImageStat
except Exception:
    Image = None
    ImageStat = None


def __getattr__(name):
    return getattr(legacy, name)


def _legacy_ref(name):
    return getattr(legacy, name)


ASSETS_DIR = _legacy_ref("ASSETS_DIR")
BASE_DIR = _legacy_ref("BASE_DIR")
DATA_DIR = _legacy_ref("DATA_DIR")
DIGITAL_HUMAN_AUDIO_DIR = _legacy_ref("DIGITAL_HUMAN_AUDIO_DIR")
DIGITAL_HUMAN_CONFIG_FILE = _legacy_ref("DIGITAL_HUMAN_CONFIG_FILE")
DIGITAL_HUMAN_CONFIG_LOCK = _legacy_ref("DIGITAL_HUMAN_CONFIG_LOCK")
DIGITAL_HUMAN_INPUT_DIR = _legacy_ref("DIGITAL_HUMAN_INPUT_DIR")
DIGITAL_HUMAN_LIBRARY_FILE = _legacy_ref("DIGITAL_HUMAN_LIBRARY_FILE")
DIGITAL_HUMAN_LIBRARY_LOCK = _legacy_ref("DIGITAL_HUMAN_LIBRARY_LOCK")
DIGITAL_HUMAN_TASKS = _legacy_ref("DIGITAL_HUMAN_TASKS")
DIGITAL_HUMAN_TASK_LOCK = _legacy_ref("DIGITAL_HUMAN_TASK_LOCK")
DIGITAL_HUMAN_VIDEO_DIR = _legacy_ref("DIGITAL_HUMAN_VIDEO_DIR")
OUTPUT_INPUT_DIR = _legacy_ref("OUTPUT_INPUT_DIR")
OUTPUT_OUTPUT_DIR = _legacy_ref("OUTPUT_OUTPUT_DIR")
DIGITAL_HUMAN_POSTER_DIR = os.path.join(DIGITAL_HUMAN_INPUT_DIR, "posters")
DIGITAL_HUMAN_QUEUE = []
DIGITAL_HUMAN_QUEUE_WORKER = None
DIGITAL_HUMAN_RECENT_LIMIT = 30
DIGITAL_HUMAN_TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}
DIGITAL_HUMAN_QUEUE_STATE = {
    "paused": False,
    "pause_reason": "",
    "paused_at": 0,
    "consecutive_infra_failures": 0,
}
DIGITAL_HUMAN_RESOURCE_LOCK = asyncio.Lock()
DIGITAL_HUMAN_RESOURCE_STATE = {
    "active": "idle",
    "task_id": "",
    "stage": "",
    "started_at": 0,
    "owner": "",
    "waiting_tts": 0,
    "waiting_heygem": 0,
}
DIGITAL_HUMAN_RETRYABLE_FAILURE_TYPES = {
    "heygem_task_not_found",
    "heygem_query_timeout",
    "heygem_submit_failed",
    "heygem_gpu_conflict",
}
DIGITAL_HUMAN_INFRA_FAILURE_TYPES = {
    "heygem_task_not_found",
    "heygem_stall_at_20",
    "heygem_queue_blocked",
    "heygem_query_timeout",
    "heygem_submit_failed",
    "heygem_not_ready",
    "heygem_gpu_conflict",
}
HEYGEM_HARD_STOP_FAILURE_TYPES = {"heygem_queue_blocked", "heygem_gpu_conflict"}

digital_human_log = legacy.digital_human_log
safe_upstream_summary = legacy.safe_upstream_summary


def _norm_path_text(value):
    return str(value or "").replace("/", "\\").lower()


def heygem_app_local_processes(config):
    if psutil is None:
        return []
    root = _norm_path_text(os.path.abspath(((config or {}).get("heygem") or {}).get("root_dir") or os.path.join(BASE_DIR, "heygem-win-fix", "heygem-win")))
    current_pid = os.getpid()
    items = []
    for process in psutil.process_iter(["pid", "name", "exe", "cmdline", "ppid", "create_time"]):
        try:
            pid = int(process.info.get("pid") or 0)
            if not pid or pid == current_pid:
                continue
            cmdline = " ".join(process.info.get("cmdline") or [])
            text = _norm_path_text(f"{process.info.get('exe') or ''} {cmdline}")
            if root and root in text and "app_local.py" in text:
                items.append({
                    "pid": pid,
                    "ppid": int(process.info.get("ppid") or 0),
                    "name": process.info.get("name") or "",
                    "cmd": cmdline[:260],
                    "create_time": float(process.info.get("create_time") or 0),
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:
            continue
    main_times = []
    for process in psutil.process_iter(["pid", "exe", "cmdline", "create_time"]):
        try:
            cmdline = " ".join(process.info.get("cmdline") or [])
            text = _norm_path_text(f"{process.info.get('exe') or ''} {cmdline}")
            if root and root in text and "app.py" in text and "app_local.py" not in text:
                main_times.append(float(process.info.get("create_time") or 0))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:
            continue
    if len(items) == 1 and main_times:
        rest_time = float(items[0].get("create_time") or 0)
        main_time = min(main_times)
        if not rest_time or not main_time or rest_time >= main_time - 3:
            return []
    if len(items) > 1:
        newest = max(float(item.get("create_time") or 0) for item in items)
        items = [
            item
            for item in items
            if not float(item.get("create_time") or 0) or float(item.get("create_time") or 0) < newest
        ]
    return items


def heygem_gpu_conflict_detail(message, conflicts=None, handoff=None, retryable=True):
    detail = {
        "message": message,
        "failure_type": "heygem_gpu_conflict",
        "retryable": bool(retryable),
    }
    if conflicts:
        detail["conflicts"] = conflicts
    if handoff:
        detail["tts_handoff"] = handoff
    return detail


def pause_digital_human_queue(reason):
    with DIGITAL_HUMAN_TASK_LOCK:
        DIGITAL_HUMAN_QUEUE_STATE.update({
            "paused": True,
            "pause_reason": str(reason or "数字人队列已暂停。"),
            "paused_at": time.time(),
        })
        update_digital_human_queue_positions_locked()


async def ensure_heygem_gpu_lane(config, task_id):
    conflicts = heygem_app_local_processes(config)
    if conflicts:
        message = "检测到本项目残留 HeyGem app_local.py 进程，可能正在占用显存或内部队列；请先停止残留 HeyGem 后再生成。"
        raise HTTPException(status_code=409, detail=heygem_gpu_conflict_detail(message, conflicts=conflicts, retryable=True))
    with DIGITAL_HUMAN_TASK_LOCK:
        task = DIGITAL_HUMAN_TASKS.get(task_id)
        if task is not None:
            task["stage"] = "gpu-handoff"
            task["updated_at"] = time.time()
    handoff = await stop_tts_for_gpu_handoff(config, task_id)
    if not handoff.get("ok"):
        message = "TTS 已生成音频，但 TTS 进程/端口未能释放，暂不提交 HeyGem，避免 TTS 与 HeyGem 抢显存。"
        raise HTTPException(status_code=409, detail=heygem_gpu_conflict_detail(message, handoff=handoff, retryable=True))
    conflicts = heygem_app_local_processes(config)
    if conflicts:
        message = "TTS 显存释放后仍检测到残留 HeyGem app_local.py 进程；请先停止残留 HeyGem 后再生成。"
        raise HTTPException(status_code=409, detail=heygem_gpu_conflict_detail(message, conflicts=conflicts, handoff=handoff, retryable=True))
    digital_human_log(f"task {task_id} GPU handoff ready stopped_tts={handoff.get('stopped_pids') or []} waited={handoff.get('waited')}")
    return handoff


def digital_human_resource_snapshot():
    snapshot = dict(DIGITAL_HUMAN_RESOURCE_STATE)
    snapshot.pop("owner", None)
    return snapshot


def update_digital_human_task_stage(task_id, stage):
    if not task_id:
        return
    with DIGITAL_HUMAN_TASK_LOCK:
        task = DIGITAL_HUMAN_TASKS.get(task_id)
        if task and task.get("status") == "running":
            task["stage"] = stage
            task["updated_at"] = time.time()


async def acquire_digital_human_resource(kind, task_id="", waiting_stage="", running_stage=""):
    kind = "heygem" if kind == "heygem" else "tts"
    waiting_key = "waiting_heygem" if kind == "heygem" else "waiting_tts"
    started_wait = time.time()
    if waiting_stage:
        update_digital_human_task_stage(task_id, waiting_stage)
    DIGITAL_HUMAN_RESOURCE_STATE[waiting_key] = int(DIGITAL_HUMAN_RESOURCE_STATE.get(waiting_key) or 0) + 1
    if DIGITAL_HUMAN_RESOURCE_STATE.get("active") != "idle":
        digital_human_log(
            f"resource wait kind={kind} task={task_id or '-'} active={DIGITAL_HUMAN_RESOURCE_STATE.get('active')}"
        )
    try:
        await DIGITAL_HUMAN_RESOURCE_LOCK.acquire()
    except BaseException:
        DIGITAL_HUMAN_RESOURCE_STATE[waiting_key] = max(0, int(DIGITAL_HUMAN_RESOURCE_STATE.get(waiting_key) or 0) - 1)
        raise
    DIGITAL_HUMAN_RESOURCE_STATE[waiting_key] = max(0, int(DIGITAL_HUMAN_RESOURCE_STATE.get(waiting_key) or 0) - 1)
    DIGITAL_HUMAN_RESOURCE_STATE.update({
        "active": kind,
        "task_id": task_id or "",
        "stage": running_stage or kind,
        "started_at": time.time(),
    })
    if running_stage:
        update_digital_human_task_stage(task_id, running_stage)
    owner = uuid.uuid4().hex
    digital_human_log(
        f"resource acquired kind={kind} task={task_id or '-'} wait={time.time() - started_wait:.1f}s"
    )
    DIGITAL_HUMAN_RESOURCE_STATE["owner"] = owner
    return owner


def release_digital_human_resource(kind, task_id="", owner=""):
    active = DIGITAL_HUMAN_RESOURCE_STATE.get("active") or "idle"
    current_owner = DIGITAL_HUMAN_RESOURCE_STATE.get("owner") or ""
    if current_owner and owner != current_owner:
        digital_human_log(f"resource release skipped kind={kind} task={task_id or '-'} owner mismatch")
        return
    if active != "idle":
        elapsed = time.time() - float(DIGITAL_HUMAN_RESOURCE_STATE.get("started_at") or time.time())
        digital_human_log(f"resource released kind={active} task={task_id or '-'} elapsed={elapsed:.1f}s")
    DIGITAL_HUMAN_RESOURCE_STATE.update({
        "active": "idle",
        "task_id": "",
        "stage": "",
        "started_at": 0,
        "owner": "",
    })
    if DIGITAL_HUMAN_RESOURCE_LOCK.locked():
        DIGITAL_HUMAN_RESOURCE_LOCK.release()


async def run_with_digital_human_resource(kind, task_id, waiting_stage, running_stage, operation):
    owner = await acquire_digital_human_resource(kind, task_id, waiting_stage, running_stage)
    try:
        return await operation()
    finally:
        release_digital_human_resource(kind, task_id, owner)


def _heygem_service():
    from app.services import heygem_service

    return heygem_service


def heygem_root_dir(config):
    return _heygem_service().heygem_root_dir(config)


async def check_heygem_health(config):
    return await _heygem_service().check_heygem_health(config)


async def generate_heygem_video_monitored(audio_path, video_path, config, task_id="", request_base_url="", job_code=""):
    return await _heygem_service().generate_heygem_video_monitored(audio_path, video_path, config, task_id, request_base_url, job_code)


def digital_human_default_config():
    tts_root = os.path.join(BASE_DIR, "index-tts-2")
    heygem_root = os.path.join(BASE_DIR, "heygem-win-fix", "heygem-win")
    return {
        "public_base_url": os.getenv("DIGITAL_HUMAN_PUBLIC_BASE_URL", ""),
        "tts": {
            "mode": os.getenv("DIGITAL_HUMAN_TTS_MODE", "api"),
            "base_url": os.getenv("DIGITAL_HUMAN_TTS_BASE_URL", "http://localhost:7861/"),
            "generate_path": os.getenv("DIGITAL_HUMAN_TTS_GENERATE_PATH", ""),
            "root_dir": os.getenv("DIGITAL_HUMAN_TTS_ROOT", tts_root),
            "python_path": os.getenv("DIGITAL_HUMAN_TTS_PYTHON", os.path.join(tts_root, "py312", "python.exe")),
            "script_path": os.getenv("DIGITAL_HUMAN_TTS_SCRIPT", os.path.join(tts_root, "app.py")),
            "config_path": os.getenv("DIGITAL_HUMAN_TTS_CONFIG", os.path.join(tts_root, "checkpoints", "config.yaml")),
            "model_dir": os.getenv("DIGITAL_HUMAN_TTS_MODEL_DIR", os.path.join(tts_root, "checkpoints")),
            "default_voice": os.getenv("DIGITAL_HUMAN_TTS_DEFAULT_VOICE", "使用参考音频"),
        },
        "heygem": {
            "base_url": os.getenv("DIGITAL_HUMAN_HEYGEM_BASE_URL", "http://127.0.0.1:7860/"),
            "api_base_url": os.getenv("DIGITAL_HUMAN_HEYGEM_API_BASE_URL", "http://127.0.0.1:8383/"),
            "submit_path": os.getenv("DIGITAL_HUMAN_HEYGEM_SUBMIT_PATH", "/easy/submit"),
            "query_path": os.getenv("DIGITAL_HUMAN_HEYGEM_QUERY_PATH", "/easy/query"),
            "min_resolution": float(os.getenv("DIGITAL_HUMAN_HEYGEM_MIN_RESOLUTION", "720") or 720),
            "if_res": str(os.getenv("DIGITAL_HUMAN_HEYGEM_IF_RES", "false")).lower() in {"1", "true", "yes", "on"},
            "max_wait_seconds": int(os.getenv("DIGITAL_HUMAN_HEYGEM_MAX_WAIT_SECONDS", "1800") or 1800),
            "stall_timeout_seconds": int(os.getenv("DIGITAL_HUMAN_HEYGEM_STALL_TIMEOUT_SECONDS", "240") or 240),
            "root_dir": heygem_root,
        },
    }

def merge_dict(base, override):
    result = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result.get(key), value)
        elif value is not None:
            result[key] = value
    return result

def load_digital_human_config():
    config = digital_human_default_config()
    if os.path.exists(DIGITAL_HUMAN_CONFIG_FILE):
        try:
            with open(DIGITAL_HUMAN_CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            config = merge_dict(config, saved)
        except Exception as e:
            print(f"Load digital human config failed: {e}")
    return config

def save_digital_human_config(config):
    os.makedirs(DATA_DIR, exist_ok=True)
    with DIGITAL_HUMAN_CONFIG_LOCK:
        with open(DIGITAL_HUMAN_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

def normalize_digital_human_config(payload=None):
    config = load_digital_human_config()
    if payload:
        data = payload.dict() if hasattr(payload, "dict") else dict(payload or {})
        config = merge_dict(config, data)
    config["tts"] = merge_dict(digital_human_default_config()["tts"], config.get("tts") or {})
    config["heygem"] = merge_dict(digital_human_default_config()["heygem"], config.get("heygem") or {})
    config["tts"]["mode"] = "api"
    if not str(config["tts"].get("base_url") or "").strip():
        config["tts"]["base_url"] = "http://localhost:7861/"
    if not str(config["heygem"].get("base_url") or "").strip():
        config["heygem"]["base_url"] = "http://127.0.0.1:7860/"
    if not str(config["heygem"].get("api_base_url") or "").strip():
        config["heygem"]["api_base_url"] = "http://127.0.0.1:8383/"
    try:
        config["heygem"]["min_resolution"] = float(config["heygem"].get("min_resolution") or 720)
    except Exception:
        config["heygem"]["min_resolution"] = 720
    config["heygem"]["if_res"] = normalize_bool(config["heygem"].get("if_res"), False)
    try:
        config["heygem"]["max_wait_seconds"] = max(60, int(config["heygem"].get("max_wait_seconds") or 1800))
    except Exception:
        config["heygem"]["max_wait_seconds"] = 1800
    try:
        config["heygem"]["stall_timeout_seconds"] = max(30, int(config["heygem"].get("stall_timeout_seconds") or 240))
    except Exception:
        config["heygem"]["stall_timeout_seconds"] = 240
    return config

def safe_join_url(base, path_value):
    base = str(base or "").strip().rstrip("/")
    path_value = str(path_value or "").strip()
    if not base:
        return ""
    if not path_value:
        return base
    if path_value.startswith("http://") or path_value.startswith("https://"):
        return path_value
    return base + "/" + path_value.lstrip("/")

def normalize_tts_base_url(base_url):
    base_url = str(base_url or "http://localhost:7861/").strip()
    if not base_url:
        base_url = "http://localhost:7861/"
    if not base_url.startswith(("http://", "https://")):
        base_url = "http://" + base_url
    return base_url.rstrip("/") + "/"

def normalize_service_base_url(base_url, fallback):
    base_url = str(base_url or fallback).strip() or fallback
    if not base_url.startswith(("http://", "https://")):
        base_url = "http://" + base_url
    return base_url.rstrip("/") + "/"

def tts_port_from_base_url(base_url):
    parsed = urllib.parse.urlparse(normalize_tts_base_url(base_url))
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80

def public_url_for(local_url, request=None, config=None):
    if not local_url:
        return ""
    if str(local_url).startswith("http://") or str(local_url).startswith("https://"):
        return local_url
    base = str((config or {}).get("public_base_url") or "").strip().rstrip("/")
    if not base and request is not None:
        base = str(request.base_url).rstrip("/")
    return base + str(local_url)

def digital_human_output_url(path):
    abs_path = os.path.abspath(path)
    root = os.path.abspath(ASSETS_DIR)
    if os.path.commonpath([root, abs_path]) != root:
        return ""
    rel = os.path.relpath(abs_path, root).replace("\\", "/")
    return f"/assets/{urllib.parse.quote(rel, safe='/')}"

def digital_human_media_url(path):
    if not path:
        return ""
    local = digital_human_local_path(path)
    if local:
        asset_url = digital_human_output_url(local)
        if asset_url:
            return asset_url
        return f"/api/digital-human/media?path={urllib.parse.quote(local)}"
    return str(path) if str(path).startswith(("/assets/", "/output/", "http://", "https://")) else ""

def is_digital_human_media_path_allowed(path, config=None):
    if not path:
        return False
    config = normalize_digital_human_config(config)
    abs_path = os.path.abspath(path)
    heygem_root = heygem_root_dir(config)
    roots = [
        os.path.abspath(DIGITAL_HUMAN_INPUT_DIR),
        os.path.abspath(DIGITAL_HUMAN_AUDIO_DIR),
        os.path.abspath(DIGITAL_HUMAN_VIDEO_DIR),
        os.path.abspath(tts_voice_library_dir(config)),
        os.path.abspath(os.path.join(heygem_root, "save")),
        os.path.abspath(os.path.join(heygem_root, "result")),
        os.path.abspath(os.path.join(heygem_root, "视频输出")),
    ]
    try:
        return any(os.path.commonpath([root, abs_path]) == root for root in roots)
    except ValueError:
        return False

def is_path_under(root, path):
    if not root or not path:
        return False
    try:
        return os.path.commonpath([os.path.abspath(root), os.path.abspath(path)]) == os.path.abspath(root)
    except ValueError:
        return False

def sanitize_output_filename(name, fallback, ext):
    return safe_output_filename(name, fallback, ext)

def digital_human_ffmpeg_candidates(config=None):
    config = normalize_digital_human_config(config)
    candidates = []
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        candidates.append(system_ffmpeg)
    heygem_root = heygem_root_dir(config)
    tts_root = str((config.get("tts") or {}).get("root_dir") or "")
    candidates.extend([
        os.path.join(heygem_root, "py38", "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(tts_root, "py312", "ffmpeg", "bin", "ffmpeg.exe"),
    ])
    result = []
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = os.path.abspath(candidate)
        key = path.lower()
        if key in seen or not os.path.isfile(path):
            continue
        seen.add(key)
        result.append(path)
    return result

def digital_human_ffprobe_candidates(config=None):
    candidates = [
        os.getenv("FFPROBE_PATH", ""),
        shutil.which("ffprobe") or "",
    ]
    for ffmpeg in digital_human_ffmpeg_candidates(config):
        folder = os.path.dirname(ffmpeg)
        candidates.extend([
            os.path.join(folder, "ffprobe.exe"),
            os.path.join(folder, "ffprobe"),
        ])
    result = []
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = os.path.abspath(candidate)
        key = path.lower()
        if key in seen or not os.path.isfile(path):
            continue
        seen.add(key)
        result.append(path)
    return result

def _digital_human_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def probe_digital_human_video_metadata(video_path, config=None):
    local = digital_human_local_path(video_path) or str(video_path or "")
    if not local or not os.path.isfile(local):
        return {}
    ffprobe_candidates = digital_human_ffprobe_candidates(config)
    if not ffprobe_candidates:
        return {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for ffprobe in ffprobe_candidates:
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height,duration:stream_tags=rotate:format=duration",
                    "-of",
                    "json",
                    local,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                check=False,
                creationflags=creationflags,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            continue
        stream = next((item for item in data.get("streams") or [] if isinstance(item, dict)), {})
        width = int(_digital_human_float(stream.get("width"), 0))
        height = int(_digital_human_float(stream.get("height"), 0))
        rotate = str((stream.get("tags") or {}).get("rotate") or "").strip()
        if rotate in {"90", "270", "-90", "-270"}:
            width, height = height, width
        duration = _digital_human_float(stream.get("duration"), 0.0) or _digital_human_float((data.get("format") or {}).get("duration"), 0.0)
        if width <= 0 or height <= 0:
            return {}
        return {
            "width": width,
            "height": height,
            "duration": round(duration, 3) if duration > 0 else 0,
            "aspect_ratio": round(width / height, 6),
            "orientation": "portrait" if height > width else ("landscape" if width > height else "square"),
        }
    return {}

def enrich_digital_human_video_metadata(video, config=None, force=False):
    if not isinstance(video, dict):
        return video
    has_size = int(_digital_human_float(video.get("width"), 0)) > 0 and int(_digital_human_float(video.get("height"), 0)) > 0
    if has_size and not force:
        width = int(_digital_human_float(video.get("width"), 0))
        height = int(_digital_human_float(video.get("height"), 0))
        video["width"] = width
        video["height"] = height
        video["aspect_ratio"] = _digital_human_float(video.get("aspect_ratio"), 0.0) or round(width / height, 6)
        video["orientation"] = video.get("orientation") or ("portrait" if height > width else ("landscape" if width > height else "square"))
        return video
    metadata = probe_digital_human_video_metadata(video.get("path") or video.get("url") or "", config=config)
    if metadata:
        video.update(metadata)
    return video

def digital_human_poster_path(video_path):
    stem = safe_filename_stem(video_path, "poster")
    digest = hashlib.sha1(os.path.abspath(video_path).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return os.path.join(DIGITAL_HUMAN_POSTER_DIR, f"{stem}_{digest}.jpg")

def image_brightness(path):
    if not Image or not ImageStat:
        return 1.0
    try:
        with Image.open(path) as img:
            img = img.convert("L")
            img.thumbnail((96, 96))
            return float(ImageStat.Stat(img).mean[0])
    except Exception:
        return 0.0

def generate_digital_human_video_poster(video_path, config=None, force=False):
    video_path = digital_human_local_path(video_path)
    if not video_path or not os.path.isfile(video_path):
        return ""
    target = digital_human_poster_path(video_path)
    if os.path.isfile(target) and not force:
        return digital_human_media_url(target)
    ffmpeg_candidates = digital_human_ffmpeg_candidates(config)
    if not ffmpeg_candidates:
        return ""
    os.makedirs(DIGITAL_HUMAN_POSTER_DIR, exist_ok=True)
    temp_paths = []
    best_path = ""
    best_score = -1.0
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for ffmpeg in ffmpeg_candidates:
        for index, second in enumerate((0.8, 1.5, 3.0)):
            temp_path = f"{target}.{index}.tmp.jpg"
            temp_paths.append(temp_path)
            command = [
                ffmpeg,
                "-y",
                "-nostdin",
                "-loglevel",
                "error",
                "-ss",
                f"{second:.2f}",
                "-i",
                video_path,
                "-frames:v",
                "1",
                "-vf",
                "scale=720:-2:force_original_aspect_ratio=decrease",
                "-q:v",
                "3",
                temp_path,
            ]
            try:
                subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    check=False,
                    creationflags=creationflags,
                )
            except Exception:
                continue
            if not os.path.isfile(temp_path) or os.path.getsize(temp_path) <= 0:
                continue
            score = image_brightness(temp_path)
            if score > best_score:
                best_score = score
                best_path = temp_path
            if score >= 16:
                break
        if best_path:
            break
    try:
        if best_path:
            os.replace(best_path, target)
            return digital_human_media_url(target)
    finally:
        for temp_path in temp_paths:
            if temp_path != best_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
    return ""

def ensure_digital_human_video_poster(video, config=None, force=False):
    raw = video or {}
    existing = digital_human_local_path(raw.get("poster_url") or raw.get("poster_path") or "")
    if existing and os.path.isfile(existing) and not force:
        return digital_human_media_url(existing)
    video_path = digital_human_local_path(raw.get("path") or raw.get("url") or "")
    if not video_path:
        return ""
    return generate_digital_human_video_poster(video_path, config=config, force=force)

def delete_digital_human_video_poster(video):
    poster_path = digital_human_local_path((video or {}).get("poster_url") or (video or {}).get("poster_path") or "")
    if not poster_path:
        video_path = digital_human_local_path((video or {}).get("path") or (video or {}).get("url") or "")
        poster_path = digital_human_poster_path(video_path) if video_path else ""
    try:
        if poster_path and os.path.isfile(poster_path) and os.path.commonpath([os.path.abspath(DIGITAL_HUMAN_POSTER_DIR), os.path.abspath(poster_path)]) == os.path.abspath(DIGITAL_HUMAN_POSTER_DIR):
            os.remove(poster_path)
            return poster_path
    except (OSError, ValueError):
        pass
    return ""

def digital_human_local_path(value):
    if not value:
        return ""
    if isinstance(value, dict):
        value = value.get("url") or value.get("path") or ""
    text = str(value)
    if text.startswith("/assets/") or text.startswith("/output/"):
        return output_file_from_url(text) or ""
    path = os.path.abspath(text)
    roots = [
        os.path.abspath(BASE_DIR),
        os.path.abspath(OUTPUT_INPUT_DIR),
        os.path.abspath(OUTPUT_OUTPUT_DIR),
        os.path.abspath(os.path.join(BASE_DIR, "heygem-win-fix")),
        os.path.abspath(os.path.join(BASE_DIR, "index-tts-2")),
    ]
    try:
        if any(os.path.commonpath([root, path]) == root for root in roots) and os.path.exists(path):
            return path
    except ValueError:
        pass
    return ""

def heygem_runtime_save_dir(config=None):
    config = normalize_digital_human_config(config)
    root = config["heygem"].get("root_dir") or os.path.join(BASE_DIR, "heygem-win-fix", "heygem-win")
    return os.path.abspath(os.path.join(root, "save"))

def is_heygem_runtime_save_path(path, config=None):
    local = digital_human_local_path(path) or (os.path.abspath(path) if path else "")
    return bool(local and is_path_under(heygem_runtime_save_dir(config), local))

def heygem_relative_local_path(config, relative_path):
    if not relative_path:
        return ""
    root = heygem_root_dir(normalize_digital_human_config(config))
    candidate = os.path.abspath(os.path.join(root, str(relative_path).replace("/", os.sep).replace("\\", os.sep)))
    return candidate if is_path_under(root, candidate) else ""

def mark_ignored_video_paths(paths):
    cleaned = []
    for path in paths or []:
        local = digital_human_local_path(path) or (os.path.abspath(path) if path else "")
        if local and os.path.splitext(local)[1].lower() in {".mp4", ".mov", ".webm", ".m4v"}:
            cleaned.append(os.path.abspath(local))
    if not cleaned:
        return
    library = load_digital_human_library_raw()
    ignored = {os.path.abspath(p) for p in library.get("ignored_video_paths") or [] if p}
    before = len(ignored)
    ignored.update(cleaned)
    if len(ignored) != before:
        library["ignored_video_paths"] = sorted(ignored)
        save_digital_human_library(library)

def mark_heygem_runtime_video_ignored(config, heygem_state):
    state = heygem_state or {}
    paths = []
    video_rel = state.get("video_rel")
    if video_rel:
        paths.append(heygem_relative_local_path(config, video_rel))
    video_path = state.get("video_path")
    if video_path and is_heygem_runtime_save_path(video_path, config):
        paths.append(video_path)
    paths = [path for path in paths if path and is_heygem_runtime_save_path(path, config)]
    mark_ignored_video_paths(paths)

def mark_task_heygem_runtime_video_ignored(task_id, config):
    if not task_id:
        return
    with DIGITAL_HUMAN_TASK_LOCK:
        state = dict((DIGITAL_HUMAN_TASKS.get(task_id) or {}).get("heygem") or {})
    mark_heygem_runtime_video_ignored(config, state)

def list_heygem_video_inputs(config=None):
    config = normalize_digital_human_config(config)
    root = config["heygem"].get("root_dir") or os.path.join(BASE_DIR, "heygem-win-fix", "heygem-win")
    candidates = []
    for folder in [os.path.join(root, "视频输出")]:
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and os.path.splitext(name)[1].lower() in {".mp4", ".mov", ".webm", ".m4v"}:
                candidates.append({"name": name, "path": path, "url": digital_human_media_url(path)})
    return candidates

def digital_human_library_default():
    return {
        "version": 1,
        "people": [],
        "voice_meta": {},
        "ignored_video_paths": [],
        "updated_at": time.time(),
    }

def digital_human_stable_id(prefix, value):
    digest = hashlib.sha1(str(value or uuid.uuid4().hex).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{prefix}_{digest}"

def load_digital_human_library_raw():
    data = digital_human_library_default()
    if os.path.exists(DIGITAL_HUMAN_LIBRARY_FILE):
        try:
            with open(DIGITAL_HUMAN_LIBRARY_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                data = merge_dict(data, saved)
        except Exception as e:
            print(f"Load digital human library failed: {e}")
    data["people"] = data.get("people") if isinstance(data.get("people"), list) else []
    data["voice_meta"] = data.get("voice_meta") if isinstance(data.get("voice_meta"), dict) else {}
    data["ignored_video_paths"] = data.get("ignored_video_paths") if isinstance(data.get("ignored_video_paths"), list) else []
    return data

def save_digital_human_library(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    data["updated_at"] = time.time()
    with DIGITAL_HUMAN_LIBRARY_LOCK:
        with open(DIGITAL_HUMAN_LIBRARY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def normalize_library_video(item):
    path = digital_human_local_path((item or {}).get("path") or (item or {}).get("url") or "")
    url = digital_human_media_url(path) if path else ((item or {}).get("url") or "")
    poster_path = digital_human_local_path((item or {}).get("poster_path") or (item or {}).get("poster_url") or "")
    if not poster_path and path:
        candidate_poster = digital_human_poster_path(path)
        if os.path.isfile(candidate_poster):
            poster_path = candidate_poster
    poster_url = digital_human_media_url(poster_path) if poster_path else ""
    name = str((item or {}).get("name") or (os.path.basename(path) if path else "驱动视频")).strip() or "驱动视频"
    video_id = str((item or {}).get("id") or digital_human_stable_id("video", path or url or name)).strip()
    source = (item or {}).get("source") or "library"
    try:
        if path and os.path.commonpath([os.path.abspath(DIGITAL_HUMAN_INPUT_DIR), os.path.abspath(path)]) == os.path.abspath(DIGITAL_HUMAN_INPUT_DIR):
            source = "upload"
    except ValueError:
        pass
    width = int(_digital_human_float((item or {}).get("width"), 0))
    height = int(_digital_human_float((item or {}).get("height"), 0))
    duration = _digital_human_float((item or {}).get("duration"), 0.0)
    aspect_ratio = _digital_human_float((item or {}).get("aspect_ratio"), 0.0)
    if width > 0 and height > 0 and not aspect_ratio:
        aspect_ratio = round(width / height, 6)
    orientation = (item or {}).get("orientation") or ("portrait" if height > width > 0 else ("landscape" if width > height > 0 else ("square" if width > 0 and height > 0 else "")))
    return {
        "id": video_id,
        "name": name,
        "path": path,
        "url": url,
        "preview_url": (item or {}).get("preview_url") or url,
        "poster_path": poster_path,
        "poster_url": poster_url,
        "width": width,
        "height": height,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "orientation": orientation,
        "source": source,
        "created_at": (item or {}).get("created_at") or time.time(),
    }

def normalize_library_person(item):
    person_id = str((item or {}).get("id") or digital_human_stable_id("person", (item or {}).get("name") or uuid.uuid4().hex)).strip()
    name = str((item or {}).get("name") or "未命名人物").strip() or "未命名人物"
    videos = []
    seen = set()
    for raw in (item or {}).get("videos") or []:
        video = normalize_library_video(raw)
        key = video.get("path") or video.get("url") or video.get("id")
        if key and key not in seen:
            seen.add(key)
            videos.append(video)
    current_video_id = str((item or {}).get("current_video_id") or "").strip()
    if current_video_id and not any(v["id"] == current_video_id for v in videos):
        current_video_id = ""
    if not current_video_id and videos:
        current_video_id = videos[0]["id"]
    return {
        "id": person_id,
        "name": name,
        "note": str((item or {}).get("note") or ""),
        "default_voice_name": str((item or {}).get("default_voice_name") or ""),
        "current_video_id": current_video_id,
        "videos": videos,
        "created_at": (item or {}).get("created_at") or time.time(),
        "updated_at": (item or {}).get("updated_at") or time.time(),
    }

def prune_runtime_videos_from_unsorted(people, ignored_paths, config=None):
    cleaned_people = []
    ignored = set(ignored_paths or set())
    for person in people:
        if person.get("id") != "person_unsorted":
            cleaned_people.append(person)
            continue
        kept = []
        removed_current = False
        current_id = person.get("current_video_id") or ""
        for video in person.get("videos", []):
            path = digital_human_local_path(video.get("path") or video.get("url") or "")
            if path and is_heygem_runtime_save_path(path, config):
                ignored.add(os.path.abspath(path))
                if video.get("id") == current_id:
                    removed_current = True
                continue
            kept.append(video)
        person["videos"] = kept
        if removed_current or (person.get("current_video_id") and not any(v.get("id") == person.get("current_video_id") for v in kept)):
            person["current_video_id"] = kept[0]["id"] if kept else ""
        if kept:
            cleaned_people.append(person)
    return cleaned_people, ignored

def merge_library_with_disk_videos(library, config=None):
    people = [normalize_library_person(p) for p in library.get("people") or []]
    ignored_paths = {os.path.abspath(p) for p in library.get("ignored_video_paths") or [] if p}
    people, ignored_paths = prune_runtime_videos_from_unsorted(people, ignored_paths, config)
    known_paths = {os.path.abspath(v["path"]) for p in people for v in p.get("videos", []) if v.get("path")}
    disk_videos = list_heygem_video_inputs(config)
    missing = []
    for item in disk_videos:
        path = digital_human_local_path(item.get("path") or item.get("url"))
        if path and os.path.abspath(path) not in known_paths and os.path.abspath(path) not in ignored_paths:
            video = normalize_library_video({**item, "source": "library"})
            missing.append(video)
            known_paths.add(os.path.abspath(path))
    if missing:
        inbox = next((p for p in people if p.get("id") == "person_unsorted"), None)
        if not inbox:
            inbox = normalize_library_person({"id": "person_unsorted", "name": "未整理人物", "videos": []})
            people.insert(0, inbox)
        inbox["videos"].extend(missing)
        if not inbox.get("current_video_id") and inbox["videos"]:
            inbox["current_video_id"] = inbox["videos"][0]["id"]
        inbox["updated_at"] = time.time()
    library["people"] = people
    library["ignored_video_paths"] = sorted(ignored_paths)
    return library

def load_digital_human_library(config=None, persist=True):
    library = load_digital_human_library_raw()
    before = json.dumps(library, ensure_ascii=False, sort_keys=True)
    library = merge_library_with_disk_videos(library, config)
    library["people"] = [normalize_library_person(p) for p in library.get("people") or []]
    if persist and json.dumps(library, ensure_ascii=False, sort_keys=True) != before:
        save_digital_human_library(library)
    return library

def find_library_person(library, person_id):
    return next((p for p in library.get("people", []) if p.get("id") == person_id), None)

def find_library_video(person, video_id):
    return next((v for v in person.get("videos", []) if v.get("id") == video_id), None)

def enrich_voice_library(config, voices, library=None):
    library = library or load_digital_human_library(config, persist=False)
    voice_meta = library.get("voice_meta") or {}
    enriched = []
    for item in voices or []:
        value = str(item.get("value") or item.get("name") or "")
        meta = voice_meta.get(value) or {}
        enriched.append({
            **item,
            "display_name": meta.get("display_name") or item.get("name") or value,
            "note": meta.get("note") or "",
        })
    return enriched

def digital_human_library_response(config=None, voices=None, tts_status=None):
    config = normalize_digital_human_config(config)
    library = load_digital_human_library(config)
    if voices is None:
        voices = []
    enriched_voices = enrich_voice_library(config, voices, library)
    return {
        "library": library,
        "people": library.get("people", []),
        "voices": enriched_voices,
        "voice_meta": library.get("voice_meta", {}),
        "videos": list_heygem_video_inputs(config),
        "tts_status": tts_status,
    }

def assert_deletable_digital_human_media(path, config=None):
    local = digital_human_local_path(path)
    if not local or not os.path.isfile(local):
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    if not is_digital_human_media_path_allowed(local, config):
        raise HTTPException(status_code=400, detail="媒体路径不允许删除")
    return local

async def digital_human_config():
    config = normalize_digital_human_config()
    voices, tts_status = await list_tts_voices(config, auto_start=False)
    return {
        "config": config,
        "voices": enrich_voice_library(config, voices),
        "videos": list_heygem_video_inputs(config),
        **digital_human_library_response(config, voices, tts_status),
        "tts_status": tts_status,
    }

async def save_digital_human_config_api(payload: DigitalHumanConfigPayload):
    config = normalize_digital_human_config(payload)
    save_digital_human_config(config)
    return {"config": config}

async def digital_human_tts_status(auto_start: bool = False):
    config = normalize_digital_human_config()
    status = await ensure_tts_service(config, wait_seconds=45 if auto_start else 3, auto_start=auto_start)
    return status

async def digital_human_heygem_status():
    config = normalize_digital_human_config()
    return await check_heygem_health(config)

async def digital_human_media(path: str):
    local = digital_human_local_path(path)
    if not local:
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    if not is_digital_human_media_path_allowed(local):
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    if os.path.splitext(local)[1].lower() not in {".wav", ".mp3", ".m4a", ".ogg", ".mp4", ".mov", ".webm", ".m4v", ".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=400, detail="媒体格式不支持")
    return FileResponse(local, media_type=content_type_for_path(local), filename=os.path.basename(local))

async def digital_human_library():
    config = normalize_digital_human_config()
    voices, tts_status = await list_tts_voices(config, auto_start=False)
    return digital_human_library_response(config, voices, tts_status)

async def upsert_digital_human_person(payload: DigitalHumanPersonPayload):
    config = normalize_digital_human_config()
    library = load_digital_human_library(config)
    person_id = payload.id.strip() or digital_human_stable_id("person", f"{payload.name}-{uuid.uuid4().hex}")
    person = find_library_person(library, person_id)
    if person:
        if payload.name.strip():
            person["name"] = payload.name.strip()
        person["note"] = payload.note
        person["default_voice_name"] = payload.default_voice_name.strip()
        if payload.current_video_id.strip() and any(v.get("id") == payload.current_video_id.strip() for v in person.get("videos", [])):
            person["current_video_id"] = payload.current_video_id.strip()
        person["updated_at"] = time.time()
    else:
        person = normalize_library_person({
            "id": person_id,
            "name": payload.name.strip() or "未命名人物",
            "note": payload.note,
            "default_voice_name": payload.default_voice_name.strip(),
            "current_video_id": payload.current_video_id.strip(),
            "videos": [],
        })
        library["people"].append(person)
    save_digital_human_library(library)
    voices, tts_status = await list_tts_voices(config, auto_start=False)
    return digital_human_library_response(config, voices, tts_status)

async def add_digital_human_person_video(person_id: str, payload: DigitalHumanPersonVideoPayload):
    config = normalize_digital_human_config()
    library = load_digital_human_library(config)
    person = find_library_person(library, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="人物不存在")
    raw_video = dict(payload.video or {})
    if payload.name.strip():
        raw_video["name"] = payload.name.strip()
    video = normalize_library_video(raw_video)
    enrich_digital_human_video_metadata(video, config=config)
    if not video.get("path") and not video.get("url"):
        raise HTTPException(status_code=400, detail="视频素材不存在")
    existing = None
    for item in person.get("videos", []):
        if (video.get("path") and item.get("path") == video.get("path")) or item.get("id") == video.get("id"):
            existing = item
            break
    if existing:
        existing.update(video)
        video = existing
    else:
        person.setdefault("videos", []).append(video)
    if video.get("path"):
        ignored = {os.path.abspath(p) for p in library.get("ignored_video_paths") or [] if p}
        ignored.discard(os.path.abspath(video["path"]))
        library["ignored_video_paths"] = sorted(ignored)
    if payload.set_current:
        person["current_video_id"] = video["id"]
    person["updated_at"] = time.time()
    save_digital_human_library(library)
    voices, tts_status = await list_tts_voices(config, auto_start=False)
    return digital_human_library_response(config, voices, tts_status)

async def add_digital_human_person_videos(person_id: str, payload: DigitalHumanPersonVideosPayload):
    config = normalize_digital_human_config()
    library = load_digital_human_library(config)
    person = find_library_person(library, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="人物不存在")
    videos = [dict(item or {}) for item in (payload.videos or [])]
    if not videos:
        raise HTTPException(status_code=400, detail="视频素材不存在")
    last_video = None
    for raw_video in videos:
        video = normalize_library_video(raw_video)
        enrich_digital_human_video_metadata(video, config=config)
        if not video.get("path") and not video.get("url"):
            raise HTTPException(status_code=400, detail="视频素材不存在")
        existing = None
        for item in person.get("videos", []):
            if (video.get("path") and item.get("path") == video.get("path")) or item.get("id") == video.get("id"):
                existing = item
                break
        if existing:
            existing.update(video)
            video = existing
        else:
            person.setdefault("videos", []).append(video)
        if video.get("path"):
            ignored = {os.path.abspath(p) for p in library.get("ignored_video_paths") or [] if p}
            ignored.discard(os.path.abspath(video["path"]))
            library["ignored_video_paths"] = sorted(ignored)
        last_video = video
    if payload.set_current and last_video:
        person["current_video_id"] = last_video["id"]
    person["updated_at"] = time.time()
    save_digital_human_library(library)
    voices, tts_status = await list_tts_voices(config, auto_start=False)
    return digital_human_library_response(config, voices, tts_status)

async def ensure_digital_human_person_video_poster(person_id: str, video_id: str):
    config = normalize_digital_human_config()
    library = load_digital_human_library(config)
    person = find_library_person(library, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="人物不存在")
    video = find_library_video(person, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    poster_url = ensure_digital_human_video_poster(video, config=config)
    enrich_digital_human_video_metadata(video, config=config)
    if poster_url:
        video["poster_url"] = poster_url
        video["poster_path"] = digital_human_local_path(poster_url)
    if poster_url or video.get("width") or video.get("height"):
        person["updated_at"] = time.time()
        save_digital_human_library(library)
    return {"ok": bool(poster_url), "person_id": person_id, "video_id": video_id, "poster_url": poster_url, "video": video}

async def delete_digital_human_person(person_id: str, delete_files: bool = False):
    config = normalize_digital_human_config()
    library = load_digital_human_library(config)
    person = find_library_person(library, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="人物不存在")
    deleted_paths = []
    if delete_files:
        for video in person.get("videos", []):
            path = video.get("path")
            if not path:
                continue
            local = assert_deletable_digital_human_media(path, config)
            try:
                os.remove(local)
                deleted_paths.append(local)
                poster_path = delete_digital_human_video_poster(video)
                if poster_path:
                    deleted_paths.append(poster_path)
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"删除视频失败：{exc}") from exc
    else:
        ignored = {os.path.abspath(p) for p in library.get("ignored_video_paths") or [] if p}
        for video in person.get("videos", []):
            if video.get("path"):
                ignored.add(os.path.abspath(video["path"]))
        library["ignored_video_paths"] = sorted(ignored)
    library["people"] = [p for p in library.get("people", []) if p.get("id") != person_id]
    save_digital_human_library(library)
    voices, tts_status = await list_tts_voices(config, auto_start=False)
    data = digital_human_library_response(config, voices, tts_status)
    data["deleted_paths"] = deleted_paths
    return data

async def delete_digital_human_person_video(person_id: str, video_id: str, delete_file: bool = False):
    config = normalize_digital_human_config()
    library = load_digital_human_library(config)
    person = find_library_person(library, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="人物不存在")
    video = find_library_video(person, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    deleted_paths = []
    if delete_file and video.get("path"):
        local = assert_deletable_digital_human_media(video.get("path"), config)
        try:
            os.remove(local)
            deleted_paths.append(local)
            poster_path = delete_digital_human_video_poster(video)
            if poster_path:
                deleted_paths.append(poster_path)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"删除视频失败：{exc}") from exc
    elif video.get("path"):
        ignored = {os.path.abspath(p) for p in library.get("ignored_video_paths") or [] if p}
        ignored.add(os.path.abspath(video["path"]))
        library["ignored_video_paths"] = sorted(ignored)
    person["videos"] = [v for v in person.get("videos", []) if v.get("id") != video_id]
    if person.get("current_video_id") == video_id:
        person["current_video_id"] = person["videos"][0]["id"] if person["videos"] else ""
    person["updated_at"] = time.time()
    save_digital_human_library(library)
    voices, tts_status = await list_tts_voices(config, auto_start=False)
    data = digital_human_library_response(config, voices, tts_status)
    data["deleted_paths"] = deleted_paths
    return data

async def patch_digital_human_voice_meta(voice_name: str, payload: DigitalHumanVoiceMetaPayload):
    config = normalize_digital_human_config()
    name = urllib.parse.unquote(str(voice_name or "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="音色名称不能为空")
    library = load_digital_human_library(config)
    meta = library.setdefault("voice_meta", {}).setdefault(name, {})
    meta["display_name"] = payload.display_name.strip()
    meta["note"] = payload.note.strip()
    save_digital_human_library(library)
    voices, tts_status = await list_tts_voices(config, auto_start=False)
    return digital_human_library_response(config, voices, tts_status)

async def delete_digital_human_voice(voice_name: str):
    config = normalize_digital_human_config()
    name = urllib.parse.unquote(str(voice_name or "")).strip()
    path = tts_voice_file_for_name(config, name)
    if not path:
        raise HTTPException(status_code=404, detail="音色文件不存在或不可删除")
    library = tts_voice_library_dir(config)
    try:
        if path and os.path.commonpath([library, os.path.abspath(path)]) != library:
            raise HTTPException(status_code=400, detail="音色路径不允许删除")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="音色路径不允许删除") from exc
    try:
        deleted_paths = []
        if path and os.path.isfile(path):
            os.remove(path)
            deleted_paths.append(path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"音色删除失败：{exc}") from exc
    voices, tts_status = await list_tts_voices(config, auto_start=False)
    return {"ok": True, "deleted": name, "path": path, "deleted_paths": deleted_paths, "voices": voices, "tts_status": tts_status}

async def upload_digital_human_asset(files: List[UploadFile] = File(...), kind: str = "asset", save_voice: bool = False, voice_name: str = "", overwrite: bool = False):
    config = normalize_digital_human_config()
    uploaded = []
    allowed = {
        "voice": {".wav", ".mp3", ".m4a", ".ogg"},
        "video": {".mp4", ".mov", ".webm", ".m4v"},
        "asset": {".wav", ".mp3", ".m4a", ".ogg", ".mp4", ".mov", ".webm", ".m4v"},
    }.get(kind, {".wav", ".mp3", ".m4a", ".ogg", ".mp4", ".mov", ".webm", ".m4v"})
    for file in files:
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in allowed:
            raise HTTPException(status_code=400, detail=f"文件格式不支持: {file.filename}")
        prefix = "voice" if ext in {".wav", ".mp3", ".m4a", ".ogg"} else "video"
        filename = sanitize_output_filename(file.filename, f"digital_{prefix}", ext)
        path = os.path.join(DIGITAL_HUMAN_INPUT_DIR, filename)
        size = await save_upload_limited(file, path)
        if not size:
            continue
        item = {
            "url": digital_human_output_url(path),
            "path": path,
            "name": file.filename or filename,
            "kind": prefix,
        }
        if prefix == "video":
            item["preview_url"] = item["url"]
            enrich_digital_human_video_metadata(item, config=config)
            poster_url = ensure_digital_human_video_poster(item, config=config)
            if poster_url:
                item["poster_url"] = poster_url
                item["poster_path"] = digital_human_local_path(poster_url)
        if prefix == "voice":
            item["preview_url"] = item["url"]
        if prefix == "voice" and (save_voice or kind == "voice"):
            requested_name = sanitize_tts_voice_name(voice_name or os.path.splitext(file.filename or filename)[0])
            existing_voice_path = tts_voice_file_for_name(config, requested_name)
            existing_embedding_path = tts_voice_embedding_for_name(config, requested_name)
            if (existing_voice_path or existing_embedding_path) and not overwrite:
                raise HTTPException(status_code=409, detail={
                    "message": f"音色已存在：{requested_name}",
                    "voice_name": requested_name,
                    "path": existing_voice_path or existing_embedding_path,
                    "preview_url": digital_human_media_url(existing_voice_path),
                })
            try:
                item["tts_voice"] = await run_with_digital_human_resource(
                    "tts",
                    f"voice:{requested_name}",
                    "",
                    "save-voice",
                    lambda: save_tts_voice(path, requested_name, config),
                )
                preview_path = save_tts_voice_preview_audio(config, path, requested_name)
                item["voice_name"] = requested_name
                voices, tts_status = await list_tts_voices(config, auto_start=False)
                item["saved_voice"] = enrich_tts_voice_item(config, {"name": requested_name, "value": requested_name})
                item["saved_voice"]["path"] = item["saved_voice"].get("path") or preview_path
                item["saved_voice"]["preview_url"] = item["saved_voice"].get("preview_url") or digital_human_media_url(preview_path)
                item["saved_voice"]["deletable"] = True
                item["voices"] = voices
                item["tts_status"] = tts_status
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"TTS voice save failed: {exc}") from exc
        uploaded.append(item)
    return {"files": uploaded}

async def digital_human_tts(payload: DigitalHumanTTSRequest):
    config = normalize_digital_human_config(payload.config)
    voice_path = digital_human_local_path(payload.voice_url) or digital_human_local_path(payload.voice_path)
    result = await run_with_digital_human_resource(
        "tts",
        "preview-tts",
        "",
        "tts-submit",
        lambda: generate_digital_human_tts(payload.text, voice_path, config, payload.voice_name, payload.tts_options),
    )
    return {"audio": result}

def digital_human_error_message(detail):
    if isinstance(detail, dict):
        for key in ("message", "detail", "error", "last_error", "reason"):
            value = detail.get(key)
            if value:
                return digital_human_error_message(value)
        return safe_upstream_summary(detail)
    return str(detail or "")

def canonical_digital_human_failure_type(failure_type):
    aliases = {
        "task_not_found": "heygem_task_not_found",
        "stall_at_20": "heygem_stall_at_20",
        "query_timeout": "heygem_query_timeout",
        "submit_failed": "heygem_submit_failed",
    }
    return aliases.get(str(failure_type or "").strip(), str(failure_type or "").strip())

def classify_digital_human_failure(detail):
    if isinstance(detail, dict) and detail.get("failure_type"):
        return canonical_digital_human_failure_type(detail.get("failure_type"))
    text = digital_human_error_message(detail).lower()
    if "task_not_found" in text or "task not found" in text or "任务不存在" in text or "10004" in text:
        return "heygem_task_not_found"
    if "queue full" in text or "队列满" in text or "下游队列异常" in text or "heygem_queue_blocked" in text:
        return "heygem_queue_blocked"
    if "heygem_gpu_conflict" in text or "gpu" in text and "conflict" in text or "抢显存" in text or "显存" in text and "残留" in text:
        return "heygem_gpu_conflict"
    if "tts_output_write_failed" in text or "invalid argument" in text or "errno 22" in text:
        return "tts_output_write_failed"
    if "stall_at_20" in text or "20%" in text or "no progress" in text or "blocked" in text:
        return "heygem_stall_at_20"
    if "tts" in text and ("timeout" in text or "timed out" in text or "超时" in text):
        return "tts_timeout"
    if "query_timeout" in text or "timeout" in text or "timed out" in text:
        return "heygem_query_timeout"
    if "submit_failed" in text or "submit failed" in text:
        return "heygem_submit_failed"
    if "heygem service is not ready" in text or "heygem_not_ready" in text:
        return "heygem_not_ready"
    if "video" in text and ("missing" in text or "return" in text):
        return "output_missing"
    return "unknown"

def normalize_digital_human_failure(detail):
    if isinstance(detail, dict):
        data = dict(detail)
    else:
        data = {"message": digital_human_error_message(detail)}
    failure_type = classify_digital_human_failure(data)
    data["failure_type"] = failure_type
    data.setdefault("message", digital_human_error_message(detail) or failure_type)
    data["retryable"] = bool(data.get("retryable", failure_type in DIGITAL_HUMAN_RETRYABLE_FAILURE_TYPES))
    return data

def normalize_tts_generation_failure(detail):
    data = normalize_digital_human_failure(detail)
    message = digital_human_error_message(data)
    lower = message.lower()
    if data.get("failure_type") in {"unknown", "heygem_query_timeout"} and (
        "timeout" in lower or "timed out" in lower or "超时" in lower
    ):
        data["failure_type"] = "tts_timeout"
        data["message"] = "TTS generation exceeded the local wait window; the TTS service may still be finishing the audio. Shorten the script or retry after TTS becomes idle."
    elif data.get("failure_type") == "unknown":
        data["failure_type"] = "tts_failed"
        data["message"] = message or "TTS task failed."
    data["retryable"] = data.get("failure_type") == "tts_timeout"
    return data

def make_digital_human_task_payload(task_id, payload_data, request_base_url):
    text_preview = re.sub(r"\s+", " ", str(payload_data.get("text") or "")).strip()
    if len(text_preview) > 80:
        text_preview = text_preview[:80] + "..."
    return {
        "task_id": task_id,
        "status": "queued",
        "stage": "queued",
        "queue_position": len(DIGITAL_HUMAN_QUEUE) + 1,
        "script_preview": text_preview,
        "voice_name": payload_data.get("voice_name") or "",
        "video_name": os.path.basename(payload_data.get("video_path") or payload_data.get("video_url") or "") or "",
        "retry_count": 0,
        "_payload_data": dict(payload_data or {}),
        "_request_base_url": str(request_base_url or "").rstrip("/"),
        "created_at": time.time(),
        "updated_at": time.time(),
    }

async def run_digital_human_task(task_id, payload_data, request_base_url):
    with DIGITAL_HUMAN_TASK_LOCK:
        DIGITAL_HUMAN_TASKS[task_id].update({"status": "running", "stage": "tts", "updated_at": time.time()})
    digital_human_log(f"task {task_id} started")
    try:
        payload = DigitalHumanGenerateRequest(**payload_data)
        config = normalize_digital_human_config(payload.config)
        if not config.get("public_base_url") and request_base_url:
            config["public_base_url"] = request_base_url.rstrip("/")
        voice_path = digital_human_local_path(payload.voice_url) or digital_human_local_path(payload.voice_path)
        if payload.audio_url:
            audio = {"url": payload.audio_url, "path": digital_human_local_path(payload.audio_url), "name": os.path.basename(payload.audio_url)}
            digital_human_log(f"task {task_id} using provided audio {audio.get('name') or audio.get('url')}")
        else:
            with DIGITAL_HUMAN_TASK_LOCK:
                DIGITAL_HUMAN_TASKS[task_id].update({"stage": "tts-submit", "updated_at": time.time()})
            audio = await run_with_digital_human_resource(
                "tts",
                task_id,
                "waiting-tts-light",
                "tts-submit",
                lambda: generate_digital_human_tts(payload.text, voice_path, config, payload.voice_name, payload.tts_options),
            )
            digital_human_log(f"task {task_id} audio ready {audio.get('name')}")
        with DIGITAL_HUMAN_TASK_LOCK:
            DIGITAL_HUMAN_TASKS[task_id].update({"stage": "heygem-submit", "audio": audio, "updated_at": time.time()})
        video_path = digital_human_local_path(payload.video_url) or digital_human_local_path(payload.video_path)
        video_url = payload.video_url or (digital_human_output_url(video_path) if video_path else "")
        if not video_url:
            videos = list_heygem_video_inputs(config)
            if videos:
                video_path = videos[0]["path"]
                video_url = digital_human_media_url(video_path)
        if not video_url and not video_path:
            raise HTTPException(status_code=400, detail="请先选择或上传驱动视频")
        audio_path = digital_human_local_path(audio.get("url")) or audio.get("path") or ""
        video_path = digital_human_local_path(video_url) or video_path or ""
        with DIGITAL_HUMAN_TASK_LOCK:
            DIGITAL_HUMAN_TASKS[task_id].update({"stage": "heygem-generate", "updated_at": time.time()})
        digital_human_log(f"task {task_id} HeyGem start audio={os.path.basename(audio_path)} video={os.path.basename(video_path)}")
        try:
            result = await run_with_digital_human_resource(
                "heygem",
                task_id,
                "waiting-heygem-light",
                "heygem-generate",
                lambda: generate_heygem_video_monitored(audio_path, video_path, config, task_id, request_base_url),
            )
        finally:
            mark_task_heygem_runtime_video_ignored(task_id, config)
        digital_human_log(f"task {task_id} HeyGem done video={result.get('video', {}).get('name') or result.get('video', {}).get('url') or '-'}")
        with DIGITAL_HUMAN_TASK_LOCK:
            DIGITAL_HUMAN_TASKS[task_id].update({
                "status": "succeeded",
                "stage": "done",
                "video": result.get("video"),
                "raw": result.get("raw"),
                "heygem_result": {
                    "code": result.get("code"),
                    "status": result.get("status"),
                    "submit": result.get("submit"),
                },
                "updated_at": time.time(),
            })
    except HTTPException as exc:
        digital_human_log(f"task {task_id} failed: {exc.detail}")
        with DIGITAL_HUMAN_TASK_LOCK:
            DIGITAL_HUMAN_TASKS[task_id].update({
                "status": "failed",
                "stage": "failed",
                "error": exc.detail,
                "updated_at": time.time(),
            })
    except Exception as exc:
        digital_human_log(f"task {task_id} failed: {exc}")
        with DIGITAL_HUMAN_TASK_LOCK:
            DIGITAL_HUMAN_TASKS[task_id].update({
                "status": "failed",
                "stage": "failed",
                "error": str(exc),
                "updated_at": time.time(),
            })

async def run_digital_human_task_v2(task_id, payload_data, request_base_url):
    with DIGITAL_HUMAN_TASK_LOCK:
        DIGITAL_HUMAN_TASKS[task_id].update({"status": "running", "stage": "tts", "updated_at": time.time()})
    digital_human_log(f"task {task_id} started")
    try:
        payload = DigitalHumanGenerateRequest(**payload_data)
        config = normalize_digital_human_config(payload.config)
        if not config.get("public_base_url") and request_base_url:
            config["public_base_url"] = request_base_url.rstrip("/")
        initial_conflicts = heygem_app_local_processes(config)
        if initial_conflicts:
            raise HTTPException(
                status_code=409,
                detail=heygem_gpu_conflict_detail(
                    "检测到本项目残留 HeyGem app_local.py 进程，可能正在占用显存或内部队列；请先停止残留 HeyGem 后再生成。",
                    conflicts=initial_conflicts,
                    retryable=True,
                ),
            )

        voice_path = digital_human_local_path(payload.voice_url) or digital_human_local_path(payload.voice_path)
        if payload.audio_url:
            audio = {"url": payload.audio_url, "path": digital_human_local_path(payload.audio_url), "name": os.path.basename(payload.audio_url)}
            digital_human_log(f"task {task_id} using provided audio {audio.get('name') or audio.get('url')}")
        else:
            with DIGITAL_HUMAN_TASK_LOCK:
                DIGITAL_HUMAN_TASKS[task_id].update({"stage": "tts-submit", "updated_at": time.time()})
            try:
                audio = await run_with_digital_human_resource(
                    "tts",
                    task_id,
                    "waiting-tts-light",
                    "tts-submit",
                    lambda: generate_digital_human_tts(payload.text, voice_path, config, payload.voice_name, payload.tts_options),
                )
            except HTTPException as exc:
                raise HTTPException(status_code=exc.status_code, detail=normalize_tts_generation_failure(exc.detail)) from exc
            except Exception as exc:
                raise HTTPException(status_code=502, detail=normalize_tts_generation_failure(str(exc))) from exc
            digital_human_log(f"task {task_id} audio ready {audio.get('name')}")

        video_path = digital_human_local_path(payload.video_url) or digital_human_local_path(payload.video_path)
        video_url = payload.video_url or (digital_human_output_url(video_path) if video_path else "")
        if not video_url:
            videos = list_heygem_video_inputs(config)
            if videos:
                video_path = videos[0]["path"]
                video_url = digital_human_media_url(video_path)
        if not video_url and not video_path:
            raise HTTPException(status_code=400, detail="Please select or upload a drive video first.")

        audio_path = digital_human_local_path(audio.get("url")) or audio.get("path") or ""
        video_path = digital_human_local_path(video_url) or video_path or ""
        handoff = await ensure_heygem_gpu_lane(config, task_id)
        with DIGITAL_HUMAN_TASK_LOCK:
            DIGITAL_HUMAN_TASKS[task_id].update({
                "stage": "heygem-submit",
                "audio": audio,
                "debug": {
                    "audio_path": audio_path,
                    "video_path": video_path,
                    "gpu_handoff": handoff,
                },
                "updated_at": time.time(),
            })

        last_failure = None
        max_attempts = 2
        for attempt in range(max_attempts):
            if attempt > 0:
                await asyncio.sleep(6)
            with DIGITAL_HUMAN_TASK_LOCK:
                DIGITAL_HUMAN_TASKS[task_id].update({
                    "stage": "heygem-retry" if attempt else "heygem-generate",
                    "retry_count": attempt,
                    "heygem_attempt": attempt + 1,
                    "failure_type": "",
                    "updated_at": time.time(),
                })
            digital_human_log(f"task {task_id} HeyGem attempt {attempt + 1} audio={os.path.basename(audio_path)} video={os.path.basename(video_path)}")
            try:
                result = await run_with_digital_human_resource(
                    "heygem",
                    task_id,
                    "waiting-heygem-light",
                    "heygem-retry" if attempt else "heygem-generate",
                    lambda: generate_heygem_video_monitored(
                        audio_path,
                        video_path,
                        config,
                        task_id,
                        request_base_url,
                        f"{task_id}-{attempt + 1}",
                    ),
                )
            except HTTPException as exc:
                failure = normalize_digital_human_failure(exc.detail)
                failure_type = failure.get("failure_type") or "unknown"
                last_failure = failure
                with DIGITAL_HUMAN_TASK_LOCK:
                    DIGITAL_HUMAN_TASKS[task_id].update({
                        "failure_type": failure_type,
                        "error": failure,
                        "retryable": bool(failure.get("retryable")),
                        "updated_at": time.time(),
                    })
                if failure_type in HEYGEM_HARD_STOP_FAILURE_TYPES:
                    pause_digital_human_queue("HeyGem 内部队列阻塞或 GPU 资源冲突，队列已暂停。请清理残留进程/重启 HeyGem 后再继续。")
                    raise HTTPException(status_code=exc.status_code, detail=failure) from exc
                if attempt == 0 and failure_type in DIGITAL_HUMAN_RETRYABLE_FAILURE_TYPES:
                    digital_human_log(f"task {task_id} HeyGem retry after {failure_type}")
                    continue
                raise HTTPException(status_code=exc.status_code, detail=failure) from exc
            finally:
                mark_task_heygem_runtime_video_ignored(task_id, config)

            digital_human_log(f"task {task_id} HeyGem done video={result.get('video', {}).get('name') or result.get('video', {}).get('url') or '-'}")
            with DIGITAL_HUMAN_TASK_LOCK:
                DIGITAL_HUMAN_TASKS[task_id].update({
                    "status": "succeeded",
                    "stage": "done",
                    "video": result.get("video"),
                    "raw": result.get("raw"),
                    "heygem_result": {
                        "code": result.get("code"),
                        "status": result.get("status"),
                        "submit": result.get("submit"),
                        "attempts": attempt + 1,
                    },
                    "failure_type": "",
                    "retryable": False,
                    "updated_at": time.time(),
                })
            return {"status": "succeeded"}

        raise HTTPException(status_code=504, detail=last_failure or {"message": "HeyGem failed.", "failure_type": "unknown"})
    except HTTPException as exc:
        failure = normalize_digital_human_failure(exc.detail)
        failure_type = failure.get("failure_type") or "unknown"
        if failure_type in HEYGEM_HARD_STOP_FAILURE_TYPES:
            pause_digital_human_queue("HeyGem 内部队列阻塞或 GPU 资源冲突，队列已暂停。请清理残留进程/重启 HeyGem 后再继续。")
        digital_human_log(f"task {task_id} failed: {digital_human_error_message(failure)}")
        with DIGITAL_HUMAN_TASK_LOCK:
            DIGITAL_HUMAN_TASKS[task_id].update({
                "status": "failed",
                "stage": "failed",
                "error": failure,
                "failure_type": failure_type,
                "retryable": bool(failure.get("retryable")),
                "updated_at": time.time(),
            })
        return {"status": "failed", "failure_type": failure_type}
    except Exception as exc:
        failure = normalize_digital_human_failure(str(exc))
        digital_human_log(f"task {task_id} failed: {exc}")
        with DIGITAL_HUMAN_TASK_LOCK:
            DIGITAL_HUMAN_TASKS[task_id].update({
                "status": "failed",
                "stage": "failed",
                "error": failure,
                "failure_type": failure.get("failure_type") or "unknown",
                "retryable": bool(failure.get("retryable")),
                "updated_at": time.time(),
            })
        return {"status": "failed", "failure_type": failure.get("failure_type") or "unknown"}

def digital_human_task_public(task):
    public = {}
    for key, value in (task or {}).items():
        if str(key).startswith("_"):
            continue
        if key == "raw":
            continue
        public[key] = value
    return public

def update_digital_human_queue_positions_locked():
    active_queue = []
    position = 1
    for task_id in list(DIGITAL_HUMAN_QUEUE):
        task = DIGITAL_HUMAN_TASKS.get(task_id)
        if not task or task.get("status") != "queued":
            continue
        task["queue_position"] = position
        active_queue.append(task_id)
        position += 1
    DIGITAL_HUMAN_QUEUE[:] = active_queue
    for task in DIGITAL_HUMAN_TASKS.values():
        if task.get("status") != "queued":
            task["queue_position"] = 0

def digital_human_queue_snapshot_locked(limit=DIGITAL_HUMAN_RECENT_LIMIT):
    update_digital_human_queue_positions_locked()
    tasks = [digital_human_task_public(task) for task in DIGITAL_HUMAN_TASKS.values()]
    active = [task for task in tasks if task.get("status") in {"queued", "running", "pending"}]
    recent = [task for task in tasks if task.get("status") in DIGITAL_HUMAN_TERMINAL_STATUSES]
    active.sort(key=lambda item: (0 if item.get("status") == "running" else 1, item.get("queue_position") or 0, item.get("created_at") or 0))
    recent.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or 0, reverse=True)
    visible = active + recent[:max(0, int(limit or DIGITAL_HUMAN_RECENT_LIMIT))]
    return {
        "tasks": visible,
        "queued": [task for task in active if task.get("status") in {"queued", "pending"}],
        "running": next((task for task in active if task.get("status") == "running"), None),
        "recent": recent[:max(0, int(limit or DIGITAL_HUMAN_RECENT_LIMIT))],
        "queue": {**dict(DIGITAL_HUMAN_QUEUE_STATE), "resource": digital_human_resource_snapshot()},
    }

def late_heygem_recovery_candidates(limit=5):
    now = time.time()
    candidates = []
    with DIGITAL_HUMAN_TASK_LOCK:
        for task_id, task in list(DIGITAL_HUMAN_TASKS.items()):
            if len(candidates) >= max(1, int(limit or 5)):
                break
            if task.get("status") != "failed":
                continue
            if task.get("failure_type") not in {"heygem_stall_at_20", "heygem_query_timeout"}:
                continue
            if now - float(task.get("_late_heygem_checked_at") or 0) < 10:
                continue
            heygem = dict(task.get("heygem") or {})
            error = task.get("error") if isinstance(task.get("error"), dict) else {}
            code = str(heygem.get("code") or error.get("heygem_code") or "").strip()
            if not code:
                continue
            task["_late_heygem_checked_at"] = now
            candidates.append((task_id, code, dict(task.get("_payload_data") or {}), heygem))
    return candidates

def recover_late_heygem_results(limit=5):
    service = _heygem_service()
    recovered = []
    for task_id, code, payload_data, heygem_state in late_heygem_recovery_candidates(limit):
        try:
            config = normalize_digital_human_config(payload_data.get("config") or {})
            query_url = heygem_state.get("query_url") or safe_join_url(
                service.heygem_api_base_url(config),
                (config.get("heygem") or {}).get("query_path") or "/easy/query",
            )
            with service.local_requests_session() as session:
                response = session.get(query_url, params={"code": code}, timeout=8)
                response.raise_for_status()
                try:
                    raw = response.json()
                except Exception:
                    raw = {"text": response.text[:500]}
            urls = service.heygem_result_urls(raw)
            if not urls:
                continue
            mark_heygem_runtime_video_ignored(config, heygem_state)
            video = service.save_heygem_video_result_sync(urls[0], config)
            with DIGITAL_HUMAN_TASK_LOCK:
                task = DIGITAL_HUMAN_TASKS.get(task_id)
                if task and task.get("status") == "failed":
                    task.update({
                        "status": "succeeded",
                        "stage": "done",
                        "video": video,
                        "raw": service.safe_upstream_summary(raw),
                        "heygem_result": {
                            "code": code,
                            "status": service.heygem_status(raw) or "DONE",
                            "recovered_late": True,
                        },
                        "failure_type": "",
                        "retryable": False,
                        "error": "",
                        "updated_at": time.time(),
                    })
                    DIGITAL_HUMAN_QUEUE_STATE["consecutive_infra_failures"] = 0
                    recovered.append(task_id)
                    digital_human_log(f"task {task_id} recovered late HeyGem result code={code}")
        except Exception as exc:
            digital_human_log(f"task {task_id} late HeyGem recovery check failed: {exc}")
    return recovered

def ensure_digital_human_queue_worker():
    global DIGITAL_HUMAN_QUEUE_WORKER
    if DIGITAL_HUMAN_QUEUE_WORKER and not DIGITAL_HUMAN_QUEUE_WORKER.done():
        return
    DIGITAL_HUMAN_QUEUE_WORKER = asyncio.create_task(digital_human_queue_worker())

async def digital_human_queue_worker():
    global DIGITAL_HUMAN_QUEUE_WORKER
    try:
        while True:
            with DIGITAL_HUMAN_TASK_LOCK:
                update_digital_human_queue_positions_locked()
                if DIGITAL_HUMAN_QUEUE_STATE.get("paused"):
                    DIGITAL_HUMAN_QUEUE_WORKER = None
                    return
                task_id = ""
                for candidate in list(DIGITAL_HUMAN_QUEUE):
                    task = DIGITAL_HUMAN_TASKS.get(candidate)
                    if task and task.get("status") == "queued":
                        task_id = candidate
                        break
                if not task_id:
                    DIGITAL_HUMAN_QUEUE_WORKER = None
                    return
                DIGITAL_HUMAN_QUEUE.remove(task_id)
                task = DIGITAL_HUMAN_TASKS[task_id]
                task["status"] = "running"
                task["stage"] = "tts"
                task["queue_position"] = 0
                task["started_at"] = time.time()
                task["updated_at"] = time.time()
                payload_data = dict(task.get("_payload_data") or {})
                request_base_url = str(task.get("_request_base_url") or "")
                update_digital_human_queue_positions_locked()
            result = await run_digital_human_task_v2(task_id, payload_data, request_base_url)
            with DIGITAL_HUMAN_TASK_LOCK:
                failure_type = str((result or {}).get("failure_type") or "")
                if (result or {}).get("status") == "failed" and failure_type in DIGITAL_HUMAN_INFRA_FAILURE_TYPES:
                    DIGITAL_HUMAN_QUEUE_STATE["consecutive_infra_failures"] = int(DIGITAL_HUMAN_QUEUE_STATE.get("consecutive_infra_failures") or 0) + 1
                else:
                    DIGITAL_HUMAN_QUEUE_STATE["consecutive_infra_failures"] = 0
                if DIGITAL_HUMAN_QUEUE_STATE["consecutive_infra_failures"] >= 2 and any(
                    (DIGITAL_HUMAN_TASKS.get(candidate) or {}).get("status") == "queued"
                    for candidate in DIGITAL_HUMAN_QUEUE
                ):
                    DIGITAL_HUMAN_QUEUE_STATE.update({
                        "paused": True,
                        "pause_reason": f"HeyGem infrastructure failed twice in a row ({failure_type}).",
                        "paused_at": time.time(),
                    })
                    update_digital_human_queue_positions_locked()
                    return
    finally:
        current = asyncio.current_task()
        with DIGITAL_HUMAN_TASK_LOCK:
            if DIGITAL_HUMAN_QUEUE_WORKER is current:
                DIGITAL_HUMAN_QUEUE_WORKER = None
        if DIGITAL_HUMAN_QUEUE and not DIGITAL_HUMAN_QUEUE_STATE.get("paused"):
            try:
                ensure_digital_human_queue_worker()
            except RuntimeError:
                pass

async def digital_human_generate(payload: DigitalHumanGenerateRequest, request: Request):
    task_id = payload.code or f"dh_{uuid.uuid4().hex[:12]}"
    payload_data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    with DIGITAL_HUMAN_TASK_LOCK:
        DIGITAL_HUMAN_TASKS[task_id] = make_digital_human_task_payload(task_id, payload_data, str(request.base_url).rstrip("/"))
        DIGITAL_HUMAN_QUEUE.append(task_id)
        update_digital_human_queue_positions_locked()
        task = digital_human_task_public(DIGITAL_HUMAN_TASKS.get(task_id))
        paused = bool(DIGITAL_HUMAN_QUEUE_STATE.get("paused"))
    if not paused:
        ensure_digital_human_queue_worker()
    return {"task_id": task_id, "status": task.get("status", "queued"), "queue_position": task.get("queue_position", 0)}

async def digital_human_task(task_id: str):
    with DIGITAL_HUMAN_TASK_LOCK:
        task = dict(DIGITAL_HUMAN_TASKS.get(task_id) or {})
    if not task:
        raise HTTPException(status_code=404, detail="数字人任务不存在")
    return digital_human_task_public(task)

async def digital_human_tasks(limit: int = DIGITAL_HUMAN_RECENT_LIMIT):
    recovered = await asyncio.to_thread(recover_late_heygem_results, 5)
    with DIGITAL_HUMAN_TASK_LOCK:
        if recovered and DIGITAL_HUMAN_QUEUE_STATE.get("paused"):
            DIGITAL_HUMAN_QUEUE_STATE.update({
                "paused": False,
                "pause_reason": "",
                "paused_at": 0,
                "consecutive_infra_failures": 0,
            })
        snapshot = digital_human_queue_snapshot_locked(limit)
        should_start = bool(recovered) and any((DIGITAL_HUMAN_TASKS.get(candidate) or {}).get("status") == "queued" for candidate in DIGITAL_HUMAN_QUEUE)
    if should_start:
        ensure_digital_human_queue_worker()
    return snapshot

async def cancel_digital_human_task(task_id: str):
    with DIGITAL_HUMAN_TASK_LOCK:
        task = DIGITAL_HUMAN_TASKS.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="数字人任务不存在")
        if task.get("status") != "queued":
            raise HTTPException(status_code=400, detail="仅可取消尚未开始的排队任务")
        task["status"] = "canceled"
        task["stage"] = "canceled"
        task["queue_position"] = 0
        task["canceled_at"] = time.time()
        task["updated_at"] = time.time()
        if task_id in DIGITAL_HUMAN_QUEUE:
            DIGITAL_HUMAN_QUEUE.remove(task_id)
        update_digital_human_queue_positions_locked()
        return digital_human_task_public(task)

async def retry_digital_human_task(task_id: str):
    with DIGITAL_HUMAN_TASK_LOCK:
        task = DIGITAL_HUMAN_TASKS.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Digital human task does not exist.")
        if task.get("status") not in {"failed", "canceled"}:
            raise HTTPException(status_code=400, detail="Only failed or canceled tasks can be retried.")
        payload_data = dict(task.get("_payload_data") or {})
        if not payload_data:
            raise HTTPException(status_code=400, detail="Task payload is missing and cannot be retried.")
        retry_id = f"{task_id}_retry_{uuid.uuid4().hex[:8]}"
        DIGITAL_HUMAN_TASKS[retry_id] = make_digital_human_task_payload(
            retry_id,
            payload_data,
            task.get("_request_base_url") or "",
        )
        DIGITAL_HUMAN_TASKS[retry_id]["source_task_id"] = task_id
        DIGITAL_HUMAN_QUEUE.append(retry_id)
        update_digital_human_queue_positions_locked()
        public = digital_human_task_public(DIGITAL_HUMAN_TASKS[retry_id])
        paused = bool(DIGITAL_HUMAN_QUEUE_STATE.get("paused"))
    if not paused:
        ensure_digital_human_queue_worker()
    return public

async def continue_digital_human_queue():
    with DIGITAL_HUMAN_TASK_LOCK:
        DIGITAL_HUMAN_QUEUE_STATE.update({
            "paused": False,
            "pause_reason": "",
            "paused_at": 0,
            "consecutive_infra_failures": 0,
        })
        update_digital_human_queue_positions_locked()
        snapshot = digital_human_queue_snapshot_locked(DIGITAL_HUMAN_RECENT_LIMIT)
        should_start = any((DIGITAL_HUMAN_TASKS.get(candidate) or {}).get("status") == "queued" for candidate in DIGITAL_HUMAN_QUEUE)
    if should_start:
        ensure_digital_human_queue_worker()
    return snapshot


def _open_local_path(target, select_file=False):
    path = os.path.abspath(str(target or ""))
    if not path or not os.path.exists(path):
        return False
    try:
        if os.name == "nt":
            if select_file and os.path.isfile(path):
                subprocess.Popen(["explorer.exe", f"/select,{path}"])
            else:
                os.startfile(path)  # type: ignore[attr-defined]
            return True
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, path])
        return True
    except Exception:
        return False


async def open_digital_human_task_output(task_id: str):
    with DIGITAL_HUMAN_TASK_LOCK:
        task = dict(DIGITAL_HUMAN_TASKS.get(task_id) or {})
    if not task:
        raise HTTPException(status_code=404, detail="Digital human task does not exist.")
    video_path = digital_human_local_path(((task.get("video") or {}).get("path")) or ((task.get("video") or {}).get("url")) or "")
    audio_path = digital_human_local_path(((task.get("audio") or {}).get("path")) or ((task.get("audio") or {}).get("url")) or "")

    target = ""
    select_file = False
    if video_path and is_path_under(DIGITAL_HUMAN_VIDEO_DIR, video_path):
        target = video_path
        select_file = True
    elif audio_path and is_path_under(DIGITAL_HUMAN_AUDIO_DIR, audio_path):
        target = audio_path
        select_file = True
    else:
        target = DIGITAL_HUMAN_VIDEO_DIR

    ok = _open_local_path(target, select_file=select_file)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to open digital human output directory.")
    return {"ok": True, "target": target, "selected": select_file}


async def open_digital_human_output_dir():
    ok = _open_local_path(DIGITAL_HUMAN_VIDEO_DIR, select_file=False)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to open digital human video output directory.")
    return {"ok": True, "target": DIGITAL_HUMAN_VIDEO_DIR}
