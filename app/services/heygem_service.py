import asyncio
import os
import random
import re
import shutil
import sys
import time
import urllib.parse
import uuid

import httpx
import requests
from fastapi import HTTPException

from app import legacy


def __getattr__(name):
    return getattr(legacy, name)


HEYGEM_BLOCKING_HINTS = (
    "队列满",
    "严重阻塞",
    "下游队列异常",
    "下游异常",
    "queue full",
    "blocked",
    "blocking",
)
HEYGEM_VIDEO_EXTENSIONS = getattr(legacy, "HEYGEM_VIDEO_EXTENSIONS", (".mp4", ".mov", ".webm", ".m4v"))

for _name in (
    "BASE_DIR",
    "DIGITAL_HUMAN_TASKS",
    "DIGITAL_HUMAN_TASK_LOCK",
    "DIGITAL_HUMAN_VIDEO_DIR",
    "HEYGEM_GENERATION_LOCK",
):
    globals()[_name] = getattr(legacy, _name)

safe_upstream_summary = legacy.safe_upstream_summary


def _digital_human_service():
    from app.services import digital_human_service

    return digital_human_service


def normalize_service_base_url(base_url, fallback):
    return _digital_human_service().normalize_service_base_url(base_url, fallback)


def safe_join_url(base, path_value):
    return _digital_human_service().safe_join_url(base, path_value)


def digital_human_local_path(path):
    return _digital_human_service().digital_human_local_path(path)


def digital_human_output_url(path):
    return _digital_human_service().digital_human_output_url(path)


def public_url_for(local_url, request=None, config=None):
    return _digital_human_service().public_url_for(local_url, request, config)


def gradio_handle_file(path):
    from app.services.tts_service import gradio_handle_file as _gradio_handle_file

    return _gradio_handle_file(path)


def local_requests_session():
    session = requests.Session()
    session.trust_env = False
    return session


def get_heygem_gradio_client(config):
    try:
        from gradio_client import Client
    except Exception:
        tts = (config or {}).get("tts") or {}
        root_dir = tts.get("root_dir") or os.path.join(BASE_DIR, "index-tts-2")
        embedded_site = os.path.join(root_dir, "py312", "Lib", "site-packages")
        if embedded_site and os.path.isdir(embedded_site) and embedded_site not in sys.path:
            sys.path.insert(0, embedded_site)
        try:
            from gradio_client import Client
        except Exception as exc:
            raise RuntimeError("gradio_client is not installed. Please install project requirements.") from exc
    base_url = normalize_service_base_url((config.get("heygem") or {}).get("base_url"), "http://127.0.0.1:7860/")
    return Client(base_url, verbose=False, httpx_kwargs={"trust_env": False})

def heygem_video_candidate_from_text(value):
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return ""
    clean = text.split("?", 1)[0].strip().strip('"').strip("'")
    if clean.lower().endswith(HEYGEM_VIDEO_EXTENSIONS):
        return text
    match = re.search(r'([A-Za-z]:[\\/][^\s"\'\]\)]+?\.(?:mp4|mov|webm|m4v)|(?:\.?[\\/])?result[\\/][^\s"\'\]\)]+?\.(?:mp4|mov|webm|m4v)|[^\s"\'\]\)]+?\.(?:mp4|mov|webm|m4v))', text, re.IGNORECASE)
    if match:
        return match.group(1)
    return ""

def heygem_is_video_candidate(value):
    return bool(heygem_video_candidate_from_text(value))

def heygem_result_urls(payload):
    urls = []
    if isinstance(payload, dict):
        for key in ("video_url", "video", "url", "result", "result_url", "output", "file", "path"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                urls.append(value)
            elif isinstance(value, list):
                urls.extend([v for v in value if isinstance(v, str)])
            elif isinstance(value, dict):
                urls.extend(heygem_result_urls(value))
        data = payload.get("data")
        if isinstance(data, (dict, list)):
            urls.extend(heygem_result_urls(data))
    elif isinstance(payload, list):
        for item in payload:
            urls.extend(heygem_result_urls(item))
    return [heygem_video_candidate_from_text(u) for u in urls if heygem_is_video_candidate(u)]

def heygem_output_candidate(payload):
    if isinstance(payload, dict):
        for key in ("video", "video_url", "url", "result", "result_url", "output", "file", "path"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                found = heygem_video_candidate_from_text(value)
                if found:
                    return found
            if isinstance(value, (dict, list, tuple)):
                found = heygem_output_candidate(value)
                if found:
                    return found
        data = payload.get("data")
        if isinstance(data, (dict, list, tuple)):
            return heygem_output_candidate(data)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found = heygem_output_candidate(item)
            if found:
                return found
    elif isinstance(payload, str):
        return heygem_video_candidate_from_text(payload)
    return ""

def heygem_resolve_local_result(candidate, config):
    local = digital_human_local_path(candidate)
    if local:
        return local
    text = heygem_video_candidate_from_text(candidate) or str(candidate or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://")):
        return ""
    clean = urllib.parse.unquote(text.split("?", 1)[0]).strip().strip('"').strip("'")
    clean = clean.replace("/", os.sep).replace("\\", os.sep)
    if os.path.isabs(clean) and os.path.isfile(clean):
        return os.path.abspath(clean)
    root_dir = (config.get("heygem") or {}).get("root_dir") or os.path.join(BASE_DIR, "heygem-win-fix", "heygem-win")
    root_dir = os.path.abspath(root_dir)
    relative = clean.lstrip(".").lstrip("\\/")
    candidates = [
        os.path.abspath(os.path.join(root_dir, relative)),
        os.path.abspath(os.path.join(root_dir, os.path.basename(clean))),
        os.path.abspath(os.path.join(root_dir, "result", os.path.basename(clean))),
    ]
    for path in candidates:
        try:
            if os.path.commonpath([root_dir, path]) == root_dir and os.path.isfile(path):
                return path
        except ValueError:
            pass
    return ""

def heygem_media_candidate(payload):
    if isinstance(payload, dict):
        for key in ("video", "audio", "video_url", "audio_url", "url", "result", "result_url", "output", "file", "path"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, (dict, list, tuple)):
                found = heygem_media_candidate(value)
                if found:
                    return found
        data = payload.get("data")
        if isinstance(data, (dict, list, tuple)):
            return heygem_media_candidate(data)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found = heygem_media_candidate(item)
            if found:
                return found
    elif isinstance(payload, str):
        return payload.strip()
    return ""

def heygem_api_base_url(config):
    heygem = (config or {}).get("heygem") or {}
    return normalize_service_base_url(heygem.get("api_base_url") or "http://127.0.0.1:8383/", "http://127.0.0.1:8383/")

def heygem_root_dir(config):
    return os.path.abspath(((config or {}).get("heygem") or {}).get("root_dir") or os.path.join(BASE_DIR, "heygem-win-fix", "heygem-win"))

def heygem_safe_job_code(value=""):
    text = str(value or "").strip()
    if re.fullmatch(r"\d{3,48}", text):
        return text
    return f"{int(time.time() * 1000)}{random.randint(100, 999)}"

def heygem_save_relative_path(path, config):
    local = digital_human_local_path(path)
    if not local:
        return ""
    root_dir = heygem_root_dir(config)
    save_dir = os.path.abspath(os.path.join(root_dir, "save"))
    os.makedirs(save_dir, exist_ok=True)
    ext = os.path.splitext(local)[1].lower() or ".bin"
    dest = os.path.abspath(os.path.join(save_dir, f"{uuid.uuid4().hex[:12]}{ext}"))
    try:
        if os.path.commonpath([save_dir, dest]) != save_dir:
            return ""
    except ValueError:
        return ""
    shutil.copyfile(local, dest)
    return os.path.relpath(dest, root_dir).replace("\\", "/")

def heygem_log_path(config):
    root_dir = heygem_root_dir(config)
    return os.path.join(root_dir, "data", "log", "dh.log")

def heygem_log_cursor(config):
    path = heygem_log_path(config)
    try:
        return os.path.getsize(path) if os.path.isfile(path) else 0
    except OSError:
        return 0

def heygem_read_log_since(config, cursor, max_chars=12000):
    path = heygem_log_path(config)
    if not os.path.isfile(path):
        return "", cursor
    try:
        size = os.path.getsize(path)
        start = min(max(int(cursor or 0), 0), size)
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(max_chars)
        decoded = []
        for encoding in ("utf-8", "gb18030", "cp936"):
            try:
                decoded.append(data.decode(encoding, errors="replace"))
            except Exception:
                pass
        text = "\n".join(dict.fromkeys(decoded))
        return text, size
    except Exception:
        return "", cursor

def heygem_blocking_reason_from_log(text, task_id=""):
    if not text:
        return ""
    lines = text.splitlines()
    task = str(task_id or "").strip()
    for line in reversed(lines[-120:]):
        lower = line.lower()
        is_known_queue_error = "[error]" in lower and "trans_dh_service.py[line:300]" in lower
        is_blocking_text = any(hint.lower() in lower for hint in HEYGEM_BLOCKING_HINTS)
        if is_blocking_text or is_known_queue_error:
            if not task or f"[{task}]" in line or task in line:
                return "HeyGem 内部队列阻塞：视频驱动队列满或下游队列异常。请重启 HeyGem 服务后重试，或换一个更短/分辨率更低的驱动视频。"
    return ""

def heygem_progress_payload(payload):
    data = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return {"status": "", "progress": None, "message": ""}
    progress = data.get("progress")
    try:
        progress = float(progress) if progress is not None and str(progress) != "" else None
    except Exception:
        progress = None
    return {
        "status": str(data.get("status") or data.get("state") or data.get("task_status") or "").strip(),
        "progress": progress,
        "message": str(data.get("msg") or data.get("message") or data.get("error") or "").strip(),
    }

def update_digital_human_task(task_id, **updates):
    with DIGITAL_HUMAN_TASK_LOCK:
        task = DIGITAL_HUMAN_TASKS.get(task_id)
        if task is not None:
            updates.setdefault("updated_at", time.time())
            task.update(updates)

def save_heygem_video_result_sync(candidate, config):
    output_name = f"digital_human_{uuid.uuid4().hex[:12]}.mp4"
    output_path = os.path.join(DIGITAL_HUMAN_VIDEO_DIR, output_name)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    local = heygem_resolve_local_result(candidate, config)
    if local:
        shutil.copyfile(local, output_path)
    else:
        full_url = str(candidate or "")
        if full_url and not (full_url.startswith("http://") or full_url.startswith("https://")):
            heygem_base = str((config.get("heygem") or {}).get("base_url") or "").rstrip("/")
            full_url = heygem_base + "/" + full_url.lstrip("/")
        with local_requests_session() as session:
            response = session.get(full_url, timeout=900)
            response.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(response.content)
    return {"url": digital_human_output_url(output_path), "path": output_path, "name": os.path.basename(output_path)}

async def save_heygem_video_result(url, client, config):
    return await asyncio.to_thread(save_heygem_video_result_sync, url, config)

def check_heygem_health_sync(config):
    gradio_url = safe_join_url(normalize_service_base_url((config.get("heygem") or {}).get("base_url"), "http://127.0.0.1:7860/"), "/config")
    api_url = safe_join_url(heygem_api_base_url(config), "/easy/query")
    result = {
        "base_url": normalize_service_base_url((config.get("heygem") or {}).get("base_url"), "http://127.0.0.1:7860/"),
        "api_base_url": heygem_api_base_url(config),
        "connected": False,
        "gradio_connected": False,
        "api_connected": False,
        "last_error": "",
    }
    errors = []
    with local_requests_session() as session:
        try:
            response = session.get(gradio_url, timeout=4)
            response.raise_for_status()
            result["gradio_connected"] = True
        except Exception as exc:
            errors.append(f"Gradio: {exc}")
        try:
            response = session.get(api_url, params={"code": "0"}, timeout=4)
            response.raise_for_status()
            result["api_connected"] = True
        except Exception as exc:
            errors.append(f"任务接口: {exc}")
    result["connected"] = bool(result["api_connected"])
    if not result["connected"]:
        result["last_error"] = "; ".join(errors) or "HeyGem 任务接口未连接"
    return result

async def check_heygem_health(config):
    try:
        return await asyncio.to_thread(check_heygem_health_sync, config)
    except Exception as exc:
        return {
            "base_url": normalize_service_base_url((config.get("heygem") or {}).get("base_url"), "http://127.0.0.1:7860/"),
            "api_base_url": heygem_api_base_url(config),
            "connected": False,
            "gradio_connected": False,
            "api_connected": False,
            "last_error": str(exc),
        }

def generate_heygem_video_sync(audio_path, video_path, config):
    heygem = config.get("heygem") or {}
    if not audio_path or not os.path.isfile(audio_path):
        raise HTTPException(status_code=400, detail="TTS audio file is missing.")
    if not video_path or not os.path.isfile(video_path):
        raise HTTPException(status_code=400, detail="Drive video file is missing.")
    client = get_heygem_gradio_client(config)
    video_loaded = client.predict(
        video={"video": gradio_handle_file(video_path)},
        api_name="/display_video_path",
    )
    audio_loaded = client.predict(
        video=gradio_handle_file(audio_path),
        api_name="/display_audio_path",
    )
    video_value = heygem_media_candidate(video_loaded) or str(video_loaded or "")
    audio_value = heygem_media_candidate(audio_loaded) or str(audio_loaded or "")
    result = client.predict(
        video=video_value,
        audio=audio_value,
        min_resolution=float(heygem.get("min_resolution") or 720),
        if_res=bool(heygem.get("if_res")),
        api_name="/do_make",
    )
    candidate = heygem_output_candidate(result)
    if not candidate:
        raise HTTPException(status_code=502, detail=f"HeyGem did not return a video file: {safe_upstream_summary(result)}")
    video = save_heygem_video_result_sync(candidate, config)
    return {
        "video": video,
        "raw": safe_upstream_summary(result),
        "loaded": {
            "video": safe_upstream_summary(video_loaded),
            "audio": safe_upstream_summary(audio_loaded),
        },
        "status": "DONE",
    }

async def generate_heygem_video(audio_path, video_path, config):
    status = await check_heygem_health(config)
    if not status.get("connected"):
        raise HTTPException(status_code=503, detail=f"HeyGem service is not ready: {status.get('last_error') or 'connection failed'}")
    return await asyncio.to_thread(generate_heygem_video_sync, audio_path, video_path, config)

def generate_heygem_video_rest_sync(audio_path, video_path, config, task_id="", request_base_url=""):
    heygem = config.get("heygem") or {}
    if not audio_path or not os.path.isfile(audio_path):
        raise HTTPException(status_code=400, detail="TTS audio file is missing.")
    if not video_path or not os.path.isfile(video_path):
        raise HTTPException(status_code=400, detail="Drive video file is missing.")
    with HEYGEM_GENERATION_LOCK:
        code = heygem_safe_job_code(task_id)
        audio_rel = heygem_save_relative_path(audio_path, config)
        video_rel = heygem_save_relative_path(video_path, config)
        if not audio_rel or not video_rel:
            raise HTTPException(status_code=400, detail="无法把音频或驱动视频交给 HeyGem。")
        api_base = heygem_api_base_url(config)
        submit_url = safe_join_url(api_base, heygem.get("submit_path") or "/easy/submit")
        query_url = safe_join_url(api_base, heygem.get("query_path") or "/easy/query")
        log_cursor = heygem_log_cursor(config)
        submit_body = {"audio_url": audio_rel, "video_url": video_rel, "code": code}
        update_digital_human_task(
            task_id,
            heygem={
                "code": code,
                "api_base_url": api_base,
                "submit_url": submit_url,
                "query_url": query_url,
                "progress": 0,
                "message": "素材已载入",
            },
        )
        session = local_requests_session()
        try:
            response = session.post(submit_url, json=submit_body, timeout=30)
            response.raise_for_status()
            try:
                submit_raw = response.json()
            except Exception:
                submit_raw = {"text": response.text[:500]}
        except Exception as exc:
            session.close()
            raise HTTPException(status_code=502, detail=f"HeyGem 提交失败：{exc}")
        max_wait = max(60, int(heygem.get("max_wait_seconds") or 1800))
        stall_timeout = max(30, int(heygem.get("stall_timeout_seconds") or 240))
        deadline = time.monotonic() + max_wait
        last_change = time.monotonic()
        last_progress_key = None
        last_payload = submit_raw
        poll_count = 0
        while time.monotonic() < deadline:
            time.sleep(2)
            poll_count += 1
            log_text, log_cursor = heygem_read_log_since(config, log_cursor)
            blocking_reason = heygem_blocking_reason_from_log(log_text, code)
            if blocking_reason:
                session.close()
                raise HTTPException(status_code=502, detail=blocking_reason)
            try:
                response = session.get(query_url, params={"code": code}, timeout=20)
                response.raise_for_status()
                try:
                    raw = response.json()
                except Exception:
                    raw = {"text": response.text[:500]}
            except Exception as exc:
                if time.monotonic() - last_change > stall_timeout:
                    session.close()
                    raise HTTPException(status_code=504, detail=f"HeyGem 查询无响应：{exc}")
                continue
            last_payload = raw
            urls = heygem_result_urls(raw)
            progress_info = heygem_progress_payload(raw)
            progress_key = (
                progress_info.get("status"),
                progress_info.get("progress"),
                progress_info.get("message"),
                bool(urls),
            )
            if progress_key != last_progress_key:
                last_change = time.monotonic()
                last_progress_key = progress_key
            update_digital_human_task(
                task_id,
                heygem={
                    "code": code,
                    "api_base_url": api_base,
                    "submit_url": submit_url,
                    "query_url": query_url,
                    "poll_count": poll_count,
                    "poll_status": progress_info.get("status"),
                    "progress": progress_info.get("progress"),
                    "message": progress_info.get("message"),
                    "video_url_count": len(urls),
                },
            )
            if urls:
                video = save_heygem_video_result_sync(urls[0], config)
                session.close()
                return {
                    "video": video,
                    "raw": safe_upstream_summary(raw),
                    "submit": safe_upstream_summary(submit_raw),
                    "status": heygem_status(raw) or "DONE",
                    "code": code,
                }
            status = heygem_status(raw)
            if status in {"FAIL", "FAILED", "ERROR", "ERRORED", "CANCELED", "CANCELLED", "-1"}:
                session.close()
                raise HTTPException(status_code=502, detail=f"HeyGem 任务失败：{safe_upstream_summary(raw)}")
            if time.monotonic() - last_change > stall_timeout:
                session.close()
                raise HTTPException(
                    status_code=504,
                    detail=f"HeyGem 长时间没有新进度，可能已阻塞。最后状态：{safe_upstream_summary(raw)}",
                )
        session.close()
        raise HTTPException(status_code=504, detail=f"HeyGem 任务超时：{safe_upstream_summary(last_payload)}")

async def generate_heygem_video_monitored(audio_path, video_path, config, task_id="", request_base_url=""):
    status = await check_heygem_health(config)
    if not status.get("connected"):
        raise HTTPException(status_code=503, detail=f"HeyGem service is not ready: {status.get('last_error') or 'connection failed'}")
    return await asyncio.to_thread(generate_heygem_video_rest_sync, audio_path, video_path, config, task_id, request_base_url)

def heygem_task_id(payload):
    if not isinstance(payload, dict):
        return ""
    for key in ("task_id", "taskId", "id", "code", "job_id", "jobId"):
        value = payload.get(key)
        if value:
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        return heygem_task_id(data)
    return ""

def heygem_status(payload):
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return str(data.get("status") or data.get("state") or data.get("task_status") or payload.get("status") or "").upper()

async def submit_heygem_task(audio_url, video_url, config, request=None, code=""):
    heygem = config.get("heygem") or {}
    submit_url = safe_join_url(heygem.get("base_url"), heygem.get("submit_path") or "/easy/submit")
    if not submit_url:
        raise HTTPException(status_code=400, detail="HeyGem 服务地址未配置")
    code = code or uuid.uuid4().hex[:12]
    body = {
        "audio_url": public_url_for(audio_url, request, config),
        "video_url": public_url_for(video_url, request, config),
        "code": code,
    }
    if not body["audio_url"] or not body["video_url"]:
        raise HTTPException(status_code=400, detail="音频或驱动视频地址缺失")
    async with httpx.AsyncClient(timeout=900, trust_env=False) as client:
        response = await client.post(submit_url, json=body)
        response.raise_for_status()
        try:
            raw = response.json()
        except Exception:
            raw = {"text": response.text[:500]}
        task_id = heygem_task_id(raw) or code
    return {"task_id": task_id, "code": code, "submit": raw, "submit_url": submit_url}

async def poll_heygem_task(task_id, config):
    heygem = config.get("heygem") or {}
    query_url = safe_join_url(heygem.get("base_url"), heygem.get("query_path") or "/easy/query")
    if not query_url:
        raise HTTPException(status_code=400, detail="HeyGem 查询地址未配置")
    deadline = time.monotonic() + 1800
    last_payload = {}
    async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
        while time.monotonic() < deadline:
            response = await client.get(query_url, params={"code": task_id})
            response.raise_for_status()
            try:
                raw = response.json()
            except Exception:
                raw = {"text": response.text[:500]}
            last_payload = raw
            urls = heygem_result_urls(raw)
            status = heygem_status(raw)
            if urls:
                video = await save_heygem_video_result(urls[0], client, config)
                return {"video": video, "raw": raw, "status": status or "DONE"}
            if status in {"FAIL", "FAILED", "ERROR", "ERRORED", "CANCELED", "CANCELLED"}:
                raise HTTPException(status_code=502, detail=f"HeyGem 任务失败: {safe_upstream_summary(raw)}")
            await asyncio.sleep(3)
    raise HTTPException(status_code=504, detail=f"HeyGem 任务超时: {safe_upstream_summary(last_payload)}")
