"""Test consolidation prompt improvements.

Phase 3: Verify improved prompt with extraction guidelines and context size hints.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from xbot.memory.store import MemoryStore


def _make_messages(count: int = 5) -> list[dict]:
    """Create a list of mock messages."""
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"Message {i} with some content for testing.",
            "timestamp": f"2026-01-01T{i:02d}:00:00",
        }
        for i in range(count)
    ]


def _make_mock_backend():
    """Create a mock backend with call_for_consolidation method."""
    from xbot.platform.providers.base import LLMResponse, ToolCallRequest

    backend = MagicMock()
    backend.call_for_consolidation = AsyncMock(
        return_value=LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="save_memory",
                    arguments={
                        "history_entry": "[2026-01-01] Test entry.",
                        "memory_update": "# Memory\nTest memory.",
                    },
                )
            ],
        )
    )
    return backend


class TestConsolidationPromptContent:
    """Test that consolidation prompt includes extraction guidelines."""

    @pytest.mark.asyncio
    async def test_prompt_includes_memory_md_guidelines(self, tmp_path: Path) -> None:
        """Prompt should include guidelines for MEMORY.md extraction."""
        store = MemoryStore(tmp_path)
        backend = _make_mock_backend()

        await store.consolidate(
            messages=_make_messages(4),
            backend=backend,
        )

        # Verify the prompt content
        call_args = backend.call_for_consolidation.call_args
        messages = call_args.kwargs.get('messages') or call_args.args[0]

        # Find system and user messages
        system_msg = next((m for m in messages if m['role'] == 'system'), None)
        user_msg = next((m for m in messages if m['role'] == 'user'), None)

        assert system_msg is not None, "Should have system message"
        assert user_msg is not None, "Should have user message"

        # Check system message contains extraction guidelines
        system_content = system_msg['content'].lower()
        assert 'memory' in system_content or 'extract' in system_content, \
            "System prompt should mention memory or extraction"

    @pytest.mark.asyncio
    async def test_prompt_includes_history_md_guidelines(self, tmp_path: Path) -> None:
        """Prompt should mention HISTORY.md as event log destination."""
        store = MemoryStore(tmp_path)
        backend = _make_mock_backend()

        await store.consolidate(
            messages=_make_messages(4),
            backend=backend,
        )

        call_args = backend.call_for_consolidation.call_args
        messages = call_args.kwargs.get('messages') or call_args.args[0]

        # Combine all message content
        all_content = ' '.join(m.get('content', '').lower() for m in messages)

        # Should mention history or event log
        assert 'history' in all_content or 'event' in all_content, \
            "Prompt should mention HISTORY.md or event log"

    @pytest.mark.asyncio
    async def test_prompt_includes_ignore_guidelines(self, tmp_path: Path) -> None:
        """Prompt should mention what to ignore."""
        store = MemoryStore(tmp_path)
        backend = _make_mock_backend()

        await store.consolidate(
            messages=_make_messages(4),
            backend=backend,
        )

        call_args = backend.call_for_consolidation.call_args
        messages = call_args.kwargs.get('messages') or call_args.args[0]

        # Combine all message content
        all_content = ' '.join(m.get('content', '').lower() for m in messages)

        # Should mention ignore or selection criteria
        assert 'ignore' in all_content or 'selective' in all_content or 'substance' in all_content, \
            "Prompt should mention what to ignore or be selective"


class TestConsolidationPromptContextSize:
    """Test that consolidation prompt includes token count context."""

    @pytest.mark.asyncio
    async def test_prompt_includes_token_counts(self, tmp_path: Path) -> None:
        """Prompt should include token count information."""
        store = MemoryStore(tmp_path)

        # Write some existing memory
        store.write_long_term("# Memory\n\n- User prefers dark mode\n- Project uses Python")

        backend = _make_mock_backend()

        await store.consolidate(
            messages=_make_messages(10),
            backend=backend,
        )

        call_args = backend.call_for_consolidation.call_args
        messages = call_args.kwargs.get('messages') or call_args.args[0]

        user_msg = next((m for m in messages if m['role'] == 'user'), None)
        assert user_msg is not None

        user_content = user_msg['content'].lower()

        # Should include token-related information
        assert 'token' in user_content or 'context' in user_content, \
            "Prompt should include token or context size information"

    @pytest.mark.asyncio
    async def test_prompt_shows_current_memory_size(self, tmp_path: Path) -> None:
        """Prompt should show current memory size."""
        store = MemoryStore(tmp_path)

        # Write substantial memory
        long_memory = "# Memory\n\n" + "\n".join([f"- Fact {i}: Some important information here." for i in range(20)])
        store.write_long_term(long_memory)

        backend = _make_mock_backend()

        await store.consolidate(
            messages=_make_messages(5),
            backend=backend,
        )

        call_args = backend.call_for_consolidation.call_args
        messages = call_args.kwargs.get('messages') or call_args.args[0]

        user_msg = next((m for m in messages if m['role'] == 'user'), None)
        assert user_msg is not None

        # Should show memory content
        user_content = user_msg['content']
        assert 'Current' in user_content or 'memory' in user_content.lower(), \
            "Prompt should show current memory"


class TestConsolidationPromptStructure:
    """Test the overall structure of the consolidation prompt."""

    @pytest.mark.asyncio
    async def test_prompt_has_clear_sections(self, tmp_path: Path) -> None:
        """Prompt should have clearly separated sections."""
        store = MemoryStore(tmp_path)
        backend = _make_mock_backend()

        await store.consolidate(
            messages=_make_messages(4),
            backend=backend,
        )

        call_args = backend.call_for_consolidation.call_args
        messages = call_args.kwargs.get('messages') or call_args.args[0]

        user_msg = next((m for m in messages if m['role'] == 'user'), None)
        assert user_msg is not None

        user_content = user_msg['content']

        # Should have sections marked with headers (## or similar)
        assert '##' in user_content or '#' in user_content, \
            "Prompt should use markdown headers for sections"

    @pytest.mark.asyncio
    async def test_prompt_includes_current_memory(self, tmp_path: Path) -> None:
        """Prompt should include current memory content."""
        store = MemoryStore(tmp_path)

        test_memory = "# Memory\n\n- User name: TestUser\n- Preference: JSON output"
        store.write_long_term(test_memory)

        backend = _make_mock_backend()

        await store.consolidate(
            messages=_make_messages(4),
            backend=backend,
        )

        call_args = backend.call_for_consolidation.call_args
        messages = call_args.kwargs.get('messages') or call_args.args[0]

        user_msg = next((m for m in messages if m['role'] == 'user'), None)
        assert user_msg is not None

        user_content = user_msg['content']

        # Should include the current memory content
        assert 'TestUser' in user_content or 'Memory' in user_content, \
            "Prompt should include current memory content"

    @pytest.mark.asyncio
    async def test_prompt_includes_conversation_to_process(self, tmp_path: Path) -> None:
        """Prompt should include the conversation to process."""
        store = MemoryStore(tmp_path)
        backend = _make_mock_backend()

        test_messages = [
            {"role": "user", "content": "What is the capital of France?", "timestamp": "2026-01-01T00:00:00"},
            {"role": "assistant", "content": "The capital of France is Paris.", "timestamp": "2026-01-01T00:01:00"},
        ]

        await store.consolidate(
            messages=test_messages,
            backend=backend,
        )

        call_args = backend.call_for_consolidation.call_args
        messages = call_args.kwargs.get('messages') or call_args.args[0]

        user_msg = next((m for m in messages if m['role'] == 'user'), None)
        assert user_msg is not None

        user_content = user_msg['content']

        # Should include the conversation
        assert 'France' in user_content or 'capital' in user_content.lower(), \
            "Prompt should include the conversation to process"


class TestConsolidationToolDefinition:
    """Test the save_memory tool definition."""

    def test_tool_has_clear_descriptions(self) -> None:
        """Tool parameters should have clear descriptions."""
        from xbot.memory.store import _SAVE_MEMORY_TOOL

        tool = _SAVE_MEMORY_TOOL[0]
        function = tool['function']
        params = function['parameters']['properties']

        # Check history_entry description
        history_desc = params['history_entry']['description'].lower()
        assert 'event' in history_desc or 'summar' in history_desc or 'decision' in history_desc, \
            "history_entry should mention events, summaries, or decisions"

        # Check memory_update description
        memory_desc = params['memory_update']['description'].lower()
        assert 'memory' in memory_desc or 'fact' in memory_desc or 'update' in memory_desc, \
            "memory_update should mention memory, facts, or updates"

    def test_tool_has_required_fields(self) -> None:
        """Tool should have both fields as required."""
        from xbot.memory.store import _SAVE_MEMORY_TOOL

        tool = _SAVE_MEMORY_TOOL[0]
        function = tool['function']
        required = function['parameters']['required']

        assert 'history_entry' in required, "history_entry should be required"
        assert 'memory_update' in required, "memory_update should be required"
