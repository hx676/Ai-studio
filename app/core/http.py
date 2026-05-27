
# HTTP utility facade for the staged backend split.
from app.legacy import (
    QuietAccessLogFilter,
    content_type_for_path,
    current_app_version,
    download_output,
    friendly_validation_error,
    read_upload_limited,
    request_validation_exception_handler,
    save_upload_limited,
    static_html_response,
    sync_static_html_versions,
    upload_ai_reference,
    upload_image,
    versioned_static_html,
    view_image,
)
