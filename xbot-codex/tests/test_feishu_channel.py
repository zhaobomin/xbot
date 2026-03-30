import pytest

from xbot_codex.channels.feishu import FeishuChannel
from xbot_codex.config import FeishuConfig
from xbot_codex.events import InboundMessage, OutboundMessage


@pytest.mark.asyncio
async def test_feishu_channel_forwards_inbound_messages_to_callback() -> None:
    seen: list[InboundMessage] = []

    async def on_message(msg: InboundMessage) -> None:
        seen.append(msg)

    channel = FeishuChannel(FeishuConfig(enabled=True, allow_from=["ou_1"]), on_message=on_message)
    await channel.handle_text_message(sender_id="ou_1", chat_id="oc_1", content="hello")

    assert len(seen) == 1
    assert seen[0].channel == "feishu"
    assert seen[0].sender_id == "ou_1"
    assert seen[0].chat_id == "oc_1"
    assert seen[0].content == "hello"


@pytest.mark.asyncio
async def test_feishu_channel_send_uses_attached_sender() -> None:
    sent: list[OutboundMessage] = []

    async def sender(msg: OutboundMessage) -> None:
        sent.append(msg)

    channel = FeishuChannel(FeishuConfig(enabled=True, allow_from=["*"]), send_impl=sender)
    await channel.send(OutboundMessage(channel="feishu", chat_id="oc_1", content="hi"))

    assert [m.content for m in sent] == ["hi"]


@pytest.mark.asyncio
async def test_feishu_channel_reacts_before_forwarding_message() -> None:
    seen: list[InboundMessage] = []
    reactions: list[tuple[str, str]] = []

    async def on_message(msg: InboundMessage) -> None:
        seen.append(msg)

    channel = FeishuChannel(
        FeishuConfig(enabled=True, app_id="id", app_secret="secret", allow_from=["*"], react_emoji="THUMBSUP"),
        on_message=on_message,
    )

    async def fake_add_reaction(message_id: str, emoji_type: str = "THUMBSUP") -> None:
        reactions.append((message_id, emoji_type))

    channel._add_reaction = fake_add_reaction
    await channel.handle_text_message(sender_id="ou_1", chat_id="oc_1", content="hello", message_id="mid-1")

    assert reactions == [("mid-1", "THUMBSUP")]
    assert seen[0].content == "hello"


@pytest.mark.asyncio
async def test_feishu_channel_routes_p2p_messages_by_sender_id() -> None:
    seen: list[InboundMessage] = []

    async def on_message(msg: InboundMessage) -> None:
        seen.append(msg)

    channel = FeishuChannel(
        FeishuConfig(enabled=True, app_id="id", app_secret="secret", allow_from=["*"]),
        on_message=on_message,
    )

    await channel.handle_text_message(
        sender_id="ou_1",
        chat_id="oc_chat",
        content="hello",
        metadata={"chat_type": "p2p"},
    )

    assert seen[0].chat_id == "ou_1"


@pytest.mark.asyncio
async def test_feishu_channel_send_selects_text_post_and_interactive_formats(monkeypatch) -> None:
    channel = FeishuChannel(FeishuConfig(enabled=True, app_id="id", app_secret="secret", allow_from=["*"]))
    channel._client = object()
    sent: list[tuple[str, str, str]] = []

    def fake_send(receive_id_type: str, receive_id: str, msg_type: str, content: str) -> bool:
        sent.append((receive_id_type, receive_id, msg_type))
        return True

    monkeypatch.setattr(channel, "_send_message_sync", fake_send)

    await channel.send(OutboundMessage(channel="feishu", chat_id="oc_1", content="hi"))
    await channel.send(
        OutboundMessage(
            channel="feishu",
            chat_id="oc_1",
            content="see [OpenAI](https://openai.com)",
        )
    )
    await channel.send(
        OutboundMessage(
            channel="feishu",
            chat_id="oc_1",
            content="**bold**",
        )
    )

    assert sent == [
        ("chat_id", "oc_1", "text"),
        ("chat_id", "oc_1", "post"),
        ("chat_id", "oc_1", "interactive"),
    ]


@pytest.mark.asyncio
async def test_feishu_channel_prefers_reply_api_for_first_send(monkeypatch) -> None:
    channel = FeishuChannel(
        FeishuConfig(enabled=True, app_id="id", app_secret="secret", allow_from=["*"], reply_to_message=True)
    )
    channel._client = object()
    replies: list[tuple[str, str]] = []
    sends: list[tuple[str, str, str]] = []

    def fake_reply(parent_message_id: str, msg_type: str, content: str) -> bool:
        replies.append((parent_message_id, msg_type))
        return True

    def fake_send(receive_id_type: str, receive_id: str, msg_type: str, content: str) -> bool:
        sends.append((receive_id_type, receive_id, msg_type))
        return True

    monkeypatch.setattr(channel, "_reply_message_sync", fake_reply)
    monkeypatch.setattr(channel, "_send_message_sync", fake_send)

    await channel.send(
        OutboundMessage(
            channel="feishu",
            chat_id="ou_1",
            content="hi",
            metadata={"message_id": "mid-1"},
        )
    )

    assert replies == [("mid-1", "text")]
    assert sends == []

