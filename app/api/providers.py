from fastapi import APIRouter
from app.services import provider_service as service

router = APIRouter()

router.post('/api/providers/logo')(service.upload_provider_logo)
router.get('/api/config')(service.ai_config)
router.get('/api/models')(service.ai_models)
router.get('/api/providers')(service.api_providers)
router.put('/api/providers')(service.save_providers)
router.get('/api/config/token')(service.get_global_token)
router.post('/api/providers/test-connection')(service.test_provider_connection)
router.post('/api/providers/probe-async')(service.probe_async_endpoint)
router.post('/api/providers/fetch-models')(service.fetch_upstream_models_from_payload)
router.get('/api/providers/{provider_id}/fetch-models')(service.fetch_upstream_models)
