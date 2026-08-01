"""ComfyUI-style custom node discovery and execution.

Extensions are trusted local Python packages. They are imported once during
application startup; rescans only stage changes for the next backend restart.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.core.json_store import atomic_write_json
from app.core.paths import BASE_DIR, DATA_DIR
from app.models.node_extensions import NodeRunCreateRequest


PACKAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
NODE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_RUN_PAYLOAD_BYTES = 4 * 1024 * 1024
TERMINAL_RUN_STATES = {"succeeded", "failed", "cancelled", "interrupted"}


class PortManifest(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(default="", max_length=120)
    name_zh: str = Field(default="", max_length=120)
    types: List[str] = Field(default_factory=lambda: ["any"])
    required: bool = False
    multiple: bool = False


class NodeSizeManifest(BaseModel):
    width: int = Field(default=360, ge=180, le=1600)
    height: int = Field(default=320, ge=100, le=2000)


class NodeManifest(BaseModel):
    id: str
    display_name: str = Field(min_length=1, max_length=120)
    display_name_zh: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    description_zh: str = Field(default="", max_length=500)
    category: str = Field(default="extension", max_length=80)
    icon: str = Field(default="blocks", max_length=80)
    version: int = Field(default=1, ge=1)
    surfaces: List[Literal["classic", "smart"]] = Field(default_factory=lambda: ["classic", "smart"])
    legacy_types: Dict[str, List[str]] = Field(default_factory=dict)
    inputs: List[PortManifest] = Field(default_factory=list)
    outputs: List[PortManifest] = Field(default_factory=list)
    defaults: Dict[str, Any] = Field(default_factory=dict)
    size: Dict[str, NodeSizeManifest] = Field(default_factory=dict)
    execution: Literal["python", "frontend", "host"] = "python"
    backend_class: str = ""
    frontend_key: str = ""


class ExtensionManifest(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    id: str
    name: str = Field(min_length=1, max_length=120)
    name_zh: str = Field(default="", max_length=120)
    version: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    description_zh: str = Field(default="", max_length=1000)
    app_version: str = Field(default="*", max_length=80)
    enabled_by_default: bool = True
    web_directory: str = Field(default="web", max_length=160)
    requirements: str = Field(default="requirements.txt", max_length=160)
    nodes: List[NodeManifest] = Field(min_length=1)


def _model_validate(model, value):
    if hasattr(model, "model_validate"):
        return model.model_validate(value)
    return model.parse_obj(value)


def _model_dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _atomic_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Node data must be JSON serializable: {exc}") from exc


def _safe_child(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    candidate = (root / str(relative or "")).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Path escapes extension directory: {relative}") from exc
    if must_exist and not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate


def _read_json(path: Path) -> Dict[str, Any]:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("node.json exceeds 2 MB")
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("node.json root must be an object")
    return value


def _tree_fingerprint(package_dir: Path) -> str:
    digest = hashlib.sha256()
    interesting = {"node.json", "__init__.py", "requirements.txt"}
    paths: List[Path] = []
    for path in package_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(package_dir).as_posix()
        if path.name in interesting or rel.startswith("web/"):
            paths.append(path)
    for path in sorted(paths, key=lambda item: item.as_posix().lower()):
        rel = path.relative_to(package_dir).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _requirement_names(path: Path) -> List[str]:
    if not path.is_file():
        return []
    names: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if match:
            names.append(match.group(1))
    return names


def _missing_requirements(path: Path) -> List[str]:
    missing = []
    for name in _requirement_names(path):
        try:
            importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    return missing


@dataclass
class DiscoveredPackage:
    directory: Path
    manifest: Optional[ExtensionManifest] = None
    enabled: bool = False
    fingerprint: str = ""
    error: str = ""
    handlers: Dict[str, Any] = field(default_factory=dict)
    module: Any = None
    loaded: bool = False

    @property
    def package_id(self) -> str:
        return self.manifest.id if self.manifest else self.directory.name


class NodeExtensionRegistry:
    def __init__(self, root: Optional[Path] = None, state_file: Optional[Path] = None):
        self.root = Path(root or (Path(BASE_DIR) / "custom_nodes"))
        self.state_file = Path(state_file or (Path(DATA_DIR) / "node_extensions.json"))
        self.packages: Dict[str, DiscoveredPackage] = {}
        self.active_packages: Dict[str, DiscoveredPackage] = {}
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.aliases: Dict[str, Dict[str, str]] = {"classic": {}, "smart": {}}
        self.active_digest = ""
        self.discovered_digest = ""
        self.restart_required = False
        self.forced_restart_required = False
        self.initialized = False

    def _load_settings(self) -> Dict[str, bool]:
        if not self.state_file.is_file():
            return {}
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            enabled = raw.get("enabled", {}) if isinstance(raw, dict) else {}
            return {str(key): bool(value) for key, value in enabled.items()}
        except Exception:
            logging.exception("Failed to read node extension settings")
            return {}

    def _save_settings(self, enabled: Dict[str, bool]) -> None:
        _atomic_json(self.state_file, {"schema_version": 1, "enabled": enabled})

    def _validate_manifest(self, raw: Dict[str, Any], directory: Path) -> ExtensionManifest:
        manifest = _model_validate(ExtensionManifest, raw)
        if not PACKAGE_ID_RE.fullmatch(manifest.id):
            raise ValueError("Extension id must use lowercase letters, digits, '.', '_' or '-'")
        seen = set()
        for node in manifest.nodes:
            if not NODE_ID_RE.fullmatch(node.id):
                raise ValueError(f"Invalid node id: {node.id}")
            if node.id in seen:
                raise ValueError(f"Duplicate node id: {node.id}")
            seen.add(node.id)
            if not node.frontend_key:
                node.frontend_key = node.id
            if node.execution == "python" and not node.backend_class:
                node.backend_class = node.id
            for port in [*node.inputs, *node.outputs]:
                if not NODE_ID_RE.fullmatch(port.id):
                    raise ValueError(f"Invalid port id: {node.id}.{port.id}")
                if not port.types or any(not str(kind).strip() for kind in port.types):
                    raise ValueError(f"Port types cannot be empty: {node.id}.{port.id}")
        _safe_child(directory, manifest.web_directory)
        _safe_child(directory, manifest.requirements)
        return manifest

    def _discover(self) -> Dict[str, DiscoveredPackage]:
        self.root.mkdir(parents=True, exist_ok=True)
        settings = self._load_settings()
        result: Dict[str, DiscoveredPackage] = {}
        for directory in sorted(self.root.iterdir(), key=lambda item: item.name.lower()):
            if not directory.is_dir() or directory.name.startswith((".", "_")):
                continue
            package = DiscoveredPackage(directory=directory)
            manifest_path = directory / "node.json"
            try:
                if directory.resolve().parent != self.root.resolve():
                    raise ValueError("Extension directory must be directly inside custom_nodes")
                if not manifest_path.is_file():
                    raise ValueError("Missing node.json")
                manifest = self._validate_manifest(_read_json(manifest_path), directory)
                if manifest.id in result:
                    raise ValueError(f"Duplicate extension id: {manifest.id}")
                package.manifest = manifest
                package.enabled = settings.get(manifest.id, manifest.enabled_by_default)
                package.fingerprint = _tree_fingerprint(directory)
                result[manifest.id] = package
            except Exception as exc:
                package.error = str(exc)
                result[f"invalid:{directory.name}"] = package
        return result

    def _digest(self, packages: Dict[str, DiscoveredPackage]) -> str:
        rows = [
            f"{key}:{package.fingerprint}:{int(package.enabled)}:{package.error}"
            for key, package in sorted(packages.items())
        ]
        return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()

    def _import_package(self, package: DiscoveredPackage) -> None:
        assert package.manifest is not None
        init_path = package.directory / "__init__.py"
        python_nodes = [node for node in package.manifest.nodes if node.execution == "python"]
        if not python_nodes:
            package.loaded = True
            return
        if not init_path.is_file():
            raise ValueError("Python nodes require __init__.py")
        module_name = "syncanvas_custom_nodes." + re.sub(r"[^a-zA-Z0-9_]", "_", package.manifest.id)
        spec = importlib.util.spec_from_file_location(
            module_name,
            init_path,
            submodule_search_locations=[str(package.directory)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to create import spec for {package.manifest.id}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        mappings = getattr(module, "NODE_CLASS_MAPPINGS", None)
        if not isinstance(mappings, dict):
            raise ValueError("__init__.py must export NODE_CLASS_MAPPINGS")
        for node in python_nodes:
            handler = mappings.get(node.backend_class) or mappings.get(node.id)
            if handler is None:
                raise ValueError(f"Missing Python handler mapping: {node.backend_class or node.id}")
            package.handlers[node.id] = handler
        package.module = module
        package.loaded = True

    def _register_active_nodes(self, packages: Optional[Dict[str, DiscoveredPackage]] = None) -> None:
        self.nodes = {}
        self.aliases = {"classic": {}, "smart": {}}
        for package_id, package in (packages or self.packages).items():
            if not package.manifest or not package.enabled or package.error or not package.loaded:
                continue
            for node in package.manifest.nodes:
                canonical = f"{package_id}/{node.id}"
                if canonical in self.nodes:
                    package.error = f"Duplicate canonical node type: {canonical}"
                    continue
                public_node = _model_dump(node)
                public_node.update({
                    "type": canonical,
                    "package_id": package_id,
                    "package_name": package.manifest.name,
                    "package_name_zh": package.manifest.name_zh,
                })
                self.nodes[canonical] = public_node
                for surface in node.surfaces:
                    self.aliases[surface][canonical] = canonical
                    for alias in node.legacy_types.get(surface, []):
                        current = self.aliases[surface].get(alias)
                        if current and current != canonical:
                            package.error = f"Duplicate {surface} node alias: {alias}"
                        else:
                            self.aliases[surface][alias] = canonical

    def initialize(self) -> Dict[str, Any]:
        packages = self._discover()
        for package in packages.values():
            if not package.manifest or not package.enabled or package.error:
                continue
            try:
                self._import_package(package)
            except Exception as exc:
                package.error = str(exc)
                logging.exception("Failed to load node extension %s", package.package_id)
        self.packages = packages
        self.active_packages = dict(packages)
        self._register_active_nodes(self.active_packages)
        self.active_digest = self._digest(packages)
        self.discovered_digest = self.active_digest
        self.restart_required = False
        self.forced_restart_required = False
        self.initialized = True
        return self.public_state()

    def rescan(self) -> Dict[str, Any]:
        if not self.initialized:
            return self.initialize()
        discovered = self._discover()
        self.discovered_digest = self._digest(discovered)
        self.restart_required = self.forced_restart_required or self.discovered_digest != self.active_digest
        active_by_id = self.active_packages
        for package_id, package in discovered.items():
            active = active_by_id.get(package_id)
            if active and active.loaded and active.fingerprint == package.fingerprint and active.enabled == package.enabled:
                package.loaded = True
                package.module = active.module
                package.handlers = active.handlers
                package.error = package.error or active.error
        self.packages = discovered
        self._register_active_nodes(self.active_packages)
        return self.public_state()

    def set_enabled(self, package_id: str, enabled: bool) -> Dict[str, Any]:
        package = self.packages.get(package_id)
        if not package or not package.manifest:
            raise HTTPException(status_code=404, detail="Node extension not found")
        settings = self._load_settings()
        settings[package_id] = bool(enabled)
        self._save_settings(settings)
        return self.rescan()

    def resolve_node(self, node_type: str, surface: str = "") -> Optional[Dict[str, Any]]:
        canonical = node_type
        if surface in self.aliases:
            canonical = self.aliases[surface].get(node_type, node_type)
        if canonical not in self.nodes:
            for aliases in self.aliases.values():
                candidate = aliases.get(node_type)
                if candidate in self.nodes:
                    canonical = candidate
                    break
        return self.nodes.get(canonical)

    def handler_for(self, node_type: str) -> tuple[Dict[str, Any], Any]:
        node = self.resolve_node(node_type)
        if not node:
            raise HTTPException(status_code=404, detail=f"Node type is unavailable: {node_type}")
        if node.get("execution") != "python":
            raise HTTPException(status_code=409, detail=f"Node type does not use the Python runtime: {node_type}")
        package = self.active_packages.get(node["package_id"])
        handler = package.handlers.get(node["id"]) if package else None
        if handler is None:
            raise HTTPException(status_code=503, detail=f"Node handler is not loaded: {node_type}")
        return node, handler

    def _package_public(self, package_id: str, package: DiscoveredPackage) -> Dict[str, Any]:
        manifest = package.manifest
        if not manifest:
            return {
                "id": package_id,
                "directory": package.directory.name,
                "enabled": False,
                "loaded": False,
                "status": "invalid",
                "error": package.error,
                "nodes": [],
            }
        requirements_path = _safe_child(package.directory, manifest.requirements)
        missing = _missing_requirements(requirements_path)
        if package.error:
            status = "error"
        elif not package.enabled:
            status = "disabled"
        elif self.restart_required and not package.loaded:
            status = "pending_restart"
        elif missing:
            status = "missing_dependencies"
        elif package.loaded:
            status = "loaded"
        else:
            status = "pending_restart"
        web_module = ""
        web_path = _safe_child(package.directory, manifest.web_directory)
        if web_path.is_dir() and (web_path / "index.js").is_file():
            web_module = f"/api/node-extensions/{manifest.id}/web/index.js"
        return {
            "id": manifest.id,
            "name": manifest.name,
            "name_zh": manifest.name_zh,
            "version": manifest.version,
            "description": manifest.description,
            "description_zh": manifest.description_zh,
            "app_version": manifest.app_version,
            "directory": package.directory.name,
            "enabled": package.enabled,
            "loaded": package.loaded,
            "status": status,
            "error": package.error,
            "fingerprint": package.fingerprint[:16],
            "web_module": web_module,
            "styles": [f"/api/node-extensions/{manifest.id}/web/styles.css"] if (web_path / "styles.css").is_file() else [],
            "requirements": _requirement_names(requirements_path),
            "missing_dependencies": missing,
            "nodes": [
                {
                    **_model_dump(node),
                    "type": f"{manifest.id}/{node.id}",
                    "package_id": manifest.id,
                    "package_name": manifest.name,
                    "package_name_zh": manifest.name_zh,
                }
                for node in manifest.nodes
            ],
        }

    def public_state(self) -> Dict[str, Any]:
        packages = [self._package_public(key, package) for key, package in sorted(self.packages.items())]
        active_nodes = [deepcopy(node) for _, node in sorted(self.nodes.items())]
        revision_source = self.discovered_digest or self.active_digest or "empty"
        return {
            "schema_version": 1,
            "revision": revision_source[:16],
            "restart_required": self.restart_required,
            "root": "custom_nodes",
            "packages": packages,
            "nodes": active_nodes,
            "aliases": deepcopy(self.aliases),
        }

    async def install_dependencies(self, package_id: str, confirmed: bool) -> Dict[str, Any]:
        if not confirmed:
            raise HTTPException(status_code=400, detail="Dependency installation requires explicit confirmation")
        package = self.packages.get(package_id)
        if not package or not package.manifest:
            raise HTTPException(status_code=404, detail="Node extension not found")
        requirements = _safe_child(package.directory, package.manifest.requirements)
        if not requirements.is_file() or not _requirement_names(requirements):
            return {"package_id": package_id, "installed": True, "restart_required": self.restart_required, "log": "No dependencies declared."}

        def run_pip():
            return subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
                cwd=str(package.directory),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
                check=False,
            )

        try:
            completed = await asyncio.to_thread(run_pip)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="Dependency installation timed out") from exc
        log = (completed.stdout + "\n" + completed.stderr).strip()[-20000:]
        if completed.returncode != 0:
            raise HTTPException(status_code=500, detail={"message": "Dependency installation failed", "log": log})
        self.restart_required = True
        self.forced_restart_required = True
        return {"package_id": package_id, "installed": True, "restart_required": True, "log": log}

    def web_asset_path(self, package_id: str, asset_path: str) -> Path:
        package = self.packages.get(package_id)
        if not package or not package.manifest or not package.enabled or package.error:
            raise HTTPException(status_code=404, detail="Node extension web assets are unavailable")
        web_root = _safe_child(package.directory, package.manifest.web_directory, must_exist=True)
        try:
            path = _safe_child(web_root, asset_path, must_exist=True)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="Node extension web asset not found") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Node extension web asset not found")
        return path


class NodeExecutionContext:
    def __init__(self, run_id: str, payload: NodeRunCreateRequest, app: Any, record: Dict[str, Any]):
        self.run_id = run_id
        self.canvas_id = payload.canvas_id
        self.node_id = payload.node_id
        self.app = app
        self._record = record

    def progress(self, value: float, message: str = "") -> None:
        self._record["progress"] = max(0.0, min(1.0, float(value)))
        self._record["message"] = str(message or "")[:500]


def _infer_value(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict) and "kind" in value and "value" in value:
        return {"kind": str(value["kind"]), "value": value["value"], "metadata": value.get("metadata", {})}
    if isinstance(value, str):
        kind = "image" if value.startswith(("/assets/", "/output/", "http://", "https://")) and re.search(r"\.(png|jpe?g|webp|gif)(\?|$)", value, re.I) else "text"
        return {"kind": kind, "value": value, "metadata": {}}
    return {"kind": "json", "value": value, "metadata": {}}


def _normalize_result(result: Any) -> Dict[str, Any]:
    raw = result if isinstance(result, dict) else {"outputs": {"result": result}}
    outputs = raw.get("outputs")
    if not isinstance(outputs, dict):
        outputs = {"result": raw}
    normalized: Dict[str, List[Dict[str, Any]]] = {}
    for port, values in outputs.items():
        items = values if isinstance(values, list) else [values]
        normalized[str(port)] = [_infer_value(value) for value in items]
    payload = {"outputs": normalized}
    flat = [item for items in normalized.values() for item in items]
    text_value = next((item["value"] for item in flat if item["kind"] == "text"), "")
    json_value = next((item["value"] for item in flat if item["kind"] == "json"), None)
    images = [item["value"] for item in flat if item["kind"] == "image"]
    payload.update({"output_text": str(text_value or ""), "structured_output": json_value, "images": images})
    if _json_size(payload) > MAX_RUN_PAYLOAD_BYTES:
        raise ValueError("Node result exceeds 4 MB")
    return payload


class NodeRunManager:
    def __init__(self, registry_instance: NodeExtensionRegistry, run_dir: Optional[Path] = None):
        self.registry = registry_instance
        self.run_dir = Path(run_dir or (Path(DATA_DIR) / "node-runs"))
        self.records: Dict[str, Dict[str, Any]] = {}
        self.tasks: Dict[str, asyncio.Task] = {}

    def _persist(self, record: Dict[str, Any]) -> None:
        _atomic_json(self.run_dir / f"{record['run_id']}.json", record)

    def recover(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        for path in self.run_dir.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("status") not in TERMINAL_RUN_STATES:
                    record["status"] = "interrupted"
                    record["error"] = "Application restarted while the node was running"
                    record["completed_at"] = _now_ms()
                    _atomic_json(path, record)
                self.records[record["run_id"]] = record
            except Exception:
                continue

    def active_records(self) -> List[Dict[str, Any]]:
        return [deepcopy(record) for record in self.records.values() if record.get("status") not in TERMINAL_RUN_STATES]

    def submit(self, payload: NodeRunCreateRequest, app: Any = None) -> Dict[str, Any]:
        if _json_size({"state": payload.state, "inputs": payload.inputs, "context": payload.context}) > MAX_RUN_PAYLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Node run payload exceeds 4 MB")
        node, handler = self.registry.handler_for(payload.node_type)
        target_version = int(node.get("version") or 1)
        if payload.node_version > target_version:
            raise HTTPException(
                status_code=422,
                detail=f"Node state version {payload.node_version} is newer than supported version {target_version}",
            )
        run_id = uuid.uuid4().hex
        record = {
            "run_id": run_id,
            "node_type": node["type"],
            "package_id": node["package_id"],
            "node_version": target_version,
            "source_node_version": payload.node_version,
            "canvas_id": payload.canvas_id,
            "node_id": payload.node_id,
            "status": "queued",
            "progress": 0.0,
            "message": "",
            "created_at": _now_ms(),
            "started_at": None,
            "completed_at": None,
            "duration_ms": None,
            "result": None,
            "error": "",
        }
        self.records[run_id] = record
        self._persist(record)
        task = asyncio.create_task(self._execute(run_id, node, handler, payload, app))
        self.tasks[run_id] = task
        task.add_done_callback(lambda _task, rid=run_id: self.tasks.pop(rid, None))
        return deepcopy(record)

    async def _migrate_state(
        self,
        instance: Any,
        state: Dict[str, Any],
        source_version: int,
        target_version: int,
    ) -> Dict[str, Any]:
        current = int(source_version or 1)
        migrated = deepcopy(state)
        migrations = getattr(instance, "STATE_MIGRATIONS", {})
        while current < target_version:
            migration = migrations.get(current) if isinstance(migrations, dict) else None
            if not callable(migration):
                raise ValueError(f"Missing state migration from node version {current} to {current + 1}")
            migrated = migration(deepcopy(migrated))
            if inspect.isawaitable(migrated):
                migrated = await migrated
            if not isinstance(migrated, dict):
                raise TypeError(f"State migration {current} must return an object")
            current += 1
        return migrated

    async def _execute(
        self,
        run_id: str,
        node: Dict[str, Any],
        handler: Any,
        payload: NodeRunCreateRequest,
        app: Any,
    ) -> None:
        record = self.records[run_id]
        try:
            record["status"] = "running"
            record["started_at"] = _now_ms()
            self._persist(record)
            instance = handler() if inspect.isclass(handler) else handler
            execute = getattr(instance, "execute", None)
            if not callable(execute):
                raise TypeError("Node handler must define execute(context, state, inputs)")
            context = NodeExecutionContext(run_id, payload, app, record)
            state = await self._migrate_state(
                instance,
                payload.state,
                payload.node_version,
                int(node.get("version") or 1),
            )
            result = execute(context, state, deepcopy(payload.inputs))
            if inspect.isawaitable(result):
                result = await result
            record["result"] = _normalize_result(result)
            record["progress"] = 1.0
            record["status"] = "succeeded"
        except asyncio.CancelledError:
            record["status"] = "cancelled"
            record["error"] = "Node run cancelled"
        except Exception as exc:
            logging.exception("Node extension run failed: %s", record.get("node_type"))
            record["status"] = "failed"
            record["error"] = str(exc)[:5000]
        finally:
            record["completed_at"] = _now_ms()
            if record.get("started_at"):
                record["duration_ms"] = record["completed_at"] - record["started_at"]
            self._persist(record)

    def get(self, run_id: str) -> Dict[str, Any]:
        record = self.records.get(run_id)
        if not record:
            path = self.run_dir / f"{run_id}.json"
            if path.is_file():
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    self.records[run_id] = record
                except Exception:
                    record = None
        if not record:
            raise HTTPException(status_code=404, detail="Node run not found")
        return deepcopy(record)

    def cancel(self, run_id: str) -> Dict[str, Any]:
        record = self.get(run_id)
        if record.get("status") in TERMINAL_RUN_STATES:
            return record
        task = self.tasks.get(run_id)
        if task:
            task.cancel()
        current = self.records[run_id]
        current["status"] = "cancelled"
        current["error"] = "Node run cancelled"
        current["completed_at"] = _now_ms()
        self._persist(current)
        return deepcopy(current)


registry = NodeExtensionRegistry()
run_manager = NodeRunManager(registry)


def initialize_node_extensions() -> Dict[str, Any]:
    state = registry.initialize()
    run_manager.recover()
    return state


def apply_extension_changes(restart_delay: int = 3) -> Dict[str, Any]:
    active = run_manager.active_records()
    if active:
        raise HTTPException(
            status_code=409,
            detail={"message": "Node extensions are still running", "runs": active},
        )
    state = registry.rescan()
    if not state["restart_required"]:
        return {"restart_scheduled": False, "restart_required": False}
    from app.services.system_service import schedule_self_restart

    scheduled = schedule_self_restart(restart_delay)
    if not scheduled:
        raise HTTPException(status_code=500, detail="Unable to schedule backend restart")
    return {"restart_scheduled": True, "restart_required": True, "delay": restart_delay}
