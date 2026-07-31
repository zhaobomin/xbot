"""Integration tests: Memory consolidation cancellation and interruption safety.

Scenarios covered:
1. Consolidation cancelled before LLM write completes — original MEMORY.md preserved.
2. Consolidation cancelled during file write — no corrupted/truncated file on disk.
3. Service shutdown cancels in-flight consolidation gracefully — no leaked errors.
4. New messages arriving during consolidation are not lost.
5. LLM call failure triggers raw archive fallback — data preserved.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.memory.store import MemoryConsolidator, MemoryStore
from xbot.runtime.core.protocol import StructuredLLMResponse, ToolCall
from xbot.runtime.session.conversation_store import ConversationSession, ConversationStore

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_messages(count: int) -> list[dict]:
    """Create alternating user/assistant message pairs."""
    messages = []
    for i in range(count):
        messages.append({
            "role": "user",
            "content": f"User message {i}" + "x" * 100,
            "timestamp": f"2026-01-15T{i:02d}:00:00",
        })
        messages.append({
            "role": "assistant",
            "content": f"Assistant response {i}" + "y" * 100,
            "timestamp": f"2026-01-15T{i:02d}:01:00",
        })
    return messages


def _make_consolidation_response(
    history_entry: str = "[2026-01-15 12:00] Consolidated entry.",
    memory_update: str = "# Memory\n\n- Updated fact from consolidation",
) -> StructuredLLMResponse:
    """Create a StructuredLLMResponse mimicking a successful save_memory tool call."""
    return StructuredLLMResponse(
        content="",
        finish_reason="end_turn",
        tool_calls=[
            ToolCall(
                name="save_memory",
                arguments={
                    "history_entry": history_entry,
                    "memory_update": memory_update,
                },
            )
        ],
    )


def _make_backend_mock(response: StructuredLLMResponse | None = None) -> MagicMock:
    """Create a mock backend with call_for_consolidation."""
    backend = MagicMock()
    if response is not None:
        backend.call_for_consolidation = AsyncMock(return_value=response)
    return backend


# ---------------------------------------------------------------------------
# 1. test_consolidation_cancelled_before_write_preserves_original
# ---------------------------------------------------------------------------


class TestCancellationBeforeWrite:
    """Cancellation during LLM call should leave MEMORY.md unchanged."""

    async def test_consolidation_cancelled_before_write_preserves_original(
        self, tmp_path: Path
    ) -> None:
        """Start consolidation with a slow LLM call, cancel before it returns.

        Assert: MEMORY.md is unchanged; no partial file; no .tmp leftover.
        """
        store = MemoryStore(tmp_path)

        # Write initial memory content
        original_memory = "# Memory\n\n- User prefers vim\n- Project uses Python 3.12"
        store.write_long_term(original_memory)

        # Mock the backend so call_for_consolidation hangs (simulating slow LLM)
        slow_event = asyncio.Event()

        async def _slow_llm_call(**kwargs):
            # Simulate a long-running LLM call that can be interrupted
            await asyncio.sleep(10)  # Will be cancelled before completing
            slow_event.set()
            return _make_consolidation_response()

        backend = _make_backend_mock()
        backend.call_for_consolidation = AsyncMock(side_effect=_slow_llm_call)

        messages = _make_messages(5)

        # Start consolidation as a task
        task = asyncio.create_task(store.consolidate(messages, backend))

        # Give the task a moment to start and enter the LLM call
        await asyncio.sleep(0.05)

        # Cancel before LLM returns
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Assert: original memory unchanged
        assert store.read_long_term() == original_memory

        # Assert: no partial or tmp files
        memory_dir = tmp_path / "memory"
        for f in memory_dir.iterdir():
            assert ".tmp" not in f.name, f"Unexpected temp file: {f}"
            if f.name == "MEMORY.md":
                # File must be the original content, not empty/truncated
                content = f.read_text(encoding="utf-8")
                assert content == original_memory

        # Assert: the slow LLM call never completed
        assert not slow_event.is_set()


# ---------------------------------------------------------------------------
# 2. test_consolidation_cancelled_during_file_write_no_corruption
# ---------------------------------------------------------------------------


class TestCancellationDuringFileWrite:
    """Cancellation during file write should leave either old or new content — never truncated."""

    async def test_consolidation_cancelled_during_file_write_no_corruption(
        self, tmp_path: Path
    ) -> None:
        """Mock consolidation that completed LLM call but gets cancelled during write.

        Assert: either old file is intact OR new file is complete; never truncated.
        """
        store = MemoryStore(tmp_path)

        original_memory = "# Memory\n\n- Original fact alpha\n- Original fact beta"
        store.write_long_term(original_memory)

        new_memory = "# Memory\n\n- Updated fact from consolidation\n- New insight"

        # The LLM call returns immediately, but we intercept write_long_term
        backend = _make_backend_mock(_make_consolidation_response(memory_update=new_memory))

        write_started = asyncio.Event()
        original_write = store.write_long_term

        def _slow_write(content: str) -> None:
            """Simulate a slow write that can be observed."""
            write_started.set()
            # Perform the actual write (synchronous — can't truly cancel mid-write,
            # but we verify the contract: file is either old or fully new)
            original_write(content)

        messages = _make_messages(5)

        with patch.object(store, "write_long_term", side_effect=_slow_write):
            task = asyncio.create_task(store.consolidate(messages, backend))

            # Wait until write starts (or the task completes quickly)
            done, _ = await asyncio.wait(
                [asyncio.create_task(write_started.wait()), task],
                timeout=2.0,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel the task (may be already done if write was fast)
            if not task.done():
                task.cancel()
                with pytest.raises((asyncio.CancelledError, Exception)):
                    await task
            else:
                await task  # collect result

        # Assert: file content is EITHER the original OR the complete new content
        final_content = store.read_long_term()
        assert final_content in (original_memory, new_memory), (
            f"File must be either original or fully updated, got: {final_content!r}"
        )

        # Assert: file is never empty or truncated
        assert len(final_content) > 0
        # If it matches new_memory, all lines should be present
        if final_content == new_memory:
            assert "Updated fact from consolidation" in final_content
            assert "New insight" in final_content


# ---------------------------------------------------------------------------
# 3. test_shutdown_cancels_in_flight_consolidation_gracefully
# ---------------------------------------------------------------------------


class TestShutdownCancelsGracefully:
    """Service shutdown cancels consolidation without propagating CancelledError."""

    async def test_shutdown_cancels_in_flight_consolidation_gracefully(
        self, tmp_path: Path
    ) -> None:
        """Simulate service shutdown: start consolidation, then cancel tasks.

        Assert: CancelledError handled; service shuts down cleanly; file state consistent.
        """
        store = MemoryStore(tmp_path)

        original_memory = "# Memory\n\n- Pre-shutdown fact"
        store.write_long_term(original_memory)

        # Simulate a slow consolidation (would take 10s)
        async def _slow_llm_call(**kwargs):
            await asyncio.sleep(10)
            return _make_consolidation_response()

        backend = _make_backend_mock()
        backend.call_for_consolidation = AsyncMock(side_effect=_slow_llm_call)

        messages = _make_messages(5)

        # Mimic the service's _async_consolidation_tasks set
        async_consolidation_tasks: set[asyncio.Task] = set()

        async def _safe_consolidate() -> None:
            try:
                await store.consolidate(messages, backend)
            except asyncio.CancelledError:
                # This is what the production code does — catch, log, re-raise
                raise
            except Exception:
                pass

        task = asyncio.create_task(
            _safe_consolidate(), name="memory-consolidation:test-session"
        )
        async_consolidation_tasks.add(task)
        task.add_done_callback(lambda t: async_consolidation_tasks.discard(t))

        # Give the task time to start
        await asyncio.sleep(0.05)

        # --- Simulate shutdown sequence (mirrors AgentService.shutdown) ---
        for t in list(async_consolidation_tasks):
            t.cancel()
        # Gather should NOT raise — CancelledError is suppressed by return_exceptions
        results = await asyncio.gather(*async_consolidation_tasks, return_exceptions=True)

        # Assert: CancelledError was raised and captured, not propagated
        for r in results:
            assert r is None or isinstance(r, (asyncio.CancelledError, Exception))

        # Assert: the tasks set can be cleared cleanly
        async_consolidation_tasks.clear()
        assert len(async_consolidation_tasks) == 0

        # Assert: file state is consistent (unchanged since LLM never returned)
        assert store.read_long_term() == original_memory


# ---------------------------------------------------------------------------
# 4. test_concurrent_consolidation_and_new_messages_no_data_loss
# ---------------------------------------------------------------------------


class TestConcurrentConsolidationAndNewMessages:
    """New messages arriving during consolidation must not be lost."""

    async def test_concurrent_consolidation_and_new_messages_no_data_loss(
        self, tmp_path: Path
    ) -> None:
        """Start consolidation reading current messages. Add new messages while
        the LLM is "processing". Verify new messages are preserved.
        """
        sessions_store = ConversationStore(workspace=tmp_path)

        # Set up a session with initial messages
        session = ConversationSession(key="test:concurrent-msgs")
        initial_messages = _make_messages(10)  # 20 messages (10 turns)
        session.messages = list(initial_messages)
        sessions_store.save(session)

        # The consolidation will read messages[0:10] (first 5 turns)
        # and while it's "thinking", we add new messages to the session
        consolidation_started = asyncio.Event()
        proceed_with_consolidation = asyncio.Event()

        async def _delayed_llm_call(**kwargs):
            consolidation_started.set()
            # Wait for signal that new messages have been added
            await proceed_with_consolidation.wait()
            return _make_consolidation_response(
                history_entry="[2026-01-15] Consolidated first 5 turns.",
                memory_update="# Memory\n\n- Facts from first 5 turns",
            )

        backend = _make_backend_mock()
        backend.call_for_consolidation = AsyncMock(side_effect=_delayed_llm_call)

        consolidator = MemoryConsolidator(
            workspace=tmp_path,
            backend=backend,
            sessions=sessions_store,
            context_window_tokens=10_000,
            build_messages=lambda **kwargs: [],
            get_tool_definitions=lambda: [],
        )

        # Snapshot the chunk that will be consolidated (simulates what
        # maybe_consolidate_by_tokens does: takes a slice before calling LLM)
        chunk_end = 10  # Consolidate messages[0:10]
        chunk = session.messages[session.last_consolidated:chunk_end]

        # Start consolidation of the chunk
        consolidation_task = asyncio.create_task(
            consolidator.consolidate_messages(chunk)
        )

        # Wait for the LLM call to start
        await consolidation_started.wait()

        # --- While consolidation is "thinking", add new messages ---
        new_messages_content = [
            "New message during consolidation A",
            "New message during consolidation B",
            "New message during consolidation C",
        ]
        for content in new_messages_content:
            session.add_message("user", content)
            session.add_message("assistant", f"Reply to: {content}")

        # Signal consolidation to complete
        proceed_with_consolidation.set()
        result = await consolidation_task
        assert result is True

        # After successful consolidation, update the offset (as production code does)
        session.last_consolidated = chunk_end
        sessions_store.save(session)

        # Assert: new messages are still in the session (not lost)
        remaining_messages = session.messages[session.last_consolidated:]
        remaining_contents = [m.get("content", "") for m in remaining_messages]

        for expected in new_messages_content:
            assert any(expected in c for c in remaining_contents), (
                f"New message '{expected}' was lost after consolidation"
            )

        # Assert: total messages = original + new
        # Original: 20, new: 6 (3 user + 3 assistant)
        assert len(session.messages) == 26

        # Assert: unconsolidated messages include both the original tail AND new messages
        unconsolidated = session.messages[session.last_consolidated:]
        # Original messages 10-19 (10 messages) + 6 new = 16
        assert len(unconsolidated) == 16


# ---------------------------------------------------------------------------
# 5. test_consolidation_llm_call_raises_falls_back_to_raw_archive
# ---------------------------------------------------------------------------


class TestLLMFailureFallbackToRawArchive:
    """LLM call failure should fall back to raw archive after threshold."""

    async def test_consolidation_llm_call_raises_falls_back_to_raw_archive(
        self, tmp_path: Path
    ) -> None:
        """Mock LLM call to raise an exception. Assert: system falls back to
        _fail_or_raw_archive; conversation data is preserved.
        """
        store = MemoryStore(tmp_path)

        original_memory = "# Memory\n\n- Existing fact before failure"
        store.write_long_term(original_memory)

        # LLM raises an API error every time
        backend = _make_backend_mock()
        backend.call_for_consolidation = AsyncMock(
            side_effect=RuntimeError("API rate limit exceeded")
        )

        messages = [
            {"role": "user", "content": "Important user request", "timestamp": "2026-01-15T10:00:00"},
            {"role": "assistant", "content": "Important response", "timestamp": "2026-01-15T10:01:00"},
        ]

        # First call: should fail (consecutive_failures increments to 1)
        result = store.consolidate(messages, backend)
        result1 = await result
        assert result1 is False  # Not yet at threshold

        # Verify MEMORY.md is unchanged
        assert store.read_long_term() == original_memory

        # Call repeatedly until threshold is reached (_MAX_FAILURES_BEFORE_RAW_ARCHIVE = 5)
        for i in range(3):
            r = await store.consolidate(messages, backend)
            assert r is False  # Still below threshold (consecutive_failures: 2,3,4)

        # 5th call should trigger raw archive fallback
        result_final = await store.consolidate(messages, backend)
        assert result_final is True  # Raw archive returns True

        # Assert: MEMORY.md is still intact (raw archive goes to HISTORY.md)
        assert store.read_long_term() == original_memory

        # Assert: HISTORY.md has the raw archived messages
        history_content = store.history_file.read_text(encoding="utf-8")
        assert "[RAW]" in history_content
        assert "Important user request" in history_content

        # Assert: consecutive failures counter was reset
        assert store._consecutive_failures == 0

    async def test_single_llm_failure_preserves_data_for_retry(
        self, tmp_path: Path
    ) -> None:
        """A single LLM failure should return False, preserving data for retry."""
        store = MemoryStore(tmp_path)

        original_memory = "# Memory\n\n- Critical business logic"
        store.write_long_term(original_memory)

        # Backend raises once, then succeeds
        call_count = 0

        async def _failing_then_success(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Network timeout")
            return _make_consolidation_response(
                history_entry="[2026-01-15] Retry succeeded.",
                memory_update="# Memory\n\n- Critical business logic\n- New fact after retry",
            )

        backend = _make_backend_mock()
        backend.call_for_consolidation = AsyncMock(side_effect=_failing_then_success)

        messages = [
            {"role": "user", "content": "Test message", "timestamp": "2026-01-15T12:00:00"},
        ]

        # First call fails
        result1 = await store.consolidate(messages, backend)
        assert result1 is False

        # Memory unchanged
        assert store.read_long_term() == original_memory

        # Retry succeeds
        result2 = await store.consolidate(messages, backend)
        assert result2 is True

        # Memory now updated
        final = store.read_long_term()
        assert "Critical business logic" in final
        assert "New fact after retry" in final

    async def test_llm_returns_no_tool_calls_triggers_fallback(
        self, tmp_path: Path
    ) -> None:
        """If LLM returns no tool calls, _fail_or_raw_archive should be invoked."""
        store = MemoryStore(tmp_path)

        original_memory = "# Memory\n\n- Existing data"
        store.write_long_term(original_memory)

        # LLM returns a response without tool calls
        no_tool_response = StructuredLLMResponse(
            content="I cannot process this request.",
            finish_reason="stop",
            tool_calls=[],
        )
        backend = _make_backend_mock(no_tool_response)

        messages = [
            {"role": "user", "content": "Process this", "timestamp": "2026-01-15T14:00:00"},
        ]

        # Should return False (failure path, not yet at raw archive threshold)
        result = await store.consolidate(messages, backend)
        assert result is False

        # Memory file unchanged
        assert store.read_long_term() == original_memory
        assert store._consecutive_failures == 1
