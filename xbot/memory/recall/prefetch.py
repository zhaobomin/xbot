from __future__ import annotations

from xbot.memory.memdir.store import MemoryDirStore
from xbot.memory.models import MemoryHeader
from xbot.memory.recall.selector import select_relevant_memories


class RelevantMemoryMatcher:
    """Synchronous keyword-based memory matcher."""

    def __init__(self, store: MemoryDirStore):
        self.store = store
        self._ready: list[MemoryHeader] = []

    def select(self, query: str) -> None:
        self._ready = select_relevant_memories(query, self.store.scan_headers())

    def collect_ready(self) -> list[MemoryHeader]:
        return list(self._ready)


# Backward-compatible alias
RelevantMemoryPrefetch = RelevantMemoryMatcher
