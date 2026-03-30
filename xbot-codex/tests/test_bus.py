import pytest

from xbot_codex.bus import MessageBus
from xbot_codex.events import InboundMessage, OutboundMessage


@pytest.mark.asyncio
async def test_message_bus_round_trips_inbound_and_outbound() -> None:
    bus = MessageBus()
    inbound = InboundMessage(channel="telegram", sender_id="u1", chat_id="1", content="hello")
    outbound = OutboundMessage(channel="telegram", chat_id="1", content="world")

    await bus.publish_inbound(inbound)
    await bus.publish_outbound(outbound)

    assert await bus.consume_inbound() == inbound
    assert await bus.consume_outbound() == outbound
