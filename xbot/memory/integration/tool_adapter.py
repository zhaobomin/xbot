from __future__ import annotations

from pathlib import Path
from typing import Any

from xbot.memory.memdir.store import MemoryDirStore


def resolve_memory_store(workspace: Path, shared_resources: dict[str, Any] | None = None) -> MemoryDirStore:
    if shared_resources:
        store = shared_resources.get("memory_store")
        if isinstance(store, MemoryDirStore):
            return store
    return MemoryDirStore(Path(workspace))
