"""Integration tests: Channel send failure resilience.

These tests verify that the outbound dispatch loop degrades gracefully
when one or more channels fail to send messages.  Key expectations:

- A single send failure does NOT crash or block the dispatch loop.
- Repeated failures do NOT prevent bus consumption.
- A hanging send (no timeout in _send_with_channel) IS a hang risk (documented).
- During streaming, partial delivery failures still result in final text delivery.
- One failing channel does NOT affect co-registered healthy channels.
- The outbound queue is bounded by asyncio.Queue(maxsize=1000).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from xbot.channels.base import BaseChannel
from xbot.channels.manager import ChannelManager
from xbot.platform.bus.events import OutboundMessage
from xbot.platform.bus.queue import MessageBus

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _cancel_task(task: asyncio.Task) -> None:
    """Cancel a dispatch task and wait for it to finish.

    The dispatch loop catches CancelledError internally (break), so the task
    may complete normally without re-raising.  We handle both cases.
    """
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class FailingChannel(BaseChannel):
    """A channel whose send() always raises."""

    name = "failing"
    display_name = "Failing"

    def __init__(self, config, bus):
        super().__init__(config, bus)
        self.send_attempts: list[OutboundMessage] = []

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        self.send_attempts.append(msg)
        raise RuntimeError("Simulated channel send failure")


class HangingChannel(BaseChannel):
    """A channel whose send() hangs forever."""

    name = "hanging"
    display_name = "Hanging"

    def __init__(self, config, bus):
        super().__init__(config, bus)
        self.send_started = asyncio.Event()

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        self.send_started.set()
        await asyncio.Event().wait()  # hang forever


class HealthyChannel(BaseChannel):
    """A channel whose send() always succeeds and records calls."""

    name = "healthy"
    display_name = "Healthy"

    def __init__(self, config, bus):
        super().__init__(config, bus)
        self.sent_messages: list[OutboundMessage] = []

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        self.sent_messages.append(msg)


class StreamingChannel(BaseChannel):
    """A channel that records draft/final sends, failing on a configurable draft index."""

    name = "streaming"
    display_name = "Streaming"

    def __init__(self, config, bus, *, fail_on_draft_index: int = -1):
        super().__init__(config, bus)
        self.draft_sends: list[OutboundMessage] = []
        self.final_sends: list[OutboundMessage] = []
        self._fail_on_draft_index = fail_on_draft_index

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        is_progress = msg.metadata.get("_progress", False)
        if is_progress:
            self.draft_sends.append(msg)
            if len(self.draft_sends) == self._fail_on_draft_index:
                raise RuntimeError("Simulated draft send failure")
        else:
            self.final_sends.append(msg)


def _make_manager_with_channels(
    bus: MessageBus,
    channels: dict[str, BaseChannel],
) -> ChannelManager:
    """Create a ChannelManager with pre-injected channels, bypassing _init_channels."""
    config = MagicMock()
    config.channels = MagicMock()
    config.channels.send_progress = True
    config.channels.send_tool_hints = True
    config.channels.send_usage_summary = True
    config.providers = MagicMock()
    config.providers.groq = MagicMock()
    config.providers.groq.api_key = None

    # Bypass normal discovery by constructing without __init__
    manager = ChannelManager.__new__(ChannelManager)
    manager.config = config
    manager.bus = bus
    manager.channels = dict(channels)
    manager._dispatch_task = None

    from xbot.runtime.core.task_supervisor import ServiceTaskRegistry

    manager._task_registry = ServiceTaskRegistry(
        error_reporter=ChannelManager._report_task_error
    )
    return manager


def _make_outbound(channel: str, content: str, **metadata_kw) -> OutboundMessage:
    return OutboundMessage(
        channel=channel,
        chat_id="chat1",
        content=content,
        metadata=metadata_kw,
    )


# ---------------------------------------------------------------------------
# 1. test_single_send_failure_logs_and_continues
# ---------------------------------------------------------------------------


class TestSingleSendFailureLogsAndContinues:
    """A channel's send() raises an exception for one message.

    Assert:
    - The error is caught and NOT propagated to crash the dispatch loop.
    - Subsequent messages to the same or other channels still go through.
    """

    async def test_single_send_failure_logs_and_continues(self, caplog):
        bus = MessageBus()
        failing = FailingChannel(MagicMock(), bus)
        healthy = HealthyChannel(MagicMock(), bus)
        manager = _make_manager_with_channels(bus, {"failing": failing, "healthy": healthy})

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            # First message to the failing channel
            await bus.publish_outbound(_make_outbound("failing", "msg1"))
            await asyncio.sleep(0.05)

            # Second message to the healthy channel
            await bus.publish_outbound(_make_outbound("healthy", "msg2"))
            await asyncio.sleep(0.05)

            # Third message to the failing channel again
            await bus.publish_outbound(_make_outbound("failing", "msg3"))
            await asyncio.sleep(0.05)

            # Fourth message to the healthy channel
            await bus.publish_outbound(_make_outbound("healthy", "msg4"))
            await asyncio.sleep(0.05)
        finally:
            await _cancel_task(task)

        # The dispatch loop did NOT die after the first failure
        assert len(failing.send_attempts) == 2
        assert len(healthy.sent_messages) == 2
        assert healthy.sent_messages[0].content == "msg2"
        assert healthy.sent_messages[1].content == "msg4"

    async def test_error_is_logged_not_propagated(self, caplog):
        bus = MessageBus()
        failing = FailingChannel(MagicMock(), bus)
        manager = _make_manager_with_channels(bus, {"failing": failing})

        with caplog.at_level(logging.ERROR):
            task = asyncio.create_task(manager._dispatch_outbound())
            try:
                await bus.publish_outbound(_make_outbound("failing", "test"))
                await asyncio.sleep(0.05)
            finally:
                await _cancel_task(task)

        # Verify the error was logged (not silently swallowed)
        assert any("send failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 2. test_repeated_send_failures_do_not_block_bus_consumption
# ---------------------------------------------------------------------------


class TestRepeatedSendFailuresDoNotBlockBusConsumption:
    """Mock a channel that always raises on send.
    Publish 10 outbound messages to the bus.

    Assert:
    - All 10 are consumed from the bus (not blocking).
    - The dispatch loop doesn't hang.
    - Errors are logged for each.
    """

    async def test_repeated_send_failures_do_not_block_bus_consumption(self, caplog):
        bus = MessageBus()
        failing = FailingChannel(MagicMock(), bus)
        manager = _make_manager_with_channels(bus, {"failing": failing})

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            # Publish 10 messages
            for i in range(10):
                await bus.publish_outbound(_make_outbound("failing", f"msg-{i}"))

            # Wait for all to be consumed (with a safety timeout)
            await asyncio.wait_for(self._wait_queue_empty(bus), timeout=5.0)
        finally:
            await _cancel_task(task)

        # All 10 messages were attempted
        assert len(failing.send_attempts) == 10
        # All 10 had unique content
        contents = [m.content for m in failing.send_attempts]
        assert contents == [f"msg-{i}" for i in range(10)]

    async def test_dispatch_loop_does_not_hang_under_sustained_failure(self):
        """The dispatch loop must keep consuming even when every send fails."""
        bus = MessageBus()
        failing = FailingChannel(MagicMock(), bus)
        manager = _make_manager_with_channels(bus, {"failing": failing})

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            for i in range(10):
                await bus.publish_outbound(_make_outbound("failing", f"msg-{i}"))

            # This MUST complete within 5 seconds; if it hangs, the loop is blocked
            await asyncio.wait_for(self._wait_queue_empty(bus), timeout=5.0)
        finally:
            await _cancel_task(task)

        assert bus.outbound.qsize() == 0

    @staticmethod
    async def _wait_queue_empty(bus: MessageBus) -> None:
        while bus.outbound.qsize() > 0:
            await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# 3. test_send_timeout_does_not_hang_dispatch_loop
# ---------------------------------------------------------------------------


class TestSendTimeoutDoesNotHangDispatchLoop:
    """Mock a channel's send to await asyncio.sleep(forever) (simulating a hang).

    FINDING: The current implementation of _send_with_channel does NOT have a
    per-send timeout.  If channel.send() hangs, the entire dispatch loop hangs,
    blocking all other channels.  This IS a hang risk.

    The test below documents this behavior:
    - It verifies that a hanging send DOES block the dispatch loop.
    - It uses asyncio.wait_for to detect the hang within a test timeout.
    """

    async def test_hanging_send_blocks_dispatch_loop(self):
        """Document: a hanging channel.send() blocks the entire dispatch loop.

        This is a known limitation -- _send_with_channel awaits channel.send()
        without a timeout wrapper.  A future improvement could wrap the call
        with asyncio.wait_for().
        """
        bus = MessageBus()
        hanging = HangingChannel(MagicMock(), bus)
        healthy = HealthyChannel(MagicMock(), bus)
        manager = _make_manager_with_channels(
            bus, {"hanging": hanging, "healthy": healthy}
        )

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            # Send a message that will hang
            await bus.publish_outbound(_make_outbound("hanging", "will-hang"))
            # Wait for the channel to actually start the send
            await asyncio.wait_for(hanging.send_started.wait(), timeout=2.0)

            # Now send a message to the healthy channel
            await bus.publish_outbound(_make_outbound("healthy", "should-arrive"))

            # The healthy channel's message should NOT arrive because the loop is stuck
            # We give it a short window to prove it's blocked
            await asyncio.sleep(0.3)
            assert len(healthy.sent_messages) == 0, (
                "UNEXPECTED: healthy channel received a message while the hanging "
                "channel was blocking.  If this assertion fails, it means a timeout "
                "mechanism was added -- which is good!  Update this test."
            )
        finally:
            await _cancel_task(task)

    async def test_dispatch_loop_hang_is_detectable(self):
        """Verify we can detect the hang using asyncio.wait_for as a watchdog.

        The hang occurs during channel.send(), AFTER the message is consumed
        from the queue.  So we publish a second message after the hang starts
        and verify it remains stuck in the queue (never consumed).
        """
        bus = MessageBus()
        hanging = HangingChannel(MagicMock(), bus)
        manager = _make_manager_with_channels(bus, {"hanging": hanging})

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            await bus.publish_outbound(_make_outbound("hanging", "block"))
            await asyncio.wait_for(hanging.send_started.wait(), timeout=2.0)

            # The first message was consumed but the loop is now stuck in send().
            # Publish a second message -- it should remain in the queue because
            # the dispatch loop can't get back to consume_outbound().
            await bus.publish_outbound(_make_outbound("hanging", "stuck"))

            # Wait a bit and verify the second message is NOT consumed
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._wait_for_consumption(bus), timeout=1.0
                )

            # The second message is still sitting in the queue
            assert bus.outbound.qsize() == 1
        finally:
            await _cancel_task(task)

    @staticmethod
    async def _wait_for_consumption(bus: MessageBus) -> None:
        """Wait until the outbound queue is fully empty."""
        while bus.outbound.qsize() > 0:
            await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# 4. test_channel_send_failure_during_streaming_partial_delivery
# ---------------------------------------------------------------------------


class TestChannelSendFailureDuringStreamingPartialDelivery:
    """During streaming (multiple draft/progress messages), the channel fails
    on the 3rd draft.

    Assert:
    - The first 2 drafts were delivered.
    - The final _send_text (non-progress message) is still attempted.
    - User gets at least the complete final message.

    Note: In xbot's architecture, streaming drafts and the final message are
    separate OutboundMessage objects published to the bus.  The dispatch loop
    processes each independently.  A failure on one draft does NOT prevent
    subsequent messages from being dispatched.
    """

    async def test_partial_draft_failure_does_not_block_final_message(self):
        bus = MessageBus()
        channel = StreamingChannel(MagicMock(), bus, fail_on_draft_index=3)
        manager = _make_manager_with_channels(bus, {"streaming": channel})

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            # Simulate 5 streaming draft messages
            for i in range(5):
                await bus.publish_outbound(
                    _make_outbound(
                        "streaming",
                        f"draft-{i}",
                        _progress=True,
                        _event_type="content_delta",
                    )
                )

            # Final complete message (non-progress)
            await bus.publish_outbound(_make_outbound("streaming", "final-complete"))

            # Wait for processing
            await asyncio.sleep(0.2)
        finally:
            await _cancel_task(task)

        # The StreamingChannel appends to draft_sends BEFORE checking the failure
        # index, so all 5 drafts are recorded.  The 3rd one (index=3 means the
        # 3rd append) raises after recording.  The dispatch loop catches the
        # error and continues processing subsequent messages.
        assert len(channel.draft_sends) == 5

        # The final message MUST still be delivered
        assert len(channel.final_sends) == 1
        assert channel.final_sends[0].content == "final-complete"

    async def test_first_two_drafts_delivered_before_failure(self):
        bus = MessageBus()
        channel = StreamingChannel(MagicMock(), bus, fail_on_draft_index=3)
        manager = _make_manager_with_channels(bus, {"streaming": channel})

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            for i in range(5):
                await bus.publish_outbound(
                    _make_outbound(
                        "streaming",
                        f"chunk-{i}",
                        _progress=True,
                        _event_type="content_delta",
                    )
                )
            await asyncio.sleep(0.2)
        finally:
            await _cancel_task(task)

        # The first 2 drafts (indices 0, 1) were delivered without error.
        # Draft at index 2 (the 3rd append, fail_on_draft_index=3) raised.
        # Drafts at indices 3, 4 continued normally after the error.
        delivered_contents = [m.content for m in channel.draft_sends]
        assert "chunk-0" in delivered_contents
        assert "chunk-1" in delivered_contents


# ---------------------------------------------------------------------------
# 5. test_multiple_channels_one_fails_others_unaffected
# ---------------------------------------------------------------------------


class TestMultipleChannelsOneFailsOthersUnaffected:
    """Two channels registered for the same session.  One fails consistently.

    Assert:
    - The other channel still receives all messages normally.
    - The failing channel's errors don't affect the healthy one.
    """

    async def test_healthy_channel_unaffected_by_failing_sibling(self):
        bus = MessageBus()
        failing = FailingChannel(MagicMock(), bus)
        healthy = HealthyChannel(MagicMock(), bus)
        manager = _make_manager_with_channels(
            bus, {"failing": failing, "healthy": healthy}
        )

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            # Interleave messages to both channels
            for i in range(5):
                await bus.publish_outbound(_make_outbound("failing", f"fail-{i}"))
                await bus.publish_outbound(_make_outbound("healthy", f"ok-{i}"))

            await asyncio.sleep(0.3)
        finally:
            await _cancel_task(task)

        # Healthy channel received ALL its messages
        assert len(healthy.sent_messages) == 5
        assert [m.content for m in healthy.sent_messages] == [f"ok-{i}" for i in range(5)]

        # Failing channel attempted all its messages (they just errored)
        assert len(failing.send_attempts) == 5

    async def test_failing_channel_errors_do_not_corrupt_healthy_channel_state(self):
        bus = MessageBus()
        failing = FailingChannel(MagicMock(), bus)
        healthy = HealthyChannel(MagicMock(), bus)
        manager = _make_manager_with_channels(
            bus, {"failing": failing, "healthy": healthy}
        )

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            # Send many failures followed by a healthy message
            for i in range(20):
                await bus.publish_outbound(_make_outbound("failing", f"fail-{i}"))
            await bus.publish_outbound(_make_outbound("healthy", "important"))

            await asyncio.sleep(0.5)
        finally:
            await _cancel_task(task)

        # The healthy message arrived intact despite 20 preceding failures
        assert len(healthy.sent_messages) == 1
        assert healthy.sent_messages[0].content == "important"

    async def test_message_ordering_preserved_across_channels(self):
        """Messages are dispatched in FIFO order from the bus regardless of channel health."""
        bus = MessageBus()
        failing = FailingChannel(MagicMock(), bus)
        healthy = HealthyChannel(MagicMock(), bus)
        manager = _make_manager_with_channels(
            bus, {"failing": failing, "healthy": healthy}
        )

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            await bus.publish_outbound(_make_outbound("healthy", "first"))
            await bus.publish_outbound(_make_outbound("failing", "error"))
            await bus.publish_outbound(_make_outbound("healthy", "second"))
            await bus.publish_outbound(_make_outbound("failing", "error2"))
            await bus.publish_outbound(_make_outbound("healthy", "third"))

            await asyncio.sleep(0.2)
        finally:
            await _cancel_task(task)

        assert [m.content for m in healthy.sent_messages] == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# 6. test_outbound_queue_memory_bounded_under_sustained_failure
# ---------------------------------------------------------------------------


class TestOutboundQueueMemoryBoundedUnderSustainedFailure:
    """A channel fails for a sustained period.  Messages keep flowing into outbound.

    Assert: the queue doesn't grow unboundedly.

    FINDING: The MessageBus uses asyncio.Queue(maxsize=1000) for the outbound
    queue.  This means:
    - The queue IS bounded (maxsize=1000).
    - When the queue is full, publish_outbound will block (back-pressure the
      producer) because asyncio.Queue.put() blocks when full.
    - Messages are NOT dropped; instead, the producer is back-pressured.
    """

    async def test_outbound_queue_has_max_size(self):
        """Verify the outbound queue is bounded."""
        bus = MessageBus()
        # Default max_queue_size=1000
        assert bus.outbound.maxsize == 1000

    async def test_outbound_queue_custom_max_size(self):
        """Verify custom maxsize is respected."""
        bus = MessageBus(max_queue_size=50)
        assert bus.outbound.maxsize == 50

    async def test_producer_backpressured_when_queue_full(self):
        """When the outbound queue is full, publish_outbound blocks (back-pressure).

        This is the mechanism that prevents unbounded memory growth:
        asyncio.Queue.put() blocks when maxsize is reached.
        """
        small_bus = MessageBus(max_queue_size=5)

        # Fill the queue
        for i in range(5):
            await small_bus.publish_outbound(_make_outbound("test", f"msg-{i}"))

        assert small_bus.outbound.qsize() == 5

        # The next publish should block (not complete immediately)
        publish_task = asyncio.create_task(
            small_bus.publish_outbound(_make_outbound("test", "overflow"))
        )
        await asyncio.sleep(0.1)
        assert not publish_task.done(), (
            "publish_outbound should block when queue is full (back-pressure)"
        )

        # Consuming one message should unblock the publisher
        consumed = await small_bus.consume_outbound()
        assert consumed.content == "msg-0"

        # Now the publish should complete
        await asyncio.wait_for(publish_task, timeout=1.0)
        assert small_bus.outbound.qsize() == 5  # back to full

    async def test_sustained_failure_does_not_grow_queue_unboundedly(self):
        """Under sustained failure, the queue stays bounded at maxsize.

        The dispatch loop consumes messages (even if send fails), so the queue
        drains. The risk would be if failures caused re-queuing, but the current
        implementation does NOT re-queue failed messages.
        """
        bus = MessageBus(max_queue_size=20)
        failing = FailingChannel(MagicMock(), bus)
        manager = _make_manager_with_channels(bus, {"failing": failing})

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            # Rapidly publish messages
            for i in range(50):
                await bus.publish_outbound(_make_outbound("failing", f"msg-{i}"))

            # Wait for the dispatcher to drain the queue
            await asyncio.sleep(0.5)

            # Queue should be empty -- messages consumed even though sends failed
            assert bus.outbound.qsize() == 0

            # All 50 messages were attempted (consumed from queue, sent to channel)
            assert len(failing.send_attempts) == 50
        finally:
            await _cancel_task(task)

    async def test_failed_messages_are_not_requeued(self):
        """Verify that failed messages are consumed and discarded, not re-enqueued.

        This is important: if the dispatcher re-queued failed messages, it could
        cause infinite loops or queue growth.
        """
        bus = MessageBus()
        failing = FailingChannel(MagicMock(), bus)
        manager = _make_manager_with_channels(bus, {"failing": failing})

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            await bus.publish_outbound(_make_outbound("failing", "once"))
            await asyncio.sleep(0.1)
        finally:
            await _cancel_task(task)

        # Message was attempted exactly once (not retried at this layer)
        assert len(failing.send_attempts) == 1
        # Queue is empty -- message was consumed, not re-enqueued
        assert bus.outbound.qsize() == 0
