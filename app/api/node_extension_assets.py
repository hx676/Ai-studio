from fastapi import APIRouter

from app.services import node_extension_asset_service as service


router = APIRouter()

router.post("/api/node-extension-assets")(service.upload_node_extension_asset)
