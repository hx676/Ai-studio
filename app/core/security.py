"""Local-only request boundary and secret redaction helpers."""

from __future__ import annotations

import ipaddress
import builtins
import logging
import os
import re
from urllib.parse import urlsplit

from fastapi import Request, WebSocket


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_TOKEN_ASSIGNMENT_RE = re.compile(
    r"(?i)(authorization|api[-_ ]?key|[a-z0-9_.-]*token|secret|password)"
    r"(\s*[:=]\s*)(?:bearer\s+)?([^\s,;\]\}\"']+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_CREDENTIAL_RE = re.compile(r"(?i)([?&][a-z0-9_.-]*(?:key|token|secret|password)=)[^&#\s]+")
_LOG_FACTORY_INSTALLED = False
_ORIGINAL_PRINT = builtins.print


def _hostname(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return (urlsplit(value if "://" in value else f"//{value}").hostname or "").lower()
    except ValueError:
        return ""


def is_loopback_host(value: str) -> bool:
    host = _hostname(value)
    if host in {"localhost", "testserver", "test"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def configured_origins() -> list[str]:
    """Return explicitly configured loopback origins only."""

    raw = os.getenv("SYNCANVAS_ALLOWED_ORIGINS", os.getenv("ALLOWED_ORIGINS", ""))
    origins: list[str] = []
    for item in raw.split(","):
        origin = item.strip().rstrip("/")
        if not origin:
            continue
        if not is_loopback_host(origin):
            raise RuntimeError(f"SynCanvas 暂不支持非本机浏览器来源：{origin}")
        origins.append(origin)
    return origins


def origin_matches_host(origin: str, host: str) -> bool:
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not is_loopback_host(parsed.netloc):
        return False
    return parsed.netloc.lower() == str(host or "").strip().lower()


def browser_write_allowed(request: Request) -> bool:
    if request.method.upper() not in UNSAFE_METHODS:
        return True
    origin = request.headers.get("origin", "").strip()
    if not origin:
        # The local launcher and native plugins do not send Origin.
        return True
    if origin_matches_host(origin, request.headers.get("host", "")):
        return True
    return origin.rstrip("/") in configured_origins()


def request_host_allowed(request: Request) -> bool:
    return is_loopback_host(request.headers.get("host", ""))


def websocket_origin_allowed(websocket: WebSocket) -> bool:
    if not is_loopback_host(websocket.headers.get("host", "")):
        return False
    origin = websocket.headers.get("origin", "").strip()
    if not origin:
        return True
    if origin_matches_host(origin, websocket.headers.get("host", "")):
        return True
    return origin.rstrip("/") in configured_origins()


def redact_sensitive_text(value: object) -> str:
    text = str(value or "")
    text = _BEARER_RE.sub("Bearer ***REDACTED***", text)
    text = _TOKEN_ASSIGNMENT_RE.sub(r"\1\2***REDACTED***", text)
    return _URL_CREDENTIAL_RE.sub(r"\1***REDACTED***", text)


def redact_sensitive_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if _sensitive_key(key) else redact_sensitive_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_value(item) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def _sensitive_key(value: object) -> bool:
    key = str(value or "").casefold().replace("-", "_").replace(" ", "_")
    return (
        key in {"authorization", "api_key", "apikey"}
        or "token" in key
        or "secret" in key
        or "password" in key
    )


def safe_print(*values, **kwargs) -> None:
    """Print through the process-wide secret redactor."""

    _ORIGINAL_PRINT(*(redact_sensitive_value(value) for value in values), **kwargs)


def install_log_redaction() -> None:
    """Redact every stdlib logging record before any handler formats it."""

    global _LOG_FACTORY_INSTALLED
    if _LOG_FACTORY_INSTALLED:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        if isinstance(record.args, dict):
            record.args = redact_sensitive_value(record.args)
        elif isinstance(record.args, tuple):
            record.args = tuple(redact_sensitive_value(value) for value in record.args)
        # Uvicorn's AccessFormatter intentionally consumes the five structured
        # args (client, method, path, HTTP version, status). Flattening those
        # args makes every request emit a logging traceback and can flood the
        # service log. The individual string args were already redacted above.
        if record.name == "uvicorn.access" and isinstance(record.args, tuple):
            record.msg = redact_sensitive_text(record.msg)
            return record
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = f"{record.msg} {record.args}"
        record.msg = redact_sensitive_text(rendered)
        record.args = ()
        return record

    logging.setLogRecordFactory(factory)
    builtins.print = safe_print
    _LOG_FACTORY_INSTALLED = True
