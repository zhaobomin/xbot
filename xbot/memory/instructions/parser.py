from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(slots=True)
class ParsedInstruction:
    content: str
    include_paths: list[Path]
    globs: list[str] | None


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def parse_instruction_file(file_path: Path, raw: str) -> ParsedInstruction:
    frontmatter: dict[str, str] = {}
    content = raw
    match = _FRONTMATTER_RE.match(raw)
    if match:
      # preserve indentation? none
        block = match.group(1)
        content = raw[match.end():]
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip()
    globs = _parse_globs(frontmatter.get("paths"))
    include_paths = _extract_include_paths(file_path.parent, content)
    return ParsedInstruction(content=content, include_paths=include_paths, globs=globs)


def _parse_globs(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items or all(item == "**" for item in items):
        return None
    return items


def _extract_include_paths(base_dir: Path, content: str) -> list[Path]:
    includes: list[Path] = []
    in_fence = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if "@include " in line:
            _, raw_path = line.split("@include ", 1)
            path_part = raw_path.strip()
            if path_part:
                includes.append((base_dir / path_part).resolve())
    return includes
