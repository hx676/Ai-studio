from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "output"
PACK_NAME = "prompt-pack-20260728"
PACK_DIR = OUTPUT_ROOT / PACK_NAME
ZIP_PATH = OUTPUT_ROOT / "SynCanvas-prompt-pack-20260728.zip"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def markdown_block(value: Any, language: str = "text") -> str:
    text = str(value or "")
    fence = "````" if "```" in text else "```"
    return f"{fence}{language}\n{text}\n{fence}"


def safe_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned or fallback


def category_name(library: dict[str, Any], category_id: str) -> str:
    for category in library.get("categories", []):
        if category.get("id") == category_id:
            return str(category.get("name") or category_id)
    return category_id or "未分类"


def collect_prompt_templates(data: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for library in data.get("libraries", []):
        for item in library.get("items", []):
            result.append(
                {
                    **item,
                    "library_id": library.get("id", ""),
                    "library_name": library.get("name", ""),
                    "category_name": category_name(library, str(item.get("category", ""))),
                }
            )
    return result


def export_prompt_library(source: Path, original_markdown: Path) -> list[dict[str, Any]]:
    destination = PACK_DIR / "01-prompt-library"
    destination.mkdir(parents=True, exist_ok=True)
    data = read_json(source)
    templates = collect_prompt_templates(data)
    shutil.copy2(source, destination / "prompt_libraries.raw.json")
    shutil.copy2(original_markdown, destination / original_markdown.name)

    lines = ["# 提示词模板库", ""]
    for library in data.get("libraries", []):
        lines.extend([f"## {library.get('name', '未命名提示词库')}", ""])
        items = library.get("items", [])
        categories = list(library.get("categories", []))
        known_ids = {str(item.get("id", "")) for item in categories}
        unknown_ids = sorted({str(item.get("category", "")) for item in items} - known_ids)
        categories.extend({"id": item, "name": item or "未分类"} for item in unknown_ids)
        for category in categories:
            category_items = [item for item in items if item.get("category") == category.get("id")]
            if not category_items:
                continue
            lines.extend([f"### {category.get('name', category.get('id', '未分类'))}", ""])
            for item in category_items:
                lines.extend(
                    [
                        f"#### {item.get('name', item.get('id', '未命名'))}",
                        "",
                        f"- ID: `{item.get('id', '')}`",
                        f"- 适用场景: {item.get('scene', '')}",
                        "",
                        "**正向提示词**",
                        "",
                        markdown_block(item.get("positive", "")),
                        "",
                        "**负向提示词**",
                        "",
                        markdown_block(item.get("negative", "")),
                        "",
                        "**参数建议**",
                        "",
                    ]
                )
                params = item.get("params", {})
                if isinstance(params, dict) and params:
                    lines.extend(f"- {key}: {value}" for key, value in params.items())
                else:
                    lines.append("- 无")
                lines.extend(["", "---", ""])
    write_text(destination / "prompt-library.md", "\n".join(lines).rstrip() + "\n")

    csv_path = destination / "prompt-library.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "id",
            "name",
            "library_id",
            "library_name",
            "category",
            "category_name",
            "scene",
            "positive",
            "negative",
            "params_json",
            "created_at",
            "updated_at",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in templates:
            writer.writerow(
                {
                    **{key: item.get(key, "") for key in fieldnames},
                    "params_json": json.dumps(item.get("params", {}), ensure_ascii=False),
                }
            )
    return templates


def export_agents(source: Path, defaults_source: Path) -> list[dict[str, Any]]:
    destination = PACK_DIR / "02-agent-system-prompts"
    destination.mkdir(parents=True, exist_ok=True)
    agents = read_json(source)
    allowed_fields = ("id", "name", "description", "modelKind", "temperature", "systemPrompt")
    sanitized = [{key: agent.get(key) for key in allowed_fields} for agent in agents]

    shutil.copy2(source, destination / "active-agents.raw.json")
    write_json(destination / "active-agents.sanitized.json", sanitized)
    shutil.copy2(defaults_source, destination / "reference-agents.defaults.json")

    index_rows: list[dict[str, Any]] = []
    for agent in sanitized:
        agent_id = str(agent.get("id") or "agent")
        filename = safe_filename(agent_id, "agent") + ".md"
        prompt = str(agent.get("systemPrompt") or "")
        body = "\n".join(
            [
                f"# {agent.get('name') or agent_id}",
                "",
                f"- ID: `{agent_id}`",
                f"- 模型类型: `{agent.get('modelKind', '')}`",
                f"- Temperature: `{agent.get('temperature', '')}`",
                f"- 说明: {agent.get('description', '')}",
                f"- 字符数: `{len(prompt)}`",
                "",
                "## System Prompt",
                "",
                markdown_block(prompt),
                "",
            ]
        )
        write_text(destination / "agents" / filename, body)
        index_rows.append(
            {
                "id": agent_id,
                "name": agent.get("name", ""),
                "description": agent.get("description", ""),
                "modelKind": agent.get("modelKind", ""),
                "temperature": agent.get("temperature", ""),
                "character_count": len(prompt),
                "file": f"agents/{filename}",
                "systemPrompt": prompt,
            }
        )

    with (destination / "agent-index.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(index_rows[0]))
        writer.writeheader()
        writer.writerows(index_rows)
    write_json(destination / "agent-index.json", index_rows)
    return sanitized


def export_skills(source: Path) -> list[dict[str, Any]]:
    sys.path.insert(0, str(ROOT))
    from app.services.skill_runtime import list_skill_metadata

    destination = PACK_DIR / "03-skill-catalog"
    destination.mkdir(parents=True, exist_ok=True)
    skills = list_skill_metadata()
    write_json(destination / "skill-metadata.json", skills)
    shutil.copy2(source, destination / "skill_definitions.source.py")

    catalog_lines = [
        "# Skill 目录",
        "",
        "> Skill 本身主要负责组织输入并调用 Agent。完整运行时提示拼接与兜底提示保存在 `skill_definitions.source.py`。",
        "",
    ]
    for skill in skills:
        skill_id = str(skill.get("id") or "skill")
        filename = safe_filename(skill_id, "skill") + ".md"
        agents = skill.get("agents", [])
        body = "\n".join(
            [
                f"# {skill.get('name') or skill_id}",
                "",
                f"- ID: `{skill_id}`",
                f"- 说明: {skill.get('description', '')}",
                f"- 调用 Agent: {', '.join(f'`{item}`' for item in agents) or '无'}",
                "",
                "## 输入结构",
                "",
                markdown_block(json.dumps(skill.get("inputSchema", {}), ensure_ascii=False, indent=2), "json"),
                "",
                "## 输出结构",
                "",
                markdown_block(json.dumps(skill.get("outputSchema", {}), ensure_ascii=False, indent=2), "json"),
                "",
            ]
        )
        write_text(destination / "skills" / filename, body)
        catalog_lines.extend(
            [
                f"## {skill.get('name') or skill_id}",
                "",
                f"- ID: `{skill_id}`",
                f"- Agent: {', '.join(f'`{item}`' for item in agents) or '无'}",
                f"- 说明: {skill.get('description', '')}",
                f"- 详情: [skills/{filename}](skills/{filename})",
                "",
            ]
        )
    write_text(destination / "skill-catalog.md", "\n".join(catalog_lines).rstrip() + "\n")
    return skills


def build_master_index(
    templates: list[dict[str, Any]], agents: list[dict[str, Any]], skills: list[dict[str, Any]]
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for item in templates:
        entries.append(
            {
                "type": "prompt-template",
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "group": f"{item.get('library_name', '')} / {item.get('category_name', '')}",
                "description": item.get("scene", ""),
                "text": "\n".join([str(item.get("positive", "")), str(item.get("negative", ""))]),
                "source": "01-prompt-library/prompt_libraries.raw.json",
            }
        )
    for agent in agents:
        agent_id = str(agent.get("id") or "agent")
        entries.append(
            {
                "type": "agent-system-prompt",
                "id": agent_id,
                "name": agent.get("name", ""),
                "group": agent.get("modelKind", ""),
                "description": agent.get("description", ""),
                "text": agent.get("systemPrompt", ""),
                "source": f"02-agent-system-prompts/agents/{safe_filename(agent_id, 'agent')}.md",
            }
        )
    for skill in skills:
        skill_id = str(skill.get("id") or "skill")
        entries.append(
            {
                "type": "skill",
                "id": skill_id,
                "name": skill.get("name", ""),
                "group": ", ".join(skill.get("agents", [])),
                "description": skill.get("description", ""),
                "text": "",
                "source": f"03-skill-catalog/skills/{safe_filename(skill_id, 'skill')}.md",
            }
        )
    return {
        "format_version": 1,
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "counts": {
            "prompt_libraries": 1,
            "prompt_templates": len(templates),
            "agent_system_prompts": len(agents),
            "skills": len(skills),
            "indexed_entries": len(entries),
        },
        "entries": entries,
    }


def validate_and_manifest(expected_counts: dict[str, int]) -> dict[str, Any]:
    parsed_json = 0
    scanned_files = 0
    suspicious_values: list[dict[str, str]] = []
    assignment_pattern = re.compile(
        r"(?i)(api[_-]?key|authorization|password|passwd|secret)\s*[:=]\s*[\"']?([^\s\"',}]{8,})"
    )
    token_patterns = [
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}={0,2}\b", re.IGNORECASE),
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    ]

    for path in sorted(PACK_DIR.rglob("*")):
        if not path.is_file():
            continue
        scanned_files += 1
        if path.suffix.lower() == ".json":
            read_json(path)
            parsed_json += 1
        if path.suffix.lower() not in {".json", ".md", ".csv", ".py", ".txt"}:
            continue
        content = path.read_text(encoding="utf-8")
        for match in assignment_pattern.finditer(content):
            value = match.group(2)
            if value in {"********", "REDACTED"} or set(value) == {"*"}:
                continue
            suspicious_values.append({"file": path.relative_to(PACK_DIR).as_posix(), "match": match.group(0)[:120]})
        for pattern in token_patterns:
            if pattern.search(content):
                suspicious_values.append({"file": path.relative_to(PACK_DIR).as_posix(), "match": pattern.pattern})

    if suspicious_values:
        raise RuntimeError(f"Sensitive-looking values found: {suspicious_values}")

    index = read_json(PACK_DIR / "index.json")
    if index.get("counts") != expected_counts:
        raise RuntimeError(f"Count mismatch: {index.get('counts')} != {expected_counts}")

    validation = {
        "status": "passed",
        "json_files_parsed": parsed_json,
        "files_scanned": scanned_files,
        "sensitive_values_found": 0,
        "counts": expected_counts,
    }
    write_json(PACK_DIR / "validation-report.json", validation)

    files = []
    for path in sorted(PACK_DIR.rglob("*")):
        if path.is_file() and path.name != "manifest.sha256":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            files.append(f"{digest}  {path.relative_to(PACK_DIR).as_posix()}")
    write_text(PACK_DIR / "manifest.sha256", "\n".join(files) + "\n")
    return validation


def write_readme(index: dict[str, Any]) -> None:
    counts = index["counts"]
    body = f"""# SynCanvas 提示词归档

导出时间：{index['exported_at']}

## 内容统计

- 提示词库：{counts['prompt_libraries']} 个
- 可直接使用的提示词模板：{counts['prompt_templates']} 条
- Agent 系统提示词：{counts['agent_system_prompts']} 条
- Skill：{counts['skills']} 个
- 总索引条目：{counts['indexed_entries']} 条

## 目录

- `index.json`：完整可检索索引，包含模板和 Agent 提示词正文。
- `01-prompt-library/`：提示词库原始 JSON、分类 Markdown、CSV 和原始 Markdown 备份。
- `02-agent-system-prompts/`：当前 Agent 提示词、逐 Agent Markdown、CSV/JSON 索引和默认值参考快照。
- `03-skill-catalog/`：Skill 元数据、逐 Skill 输入输出结构、Agent 调用关系及运行时提示拼接源码快照。
- `validation-report.json`：数量、JSON 解析和敏感值扫描结果。
- `manifest.sha256`：归档内文件的 SHA-256 校验值。

## 范围说明

本归档保存当前项目中可编辑或实际参与运行的提示词配置。未包含 API Key、Authorization 值、密码、供应商连接配置、运行历史和模型调用日志。`api-doctor` 提示词中出现的 `apiKey` 仅是字段说明，运行时源码中的值为脱敏占位符。

所有文本使用 UTF-8（无 BOM），提示词正文保持源文件内容不变。
"""
    write_text(PACK_DIR / "README.md", body)


def create_zip() -> None:
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(PACK_DIR.rglob("*")):
            if path.is_file():
                archive.write(path, Path(PACK_NAME) / path.relative_to(PACK_DIR))
    with zipfile.ZipFile(ZIP_PATH, "r") as archive:
        bad_file = archive.testzip()
        if bad_file:
            raise RuntimeError(f"ZIP CRC validation failed: {bad_file}")


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    resolved_output = OUTPUT_ROOT.resolve()
    resolved_pack = PACK_DIR.resolve()
    if resolved_pack.parent != resolved_output or resolved_pack.name != PACK_NAME:
        raise RuntimeError(f"Refusing to replace unexpected directory: {resolved_pack}")
    if PACK_DIR.exists():
        shutil.rmtree(PACK_DIR)
    PACK_DIR.mkdir(parents=True)

    templates = export_prompt_library(
        ROOT / "data" / "prompt_libraries.json",
        ROOT / "static" / "system-prompts" / "infinite-canvas-prompt-templates.md",
    )
    agents = export_agents(
        ROOT / "data" / "agents.json",
        ROOT / "app" / "resources" / "agents.defaults.json",
    )
    skills = export_skills(ROOT / "app" / "services" / "skill_definitions.py")
    index = build_master_index(templates, agents, skills)
    write_json(PACK_DIR / "index.json", index)
    write_readme(index)
    validation = validate_and_manifest(index["counts"])
    create_zip()

    print(
        json.dumps(
            {
                "directory": str(PACK_DIR),
                "zip": str(ZIP_PATH),
                "zip_bytes": ZIP_PATH.stat().st_size,
                "counts": index["counts"],
                "validation": validation,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
