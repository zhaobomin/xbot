"""Tests for Phase 1: atomic write in MemoryDirStore."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from xbot.memory.memdir.store import MemoryDirStore


def test_atomic_write_creates_file_correctly(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    target = tmp_path / "memory" / "test.md"
    store._atomic_write(target, "hello world")

    assert target.exists()
    assert target.read_text(encoding="utf-8") == "hello world"
    # No leftover tmp file
    assert not target.with_suffix(".md.tmp").exists()


def test_atomic_write_overwrites_existing_file(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    target = tmp_path / "memory" / "test.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old content", encoding="utf-8")

    store._atomic_write(target, "new content")

    assert target.read_text(encoding="utf-8") == "new content"


def test_atomic_write_cleans_up_tmp_on_write_error(tmp_path: Path) -> None:
    store = MemoryDirStore(tmp_path)
    target = tmp_path / "memory" / "test.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("original", encoding="utf-8")

    # Make os.replace fail
    with patch("os.replace", side_effect=OSError("disk full")):
        try:
            store._atomic_write(target, "new content")
        except OSError:
            pass

    # Temp file cleaned up
    assert not target.with_suffix(".md.tmp").exists()
    # Original content preserved
    assert target.read_text(encoding="utf-8") == "original"


def test_atomic_write_used_by_create_memory(tmp_path: Path) -> None:
    """Ensure create_memory goes through _atomic_write (no direct write_text)."""
    store = MemoryDirStore(tmp_path)
    calls: list[str] = []
    original = store._atomic_write

    def spy(path: Path, content: str) -> None:
        calls.append(str(path))
        original(path, content)

    with patch.object(store, "_atomic_write", side_effect=spy):
        store.create_memory(
            memory_type="project",
            title="Test",
            description="desc",
            body="body",
        )

    # At least 2 calls: one for the memory file, one for the index
    assert len(calls) >= 2


def test_atomic_write_used_by_update_memory(tmp_path: Path) -> None:
    """Ensure update_memory goes through _atomic_write."""
    store = MemoryDirStore(tmp_path)
    path = store.create_memory(
        memory_type="project",
        title="Update Target",
        description="desc",
        body="original",
    )

    calls: list[str] = []
    original = store._atomic_write

    def spy(p: Path, content: str) -> None:
        calls.append(str(p))
        original(p, content)

    with patch.object(store, "_atomic_write", side_effect=spy):
        store.update_memory(path, body="updated body")

    assert len(calls) >= 2
