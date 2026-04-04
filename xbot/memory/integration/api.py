from __future__ import annotations

from pathlib import Path

from xbot.memory.memdir.store import MemoryDirStore


def read_workspace_memory_snapshot(workspace: Path) -> dict[str, object]:
    store = MemoryDirStore(Path(workspace))
    return {
        "memory_index": store.load_index_for_prompt(),
        "topics": [
            {
                "path": header.file_path.relative_to(store.memory_dir).as_posix(),
                "name": header.name or header.filename,
                "description": header.description or "",
                "type": header.memory_type or "unknown",
            }
            for header in store.scan_headers()
        ],
    }
