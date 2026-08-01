from fastapi import APIRouter, Request, status
from fastapi.responses import FileResponse

from app.models.node_extensions import (
    NodeExtensionApplyRequest,
    NodeExtensionDependencyInstallRequest,
    NodeExtensionUpdateRequest,
    NodeRunCreateRequest,
)
from app.services import node_extension_service as service


router = APIRouter()


@router.get("/api/node-extensions")
async def list_node_extensions():
    return service.registry.public_state()


@router.post("/api/node-extensions/rescan")
async def rescan_node_extensions():
    return service.registry.rescan()


@router.patch("/api/node-extensions/{package_id}")
async def update_node_extension(package_id: str, payload: NodeExtensionUpdateRequest):
    return service.registry.set_enabled(package_id, payload.enabled)


@router.post("/api/node-extensions/{package_id}/dependencies/install")
async def install_node_extension_dependencies(
    package_id: str,
    payload: NodeExtensionDependencyInstallRequest,
):
    return await service.registry.install_dependencies(package_id, payload.confirmed)


@router.post("/api/node-extensions/apply")
async def apply_node_extension_changes(payload: NodeExtensionApplyRequest):
    return service.apply_extension_changes(payload.restart_delay)


@router.get("/api/node-extensions/{package_id}/web/{asset_path:path}")
async def node_extension_web_asset(package_id: str, asset_path: str):
    path = service.registry.web_asset_path(package_id, asset_path)
    # Extension entry modules may import sibling files with relative URLs. Those
    # imports do not inherit the registry revision query string, so caching them
    # can mix a new entry module with stale dependencies after an upgrade.
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@router.post("/api/node-runs", status_code=status.HTTP_202_ACCEPTED)
async def create_node_run(payload: NodeRunCreateRequest, request: Request):
    return service.run_manager.submit(payload, request.app)


@router.get("/api/node-runs/{run_id}")
async def get_node_run(run_id: str):
    return service.run_manager.get(run_id)


@router.delete("/api/node-runs/{run_id}")
async def cancel_node_run(run_id: str):
    return service.run_manager.cancel(run_id)
