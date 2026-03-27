"""Tests for agent/state_machine.py - SessionStateMachine."""

import pytest

from xbot.agent.state_machine import (
    SessionPhase,
    SessionState,
    SessionStateMachine,
    VALID_TRANSITIONS,
)


class TestSessionPhase:
    """Tests for SessionPhase enum."""

    def test_phase_values(self):
        """Test that all phases have correct string values."""
        assert SessionPhase.IDLE.value == "idle"
        assert SessionPhase.RUNNING.value == "running"
        assert SessionPhase.WAITING_PERMISSION.value == "waiting_permission"
        assert SessionPhase.WAITING_INTERACTION.value == "waiting_interaction"
        assert SessionPhase.STOPPING.value == "stopping"
        assert SessionPhase.RESETTING.value == "resetting"
        assert SessionPhase.ERROR.value == "error"

    def test_phase_is_string(self):
        """Test that SessionPhase is a string enum."""
        assert isinstance(SessionPhase.IDLE, str)
        assert SessionPhase.IDLE == "idle"


class TestValidTransitions:
    """Tests for VALID_TRANSITIONS mapping."""

    def test_idle_transitions(self):
        """Test valid transitions from IDLE."""
        assert SessionPhase.RUNNING in VALID_TRANSITIONS[SessionPhase.IDLE]
        assert SessionPhase.WAITING_PERMISSION in VALID_TRANSITIONS[SessionPhase.IDLE]
        assert SessionPhase.WAITING_INTERACTION in VALID_TRANSITIONS[SessionPhase.IDLE]
        assert SessionPhase.STOPPING in VALID_TRANSITIONS[SessionPhase.IDLE]
        assert SessionPhase.RESETTING in VALID_TRANSITIONS[SessionPhase.IDLE]
        assert SessionPhase.ERROR in VALID_TRANSITIONS[SessionPhase.IDLE]

    def test_running_transitions(self):
        """Test valid transitions from RUNNING."""
        assert SessionPhase.IDLE in VALID_TRANSITIONS[SessionPhase.RUNNING]
        assert SessionPhase.WAITING_PERMISSION in VALID_TRANSITIONS[SessionPhase.RUNNING]
        assert SessionPhase.WAITING_INTERACTION in VALID_TRANSITIONS[SessionPhase.RUNNING]
        assert SessionPhase.STOPPING in VALID_TRANSITIONS[SessionPhase.RUNNING]
        assert SessionPhase.RESETTING in VALID_TRANSITIONS[SessionPhase.RUNNING]
        assert SessionPhase.ERROR in VALID_TRANSITIONS[SessionPhase.RUNNING]

    def test_waiting_permission_transitions(self):
        """Test valid transitions from WAITING_PERMISSION."""
        assert SessionPhase.RUNNING in VALID_TRANSITIONS[SessionPhase.WAITING_PERMISSION]
        assert SessionPhase.IDLE in VALID_TRANSITIONS[SessionPhase.WAITING_PERMISSION]
        assert SessionPhase.STOPPING in VALID_TRANSITIONS[SessionPhase.WAITING_PERMISSION]

    def test_waiting_interaction_transitions(self):
        """Test valid transitions from WAITING_INTERACTION."""
        assert SessionPhase.RUNNING in VALID_TRANSITIONS[SessionPhase.WAITING_INTERACTION]
        assert SessionPhase.IDLE in VALID_TRANSITIONS[SessionPhase.WAITING_INTERACTION]
        assert SessionPhase.STOPPING in VALID_TRANSITIONS[SessionPhase.WAITING_INTERACTION]

    def test_stopping_transitions(self):
        """Test valid transitions from STOPPING."""
        assert SessionPhase.IDLE in VALID_TRANSITIONS[SessionPhase.STOPPING]
        assert SessionPhase.ERROR in VALID_TRANSITIONS[SessionPhase.STOPPING]

    def test_resetting_transitions(self):
        """Test valid transitions from RESETTING."""
        assert SessionPhase.IDLE in VALID_TRANSITIONS[SessionPhase.RESETTING]
        assert SessionPhase.ERROR in VALID_TRANSITIONS[SessionPhase.RESETTING]

    def test_error_transitions(self):
        """Test valid transitions from ERROR."""
        assert SessionPhase.IDLE in VALID_TRANSITIONS[SessionPhase.ERROR]
        assert SessionPhase.RESETTING in VALID_TRANSITIONS[SessionPhase.ERROR]

    def test_no_self_transition_in_mapping(self):
        """Test that self-transitions are not in the mapping (handled separately)."""
        for phase, targets in VALID_TRANSITIONS.items():
            assert phase not in targets, f"{phase} should not transition to itself"


class TestSessionState:
    """Tests for SessionState dataclass."""

    def test_default_values(self):
        """Test default values for SessionState."""
        state = SessionState()
        assert state.phase == SessionPhase.IDLE
        assert state.reason == ""
        assert state.previous_phase is None
        assert state.transition_count == 0

    def test_custom_values(self):
        """Test custom values for SessionState."""
        state = SessionState(
            phase=SessionPhase.RUNNING,
            reason="Processing request",
            previous_phase=SessionPhase.IDLE,
            transition_count=5,
        )
        assert state.phase == SessionPhase.RUNNING
        assert state.reason == "Processing request"
        assert state.previous_phase == SessionPhase.IDLE
        assert state.transition_count == 5


class TestSessionStateMachine:
    """Tests for SessionStateMachine."""

    def test_create_machine(self):
        """Test creating a state machine."""
        machine = SessionStateMachine()
        assert len(machine.list_session_keys()) == 0

    def test_get_state_creates_new(self):
        """Test that get_state creates new state for unknown sessions."""
        machine = SessionStateMachine()
        state = machine.get_state("session:1")
        assert state.phase == SessionPhase.IDLE
        assert "session:1" in machine.list_session_keys()

    def test_get_phase(self):
        """Test getting phase for a session."""
        machine = SessionStateMachine()
        phase = machine.get_phase("session:1")
        assert phase == SessionPhase.IDLE

    def test_valid_transition(self):
        """Test a valid state transition."""
        machine = SessionStateMachine()
        result = machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        assert result is True
        assert machine.get_phase("session:1") == SessionPhase.RUNNING

        state = machine.get_state("session:1")
        assert state.reason == "start"
        assert state.previous_phase == SessionPhase.IDLE
        assert state.transition_count == 1

    def test_invalid_transition(self):
        """Test an invalid state transition."""
        machine = SessionStateMachine()
        # First go to RUNNING
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")

        # Try invalid transition: RUNNING -> STOPPING -> RUNNING (not allowed)
        machine.transition("session:1", SessionPhase.STOPPING, reason="stop")
        result = machine.transition("session:1", SessionPhase.RUNNING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.STOPPING

    def test_same_phase_same_reason_no_change(self):
        """Test that same phase with same reason doesn't increment count."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        count_before = machine.get_state("session:1").transition_count

        result = machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        assert result is True
        assert machine.get_state("session:1").transition_count == count_before

    def test_same_phase_different_reason_updates(self):
        """Test that same phase with different reason updates."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")

        result = machine.transition("session:1", SessionPhase.RUNNING, reason="processing")
        assert result is True
        state = machine.get_state("session:1")
        assert state.reason == "processing"
        assert state.transition_count == 2  # Incremented

    def test_force_transition(self):
        """Test forced transition bypasses validation."""
        machine = SessionStateMachine()
        # Go to STOPPING
        machine.transition("session:1", SessionPhase.STOPPING, reason="stop")

        # Force transition to RUNNING (normally invalid)
        result = machine.force_transition("session:1", SessionPhase.RUNNING, reason="forced")
        assert result is True
        assert machine.get_phase("session:1") == SessionPhase.RUNNING

    def test_transition_with_force_flag(self):
        """Test transition with force=True flag."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.STOPPING, reason="stop")

        result = machine.transition("session:1", SessionPhase.RUNNING, reason="forced", force=True)
        assert result is True

    def test_reset(self):
        """Test resetting a session."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")

        machine.reset("session:1")
        state = machine.get_state("session:1")
        assert state.phase == SessionPhase.IDLE
        assert state.reason == ""
        assert state.transition_count == 0

    def test_clear(self):
        """Test clearing a session entirely."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")

        machine.clear("session:1")
        assert machine.has_session("session:1") is False

    def test_has_session(self):
        """Test checking if session exists."""
        machine = SessionStateMachine()
        assert machine.has_session("session:1") is False

        machine.get_state("session:1")
        assert machine.has_session("session:1") is True

    def test_list_session_keys(self):
        """Test listing all session keys."""
        machine = SessionStateMachine()
        machine.get_state("session:1")
        machine.get_state("session:2")
        machine.get_state("session:3")

        keys = machine.list_session_keys()
        assert keys == {"session:1", "session:2", "session:3"}

    def test_is_idle(self):
        """Test is_idle check."""
        machine = SessionStateMachine()
        assert machine.is_idle("session:1") is True

        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        assert machine.is_idle("session:1") is False

    def test_is_waiting(self):
        """Test is_waiting check."""
        machine = SessionStateMachine()
        assert machine.is_waiting("session:1") is False

        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="perm")
        assert machine.is_waiting("session:1") is True

        machine.transition("session:1", SessionPhase.WAITING_INTERACTION, reason="int", force=True)
        assert machine.is_waiting("session:1") is True

    def test_is_active(self):
        """Test is_active check."""
        machine = SessionStateMachine()
        assert machine.is_active("session:1") is False

        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        assert machine.is_active("session:1") is True


class TestStateMachineCallback:
    """Tests for state machine callback functionality."""

    def test_callback_called_on_transition(self):
        """Test that callback is called on each transition."""
        transitions = []

        def on_transition(session_key, from_phase, to_phase, reason):
            transitions.append((session_key, from_phase, to_phase, reason))

        machine = SessionStateMachine(on_transition=on_transition)
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        machine.transition("session:1", SessionPhase.IDLE, reason="done")

        assert len(transitions) == 2
        assert transitions[0] == ("session:1", SessionPhase.IDLE, SessionPhase.RUNNING, "start")
        assert transitions[1] == ("session:1", SessionPhase.RUNNING, SessionPhase.IDLE, "done")

    def test_callback_on_force_transition(self):
        """Test callback is called on forced transitions."""
        transitions = []

        def on_transition(session_key, from_phase, to_phase, reason):
            transitions.append((session_key, from_phase, to_phase, reason))

        machine = SessionStateMachine(on_transition=on_transition)
        machine.transition("session:1", SessionPhase.STOPPING, reason="stop")
        machine.force_transition("session:1", SessionPhase.RUNNING, reason="forced")

        assert transitions[-1] == ("session:1", SessionPhase.STOPPING, SessionPhase.RUNNING, "forced")


class TestStateMachineFullLifecycle:
    """Tests for complete session lifecycle scenarios."""

    def test_normal_request_lifecycle(self):
        """Test normal request lifecycle: IDLE -> RUNNING -> IDLE."""
        machine = SessionStateMachine()

        # Start request
        assert machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        assert machine.is_active("session:1")

        # Complete request
        assert machine.transition("session:1", SessionPhase.IDLE, reason="complete")
        assert machine.is_idle("session:1")

    def test_permission_flow_lifecycle(self):
        """Test permission flow: IDLE -> RUNNING -> WAITING_PERMISSION -> RUNNING -> IDLE."""
        machine = SessionStateMachine()

        # Start
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        assert machine.get_phase("session:1") == SessionPhase.RUNNING

        # Request permission
        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="bash tool")
        assert machine.is_waiting("session:1")
        assert machine.get_state("session:1").reason == "bash tool"

        # User responds
        machine.transition("session:1", SessionPhase.RUNNING, reason="permission granted")
        assert machine.is_active("session:1")

        # Complete
        machine.transition("session:1", SessionPhase.IDLE, reason="complete")

    def test_interaction_flow_lifecycle(self):
        """Test interaction flow: IDLE -> RUNNING -> WAITING_INTERACTION -> RUNNING -> IDLE."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.WAITING_INTERACTION, reason="ask question")
        assert machine.is_waiting("session:1")

        machine.transition("session:1", SessionPhase.RUNNING, reason="user answered")
        machine.transition("session:1", SessionPhase.IDLE, reason="complete")

    def test_error_recovery_lifecycle(self):
        """Test error recovery: RUNNING -> ERROR -> IDLE."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.ERROR, reason="exception occurred")
        assert machine.get_phase("session:1") == SessionPhase.ERROR

        # Recover to IDLE
        machine.transition("session:1", SessionPhase.IDLE, reason="recovered")
        assert machine.is_idle("session:1")

    def test_stop_lifecycle(self):
        """Test stop lifecycle: RUNNING -> STOPPING -> IDLE."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.STOPPING, reason="user requested stop")
        machine.transition("session:1", SessionPhase.IDLE, reason="stopped")
        assert machine.is_idle("session:1")

    def test_reset_lifecycle(self):
        """Test reset lifecycle: RUNNING -> RESETTING -> IDLE."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.RESETTING, reason="reset requested")
        machine.transition("session:1", SessionPhase.IDLE, reason="reset complete")
        assert machine.is_idle("session:1")