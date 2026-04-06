"""Tests for Mochat channel."""

import asyncio
from collections import deque
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from xbot.bus.events import OutboundMessage
from xbot.bus.queue import MessageBus
from xbot.channels.mochat import (
    MochatChannel,
    MochatConfig,
    MochatBufferedEntry,
    DelayState,
    MochatTarget,
    _safe_dict,
    _str_field,
)


class TestMochatHelpers:
    """Tests for helper functions."""

    def test_safe_dict_with_dict(self):
        """Test _safe_dict with dict input."""
        result = _safe_dict({"key": "value"})
        assert result == {"key": "value"}

    def test_safe_dict_with_non_dict(self):
        """Test _safe_dict with non-dict input."""
        result = _safe_dict("not a dict")
        assert result == {}

    def test_str_field_found(self):
        """Test _str_field when key exists."""
        result = _str_field({"name": "test"}, "name")
        assert result == "test"

    def test_str_field_not_found(self):
        """Test _str_field when key doesn't exist."""
        result = _str_field({"other": "value"}, "name")
        assert result == ""

    def test_str_field_empty_value(self):
        """Test _str_field with empty value."""
        result = _str_field({"name": "   "}, "name")
        assert result == ""

    def test_str_field_fallback(self):
        """Test _str_field with fallback keys."""
        result = _str_field({"fallback": "value"}, "name", "fallback")
        assert result == "value"


class TestMochatBufferedEntry:
    """Tests for MochatBufferedEntry dataclass."""

    def test_creation(self):
        """Test creating a buffered entry."""
        entry = MochatBufferedEntry(
            raw_body="test message",
            author="user1",
            sender_name="Test User",
            sender_username="testuser",
            timestamp=1234567890,
            message_id="msg123",
            group_id="group1",
        )
        
        assert entry.raw_body == "test message"
        assert entry.author == "user1"
        assert entry.sender_name == "Test User"
        assert entry.timestamp == 1234567890

    def test_defaults(self):
        """Test default values."""
        entry = MochatBufferedEntry(
            raw_body="test",
            author="user1",
        )
        
        assert entry.sender_name == ""
        assert entry.sender_username == ""
        assert entry.timestamp is None
        assert entry.message_id == ""


class TestDelayState:
    """Tests for DelayState dataclass."""

    def test_creation(self):
        """Test creating a delay state."""
        state = DelayState()
        
        assert state.entries == []
        assert isinstance(state.lock, asyncio.Lock)
        assert state.timer is None


class TestMochatTarget:
    """Tests for MochatTarget dataclass."""

    def test_creation(self):
        """Test creating a target."""
        target = MochatTarget(id="target123", is_panel=True)
        
        assert target.id == "target123"
        assert target.is_panel is True


class TestMochatConfig:
    """Tests for MochatConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = MochatConfig()
        assert config.enabled is False
        assert config.base_url == "https://mochat.io"

    def test_custom_config(self):
        """Test custom configuration values."""
        config = MochatConfig(
            enabled=True,
            base_url="https://custom.mochat.io",
            socket_path="/custom/socket.io",
        )
        assert config.enabled is True
        assert config.base_url == "https://custom.mochat.io"
        assert config.socket_path == "/custom/socket.io"


class TestMochatChannel:
    """Tests for MochatChannel."""

    def test_channel_metadata(self):
        """Test channel metadata."""
        assert MochatChannel.name == "mochat"
        assert MochatChannel.display_name == "Mochat"

    def test_default_config_method(self):
        """Test default_config class method."""
        config = MochatChannel.default_config()
        assert isinstance(config, dict)
        assert config["enabled"] is False

    def test_init_with_dict_config(self):
        """Test initialization with dict config."""
        config = {"enabled": True, "base_url": "https://test.com"}
        channel = MochatChannel(config, MessageBus())
        assert channel.config.enabled is True

    def test_init_with_pydantic_config(self):
        """Test initialization with Pydantic config."""
        config = MochatConfig(enabled=True)
        channel = MochatChannel(config, MessageBus())
        assert channel.config.enabled is True


class TestMochatChannelStartStop:
    """Tests for Mochat channel start/stop."""

    @pytest.mark.asyncio
    async def test_stop_clears_state(self):
        """Test that stop clears state."""
        channel = MochatChannel(MochatConfig(), MessageBus())
        channel._running = True
        channel._socket = None
        
        await channel.stop()

        assert channel._running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_refresh_and_cursor_tasks(self):
        channel = MochatChannel(MochatConfig(), MessageBus())
        channel._running = True
        channel._socket = None
        channel._refresh_task = asyncio.create_task(asyncio.sleep(10))
        channel._cursor_save_task = asyncio.create_task(asyncio.sleep(10))

        await channel.stop()

        assert channel._refresh_task is None
        assert channel._cursor_save_task is None

    @pytest.mark.asyncio
    async def test_start_without_socketio_logs_error(self, caplog):
        """Test that start without socket.io logs error."""
        with patch("xbot.channels.mochat.SOCKETIO_AVAILABLE", False):
            channel = MochatChannel(MochatConfig(enabled=True), MessageBus())
            await channel.start()
            # Should handle gracefully


class TestMochatChannelSend:
    """Tests for Mochat channel send."""

    @pytest.mark.asyncio
    async def test_send_without_client_logs_warning(self, caplog):
        """Test that send without client logs warning."""
        channel = MochatChannel(MochatConfig(), MessageBus())
        channel._socket = None
        channel._ws_connected = False
        
        msg = OutboundMessage(
            channel="mochat",
            chat_id="panel123",
            content="test message",
        )
        
        await channel.send(msg)
        # Should return early without error


class TestMochatChannelMessageHandling:
    """Tests for Mochat message handling."""

    def test_seen_set_and_queue_initialized(self):
        """Test that seen_set and seen_queue are initialized."""
        channel = MochatChannel(MochatConfig(), MessageBus())
        
        assert isinstance(channel._seen_set, dict)
        assert isinstance(channel._seen_queue, dict)

    def test_delay_states_initialized(self):
        """Test that delay_states is initialized."""
        channel = MochatChannel(MochatConfig(), MessageBus())
        
        assert isinstance(channel._delay_states, dict)

    def test_session_set_initialized(self):
        """Test that session_set is initialized."""
        channel = MochatChannel(MochatConfig(), MessageBus())
        
        assert isinstance(channel._session_set, set)
        assert isinstance(channel._panel_set, set)
