from fastapi import APIRouter
from app.services import system_service as service

router = APIRouter()

router.get('/api/app-info')(service.app_info)
router.post('/api/update-from-github')(service.update_from_github)
router.get('/api/update-backups')(service.get_update_backups)
router.post('/api/update-rollback')(service.rollback_update)
router.get('/')(service.index)
router.get('/api/view')(service.view_image)
router.get('/api/download-output')(service.download_output)
router.post('/api/upload')(service.upload_image)
router.post('/api/ai/upload')(service.upload_ai_reference)
router.get('/api/history')(service.get_history_api)
router.get('/api/queue_status')(service.get_queue_status)
router.post('/api/history/delete')(service.delete_history)
router.post('/api/output/open')(service.open_output_location)
router.post('/api/output/open-dir')(service.open_output_dir)
