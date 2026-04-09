"""Test consolidation boundary protection.

Phase 2: Verify minimum reserve boundaries for memory consolidation.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.memory.store import MemoryConsolidator
from xbot.platform.providers.base import LLMResponse, ToolCallRequest
from xbot.runtime.session.conversation_store import ConversationSession, ConversationStore


def _make_messages_with_turns(turn_count: int) -> list[dict]:
    """Create messages with alternating user/assistant pairs.

    Each "turn" consists of a user message followed by an assistant response.
    """
    messages = []
    for i in range(turn_count):
        # User message
        messages.append({
            "role": "user",
            "content": f"User message {i}" + "x" * 200,  # ~100 tokens
            "timestamp": f"2026-01-01T{i:02d}:00:00",
        })
        # Assistant response
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


def _make_mock_backend():
    """Create a mock backend with call_for_consolidation method."""
    backend = MagicMock()
    backend.call_for_consolidation = AsyncMock(
        return_value=_make_tool_response(
            "[2026-01-01] Conversation archived.",
            "# Memory\nTest memory."
        )
    )
    return backend


class TestConsolidationBoundary:
    """Test that consolidation respects minimum reserve boundaries."""

    @pytest.mark.asyncio
    async def test_reserve_last_5_turns(self, tmp_path: Path) -> None:
        """Should always reserve at least 5 user-assistant turns.

        MIN_RESERVE_TURNS = 5 means the last 5 turns (10 messages)
        should never be consolidated.
        """
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=ConversationStore(tmp_path),
            context_window_tokens=10_000,  # Small to force consolidation
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # 20 turns (40 messages), expect at most 15 turns consolidated
        session = ConversationSession(key="test:reserve-turns")
        session.messages = _make_messages_with_turns(20)  # 40 messages total

        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(9_000, 'mock')  # Above 70% threshold
        ):
            await consolidator.maybe_consolidate_by_tokens(session)

        # Should consolidate at most 15 turns (30 messages)
        # Reserve 5 turns (10 messages) = messages 30-39
        max_consolidated = 30  # 40 - 10 = 30
        assert session.last_consolidated <= max_consolidated, \
            f"Expected at most {max_consolidated} messages consolidated, " \
            f"got {session.last_consolidated}. Should reserve last 5 turns."

    @pytest.mark.asyncio
    async def test_reserve_minimum_tokens(self, tmp_path: Path) -> None:
        """Should reserve at least MIN_RESERVE_TOKENS worth of context.

        This test verifies that consolidation doesn't remove too much context.
        """
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=ConversationStore(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # 50 turns (100 messages)
        session = ConversationSession(key="test:reserve-tokens")
        session.messages = _make_messages_with_turns(50)

        # Simulate high token usage that triggers consolidation
        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(9_500, 'mock')  # Above 70% threshold
        ):
            await consolidator.maybe_consolidate_by_tokens(session)

        # Verify some context remains (not all messages consolidated)
        remaining_messages = len(session.messages) - session.last_consolidated
        assert remaining_messages >= 10, \
            f"Expected at least 10 messages remaining, got {remaining_messages}"

    @pytest.mark.asyncio
    async def test_no_consolidation_when_conversation_too_short(self, tmp_path: Path) -> None:
        """Should not consolidate if conversation is shorter than reserve requirement.

        If the conversation only has 3 turns and MIN_RESERVE_TURNS is 5,
        consolidation should not happen.
        """
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=ConversationStore(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # Only 3 turns (6 messages), less than MIN_RESERVE_TURNS (5)
        session = ConversationSession(key="test:short-conversation")
        session.messages = _make_messages_with_turns(3)

        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(9_000, 'mock')  # Above threshold
        ):
            await consolidator.maybe_consolidate_by_tokens(session)

        # Should not consolidate - conversation too short to reserve 5 turns
        assert session.last_consolidated == 0, \
            "Should not consolidate conversation shorter than MIN_RESERVE_TURNS"

    @pytest.mark.asyncio
    async def test_boundary_at_user_turn(self, tmp_path: Path) -> None:
        """Consolidation boundary should always be at user turn start.

        Even indices (0, 2, 4, ...) are user messages.
        Odd indices (1, 3, 5, ...) are assistant messages.
        The boundary should always be at an even index.
        """
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=ConversationStore(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        session = ConversationSession(key="test:user-boundary")
        session.messages = _make_messages_with_turns(15)

        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(8_000, 'mock')
        ):
            await consolidator.maybe_consolidate_by_tokens(session)

        # last_consolidated should point to a user message (even index)
        if session.last_consolidated > 0:
            assert session.last_consolidated % 2 == 0, \
                f"Expected boundary at user message (even index), " \
                f"got index {session.last_consolidated}"

    @pytest.mark.asyncio
    async def test_exact_min_reserve_boundary(self, tmp_path: Path) -> None:
        """Test consolidation when conversation is exactly MIN_RESERVE_TURNS + 1.

        Should consolidate exactly 1 turn.
        """
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=ConversationStore(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # 6 turns (12 messages), can consolidate at most 1 turn
        # MIN_RESERVE_TURNS = 5, so reserve last 10 messages (5 turns)
        session = ConversationSession(key="test:exact-boundary")
        session.messages = _make_messages_with_turns(6)

        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(8_000, 'mock')
        ):
            await consolidator.maybe_consolidate_by_tokens(session)

        # Can consolidate at most 1 turn (2 messages)
        # last_consolidated should be 0 or 2
        assert session.last_consolidated <= 2, \
            f"Expected at most 2 messages consolidated, got {session.last_consolidated}"

    @pytest.mark.asyncio
    async def test_multiple_rounds_respect_reserve(self, tmp_path: Path) -> None:
        """Multiple consolidation rounds should still respect reserve.

        Each consolidation round should stop at the reserve boundary.
        """
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=ConversationStore(tmp_path),
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # 30 turns (60 messages)
        session = ConversationSession(key="test:multi-round-reserve")
        session.messages = _make_messages_with_turns(30)

        # Simulate multiple rounds of consolidation
        call_count = [0]
        estimates = [9_000, 8_000, 7_500, 5_000]  # Decreasing each round

        def mock_estimate(*args, **kwargs):
            idx = min(call_count[0], len(estimates) - 1)
            call_count[0] += 1
            return (estimates[idx], 'mock')

        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            side_effect=mock_estimate
        ):
            await consolidator.maybe_consolidate_by_tokens(session)

        # After all rounds, should still reserve last 5 turns (10 messages)
        remaining = len(session.messages) - session.last_consolidated
        assert remaining >= 10, \
            f"After multiple rounds, expected >= 10 messages remaining, got {remaining}"


class TestBoundaryConstants:
    """Test that boundary constants are properly defined."""

    def test_min_reserve_turns_defined(self) -> None:
        """MIN_RESERVE_TURNS should be defined on MemoryConsolidator."""
        assert hasattr(MemoryConsolidator, 'MIN_RESERVE_TURNS'), \
            "MemoryConsolidator should have MIN_RESERVE_TURNS constant"

        value = MemoryConsolidator.MIN_RESERVE_TURNS
        assert isinstance(value, int), "MIN_RESERVE_TURNS should be an integer"
        assert value > 0, "MIN_RESERVE_TURNS should be positive"
        assert value >= 3, "MIN_RESERVE_TURNS should be at least 3 turns"

    def test_min_reserve_tokens_defined(self) -> None:
        """MIN_RESERVE_TOKENS should be defined on MemoryConsolidator."""
        assert hasattr(MemoryConsolidator, 'MIN_RESERVE_TOKENS'), \
            "MemoryConsolidator should have MIN_RESERVE_TOKENS constant"

        value = MemoryConsolidator.MIN_RESERVE_TOKENS
        assert isinstance(value, int), "MIN_RESERVE_TOKENS should be an integer"
        assert value > 0, "MIN_RESERVE_TOKENS should be positive"
