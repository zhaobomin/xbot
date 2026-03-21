"""Tests for MemoryTool."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from xbot.agent.tools.memory import MemoryTool


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace with sample memory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        memory_dir = workspace / "memory"
        memory_dir.mkdir(parents=True)

        # Create sample MEMORY.md
        memory_content = """# Long-term Memory

This file stores important information.

## User Information

- **Name**: Test User
- **Role**: Developer

## Preferences

- **Language**: Chinese
- **Style**: Casual

---

*This file is automatically updated.*
"""
        (memory_dir / "MEMORY.md").write_text(memory_content, encoding="utf-8")

        # Create sample HISTORY.md
        history_content = """[2026-01-01 10:00] User asked about weather.

[2026-01-02 15:30] Discussed coding preferences.
"""
        (memory_dir / "HISTORY.md").write_text(history_content, encoding="utf-8")

        yield workspace


class TestMemoryToolRead:
    """Tests for memory read action."""

    def test_read_all(self, temp_workspace):
        """Test reading all memory."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(action="read"))

        assert "User Information" in result
        assert "Test User" in result
        assert "Preferences" in result

    def test_read_section(self, temp_workspace):
        """Test reading a specific section."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(action="read", section="User Information"))

        assert "Test User" in result
        assert "Developer" in result
        assert "Preferences" not in result

    def test_read_nonexistent_section(self, temp_workspace):
        """Test reading a section that doesn't exist."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(action="read", section="Nonexistent"))

        assert "not found" in result.lower()

    def test_read_empty_memory(self):
        """Test reading when no memory file exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tool = MemoryTool(workspace=tmpdir)
            result = asyncio.run(tool.execute(action="read"))

            assert "No long-term memory" in result


class TestMemoryToolSearch:
    """Tests for memory search action."""

    def test_search_basic(self, temp_workspace):
        """Test basic search."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(action="search", query="Test User"))

        assert "result" in result.lower()

    def test_search_no_query(self, temp_workspace):
        """Test search without query."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(action="search", query=None))

        assert "provide a search query" in result.lower()

    def test_search_no_results(self, temp_workspace):
        """Test search with no matching results."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(action="search", query="xyznonexistent123"))

        assert "No results" in result or "0 result" in result.lower()


class TestMemoryToolWrite:
    """Tests for memory write action."""

    def test_write_new_section(self, temp_workspace):
        """Test writing a new section."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(
            action="write",
            section="New Section",
            content="This is new content."
        ))

        assert "written to memory" in result.lower()

        # Verify it was written
        content = (temp_workspace / "memory" / "MEMORY.md").read_text()
        assert "New Section" in content
        assert "new content" in content

    def test_write_update_section(self, temp_workspace):
        """Test updating an existing section."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(
            action="write",
            section="User Information",
            content="- **Name**: Updated Name\n- **Role**: Manager"
        ))

        assert "written to memory" in result.lower()

        # Verify it was updated
        content = (temp_workspace / "memory" / "MEMORY.md").read_text()
        assert "Updated Name" in content
        assert "Test User" not in content

    def test_write_no_section(self, temp_workspace):
        """Test write without section name."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(action="write", content="some content"))

        assert "provide a section name" in result.lower()

    def test_write_no_content(self, temp_workspace):
        """Test write without content."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(action="write", section="Test Section"))

        assert "provide content" in result.lower()

    def test_write_preserves_other_sections(self, temp_workspace):
        """Test that writing doesn't affect other sections."""
        tool = MemoryTool(workspace=temp_workspace)

        asyncio.run(tool.execute(
            action="write",
            section="User Information",
            content="- **Name**: New Name"
        ))

        content = (temp_workspace / "memory" / "MEMORY.md").read_text()
        assert "Preferences" in content  # Other section preserved
        assert "Chinese" in content  # Other section content preserved


class TestMemoryToolAppend:
    """Tests for memory append action."""

    def test_append_history(self, temp_workspace):
        """Test appending to history."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(
            action="append",
            content="Test entry for history."
        ))

        assert "appended to history" in result.lower()

        # Verify it was appended
        content = (temp_workspace / "memory" / "HISTORY.md").read_text()
        assert "Test entry for history" in content

    def test_append_no_content(self, temp_workspace):
        """Test append without content."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(action="append"))

        assert "provide content" in result.lower()

    def test_append_creates_file(self):
        """Test that append creates history file if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tool = MemoryTool(workspace=tmpdir)
            result = asyncio.run(tool.execute(
                action="append",
                content="First entry."
            ))

            assert "appended to history" in result.lower()
            assert (Path(tmpdir) / "memory" / "HISTORY.md").exists()


class TestMemoryToolEdgeCases:
    """Edge case tests."""

    def test_unknown_action(self, temp_workspace):
        """Test unknown action."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(action="unknown"))

        assert "Unknown action" in result

    def test_special_characters_in_section(self, temp_workspace):
        """Test section name with special characters."""
        tool = MemoryTool(workspace=temp_workspace)
        result = asyncio.run(tool.execute(
            action="write",
            section="Test (Special) [Chars]",
            content="Content with special chars."
        ))

        # Should not crash
        assert "written" in result.lower()

    def test_multiline_content(self, temp_workspace):
        """Test writing multiline content."""
        tool = MemoryTool(workspace=temp_workspace)
        content = """Line 1
Line 2
Line 3"""
        result = asyncio.run(tool.execute(
            action="write",
            section="Multiline",
            content=content
        ))

        assert "written" in result.lower()

        written = (temp_workspace / "memory" / "MEMORY.md").read_text()
        assert "Line 1" in written
        assert "Line 3" in written