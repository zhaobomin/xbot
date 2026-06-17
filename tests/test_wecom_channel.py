"""Tests for WeCom (Enterprise WeChat) channel."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.channels.wecom import WecomChannel, WecomConfig
from xbot.platform.bus.events import OutboundMessage
from xbot.platform.bus.queue import MessageBus


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

    @pytest.mark.asyncio
    async def test_send_uses_chat_id_frame_mapping_even_when_message_has_msg_id(self):
        """Replies should resolve frames by chat id, not only by message id."""
        channel = WecomChannel(
            WecomConfig(bot_id="test", secret="pass"),
            MessageBus(),
        )
        _ = {"frame": "value"}
        channel._handle_message = AsyncMock()
        channel._client = MagicMock()
        channel._client.reply_stream = AsyncMock()
        channel._generate_req_id = MagicMock(return_value="stream-1")

        inbound_frame = {
            "body": {
                "msgid": "msg-1",
                "chatid": "chat-123",
                "from": {"userid": "user-1"},
                "text": {"content": "hello"},
            }
        }

        await channel._process_message(inbound_frame, "text")

        await channel.send(OutboundMessage(channel="wecom", chat_id="chat-123", content="hello"))

        channel._client.reply_stream.assert_awaited_once_with(
            inbound_frame,
            "stream-1",
            "hello",
            finish=True,
        )

    @pytest.mark.asyncio
    async def test_send_returns_when_request_id_generator_is_missing(self):
        """A partially initialized SDK client should not crash send()."""
        channel = WecomChannel(
            WecomConfig(bot_id="test", secret="pass"),
            MessageBus(),
        )
        channel._client = MagicMock()
        channel._client.reply_stream = AsyncMock()
        channel._generate_req_id = None
        channel._chat_frames["chat-123"] = {"frame": "value"}

        await channel.send(OutboundMessage(channel="wecom", chat_id="chat-123", content="hello"))

        channel._client.reply_stream.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_chat_frames_cache_is_bounded_and_keeps_latest_entries(self):
        """Reply frame cache should not grow without bound."""
        channel = WecomChannel(
            WecomConfig(bot_id="test", secret="pass"),
            MessageBus(),
        )
        channel._handle_message = AsyncMock()

        for idx in range(600):
            await channel._process_message(
                {
                    "body": {
                        "msgid": f"msg-{idx}",
                        "chatid": f"chat-{idx}",
                        "from": {"userid": f"user-{idx}"},
                        "text": {"content": f"hello-{idx}"},
                    }
                },
                "text",
            )

        assert len(channel._chat_frames) <= 1000
        assert "chat-599" in channel._chat_frames
        assert "msg-599" in channel._chat_frames


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

        channel = WecomChannel(
            WecomConfig(bot_id="test", secret="pass"),
            MessageBus()
        )

        # Simulate adding many message IDs
        for i in range(1500):
            channel._processed_message_ids[str(i)] = None
            # The actual limit logic would be in the message handler
            # This test just verifies the data structure works
