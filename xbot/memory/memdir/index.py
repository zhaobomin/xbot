from __future__ import annotations

from pathlib import Path

from xbot.memory.models import INDEX_BYTE_LIMIT, INDEX_LINE_LIMIT, MemoryHeader


def render_index(headers: list[MemoryHeader], memory_dir: Path | None = None) -> str:
    lines: list[str] = []
    for header in headers:
        if memory_dir is not None:
            rel = header.file_path.relative_to(memory_dir).as_posix()
        else:
            # Fallback for callers that don't pass memory_dir
            rel = header.file_path.relative_to(header.file_path.parents[1]).as_posix()
        name = header.name or header.file_path.stem.replace("-", " ").title()
        line = f"- [{name}]({rel})"
        if header.description:
            line += f" — {header.description}"
        lines.append(line)
    raw = "\n".join(lines)
    if len(lines) <= INDEX_LINE_LIMIT and len(raw.encode("utf-8")) <= INDEX_BYTE_LIMIT:
        return raw

    kept = "\n".join(lines[:INDEX_LINE_LIMIT])
    encoded = kept.encode("utf-8")
    if len(encoded) > INDEX_BYTE_LIMIT:
        kept = encoded[:INDEX_BYTE_LIMIT].decode("utf-8", errors="ignore")
    return (
        kept.rstrip()
        + "\n\n> WARNING: MEMORY.md is oversized. Only part of it was loaded. "
        + "Keep index entries brief and move detail into topic files."
    )
