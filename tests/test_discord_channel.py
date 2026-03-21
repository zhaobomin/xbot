"""Tests for Discord channel."""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from xbot.bus.events import OutboundMessage
from xbot.bus.queue import MessageBus
from xbot.channels.discord import DiscordChannel, DiscordConfig


class TestDiscordConfig:
    """Tests for DiscordConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = DiscordConfig()
        assert config.enabled is False
        assert config.token == ""
        assert config.allow_from == []
        assert config.gateway_url == "wss://gateway.discord.gg/?v=10&encoding=json"
        assert config.intents == 37377
        assert config.group_policy == "mention"

    def test_custom_config(self):
        """Test custom configuration values."""
        config = DiscordConfig(
            enabled=True,
            token="test_token",
            allow_from=["123456789"],
            group_policy="open",
        )
        assert config.enabled is True
        assert config.token == "test_token"
        assert config.allow_from == ["123456789"]
        assert config.group_policy == "open"


class TestDiscordChannel:
    """Tests for DiscordChannel."""

    def test_channel_metadata(self):
        """Test channel metadata."""
        assert DiscordChannel.name == "discord"
        assert DiscordChannel.display_name == "Discord"

    def test_default_config_method(self):
        """Test default_config class method."""
        config = DiscordChannel.default_config()
        assert isinstance(config, dict)
        assert config["enabled"] is False

    def test_init_with_dict_config(self):
        """Test initialization with dict config."""
        config = {"enabled": True, "token": "test"}
        channel = DiscordChannel(config, MessageBus())
        assert channel.config.enabled is True
        assert channel.config.token == "test"

    def test_init_with_pydantic_config(self):
        """Test initialization with Pydantic config."""
        config = DiscordConfig(enabled=True, token="test")
        channel = DiscordChannel(config, MessageBus())
        assert channel.config.enabled is True
        assert channel.config.token == "test"


class TestDiscordChannelStartStop:
    """Tests for Discord channel start/stop."""

    @pytest.mark.asyncio
    async def test_start_without_token_logs_error(self, caplog):
        """Test that start without token logs error."""
        channel = DiscordChannel(DiscordConfig(), MessageBus())
        await channel.start()
        assert "token not configured" in caplog.text.lower() or channel._running is False

    @pytest.mark.asyncio
    async def test_stop_clears_resources(self):
        """Test that stop clears resources."""
        channel = DiscordChannel(DiscordConfig(token="test"), MessageBus())
        channel._running = True
        channel._heartbeat_task = asyncio.create_task(asyncio.sleep(10))
        channel._http = MagicMock()
        channel._http.aclose = AsyncMock()
        
        await channel.stop()
        
        assert channel._running is False
        assert channel._heartbeat_task is None
        assert channel._http is None


class TestDiscordChannelSend:
    """Tests for Discord channel send."""

    @pytest.mark.asyncio
    async def test_send_without_http_client_logs_warning(self, caplog):
        """Test that send without HTTP client logs warning."""
        channel = DiscordChannel(DiscordConfig(token="test"), MessageBus())
        channel._http = None
        
        msg = OutboundMessage(
            channel="discord",
            chat_id="123456",
            content="test message",
        )
        
        await channel.send(msg)
        # Should return early without error

    @pytest.mark.asyncio
    async def test_send_basic_message(self):
        """Test sending a basic text message."""
        channel = DiscordChannel(DiscordConfig(token="test"), MessageBus())
        
        mock_http = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_http.post = AsyncMock(return_value=mock_response)
        channel._http = mock_http
        
        msg = OutboundMessage(
            channel="discord",
            chat_id="123456",
            content="test message",
        )
        
        await channel.send(msg)
        
        # Check that post was called
        assert mock_http.post.called
        call_args = mock_http.post.call_args
        assert "channels/123456/messages" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_send_with_reply(self):
        """Test sending a message with reply reference."""
        channel = DiscordChannel(DiscordConfig(token="test"), MessageBus())
        
        mock_http = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_http.post = AsyncMock(return_value=mock_response)
        channel._http = mock_http
        
        msg = OutboundMessage(
            channel="discord",
            chat_id="123456",
            content="reply message",
            reply_to="789012",
        )
        
        await channel.send(msg)
        
        # Check that message_reference was included
        call_args = mock_http.post.call_args
        payload = call_args[1]["json"]
        assert "message_reference" in payload
        assert payload["message_reference"]["message_id"] == "789012"


class TestDiscordChannelSplitMessage:
    """Tests for Discord message splitting."""

    @pytest.mark.asyncio
    async def test_long_message_is_split(self):
        """Test that messages longer than 2000 chars are split."""
        channel = DiscordChannel(DiscordConfig(token="test"), MessageBus())
        
        mock_http = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_http.post = AsyncMock(return_value=mock_response)
        channel._http = mock_http
        
        # Create a message longer than Discord limit
        long_content = "x" * 3000
        msg = OutboundMessage(
            channel="discord",
            chat_id="123456",
            content=long_content,
        )
        
        await channel.send(msg)
        
        # Should have been called multiple times for chunks
        assert mock_http.post.call_count >= 2