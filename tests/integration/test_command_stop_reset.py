"""Integration tests: _do_stop / _do_reset must clear pending permission/interaction waiters.

These tests use a real MessageBus (no mocking) to verify that the command handler
cleanup path correctly unblocks Agent-side waiters. If a waiter is NOT unblocked
within 1 second, the test fails — proving the bug exists.

Covers:
- HIGH-1 hypothesis: _do_stop/_do_reset should call aclear_session_requests
- Verifies the _clear_pending_requests static helper works end-to-end
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from xbot.platform.bus.queue import (
    InteractionRequest,
    InteractionResponse,
    MessageBus,
    PermissionRequest,
    PermissionResponse,
)
from xbot.runtime.core.command_handlers import LocalCommandHandler
from xbot.runtime.state import RuntimeSessionRegistry, SessionEvent, SessionPhase

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bus() -> MessageBus:
    return MessageBus()


@pytest.fixture
def registry() -> RuntimeSessionRegistry:
    return RuntimeSessionRegistry()


@pytest.fixture
def session_key() -> str:
    return "web:testuser:sess1"


# ---------------------------------------------------------------------------
# Tests: _clear_pending_requests (the static helper)
# ---------------------------------------------------------------------------


class TestClearPendingRequests:
    """Verify LocalCommandHandler._clear_pending_requests unblocks waiters via real bus."""

    async def test_clears_permission_waiter(self, bus: MessageBus, session_key: str):
        """A waiting permission coroutine must be unblocked with deny."""
        await bus.publish_permission_request(
            PermissionRequest(
                request_id="perm-stop-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="shell",
                tool_input={"command": "ls"},
                message="Allow shell?",
            )
        )

        # Simulate Agent waiting for user to approve
        waiter = asyncio.create_task(
            bus.wait_permission_response("perm-stop-1", timeout=30.0)
        )
        await asyncio.sleep(0)  # Let waiter register its event

        # Execute the helper (same code path as _do_stop)
        cleared_perm, cleared_inter = await LocalCommandHandler._clear_pending_requests(
            bus, session_key
        )

        # Waiter must be woken within 1s
        response = await asyncio.wait_for(waiter, timeout=1.0)

        assert cleared_perm is True
        assert isinstance(response, PermissionResponse)
        assert response.decision == "deny"
        assert "cleared" in response.reason.lower() or "clear" in response.reason.lower()

    async def test_clears_interaction_waiter(self, bus: MessageBus, session_key: str):
        """A waiting interaction coroutine must be unblocked with cancel."""
        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="int-stop-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                kind="question",
                prompt="Which file?",
            )
        )

        waiter = asyncio.create_task(
            bus.wait_interaction_response("int-stop-1", timeout=30.0)
        )
        await asyncio.sleep(0)

        cleared_perm, cleared_inter = await LocalCommandHandler._clear_pending_requests(
            bus, session_key
        )

        response = await asyncio.wait_for(waiter, timeout=1.0)

        assert cleared_inter is True
        assert isinstance(response, InteractionResponse)
        assert response.action == "cancel"

    async def test_clears_both_permission_and_interaction(
        self, bus: MessageBus, session_key: str
    ):
        """When both a permission and interaction are pending, both must be cleared."""
        await bus.publish_permission_request(
            PermissionRequest(
                request_id="perm-both-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="web_fetch",
                tool_input={"url": "http://example.com"},
                message="Allow fetch?",
            )
        )
        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="int-both-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                kind="confirmation",
                prompt="Proceed?",
            )
        )

        perm_waiter = asyncio.create_task(
            bus.wait_permission_response("perm-both-1", timeout=30.0)
        )
        int_waiter = asyncio.create_task(
            bus.wait_interaction_response("int-both-1", timeout=30.0)
        )
        await asyncio.sleep(0)

        cleared_perm, cleared_inter = await LocalCommandHandler._clear_pending_requests(
            bus, session_key
        )

        perm_resp = await asyncio.wait_for(perm_waiter, timeout=1.0)
        int_resp = await asyncio.wait_for(int_waiter, timeout=1.0)

        assert cleared_perm is True
        assert cleared_inter is True
        assert perm_resp.decision == "deny"
        assert int_resp.action == "cancel"

    async def test_no_pending_is_safe(self, bus: MessageBus):
        """No pending requests should return (False, False), no error."""
        cleared_perm, cleared_inter = await LocalCommandHandler._clear_pending_requests(
            bus, "web:nobody:nosession"
        )
        assert cleared_perm is False
        assert cleared_inter is False

    async def test_bus_is_none(self):
        """Null bus should return (False, False), no crash."""
        cleared_perm, cleared_inter = await LocalCommandHandler._clear_pending_requests(
            None, "web:u:s"
        )
        assert cleared_perm is False
        assert cleared_inter is False


# ---------------------------------------------------------------------------
# Tests: Full _do_stop flow (with mocked AgentService)
# ---------------------------------------------------------------------------


class TestDoStopIntegration:
    """Test _do_stop with real bus + real registry, only mock the AgentService shell."""

    def _make_service_mock(self, bus: MessageBus, registry: RuntimeSessionRegistry):
        """Create minimal AgentService-like object with real bus and registry."""
        svc = MagicMock()
        svc._shared_resources = {"bus": bus, "runtime_registry": registry}
        svc.interrupt_session = AsyncMock(
            return_value={"interrupted": False, "queued_cleared": 0}
        )
        return svc

    def _make_handlers(self, svc) -> LocalCommandHandler:
        handlers = LocalCommandHandler.__new__(LocalCommandHandler)
        handlers._service = svc
        return handlers

    async def test_stop_unblocks_waiter_and_transitions_to_idle(
        self, bus: MessageBus, registry: RuntimeSessionRegistry, session_key: str
    ):
        """Full _do_stop: waiter unblocked + state transitions to IDLE."""
        # Setup: advance session to WAITING_PERMISSION via correct path
        registry.dispatch(session_key, SessionEvent.USER_MESSAGE)  # → ACQUIRING_CLIENT
        registry.dispatch(session_key, SessionEvent.CLIENT_ACQUIRED)  # → SENDING_QUERY
        registry.dispatch(session_key, SessionEvent.QUERY_SENT)  # → RECEIVING_STREAM
        registry.dispatch(session_key, SessionEvent.PERMISSION_PENDING)  # → WAITING_PERMISSION
        assert registry.get_phase(session_key) == SessionPhase.WAITING_PERMISSION

        # Publish permission request
        await bus.publish_permission_request(
            PermissionRequest(
                request_id="perm-full-stop",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="shell",
                tool_input={},
                message="Allow?",
            )
        )
        waiter = asyncio.create_task(
            bus.wait_permission_response("perm-full-stop", timeout=30.0)
        )
        await asyncio.sleep(0)

        # Execute _do_stop
        svc = self._make_service_mock(bus, registry)
        handlers = self._make_handlers(svc)
        result = await handlers._do_stop(session_key, bus)

        # Verify: waiter unblocked
        response = await asyncio.wait_for(waiter, timeout=1.0)
        assert response.decision == "deny"

        # Verify: state is IDLE
        assert registry.get_phase(session_key) == SessionPhase.IDLE

        # Verify: response message mentions "permission"
        assert "permission" in result.lower()

    async def test_stop_with_running_task_interrupted(
        self, bus: MessageBus, registry: RuntimeSessionRegistry, session_key: str
    ):
        """_do_stop must not force IDLE before the SDK idle boundary."""
        # Advance to RECEIVING_STREAM (active processing)
        registry.dispatch(session_key, SessionEvent.USER_MESSAGE)
        registry.dispatch(session_key, SessionEvent.CLIENT_ACQUIRED)
        registry.dispatch(session_key, SessionEvent.QUERY_SENT)
        registry.dispatch(session_key, SessionEvent.INTERRUPT)
        assert registry.get_phase(session_key) == SessionPhase.STOPPING

        svc = self._make_service_mock(bus, registry)
        svc.interrupt_session = AsyncMock(
            return_value={
                "interrupted": True,
                "interrupt_sent": True,
                "confirmed": True,
                "terminal_reason": "aborted_tools",
                "queued_cleared": 2,
                "fallback_disconnect": False,
            }
        )

        handlers = self._make_handlers(svc)
        result = await handlers._do_stop(session_key, bus)

        assert "current SDK turn" in result.lower() or "stopped" in result.lower()
        assert registry.get_phase(session_key) == SessionPhase.STOPPING


# ---------------------------------------------------------------------------
# Tests: Full _do_reset flow
# ---------------------------------------------------------------------------


class TestDoResetIntegration:
    """Test _do_reset with real bus + real registry."""

    def _make_service_mock(self, bus: MessageBus, registry: RuntimeSessionRegistry):
        from unittest.mock import AsyncMock, MagicMock

        svc = MagicMock()
        svc._shared_resources = {"bus": bus, "runtime_registry": registry}
        svc._session_workers = {}
        svc.reset_session = AsyncMock()
        return svc

    def _make_handlers(self, svc) -> LocalCommandHandler:
        handlers = LocalCommandHandler.__new__(LocalCommandHandler)
        handlers._service = svc
        return handlers

    async def test_reset_clears_waiter_and_resets_state(
        self, bus: MessageBus, registry: RuntimeSessionRegistry, session_key: str
    ):
        """Full _do_reset: waiter cleared + state IDLE + SDK context dropped."""
        # Advance to WAITING_INTERACTION via correct path
        registry.dispatch(session_key, SessionEvent.USER_MESSAGE)  # → ACQUIRING_CLIENT
        registry.dispatch(session_key, SessionEvent.CLIENT_ACQUIRED)  # → SENDING_QUERY
        registry.dispatch(session_key, SessionEvent.QUERY_SENT)  # → RECEIVING_STREAM
        registry.dispatch(session_key, SessionEvent.INTERACTION_PENDING)  # → WAITING_INTERACTION
        assert registry.get_phase(session_key) == SessionPhase.WAITING_INTERACTION

        await bus.publish_interaction_request(
            InteractionRequest(
                request_id="int-reset-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                kind="question",
                prompt="Which file?",
            )
        )
        waiter = asyncio.create_task(
            bus.wait_interaction_response("int-reset-1", timeout=30.0)
        )
        await asyncio.sleep(0)

        svc = self._make_service_mock(bus, registry)
        handlers = self._make_handlers(svc)
        result = await handlers._do_reset(session_key, bus)

        # Waiter must be unblocked
        response = await asyncio.wait_for(waiter, timeout=1.0)
        assert response.action == "cancel"

        # State must be IDLE
        assert registry.get_phase(session_key) == SessionPhase.IDLE

        # SDK session must be fully reset (not soft)
        svc.reset_session.assert_called_once_with(session_key, drop_sdk_context=True)

        # Response mentions "reset"
        assert "reset" in result.lower()

    async def test_soft_reset_preserves_sdk_context(
        self, bus: MessageBus, registry: RuntimeSessionRegistry, session_key: str
    ):
        """_do_reset(soft=True) preserves SDK context."""
        # Advance to RECEIVING_STREAM so TURN_COMPLETED is valid
        registry.dispatch(session_key, SessionEvent.USER_MESSAGE)
        registry.dispatch(session_key, SessionEvent.CLIENT_ACQUIRED)
        registry.dispatch(session_key, SessionEvent.QUERY_SENT)

        svc = self._make_service_mock(bus, registry)
        handlers = self._make_handlers(svc)
        result = await handlers._do_reset(session_key, bus, soft=True)

        svc.reset_session.assert_called_once_with(session_key, drop_sdk_context=False)
        assert "preserved" in result.lower() or "soft" in result.lower()
