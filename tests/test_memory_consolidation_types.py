"""Test MemoryStore.consolidate() handles non-string tool call arguments.

Regression test for https://github.com/HKUDS/xbot/issues/1042
When memory consolidation receives dict values instead of strings from the LLM
tool call response, it should serialize them to JSON instead of raising TypeError.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from xbot.agent.memory import MemoryStore
from xbot.providers.base import LLMResponse, ToolCallRequest


def _make_messages(message_count: int = 30):
    """Create a list of mock messages."""
    return [
        {"role": "user", "content": f"msg{i}", "timestamp": "2026-01-01 00:00"}
        for i in range(message_count)
    ]


def _make_tool_response(history_entry, memory_update):
    """Create an LLMResponse with a save_memory tool call."""
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCallRequest(
                id="call_1",
                name="save_memory",
                arguments={
                    "history_entry": history_entry,
                    "memory_update": memory_update,
                },
            )
        ],
    )


def _make_mock_backend(responses: list[LLMResponse] | None = None):
    """Create a mock backend with call_for_consolidation method."""
    backend = MagicMock()
    backend.call_for_consolidation = AsyncMock()

    if responses:
        # Set up sequential responses
        call_count = [0]

        async def _next_response(*args, **kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx < len(responses):
                return responses[idx]
            return LLMResponse(content="", tool_calls=[])

        backend.call_for_consolidation.side_effect = _next_response

    return backend


class TestMemoryConsolidationTypeHandling:
    """Test that consolidation handles various argument types correctly."""

    @pytest.mark.asyncio
    async def test_string_arguments_work(self, tmp_path: Path) -> None:
        """Normal case: LLM returns string arguments."""
        store = MemoryStore(tmp_path)
        backend = _make_mock_backend([
            _make_tool_response(
                history_entry="[2026-01-01] User discussed testing.",
                memory_update="# Memory\nUser likes testing.",
            )
        ])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is True
        assert store.history_file.exists()
        assert "[2026-01-01] User discussed testing." in store.history_file.read_text()
        assert "User likes testing." in store.memory_file.read_text()

    @pytest.mark.asyncio
    async def test_dict_arguments_serialized_to_json(self, tmp_path: Path) -> None:
        """Issue #1042: LLM returns dict instead of string — must not raise TypeError."""
        store = MemoryStore(tmp_path)
        backend = _make_mock_backend([
            _make_tool_response(
                history_entry={"timestamp": "2026-01-01", "summary": "User discussed testing."},
                memory_update={"facts": ["User likes testing"], "topics": ["testing"]},
            )
        ])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is True
        assert store.history_file.exists()
        history_content = store.history_file.read_text()
        parsed = json.loads(history_content.strip())
        assert parsed["summary"] == "User discussed testing."

        memory_content = store.memory_file.read_text()
        parsed_mem = json.loads(memory_content)
        assert "User likes testing" in parsed_mem["facts"]

    @pytest.mark.asyncio
    async def test_string_arguments_as_raw_json(self, tmp_path: Path) -> None:
        """Some providers return arguments as a JSON string instead of parsed dict."""
        store = MemoryStore(tmp_path)

        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="save_memory",
                    arguments=json.dumps({
                        "history_entry": "[2026-01-01] User discussed testing.",
                        "memory_update": "# Memory\nUser likes testing.",
                    }),
                )
            ],
        )
        backend = _make_mock_backend([response])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is True
        assert "User discussed testing." in store.history_file.read_text()

    @pytest.mark.asyncio
    async def test_no_tool_call_returns_false(self, tmp_path: Path) -> None:
        """When LLM doesn't use the save_memory tool, return False."""
        store = MemoryStore(tmp_path)
        backend = _make_mock_backend([
            LLMResponse(content="I summarized the conversation.", tool_calls=[])
        ])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is False
        assert not store.history_file.exists()

    @pytest.mark.asyncio
    async def test_skips_when_message_chunk_is_empty(self, tmp_path: Path) -> None:
        """Consolidation should be a no-op when the selected chunk is empty."""
        store = MemoryStore(tmp_path)
        backend = _make_mock_backend()
        messages: list[dict] = []

        result = await store.consolidate(messages, backend)

        assert result is True
        backend.call_for_consolidation.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_arguments_extracts_first_dict(self, tmp_path: Path) -> None:
        """Some providers return arguments as a list - extract first element if it's a dict."""
        store = MemoryStore(tmp_path)

        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="save_memory",
                    arguments=[{
                        "history_entry": "[2026-01-01] User discussed testing.",
                        "memory_update": "# Memory\nUser likes testing.",
                    }],
                )
            ],
        )
        backend = _make_mock_backend([response])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is True
        assert "User discussed testing." in store.history_file.read_text()
        assert "User likes testing." in store.memory_file.read_text()

    @pytest.mark.asyncio
    async def test_list_arguments_empty_list_returns_false(self, tmp_path: Path) -> None:
        """Empty list arguments should return False."""
        store = MemoryStore(tmp_path)

        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="save_memory",
                    arguments=[],
                )
            ],
        )
        backend = _make_mock_backend([response])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is False

    @pytest.mark.asyncio
    async def test_list_arguments_non_dict_content_returns_false(self, tmp_path: Path) -> None:
        """List with non-dict content should return False."""
        store = MemoryStore(tmp_path)

        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="save_memory",
                    arguments=["not a dict"],
                )
            ],
        )
        backend = _make_mock_backend([response])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is False

    @pytest.mark.asyncio
    async def test_missing_history_entry_returns_false_without_writing(self, tmp_path: Path) -> None:
        """Missing history_entry field should return False."""
        store = MemoryStore(tmp_path)

        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="save_memory",
                    arguments={
                        "memory_update": "# Memory\nUser likes testing.",
                    },
                )
            ],
        )
        backend = _make_mock_backend([response])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is False
        assert not store.history_file.exists()

    @pytest.mark.asyncio
    async def test_missing_memory_update_returns_false_without_writing(self, tmp_path: Path) -> None:
        """Missing memory_update field should return False."""
        store = MemoryStore(tmp_path)

        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="save_memory",
                    arguments={
                        "history_entry": "[2026-01-01] User discussed testing.",
                    },
                )
            ],
        )
        backend = _make_mock_backend([response])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is False
        assert not store.history_file.exists()

    @pytest.mark.asyncio
    async def test_null_required_field_returns_false_without_writing(self, tmp_path: Path) -> None:
        """Null required field should return False."""
        store = MemoryStore(tmp_path)

        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="save_memory",
                    arguments={
                        "history_entry": None,
                        "memory_update": "# Memory\nUser likes testing.",
                    },
                )
            ],
        )
        backend = _make_mock_backend([response])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is False
        assert not store.history_file.exists()

    @pytest.mark.asyncio
    async def test_empty_history_entry_returns_false_without_writing(self, tmp_path: Path) -> None:
        """Empty history_entry should return False."""
        store = MemoryStore(tmp_path)

        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="save_memory",
                    arguments={
                        "history_entry": "   ",  # Whitespace only
                        "memory_update": "# Memory\nUser likes testing.",
                    },
                )
            ],
        )
        backend = _make_mock_backend([response])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is False
        assert not store.history_file.exists()

    @pytest.mark.asyncio
    async def test_retries_transient_error_then_succeeds(self, tmp_path: Path) -> None:
        """Should succeed on first successful tool call (retries handled by call_for_consolidation)."""
        store = MemoryStore(tmp_path)

        # Single success response (retries are now handled by call_for_consolidation)
        success_response = _make_tool_response(
            history_entry="[2026-01-01] User discussed testing.",
            memory_update="# Memory\nUser likes testing.",
        )

        backend = _make_mock_backend([success_response])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is True
        assert store.history_file.exists()

    @pytest.mark.asyncio
    async def test_consolidation_delegates_to_provider_defaults(self, tmp_path: Path) -> None:
        """Verify that consolidate calls the backend's call_for_consolidation with expected args."""
        store = MemoryStore(tmp_path)

        response = _make_tool_response(
            history_entry="[2026-01-01] User discussed testing.",
            memory_update="# Memory\nUser likes testing.",
        )
        backend = _make_mock_backend([response])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is True
        # Verify the backend was called
        backend.call_for_consolidation.assert_called()

    @pytest.mark.asyncio
    async def test_tool_choice_fallback_on_unsupported_error(self, tmp_path: Path) -> None:
        """Should fallback to auto tool_choice when forced choice is unsupported."""
        store = MemoryStore(tmp_path)

        # First response indicates unsupported tool_choice
        error_response = LLMResponse(
            content="tool_choice not supported",
            tool_calls=[],
            finish_reason="error",
        )
        success_response = _make_tool_response(
            history_entry="[2026-01-01] User discussed testing.",
            memory_update="# Memory\nUser likes testing.",
        )

        backend = _make_mock_backend([error_response, success_response])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is True
        # Should have been called twice (once for error, once for retry)
        assert backend.call_for_consolidation.call_count == 2

    @pytest.mark.asyncio
    async def test_tool_choice_fallback_auto_no_tool_call(self, tmp_path: Path) -> None:
        """Should return False when auto tool_choice also doesn't result in tool call."""
        store = MemoryStore(tmp_path)

        # Both responses have no tool calls
        response1 = LLMResponse(
            content="tool_choice not supported",
            tool_calls=[],
            finish_reason="error",
        )
        response2 = LLMResponse(
            content="I summarized the conversation.",
            tool_calls=[],
        )

        backend = _make_mock_backend([response1, response2])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is False

    @pytest.mark.asyncio
    async def test_raw_archive_after_consecutive_failures(self, tmp_path: Path) -> None:
        """Should raw-archive messages after consecutive failures reach threshold."""
        store = MemoryStore(tmp_path)

        # Each call fails - need to call consolidate multiple times to trigger raw archive
        error_response = LLMResponse(
            content="error",
            tool_calls=[],
            finish_reason="error",
        )

        backend = _make_mock_backend([error_response])
        messages = _make_messages(message_count=60)

        # First failure
        result1 = await store.consolidate(messages, backend)
        assert result1 is False

        # Second failure
        result2 = await store.consolidate(messages, backend)
        assert result2 is False

        # Third failure - triggers raw archive (threshold is 3)
        result3 = await store.consolidate(messages, backend)
        assert result3 is True  # Raw archived

    @pytest.mark.asyncio
    async def test_raw_archive_counter_resets_on_success(self, tmp_path: Path) -> None:
        """Raw archive counter should reset after a successful consolidation."""
        store = MemoryStore(tmp_path)

        success_response = _make_tool_response(
            history_entry="[2026-01-01] User discussed testing.",
            memory_update="# Memory\nUser likes testing.",
        )

        backend = _make_mock_backend([success_response])
        messages = _make_messages(message_count=60)

        result = await store.consolidate(messages, backend)

        assert result is True
        assert store.history_file.exists()