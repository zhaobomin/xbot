from __future__ import annotations

from pathlib import Path


def get_memory_dir(workspace: Path) -> Path:
    return Path(workspace) / "memory"
