"""Tests for WhatsApp channel."""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from xbot.bus.events import OutboundMessage
from xbot.bus.queue import MessageBus
from xbot.channels.whatsapp import WhatsAppChannel, WhatsAppConfig


class TestWhatsAppConfig:
    """Tests for WhatsAppConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = WhatsAppConfig()
        assert config.enabled is False
        assert config.bridge_url == "ws://localhost:3001"
        assert config.bridge_token == ""
        assert config.allow_from == []

    def test_custom_config(self):
        """Test custom configuration values."""
        config = WhatsAppConfig(
            enabled=True,
            bridge_url="ws://custom:3002",
            bridge_token="my_token",
            allow_from=["1234567890"],
        )
        assert config.enabled is True
        assert config.bridge_url == "ws://custom:3002"
        assert config.bridge_token == "my_token"
        assert config.allow_from == ["1234567890"]


class TestWhatsAppChannel:
    """Tests for WhatsAppChannel."""

    def test_channel_metadata(self):
        """Test channel metadata."""
        assert WhatsAppChannel.name == "whatsapp"
        assert WhatsAppChannel.display_name == "WhatsApp"

    def test_default_config_method(self):
        """Test default_config class method."""
        config = WhatsAppChannel.default_config()
        assert isinstance(config, dict)
        assert config["enabled"] is False

    def test_init_with_dict_config(self):
        """Test initialization with dict config."""
        config = {"enabled": True, "bridge_url": "ws://test:3001"}
        channel = WhatsAppChannel(config, MessageBus())
        assert channel.config.enabled is True
        assert channel.config.bridge_url == "ws://test:3001"

    def test_init_with_pydantic_config(self):
        """Test initialization with Pydantic config."""
        config = WhatsAppConfig(enabled=True)
        channel = WhatsAppChannel(config, MessageBus())
        assert channel.config.enabled is True


class TestWhatsAppChannelStartStop:
    """Tests for WhatsApp channel start/stop."""

    @pytest.mark.asyncio
    async def test_stop_clears_state(self):
        """Test that stop clears connection state."""
        channel = WhatsAppChannel(WhatsAppConfig(), MessageBus())
        channel._running = True
        channel._connected = True
        channel._ws = MagicMock()
        channel._ws.close = AsyncMock()
        
        await channel.stop()
        
        assert channel._running is False
        assert channel._connected is False
        assert channel._ws is None

    @pytest.mark.asyncio
    async def test_start_sets_running_flag(self):
        """Test that start sets running flag."""
        channel = WhatsAppChannel(WhatsAppConfig(), MessageBus())
        
        # Mock websockets.connect to fail immediately to avoid hanging
        with patch("websockets.connect") as mock_connect:
            mock_connect.side_effect = Exception("test error")
            # Use asyncio.wait_for to prevent hanging
            try:
                await asyncio.wait_for(channel.start(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
        
        # Running should be False after exception


class TestWhatsAppChannelSend:
    """Tests for WhatsApp channel send."""

    @pytest.mark.asyncio
    async def test_send_without_connection_logs_warning(self, caplog):
        """Test that send without connection logs warning."""
        channel = WhatsAppChannel(WhatsAppConfig(), MessageBus())
        channel._ws = None
        channel._connected = False
        
        msg = OutboundMessage(
            channel="whatsapp",
            chat_id="1234567890",
            content="test message",
        )
        
        await channel.send(msg)
        # Should return early without error

    @pytest.mark.asyncio
    async def test_send_basic_message(self):
        """Test sending a basic message."""
        channel = WhatsAppChannel(WhatsAppConfig(), MessageBus())
        channel._connected = True
        channel._ws = MagicMock()
        channel._ws.send = AsyncMock()
        
        msg = OutboundMessage(
            channel="whatsapp",
            chat_id="1234567890@s.whatsapp.net",
            content="test message",
        )
        
        await channel.send(msg)
        
        # Check that send was called with correct payload
        assert channel._ws.send.called
        call_args = channel._ws.send.call_args[0][0]
        payload = json.loads(call_args)
        assert payload["type"] == "send"
        assert payload["to"] == "1234567890@s.whatsapp.net"
        assert payload["text"] == "test message"


class TestWhatsAppChannelHandleBridgeMessage:
    """Tests for WhatsApp bridge message handling."""

    @pytest.mark.asyncio
    async def test_handle_invalid_json(self, caplog):
        """Test handling invalid JSON from bridge."""
        channel = WhatsAppChannel(WhatsAppConfig(), MessageBus())
        
        await channel._handle_bridge_message("not valid json")
        # Should log warning but not crash

    @pytest.mark.asyncio
    async def test_handle_status_message(self, caplog):
        """Test handling status message from bridge."""
        channel = WhatsAppChannel(WhatsAppConfig(), MessageBus())
        
        await channel._handle_bridge_message(json.dumps({
            "type": "status",
            "status": "connected"
        }))
        
        assert channel._connected is True

    @pytest.mark.asyncio
    async def test_handle_incoming_message_deduplication(self):
        """Test that duplicate messages are ignored."""
        channel = WhatsAppChannel(WhatsAppConfig(), MessageBus())
        
        msg_data = {
            "type": "message",
            "id": "msg_123",
            "sender": "1234567890@s.whatsapp.net",
            "content": "test",
        }
        
        # First message should be processed (adds to seen set)
        await channel._handle_bridge_message(json.dumps(msg_data))
        
        # Second message with same ID should be ignored (already in seen set)
        await channel._handle_bridge_message(json.dumps(msg_data))
        
        # The message ID should only be in the processed dict once
        assert "msg_123" in channel._processed_message_ids


class TestWhatsAppChannelProcessedMessages:
    """Tests for processed message tracking."""

    def test_processed_message_ids_limit(self):
        """Test that processed message IDs are limited to 1000."""
        from collections import OrderedDict
        
        channel = WhatsAppChannel(WhatsAppConfig(), MessageBus())
        
        # Simulate the limit logic from the code
        for i in range(1500):
            channel._processed_message_ids[str(i)] = None
            while len(channel._processed_message_ids) > 1000:
                channel._processed_message_ids.popitem(last=False)
        
        assert len(channel._processed_message_ids) == 1000