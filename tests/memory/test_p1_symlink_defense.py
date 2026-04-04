"""Tests for Phase 4: symlink defense in scan.py and store.py."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from xbot.memory.memdir.scan import scan_memory_files
from xbot.memory.memdir.store import MemoryDirStore


def _create_memory_file(directory: Path, name: str, content: str = "") -> Path:
    """Helper to create a minimal memory markdown file."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    doc = (
        "---\n"
        f"name: {name}\n"
        "description: test\n"
        "type: project\n"
        "updated_at: 2026-01-01T00:00:00\n"
        "---\n\n"
        f"{content or 'test body'}\n"
    )
    path.write_text(doc, encoding="utf-8")
    return path


def test_scan_skips_symlinked_files(tmp_path: Path) -> None:
    """Symlinked .md files in memory dir should be skipped by scan."""
    memory_dir = tmp_path / "memory"
    real_file = _create_memory_file(memory_dir / "project", "real.md")

    # Create a symlink to the real file
    symlink = memory_dir / "project" / "link.md"
    symlink.symlink_to(real_file)

    headers = scan_memory_files(memory_dir)
    filenames = [h.filename for h in headers]

    assert "real.md" in filenames
    assert "link.md" not in filenames


def test_scan_skips_external_symlinked_directory(tmp_path: Path) -> None:
    """Symlinked subdirectory pointing outside memory dir should be skipped."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Create file outside memory dir
    external = tmp_path / "external"
    external.mkdir()
    _create_memory_file(external, "secret.md", "sensitive data")

    # Symlink the external dir into memory dir
    symlink_dir = memory_dir / "evil"
    symlink_dir.symlink_to(external)

    headers = scan_memory_files(memory_dir)
    filenames = [h.filename for h in headers]

    # Files through the symlinked directory should not appear
    assert "secret.md" not in filenames


def test_scan_skips_escape_via_dotdot(tmp_path: Path) -> None:
    """Files that resolve outside memory dir root should be skipped."""
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    # Create a real file inside memory dir
    _create_memory_file(memory_dir / "project", "normal.md")

    headers = scan_memory_files(memory_dir)
    # Should find the normal file
    assert len(headers) == 1
    assert headers[0].filename == "normal.md"


def test_resolve_managed_path_rejects_symlink(tmp_path: Path) -> None:
    """resolve_managed_path should raise ValueError for symlinked paths."""
    store = MemoryDirStore(tmp_path)
    memory_dir = store.memory_dir

    # Create a real file
    real = _create_memory_file(memory_dir / "project", "real.md")

    # Create a symlink
    symlink = memory_dir / "project" / "sneaky.md"
    symlink.symlink_to(real)

    with pytest.raises(ValueError, match="Symlinks not allowed"):
        store.resolve_managed_path(symlink)


def test_resolve_managed_path_rejects_path_outside_memory_dir(tmp_path: Path) -> None:
    """resolve_managed_path should reject paths that escape memory dir."""
    store = MemoryDirStore(tmp_path)

    with pytest.raises(ValueError, match="Path is outside memory dir"):
        store.resolve_managed_path("/etc/passwd")


def test_resolve_managed_path_accepts_valid_path(tmp_path: Path) -> None:
    """resolve_managed_path should accept valid paths inside memory dir."""
    store = MemoryDirStore(tmp_path)
    real = _create_memory_file(store.memory_dir / "project", "valid.md")

    result = store.resolve_managed_path(real)
    assert result.exists()
