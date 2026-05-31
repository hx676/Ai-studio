import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.models.common import DeleteHistoryRequest, OutputLocationRequest, RollbackRequest, UpdateRequest
from app.services.storage_service import view_image, download_output, upload_image, upload_ai_reference, output_file_from_url
from app import legacy


class _LegacyRef:
    def __init__(self, name: str):
        self.name = name

    @property
    def value(self):
        return getattr(legacy, self.name)

    def __fspath__(self):
        return os.fspath(self.value)

    def __str__(self):
        return str(self.value)

    def __repr__(self):
        return repr(self.value)

    def __format__(self, format_spec: str):
        return format(str(self.value), format_spec)

    def __eq__(self, other):
        if isinstance(other, _LegacyRef):
            other = other.value
        return self.value == other

    def __hash__(self):
        return hash(self.value)

    def __bool__(self):
        return bool(self.value)

    def __len__(self):
        return len(self.value)

    def __iter__(self):
        return iter(self.value)

    def __contains__(self, item):
        return item in self.value

    def __getitem__(self, key):
        return self.value[key]

    def __setitem__(self, key, value):
        self.value[key] = value

    def __getattr__(self, name: str):
        return getattr(self.value, name)

    def __enter__(self):
        return self.value.__enter__()

    def __exit__(self, exc_type, exc, tb):
        return self.value.__exit__(exc_type, exc, tb)


for _name in (
    "BASE_DIR",
    "DATA_DIR",
    "FIELD_LABELS",
    "GITHUB_RAW_ROOT",
    "GITHUB_REPO_URL",
    "GITHUB_TREE_CACHE",
    "GITHUB_TREE_URL",
    "GITHUB_VERSION_URL",
    "HISTORY_FILE",
    "HISTORY_LOCK",
    "QUEUE",
    "QUEUE_LOCK",
    "QUIET_ACCESS_PATHS",
    "QUIET_ACCESS_PREFIXES",
    "STATIC_DIR",
    "UPDATE_LOCK",
):
    globals()[_name] = _LegacyRef(_name)

class QuietAccessLogFilter(logging.Filter):
    def filter(self, record):
        args = record.args if isinstance(record.args, tuple) else ()
        if len(args) >= 3:
            path = str(args[2]).split("?", 1)[0]
            status = int(args[4]) if len(args) >= 5 and str(args[4]).isdigit() else 0
            quiet_dynamic = (
                any(path.startswith(prefix) and path.endswith("/meta") for prefix in QUIET_ACCESS_PREFIXES)
                or path.startswith("/api/digital-human/task/")
            )
            if (path in QUIET_ACCESS_PATHS or quiet_dynamic) and status < 400:
                return False
        message = record.getMessage()
        if any(f'"GET {path}' in message and '" 200' in message for path in QUIET_ACCESS_PATHS):
            return False
        if 'GET /api/canvases/' in message and '/meta' in message and '" 200' in message:
            return False
        if 'GET /api/digital-human/task/' in message and '" 200' in message:
            return False
        return True

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.user_connections: Dict[str, WebSocket] = {}
        self.connection_clients: Dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, client_id: str = None):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.connection_clients[websocket] = client_id or f"anon-{id(websocket)}"
        if client_id:
            self.user_connections[client_id] = websocket
        print(f"WS Connected. Total: {len(self.active_connections)}, Online: {self.online_count()}")
        await self.broadcast_count()

    async def disconnect(self, websocket: WebSocket, client_id: str = None):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.connection_clients.pop(websocket, None)
        if client_id and self.user_connections.get(client_id) is websocket:
            del self.user_connections[client_id]
        print(f"WS Disconnected. Total: {len(self.active_connections)}, Online: {self.online_count()}")
        await self.broadcast_count()

    def online_count(self):
        visible_clients = {
            client_id for client_id in self.connection_clients.values()
            if client_id and not str(client_id).startswith("canvas_")
        }
        return len(visible_clients)

    async def broadcast_count(self):
        count = self.online_count()
        data = json.dumps({"type": "stats", "online_count": count})
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(data)
            except Exception as e:
                print(f"Broadcast error: {e}")
                self.active_connections.remove(connection)

    async def broadcast_new_image(self, image_data: dict):
        data = json.dumps({"type": "new_image", "data": image_data})
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(data)
            except Exception as e:
                print(f"Broadcast image error: {e}")
                self.active_connections.remove(connection)

    async def broadcast_canvas_updated(self, canvas_id: str, updated_at: int, client_id: str = ""):
        data = json.dumps({
            "type": "canvas_updated",
            "canvas_id": canvas_id,
            "updated_at": updated_at,
            "client_id": client_id or "",
        })
        for connection in self.active_connections[:]:
            try:
                await connection.send_text(data)
            except Exception as e:
                print(f"Broadcast canvas error: {e}")
                self.active_connections.remove(connection)

    async def send_personal_message(self, message: dict, client_id: str):
        ws = self.user_connections.get(client_id)
        if ws:
            try:
                await ws.send_text(json.dumps(message))
            except Exception as e:
                print(f"Personal message error for {client_id}: {e}")

def friendly_validation_error(errors):
    parts = []
    for err in errors or []:
        loc = [str(item) for item in err.get("loc", []) if item != "body"]
        field = loc[-1] if loc else ""
        label = FIELD_LABELS.get(field, field or "请求参数")
        ctx = err.get("ctx") or {}
        limit = ctx.get("limit_value") or ctx.get("max_length") or ctx.get("min_length")
        err_type = str(err.get("type") or "")
        msg = str(err.get("msg") or "")
        if "max_length" in err_type or "at most" in msg:
            parts.append(f"{label}过长：当前内容超过后端上限 {limit} 个字符。请拆分为多个提示词节点，或先用 LLM 节点压缩后再生成。")
        elif "min_length" in err_type:
            parts.append(f"{label}不能为空。")
        else:
            parts.append(f"{label}格式不正确：{msg}")
    return "\n".join(parts) or "请求参数不正确。"

async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": friendly_validation_error(exc.errors()), "errors": exc.errors()},
    )

def current_app_version():
    version_file = os.path.join(BASE_DIR, "VERSION")
    try:
        if os.path.exists(version_file):
            with open(version_file, "r", encoding="utf-8") as f:
                version = (f.read().strip().splitlines() or [""])[0].strip()
                if version:
                    return version
    except Exception:
        pass
    try:
        return time.strftime("%Y.%m.%d", time.localtime())
    except Exception:
        return ""

def versioned_static_html(html: str) -> str:
    version = current_app_version()
    if not version:
        return html
    safe_version = urllib.parse.quote(version, safe="._-")
    pattern = re.compile(r'(?P<prefix>(?:src|href)=["\']|@import\s+url\(["\'])(?P<url>/static/[^"\')?#]+(?:\.(?:js|css|html)))(?:\?v=[^"\')#]*)?', re.I)
    return pattern.sub(lambda m: f"{m.group('prefix')}{m.group('url')}?v={safe_version}", html)

def sync_static_html_versions():
    version = current_app_version()
    if not version:
        return
    safe_version = urllib.parse.quote(version, safe="._-")
    try:
        for name in os.listdir(STATIC_DIR):
            if not name.lower().endswith(".html"):
                continue
            path = os.path.join(STATIC_DIR, name)
            if not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                old = f.read()
            new = re.sub(r'([?&]v=)[^"\'`\s<>)]*', rf'\g<1>{safe_version}', old)
            if new != old:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new)
    except Exception as e:
        print(f"同步静态页面版本号失败: {e}")

def static_html_response(filename: str):
    path = os.path.join(STATIC_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    return Response(
        versioned_static_html(html),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )

def app_info():
    version = current_app_version()
    return {
        "name": "SynCanvas",
        "version": version,
    }

def update_allowed_file(path: str) -> bool:
    path = str(path or "").replace("\\", "/").lstrip("/")
    if not path or any(part in {"", ".", ".."} for part in path.split("/")):
        return False
    return path in {"main.py", "VERSION"} or path.startswith("static/")

def github_json(url: str, use_etag_cache: bool = False):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "SynCanvas-Updater",
    }
    cache_key = url
    if use_etag_cache and cache_key == GITHUB_TREE_URL:
        if GITHUB_TREE_CACHE["data"] and time.time() < GITHUB_TREE_CACHE["expires_at"]:
            return GITHUB_TREE_CACHE["data"]
        if GITHUB_TREE_CACHE["etag"]:
            headers["If-None-Match"] = GITHUB_TREE_CACHE["etag"]
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            etag = resp.headers.get("ETag", "")
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            if use_etag_cache and cache_key == GITHUB_TREE_URL:
                GITHUB_TREE_CACHE.update({
                    "etag": etag,
                    "data": payload,
                    "expires_at": time.time() + 600,  # 10 分钟内复用
                })
            return payload
    except urllib.error.HTTPError as exc:
        # 304 表示对方树未变，沿用缓存
        if exc.code == 304 and use_etag_cache and GITHUB_TREE_CACHE["data"]:
            GITHUB_TREE_CACHE["expires_at"] = time.time() + 600
            return GITHUB_TREE_CACHE["data"]
        raise

def github_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "SynCanvas-Updater"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()

def safe_update_target(path: str) -> str:
    rel = str(path or "").replace("\\", "/").lstrip("/")
    if not update_allowed_file(rel):
        raise ValueError(f"更新文件不在允许范围：{rel}")
    target = os.path.abspath(os.path.join(BASE_DIR, *rel.split("/")))
    base = os.path.abspath(BASE_DIR)
    if os.path.commonpath([base, target]) != base:
        raise ValueError(f"更新路径不安全：{rel}")
    return target

def schedule_self_restart(delay_seconds: int = 3) -> bool:
    """派生脱离父进程的小脚本，等几秒后启动启动服务脚本，并干掉当前 PID。"""
    delay = max(1, int(delay_seconds or 3))
    pid = os.getpid()
    try:
        if os.name == "nt":
            launcher = os.path.join(BASE_DIR, "启动服务.bat")
            if not os.path.exists(launcher):
                launcher = os.path.join(BASE_DIR, "start.bat")
            bat_path = os.path.join(BASE_DIR, "_self_restart.bat")
            log_path = os.path.join(BASE_DIR, "_self_restart.log")
            script = (
                "@echo off\r\n"
                "chcp 65001 >nul\r\n"
                "setlocal\r\n"
                f"set \"APP_DIR={BASE_DIR}\"\r\n"
                f"set \"LAUNCHER={launcher}\"\r\n"
                f"set \"LOG_FILE={log_path}\"\r\n"
                "echo [%date% %time%] restart scheduled >> \"%LOG_FILE%\"\r\n"
                f"timeout /t {delay} /nobreak >nul\r\n"
                "echo [%date% %time%] stopping old process >> \"%LOG_FILE%\"\r\n"
                f"taskkill /F /PID {pid} >nul 2>&1\r\n"
                "timeout /t 2 /nobreak >nul\r\n"
                "cd /d \"%APP_DIR%\"\r\n"
                "if exist \"%LAUNCHER%\" (\r\n"
                "  echo [%date% %time%] starting launcher: %LAUNCHER% >> \"%LOG_FILE%\"\r\n"
                "  start \"ComfyUI-API-Modelscope\" /D \"%APP_DIR%\" cmd /k call \"%LAUNCHER%\"\r\n"
                ") else (\r\n"
                "  echo [%date% %time%] launcher missing, fallback to python main.py >> \"%LOG_FILE%\"\r\n"
                "  if exist \"%APP_DIR%\\python\\python.exe\" (\r\n"
                "    start \"ComfyUI-API-Modelscope\" /D \"%APP_DIR%\" cmd /k \"\"%APP_DIR%\\python\\python.exe\" main.py\"\r\n"
                "  ) else (\r\n"
                "    start \"ComfyUI-API-Modelscope\" /D \"%APP_DIR%\" cmd /k python main.py\r\n"
                "  )\r\n"
                ")\r\n"
                "del \"%~f0\"\r\n"
            )
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(script)
            subprocess.Popen(
                ["cmd", "/c", bat_path],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
        else:
            launcher = os.path.join(BASE_DIR, "mac-启动服务.command")
            if not os.path.exists(launcher):
                launcher = os.path.join(BASE_DIR, "start.sh")
            sh_path = os.path.join(BASE_DIR, "_self_restart.sh")
            script = (
                "#!/bin/sh\n"
                f"sleep {delay}\n"
                f"kill -9 {pid} 2>/dev/null\n"
                f"cd \"{BASE_DIR}\"\n"
                f"if [ -x \"{launcher}\" ]; then nohup \"{launcher}\" >/dev/null 2>&1 &\n"
                f"elif [ -f \"{launcher}\" ]; then nohup /bin/sh \"{launcher}\" >/dev/null 2>&1 &\n"
                "fi\n"
                "rm -- \"$0\"\n"
            )
            with open(sh_path, "w", encoding="utf-8") as f:
                f.write(script)
            os.chmod(sh_path, 0o755)
            subprocess.Popen(
                ["/bin/sh", sh_path],
                start_new_session=True,
                close_fds=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return True
    except Exception as exc:
        logging.exception("schedule_self_restart failed: %s", exc)
        return False

def update_from_github(req: UpdateRequest = UpdateRequest()):
    raise HTTPException(status_code=503, detail="在线更新源暂未配置，后续将接入 SynCanvas 新仓库。")

def list_update_backups() -> List[Dict[str, Any]]:
    root = os.path.join(DATA_DIR, "update_backups")
    if not os.path.isdir(root):
        return []
    items = []
    for name in sorted(os.listdir(root), reverse=True):
        bp = os.path.join(root, name)
        if not os.path.isdir(bp):
            continue
        file_count = 0
        for _, _, fs in os.walk(bp):
            file_count += len(fs)
        try:
            created_at = os.path.getmtime(bp)
        except OSError:
            created_at = 0.0
        items.append({
            "name": name,
            "file_count": file_count,
            "created_at": created_at,
        })
    return items

def get_update_backups():
    return {"backups": list_update_backups()}

def rollback_update(req: RollbackRequest):
    if not req.name:
        raise HTTPException(status_code=400, detail="缺少备份名称")
    if not UPDATE_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="正在更新中，请稍后再试")
    try:
        backup_root_abs = os.path.abspath(os.path.join(DATA_DIR, "update_backups"))
        backup_dir = os.path.abspath(os.path.join(backup_root_abs, req.name))
        if os.path.commonpath([backup_root_abs, backup_dir]) != backup_root_abs:
            raise HTTPException(status_code=400, detail="备份路径不安全")
        if not os.path.isdir(backup_dir):
            raise HTTPException(status_code=404, detail="备份不存在")
        restored = []
        skipped = []
        for dirpath, _, filenames in os.walk(backup_dir):
            for fn in filenames:
                src = os.path.join(dirpath, fn)
                rel = os.path.relpath(src, backup_dir).replace("\\", "/")
                if not update_allowed_file(rel):
                    skipped.append(rel)
                    continue
                try:
                    target = safe_update_target(rel)
                except ValueError:
                    skipped.append(rel)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                temp_path = f"{target}.rollback_tmp"
                with open(src, "rb") as fin, open(temp_path, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
                os.replace(temp_path, target)
                restored.append(rel)
        restart_scheduled = False
        if req.auto_restart and restored:
            restart_scheduled = schedule_self_restart(req.restart_delay)
        return {
            "ok": True,
            "restored": restored,
            "skipped": skipped,
            "count": len(restored),
            "restart_required": True,
            "restart_scheduled": restart_scheduled,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"回滚失败：{exc}") from exc
    finally:
        UPDATE_LOCK.release()

async def index():
    return static_html_response("index.html")

def _open_local_path(target, select_file=False):
    path = os.path.abspath(str(target or ""))
    if not path or not os.path.exists(path):
        return False
    try:
        if os.name == "nt":
            if select_file and os.path.isfile(path):
                subprocess.Popen(["explorer.exe", f"/select,{path}"])
            else:
                os.startfile(path)  # type: ignore[attr-defined]
            return True
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, path])
        return True
    except Exception:
        return False

async def get_history_api(type: str = None):
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if type:
                    data = [item for item in data if item.get("type", "zimage") == type]
                data = [item for item in data if item.get("images") and len(item["images"]) > 0]

                def sort_key(item):
                    ts = item.get("timestamp", 0)
                    if isinstance(ts, (int, float)):
                        return float(ts)
                    return 0

                data.sort(key=sort_key, reverse=True)
                return data
        except Exception as e:
            print(f"读取历史文件失败: {e}")
            return []
    return []

async def get_queue_status(client_id: str):
    with QUEUE_LOCK:
        total = len(QUEUE)
        positions = [i + 1 for i, t in enumerate(QUEUE) if t["client_id"] == client_id]
        position = positions[0] if positions else 0
    return {"total": total, "position": position}

async def delete_history(req: DeleteHistoryRequest):
    if not os.path.exists(HISTORY_FILE):
        return {"success": False, "message": "History file not found"}
    try:
        with HISTORY_LOCK:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)
            target_record = None
            new_history = []
            for item in history:
                is_match = False
                item_ts = item.get("timestamp", 0)
                if isinstance(req.timestamp, (int, float)) and isinstance(item_ts, (int, float)):
                    if abs(float(item_ts) - float(req.timestamp)) < 0.001:
                        is_match = True
                elif str(item_ts) == str(req.timestamp):
                    is_match = True
                if is_match:
                    target_record = item
                else:
                    new_history.append(item)
            if target_record:
                with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
                    json.dump(new_history, f, ensure_ascii=False, indent=4)

        if target_record:
            for img_url in target_record.get("images", []):
                file_path = output_file_from_url(img_url)
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        print(f"Failed to delete file {file_path}: {e}")
            return {"success": True}
        else:
            return {"success": False, "message": "Record not found"}
    except Exception as e:
        print(f"Delete history error: {e}")
        return {"success": False, "message": str(e)}

async def open_output_location(req: OutputLocationRequest):
    path = output_file_from_url(req.url)
    if not path:
        raise HTTPException(status_code=404, detail="Output file not found.")
    ok = _open_local_path(path, select_file=True)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to open output file location.")
    return {"ok": True, "target": path, "selected": True}

async def open_output_dir():
    target = os.path.abspath(str(legacy.OUTPUT_OUTPUT_DIR))
    ok = _open_local_path(target, select_file=False)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to open output directory.")
    return {"ok": True, "target": target}
