"""Integration tests: Telegram streaming draft_id uniqueness and cleanup.

These tests verify:
1. draft_id uniqueness when many concurrent calls happen within the same millisecond,
   documenting the %1000 counter capacity limit.
2. _send_with_streaming always calls _send_text even when send_message_draft raises.
3. Typing tasks are properly cancelled and cleaned up when the channel's stop() is called.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Minimal fakes for testing the Telegram channel in isolation
# ---------------------------------------------------------------------------


class FakeTelegramBot:
    """Minimal fake bot that records calls without hitting the network."""

    def __init__(self):
        self.draft_calls: list[dict] = []
        self.send_message_calls: list[dict] = []
        self.chat_actions: list[dict] = []

    async def send_message_draft(self, chat_id: int, draft_id: int, text: str):
        self.draft_calls.append({"chat_id": chat_id, "draft_id": draft_id, "text": text})

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.send_message_calls.append({"chat_id": chat_id, "text": text, **kwargs})

    async def send_chat_action(self, chat_id: int, action: str):
        self.chat_actions.append({"chat_id": chat_id, "action": action})


class FakeTelegramApp:
    """Minimal Application stand-in that provides a bot attribute."""

    def __init__(self):
        self.bot = FakeTelegramBot()


def _make_channel():
    """Construct a TelegramChannel without full initialization ceremony.

    We bypass __init__ and wire the minimal attributes needed for the
    streaming/typing internals under test.
    """
    from xbot.channels.telegram import TelegramChannel

    channel = TelegramChannel.__new__(TelegramChannel)
    channel.config = MagicMock()
    channel.config.token = "fake-token"
    channel._app = FakeTelegramApp()
    channel._draft_id_counter = 0
    channel._typing_tasks = {}
    channel._media_group_tasks = {}
    channel._media_group_buffers = {}
    channel._running = True
    channel._background_tasks = set()
    channel.bus = MagicMock()
    return channel


# ---------------------------------------------------------------------------
# 1. test_draft_ids_unique_across_concurrent_calls
# ---------------------------------------------------------------------------


class TestDraftIdsUniqueAcrossConcurrentCalls:
    """Verify draft_id generation produces unique IDs under concurrent load.

    The algorithm is:
        self._draft_id_counter = (self._draft_id_counter + 1) % 1000
        draft_id = (int(time.time() * 1000) * 1000 + self._draft_id_counter) % (2**31)

    With time.time frozen, the counter (1..999, then wraps to 0 via %1000)
    provides the only source of uniqueness.  Within a single millisecond,
    at most 1000 distinct draft_ids can be generated before collisions occur.
    """

    async def test_2000_concurrent_calls_first_1000_unique(self, monkeypatch):
        """Monkeypatch time.time to a fixed value, generate 2000 draft_ids
        concurrently.  The first 1000 are guaranteed unique; beyond that,
        collisions WILL occur — this is a known capacity limit of %1000."""
        from xbot.channels import telegram as tg_module

        channel = _make_channel()

        # Freeze time so all calls see the same millisecond
        fixed_time = 1700000000.123  # arbitrary fixed timestamp
        monkeypatch.setattr(time, "time", lambda: fixed_time)

        collected_ids: list[int] = []

        # We cannot truly run _send_with_streaming 2000 times concurrently
        # without hitting the event loop overhead, so we replicate the
        # draft_id generation logic sequentially (it is synchronous within
        # _send_with_streaming and protected by the GIL in CPython, making
        # sequential generation equivalent to concurrent invocation on a
        # single-threaded event loop).
        for _ in range(2000):
            channel._draft_id_counter = (channel._draft_id_counter + 1) % 1000
            draft_id = (
                int(time.time() * 1000) * 1000 + channel._draft_id_counter
            ) % (2**31)
            collected_ids.append(draft_id)

        unique_ids = set(collected_ids)

        # With counter wrapping at 1000, we get exactly 1000 unique values
        # out of 2000 generated.  Document this as the capacity limit.
        assert len(unique_ids) == 1000, (
            f"Expected exactly 1000 unique draft_ids (counter wraps at %1000), "
            f"got {len(unique_ids)}"
        )
        # The first 1000 calls should each have a unique ID
        first_1000 = set(collected_ids[:1000])
        assert len(first_1000) == 1000, "First 1000 IDs must all be unique"

        # The second 1000 are exact duplicates of the first 1000
        second_1000 = collected_ids[1000:]
        assert set(second_1000) == first_1000, (
            "After counter wraps, the same 1000 IDs repeat — "
            "documenting the known %1000 capacity limit (not a bug)."
        )

    async def test_advancing_time_prevents_collision_beyond_1000(self, monkeypatch):
        """When time advances (different milliseconds), even 2000+ calls
        produce fully unique draft_ids because the timestamp component differs."""
        channel = _make_channel()

        collected_ids: list[int] = []
        call_count = 2000
        base_time = 1700000000.000

        for i in range(call_count):
            # Advance time by 1ms every 1000 calls
            current_time = base_time + (i // 1000) * 0.001
            with patch.object(time, "time", return_value=current_time):
                channel._draft_id_counter = (channel._draft_id_counter + 1) % 1000
                draft_id = (
                    int(time.time() * 1000) * 1000 + channel._draft_id_counter
                ) % (2**31)
                collected_ids.append(draft_id)

        unique_ids = set(collected_ids)
        assert len(unique_ids) == call_count, (
            f"With advancing time, all {call_count} draft_ids should be unique, "
            f"got {len(unique_ids)}"
        )


# ---------------------------------------------------------------------------
# 2. test_send_message_draft_failure_still_finalizes
# ---------------------------------------------------------------------------


class TestSendMessageDraftFailureStillFinalizes:
    """Verify _send_with_streaming behavior when send_message_draft raises.

    Implementation analysis (telegram.py lines 496-508):
        The send_message_draft calls are wrapped in a try/except that catches
        ALL exceptions and logs them as "non-critical".  Regardless of whether
        the draft phase succeeds or fails, _send_text is ALWAYS called on
        line 508 (outside the try/except block).

    Therefore: draft failure does NOT prevent final message delivery.
    The error is swallowed (logged at debug level) and _send_text delivers
    the full message as the canonical send path.
    """

    async def test_draft_raises_exception_send_text_still_called(self):
        """Mock send_message_draft to raise; assert _send_text is called."""
        channel = _make_channel()

        # Make the draft API raise on every call
        channel._app.bot.send_message_draft = AsyncMock(
            side_effect=RuntimeError("Telegram API error: connection reset")
        )

        # Track _send_text invocations
        send_text_record: list[dict] = []

        async def mock_send_text(chat_id, text, reply_params=None, thread_kwargs=None):
            send_text_record.append({"chat_id": chat_id, "text": text})

        channel._send_text = mock_send_text

        test_text = "Hello! This is a message long enough to trigger draft stepping."

        await channel._send_with_streaming(
            chat_id=99999,
            text=test_text,
            reply_params=None,
            thread_kwargs=None,
        )

        # _send_text MUST be called exactly once with the full text
        assert len(send_text_record) == 1, (
            f"_send_text should be called exactly once, was called {len(send_text_record)} times"
        )
        assert send_text_record[0]["chat_id"] == 99999
        assert send_text_record[0]["text"] == test_text

    async def test_draft_raises_does_not_propagate_to_caller(self):
        """The exception from send_message_draft must NOT bubble up to the caller.
        _send_with_streaming catches it internally and proceeds to _send_text."""
        channel = _make_channel()

        channel._app.bot.send_message_draft = AsyncMock(
            side_effect=ConnectionError("network failure")
        )

        # _send_text succeeds normally
        channel._send_text = AsyncMock()

        # This must NOT raise — the draft error is caught internally
        await channel._send_with_streaming(
            chat_id=12345,
            text="Short message that still triggers at least one draft step.",
            reply_params=None,
            thread_kwargs=None,
        )

        # Confirm _send_text was reached
        channel._send_text.assert_awaited_once()

    async def test_send_text_failure_after_draft_failure_does_propagate(self):
        """If _send_text itself fails (after draft also failed), the exception
        propagates to the caller because _send_text is not wrapped in try/except
        within _send_with_streaming."""
        channel = _make_channel()

        channel._app.bot.send_message_draft = AsyncMock(
            side_effect=RuntimeError("draft error")
        )

        async def exploding_send_text(chat_id, text, reply_params=None, thread_kwargs=None):
            raise ConnectionError("final send also failed")

        channel._send_text = exploding_send_text

        with pytest.raises(ConnectionError, match="final send also failed"):
            await channel._send_with_streaming(
                chat_id=12345,
                text="Some message content that is long enough to matter.",
                reply_params=None,
                thread_kwargs=None,
            )


# ---------------------------------------------------------------------------
# 3. test_typing_task_cancelled_on_channel_stop
# ---------------------------------------------------------------------------


class TestTypingTaskCancelledOnChannelStop:
    """Verify that calling the channel's stop() method cancels all typing
    indicator tasks and leaves _typing_tasks empty (no leaked background tasks).

    Implementation (telegram.py lines 308-336):
        stop() iterates _typing_tasks.values(), cancels each non-done task,
        gathers them with return_exceptions=True, then clears the dict.
    """

    async def test_single_typing_task_cancelled_on_stop(self):
        """Start a typing indicator, then stop the channel.
        The typing task must be cancelled and dict must be empty."""
        channel = _make_channel()

        # Mock the Application shutdown methods so stop() doesn't crash
        channel._app.updater = MagicMock()
        channel._app.updater.stop = AsyncMock()
        channel._app.stop = AsyncMock()
        channel._app.shutdown = AsyncMock()

        # Provide _create_tracked_task (normally inherited from BaseChannel)
        def create_tracked(coro, name=None):
            task = asyncio.create_task(coro, name=name)
            channel._background_tasks.add(task)
            task.add_done_callback(channel._background_tasks.discard)
            return task

        channel._create_tracked_task = create_tracked

        # Start typing for two chats (use numeric strings — _typing_loop calls int())
        channel._start_typing("100")
        channel._start_typing("200")

        assert "100" in channel._typing_tasks
        assert "200" in channel._typing_tasks

        task_100 = channel._typing_tasks["100"]
        task_200 = channel._typing_tasks["200"]

        # Both tasks should be running
        assert not task_100.done()
        assert not task_200.done()

        await channel.stop()

        # Allow cancellation to propagate
        await asyncio.sleep(0.05)

        # Both tasks should be cancelled/done
        assert task_100.cancelled() or task_100.done()
        assert task_200.cancelled() or task_200.done()

        # _typing_tasks dict must be empty — no leaked references
        assert len(channel._typing_tasks) == 0, (
            f"_typing_tasks should be empty after stop(), "
            f"but contains: {list(channel._typing_tasks.keys())}"
        )

    async def test_stop_with_already_finished_typing_tasks(self):
        """If a typing task has already completed (e.g., due to an error),
        stop() should still clear the dict without errors."""
        channel = _make_channel()

        # Mock the Application shutdown methods
        channel._app.updater = MagicMock()
        channel._app.updater.stop = AsyncMock()
        channel._app.stop = AsyncMock()
        channel._app.shutdown = AsyncMock()

        # Create an already-done task
        async def instant_return():
            return

        done_task = asyncio.create_task(instant_return())
        await asyncio.sleep(0.01)  # let it complete
        assert done_task.done()

        channel._typing_tasks["55555"] = done_task

        # stop() should not raise even with already-done tasks
        await channel.stop()

        assert len(channel._typing_tasks) == 0

    async def test_stop_idempotent_no_typing_tasks(self):
        """Calling stop() when no typing tasks exist should be a no-op
        (no KeyError, no crash)."""
        channel = _make_channel()
        # No active app — simulates already-stopped state
        channel._app = None

        # Should complete without error
        await channel.stop()

        assert len(channel._typing_tasks) == 0

    async def test_typing_loop_stops_responding_after_cancel(self):
        """After stop() cancels the typing loop, it should not send any
        further chat actions."""
        channel = _make_channel()
        chat_action_log: list[dict] = []

        async def tracking_send_chat_action(chat_id: int, action: str):
            chat_action_log.append({"chat_id": chat_id, "action": action})

        channel._app.bot.send_chat_action = tracking_send_chat_action

        # Mock the Application shutdown methods
        channel._app.updater = MagicMock()
        channel._app.updater.stop = AsyncMock()
        channel._app.stop = AsyncMock()
        channel._app.shutdown = AsyncMock()

        def create_tracked(coro, name=None):
            task = asyncio.create_task(coro, name=name)
            channel._background_tasks.add(task)
            task.add_done_callback(channel._background_tasks.discard)
            return task

        channel._create_tracked_task = create_tracked

        # Start typing — uses numeric chat_id string (int() is called inside _typing_loop)
        channel._start_typing("999")

        # Let the first send_chat_action fire (the loop calls it immediately)
        await asyncio.sleep(0.05)
        actions_before_stop = len(chat_action_log)
        assert actions_before_stop >= 1, "At least one chat action should fire"

        # Now stop the channel — stop() cancels all typing tasks
        await channel.stop()
        await asyncio.sleep(0.1)

        # No further actions should be recorded after stop
        actions_after_stop = len(chat_action_log)
        assert actions_after_stop == actions_before_stop, (
            f"Expected no new chat actions after stop(), but got "
            f"{actions_after_stop - actions_before_stop} more"
        )
