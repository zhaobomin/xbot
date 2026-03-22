from __future__ import annotations

import asyncio
from typing import Any

import pytest

from xbot.agent.protocol import AgentContext, AgentResponse
from xbot.agent.router import AgentRouter
from xbot.bus.events import OutboundMessage
from xbot.bus.events import InboundMessage
from xbot.config.schema import Config
from xbot.session.manager import SessionManager


class _FakeBackend:
    name = "claude_sdk"  # Match the hardcoded backend type

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

    async def reset_session(self, session_key: str) -> None:
        return None

    async def cancel_session(self, session_key: str) -> int:
        return 0

    async def stop_active_task(self, session_key: str) -> bool:
        return False

    async def interrupt_session(self, session_key: str) -> dict[str, Any]:
        return {"interrupted": False, "usage": None}

    async def compact_session(self, session_key: str) -> dict[str, Any]:
        return {"messages_consolidated": 0, "tokens_before": 0, "tokens_after": 0, "success": True}

    async def get_session_commands(self, session_key: str) -> list[str]:
        return ["/compact", "/clear", "/help"]


@pytest.mark.asyncio
async def test_router_runtime_process_direct_routes_through_selected_backend(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    AgentRouter._backends = {"claude_sdk": _FakeBackend}

    config = Config()
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
async def test_router_runtime_help_includes_dynamic_sdk_commands(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    backend = _FakeBackend()

    class _BackendFactory:
        def __call__(self):
            return backend

    AgentRouter._backends = {"claude_sdk": _BackendFactory()}  # type: ignore[dict-item]

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": MessageBus(),
            "workspace": tmp_path,
            "config": config,
        },
    )

    response = await runtime.process_direct("!help")

    assert "!restart" in response
    assert "/restart" in response
    assert "/compact" in response
    assert "Claude SDK slash commands" in response
    assert backend.initialized is True


@pytest.mark.asyncio
async def test_router_runtime_slash_help_is_local_compat_entry(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    backend = _FakeBackend()

    class _BackendFactory:
        def __call__(self):
            return backend

    AgentRouter._backends = {"claude_sdk": _BackendFactory()}  # type: ignore[dict-item]

    config = Config()
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

    assert "command reference" in response
    assert "Claude SDK slash commands" in response


@pytest.mark.asyncio
async def test_router_runtime_compact_routes_to_backend(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    backend = _FakeBackend()

    class _BackendFactory:
        def __call__(self):
            return backend

    AgentRouter._backends = {"claude_sdk": _BackendFactory()}  # type: ignore[dict-item]

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": MessageBus(),
            "workspace": tmp_path,
            "config": config,
        },
    )

    response = await runtime.process_direct("/compact")

    assert response == "echo:/compact"
    assert backend.initialized is True


@pytest.mark.asyncio
async def test_router_runtime_help_uses_sdk_fallback_commands_when_discovery_empty(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    class _NoCommandBackend(_FakeBackend):
        async def get_session_commands(self, session_key: str) -> list[str]:
            return []

    AgentRouter._backends = {"claude_sdk": _NoCommandBackend}

    config = Config()
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

    assert "/help" in response
    assert "/clear" in response
    assert "/compact" in response


@pytest.mark.asyncio
async def test_router_runtime_new_is_passthrough_without_local_alias(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    backend = _FakeBackend()

    class _BackendFactory:
        def __call__(self):
            return backend

    AgentRouter._backends = {"claude_sdk": _BackendFactory()}  # type: ignore[dict-item]

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": MessageBus(),
            "workspace": tmp_path,
            "config": config,
        },
    )

    response = await runtime.process_direct("/new")

    assert response == "echo:/new"
    assert backend.initialized is True


@pytest.mark.asyncio
async def test_router_runtime_stop_delegates_backend_session_cancellation(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    backend = _FakeBackend()

    async def _cancel_session(session_key: str) -> int:
        assert session_key == "cli:direct"
        return 2

    backend.cancel_session = _cancel_session  # type: ignore[method-assign]

    class _BackendFactory:
        def __call__(self):
            return backend

    AgentRouter._backends = {"claude_sdk": _BackendFactory()}  # type: ignore[dict-item]

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": MessageBus(),
            "workspace": tmp_path,
            "config": config,
        },
    )

    response = await runtime.process_direct("!stop")

    assert "2 subagent" in response


@pytest.mark.asyncio
async def test_router_runtime_stop_includes_sdk_task_when_stopped(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    backend = _FakeBackend()

    async def _stop_active_task(session_key: str) -> bool:
        assert session_key == "cli:direct"
        return True

    backend.stop_active_task = _stop_active_task  # type: ignore[method-assign]

    class _BackendFactory:
        def __call__(self):
            return backend

    AgentRouter._backends = {"claude_sdk": _BackendFactory()}  # type: ignore[dict-item]

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": MessageBus(),
            "workspace": tmp_path,
            "config": config,
        },
    )

    response = await runtime.process_direct("!stop")

    assert "SDK task" in response


@pytest.mark.asyncio
async def test_router_runtime_process_direct_forwards_progress_deltas(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    class _DeltaBackend(_FakeBackend):
        async def process(self, context: AgentContext):
            yield AgentResponse(content="", is_delta=True, delta_content="thinking")
            yield AgentResponse(content="done", finish_reason="stop")

    AgentRouter._backends = {"claude_sdk": _DeltaBackend}

    config = Config()
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
async def test_router_runtime_dispatch_serializes_same_session(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    AgentRouter._backends = {"claude_sdk": _FakeBackend}

    config = Config()
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

    active = 0
    max_active = 0
    events: list[tuple[str, str]] = []

    async def _fake_handle(msg: InboundMessage, on_progress=None) -> OutboundMessage:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        events.append(("start", msg.content))
        await asyncio.sleep(0.05)
        events.append(("end", msg.content))
        active -= 1
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=f"ok:{msg.content}")

    runtime._handle_message = _fake_handle  # type: ignore[method-assign]

    msg1 = InboundMessage(channel="cli", sender_id="u1", chat_id="same", content="first")
    msg2 = InboundMessage(channel="cli", sender_id="u1", chat_id="same", content="second")
    await asyncio.gather(runtime._dispatch(msg1), runtime._dispatch(msg2))

    assert max_active == 1
    assert events == [("start", "first"), ("end", "first"), ("start", "second"), ("end", "second")]

@pytest.mark.asyncio
async def test_router_runtime_process_direct_forwards_progress_texts(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    class _ProgressBackend(_FakeBackend):
        async def process(self, context: AgentContext):
            yield AgentResponse(content="", progress_texts=["planning", "reading files"])
            yield AgentResponse(content="done", finish_reason="stop")

    AgentRouter._backends = {"claude_sdk": _ProgressBackend}

    config = Config()
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
async def test_router_runtime_process_direct_forwards_usage_summary(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    class _UsageBackend(_FakeBackend):
        async def process(self, context: AgentContext):
            yield AgentResponse(
                content="done",
                finish_reason="stop",
                usage={"input_tokens": 120, "output_tokens": 45},
            )

    AgentRouter._backends = {"claude_sdk": _UsageBackend}

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    config.channels.send_usage_summary = True

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
    assert seen == [("Usage: input 120 tokens, output 45 tokens", False)]


@pytest.mark.asyncio
async def test_router_runtime_run_publishes_progress_messages_to_bus(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    class _ProgressBackend(_FakeBackend):
        async def process(self, context: AgentContext):
            yield AgentResponse(content="", progress_texts=["thinking"])
            yield AgentResponse(content="", tool_calls=[{"name": "read_file", "input": {"path": "README.md"}}])
            yield AgentResponse(content="done", finish_reason="stop")

    AgentRouter._backends = {"claude_sdk": _ProgressBackend}

    config = Config()
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
    assert progress.metadata["_event_type"] == "progress"
    assert progress.metadata["_progress_kind"] == "progress"

    assert tool_hint.content == 'Tool: read_file("README.md")'
    assert tool_hint.metadata["_progress"] is True
    assert tool_hint.metadata["_tool_hint"] is True
    assert tool_hint.metadata["_event_type"] == "tool_call"
    assert tool_hint.metadata["_progress_kind"] == "tool"


@pytest.mark.asyncio
async def test_router_runtime_run_publishes_usage_event_metadata(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    class _UsageBackend(_FakeBackend):
        async def process(self, context: AgentContext):
            yield AgentResponse(
                content="done",
                finish_reason="stop",
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    AgentRouter._backends = {"claude_sdk": _UsageBackend}

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    config.channels.send_usage_summary = True
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

        usage_msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
        final = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
    finally:
        runtime.stop()
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

    assert usage_msg.content == "Usage: input 10 tokens, output 5 tokens"
    assert usage_msg.metadata["_event_type"] == "usage"
    assert usage_msg.metadata["_progress_kind"] == "usage"
    assert usage_msg.metadata["_event_data"]["usage"]["input_tokens"] == 10
    assert final.content == "done"


@pytest.mark.asyncio
async def test_router_runtime_process_direct_forwards_preformatted_tool_hint(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    class _ProgressBackend(_FakeBackend):
        async def process(self, context: AgentContext):
            yield AgentResponse(content="", tool_hint_text='Tool: read_file("README.md")')
            yield AgentResponse(content="done", finish_reason="stop")

    AgentRouter._backends = {"claude_sdk": _ProgressBackend}

    config = Config()
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
    assert seen == [('Tool: read_file("README.md")', True)]


def test_router_runtime_tool_hint_formats_kind_prefixes() -> None:
    from xbot.agent.runtime import AgentRuntime

    assert AgentRuntime._tool_hint([{"name": "read_file", "input": {"path": "README.md"}, "kind": "tool"}]) == 'Tool: read_file("README.md")'
    assert AgentRuntime._tool_hint([{"name": "skill_writer", "input": {"query": "x"}, "kind": "skill"}]) == 'Skill: skill_writer("x")'
    assert AgentRuntime._tool_hint([{"name": "github_search", "input": {"query": "x"}, "kind": "mcp"}]) == 'MCP: github_search("x")'


def test_router_runtime_describe_runtime_includes_backend_and_summary(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    backend = _FakeBackend()
    backend.get_tools_summary = lambda: "builtin_tools=10 | skills=2"  # type: ignore[attr-defined]

    class _BackendFactory:
        def __call__(self):
            return backend

    AgentRouter._backends = {"claude_sdk": _BackendFactory()}  # type: ignore[dict-item]

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": MessageBus(),
            "workspace": tmp_path,
            "config": config,
        },
    )

    runtime.router._backend = backend

    summary = runtime.describe_runtime()

    assert "backend=claude_sdk" in summary
    assert "workspace=" in summary
    assert "builtin_tools=10 | skills=2" in summary


@pytest.mark.asyncio
async def test_router_runtime_writes_session_runtime_trace(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    class _ProgressBackend(_FakeBackend):
        async def process(self, context: AgentContext):
            yield AgentResponse(content="", progress_texts=["planning"])
            yield AgentResponse(content="", tool_hint_text='Tool: read_file("README.md")')
            yield AgentResponse(content="done", finish_reason="stop")

    AgentRouter._backends = {"claude_sdk": _ProgressBackend}

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    sessions = SessionManager(tmp_path)

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": MessageBus(),
            "workspace": tmp_path,
            "config": config,
            "session_manager": sessions,
        },
    )

    response = await runtime.process_direct("hello", session_key="cli:direct")

    assert response == "done"
    trace = sessions.get_or_create("cli:direct").metadata["runtime_trace"]
    assert [entry["event"] for entry in trace] == [
        "request_start",
        "progress",
        "tool_hint",
        "response_complete",
    ]
    assert trace[0]["backend"] == "claude_sdk"
    assert trace[-1]["content_preview"] == "done"
