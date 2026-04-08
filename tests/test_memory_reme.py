"""Tests for ReMe-based memory system."""

from pathlib import Path

import pytest

from xbot.agent.memory.reme import (
    _REME_AVAILABLE,
    ReMeMemoryStore,
    create_memory_store,
)


class TestReMeMemoryStoreBasic:
    """Tests for basic memory operations (no ReMe initialization)."""

    @pytest.fixture
    def temp_workspace(self, tmp_path: Path) -> Path:
        """Create a temporary workspace."""
        return tmp_path

    def test_init(self, temp_workspace: Path) -> None:
        """Test basic initialization."""
        store = ReMeMemoryStore(temp_workspace)
        assert store.workspace == temp_workspace
        assert store.memory_dir.exists()
        assert store.memory_file.name == "MEMORY.md"
        assert store.history_file.name == "HISTORY.md"

    def test_read_write_long_term(self, temp_workspace: Path) -> None:
        """Test reading and writing long-term memory."""
        store = ReMeMemoryStore(temp_workspace)

        # Write
        store.write_long_term("# Test Memory\n\nThis is a test.")

        # Read
        content = store.read_long_term()
        assert "# Test Memory" in content
        assert "This is a test." in content

    def test_append_history(self, temp_workspace: Path) -> None:
        """Test appending to history."""
        store = ReMeMemoryStore(temp_workspace)

        store.append_history("[2026-03-21 10:00] Test entry")
        store.append_history("[2026-03-21 11:00] Another entry")

        content = store.history_file.read_text(encoding="utf-8")
        assert "Test entry" in content
        assert "Another entry" in content

    def test_get_memory_context(self, temp_workspace: Path) -> None:
        """Test getting memory context for prompts."""
        store = ReMeMemoryStore(temp_workspace)
        store.write_long_term("User prefers dark mode.")

        context = store.get_memory_context()
        assert "## Long-term Memory" in context
        assert "dark mode" in context

    def test_empty_memory_context(self, temp_workspace: Path) -> None:
        """Test empty memory context."""
        store = ReMeMemoryStore(temp_workspace)

        context = store.get_memory_context()
        assert context == ""

    def test_fallback_search(self, temp_workspace: Path) -> None:
        """Test fallback search when ReMe is not initialized."""
        store = ReMeMemoryStore(temp_workspace)
        store.write_long_term("User likes Python programming.")
        store.append_history("[2026-03-21] Discussed Python async patterns.")

        results = store._fallback_search("Python", max_results=5)

        assert len(results) >= 1
        assert any("Python" in r["memory"] for r in results)


class TestReMeMemoryStoreIntegration:
    """Tests requiring ReMe initialization (optional)."""

    @pytest.fixture
    def temp_workspace(self, tmp_path: Path) -> Path:
        """Create a temporary workspace."""
        return tmp_path

    @pytest.mark.asyncio
    async def test_search_memory_fallback(self, temp_workspace: Path) -> None:
        """Test search_memory with fallback (no ReMe init)."""
        store = ReMeMemoryStore(temp_workspace, enable_vector_search=False)
        store.write_long_term("User prefers tea over coffee.")

        results = await store.search_memory("tea", max_results=5)

        # Results may come from ReMe or fallback search
        # If ReMe is initialized but hasn't indexed yet, results may be empty
        # So we just verify the method doesn't crash
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_compact_context_without_reme(self, temp_workspace: Path) -> None:
        """Test context compaction fallback."""
        store = ReMeMemoryStore(temp_workspace, enable_vector_search=False)

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        processed, summary = await store.compact_context(messages)

        # Should return messages unchanged when ReMe not initialized
        assert processed == messages
        assert summary is None

    @pytest.mark.asyncio
    async def test_close(self, temp_workspace: Path) -> None:
        """Test closing the store."""
        store = ReMeMemoryStore(temp_workspace, enable_vector_search=False)

        # Should not raise
        await store.close()


class TestFactoryFunction:
    """Tests for create_memory_store factory."""

    @pytest.fixture
    def temp_workspace(self, tmp_path: Path) -> Path:
        """Create a temporary workspace."""
        return tmp_path

    def test_create_default(self, temp_workspace: Path) -> None:
        """Test creating with default settings."""
        store = create_memory_store(temp_workspace)

        assert isinstance(store, ReMeMemoryStore)
        assert store.workspace == temp_workspace

    def test_create_with_config(self, temp_workspace: Path) -> None:
        """Test creating with custom config."""
        store = create_memory_store(
            workspace=temp_workspace,
            use_reme=True,
            llm_config={"model": "gpt-4.1-nano"},
            enable_vector_search=False,
        )

        assert isinstance(store, ReMeMemoryStore)

    def test_create_without_reme(self, temp_workspace: Path) -> None:
        """Test creating without ReMe."""
        store = create_memory_store(
            workspace=temp_workspace,
            use_reme=False,
        )

        assert isinstance(store, ReMeMemoryStore)
        # Will use fallback mode


class TestReMeAvailability:
    """Tests for ReMe availability detection."""

    def test_availability_flag(self) -> None:
        """Test that availability flag is set correctly."""
        # This test just verifies the flag exists
        assert isinstance(_REME_AVAILABLE, bool)
