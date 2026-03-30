from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from xbot_codex.bus import MessageBus
from xbot_codex.config import CodexConfig, ServiceConfig
from xbot_codex.runtime import CodexRuntime
from xbot_codex.service.app import CodexService
from xbot_codex.session.store import SessionStore


class ClosableTransport:
    def __init__(self) -> None:
        self.closed = False
        self.interrupted: list[str] = []

    async def interrupt(self, session_key: str) -> bool:
        self.interrupted.append(session_key)
        return True

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_service_shutdown_stops_background_tasks_and_running_sessions() -> None:
    transport = ClosableTransport()
    runtime = CodexRuntime(
        config=ServiceConfig(),
        session_store=SessionStore(default_workdir_root="/tmp/xbot-codex"),
        transport=transport,
    )
    runtime.session_store.get_or_create("telegram", "1").process_state = "running"
    service = CodexService(config=ServiceConfig(), runtime=runtime, bus=MessageBus())
    service._tasks = [asyncio.create_task(asyncio.sleep(60))]

    await service.shutdown()

    assert transport.interrupted == ["telegram:1"]
    assert transport.closed is True
    assert all(task.done() for task in service._tasks)


@pytest.mark.asyncio
async def test_service_start_bootstraps_minimal_codex_home_config(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    runtime = CodexRuntime(
        config=ServiceConfig(
            codex=CodexConfig(
                binary_path="codex",
                workdir_root=str(tmp_path / "workdir"),
                home=str(codex_home),
                default_model="gpt-5-codex",
            )
        ),
        session_store=SessionStore(default_workdir_root=str(tmp_path / "workdir")),
        transport=ClosableTransport(),
    )
    service = CodexService(config=runtime.config, runtime=runtime, bus=MessageBus())

    await service.start()
    await service.shutdown()

    config_text = (codex_home / "config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-5-codex"' in config_text
    assert "[mcp_servers." not in config_text
    assert "[plugins." not in config_text
