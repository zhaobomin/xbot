"""Tests for legacy MEMORY.md migration in MemoryDirStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from xbot.memory.memdir.store import MemoryDirStore


# ---------------------------------------------------------------------------
# _is_legacy_memory_content detection
# ---------------------------------------------------------------------------


class TestIsLegacyMemoryContent:
    """Tests for detecting old-style inline MEMORY.md content."""

    def _store(self, tmp_path: Path) -> MemoryDirStore:
        return MemoryDirStore(tmp_path)

    def test_empty_content_is_not_legacy(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        assert store._is_legacy_memory_content("") is False
        assert store._is_legacy_memory_content("  \n  ") is False

    def test_frontmatter_is_legacy(self, tmp_path: Path) -> None:
        content = "---\nname: My Memory\ntype: project\n---\n\nSome content here."
        store = self._store(tmp_path)
        assert store._is_legacy_memory_content(content) is True

    def test_numbered_list_is_legacy(self, tmp_path: Path) -> None:
        content = "1. 用户信息 - 你的名字，称呼，角色\n2. 偏好设置 - 表情风格，兴趣爱好\n3. 主要项目 - xbot 项目（主项目）"
        store = self._store(tmp_path)
        assert store._is_legacy_memory_content(content) is True

    def test_prose_is_legacy(self, tmp_path: Path) -> None:
        content = "这是我的长期记忆文件，记录了：\n用户信息\n喜欢笑表情\n主要项目"
        store = self._store(tmp_path)
        assert store._is_legacy_memory_content(content) is True

    def test_valid_index_is_not_legacy(self, tmp_path: Path) -> None:
        content = (
            "- [Legacy Memory](project/legacy-memory.md) — Migrated from old MEMORY.md\n"
            "- [User Prefs](user/prefs.md) — User preferences"
        )
        store = self._store(tmp_path)
        assert store._is_legacy_memory_content(content) is False

    def test_index_with_warning_is_not_legacy(self, tmp_path: Path) -> None:
        content = (
            "- [Item 1](project/item1.md) — First item\n"
            "> WARNING: MEMORY.md is oversized."
        )
        store = self._store(tmp_path)
        assert store._is_legacy_memory_content(content) is False

    def test_mixed_content_majority_index(self, tmp_path: Path) -> None:
        """If most lines are index links, it's not legacy."""
        content = (
            "- [A](project/a.md)\n"
            "- [B](project/b.md)\n"
            "- [C](project/c.md)\n"
            "Some stray line"
        )
        store = self._store(tmp_path)
        # 3/4 = 75% are index lines, so non-index is 25% < 50%
        assert store._is_legacy_memory_content(content) is False

    def test_mixed_content_majority_prose(self, tmp_path: Path) -> None:
        """If most lines are prose, it's legacy."""
        content = (
            "- [A](project/a.md)\n"
            "This is content line 1\n"
            "This is content line 2\n"
            "This is content line 3"
        )
        store = self._store(tmp_path)
        # 3/4 = 75% non-index > 50%
        assert store._is_legacy_memory_content(content) is True


# ---------------------------------------------------------------------------
# Full migration flow
# ---------------------------------------------------------------------------


class TestLegacyMemoryMigration:
    """Tests for the full migration from legacy inline MEMORY.md to index."""

    def test_migration_creates_legacy_memory_file(self, tmp_path: Path) -> None:
        """Legacy content should be saved as project/legacy-memory.md."""
        store = MemoryDirStore(tmp_path)
        legacy_content = "1. 用户信息\n2. 偏好设置\n3. 主要项目"
        store.index_path.write_text(legacy_content, encoding="utf-8")

        result = store.load_index_for_prompt()

        # Should have created the backup file
        backup = store.memory_dir / "project" / "legacy-memory.md"
        assert backup.exists()

        # Backup should contain the old content
        backup_text = backup.read_text(encoding="utf-8")
        assert "1. 用户信息" in backup_text
        assert "Legacy Memory" in backup_text  # frontmatter title

        # MEMORY.md should now be a proper index
        assert "- [Legacy Memory]" in result

    def test_migration_strips_frontmatter_from_backup(self, tmp_path: Path) -> None:
        """If legacy MEMORY.md has frontmatter, the body is extracted."""
        store = MemoryDirStore(tmp_path)
        legacy = "---\nname: Old Memory\ntype: project\n---\n\nActual content here."
        store.index_path.write_text(legacy, encoding="utf-8")

        store.load_index_for_prompt()

        backup = store.memory_dir / "project" / "legacy-memory.md"
        backup_text = backup.read_text(encoding="utf-8")
        # Body should contain the actual content, not the old frontmatter
        assert "Actual content here." in backup_text
        # But it should have new frontmatter
        assert "name: Legacy Memory" in backup_text

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        """Calling load_index_for_prompt twice should not duplicate the backup."""
        store = MemoryDirStore(tmp_path)
        legacy = "Some old memory content\nWith multiple lines"
        store.index_path.write_text(legacy, encoding="utf-8")

        store.load_index_for_prompt()
        first_backup = (store.memory_dir / "project" / "legacy-memory.md").read_text(encoding="utf-8")

        # Modify MEMORY.md back to legacy to simulate re-trigger
        # (in practice won't happen, but tests idempotency of backup creation)
        store.index_path.write_text(legacy, encoding="utf-8")
        store.load_index_for_prompt()

        second_backup = (store.memory_dir / "project" / "legacy-memory.md").read_text(encoding="utf-8")
        assert first_backup == second_backup

    def test_no_migration_for_valid_index(self, tmp_path: Path) -> None:
        """A valid index should not trigger migration."""
        store = MemoryDirStore(tmp_path)

        # Create a real memory file first
        store.create_memory("project", "Test Item", "A test", "Test body")

        # load_index_for_prompt should return the index as-is
        result = store.load_index_for_prompt()
        assert "- [Test Item]" in result

        # No backup should be created
        backup = store.memory_dir / "project" / "legacy-memory.md"
        assert not backup.exists()

    def test_no_migration_for_missing_memory_md(self, tmp_path: Path) -> None:
        """If MEMORY.md doesn't exist, rebuild_index is called, not migration."""
        store = MemoryDirStore(tmp_path)
        # Don't create MEMORY.md - it should be auto-built
        if store.index_path.exists():
            store.index_path.unlink()

        result = store.load_index_for_prompt()
        # Should be empty index (no memory files)
        assert result.strip() == ""

        backup = store.memory_dir / "project" / "legacy-memory.md"
        assert not backup.exists()

    def test_migration_with_existing_memory_files(self, tmp_path: Path) -> None:
        """Migration should include both legacy backup and existing files in index."""
        store = MemoryDirStore(tmp_path)

        # Create an existing memory file first
        store.create_memory("user", "Preferences", "User prefs", "Dark mode enabled")

        # Overwrite MEMORY.md with legacy content
        store.index_path.write_text("Old inline content\nMore old stuff", encoding="utf-8")

        result = store.load_index_for_prompt()

        # Index should contain both
        assert "- [Legacy Memory]" in result
        assert "- [Preferences]" in result
