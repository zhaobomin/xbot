"""Tests for channel manager."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from xbot.bus.queue import MessageBus
from xbot.channels.base import BaseChannel
from xbot.channels.manager import ChannelManager


class MockChannel(BaseChannel):
    """Mock channel for testing."""

    name = "mock"
    display_name = "Mock"

    @classmethod
    def default_config(cls):
        return {"enabled": False}

    def __init__(self, config, bus):
        super().__init__(config, bus)
        self._started = False
        self._stopped = False

    async def start(self):
        self._started = True
        while self._running:
            await asyncio.sleep(0.1)

    async def stop(self):
        self._stopped = True

    async def send(self, msg):
        pass


def make_mock_config(enabled_channels=None):
    """Create a mock config with specified enabled channels."""
    mock_config = MagicMock()

    # Set up channels attribute
    mock_config.channels = MagicMock()

    # Disable all channels by default
    if enabled_channels is None:
        enabled_channels = []

    # Configure each possible channel
    for ch in ["telegram", "feishu", "discord", "slack", "email", "matrix", "qq", "dingtalk", "wecom", "whatsapp", "mochat"]:
        if ch in enabled_channels:
            setattr(mock_config.channels, ch, MagicMock(enabled=True, allow_from=["*"]))
        else:
            # Set to a mock with enabled=False
            ch_mock = MagicMock()
            ch_mock.enabled = False
            setattr(mock_config.channels, ch, ch_mock)

    mock_config.providers = MagicMock()
    mock_config.providers.groq = MagicMock()
    mock_config.providers.groq.api_key = None
    mock_config.channels.send_tool_hints = True
    mock_config.channels.send_progress = True
    mock_config.channels.send_usage_summary = True

    return mock_config


class TestChannelManagerInit:
    """Tests for ChannelManager initialization."""

    def test_init_with_no_enabled_channels(self):
        """Test initialization with no enabled channels."""
        mock_config = make_mock_config(enabled_channels=[])

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)

        assert manager.channels == {}
        assert manager.enabled_channels == []

    def test_init_with_disabled_channel(self):
        """Test that disabled channels are not initialized."""
        mock_config = make_mock_config(enabled_channels=[])

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)

        assert "telegram" not in manager.channels


class TestChannelManagerStatus:
    """Tests for ChannelManager status methods."""

    def test_get_status(self):
        """Test get_status returns correct structure."""
        mock_config = make_mock_config(enabled_channels=[])

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)

        # Manually add a mock channel
        mock_channel = MagicMock(spec=BaseChannel)
        mock_channel.is_running = True
        manager.channels["test"] = mock_channel

        status = manager.get_status()

        assert "test" in status
        assert status["test"]["enabled"] is True
        assert status["test"]["running"] is True

    def test_enabled_channels_property(self):
        """Test enabled_channels property."""
        mock_config = make_mock_config(enabled_channels=[])

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)

        # Manually add mock channels
        manager.channels["telegram"] = MagicMock()
        manager.channels["feishu"] = MagicMock()

        assert set(manager.enabled_channels) == {"telegram", "feishu"}

    def test_get_channel(self):
        """Test get_channel returns correct channel."""
        mock_config = make_mock_config(enabled_channels=[])

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)

        mock_channel = MagicMock()
        manager.channels["test"] = mock_channel

        assert manager.get_channel("test") is mock_channel
        assert manager.get_channel("nonexistent") is None


class TestChannelManagerValidation:
    """Tests for ChannelManager validation."""

    def test_empty_allow_from_raises_system_exit(self):
        """Test that empty allow_from raises SystemExit."""
        mock_config = MagicMock()
        mock_config.channels = MagicMock()

        # Create a channel config with empty allow_from
        telegram_mock = MagicMock()
        telegram_mock.enabled = True
        telegram_mock.allow_from = []  # Empty list - should raise
        mock_config.channels.telegram = telegram_mock

        # Set up other required attributes
        mock_config.providers = MagicMock()
        mock_config.providers.groq = MagicMock()
        mock_config.providers.groq.api_key = None

        bus = MessageBus()

        # This should raise SystemExit because allow_from is empty
        with pytest.raises(SystemExit) as exc_info:
            ChannelManager(mock_config, bus)

        assert "allowFrom" in str(exc_info.value)


class TestChannelManagerStopAll:
    """Tests for ChannelManager stop_all."""

    @pytest.mark.asyncio
    async def test_stop_all_cancels_dispatch_task(self):
        """Test that stop_all cancels the dispatch task."""
        mock_config = make_mock_config(enabled_channels=[])

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)

        # Create a mock dispatch task
        async def mock_dispatch():
            while True:
                await asyncio.sleep(1)

        manager._dispatch_task = asyncio.create_task(mock_dispatch())

        await manager.stop_all()

        assert manager._dispatch_task.cancelled()

    @pytest.mark.asyncio
    async def test_stop_all_stops_channels(self):
        """Test that stop_all calls stop on all channels."""
        mock_config = make_mock_config(enabled_channels=[])

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)

        # Add mock channels
        mock_channel1 = AsyncMock(spec=BaseChannel)
        mock_channel2 = AsyncMock(spec=BaseChannel)
        manager.channels["ch1"] = mock_channel1
        manager.channels["ch2"] = mock_channel2

        await manager.stop_all()

        mock_channel1.stop.assert_called_once()
        mock_channel2.stop.assert_called_once()


class TestChannelManagerProgressFiltering:
    """Tests for structured progress filtering."""

    @pytest.mark.asyncio
    async def test_dispatch_usage_when_progress_disabled_but_usage_enabled(self):
        mock_config = make_mock_config(enabled_channels=[])
        mock_config.channels.send_progress = False
        mock_config.channels.send_usage_summary = True

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)
        mock_channel = AsyncMock(spec=BaseChannel)
        manager.channels["telegram"] = mock_channel

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            from xbot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel="telegram",
                    chat_id="c1",
                    content="Usage: input 10 tokens, output 5 tokens",
                    metadata={"_progress": True, "_event_type": "usage"},
                )
            )
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await task

        mock_channel.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_blocks_usage_when_usage_disabled(self):
        mock_config = make_mock_config(enabled_channels=[])
        mock_config.channels.send_progress = True
        mock_config.channels.send_usage_summary = False

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)
        mock_channel = AsyncMock(spec=BaseChannel)
        manager.channels["telegram"] = mock_channel

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            from xbot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel="telegram",
                    chat_id="c1",
                    content="Usage: input 10 tokens, output 5 tokens",
                    metadata={"_progress": True, "_event_type": "usage"},
                )
            )
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await task

        mock_channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_sends_content_delta_when_progress_enabled(self):
        mock_config = make_mock_config(enabled_channels=[])
        mock_config.channels.send_progress = True
        mock_config.channels.send_usage_summary = True

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)
        mock_channel = AsyncMock(spec=BaseChannel)
        manager.channels["telegram"] = mock_channel

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            from xbot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel="telegram",
                    chat_id="c1",
                    content="partial delta",
                    metadata={"_progress": True, "_event_type": "content_delta"},
                )
            )
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await task

        mock_channel.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_blocks_content_delta_when_progress_disabled(self):
        mock_config = make_mock_config(enabled_channels=[])
        mock_config.channels.send_progress = False
        mock_config.channels.send_usage_summary = True

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)
        mock_channel = AsyncMock(spec=BaseChannel)
        manager.channels["telegram"] = mock_channel

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            from xbot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel="telegram",
                    chat_id="c1",
                    content="partial delta",
                    metadata={"_progress": True, "_event_type": "content_delta"},
                )
            )
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await task

        mock_channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_thinking_progress_when_enabled(self):
        mock_config = make_mock_config(enabled_channels=[])
        mock_config.channels.send_progress = True
        mock_config.channels.send_tool_hints = True
        mock_config.channels.send_usage_summary = True

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)
        mock_channel = AsyncMock(spec=BaseChannel)
        manager.channels["telegram"] = mock_channel

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            from xbot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel="telegram",
                    chat_id="c1",
                    content="thinking...",
                    metadata={"_progress": True, "_event_type": "thinking"},
                )
            )
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await task

        mock_channel.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_thinking_respects_send_progress_switch(self):
        mock_config = make_mock_config(enabled_channels=[])
        mock_config.channels.send_progress = False
        mock_config.channels.send_tool_hints = True
        mock_config.channels.send_usage_summary = True

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)
        mock_channel = AsyncMock(spec=BaseChannel)
        manager.channels["telegram"] = mock_channel

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            from xbot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel="telegram",
                    chat_id="c1",
                    content="thinking...",
                    metadata={"_progress": True, "_event_type": "thinking"},
                )
            )
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await task

        mock_channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_allows_tool_hint_when_enabled(self):
        mock_config = make_mock_config(enabled_channels=[])
        mock_config.channels.send_tool_hints = True

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)
        mock_channel = AsyncMock(spec=BaseChannel)
        manager.channels["telegram"] = mock_channel

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            from xbot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel="telegram",
                    chat_id="c1",
                    content='Tool: read_file("README.md")',
                    metadata={"_progress": True, "_event_type": "tool_hint", "_tool_hint": True},
                )
            )
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await task

        mock_channel.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_blocks_tool_hint_when_disabled(self):
        mock_config = make_mock_config(enabled_channels=[])
        mock_config.channels.send_tool_hints = False

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)
        mock_channel = AsyncMock(spec=BaseChannel)
        manager.channels["telegram"] = mock_channel

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            from xbot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel="telegram",
                    chat_id="c1",
                    content='Tool: read_file("README.md")',
                    metadata={"_progress": True, "_event_type": "tool_hint", "_tool_hint": True},
                )
            )
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await task

        mock_channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_unknown_progress_respects_send_progress_switch(self):
        mock_config = make_mock_config(enabled_channels=[])
        mock_config.channels.send_progress = False
        mock_config.channels.send_usage_summary = True
        mock_config.channels.send_tool_hints = True

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)
        mock_channel = AsyncMock(spec=BaseChannel)
        manager.channels["telegram"] = mock_channel

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            from xbot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel="telegram",
                    chat_id="c1",
                    content="other progress event",
                    metadata={"_progress": True, "_event_type": "status_update"},
                )
            )
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await task

        mock_channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_task_event_respects_send_progress(self):
        mock_config = make_mock_config(enabled_channels=[])
        mock_config.channels.send_progress = True

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)
        mock_channel = AsyncMock(spec=BaseChannel)
        manager.channels["telegram"] = mock_channel

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            from xbot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel="telegram",
                    chat_id="c1",
                    content="Task completed",
                    metadata={"_progress": True, "_event_type": "task"},
                )
            )
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await task

        mock_channel.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_system_event_blocked_when_send_progress_disabled(self):
        mock_config = make_mock_config(enabled_channels=[])
        mock_config.channels.send_progress = False
        mock_config.channels.send_usage_summary = True
        mock_config.channels.send_tool_hints = True

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)
        mock_channel = AsyncMock(spec=BaseChannel)
        manager.channels["telegram"] = mock_channel

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            from xbot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel="telegram",
                    chat_id="c1",
                    content="Context compacted.",
                    metadata={"_progress": True, "_event_type": "system"},
                )
            )
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            await task

        mock_channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_default_visibility_snapshot_for_core_events(self):
        mock_config = make_mock_config(enabled_channels=[])
        # Defaults should expose all core progress categories.
        mock_config.channels.send_progress = True
        mock_config.channels.send_tool_hints = True
        mock_config.channels.send_usage_summary = True

        bus = MessageBus()
        manager = ChannelManager(mock_config, bus)
        mock_channel = AsyncMock(spec=BaseChannel)
        manager.channels["telegram"] = mock_channel

        task = asyncio.create_task(manager._dispatch_outbound())
        try:
            from xbot.bus.events import OutboundMessage

            events = [
                ("content_delta", False, "delta chunk"),
                ("thinking", False, "Thinking: planning"),
                ("task", False, "Running: compact"),
                ("system", False, "Context compacted."),
                ("usage", False, "Usage: input 12 tokens, output 3 tokens"),
                ("tool_hint", True, 'Tool: compact()'),
            ]

            for event_type, tool_hint, content in events:
                await bus.publish_outbound(
                    OutboundMessage(
                        channel="telegram",
                        chat_id="c1",
                        content=content,
                        metadata={
                            "_progress": True,
                            "_event_type": event_type,
                            "_tool_hint": tool_hint,
                        },
                    )
                )
            await asyncio.sleep(0.1)
        finally:
            task.cancel()
            await task

        assert mock_channel.send.call_count == 6
        sent_event_types = [
            call.args[0].metadata["_event_type"]
            for call in mock_channel.send.call_args_list
        ]
        assert sent_event_types == [
            "content_delta",
            "thinking",
            "task",
            "system",
            "usage",
            "tool_hint",
        ]
