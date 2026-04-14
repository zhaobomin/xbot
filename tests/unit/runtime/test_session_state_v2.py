from __future__ import annotations

import pytest

from xbot.runtime.core.service import AgentService
from xbot.runtime.state import RuntimeSessionRegistry
from xbot.runtime.state.coordinator import (
    VALID_TRANSITIONS,
    SessionEvent,
    SessionPhase,
)


def test_valid_transition_table_allows_declared_edges() -> None:
    registry = RuntimeSessionRegistry()
    session_key = "test:table"

    for from_phase, allowed_targets in VALID_TRANSITIONS.items():
        state = registry.get_or_create(session_key)
        state.phase = from_phase
        for event in SessionEvent:
            state.phase = from_phase
            registry.dispatch(session_key, event, strict=False)
            target = state.phase
            state.phase = from_phase
            result = registry.dispatch(session_key, event, strict=True)
            expected = (target == from_phase) or (target in allowed_targets)
            assert result is expected


def test_illegal_transition_is_rejected_and_counted() -> None:
    registry = RuntimeSessionRegistry()
    session_key = "test:illegal"

    assert registry.get_phase(session_key) == SessionPhase.IDLE
    ok = registry.dispatch(
        session_key,
        SessionEvent.QUERY_SENT,  # IDLE -> RECEIVING_STREAM is illegal directly
        reason="illegal_direct_query",
        strict=True,
    )
    assert ok is False

    state = registry.get_or_create(session_key)
    assert state.phase == SessionPhase.IDLE
    assert state.illegal_transition_count == 1


@pytest.mark.asyncio
async def test_recovery_success_moves_back_to_acquiring() -> None:
    registry = RuntimeSessionRegistry()
    service = AgentService()
    service._shared_resources = {"runtime_registry": registry}

    async def _release_ok(session_key: str, *, reason: str) -> bool:
        _ = session_key
        _ = reason
        return True

    service._release_session_client = _release_ok  # type: ignore[method-assign]

    ok = await service._attempt_broken_session_recovery("test:recover", reason="unit")
    assert ok is True
    assert registry.get_phase("test:recover") == SessionPhase.ACQUIRING_CLIENT
    assert registry.get_recovery_failures("test:recover") == 0


@pytest.mark.asyncio
async def test_recovery_three_failures_clear_resume_context_once() -> None:
    registry = RuntimeSessionRegistry()
    service = AgentService()
    service._shared_resources = {"runtime_registry": registry}

    cleared: list[str] = []

    async def _release_fail(session_key: str, *, reason: str) -> bool:
        _ = session_key
        _ = reason
        return False

    def _clear_resume(session_key: str) -> None:
        cleared.append(session_key)

    service._release_session_client = _release_fail  # type: ignore[method-assign]
    service._clear_sdk_resume_context = _clear_resume  # type: ignore[method-assign]

    for _ in range(3):
        ok = await service._attempt_broken_session_recovery("test:fail", reason="unit")
        assert ok is False

    assert cleared == ["test:fail"]
    assert registry.get_recovery_failures("test:fail") == 0
    assert registry.get_phase("test:fail") == SessionPhase.BROKEN
