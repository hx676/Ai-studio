"""Constrained local asset storage for trusted node-extension editors."""

from __future__ import annotations

import json
import os
import re
import struct
import uuid
from pathlib import Path

from fastapi import File, Form, HTTPException, UploadFile

from app import legacy
from app.core.upload_limits import save_upload_to_path_limited


MODEL_MAX_BYTES = 100 * 1024 * 1024
MODEL_EXTENSIONS = {".glb", ".gltf"}
MODEL_MIME_TYPES = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
}
_EXTENSION_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$", re.I)


def _extension_slug(value: str) -> str:
    text = str(value or "").strip()
    if text == "syncanvas.3d-director":
        return "3d-director"
    if not _EXTENSION_SLUG_RE.fullmatch(text):
        raise HTTPException(status_code=400, detail="扩展标识无效")
    return text


def _validate_glb(path: Path) -> None:
    size = path.stat().st_size
    if size < 20:
        raise HTTPException(status_code=400, detail="GLB 文件头不完整")
    with path.open("rb") as handle:
        magic, version, declared_length = struct.unpack("<4sII", handle.read(12))
        if magic != b"glTF" or version != 2 or declared_length != size:
            raise HTTPException(status_code=400, detail="仅支持结构完整的 glTF 2.0 GLB 文件")
        json_length, json_type = struct.unpack("<II", handle.read(8))
        if json_type != 0x4E4F534A or json_length <= 0 or 20 + json_length > size:
            raise HTTPException(status_code=400, detail="GLB 缺少有效的 JSON 场景块")
        try:
            manifest = json.loads(handle.read(json_length).decode("utf-8").rstrip(" \t\r\n\x00"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="GLB 场景描述无法解析") from exc
    if not str(manifest.get("asset", {}).get("version", "")).startswith("2"):
        raise HTTPException(status_code=400, detail="仅支持 glTF 2.0 模型")
    _validate_embedded_dependencies(manifest, allow_primary_binary=True)


def _validate_embedded_dependencies(manifest: dict, *, allow_primary_binary: bool) -> None:
    for index, buffer in enumerate(manifest.get("buffers", [])):
        uri = str(buffer.get("uri", "")) if isinstance(buffer, dict) else ""
        if uri.startswith("data:"):
            continue
        if allow_primary_binary and index == 0 and isinstance(buffer, dict) and "uri" not in buffer:
            continue
        raise HTTPException(status_code=400, detail="模型必须内嵌 Buffer；不允许外部文件路径")
    for image in manifest.get("images", []):
        if not isinstance(image, dict) or "bufferView" in image:
            continue
        uri = str(image.get("uri", ""))
        if not uri.startswith("data:"):
            raise HTTPException(status_code=400, detail="模型必须内嵌贴图；不允许外部文件路径")


def _validate_gltf(path: Path) -> None:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="GLTF 文件不是有效的 UTF-8 JSON") from exc
    if not isinstance(manifest, dict) or not str(manifest.get("asset", {}).get("version", "")).startswith("2"):
        raise HTTPException(status_code=400, detail="仅支持 glTF 2.0 模型")
    _validate_embedded_dependencies(manifest, allow_primary_binary=False)


async def upload_node_extension_asset(
    file: UploadFile = File(...),
    kind: str = Form("model"),
    extension_id: str = Form("3d-director"),
):
    if kind != "model":
        raise HTTPException(status_code=400, detail="该接口当前只接受 3D 模型")
    extension = Path(file.filename or "").suffix.lower()
    if extension not in MODEL_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 .glb 或内嵌资源的 .gltf 文件")
    slug = _extension_slug(extension_id)
    target_dir = Path(legacy.ASSETS_DIR) / "node-extensions" / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"model_{uuid.uuid4().hex[:16]}{extension}"
    temporary = target_dir / f".{filename}.validating"
    final_path = target_dir / filename
    try:
        size = await save_upload_to_path_limited(file, temporary, MODEL_MAX_BYTES, "3D 模型")
        if size <= 0:
            raise HTTPException(status_code=400, detail="3D 模型为空")
        if extension == ".glb":
            _validate_glb(temporary)
        else:
            _validate_gltf(temporary)
        os.replace(temporary, final_path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "url": f"/assets/node-extensions/{slug}/{filename}",
        "name": Path(file.filename or filename).name[:180],
        "kind": "model",
        "mime": MODEL_MIME_TYPES[extension],
        "size": size,
    }
