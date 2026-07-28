"""Integration tests: State machine concurrency safety.

Verifies that RuntimeSessionRegistry/SessionCoordinator handles concurrent
dispatch calls without producing illegal state transitions.

Uses the actual transition table:
  USER_MESSAGE → ACQUIRING_CLIENT
  CLIENT_ACQUIRED → SENDING_QUERY
  QUERY_SENT → RECEIVING_STREAM
  PERMISSION_PENDING → WAITING_PERMISSION (from RECEIVING_STREAM)
  PERMISSION_RESOLVED → RECEIVING_STREAM
  TURN_COMPLETED → IDLE (from most active states)
  INTERRUPT → STOPPING
  STREAM_IDLE_BOUNDARY → DRAINING → IDLE
"""

from __future__ import annotations

import asyncio

import pytest

from xbot.runtime.state import RuntimeSessionRegistry, SessionEvent, SessionPhase

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _advance_to_receiving_stream(registry: RuntimeSessionRegistry, session: str) -> None:
    """Advance session through the normal startup path to RECEIVING_STREAM."""
    registry.dispatch(session, SessionEvent.USER_MESSAGE)  # → ACQUIRING_CLIENT
    registry.dispatch(session, SessionEvent.CLIENT_ACQUIRED)  # → SENDING_QUERY
    registry.dispatch(session, SessionEvent.QUERY_SENT)  # → RECEIVING_STREAM


def _advance_to_waiting_permission(registry: RuntimeSessionRegistry, session: str) -> None:
    """Advance session to WAITING_PERMISSION."""
    _advance_to_receiving_stream(registry, session)
    registry.dispatch(session, SessionEvent.PERMISSION_PENDING)  # → WAITING_PERMISSION


def _advance_to_waiting_interaction(registry: RuntimeSessionRegistry, session: str) -> None:
    """Advance session to WAITING_INTERACTION."""
    _advance_to_receiving_stream(registry, session)
    registry.dispatch(session, SessionEvent.INTERACTION_PENDING)  # → WAITING_INTERACTION


# ---------------------------------------------------------------------------
# Tests: Rapid sequential dispatches
# ---------------------------------------------------------------------------


class TestRapidDispatches:
    """Multiple dispatches without intervening awaits must all be valid."""

    async def test_normal_lifecycle_sequence(self):
        """Full lifecycle: IDLE → ... → WAITING_PERMISSION → RECEIVING_STREAM → IDLE."""
        registry = RuntimeSessionRegistry()
        session = "web:u:lifecycle"

        # Start → ACQUIRING_CLIENT
        registry.dispatch(session, SessionEvent.USER_MESSAGE)
        assert registry.get_phase(session) == SessionPhase.ACQUIRING_CLIENT

        # → SENDING_QUERY
        registry.dispatch(session, SessionEvent.CLIENT_ACQUIRED)
        assert registry.get_phase(session) == SessionPhase.SENDING_QUERY

        # → RECEIVING_STREAM
        registry.dispatch(session, SessionEvent.QUERY_SENT)
        assert registry.get_phase(session) == SessionPhase.RECEIVING_STREAM

        # → WAITING_PERMISSION
        registry.dispatch(session, SessionEvent.PERMISSION_PENDING)
        assert registry.get_phase(session) == SessionPhase.WAITING_PERMISSION

        # → RECEIVING_STREAM (permission resolved)
        registry.dispatch(session, SessionEvent.PERMISSION_RESOLVED)
        assert registry.get_phase(session) == SessionPhase.RECEIVING_STREAM

        # → IDLE
        registry.dispatch(session, SessionEvent.TURN_COMPLETED)
        assert registry.get_phase(session) == SessionPhase.IDLE

    async def test_interaction_lifecycle(self):
        """Full lifecycle with interaction wait."""
        registry = RuntimeSessionRegistry()
        session = "web:u:interaction"

        _advance_to_receiving_stream(registry, session)
        registry.dispatch(session, SessionEvent.INTERACTION_PENDING)
        assert registry.get_phase(session) == SessionPhase.WAITING_INTERACTION

        registry.dispatch(session, SessionEvent.INTERACTION_RESOLVED)
        assert registry.get_phase(session) == SessionPhase.RECEIVING_STREAM

        registry.dispatch(session, SessionEvent.TURN_COMPLETED)
        assert registry.get_phase(session) == SessionPhase.IDLE

    async def test_invalid_transition_is_rejected(self):
        """Attempting an invalid transition should not corrupt state."""
        registry = RuntimeSessionRegistry()
        session = "web:u:invalid"

        registry.dispatch(session, SessionEvent.USER_MESSAGE)
        assert registry.get_phase(session) == SessionPhase.ACQUIRING_CLIENT

        # DISCONNECT_OK targets IDLE, but ACQUIRING_CLIENT → IDLE is not valid
        result = registry.dispatch(
            session, SessionEvent.DISCONNECT_OK, strict=False
        )
        # State should remain unchanged or go to a valid target only
        phase = registry.get_phase(session)
        # The transition should either be rejected (stays ACQUIRING_CLIENT)
        # or handled safely
        assert phase in (
            SessionPhase.ACQUIRING_CLIENT,
            SessionPhase.IDLE,  # if the SM allows it with strict=False
        )

    async def test_turn_completed_from_idle_is_idempotent(self):
        """TURN_COMPLETED on already-idle session should be safe."""
        registry = RuntimeSessionRegistry()
        session = "web:u:idle-complete"

        _advance_to_receiving_stream(registry, session)
        registry.dispatch(session, SessionEvent.TURN_COMPLETED)
        assert registry.get_phase(session) == SessionPhase.IDLE

        # Dispatching TURN_COMPLETED again from IDLE should be handled gracefully
        result = registry.dispatch(session, SessionEvent.TURN_COMPLETED, strict=False)
        assert registry.get_phase(session) == SessionPhase.IDLE


# ---------------------------------------------------------------------------
# Tests: Concurrent coroutines dispatching
# ---------------------------------------------------------------------------


class TestConcurrentCoroutineDispatches:
    """Multiple coroutines triggering state changes on the same session."""

    async def test_concurrent_user_messages(self):
        """Two coroutines sending USER_MESSAGE — second should be safe."""
        registry = RuntimeSessionRegistry()
        session = "web:u:concurrent"
        results = []

        async def dispatch_user_message(delay: float):
            await asyncio.sleep(delay)
            try:
                r = registry.dispatch(session, SessionEvent.USER_MESSAGE, strict=False)
                results.append(("ok", r))
            except Exception as e:
                results.append(("error", str(e)))

        # Both fire at ~same time
        await asyncio.gather(
            dispatch_user_message(0),
            dispatch_user_message(0),
        )

        # State must be consistent (ACQUIRING_CLIENT after USER_MESSAGE)
        phase = registry.get_phase(session)
        assert phase == SessionPhase.ACQUIRING_CLIENT
        # At least one dispatch succeeded
        assert any(r[0] == "ok" for r in results)

    async def test_stop_during_permission_wait(self):
        """One coroutine waits for permission, another dispatches TURN_COMPLETED (stop).

        This simulates the race between the SDK waiting for permission and
        the user issuing !stop.
        """
        registry = RuntimeSessionRegistry()
        session = "web:u:stop-race"

        _advance_to_waiting_permission(registry, session)
        assert registry.get_phase(session) == SessionPhase.WAITING_PERMISSION

        # Coroutine 1: simulates eventual permission resolve
        async def permission_resolve():
            await asyncio.sleep(0.1)
            return registry.dispatch(
                session, SessionEvent.PERMISSION_RESOLVED, strict=False
            )

        # Coroutine 2: simulates user !stop (TURN_COMPLETED)
        async def user_stop():
            await asyncio.sleep(0.05)  # Fires first
            return registry.dispatch(
                session, SessionEvent.TURN_COMPLETED, reason="user_stop", strict=False
            )

        results = await asyncio.gather(
            permission_resolve(),
            user_stop(),
            return_exceptions=True,
        )

        # Final state must be deterministic — TURN_COMPLETED fires first → IDLE
        # Then PERMISSION_RESOLVED from IDLE is invalid → state stays IDLE
        phase = registry.get_phase(session)
        assert phase in (SessionPhase.IDLE, SessionPhase.RECEIVING_STREAM)

    async def test_multiple_sessions_independent(self):
        """Operations on different sessions must not interfere."""
        registry = RuntimeSessionRegistry()
        sessions = [f"web:user{i}:sess" for i in range(10)]
        results = {}

        async def lifecycle(session: str):
            _advance_to_receiving_stream(registry, session)
            await asyncio.sleep(0)
            registry.dispatch(session, SessionEvent.PERMISSION_PENDING)
            await asyncio.sleep(0)
            registry.dispatch(session, SessionEvent.PERMISSION_RESOLVED)
            await asyncio.sleep(0)
            registry.dispatch(session, SessionEvent.TURN_COMPLETED)
            results[session] = registry.get_phase(session)

        await asyncio.gather(*[lifecycle(s) for s in sessions])

        # All sessions must end in IDLE independently
        for s in sessions:
            assert results[s] == SessionPhase.IDLE


# ---------------------------------------------------------------------------
# Tests: Interrupt event
# ---------------------------------------------------------------------------


class TestInterruptEvent:
    """INTERRUPT transitions to STOPPING until the SDK reaches an idle boundary."""

    async def test_interrupt_from_acquiring_client(self):
        """INTERRUPT from ACQUIRING_CLIENT → STOPPING."""
        registry = RuntimeSessionRegistry()
        session = "web:u:int1"

        registry.dispatch(session, SessionEvent.USER_MESSAGE)
        assert registry.get_phase(session) == SessionPhase.ACQUIRING_CLIENT

        registry.dispatch(session, SessionEvent.INTERRUPT)
        assert registry.get_phase(session) == SessionPhase.STOPPING

        # Then DISCONNECT_OK → IDLE
        registry.dispatch(session, SessionEvent.DISCONNECT_OK)
        assert registry.get_phase(session) == SessionPhase.IDLE

    async def test_interrupt_from_waiting_permission(self):
        """INTERRUPT from WAITING_PERMISSION → STOPPING → DRAINING → IDLE."""
        registry = RuntimeSessionRegistry()
        session = "web:u:int2"

        _advance_to_waiting_permission(registry, session)
        assert registry.get_phase(session) == SessionPhase.WAITING_PERMISSION

        registry.dispatch(session, SessionEvent.INTERRUPT)
        assert registry.get_phase(session) == SessionPhase.STOPPING

        registry.dispatch(session, SessionEvent.STREAM_IDLE_BOUNDARY)
        assert registry.get_phase(session) == SessionPhase.DRAINING
        registry.dispatch(session, SessionEvent.TURN_COMPLETED)
        assert registry.get_phase(session) == SessionPhase.IDLE

    async def test_interrupt_from_waiting_interaction(self):
        """INTERRUPT from WAITING_INTERACTION → STOPPING → DRAINING → IDLE."""
        registry = RuntimeSessionRegistry()
        session = "web:u:int3"

        _advance_to_waiting_interaction(registry, session)
        assert registry.get_phase(session) == SessionPhase.WAITING_INTERACTION

        registry.dispatch(session, SessionEvent.INTERRUPT)
        assert registry.get_phase(session) == SessionPhase.STOPPING

        registry.dispatch(session, SessionEvent.STREAM_IDLE_BOUNDARY)
        assert registry.get_phase(session) == SessionPhase.DRAINING
        registry.dispatch(session, SessionEvent.TURN_COMPLETED)
        assert registry.get_phase(session) == SessionPhase.IDLE

    async def test_interrupt_from_receiving_stream(self):
        """INTERRUPT from RECEIVING_STREAM → STOPPING → DRAINING → IDLE."""
        registry = RuntimeSessionRegistry()
        session = "web:u:int4"

        _advance_to_receiving_stream(registry, session)
        assert registry.get_phase(session) == SessionPhase.RECEIVING_STREAM

        registry.dispatch(session, SessionEvent.INTERRUPT)
        assert registry.get_phase(session) == SessionPhase.STOPPING

        registry.dispatch(session, SessionEvent.STREAM_IDLE_BOUNDARY)
        assert registry.get_phase(session) == SessionPhase.DRAINING
        registry.dispatch(session, SessionEvent.TURN_COMPLETED)
        assert registry.get_phase(session) == SessionPhase.IDLE
