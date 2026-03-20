from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nanobot.agent.protocol import AgentContext, AgentResponse
from nanobot.agent.router import AgentRouter
from nanobot.bus.events import InboundMessage
from nanobot.config.schema import Config


class _FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        self.initialized = False
        self.shared_resources: dict[str, Any] = {}

    async def initialize(self, config: Any, shared_resources: dict[str, Any]) -> None:
        self.initialized = True
        self.shared_resources = shared_resources

    async def process(self, context: AgentContext):
        yield AgentResponse(
            content=f"echo:{context.prompt}",
            finish_reason="stop",
        )

    async def shutdown(self) -> None:
        return None

    async def cancel_session(self, session_key: str) -> int:
        return 0


@pytest.mark.asyncio
async def test_router_runtime_process_direct_routes_through_selected_backend(tmp_path) -> None:
    from nanobot.agent.runtime import AgentRuntime
    from nanobot.bus.queue import MessageBus

    AgentRouter._backends = {"fake": _FakeBackend}

    config = Config()
    config.agents.type = "fake"  # type: ignore[assignment]
    config.agents.defaults.workspace = str(tmp_path)

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": MessageBus(),
            "workspace": tmp_path,
            "config": config,
        },
    )

    response = await runtime.process_direct(
        "hello",
        session_key="cli:direct",
        channel="cli",
        chat_id="direct",
    )

    assert response == "echo:hello"


@pytest.mark.asyncio
async def test_router_runtime_help_is_handled_before_backend_invocation(tmp_path) -> None:
    from nanobot.agent.runtime import AgentRuntime
    from nanobot.bus.queue import MessageBus

    backend = _FakeBackend()

    class _BackendFactory:
        def __call__(self):
            return backend

    AgentRouter._backends = {"fake": _BackendFactory()}  # type: ignore[dict-item]

    config = Config()
    config.agents.type = "fake"  # type: ignore[assignment]
    config.agents.defaults.workspace = str(tmp_path)

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": MessageBus(),
            "workspace": tmp_path,
            "config": config,
        },
    )

    response = await runtime.process_direct("/help")

    assert "/restart" in response
    assert backend.initialized is False


@pytest.mark.asyncio
async def test_router_runtime_stop_delegates_backend_session_cancellation(tmp_path) -> None:
    from nanobot.agent.runtime import AgentRuntime
    from nanobot.bus.queue import MessageBus

    backend = _FakeBackend()

    async def _cancel_session(session_key: str) -> int:
        assert session_key == "cli:direct"
        return 2

    backend.cancel_session = _cancel_session  # type: ignore[method-assign]

    class _BackendFactory:
        def __call__(self):
            return backend

    AgentRouter._backends = {"fake": _BackendFactory()}  # type: ignore[dict-item]

    config = Config()
    config.agents.type = "fake"  # type: ignore[assignment]
    config.agents.defaults.workspace = str(tmp_path)

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": MessageBus(),
            "workspace": tmp_path,
            "config": config,
        },
    )

    response = await runtime.process_direct("/stop")

    assert "2 subagent" in response


@pytest.mark.asyncio
async def test_router_runtime_process_direct_forwards_progress_deltas(tmp_path) -> None:
    from nanobot.agent.runtime import AgentRuntime
    from nanobot.bus.queue import MessageBus

    class _DeltaBackend(_FakeBackend):
        async def process(self, context: AgentContext):
            yield AgentResponse(content="", is_delta=True, delta_content="thinking")
            yield AgentResponse(content="done", finish_reason="stop")

    AgentRouter._backends = {"fake": _DeltaBackend}

    config = Config()
    config.agents.type = "fake"  # type: ignore[assignment]
    config.agents.defaults.workspace = str(tmp_path)

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": MessageBus(),
            "workspace": tmp_path,
            "config": config,
        },
    )

    seen: list[tuple[str, bool]] = []

    async def _progress(content: str, *, tool_hint: bool = False) -> None:
        seen.append((content, tool_hint))

    response = await runtime.process_direct("hello", on_progress=_progress)

    assert response == "done"
    assert seen == [("thinking", False)]


@pytest.mark.asyncio
async def test_router_runtime_process_direct_forwards_progress_texts(tmp_path) -> None:
    from nanobot.agent.runtime import AgentRuntime
    from nanobot.bus.queue import MessageBus

    class _ProgressBackend(_FakeBackend):
        async def process(self, context: AgentContext):
            yield AgentResponse(content="", progress_texts=["planning", "reading files"])
            yield AgentResponse(content="done", finish_reason="stop")

    AgentRouter._backends = {"fake": _ProgressBackend}

    config = Config()
    config.agents.type = "fake"  # type: ignore[assignment]
    config.agents.defaults.workspace = str(tmp_path)

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": MessageBus(),
            "workspace": tmp_path,
            "config": config,
        },
    )

    seen: list[tuple[str, bool]] = []

    async def _progress(content: str, *, tool_hint: bool = False) -> None:
        seen.append((content, tool_hint))

    response = await runtime.process_direct("hello", on_progress=_progress)

    assert response == "done"
    assert seen == [("planning", False), ("reading files", False)]


@pytest.mark.asyncio
async def test_router_runtime_run_publishes_progress_messages_to_bus(tmp_path) -> None:
    from nanobot.agent.runtime import AgentRuntime
    from nanobot.bus.queue import MessageBus

    class _ProgressBackend(_FakeBackend):
        async def process(self, context: AgentContext):
            yield AgentResponse(content="", progress_texts=["thinking"])
            yield AgentResponse(content="", tool_calls=[{"name": "read_file", "input": {"path": "README.md"}}])
            yield AgentResponse(content="done", finish_reason="stop")

    AgentRouter._backends = {"fake": _ProgressBackend}

    config = Config()
    config.agents.type = "fake"  # type: ignore[assignment]
    config.agents.defaults.workspace = str(tmp_path)
    bus = MessageBus()

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": bus,
            "workspace": tmp_path,
            "config": config,
        },
    )

    run_task = asyncio.create_task(runtime.run())
    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="telegram",
                sender_id="u1",
                chat_id="c1",
                content="hello",
            )
        )

        progress = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        tool_hint = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        final = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
    finally:
        runtime.stop()
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

    assert progress.content == "thinking"
    assert progress.metadata["_progress"] is True
    assert progress.metadata["_tool_hint"] is False

    assert tool_hint.content == 'Tool: read_file("README.md")'
    assert tool_hint.metadata["_progress"] is True
    assert tool_hint.metadata["_tool_hint"] is True


def test_router_runtime_tool_hint_formats_kind_prefixes() -> None:
    from nanobot.agent.runtime import AgentRuntime

    assert AgentRuntime._tool_hint([{"name": "read_file", "input": {"path": "README.md"}, "kind": "tool"}]) == 'Tool: read_file("README.md")'
    assert AgentRuntime._tool_hint([{"name": "skill_writer", "input": {"query": "x"}, "kind": "skill"}]) == 'Skill: skill_writer("x")'
    assert AgentRuntime._tool_hint([{"name": "github_search", "input": {"query": "x"}, "kind": "mcp"}]) == 'MCP: github_search("x")'
