"""Tests for subagent manager behavior."""

from pathlib import Path

import pytest

from xbot.agent.subagent import SubagentManager
from xbot.bus.queue import MessageBus


class _FakeProvider:
    def get_default_model(self) -> str:
        return "test-model"


@pytest.mark.asyncio
async def test_subagent_announce_result_routes_back_to_origin_channel(tmp_path: Path) -> None:
    bus = MessageBus()
    manager = SubagentManager(
        provider=_FakeProvider(),
        workspace=tmp_path,
        bus=bus,
    )

    await manager._announce_result(
        task_id="task-1",
        label="demo",
        task="do work",
        result="done",
        origin={"channel": "telegram", "chat_id": "group-1", "session_key": "telegram:group-1"},
        status="ok",
    )

    inbound = await bus.consume_inbound()
    assert inbound.channel == "telegram"
    assert inbound.chat_id == "group-1"
    assert inbound.session_key == "telegram:group-1"
    assert inbound.sender_id == "subagent"
