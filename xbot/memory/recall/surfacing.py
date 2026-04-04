from __future__ import annotations

from pathlib import Path

from xbot.memory.memdir.store import MemoryDirStore
from xbot.memory.models import MemoryHeader


def surface_memory_documents(
    store: MemoryDirStore,
    headers: list[MemoryHeader],
    *,
    surfaced_paths: set[Path],
    total_byte_limit: int,
    item_byte_limit: int,
) -> str:
    parts: list[str] = []
    used_bytes = 0
    for header in headers:
        if header.file_path in surfaced_paths:
            continue
        doc = store.read_memory(header.file_path)
        freshness = f"updated {doc.updated_at}"
        body = doc.body
        truncated = False
        body_bytes = body.encode("utf-8")
        if len(body_bytes) > item_byte_limit:
            body = body_bytes[:item_byte_limit].decode("utf-8", errors="ignore").rstrip()
            truncated = True

        snippet = (
            f"## {doc.name} ({header.file_path.name}, {freshness})\n\n"
            f"{doc.description}\n\n"
            f"{body}"
        )
        if truncated:
            snippet += f"\n\n[truncated] Use Read on {header.file_path} for the full topic."
        snippet_bytes = len(snippet.encode("utf-8"))
        if used_bytes + snippet_bytes > total_byte_limit:
            break
        parts.append(snippet)
        used_bytes += snippet_bytes
        surfaced_paths.add(header.file_path)
    return "\n\n".join(parts)
