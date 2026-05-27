from fastapi import APIRouter
from app.services import image_service as service

router = APIRouter()

router.post('/api/online-image')(service.online_image)
router.post('/api/zimage-api-image')(service.zimage_api_image)
router.post('/api/angle/poll_status')(service.poll_angle_cloud)
router.post('/api/angle/generate')(service.generate_angle_cloud)
router.post('/generate')(service.generate_cloud)
router.post('/api/ms/generate')(service.ms_generate)
router.post('/api/generate')(service.generate)
