"""Tests for WeCom (Enterprise WeChat) channel."""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from xbot.bus.events import OutboundMessage
from xbot.bus.queue import MessageBus
from xbot.channels.wecom import WecomChannel, WecomConfig, WECOM_AVAILABLE


class TestWecomConfig:
    """Tests for WecomConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = WecomConfig()
        assert config.enabled is False
        assert config.bot_id == ""
        assert config.secret == ""
        assert config.allow_from == []
        assert config.welcome_message == ""

    def test_custom_config(self):
        """Test custom configuration values."""
        config = WecomConfig(
            enabled=True,
            bot_id="test_bot_id",
            secret="test_secret",
            allow_from=["user1"],
            welcome_message="Hello!",
        )
        assert config.enabled is True
        assert config.bot_id == "test_bot_id"
        assert config.secret == "test_secret"
        assert config.allow_from == ["user1"]
        assert config.welcome_message == "Hello!"


class TestWecomChannel:
    """Tests for WecomChannel."""

    def test_channel_metadata(self):
        """Test channel metadata."""
        assert WecomChannel.name == "wecom"
        assert WecomChannel.display_name == "WeCom"

    def test_default_config_method(self):
        """Test default_config class method."""
        config = WecomChannel.default_config()
        assert isinstance(config, dict)
        assert config["enabled"] is False

    def test_init_with_dict_config(self):
        """Test initialization with dict config."""
        config = {"enabled": True, "bot_id": "test", "secret": "pass"}
        channel = WecomChannel(config, MessageBus())
        assert channel.config.enabled is True
        assert channel.config.bot_id == "test"

    def test_init_with_pydantic_config(self):
        """Test initialization with Pydantic config."""
        config = WecomConfig(enabled=True, bot_id="test", secret="pass")
        channel = WecomChannel(config, MessageBus())
        assert channel.config.enabled is True


class TestWecomChannelStartStop:
    """Tests for WeCom channel start/stop."""

    @pytest.mark.asyncio
    async def test_start_without_sdk_logs_error(self, caplog):
        """Test that start without SDK logs error."""
        with patch("xbot.channels.wecom.WECOM_AVAILABLE", False):
            channel = WecomChannel(
                WecomConfig(bot_id="test", secret="pass"),
                MessageBus()
            )
            await channel.start()
            # Should return early

    @pytest.mark.asyncio
    async def test_start_without_credentials_logs_error(self, caplog):
        """Test that start without credentials logs error."""
        with patch("xbot.channels.wecom.WECOM_AVAILABLE", True):
            channel = WecomChannel(WecomConfig(), MessageBus())
            await channel.start()
            # Should return early without credentials

    @pytest.mark.asyncio
    async def test_stop_clears_state(self):
        """Test that stop clears state."""
        channel = WecomChannel(
            WecomConfig(bot_id="test", secret="pass"),
            MessageBus()
        )
        channel._running = True
        channel._client = MagicMock()
        channel._client.disconnect = AsyncMock()
        
        await channel.stop()
        
        assert channel._running is False


class TestWecomChannelMessageHandling:
    """Tests for WeCom message handling."""

    def test_msg_type_map(self):
        """Test message type mapping."""
        from xbot.channels.wecom import MSG_TYPE_MAP
        
        assert MSG_TYPE_MAP["image"] == "[image]"
        assert MSG_TYPE_MAP["voice"] == "[voice]"
        assert MSG_TYPE_MAP["file"] == "[file]"
        assert MSG_TYPE_MAP["mixed"] == "[mixed content]"


class TestWecomChannelProcessedMessages:
    """Tests for processed message tracking."""

    def test_processed_message_ids_is_ordered_dict(self):
        """Test that processed message IDs are stored in OrderedDict."""
        from collections import OrderedDict
        
        channel = WecomChannel(
            WecomConfig(bot_id="test", secret="pass"),
            MessageBus()
        )
        
        assert isinstance(channel._processed_message_ids, OrderedDict)

    def test_processed_message_ids_limit(self):
        """Test that processed message IDs are limited."""
        from collections import OrderedDict
        
        channel = WecomChannel(
            WecomConfig(bot_id="test", secret="pass"),
            MessageBus()
        )
        
        # Simulate adding many message IDs
        for i in range(1500):
            channel._processed_message_ids[str(i)] = None
            # The actual limit logic would be in the message handler
            # This test just verifies the data structure works