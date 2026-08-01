from __future__ import annotations

import argparse
import importlib.util
import importlib.metadata
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
REQUIREMENTS_FILE = BASE_DIR / "requirements.txt"
LOCK_FILE = BASE_DIR / "requirements.lock"
REQUIRED_MODULES = (
    "fastapi",
    "uvicorn",
    "requests",
    "pydantic",
    "multipart",
    "httpx",
    "PIL",
    "qrcode",
    "gradio_client",
)


def missing_modules() -> list[str]:
    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def locked_versions() -> dict[str, str]:
    path = LOCK_FILE if LOCK_FILE.is_file() else REQUIREMENTS_FILE
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        line = raw.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        result[name.strip()] = version.strip()
    return result


def version_mismatches() -> list[str]:
    mismatches = []
    for name, expected in locked_versions().items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            mismatches.append(f"{name}: missing (expected {expected})")
            continue
        if actual != expected:
            mismatches.append(f"{name}: {actual} (expected {expected})")
    return mismatches


def repair_runtime() -> int:
    missing = missing_modules()
    mismatches = version_mismatches()
    if not missing and not mismatches:
        print("Main-app Python dependencies are ready.")
        return 0
    if not REQUIREMENTS_FILE.is_file():
        print(f"Requirements file not found: {REQUIREMENTS_FILE}", file=sys.stderr)
        return 2
    if missing:
        print("Missing modules: " + ", ".join(missing))
    if mismatches:
        print("Version mismatches: " + "; ".join(mismatches))
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-warn-script-location",
        "-r",
        str(LOCK_FILE if LOCK_FILE.is_file() else REQUIREMENTS_FILE),
    ]
    completed = subprocess.run(command, cwd=BASE_DIR, check=False)
    if completed.returncode:
        return completed.returncode
    remaining = missing_modules()
    mismatches = version_mismatches()
    if remaining or mismatches:
        print("Runtime remains incompatible: " + ", ".join([*remaining, *mismatches]), file=sys.stderr)
        return 3
    print("Main-app Python dependencies repaired.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or repair the SynCanvas main-app Python runtime.")
    parser.add_argument("--check", action="store_true", help="Check required imports without changing the environment.")
    parser.add_argument("--repair", action="store_true", help="Install missing dependencies from requirements.txt.")
    args = parser.parse_args()
    if args.repair:
        return repair_runtime()
    missing = missing_modules()
    mismatches = version_mismatches()
    if missing or mismatches:
        if missing:
            print("Missing modules: " + ", ".join(missing), file=sys.stderr)
        if mismatches:
            print("Version mismatches: " + "; ".join(mismatches), file=sys.stderr)
        return 1
    print("Main-app Python dependencies are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
