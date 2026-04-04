from __future__ import annotations

from pathlib import Path

from xbot.memory.memdir.frontmatter import parse_frontmatter
from xbot.memory.models import MAX_MEMORY_FILES, MemoryHeader


def scan_memory_files(memory_dir: Path, limit: int | None = MAX_MEMORY_FILES) -> list[MemoryHeader]:
    if not memory_dir.exists():
        return []
    resolved_root = memory_dir.resolve()
    headers: list[MemoryHeader] = []
    for path in sorted(memory_dir.rglob("*.md")):
        if path.name == "MEMORY.md":
            continue
        if path.is_symlink():
            continue  # skip symlinks to prevent symlink attacks
        try:
            path.resolve().relative_to(resolved_root)  # directory escape check
        except ValueError:
            continue
        try:
            content = path.read_text(encoding="utf-8")
            mtime_ms = path.stat().st_mtime * 1000
        except OSError:
            continue
        document, _ = parse_frontmatter(content)
        headers.append(
            MemoryHeader(
                filename=path.name,
                file_path=path,
                mtime_ms=mtime_ms,
                name=document.get("name"),
                description=document.get("description"),
                memory_type=document.get("type"),  # type: ignore[arg-type]
            )
        )
    headers.sort(key=lambda item: item.mtime_ms, reverse=True)
    return headers[:limit] if limit is not None else headers
