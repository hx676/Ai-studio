"""Crash-safe JSON persistence with a process-wide lock per data file."""

from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def path_lock(path: str | os.PathLike[str]) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def atomic_write_json(path: str | os.PathLike[str], value: Any, *, indent: int = 2) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    with path_lock(target):
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=indent)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def atomic_write_text(path: str | os.PathLike[str], value: str, *, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    with path_lock(target):
        try:
            with temp.open("w", encoding=encoding, newline="\n") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def read_json_resilient(path: str | os.PathLike[str], default: Any) -> Any:
    target = Path(path)
    with path_lock(target):
        if not target.exists():
            return copy.deepcopy(default)
        try:
            with target.open("r", encoding="utf-8-sig") as handle:
                return json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = target.with_name(f"{target.name}.corrupt-{stamp}")
            try:
                shutil.copy2(target, backup)
            except OSError:
                pass
            return copy.deepcopy(default)
