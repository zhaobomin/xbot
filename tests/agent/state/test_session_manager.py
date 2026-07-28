"""Tests for the reduced RuntimeSessionRegistry API."""

from __future__ import annotations

import pytest

from xbot.runtime.state.machine import SessionEvent, SessionPhase
from xbot.runtime.state.runtime_registry import RuntimeSessionRegistry


def test_get_or_create_and_routing_resolution() -> None:
    manager = RuntimeSessionRegistry()
    key = "slack:C12345"

    assert manager.get(key) is None
    assert manager.get_phase(key) == SessionPhase.IDLE
    assert manager.has_session(key) is False

    state = manager.get_or_create(key)
    assert state.session_key == key
    assert state.phase == SessionPhase.IDLE
    assert manager.has_session(key) is True

    manager.set_routing(key, "slack", "C12345")
    assert manager.get_routing(key) == ("slack", "C12345")
    assert manager.get_context_by_session_key(key) == ("slack", "C12345")
    assert manager.resolve_routing(key) == (key, "slack", "C12345")


@pytest.mark.asyncio
async def test_sdk_session_mapping_updates_and_cleanup() -> None:
    manager = RuntimeSessionRegistry()
    key = "slack:C12345"

    manager.set_routing(key, "slack", "C12345")
    await manager.set_sdk_session_id(key, "sdk-uuid-old")
    assert manager.resolve_sdk_session_id(key) == "sdk-uuid-old"
    assert manager.get_by_sdk_id("sdk-uuid-old") is manager.get(key)
    assert manager.resolve_routing("sdk-uuid-old") == (key, "slack", "C12345")

    await manager.set_sdk_session_id(key, "sdk-uuid-new")
    assert manager.get_by_sdk_id("sdk-uuid-old") is None
    assert manager.get_by_sdk_id("sdk-uuid-new") is manager.get(key)

    await manager.cleanup_session(key)
    assert manager.get(key) is None
    assert manager.get_by_sdk_id("sdk-uuid-new") is None


def test_metadata_accessors_are_session_scoped() -> None:
    manager = RuntimeSessionRegistry()
    key = "slack:C12345"

    manager.set_execution_cwd(key, "/tmp/work")
    manager.set_workspace_dir(key, "/tmp/ws")
    manager.set_commands(key, ["/help", "/clear"])
    manager.set_sdk_capabilities(
        key,
        skills=["skill-a"],
        tools=["tool-a"],
        slash_commands=["/x"],
        skill_source="sdk_only",
    )
    manager.set_task_id(key, "task-1")
    manager.set_request_id(key, "req-1")

    assert manager.get_execution_cwd(key) == "/tmp/work"
    assert manager.get_workspace_dir(key) == "/tmp/ws"
    assert manager.get_commands(key) == ["/help", "/clear"]
    assert manager.get_sdk_capabilities(key) == {
        "skills": ["skill-a"],
        "tools": ["tool-a"],
        "slash_commands": ["/x"],
        "skill_source": "sdk_only",
    }
    assert manager.get_task_id(key) == "task-1"
    assert manager.get_request_id(key) == "req-1"


def test_phase_changes_are_event_driven() -> None:
    manager = RuntimeSessionRegistry()
    key = "slack:C12345"

    assert manager.dispatch(key, SessionEvent.USER_MESSAGE, strict=True) is True
    assert manager.get_phase(key) == SessionPhase.ACQUIRING_CLIENT
    assert manager.dispatch(key, SessionEvent.CLIENT_ACQUIRED, strict=True) is True
    assert manager.get_phase(key) == SessionPhase.SENDING_QUERY
    assert manager.dispatch(key, SessionEvent.QUERY_SENT, strict=True) is True
    assert manager.get_phase(key) == SessionPhase.RECEIVING_STREAM
    assert manager.dispatch(key, SessionEvent.STREAM_IDLE_BOUNDARY, strict=True) is True
    assert manager.get_phase(key) == SessionPhase.DRAINING
    assert manager.dispatch(key, SessionEvent.TURN_COMPLETED, strict=True) is True
    assert manager.get_phase(key) == SessionPhase.IDLE


def test_interrupt_enters_stopping_until_idle_boundary() -> None:
    manager = RuntimeSessionRegistry()
    key = "slack:C-interrupt"

    manager.dispatch(key, SessionEvent.USER_MESSAGE)
    manager.dispatch(key, SessionEvent.CLIENT_ACQUIRED)
    manager.dispatch(key, SessionEvent.QUERY_SENT)

    assert manager.dispatch(key, SessionEvent.INTERRUPT, strict=True) is True
    assert manager.get_phase(key) == SessionPhase.STOPPING
    assert manager.get(key).interrupt_pending is True

    assert manager.dispatch(key, SessionEvent.STREAM_IDLE_BOUNDARY, strict=True) is True
    assert manager.get_phase(key) == SessionPhase.DRAINING
    assert manager.dispatch(key, SessionEvent.TURN_COMPLETED, strict=True) is True
    assert manager.get_phase(key) == SessionPhase.IDLE
    assert manager.get(key).interrupt_pending is False


@pytest.mark.parametrize(
    ("terminal_reason", "is_error", "expected_outcome"),
    [
        ("completed", False, "completed"),
        ("aborted_streaming", False, "interrupted"),
        ("aborted_tools", False, "interrupted"),
        ("aborted_tools", True, "interrupted"),
        ("max_turns", False, "limit_reached"),
        ("max_turns", True, "limit_reached"),
        ("blocking_limit", False, "limit_reached"),
        ("future_reason", False, "unknown"),
        (None, False, "unknown"),
        ("completed", True, "error"),
    ],
)
def test_record_turn_result_normalizes_sdk_terminal_reason(
    terminal_reason: str | None,
    is_error: bool,
    expected_outcome: str,
) -> None:
    manager = RuntimeSessionRegistry()
    key = "slack:C-result"

    outcome = manager.record_turn_result(
        key,
        terminal_reason=terminal_reason,
        is_error=is_error,
    )

    state = manager.get(key)
    assert outcome == expected_outcome
    assert state.last_terminal_reason == terminal_reason
    assert state.last_turn_outcome == expected_outcome


def test_worker_idle_boundary_can_finalize_from_streaming_phase() -> None:
    manager = RuntimeSessionRegistry()
    key = "feishu:ou_7b48795a"

    assert manager.dispatch(key, SessionEvent.QUERY_SENT, strict=False) is True
    assert manager.get_phase(key) == SessionPhase.RECEIVING_STREAM
    assert manager.dispatch(key, SessionEvent.TURN_COMPLETED, strict=True) is True
    assert manager.get_phase(key) == SessionPhase.IDLE


def test_invalid_transition_is_observed_without_legacy_transition_api() -> None:
    manager = RuntimeSessionRegistry()
    key = "slack:C12345"

    manager.dispatch(key, SessionEvent.USER_MESSAGE, strict=False)
    assert manager.dispatch(key, SessionEvent.CLIENT_ACQUIRED, strict=True) is True
    assert manager.dispatch(key, SessionEvent.USER_MESSAGE, strict=True) is False

    check = manager.check_session(key)
    assert check["exists"] is True
    assert check["illegal_transition_count"] == 1


@pytest.mark.asyncio
async def test_delete_is_idempotent() -> None:
    manager = RuntimeSessionRegistry()
    key = "slack:C12345"

    manager.get_or_create(key)
    assert await manager.delete(key) is True
    assert await manager.delete(key) is True
    assert manager.get(key) is None
