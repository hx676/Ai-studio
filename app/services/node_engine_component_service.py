from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from app.core.json_store import atomic_write_json, read_json_resilient
from app.core.paths import BASE_DIR, DATA_DIR
from app.core.platforms import current_platform_tag, platform_supported


COMPONENT_ID = "node-engine"
MANIFEST_FILE = Path(BASE_DIR) / "node-engine-manifest.json"
DEFAULT_INSTALL_ROOT = Path(BASE_DIR) / "components" / COMPONENT_ID
STATE_DIR = Path(DATA_DIR) / "components"
STATE_FILE = STATE_DIR / f"{COMPONENT_ID}-state.json"
REGISTRY_FILE = STATE_DIR / f"{COMPONENT_ID}-installed.json"
CACHE_DIR = Path(DATA_DIR) / "component-cache" / COMPONENT_ID
ACTIVE_STATES = {"queued", "copying", "downloading", "verifying", "installing", "cancelling"}
COPY_EXCLUDES = {
    ".cache",
    ".git",
    ".github",
    ".idea",
    ".launcher",
    ".pytest_cache",
    ".ruff_cache",
    ".vscode",
    "__pycache__",
    "custom_nodes",
    "extra_model_paths.yaml",
    "input",
    "output",
    "temp",
    "user",
    "models",
    "logs",
}

_LOCK = threading.RLock()
_WORKER_LOCK = threading.Lock()
_CANCEL = threading.Event()
_WORKER: Optional[threading.Thread] = None


class NodeEngineComponentError(RuntimeError):
    pass


class NodeEngineComponentBusy(NodeEngineComponentError):
    pass


class NodeEngineComponentCancelled(NodeEngineComponentError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_json(path: Path, fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    value = read_json_resilient(path, dict(fallback or {}))
    return value if isinstance(value, dict) else dict(fallback or {})


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    atomic_write_json(path, value)


def _replace_state(**values: Any) -> Dict[str, Any]:
    with _LOCK:
        state = _read_json(STATE_FILE)
        state.update(values)
        state["updated_at"] = _now()
        _write_json(STATE_FILE, state)
        return state


def _load_manifest(url: str = "") -> Dict[str, Any]:
    try:
        if url:
            request = urllib.request.Request(url, headers={"User-Agent": "SynCanvas-NodeEngine-Installer/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                manifest = json.loads(response.read().decode("utf-8-sig"))
        else:
            manifest = _read_json(MANIFEST_FILE)
    except Exception as exc:
        raise NodeEngineComponentError(f"无法读取节点引擎清单：{exc}") from exc
    component = manifest.get("component") if isinstance(manifest, dict) else None
    artifact = component.get("artifact") if isinstance(component, dict) else None
    if not isinstance(component, dict) or component.get("id") != COMPONENT_ID or not isinstance(artifact, dict):
        raise NodeEngineComponentError("节点引擎组件清单无效")
    checksum = str(artifact.get("sha256") or "").strip().lower()
    urls = [str(item).strip() for item in artifact.get("urls") or [] if str(item).strip()]
    if urls and len(checksum) == 64:
        if not str(component.get("source_url") or "").strip() or not str(component.get("source_version") or "").strip():
            raise NodeEngineComponentError("可下载的 GPL 节点引擎清单必须声明确切源码地址和上游版本")
    return manifest


def _registry_runtime_root() -> Optional[Path]:
    value = str(_read_json(REGISTRY_FILE).get("runtime_root") or "").strip()
    return Path(value).expanduser().resolve() if value else None


def runtime_root() -> Path:
    override = str(os.getenv("SYNCANVAS_NODE_ENGINE_RUNTIME_ROOT", "")).strip()
    if override:
        return Path(override).expanduser().resolve()
    registered = _registry_runtime_root()
    return registered or (DEFAULT_INSTALL_ROOT / "runtime").resolve()


def _runtime_ready(root: Path, manifest: Dict[str, Any]) -> bool:
    sentinels = manifest["component"]["artifact"].get("sentinels") or []
    return bool(sentinels) and all((root / str(relative)).is_file() for relative in sentinels)


def recover_interrupted_install() -> None:
    state = _read_json(STATE_FILE)
    if state.get("state") in ACTIVE_STATES:
        _replace_state(state="interrupted", phase="interrupted", message="节点引擎安装曾被中断，可以重新安装", error="")


def get_status() -> Dict[str, Any]:
    try:
        manifest = _load_manifest()
        component = manifest["component"]
        artifact = component["artifact"]
        supported = platform_supported(artifact.get("platforms"))
        root = runtime_root()
        ready = supported and _runtime_ready(root, manifest)
        state = _read_json(STATE_FILE)
        task_state = str(state.get("state") or "")
        status = "unsupported" if not supported else task_state if task_state in ACTIVE_STATES else ("ready" if ready else task_state or "not_installed")
        checksum = str(artifact.get("sha256") or "").strip().lower()
        urls = [str(item).strip() for item in artifact.get("urls") or [] if str(item).strip()]
        source_available = bool(urls and len(checksum) == 64)
        process = {"running": False, "ready": False, "pid": None, "error": ""}
        try:
            from app.services import node_engine_service

            process = node_engine_service.process_status(probe=True)
        except Exception as exc:
            process["error"] = str(exc)
        return {
            "ok": True,
            "component_id": COMPONENT_ID,
            "display_name": component.get("display_name") or "SynCanvas 节点引擎",
            "version": component.get("version") or "",
            "license": component.get("license") or "GPL-3.0",
            "source_url": component.get("source_url") or "",
            "state": status,
            "platform": current_platform_tag(),
            "supported": supported,
            "ready": ready,
            "runtime_root": str(root),
            "installed_source": "override" if os.getenv("SYNCANVAS_NODE_ENGINE_RUNTIME_ROOT") else ("managed" if ready else ""),
            "can_install": supported and source_available,
            "download_size": int(component.get("download_size") or 0),
            "installed_size": int(component.get("installed_size") or 0),
            "minimum_free_bytes": int(component.get("minimum_free_bytes") or 0),
            "progress_percent": float(state.get("progress_percent") or (100 if ready else 0)),
            "phase": str(state.get("phase") or status),
            "message": str(state.get("message") or ""),
            "error": str(state.get("error") or ""),
            "task": state,
            "process": process,
        }
    except Exception as exc:
        return {"ok": False, "component_id": COMPONENT_ID, "state": "error", "ready": False, "error": str(exc), "process": {"running": False, "ready": False}}


def _check_cancelled() -> None:
    if _CANCEL.is_set():
        raise NodeEngineComponentCancelled("节点引擎安装已取消")


def _validate_source(source: Path) -> None:
    required = (source / "main.py", source / "python" / "python.exe", source / "LICENSE")
    if not all(path.is_file() for path in required):
        raise NodeEngineComponentError("来源目录不是完整的便携节点引擎，缺少 main.py、python/python.exe 或 LICENSE")


def _copy_source(source: Path, staging: Path) -> None:
    _validate_source(source)
    entries = [entry for entry in source.iterdir() if entry.name not in COPY_EXCLUDES]
    total = max(1, len(entries))
    for index, entry in enumerate(entries, 1):
        _check_cancelled()
        target = staging / entry.name
        if entry.is_dir():
            shutil.copytree(
                entry,
                target,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    "__pycache__",
                    "*.pyc",
                    ".cache",
                    ".launcher",
                    "extra_model_paths.yaml",
                ),
            )
        else:
            shutil.copy2(entry, target)
        _replace_state(progress_percent=5 + index / total * 80, message=f"正在复制 {entry.name}")


def _download_artifact(manifest: Dict[str, Any]) -> Path:
    artifact = manifest["component"]["artifact"]
    filename = Path(str(artifact.get("filename") or "node-engine.zip")).name
    expected_hash = str(artifact.get("sha256") or "").strip().lower()
    urls = [str(item).strip() for item in artifact.get("urls") or [] if str(item).strip()]
    local_candidates = [Path(BASE_DIR) / "packages" / "components" / filename, Path(BASE_DIR) / filename, Path.home() / "Downloads" / filename]
    for candidate in local_candidates:
        if candidate.is_file():
            return candidate
    if not urls or len(expected_hash) != 64:
        raise NodeEngineComponentError("节点引擎安装包尚未配置下载地址和 SHA256；也可以选择本地运行时目录导入")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / filename
    last_error: Optional[Exception] = None
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "SynCanvas-NodeEngine-Installer/1.0"})
            with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as output:
                total = int(response.headers.get("Content-Length") or 0)
                received = 0
                while True:
                    _check_cancelled()
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    received += len(chunk)
                    if total:
                        _replace_state(progress_percent=min(60, received / total * 60), message="正在下载节点引擎")
            return target
        except Exception as exc:
            last_error = exc
    raise NodeEngineComponentError(f"节点引擎下载失败：{last_error}")


def _verify_archive(path: Path, manifest: Dict[str, Any]) -> None:
    expected = str(manifest["component"]["artifact"].get("sha256") or "").strip().lower()
    if not expected:
        return
    digest = hashlib.sha256()
    total = max(1, path.stat().st_size)
    read = 0
    with path.open("rb") as handle:
        while True:
            _check_cancelled()
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            read += len(chunk)
            _replace_state(progress_percent=60 + read / total * 10, message="正在校验节点引擎安装包")
    if digest.hexdigest().lower() != expected:
        raise NodeEngineComponentError("节点引擎安装包 SHA256 校验失败")


def _extract_archive(path: Path, staging: Path) -> None:
    with zipfile.ZipFile(path, "r", allowZip64=True) as archive:
        entries = archive.infolist()
        total = max(1, len(entries))
        root = staging.resolve()
        for index, entry in enumerate(entries, 1):
            _check_cancelled()
            relative = Path(entry.filename.replace("\\", "/"))
            destination = (staging / relative).resolve()
            if root not in destination.parents and destination != root:
                raise NodeEngineComponentError(f"安装包包含不安全路径：{entry.filename}")
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
            if index % 20 == 0 or index == total:
                _replace_state(progress_percent=70 + index / total * 20, message="正在解压节点引擎")


def _safe_remove(path: Path, allowed_parent: Path) -> None:
    if not path.exists():
        return
    resolved = path.resolve()
    parent = allowed_parent.resolve()
    if resolved == parent or parent not in resolved.parents:
        raise NodeEngineComponentError(f"拒绝清理不安全路径：{resolved}")
    shutil.rmtree(resolved)


def _install_worker(install_root: Path, manifest: Dict[str, Any], source_root: str, force: bool) -> None:
    target = (install_root / "runtime").resolve()
    staging = (install_root / ".runtime-staging").resolve()
    backup = (install_root / ".runtime-previous").resolve()
    try:
        install_root.mkdir(parents=True, exist_ok=True)
        _safe_remove(staging, install_root)
        staging.mkdir(parents=True)
        if source_root:
            source = Path(source_root).expanduser().resolve()
            _replace_state(state="copying", phase="copying", progress_percent=5, message="正在导入本地节点引擎")
            _copy_source(source, staging)
        else:
            _replace_state(state="downloading", phase="downloading", progress_percent=1, message="正在准备节点引擎安装包")
            archive = _download_artifact(manifest)
            _replace_state(state="verifying", phase="verifying", progress_percent=60, message="正在校验节点引擎安装包")
            _verify_archive(archive, manifest)
            _replace_state(state="installing", phase="installing", progress_percent=70, message="正在安装节点引擎")
            _extract_archive(archive, staging)
        _validate_source(staging)
        _safe_remove(backup, install_root)
        if target.exists():
            os.replace(target, backup)
        os.replace(staging, target)
        _safe_remove(backup, install_root)
        source_version = str(manifest["component"].get("source_version") or "").strip()
        if source_root and not source_version:
            source_version = "local-import-unpinned"
        registry = {
            "component_id": COMPONENT_ID,
            "version": manifest["component"].get("version") or "",
            "runtime_root": str(target),
            "installed_at": _now(),
            "source": "local_import" if source_root else "artifact",
            "license": manifest["component"].get("license") or "GPL-3.0",
            "source_url": manifest["component"].get("source_url") or "",
            "source_version": source_version,
            "source_offer_url": manifest["component"].get("source_offer_url") or "",
        }
        _write_json(
            target / "SYNCANVAS-NODE-ENGINE-SOURCE.json",
            {
                "component_id": COMPONENT_ID,
                "license": registry["license"],
                "source_url": registry["source_url"],
                "source_version": registry["source_version"],
                "source_offer_url": registry["source_offer_url"],
                "installed_at": registry["installed_at"],
            },
        )
        _write_json(REGISTRY_FILE, registry)
        _replace_state(state="ready", phase="ready", progress_percent=100, message="节点引擎安装完成", error="")
    except NodeEngineComponentCancelled as exc:
        _replace_state(state="cancelled", phase="cancelled", message=str(exc), error="")
    except Exception as exc:
        _replace_state(state="error", phase="error", message="节点引擎安装失败", error=str(exc))
    finally:
        try:
            _safe_remove(staging, install_root)
        except Exception:
            pass


def start_install(*, install_root: str = "", manifest_url: str = "", source_root: str = "", force: bool = False) -> Dict[str, Any]:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER and _WORKER.is_alive():
            raise NodeEngineComponentBusy("节点引擎安装任务正在运行")
        manifest = _load_manifest(manifest_url)
        if not platform_supported(manifest["component"]["artifact"].get("platforms")):
            raise NodeEngineComponentError(f"当前节点引擎运行时不支持 {current_platform_tag()}")
        root = Path(install_root).expanduser().resolve() if install_root else DEFAULT_INSTALL_ROOT.resolve()
        if root == Path(BASE_DIR).resolve() or root.parent == root:
            raise NodeEngineComponentError("节点引擎安装目录不能是项目根目录或磁盘根目录")
        if not source_root and not get_status().get("can_install"):
            raise NodeEngineComponentError("没有可用的节点引擎安装包，请提供本地便携运行时目录")
        _CANCEL.clear()
        _replace_state(
            state="queued",
            phase="queued",
            progress_percent=0,
            message="节点引擎安装任务已创建",
            error="",
            install_root=str(root),
            source_root=str(source_root or ""),
            force=bool(force),
            started_at=_now(),
        )
        _WORKER = threading.Thread(target=_install_worker, args=(root, manifest, source_root, force), name="syncanvas-node-engine-installer", daemon=True)
        _WORKER.start()
    return get_status()


def cancel_install() -> Dict[str, Any]:
    if _WORKER and _WORKER.is_alive():
        _CANCEL.set()
        _replace_state(state="cancelling", phase="cancelling", message="正在取消节点引擎安装")
    return get_status()
