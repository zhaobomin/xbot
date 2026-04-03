"""Tests for run() dispatch sequencing with real task registration order."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from xbot.agent.runtime import AgentRuntime, SessionPhase, SessionStateMachine
from xbot.agent.state.checker import StateConsistencyChecker
from xbot.agent.state.coordinator import SessionStateCoordinator
from xbot.agent.state.store import SessionStore
from xbot.bus.events import InboundMessage, OutboundMessage


class _OneShotBus:
    """A bus that emits one inbound message, then lets runtime exit."""

    def __init__(self, msg: InboundMessage):
        self._msg = msg
        self._consumed = False
        self._published: list[OutboundMessage] = []
        self._session_pending_permission_requests: dict[str, str] = {}
        self._session_pending_interaction_requests: dict[str, str] = {}

    async def consume_inbound(self) -> InboundMessage:
        if not self._consumed:
            self._consumed = True
            return self._msg
        # Keep waiting so asyncio.wait_for in run() times out and re-checks _running.
        await asyncio.sleep(2)
        raise RuntimeError("unreachable")

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        self._published.append(msg)

    def get_pending_request_for_session(self, session_key: str) -> str | None:
        return self._session_pending_permission_requests.get(session_key)

    def get_pending_interaction_for_session(self, session_key: str) -> str | None:
        return self._session_pending_interaction_requests.get(session_key)


class _MockRuntimeForRun:
    """Minimal runtime shell for binding AgentRuntime methods in tests."""

    def __init__(self, bus: _OneShotBus):
        self.bus = bus
        self._running = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_store = SessionStore()
        self._state_machine = SessionStateMachine()
        self._state_check_enabled = False
        self.sessions = None
        self.channels_config = None
        self.shared_resources = {}
        self.config = MagicMock()
        self.router = MagicMock()
        self.router.backend_type = "test"
        self.router._backend = MagicMock()
        self.router._backend._clients = {}
        self.router._backend._active_task_ids = {}
        self.router._backend._client_last_used = {}
        self._state_checker = StateConsistencyChecker(self)
        self._state_coordinator = SessionStateCoordinator(self, self._session_store)

    async def initialize(self) -> None:
        return None

    def describe_runtime(self) -> str:
        return "test-runtime"

    @staticmethod
    def _is_local_runtime_command(content: str) -> bool:
        return False


def _bind_runtime_methods(runtime: _MockRuntimeForRun) -> None:
    runtime.run = AgentRuntime.run.__get__(runtime, _MockRuntimeForRun)
    runtime._dispatch = AgentRuntime._dispatch.__get__(runtime, _MockRuntimeForRun)
    runtime._handle_permission_response = AgentRuntime._handle_permission_response.__get__(
        runtime, _MockRuntimeForRun
    )
    runtime._handle_interaction_response = AgentRuntime._handle_interaction_response.__get__(
        runtime, _MockRuntimeForRun
    )
    runtime._make_task_done_callback = AgentRuntime._make_task_done_callback.__get__(
        runtime, _MockRuntimeForRun
    )
    runtime._spawn_session_task = AgentRuntime._spawn_session_task.__get__(
        runtime, _MockRuntimeForRun
    )
    runtime._set_session_phase = AgentRuntime._set_session_phase.__get__(runtime, _MockRuntimeForRun)
    runtime._sync_session_phase = AgentRuntime._sync_session_phase.__get__(runtime, _MockRuntimeForRun)
    runtime._log_state_snapshot = AgentRuntime._log_state_snapshot.__get__(runtime, _MockRuntimeForRun)
    runtime._bus_progress = AgentRuntime._bus_progress.__get__(runtime, _MockRuntimeForRun)
    runtime.get_session_phase = AgentRuntime.get_session_phase.__get__(runtime, _MockRuntimeForRun)


@pytest.mark.asyncio
async def test_run_loop_dispatch_sequence_ends_idle() -> None:
    msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="hello")
    bus = _OneShotBus(msg)
    runtime = _MockRuntimeForRun(bus)
    _bind_runtime_methods(runtime)

    async def _handle_message(_msg, on_progress=None):
        return OutboundMessage(channel=_msg.channel, chat_id=_msg.chat_id, content="ok")

    runtime._handle_message = _handle_message

    async def _stop_when_done() -> None:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if bus._consumed and not runtime._active_tasks.get(msg.session_key):
                runtime._running = False
                return
        runtime._running = False

    stopper = asyncio.create_task(_stop_when_done())
    await asyncio.wait_for(runtime.run(), timeout=3)
    await stopper

    assert runtime.get_session_phase(msg.session_key) == SessionPhase.IDLE
    assert runtime._active_tasks.get(msg.session_key) in (None, [])


@pytest.mark.asyncio
async def test_run_loop_dispatch_keeps_waiting_permission_when_pending() -> None:
    msg = InboundMessage(channel="test", sender_id="u3", chat_id="c3", content="hello")
    bus = _OneShotBus(msg)
    runtime = _MockRuntimeForRun(bus)
    _bind_runtime_methods(runtime)

    async def _handle_message(_msg, on_progress=None):
        bus._session_pending_permission_requests[_msg.session_key] = "perm-1"
        return OutboundMessage(channel=_msg.channel, chat_id=_msg.chat_id, content="ok")

    runtime._handle_message = _handle_message

    async def _stop_when_done() -> None:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if bus._consumed and not runtime._active_tasks.get(msg.session_key):
                runtime._running = False
                return
        runtime._running = False

    stopper = asyncio.create_task(_stop_when_done())
    await asyncio.wait_for(runtime.run(), timeout=3)
    await stopper

    assert runtime.get_session_phase(msg.session_key) == SessionPhase.WAITING_PERMISSION
    assert runtime._active_tasks.get(msg.session_key) in (None, [])


@pytest.mark.asyncio
async def test_run_loop_dispatch_keeps_waiting_interaction_when_pending() -> None:
    msg = InboundMessage(channel="test", sender_id="u4", chat_id="c4", content="hello")
    bus = _OneShotBus(msg)
    runtime = _MockRuntimeForRun(bus)
    _bind_runtime_methods(runtime)

    async def _handle_message(_msg, on_progress=None):
        bus._session_pending_interaction_requests[_msg.session_key] = "inter-1"
        return OutboundMessage(channel=_msg.channel, chat_id=_msg.chat_id, content="ok")

    runtime._handle_message = _handle_message

    async def _stop_when_done() -> None:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if bus._consumed and not runtime._active_tasks.get(msg.session_key):
                runtime._running = False
                return
        runtime._running = False

    stopper = asyncio.create_task(_stop_when_done())
    await asyncio.wait_for(runtime.run(), timeout=3)
    await stopper

    assert runtime.get_session_phase(msg.session_key) == SessionPhase.WAITING_INTERACTION
    assert runtime._active_tasks.get(msg.session_key) in (None, [])
