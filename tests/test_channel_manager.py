"""Tests for channel manager."""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from xbot.bus.queue import MessageBus
from xbot.channels.manager import ChannelManager
from xbot.channels.base import BaseChannel


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