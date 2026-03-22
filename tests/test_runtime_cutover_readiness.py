"""Tests for runtime state management under concurrency/races."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from xbot.agent.runtime import AgentRuntime, SessionPhase, SessionStateMachine
from xbot.agent.state_checker import StateConsistencyChecker
from xbot.agent.state_coordinator import SessionStateCoordinator
from xbot.bus.events import InboundMessage, OutboundMessage


class _QueueBus:
    """Inbound queue bus for run() tests with finite messages."""

    def __init__(self, messages: list[InboundMessage]):
        self._queue = deque(messages)
        self.published: list[OutboundMessage] = []
        self._session_pending_permission_requests: dict[str, str] = {}
        self._session_pending_interaction_requests: dict[str, str] = {}

    async def consume_inbound(self) -> InboundMessage:
        if self._queue:
            return self._queue.popleft()
        await asyncio.sleep(2)
        raise RuntimeError("unreachable")

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        self.published.append(msg)

    def get_pending_request_for_session(self, session_key: str) -> str | None:
        return self._session_pending_permission_requests.get(session_key)

    def get_pending_interaction_for_session(self, session_key: str) -> str | None:
        return self._session_pending_interaction_requests.get(session_key)

    def clear_session_requests(self, session_key: str) -> dict[str, bool]:
        had_perm = self._session_pending_permission_requests.pop(session_key, None) is not None
        had_inter = self._session_pending_interaction_requests.pop(session_key, None) is not None
        return {"permission": had_perm, "interaction": had_inter}


@dataclass
class _BackendStub:
    _clients: dict[str, Any]
    _active_task_ids: dict[str, Any]
    _client_last_used: dict[str, Any]

    async def cancel_session(self, session_key: str) -> int:
        return 0

    async def stop_active_task(self, session_key: str) -> bool:
        return False

    async def interrupt_session(self, session_key: str) -> dict[str, Any]:
        return {"interrupted": False, "usage": None}

    async def reset_session(self, session_key: str) -> None:
        return None


class _RuntimeHarness:
    """Minimal object to bind AgentRuntime methods for deterministic tests."""

    def __init__(self, bus: _QueueBus):
        self.bus = bus
        self._running = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._state_machine = SessionStateMachine()
        self._state_check_enabled = False
        self.sessions = None
        self.channels_config = None
        self.shared_resources = {}
        self.config = MagicMock()
        self.router = MagicMock()
        self.router.backend_type = "test"
        self.router._backend = _BackendStub(_clients={}, _active_task_ids={}, _client_last_used={})
        self.router.backend = self.router._backend
        self._state_checker = StateConsistencyChecker(self)
        self._state_coordinator = SessionStateCoordinator(self)

    async def initialize(self) -> None:
        return None

    def describe_runtime(self) -> str:
        return "cutover-harness"

    @staticmethod
    def _is_local_runtime_command(content: str) -> bool:
        return False


def _bind(runtime: _RuntimeHarness) -> None:
    runtime.run = AgentRuntime.run.__get__(runtime, _RuntimeHarness)
    runtime._dispatch = AgentRuntime._dispatch.__get__(runtime, _RuntimeHarness)
    runtime._handle_permission_response = AgentRuntime._handle_permission_response.__get__(
        runtime, _RuntimeHarness
    )
    runtime._handle_interaction_response = AgentRuntime._handle_interaction_response.__get__(
        runtime, _RuntimeHarness
    )
    runtime._make_task_done_callback = AgentRuntime._make_task_done_callback.__get__(
        runtime, _RuntimeHarness
    )
    runtime._set_session_phase = AgentRuntime._set_session_phase.__get__(runtime, _RuntimeHarness)
    runtime._sync_session_phase = AgentRuntime._sync_session_phase.__get__(runtime, _RuntimeHarness)
    runtime._log_state_snapshot = AgentRuntime._log_state_snapshot.__get__(runtime, _RuntimeHarness)
    runtime._bus_progress = AgentRuntime._bus_progress.__get__(runtime, _RuntimeHarness)
    runtime.get_session_phase = AgentRuntime.get_session_phase.__get__(runtime, _RuntimeHarness)
    runtime._terminate_session = AgentRuntime._terminate_session.__get__(runtime, _RuntimeHarness)
    runtime._on_backend_client_cleanup = AgentRuntime._on_backend_client_cleanup.__get__(
        runtime, _RuntimeHarness
    )
    runtime.get_session_state = AgentRuntime.get_session_state.__get__(runtime, _RuntimeHarness)


async def _run_until_quiet(runtime: _RuntimeHarness, session_keys: set[str], timeout_s: float = 5.0) -> None:
    async def _stopper() -> None:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.01)
            if all(not runtime._active_tasks.get(s) for s in session_keys):
                runtime._running = False
                return
        runtime._running = False

    stopper = asyncio.create_task(_stopper())
    await asyncio.wait_for(runtime.run(), timeout=timeout_s + 1)
    await stopper


@pytest.mark.asyncio
async def test_run_concurrent_same_session_converges_idle() -> None:
    msg1 = InboundMessage(channel="test", sender_id="u", chat_id="same", content="m1")
    msg2 = InboundMessage(channel="test", sender_id="u", chat_id="same", content="m2")
    bus = _QueueBus([msg1, msg2])
    runtime = _RuntimeHarness(bus)
    _bind(runtime)

    async def _handle_message(msg, on_progress=None):
        await asyncio.sleep(0.05)
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="ok")

    runtime._handle_message = _handle_message
    await _run_until_quiet(runtime, {msg1.session_key})

    assert runtime.get_session_phase(msg1.session_key) == SessionPhase.IDLE
    assert runtime._active_tasks.get(msg1.session_key) in (None, [])


@pytest.mark.asyncio
async def test_run_concurrent_multi_session_converges_idle() -> None:
    msg1 = InboundMessage(channel="test", sender_id="u", chat_id="a", content="m1")
    msg2 = InboundMessage(channel="test", sender_id="u", chat_id="b", content="m2")
    bus = _QueueBus([msg1, msg2])
    runtime = _RuntimeHarness(bus)
    _bind(runtime)

    async def _handle_message(msg, on_progress=None):
        await asyncio.sleep(0.05)
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="ok")

    runtime._handle_message = _handle_message
    await _run_until_quiet(runtime, {msg1.session_key, msg2.session_key})

    assert runtime.get_session_phase(msg1.session_key) == SessionPhase.IDLE
    assert runtime.get_session_phase(msg2.session_key) == SessionPhase.IDLE
    assert runtime._active_tasks.get(msg1.session_key) in (None, [])
    assert runtime._active_tasks.get(msg2.session_key) in (None, [])


@pytest.mark.asyncio
async def test_terminate_race_with_running_dispatch_recovers_idle() -> None:
    msg = InboundMessage(channel="test", sender_id="u", chat_id="race1", content="m")
    bus = _QueueBus([])
    runtime = _RuntimeHarness(bus)
    _bind(runtime)

    gate = asyncio.Event()

    async def _handle_message(_msg, on_progress=None):
        await gate.wait()
        return OutboundMessage(channel=_msg.channel, chat_id=_msg.chat_id, content="ok")

    runtime._handle_message = _handle_message

    task = asyncio.create_task(runtime._dispatch(msg))
    runtime._active_tasks.setdefault(msg.session_key, []).append(task)
    runtime._set_session_phase(msg.session_key, SessionPhase.RUNNING, reason="dispatch_started")
    task.add_done_callback(runtime._make_task_done_callback(msg.session_key))

    await asyncio.sleep(0.05)
    state = await runtime._terminate_session(msg.session_key, hard_reset=False)
    gate.set()
    await asyncio.sleep(0)

    assert state["cancelled"] >= 1
    assert runtime.get_session_phase(msg.session_key) == SessionPhase.IDLE
    assert runtime._active_tasks.get(msg.session_key) in (None, [])


@pytest.mark.asyncio
async def test_hard_reset_clears_state_and_lock() -> None:
    session_key = "test:hard-reset"
    bus = _QueueBus([])
    runtime = _RuntimeHarness(bus)
    _bind(runtime)

    runtime._state_machine.force_transition(session_key, SessionPhase.RUNNING, reason="seed")
    runtime._session_locks[session_key] = asyncio.Lock()
    runtime._active_tasks[session_key] = []

    result = await runtime._terminate_session(session_key, hard_reset=True)

    assert result["backend_cancelled"] == 0
    assert session_key not in runtime._state_machine._states
    assert session_key not in runtime._session_locks


def test_backend_client_cleanup_running_session_cleans_state() -> None:
    session_key = "test:cleanup-running"
    bus = _QueueBus([])
    runtime = _RuntimeHarness(bus)
    _bind(runtime)

    runtime._state_machine.force_transition(session_key, SessionPhase.RUNNING, reason="seed")
    runtime._active_tasks[session_key] = [MagicMock()]
    runtime._session_locks[session_key] = asyncio.Lock()

    runtime._on_backend_client_cleanup(session_key)

    assert runtime.get_session_phase(session_key) == SessionPhase.IDLE
    assert session_key not in runtime._active_tasks
    assert session_key not in runtime._session_locks


def test_backend_client_cleanup_idle_session_noop() -> None:
    session_key = "test:cleanup-idle"
    bus = _QueueBus([])
    runtime = _RuntimeHarness(bus)
    _bind(runtime)

    runtime._state_machine.force_transition(session_key, SessionPhase.IDLE, reason="seed")
    runtime._active_tasks[session_key] = []
    runtime._session_locks[session_key] = asyncio.Lock()

    runtime._on_backend_client_cleanup(session_key)

    assert runtime.get_session_phase(session_key) == SessionPhase.IDLE
    # Idle path should not force cleanup
    assert session_key in runtime._active_tasks
    assert session_key in runtime._session_locks


@pytest.mark.asyncio
async def test_run_burst_same_session_converges_idle() -> None:
    messages = [
        InboundMessage(channel="test", sender_id="u", chat_id="burst", content=f"m{i}")
        for i in range(10)
    ]
    bus = _QueueBus(messages)
    runtime = _RuntimeHarness(bus)
    _bind(runtime)

    async def _handle_message(msg, on_progress=None):
        await asyncio.sleep(0.01)
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="ok")

    runtime._handle_message = _handle_message
    session_key = messages[0].session_key
    await _run_until_quiet(runtime, {session_key}, timeout_s=6.0)

    assert runtime.get_session_phase(session_key) == SessionPhase.IDLE
    assert runtime._active_tasks.get(session_key) in (None, [])


@pytest.mark.asyncio
async def test_waiting_permission_state_survives_done_callback_sync() -> None:
    msg = InboundMessage(channel="test", sender_id="u", chat_id="wait-survive", content="m")
    bus = _QueueBus([msg])
    runtime = _RuntimeHarness(bus)
    _bind(runtime)

    async def _handle_message(_msg, on_progress=None):
        bus._session_pending_permission_requests[_msg.session_key] = "perm-survive"
        await asyncio.sleep(0.01)
        return OutboundMessage(channel=_msg.channel, chat_id=_msg.chat_id, content="ok")

    runtime._handle_message = _handle_message
    await _run_until_quiet(runtime, {msg.session_key}, timeout_s=4.0)

    # done callback sync should still keep WAITING_PERMISSION when pending exists
    assert runtime.get_session_phase(msg.session_key) == SessionPhase.WAITING_PERMISSION
    assert runtime._active_tasks.get(msg.session_key) in (None, [])