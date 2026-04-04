from __future__ import annotations

import re

from xbot.memory.models import MAX_RELEVANT_MEMORIES, MemoryHeader


def select_relevant_memories(query: str, headers: list[MemoryHeader]) -> list[MemoryHeader]:
    if not query or not re.search(r"\s", query.strip()):
        return []
    terms = {token.lower() for token in re.findall(r"[a-zA-Z0-9_-]+", query) if len(token) > 2}
    scored: list[tuple[int, MemoryHeader]] = []
    for header in headers:
        haystack = f"{header.filename} {header.description or ''}".lower()
        score = sum(1 for term in terms if term in haystack)
        if score > 0:
            scored.append((score, header))
    scored.sort(key=lambda item: (item[0], item[1].mtime_ms), reverse=True)
    return [header for _, header in scored[:MAX_RELEVANT_MEMORIES]]
