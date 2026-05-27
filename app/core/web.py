"""Web helpers kept separate from business services."""

from app.legacy import (
    QuietAccessLogFilter,
    current_app_version,
    friendly_validation_error,
    request_validation_exception_handler,
    static_html_response,
    sync_static_html_versions,
    versioned_static_html,
    websocket_endpoint,
)
