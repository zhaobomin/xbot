import pytest

from xbot_codex.channels.telegram import TelegramChannel
from xbot_codex.config import TelegramConfig
from xbot_codex.events import InboundMessage, OutboundMessage


@pytest.mark.asyncio
async def test_telegram_channel_forwards_inbound_messages_to_callback() -> None:
    seen: list[InboundMessage] = []

    async def on_message(msg: InboundMessage) -> None:
        seen.append(msg)

    channel = TelegramChannel(TelegramConfig(enabled=True, allow_from=["42"]), on_message=on_message)
    await channel.handle_text_message(sender_id="42", chat_id="100", content="hello")

    assert len(seen) == 1
    assert seen[0].channel == "telegram"
    assert seen[0].sender_id == "42"
    assert seen[0].chat_id == "100"
    assert seen[0].content == "hello"


@pytest.mark.asyncio
async def test_telegram_channel_send_uses_attached_sender() -> None:
    sent: list[OutboundMessage] = []

    async def sender(msg: OutboundMessage) -> None:
        sent.append(msg)

    channel = TelegramChannel(TelegramConfig(enabled=True, allow_from=["*"]), send_impl=sender)
    await channel.send(OutboundMessage(channel="telegram", chat_id="100", content="hi"))

    assert [m.content for m in sent] == ["hi"]
