from __future__ import annotations

import asyncio
from typing import Any

import pytest

from xbot.agent.protocol import AgentContext, AgentResponse
from xbot.agent.router import AgentRouter
from xbot.bus.events import OutboundMessage
from xbot.bus.events import InboundMessage
from xbot.bus.queue import InteractionRequest
from xbot.bus.queue import PermissionRequest
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
    assert "!reset" in response
    assert "/reset" in response
    assert "!state" in response
    assert "/state" in response
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

    # Use !help for local help command (all / commands go to SDK)
    response = await runtime.process_direct("!help")

    assert "command reference" in response
    assert "Claude SDK slash commands" in response


@pytest.mark.asyncio
async def test_router_runtime_slash_state_is_local_alias(tmp_path) -> None:
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

    response = await runtime.process_direct("/state")

    assert "session: cli:direct" in response.lower()
    assert "phase: idle" in response.lower()
    assert response != "echo:/state"


@pytest.mark.asyncio
async def test_router_runtime_session_commands_fail_gracefully_without_sdk_session_api(tmp_path) -> None:
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

    response = await runtime.process_direct("!session list")
    assert "does not support sdk session listing" in response.lower()

    response = await runtime.process_direct("!session delete")
    assert "does not support sdk session deletion" in response.lower()

    response = await runtime.process_direct("!session fork")
    assert "does not support sdk session forking" in response.lower()


@pytest.mark.asyncio
async def test_router_runtime_session_info_uses_target_session_key(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    backend = _FakeBackend()
    backend._resolve_sdk_session_id = lambda session_key: {
        "cli:direct": "sdk_current",
        "cli:other": "sdk_other",
    }.get(session_key)

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

    response = await runtime.process_direct("!session info cli:other")

    assert "cli:other" in response
    assert "sdk_other" in response


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

    # Use !help for local help command (all / commands go to SDK)
    response = await runtime.process_direct("!help")

    assert "/help" in response
    assert "/clear" in response
    assert "/compact" in response


@pytest.mark.asyncio
async def test_router_runtime_help_merges_sdk_commands_with_fallback_baseline(tmp_path) -> None:
    """Regression: !help must remain complete when SDK command discovery is partial."""
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    class _PartialCommandBackend(_FakeBackend):
        async def get_session_commands(self, session_key: str) -> list[str]:
            # Simulate SDK returning only a subset of user-known slash commands.
            return ["/compact", "/debug"]

    AgentRouter._backends = {"claude_sdk": _PartialCommandBackend}

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

    # Use !help for local help command (all / commands go to SDK)
    response = await runtime.process_direct("!help")

    assert "/debug" in response
    # Baseline compatibility commands should always be visible.
    assert "/help" in response
    assert "/clear" in response
    assert "/compact" in response


@pytest.mark.asyncio
async def test_router_runtime_new_is_local_clear(tmp_path) -> None:
    """Test that /new is handled locally (not forwarded to SDK)."""
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

    # /new is handled locally, not forwarded to SDK
    response = await runtime.process_direct("/new")

    assert "Session cleared" in response
    # Backend is initialized during _do_clear_session -> backend.reset_session
    assert backend.initialized is True


@pytest.mark.asyncio
async def test_router_runtime_restart_background_errors_are_retrieved(tmp_path) -> None:
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

    errors: list[Exception] = []

    async def _boom() -> None:
        raise RuntimeError("restart failed")

    runtime._do_restart = _boom  # type: ignore[method-assign]
    runtime._record_background_task_error = lambda name, exc: errors.append(exc)  # type: ignore[method-assign]

    response = await runtime.process_direct("!restart")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert response == "Restarting..."
    assert len(errors) == 1
    assert str(errors[0]) == "restart failed"


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

    assert "2 background task" in response


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
async def test_router_runtime_reset_performs_hard_session_cleanup(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    backend = _FakeBackend()
    calls: list[tuple[str, str]] = []

    async def _cancel_session(session_key: str) -> int:
        calls.append(("cancel_session", session_key))
        return 1

    async def _stop_active_task(session_key: str) -> bool:
        calls.append(("stop_active_task", session_key))
        return True

    async def _interrupt_session(session_key: str) -> dict[str, Any]:
        calls.append(("interrupt_session", session_key))
        return {"interrupted": True, "usage": None}

    async def _reset_session(session_key: str) -> None:
        calls.append(("reset_session", session_key))

    backend.cancel_session = _cancel_session  # type: ignore[method-assign]
    backend.stop_active_task = _stop_active_task  # type: ignore[method-assign]
    backend.interrupt_session = _interrupt_session  # type: ignore[method-assign]
    backend.reset_session = _reset_session  # type: ignore[method-assign]

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

    response = await runtime.process_direct("!reset")

    assert "reset" in response.lower()
    assert calls == [
        ("cancel_session", "cli:direct"),
        ("stop_active_task", "cli:direct"),
        ("interrupt_session", "cli:direct"),
        ("reset_session", "cli:direct"),
    ]


@pytest.mark.asyncio
async def test_router_runtime_reset_clears_pending_bus_requests_for_session(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    AgentRouter._backends = {"claude_sdk": _FakeBackend}

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    bus = MessageBus()

    await bus.publish_permission_request(
        PermissionRequest(
            request_id="perm-1",
            session_key="cli:direct",
            channel="cli",
            chat_id="direct",
            tool_name="exec_command",
            tool_input={"cmd": "echo hi"},
            message="allow?",
        )
    )
    await bus.publish_interaction_request(
        InteractionRequest(
            request_id="int-1",
            session_key="cli:direct",
            channel="cli",
            chat_id="direct",
            kind="question",
            prompt="continue?",
        )
    )

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": bus,
            "workspace": tmp_path,
            "config": config,
        },
    )

    # Use !reset to clear pending requests (local command)
    await runtime.process_direct("!reset")

    assert bus.get_pending_request_for_session("cli:direct") is None
    assert bus.get_pending_interaction_for_session("cli:direct") is None


@pytest.mark.asyncio
async def test_router_runtime_stop_clears_pending_bus_requests_for_session(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    AgentRouter._backends = {"claude_sdk": _FakeBackend}

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    bus = MessageBus()

    await bus.publish_permission_request(
        PermissionRequest(
            request_id="perm-stop-1",
            session_key="cli:direct",
            channel="cli",
            chat_id="direct",
            tool_name="exec_command",
            tool_input={"cmd": "echo hi"},
            message="allow?",
        )
    )
    await bus.publish_interaction_request(
        InteractionRequest(
            request_id="int-stop-1",
            session_key="cli:direct",
            channel="cli",
            chat_id="direct",
            kind="question",
            prompt="continue?",
        )
    )

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": bus,
            "workspace": tmp_path,
            "config": config,
        },
    )

    # Use !stop to clear pending requests (local command)
    response = await runtime.process_direct("!stop")

    assert "pending permission" in response
    assert "pending interaction" in response
    assert bus.get_pending_request_for_session("cli:direct") is None
    assert bus.get_pending_interaction_for_session("cli:direct") is None
    assert runtime.get_session_state("cli:direct") == "idle"


@pytest.mark.asyncio
async def test_router_runtime_process_direct_sets_session_state_back_to_idle(tmp_path) -> None:
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

    response = await runtime.process_direct("hello")
    assert response == "echo:hello"
    assert runtime.get_session_state("cli:direct") == "idle"


@pytest.mark.asyncio
async def test_router_runtime_process_managed_direct_tracks_running_state_and_tasks(tmp_path) -> None:
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

    observed: dict[str, int | str] = {}

    async def _fake_handle(msg: InboundMessage, on_progress=None) -> OutboundMessage:
        observed["phase"] = runtime.get_session_state(msg.session_key)
        observed["active_tasks"] = len(runtime._state_coordinator.get_active_tasks(msg.session_key))
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=f"ok:{msg.content}")

    runtime._handle_message = _fake_handle  # type: ignore[method-assign]

    response = await runtime.process_managed_direct(
        "hello",
        session_key="cron:job-1",
        channel="cli",
        chat_id="direct",
    )

    assert response == "ok:hello"
    assert observed == {"phase": "running", "active_tasks": 1}


@pytest.mark.asyncio
async def test_router_runtime_process_managed_direct_cleans_up_cron_session(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus
    from xbot.session.manager import SessionManager

    AgentRouter._backends = {"claude_sdk": _FakeBackend}

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

    response = await runtime.process_managed_direct(
        "hello",
        session_key="cron:job-2",
        channel="cli",
        chat_id="direct",
    )

    assert response == "echo:hello"
    assert runtime._session_store.get("cron:job-2") is None
    assert runtime._state_coordinator.has_session("cron:job-2") is False
    assert sessions.get("cron:job-2") is None
    assert sessions._get_session_path("cron:job-2").exists() is False


@pytest.mark.asyncio
async def test_router_runtime_ephemeral_cleanup_uses_state_coordinator_cleanup(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus
    from xbot.session.manager import SessionManager

    AgentRouter._backends = {"claude_sdk": _FakeBackend}

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

    called: list[str] = []
    original_cleanup = runtime._state_coordinator.cleanup_session

    async def _cleanup(session_key: str):
        called.append(session_key)
        return await original_cleanup(session_key)

    runtime._state_coordinator.cleanup_session = _cleanup  # type: ignore[method-assign]

    response = await runtime.process_managed_direct(
        "hello",
        session_key="cron:job-coordinator",
        channel="cli",
        chat_id="direct",
    )

    assert response == "echo:hello"
    assert called == ["cron:job-coordinator"]
    assert runtime._state_coordinator.has_session("cron:job-coordinator") is False


@pytest.mark.asyncio
async def test_router_runtime_process_managed_direct_forgets_ephemeral_client_lifecycle(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus
    from xbot.session.manager import SessionManager

    class _LifecycleBackend(_FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.forgotten: list[str] = []

        async def forget_client_lifecycle(self, session_key: str) -> None:
            self.forgotten.append(session_key)

    AgentRouter._backends = {"claude_sdk": _LifecycleBackend}

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

    response = await runtime.process_managed_direct(
        "hello",
        session_key="cron:job-lifecycle",
        channel="cli",
        chat_id="direct",
    )

    assert response == "echo:hello"
    backend = runtime.router.backend
    assert backend.forgotten == ["cron:job-lifecycle"]


@pytest.mark.asyncio
async def test_router_runtime_process_managed_direct_serializes_same_session(tmp_path) -> None:
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

    first, second = await asyncio.gather(
        runtime.process_managed_direct("first", session_key="cron:job-3"),
        runtime.process_managed_direct("second", session_key="cron:job-3"),
    )

    assert (first, second) == ("ok:first", "ok:second")
    assert max_active == 1
    assert events == [("start", "first"), ("end", "first"), ("start", "second"), ("end", "second")]


@pytest.mark.asyncio
async def test_router_runtime_state_command_reports_session_diagnostics(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import InteractionRequest, MessageBus, PermissionRequest

    AgentRouter._backends = {"claude_sdk": _FakeBackend}

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    bus = MessageBus()

    await bus.publish_permission_request(
        PermissionRequest(
            request_id="perm-state-1",
            session_key="cli:direct",
            channel="cli",
            chat_id="direct",
            tool_name="exec_command",
            tool_input={"cmd": "echo hi"},
            message="allow?",
        )
    )
    await bus.publish_interaction_request(
        InteractionRequest(
            request_id="int-state-1",
            session_key="cli:direct",
            channel="cli",
            chat_id="direct",
            kind="question",
            prompt="continue?",
        )
    )

    runtime = AgentRuntime(
        config=config,
        shared_resources={
            "bus": bus,
            "workspace": tmp_path,
            "config": config,
        },
    )
    # Explicit set for deterministic output
    from xbot.agent.runtime import SessionPhase
    runtime._set_session_phase("cli:direct", SessionPhase.RUNNING, reason="test")

    # Use !state for local diagnostics (all / commands go to SDK)
    response = await runtime.process_direct("!state")

    assert "Session: cli:direct" in response
    assert "Phase: running" in response
    assert "Pending permission: perm-state-1" in response
    assert "Pending interaction: int-state-1" in response
    assert "Backend: claude_sdk" in response


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


@pytest.mark.asyncio
async def test_router_runtime_process_direct_normalizes_bare_cli_session_key(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    class _CaptureBackend(_FakeBackend):
        async def process(self, context: AgentContext):
            yield AgentResponse(
                content=context.session_key,
                finish_reason="stop",
            )

    AgentRouter._backends = {"claude_sdk": _CaptureBackend}

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

    response = await runtime.process_direct("hello", session_key="direct")

    assert response == "cli:direct"


@pytest.mark.asyncio
async def test_router_runtime_process_direct_preserves_prefixed_cli_session_key(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    class _CaptureBackend(_FakeBackend):
        async def process(self, context: AgentContext):
            yield AgentResponse(
                content=context.session_key,
                finish_reason="stop",
            )

    AgentRouter._backends = {"claude_sdk": _CaptureBackend}

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

    response = await runtime.process_direct("hello", session_key="cli:direct")

    assert response == "cli:direct"


@pytest.mark.asyncio
async def test_router_runtime_process_direct_namespaces_reserved_heartbeat_session_key(tmp_path) -> None:
    from xbot.agent.runtime import AgentRuntime
    from xbot.bus.queue import MessageBus

    class _CaptureBackend(_FakeBackend):
        async def process(self, context: AgentContext):
            yield AgentResponse(
                content=context.session_key,
                finish_reason="stop",
            )

    AgentRouter._backends = {"claude_sdk": _CaptureBackend}

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

    response = await runtime.process_direct("hello", session_key="heartbeat")

    assert response == "cli:heartbeat"


@pytest.mark.asyncio
async def test_router_runtime_process_direct_rejects_non_cli_prefixed_session_key(tmp_path) -> None:
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

    with pytest.raises(ValueError, match="CLI session key"):
        await runtime.process_direct("hello", session_key="cron:abc")
