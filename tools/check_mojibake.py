from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


DEFAULT_ROOTS = ["app", "static", "launcher", "tools"]
TEXT_SUFFIXES = {
    ".bat",
    ".cs",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".txt",
    ".xaml",
    ".xml",
}
SKIP_DIRS = {
    ".git",
    "__pycache__",
    "_archive",
    "assets",
    "data",
    "heygem-win-fix",
    "index-tts-2",
    "node_modules",
    "obj",
    "output",
    "packages",
    "python",
    "static/vendor",
}
PATTERNS = [
    "\u5bee\u20ac",
    "\u93c1\u677f",
    "\u6d60\u546d",
    "\u6d93\u5b2d",
    "\u701b\u6a3a",
    "\u9422\u3126",
    "\u935a\ue21a",
    "\u675e\u85c9",
    "\u6fb6\u8fab",
    "\ufffd",
]


def should_skip(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return True
    parts = set(rel.split("/"))
    if parts & SKIP_DIRS:
        return True
    return any(rel.startswith(skip + "/") for skip in SKIP_DIRS if "/" in skip)


def iter_files(root: Path, names: list[str]):
    for name in names:
        base = root / name
        if not base.exists():
            continue
        if base.is_file():
            yield base
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES and not should_skip(path, root):
                yield path


def scan_file(path: Path, root: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [{"file": path.relative_to(root).as_posix(), "line": 0, "pattern": "decode-error", "text": str(exc)}]
    hits = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for pattern in PATTERNS:
            if pattern in line:
                hits.append(
                    {
                        "file": path.relative_to(root).as_posix(),
                        "line": line_no,
                        "pattern": pattern,
                        "text": line.strip()[:240],
                    }
                )
                break
    return hits


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Scan source files for likely UTF-8 mojibake.")
    parser.add_argument("paths", nargs="*", default=DEFAULT_ROOTS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path.cwd()
    hits = []
    for path in iter_files(root, args.paths):
        hits.extend(scan_file(path, root))

    if args.json:
        print(json.dumps({"count": len(hits), "hits": hits}, ensure_ascii=False, indent=2))
    elif hits:
        for hit in hits:
            print(f"{hit['file']}:{hit['line']}: [{hit['pattern']}] {hit['text']}")
    else:
        print("No mojibake patterns found.")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
