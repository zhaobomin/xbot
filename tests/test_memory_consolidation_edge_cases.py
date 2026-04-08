"""Additional edge case tests for memory consolidation.

Tests for potential bugs and edge cases discovered during code review.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.agent.memory.store import MemoryConsolidator, MemoryStore
from xbot.providers.base import LLMResponse, ToolCallRequest
from xbot.session.manager import Session, SessionManager


def _make_messages_with_turns(turn_count: int) -> list[dict]:
    """Create messages with alternating user/assistant pairs."""
    messages = []
    for i in range(turn_count):
        messages.append({
            "role": "user",
            "content": f"User message {i}" + "x" * 200,
            "timestamp": f"2026-01-01T{i:02d}:00:00",
        })
        messages.append({
            "role": "assistant",
            "content": f"Assistant response {i}" + "y" * 200,
            "timestamp": f"2026-01-01T{i:02d}:01:00",
        })
    return messages


def _make_tool_response(history_entry: str, memory_update: str) -> LLMResponse:
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


def _make_mock_backend_with_response(response: LLMResponse) -> MagicMock:
    """Create a mock backend with specific response."""
    backend = MagicMock()
    backend.call_for_consolidation = AsyncMock(return_value=response)
    return backend


class TestNO_CHANGEHandling:
    """Test that 'NO_CHANGE' response is handled correctly."""

    @pytest.mark.asyncio
    async def test_no_change_does_not_overwrite_memory(self, tmp_path: Path) -> None:
        """If LLM returns 'NO_CHANGE', memory should not be updated."""
        store = MemoryStore(tmp_path)

        # Write initial memory
        initial_memory = "# Memory\n\n- User prefers dark mode"
        store.write_long_term(initial_memory)

        # Backend returns NO_CHANGE
        backend = _make_mock_backend_with_response(
            _make_tool_response("[2026-01-01] Test entry.", "NO_CHANGE")
        )

        await store.consolidate(
            messages=[{"role": "user", "content": "Hello", "timestamp": "2026-01-01T00:00:00"}],
            backend=backend,
        )

        # Memory should NOT be changed to "NO_CHANGE"
        final_memory = store.read_long_term()
        assert final_memory == initial_memory, \
            f"Memory should remain unchanged, but got: {final_memory}"

    @pytest.mark.asyncio
    async def test_no_change_case_insensitive(self, tmp_path: Path) -> None:
        """NO_CHANGE should be case-insensitive."""
        store = MemoryStore(tmp_path)

        initial_memory = "# Memory\n\n- Test fact"
        store.write_long_term(initial_memory)

        backend = _make_mock_backend_with_response(
            _make_tool_response("[2026-01-01] Test entry.", "no_change")
        )

        await store.consolidate(
            messages=[{"role": "user", "content": "Hello", "timestamp": "2026-01-01T00:00:00"}],
            backend=backend,
        )

        final_memory = store.read_long_term()
        assert final_memory == initial_memory, \
            "Memory should remain unchanged for lowercase no_change"


class TestForceConsolidateBoundary:
    """Test that force_consolidate respects boundaries."""

    @pytest.mark.asyncio
    async def test_force_consolidate_respects_reserve(self, tmp_path: Path) -> None:
        """Force consolidation should now respect MIN_RESERVE_TURNS."""
        backend = _make_mock_backend_with_response(
            _make_tool_response("[2026-01-01] Entry.", "# Memory\nUpdated.")
        )

        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=backend,
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # 6 turns (12 messages), with MIN_RESERVE_TURNS=5
        # Should consolidate at most 1 turn (2 messages)
        session = Session(key="test:force-reserve")
        session.messages = _make_messages_with_turns(6)

        result = await consolidator.force_consolidate(session)

        # Should consolidate at most 2 messages (1 turn), reserving 5 turns
        assert result["success"] is True
        assert result["messages_consolidated"] <= 2

    @pytest.mark.asyncio
    async def test_force_consolidate_with_custom_reserve(self, tmp_path: Path) -> None:
        """Force consolidation with custom reserve_last_n."""
        backend = _make_mock_backend_with_response(
            _make_tool_response("[2026-01-01] Entry.", "# Memory\nUpdated.")
        )

        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=backend,
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # 10 turns (20 messages)
        session = Session(key="test:custom-reserve")
        session.messages = _make_messages_with_turns(10)

        # Reserve only 2 turns (4 messages)
        result = await consolidator.force_consolidate(session, reserve_last_n=2)

        # Should consolidate 16 messages (8 turns), reserving 4 messages (2 turns)
        assert result["success"] is True
        assert result["messages_consolidated"] == 16

    @pytest.mark.asyncio
    async def test_force_consolidate_all_with_zero_reserve(self, tmp_path: Path) -> None:
        """Force consolidation with reserve_last_n=0 consolidates all."""
        backend = _make_mock_backend_with_response(
            _make_tool_response("[2026-01-01] Entry.", "# Memory\nUpdated.")
        )

        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=backend,
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # 5 turns (10 messages)
        session = Session(key="test:zero-reserve")
        session.messages = _make_messages_with_turns(5)

        # With reserve_last_n=0, consolidate all
        result = await consolidator.force_consolidate(session, reserve_last_n=0)

        assert result["success"] is True
        assert result["messages_consolidated"] == 10


class TestIncompleteMessagePairs:
    """Test handling of incomplete user-assistant pairs."""

    @pytest.mark.asyncio
    async def test_last_message_is_user_only(self, tmp_path: Path) -> None:
        """Messages ending with user message (no assistant reply) should be handled."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend_with_response(
                _make_tool_response("[2026-01-01] Entry.", "# Memory\nTest.")
            ),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # Create messages with an extra user message at the end (no reply)
        messages = _make_messages_with_turns(10)
        messages.append({
            "role": "user",
            "content": "Final question without reply",
            "timestamp": "2026-01-01T99:00:00",
        })

        session = Session(key="test:incomplete")
        session.messages = messages

        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(8_000, 'mock')
        ):
            # Should not raise
            await consolidator.maybe_consolidate_by_tokens(session)

        # last_consolidated should still be at a user turn boundary
        if session.last_consolidated > 0:
            # Even index = user message
            assert session.last_consolidated % 2 == 0

    @pytest.mark.asyncio
    async def test_single_user_message_no_consolidation(self, tmp_path: Path) -> None:
        """Single user message with no reply should not be consolidated."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend_with_response(
                _make_tool_response("[2026-01-01] Entry.", "# Memory\nTest.")
            ),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # Just one user message
        session = Session(key="test:single-msg")
        session.messages = [{"role": "user", "content": "Hello", "timestamp": "2026-01-01T00:00:00"}]

        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(8_000, 'mock')
        ):
            await consolidator.maybe_consolidate_by_tokens(session)

        # Should not consolidate (only 1 message, need to reserve 10)
        assert session.last_consolidated == 0


class TestBoundaryEdgeCases:
    """Test edge cases in boundary selection."""

    @pytest.mark.asyncio
    async def test_exact_reserve_boundary(self, tmp_path: Path) -> None:
        """Test when conversation is exactly MIN_RESERVE_TURNS."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend_with_response(
                _make_tool_response("[2026-01-01] Entry.", "# Memory\nTest.")
            ),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # Exactly 5 turns (10 messages) = MIN_RESERVE_TURNS
        session = Session(key="test:exact-reserve")
        session.messages = _make_messages_with_turns(5)

        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(9_000, 'mock')
        ):
            await consolidator.maybe_consolidate_by_tokens(session)

        # Should NOT consolidate - all messages are reserve
        assert session.last_consolidated == 0

    @pytest.mark.asyncio
    async def test_partial_consolidation_when_cannot_reach_target(self, tmp_path: Path) -> None:
        """Should consolidate what it can, even if target tokens not reached."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend_with_response(
                _make_tool_response("[2026-01-01] Entry.", "# Memory\nTest.")
            ),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # 6 turns - can consolidate at most 1 turn
        session = Session(key="test:partial")
        session.messages = _make_messages_with_turns(6)

        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            side_effect=[
                (9_000, 'mock'),  # Initial - above threshold
                (8_500, 'mock'),  # After partial consolidation - still above target
            ]
        ):
            await consolidator.maybe_consolidate_by_tokens(session)

        # Should consolidate at most 1 turn (2 messages) even if target not reached
        assert session.last_consolidated <= 2

    def test_pick_boundary_with_zero_tokens_to_remove(self, tmp_path: Path) -> None:
        """pick_consolidation_boundary should return None for zero tokens_to_remove."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=MagicMock(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        session = Session(key="test:zero-remove")
        session.messages = _make_messages_with_turns(20)

        # Zero tokens to remove
        result = consolidator.pick_consolidation_boundary(session, 0)
        assert result is None

    def test_pick_boundary_with_negative_tokens_to_remove(self, tmp_path: Path) -> None:
        """pick_consolidation_boundary should return None for negative tokens_to_remove."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=MagicMock(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        session = Session(key="test:negative-remove")
        session.messages = _make_messages_with_turns(20)

        result = consolidator.pick_consolidation_boundary(session, -100)
        assert result is None

    def test_pick_boundary_when_all_already_consolidated(self, tmp_path: Path) -> None:
        """pick_consolidation_boundary should return None when all messages consolidated."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=MagicMock(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        session = Session(key="test:all-consolidated")
        session.messages = _make_messages_with_turns(20)
        session.last_consolidated = len(session.messages)  # All consolidated

        result = consolidator.pick_consolidation_boundary(session, 1000)
        assert result is None

    def test_pick_boundary_returns_user_turn_index(self, tmp_path: Path) -> None:
        """pick_consolidation_boundary should always return user turn (even index)."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=MagicMock(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        session = Session(key="test:user-boundary")
        session.messages = _make_messages_with_turns(20)

        result = consolidator.pick_consolidation_boundary(session, 5000)

        if result is not None:
            boundary_idx = result[0]
            # Even index = user message start
            assert boundary_idx % 2 == 0, \
                f"Boundary should be at user turn (even index), got {boundary_idx}"

    def test_pick_boundary_with_already_consolidated_offset(self, tmp_path: Path) -> None:
        """pick_consolidation_boundary should start from last_consolidated."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=MagicMock(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        session = Session(key="test:offset-start")
        session.messages = _make_messages_with_turns(20)
        session.last_consolidated = 10  # Start from message 10

        result = consolidator.pick_consolidation_boundary(session, 5000)

        if result is not None:
            boundary_idx = result[0]
            # Should be >= last_consolidated (10)
            assert boundary_idx >= 10, \
                f"Boundary should be >= last_consolidated (10), got {boundary_idx}"


class TestConcurrentEdgeCases:
    """Test edge cases in concurrent consolidation."""

    @pytest.mark.asyncio
    async def test_consolidation_during_session_reset(self, tmp_path: Path) -> None:
        """Consolidation should handle session being reset during operation."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend_with_response(
                _make_tool_response("[2026-01-01] Entry.", "# Memory\nTest.")
            ),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        session = Session(key="test:reset-during")
        session.messages = _make_messages_with_turns(20)

        # Simulate consolidation in progress
        async def slow_consolidate(msgs):
            await asyncio.sleep(0.1)
            return True

        with patch.object(consolidator, 'consolidate_messages', slow_consolidate):
            with patch.object(
                consolidator,
                'estimate_session_prompt_tokens',
                return_value=(8_000, 'mock')
            ):
                task = asyncio.create_task(consolidator.maybe_consolidate_by_tokens(session))

                # Wait a bit then reset session
                await asyncio.sleep(0.05)
                session.clear()

                # Wait for consolidation to complete
                await task

        # Should complete without error
        assert True

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent_locks(self, tmp_path: Path) -> None:
        """Different sessions should have independent locks."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend_with_response(
                _make_tool_response("[2026-01-01] Entry.", "# Memory\nTest.")
            ),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        sessions = [Session(key=f"test:multi-{i}") for i in range(5)]
        for s in sessions:
            s.messages = _make_messages_with_turns(20)

        results = []

        async def consolidate_session(s):
            with patch.object(
                consolidator,
                'estimate_session_prompt_tokens',
                return_value=(8_000, 'mock')
            ):
                await consolidator.maybe_consolidate_by_tokens(s)
                results.append(s.key)

        # Run all consolidations concurrently
        await asyncio.gather(*[consolidate_session(s) for s in sessions])

        # All should complete
        assert len(results) == 5


class TestMemoryUpdateEdgeCases:
    """Test edge cases in memory update handling."""

    @pytest.mark.asyncio
    async def test_empty_memory_update(self, tmp_path: Path) -> None:
        """Empty memory_update should be handled gracefully."""
        store = MemoryStore(tmp_path)

        initial = "# Memory\n\n- Test fact"
        store.write_long_term(initial)

        backend = _make_mock_backend_with_response(
            _make_tool_response("[2026-01-01] Entry.", "")
        )

        result = await store.consolidate(
            messages=[{"role": "user", "content": "Test", "timestamp": "2026-01-01T00:00:00"}],
            backend=backend,
        )

        # Empty update should trigger failure handling
        # Current behavior: empty string is valid but not written (different from initial)
        assert result is False or store.read_long_term() == initial

    @pytest.mark.asyncio
    async def test_very_large_memory_update(self, tmp_path: Path) -> None:
        """Large memory updates should be handled."""
        store = MemoryStore(tmp_path)

        # Large memory content
        large_memory = "# Memory\n\n" + "\n".join([f"- Fact {i}: " + "x" * 100 for i in range(100)])

        backend = _make_mock_backend_with_response(
            _make_tool_response("[2026-01-01] Entry.", large_memory)
        )

        result = await store.consolidate(
            messages=[{"role": "user", "content": "Test", "timestamp": "2026-01-01T00:00:00"}],
            backend=backend,
        )

        assert result is True
        stored = store.read_long_term()
        assert len(stored) == len(large_memory)


class TestTokenEstimationEdgeCases:
    """Test edge cases in token estimation."""

    @pytest.mark.asyncio
    async def test_zero_context_window(self, tmp_path: Path) -> None:
        """Zero context window should not trigger consolidation."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend_with_response(
                _make_tool_response("[2026-01-01] Entry.", "# Memory\nTest.")
            ),
            sessions=SessionManager(tmp_path),
            context_window_tokens=0,  # Zero!
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        session = Session(key="test:zero-window")
        session.messages = _make_messages_with_turns(20)

        await consolidator.maybe_consolidate_by_tokens(session)

        # Should not consolidate
        assert session.last_consolidated == 0

    @pytest.mark.asyncio
    async def test_negative_token_estimate(self, tmp_path: Path) -> None:
        """Negative token estimate should be handled gracefully."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend_with_response(
                _make_tool_response("[2026-01-01] Entry.", "# Memory\nTest.")
            ),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        session = Session(key="test:negative-tokens")
        session.messages = _make_messages_with_turns(20)

        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(-100, 'mock')  # Negative!
        ):
            await consolidator.maybe_consolidate_by_tokens(session)

        # Should not consolidate with negative estimate
        assert session.last_consolidated == 0


class TestHistoryEntryValidation:
    """Test validation of history_entry field."""

    @pytest.mark.asyncio
    async def test_history_entry_with_special_characters(self, tmp_path: Path) -> None:
        """History entry with special characters should be stored correctly."""
        store = MemoryStore(tmp_path)

        special_entry = "[2026-01-01 12:00] Test with émojis 🎉 and special chars: \n\t"

        backend = _make_mock_backend_with_response(
            _make_tool_response(special_entry, "# Memory\nTest")
        )

        await store.consolidate(
            messages=[{"role": "user", "content": "Test", "timestamp": "2026-01-01T00:00:00"}],
            backend=backend,
        )

        history = store.history_file.read_text(encoding="utf-8")
        assert "émojis 🎉" in history

    @pytest.mark.asyncio
    async def test_history_entry_missing_timestamp(self, tmp_path: Path) -> None:
        """History entry without timestamp prefix should still be stored."""
        store = MemoryStore(tmp_path)

        # Entry without [YYYY-MM-DD HH:MM] prefix
        backend = _make_mock_backend_with_response(
            _make_tool_response("Just a plain entry without timestamp", "# Memory\nTest")
        )

        result = await store.consolidate(
            messages=[{"role": "user", "content": "Test", "timestamp": "2026-01-01T00:00:00"}],
            backend=backend,
        )

        # Should still succeed (LLM should include timestamp, but we accept without)
        assert result is True
        history = store.history_file.read_text(encoding="utf-8")
        assert "Just a plain entry" in history


class TestMessageFormatting:
    """Test _format_messages edge cases."""

    def test_format_empty_content(self) -> None:
        """Messages with empty content should be skipped."""
        messages = [
            {"role": "user", "content": "", "timestamp": "2026-01-01T00:00:00"},
            {"role": "assistant", "content": "Response", "timestamp": "2026-01-01T00:01:00"},
        ]
        formatted = MemoryStore._format_messages(messages)
        # Only assistant message should appear
        assert "USER" not in formatted
        assert "ASSISTANT" in formatted

    def test_format_missing_timestamp(self) -> None:
        """Messages without timestamp should use placeholder."""
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        formatted = MemoryStore._format_messages(messages)
        assert "[?" in formatted

    def test_format_tool_calls(self) -> None:
        """Messages with tools_used should show tool names."""
        # Note: _format_messages checks 'tools_used' field, not 'tool_calls'
        messages = [
            {
                "role": "assistant",
                "content": "I'll help",
                "timestamp": "2026-01-01T00:00:00",
                "tools_used": ["Read", "Write"],
            },
        ]
        formatted = MemoryStore._format_messages(messages)
        assert "Read" in formatted
        assert "Write" in formatted

    def test_format_with_tool_calls_field(self) -> None:
        """Messages with tool_calls should now show tool names.

        This was fixed to include tool_calls information in formatted output.
        """
        messages = [
            {
                "role": "assistant",
                "content": "I'll help",
                "timestamp": "2026-01-01T00:00:00",
                "tool_calls": [
                    {"id": "tc1", "name": "Read", "arguments": {}},
                ]
            },
        ]
        formatted = MemoryStore._format_messages(messages)
        # Now tool_calls are shown
        assert "ASSISTANT" in formatted
        assert "Read" in formatted

    def test_format_only_tool_calls_no_content(self) -> None:
        """Messages with only tool_calls (no content) should not be skipped."""
        messages = [
            {
                "role": "assistant",
                "content": None,  # No content
                "timestamp": "2026-01-01T00:00:00",
                "tool_calls": [
                    {"id": "tc1", "name": "Read", "arguments": {"file": "test.py"}},
                    {"id": "tc2", "name": "Write", "arguments": {"content": "test"}},
                ]
            },
        ]
        formatted = MemoryStore._format_messages(messages)
        # Should NOT skip this message
        assert "ASSISTANT" in formatted
        assert "Read" in formatted
        assert "Write" in formatted
        # Should show indicator of tool calls
        assert "tool calls" in formatted.lower()

    def test_format_mixed_content_and_tool_calls(self) -> None:
        """Messages with both content and tool_calls should show both."""
        messages = [
            {
                "role": "assistant",
                "content": "Let me read the file.",
                "timestamp": "2026-01-01T00:00:00",
                "tool_calls": [
                    {"id": "tc1", "name": "Read", "arguments": {}},
                ]
            },
        ]
        formatted = MemoryStore._format_messages(messages)
        assert "ASSISTANT" in formatted
        assert "Let me read the file" in formatted
        assert "Read" in formatted

    def test_format_tool_result(self) -> None:
        """Tool result messages should show tool name."""
        messages = [
            {"role": "tool", "name": "Read", "content": "file contents", "timestamp": "2026-01-01T00:00:00"},
        ]
        formatted = MemoryStore._format_messages(messages)
        assert "TOOL" in formatted

    def test_format_very_long_content(self) -> None:
        """Very long content should be included (not truncated)."""
        long_content = "x" * 10000
        messages = [
            {"role": "user", "content": long_content, "timestamp": "2026-01-01T00:00:00"},
        ]
        formatted = MemoryStore._format_messages(messages)
        assert len(formatted) > 10000

    def test_format_empty_tool_calls_list(self) -> None:
        """Messages with empty tool_calls list should only show content."""
        messages = [
            {
                "role": "assistant",
                "content": "Hello",
                "timestamp": "2026-01-01T00:00:00",
                "tool_calls": [],  # Empty list
            },
        ]
        formatted = MemoryStore._format_messages(messages)
        assert "ASSISTANT" in formatted
        assert "Hello" in formatted
        # Should not show empty tool list
        assert "[tools:]" not in formatted

    def test_format_message_with_no_content_no_tools(self) -> None:
        """Messages with neither content nor tool_calls should be skipped."""
        messages = [
            {
                "role": "assistant",
                "content": None,
                "timestamp": "2026-01-01T00:00:00",
                # No tool_calls
            },
        ]
        formatted = MemoryStore._format_messages(messages)
        # Should be skipped - empty result or just timestamp
        assert formatted == "" or "?" in formatted


class TestConsolidatorInit:
    """Test MemoryConsolidator initialization."""

    def test_constants_have_correct_values(self) -> None:
        """Verify constants have expected values."""
        assert MemoryConsolidator.TRIGGER_RATIO == 0.7
        assert MemoryConsolidator.MIN_RESERVE_TURNS == 5
        assert MemoryConsolidator.MIN_RESERVE_TOKENS == 5000
        assert MemoryConsolidator._MAX_CONSOLIDATION_ROUNDS == 5

    def test_lock_created_on_demand(self, tmp_path: Path) -> None:
        """Lock should be created when first accessed."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=MagicMock(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        assert "test:session" not in consolidator._locks
        lock = consolidator.get_lock("test:session")
        assert "test:session" in consolidator._locks
        assert lock is not None


class TestConsolidationWithExistingMemory:
    """Test consolidation when memory file already exists."""

    @pytest.mark.asyncio
    async def test_consolidation_preserves_existing_facts(self, tmp_path: Path) -> None:
        """Existing memory facts should be preserved in the prompt."""
        store = MemoryStore(tmp_path)

        existing = """# Memory

## User Preferences
- Prefers dark mode
- Uses Python

## Project Info
- Project name: TestProject
"""
        store.write_long_term(existing)

        backend = _make_mock_backend_with_response(
            _make_tool_response("[2026-01-01] New entry.", existing + "\n- New fact")
        )

        await store.consolidate(
            messages=[{"role": "user", "content": "Test", "timestamp": "2026-01-01T00:00:00"}],
            backend=backend,
        )

        # Verify the prompt included existing memory
        call_args = backend.call_for_consolidation.call_args
        messages = call_args.kwargs.get('messages') or call_args.args[0]
        user_msg = next((m for m in messages if m['role'] == 'user'), None)

        assert "dark mode" in user_msg['content']
        assert "TestProject" in user_msg['content']

    @pytest.mark.asyncio
    async def test_consolidation_with_corrupted_memory_file(self, tmp_path: Path) -> None:
        """Should handle corrupted memory file gracefully."""
        store = MemoryStore(tmp_path)

        # Write some corrupted content
        store.memory_file.write_bytes(b'\xff\xfe Invalid UTF-8 \x00\x01')

        backend = _make_mock_backend_with_response(
            _make_tool_response("[2026-01-01] Entry.", "# Memory\nNew start")
        )

        # This might fail or succeed depending on error handling
        try:
            result = await store.consolidate(
                messages=[{"role": "user", "content": "Test", "timestamp": "2026-01-01T00:00:00"}],
                backend=backend,
            )
            # If it succeeds, that's fine
            assert result in (True, False)
        except UnicodeDecodeError:
            # If it fails with encoding error, that's a bug to fix
            pytest.fail("Should handle corrupted memory file without UnicodeDecodeError")
