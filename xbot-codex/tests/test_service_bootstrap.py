from __future__ import annotations

import asyncio

import pytest

from xbot_codex.bus import MessageBus
from xbot_codex.config import ServiceConfig
from xbot_codex.runtime import CodexRuntime
from xbot_codex.service.app import CodexService
from xbot_codex.session.store import SessionStore


class FakeChannelManager:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start_all(self) -> None:
        self.started = True

    async def stop_all(self) -> None:
        self.stopped = True

    async def dispatch_loop(self, queue) -> None:
        await asyncio.sleep(60)


@pytest.mark.asyncio
async def test_service_start_registers_background_tasks_and_starts_channels() -> None:
    runtime = CodexRuntime(
        config=ServiceConfig(),
        session_store=SessionStore(default_workdir_root="/tmp/xbot-codex"),
    )
    manager = FakeChannelManager()
    service = CodexService(
        config=ServiceConfig(),
        runtime=runtime,
        bus=MessageBus(),
        channel_manager=manager,
    )

    await service.start()
    try:
        assert manager.started is True
        assert len(service._tasks) == 2
    finally:
        await service.shutdown()
        assert manager.stopped is True
