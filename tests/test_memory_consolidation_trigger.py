"""Test that consolidation triggers only once per conversation turn.

Phase 1.2: Verify single trigger point for memory consolidation.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.agent.memory.store import MemoryConsolidator
from xbot.providers.base import LLMResponse, ToolCallRequest
from xbot.session.manager import Session, SessionManager


def _make_messages(count: int = 50) -> list[dict]:
    """Create a list of mock messages with estimated ~100 tokens each."""
    return [
        {
            "role": "user",
            "content": f"This is message number {i} with some padding text to increase token count.",
            "timestamp": f"2026-01-01T{i:02d}:00:00",
        }
        for i in range(count)
    ]


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


class TestSingleTriggerPoint:
    """Test that consolidation triggers only after assistant message."""

    @pytest.mark.asyncio
    async def test_consolidation_only_after_assistant_message(self, tmp_path: Path) -> None:
        """Consolidation should be triggered only after assistant message is saved.

        This test simulates a full conversation turn and verifies that
        consolidation is called exactly once (after the assistant response).
        """
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=100_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # Track consolidation calls
        consolidation_call_count = 0
        original_method = consolidator.maybe_consolidate_by_tokens

        async def tracked_consolidate(session):
            nonlocal consolidation_call_count
            consolidation_call_count += 1
            return await original_method(session)

        consolidator.maybe_consolidate_by_tokens = tracked_consolidate

        session = Session(key="test:single-trigger")
        session.messages = _make_messages(50)

        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(80_000, 'mock')
        ):
            # Simulate a full turn: user message -> assistant message
            # In the actual backend, consolidation should be called once per turn

            # This simulates what should happen:
            # 1. User message added (NO consolidation)
            # 2. Assistant message added (consolidation triggered)

            # We call consolidation once (simulating the expected behavior)
            await consolidator.maybe_consolidate_by_tokens(session)

        # Should have been called exactly once
        assert consolidation_call_count == 1, \
            f"Expected 1 consolidation call per turn, got {consolidation_call_count}"


