import base64
import os
import urllib.parse
import uuid
import requests
from typing import List
from fastapi import HTTPException, UploadFile, File
from fastapi.responses import FileResponse, Response

from app import legacy


def _comfy_instances():
    return legacy.COMFYUI_INSTANCES

UPLOAD_MAX_BYTES = 500 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024

def output_storage(category="output"):
    return (legacy.OUTPUT_INPUT_DIR, "input") if category == "input" else (legacy.OUTPUT_OUTPUT_DIR, "output")

def output_url_for(filename, category="output"):
    _, subdir = output_storage(category)
    return f"/assets/{subdir}/{filename}"

def output_path_for(filename, category="output"):
    folder, _ = output_storage(category)
    return os.path.join(folder, filename)

def output_file_from_url(url):
    if isinstance(url, dict):
        url = url.get("url", "")
    if not url or not (url.startswith("/output/") or url.startswith("/assets/")):
        return None
    clean = urllib.parse.unquote(url.split("#", 1)[0].split("?", 1)[0]).replace("\\", "/")
    if clean.startswith("/assets/"):
        root = legacy.ASSETS_DIR
        rel = clean[len("/assets/"):]
    else:
        root = legacy.OUTPUT_DIR
        rel = clean[len("/output/"):]
    rel = rel.lstrip("/")
    if not rel:
        return None
    path = os.path.abspath(os.path.join(root, rel))
    output_root = os.path.abspath(root)
    if os.path.commonpath([output_root, path]) != output_root or not os.path.exists(path):
        return None
    return path

def content_type_for_path(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in [".mp4", ".m4v"]:
        return "video/mp4"
    if ext == ".webm":
        return "video/webm"
    if ext == ".mov":
        return "video/quicktime"
    if ext == ".wav":
        return "audio/wav"
    if ext == ".mp3":
        return "audio/mpeg"
    if ext == ".m4a":
        return "audio/mp4"
    if ext == ".ogg":
        return "audio/ogg"
    if ext in [".jpg", ".jpeg"]:
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    return "image/png"

CANVAS_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg"}

def canvas_audio_extension(file: UploadFile):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext in CANVAS_AUDIO_EXTENSIONS:
        return ext
    content_type = (file.content_type or "").lower()
    if "wav" in content_type:
        return ".wav"
    if "mpeg" in content_type or "mp3" in content_type:
        return ".mp3"
    if "mp4" in content_type or "m4a" in content_type:
        return ".m4a"
    if "ogg" in content_type:
        return ".ogg"
    return ""

def view_image(filename: str, type: str = "input", subfolder: str = ""):
    # 先按原逻辑去各 ComfyUI 后端找
    for addr in legacy.COMFYUI_INSTANCES:
        try:
            url = f"http://{addr}/view"
            params = {"filename": filename, "type": type, "subfolder": subfolder}
            r = requests.get(url, params=params, timeout=1)
            if r.status_code == 200:
                return Response(content=r.content, media_type=r.headers.get('Content-Type'))
        except Exception:
            continue
    # 后端都拿不到时回退本地 assets/<input|output>/
    # 适用场景：画布通过 /api/ai/upload 把参考图直接落到本地 assets/input/，
    # 但 ComfyUI 的 input 可能因为重启/清理而丢失，导致 enhance/klein 等页面预览对比图 404
    if not subfolder and type in ("input", "output"):
        safe_name = os.path.basename(filename or "")
        if safe_name:
            local_path = output_path_for(safe_name, "input" if type == "input" else "output")
            if os.path.isfile(local_path):
                return FileResponse(local_path, media_type=content_type_for_path(local_path))
    raise HTTPException(status_code=404, detail="Image not found on any available backend")

def download_output(url: str, name: str = ""):
    path = output_file_from_url(url)
    if not path:
        raise HTTPException(status_code=404, detail="文件不存在")
    filename = os.path.basename(name) if name else os.path.basename(path)
    return FileResponse(path, media_type=content_type_for_path(path), filename=filename)

async def read_upload_limited(file: UploadFile, max_bytes=UPLOAD_MAX_BYTES, detail="Upload file cannot exceed 500MB"):
    data = bytearray()
    total = 0
    while True:
        chunk = await file.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=detail)
        data.extend(chunk)
    return bytes(data)

async def save_upload_limited(file: UploadFile, path: str, max_bytes=UPLOAD_MAX_BYTES, detail="Upload file cannot exceed 500MB"):
    total = 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "wb") as f:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail=detail)
                f.write(chunk)
    except Exception:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        raise
    return total

async def upload_image(files: List[UploadFile] = File(...)):
    uploaded_files = []
    files_content = []
    for file in files:
        content = await read_upload_limited(file)
        files_content.append((file, content))

    for file, content in files_content:
        success_count = 0
        last_result = None
        for addr in legacy.COMFYUI_INSTANCES:
            try:
                files_data = {'image': (file.filename, content, file.content_type)}
                response = requests.post(f"http://{addr}/upload/image", files=files_data, timeout=5)
                if response.status_code == 200:
                    last_result = response.json()
                    success_count += 1
            except Exception as e:
                print(f"Upload error for {addr}: {e}")

        if success_count > 0 and last_result:
            uploaded_files.append({"comfy_name": last_result.get("name", file.filename)})
        else:
            raise HTTPException(status_code=500, detail="Failed to upload to any backend")

    return {"files": uploaded_files}

async def upload_ai_reference(files: List[UploadFile] = File(...)):
    uploaded = []
    for file in files:
        content = await read_upload_limited(file)
        if not content:
            continue
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
            content_type = (file.content_type or "").lower()
            ext = ".jpg" if "jpeg" in content_type else ".webp" if "webp" in content_type else ".png"
        filename = f"ai_ref_{uuid.uuid4().hex[:12]}{ext}"
        path = output_path_for(filename, "input")
        with open(path, "wb") as f:
            f.write(content)
        mime = file.content_type or content_type_for_path(path)
        if not str(mime).startswith("image/"):
            mime = content_type_for_path(path)
        uploaded.append({
            "url": output_url_for(filename, "input"),
            "name": file.filename or filename,
            "data_url": f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}",
        })
    return {"files": uploaded}

async def upload_canvas_media(files: List[UploadFile] = File(...)):
    uploaded = []
    for file in files:
        ext = canvas_audio_extension(file)
        if not ext:
            raise HTTPException(status_code=400, detail="Only audio files are supported for canvas media upload.")
        filename = f"canvas_audio_{uuid.uuid4().hex[:12]}{ext}"
        path = output_path_for(filename, "input")
        size = await save_upload_limited(file, path)
        if size <= 0:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        mime = file.content_type or content_type_for_path(path)
        if not str(mime).startswith("audio/"):
            mime = content_type_for_path(path)
        uploaded.append({
            "url": output_url_for(filename, "input"),
            "name": file.filename or filename,
            "mime": mime,
            "kind": "audio",
            "size": size,
        })
    return {"files": uploaded}
