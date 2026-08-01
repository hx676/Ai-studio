from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import uuid
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException

from app.core.json_store import atomic_write_json, atomic_write_text, read_json_resilient
from app.core.paths import DATA_DIR
from app.models.runtime_nodes import (
    NodeEngineExtensionInstallRequest,
    NodeEngineModelImportRequest,
    NodeEngineModelPathsRequest,
)
from app.services import node_engine_component_service


DATA_ROOT = Path(DATA_DIR) / "node-engine"
MODELS_DIR = DATA_ROOT / "models"
CUSTOM_NODES_DIR = DATA_ROOT / "custom_nodes"
DISABLED_CUSTOM_NODES_DIR = DATA_ROOT / "disabled_custom_nodes"
TASKS_DIR = DATA_ROOT / "tasks"
MODEL_IMPORT_DIR = TASKS_DIR / "model-imports"
EXTENSION_TASK_DIR = TASKS_DIR / "extensions"
MODEL_REGISTRY_FILE = DATA_ROOT / "model-registry.json"
MODEL_PATHS_FILE = DATA_ROOT / "model-paths.json"
EXTENSION_REGISTRY_FILE = DATA_ROOT / "extension-registry.json"
EXTRA_PATHS_FILE = DATA_ROOT / "extra_model_paths.yaml"
EXTENSION_STAGING_DIR = DATA_ROOT / ".extension-staging"

MODEL_CATEGORIES = {
    "checkpoints",
    "clip",
    "clip_vision",
    "configs",
    "controlnet",
    "diffusion_models",
    "embeddings",
    "gligen",
    "hypernetworks",
    "loras",
    "photomaker",
    "style_models",
    "text_encoders",
    "unet",
    "upscale_models",
    "vae",
    "vae_approx",
}
MODEL_SUFFIXES = {
    ".bin",
    ".ckpt",
    ".gguf",
    ".json",
    ".model",
    ".onnx",
    ".pt",
    ".pth",
    ".safetensors",
    ".yaml",
    ".yml",
}
PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
COPY_EXCLUDES = {
    ".git",
    ".github",
    ".idea",
    ".pytest_cache",
    ".ruff_cache",
    ".vscode",
    "__pycache__",
    "input",
    "logs",
    "models",
    "output",
    "temp",
    "user",
}
TERMINAL_STATES = {"succeeded", "failed", "cancelled", "interrupted"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _read_json(path: Path, fallback: Any) -> Any:
    return read_json_resilient(path, deepcopy(fallback))


def _atomic_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def _safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    base = root.resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"路径超出节点引擎数据目录：{relative}") from exc
    return candidate


def _category(value: str) -> str:
    category = str(value or "").strip()
    if category not in MODEL_CATEGORIES:
        raise HTTPException(status_code=422, detail=f"不支持的模型分类：{category}")
    return category


def _is_model_file(path: Path) -> bool:
    return path.is_file() and path.suffix.casefold() in MODEL_SUFFIXES and not path.is_symlink()


def _sha256(path: Path, cancel: Optional[threading.Event] = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            if cancel and cancel.is_set():
                raise InterruptedError("任务已取消")
            chunk = handle.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _managed_extra_paths() -> Dict[str, Any]:
    return {
        "syncanvas": {
            "base_path": str(DATA_ROOT.resolve()),
            "custom_nodes": "custom_nodes",
            **{category: f"models/{category}" for category in sorted(MODEL_CATEGORIES)},
        }
    }


def get_model_paths() -> Dict[str, Any]:
    value = _read_json(MODEL_PATHS_FILE, {"schema_version": 1, "sources": []})
    sources = value.get("sources") if isinstance(value, dict) else []
    return {"schema_version": 1, "sources": sources if isinstance(sources, list) else []}


def write_extra_model_paths() -> Dict[str, Any]:
    payload = _managed_extra_paths()
    for source in get_model_paths()["sources"]:
        if not isinstance(source, dict) or not source.get("enabled"):
            continue
        source_id = str(source.get("id") or "")
        if not PACKAGE_ID_RE.fullmatch(source_id):
            continue
        base = Path(str(source.get("base_path") or "")).expanduser().resolve()
        paths = source.get("paths") if isinstance(source.get("paths"), dict) else {}
        if not base.is_dir():
            continue
        row: Dict[str, Any] = {"base_path": str(base)}
        for category, relative in paths.items():
            if category not in MODEL_CATEGORIES:
                continue
            candidate = (base / str(relative or ".")).resolve()
            try:
                candidate.relative_to(base)
            except ValueError:
                continue
            if candidate.is_dir():
                row[category] = str(relative or ".").replace("\\", "/")
        if len(row) > 1:
            payload[f"readonly_{source_id}"] = row
    # JSON is valid YAML and avoids adding a parser dependency to the host app.
    atomic_write_text(EXTRA_PATHS_FILE, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def set_model_paths(request: NodeEngineModelPathsRequest) -> Dict[str, Any]:
    sources: List[Dict[str, Any]] = []
    seen = set()
    for source_model in request.sources:
        source = source_model.model_dump() if hasattr(source_model, "model_dump") else source_model.dict()
        source_id = source["id"]
        if source_id in seen:
            raise HTTPException(status_code=422, detail=f"只读模型源 ID 重复：{source_id}")
        seen.add(source_id)
        base = Path(source["base_path"]).expanduser().resolve()
        if not base.is_dir():
            raise HTTPException(status_code=422, detail=f"只读模型源目录不存在：{base}")
        normalized_paths: Dict[str, str] = {}
        for category, relative in source.get("paths", {}).items():
            _category(category)
            candidate = (base / str(relative or ".")).resolve()
            try:
                candidate.relative_to(base)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"模型路径超出源目录：{source_id}.{category}") from exc
            if not candidate.is_dir():
                raise HTTPException(status_code=422, detail=f"模型路径不存在：{candidate}")
            normalized_paths[category] = str(relative or ".").replace("\\", "/")
        if not normalized_paths:
            raise HTTPException(status_code=422, detail=f"模型源没有有效分类：{source_id}")
        sources.append({**source, "base_path": str(base), "paths": normalized_paths})
    value = {"schema_version": 1, "sources": sources, "updated_at": _now_ms()}
    _atomic_json(MODEL_PATHS_FILE, value)
    write_extra_model_paths()
    return value


def _iter_models() -> Iterable[Dict[str, Any]]:
    for category in sorted(MODEL_CATEGORIES):
        root = MODELS_DIR / category
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not _is_model_file(path):
                continue
            yield {
                "id": f"managed:{category}:{path.relative_to(root).as_posix()}",
                "name": path.name,
                "category": category,
                "relative_path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "modified_at": int(path.stat().st_mtime * 1000),
                "source": "managed",
                "source_id": "syncanvas",
                "readonly": False,
            }
    for source in get_model_paths()["sources"]:
        if not isinstance(source, dict) or not source.get("enabled"):
            continue
        base = Path(str(source.get("base_path") or "")).expanduser().resolve()
        for category, relative in (source.get("paths") or {}).items():
            root = (base / str(relative or ".")).resolve()
            if category not in MODEL_CATEGORIES or not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not _is_model_file(path):
                    continue
                try:
                    path.resolve().relative_to(root)
                except ValueError:
                    continue
                yield {
                    "id": f"readonly:{source.get('id')}:{category}:{path.relative_to(root).as_posix()}",
                    "name": path.name,
                    "category": category,
                    "relative_path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "modified_at": int(path.stat().st_mtime * 1000),
                    "source": "readonly",
                    "source_id": source.get("id"),
                    "readonly": True,
                }


def list_models(query: str = "", category: str = "", page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    if category:
        _category(category)
    needle = str(query or "").strip().casefold()
    rows = [
        row for row in _iter_models()
        if (not category or row["category"] == category)
        and (not needle or needle in f"{row['name']} {row['relative_path']} {row['source_id']}".casefold())
    ]
    rows.sort(key=lambda row: (row["category"], row["name"].casefold(), row["relative_path"].casefold()))
    total = len(rows)
    start = (max(1, page) - 1) * page_size
    counts = {name: 0 for name in MODEL_CATEGORIES}
    for row in rows:
        counts[row["category"]] += 1
    return {
        "page": max(1, page),
        "page_size": page_size,
        "total": total,
        "items": rows[start : start + page_size],
        "categories": [{"name": name, "count": count} for name, count in sorted(counts.items())],
    }


class ModelImportManager:
    def __init__(self) -> None:
        self.records: Dict[str, Dict[str, Any]] = {}
        self.cancel_events: Dict[str, threading.Event] = {}
        self.lock = threading.Lock()

    def recover(self) -> None:
        MODEL_IMPORT_DIR.mkdir(parents=True, exist_ok=True)
        for path in MODEL_IMPORT_DIR.glob("*.json"):
            record = _read_json(path, {})
            if not isinstance(record, dict) or not record.get("task_id"):
                continue
            if record.get("status") not in TERMINAL_STATES:
                record.update({"status": "interrupted", "error": "SynCanvas 重启时导入仍在运行", "completed_at": _now_ms()})
                _atomic_json(path, record)
            self.records[record["task_id"]] = record

    def _persist(self, record: Dict[str, Any]) -> None:
        _atomic_json(MODEL_IMPORT_DIR / f"{record['task_id']}.json", record)

    def submit(self, request: NodeEngineModelImportRequest) -> Dict[str, Any]:
        source = Path(request.source_path).expanduser().resolve()
        if not source.exists() or source.is_symlink():
            raise HTTPException(status_code=422, detail=f"模型来源不存在或是符号链接：{source}")
        category = _category(request.category)
        task_id = uuid.uuid4().hex
        record = {
            "task_id": task_id,
            "status": "queued",
            "phase": "queued",
            "source_path": str(source),
            "category": category,
            "conflict": request.conflict,
            "recursive": request.recursive,
            "progress": 0.0,
            "processed_bytes": 0,
            "total_bytes": 0,
            "processed_files": 0,
            "total_files": 0,
            "imported": [],
            "duplicates": [],
            "skipped": [],
            "error": "",
            "created_at": _now_ms(),
            "completed_at": None,
        }
        self.records[task_id] = record
        self.cancel_events[task_id] = threading.Event()
        self._persist(record)
        threading.Thread(target=self._worker, args=(task_id,), name=f"node-engine-model-import-{task_id[:8]}", daemon=True).start()
        return deepcopy(record)

    def _source_files(self, record: Dict[str, Any]) -> List[tuple[Path, Path]]:
        source = Path(record["source_path"])
        if source.is_file():
            if not _is_model_file(source):
                raise ValueError(f"不支持的模型文件类型：{source.suffix or '(无扩展名)'}")
            return [(source, Path(source.name))]
        iterator = source.rglob("*") if record.get("recursive") else source.glob("*")
        rows = []
        for path in iterator:
            if _is_model_file(path):
                rows.append((path, path.relative_to(source)))
        return rows

    def _worker(self, task_id: str) -> None:
        record = self.records[task_id]
        cancel = self.cancel_events[task_id]
        try:
            with self.lock:
                record.update({"status": "running", "phase": "scanning"})
                self._persist(record)
                files = self._source_files(record)
                if not files:
                    raise ValueError("来源中没有支持的模型文件")
                record["total_files"] = len(files)
                record["total_bytes"] = sum(path.stat().st_size for path, _ in files)
                registry = _read_json(MODEL_REGISTRY_FILE, {"schema_version": 1, "files": {}})
                registry_files = registry.setdefault("files", {})
                known_hashes = {str(item.get("sha256")): key for key, item in registry_files.items() if isinstance(item, dict)}
                destination_root = MODELS_DIR / record["category"]
                destination_root.mkdir(parents=True, exist_ok=True)
                for source, relative in files:
                    if cancel.is_set():
                        raise InterruptedError("模型导入已取消")
                    target = _safe_child(destination_root, relative.as_posix())
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name(f".{target.name}.{task_id[:8]}.part")
                    digest = hashlib.sha256()
                    try:
                        with source.open("rb") as input_file, temporary.open("wb") as output_file:
                            while True:
                                if cancel.is_set():
                                    raise InterruptedError("模型导入已取消")
                                chunk = input_file.read(4 * 1024 * 1024)
                                if not chunk:
                                    break
                                output_file.write(chunk)
                                digest.update(chunk)
                                record["processed_bytes"] += len(chunk)
                                record["progress"] = record["processed_bytes"] / max(1, record["total_bytes"])
                                record["phase"] = "copying"
                                self._persist(record)
                        sha256 = digest.hexdigest()
                        duplicate = known_hashes.get(sha256)
                        if duplicate:
                            temporary.unlink(missing_ok=True)
                            record["duplicates"].append({"source": str(source), "existing": duplicate, "sha256": sha256})
                        elif target.exists() and record["conflict"] == "skip":
                            temporary.unlink(missing_ok=True)
                            record["skipped"].append({"source": str(source), "target": str(target), "reason": "target_exists"})
                        else:
                            if target.exists() and record["conflict"] == "rename":
                                original_target = target
                                counter = 2
                                while target.exists():
                                    target = original_target.with_name(f"{original_target.stem}-{counter}{original_target.suffix}")
                                    counter += 1
                            if target.exists() and record["conflict"] == "replace":
                                target.unlink()
                            os.replace(temporary, target)
                            if _sha256(target, cancel) != sha256:
                                target.unlink(missing_ok=True)
                                raise IOError(f"复制后校验失败：{target}")
                            key = f"{record['category']}/{target.relative_to(destination_root).as_posix()}"
                            registry_files[key] = {
                                "sha256": sha256,
                                "size": target.stat().st_size,
                                "source_path": str(source),
                                "imported_at": _now_ms(),
                            }
                            known_hashes[sha256] = key
                            record["imported"].append({"path": key, "sha256": sha256, "size": target.stat().st_size})
                    finally:
                        temporary.unlink(missing_ok=True)
                    record["processed_files"] += 1
                    self._persist(record)
                registry["updated_at"] = _now_ms()
                _atomic_json(MODEL_REGISTRY_FILE, registry)
                record.update({"status": "succeeded", "phase": "complete", "progress": 1.0})
        except InterruptedError as exc:
            record.update({"status": "cancelled", "phase": "cancelled", "error": str(exc)})
        except Exception as exc:
            record.update({"status": "failed", "phase": "failed", "error": str(exc)[:10000]})
        finally:
            record["completed_at"] = _now_ms()
            self._persist(record)

    def get(self, task_id: str) -> Dict[str, Any]:
        record = self.records.get(task_id) or _read_json(MODEL_IMPORT_DIR / f"{task_id}.json", {})
        if not record:
            raise HTTPException(status_code=404, detail="模型导入任务不存在")
        self.records[task_id] = record
        return deepcopy(record)

    def cancel(self, task_id: str) -> Dict[str, Any]:
        record = self.get(task_id)
        if record.get("status") in TERMINAL_STATES:
            return record
        event = self.cancel_events.get(task_id)
        if event:
            event.set()
        current = self.records[task_id]
        current["phase"] = "cancelling"
        self._persist(current)
        return deepcopy(current)


def _extension_registry() -> Dict[str, Any]:
    value = _read_json(EXTENSION_REGISTRY_FILE, {"schema_version": 1, "packages": {}})
    if not isinstance(value, dict):
        value = {"schema_version": 1, "packages": {}}
    if not isinstance(value.get("packages"), dict):
        value["packages"] = {}
    return value


def _package_id(value: str, source: str) -> str:
    package_id = str(value or "").strip()
    if not package_id:
        parsed = urllib.parse.urlparse(source)
        package_id = Path(parsed.path if parsed.scheme else source).stem
    if not PACKAGE_ID_RE.fullmatch(package_id) or package_id.casefold() == "syncanvas_bridge":
        raise HTTPException(status_code=422, detail="扩展包 ID 只能包含字母、数字、点、下划线和连字符")
    return package_id


def _copy_extension_source(source: Path, destination: Path) -> None:
    ignore = shutil.ignore_patterns(*COPY_EXCLUDES, "*.pyc", "*.pyo")
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignore)


def _extract_extension_zip(source: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(source, "r", allowZip64=True) as archive:
        for entry in archive.infolist():
            relative = Path(entry.filename.replace("\\", "/"))
            target = (destination / relative).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"扩展压缩包包含不安全路径：{entry.filename}")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"扩展压缩包包含符号链接：{entry.filename}")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as input_file, target.open("wb") as output_file:
                    shutil.copyfileobj(input_file, output_file, 1024 * 1024)


def _extension_content_root(staging: Path) -> Path:
    if (staging / "__init__.py").is_file():
        return staging
    children = [item for item in staging.iterdir() if item.is_dir() and item.name not in COPY_EXCLUDES]
    if len(children) == 1 and (children[0] / "__init__.py").is_file():
        return children[0]
    raise ValueError("扩展来源必须包含顶层 __init__.py 或单一扩展目录")


def _run_git_clone(source: str, destination: Path) -> None:
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--", source, str(destination)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"扩展仓库下载失败：{(result.stderr or result.stdout)[-4000:]}")


def _prepare_extension_source(source_value: str, staging: Path) -> Path:
    parsed = urllib.parse.urlparse(source_value)
    windows_drive_path = bool(re.match(r"^[A-Za-z]:[\\/]", source_value))
    if parsed.scheme and not windows_drive_path:
        if parsed.scheme != "https":
            raise ValueError("扩展仓库只允许 HTTPS 地址")
        _run_git_clone(source_value, staging)
        return _extension_content_root(staging)
    source = Path(source_value).expanduser().resolve()
    if not source.exists() or source.is_symlink():
        raise ValueError(f"扩展来源不存在或是符号链接：{source}")
    if source.is_dir():
        _copy_extension_source(source, staging)
    elif source.suffix.casefold() == ".zip":
        staging.mkdir(parents=True, exist_ok=True)
        _extract_extension_zip(source, staging)
    else:
        raise ValueError("扩展来源必须是目录、ZIP 或 HTTPS Git 仓库")
    return _extension_content_root(staging)


def _install_requirements(package_root: Path, log_path: Path) -> None:
    requirements = package_root / "requirements.txt"
    if not requirements.is_file() or not requirements.read_text(encoding="utf-8", errors="ignore").strip():
        return
    runtime = node_engine_component_service.runtime_root()
    python_exe = runtime / "python" / "python.exe"
    if not python_exe.is_file():
        raise RuntimeError("节点引擎 Python 环境不可用，无法安装扩展依赖")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        result = subprocess.run(
            [str(python_exe), "-m", "pip", "install", "-r", str(requirements), "--disable-pip-version-check"],
            cwd=str(package_root),
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=1800,
            check=False,
        )
    if result.returncode:
        raise RuntimeError(f"扩展依赖安装失败，退出码 {result.returncode}；日志：{log_path}")


def list_extensions() -> Dict[str, Any]:
    registry = _extension_registry()
    packages = registry["packages"]
    catalog = {}
    catalog_meta: Dict[str, Any] = {}
    try:
        from app.services import node_engine_service

        loaded = node_engine_service.load_catalog()
        catalog = loaded.get("nodes") or {}
        catalog_meta = loaded.get("meta") or {}
    except Exception:
        pass
    counts: Dict[str, int] = {}
    for definition in catalog.values():
        package = str(definition.get("package") or "").casefold()
        if package:
            counts[package] = counts.get(package, 0) + 1
    names = {
        item.name for root in (CUSTOM_NODES_DIR, DISABLED_CUSTOM_NODES_DIR) if root.is_dir()
        for item in root.iterdir() if item.is_dir() and item.name != "syncanvas_bridge"
    } | set(packages)
    items = []
    for package_id in sorted(names, key=str.casefold):
        enabled_path = CUSTOM_NODES_DIR / package_id
        disabled_path = DISABLED_CUSTOM_NODES_DIR / package_id
        enabled = enabled_path.is_dir()
        installed = enabled or disabled_path.is_dir()
        record = packages.get(package_id) if isinstance(packages.get(package_id), dict) else {}
        node_count = counts.get(package_id.casefold(), 0)
        error = str(record.get("error") or "")
        status = "enabled" if enabled else "disabled"
        if not installed:
            status = "missing"
        elif enabled and catalog_meta.get("scanned_at") and not node_count:
            status = "load_error"
            error = error or "扫描后未发现该扩展的节点；请检查节点引擎错误日志和依赖"
        items.append({
            "id": package_id,
            "enabled": enabled,
            "status": status,
            "node_count": node_count,
            "source": record.get("source") or "",
            "installed_at": record.get("installed_at"),
            "updated_at": record.get("updated_at"),
            "dependencies_installed": bool(record.get("dependencies_installed")),
            "error": error,
        })
    return {"items": items, "total": len(items), "catalog_revision": catalog_meta.get("revision", "")}


class ExtensionTaskManager:
    def __init__(self) -> None:
        self.records: Dict[str, Dict[str, Any]] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.cancel_events: Dict[str, asyncio.Event] = {}
        self.lock = asyncio.Lock()

    def recover(self) -> None:
        EXTENSION_TASK_DIR.mkdir(parents=True, exist_ok=True)
        for path in EXTENSION_TASK_DIR.glob("*.json"):
            record = _read_json(path, {})
            if not isinstance(record, dict) or not record.get("task_id"):
                continue
            if record.get("status") not in TERMINAL_STATES:
                record.update({"status": "interrupted", "error": "SynCanvas 重启时扩展操作仍在运行", "completed_at": _now_ms()})
                _atomic_json(path, record)
            self.records[record["task_id"]] = record

    def _persist(self, record: Dict[str, Any]) -> None:
        _atomic_json(EXTENSION_TASK_DIR / f"{record['task_id']}.json", record)

    def submit(self, request: NodeEngineExtensionInstallRequest) -> Dict[str, Any]:
        package_id = _package_id(request.package_id, request.source)
        target = CUSTOM_NODES_DIR / package_id
        disabled = DISABLED_CUSTOM_NODES_DIR / package_id
        if (target.exists() or disabled.exists()) and not request.replace:
            raise HTTPException(status_code=409, detail=f"扩展已存在：{package_id}；升级时请启用 replace")
        task_id = uuid.uuid4().hex
        record = {
            "task_id": task_id,
            "package_id": package_id,
            "source": request.source,
            "install_dependencies": request.install_dependencies,
            "replace": request.replace,
            "status": "queued",
            "phase": "queued",
            "progress": 0.0,
            "message": "扩展已加入安装队列",
            "error": "",
            "created_at": _now_ms(),
            "completed_at": None,
        }
        self.records[task_id] = record
        self.cancel_events[task_id] = asyncio.Event()
        self._persist(record)
        task = asyncio.create_task(self._execute(task_id))
        self.tasks[task_id] = task
        task.add_done_callback(lambda _task, key=task_id: self.tasks.pop(key, None))
        return deepcopy(record)

    async def _execute(self, task_id: str) -> None:
        record = self.records[task_id]
        cancel = self.cancel_events[task_id]
        staging = EXTENSION_STAGING_DIR / task_id
        backup = EXTENSION_STAGING_DIR / f"{task_id}.previous"
        target = CUSTOM_NODES_DIR / record["package_id"]
        disabled = DISABLED_CUSTOM_NODES_DIR / record["package_id"]
        was_running = False
        engine_stopped = False
        try:
            async with self.lock:
                if cancel.is_set():
                    raise asyncio.CancelledError
                shutil.rmtree(staging, ignore_errors=True)
                shutil.rmtree(backup, ignore_errors=True)
                record.update({"status": "running", "phase": "copying", "progress": 0.1, "message": "正在准备扩展来源"})
                self._persist(record)
                package_root = await asyncio.to_thread(_prepare_extension_source, record["source"], staging)
                if cancel.is_set():
                    raise asyncio.CancelledError
                from app.services import node_engine_service

                was_running = node_engine_service.process_status(probe=True).get("ready", False)
                if was_running:
                    record.update({"phase": "stopping", "progress": 0.25, "message": "正在停止节点引擎"})
                    self._persist(record)
                    await node_engine_service.stop_engine()
                    engine_stopped = True
                if record["install_dependencies"]:
                    record.update({"phase": "dependencies", "progress": 0.35, "message": "正在安装扩展依赖"})
                    self._persist(record)
                    await asyncio.to_thread(_install_requirements, package_root, EXTENSION_TASK_DIR / f"{task_id}.pip.log")
                if cancel.is_set():
                    raise asyncio.CancelledError
                CUSTOM_NODES_DIR.mkdir(parents=True, exist_ok=True)
                DISABLED_CUSTOM_NODES_DIR.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    os.replace(target, backup)
                elif disabled.exists():
                    os.replace(disabled, backup)
                try:
                    os.replace(package_root, target)
                except Exception:
                    if backup.exists() and not target.exists():
                        os.replace(backup, target)
                    raise
                shutil.rmtree(backup, ignore_errors=True)
                registry = _extension_registry()
                previous = registry["packages"].get(record["package_id"], {})
                registry["packages"][record["package_id"]] = {
                    "source": record["source"],
                    "installed_at": previous.get("installed_at") or _now_ms(),
                    "updated_at": _now_ms(),
                    "dependencies_installed": bool(record["install_dependencies"]),
                    "error": "",
                }
                _atomic_json(EXTENSION_REGISTRY_FILE, registry)
                record.update({"phase": "restarting", "progress": 0.75, "message": "正在扫描扩展节点"})
                self._persist(record)
                if was_running:
                    await node_engine_service.start_engine(90)
                    engine_stopped = False
                elif node_engine_component_service.get_status().get("ready"):
                    await node_engine_service.start_engine(90)
                else:
                    node_engine_service.invalidate_catalog()
                record.update({"status": "succeeded", "phase": "complete", "progress": 1.0, "message": "扩展安装完成"})
        except asyncio.CancelledError:
            record.update({"status": "cancelled", "phase": "cancelled", "error": "扩展安装已取消", "message": "扩展安装已取消"})
        except Exception as exc:
            record.update({"status": "failed", "phase": "failed", "error": str(exc)[:10000], "message": "扩展安装失败"})
            registry = _extension_registry()
            registry["packages"].setdefault(record["package_id"], {})["error"] = record["error"]
            _atomic_json(EXTENSION_REGISTRY_FILE, registry)
        finally:
            if engine_stopped:
                try:
                    from app.services import node_engine_service

                    await node_engine_service.start_engine(90)
                except Exception as restart_exc:
                    restart_error = f"节点引擎恢复启动失败：{restart_exc}"
                    record["error"] = f"{record.get('error') or ''}\n{restart_error}".strip()
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(backup, ignore_errors=True)
            record["completed_at"] = _now_ms()
            self._persist(record)

    def get(self, task_id: str) -> Dict[str, Any]:
        record = self.records.get(task_id) or _read_json(EXTENSION_TASK_DIR / f"{task_id}.json", {})
        if not record:
            raise HTTPException(status_code=404, detail="扩展任务不存在")
        self.records[task_id] = record
        return deepcopy(record)

    def cancel(self, task_id: str) -> Dict[str, Any]:
        record = self.get(task_id)
        if record.get("status") in TERMINAL_STATES:
            return record
        event = self.cancel_events.get(task_id)
        if event:
            event.set()
        return record


async def set_extension_enabled(package_id: str, enabled: bool, wait_seconds: int = 90) -> Dict[str, Any]:
    if not PACKAGE_ID_RE.fullmatch(package_id) or package_id.casefold() == "syncanvas_bridge":
        raise HTTPException(status_code=422, detail="扩展包 ID 无效")
    source = DISABLED_CUSTOM_NODES_DIR / package_id if enabled else CUSTOM_NODES_DIR / package_id
    target = CUSTOM_NODES_DIR / package_id if enabled else DISABLED_CUSTOM_NODES_DIR / package_id
    if target.exists():
        return list_extensions()
    if not source.is_dir():
        raise HTTPException(status_code=404, detail=f"扩展不存在：{package_id}")
    from app.services import node_engine_service

    was_running = node_engine_service.process_status(probe=True).get("ready", False)
    if was_running:
        await node_engine_service.stop_engine()
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)
    if was_running:
        await node_engine_service.start_engine(wait_seconds)
    else:
        node_engine_service.invalidate_catalog()
    return list_extensions()


async def remove_extension(package_id: str, wait_seconds: int = 90) -> Dict[str, Any]:
    if not PACKAGE_ID_RE.fullmatch(package_id) or package_id.casefold() == "syncanvas_bridge":
        raise HTTPException(status_code=422, detail="扩展包 ID 无效")
    enabled = CUSTOM_NODES_DIR / package_id
    disabled = DISABLED_CUSTOM_NODES_DIR / package_id
    target = enabled if enabled.is_dir() else disabled
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"扩展不存在：{package_id}")
    from app.services import node_engine_service

    was_running = node_engine_service.process_status(probe=True).get("ready", False)
    if was_running:
        await node_engine_service.stop_engine()
    trash = EXTENSION_STAGING_DIR / f"removed-{package_id}-{uuid.uuid4().hex[:8]}"
    trash.parent.mkdir(parents=True, exist_ok=True)
    os.replace(target, trash)
    shutil.rmtree(trash, ignore_errors=True)
    registry = _extension_registry()
    registry["packages"].pop(package_id, None)
    _atomic_json(EXTENSION_REGISTRY_FILE, registry)
    if was_running:
        await node_engine_service.start_engine(wait_seconds)
    else:
        node_engine_service.invalidate_catalog()
    return list_extensions()


model_import_manager = ModelImportManager()
extension_task_manager = ExtensionTaskManager()


def initialize() -> None:
    for path in (MODELS_DIR, CUSTOM_NODES_DIR, DISABLED_CUSTOM_NODES_DIR, TASKS_DIR, EXTENSION_STAGING_DIR):
        path.mkdir(parents=True, exist_ok=True)
    for category in MODEL_CATEGORIES:
        (MODELS_DIR / category).mkdir(parents=True, exist_ok=True)
    write_extra_model_paths()
    model_import_manager.recover()
    extension_task_manager.recover()
