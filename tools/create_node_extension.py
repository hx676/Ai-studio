"""Create or validate SynCanvas custom node packages."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CUSTOM_NODES = ROOT / "custom_nodes"
TEMPLATE = CUSTOM_NODES / "_template"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")


def validate() -> int:
    errors = []
    package_ids = set()
    for directory in sorted(CUSTOM_NODES.iterdir()):
        if not directory.is_dir() or directory.name.startswith((".", "_")):
            continue
        path = directory / "node.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            package_id = str(value.get("id") or "")
            if not ID_RE.fullmatch(package_id):
                raise ValueError(f"invalid extension id: {package_id}")
            if package_id in package_ids:
                raise ValueError(f"duplicate extension id: {package_id}")
            package_ids.add(package_id)
            nodes = value.get("nodes")
            if not isinstance(nodes, list) or not nodes:
                raise ValueError("nodes must be a non-empty array")
            node_ids = [str(node.get("id") or "") for node in nodes if isinstance(node, dict)]
            if len(node_ids) != len(nodes) or len(set(node_ids)) != len(node_ids):
                raise ValueError("node ids must be present and unique")
            print(f"OK  {package_id} ({len(nodes)} nodes)")
        except Exception as exc:
            errors.append(f"{directory.name}: {exc}")
    for error in errors:
        print(f"ERR {error}", file=sys.stderr)
    return 1 if errors else 0


def create(package_id: str, name: str) -> int:
    if not ID_RE.fullmatch(package_id):
        raise SystemExit("Extension id must use lowercase letters, digits, '.', '_' or '-'.")
    directory_name = package_id.replace(".", "_").replace("-", "_")
    target = CUSTOM_NODES / directory_name
    if target.exists():
        raise SystemExit(f"Target already exists: {target}")
    shutil.copytree(TEMPLATE, target)
    manifest_path = target / "node.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["id"] = package_id
    manifest["name"] = name or package_id
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_id", nargs="?")
    parser.add_argument("--name", default="")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    if args.validate:
        return validate()
    if not args.package_id:
        parser.error("package_id is required unless --validate is used")
    return create(args.package_id, args.name)


if __name__ == "__main__":
    raise SystemExit(main())
