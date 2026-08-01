"""Audit SynCanvas source or a staged Windows release before packaging."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


VERSION_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}\.\d+$")

SOURCE_REQUIRED = (
    "LICENSE",
    "VERSION",
    "main.py",
    "requirements.txt",
    "requirements.lock",
    "package.json",
    "package-lock.json",
    "components-manifest.json",
    "node-engine-manifest.json",
    "launcher/SynCanvasLauncher.csproj",
    "tools/build_modular_release.ps1",
    "tools/release_smoke_test.py",
    "custom_nodes/syncanvas_agent_skill/node.json",
    "custom_nodes/syncanvas_image_compare/node.json",
    "custom_nodes/syncanvas_output_folder/node.json",
    "custom_nodes/syncanvas_runtime_node/node.json",
    "custom_nodes/syncanvas_templates/node.json",
)

STAGE_REQUIRED = (
    "LICENSE",
    "VERSION",
    "main.py",
    "requirements.lock",
    "components-manifest.json",
    "node-engine-manifest.json",
    "SynCanvasLauncher.exe",
    "一键启动 SynCanvas.bat",
    "python/python.exe",
    "tools/runtime_preflight.py",
    "tools/release_preflight.py",
    "tools/release_smoke_test.py",
    "static/canvas.html",
    "static/smart-canvas.html",
    "static/node-engine.html",
    "static/vendor/css/tailwind.css",
    "custom_nodes/syncanvas_agent_skill/node.json",
    "custom_nodes/syncanvas_image_compare/node.json",
    "custom_nodes/syncanvas_output_folder/node.json",
    "custom_nodes/syncanvas_runtime_node/node.json",
    "custom_nodes/syncanvas_templates/node.json",
)

FORBIDDEN_STAGE_PATHS = (
    ".git",
    "API/.env",
    "_self_restart.log",
    "data/service-logs",
    "launcher",
    "logs",
    "node_modules",
    "tests",
    "history.json",
    "PROJECT_SELF_CHECK.md",
    "get-pip.py",
    "安装依赖.bat",
    "运行说明.txt",
)

EMPTY_STAGE_DIRS = (
    "API",
    "assets/input",
    "assets/output",
    "components",
    "data",
    "output",
    "packages/components",
    "workflows",
)


def _read_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"无法读取 JSON：{path.name}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"JSON 根对象必须是对象：{path.name}")
        return {}
    return value


def _missing(root: Path, required: tuple[str, ...]) -> list[str]:
    return [item for item in required if not (root / item).exists()]


def _requirements(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            raise ValueError(f"依赖没有固定版本：{line}")
        name, version = line.split("==", 1)
        values[name.strip().casefold()] = version.strip()
    return values


def _audit_manifests(root: Path, errors: list[str], warnings: list[str]) -> None:
    digital = _read_json(root / "components-manifest.json", errors)
    node = _read_json(root / "node-engine-manifest.json", errors)
    digital_component = digital.get("component") if isinstance(digital, dict) else None
    node_component = node.get("component") if isinstance(node, dict) else None
    if not isinstance(digital_component, dict) or digital_component.get("id") != "digital-human":
        errors.append("components-manifest.json 缺少 digital-human 组件定义")
    else:
        for artifact in digital_component.get("artifacts") or []:
            urls = [str(item).strip() for item in artifact.get("urls") or [] if str(item).strip()]
            manual = artifact.get("manual_download") if isinstance(artifact.get("manual_download"), dict) else {}
            manual_url = str(manual.get("share_url") or "").strip()
            manual_filename = str(manual.get("filename") or "").strip()
            expected_filename = str(artifact.get("filename") or "").strip()
            has_manual_source = bool(
                manual_url.startswith("https://")
                and manual_filename
                and manual_filename == expected_filename
            )
            checksum = str(artifact.get("sha256") or "").strip()
            if manual_url and not has_manual_source:
                errors.append(f"数字人组件 {artifact.get('id')} 的手动下载信息无效")
            if (urls or has_manual_source) and not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
                errors.append(f"数字人组件 {artifact.get('id')} 已配置下载地址但缺少有效 SHA-256")
            if not urls and not has_manual_source:
                warnings.append(f"数字人组件 {artifact.get('id')} 尚未配置发布下载地址")
    if not isinstance(node_component, dict) or node_component.get("id") != "node-engine":
        errors.append("node-engine-manifest.json 缺少 node-engine 组件定义")
        return
    if node_component.get("license") != "GPL-3.0":
        errors.append("节点引擎必须声明 GPL-3.0")
    for field in ("source_url", "source_version", "source_offer_url"):
        value = str(node_component.get(field) or "").strip()
        if not value or (field.endswith("url") and not value.startswith("https://")):
            errors.append(f"节点引擎缺少有效 {field}")
    artifact = node_component.get("artifact") or {}
    urls = [str(item).strip() for item in artifact.get("urls") or [] if str(item).strip()]
    checksum = str(artifact.get("sha256") or "").strip()
    if urls and not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        errors.append("节点引擎已配置下载地址但缺少有效 SHA-256")
    if not urls:
        warnings.append("节点引擎发布包尚未配置下载地址；当前只能使用本地导入")


def audit_source(root: Path) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    missing = _missing(root, SOURCE_REQUIRED)
    if missing:
        errors.append("源码缺少发布文件：" + ", ".join(missing))
        return {"mode": "source", "root": str(root), "errors": errors, "warnings": warnings}

    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not VERSION_RE.fullmatch(version):
        errors.append(f"VERSION 格式无效：{version!r}")
    if not (root / "LICENSE").read_text(encoding="utf-8").strip():
        errors.append("LICENSE 为空")

    try:
        direct = _requirements(root / "requirements.txt")
        locked = _requirements(root / "requirements.lock")
        for name, expected in direct.items():
            if locked.get(name) != expected:
                errors.append(f"requirements.lock 与 requirements.txt 不一致：{name}")
    except ValueError as exc:
        errors.append(str(exc))

    _audit_manifests(root, errors, warnings)
    package_cache = root / "packages"
    if package_cache.is_dir() and any(path.is_file() for path in package_cache.rglob("*")):
        warnings.append("检测到本地 packages 缓存；发布脚本会忽略通用 wheel，只保留空的 packages/components 入口")
    return {
        "mode": "source",
        "root": str(root),
        "version": version,
        "errors": errors,
        "warnings": warnings,
    }


def audit_stage(root: Path, expected_version: str = "") -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    missing = _missing(root, STAGE_REQUIRED)
    if missing:
        errors.append("发布暂存目录缺少文件：" + ", ".join(missing))

    version_path = root / "VERSION"
    version = version_path.read_text(encoding="utf-8").strip() if version_path.is_file() else ""
    if expected_version and version != expected_version:
        errors.append(f"暂存版本 {version!r} 与预期版本 {expected_version!r} 不一致")
    if version and not VERSION_RE.fullmatch(version):
        errors.append(f"VERSION 格式无效：{version!r}")

    for relative in FORBIDDEN_STAGE_PATHS:
        if (root / relative).exists():
            errors.append(f"发布包包含禁止路径：{relative}")
    for relative in EMPTY_STAGE_DIRS:
        path = root / relative
        if not path.is_dir():
            errors.append(f"发布包缺少空数据目录：{relative}")
        elif any(item.is_file() or item.is_symlink() for item in path.rglob("*")):
            errors.append(f"发布包混入用户或运行时数据：{relative}")
    for path in root.rglob("*"):
        if path.is_symlink():
            errors.append(f"发布包包含符号链接：{path.relative_to(root).as_posix()}")
        relative = path.relative_to(root).as_posix()
        if not relative.startswith("python/") and (
            path.name == "__pycache__" or path.suffix.casefold() in {".pyc", ".pyo"}
        ):
            errors.append(f"发布包包含源码缓存：{relative}")

    if (root / "components-manifest.json").is_file() and (root / "node-engine-manifest.json").is_file():
        _audit_manifests(root, errors, warnings)
    return {
        "mode": "stage",
        "root": str(root),
        "version": version,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="SynCanvas Windows 发布预检")
    parser.add_argument("--root", default=".")
    parser.add_argument("--stage", action="store_true", help="把 root 作为已整理的发布暂存目录检查")
    parser.add_argument("--version", default="", help="暂存目录应匹配的版本")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = audit_stage(Path(args.root), args.version) if args.stage else audit_source(Path(args.root))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"SynCanvas 发布预检：{result['mode']} {result['root']}")
        for warning in result["warnings"]:
            print(f"[WARN] {warning}")
        for error in result["errors"]:
            print(f"[FAIL] {error}")
        if not result["errors"]:
            print("[PASS] 发布预检通过")
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
