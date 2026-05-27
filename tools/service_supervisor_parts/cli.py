from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def find_project_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in (current.parent, *current.parents):
        if (candidate / "main.py").exists() and (candidate / "tools").is_dir():
            return candidate
    return current.parents[2] if len(current.parents) > 2 else current.parent


BASE_DIR = find_project_root()
DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "service-logs"
RUNTIME_FILE = DATA_DIR / "service_supervisor.json"
LAUNCHER_CONFIG_FILE = DATA_DIR / "launcher_config.json"
DIGITAL_HUMAN_CONFIG_FILE = DATA_DIR / "digital_human_config.json"
MAIN_URL = "http://127.0.0.1:3000/"
LAUNCHER_PORT = 2999
MIN_FREE_BYTES = 10 * 1024 * 1024 * 1024
CUDA_PROBE_CACHE: Dict[str, Dict] = {}
CUDA_PROBE_TTL_SECONDS = 300


def default_launcher_config() -> Dict:
    return {
        "launcher": {
            "port": 2999,
            "open_main_after_ready": False,
        },
        "main": {
            "base_url": "http://127.0.0.1:3000/",
            "port": 3000,
            "python_path": str(BASE_DIR / "python" / "python.exe"),
            "script_path": str(BASE_DIR / "main.py"),
        },
        "tts": {
            "base_url": "http://127.0.0.1:7861/",
            "port": 7861,
            "root_dir": str(BASE_DIR / "index-tts-2"),
            "python_path": str(BASE_DIR / "index-tts-2" / "py312" / "python.exe"),
            "script_path": str(BASE_DIR / "index-tts-2" / "app.py"),
        },
        "heygem": {
            "base_url": "http://127.0.0.1:7860/",
            "port": 7860,
            "api_base_url": "http://127.0.0.1:8383/",
            "api_port": 8383,
            "root_dir": str(BASE_DIR / "heygem-win-fix" / "heygem-win"),
            "python_path": str(BASE_DIR / "heygem-win-fix" / "heygem-win" / "py38" / "python.exe"),
            "script_path": str(BASE_DIR / "heygem-win-fix" / "heygem-win" / "app.py"),
        },
    }


def merge_dict(base: Dict, override: Dict) -> Dict:
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def normalize_url(value: str, fallback: str) -> str:
    text = str(value or fallback).strip() or fallback
    if not text.startswith(("http://", "https://")):
        text = "http://" + text
    return text.rstrip("/") + "/"


def prefer_ipv4_loopback_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlparse(value)
        host = (parsed.hostname or "").lower()
        if host not in {"localhost", "::1", "0:0:0:0:0:0:0:1"}:
            return value
        netloc = "127.0.0.1"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urllib.parse.urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        return value


def normalize_local_url(value: str, fallback: str) -> str:
    return prefer_ipv4_loopback_url(normalize_url(value, fallback))


def url_port(value: str, fallback: int) -> int:
    try:
        parsed = urllib.parse.urlparse(normalize_url(value, f"http://127.0.0.1:{fallback}/"))
        if parsed.port:
            return parsed.port
    except Exception:
        pass
    return fallback


def load_json_file(path: Path) -> Dict:
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def write_json_file(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def load_launcher_config() -> Dict:
    cfg = merge_dict(default_launcher_config(), load_json_file(LAUNCHER_CONFIG_FILE))
    dh = load_json_file(DIGITAL_HUMAN_CONFIG_FILE)
    if isinstance(dh.get("tts"), dict):
        tts = dh["tts"]
        if tts.get("base_url"):
            cfg["tts"]["base_url"] = normalize_local_url(tts.get("base_url"), cfg["tts"]["base_url"])
        for key in ("root_dir", "python_path"):
            if tts.get(key):
                cfg["tts"][key] = str(tts[key])
    if isinstance(dh.get("heygem"), dict):
        heygem = dh["heygem"]
        if heygem.get("base_url"):
            cfg["heygem"]["base_url"] = normalize_local_url(heygem.get("base_url"), cfg["heygem"]["base_url"])
        if heygem.get("api_base_url"):
            cfg["heygem"]["api_base_url"] = normalize_local_url(heygem.get("api_base_url"), cfg["heygem"]["api_base_url"])
        for key in ("root_dir", "python_path"):
            if heygem.get(key):
                cfg["heygem"][key] = str(heygem[key])
    cfg["main"]["base_url"] = normalize_local_url(cfg["main"].get("base_url"), "http://127.0.0.1:3000/")
    cfg["tts"]["base_url"] = normalize_local_url(cfg["tts"].get("base_url"), "http://127.0.0.1:7861/")
    cfg["heygem"]["base_url"] = normalize_local_url(cfg["heygem"].get("base_url"), "http://127.0.0.1:7860/")
    cfg["heygem"]["api_base_url"] = normalize_local_url(cfg["heygem"].get("api_base_url"), "http://127.0.0.1:8383/")
    cfg["main"]["port"] = url_port(cfg["main"]["base_url"], int(cfg["main"].get("port") or 3000))
    cfg["tts"]["port"] = url_port(cfg["tts"]["base_url"], int(cfg["tts"].get("port") or 7861))
    cfg["heygem"]["port"] = url_port(cfg["heygem"]["base_url"], int(cfg["heygem"].get("port") or 7860))
    cfg["heygem"]["api_port"] = url_port(cfg["heygem"]["api_base_url"], int(cfg["heygem"].get("api_port") or 8383))
    cfg["launcher"]["port"] = int(cfg["launcher"].get("port") or 2999)
    return cfg


def save_launcher_config(payload: Dict, sync_digital_human: bool = True) -> Dict:
    cfg = merge_dict(load_launcher_config(), payload or {})
    cfg["main"]["base_url"] = normalize_local_url(cfg["main"].get("base_url"), "http://127.0.0.1:3000/")
    cfg["tts"]["base_url"] = normalize_local_url(cfg["tts"].get("base_url"), "http://127.0.0.1:7861/")
    cfg["heygem"]["base_url"] = normalize_local_url(cfg["heygem"].get("base_url"), "http://127.0.0.1:7860/")
    cfg["heygem"]["api_base_url"] = normalize_local_url(cfg["heygem"].get("api_base_url"), "http://127.0.0.1:8383/")
    cfg["main"]["port"] = url_port(cfg["main"]["base_url"], int(cfg["main"].get("port") or 3000))
    cfg["tts"]["port"] = url_port(cfg["tts"]["base_url"], int(cfg["tts"].get("port") or 7861))
    cfg["heygem"]["port"] = url_port(cfg["heygem"]["base_url"], int(cfg["heygem"].get("port") or 7860))
    cfg["heygem"]["api_port"] = url_port(cfg["heygem"]["api_base_url"], int(cfg["heygem"].get("api_port") or 8383))
    write_json_file(LAUNCHER_CONFIG_FILE, cfg)
    if sync_digital_human:
        dh = load_json_file(DIGITAL_HUMAN_CONFIG_FILE)
        dh.setdefault("tts", {})
        dh.setdefault("heygem", {})
        dh["tts"]["base_url"] = cfg["tts"]["base_url"]
        dh["tts"]["root_dir"] = cfg["tts"]["root_dir"]
        dh["tts"]["python_path"] = cfg["tts"]["python_path"]
        dh["heygem"]["base_url"] = cfg["heygem"]["base_url"]
        dh["heygem"]["api_base_url"] = cfg["heygem"]["api_base_url"]
        dh["heygem"]["root_dir"] = cfg["heygem"]["root_dir"]
        dh["heygem"]["python_path"] = cfg["heygem"]["python_path"]
        write_json_file(DIGITAL_HUMAN_CONFIG_FILE, dh)
    return cfg


@dataclass(frozen=True)
class HealthCheck:
    key: str
    label: str
    url: str
    host: str
    port: int


@dataclass(frozen=True)
class ServiceSpec:
    key: str
    label: str
    checks: Tuple[HealthCheck, ...]
    command_builder: Callable[[], List[str]]
    cwd: Path
    env_builder: Callable[[], Dict[str, str]]
    ready_timeout: int


@dataclass
class ServiceState:
    key: str
    label: str
    source: str
    pid: Optional[int] = None
    process: Optional[subprocess.Popen] = None


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_runtime() -> Dict:
    try:
        with RUNTIME_FILE.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict):
            payload.setdefault("services", {})
            return prune_stale_runtime(payload)
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return {"version": 1, "base_dir": str(BASE_DIR), "services": {}}


def prune_stale_runtime(runtime: Dict) -> Dict:
    services = runtime.get("services")
    if not isinstance(services, dict):
        runtime["services"] = {}
        return runtime

    changed = False
    for key, payload in list(services.items()):
        try:
            pid = int((payload or {}).get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid and not is_pid_running(pid):
            stale = dict(payload or {})
            stale["pid"] = pid
            stale["stopped_at"] = now_text()
            runtime.setdefault("last_exited_services", {})[key] = stale
            services.pop(key, None)
            changed = True

    if changed:
        save_runtime(runtime)
    return runtime


def save_runtime(runtime: Dict) -> None:
    ensure_dirs()
    runtime["version"] = 1
    runtime["base_dir"] = str(BASE_DIR)
    tmp_path = RUNTIME_FILE.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(runtime, fh, ensure_ascii=False, indent=2)
    tmp_path.replace(RUNTIME_FILE)


def clear_runtime_if_empty(runtime: Dict) -> None:
    if runtime.get("services"):
        save_runtime(runtime)
        return
    try:
        RUNTIME_FILE.unlink()
    except FileNotFoundError:
        pass


def hidden_creationflags() -> int:
    if os.name != "nt":
        return 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def is_pid_running(pid: Optional[int]) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            process_query_limited_information = 0x1000
            handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return ctypes.get_last_error() == 5
        except Exception:
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=5,
                )
                return f'"{pid}"' in result.stdout or f",{pid}," in result.stdout
            except Exception:
                return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_process_tree(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=10,
            )
            return result.returncode == 0 or not is_pid_running(pid)
        except subprocess.TimeoutExpired:
            print(f"[ERROR] Timed out while stopping project backend PID {pid}")
            return not is_pid_running(pid)
        except Exception as exc:
            print(f"[ERROR] Failed to stop project backend PID {pid}: {exc}")
            return not is_pid_running(pid)
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def norm_path_text(value: str) -> str:
    return str(value or "").replace("/", "\\").lower()


def path_contains(path_text: str, root: Path) -> bool:
    root_text = norm_path_text(str(root))
    text = norm_path_text(path_text)
    return bool(root_text and root_text in text)


def query_windows_processes() -> List[Dict]:
    if os.name != "nt":
        return []
    ps = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=12,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        payload = json.loads(result.stdout)
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]
    except Exception:
        return []


def process_command_text(process: Dict) -> str:
    return str(process.get("CommandLine") or process.get("ExecutablePath") or "")


def process_pid(process: Dict) -> int:
    try:
        return int(process.get("ProcessId") or 0)
    except (TypeError, ValueError):
        return 0


def is_project_process(process: Dict, root: Path = BASE_DIR) -> bool:
    return path_contains(str(process.get("ExecutablePath") or ""), root) or path_contains(
        process_command_text(process), root
    )


def is_heygem_rest_only_process(process: Dict) -> bool:
    text = norm_path_text(process_command_text(process))
    exe = norm_path_text(str(process.get("ExecutablePath") or ""))
    return (
        is_project_process(process, heygem_root())
        and "app_local.py" in text
        and path_contains(exe or text, heygem_root() / "py38")
    )


def is_web_launcher_process(process: Dict) -> bool:
    text = norm_path_text(process_command_text(process))
    return is_project_process(process, BASE_DIR) and "tools\\launcher_server.py" in text


def is_main_backend_process(process: Dict) -> bool:
    text = norm_path_text(process_command_text(process))
    exe = norm_path_text(str(process.get("ExecutablePath") or ""))
    return (
        is_project_process(process, BASE_DIR)
        and "main.py" in text
        and path_contains(exe or text, BASE_DIR / "python")
    )


def is_tts_backend_process(process: Dict) -> bool:
    exe = norm_path_text(str(process.get("ExecutablePath") or ""))
    text = norm_path_text(process_command_text(process))
    return (
        is_project_process(process, tts_root())
        and (
            path_contains(exe, tts_root() / "py312")
            or ("index-tts-2" in text and ("app.py" in text or "multiprocessing" in text))
        )
    )


def is_heygem_backend_process(process: Dict) -> bool:
    exe = norm_path_text(str(process.get("ExecutablePath") or ""))
    text = norm_path_text(process_command_text(process))
    return (
        is_project_process(process, heygem_root())
        and (
            path_contains(exe, heygem_root() / "py38")
            or ("heygem-win" in text and ("app.py" in text or "app_local.py" in text or "multiprocessing" in text))
        )
    )


def is_project_backend_process(process: Dict) -> bool:
    return is_main_backend_process(process) or is_tts_backend_process(process) or is_heygem_backend_process(process)


def project_heygem_rest_pids() -> List[int]:
    return [
        pid
        for process in query_windows_processes()
        if (pid := process_pid(process)) and is_heygem_rest_only_process(process)
    ]


def project_web_launcher_pids() -> List[int]:
    return [
        pid
        for process in query_windows_processes()
        if (pid := process_pid(process)) and is_web_launcher_process(process)
    ]


def project_backend_pids() -> List[int]:
    pids: List[int] = []
    current_pid = os.getpid()
    for process in query_windows_processes():
        pid = process_pid(process)
        if not pid or pid == current_pid:
            continue
        if is_project_backend_process(process):
            pids.append(pid)
    return sorted(set(pids))


def project_backend_process_items() -> List[Dict]:
    items: List[Dict] = []
    current_pid = os.getpid()
    for process in query_windows_processes():
        pid = process_pid(process)
        if not pid or pid == current_pid:
            continue
        key = ""
        label = ""
        if is_main_backend_process(process):
            key = "main"
            label = "Main app"
        elif is_tts_backend_process(process):
            key = "tts"
            label = "TTS"
        elif is_heygem_backend_process(process) or is_heygem_rest_only_process(process):
            key = "heygem"
            label = "HeyGem"
        if not key:
            continue
        items.append(
            {
                "pid": pid,
                "key": key,
                "label": label,
                "command": process_command_text(process),
            }
        )
    deduped: Dict[int, Dict] = {}
    for item in items:
        deduped[int(item["pid"])] = item
    return [deduped[pid] for pid in sorted(deduped)]


def port_owner_pids(port: int) -> List[int]:
    if os.name != "nt":
        return []
    ps = (
        f"Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty OwningProcess | Sort-Object -Unique | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=8,
        )
        text = result.stdout.strip()
        if not text:
            return []
        payload = json.loads(text)
        if isinstance(payload, int):
            return [payload]
        if isinstance(payload, list):
            return [int(item) for item in payload if int(item or 0)]
    except Exception:
        return []
    return []


def cleanup_project_heygem_rest_only(reason: str = "") -> List[int]:
    stopped: List[int] = []
    for pid in project_heygem_rest_pids():
        if not is_pid_running(pid):
            continue
        message = f"[CLEAN] HeyGem REST-only residue PID {pid}"
        if reason:
            message += f" ({reason})"
        print(message)
        if kill_process_tree(pid):
            stopped.append(pid)
    return stopped


def cleanup_project_web_launcher() -> List[int]:
    current_pid = os.getpid()
    stopped: List[int] = []
    for pid in project_web_launcher_pids():
        if pid == current_pid or not is_pid_running(pid):
            continue
        print(f"[CLEAN] old web launcher PID {pid}")
        if kill_process_tree(pid):
            stopped.append(pid)
    return stopped


def cleanup_all_project_backends(reason: str = "") -> List[int]:
    stopped: List[int] = []
    for pid in project_backend_pids():
        if not is_pid_running(pid):
            continue
        message = f"[CLEAN] project backend PID {pid}"
        if reason:
            message += f" ({reason})"
        print(message)
        if kill_process_tree(pid):
            stopped.append(pid)
    return stopped


def tcp_port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_health(url: str, timeout: float = 3.0) -> Tuple[bool, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "SynCanvas-Service-Supervisor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            resp.read(512)
            if 200 <= status < 400:
                return True, ""
            return False, f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)


def run_checks(checks: Tuple[HealthCheck, ...], timeout: float = 3.0) -> Dict[str, Tuple[bool, str]]:
    return {check.key: http_health(check.url, timeout=timeout) for check in checks}


def checks_ready(check_results: Dict[str, Tuple[bool, str]]) -> bool:
    return all(ok for ok, _ in check_results.values())


def all_ready(spec: ServiceSpec) -> bool:
    return checks_ready(run_checks(spec.checks, timeout=2))


def base_env() -> Dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def prepend_path(env: Dict[str, str], paths: List[Path]) -> None:
    existing = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([str(path) for path in paths] + [existing])


def main_python() -> Path:
    configured = Path(load_launcher_config()["main"].get("python_path") or "")
    if configured.exists():
        return configured
    bundled = BASE_DIR / "python" / "python.exe"
    if bundled.exists():
        return bundled
    return Path(sys.executable)


def main_command() -> List[str]:
    script = Path(load_launcher_config()["main"].get("script_path") or BASE_DIR / "main.py")
    return [str(main_python()), str(script)]


def main_env() -> Dict[str, str]:
    return base_env()


def tts_root() -> Path:
    return Path(load_launcher_config()["tts"].get("root_dir") or BASE_DIR / "index-tts-2")


def tts_python() -> Path:
    configured = Path(load_launcher_config()["tts"].get("python_path") or "")
    if configured.exists():
        return configured
    return tts_root() / "py312" / "python.exe"


def tts_command() -> List[str]:
    script = Path(load_launcher_config()["tts"].get("script_path") or tts_root() / "app.py")
    return [str(tts_python()), "-s", str(script)]


def tts_env() -> Dict[str, str]:
    """
        message = str((error_item or {}).get("message") or "启动命令失败")
        items.append(
            check_item(
                "启动结果",
                f"start_error_{key}",
                spec.label,
                "error",
                f"启动命令失败：{message}",
                startup_suggestion(message),
            )
        )

    for spec in current_specs:
        status = statuses_by_key.get(spec.key) or {}
        if status.get("ready"):
            continue
        tracked = (runtime.get("services") or {}).get(spec.key) or {}
        exited = (runtime.get("last_exited_services") or {}).get(spec.key) or {}
        if exited and not tracked:
            pid = exited.get("pid") or ""
            stopped_at = exited.get("stopped_at") or "未知时间"
            items.append(
                check_item(
                    "启动结果",
                    f"exited_{spec.key}",
                    spec.label,
                    "error",
                    f"服务进程已退出：PID {pid}，记录时间 {stopped_at}",
                    "服务在接口就绪前退出。请优先查看该服务 stderr 最后一段，通常是依赖缺失、路径错误、端口冲突或 GPU/CUDA 异常。",
                )
            )
        if tracked and status.get("state") in {"starting", "partial", "stopped"}:
            elapsed = elapsed_since_seconds(str(tracked.get("started_at") or ""))
            if elapsed is not None and elapsed > spec.ready_timeout:
                minutes = max(1, int(round(elapsed / 60)))
                timeout_minutes = max(1, int(round(spec.ready_timeout / 60)))
                items.append(
                    check_item(
                        "启动结果",
                        f"timeout_{spec.key}",
                        spec.label,
                        "error",
                        f"已启动约 {minutes} 分钟，超过预期预热窗口 {timeout_minutes} 分钟，接口仍未全部就绪。",
                        "这不是正常待启动状态。请查看控制台中该服务最新日志；若端口已打开但接口一直等待，多半是模型加载、依赖、显存或内部异常卡住。",
                    )
                )
        if tracked or exited:
            log_item = log_diagnostic_for_service(spec, runtime)
            if log_item:
                items.append(log_item)

    """
    cfg = load_launcher_config()
    root = tts_root()
    py_dir = root / "py312"
    python_path = tts_python()
    port = str(cfg["tts"].get("port") or 7861)
    env = base_env()
    env.update(
        {
            "GRADIO_TEMP_DIR": str(root / "tmp"),
            "GRADIO_SERVER_PORT": port,
            "PORT": port,
            "GRADIO_PORT": port,
            "GRADIO_INBROWSER": "0",
            "GRADIO_BROWSER": "none",
            "BROWSER": "",
            "INFINITE_CANVAS_SILENT_SERVICE": "1",
            "INDEXTTS_DISABLE_CUDA_KERNEL": "1",
            "GRADIO_ANALYTICS_ENABLED": "False",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "PYTHONHOME": "",
            "PYTHONPATH": "",
            "PYTHONEXECUTABLE": str(python_path),
            "PYTHONWEXECUTABLE": str(py_dir / "pythonw.exe"),
            "PYTHON_EXECUTABLE": str(python_path),
            "PYTHONW_EXECUTABLE": str(py_dir / "pythonw.exe"),
            "PYTHON_BIN_PATH": str(python_path),
            "PYTHON_LIB_PATH": str(py_dir / "Lib" / "site-packages"),
            "HF_ENDPOINT": env.get("HF_ENDPOINT") or "https://hf-mirror.com",
            "HF_HOME": str(root / "checkpoints"),
            "TRANSFORMERS_CACHE": str(root / "tf_download"),
            "XFORMERS_FORCE_DISABLE_TRITON": "1",
            "DS_BUILD_AIO": "0",
            "DS_BUILD_SPARSE_ATTN": "0",
        }
    )
    prepend_path(
        env,
        [
            py_dir,
            py_dir / "Scripts",
            py_dir / "ffmpeg" / "bin",
            py_dir / "Lib" / "site-packages" / "torch" / "lib",
            py_dir / "Library" / "bin",
        ],
    )
    Path(env["GRADIO_TEMP_DIR"]).mkdir(parents=True, exist_ok=True)
    return env


def heygem_root() -> Path:
    return Path(load_launcher_config()["heygem"].get("root_dir") or BASE_DIR / "heygem-win-fix" / "heygem-win")


def heygem_python() -> Path:
    configured = Path(load_launcher_config()["heygem"].get("python_path") or "")
    if configured.exists():
        return configured
    return heygem_root() / "py38" / "python.exe"


def heygem_command() -> List[str]:
    script = Path(load_launcher_config()["heygem"].get("script_path") or heygem_root() / "app.py")
    return [str(heygem_python()), "-s", str(script)]


def heygem_env() -> Dict[str, str]:
    root = heygem_root()
    py_dir = root / "py38"
    python_path = heygem_python()
    env = base_env()
    env.update(
        {
            "GRADIO_TEMP_DIR": str(root / "tmp"),
            "GRADIO_INBROWSER": "0",
            "GRADIO_BROWSER": "none",
            "BROWSER": "",
            "INFINITE_CANVAS_SILENT_SERVICE": "1",
            "GRADIO_ANALYTICS_ENABLED": "False",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "PYTHONHOME": "",
            "PYTHONPATH": "",
            "PYTHONEXECUTABLE": str(python_path),
            "PYTHONWEXECUTABLE": str(py_dir / "pythonw.exe"),
            "PYTHON_EXECUTABLE": str(python_path),
            "PYTHONW_EXECUTABLE": str(py_dir / "pythonw.exe"),
            "PYTHON_BIN_PATH": str(python_path),
            "PYTHON_LIB_PATH": str(py_dir / "Lib" / "site-packages"),
            "HF_ENDPOINT": env.get("HF_ENDPOINT") or "https://hf-mirror.com",
            "HF_HOME": str(root / "hf_download"),
            "TRANSFORMERS_CACHE": str(root / "tf_download"),
            "XFORMERS_FORCE_DISABLE_TRITON": "1",
            "PYTORCH_CUDA_ALLOC_CONF": "max_split_size_mb:512",
        }
    )
    prepend_path(
        env,
        [
            py_dir,
            py_dir / "Scripts",
            py_dir / "ffmpeg" / "bin",
            py_dir / "Lib" / "site-packages" / "torch" / "lib",
            py_dir / "Library" / "bin",
        ],
    )
    Path(env["GRADIO_TEMP_DIR"]).mkdir(parents=True, exist_ok=True)
    return env


def main_check() -> HealthCheck:
    cfg = load_launcher_config()
    url = normalize_url(cfg["main"]["base_url"], "http://127.0.0.1:3000/")
    port = int(cfg["main"].get("port") or url_port(url, 3000))
    return HealthCheck("main", "Main app", urllib.parse.urljoin(url, "api/app-info"), "127.0.0.1", port)


def tts_check() -> HealthCheck:
    cfg = load_launcher_config()
    url = normalize_url(cfg["tts"]["base_url"], "http://127.0.0.1:7861/")
    port = int(cfg["tts"].get("port") or url_port(url, 7861))
    return HealthCheck("tts", "TTS Gradio", urllib.parse.urljoin(url, "config"), "127.0.0.1", port)


def heygem_gradio_check() -> HealthCheck:
    cfg = load_launcher_config()
    url = normalize_url(cfg["heygem"]["base_url"], "http://127.0.0.1:7860/")
    port = int(cfg["heygem"].get("port") or url_port(url, 7860))
    return HealthCheck("heygem_gradio", "HeyGem Gradio", urllib.parse.urljoin(url, "config"), "127.0.0.1", port)


def heygem_rest_check() -> HealthCheck:
    cfg = load_launcher_config()
    url = normalize_url(cfg["heygem"]["api_base_url"], "http://127.0.0.1:8383/")
    port = int(cfg["heygem"].get("api_port") or url_port(url, 8383))
    return HealthCheck("heygem_rest", "HeyGem REST", urllib.parse.urljoin(url, "easy/query?code=0"), "127.0.0.1", port)


def specs() -> Tuple[ServiceSpec, ...]:
    return (
        ServiceSpec("main", "Main app", (main_check(),), main_command, BASE_DIR, main_env, 60),
        ServiceSpec("tts", "TTS", (tts_check(),), tts_command, tts_root(), tts_env, 180),
        ServiceSpec(
            "heygem",
            "HeyGem",
            (heygem_gradio_check(), heygem_rest_check()),
            heygem_command,
            heygem_root(),
            heygem_env,
            420,
        ),
    )


def service_by_key(key: str) -> Optional[ServiceSpec]:
    for spec in specs():
        if spec.key == key:
            return spec
    return None


def validate_start_files(spec: ServiceSpec, command: List[str]) -> None:
    if not command:
        raise RuntimeError(f"{spec.label} command is empty")
    executable = Path(command[0])
    if not executable.exists():
        raise RuntimeError(f"{spec.label} Python not found: {executable}")
    script_name = next((part for part in reversed(command) if part.endswith(".py")), "")
    if script_name:
        script = spec.cwd / script_name
        if not script.exists():
            raise RuntimeError(f"{spec.label} script not found: {script}")


def start_process(spec: ServiceSpec, runtime: Dict) -> ServiceState:
    command = spec.command_builder()
    validate_start_files(spec, command)
    env = spec.env_builder()
    out_path = LOG_DIR / f"{spec.key}.out.log"
    err_path = LOG_DIR / f"{spec.key}.err.log"
    out_offset = out_path.stat().st_size if out_path.exists() else 0
    err_offset = err_path.stat().st_size if err_path.exists() else 0
    out_fh = out_path.open("a", encoding="utf-8", errors="replace")
    err_fh = err_path.open("a", encoding="utf-8", errors="replace")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(spec.cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out_fh,
            stderr=err_fh,
            creationflags=hidden_creationflags(),
        )
    finally:
        out_fh.close()
        err_fh.close()

    runtime.setdefault("services", {})[spec.key] = {
        "pid": process.pid,
        "cwd": str(spec.cwd),
        "cmd": command,
        "started_at": now_text(),
        "logs": {"stdout": str(out_path), "stderr": str(err_path)},
        "log_offsets": {"stdout": out_offset, "stderr": err_offset},
    }
    (runtime.get("last_exited_services") or {}).pop(spec.key, None)
    save_runtime(runtime)
    return ServiceState(spec.key, spec.label, "started", pid=process.pid, process=process)


def runtime_pid(runtime: Dict, spec: ServiceSpec) -> Optional[int]:
    service = (runtime.get("services") or {}).get(spec.key) or {}
    try:
        return int(service.get("pid") or 0)
    except (TypeError, ValueError):
        return None


def check_port_conflicts(spec: ServiceSpec, check_results: Optional[Dict[str, Tuple[bool, str]]] = None) -> List[str]:
    conflicts: List[str] = []
    for check in spec.checks:
        ok, error = (
            check_results.get(check.key, (False, ""))
            if check_results is not None
            else http_health(check.url, timeout=2)
        )
        if ok:
            continue
        if tcp_port_open(check.host, check.port):
            conflicts.append(f"{check.label} port {check.port} is occupied but health check failed: {error}")
    return conflicts


def prepare_heygem_start(spec: ServiceSpec, check_results: Dict[str, Tuple[bool, str]]) -> Dict[str, Tuple[bool, str]]:
    if spec.key != "heygem":
        return check_results

    gradio_ok = check_results.get("heygem_gradio", (False, ""))[0]
    rest_ok = check_results.get("heygem_rest", (False, ""))[0]
    if gradio_ok or not rest_ok:
        return check_results

    gradio_check = next((check for check in spec.checks if check.key == "heygem_gradio"), None)
    rest_check = next((check for check in spec.checks if check.key == "heygem_rest"), None)
    if not gradio_check or not rest_check:
        return check_results
    if tcp_port_open(gradio_check.host, gradio_check.port):
        return check_results

    owner_pids = set(port_owner_pids(rest_check.port))
    residue_pids = set(project_heygem_rest_pids())
    if owner_pids and not owner_pids.intersection(residue_pids):
        return check_results

    stopped = cleanup_project_heygem_rest_only("REST ready without Gradio")
    if stopped:
        time.sleep(1.5)
        return run_checks(spec.checks, timeout=2)
    return check_results


def ensure_service(spec: ServiceSpec, runtime: Dict) -> ServiceState:
    pid = runtime_pid(runtime, spec)
    check_results = run_checks(spec.checks, timeout=2)
    if checks_ready(check_results):
        if pid and is_pid_running(pid):
            print(f"[READY] {spec.label} already managed by supervisor (PID {pid})")
            return ServiceState(spec.key, spec.label, "tracked", pid=pid)
        if pid:
            runtime.get("services", {}).pop(spec.key, None)
            save_runtime(runtime)
        print(f"[READY] {spec.label} already running")
        return ServiceState(spec.key, spec.label, "external")

    if pid and is_pid_running(pid):
        print(f"[WAIT ] {spec.label} already started by supervisor (PID {pid})")
        return ServiceState(spec.key, spec.label, "tracked", pid=pid)
    if pid:
        runtime.get("services", {}).pop(spec.key, None)
        save_runtime(runtime)

    check_results = prepare_heygem_start(spec, check_results)

    conflicts = check_port_conflicts(spec, check_results)
    if conflicts:
        raise RuntimeError("; ".join(conflicts))

    if any(ok for ok, _ in check_results.values()):
        missing = [check for check in spec.checks if not check_results.get(check.key, (False, ""))[0]]
        if missing and all(not tcp_port_open(check.host, check.port) for check in missing):
            labels = ", ".join(check.label for check in missing)
            print(f"[START] {spec.label} is partially ready; starting full service for missing endpoint(s): {labels}")
        else:
            print(f"[WAIT ] {spec.label} partially ready; waiting for the remaining endpoint")
            return ServiceState(spec.key, spec.label, "external")

    state = start_process(spec, runtime)
    print(f"[START] {spec.label} PID {state.pid}")
    return state


def start_services_once(keys: Optional[Iterable[str]] = None) -> Dict:
    ensure_dirs()
    runtime = load_runtime()
    current_specs = specs()
    wanted = set(keys or [spec.key for spec in current_specs])
    result = {"ok": True, "started": [], "reused": [], "errors": [], "started_at": now_text()}
    for spec in current_specs:
        if spec.key not in wanted:
            continue
        try:
            state = ensure_service(spec, runtime)
            item = {"key": spec.key, "label": spec.label, "source": state.source, "pid": state.pid}
            if state.source == "started":
                result["started"].append(item)
            else:
                result["reused"].append(item)
        except Exception as exc:
            result["ok"] = False
            result["errors"].append({"key": spec.key, "label": spec.label, "message": str(exc)})
    result["completed_at"] = now_text()
    runtime["last_start"] = result
    save_runtime(runtime)
    return result


def process_has_exited(state: ServiceState) -> bool:
    if state.process is not None:
        return state.process.poll() is not None
    if state.pid and state.source in {"started", "tracked"}:
        return not is_pid_running(state.pid)
    return False


def print_check_summary(specs: Tuple[ServiceSpec, ...]) -> None:
    print("")
    print("Service status:")
    for spec in specs:
        for check in spec.checks:
            ok, error = http_health(check.url, timeout=2)
            if ok:
                print(f"  [READY] {check.label}: {check.url}")
            else:
                print(f"  [WAIT ] {check.label}: {error}")
    print("")


def wait_for_services(
    specs: Tuple[ServiceSpec, ...],
    states: Dict[str, ServiceState],
    open_browser: bool,
) -> None:
    ready: Dict[str, bool] = {}
    timed_out: Dict[str, bool] = {}
    deadlines = {spec.key: time.monotonic() + spec.ready_timeout for spec in specs}
    browser_opened = False
    last_summary = 0.0

    while True:
        all_done = True
        for spec in specs:
            if ready.get(spec.key):
                continue
            state = states[spec.key]
            if all_ready(spec):
                ready[spec.key] = True
                print(f"[READY] {spec.label}")
                if spec.key == "main" and open_browser and not browser_opened:
                    webbrowser.open(MAIN_URL)
                    browser_opened = True
                    print(f"[OPEN ] {MAIN_URL}")
                continue

            all_done = False
            if process_has_exited(state):
                raise RuntimeError(f"{spec.label} exited before it became ready")

            if time.monotonic() > deadlines[spec.key] and not timed_out.get(spec.key):
                timed_out[spec.key] = True
                log_info = (load_runtime().get("services") or {}).get(spec.key, {}).get("logs") or {}
                print(f"[WAIT ] {spec.label} is still warming up")
                if log_info:
                    print(f"       logs: {log_info.get('stdout')} / {log_info.get('stderr')}")
                if state.source == "external":
                    raise RuntimeError(f"{spec.label} did not become fully ready in time")

        now = time.monotonic()
        if now - last_summary >= 10:
            print_check_summary(specs)
            last_summary = now

        if all_done:
            return
        time.sleep(3)


def stop_tracked_services() -> int:
    ensure_dirs()
    runtime = load_runtime()
    services = runtime.get("services") or {}
    exit_code = 0

    tracked_pids = []
    for payload in services.values():
        try:
            pid = int(payload.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid:
            tracked_pids.append(pid)

    project_pids = project_backend_pids()
    all_pids = sorted(set(tracked_pids + project_pids))
    if not all_pids:
        print("No project backend services were found.")
    for pid in all_pids:
        if not is_pid_running(pid):
            continue
        print(f"[STOP ] project backend PID {pid}")
        if not kill_process_tree(pid):
            print(f"[ERROR] Failed to stop project backend PID {pid}")
            exit_code = 1

    cleanup_project_heygem_rest_only("stop requested")
    runtime["services"] = {}
    runtime.pop("last_start", None)
    runtime.pop("last_exited_services", None)
    clear_runtime_if_empty(runtime)
    return exit_code


def stop_services_by_keys(keys: Iterable[str]) -> int:
    key_set = set(keys)
    runtime = load_runtime()
    services = runtime.get("services") or {}
    exit_code = 0
    for key in list(key_set):
        payload = services.get(key) or {}
        pid = payload.get("pid")
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            pid = 0
        if not pid:
            continue
        if not is_pid_running(pid):
            services.pop(key, None)
            continue
        print(f"[STOP ] {key} PID {pid}")
        if kill_process_tree(pid):
            services.pop(key, None)
        else:
            print(f"[ERROR] Failed to stop {key} PID {pid}")
            exit_code = 1
    if "heygem" in key_set:
        cleanup_project_heygem_rest_only("stop requested")
    runtime["services"] = services
    if not services:
        runtime.pop("last_start", None)
        runtime.pop("last_exited_services", None)
    clear_runtime_if_empty(runtime)
    return exit_code


def bytes_to_gb(value: int) -> float:
    return round(value / (1024 * 1024 * 1024), 1)


def path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    try:
        for root, _, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def can_write_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def check_item(group: str, key: str, label: str, status: str, detail: str, suggestion: str = "") -> Dict:
    return {
        "group": group,
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "suggestion": suggestion,
    }


def parse_runtime_time(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def elapsed_since_seconds(value: str) -> Optional[int]:
    parsed = parse_runtime_time(value)
    if not parsed:
        return None
    return max(0, int((datetime.now() - parsed).total_seconds()))


def read_log_window(path: Path, start_offset: int = 0, limit: int = 16000) -> str:
    try:
        if not path.exists():
            return ""
        size = path.stat().st_size
        offset = max(0, min(int(start_offset or 0), size))
        if offset == 0 and size > limit:
            offset = size - limit
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read(limit)
        text = data.decode("utf-8", errors="replace")
        if offset > 0:
            lines = text.splitlines()
            text = "\n".join(lines[1:]) if len(lines) > 1 else ""
        return text
    except Exception:
        return ""


def recent_service_log_text(spec: ServiceSpec, runtime: Dict) -> str:
    payload = (runtime.get("services") or {}).get(spec.key) or (runtime.get("last_exited_services") or {}).get(spec.key) or {}
    logs = payload.get("logs") or {
        "stdout": str(LOG_DIR / f"{spec.key}.out.log"),
        "stderr": str(LOG_DIR / f"{spec.key}.err.log"),
    }
    offsets = payload.get("log_offsets") or {}
    parts: List[str] = []
    for stream in ("stderr", "stdout"):
        path = Path(logs.get(stream) or "")
        try:
            offset = int(offsets.get(stream) or 0)
        except (TypeError, ValueError):
            offset = 0
        text = read_log_window(path, offset, limit=12000).strip()
        if text:
            parts.append(f"[{stream}]\n{text}")
    return "\n".join(parts)


def last_meaningful_log_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        clean = line.strip()
        if clean:
            return clean[-500:]
    return ""


def startup_suggestion(message: str) -> str:
    lower = message.lower()
    if "address already in use" in lower or "winerror 10048" in lower or "10048" in lower or "port" in lower and "occupied" in lower:
        return "端口被占用。请先停止占用该端口的旧进程，或到设置里修改对应服务端口后再启动。"
    if "no module named" in lower or "modulenotfounderror" in lower or "importerror" in lower:
        return "Python 依赖缺失或环境损坏。请确认项目包完整，必要时重新解压/修复对应服务环境。"
    if "filenotfounderror" in lower or "no such file or directory" in lower or "not found" in lower:
        return "关键文件缺失。请检查设置里的 Python、脚本和服务目录路径是否正确，或重新补全项目文件。"
    if "permissionerror" in lower or "access is denied" in lower or "拒绝访问" in lower:
        return "权限不足。请关闭占用文件的程序，或用有权限的目录/管理员方式重试。"
    if "cuda out of memory" in lower or "outofmemory" in lower or "out of memory" in lower:
        return "显存或内存不足。请关闭占用 GPU 的程序，降低并发/分辨率，或重启后再试。"
    if "cuda" in lower or "torch" in lower or "nvidia" in lower:
        return "GPU/CUDA 环境异常。请检查显卡驱动、CUDA 依赖和对应服务 Python 环境。"
    if "traceback" in lower or "runtimeerror" in lower or "exception" in lower:
        return "服务启动时抛出了异常。请打开控制台或导出日志查看完整 traceback，再按最后一条错误处理。"
    return "请打开控制台查看该服务最新 stdout/stderr，或导出完整日志后按最后一条错误处理。"


def log_diagnostic_for_service(spec: ServiceSpec, runtime: Dict) -> Optional[Dict]:
    text = recent_service_log_text(spec, runtime)
    if not text:
        return None
    lower = text.lower()
    interesting = (
        "traceback",
        "error",
        "exception",
        "failed",
        "modulenotfounderror",
        "importerror",
        "filenotfounderror",
        "permissionerror",
        "address already in use",
        "winerror 10048",
        "cuda",
        "out of memory",
    )
    if not any(token in lower for token in interesting):
        return None
    line = last_meaningful_log_line(text)
    return check_item(
        "启动日志",
        f"log_{spec.key}",
        spec.label,
        "error",
        line or "日志中出现启动错误。",
        startup_suggestion(text),
    )


def service_status_payload(spec: ServiceSpec, runtime: Dict, quick: bool = False) -> Dict:
    tracked = (runtime.get("services") or {}).get(spec.key) or {}
    pid = None
    try:
        pid = int(tracked.get("pid") or 0) or None
    except (TypeError, ValueError):
        pid = None
    managed = bool(pid and is_pid_running(pid))

    checks = []
    ready_count = 0
    for check in spec.checks:
        port_open = tcp_port_open(check.host, check.port, timeout=0.1)
        if quick and not managed and not port_open:
            ok = False
            error = ""
        else:
            ok, error = http_health(check.url, timeout=2)
            port_open = True if ok else port_open
        ready_count += 1 if ok else 0
        checks.append(
            {
                "key": check.key,
                "label": check.label,
                "url": check.url,
                "port": check.port,
                "ready": ok,
                "port_open": port_open,
                "error": "" if ok else error,
            }
        )
    ready = ready_count == len(spec.checks)
    partial = 0 < ready_count < len(spec.checks)
    port_open = any(check.get("port_open") for check in checks)
    if ready:
        state = "ready"
    elif partial:
        state = "partial"
    elif managed or port_open:
        state = "starting"
    else:
        state = "stopped"
    return {
        "key": spec.key,
        "label": spec.label,
        "state": state,
        "ready": ready,
        "managed": managed,
        "pid": pid if managed else None,
        "source": "managed" if managed else ("external" if ready else ("partial" if partial else ("warming" if port_open else "none"))),
        "checks": checks,
        "logs": tracked.get("logs")
        or {
            "stdout": str(LOG_DIR / f"{spec.key}.out.log"),
            "stderr": str(LOG_DIR / f"{spec.key}.err.log"),
        },
    }


def run_torch_cuda_probe(python_path: Path) -> Dict:
    cache_key = str(python_path)
    cached = CUDA_PROBE_CACHE.get(cache_key)
    if cached and time.time() - cached.get("time", 0) < CUDA_PROBE_TTL_SECONDS:
        return dict(cached.get("value") or {})
    if not python_path.exists():
        return {"ok": False, "message": "Python 不存在，无法检测 CUDA"}
    code = (
        "import json\n"
        "try:\n"
        " import torch\n"
        " ok=torch.cuda.is_available()\n"
        " name=torch.cuda.get_device_name(0) if ok else ''\n"
        " print(json.dumps({'ok':ok,'name':name,'torch':getattr(torch,'__version__','')}))\n"
        "except Exception as e:\n"
        " print(json.dumps({'ok':False,'error':str(e)}))\n"
    )
    try:
        result = subprocess.run(
            [str(python_path), "-s", "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=12,
            creationflags=hidden_creationflags(),
        )
        line = (result.stdout or "").strip().splitlines()[-1] if result.stdout else "{}"
        payload = json.loads(line)
        if payload.get("ok"):
            value = {
                "ok": True,
                "message": f"{payload.get('name') or 'CUDA available'} / torch {payload.get('torch') or 'unknown'}",
            }
            CUDA_PROBE_CACHE[cache_key] = {"time": time.time(), "value": value}
            return value
        value = {"ok": False, "message": payload.get("error") or "torch.cuda.is_available() = False"}
        CUDA_PROBE_CACHE[cache_key] = {"time": time.time(), "value": value}
        return value
    except Exception as exc:
        value = {"ok": False, "message": str(exc)}
        CUDA_PROBE_CACHE[cache_key] = {"time": time.time(), "value": value}
        return value


def build_diagnostics(
    include_gpu: bool = True,
    service_statuses: Optional[List[Dict]] = None,
    current_specs: Optional[Tuple[ServiceSpec, ...]] = None,
    runtime: Optional[Dict] = None,
) -> List[Dict]:
    items: List[Dict] = []
    runtime = runtime or load_runtime()
    current_specs = current_specs or specs()
    if service_statuses is None:
        service_statuses = [service_status_payload(spec, runtime) for spec in current_specs]
    statuses_by_key = {status.get("key"): status for status in service_statuses}
    check_statuses: Dict[str, Dict] = {}
    for service in service_statuses:
        for check in service.get("checks") or []:
            check_statuses[str(check.get("key"))] = check

    for spec in current_specs:
        status = statuses_by_key.get(spec.key) or service_status_payload(spec, runtime)
        if status["ready"]:
            items.append(check_item("服务接口", spec.key, spec.label, "ok", "服务接口可访问"))
        elif status["state"] == "starting":
            items.append(check_item("服务接口", spec.key, spec.label, "running", "服务已启动，仍在预热"))
        elif status["state"] == "partial":
            ready_labels = [check["label"] for check in status["checks"] if check.get("ready")]
            pending_labels = [check["label"] for check in status["checks"] if not check.get("ready")]
            detail_parts = []
            if ready_labels:
                detail_parts.append("已就绪：" + "、".join(ready_labels))
            if pending_labels:
                detail_parts.append("未就绪：" + "、".join(pending_labels))
            items.append(
                check_item(
                    "服务接口",
                    spec.key,
                    spec.label,
                    "warning",
                    "；".join(detail_parts) or "部分接口可访问",
                    "部分接口可用，请检查未就绪接口或对应日志。",
                )
            )
        else:
            items.append(
                check_item(
                    "服务接口",
                    spec.key,
                    spec.label,
                    "idle",
                    "服务尚未启动",
                    "点击一键启动后，启动器会等待服务完成预热。",
                )
            )

    last_start = runtime.get("last_start") or {}
    last_start_errors = last_start.get("errors") if isinstance(last_start.get("errors"), list) else []
    for error_item in last_start_errors:
        key = str((error_item or {}).get("key") or "")
        spec = service_by_key(key)
        if not spec:
            continue
        status = statuses_by_key.get(key)
        if status and status.get("ready"):
            continue
        message = str((error_item or {}).get("message") or "启动命令失败")
        items.append(
            check_item(
                "启动结果",
                f"start_error_{key}",
                spec.label,
                "error",
                f"启动命令失败：{message}",
                startup_suggestion(message),
            )
        )

    for spec in current_specs:
        status = statuses_by_key.get(spec.key) or {}
        if status.get("ready"):
            continue
        tracked = (runtime.get("services") or {}).get(spec.key) or {}
        exited = (runtime.get("last_exited_services") or {}).get(spec.key) or {}
        if exited and not tracked:
            pid = exited.get("pid") or ""
            stopped_at = exited.get("stopped_at") or "未知时间"
            items.append(
                check_item(
                    "启动结果",
                    f"exited_{spec.key}",
                    spec.label,
                    "error",
                    f"服务进程已退出：PID {pid}，记录时间 {stopped_at}",
                    "服务在接口就绪前退出。请优先查看该服务 stderr 最后一段，通常是依赖缺失、路径错误、端口冲突或 GPU/CUDA 异常。",
                )
            )
        if tracked and status.get("state") in {"starting", "partial", "stopped"}:
            elapsed = elapsed_since_seconds(str(tracked.get("started_at") or ""))
            if elapsed is not None and elapsed > spec.ready_timeout:
                minutes = max(1, int(round(elapsed / 60)))
                timeout_minutes = max(1, int(round(spec.ready_timeout / 60)))
                items.append(
                    check_item(
                        "启动结果",
                        f"timeout_{spec.key}",
                        spec.label,
                        "error",
                        f"已启动约 {minutes} 分钟，超过预期预热窗口 {timeout_minutes} 分钟，接口仍未全部就绪。",
                        "这不是正常待启动状态。请查看控制台中该服务最新日志；若端口已打开但接口一直等待，多半是模型加载、依赖、显存或内部异常卡住。",
                    )
                )
        if tracked or exited:
            log_item = log_diagnostic_for_service(spec, runtime)
            if log_item:
                items.append(log_item)

    cfg = load_launcher_config()
    port_checks = [("launcher", "Launcher", "127.0.0.1", int(cfg["launcher"].get("port") or LAUNCHER_PORT), None)]
    for spec in current_specs:
        for check in spec.checks:
            port_checks.append((check.key, check.label, check.host, check.port, check.url))
    for key, label, host, port, health_url in port_checks:
        port_open = tcp_port_open(host, port, timeout=0.1)
        if not port_open:
            items.append(check_item("端口状态", f"port_{key}", f"{label} :{port}", "ok", "端口空闲"))
            continue
        if health_url:
            cached_check = check_statuses.get(key)
            if cached_check is not None:
                ok = bool(cached_check.get("ready"))
                error = str(cached_check.get("error") or "")
            else:
                ok, error = http_health(health_url, timeout=1.5)
            if ok:
                items.append(check_item("端口状态", f"port_{key}", f"{label} :{port}", "ok", "端口由目标服务占用"))
            else:
                items.append(
                    check_item(
                        "端口状态",
                        f"port_{key}",
                        f"{label} :{port}",
                        "error",
                        f"端口被占用，但目标健康检查失败：{error}",
                        "关闭占用该端口的程序，或修改对应服务端口。",
                    )
                )
        else:
            old_web_pids = project_web_launcher_pids() if key == "launcher" else []
            if old_web_pids:
                items.append(
                    check_item(
                        "端口状态",
                        f"port_{key}",
                        f"{label} :{port}",
                        "warning",
                        f"旧 Web 启动器仍在运行：PID {', '.join(str(pid) for pid in old_web_pids)}",
                        "当前 WPF 启动器不需要 2999；需要时可关闭旧 Web 启动器，或保留作为回退入口。",
                    )
                )
            else:
                items.append(check_item("端口状态", f"port_{key}", f"{label} :{port}", "ok", "端口已占用"))

    runtime_files = [
        ("main_python", "主应用 Python", main_python()),
        ("main_script", "主应用入口", BASE_DIR / "main.py"),
        ("tts_python", "TTS Python", tts_python()),
        ("tts_script", "TTS 入口", tts_root() / "app.py"),
        ("heygem_python", "HeyGem Python", heygem_python()),
        ("heygem_script", "HeyGem 入口", heygem_root() / "app.py"),
    ]
    for key, label, path in runtime_files:
        if path.exists():
            items.append(check_item("运行环境", key, label, "ok", str(path)))
        else:
            items.append(check_item("运行环境", key, label, "error", f"缺失：{path}", "请确认项目包是否完整。"))

    required_dirs = [
        ("data", "运行数据目录", DATA_DIR, True),
        ("logs", "日志目录", LOG_DIR, True),
        ("assets_output", "主输出目录", BASE_DIR / "assets" / "output", True),
        ("tts_root", "TTS 目录", tts_root(), False),
        ("tts_cache", "TTS 模型/缓存目录", tts_root() / "checkpoints", False),
        ("tts_tf_cache", "TTS Transformers 缓存", tts_root() / "tf_download", False),
        ("heygem_root", "HeyGem 目录", heygem_root(), False),
        ("heygem_hf_cache", "HeyGem HF 缓存", heygem_root() / "hf_download", False),
        ("heygem_tf_cache", "HeyGem Transformers 缓存", heygem_root() / "tf_download", False),
    ]
    for key, label, path, writable in required_dirs:
        if not path.exists():
            severity = "error" if key.endswith("_root") else "warning"
            items.append(check_item("关键路径", key, label, severity, f"目录不存在：{path}", "请确认项目包是否完整。"))
            continue
        if writable and not can_write_dir(path):
            items.append(check_item("关键路径", key, label, "error", f"目录不可写：{path}", "请检查目录权限。"))
            continue
        size = path_size(path) if include_gpu else 0
        detail = f"{path} / {bytes_to_gb(size)} GB" if size else str(path)
        items.append(check_item("关键路径", key, label, "ok", detail))

    media_tools = [
        ("tts_ffmpeg", "TTS FFmpeg", tts_root() / "py312" / "ffmpeg" / "bin" / "ffmpeg.exe"),
        ("heygem_ffmpeg", "HeyGem FFmpeg", heygem_root() / "py38" / "ffmpeg" / "bin" / "ffmpeg.exe"),
    ]
    for key, label, path in media_tools:
        if path.exists():
            items.append(check_item("媒体依赖", key, label, "ok", str(path)))
        else:
            items.append(check_item("媒体依赖", key, label, "warning", f"未找到：{path}", "视频/音频处理可能失败。"))

    try:
        usage = shutil.disk_usage(BASE_DIR)
        free_gb = bytes_to_gb(usage.free)
        status = "ok" if usage.free >= MIN_FREE_BYTES else "warning"
        suggestion = "" if status == "ok" else "建议清理输出、缓存或临时目录后再生成视频。"
        items.append(check_item("磁盘空间", "disk_free", "项目盘剩余空间", status, f"{free_gb} GB 可用", suggestion))
    except Exception as exc:
        items.append(check_item("磁盘空间", "disk_free", "项目盘剩余空间", "warning", str(exc)))

    if include_gpu:
        probes = [
            ("tts_cuda", "TTS CUDA", tts_python()),
            ("heygem_cuda", "HeyGem CUDA", heygem_python()),
        ]
        for key, label, python_path in probes:
            probe = run_torch_cuda_probe(python_path)
            if probe["ok"]:
                items.append(check_item("GPU/CUDA", key, label, "ok", probe["message"]))
            else:
                items.append(
                    check_item(
                        "GPU/CUDA",
                        key,
                        label,
                        "warning",
                        probe["message"],
                        "CUDA 不可用时可能回落 CPU 或启动失败，请检查显卡驱动和对应环境。",
                    )
                )

    return items


def build_status(include_gpu: bool = True, quick: bool = False) -> Dict:
    ensure_dirs()
    runtime = load_runtime()
    cfg = load_launcher_config()
    current_specs = specs()
    services = [service_status_payload(spec, runtime, quick=quick) for spec in current_specs]
    diagnostics = build_diagnostics(
        include_gpu=include_gpu,
        service_statuses=services,
        current_specs=current_specs,
        runtime=runtime,
    )
    counts = {"ok": 0, "warning": 0, "error": 0, "running": 0, "idle": 0}
    for item in diagnostics:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "ok": counts.get("error", 0) == 0,
        "time": now_text(),
        "root": str(BASE_DIR),
        "config": cfg,
        "main_url": cfg["main"]["base_url"],
        "launcher_url": f"http://127.0.0.1:{cfg['launcher']['port']}/",
        "log_dir": str(LOG_DIR),
        "runtime_file": str(RUNTIME_FILE),
        "services": services,
        "diagnostics": diagnostics,
        "counts": counts,
    }


def cursor_key(service: str, stream: str) -> str:
    return f"{service}:{stream}"


def load_cursor_b64(encoded: str = "") -> Dict[str, int]:
    if not encoded:
        return {}
    try:
        raw = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return {}
        cursor: Dict[str, int] = {}
        for key, value in payload.items():
            try:
                cursor[str(key)] = max(0, int(value))
            except (TypeError, ValueError):
                pass
        return cursor
    except Exception:
        return {}


def read_recent_log(service: str, stream: str = "stdout", limit: int = 12000, cursor: Optional[Dict[str, int]] = None) -> Dict:
    spec = service_by_key(service)
    if not spec:
        raise ValueError("Unknown service")
    runtime = load_runtime()
    tracked = (runtime.get("services") or {}).get(service) or {}
    logs = tracked.get("logs") or {
        "stdout": str(LOG_DIR / f"{service}.out.log"),
        "stderr": str(LOG_DIR / f"{service}.err.log"),
    }
    path = Path(logs.get(stream) or logs.get("stdout") or "")
    if not path.exists():
        return {"service": service, "stream": stream, "path": str(path), "text": "", "size": 0, "mtime": "", "next_offset": 0}
    stat = path.stat()
    key = cursor_key(service, stream)
    if cursor is not None and key in cursor:
        start_offset = int(cursor.get(key) or 0)
    else:
        log_offsets = tracked.get("log_offsets") or {}
        try:
            start_offset = int(log_offsets.get(stream) or 0)
        except (TypeError, ValueError):
            start_offset = 0
    if start_offset < 0 or start_offset > stat.st_size:
        start_offset = 0
    with path.open("rb") as fh:
        if cursor is not None:
            offset = start_offset
            read_limit = max(0, min(limit, stat.st_size - offset))
        else:
            offset = max(start_offset, stat.st_size - limit)
            read_limit = limit
        try:
            fh.seek(offset)
        except OSError:
            pass
        data = fh.read(read_limit)
    text = data.decode("utf-8", errors="replace")
    if offset > start_offset or (start_offset == 0 and offset > 0):
        parts = text.splitlines()
        text = "\n".join(parts[1:]) if len(parts) > 1 else ""
    next_offset = min(stat.st_size, offset + len(data))
    return {
        "service": service,
        "stream": stream,
        "path": str(path),
        "text": text,
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "next_offset": next_offset,
    }


def read_console_logs(limit: int = 12000, cursor: Optional[Dict[str, int]] = None) -> Dict:
    entries = []
    next_cursor: Dict[str, int] = {}
    for spec in specs():
        for stream in ("stdout", "stderr"):
            item = read_recent_log(spec.key, stream, limit=limit, cursor=cursor)
            entries.append(item)
            next_cursor[cursor_key(spec.key, stream)] = int(item.get("next_offset") or item.get("size") or 0)
    return {"ok": True, "time": now_text(), "logs": entries, "next_cursor": next_cursor}


def print_config() -> int:
    print(json.dumps({"ok": True, "config": load_launcher_config()}, ensure_ascii=False, indent=2))
    return 0


def save_config_from_json(json_text: str) -> int:
    try:
        payload = json.loads(json_text or "{}")
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    config_payload = payload.get("config") if isinstance(payload, dict) and "config" in payload else payload
    cfg = save_launcher_config(config_payload)
    print(json.dumps({"ok": True, "config": cfg}, ensure_ascii=False, indent=2))
    return 0


def save_config_from_b64(encoded: str) -> int:
    try:
        json_text = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    return save_config_from_json(json_text)


def show_status(json_output: bool = False, include_gpu: bool = True, quick: bool = False) -> int:
    if json_output:
        print(json.dumps(build_status(include_gpu=include_gpu, quick=quick), ensure_ascii=False, indent=2))
        return 0
    print_check_summary(specs())
    runtime = load_runtime()
    services = runtime.get("services") or {}
    if services:
        print("Supervisor-owned processes:")
        for key, payload in services.items():
            pid = payload.get("pid")
            print(f"  {key}: PID {pid} ({'running' if is_pid_running(int(pid or 0)) else 'stopped'})")
    else:
        print("Supervisor-owned processes: none")
    return 0


def print_project_backend_pids() -> int:
    processes = project_backend_process_items()
    print(
        json.dumps(
            {
                "ok": True,
                "time": now_text(),
                "root": str(BASE_DIR),
                "pids": [int(item["pid"]) for item in processes],
                "processes": processes,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def start_all(open_browser: bool) -> int:
    ensure_dirs()
    runtime = load_runtime()
    runtime.setdefault("started_at", now_text())
    original_runtime_services = {
        key
        for key, payload in (runtime.get("services") or {}).items()
        if is_pid_running(int(payload.get("pid") or 0))
    }

    print("SynCanvas one-click services")
    print(f"Root: {BASE_DIR}")
    print("")

    states: Dict[str, ServiceState] = {}
    try:
        current_specs = specs()
        for spec in current_specs:
            states[spec.key] = ensure_service(spec, runtime)
        wait_for_services(current_specs, states, open_browser=open_browser)
        print("")
        print("All services are ready.")
        print("Press Ctrl+C to stop services started by this launcher.")
        while True:
            for state in states.values():
                if state.source in {"started", "tracked"} and process_has_exited(state):
                    raise RuntimeError(f"{state.label} stopped unexpectedly")
            time.sleep(5)
    except KeyboardInterrupt:
        print("")
        print("Stopping services started by this launcher...")
        return stop_tracked_services()
    except Exception as exc:
        print("")
        print(f"[ERROR] {exc}")
        print("Stopping services started by this launcher...")
        stop_services_by_keys({key for key in states if key not in original_runtime_services})
        return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start, stop, or inspect SynCanvas local services.")
    parser.add_argument("--stop", action="store_true", help="Stop services started by this supervisor.")
    parser.add_argument("--status", action="store_true", help="Show service health status.")
    parser.add_argument("--json", action="store_true", help="Print status as JSON.")
    parser.add_argument("--start-once", action="store_true", help="Start missing services and return immediately.")
    parser.add_argument("--logs", choices=["main", "tts", "heygem", "all"], help="Print recent service log.")
    parser.add_argument("--stream", choices=["stdout", "stderr", "both"], default="stdout", help="Log stream for --logs.")
    parser.add_argument("--cursor-b64", default="", help="Base64 encoded log cursor for incremental reads.")
    parser.add_argument("--config", action="store_true", help="Print launcher configuration as JSON.")
    parser.add_argument("--save-config-json", help="Save launcher configuration from a JSON string.")
    parser.add_argument("--save-config-b64", help="Save launcher configuration from base64 encoded JSON.")
    parser.add_argument("--project-backend-pids", action="store_true", help="Print project backend process IDs as JSON.")
    parser.add_argument("--no-gpu", action="store_true", help="Skip slow CUDA diagnostics.")
    parser.add_argument("--quick", action="store_true", help="Skip HTTP health checks for services with no tracked PID and closed ports.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the main app in a browser.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.stop:
        return stop_tracked_services()
    if args.start_once:
        result = start_services_once()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.logs:
        cursor = load_cursor_b64(args.cursor_b64)
        if args.logs == "all":
            print(json.dumps(read_console_logs(cursor=cursor if args.cursor_b64 else None), ensure_ascii=False, indent=2))
        elif args.stream == "both":
            logs = [
                read_recent_log(args.logs, "stdout", cursor=cursor if args.cursor_b64 else None),
                read_recent_log(args.logs, "stderr", cursor=cursor if args.cursor_b64 else None),
            ]
            next_cursor = {
                cursor_key(item["service"], item["stream"]): int(item.get("next_offset") or item.get("size") or 0)
                for item in logs
            }
            print(json.dumps({"ok": True, "time": now_text(), "logs": logs, "next_cursor": next_cursor}, ensure_ascii=False, indent=2))
        else:
            item = read_recent_log(args.logs, args.stream, cursor=cursor if args.cursor_b64 else None)
            print(
                json.dumps(
                    {
                        **item,
                        "ok": True,
                        "time": now_text(),
                        "next_cursor": {cursor_key(item["service"], item["stream"]): int(item.get("next_offset") or item.get("size") or 0)},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 0
    if args.config:
        return print_config()
    if args.save_config_json is not None:
        return save_config_from_json(args.save_config_json)
    if args.save_config_b64 is not None:
        return save_config_from_b64(args.save_config_b64)
    if args.project_backend_pids:
        return print_project_backend_pids()
    if args.status:
        return show_status(json_output=args.json, include_gpu=not args.no_gpu, quick=args.quick)
    return start_all(open_browser=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
