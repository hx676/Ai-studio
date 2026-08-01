from fastapi import APIRouter, HTTPException

from app.models.components import ComponentInstallRequest
from app.services import component_service


router = APIRouter()


@router.get("/api/components/digital-human/status")
async def digital_human_component_status():
    return component_service.get_component_status()


@router.post("/api/components/digital-human/install")
async def install_digital_human_component(payload: ComponentInstallRequest):
    try:
        return component_service.start_component_install(
            install_root=payload.install_root or "",
            manifest_url=payload.manifest_url or "",
            force=payload.force,
        )
    except component_service.ComponentBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except component_service.ComponentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/components/digital-human/repair")
async def repair_digital_human_component(payload: ComponentInstallRequest):
    try:
        return component_service.start_component_install(
            install_root=payload.install_root or "",
            manifest_url=payload.manifest_url or "",
            force=True,
        )
    except component_service.ComponentBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except component_service.ComponentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/components/digital-human/cancel")
async def cancel_digital_human_component_install():
    return component_service.cancel_component_install()
