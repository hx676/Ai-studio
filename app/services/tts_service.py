import asyncio
import os
import re
import requests
import shutil
import subprocess
import sys
import time
from typing import List

import httpx
from fastapi import HTTPException

from app import legacy


def __getattr__(name):
    return getattr(legacy, name)


TTS_VOICE_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg"}
TTS_EMO_METHODS = {
    "与音色参考音频相同",
    "使用情感参考音频",
    "使用情感向量控制",
    "使用情感描述文本控制",
}
TTS_PROCESS = legacy.TTS_PROCESS
TTS_LAST_ERROR = legacy.TTS_LAST_ERROR

for _name in (
    "BASE_DIR",
    "DIGITAL_HUMAN_AUDIO_DIR",
    "TTS_GENERATION_LOCK",
    "TTS_SERVICE_LOCK",
):
    globals()[_name] = getattr(legacy, _name)

digital_human_log = legacy.digital_human_log
safe_upstream_summary = legacy.safe_upstream_summary


def _digital_human_service():
    from app.services import digital_human_service

    return digital_human_service


def normalize_tts_base_url(base_url):
    return _digital_human_service().normalize_tts_base_url(base_url)


def safe_join_url(base, path_value):
    return _digital_human_service().safe_join_url(base, path_value)


def tts_port_from_base_url(base_url):
    return _digital_human_service().tts_port_from_base_url(base_url)


def normalize_digital_human_config(payload=None):
    return _digital_human_service().normalize_digital_human_config(payload)


def digital_human_local_path(path):
    return _digital_human_service().digital_human_local_path(path)


def digital_human_media_url(path):
    return _digital_human_service().digital_human_media_url(path)


def digital_human_output_url(path):
    return _digital_human_service().digital_human_output_url(path)


def sanitize_output_filename(name, fallback, ext):
    return _digital_human_service().sanitize_output_filename(name, fallback, ext)


def local_requests_session():
    session = requests.Session()
    session.trust_env = False
    return session


def tts_status_payload(config, connected=False, error="", started=False):
    global TTS_PROCESS, TTS_LAST_ERROR
    process = TTS_PROCESS
    running = bool(process and process.poll() is None)
    display_error = local_service_error_message(error or TTS_LAST_ERROR)
    return {
        "base_url": normalize_tts_base_url((config.get("tts") or {}).get("base_url")),
        "connected": bool(connected),
        "managed": running,
        "pid": process.pid if running else None,
        "started": bool(started),
        "last_error": display_error,
    }

def local_service_error_message(error):
    text = str(error or "").strip()
    if not text:
        return ""
    noisy_tokens = (
        "502 Bad Gateway",
        "Server error",
        "http://localhost:",
        "http://127.0.0.1:",
        "For more information check:",
    )
    if any(token in text for token in noisy_tokens):
        return "TTS 正在预热或暂时不可用，请稍后重试。"
    return text

async def check_tts_health(config, timeout=4):
    base_url = normalize_tts_base_url((config.get("tts") or {}).get("base_url"))
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.get(safe_join_url(base_url, "/config"))
            response.raise_for_status()
        return tts_status_payload(config, connected=True)
    except Exception as exc:
        return tts_status_payload(config, connected=False, error=str(exc))

def start_tts_service_process(config):
    global TTS_PROCESS, TTS_LAST_ERROR
    tts = config.get("tts") or {}
    root_dir = os.path.abspath(tts.get("root_dir") or os.path.join(BASE_DIR, "index-tts-2"))
    python_path = tts.get("python_path") or os.path.join(root_dir, "py312", "python.exe")
    script_path = tts.get("script_path") or os.path.join(root_dir, "app.py")
    if not os.path.isfile(python_path):
        raise RuntimeError(f"TTS Python not found: {python_path}")
    if not os.path.isfile(script_path):
        raise RuntimeError(f"TTS app not found: {script_path}")

    port = str(tts_port_from_base_url(tts.get("base_url")))
    py_dir = os.path.join(root_dir, "py312")
    env = os.environ.copy()
    env.update({
        "GRADIO_TEMP_DIR": os.path.join(root_dir, "tmp"),
        "GRADIO_SERVER_PORT": port,
        "PORT": port,
        "GRADIO_PORT": port,
        "GRADIO_INBROWSER": "0",
        "GRADIO_BROWSER": "none",
        "BROWSER": "",
        "INFINITE_CANVAS_SILENT_SERVICE": "1",
        "PYTHONHOME": "",
        "PYTHONPATH": "",
        "PYTHONEXECUTABLE": python_path,
        "PYTHONWEXECUTABLE": os.path.join(py_dir, "pythonw.exe"),
        "PYTHON_EXECUTABLE": python_path,
        "PYTHONW_EXECUTABLE": os.path.join(py_dir, "pythonw.exe"),
        "PYTHON_BIN_PATH": python_path,
        "PYTHON_LIB_PATH": os.path.join(py_dir, "Lib", "site-packages"),
        "HF_ENDPOINT": env.get("HF_ENDPOINT") or "https://hf-mirror.com",
        "HF_HOME": os.path.join(root_dir, "checkpoints"),
        "TRANSFORMERS_CACHE": os.path.join(root_dir, "tf_download"),
        "XFORMERS_FORCE_DISABLE_TRITON": "1",
    })
    extra_path = [
        py_dir,
        os.path.join(py_dir, "Scripts"),
        os.path.join(py_dir, "ffmpeg", "bin"),
        os.path.join(py_dir, "Lib", "site-packages", "torch", "lib"),
        os.path.join(py_dir, "Library", "bin"),
    ]
    env["PATH"] = os.pathsep.join(extra_path + [env.get("PATH", "")])
    os.makedirs(env["GRADIO_TEMP_DIR"], exist_ok=True)

    creationflags = 0
    if os.name == "nt":
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    TTS_PROCESS = subprocess.Popen(
        [python_path, "-s", script_path],
        cwd=root_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    TTS_LAST_ERROR = ""
    legacy.TTS_PROCESS = TTS_PROCESS
    legacy.TTS_LAST_ERROR = TTS_LAST_ERROR
    return TTS_PROCESS

async def ensure_tts_service(config, wait_seconds=45, auto_start=True):
    global TTS_PROCESS, TTS_LAST_ERROR
    status = await check_tts_health(config)
    if status.get("connected"):
        return status
    if not auto_start:
        return status

    started = False
    with TTS_SERVICE_LOCK:
        process_running = bool(TTS_PROCESS and TTS_PROCESS.poll() is None)
        if not process_running:
            try:
                start_tts_service_process(config)
                started = True
            except Exception as exc:
                TTS_LAST_ERROR = str(exc)
                legacy.TTS_LAST_ERROR = TTS_LAST_ERROR
                return tts_status_payload(config, connected=False, error=str(exc), started=False)

    deadline = time.monotonic() + max(1, wait_seconds)
    last_status = status
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        last_status = await check_tts_health(config, timeout=8)
        if last_status.get("connected"):
            last_status["started"] = started
            return last_status
    return tts_status_payload(config, connected=False, error=last_status.get("last_error") or "TTS service did not become ready in time", started=started)

def get_gradio_client(config):
    tts = (config or {}).get("tts") or {}
    root_dir = tts.get("root_dir") or os.path.join(BASE_DIR, "index-tts-2")
    embedded_site = os.path.join(root_dir, "py312", "Lib", "site-packages")
    try:
        from gradio_client import Client
    except Exception:
        if embedded_site and os.path.isdir(embedded_site) and embedded_site not in sys.path:
            sys.path.insert(0, embedded_site)
        try:
            from gradio_client import Client
        except Exception as exc:
            raise RuntimeError("gradio_client is not installed. Please install project requirements.") from exc
    return Client(
        normalize_tts_base_url((config.get("tts") or {}).get("base_url")),
        verbose=False,
        httpx_kwargs={"trust_env": False},
    )

def tts_handle_file(path):
    config = normalize_digital_human_config()
    root_dir = (config.get("tts") or {}).get("root_dir") or os.path.join(BASE_DIR, "index-tts-2")
    embedded_site = os.path.join(root_dir, "py312", "Lib", "site-packages")
    try:
        from gradio_client import handle_file
    except Exception:
        if embedded_site and os.path.isdir(embedded_site) and embedded_site not in sys.path:
            sys.path.insert(0, embedded_site)
        try:
            from gradio_client import handle_file
        except Exception as exc:
            raise RuntimeError("gradio_client is not installed. Please install project requirements.") from exc
    return handle_file(path)

def gradio_handle_file(path):
    return tts_handle_file(path)

def collect_strings(value, result=None):
    result = result if result is not None else []
    if isinstance(value, str):
        text = value.strip()
        if text:
            result.append(text)
    elif isinstance(value, dict):
        preferred = []
        for key in ("choices", "value", "values", "data"):
            if key in value:
                preferred.append(value.get(key))
        for item in preferred or value.values():
            collect_strings(item, result)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            collect_strings(item, result)
    return result

def tts_voice_library_dir(config):
    tts = config.get("tts") or {}
    root_dir = tts.get("root_dir") or os.path.join(BASE_DIR, "index-tts-2")
    return os.path.abspath(os.path.join(root_dir, "assets", "bak"))

def tts_voice_embedding_dir(config):
    tts = config.get("tts") or {}
    root_dir = tts.get("root_dir") or os.path.join(BASE_DIR, "index-tts-2")
    return os.path.abspath(os.path.join(root_dir, "voices"))

def sanitize_tts_voice_name(name):
    text = re.sub(r"\s+", " ", str(name or "").strip())
    if not text:
        raise HTTPException(status_code=400, detail="音色名称不能为空")
    if os.path.basename(text) != text or any(ch in text for ch in '\\/:*?"<>|'):
        raise HTTPException(status_code=400, detail="音色名称不能包含路径或特殊字符")
    return text[:80]

def tts_voice_embedding_for_name(config, voice_name):
    name = str(voice_name or "").strip()
    if not name:
        return ""
    safe_name = os.path.basename(name)
    if safe_name != name:
        return ""
    folder = tts_voice_embedding_dir(config)
    candidate = os.path.abspath(os.path.join(folder, f"{safe_name}.pt"))
    try:
        if os.path.commonpath([folder, candidate]) == folder and os.path.isfile(candidate):
            return candidate
    except ValueError:
        pass
    return ""

def tts_voice_file_for_name(config, voice_name):
    name = str(voice_name or "").strip()
    if not name:
        return ""
    safe_name = os.path.basename(name)
    if safe_name != name:
        return ""
    library = tts_voice_library_dir(config)
    for ext in TTS_VOICE_EXTENSIONS:
        candidate = os.path.abspath(os.path.join(library, f"{safe_name}{ext}"))
        try:
            if os.path.commonpath([library, candidate]) == library and os.path.isfile(candidate):
                return candidate
        except ValueError:
            pass
    return ""

def save_tts_voice_preview_audio(config, source_path, voice_name):
    ext = os.path.splitext(source_path or "")[1].lower()
    if ext not in TTS_VOICE_EXTENSIONS:
        ext = ".wav"
    library = tts_voice_library_dir(config)
    os.makedirs(library, exist_ok=True)
    target = os.path.abspath(os.path.join(library, f"{voice_name}{ext}"))
    try:
        if os.path.commonpath([library, target]) != library:
            raise HTTPException(status_code=400, detail="音色路径不允许保存")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="音色路径不允许保存") from exc
    for old_ext in TTS_VOICE_EXTENSIONS:
        old_path = os.path.abspath(os.path.join(library, f"{voice_name}{old_ext}"))
        if old_path != target and os.path.isfile(old_path):
            os.remove(old_path)
    shutil.copyfile(source_path, target)
    return target

def enrich_tts_voice_item(config, item):
    name = str((item or {}).get("name") or (item or {}).get("value") or "").strip()
    value = str((item or {}).get("value") or name).strip()
    path = tts_voice_file_for_name(config, value) or tts_voice_file_for_name(config, name)
    enriched = {
        "name": name,
        "value": value,
        "path": path,
        "preview_url": digital_human_media_url(path) if path else "",
        "deletable": bool(path),
    }
    return enriched

def normalize_tts_voice_list(raw, config=None):
    names = []
    seen = set()
    for item in collect_strings(raw):
        if item.startswith(("http://", "https://")) or os.path.sep in item:
            continue
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        names.append({"name": key, "value": key, "path": "", "preview_url": "", "deletable": False})
    if config:
        names = [enrich_tts_voice_item(config, item) for item in names]
    return names

def tts_default_reference_audio(config):
    tts = config.get("tts") or {}
    default_voice = str(tts.get("default_voice") or "").strip()
    if default_voice and os.path.isfile(default_voice):
        return default_voice
    root_dir = tts.get("root_dir") or os.path.join(BASE_DIR, "index-tts-2")
    for folder in [os.path.join(root_dir, "voices"), os.path.join(root_dir, "examples")]:
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and os.path.splitext(name)[1].lower() in TTS_VOICE_EXTENSIONS:
                return path
    return ""

def extract_tts_job_id(value):
    if isinstance(value, (list, tuple)) and value:
        first = value[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    if isinstance(value, dict):
        for key in ("job_id", "jobId", "task_id", "taskId", "id"):
            if value.get(key):
                return str(value.get(key))
        for item in value.values():
            found = extract_tts_job_id(item)
            if found:
                return found
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""

def iter_tts_rows(table):
    if isinstance(table, dict):
        rows = table.get("data") or table.get("rows") or table.get("value")
        if rows is not None:
            yield from iter_tts_rows(rows)
    elif isinstance(table, (list, tuple)):
        if table and all(not isinstance(item, (list, tuple, dict)) for item in table):
            yield list(table)
        else:
            for item in table:
                yield from iter_tts_rows(item)

def tts_job_status_from_refresh(refresh_raw, job_id):
    table = refresh_raw[0] if isinstance(refresh_raw, (list, tuple)) and refresh_raw else refresh_raw
    rows = list(iter_tts_rows(table))
    selected = None
    if job_id:
        selected = next((row for row in rows if row and str(row[0]) == str(job_id)), None)
    if not selected and rows:
        selected = rows[0]
    if not selected:
        return {"status": "", "row": None}
    status = str(selected[1] if len(selected) > 1 else "").strip()
    result = str(selected[4] if len(selected) > 4 else "").strip()
    return {"status": status, "result": result, "row": selected}

def extract_tts_audio_candidate(value):
    if isinstance(value, dict):
        for key in ("path", "url", "name", "orig_name", "file", "audio"):
            candidate = value.get(key)
            found = extract_tts_audio_candidate(candidate)
            if found:
                return found
        for item in value.values():
            found = extract_tts_audio_candidate(item)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        preferred = list(value[1:3]) if len(value) >= 3 else list(value)
        for item in preferred:
            found = extract_tts_audio_candidate(item)
            if found:
                return found
    elif isinstance(value, str):
        text = value.strip()
        lower = text.lower().split("?", 1)[0]
        if lower.endswith((".wav", ".mp3", ".m4a", ".ogg")):
            return text
    return ""

def save_tts_audio_candidate(candidate, output_path, config):
    if not candidate:
        return False
    local = digital_human_local_path(candidate)
    if not local and os.path.isfile(str(candidate)):
        local = str(candidate)
    if not local:
        root_dir = (config.get("tts") or {}).get("root_dir") or os.path.join(BASE_DIR, "index-tts-2")
        maybe = os.path.abspath(os.path.join(root_dir, str(candidate)))
        if os.path.isfile(maybe):
            local = maybe
    if local:
        shutil.copyfile(local, output_path)
        return True
    if str(candidate).startswith(("http://", "https://")):
        with local_requests_session() as session:
            response = session.get(str(candidate), timeout=120)
            response.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(response.content)
        return True
    return False

def save_tts_voice_sync(file_path, voice_name, config):
    client = get_gradio_client(config)
    result = client.predict(
        name=voice_name or "",
        ref_audio_input=tts_handle_file(file_path),
        api_name="/save_name",
    )
    return {"voice_name": voice_name, "raw": safe_upstream_summary(result)}

async def save_tts_voice(file_path, voice_name, config):
    status = await ensure_tts_service(config, wait_seconds=90, auto_start=True)
    if not status.get("connected"):
        raise HTTPException(status_code=503, detail=f"TTS service is not ready: {status.get('last_error') or 'connection failed'}")
    return await asyncio.to_thread(save_tts_voice_sync, file_path, voice_name, config)

def clamp_number(value, default, minimum, maximum):
    try:
        number = float(value)
    except Exception:
        number = float(default)
    return max(minimum, min(maximum, number))

def normalize_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default

def normalize_tts_options(options=None):
    raw = options.model_dump() if hasattr(options, "model_dump") else options.dict() if hasattr(options, "dict") else dict(options or {})
    method = str(raw.get("emo_control_method") or "与音色参考音频相同").strip()
    if method not in TTS_EMO_METHODS:
        method = "与音色参考音频相同"
    normalized = {
        "speed": clamp_number(raw.get("speed"), 1.0, 0.1, 2.5),
        "emo_control_method": method,
        "emo_ref_url": str(raw.get("emo_ref_url") or "").strip(),
        "emo_ref_path": str(raw.get("emo_ref_path") or "").strip(),
        "emo_weight": clamp_number(raw.get("emo_weight"), 0.8, 0.0, 1.6),
        "emo_text": str(raw.get("emo_text") or "").strip(),
        "emo_random": normalize_bool(raw.get("emo_random"), False),
        "max_tokens": clamp_number(raw.get("max_tokens"), 120, 20, 600),
        "do_sample": normalize_bool(raw.get("do_sample"), True),
        "top_p": clamp_number(raw.get("top_p"), 0.8, 0.0, 1.0),
        "top_k": clamp_number(raw.get("top_k"), 30, 0, 100),
        "temperature": clamp_number(raw.get("temperature"), 0.8, 0.1, 2.0),
        "length_penalty": clamp_number(raw.get("length_penalty"), 0.0, -10, 10),
        "num_beams": clamp_number(raw.get("num_beams"), 3, 1, 10),
        "repetition_penalty": clamp_number(raw.get("repetition_penalty"), 10.0, 0, 20),
        "max_mel": clamp_number(raw.get("max_mel"), 1500, 50, 1815),
    }
    for index in range(1, 9):
        normalized[f"vec{index}"] = clamp_number(raw.get(f"vec{index}"), 0, 0.0, 1.4)
    return normalized

def generate_digital_human_tts_sync(text, voice_path, voice_name, config, output_path, tts_options=None):
    with TTS_GENERATION_LOCK:
        client = get_gradio_client(config)
        reference_audio = voice_path if voice_path and os.path.isfile(voice_path) else tts_default_reference_audio(config)
        if not reference_audio or not os.path.isfile(reference_audio):
            raise HTTPException(status_code=400, detail="Please upload a reference audio file first.")
        options = normalize_tts_options(tts_options)
        emo_ref_path = digital_human_local_path(options.get("emo_ref_url")) or digital_human_local_path(options.get("emo_ref_path"))
        if not emo_ref_path or not os.path.isfile(emo_ref_path):
            emo_ref_path = reference_audio
        selected_voice = str(voice_name or "").strip()
        if not selected_voice or set(selected_voice) == {"?"}:
            selected_voice = "\u4f7f\u7528\u53c2\u8003\u97f3\u9891"
        submit_raw = client.predict(
            voices_dropdown=selected_voice,
            speed=options["speed"],
            prompt=tts_handle_file(reference_audio),
            text=text,
            emo_control_method=options["emo_control_method"],
            emo_ref_path=tts_handle_file(emo_ref_path),
            emo_weight=options["emo_weight"],
            emo_text=options["emo_text"],
            emo_random=options["emo_random"],
            max_tokens=options["max_tokens"],
            vec1=options["vec1"],
            vec2=options["vec2"],
            vec3=options["vec3"],
            vec4=options["vec4"],
            vec5=options["vec5"],
            vec6=options["vec6"],
            vec7=options["vec7"],
            vec8=options["vec8"],
            do_sample=options["do_sample"],
            top_p=options["top_p"],
            top_k=options["top_k"],
            temperature=options["temperature"],
            length_penalty=options["length_penalty"],
            num_beams=options["num_beams"],
            repetition_penalty=options["repetition_penalty"],
            max_mel=options["max_mel"],
            api_name="/submit_and_refresh",
        )
        job_id = extract_tts_job_id(submit_raw)
        deadline = time.monotonic() + 900
        last_raw = submit_raw
        while time.monotonic() < deadline:
            time.sleep(2)
            refresh_raw = client.predict(api_name="/refresh_all_outputs")
            last_raw = refresh_raw
            job_state = tts_job_status_from_refresh(refresh_raw, job_id)
            status_text = str(job_state.get("status") or "").lower()
            if any(token in status_text for token in ("fail", "error", "失败")):
                raise HTTPException(status_code=502, detail=f"TTS task failed: {safe_upstream_summary(job_state)}")
            candidate = ""
            if isinstance(refresh_raw, (list, tuple)) and len(refresh_raw) >= 3:
                candidate = extract_tts_audio_candidate([refresh_raw[1], refresh_raw[2]])
            candidate = candidate or extract_tts_audio_candidate(refresh_raw)
            if candidate and save_tts_audio_candidate(candidate, output_path, config):
                return {"job_id": job_id, "submit": safe_upstream_summary(submit_raw), "refresh": safe_upstream_summary(refresh_raw)}
        raise HTTPException(status_code=504, detail=f"TTS task timed out: {safe_upstream_summary(last_raw)}")

def list_tts_voices_sync(config):
    client = get_gradio_client(config)
    raw = client.predict(api_name="/update_voices")
    return normalize_tts_voice_list(raw, config)

async def list_tts_voices(config=None, auto_start=True):
    config = normalize_digital_human_config(config)
    status = await ensure_tts_service(config, wait_seconds=30 if auto_start else 2, auto_start=auto_start)
    if not status.get("connected"):
        return [], status
    try:
        voices = await asyncio.to_thread(list_tts_voices_sync, config)
        return voices, status
    except Exception as exc:
        status["connected"] = False
        status["last_error"] = str(exc)
        return [], status

async def run_subprocess_capture(cmd, cwd=None, timeout=900):
    def _run():
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return await asyncio.to_thread(_run)

async def generate_digital_human_tts(text, voice_path, config, voice_name="", tts_options=None):
    output_name = sanitize_output_filename(text[:24], "digital_tts", ".wav")
    output_path = os.path.join(DIGITAL_HUMAN_AUDIO_DIR, output_name)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    digital_human_log(f"TTS preparing text_len={len(text or '')} voice={voice_name or 'reference'} output={os.path.basename(output_path)}")
    status = await ensure_tts_service(config, wait_seconds=120, auto_start=True)
    if not status.get("connected"):
        raise HTTPException(status_code=503, detail=f"TTS service is not ready: {status.get('last_error') or 'connection failed'}")
    options = normalize_tts_options(tts_options)
    digital_human_log(f"TTS start speed={options.get('speed')} method={options.get('emo_control_method')}")
    debug = await asyncio.to_thread(generate_digital_human_tts_sync, text, voice_path, voice_name, config, output_path, options)
    if not os.path.isfile(output_path):
        raise HTTPException(status_code=502, detail="TTS did not return an audio file.")
    digital_human_log(f"TTS done job={debug.get('job_id') or '-'} file={os.path.basename(output_path)}")
    return {
        "url": digital_human_output_url(output_path),
        "path": output_path,
        "name": os.path.basename(output_path),
        "tts": {**debug, "options": options},
    }
