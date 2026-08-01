from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from app.core.json_store import atomic_write_json, read_json_resilient
from app.core.platforms import current_platform_tag, normalize_platforms, platform_supported


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
MANIFEST_FILE = BASE_DIR / "components-manifest.json"
COMPONENT_ID = "digital-human"
DEFAULT_INSTALL_ROOT = BASE_DIR / "components" / COMPONENT_ID
LEGACY_TTS_ROOT = BASE_DIR / "index-tts-2"
LEGACY_HEYGEM_ROOT = BASE_DIR / "heygem-win-fix" / "heygem-win"
COMPONENT_DATA_DIR = DATA_DIR / "components"
STATE_FILE = COMPONENT_DATA_DIR / f"{COMPONENT_ID}-state.json"
REGISTRY_FILE = COMPONENT_DATA_DIR / f"{COMPONENT_ID}-installed.json"
DOWNLOAD_CACHE_DIR = DATA_DIR / "component-cache" / COMPONENT_ID
ACTIVE_STATES = {"queued", "downloading", "verifying", "installing", "cancelling"}
CHUNK_SIZE = 1024 * 1024

_STATE_LOCK = threading.RLock()
_WORKER_LOCK = threading.Lock()
_CANCEL_EVENT = threading.Event()
_WORKER: Optional[threading.Thread] = None


class ComponentError(RuntimeError):
    pass


class ComponentBusyError(ComponentError):
    pass


class ComponentCancelled(ComponentError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_json(path: Path, fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = read_json_resilient(path, dict(fallback or {}))
    return data if isinstance(data, dict) else dict(fallback or {})


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    atomic_write_json(path, payload)


def _normalize_manual_download(value: Any, filename: str) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    share_url = str(value.get("share_url") or "").strip()
    parsed = urllib.parse.urlsplit(share_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {}
    source_filename = Path(str(value.get("filename") or filename)).name
    if source_filename != filename:
        return {}
    provider = str(value.get("provider") or "external").strip()[:64] or "external"
    extraction_code = str(value.get("extraction_code") or "").strip()[:64]
    return {
        "provider": provider,
        "share_url": share_url[:2048],
        "extraction_code": extraction_code,
        "filename": filename,
    }


def _normalize_manifest(payload: Dict[str, Any]) -> Dict[str, Any]:
    component = payload.get("component") if isinstance(payload.get("component"), dict) else {}
    if component.get("id") != COMPONENT_ID:
        raise ComponentError("组件清单无效：缺少 digital-human 组件定义")
    artifacts = component.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ComponentError("组件清单无效：没有可安装文件")
    normalized = []
    for raw in artifacts:
        if not isinstance(raw, dict):
            continue
        artifact = dict(raw)
        artifact_id = str(artifact.get("id") or "").strip()
        filename = Path(str(artifact.get("filename") or "")).name
        target = str(artifact.get("target") or artifact_id).strip()
        if not artifact_id or not filename or not target or "/" in target or "\\" in target:
            raise ComponentError("组件清单无效：安装文件定义不完整")
        artifact["id"] = artifact_id
        artifact["filename"] = filename
        artifact["target"] = target
        artifact["platforms"] = normalize_platforms(artifact.get("platforms"))
        artifact["urls"] = [str(value).strip() for value in artifact.get("urls") or [] if str(value).strip()]
        manual_download = _normalize_manual_download(artifact.get("manual_download"), filename)
        if manual_download:
            artifact["manual_download"] = manual_download
        else:
            artifact.pop("manual_download", None)
        artifact["sentinels"] = [str(value).replace("\\", "/").strip("/") for value in artifact.get("sentinels") or [] if str(value).strip()]
        artifact["preserve_paths"] = [
            str(value).replace("\\", "/").strip("/")
            for value in artifact.get("preserve_paths") or []
            if str(value).strip()
        ]
        normalized.append(artifact)
    if not normalized:
        raise ComponentError("组件清单无效：没有可安装文件")
    result = dict(payload)
    result["component"] = {**component, "artifacts": normalized}
    return result


def _fetch_json(url: str, timeout: int = 30) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SynCanvas-ComponentManager/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8-sig"))


def load_component_manifest(manifest_url: str = "", remote_required: bool = False) -> Dict[str, Any]:
    configured_url = str(manifest_url or os.getenv("SYNCANVAS_COMPONENT_MANIFEST_URL", "")).strip()
    if configured_url:
        try:
            return _normalize_manifest(_fetch_json(configured_url))
        except Exception as exc:
            if remote_required:
                raise ComponentError(f"无法读取数字人组件清单：{exc}") from exc
    if not MANIFEST_FILE.is_file():
        raise ComponentError(f"数字人组件清单不存在：{MANIFEST_FILE}")
    return _normalize_manifest(_read_json(MANIFEST_FILE))


def _artifact_map(manifest: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in manifest["component"].get("artifacts") or []
        if isinstance(item, dict)
    }


def _sentinels_ready(root: Path, artifact: Dict[str, Any]) -> bool:
    sentinels = artifact.get("sentinels") or []
    return bool(sentinels) and all((root / relative).exists() for relative in sentinels)


def _sentinel_count(root: Path, artifact: Dict[str, Any]) -> int:
    return sum(1 for relative in artifact.get("sentinels") or [] if (root / relative).exists())


def _registry_install_root() -> Optional[Path]:
    registry = _read_json(REGISTRY_FILE)
    value = str(registry.get("install_root") or "").strip()
    return Path(value).expanduser().resolve() if value else None


def _layout_for_root(root: Path, source: str) -> Dict[str, Any]:
    return {
        "source": source,
        "install_root": root,
        "tts": root / "tts",
        "heygem": root / "heygem",
    }


def _legacy_layout() -> Dict[str, Any]:
    return {
        "source": "legacy",
        "install_root": BASE_DIR,
        "tts": LEGACY_TTS_ROOT,
        "heygem": LEGACY_HEYGEM_ROOT,
    }


def _candidate_layouts() -> List[Dict[str, Any]]:
    layouts: List[Dict[str, Any]] = []
    registry_root = _registry_install_root()
    if registry_root:
        layouts.append(_layout_for_root(registry_root, "managed"))
    default_resolved = DEFAULT_INSTALL_ROOT.resolve()
    if not registry_root or registry_root != default_resolved:
        layouts.append(_layout_for_root(default_resolved, "managed"))
    layouts.append(_legacy_layout())
    return layouts


def _layout_score(layout: Dict[str, Any], artifacts: Dict[str, Dict[str, Any]]) -> Tuple[int, int]:
    ready = 0
    present = 0
    for artifact_id in ("tts", "heygem"):
        artifact = artifacts.get(artifact_id) or {}
        root = Path(layout[artifact_id])
        if _sentinels_ready(root, artifact):
            ready += 1
        present += _sentinel_count(root, artifact)
    return ready, present


def resolve_digital_human_roots(manifest: Optional[Dict[str, Any]] = None) -> Tuple[Path, Path, str]:
    manifest = manifest or load_component_manifest()
    artifacts = _artifact_map(manifest)
    layouts = _candidate_layouts()
    complete = [layout for layout in layouts if _layout_score(layout, artifacts)[0] == 2]
    selected = complete[0] if complete else max(layouts, key=lambda item: _layout_score(item, artifacts))
    return Path(selected["tts"]), Path(selected["heygem"]), str(selected["source"])


def digital_human_component_ready() -> bool:
    try:
        manifest = load_component_manifest()
        artifacts = _artifact_map(manifest)
        tts_root, heygem_root, _ = resolve_digital_human_roots(manifest)
        return _sentinels_ready(tts_root, artifacts["tts"]) and _sentinels_ready(heygem_root, artifacts["heygem"])
    except Exception:
        return False


def _load_state() -> Dict[str, Any]:
    with _STATE_LOCK:
        return _read_json(STATE_FILE)


def _replace_state(payload: Dict[str, Any]) -> Dict[str, Any]:
    with _STATE_LOCK:
        state = {**payload, "updated_at": _now_iso()}
        _write_json(STATE_FILE, state)
        return state


def _update_state(**values: Any) -> Dict[str, Any]:
    with _STATE_LOCK:
        state = _read_json(STATE_FILE)
        state.update(values)
        state["updated_at"] = _now_iso()
        _write_json(STATE_FILE, state)
        return state


def recover_interrupted_component_install() -> None:
    state = _load_state()
    if state.get("state") in ACTIVE_STATES:
        _update_state(
            state="interrupted",
            phase="interrupted",
            message="上次安装被中断，可以继续下载并安装",
            error="",
        )


def _component_base_url() -> str:
    return str(os.getenv("SYNCANVAS_COMPONENT_BASE_URL", "")).strip().rstrip("/")


def _artifact_urls(artifact: Dict[str, Any]) -> List[str]:
    urls = list(artifact.get("urls") or [])
    base_url = _component_base_url()
    if base_url:
        urls.append(f"{base_url}/{urllib.parse.quote(str(artifact['filename']))}")
    return list(dict.fromkeys(urls))


def _local_artifact_candidates(artifact: Dict[str, Any]) -> Iterable[Path]:
    filename = artifact["filename"]
    yield BASE_DIR / "packages" / "components" / filename
    yield BASE_DIR / filename
    yield BASE_DIR.parent / filename
    yield Path.home() / "Downloads" / filename
    yield MANIFEST_FILE.parent / filename


def _valid_sha256(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _artifact_source_available(artifact: Dict[str, Any]) -> bool:
    return bool(_local_artifact_source_available(artifact) or _artifact_urls(artifact)) and _valid_sha256(artifact.get("sha256"))


def _local_artifact_source_available(artifact: Dict[str, Any]) -> bool:
    return any(path.is_file() for path in _local_artifact_candidates(artifact))


def _disk_usage(path: Path) -> shutil._ntuple_diskusage:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe)


def get_component_status() -> Dict[str, Any]:
    try:
        manifest = load_component_manifest()
        component = manifest["component"]
        artifacts = _artifact_map(manifest)
        tts_root, heygem_root, source = resolve_digital_human_roots(manifest)
        roots = {"tts": tts_root, "heygem": heygem_root}
        artifact_statuses = []
        ready_count = 0
        present_count = 0
        for artifact_id, artifact in artifacts.items():
            root = roots.get(artifact_id, DEFAULT_INSTALL_ROOT / artifact.get("target", artifact_id))
            artifact_platform_supported = platform_supported(artifact.get("platforms"))
            ready = artifact_platform_supported and _sentinels_ready(root, artifact)
            present = _sentinel_count(root, artifact)
            local_source_available = artifact_platform_supported and _local_artifact_source_available(artifact)
            direct_download_available = artifact_platform_supported and bool(_artifact_urls(artifact))
            ready_count += 1 if ready else 0
            present_count += present
            artifact_statuses.append(
                {
                    "id": artifact_id,
                    "display_name": artifact.get("display_name") or artifact_id,
                    "version": artifact.get("version") or component.get("version") or "",
                    "state": "ready" if ready else ("partial" if present else "not_installed"),
                    "ready": ready,
                    "root": str(root),
                    "download_size": int(artifact.get("download_size") or 0),
                    "installed_size": int(artifact.get("installed_size") or 0),
                    "filename": artifact.get("filename") or "",
                    "platforms": list(artifact.get("platforms") or []),
                    "platform_supported": artifact_platform_supported,
                    "source_available": bool(local_source_available or direct_download_available)
                    and _valid_sha256(artifact.get("sha256")),
                    "local_source_available": local_source_available,
                    "direct_download_available": direct_download_available,
                    "manual_download": dict(artifact.get("manual_download") or {}) if artifact_platform_supported else {},
                }
            )

        task = _load_state()
        task_state = str(task.get("state") or "")
        all_platform_supported = all(item["platform_supported"] for item in artifact_statuses)
        if not all_platform_supported:
            state = "unsupported"
        elif task_state in ACTIVE_STATES:
            state = task_state
        elif ready_count == len(artifacts):
            state = "ready"
        elif ready_count or present_count:
            state = "partial"
        elif task_state in {"error", "cancelled", "interrupted"}:
            state = task_state
        else:
            state = "not_installed"

        install_root_value = str(task.get("install_root") or "")
        install_root = Path(install_root_value).expanduser() if install_root_value else DEFAULT_INSTALL_ROOT
        try:
            free_bytes = int(_disk_usage(install_root).free)
        except OSError:
            free_bytes = 0
        minimum_free = int(component.get("minimum_free_bytes") or 0)
        source_ready = all(item["source_available"] for item in artifact_statuses)
        manual_download_available = any(item["manual_download"].get("share_url") for item in artifact_statuses)
        return {
            "ok": True,
            "component_id": COMPONENT_ID,
            "display_name": component.get("display_name") or "数字人组件",
            "version": component.get("version") or "",
            "state": state,
            "platform": current_platform_tag(),
            "supported": all_platform_supported,
            "ready": state == "ready",
            "installed_source": source if ready_count else "",
            "install_root": str(install_root),
            "tts_root": str(tts_root),
            "heygem_root": str(heygem_root),
            "download_size": int(component.get("download_size") or 0),
            "installed_size": int(component.get("installed_size") or 0),
            "minimum_free_bytes": minimum_free,
            "free_bytes": free_bytes,
            "enough_space": free_bytes >= minimum_free if minimum_free else True,
            "can_install": source_ready and all_platform_supported,
            "manual_download_available": manual_download_available,
            "manual_download_required": manual_download_available and not source_ready and state != "ready",
            "manifest_url": str(os.getenv("SYNCANVAS_COMPONENT_MANIFEST_URL", "")).strip(),
            "component_base_url": _component_base_url(),
            "artifacts": artifact_statuses,
            "task": task,
            "progress_percent": float(task.get("progress_percent") or (100 if state == "ready" else 0)),
            "phase": str(task.get("phase") or state),
            "message": str(task.get("message") or ""),
            "error": str(task.get("error") or ""),
        }
    except Exception as exc:
        return {
            "ok": False,
            "component_id": COMPONENT_ID,
            "state": "error",
            "ready": False,
            "can_install": False,
            "error": str(exc),
            "artifacts": [],
            "task": {},
            "progress_percent": 0,
        }


def _assert_cancelled() -> None:
    if _CANCEL_EVENT.is_set():
        raise ComponentCancelled("数字人组件安装已取消")


def _safe_child(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    root_resolved = root.resolve()
    try:
        common = Path(os.path.commonpath([str(root_resolved), str(target)]))
    except ValueError as exc:
        raise ComponentError(f"组件包包含非法路径：{relative}") from exc
    if common != root_resolved:
        raise ComponentError(f"组件包包含非法路径：{relative}")
    return target


def _remove_tree(path: Path, allowed_root: Path) -> None:
    if not path.exists():
        return
    resolved = path.resolve()
    allowed = allowed_root.resolve()
    if resolved == allowed or Path(os.path.commonpath([str(resolved), str(allowed)])) != allowed:
        raise ComponentError(f"拒绝清理不安全的目录：{resolved}")
    shutil.rmtree(resolved)


def _download_artifact(
    artifact: Dict[str, Any],
    progress: Callable[[int, int, float], None],
) -> Tuple[Path, bool]:
    for candidate in _local_artifact_candidates(artifact):
        if candidate.is_file():
            size = candidate.stat().st_size
            progress(size, size, 0.0)
            return candidate, False

    urls = _artifact_urls(artifact)
    if not urls:
        raise ComponentError(
            f"{artifact.get('display_name') or artifact['id']} 尚未配置下载地址；"
            "请配置 SYNCANVAS_COMPONENT_BASE_URL 或远程组件清单"
        )

    DOWNLOAD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    final_path = DOWNLOAD_CACHE_DIR / artifact["filename"]
    part_path = final_path.with_suffix(final_path.suffix + ".part")
    expected = int(artifact.get("download_size") or 0)
    if final_path.is_file() and (not expected or final_path.stat().st_size == expected):
        progress(final_path.stat().st_size, expected or final_path.stat().st_size, 0.0)
        return final_path, True

    last_error: Optional[Exception] = None
    for url in urls:
        for attempt in range(3):
            _assert_cancelled()
            try:
                existing = part_path.stat().st_size if part_path.is_file() else 0
                headers = {"User-Agent": "SynCanvas-ComponentManager/1.0"}
                if existing:
                    headers["Range"] = f"bytes={existing}-"
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=60) as response:
                    status_code = int(getattr(response, "status", response.getcode()) or 200)
                    append = existing > 0 and status_code == 206
                    if existing and not append:
                        existing = 0
                    content_range = str(response.headers.get("Content-Range") or "")
                    total = expected
                    if "/" in content_range:
                        try:
                            total = int(content_range.rsplit("/", 1)[-1])
                        except ValueError:
                            pass
                    if not total:
                        try:
                            total = existing + int(response.headers.get("Content-Length") or 0)
                        except ValueError:
                            total = 0
                    mode = "ab" if append else "wb"
                    received = existing
                    started = time.monotonic()
                    last_tick = started
                    last_bytes = received
                    with part_path.open(mode) as output:
                        while True:
                            _assert_cancelled()
                            chunk = response.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            output.write(chunk)
                            received += len(chunk)
                            now = time.monotonic()
                            if now - last_tick >= 0.5:
                                speed = (received - last_bytes) / max(0.001, now - last_tick)
                                progress(received, total, speed)
                                last_tick = now
                                last_bytes = received
                    progress(received, total or received, received / max(0.001, time.monotonic() - started))
                if expected and part_path.stat().st_size != expected:
                    raise ComponentError(
                        f"{artifact.get('display_name')} 下载大小不正确："
                        f"{part_path.stat().st_size} / {expected}"
                    )
                os.replace(part_path, final_path)
                return final_path, True
            except ComponentCancelled:
                raise
            except Exception as exc:
                last_error = exc
                time.sleep(min(5, attempt + 1))
        if part_path.exists() and expected and part_path.stat().st_size > expected:
            part_path.unlink(missing_ok=True)
    raise ComponentError(f"{artifact.get('display_name')} 下载失败：{last_error}")


def _verify_sha256(
    path: Path,
    expected: str,
    progress: Callable[[int, int], None],
) -> str:
    expected = str(expected or "").strip().lower()
    if not _valid_sha256(expected):
        raise ComponentError(f"{path.name} 缺少有效 SHA256，已拒绝安装")
    total = path.stat().st_size
    read_bytes = 0
    digest = hashlib.sha256()
    last_tick = 0.0
    with path.open("rb") as handle:
        while True:
            _assert_cancelled()
            chunk = handle.read(4 * CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            read_bytes += len(chunk)
            now = time.monotonic()
            if now - last_tick >= 0.5:
                progress(read_bytes, total)
                last_tick = now
    actual = digest.hexdigest().lower()
    progress(total, total)
    if actual != expected:
        raise ComponentError(f"{path.name} SHA256 校验失败")
    return actual


def _extract_zip(
    archive_path: Path,
    staging: Path,
    progress: Callable[[int, int], None],
) -> None:
    with zipfile.ZipFile(archive_path, "r", allowZip64=True) as archive:
        entries = archive.infolist()
        total = sum(max(0, int(entry.file_size)) for entry in entries)
        extracted = 0
        last_tick = 0.0
        for entry in entries:
            _assert_cancelled()
            relative = entry.filename.replace("\\", "/").lstrip("/")
            if not relative:
                continue
            target = _safe_child(staging, relative)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry, "r") as source, target.open("wb") as output:
                while True:
                    _assert_cancelled()
                    chunk = source.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
                    extracted += len(chunk)
                    now = time.monotonic()
                    if now - last_tick >= 0.5:
                        progress(extracted, total)
                        last_tick = now
        progress(total, total)


def _copy_preserved_paths(old_root: Path, new_root: Path, relative_paths: Iterable[str]) -> None:
    for relative in relative_paths:
        source = _safe_child(old_root, relative)
        if not source.exists():
            continue
        target = _safe_child(new_root, relative)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _install_artifact(
    artifact: Dict[str, Any],
    archive_path: Path,
    install_root: Path,
    progress: Callable[[int, int], None],
) -> Path:
    target = _safe_child(install_root, artifact["target"])
    staging_root = _safe_child(install_root, ".staging")
    staging = _safe_child(staging_root, artifact["id"])
    backup = _safe_child(install_root, f".{artifact['id']}.previous")
    staging_root.mkdir(parents=True, exist_ok=True)
    _remove_tree(staging, install_root)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        _extract_zip(archive_path, staging, progress)
        if not _sentinels_ready(staging, artifact):
            raise ComponentError(f"{artifact.get('display_name')} 解压后缺少关键文件")
        if target.exists():
            _copy_preserved_paths(target, staging, artifact.get("preserve_paths") or [])
        _remove_tree(backup, install_root)
        if target.exists():
            os.replace(target, backup)
        try:
            os.replace(staging, target)
        except Exception:
            if backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        _remove_tree(backup, install_root)
        return target
    finally:
        if staging.exists():
            _remove_tree(staging, install_root)


def _sync_config_paths(tts_root: Path, heygem_root: Path) -> None:
    launcher_path = DATA_DIR / "launcher_config.json"
    launcher = _read_json(launcher_path)
    if launcher:
        launcher.setdefault("tts", {})
        launcher.setdefault("heygem", {})
        launcher["tts"].update(
            {
                "root_dir": str(tts_root),
                "python_path": str(tts_root / "py312" / "python.exe"),
                "script_path": str(tts_root / "app.py"),
            }
        )
        launcher["heygem"].update(
            {
                "root_dir": str(heygem_root),
                "python_path": str(heygem_root / "py38" / "python.exe"),
                "script_path": str(heygem_root / "app.py"),
            }
        )
        _write_json(launcher_path, launcher)

    digital_path = DATA_DIR / "digital_human_config.json"
    digital = _read_json(digital_path)
    if digital:
        digital.setdefault("tts", {})
        digital.setdefault("heygem", {})
        digital["tts"].update(
            {
                "root_dir": str(tts_root),
                "python_path": str(tts_root / "py312" / "python.exe"),
                "script_path": str(tts_root / "app.py"),
                "config_path": str(tts_root / "checkpoints" / "config.yaml"),
                "model_dir": str(tts_root / "checkpoints"),
            }
        )
        digital["heygem"].update(
            {
                "root_dir": str(heygem_root),
                "python_path": str(heygem_root / "py38" / "python.exe"),
            }
        )
        _write_json(digital_path, digital)


def _install_worker(manifest: Dict[str, Any], install_root: Path, force: bool) -> None:
    component = manifest["component"]
    artifacts = list(component.get("artifacts") or [])
    install_root.mkdir(parents=True, exist_ok=True)
    try:
        usage = _disk_usage(install_root)
        minimum_free = int(component.get("minimum_free_bytes") or 0)
        if minimum_free and usage.free < minimum_free:
            raise ComponentError(
                f"磁盘空间不足：需要至少 {minimum_free / (1024 ** 3):.1f} GB，"
                f"当前可用 {usage.free / (1024 ** 3):.1f} GB"
            )
        completed: List[Dict[str, Any]] = []
        total_artifacts = max(1, len(artifacts))
        for index, artifact in enumerate(artifacts):
            _assert_cancelled()
            target = install_root / artifact["target"]
            if not force and _sentinels_ready(target, artifact):
                completed.append(
                    {
                        "id": artifact["id"],
                        "version": artifact.get("version") or component.get("version") or "",
                        "sha256": artifact.get("sha256") or "",
                        "root": str(target),
                    }
                )
                _update_state(progress_percent=round((index + 1) * 100 / total_artifacts, 2))
                continue

            name = str(artifact.get("display_name") or artifact["id"])

            def download_progress(received: int, total: int, speed: float) -> None:
                fraction = received / total if total else 0.0
                overall = (index + 0.70 * min(1.0, fraction)) * 100 / total_artifacts
                eta = int((total - received) / speed) if total and speed > 0 else None
                _update_state(
                    state="downloading",
                    phase="downloading",
                    current_artifact=artifact["id"],
                    current_artifact_name=name,
                    downloaded_bytes=received,
                    total_bytes=total,
                    speed_bytes_per_second=int(speed),
                    eta_seconds=eta,
                    progress_percent=round(overall, 2),
                    message=f"正在下载 {name}",
                    error="",
                )

            archive_path, downloaded_cache = _download_artifact(artifact, download_progress)

            def verify_progress(read_bytes: int, total: int) -> None:
                fraction = read_bytes / total if total else 0.0
                overall = (index + 0.70 + 0.05 * min(1.0, fraction)) * 100 / total_artifacts
                _update_state(
                    state="verifying",
                    phase="verifying",
                    current_artifact=artifact["id"],
                    current_artifact_name=name,
                    downloaded_bytes=read_bytes,
                    total_bytes=total,
                    speed_bytes_per_second=0,
                    eta_seconds=None,
                    progress_percent=round(overall, 2),
                    message=f"正在校验 {name}",
                    error="",
                )

            digest = _verify_sha256(archive_path, artifact.get("sha256") or "", verify_progress)

            def install_progress(extracted: int, total: int) -> None:
                fraction = extracted / total if total else 0.0
                overall = (index + 0.75 + 0.25 * min(1.0, fraction)) * 100 / total_artifacts
                _update_state(
                    state="installing",
                    phase="installing",
                    current_artifact=artifact["id"],
                    current_artifact_name=name,
                    extracted_bytes=extracted,
                    extract_total_bytes=total,
                    progress_percent=round(overall, 2),
                    message=f"正在安装 {name}",
                    error="",
                )

            installed_root = _install_artifact(artifact, archive_path, install_root, install_progress)
            completed.append(
                {
                    "id": artifact["id"],
                    "version": artifact.get("version") or component.get("version") or "",
                    "sha256": digest,
                    "root": str(installed_root),
                }
            )
            if downloaded_cache:
                archive_path.unlink(missing_ok=True)

        artifact_map = {item["id"]: item for item in completed}
        if not {"tts", "heygem"}.issubset(artifact_map):
            raise ComponentError("数字人组件安装不完整")
        registry = {
            "schema_version": 1,
            "component_id": COMPONENT_ID,
            "version": component.get("version") or "",
            "install_root": str(install_root),
            "installed_at": _now_iso(),
            "artifacts": completed,
        }
        _write_json(install_root / "installed.json", registry)
        _write_json(REGISTRY_FILE, registry)
        _sync_config_paths(install_root / "tts", install_root / "heygem")
        _update_state(
            state="ready",
            phase="ready",
            progress_percent=100,
            current_artifact="",
            current_artifact_name="",
            message="数字人组件安装完成",
            error="",
            completed_at=_now_iso(),
        )
    except ComponentCancelled as exc:
        _update_state(
            state="cancelled",
            phase="cancelled",
            message="安装已取消，已保留下载进度",
            error=str(exc),
        )
    except Exception as exc:
        _update_state(
            state="error",
            phase="error",
            message="数字人组件安装失败",
            error=str(exc),
        )


def start_component_install(
    install_root: str = "",
    manifest_url: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            raise ComponentBusyError("数字人组件正在安装")
        manifest = load_component_manifest(manifest_url, remote_required=bool(manifest_url))
        component = manifest["component"]
        artifacts = component.get("artifacts") or []
        unsupported = [
            str(item.get("display_name") or item.get("id"))
            for item in artifacts
            if not platform_supported(item.get("platforms"))
        ]
        if unsupported:
            raise ComponentError(
                f"数字人组件暂不支持当前系统 {current_platform_tag()}：" + "、".join(unsupported)
            )
        missing_sources = [
            str(item.get("display_name") or item.get("id"))
            for item in artifacts
            if not _artifact_source_available(item)
        ]
        if missing_sources:
            raise ComponentError(
                "数字人组件下载源尚未配置："
                + "、".join(missing_sources)
                + "。请提供带 SHA256 和下载地址的 components-manifest.json。"
            )
        root = Path(install_root).expanduser() if str(install_root or "").strip() else DEFAULT_INSTALL_ROOT
        root = root.resolve()
        if root == root.parent:
            raise ComponentError("不能把数字人组件安装到磁盘根目录")
        _CANCEL_EVENT.clear()
        _replace_state(
            {
                "schema_version": 1,
                "component_id": COMPONENT_ID,
                "component_version": component.get("version") or "",
                "state": "queued",
                "phase": "queued",
                "install_root": str(root),
                "progress_percent": 0,
                "message": "数字人组件已加入安装队列",
                "error": "",
                "started_at": _now_iso(),
                "worker_pid": os.getpid(),
            }
        )
        _WORKER = threading.Thread(
            target=_install_worker,
            args=(manifest, root, bool(force)),
            name="syncanvas-digital-human-installer",
            daemon=True,
        )
        _WORKER.start()
    return get_component_status()


def cancel_component_install() -> Dict[str, Any]:
    state = _load_state()
    if state.get("state") not in ACTIVE_STATES:
        return get_component_status()
    _CANCEL_EVENT.set()
    _update_state(state="cancelling", phase="cancelling", message="正在取消安装")
    return get_component_status()
