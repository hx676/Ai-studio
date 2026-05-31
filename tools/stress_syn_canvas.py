from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import os
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from test_report import ROOT, TestRun, create_run_dir, ensure_utf8_stdio, guard_allows_continue, write_summary


PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?"
    b"\x00\x05\xfe\x02\xfeA\xe2!\xbc\x00\x00\x00\x00IEND\xaeB`\x82"
)


def wav_bytes(seconds: float = 0.4, hz: int = 440, rate: int = 16000) -> bytes:
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


def first_file(patterns: List[str], max_mb: float = 80) -> Optional[Path]:
    files: List[Path] = []
    for pattern in patterns:
        files.extend(ROOT.glob(pattern))
    files = [p for p in files if p.is_file() and p.stat().st_size <= max_mb * 1024 * 1024]
    files.sort(key=lambda p: p.stat().st_size)
    return files[0] if files else None


async def timed_request(run: TestRun, client: httpx.AsyncClient, method: str, path: str, name: str, **kwargs: Any) -> Dict[str, Any]:
    start = time.perf_counter()
    try:
        response = await client.request(method, path, **kwargs)
        duration = (time.perf_counter() - start) * 1000
        status = "pass" if 200 <= response.status_code < 500 else "fail"
        body = None
        if "application/json" in response.headers.get("content-type", ""):
            try:
                body = response.json()
            except Exception:
                body = None
        run.record(name, status, duration, method=method, path=path, status_code=response.status_code, response=body)
        return {"ok": 200 <= response.status_code < 500, "status_code": response.status_code, "duration_ms": duration, "body": body}
    except Exception as exc:
        duration = (time.perf_counter() - start) * 1000
        run.record(name, "fail", duration, method=method, path=path, error=str(exc))
        return {"ok": False, "status_code": 0, "duration_ms": duration, "error": str(exc)}


async def health_ok(client: httpx.AsyncClient) -> bool:
    try:
        response = await client.get("/api/app-info", timeout=8)
        return response.status_code == 200
    except Exception:
        return False


async def guarded_phase(run: TestRun, client: httpx.AsyncClient, phase: str, coro) -> bool:
    snapshot = run.resource(f"{phase}.before")
    if not guard_allows_continue(snapshot):
        run.record(f"{phase}.guard", "fail", detail="resource guard refused phase", resource=snapshot)
        return False
    failures = 0
    for _ in range(3):
        if await health_ok(client):
            break
        failures += 1
        await asyncio.sleep(1)
    if failures >= 3:
        run.record(f"{phase}.health", "fail", detail="core service failed 3 health checks")
        return False
    await coro()
    run.resource(f"{phase}.after")
    return True


async def light_endpoint_stress(run: TestRun, client: httpx.AsyncClient, phase_seconds: float) -> None:
    endpoints = ["/api/app-info", "/api/config", "/api/providers", "/api/queue_status?client_id=stress-run"]
    for concurrency in [20, 50, 100]:
        end_at = time.perf_counter() + phase_seconds
        stats = {"total": 0, "5xx": 0}
        sem = asyncio.Semaphore(concurrency)

        async def worker(index: int) -> None:
            while time.perf_counter() < end_at:
                async with sem:
                    path = endpoints[index % len(endpoints)]
                    result = await timed_request(run, client, "GET", path, f"stress.light.c{concurrency}.{path}")
                    stats["total"] += 1
                    if int(result.get("status_code") or 0) >= 500:
                        stats["5xx"] += 1

        await asyncio.gather(*(worker(i) for i in range(concurrency)))
        error_rate = stats["5xx"] / max(1, stats["total"])
        run.record(f"stress.light.summary.c{concurrency}", "fail" if error_rate > 0.10 else "pass", total=stats["total"], error_rate=error_rate)
        if error_rate > 0.10:
            break


async def upload_stress(run: TestRun, client: httpx.AsyncClient, video_count: int) -> None:
    await timed_request(run, client, "POST", "/api/ai/upload", "stress.upload.image", files={"files": ("stress.png", PNG_1X1, "image/png")})
    await timed_request(run, client, "POST", "/api/canvas-media/upload", "stress.upload.audio", files={"files": ("stress.wav", wav_bytes(), "audio/wav")})
    video = first_file(["assets/input/digital-human/*.*", "heygem-win-fix/heygem-win/save/*.*"], max_mb=80)
    if not video:
        run.record("stress.upload.video", "skip", detail="no video under max size found")
        return
    for idx in range(video_count):
        with video.open("rb") as fh:
            await timed_request(run, client, "POST", "/api/digital-human/upload?kind=video", f"stress.upload.video.{idx+1}", files={"files": (video.name, fh, "video/mp4")})


async def canvas_stress(run: TestRun, client: httpx.AsyncClient, count: int) -> None:
    for idx in range(count):
        title = f"stress-canvas-{int(time.time())}-{idx}"
        created = await timed_request(run, client, "POST", "/api/canvases", f"stress.canvas.create.{idx}", json={"title": title, "kind": "classic", "icon": "S"})
        canvas_id = (((created.get("body") or {}).get("canvas") or {}).get("id") or (created.get("body") or {}).get("id"))
        if not canvas_id:
            continue
        await timed_request(run, client, "PUT", f"/api/canvases/{canvas_id}", f"stress.canvas.save.{idx}", json={"title": title, "icon": "S", "nodes": [], "connections": [], "viewport": {}, "logs": [], "settings": {}})
        await timed_request(run, client, "GET", f"/api/canvases/{canvas_id}", f"stress.canvas.read.{idx}")


def is_unsorted_digital_person(person: Dict[str, Any]) -> bool:
    person_id = str((person or {}).get("id") or "").strip()
    return person_id == "person_unsorted" or person_id.startswith("person_unsorted")


def digital_video_matches(video: Dict[str, Any], video_id: str = "", video_url: str = "") -> bool:
    if not video:
        return False
    if video_id and str(video.get("id") or "") == video_id:
        return True
    if video_url:
        candidates = {
            str(video.get("url") or ""),
            str(video.get("preview_url") or ""),
            str(video.get("path") or ""),
        }
        return video_url in candidates
    return False


def select_digital_voice(person: Dict[str, Any], voices: List[Dict[str, Any]]) -> Dict[str, Any]:
    default_voice = str((person or {}).get("default_voice_name") or "").strip()
    voice_pool = [v for v in voices if v.get("path")] or voices
    if default_voice:
        for voice in voice_pool:
            if default_voice in {str(voice.get("value") or ""), str(voice.get("name") or "")}:
                return voice
    return voice_pool[0] if voice_pool else {}


def resolve_digital_human_target(library: Dict[str, Any], args: argparse.Namespace) -> Optional[Dict[str, Any]]:
    people = [p for p in (library.get("people") or []) if p.get("videos")]
    normal_people = [p for p in people if not is_unsorted_digital_person(p)]
    search_people = normal_people or people
    person_id = str(args.digital_human_person_id or "").strip()
    video_id = str(args.digital_human_video_id or "").strip()
    video_url = str(args.digital_human_video_url or "").strip()

    if person_id:
        selected_person = next((p for p in people if str(p.get("id") or "") == person_id), None)
        if not selected_person:
            return None
    elif video_id or video_url:
        selected_person = next(
            (p for p in search_people if any(digital_video_matches(v, video_id, video_url) for v in (p.get("videos") or []))),
            None,
        )
        if not selected_person:
            selected_person = next(
                (p for p in people if any(digital_video_matches(v, video_id, video_url) for v in (p.get("videos") or []))),
                None,
            )
        if not selected_person and video_url:
            selected_person = search_people[0] if search_people else None
    else:
        selected_person = search_people[0] if search_people else None
    if not selected_person:
        return None

    videos = selected_person.get("videos") or []
    selected_video = None
    if video_id or video_url:
        selected_video = next((v for v in videos if digital_video_matches(v, video_id, video_url)), None)
    if not selected_video:
        current_id = str(selected_person.get("current_video_id") or "").strip()
        selected_video = next((v for v in videos if str(v.get("id") or "") == current_id), None)
    if not selected_video and video_url:
        selected_video = {"id": "", "name": os.path.basename(video_url), "url": video_url, "path": ""}
    selected_video = selected_video or (videos[0] if videos else None)
    if not selected_video:
        return None

    voices = library.get("voices") or []
    return {
        "person": selected_person,
        "video": selected_video,
        "voice": select_digital_voice(selected_person, voices),
    }


def canonical_failure_type(task: Dict[str, Any]) -> str:
    error = task.get("error")
    failure_type = str(task.get("failure_type") or "")
    if isinstance(error, dict):
        failure_type = failure_type or str(error.get("failure_type") or "")
    text = json.dumps(error, ensure_ascii=False).lower() if error is not None else ""
    stage = str(task.get("stage") or "").lower()
    if failure_type == "task_not_found":
        return "heygem_task_not_found"
    if failure_type == "stall_at_20":
        return "heygem_stall_at_20"
    if failure_type == "query_timeout" and "tts" in text:
        return "tts_timeout"
    if failure_type == "query_timeout" and ("heygem" in text or "heygem" in stage):
        return "heygem_query_timeout"
    if "queue full" in text or "队列满" in text or "下游队列异常" in text:
        return "heygem_queue_blocked"
    if "tts" in text and ("timeout" in text or "timed out" in text or "超时" in text):
        return "tts_timeout"
    return failure_type or "unknown"


def choose_digital_human_payload(target: Dict[str, Any], index: int, heygem_only: bool, audio_url: str = "") -> Optional[Dict[str, Any]]:
    person = target.get("person") or {}
    video = target.get("video") or {}
    voice = target.get("voice") or {}
    if not video:
        return None
    audio = audio_url or ""
    if heygem_only and not audio:
        found_audio = first_file(["assets/input/digital-human/*.wav", "assets/input/digital-human/*.mp3"], max_mb=20)
        audio = str(found_audio) if found_audio else ""
    payload = {
        "code": f"stress_dh_{int(time.time())}_{index}",
        "text": f"SynCanvas digital human queue stress request {index + 1}.",
        "voice_name": voice.get("value") or voice.get("name") or person.get("default_voice_name") or "",
        "voice_path": voice.get("path") or "",
        "video_url": video.get("url") or "",
        "video_path": video.get("path") or "",
    }
    if heygem_only and audio:
        payload["audio_url"] = audio
    return payload


def record_digital_queue_result(run: TestRun, submitted: List[str], tasks: List[Dict[str, Any]]) -> bool:
    selected = [t for t in tasks if t.get("task_id") in submitted]
    active = [t for t in selected if t.get("status") in {"queued", "running", "pending"}]
    if active:
        return False

    failed = [t for t in selected if t.get("status") == "failed"]
    succeeded = [t for t in selected if t.get("status") == "succeeded"]
    canceled = [t for t in selected if t.get("status") == "canceled"]
    if failed:
        run.record(
            "stress.digital.queue_complete",
            "fail",
            submitted=len(submitted),
            succeeded=len(succeeded),
            failed=len(failed),
            canceled=len(canceled),
            failure_types=sorted({canonical_failure_type(t) for t in failed}),
            errors=[
                {
                    "task_id": t.get("task_id"),
                    "stage": t.get("stage"),
                    "failure_type": canonical_failure_type(t),
                    "video_name": t.get("video_name"),
                    "retry_count": t.get("retry_count"),
                    "error": t.get("error"),
                }
                for t in failed[:10]
            ],
        )
    else:
        run.record(
            "stress.digital.queue_complete",
            "pass",
            submitted=len(submitted),
            succeeded=len(succeeded),
            canceled=len(canceled),
        )
    return True


async def digital_human_queue_stress(run: TestRun, client: httpx.AsyncClient, args: argparse.Namespace) -> None:
    library_result = await timed_request(run, client, "GET", "/api/digital-human/library", "stress.digital.library")
    library = library_result.get("body") or {}
    target = resolve_digital_human_target(library, args)
    if not target:
        run.record("stress.digital.target", "fail", detail="no matching normal person/current video available")
        return
    person = target.get("person") or {}
    video = target.get("video") or {}
    heygem_only = bool(args.digital_human_heygem_only)
    run.record(
        "stress.digital.target",
        "pass",
        mode="heygem_only" if heygem_only else "full_pipeline",
        person_id=person.get("id"),
        person_name=person.get("name"),
        current_video_id=person.get("current_video_id"),
        video_id=video.get("id"),
        video_name=video.get("name"),
        video_url=video.get("url"),
        video_path=video.get("path"),
        voice=(target.get("voice") or {}).get("value") or (target.get("voice") or {}).get("name") or person.get("default_voice_name") or "",
    )
    submitted: List[str] = []
    for idx in range(args.digital_human_jobs):
        payload = choose_digital_human_payload(target, idx, heygem_only, args.digital_human_audio_url)
        if not payload:
            run.record("stress.digital.submit", "skip", detail="no person with video available")
            return
        result = await timed_request(run, client, "POST", "/api/digital-human/generate", f"stress.digital.submit.{idx+1}", json=payload)
        task_id = ((result.get("body") or {}).get("task_id") or payload["code"])
        submitted.append(task_id)
        await asyncio.sleep(0.15)
    last_signature = ""
    last_change = time.time()
    task_limit = max(80, args.digital_human_jobs + 30)
    deadline = time.time() + args.digital_human_wait_seconds
    while time.time() < deadline:
        snapshot = await timed_request(run, client, "GET", f"/api/digital-human/tasks?limit={task_limit}", "stress.digital.tasks")
        body = snapshot.get("body") or {}
        tasks = body.get("tasks") or []
        queue_state = body.get("queue") or {}
        active = [t for t in tasks if t.get("task_id") in submitted and t.get("status") in {"queued", "running", "pending"}]
        running = [t for t in active if t.get("status") == "running"]
        signature = json.dumps([(t.get("task_id"), t.get("status"), t.get("stage"), t.get("queue_position")) for t in tasks if t.get("task_id") in submitted], ensure_ascii=False)
        if signature != last_signature:
            last_signature = signature
            last_change = time.time()
        if len(running) > 1:
            run.record("stress.digital.serial_guard", "fail", detail="more than one digital human task running", running=running)
            return
        if queue_state.get("paused"):
            selected = [t for t in tasks if t.get("task_id") in submitted]
            failed = [t for t in selected if t.get("status") == "failed"]
            run.record(
                "stress.digital.queue_paused",
                "fail",
                detail=queue_state.get("pause_reason") or "digital human queue paused",
                queue=queue_state,
                submitted=len(submitted),
                active=len(active),
                failure_types=sorted({canonical_failure_type(t) for t in failed}),
                failures=[
                    {
                        "task_id": t.get("task_id"),
                        "stage": t.get("stage"),
                        "failure_type": canonical_failure_type(t),
                        "video_name": t.get("video_name"),
                        "retry_count": t.get("retry_count"),
                        "error": t.get("error"),
                    }
                    for t in failed[:10]
                ],
            )
            return
        if not active:
            record_digital_queue_result(run, submitted, tasks)
            return
        if time.time() - last_change > 600:
            run.record("stress.digital.queue_stalled", "fail", detail="no task state change for 10 minutes", active=active[:5])
            return
        if not guard_allows_continue(run.resource("stress.digital.monitor")):
            run.record("stress.digital.guard", "fail", detail="resource guard stopped digital human stress")
            return
        await asyncio.sleep(10)

    snapshot = await timed_request(run, client, "GET", f"/api/digital-human/tasks?limit={task_limit}", "stress.digital.tasks.final")
    tasks = (snapshot.get("body") or {}).get("tasks") or []
    if record_digital_queue_result(run, submitted, tasks):
        return
    active = [t for t in tasks if t.get("task_id") in submitted and t.get("status") in {"queued", "running", "pending"}]
    run.record("stress.digital.wait_timeout", "warn", detail="tasks still active after wait window", submitted=len(submitted), active=active[:5])


async def external_provider_stress(run: TestRun, client: httpx.AsyncClient, budget: int) -> None:
    providers_result = await timed_request(run, client, "GET", "/api/providers", "stress.external.providers")
    providers = providers_result.get("body") or []
    if isinstance(providers, dict):
        providers = providers.get("providers") or []
    enabled = [p for p in providers if p.get("enabled", True)]
    for provider in enabled:
        provider_id = provider.get("id") or ""
        image_models = provider.get("image_models") or []
        chat_models = provider.get("chat_models") or []
        video_models = provider.get("video_models") or []
        if image_models:
            for idx in range(min(budget, len(image_models) or budget)):
                model = image_models[idx % len(image_models)]
                result = await timed_request(run, client, "POST", "/api/online-image", f"stress.external.image.{provider_id}.{idx+1}", json={
                    "prompt": f"SynCanvas pressure test image {idx+1}",
                    "provider_id": provider_id,
                    "model": model,
                    "size": "1024x1024",
                    "quality": "auto",
                    "reference_images": [],
                })
                if int(result.get("status_code") or 0) in {401, 402, 403, 429}:
                    break
        if chat_models:
            for idx in range(min(budget, len(chat_models) or budget)):
                model = chat_models[idx % len(chat_models)]
                result = await timed_request(run, client, "POST", "/api/chat", f"stress.external.chat.{provider_id}.{idx+1}", json={
                    "message": f"SynCanvas pressure test chat {idx+1}. Reply briefly.",
                    "provider": provider_id,
                    "model": model,
                })
                if int(result.get("status_code") or 0) in {401, 402, 403, 429}:
                    break
        if video_models:
            for idx in range(min(budget, len(video_models) or budget)):
                model = video_models[idx % len(video_models)]
                result = await timed_request(run, client, "POST", "/api/canvas-video", f"stress.external.video.{provider_id}.{idx+1}", json={
                    "prompt": f"SynCanvas pressure test short video {idx+1}",
                    "provider_id": provider_id,
                    "model": model,
                    "duration": 5,
                    "aspect_ratio": "16:9",
                    "images": [],
                    "audios": [],
                    "videos": [],
                })
                if int(result.get("status_code") or 0) in {401, 402, 403, 429}:
                    break


async def run_stress(args: argparse.Namespace) -> int:
    run_dir = create_run_dir(args.run_id)
    run = TestRun(run_dir, "stress_syn_canvas", args.base_url)
    run.copy_manual_checklist()
    run.resource("start")
    async with httpx.AsyncClient(base_url=args.base_url, timeout=httpx.Timeout(args.timeout, connect=10.0), follow_redirects=True) as client:
        if args.skip_light:
            run.record("stress.light", "skip", detail="skipped by command line")
        else:
            await guarded_phase(run, client, "light", lambda: light_endpoint_stress(run, client, args.phase_seconds))
        if args.skip_upload:
            run.record("stress.upload", "skip", detail="skipped by command line")
        else:
            await guarded_phase(run, client, "upload", lambda: upload_stress(run, client, args.upload_video_count))
        if args.skip_canvas:
            run.record("stress.canvas", "skip", detail="skipped by command line")
        else:
            await guarded_phase(run, client, "canvas", lambda: canvas_stress(run, client, args.canvas_count))
        if args.skip_digital:
            run.record("stress.digital", "skip", detail="skipped by command line")
        else:
            await guarded_phase(run, client, "digital_human", lambda: digital_human_queue_stress(run, client, args))
        if args.external and not args.skip_external:
            await guarded_phase(run, client, "external", lambda: external_provider_stress(run, client, args.external_budget))
        else:
            run.record("stress.external", "skip", detail="external provider stress disabled")
    run.resource("end")
    summary = write_summary(run_dir, "SynCanvas Stress Test")
    print(f"RUN_DIR={run_dir}")
    print(f"SUMMARY={summary}")
    return 0


def main() -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Run guarded SynCanvas stress tests.")
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--phase-seconds", type=float, default=20.0)
    parser.add_argument("--upload-video-count", type=int, default=2)
    parser.add_argument("--canvas-count", type=int, default=50)
    parser.add_argument("--digital-human-jobs", type=int, default=20)
    parser.add_argument("--digital-human-wait-seconds", type=float, default=3600.0)
    parser.add_argument("--digital-human-person-id", default="", help="Lock digital human stress to one person id.")
    parser.add_argument("--digital-human-video-id", default="", help="Lock digital human stress to one video id.")
    parser.add_argument("--digital-human-video-url", default="", help="Lock digital human stress to one video URL/path.")
    parser.add_argument("--digital-human-audio-url", default="", help="Audio URL/path used when --digital-human-heygem-only is set.")
    parser.add_argument("--digital-human-heygem-only", action="store_true", help="Reuse one existing audio file so the stress run isolates HeyGem.")
    parser.add_argument("--external", action="store_true", help="Opt in to real external provider requests; skipped by default.")
    parser.add_argument("--external-budget", type=int, default=10)
    parser.add_argument("--skip-light", action="store_true")
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--skip-canvas", action="store_true")
    parser.add_argument("--skip-digital", action="store_true")
    parser.add_argument("--skip-external", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run_stress(args))


if __name__ == "__main__":
    raise SystemExit(main())
