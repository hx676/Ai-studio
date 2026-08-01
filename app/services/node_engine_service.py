from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
from fastapi import HTTPException

from app.core.paths import BASE_DIR, DATA_DIR
from app.core.json_store import atomic_write_json, read_json_resilient
from app.core.run_retention import prune_run_history
from app.core.security import redact_sensitive_text
from app.core.upload_limits import UPLOAD_FILE_MAX_BYTES
from app.models.runtime_nodes import RuntimeGraphRunRequest
from app.services import node_engine_asset_service, node_engine_component_service
from app.services.storage_service import content_type_for_path, output_file_from_url


ENGINE_HOST = "127.0.0.1"
ENGINE_PORT = int(os.getenv("SYNCANVAS_NODE_ENGINE_PORT", "3021"))
DATA_ROOT = Path(DATA_DIR) / "node-engine"
CATALOG_FILE = DATA_ROOT / "catalog.json"
PROCESS_FILE = DATA_ROOT / "process.json"
RUN_DIR = DATA_ROOT / "runs"
LOG_DIR = DATA_ROOT / "logs"
INPUT_DIR = DATA_ROOT / "input"
OUTPUT_DIR = DATA_ROOT / "output"
TEMP_DIR = DATA_ROOT / "temp"
USER_DIR = DATA_ROOT / "user"
MODELS_DIR = DATA_ROOT / "models"
CUSTOM_NODES_DIR = DATA_ROOT / "custom_nodes"
BRIDGE_SOURCE = Path(BASE_DIR) / "app" / "resources" / "node_engine_bridge" / "__init__.py"
BRIDGE_DIR = CUSTOM_NODES_DIR / "syncanvas_bridge"
EXTRA_PATHS_FILE = DATA_ROOT / "extra_model_paths.yaml"
BUILTIN_I18N_FILE = Path(BASE_DIR) / "app" / "resources" / "node_engine_i18n" / "zh-CN.json"
TERMINAL_RUN_STATES = {"succeeded", "failed", "cancelled", "interrupted"}
OPAQUE_TYPES = {
    "MODEL",
    "CLIP",
    "VAE",
    "LATENT",
    "CONDITIONING",
    "CONTROL_NET",
    "SAMPLER",
    "SIGMAS",
    "GUIDER",
    "NOISE",
}
SERIAL_TYPES = {"STRING", "INT", "FLOAT", "NUMBER", "BOOLEAN"}
CANVAS_UTILITY_TYPES = SERIAL_TYPES | {"IMAGE", "MASK", "AUDIO", "VIDEO", "COMBO"}
MAX_CATALOG_BYTES = 64 * 1024 * 1024
MAX_GRAPH_BYTES = 8 * 1024 * 1024
MAX_QUEUED_RUNS = max(1, int(os.getenv("SYNCANVAS_NODE_ENGINE_MAX_QUEUE", "32")))
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5

_PROCESS: Optional[subprocess.Popen] = None
_LIFECYCLE_LOCK: Optional[asyncio.Lock] = None
_CATALOG: Dict[str, Dict[str, Any]] = {}
_CATALOG_META: Dict[str, Any] = {}

try:
    import websockets
except ImportError:  # The run still works through history polling on minimal installs.
    websockets = None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _model_dump(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


def _atomic_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def _read_json(path: Path, fallback: Any = None) -> Any:
    return read_json_resilient(path, fallback)


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _lifecycle_lock() -> asyncio.Lock:
    global _LIFECYCLE_LOCK
    if _LIFECYCLE_LOCK is None:
        _LIFECYCLE_LOCK = asyncio.Lock()
    return _LIFECYCLE_LOCK


def engine_base_url() -> str:
    override = str(os.getenv("SYNCANVAS_NODE_ENGINE_URL", "")).strip().rstrip("/")
    return override or f"http://{ENGINE_HOST}:{ENGINE_PORT}"


def _engine_ws_url(client_id: str) -> str:
    parsed = urllib.parse.urlparse(engine_base_url())
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/")
    query = urllib.parse.urlencode({"clientId": client_id})
    return urllib.parse.urlunparse((scheme, parsed.netloc, f"{path}/ws", "", query, ""))


async def _consume_engine_events(client_id: str, queue: asyncio.Queue, ready: asyncio.Event) -> None:
    if websockets is None:
        ready.set()
        return
    try:
        async with websockets.connect(
            _engine_ws_url(client_id),
            open_timeout=5,
            close_timeout=1,
            max_size=4 * 1024 * 1024,
        ) as websocket:
            ready.set()
            async for message in websocket:
                if not isinstance(message, str):
                    continue
                try:
                    event = json.loads(message)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                await queue.put(event)
    except asyncio.CancelledError:
        raise
    except Exception:
        logging.debug("Node engine WebSocket progress is unavailable", exc_info=True)
    finally:
        ready.set()


def _engine_request(path: str, method: str = "GET", payload: Any = None, timeout: float = 10) -> Any:
    url = f"{engine_base_url()}{path}"
    data = None
    headers = {"Accept": "application/json", "User-Agent": "SynCanvas-NodeEngine/1.0"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:5000]
        raise RuntimeError(f"节点引擎 HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"节点引擎不可用：{exc}") from exc


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, int(pid))
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, PermissionError):
        return False


def _tracked_process() -> Dict[str, Any]:
    return _read_json(PROCESS_FILE, {}) or {}


def _process_command_line(pid: int) -> str:
    if _PROCESS is not None and _PROCESS.pid == pid and _PROCESS.poll() is None:
        return "managed-current-process"
    try:
        if os.name == "nt":
            command = (
                f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\"; "
                "if($p){[Console]::Out.Write($p.CommandLine)}"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.stdout.strip()
        proc_path = Path("/proc") / str(pid) / "cmdline"
        return proc_path.read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace")
    except Exception:
        return ""


def _process_executable_path(pid: int) -> str:
    if os.name != "nt" or pid <= 0:
        return ""
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass
    return ""


def _process_matches_record(record: Dict[str, Any]) -> bool:
    pid = int(record.get("pid") or 0)
    if not pid or not _process_exists(pid):
        return False
    if _PROCESS is not None and _PROCESS.pid == pid and _PROCESS.poll() is None:
        return True
    expected_root = str(record.get("runtime_root") or "").strip()
    executable = _process_executable_path(pid)
    if executable and expected_root:
        expected_python = str((Path(expected_root) / "python" / "python.exe").resolve())
        if os.path.normcase(executable) == os.path.normcase(expected_python):
            return True
    command_line = _process_command_line(pid).casefold()
    expected_root = expected_root.casefold()
    return bool(
        command_line
        and "main.py" in command_line
        and "--port" in command_line
        and str(int(record.get("port") or ENGINE_PORT)) in command_line
        and (not expected_root or expected_root in command_line)
    )


def process_status(*, probe: bool = True) -> Dict[str, Any]:
    record = _tracked_process()
    pid = int(record.get("pid") or 0)
    running = bool(record.get("managed") and _process_matches_record(record))
    ready = False
    error = ""
    if probe:
        try:
            _engine_request("/queue", timeout=1.5)
            if os.getenv("SYNCANVAS_NODE_ENGINE_URL"):
                ready = True
            elif running:
                ready = True
            else:
                error = f"端口 {int(record.get('port') or ENGINE_PORT)} 被未受 SynCanvas 管理的服务占用"
        except Exception as exc:
            error = str(exc)
    elif _PROCESS is not None and _PROCESS.poll() is None:
        ready = running
    return {
        "running": running,
        "ready": ready,
        "pid": pid or None,
        "port": int(record.get("port") or ENGINE_PORT),
        "runtime_root": str(record.get("runtime_root") or node_engine_component_service.runtime_root()),
        "managed": bool(record.get("managed")),
        "error": error,
    }


def _prepare_data_layout() -> None:
    for path in (DATA_ROOT, RUN_DIR, LOG_DIR, INPUT_DIR, OUTPUT_DIR, TEMP_DIR, USER_DIR, MODELS_DIR, CUSTOM_NODES_DIR, BRIDGE_DIR):
        path.mkdir(parents=True, exist_ok=True)
    if not BRIDGE_SOURCE.is_file():
        raise RuntimeError("SynCanvas 节点结果桥接器缺失")
    shutil.copy2(BRIDGE_SOURCE, BRIDGE_DIR / "__init__.py")
    node_engine_asset_service.write_extra_model_paths()


def _rotate_log(path: Path) -> None:
    try:
        if not path.exists() or path.stat().st_size < LOG_MAX_BYTES:
            return
        path.with_name(f"{path.name}.{LOG_BACKUP_COUNT}").unlink(missing_ok=True)
        for index in range(LOG_BACKUP_COUNT - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))
    except OSError:
        pass


async def _start_engine_unlocked(wait_seconds: int = 45) -> Dict[str, Any]:
    global _PROCESS
    current = process_status(probe=True)
    if current["ready"]:
        return {**current, "started": False}
    if "未受 SynCanvas 管理" in str(current.get("error") or ""):
        raise HTTPException(status_code=409, detail=current["error"])
    if os.getenv("SYNCANVAS_NODE_ENGINE_URL"):
        raise HTTPException(status_code=503, detail="配置的节点引擎测试地址不可用，SynCanvas 不会管理外部进程")
    component = node_engine_component_service.get_status()
    if not component.get("ready"):
        raise HTTPException(status_code=409, detail="节点引擎组件尚未安装")
    root = node_engine_component_service.runtime_root()
    python_exe = root / "python" / "python.exe"
    main_script = root / "main.py"
    if not python_exe.is_file() or not main_script.is_file():
        raise HTTPException(status_code=409, detail="节点引擎运行时不完整")
    # ComfyUI scans its built-in custom_nodes directory unconditionally during
    # startup. Managed imports intentionally exclude source extensions, so keep
    # the expected directory present while loading extensions from DATA_ROOT.
    (root / "custom_nodes").mkdir(parents=True, exist_ok=True)
    _prepare_data_layout()
    command = [
        str(python_exe),
        str(main_script),
        "--listen",
        ENGINE_HOST,
        "--port",
        str(ENGINE_PORT),
        "--disable-auto-launch",
        "--input-directory",
        str(INPUT_DIR.resolve()),
        "--output-directory",
        str(OUTPUT_DIR.resolve()),
        "--temp-directory",
        str(TEMP_DIR.resolve()),
        "--user-directory",
        str(USER_DIR.resolve()),
        "--database-url",
        f"sqlite:///{(USER_DIR / 'comfyui.db').resolve().as_posix()}",
        "--extra-model-paths-config",
        str(EXTRA_PATHS_FILE.resolve()),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    stdout_path = LOG_DIR / "engine.out.log"
    stderr_path = LOG_DIR / "engine.err.log"
    _rotate_log(stdout_path)
    _rotate_log(stderr_path)
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        _PROCESS = subprocess.Popen(
            command,
            cwd=str(root),
            stdout=stdout,
            stderr=stderr,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
    _atomic_json(
        PROCESS_FILE,
        {
            "pid": _PROCESS.pid,
            "port": ENGINE_PORT,
            "runtime_root": str(root),
            "managed": True,
            "started_at": _now_ms(),
        },
    )
    deadline = time.monotonic() + max(0, wait_seconds)
    last_error = ""
    while time.monotonic() <= deadline:
        if _PROCESS.poll() is not None:
            raise HTTPException(status_code=500, detail=f"节点引擎启动失败，退出码 {_PROCESS.returncode}；请查看 {stderr_path}")
        try:
            _engine_request("/queue", timeout=1.5)
            await asyncio.to_thread(scan_catalog, True)
            return {**process_status(probe=True), "started": True}
        except Exception as exc:
            last_error = str(exc)
            await asyncio.sleep(0.5)
    raise HTTPException(status_code=504, detail=f"节点引擎启动超时：{last_error}")


def _terminate_pid(pid: int) -> None:
    if pid <= 0 or not _process_exists(pid):
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20, check=False)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            os.kill(pid, signal.SIGTERM)


async def _stop_engine_unlocked() -> Dict[str, Any]:
    global _PROCESS
    record = _tracked_process()
    if not record.get("managed"):
        return {**process_status(probe=False), "stopped": False}
    pid = int(record.get("pid") or 0)
    if pid and not _process_matches_record(record):
        _atomic_json(PROCESS_FILE, {**record, "pid": None, "managed": False, "stopped_at": _now_ms()})
        return {**process_status(probe=False), "stopped": False, "error": "已跟踪进程身份不匹配，未执行结束操作"}
    await asyncio.to_thread(_terminate_pid, pid)
    _PROCESS = None
    _atomic_json(PROCESS_FILE, {**record, "pid": None, "managed": False, "stopped_at": _now_ms()})
    return {**process_status(probe=False), "stopped": True}


async def restart_engine(wait_seconds: int = 45) -> Dict[str, Any]:
    async with _lifecycle_lock():
        await _stop_engine_unlocked()
        return await _start_engine_unlocked(wait_seconds)


async def start_engine(wait_seconds: int = 45) -> Dict[str, Any]:
    async with _lifecycle_lock():
        return await _start_engine_unlocked(wait_seconds)


async def stop_engine() -> Dict[str, Any]:
    async with _lifecycle_lock():
        return await _stop_engine_unlocked()


def _frontend_packages(root: Path) -> set[str]:
    packages: set[str] = set()
    for custom_root in (root / "custom_nodes", CUSTOM_NODES_DIR):
        if not custom_root.is_dir():
            continue
        for package in custom_root.iterdir():
            if not package.is_dir():
                continue
            try:
                if any(path.is_file() for path in package.rglob("*.js")):
                    packages.add(package.name.casefold())
            except OSError:
                continue
    return packages


def _module_package(module: str) -> str:
    parts = str(module or "").split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "custom_nodes" else ""


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in str(value or ""))


def _read_translation_file(path: Path) -> Dict[str, Any]:
    try:
        if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _translation_candidates(custom_root: Path) -> List[Path]:
    if not custom_root.is_dir():
        return []
    patterns = (
        "*/zh-CN/Nodes/*.json",
        "*/zh_CN/Nodes/*.json",
        "*/locales/zh/nodeDefs.json",
        "*/locales/zh-CN/nodeDefs.json",
        "*/locales/zh_CN/nodeDefs.json",
    )
    result: List[Path] = []
    for pattern in patterns:
        result.extend(custom_root.glob(pattern))
    return sorted(set(result), key=lambda path: str(path).casefold())[:500]


def _load_chinese_catalog(runtime_root: Path) -> Dict[str, Any]:
    builtin = _read_translation_file(BUILTIN_I18N_FILE)
    result = {
        "nodes": deepcopy(builtin.get("nodes") or {}),
        "fields": deepcopy(builtin.get("fields") or {}),
        "categories": deepcopy(builtin.get("categories") or {}),
        "terms": deepcopy(builtin.get("terms") or {}),
        "sources": [str(BUILTIN_I18N_FILE)] if builtin else [],
    }
    candidates: List[Path] = []
    for custom_root in (runtime_root / "custom_nodes", CUSTOM_NODES_DIR):
        candidates.extend(_translation_candidates(custom_root))
    total_bytes = 0
    for path in sorted(set(candidates), key=lambda item: str(item).casefold()):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if total_bytes + size > 32 * 1024 * 1024:
            break
        payload = _read_translation_file(path)
        if not payload:
            continue
        total_bytes += size
        used = False
        for class_type, entry in payload.items():
            if not isinstance(entry, dict):
                continue
            title = entry.get("title") or entry.get("display_name")
            if not isinstance(title, str) or not _contains_chinese(title):
                continue
            normalized: Dict[str, Any] = {"title": title}
            for field in ("inputs", "widgets", "outputs"):
                values = entry.get(field)
                if isinstance(values, dict):
                    normalized[field] = values
            if isinstance(entry.get("description"), str):
                normalized["description"] = entry["description"]
            result["nodes"][str(class_type)] = normalized
            used = True
        if used:
            result["sources"].append(str(path))
    return result


def _split_display_words(value: str) -> List[str]:
    text = re.sub(r"[_/\\-]+", " ", str(value or ""))
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return [part for part in re.split(r"\s+", text.strip()) if part]


def _translate_readable_name(value: str, terms: Dict[str, str]) -> str:
    if _contains_chinese(value):
        return value
    translated: List[str] = []
    translated_count = 0
    for word in _split_display_words(value):
        replacement = terms.get(word.casefold())
        if replacement:
            translated.append(str(replacement))
            translated_count += 1
        else:
            translated.append(word)
    if not translated_count:
        return value
    return " ".join(translated)


def _translation_value(mapping: Any, key: str, index: int = -1) -> str:
    if not isinstance(mapping, dict):
        return ""
    value = mapping.get(key)
    if value is None and index >= 0:
        value = mapping.get(str(index))
    if isinstance(value, dict):
        value = value.get("name") or value.get("title")
    return str(value) if isinstance(value, str) and _contains_chinese(value) else ""


def _localized_category(value: str, categories: Dict[str, str], terms: Dict[str, str]) -> str:
    parts = re.split(r"([/\\])", str(value or "other"))
    localized = []
    for part in parts:
        if part in {"/", "\\"}:
            localized.append("/")
            continue
        direct = categories.get(part) or categories.get(part.casefold())
        localized.append(str(direct or _translate_readable_name(part, terms)))
    return "".join(localized)


def _apply_chinese_translation(definition: Dict[str, Any], catalog: Dict[str, Any]) -> Dict[str, Any]:
    node_translation = (catalog.get("nodes") or {}).get(definition["class_type"])
    node_translation = node_translation if isinstance(node_translation, dict) else {}
    terms = catalog.get("terms") if isinstance(catalog.get("terms"), dict) else {}
    fields = catalog.get("fields") if isinstance(catalog.get("fields"), dict) else {}
    original_name = str(definition.get("display_name") or definition["class_type"])
    title = str(node_translation.get("title") or "")
    definition["display_name_zh"] = title if _contains_chinese(title) else _translate_readable_name(original_name, terms)
    definition["display_name_en"] = original_name
    definition["category_zh"] = _localized_category(
        str(definition.get("category") or "other"),
        catalog.get("categories") if isinstance(catalog.get("categories"), dict) else {},
        terms,
    )
    if isinstance(node_translation.get("description"), str) and _contains_chinese(node_translation["description"]):
        definition["description_zh"] = node_translation["description"][:4000]
    for port in definition.get("inputs") or []:
        key = str(port.get("id") or "")
        section = "widgets" if port.get("widget", {}).get("enabled") else "inputs"
        translated = _translation_value(node_translation.get(section), key)
        if not translated:
            translated = _translation_value(node_translation.get("inputs"), key)
        port["name_zh"] = translated or str(fields.get(key) or _translate_readable_name(str(port.get("name") or key), terms))
    for index, port in enumerate(definition.get("outputs") or []):
        key = str(port.get("name") or port.get("raw_type") or "")
        translated = _translation_value(node_translation.get("outputs"), key, index)
        field_key = str(port.get("raw_type") or key).casefold()
        port["name_zh"] = translated or str(fields.get(field_key) or _translate_readable_name(key, terms))
    definition["translation"] = "catalog" if node_translation else "generated"
    return definition


def _port_type(raw_type: Any) -> str:
    text = str(raw_type or "*").upper()
    return {
        "IMAGE": "image",
        "MASK": "mask",
        "AUDIO": "audio",
        "VIDEO": "video",
        "STRING": "text",
        "INT": "number",
        "FLOAT": "number",
        "NUMBER": "number",
        "BOOLEAN": "boolean",
        "*": "any",
    }.get(text, f"comfy:{text}")


def _input_definition(input_id: str, spec: Any, required: bool, input_list: bool = False) -> Dict[str, Any]:
    values = spec if isinstance(spec, list) else [spec]
    type_spec = values[0] if values else "*"
    config = values[1] if len(values) > 1 and isinstance(values[1], dict) else {}
    options = type_spec if isinstance(type_spec, list) else []
    raw_type = "COMBO" if options else str(type_spec or "*")
    widget_type = "enum" if options else raw_type.upper()
    default = config.get("default")
    if default is None:
        if options:
            default = options[0] if options else ""
        elif widget_type == "BOOLEAN":
            default = False
        elif widget_type in {"INT", "FLOAT", "NUMBER"}:
            default = 0
        elif widget_type == "STRING":
            default = ""
    is_widget = bool(options) or widget_type in {"STRING", "INT", "FLOAT", "NUMBER", "BOOLEAN"}
    if config.get("forceInput") or config.get("force_input"):
        is_widget = False
    return {
        "id": str(input_id),
        "name": str(input_id),
        "types": [_port_type(options[0] if options else raw_type)],
        "raw_type": raw_type,
        "required": bool(required),
        "multiple": bool(config.get("multiple") or input_list),
        "widget": {
            "enabled": is_widget,
            "type": widget_type.lower(),
            "default": default,
            "min": config.get("min"),
            "max": config.get("max"),
            "step": config.get("step"),
            "multiline": bool(config.get("multiline")),
            "options": options,
            "tooltip": str(config.get("tooltip") or "")[:1000],
        },
    }


def _is_canvas_utility(definition: Dict[str, Any]) -> bool:
    explicit = definition.get("canvas_ready")
    if isinstance(explicit, bool):
        return explicit
    ports = list(definition.get("inputs") or []) + list(definition.get("outputs") or [])
    return all(str(port.get("raw_type") or "").upper() in CANVAS_UTILITY_TYPES for port in ports)


def _normalize_node(
    class_type: str,
    raw: Dict[str, Any],
    frontend_packages: set[str],
    translations: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    input_block = raw.get("input") if isinstance(raw.get("input"), dict) else {}
    input_list = bool(raw.get("is_input_list") or raw.get("input_is_list"))
    inputs: List[Dict[str, Any]] = []
    for input_id, spec in (input_block.get("required") or {}).items():
        inputs.append(_input_definition(str(input_id), spec, True, input_list))
    for input_id, spec in (input_block.get("optional") or {}).items():
        inputs.append(_input_definition(str(input_id), spec, False, input_list))
    output_types = raw.get("output") if isinstance(raw.get("output"), list) else []
    output_names = raw.get("output_name") if isinstance(raw.get("output_name"), list) else []
    output_lists = raw.get("output_is_list") if isinstance(raw.get("output_is_list"), list) else []
    outputs = []
    for index, output_type in enumerate(output_types):
        outputs.append(
            {
                "id": f"out-{index}",
                "name": str(output_names[index] if index < len(output_names) and output_names[index] else output_type),
                "types": [_port_type(output_type)],
                "raw_type": str(output_type or "*"),
                "required": False,
                "multiple": bool(output_lists[index]) if index < len(output_lists) else False,
                "index": index,
            }
        )
    module = str(raw.get("python_module") or "")
    package = _module_package(module)
    reasons: List[str] = []
    compatibility = "supported"
    if not isinstance(raw.get("input"), dict) or not isinstance(raw.get("output"), list):
        compatibility = "blocked"
        reasons.append("节点定义不符合标准 INPUT_TYPES/RETURN_TYPES 结构")
    elif package and package.casefold() in frontend_packages:
        compatibility = "limited"
        reasons.append("扩展包含 ComfyUI 专用前端脚本，SynCanvas 仅使用其后端节点")
    if raw.get("deprecated"):
        compatibility = "limited" if compatibility != "blocked" else compatibility
        reasons.append("节点已被上游标记为弃用")
    if raw.get("experimental"):
        compatibility = "limited" if compatibility != "blocked" else compatibility
        reasons.append("节点被上游标记为实验性")
    raw_for_hash = {
        "class_type": class_type,
        "input": raw.get("input"),
        "output": raw.get("output"),
        "output_name": raw.get("output_name"),
        "output_is_list": raw.get("output_is_list"),
    }
    fingerprint = hashlib.sha256(json.dumps(raw_for_hash, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]
    normalized = {
        "class_type": class_type,
        "display_name": str(raw.get("display_name") or raw.get("name") or class_type),
        "description": str(raw.get("description") or "")[:4000],
        "category": str(raw.get("category") or "other"),
        "python_module": module,
        "package": package,
        "compatibility": compatibility,
        "compatibility_reasons": reasons,
        "inputs": inputs,
        "outputs": outputs,
        "output_node": bool(raw.get("output_node")),
        "deprecated": bool(raw.get("deprecated")),
        "experimental": bool(raw.get("experimental")),
        "fingerprint": fingerprint,
    }
    normalized["canvas_ready"] = _is_canvas_utility(normalized)
    if translations:
        _apply_chinese_translation(normalized, translations)
    return normalized


def scan_catalog(force: bool = False) -> Dict[str, Any]:
    global _CATALOG, _CATALOG_META
    status = process_status(probe=True)
    if not status.get("ready"):
        raise RuntimeError(status.get("error") or "节点引擎尚未启动")
    raw = _engine_request("/object_info", timeout=60)
    if not isinstance(raw, dict):
        raise RuntimeError("节点引擎返回了无效的 object_info")
    runtime_root = node_engine_component_service.runtime_root()
    frontend_packages = _frontend_packages(runtime_root)
    translations = _load_chinese_catalog(runtime_root)
    catalog = {
        str(class_type): _normalize_node(str(class_type), definition, frontend_packages, translations)
        for class_type, definition in raw.items()
        if isinstance(definition, dict)
    }
    counts: Dict[str, int] = {"supported": 0, "limited": 0, "blocked": 0}
    utility_node_count = 0
    for definition in catalog.values():
        counts[definition["compatibility"]] = counts.get(definition["compatibility"], 0) + 1
        if _is_canvas_utility(definition):
            utility_node_count += 1
    translation_digest = hashlib.sha256(
        json.dumps(translations.get("nodes") or {}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    revision = hashlib.sha256(
        ("\n".join(f"{name}:{value['fingerprint']}" for name, value in sorted(catalog.items())) + translation_digest).encode("utf-8")
    ).hexdigest()[:20]
    meta = {
        "schema_version": 2,
        "revision": revision,
        "scanned_at": _now_ms(),
        "node_count": len(catalog),
        "utility_node_count": utility_node_count,
        "compatibility": counts,
        "runtime_root": str(runtime_root),
        "translation_locale": "zh-CN",
        "translation_source_count": len(translations.get("sources") or []),
    }
    payload = {"meta": meta, "nodes": catalog}
    if _json_size(payload) > MAX_CATALOG_BYTES:
        raise RuntimeError("节点目录超过 64 MB 安全上限")
    _atomic_json(CATALOG_FILE, payload)
    _CATALOG = catalog
    _CATALOG_META = meta
    return deepcopy(meta)


def load_catalog() -> Dict[str, Any]:
    global _CATALOG, _CATALOG_META
    def matches_current_runtime(meta: Dict[str, Any]) -> bool:
        if int(meta.get("schema_version") or 0) < 2:
            return False
        if os.getenv("SYNCANVAS_NODE_ENGINE_URL"):
            return True
        root = node_engine_component_service.runtime_root().resolve()
        if not (root / "main.py").is_file() or not (root / "python" / "python.exe").is_file():
            return False
        actual = str(meta.get("runtime_root") or "").strip()
        if not actual:
            return False
        try:
            return Path(actual).expanduser().resolve() == root
        except OSError:
            return False

    if (
        _CATALOG
        and (_CATALOG_META.get("schema_version") or _CATALOG_META.get("runtime_root"))
        and not matches_current_runtime(_CATALOG_META)
    ):
        _CATALOG = {}
        _CATALOG_META = {}
    if not _CATALOG:
        payload = _read_json(CATALOG_FILE, {}) or {}
        nodes = payload.get("nodes") if isinstance(payload, dict) else None
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        if isinstance(nodes, dict) and matches_current_runtime(meta):
            _CATALOG = nodes
            _CATALOG_META = meta
    return {"meta": deepcopy(_CATALOG_META), "nodes": _CATALOG}


def invalidate_catalog() -> None:
    global _CATALOG, _CATALOG_META
    _CATALOG = {}
    _CATALOG_META = {}
    CATALOG_FILE.unlink(missing_ok=True)


def _require_catalog() -> Dict[str, Dict[str, Any]]:
    load_catalog()
    if not _CATALOG:
        try:
            scan_catalog()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"节点目录不可用：{exc}") from exc
    return _CATALOG


def catalog_categories(scope: str = "utility") -> Dict[str, Any]:
    catalog = _require_catalog()
    groups: Dict[str, Dict[str, int]] = {}
    for definition in catalog.values():
        if scope != "all" and not _is_canvas_utility(definition):
            continue
        category = definition.get("category") or "other"
        row = groups.setdefault(category, {"count": 0, "supported": 0, "limited": 0, "blocked": 0})
        row["count"] += 1
        row[definition.get("compatibility") or "blocked"] += 1
    categories = [
        {
            "name": name,
            "display_name_zh": next(
                (item.get("category_zh") for item in catalog.values() if item.get("category") == name),
                name,
            ),
            **values,
        }
        for name, values in groups.items()
    ]
    categories.sort(key=lambda item: (-item["count"], item["name"].casefold()))
    return {
        "revision": _CATALOG_META.get("revision", ""),
        "scope": scope,
        "node_count": sum(item["count"] for item in categories),
        "categories": categories,
    }


def search_catalog(
    query: str = "",
    category: str = "",
    compatibility: str = "",
    page: int = 1,
    page_size: int = 50,
    scope: str = "utility",
) -> Dict[str, Any]:
    catalog = _require_catalog()
    needle = str(query or "").strip().casefold()
    filtered = []
    for definition in catalog.values():
        if scope != "all" and not _is_canvas_utility(definition):
            continue
        if category and definition.get("category") != category:
            continue
        if compatibility and definition.get("compatibility") != compatibility:
            continue
        haystack = " ".join(
            str(definition.get(key) or "")
            for key in (
                "class_type",
                "display_name",
                "display_name_zh",
                "description",
                "description_zh",
                "category",
                "category_zh",
                "package",
            )
        ) + " " + " ".join(
            str(port.get(key) or "")
            for port in [*(definition.get("inputs") or []), *(definition.get("outputs") or [])]
            for key in ("id", "name", "name_zh", "raw_type")
        )
        haystack = haystack.casefold()
        if needle and needle not in haystack:
            continue
        filtered.append(definition)
    filtered.sort(key=lambda item: (item.get("display_name", "").casefold(), item.get("class_type", "").casefold()))
    total = len(filtered)
    start = max(0, (max(1, page) - 1) * max(1, page_size))
    summaries = []
    for definition in filtered[start : start + page_size]:
        summaries.append({key: deepcopy(definition.get(key)) for key in (
            "class_type",
            "display_name",
            "display_name_en",
            "display_name_zh",
            "description",
            "description_zh",
            "category",
            "category_zh",
            "package",
            "compatibility",
            "compatibility_reasons",
            "canvas_ready",
            "translation",
            "fingerprint",
        )})
    return {
        "revision": _CATALOG_META.get("revision", ""),
        "scope": scope,
        "page": max(1, page),
        "page_size": page_size,
        "total": total,
        "items": summaries,
    }


def get_definition(class_type: str) -> Dict[str, Any]:
    definition = _require_catalog().get(class_type)
    if not definition:
        raise HTTPException(status_code=404, detail=f"运行时节点不存在：{class_type}")
    return deepcopy(definition)


def _raw_compatible(output_type: str, input_type: str) -> bool:
    left = str(output_type or "*").upper()
    right = str(input_type or "*").upper()
    if "*" in {left, right}:
        return True
    if left == right:
        return True
    return left in {"INT", "FLOAT"} and right == "NUMBER"


def _coerce_scalar(value: Any, raw_type: str) -> Any:
    if isinstance(value, dict) and "value" in value:
        value = value.get("value")
    target = str(raw_type or "").upper()
    try:
        if target == "INT":
            return int(float(value))
        if target in {"FLOAT", "NUMBER"}:
            return float(value)
        if target == "BOOLEAN":
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if target == "STRING":
            return str(value if value is not None else "")
    except (TypeError, ValueError):
        return value
    return value


def _topological_order(node_ids: Iterable[str], connections: List[Dict[str, Any]]) -> List[str]:
    ids = list(node_ids)
    incoming = {node_id: 0 for node_id in ids}
    outgoing = {node_id: [] for node_id in ids}
    for connection in connections:
        source = connection["from_node"]
        target = connection["to_node"]
        if source in incoming and target in incoming:
            incoming[target] += 1
            outgoing[source].append(target)
    queue = [node_id for node_id in ids if incoming[node_id] == 0]
    order = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for target in outgoing[current]:
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    if len(order) != len(ids):
        raise HTTPException(status_code=422, detail="运行时节点图包含循环连接")
    return order


async def _upload_boundary_file(run_id: str, value: Any, kind: str, fallback_suffix: str) -> str:
    url = value.get("value") if isinstance(value, dict) and "value" in value else value
    path = output_file_from_url(url)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=422, detail=f"运行时{kind}输入必须来自 SynCanvas 本地素材")
    source = Path(path)
    filename = f"syncanvas_{run_id[:10]}_{uuid.uuid4().hex[:8]}{source.suffix.lower() or fallback_suffix}"
    async with httpx.AsyncClient(timeout=30) as client:
        with source.open("rb") as handle:
            response = await client.post(
                f"{engine_base_url()}/upload/image",
                files={"image": (filename, handle, content_type_for_path(str(source)))},
                data={"type": "input", "overwrite": "true"},
            )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"节点引擎拒绝{kind}输入：{response.text[:500]}")
    payload = response.json()
    return str(payload.get("name") or filename)


async def _upload_boundary_image(run_id: str, value: Any) -> str:
    return await _upload_boundary_file(run_id, value, "图片", ".png")


def _node_default_inputs(definition: Dict[str, Any]) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    for port in definition.get("inputs") or []:
        widget = port.get("widget") or {}
        if widget.get("enabled") and "default" in widget:
            values[port["id"]] = deepcopy(widget.get("default"))
    return values


async def compile_graph(payload: RuntimeGraphRunRequest, run_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    catalog = _require_catalog()
    body = _model_dump(payload)
    if _json_size(body) > MAX_GRAPH_BYTES:
        raise HTTPException(status_code=413, detail="运行时节点图超过 8 MB")
    nodes = body["nodes"]
    node_map = {node["id"]: node for node in nodes}
    if len(node_map) != len(nodes):
        raise HTTPException(status_code=422, detail="运行时节点 ID 重复")
    definitions: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        definition = catalog.get(node["class_type"])
        if not definition:
            raise HTTPException(status_code=422, detail=f"节点类型不可用：{node['class_type']}")
        if definition.get("compatibility") == "blocked":
            raise HTTPException(status_code=422, detail=f"节点被兼容层阻止：{node['class_type']}")
        expected = str(node.get("definition_fingerprint") or "")
        if expected and expected != definition.get("fingerprint"):
            raise HTTPException(status_code=409, detail=f"节点定义已变化，请刷新节点：{node['class_type']}")
        definitions[node["id"]] = definition
    connections = body.get("connections") or []
    _topological_order(node_map, connections)
    occupied_inputs: set[Tuple[str, str]] = set()
    for connection in connections:
        source_id, target_id = connection["from_node"], connection["to_node"]
        if source_id not in node_map or target_id not in node_map:
            raise HTTPException(status_code=422, detail="连接引用了运行时节点岛之外的节点")
        source_port = next((port for port in definitions[source_id]["outputs"] if port["id"] == connection["from_port"]), None)
        target_port = next((port for port in definitions[target_id]["inputs"] if port["id"] == connection["to_port"]), None)
        if not source_port or not target_port:
            raise HTTPException(status_code=422, detail="连接引用了不存在的运行时端口")
        if not _raw_compatible(source_port["raw_type"], target_port["raw_type"]):
            raise HTTPException(status_code=422, detail=f"端口类型不兼容：{source_port['raw_type']} -> {target_port['raw_type']}")
        key = (target_id, target_port["id"])
        if key in occupied_inputs and not target_port.get("multiple"):
            raise HTTPException(status_code=422, detail=f"输入端口只允许一条连接：{target_id}.{target_port['id']}")
        occupied_inputs.add(key)
    external_inputs = body.get("external_inputs") or []
    for item in external_inputs:
        target_id = item["to_node"]
        if target_id not in node_map:
            raise HTTPException(status_code=422, detail="外部输入引用了不存在的运行时节点")
        target_port = next(
            (port for port in definitions[target_id]["inputs"] if port["id"] == item["to_port"]),
            None,
        )
        if not target_port:
            raise HTTPException(status_code=422, detail=f"外部输入端口不存在：{item['to_port']}")
        key = (target_id, target_port["id"])
        if key in occupied_inputs and not target_port.get("multiple"):
            raise HTTPException(status_code=422, detail=f"输入端口只允许一个来源：{target_id}.{target_port['id']}")
        occupied_inputs.add(key)
    numeric_ids = {node_id: str(index + 1) for index, node_id in enumerate(node_map)}
    prompt: Dict[str, Any] = {}
    for node in nodes:
        definition = definitions[node["id"]]
        input_modes = node.get("input_modes") or {}
        values = {
            key: value
            for key, value in (node.get("widgets") or {}).items()
            if input_modes.get(key, "widget") != "port"
        }
        for port in definition["inputs"]:
            widget = port.get("widget") or {}
            if (
                port["id"] not in values
                and input_modes.get(port["id"], "widget") != "port"
                and widget.get("enabled")
                and "default" in widget
            ):
                values[port["id"]] = deepcopy(widget.get("default"))
        prompt[numeric_ids[node["id"]]] = {"class_type": node["class_type"], "inputs": values}
    assignment_counts: Dict[Tuple[str, str], int] = {}

    def assign_input(target_id: str, target_port: Dict[str, Any], value: Any) -> None:
        port_id = target_port["id"]
        inputs = prompt[numeric_ids[target_id]]["inputs"]
        key = (target_id, port_id)
        count = assignment_counts.get(key, 0)
        if target_port.get("multiple") and count:
            current = inputs.get(port_id)
            if count == 1:
                inputs[port_id] = [current, value]
            else:
                current.append(value)
        else:
            inputs[port_id] = value
        assignment_counts[key] = count + 1

    for connection in connections:
        source_definition = definitions[connection["from_node"]]
        source_port = next(port for port in source_definition["outputs"] if port["id"] == connection["from_port"])
        target_port = next(port for port in definitions[connection["to_node"]]["inputs"] if port["id"] == connection["to_port"])
        reference = [numeric_ids[connection["from_node"]], int(source_port["index"])]
        assign_input(connection["to_node"], target_port, reference)
    next_id = len(prompt) + 1
    for item in external_inputs:
        target_definition = definitions[item["to_node"]]
        target_port = next(port for port in target_definition["inputs"] if port["id"] == item["to_port"])
        raw_type = target_port["raw_type"].upper()
        kind = str(item.get("kind") or "json").lower()
        if kind == "image" and raw_type in {"IMAGE", "MASK"}:
            image_name = await _upload_boundary_image(run_id, item.get("value"))
            loader_id = str(next_id)
            next_id += 1
            prompt[loader_id] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
            assign_input(item["to_node"], target_port, [loader_id, 1 if raw_type == "MASK" else 0])
        elif kind in {"audio", "video"} and raw_type == "AUDIO" and "LoadAudio" in catalog:
            media_name = await _upload_boundary_file(run_id, item.get("value"), "音频", ".wav")
            loader_id = str(next_id)
            next_id += 1
            prompt[loader_id] = {"class_type": "LoadAudio", "inputs": {"audio": media_name}}
            assign_input(item["to_node"], target_port, [loader_id, 0])
        elif kind == "video" and raw_type == "VIDEO" and "LoadVideo" in catalog:
            media_name = await _upload_boundary_file(run_id, item.get("value"), "视频", ".mp4")
            loader_id = str(next_id)
            next_id += 1
            prompt[loader_id] = {"class_type": "LoadVideo", "inputs": {"file": media_name}}
            assign_input(item["to_node"], target_port, [loader_id, 0])
        elif raw_type in SERIAL_TYPES or raw_type == "COMBO":
            assign_input(item["to_node"], target_port, _coerce_scalar(item.get("value"), raw_type))
        else:
            raise HTTPException(status_code=422, detail=f"类型 {raw_type} 不能从普通 SynCanvas 节点输入")
    for node in nodes:
        definition = definitions[node["id"]]
        inputs = prompt[numeric_ids[node["id"]]]["inputs"]
        missing = [port["id"] for port in definition["inputs"] if port.get("required") and port["id"] not in inputs]
        if missing:
            raise HTTPException(status_code=422, detail=f"{node['class_type']} 缺少必填输入：{', '.join(missing)}")
    targets = [node_id for node_id in body.get("target_ids") or [] if node_id in node_map] or [nodes[-1]["id"]]
    collectors: List[Dict[str, Any]] = []
    sink_count = 0
    for target_id in targets:
        definition = definitions[target_id]
        target_numeric = numeric_ids[target_id]
        if definition.get("output_node"):
            collectors.append({"kind": "native", "node_id": target_numeric, "target_id": target_id, "port_id": ""})
            sink_count += 1
        for port in definition["outputs"]:
            raw_type = port["raw_type"].upper()
            source_ref = [target_numeric, int(port["index"])]
            if raw_type == "IMAGE" and "SaveImage" in catalog:
                sink_id = str(next_id)
                next_id += 1
                prompt[sink_id] = {"class_type": "SaveImage", "inputs": {"images": source_ref, "filename_prefix": f"syncanvas/{run_id}"}}
                collectors.append({"kind": "image", "node_id": sink_id, "target_id": target_id, "port_id": port["id"]})
                sink_count += 1
            elif raw_type == "MASK" and "MaskToImage" in catalog and "SaveImage" in catalog:
                convert_id = str(next_id)
                next_id += 1
                sink_id = str(next_id)
                next_id += 1
                prompt[convert_id] = {"class_type": "MaskToImage", "inputs": {"mask": source_ref}}
                prompt[sink_id] = {"class_type": "SaveImage", "inputs": {"images": [convert_id, 0], "filename_prefix": f"syncanvas/{run_id}"}}
                collectors.append({"kind": "image", "node_id": sink_id, "target_id": target_id, "port_id": port["id"]})
                sink_count += 1
            elif raw_type in SERIAL_TYPES and "SynCanvasResult" in catalog:
                sink_id = str(next_id)
                next_id += 1
                prompt[sink_id] = {"class_type": "SynCanvasResult", "inputs": {"value": source_ref}}
                collectors.append({"kind": "scalar", "node_id": sink_id, "target_id": target_id, "port_id": port["id"]})
                sink_count += 1
            elif raw_type == "AUDIO" and "SaveAudio" in catalog:
                sink_id = str(next_id)
                next_id += 1
                values = _node_default_inputs(catalog["SaveAudio"])
                values.update({"audio": source_ref, "filename_prefix": f"syncanvas/{run_id}"})
                prompt[sink_id] = {"class_type": "SaveAudio", "inputs": values}
                collectors.append({"kind": "audio", "node_id": sink_id, "target_id": target_id, "port_id": port["id"]})
                sink_count += 1
            elif raw_type == "VIDEO" and "SaveVideo" in catalog:
                sink_id = str(next_id)
                next_id += 1
                values = _node_default_inputs(catalog["SaveVideo"])
                values.update({"video": source_ref, "filename_prefix": f"syncanvas/{run_id}"})
                prompt[sink_id] = {"class_type": "SaveVideo", "inputs": values}
                collectors.append({"kind": "video", "node_id": sink_id, "target_id": target_id, "port_id": port["id"]})
                sink_count += 1
    if not sink_count:
        raise HTTPException(status_code=422, detail="目标节点没有可执行的输出；请选择输出节点或可预览的图片/标量输出")
    return prompt, collectors


class RuntimeGraphRunManager:
    def __init__(self):
        self.records: Dict[str, Dict[str, Any]] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.cancel_events: Dict[str, asyncio.Event] = {}
        self.execution_lock = asyncio.Lock()

    def recover(self) -> None:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        for path in RUN_DIR.glob("*.json"):
            record = _read_json(path, {}) or {}
            if not record.get("run_id"):
                continue
            if record.get("status") not in TERMINAL_RUN_STATES:
                record.update({"status": "interrupted", "error": "SynCanvas 重启时任务仍在运行", "completed_at": _now_ms()})
                _atomic_json(path, record)
            self.records[record["run_id"]] = record
        prune_run_history(RUN_DIR, self.records, TERMINAL_RUN_STATES)

    def _persist(self, record: Dict[str, Any]) -> None:
        _atomic_json(RUN_DIR / f"{record['run_id']}.json", record)

    async def _broadcast(self, record: Dict[str, Any], event_type: str) -> None:
        try:
            from app import legacy

            await legacy.manager.broadcast_message(
                {
                    "type": event_type,
                    "run_id": record["run_id"],
                    "canvas_id": record.get("canvas_id") or "",
                    "status": record.get("status") or "",
                    "progress": record.get("progress") or 0,
                    "message": record.get("message") or "",
                    "node_id": record.get("active_node_id") or "",
                    "node_progress": deepcopy(record.get("node_progress") or {}),
                }
            )
        except Exception:
            logging.debug("Unable to broadcast runtime graph event", exc_info=True)

    def submit(self, payload: RuntimeGraphRunRequest) -> Dict[str, Any]:
        pending = sum(1 for record in self.records.values() if record.get("status") not in TERMINAL_RUN_STATES)
        if pending >= MAX_QUEUED_RUNS:
            raise HTTPException(status_code=429, detail=f"节点引擎队列已满（最多 {MAX_QUEUED_RUNS} 个）")
        run_id = uuid.uuid4().hex
        record = {
            "run_id": run_id,
            "canvas_id": payload.canvas_id,
            "client_id": payload.client_id,
            "status": "queued",
            "progress": 0.0,
            "message": "等待节点引擎",
            "created_at": _now_ms(),
            "started_at": None,
            "completed_at": None,
            "duration_ms": None,
            "prompt_id": "",
            "active_node_id": "",
            "active_class_type": "",
            "node_progress": {},
            "result": None,
            "error": "",
        }
        self.records[run_id] = record
        self.cancel_events[run_id] = asyncio.Event()
        self._persist(record)
        task = asyncio.create_task(self._execute(run_id, payload))
        self.tasks[run_id] = task
        task.add_done_callback(lambda _task, rid=run_id: self.tasks.pop(rid, None))
        return deepcopy(record)

    async def _apply_engine_event(
        self,
        record: Dict[str, Any],
        event: Dict[str, Any],
        prompt_id: str,
        node_id_map: Dict[str, str],
        class_type_map: Dict[str, str],
        completed: set[str],
    ) -> None:
        event_type = str(event.get("type") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        event_prompt = str(data.get("prompt_id") or data.get("promptId") or "")
        if event_prompt and event_prompt != prompt_id:
            return
        changed = False
        active_numeric = str(data.get("node") or data.get("node_id") or "")
        active_canvas = node_id_map.get(active_numeric, "")
        if event_type == "executing":
            previous = str(record.get("active_node_id") or "")
            if previous and previous != active_canvas:
                completed.add(previous)
                record.setdefault("node_progress", {}).setdefault(previous, {}).update({"status": "succeeded", "progress": 1.0})
            if active_canvas:
                class_type = class_type_map.get(active_canvas, "")
                status = "loading_model" if any(token in class_type.casefold() for token in ("loader", "checkpoint", "unet", "vae")) else "running"
                record.update({
                    "active_node_id": active_canvas,
                    "active_class_type": class_type,
                    "status": status,
                    "message": "正在加载模型" if status == "loading_model" else f"正在执行 {class_type or active_canvas}",
                })
                record.setdefault("node_progress", {}).setdefault(active_canvas, {}).update({
                    "status": status,
                    "progress": 0.0,
                    "class_type": class_type,
                })
            changed = True
        elif event_type == "progress":
            value = float(data.get("value") or 0)
            maximum = max(1.0, float(data.get("max") or 1))
            fraction = max(0.0, min(1.0, value / maximum))
            target = active_canvas or str(record.get("active_node_id") or "")
            if target:
                record.setdefault("node_progress", {}).setdefault(target, {}).update({"status": "running", "progress": fraction})
            total_nodes = max(1, len(node_id_map))
            record["progress"] = min(0.98, 0.06 + 0.9 * (len(completed) + fraction) / total_nodes)
            record["message"] = f"节点进度 {int(value)}/{int(maximum)}"
            changed = True
        elif event_type == "execution_cached":
            for numeric in data.get("nodes") or []:
                canvas_id = node_id_map.get(str(numeric), "")
                if canvas_id:
                    completed.add(canvas_id)
                    record.setdefault("node_progress", {}).setdefault(canvas_id, {}).update({"status": "cached", "progress": 1.0})
            changed = True
        elif event_type in {"execution_error", "execution_interrupted"}:
            message = data.get("exception_message") or data.get("message") or event_type
            record["engine_error"] = str(message)
            changed = True
        elif event_type in {"execution_success", "execution_complete"}:
            record["progress"] = max(float(record.get("progress") or 0), 0.98)
            changed = True
        if changed:
            self._persist(record)
            await self._broadcast(record, "runtime_node_progress")

    async def _download_result_image(self, run_id: str, item: Dict[str, Any]) -> str:
        params = urllib.parse.urlencode(
            {
                "filename": str(item.get("filename") or ""),
                "subfolder": str(item.get("subfolder") or ""),
                "type": str(item.get("type") or "output"),
            }
        )
        extension = Path(str(item.get("filename") or "result.png")).suffix.lower() or ".png"
        output_root = Path(BASE_DIR) / "assets" / "output" / "runtime" / run_id
        output_root.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:12]}{extension}"
        target = output_root / filename
        temporary = target.with_suffix(target.suffix + ".tmp")
        total = 0
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream("GET", f"{engine_base_url()}/view?{params}") as response:
                    if response.status_code >= 400:
                        raise RuntimeError(f"无法读取节点引擎输出：HTTP {response.status_code}")
                    content_length = int(response.headers.get("content-length") or 0)
                    if content_length > UPLOAD_FILE_MAX_BYTES:
                        raise RuntimeError("节点引擎输出超过 500 MiB")
                    with temporary.open("wb") as output:
                        async for chunk in response.aiter_bytes(1024 * 1024):
                            total += len(chunk)
                            if total > UPLOAD_FILE_MAX_BYTES:
                                raise RuntimeError("节点引擎输出超过 500 MiB")
                            output.write(chunk)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return f"/assets/output/runtime/{run_id}/{filename}"

    async def _collect_result(self, run_id: str, history: Dict[str, Any], collectors: List[Dict[str, Any]]) -> Dict[str, Any]:
        outputs = history.get("outputs") if isinstance(history, dict) else {}
        normalized: Dict[str, List[Dict[str, Any]]] = {}
        images: List[str] = []
        audio: List[str] = []
        videos: List[str] = []
        structured = None
        output_text = ""
        for collector in collectors:
            row = outputs.get(collector["node_id"], {}) if isinstance(outputs, dict) else {}
            key = f"{collector['target_id']}:{collector['port_id'] or 'output'}"
            values: List[Dict[str, Any]] = []
            media_rows = [("images", "image"), ("audio", "audio"), ("videos", "video"), ("video", "video")]
            for field, default_kind in media_rows:
                for media in row.get(field) or []:
                    url = await self._download_result_image(run_id, media)
                    media_kind = collector.get("kind") if collector.get("kind") in {"image", "audio", "video"} else default_kind
                    if media_kind == "audio":
                        audio.append(url)
                    elif media_kind == "video":
                        videos.append(url)
                    else:
                        images.append(url)
                    values.append({"kind": media_kind, "value": url, "metadata": deepcopy(media)})
            for item in row.get("syncanvas") or []:
                raw_value = item.get("value") if isinstance(item, dict) else item
                try:
                    value = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
                except ValueError:
                    value = raw_value
                if isinstance(value, str) and not output_text:
                    output_text = value
                if structured is None:
                    structured = value
                values.append({"kind": "text" if isinstance(value, str) else "json", "value": value, "metadata": {}})
            if values:
                normalized.setdefault(key, []).extend(values)
        return {
            "outputs": normalized,
            "images": images,
            "audio": audio,
            "videos": videos,
            "output_text": output_text,
            "structured_output": structured,
        }

    async def _execute(self, run_id: str, payload: RuntimeGraphRunRequest) -> None:
        record = self.records[run_id]
        cancel_event = self.cancel_events[run_id]
        gpu_owner = ""
        ws_task: Optional[asyncio.Task] = None
        try:
            async with self.execution_lock:
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                record.update({"status": "compiling", "progress": 0.02, "message": "正在校验并编译运行时节点图", "started_at": _now_ms()})
                self._persist(record)
                await self._broadcast(record, "runtime_run_progress")
                prompt, collectors = await compile_graph(payload, run_id)
                from app.services import digital_human_service

                record.update({"status": "queued", "progress": 0.04, "message": "等待共享 GPU 资源"})
                self._persist(record)
                await self._broadcast(record, "runtime_run_progress")
                gpu_owner = await digital_human_service.acquire_digital_human_resource(
                    "node-engine", run_id, "", "node-engine"
                )
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                if not process_status(probe=True).get("ready"):
                    await start_engine(60)
                record.update({"status": "running", "progress": 0.05, "message": "节点引擎正在执行"})
                self._persist(record)
                await self._broadcast(record, "runtime_run_progress")
                client_id = f"{payload.client_id or 'syncanvas'}-{run_id}"
                event_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
                ws_ready = asyncio.Event()
                ws_task = asyncio.create_task(_consume_engine_events(client_id, event_queue, ws_ready))
                try:
                    await asyncio.wait_for(ws_ready.wait(), timeout=5.5)
                except asyncio.TimeoutError:
                    pass
                submit = await asyncio.to_thread(
                    _engine_request,
                    "/prompt",
                    "POST",
                    {"prompt": prompt, "client_id": client_id},
                    30,
                )
                prompt_id = str(submit.get("prompt_id") or "")
                if not prompt_id:
                    raise RuntimeError(f"节点引擎没有返回 prompt_id：{submit}")
                record["prompt_id"] = prompt_id
                self._persist(record)
                timeout_seconds = max(30, int(os.getenv("SYNCANVAS_NODE_ENGINE_RUN_TIMEOUT", "1800")))
                deadline = time.monotonic() + timeout_seconds
                history = None
                node_id_map = {str(index + 1): node.id for index, node in enumerate(payload.nodes)}
                class_type_map = {node.id: node.class_type for node in payload.nodes}
                completed_nodes: set[str] = set()
                while time.monotonic() < deadline:
                    if cancel_event.is_set():
                        await asyncio.to_thread(_engine_request, "/interrupt", "POST", {}, 10)
                        raise asyncio.CancelledError
                    while True:
                        try:
                            event = event_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        await self._apply_engine_event(
                            record,
                            event,
                            prompt_id,
                            node_id_map,
                            class_type_map,
                            completed_nodes,
                        )
                    if record.get("engine_error"):
                        raise RuntimeError(f"节点引擎执行失败：{record['engine_error']}")
                    response = await asyncio.to_thread(_engine_request, f"/history/{urllib.parse.quote(prompt_id)}", "GET", None, 15)
                    if isinstance(response, dict) and prompt_id in response:
                        history = response[prompt_id]
                        break
                    await asyncio.sleep(0.25)
                if history is None:
                    raise TimeoutError(f"节点引擎执行超过 {timeout_seconds} 秒")
                status = history.get("status") if isinstance(history, dict) else {}
                if isinstance(status, dict) and status.get("status_str") == "error":
                    messages = status.get("messages") or []
                    raise RuntimeError(f"节点引擎执行失败：{messages[-1] if messages else status}")
                record["result"] = await self._collect_result(run_id, history, collectors)
                node_progress = record.setdefault("node_progress", {})
                for node in payload.nodes:
                    row = node_progress.setdefault(node.id, {})
                    if row.get("status") != "cached":
                        row["status"] = "succeeded"
                    row.update({"progress": 1.0, "class_type": node.class_type})
                record.update({
                    "status": "succeeded",
                    "progress": 1.0,
                    "message": "运行完成",
                    "active_node_id": "",
                    "active_class_type": "",
                })
        except asyncio.CancelledError:
            active_node_id = str(record.get("active_node_id") or "")
            if active_node_id:
                record.setdefault("node_progress", {}).setdefault(active_node_id, {}).update({"status": "cancelled"})
            record.update({
                "status": "cancelled",
                "error": "运行已取消",
                "message": "运行已取消",
                "active_node_id": "",
                "active_class_type": "",
            })
        except Exception as exc:
            logging.exception("Runtime graph run failed: %s", run_id)
            active_node_id = str(record.get("active_node_id") or "")
            if active_node_id:
                record.setdefault("node_progress", {}).setdefault(active_node_id, {}).update({"status": "failed"})
            record.update({
                "status": "failed",
                "error": redact_sensitive_text(exc)[:10000],
                "message": "运行失败",
                "active_node_id": "",
                "active_class_type": "",
            })
        finally:
            if ws_task is not None:
                ws_task.cancel()
                await asyncio.gather(ws_task, return_exceptions=True)
            if gpu_owner:
                try:
                    from app.services import digital_human_service

                    digital_human_service.release_digital_human_resource("node-engine", run_id, gpu_owner)
                except Exception:
                    logging.exception("Unable to release shared GPU resource for %s", run_id)
            record["completed_at"] = _now_ms()
            if record.get("started_at"):
                record["duration_ms"] = record["completed_at"] - record["started_at"]
            self._persist(record)
            await self._broadcast(record, "runtime_run_finished")
            prune_run_history(RUN_DIR, self.records, TERMINAL_RUN_STATES)

    def get(self, run_id: str) -> Dict[str, Any]:
        record = self.records.get(run_id)
        if not record:
            record = _read_json(RUN_DIR / f"{run_id}.json", {}) or {}
            if record.get("run_id"):
                self.records[run_id] = record
        if not record:
            raise HTTPException(status_code=404, detail="运行时任务不存在")
        return deepcopy(record)

    def cancel(self, run_id: str) -> Dict[str, Any]:
        record = self.get(run_id)
        if record.get("status") in TERMINAL_RUN_STATES:
            return record
        event = self.cancel_events.get(run_id)
        if event:
            event.set()
        current = self.records[run_id]
        current["message"] = "正在取消运行"
        self._persist(current)
        return deepcopy(current)


run_manager = RuntimeGraphRunManager()


def initialize() -> None:
    node_engine_asset_service.initialize()
    _prepare_data_layout()
    load_catalog()
    run_manager.recover()


async def shutdown() -> None:
    for event in run_manager.cancel_events.values():
        event.set()
    await stop_engine()
