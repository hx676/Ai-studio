import asyncio

from fastapi import APIRouter

from app.models.template_assets import TemplateAssetCreateRequest, TemplateAssetUpdateRequest
from app.services import template_asset_service


router = APIRouter(prefix="/api/asset-library/templates", tags=["template-assets"])


@router.post("")
async def create_template(payload: TemplateAssetCreateRequest):
    # Validation, image copying and fsync are disk-bound. Keep them off the
    # FastAPI event loop so a large reference image cannot stall UI events.
    return await asyncio.to_thread(template_asset_service.create_template_asset, payload)


@router.get("/{template_id}")
async def get_template(template_id: str):
    return await asyncio.to_thread(template_asset_service.get_template_asset, template_id)


@router.patch("/{template_id}")
async def update_template(template_id: str, payload: TemplateAssetUpdateRequest):
    return await asyncio.to_thread(template_asset_service.update_template_asset, template_id, payload)


@router.delete("/{template_id}")
async def delete_template(template_id: str):
    return await asyncio.to_thread(template_asset_service.delete_template_asset, template_id)
