"""Compatibility tests for runtime state exports."""

from xbot.runtime.state.machine import VALID_TRANSITIONS, SessionEvent, SessionPhase, SessionState


def test_machine_exports_v2_types() -> None:
    assert SessionPhase.IDLE.value == "idle"
    assert SessionEvent.USER_MESSAGE.value == "user_message"
    state = SessionState(session_key="s1")
    assert state.phase == SessionPhase.IDLE


def test_transition_table_has_rules_for_all_v2_phases() -> None:
    assert set(VALID_TRANSITIONS.keys()) == set(SessionPhase)
    assert SessionPhase.ACQUIRING_CLIENT in VALID_TRANSITIONS[SessionPhase.IDLE]
    assert SessionPhase.RELEASING_CLIENT in VALID_TRANSITIONS[SessionPhase.RECEIVING_STREAM]
