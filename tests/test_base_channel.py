import asyncio
from types import SimpleNamespace

import pytest

from xbot.bus.events import OutboundMessage
from xbot.bus.queue import MessageBus
from xbot.channels.base import BaseChannel


class _DummyChannel(BaseChannel):
    name = "dummy"

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, msg: OutboundMessage) -> None:
        return None


def test_is_allowed_requires_exact_match() -> None:
    channel = _DummyChannel(SimpleNamespace(allow_from=["allow@email.com"]), MessageBus())

    assert channel.is_allowed("allow@email.com") is True
    assert channel.is_allowed("attacker|allow@email.com") is False


@pytest.mark.asyncio
async def test_create_tracked_task_consumes_exception(monkeypatch) -> None:
    import xbot.channels.base as base_module

    channel = _DummyChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    warnings: list[str] = []

    def _capture_warning(msg, *args):
        warnings.append(msg % args)

    monkeypatch.setattr(base_module.logger, "warning", _capture_warning)

    async def boom():
        raise RuntimeError("tracked-failure")

    channel._create_tracked_task(boom(), name="boom-task")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert any("tracked-failure" in message for message in warnings)


@pytest.mark.asyncio
async def test_default_stop_cancels_and_clears_tracked_tasks() -> None:
    channel = _DummyChannel(SimpleNamespace(allow_from=["*"]), MessageBus())
    channel._running = True

    started = asyncio.Event()

    async def worker():
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise

    channel._create_tracked_task(worker(), name="worker")
    await started.wait()

    await channel._default_stop()

    assert channel.is_running is False
    assert channel._background_tasks == set()
