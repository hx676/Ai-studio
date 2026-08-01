import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import quote

from fastapi import HTTPException
from PIL import Image

from app import upstream_runtime
from app.core.json_store import atomic_write_json


MAX_TEMPLATE_BYTES = 2 * 1024 * 1024
MAX_REFERENCE_IMAGES = 8
MAX_IMAGE_BYTES = 50 * 1024 * 1024
TEMPLATE_ID_RE = re.compile(r"^tmpl_[a-f0-9]{12}$")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SENSITIVE_KEYS = {
    "apikey",
    "authorization",
    "password",
    "passwd",
    "clientsecret",
    "accesskeysecret",
    "accesstoken",
    "refreshtoken",
}


def _model_fields_set(payload: Any) -> set[str]:
    fields_set = getattr(payload, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(payload, "__fields_set__", set())
    return set(fields_set)


def _normalized_secret_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _find_sensitive_key(value: Any, path: str = "template") -> str:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if _normalized_secret_key(key) in SENSITIVE_KEYS:
                return child_path
            found = _find_sensitive_key(child, child_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _find_sensitive_key(child, f"{path}[{index}]")
            if found:
                return found
    return ""


def validate_template(template: Any) -> bytes:
    if not isinstance(template, dict):
        raise HTTPException(status_code=400, detail="模板 JSON 必须是对象")
    sensitive_path = _find_sensitive_key(template)
    if sensitive_path:
        raise HTTPException(status_code=400, detail=f"模板包含敏感字段：{sensitive_path}")
    try:
        encoded = json.dumps(template, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"模板 JSON 无法序列化：{exc}") from exc
    if len(encoded) > MAX_TEMPLATE_BYTES:
        raise HTTPException(status_code=413, detail="模板 JSON 不能超过 2 MB")
    return encoded


def _template_root() -> Path:
    return Path(upstream_runtime.ASSET_LIBRARY_DIR).resolve() / "templates"


def template_directory(template_id: str) -> Path:
    if not TEMPLATE_ID_RE.fullmatch(str(template_id or "")):
        raise HTTPException(status_code=400, detail="模板 ID 无效")
    root = _template_root()
    target = (root / template_id).resolve()
    if target.parent != root:
        raise HTTPException(status_code=400, detail="模板路径无效")
    return target


def _asset_url(path: Path) -> str:
    root = Path(upstream_runtime.ASSETS_DIR).resolve()
    try:
        rel = path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="模板文件不在受控资产目录中") from exc
    return "/assets/" + quote(rel, safe="/")


def _trusted_image_path(url: str) -> Path:
    clean = str(url or "").strip()
    if not clean.startswith(("/assets/", "/output/")):
        raise HTTPException(status_code=400, detail="模板图片只支持本地 /assets 或 /output 地址")
    source = upstream_runtime.output_file_from_url(clean)
    if not source or not os.path.isfile(source):
        raise HTTPException(status_code=404, detail=f"模板图片不存在：{clean[:160]}")
    path = Path(source).resolve()
    ext = path.suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="模板参考媒体仅支持 PNG、JPG、WEBP 或 GIF 图片")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="单张模板图片不能超过 50 MB")
    try:
        with Image.open(path) as image:
            image.verify()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="模板参考图不是有效图片") from exc
    return path


def _dedupe_urls(urls: Iterable[str], exclude: str = "") -> list[str]:
    result = []
    seen = {exclude} if exclude else set()
    for raw in urls:
        url = str(raw or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(url)
        if len(result) >= MAX_REFERENCE_IMAGES:
            break
    return result


def _prepare_images(thumbnail_url: str, reference_image_urls: Iterable[str]) -> Tuple[Optional[Path], list[Path]]:
    thumbnail_url = str(thumbnail_url or "").strip()
    references = _dedupe_urls(reference_image_urls or [], thumbnail_url)
    thumbnail = _trusted_image_path(thumbnail_url) if thumbnail_url else None
    return thumbnail, [_trusted_image_path(url) for url in references]


def _write_template_directory(
    template_id: str,
    template: Dict[str, Any],
    thumbnail: Optional[Path],
    references: list[Path],
) -> Tuple[str, str, list[str]]:
    validate_template(template)
    target = template_directory(template_id)
    root = target.parent
    root.mkdir(parents=True, exist_ok=True)
    stage = root / f".{template_id}.{uuid.uuid4().hex}.tmp"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        json_path = stage / "template.json"
        atomic_write_json(json_path, template)
        thumbnail_path = None
        if thumbnail:
            thumbnail_path = stage / f"thumbnail{thumbnail.suffix.lower()}"
            shutil.copy2(thumbnail, thumbnail_path)
        reference_paths = []
        if references:
            references_dir = stage / "references"
            references_dir.mkdir()
            for index, source in enumerate(references, 1):
                dest = references_dir / f"reference-{index}{source.suffix.lower()}"
                shutil.copy2(source, dest)
                reference_paths.append(dest)
        if target.exists():
            shutil.rmtree(target)
        os.rename(stage, target)
        json_path = target / "template.json"
        thumbnail_path = target / thumbnail_path.name if thumbnail_path else None
        reference_paths = [target / "references" / path.name for path in reference_paths]
        return (
            _asset_url(json_path),
            _asset_url(thumbnail_path) if thumbnail_path else "",
            [_asset_url(path) for path in reference_paths],
        )
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _find_template(lib: Dict[str, Any], template_id: str):
    for library in lib.get("libraries", []):
        for category in library.get("categories", []):
            for item in category.get("items", []):
                if item.get("id") == template_id and item.get("kind") == "template":
                    return library, category, item
    return None, None, None


def _template_category(lib: Dict[str, Any], library_id: str, category_id: str):
    library = upstream_runtime.find_asset_library(lib, library_id)
    if not library:
        raise HTTPException(status_code=404, detail="资产库不存在")
    categories = [cat for cat in library.get("categories", []) if cat.get("type") == "template"]
    category = next((cat for cat in categories if cat.get("id") == category_id), None) if category_id else None
    if category_id and not category:
        raise HTTPException(status_code=404, detail="模板文件夹不存在")
    category = category or next((cat for cat in categories if cat.get("default")), None) or (categories[0] if categories else None)
    if not category:
        raise HTTPException(status_code=404, detail="模板文件夹不存在")
    return library, category


def _source_metadata(payload: Any, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source = dict(existing or {})
    fields_set = _model_fields_set(payload)
    mapping = {
        "source_canvas_id": "canvas_id",
        "source_node_id": "node_id",
        "source_skill_id": "skill_id",
        "source_metadata": "metadata",
    }
    for field, key in mapping.items():
        if not fields_set or field in fields_set:
            value = getattr(payload, field, None)
            if value not in (None, "", {}):
                source[key] = value
            elif field in fields_set:
                source.pop(key, None)
    return source


def create_template_asset(payload: Any) -> Dict[str, Any]:
    validate_template(payload.template)
    thumbnail, references = _prepare_images(payload.thumbnail_url, payload.reference_image_urls)
    template_id = f"tmpl_{uuid.uuid4().hex[:12]}"
    now = upstream_runtime.now_ms()
    with upstream_runtime.CANVAS_LOCK:
        lib = upstream_runtime.load_asset_library()
        library, category = _template_category(lib, payload.library_id, payload.category_id)
        json_url, thumbnail_url, reference_urls = _write_template_directory(
            template_id, payload.template, thumbnail, references
        )
        item = {
            "id": template_id,
            "name": upstream_runtime.sanitize_asset_name(payload.name, "模板"),
            "kind": "template",
            "url": json_url,
            "json_url": json_url,
            "thumbnail_url": thumbnail_url,
            "reference_image_urls": reference_urls,
            "category_id": category.get("id") or "",
            "source": _source_metadata(payload),
            "created_at": now,
            "updated_at": now,
        }
        category.setdefault("items", []).append(item)
        lib["active_library_id"] = library.get("id") or lib.get("active_library_id")
        upstream_runtime.save_asset_library(lib)
    return {"library": lib, "item": item, "template": payload.template}


def get_template_asset(template_id: str) -> Dict[str, Any]:
    lib = upstream_runtime.load_asset_library()
    _, _, item = _find_template(lib, template_id)
    if not item:
        raise HTTPException(status_code=404, detail="模板不存在或已被删除")
    path = template_directory(template_id) / "template.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="模板 JSON 文件缺失")
    try:
        with path.open("r", encoding="utf-8") as handle:
            template = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="模板 JSON 无法读取") from exc
    validate_template(template)
    return {"item": item, "template": template}


def update_template_asset(template_id: str, payload: Any) -> Dict[str, Any]:
    fields_set = _model_fields_set(payload)
    with upstream_runtime.CANVAS_LOCK:
        lib = upstream_runtime.load_asset_library()
        library, category, item = _find_template(lib, template_id)
        if not item:
            raise HTTPException(status_code=404, detail="模板不存在或已被删除")
        current = get_template_asset(template_id)["template"]
        template = payload.template if "template" in fields_set else current
        validate_template(template)
        images_changed = bool({"thumbnail_url", "reference_image_urls"} & fields_set)
        if images_changed:
            thumbnail_input = payload.thumbnail_url if "thumbnail_url" in fields_set else item.get("thumbnail_url", "")
            references_input = payload.reference_image_urls if "reference_image_urls" in fields_set else item.get("reference_image_urls", [])
            thumbnail, references = _prepare_images(thumbnail_input or "", references_input or [])
            json_url, thumbnail_url, reference_urls = _write_template_directory(
                template_id, template, thumbnail, references
            )
            item.update({
                "url": json_url,
                "json_url": json_url,
                "thumbnail_url": thumbnail_url,
                "reference_image_urls": reference_urls,
            })
        elif "template" in fields_set:
            path = template_directory(template_id) / "template.json"
            atomic_write_json(path, template)
        if "name" in fields_set and payload.name is not None:
            item["name"] = upstream_runtime.sanitize_asset_name(payload.name, item.get("name") or "模板")
        if "category_id" in fields_set or "library_id" in fields_set:
            target_category_id = payload.category_id if "category_id" in fields_set else ""
            target_library, target_category = _template_category(
                lib,
                payload.library_id if payload.library_id is not None else library.get("id", ""),
                target_category_id if "library_id" in fields_set else (target_category_id or category.get("id", "")),
            )
            if target_category is not category:
                category["items"] = [entry for entry in category.get("items", []) if entry.get("id") != template_id]
                target_category.setdefault("items", []).append(item)
                category = target_category
                library = target_library
            item["category_id"] = category.get("id") or ""
        item["source"] = _source_metadata(payload, item.get("source"))
        item["updated_at"] = upstream_runtime.now_ms()
        lib["active_library_id"] = library.get("id") or lib.get("active_library_id")
        upstream_runtime.save_asset_library(lib)
    return {"library": lib, "item": item, "template": template}


def delete_template_asset(template_id: str) -> Dict[str, Any]:
    with upstream_runtime.CANVAS_LOCK:
        lib = upstream_runtime.load_asset_library()
        _, category, item = _find_template(lib, template_id)
        if not item:
            raise HTTPException(status_code=404, detail="模板不存在或已被删除")
        category["items"] = [entry for entry in category.get("items", []) if entry.get("id") != template_id]
        shutil.rmtree(template_directory(template_id), ignore_errors=True)
        upstream_runtime.save_asset_library(lib)
    return {"library": lib, "deleted": template_id}
