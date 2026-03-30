import asyncio
from collections.abc import AsyncIterator

import pytest

from xbot_codex.bus import MessageBus
from xbot_codex.config import ServiceConfig
from xbot_codex.events import InboundMessage, OutboundMessage
from xbot_codex.service.app import CodexService


class StubRuntime:
    def __init__(self) -> None:
        self.seen: list[InboundMessage] = []

    async def handle_message(self, msg: InboundMessage) -> AsyncIterator[OutboundMessage]:
        self.seen.append(msg)
        yield OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=f"echo:{msg.content}")


class BlockingRuntime:
    def __init__(self) -> None:
        self.started: list[str] = []
        self._gate = asyncio.Event()

    async def handle_message(self, msg: InboundMessage) -> AsyncIterator[OutboundMessage]:
        self.started.append(msg.session_key)
        await self._gate.wait()
        yield OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=f"done:{msg.content}")

    def release(self) -> None:
        self._gate.set()


class FlakyRuntime:
    def __init__(self) -> None:
        self.calls = 0

    async def handle_message(self, msg: InboundMessage) -> AsyncIterator[OutboundMessage]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom")
        yield OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=f"ok:{msg.content}")


@pytest.mark.asyncio
async def test_service_processes_one_inbound_message_to_outbound() -> None:
    runtime = StubRuntime()
    bus = MessageBus()
    service = CodexService(config=ServiceConfig(), runtime=runtime, bus=bus)
    inbound = InboundMessage(channel="telegram", sender_id="u1", chat_id="1", content="hello")

    await bus.publish_inbound(inbound)
    await service.process_next_message()
    outbound = await bus.consume_outbound()

    assert runtime.seen == [inbound]
    assert outbound.content == "echo:hello"


@pytest.mark.asyncio
async def test_inbound_loop_survives_runtime_exception() -> None:
    runtime = FlakyRuntime()
    bus = MessageBus()
    service = CodexService(config=ServiceConfig(), runtime=runtime, bus=bus)

    await service.start()
    try:
        await bus.publish_inbound(InboundMessage(channel="telegram", sender_id="u1", chat_id="1", content="first"))
        await asyncio.sleep(0.05)
        await bus.publish_inbound(InboundMessage(channel="telegram", sender_id="u1", chat_id="1", content="second"))
        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=1)
    finally:
        await service.shutdown()

    assert runtime.calls >= 2
    assert outbound.content == "ok:second"


@pytest.mark.asyncio
async def test_service_returns_busy_for_same_session_while_running() -> None:
    runtime = BlockingRuntime()
    bus = MessageBus()
    service = CodexService(config=ServiceConfig(), runtime=runtime, bus=bus)

    await service.start()
    try:
        await bus.publish_inbound(InboundMessage(channel="feishu", sender_id="u1", chat_id="chat-1", content="first"))
        await asyncio.sleep(0.05)
        await bus.publish_inbound(InboundMessage(channel="feishu", sender_id="u1", chat_id="chat-1", content="second"))
        busy = await asyncio.wait_for(bus.consume_outbound(), timeout=1)
        runtime.release()
        final = await asyncio.wait_for(bus.consume_outbound(), timeout=1)
    finally:
        await service.shutdown()

    assert busy.content == "Codex is still working on your previous message. Please wait or send !stop."
    assert final.content == "done:first"
    assert runtime.started == ["feishu:chat-1"]


@pytest.mark.asyncio
async def test_service_allows_different_sessions_to_run_in_parallel() -> None:
    runtime = BlockingRuntime()
    bus = MessageBus()
    service = CodexService(config=ServiceConfig(), runtime=runtime, bus=bus)

    await service.start()
    try:
        await bus.publish_inbound(InboundMessage(channel="feishu", sender_id="u1", chat_id="chat-1", content="first"))
        await bus.publish_inbound(InboundMessage(channel="feishu", sender_id="u2", chat_id="chat-2", content="second"))
        await asyncio.sleep(0.05)
        runtime.release()
        outputs = [await asyncio.wait_for(bus.consume_outbound(), timeout=1) for _ in range(2)]
    finally:
        await service.shutdown()

    assert sorted(runtime.started) == ["feishu:chat-1", "feishu:chat-2"]
    assert sorted(message.content for message in outputs) == ["done:first", "done:second"]
