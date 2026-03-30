import pytest

from xbot_codex.channels.manager import ChannelManager
from xbot_codex.events import OutboundMessage


class FakeChannel:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.sent: list[OutboundMessage] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send(self, msg: OutboundMessage) -> None:
        self.sent.append(msg)


class FlakyChannel(FakeChannel):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def send(self, msg: OutboundMessage) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("send failed")
        await super().send(msg)


@pytest.mark.asyncio
async def test_channel_manager_starts_and_stops_registered_channels() -> None:
    manager = ChannelManager.__new__(ChannelManager)
    manager.channels = {"telegram": FakeChannel(), "feishu": FakeChannel()}

    await ChannelManager.start_all(manager)
    await ChannelManager.stop_all(manager)

    assert manager.channels["telegram"].started is True
    assert manager.channels["feishu"].stopped is True


@pytest.mark.asyncio
async def test_channel_manager_dispatches_to_matching_channel() -> None:
    manager = ChannelManager.__new__(ChannelManager)
    target = FakeChannel()
    manager.channels = {"telegram": target}

    await ChannelManager.dispatch(manager, OutboundMessage(channel="telegram", chat_id="1", content="hi"))

    assert [m.content for m in target.sent] == ["hi"]


@pytest.mark.asyncio
async def test_channel_manager_dispatch_loop_survives_send_exception() -> None:
    manager = ChannelManager.__new__(ChannelManager)
    target = FlakyChannel()
    manager.channels = {"feishu": target}
    queue = __import__("asyncio").Queue()
    task = __import__("asyncio").create_task(ChannelManager.dispatch_loop(manager, queue))

    try:
        await queue.put(OutboundMessage(channel="feishu", chat_id="1", content="first"))
        await __import__("asyncio").sleep(0.05)
        await queue.put(OutboundMessage(channel="feishu", chat_id="1", content="second"))
        await __import__("asyncio").sleep(0.05)
    finally:
        task.cancel()
        await __import__("asyncio").gather(task, return_exceptions=True)

    assert target.calls >= 2
    assert [m.content for m in target.sent] == ["second"]
