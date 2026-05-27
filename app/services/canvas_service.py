"""Canvas API service facade.

Route handlers still delegate to the legacy implementation in this first
staged split, while persistence helpers are exposed through canvas_repository.
"""

from app.legacy import (
    add_asset_library_item,
    build_online_image_result,
    canvas_llm,
    canvas_video,
    canvases,
    chat,
    chat_stream,
    check_canvas_assets,
    create_asset_library_category,
    create_canvas,
    create_canvas_image_task,
    create_conversation,
    conversations,
    delete_asset_library_category,
    delete_asset_library_item,
    delete_canvas,
    delete_conversation,
    download_canvas_assets,
    generate_ai_image,
    get_asset_library,
    get_canvas,
    get_canvas_image_task,
    get_canvas_meta,
    get_conversation,
    purge_canvas,
    rename_asset_library_category,
    rename_asset_library_item,
    restore_canvas,
    run_canvas_image_task,
    trashed_canvases,
    update_canvas,
)
from app.services.storage_service import upload_canvas_media
from app.services.canvas_repository import (
    default_asset_library,
    list_canvases,
    list_deleted_canvases,
    load_asset_library,
    load_canvas,
    load_conversation,
    new_canvas,
    save_asset_library,
    save_canvas,
    save_conversation,
)
