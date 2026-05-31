from __future__ import annotations

import argparse
import io
import json
import math
import os
import subprocess
import time
import urllib.parse
import wave
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import httpx

from test_report import ROOT, TestRun, create_run_dir, ensure_utf8_stdio, guard_allows_continue, write_summary


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?"
    b"\x00\x05\xfe\x02\xfeA\xe2!\xbc\x00\x00\x00\x00IEND\xaeB`\x82"
)


def wav_bytes(seconds: float = 0.25, hz: int = 440, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for i in range(int(seconds * rate)):
            value = int(math.sin(2 * math.pi * hz * i / rate) * 12000)
            frames += int(value).to_bytes(2, "little", signed=True)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


def run_supervisor_status(run: TestRun) -> None:
    start = time.perf_counter()
    try:
        raw = subprocess.check_output(
            ["python", "-B", "tools\\service_supervisor.py", "--status", "--json", "--no-gpu", "--quick"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.STDOUT,
            timeout=60,
        )
        data = json.loads(raw)
        run.record(
            "supervisor.quick_status",
            "pass" if data.get("ok") else "fail",
            (time.perf_counter() - start) * 1000,
            counts=data.get("counts"),
            services=[{"key": s.get("key"), "state": s.get("state"), "ready": s.get("ready")} for s in data.get("services", [])],
        )
    except Exception as exc:
        run.record("supervisor.quick_status", "fail", (time.perf_counter() - start) * 1000, error=str(exc))


async def request_json(
    run: TestRun,
    client: httpx.AsyncClient,
    method: str,
    path: str,
    name: str,
    expected_statuses: Optional[Sequence[int]] = None,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    start = time.perf_counter()
    try:
        response = await client.request(method, path, **kwargs)
        duration = (time.perf_counter() - start) * 1000
        content_type = response.headers.get("content-type", "")
        payload: Any = None
        if "application/json" in content_type:
            payload = response.json()
        if expected_statuses is not None:
            ok = response.status_code in set(expected_statuses)
        else:
            ok = 200 <= response.status_code < 300
        status = "pass" if ok else "fail"
        run.record(name, status, duration, method=method, path=path, status_code=response.status_code, response=payload)
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        run.record(name, "fail", (time.perf_counter() - start) * 1000, method=method, path=path, error=str(exc))
        return None


async def basic_endpoints(run: TestRun, client: httpx.AsyncClient) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    endpoints = [
        ("GET", "/api/app-info", "api.app_info"),
        ("GET", "/api/config", "api.config"),
        ("GET", "/api/providers", "api.providers"),
        ("GET", "/api/models", "api.models"),
        ("GET", "/api/queue_status?client_id=test-run", "api.queue_status"),
        ("GET", "/api/digital-human/config", "digital.config"),
        ("GET", "/api/digital-human/tts/status", "digital.tts_status"),
        ("GET", "/api/digital-human/heygem/status", "digital.heygem_status"),
        ("GET", "/api/digital-human/library", "digital.library"),
        ("GET", "/api/digital-human/tasks", "digital.tasks"),
        ("GET", "/api/workflows", "comfy.workflows"),
        ("GET", "/api/canvases", "canvas.list"),
        ("GET", "/api/canvases/trash", "canvas.trash"),
        ("GET", "/api/asset-library", "asset.library"),
        ("GET", "/api/history?type=online", "history.online"),
    ]
    for method, path, name in endpoints:
        payload = await request_json(run, client, method, path, name)
        if payload is not None:
            data[name] = payload
    return data


async def upload_checks(run: TestRun, client: httpx.AsyncClient) -> Dict[str, Any]:
    uploaded: Dict[str, Any] = {}
    files = {"files": ("syn-test.png", PNG_1X1, "image/png")}
    payload = await request_json(run, client, "POST", "/api/ai/upload", "upload.ai_reference", files=files)
    if payload:
        uploaded["image"] = (payload.get("files") or [{}])[0]

    audio_files = {"files": ("syn-test.wav", wav_bytes(), "audio/wav")}
    payload = await request_json(run, client, "POST", "/api/canvas-media/upload", "upload.canvas_audio", files=audio_files)
    if payload:
        uploaded["audio"] = payload
    return uploaded


async def canvas_checks(run: TestRun, client: httpx.AsyncClient, uploaded: Dict[str, Any]) -> None:
    title = f"test-canvas-{int(time.time())}"
    created = await request_json(run, client, "POST", "/api/canvases", "canvas.create", json={"title": title, "kind": "classic", "icon": "T"})
    canvas = created.get("canvas") if isinstance(created, dict) else created
    canvas_id = (canvas or {}).get("id") if isinstance(canvas, dict) else ""
    if not canvas_id:
        run.record("canvas.flow", "fail", detail="missing canvas id")
        return
    await request_json(run, client, "GET", f"/api/canvases/{canvas_id}/meta", "canvas.meta")
    await request_json(run, client, "GET", f"/api/canvases/{canvas_id}", "canvas.get")
    audio = uploaded.get("audio") or {}
    nodes = [
        {"id": "n_audio", "type": "audio", "x": 0, "y": 0, "audio": {"url": audio.get("url", ""), "name": audio.get("name", "syn-test.wav"), "mime": audio.get("mime", "audio/wav")}},
        {"id": "n_output", "type": "output", "x": 260, "y": 0, "images": []},
    ]
    await request_json(run, client, "PUT", f"/api/canvases/{canvas_id}", "canvas.save_audio_output", json={"title": title, "icon": "T", "nodes": nodes, "connections": [], "viewport": {}, "logs": [], "settings": {}})
    await request_json(run, client, "POST", "/api/canvas-assets/check", "canvas.assets_check", json={"urls": [audio.get("url", "")] if audio.get("url") else []})
    await request_json(run, client, "DELETE", f"/api/canvases/{canvas_id}", "canvas.delete")
    await request_json(run, client, "POST", f"/api/canvases/{canvas_id}/restore", "canvas.restore")


async def asset_library_checks(run: TestRun, client: httpx.AsyncClient, uploaded: Dict[str, Any]) -> None:
    name = f"test-assets-{int(time.time())}"
    cat = await request_json(run, client, "POST", "/api/asset-library/categories", "asset.category_create", json={"name": name, "type": "image"})
    category = cat.get("category") if isinstance(cat, dict) else None
    category_id = (category or {}).get("id") if isinstance(category, dict) else ""
    if not category_id:
        run.record("asset.flow", "warn", detail="category id missing; skipping item mutation")
        return
    image_url = (uploaded.get("image") or {}).get("url", "")
    if image_url:
        item = await request_json(run, client, "POST", "/api/asset-library/items", "asset.item_add", json={"category_id": category_id, "url": image_url, "name": "syn-test.png"})
        item_id = ((item or {}).get("item") or {}).get("id") if isinstance(item, dict) else ""
        if item_id:
            await request_json(run, client, "PATCH", f"/api/asset-library/items/{item_id}", "asset.item_rename", json={"name": "syn-test-renamed.png"})
            await request_json(run, client, "DELETE", f"/api/asset-library/items/{item_id}", "asset.item_delete")
    await request_json(run, client, "PATCH", f"/api/asset-library/categories/{category_id}", "asset.category_rename", json={"name": f"{name}-renamed"})
    await request_json(run, client, "DELETE", f"/api/asset-library/categories/{category_id}", "asset.category_delete")


async def digital_human_checks(run: TestRun, client: httpx.AsyncClient, library: Dict[str, Any]) -> None:
    people = library.get("people") or []
    voices = library.get("voices") or []
    if not people:
        run.record("digital.people_available", "warn", detail="no people in library")
        return
    person = people[0]
    videos = person.get("videos") or []
    if not videos:
        run.record("digital.videos_available", "warn", detail=f"person {person.get('name')} has no videos")
        return
    video = videos[0]
    if video.get("id"):
        await request_json(run, client, "POST", f"/api/digital-human/library/people/{person.get('id')}/videos/{video.get('id')}/poster", "digital.poster_backfill")
    if voices:
        run.record("digital.voices_available", "pass", voices=len(voices), first=voices[0].get("name") or voices[0].get("value"))
    else:
        run.record("digital.voices_available", "warn", detail="no TTS voices listed")
    await request_json(run, client, "GET", "/api/digital-human/tasks", "digital.queue_snapshot")


async def file_security_checks(run: TestRun, client: httpx.AsyncClient, uploaded: Dict[str, Any]) -> None:
    image_url = (uploaded.get("image") or {}).get("url", "")
    if image_url:
        filename = os.path.basename(urllib.parse.urlparse(image_url).path)
        await request_json(run, client, "GET", f"/api/view?filename={urllib.parse.quote(filename)}&type=input", "file.view_uploaded")
    await request_json(run, client, "GET", f"/api/view?filename={urllib.parse.quote('C:/Windows/win.ini')}&type=input", "file.reject_outside_path", expected_statuses=[400, 403, 404])


async def run_checks(args: argparse.Namespace) -> int:
    run_dir = create_run_dir(args.run_id)
    run = TestRun(run_dir, "full_function_check", args.base_url)
    run.copy_manual_checklist()
    run.resource("start")
    run_supervisor_status(run)
    if not guard_allows_continue(run.resource("guard.before")):
        run.record("guard.before", "fail", detail="resource guard refused to start")
        write_summary(run_dir, "SynCanvas Full Function Check")
        return 2
    async with httpx.AsyncClient(base_url=args.base_url, timeout=httpx.Timeout(args.timeout, connect=10.0), follow_redirects=True) as client:
        data = await basic_endpoints(run, client)
        uploaded = await upload_checks(run, client)
        await canvas_checks(run, client, uploaded)
        await asset_library_checks(run, client, uploaded)
        await digital_human_checks(run, client, data.get("digital.library") or {})
        await file_security_checks(run, client, uploaded)
    run.resource("end")
    summary = write_summary(run_dir, "SynCanvas Full Function Check")
    print(f"RUN_DIR={run_dir}")
    print(f"SUMMARY={summary}")
    return 0


def main() -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Run SynCanvas API-level full function checks.")
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    return __import__("asyncio").run(run_checks(args))


if __name__ == "__main__":
    raise SystemExit(main())
