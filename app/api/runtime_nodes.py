import asyncio

from fastapi import APIRouter, HTTPException, Query, status

from app.models.runtime_nodes import (
    NodeEngineExtensionActionRequest,
    NodeEngineExtensionInstallRequest,
    NodeEngineInstallRequest,
    NodeEngineModelImportRequest,
    NodeEngineModelPathsRequest,
    RuntimeGraphRunRequest,
    RuntimeProcessRequest,
)
from app.services import node_engine_asset_service, node_engine_component_service, node_engine_service


router = APIRouter()


@router.get("/api/components/node-engine/status")
async def node_engine_component_status():
    payload = node_engine_component_service.get_status()
    catalog = node_engine_service.load_catalog().get("meta") or {}
    payload["catalog"] = catalog
    return payload


def _start_component_install(payload: NodeEngineInstallRequest, force: bool = False):
    try:
        return node_engine_component_service.start_install(
            install_root=payload.install_root or "",
            manifest_url=payload.manifest_url or "",
            source_root=payload.source_root or "",
            force=force or payload.force,
        )
    except node_engine_component_service.NodeEngineComponentBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except node_engine_component_service.NodeEngineComponentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/components/node-engine/install")
async def install_node_engine(payload: NodeEngineInstallRequest):
    return _start_component_install(payload)


@router.post("/api/components/node-engine/repair")
async def repair_node_engine(payload: NodeEngineInstallRequest):
    return _start_component_install(payload, force=True)


@router.post("/api/components/node-engine/cancel")
async def cancel_node_engine_install():
    return node_engine_component_service.cancel_install()


@router.post("/api/node-engine/start")
async def start_node_engine(payload: RuntimeProcessRequest):
    return await node_engine_service.start_engine(payload.wait_seconds)


@router.post("/api/node-engine/stop")
async def stop_node_engine():
    return await node_engine_service.stop_engine()


@router.post("/api/node-engine/restart")
async def restart_node_engine(payload: RuntimeProcessRequest):
    return await node_engine_service.restart_engine(payload.wait_seconds)


@router.post("/api/runtime-nodes/rescan")
async def rescan_runtime_nodes():
    return await asyncio.to_thread(node_engine_service.scan_catalog, True)


@router.get("/api/runtime-nodes/categories")
async def runtime_node_categories(
    scope: str = Query(default="utility", pattern="^(utility|all)$"),
):
    return node_engine_service.catalog_categories(scope)


@router.get("/api/runtime-nodes")
async def runtime_nodes(
    query: str = Query(default="", max_length=200),
    category: str = Query(default="", max_length=300),
    compatibility: str = Query(default="", pattern="^(|supported|limited|blocked)$"),
    scope: str = Query(default="utility", pattern="^(utility|all)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    return node_engine_service.search_catalog(query, category, compatibility, page, page_size, scope)


@router.get("/api/runtime-nodes/definition")
async def runtime_node_definition(class_type: str = Query(min_length=1, max_length=300)):
    return node_engine_service.get_definition(class_type)


@router.get("/api/node-engine/models")
async def node_engine_models(
    query: str = Query(default="", max_length=200),
    category: str = Query(default="", max_length=80),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    return await asyncio.to_thread(node_engine_asset_service.list_models, query, category, page, page_size)


@router.get("/api/node-engine/model-paths")
async def node_engine_model_paths():
    return node_engine_asset_service.get_model_paths()


@router.put("/api/node-engine/model-paths")
async def update_node_engine_model_paths(payload: NodeEngineModelPathsRequest):
    result = node_engine_asset_service.set_model_paths(payload)
    process = node_engine_service.process_status(probe=True)
    if process.get("ready"):
        await node_engine_service.restart_engine(90)
    return {**result, "restart_applied": bool(process.get("ready"))}


@router.post("/api/node-engine/models/import", status_code=status.HTTP_202_ACCEPTED)
async def import_node_engine_models(payload: NodeEngineModelImportRequest):
    return node_engine_asset_service.model_import_manager.submit(payload)


@router.get("/api/node-engine/models/imports/{task_id}")
async def get_node_engine_model_import(task_id: str):
    return node_engine_asset_service.model_import_manager.get(task_id)


@router.delete("/api/node-engine/models/imports/{task_id}")
async def cancel_node_engine_model_import(task_id: str):
    return node_engine_asset_service.model_import_manager.cancel(task_id)


@router.get("/api/node-engine/extensions")
async def node_engine_extensions():
    return node_engine_asset_service.list_extensions()


@router.post("/api/node-engine/extensions/install", status_code=status.HTTP_202_ACCEPTED)
async def install_node_engine_extension(payload: NodeEngineExtensionInstallRequest):
    return node_engine_asset_service.extension_task_manager.submit(payload)


@router.get("/api/node-engine/extensions/tasks/{task_id}")
async def get_node_engine_extension_task(task_id: str):
    return node_engine_asset_service.extension_task_manager.get(task_id)


@router.delete("/api/node-engine/extensions/tasks/{task_id}")
async def cancel_node_engine_extension_task(task_id: str):
    return node_engine_asset_service.extension_task_manager.cancel(task_id)


@router.post("/api/node-engine/extensions/{package_id}/enable")
async def enable_node_engine_extension(package_id: str, payload: NodeEngineExtensionActionRequest):
    return await node_engine_asset_service.set_extension_enabled(package_id, True, payload.wait_seconds)


@router.post("/api/node-engine/extensions/{package_id}/disable")
async def disable_node_engine_extension(package_id: str, payload: NodeEngineExtensionActionRequest):
    return await node_engine_asset_service.set_extension_enabled(package_id, False, payload.wait_seconds)


@router.delete("/api/node-engine/extensions/{package_id}")
async def delete_node_engine_extension(
    package_id: str,
    wait_seconds: int = Query(default=90, ge=0, le=180),
):
    return await node_engine_asset_service.remove_extension(package_id, wait_seconds)


@router.post("/api/runtime-graphs/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_runtime_graph_run(payload: RuntimeGraphRunRequest):
    return node_engine_service.run_manager.submit(payload)


@router.get("/api/runtime-graphs/runs/{run_id}")
async def get_runtime_graph_run(run_id: str):
    return node_engine_service.run_manager.get(run_id)


@router.delete("/api/runtime-graphs/runs/{run_id}")
async def cancel_runtime_graph_run(run_id: str):
    return node_engine_service.run_manager.cancel(run_id)
