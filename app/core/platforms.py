from __future__ import annotations

import os
import platform
import sys
from typing import Any, Iterable


def current_platform_tag() -> str:
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        arch = "arm64"
    elif machine in {"x86_64", "amd64"}:
        arch = "x64"
    else:
        arch = machine or "unknown"
    if os.name == "nt":
        return f"win-{arch}"
    if sys.platform == "darwin":
        return f"macos-{arch}"
    if sys.platform.startswith("linux"):
        return f"linux-{arch}"
    return f"{sys.platform}-{arch}"


def normalize_platforms(value: Any) -> list[str]:
    if isinstance(value, str):
        values: Iterable[Any] = [value]
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = []
    return list(dict.fromkeys(str(item).strip().lower() for item in values if str(item).strip()))


def platform_supported(value: Any, tag: str | None = None) -> bool:
    supported = normalize_platforms(value)
    if not supported:
        return True
    current = str(tag or current_platform_tag()).strip().lower()
    family = current.split("-", 1)[0]
    return "all" in supported or current in supported or family in supported
