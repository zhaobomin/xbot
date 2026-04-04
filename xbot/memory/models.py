from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MemoryType = Literal["user", "feedback", "project", "reference"]

INDEX_LINE_LIMIT = 200
INDEX_BYTE_LIMIT = 25_000
MAX_MEMORY_FILES = 200
MAX_RELEVANT_MEMORIES = 5


@dataclass(slots=True)
class InstructionFile:
    path: Path
    content: str
    kind: Literal["project", "local", "rule"]
    globs: list[str] | None = None
    parent: Path | None = None


@dataclass(slots=True)
class MemoryHeader:
    filename: str
    file_path: Path
    mtime_ms: float
    name: str | None
    description: str | None
    memory_type: MemoryType | None


@dataclass(slots=True)
class MemoryDocument:
    path: Path
    memory_type: MemoryType
    name: str
    description: str
    updated_at: str
    body: str
