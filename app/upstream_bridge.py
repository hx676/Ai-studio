"""Expose non-conflicting routes from the original Infinite Canvas runtime.

SynCanvas has diverged into a modular application with additional launcher,
digital-human, Agent/Skill, and ComfyUI features.  The original project no
longer shares Git history or module boundaries with this repository, so its
new endpoints are installed as a compatibility layer.  Existing SynCanvas
routes always win.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app import legacy
from app import upstream_runtime


UPSTREAM_COMMIT = upstream_runtime.UPSTREAM_SOURCE_COMMIT
UPSTREAM_VERSION = upstream_runtime.UPSTREAM_SOURCE_VERSION

# Advertising an upstream update and then running SynCanvas' updater against
# the unrelated repository could overwrite local modules.  Keep update checks
# owned by SynCanvas while still importing all feature endpoints.
EXCLUDED_PATHS = {
    "/api/check-update",
    "/api/update-connectivity",
    "/api/update-connectivity/probe",
}

# The upstream asset-library schema is backward compatible with the original
# flat ``categories`` response and adds multi-library/media support required by
# the new asset manager.  Let it replace the older SynCanvas handlers while
# keeping every other existing route local-first.
PREFER_UPSTREAM_PREFIXES = ("/api/asset-library",)

# Keep the compatibility surface reviewable.  A new route added to the frozen
# upstream snapshot is not exposed by SynCanvas until it is deliberately added
# here.  The key includes the method because several paths serve both reads and
# writes with different handlers.
_UPSTREAM_ROUTE_MANIFEST = """
GET /api/storage-settings
PATCH /api/storage-settings
GET /api/storage-files
GET /api/storage-files/{kind}/{rel_path:path}
POST /api/storage-files/delete
GET /api/asset-classification-prompt
PATCH /api/asset-classification-prompt
GET /api/media-preview
GET /api/image-jpeg
POST /api/ai/upload-base64
POST /api/comfyui/upload-base64
POST /api/local-assets/upload
POST /api/local-assets/import-urls
GET /api/local-assets
POST /api/local-assets/folders
PATCH /api/local-assets/folders
PATCH /api/local-assets/items
POST /api/local-assets/delete
POST /api/local-assets/move
POST /api/local-assets/caption
POST /api/local-assets/classify
PATCH /api/local-assets/caption
POST /api/temp-sh/upload
POST /api/cloud-video/upload
POST /api/ai/import-local-image
GET /api/runninghub/app-info
POST /api/runninghub/submit
POST /api/runninghub/workflow-submit
GET /api/runninghub/workflow-info
GET /api/runninghub/workflows
GET /api/runninghub/workflows/{workflow_id:path}
POST /api/runninghub/workflows/fetch
PUT /api/runninghub/workflows/{workflow_id:path}
DELETE /api/runninghub/workflows/{workflow_id:path}
GET /api/runninghub/query
POST /api/runninghub/upload-asset
GET /api/codex/status
POST /api/codex/help
GET /api/gemini-cli/status
POST /api/gemini-cli/help
GET /api/jimeng/status
POST /api/jimeng/install/start
GET /api/jimeng/credit
POST /api/jimeng/logout
POST /api/jimeng/login/start
GET /api/jimeng/login/qr
GET /api/jimeng/login/status
POST /api/jimeng/help
POST /api/jimeng/query-media
POST /api/image-task-query
POST /api/canvas-comfy-tasks
GET /api/canvas-comfy-tasks/{task_id}
GET /api/image-params
GET /api/projects
POST /api/projects
POST /api/projects/{project_id}
DELETE /api/projects/{project_id}
GET /api/canvases/{canvas_id}/meta
POST /api/canvases/{canvas_id}/meta
POST /api/canvases/{canvas_id}/touch
GET /api/canvas-assets
GET /api/smart-canvas/prompt-templates
POST /api/canvas-workflows/export
POST /api/canvas-workflows/export-to-library
POST /api/asset-library/workflows/upload
POST /api/canvas-workflows/import
POST /api/smart-canvas/group-export
GET /api/asset-library
GET /api/asset-url-library
POST /api/asset-url-library/items
PATCH /api/asset-url-library/items/{item_id}
DELETE /api/asset-url-library/items/{item_id}
GET /api/prompt-libraries
POST /api/prompt-libraries
PATCH /api/prompt-libraries/{library_id}
DELETE /api/prompt-libraries/{library_id}
POST /api/prompt-libraries/items
PATCH /api/prompt-libraries/items/{item_id}
DELETE /api/prompt-libraries/items/{item_id}
POST /api/prompt-libraries/items/delete
POST /api/prompt-libraries/categories
PATCH /api/prompt-libraries/categories/{category_id}
DELETE /api/prompt-libraries/categories/{category_id}
POST /api/asset-library/libraries
PATCH /api/asset-library/libraries/{library_id}
DELETE /api/asset-library/libraries/{library_id}
POST /api/asset-library/categories
PATCH /api/asset-library/categories/{category_id}
DELETE /api/asset-library/categories/{category_id}
POST /api/asset-library/items
POST /api/asset-library/items/batch
GET /api/shared-folders
POST /api/shared-folders
DELETE /api/shared-folders/{folder_id}
GET /api/shared-folders/{folder_id}/tree
GET /api/shared-folders/{folder_id}/file
POST /api/shared-folders/import
PATCH /api/asset-library/items/{item_id}
POST /api/asset-library/items/classify
POST /api/asset-library/items/{item_id}/register-avatar
POST /api/asset-library/items/{item_id}/avatar-status
DELETE /api/asset-library/items/{item_id}
POST /api/asset-library/items/delete
POST /api/asset-library/items/move
POST /api/asset-library/items/crop
POST /api/canvases/{canvas_id}/logs/delete
POST /api/chat/agent
POST /api/chat/agent/stream
"""
UPSTREAM_ALLOWED_ROUTE_KEYS = frozenset(
    (path, method)
    for line in _UPSTREAM_ROUTE_MANIFEST.strip().splitlines()
    for method, path in (line.split(" ", 1),)
)


def _route_key(route: APIRoute) -> tuple[str, frozenset[str]]:
    return route.path, frozenset(route.methods or ())


def install_upstream_routes(app: FastAPI) -> dict[str, Any]:
    """Append reviewed upstream API routes that are not owned by SynCanvas."""

    existing = {
        _route_key(route)
        for route in app.router.routes
        if isinstance(route, APIRoute)
    }
    installed: list[str] = []
    replaced: list[str] = []
    skipped: list[str] = []
    unreviewed: list[str] = []

    for route in upstream_runtime.app.router.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        route_methods = frozenset(route.methods or ())
        if not route_methods or any((route.path, method) not in UPSTREAM_ALLOWED_ROUTE_KEYS for method in route_methods):
            unreviewed.append(f"{','.join(sorted(route_methods))} {route.path}")
            continue
        key = _route_key(route)
        prefer_upstream = any(route.path.startswith(prefix) for prefix in PREFER_UPSTREAM_PREFIXES)
        if prefer_upstream and key in existing:
            app.router.routes[:] = [
                current
                for current in app.router.routes
                if not (isinstance(current, APIRoute) and _route_key(current) == key)
            ]
            existing.discard(key)
            replaced.append(route.path)
        if route.path in EXCLUDED_PATHS or key in existing:
            skipped.append(route.path)
            continue
        app.router.routes.append(route)
        existing.add(key)
        installed.append(route.path)

    return {
        "commit": UPSTREAM_COMMIT,
        "version": UPSTREAM_VERSION,
        "installed_count": len(installed),
        "installed_paths": installed,
        "replaced_count": len(replaced),
        "replaced_paths": replaced,
        "skipped_count": len(skipped),
        "unreviewed_count": len(unreviewed),
        "unreviewed_routes": unreviewed,
    }


async def initialize_upstream_runtime() -> None:
    """Initialize the small amount of runtime state used by imported routes."""

    loop = asyncio.get_running_loop()
    legacy.GLOBAL_LOOP = loop
    upstream_runtime.GLOBAL_LOOP = loop

    # Imported upstream routes operate on the same files as the modular app.
    # Share the actual manager and locks rather than merely copying values.
    upstream_runtime.manager = legacy.manager
    upstream_runtime.CANVAS_TASKS = legacy.CANVAS_TASKS
    for name in (
        "QUEUE_LOCK",
        "HISTORY_LOCK",
        "GLOBAL_CONFIG_LOCK",
        "CONVERSATION_LOCK",
        "CANVAS_LOCK",
        "LOAD_LOCK",
        "UPDATE_LOCK",
        "CANVAS_TASK_LOCK",
    ):
        shared = getattr(upstream_runtime, name, None) or getattr(legacy, name, None)
        if shared is not None:
            setattr(upstream_runtime, name, shared)
            setattr(legacy, name, shared)

    upstream_runtime.COMFYUI_INSTANCES = list(legacy.COMFYUI_INSTANCES)
    upstream_runtime.COMFYUI_ADDRESS = legacy.COMFYUI_ADDRESS
    upstream_runtime.BACKEND_LOCAL_LOAD = legacy.BACKEND_LOCAL_LOAD
    migrations = (
        upstream_runtime.migrate_asset_library_into_dirs,
        upstream_runtime.migrate_double_extension_uploads,
        upstream_runtime.migrate_mislabeled_image_extensions,
    )
    for migration in migrations:
        try:
            await asyncio.to_thread(migration)
        except Exception as exc:
            print(f"Upstream compatibility migration failed ({migration.__name__}): {exc}")
