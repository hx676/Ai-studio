"""Canvas, conversation, and asset-library persistence helpers.

This module is the first extraction from app.legacy. The implementation still
delegates to the compatibility layer so public behavior stays unchanged while
call sites move to a clearer dependency.
"""

from app.legacy import (
    canvas_path,
    canvas_record,
    cleanup_expired_canvas_trash,
    conversation_path,
    default_asset_library,
    display_title,
    find_asset_category,
    iter_canvas_records,
    list_canvases,
    list_conversations,
    list_deleted_canvases,
    load_asset_library,
    load_canvas,
    load_canvas_any,
    load_conversation,
    new_canvas,
    new_conversation,
    normalize_canvas_kind,
    now_ms,
    safe_user_id,
    sanitize_asset_name,
    save_asset_library,
    save_canvas,
    save_conversation,
    user_dir,
)
