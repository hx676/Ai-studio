from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_ROOT = ROOT / "data" / "test-runs"


def ensure_utf8_stdio() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")


def timestamp_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def run_git(args: List[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.STDOUT,
        ).strip()
    except Exception as exc:
        return f"git unavailable: {exc}"


def create_run_dir(run_id: str = "") -> Path:
    run_id = run_id or timestamp_id()
    run_dir = DEFAULT_TEST_ROOT / run_id
    (run_dir / "errors").mkdir(parents=True, exist_ok=True)
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    return run_dir


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return str(value)


class TestRun:
    def __init__(self, run_dir: Path, suite: str, base_url: str = "") -> None:
        self.run_dir = run_dir
        self.suite = suite
        self.base_url = base_url.rstrip("/")
        self.results_path = run_dir / "results.jsonl"
        self.resource_path = run_dir / "resources.jsonl"
        self.meta_path = run_dir / "meta.json"
        self.started_at = time.time()
        self._write_meta()

    def _write_meta(self) -> None:
        meta = {
            "suite": self.suite,
            "base_url": self.base_url,
            "started_at": self.started_at,
            "started_at_text": datetime.fromtimestamp(self.started_at).isoformat(timespec="seconds"),
            "git_head": run_git(["rev-parse", "HEAD"]),
            "git_status": run_git(["status", "--short", "--branch"]),
            "root": str(ROOT),
        }
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def record(self, name: str, status: str, duration_ms: float = 0, **fields: Any) -> Dict[str, Any]:
        item = {
            "time": time.time(),
            "suite": self.suite,
            "name": name,
            "status": status,
            "duration_ms": round(float(duration_ms or 0), 2),
            **fields,
        }
        with self.results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False, default=json_default) + "\n")
        label = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP", "warn": "WARN"}.get(status, status.upper())
        print(f"[{label}] {name} {item['duration_ms']}ms")
        return item

    def resource(self, phase: str, **fields: Any) -> Dict[str, Any]:
        item = {"time": time.time(), "phase": phase, **resource_snapshot(), **fields}
        with self.resource_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False, default=json_default) + "\n")
        return item

    def copy_manual_checklist(self) -> None:
        src = ROOT / "docs" / "SynCanvas-全功能手动测试清单.md"
        if src.exists():
            shutil.copy2(src, self.run_dir / "manual-checklist.md")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def windows_memory_percent() -> Optional[float]:
    if os.name != "nt":
        return None

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return round(float(status.dwMemoryLoad), 2)
    except Exception:
        return None
    return None


def resource_snapshot() -> Dict[str, Any]:
    usage = shutil.disk_usage(ROOT)
    return {
        "disk_free_gb": round(usage.free / (1024 ** 3), 2),
        "disk_used_percent": round(usage.used * 100 / usage.total, 2),
        "memory_used_percent": windows_memory_percent(),
    }


def guard_allows_continue(snapshot: Dict[str, Any], min_disk_gb: float = 100, max_memory_percent: float = 90) -> bool:
    if float(snapshot.get("disk_free_gb") or 0) < min_disk_gb:
        return False
    memory = snapshot.get("memory_used_percent")
    if memory is not None and float(memory) > max_memory_percent:
        return False
    return True


def write_artifacts_index(run_dir: Path) -> None:
    rows = []
    for path in run_dir.rglob("*"):
        if path.is_file():
            rel = path.relative_to(run_dir).as_posix()
            rows.append({"path": rel, "bytes": path.stat().st_size})
    (run_dir / "artifacts-index.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def write_summary(run_dir: Path, title: str = "SynCanvas Test Report") -> Path:
    results = read_jsonl(run_dir / "results.jsonl")
    resources = read_jsonl(run_dir / "resources.jsonl")
    counts: Dict[str, int] = {}
    for row in results:
        counts[row.get("status", "unknown")] = counts.get(row.get("status", "unknown"), 0) + 1
    failures = [row for row in results if row.get("status") == "fail"]
    warnings = [row for row in results if row.get("status") == "warn"]
    max_latency = max([float(row.get("duration_ms") or 0) for row in results] or [0])
    min_disk = min([float(row.get("disk_free_gb") or 0) for row in resources] or [0])
    max_mem_values = [float(row["memory_used_percent"]) for row in resources if row.get("memory_used_percent") is not None]
    max_mem = max(max_mem_values) if max_mem_values else None

    lines = [
        f"# {title}",
        "",
        f"- Run dir: `{run_dir}`",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Total checks: {len(results)}",
        f"- Counts: `{json.dumps(counts, ensure_ascii=False)}`",
        f"- Max latency: {max_latency:.2f} ms",
        f"- Min disk free: {min_disk:.2f} GB" if min_disk else "- Min disk free: n/a",
        f"- Max memory used: {max_mem:.2f}%" if max_mem is not None else "- Max memory used: n/a",
        "",
        "## Failures",
    ]
    if failures:
        for row in failures[:100]:
            lines.append(f"- `{row.get('name')}`: {row.get('error') or row.get('detail') or row.get('status_code')}")
    else:
        lines.append("- None")
    lines.extend(["", "## Warnings"])
    if warnings:
        for row in warnings[:100]:
            lines.append(f"- `{row.get('name')}`: {row.get('detail') or row.get('error')}")
    else:
        lines.append("- None")
    lines.extend(["", "## Next Steps", "- Review failures above and linked JSONL rows before deleting any generated artifacts."])
    path = run_dir / "summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_artifacts_index(run_dir)
    return path


def main() -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Summarize a SynCanvas test run.")
    parser.add_argument("run_dir", nargs="?", default="")
    args = parser.parse_args()
    run_dir = Path(args.run_dir) if args.run_dir else max(DEFAULT_TEST_ROOT.glob("*"), key=lambda p: p.stat().st_mtime)
    summary = write_summary(run_dir)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
