"""Log-reading facade for the service supervisor."""

from .cli import (
    cursor_key,
    load_cursor_b64,
    read_console_logs,
    read_recent_log,
)
