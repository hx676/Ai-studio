"""Bound in-memory and on-disk task history without touching active runs."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, MutableMapping, Set


def prune_terminal_mapping(
    records: MutableMapping[str, dict[str, Any]],
    terminal_states: Set[str],
    *,
    memory_limit: int = 200,
) -> None:
    """Drop only old terminal records; queued/running work is never removed."""

    terminal = [
        (key, record)
        for key, record in records.items()
        if str(record.get("status") or "") in terminal_states
    ]
    terminal.sort(
        key=lambda item: float(item[1].get("completed_at") or item[1].get("updated_at") or item[1].get("created_at") or 0),
        reverse=True,
    )
    for key, _ in terminal[memory_limit:]:
        records.pop(key, None)


def retain_recent_records(
    records: list[dict[str, Any]],
    *,
    limit: int = 1000,
    retention_days: int = 30,
) -> list[dict[str, Any]]:
    """Keep recent timestamped history while preserving undated legacy rows."""

    cutoff_seconds = time.time() - retention_days * 86400
    kept: list[dict[str, Any]] = []
    for record in records:
        stamp = float(record.get("timestamp") or record.get("updated_at") or record.get("created_at") or 0)
        if stamp > 10_000_000_000:
            stamp /= 1000
        if stamp and stamp < cutoff_seconds:
            continue
        kept.append(record)
        if len(kept) >= limit:
            break
    return kept


def prune_run_history(
    run_dir: Path,
    records: MutableMapping[str, dict[str, Any]],
    terminal_states: Set[str],
    *,
    memory_limit: int = 200,
    disk_limit: int = 1000,
    retention_days: int = 30,
) -> None:
    terminal = [
        record for record in records.values()
        if str(record.get("status") or "") in terminal_states and record.get("run_id")
    ]
    terminal.sort(
        key=lambda record: int(record.get("completed_at") or record.get("created_at") or 0),
        reverse=True,
    )
    for record in terminal[memory_limit:]:
        records.pop(str(record.get("run_id")), None)

    cutoff = int((time.time() - retention_days * 86400) * 1000)
    disk_records: list[tuple[Path, int]] = []
    for path in run_dir.glob("*.json"):
        run_id = path.stem
        record = records.get(run_id)
        if record and record.get("status") not in terminal_states:
            continue
        stamp = int((record or {}).get("completed_at") or (record or {}).get("created_at") or 0)
        if not stamp:
            try:
                stamp = int(path.stat().st_mtime * 1000)
            except OSError:
                stamp = 0
        disk_records.append((path, stamp))
    disk_records.sort(key=lambda item: item[1], reverse=True)
    for index, (path, stamp) in enumerate(disk_records):
        if index >= disk_limit or stamp < cutoff:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
