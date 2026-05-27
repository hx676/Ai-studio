"""Generation API service facade."""

from app.legacy import generate, online_image, zimage_api_image
from app.services.image_generation.modelscope import (
    generate_angle_cloud,
    generate_cloud,
    ms_generate,
    poll_angle_cloud,
)
