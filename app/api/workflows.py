from fastapi import APIRouter
from app.services import workflow_service as service

router = APIRouter()

router.get('/api/comfyui/instances')(service.get_comfyui_instances)
router.put('/api/comfyui/instances')(service.save_comfyui_instances)
router.get('/api/comfyui/status')(service.comfyui_status)
router.get('/api/workflows')(service.list_workflows)
router.get('/api/workflows/{name:path}')(service.get_workflow)
router.post('/api/workflows')(service.upload_workflow)
router.put('/api/workflows/{name:path}/config')(service.save_workflow_config)
router.delete('/api/workflows/{name:path}')(service.delete_workflow)
router.post('/api/workflows/{name:path}/run')(service.run_workflow)
