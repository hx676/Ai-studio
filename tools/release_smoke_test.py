"""Start a staged SynCanvas release with empty data and verify core features."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


REQUIRED_PATHS = (
    "LICENSE",
    "components-manifest.json",
    "node-engine-manifest.json",
    "custom_nodes/syncanvas_agent_skill/node.json",
    "custom_nodes/syncanvas_image_compare/node.json",
    "custom_nodes/syncanvas_output_folder/node.json",
    "custom_nodes/syncanvas_runtime_node/node.json",
    "custom_nodes/syncanvas_templates/node.json",
    "static/agent-skills.html",
    "static/canvas.html",
    "static/smart-canvas.html",
    "static/node-engine.html",
    "static/css/canvas-assistant.css",
    "static/js/canvas-assistant.js",
    "static/vendor/css/tailwind.css",
    "static/workflows/reference-style-prompt.classic.json",
    "static/workflows/reference-style-prompt.smart.json",
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def get_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def get_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=3) as response:
        return response.read().decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    missing = [item for item in REQUIRED_PATHS if not (root / item).exists()]
    if missing:
        raise RuntimeError("release paths missing: " + ", ".join(missing))
    data_dir = root / "data"
    if data_dir.exists() and any(data_dir.iterdir()):
        raise RuntimeError("release smoke test requires an empty staged data directory")
    data_dir.mkdir(parents=True, exist_ok=True)
    port = free_port()
    env = {
        **os.environ,
        "SYNCANVAS_MAIN_HOST": "127.0.0.1",
        "SYNCANVAS_MAIN_PORT": str(port),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    process = subprocess.Popen(
        [sys.executable, str(root / "main.py")],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 45
        last_error = ""
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"staged server exited with {process.returncode}")
            try:
                agents = get_json(base + "/api/agents")
                workflows = get_json(base + "/api/skills")
                engine = get_json(base + "/api/components/node-engine/status")
                extensions = get_json(base + "/api/node-extensions")
                assistant_sources = get_json(base + "/api/canvas-assistant/sources")
                if not all(isinstance(item, dict) for item in (agents, workflows, engine, extensions, assistant_sources)):
                    raise RuntimeError("core API response shape is invalid")
                extension_types = {item.get("type") for item in extensions.get("nodes", [])}
                if "syncanvas.output-folder/export" not in extension_types:
                    raise RuntimeError("output-to-folder native node is missing")
                if "syncanvas.templates/call" not in extension_types or "syncanvas.templates/store" not in extension_types:
                    raise RuntimeError("template native nodes are missing")
                canvas_page = get_text(base + "/static/canvas.html")
                smart_page = get_text(base + "/static/smart-canvas.html")
                api_page = get_text(base + "/static/api-settings.html")
                features_js = get_text(base + "/static/js/upstream-canvas-features.js")
                compare_manifest = json.loads(
                    (root / "custom_nodes" / "syncanvas_image_compare" / "node.json").read_text(encoding="utf-8")
                )
                if compare_manifest.get("id") != "syncanvas.image-compare":
                    raise RuntimeError("image compare native node is missing")
                if (
                    "reference-style-prompt.classic.json" not in features_js
                    or "reference-style-prompt.smart.json" not in features_js
                ):
                    raise RuntimeError("built-in AI workflow entry is missing")
                if "canvas-assistant.js" not in canvas_page or "canvas-assistant.js" not in smart_page:
                    raise RuntimeError("canvas assistant is missing from one of the canvases")
                if "tailwindcss-cdn.js" in canvas_page + smart_page + api_page:
                    raise RuntimeError("release still loads the Tailwind browser compiler")
                if "/static/js/api-settings.js" not in api_page or "/static/css/api-settings.css" not in api_page:
                    raise RuntimeError("API settings page does not use its unique external assets")
                print("Clean staged release smoke test passed.")
                return 0
            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.3)
        raise RuntimeError(f"staged server did not become ready: {last_error}")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
