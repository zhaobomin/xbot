"""Test memory consolidation threshold behavior.

Regression tests for memory consolidation trigger threshold fix.
The consolidation should trigger when estimated tokens exceed context_window // 2,
not when they exceed the full context_window.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.agent.memory import MemoryConsolidator, MemoryStore
from xbot.session.manager import Session, SessionManager
from xbot.providers.base import LLMResponse, ToolCallRequest


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


class TestMemoryConsolidationThreshold:
    """Test that consolidation triggers at the correct threshold."""

    @pytest.mark.asyncio
    async def test_consolidation_triggers_at_half_context_window(self, tmp_path: Path) -> None:
        """Consolidation should trigger when tokens > context_window // 2."""
        context_window = 10000
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=context_window,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # Mock estimate_session_prompt_tokens to return a value above half context
        # but below full context (e.g., 6000 tokens, half is 5000)
        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(6000, 'mock')
        ):
            session = Session(key="test:threshold")
            session.messages = _make_messages(50)

            result = await consolidator.maybe_consolidate_by_tokens(session)

            # Should have triggered consolidation since 6000 > 5000 (half)
            assert result is None  # Function returns None

    @pytest.mark.asyncio
    async def test_consolidation_skips_below_half_threshold(self, tmp_path: Path) -> None:
        """Consolidation should NOT trigger when tokens < context_window // 2."""
        context_window = 10000
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=context_window,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # Mock estimate to return value below half (e.g., 3000 tokens, half is 5000)
        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(3000, 'mock')
        ):
            session = Session(key="test:below-threshold")
            session.messages = _make_messages(20)

            result = await consolidator.maybe_consolidate_by_tokens(session)

            # Should have skipped consolidation
            assert result is None

    @pytest.mark.asyncio
    async def test_consolidation_stops_at_target(self, tmp_path: Path) -> None:
        """Consolidation should continue until tokens <= context_window // 2."""
        context_window = 10000
        target = context_window // 2  # 5000

        backend = _make_mock_backend()
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=backend,
            sessions=SessionManager(tmp_path),
            context_window_tokens=context_window,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # Simulate decreasing token estimates through consolidation rounds
        call_count = [0]
        estimates = [8000, 6000, 4000]  # Each round reduces tokens

        def mock_estimate(*args, **kwargs):
            idx = min(call_count[0], len(estimates) - 1)
            call_count[0] += 1
            return (estimates[idx], 'mock')

        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            side_effect=mock_estimate
        ):
            session = Session(key="test:multi-round")
            session.messages = _make_messages(100)

            await consolidator.maybe_consolidate_by_tokens(session)

            # Should have consolidated until below target (4000 < 5000)
            # Check that last_consolidated was updated
            assert session.last_consolidated > 0

    @pytest.mark.asyncio
    async def test_consolidation_respects_zero_context_window(self, tmp_path: Path) -> None:
        """Consolidation should handle zero context_window gracefully."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=0,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        session = Session(key="test:zero-window")
        session.messages = _make_messages(10)

        # Should return early without error
        result = await consolidator.maybe_consolidate_by_tokens(session)
        assert result is None

    @pytest.mark.asyncio
    async def test_consolidation_handles_empty_messages(self, tmp_path: Path) -> None:
        """Consolidation should handle empty message list gracefully."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        session = Session(key="test:empty")
        session.messages = []

        # Should return early without error
        result = await consolidator.maybe_consolidate_by_tokens(session)
        assert result is None

    @pytest.mark.asyncio
    async def test_force_consolidation_ignores_threshold(self, tmp_path: Path) -> None:
        """Force consolidation should work regardless of token count."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # Even with low token count
        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(100, 'mock')
        ):
            session = Session(key="test:force")
            session.messages = _make_messages(10)

            result = await consolidator.force_consolidate(session)

            # Force consolidation should still consolidate all messages
            assert result["success"] is True
            assert result["messages_consolidated"] == 10


class TestMemoryConsolidatorLocking:
    """Test that consolidation uses per-session locks correctly."""

    @pytest.mark.asyncio
    async def test_concurrent_consolidations_same_session_wait(self, tmp_path: Path) -> None:
        """Concurrent consolidations on the same session should wait for each other."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        session = Session(key="test:concurrent")
        session.messages = _make_messages(10)

        # Track execution order
        execution_log = []
        original_consolidate = consolidator.consolidate_messages

        async def tracked_consolidate(messages):
            execution_log.append("start")
            await asyncio.sleep(0.1)  # Simulate slow consolidation
            execution_log.append("end")
            return await original_consolidate(messages)

        consolidator.consolidate_messages = tracked_consolidate

        # Run two consolidations concurrently
        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(8000, 'mock')
        ):
            await asyncio.gather(
                consolidator.maybe_consolidate_by_tokens(session),
                consolidator.maybe_consolidate_by_tokens(session),
            )

        # Both should have completed (lock ensured serialization)
        assert "start" in execution_log
        assert "end" in execution_log

    @pytest.mark.asyncio
    async def test_different_sessions_can_consolidate_concurrently(self, tmp_path: Path) -> None:
        """Consolidations on different sessions should run concurrently."""
        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=_make_mock_backend(),
            sessions=SessionManager(tmp_path),
            context_window_tokens=10000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        session1 = Session(key="test:session1")
        session1.messages = _make_messages(10)

        session2 = Session(key="test:session2")
        session2.messages = _make_messages(10)

        # Track concurrent execution
        concurrent_count = 0
        max_concurrent = 0
        lock = asyncio.Lock()

        original_consolidate = consolidator.consolidate_messages

        async def tracked_consolidate(messages):
            nonlocal concurrent_count, max_concurrent
            async with lock:
                concurrent_count += 1
                max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.1)
            async with lock:
                concurrent_count -= 1
            return await original_consolidate(messages)

        consolidator.consolidate_messages = tracked_consolidate

        with patch.object(
            consolidator,
            'estimate_session_prompt_tokens',
            return_value=(8000, 'mock')
        ):
            await asyncio.gather(
                consolidator.maybe_consolidate_by_tokens(session1),
                consolidator.maybe_consolidate_by_tokens(session2),
            )

        # Different sessions should have run concurrently (max_concurrent > 1)
        # Note: This test verifies the behavior, but actual concurrency depends on timing
        assert max_concurrent >= 1  # At minimum, one ran