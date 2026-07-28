"""Integration tests: RuntimeResponseHandlers null safety.

Verifies that _state_coordinator property handles missing/None runtime_registry
correctly — either raising a clear RuntimeError or gracefully degrading.

Covers:
- HIGH-3 hypothesis: _state_coordinator can return None → NoneType dereference
- Verifies the actual error path when runtime is misconfigured
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from xbot.interaction.response_handlers import RuntimeResponseHandlers
from xbot.platform.bus.queue import MessageBus, PermissionRequest, PermissionResponse
from xbot.runtime.state import SessionEvent, SessionPhase
from xbot.runtime.state.runtime_registry import RuntimeSessionRegistry


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runtime_with_registry(
    bus: MessageBus, registry: RuntimeSessionRegistry
) -> MagicMock:
    """Minimal AgentService mock with real bus and registry."""
    runtime = MagicMock()
    runtime._shared_resources = {"bus": bus, "runtime_registry": registry}
    runtime._interaction_retry_counts = {}
    return runtime


def _make_runtime_without_registry() -> MagicMock:
    """AgentService mock with NO registry — simulates misconfiguration."""
    runtime = MagicMock()
    runtime._shared_resources = {}  # No runtime_registry
    runtime.runtime_registry = None  # Fallback also None
    runtime._interaction_retry_counts = {}
    return runtime


def _make_runtime_empty_shared() -> MagicMock:
    """AgentService mock where _shared_resources is None (startup race)."""
    runtime = MagicMock()
    runtime._shared_resources = None
    runtime.runtime_registry = None
    runtime._interaction_retry_counts = {}
    return runtime


# ---------------------------------------------------------------------------
# Tests: _state_coordinator property
# ---------------------------------------------------------------------------


class TestStateCoordinatorProperty:
    """_state_coordinator must raise RuntimeError (not AttributeError) if missing."""

    def test_raises_runtime_error_when_registry_missing(self):
        """No registry in shared_resources AND no fallback → RuntimeError."""
        runtime = _make_runtime_without_registry()
        handlers = RuntimeResponseHandlers(runtime)

        with pytest.raises(RuntimeError, match="runtime_registry"):
            _ = handlers._state_coordinator

    def test_raises_runtime_error_when_shared_resources_is_none(self):
        """_shared_resources itself is None → RuntimeError."""
        runtime = _make_runtime_empty_shared()
        handlers = RuntimeResponseHandlers(runtime)

        with pytest.raises(RuntimeError, match="runtime_registry"):
            _ = handlers._state_coordinator

    def test_returns_registry_from_shared_resources(self):
        """Normal case: registry exists in shared_resources."""
        bus = MessageBus()
        registry = RuntimeSessionRegistry()
        runtime = _make_runtime_with_registry(bus, registry)
        handlers = RuntimeResponseHandlers(runtime)

        coord = handlers._state_coordinator
        assert coord is registry

    def test_falls_back_to_runtime_attr(self):
        """Fallback: not in shared_resources but available as attribute."""
        registry = RuntimeSessionRegistry()
        runtime = MagicMock()
        runtime._shared_resources = {}  # Empty — no "runtime_registry" key
        runtime.runtime_registry = registry
        runtime._interaction_retry_counts = {}

        handlers = RuntimeResponseHandlers(runtime)
        coord = handlers._state_coordinator
        assert coord is registry


# ---------------------------------------------------------------------------
# Tests: _bus property
# ---------------------------------------------------------------------------


class TestBusProperty:
    """_bus must not crash when bus is unavailable."""

    def test_returns_bus_from_shared_resources(self):
        """Normal case: bus in shared_resources."""
        bus = MessageBus()
        registry = RuntimeSessionRegistry()
        runtime = _make_runtime_with_registry(bus, registry)
        handlers = RuntimeResponseHandlers(runtime)

        assert handlers._bus is bus

    def test_returns_none_when_bus_missing(self):
        """No bus anywhere → returns None (not crash)."""
        runtime = MagicMock()
        runtime._shared_resources = {}
        runtime.bus = None
        runtime._interaction_retry_counts = {}

        handlers = RuntimeResponseHandlers(runtime)
        assert handlers._bus is None


# ---------------------------------------------------------------------------
# Tests: handle_permission_response with real components
# ---------------------------------------------------------------------------


class TestHandlePermissionResponseIntegration:
    """End-to-end permission response handling with real bus + registry."""

    async def test_permission_allow_transitions_state(self):
        """Approving a permission request transitions state back to RECEIVING_STREAM."""
        bus = MessageBus()
        registry = RuntimeSessionRegistry()
        session_key = "web:user1:perm-test"

        # Advance to WAITING_PERMISSION via correct path
        registry.dispatch(session_key, SessionEvent.USER_MESSAGE)
        registry.dispatch(session_key, SessionEvent.CLIENT_ACQUIRED)
        registry.dispatch(session_key, SessionEvent.QUERY_SENT)
        registry.dispatch(session_key, SessionEvent.PERMISSION_PENDING)
        assert registry.get_phase(session_key) == SessionPhase.WAITING_PERMISSION

        # Publish the request so bus has it
        await bus.publish_permission_request(
            PermissionRequest(
                request_id="perm-allow-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="shell",
                tool_input={"command": "echo hi"},
                message="Allow?",
            )
        )

        # Create handler with real components
        runtime = _make_runtime_with_registry(bus, registry)
        handlers = RuntimeResponseHandlers(runtime)

        # Submit the permission response via bus (takes PermissionResponse object)
        await bus.submit_permission_response(
            PermissionResponse(
                request_id="perm-allow-1",
                session_key=session_key,
                decision="allow",
            )
        )

    async def test_permission_deny_transitions_state(self):
        """Denying a permission request still allows the bus flow to complete."""
        bus = MessageBus()
        registry = RuntimeSessionRegistry()
        session_key = "web:user1:perm-deny"

        registry.dispatch(session_key, SessionEvent.USER_MESSAGE)
        registry.dispatch(session_key, SessionEvent.CLIENT_ACQUIRED)
        registry.dispatch(session_key, SessionEvent.QUERY_SENT)
        registry.dispatch(session_key, SessionEvent.PERMISSION_PENDING)

        await bus.publish_permission_request(
            PermissionRequest(
                request_id="perm-deny-1",
                session_key=session_key,
                channel="web",
                chat_id="chat1",
                tool_name="dangerous_tool",
                tool_input={},
                message="Allow?",
            )
        )

        runtime = _make_runtime_with_registry(bus, registry)
        handlers = RuntimeResponseHandlers(runtime)

        await bus.submit_permission_response(
            PermissionResponse(
                request_id="perm-deny-1",
                session_key=session_key,
                decision="deny",
                reason="User rejected",
            )
        )
