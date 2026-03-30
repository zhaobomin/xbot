"""Tests for agent/state_machine.py - SessionStateMachine."""

import pytest

from xbot.agent.state.machine import (
    BUSY_STATES,
    FINAL_STATES,
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
        assert SessionPhase.DELETING.value == "deleting"
        assert SessionPhase.FORKING.value == "forking"
        assert SessionPhase.ERROR.value == "error"

    def test_phase_is_string(self):
        """Test that SessionPhase is a string enum."""
        assert isinstance(SessionPhase.IDLE, str)
        assert SessionPhase.IDLE == "idle"

    def test_all_phase_count(self):
        """Test that we have exactly 9 phases."""
        assert len(SessionPhase) == 9


class TestValidTransitions:
    """Tests for VALID_TRANSITIONS mapping."""

    def test_idle_transitions(self):
        """Test valid transitions from IDLE."""
        assert SessionPhase.RUNNING in VALID_TRANSITIONS[SessionPhase.IDLE]
        assert SessionPhase.WAITING_PERMISSION in VALID_TRANSITIONS[SessionPhase.IDLE]
        assert SessionPhase.WAITING_INTERACTION in VALID_TRANSITIONS[SessionPhase.IDLE]
        assert SessionPhase.STOPPING in VALID_TRANSITIONS[SessionPhase.IDLE]
        assert SessionPhase.RESETTING in VALID_TRANSITIONS[SessionPhase.IDLE]
        assert SessionPhase.DELETING in VALID_TRANSITIONS[SessionPhase.IDLE]
        assert SessionPhase.FORKING in VALID_TRANSITIONS[SessionPhase.IDLE]
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
        assert SessionPhase.RESETTING in VALID_TRANSITIONS[SessionPhase.WAITING_PERMISSION]
        assert SessionPhase.ERROR in VALID_TRANSITIONS[SessionPhase.WAITING_PERMISSION]

    def test_waiting_interaction_transitions(self):
        """Test valid transitions from WAITING_INTERACTION."""
        assert SessionPhase.RUNNING in VALID_TRANSITIONS[SessionPhase.WAITING_INTERACTION]
        assert SessionPhase.IDLE in VALID_TRANSITIONS[SessionPhase.WAITING_INTERACTION]
        assert SessionPhase.STOPPING in VALID_TRANSITIONS[SessionPhase.WAITING_INTERACTION]
        assert SessionPhase.RESETTING in VALID_TRANSITIONS[SessionPhase.WAITING_INTERACTION]
        assert SessionPhase.ERROR in VALID_TRANSITIONS[SessionPhase.WAITING_INTERACTION]

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

    def test_all_phases_have_transition_rules(self):
        """Test that all SessionPhase values are in VALID_TRANSITIONS."""
        all_phases = set(SessionPhase)
        defined_phases = set(VALID_TRANSITIONS.keys())
        assert all_phases == defined_phases, f"Missing phases: {all_phases - defined_phases}"

    def test_deleting_transitions_defined(self):
        """Test DELETING transitions are properly defined."""
        assert SessionPhase.DELETING in VALID_TRANSITIONS
        assert SessionPhase.IDLE in VALID_TRANSITIONS[SessionPhase.DELETING]
        assert SessionPhase.ERROR in VALID_TRANSITIONS[SessionPhase.DELETING]
        # Only IDLE and ERROR are valid
        assert len(VALID_TRANSITIONS[SessionPhase.DELETING]) == 2

    def test_forking_transitions_defined(self):
        """Test FORKING transitions are properly defined."""
        assert SessionPhase.FORKING in VALID_TRANSITIONS
        assert SessionPhase.IDLE in VALID_TRANSITIONS[SessionPhase.FORKING]
        assert SessionPhase.ERROR in VALID_TRANSITIONS[SessionPhase.FORKING]
        # Only IDLE and ERROR are valid
        assert len(VALID_TRANSITIONS[SessionPhase.FORKING]) == 2

    def test_deleting_and_forking_cannot_cross_transition(self):
        """Test DELETING cannot go to FORKING and vice versa."""
        assert SessionPhase.FORKING not in VALID_TRANSITIONS[SessionPhase.DELETING]
        assert SessionPhase.DELETING not in VALID_TRANSITIONS[SessionPhase.FORKING]


class TestBusyStatesAndFinalStates:
    """Tests for BUSY_STATES and FINAL_STATES constants."""

    def test_busy_states_count(self):
        """Test BUSY_STATES has correct number of states."""
        assert len(BUSY_STATES) == 7

    def test_final_states_count(self):
        """Test FINAL_STATES has correct number of states."""
        assert len(FINAL_STATES) == 1

    def test_idle_not_in_busy_states(self):
        """Test IDLE is not in BUSY_STATES."""
        assert SessionPhase.IDLE not in BUSY_STATES

    def test_error_not_in_busy_states(self):
        """Test ERROR is not in BUSY_STATES."""
        assert SessionPhase.ERROR not in BUSY_STATES

    def test_error_in_final_states(self):
        """Test ERROR is in FINAL_STATES."""
        assert SessionPhase.ERROR in FINAL_STATES

    def test_deleting_in_busy_states(self):
        """Test DELETING is in BUSY_STATES."""
        assert SessionPhase.DELETING in BUSY_STATES

    def test_forking_in_busy_states(self):
        """Test FORKING is in BUSY_STATES."""
        assert SessionPhase.FORKING in BUSY_STATES

    def test_idle_not_in_final_states(self):
        """Test IDLE is not in FINAL_STATES."""
        assert SessionPhase.IDLE not in FINAL_STATES

    def test_all_non_idle_non_error_in_busy_states(self):
        """Test all non-IDLE, non-ERROR phases are in BUSY_STATES."""
        expected_busy = set(SessionPhase) - {SessionPhase.IDLE, SessionPhase.ERROR}
        assert BUSY_STATES == expected_busy


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


class TestNewSessionPhases:
    """Tests for DELETING and FORKING phases."""

    def test_deleting_phase_exists(self):
        """Test that DELETING phase exists."""
        assert SessionPhase.DELETING.value == "deleting"

    def test_forking_phase_exists(self):
        """Test that FORKING phase exists."""
        assert SessionPhase.FORKING.value == "forking"

    def test_idle_to_deleting_transition(self):
        """Test transition from IDLE to DELETING."""
        machine = SessionStateMachine()
        result = machine.transition("session:1", SessionPhase.DELETING, reason="delete_sdk")
        assert result is True
        assert machine.get_phase("session:1") == SessionPhase.DELETING

    def test_idle_to_forking_transition(self):
        """Test transition from IDLE to FORKING."""
        machine = SessionStateMachine()
        result = machine.transition("session:1", SessionPhase.FORKING, reason="fork_sdk")
        assert result is True
        assert machine.get_phase("session:1") == SessionPhase.FORKING

    def test_deleting_to_idle_on_success(self):
        """Test DELETING -> IDLE after successful delete."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.DELETING, reason="delete_sdk")
        result = machine.transition("session:1", SessionPhase.IDLE, reason="delete_complete")
        assert result is True
        assert machine.is_idle("session:1")

    def test_deleting_to_error_on_failure(self):
        """Test DELETING -> ERROR if delete fails."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.DELETING, reason="delete_sdk")
        result = machine.transition("session:1", SessionPhase.ERROR, reason="delete_failed")
        assert result is True
        assert machine.get_phase("session:1") == SessionPhase.ERROR

    def test_forking_to_idle_on_success(self):
        """Test FORKING -> IDLE after successful fork."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.FORKING, reason="fork_sdk")
        result = machine.transition("session:1", SessionPhase.IDLE, reason="fork_complete")
        assert result is True
        assert machine.is_idle("session:1")

    def test_forking_to_error_on_failure(self):
        """Test FORKING -> ERROR if fork fails."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.FORKING, reason="fork_sdk")
        result = machine.transition("session:1", SessionPhase.ERROR, reason="fork_failed")
        assert result is True
        assert machine.get_phase("session:1") == SessionPhase.ERROR

    def test_deleting_cannot_transition_to_running(self):
        """Test that DELETING cannot go to RUNNING (must complete or error)."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.DELETING, reason="delete_sdk")
        result = machine.transition("session:1", SessionPhase.RUNNING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.DELETING

    def test_forking_cannot_transition_to_running(self):
        """Test that FORKING cannot go to RUNNING (must complete or error)."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.FORKING, reason="fork_sdk")
        result = machine.transition("session:1", SessionPhase.RUNNING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.FORKING

    def test_deleting_cannot_transition_to_forking(self):
        """Test that DELETING cannot go to FORKING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.DELETING, reason="delete_sdk")
        result = machine.transition("session:1", SessionPhase.FORKING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.DELETING

    def test_forking_cannot_transition_to_deleting(self):
        """Test that FORKING cannot go to DELETING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.FORKING, reason="fork_sdk")
        result = machine.transition("session:1", SessionPhase.DELETING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.FORKING


class TestBusyAndFinalStates:
    """Tests for is_busy and is_final helper methods."""

    def test_is_busy_returns_true_for_running(self):
        """Test that RUNNING is a busy state."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        assert machine.is_busy("session:1") is True

    def test_is_busy_returns_true_for_stopping(self):
        """Test that STOPPING is a busy state."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.STOPPING, reason="stop", force=True)
        assert machine.is_busy("session:1") is True

    def test_is_busy_returns_true_for_deleting(self):
        """Test that DELETING is a busy state."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.DELETING, reason="delete", force=True)
        assert machine.is_busy("session:1") is True

    def test_is_busy_returns_true_for_forking(self):
        """Test that FORKING is a busy state."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.FORKING, reason="fork", force=True)
        assert machine.is_busy("session:1") is True

    def test_is_busy_returns_false_for_idle(self):
        """Test that IDLE is not a busy state."""
        machine = SessionStateMachine()
        assert machine.is_busy("session:1") is False

    def test_is_final_returns_true_for_error(self):
        """Test that ERROR is a final state."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.ERROR, reason="error", force=True)
        assert machine.is_final("session:1") is True

    def test_is_final_returns_false_for_idle(self):
        """Test that IDLE is not a final state."""
        machine = SessionStateMachine()
        assert machine.is_final("session:1") is False

    def test_can_start_operation_returns_true_for_idle(self):
        """Test that operation can start when IDLE."""
        machine = SessionStateMachine()
        assert machine.can_start_operation("session:1") is True

    def test_can_start_operation_returns_false_for_busy(self):
        """Test that operation cannot start when busy."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        assert machine.can_start_operation("session:1") is False

    def test_can_start_operation_returns_false_for_deleting(self):
        """Test that operation cannot start when DELETING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.DELETING, reason="delete", force=True)
        assert machine.can_start_operation("session:1") is False


class TestDeletingForkingTransitions:
    """Tests for DELETING and FORKING transition constraints."""

    def test_deleting_cannot_go_to_waiting_permission(self):
        """Test DELETING cannot transition to WAITING_PERMISSION."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.DELETING, reason="delete")
        result = machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.DELETING

    def test_deleting_cannot_go_to_waiting_interaction(self):
        """Test DELETING cannot transition to WAITING_INTERACTION."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.DELETING, reason="delete")
        result = machine.transition("session:1", SessionPhase.WAITING_INTERACTION, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.DELETING

    def test_deleting_cannot_go_to_stopping(self):
        """Test DELETING cannot transition to STOPPING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.DELETING, reason="delete")
        result = machine.transition("session:1", SessionPhase.STOPPING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.DELETING

    def test_deleting_cannot_go_to_resetting(self):
        """Test DELETING cannot transition to RESETTING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.DELETING, reason="delete")
        result = machine.transition("session:1", SessionPhase.RESETTING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.DELETING

    def test_forking_cannot_go_to_waiting_permission(self):
        """Test FORKING cannot transition to WAITING_PERMISSION."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.FORKING, reason="fork")
        result = machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.FORKING

    def test_forking_cannot_go_to_waiting_interaction(self):
        """Test FORKING cannot transition to WAITING_INTERACTION."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.FORKING, reason="fork")
        result = machine.transition("session:1", SessionPhase.WAITING_INTERACTION, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.FORKING

    def test_forking_cannot_go_to_stopping(self):
        """Test FORKING cannot transition to STOPPING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.FORKING, reason="fork")
        result = machine.transition("session:1", SessionPhase.STOPPING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.FORKING

    def test_forking_cannot_go_to_resetting(self):
        """Test FORKING cannot transition to RESETTING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.FORKING, reason="fork")
        result = machine.transition("session:1", SessionPhase.RESETTING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.FORKING

    def test_running_cannot_go_directly_to_deleting(self):
        """Test RUNNING cannot go directly to DELETING (must go through IDLE)."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        result = machine.transition("session:1", SessionPhase.DELETING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.RUNNING

    def test_running_cannot_go_directly_to_forking(self):
        """Test RUNNING cannot go directly to FORKING (must go through IDLE)."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        result = machine.transition("session:1", SessionPhase.FORKING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.RUNNING

    def test_waiting_permission_cannot_go_to_deleting(self):
        """Test WAITING_PERMISSION cannot go directly to DELETING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="waiting")
        result = machine.transition("session:1", SessionPhase.DELETING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.WAITING_PERMISSION

    def test_waiting_permission_cannot_go_to_forking(self):
        """Test WAITING_PERMISSION cannot go directly to FORKING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="waiting")
        result = machine.transition("session:1", SessionPhase.FORKING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.WAITING_PERMISSION


class TestMultiSessionIndependence:
    """Tests for multiple sessions having independent states."""

    def test_different_sessions_have_independent_states(self):
        """Test that different sessions maintain independent state."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:2", SessionPhase.WAITING_PERMISSION, reason="waiting")

        assert machine.get_phase("session:1") == SessionPhase.RUNNING
        assert machine.get_phase("session:2") == SessionPhase.WAITING_PERMISSION

    def test_transition_count_is_per_session(self):
        """Test that transition count is tracked per session."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.IDLE, reason="complete")
        machine.transition("session:2", SessionPhase.RUNNING, reason="dispatch")

        assert machine.get_state("session:1").transition_count == 2
        assert machine.get_state("session:2").transition_count == 1

    def test_reset_one_session_does_not_affect_others(self):
        """Test that resetting one session doesn't affect others."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:2", SessionPhase.STOPPING, reason="stop")

        machine.reset("session:1")

        assert machine.is_idle("session:1")
        assert machine.get_phase("session:2") == SessionPhase.STOPPING

    def test_clear_one_session_does_not_affect_others(self):
        """Test that clearing one session doesn't affect others."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:2", SessionPhase.STOPPING, reason="stop")

        machine.clear("session:1")

        assert not machine.has_session("session:1")
        assert machine.has_session("session:2")
        assert machine.get_phase("session:2") == SessionPhase.STOPPING


class TestErrorStateTransitions:
    """Tests for ERROR state transition paths."""

    def test_error_to_idle(self):
        """Test ERROR can transition to IDLE."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.ERROR, reason="error", force=True)
        result = machine.transition("session:1", SessionPhase.IDLE, reason="recovered")
        assert result is True
        assert machine.is_idle("session:1")

    def test_error_to_resetting(self):
        """Test ERROR can transition to RESETTING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.ERROR, reason="error", force=True)
        result = machine.transition("session:1", SessionPhase.RESETTING, reason="reset")
        assert result is True
        assert machine.get_phase("session:1") == SessionPhase.RESETTING

    def test_error_cannot_go_to_running(self):
        """Test ERROR cannot transition directly to RUNNING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.ERROR, reason="error", force=True)
        result = machine.transition("session:1", SessionPhase.RUNNING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.ERROR

    def test_error_cannot_go_to_waiting(self):
        """Test ERROR cannot transition to WAITING states."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.ERROR, reason="error", force=True)
        result = machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="invalid")
        assert result is False

    def test_error_then_resetting_then_idle(self):
        """Test ERROR -> RESETTING -> IDLE path."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.ERROR, reason="error", force=True)
        machine.transition("session:1", SessionPhase.RESETTING, reason="reset")
        machine.transition("session:1", SessionPhase.IDLE, reason="reset_complete")
        assert machine.is_idle("session:1")

    def test_error_cannot_go_to_deleting(self):
        """Test ERROR cannot transition to DELETING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.ERROR, reason="error", force=True)
        result = machine.transition("session:1", SessionPhase.DELETING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.ERROR

    def test_error_cannot_go_to_forking(self):
        """Test ERROR cannot transition to FORKING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.ERROR, reason="error", force=True)
        result = machine.transition("session:1", SessionPhase.FORKING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.ERROR

    def test_error_cannot_go_to_stopping(self):
        """Test ERROR cannot transition to STOPPING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.ERROR, reason="error", force=True)
        result = machine.transition("session:1", SessionPhase.STOPPING, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == SessionPhase.ERROR

    def test_error_cannot_go_to_waiting_interaction(self):
        """Test ERROR cannot transition to WAITING_INTERACTION."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.ERROR, reason="error", force=True)
        result = machine.transition("session:1", SessionPhase.WAITING_INTERACTION, reason="invalid")
        assert result is False


class TestPreviousPhaseTracking:
    """Tests for previous_phase tracking."""

    def test_previous_phase_is_updated_on_transition(self):
        """Test that previous_phase is updated correctly."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        state = machine.get_state("session:1")
        assert state.previous_phase == SessionPhase.IDLE

        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="waiting")
        state = machine.get_state("session:1")
        assert state.previous_phase == SessionPhase.RUNNING

    def test_previous_phase_preserved_on_invalid_transition(self):
        """Test that previous_phase is not changed on failed transition."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.STOPPING, reason="stop")
        prev_before = machine.get_state("session:1").previous_phase

        machine.transition("session:1", SessionPhase.RUNNING, reason="invalid")  # Fails
        state = machine.get_state("session:1")
        assert state.previous_phase == prev_before
        assert state.phase == SessionPhase.STOPPING


class TestTransitionCountEdgeCases:
    """Tests for transition_count edge cases."""

    def test_transition_count_increments_on_same_phase_different_reason(self):
        """Test transition count increments when reason changes in same phase."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        assert machine.get_state("session:1").transition_count == 1

        machine.transition("session:1", SessionPhase.RUNNING, reason="processing")
        assert machine.get_state("session:1").transition_count == 2

        machine.transition("session:1", SessionPhase.RUNNING, reason="almost done")
        assert machine.get_state("session:1").transition_count == 3

    def test_transition_count_not_incremented_on_same_phase_same_reason(self):
        """Test transition count not incremented when phase and reason unchanged."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        count_after_first = machine.get_state("session:1").transition_count

        # Multiple calls with same phase and reason
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")

        assert machine.get_state("session:1").transition_count == count_after_first

    def test_force_transition_increments_count(self):
        """Test that force_transition also increments count."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        count_before = machine.get_state("session:1").transition_count

        machine.force_transition("session:1", SessionPhase.IDLE, reason="forced")
        assert machine.get_state("session:1").transition_count == count_before + 1


class TestResetAndClearBehavior:
    """Tests for reset() and clear() methods."""

    def test_reset_creates_fresh_state(self):
        """Test that reset() creates a completely fresh state."""
        machine = SessionStateMachine()

        # Set up complex state
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="waiting")
        machine.transition("session:1", SessionPhase.RUNNING, reason="granted")
        machine.transition("session:1", SessionPhase.ERROR, reason="error")

        # Reset
        machine.reset("session:1")

        state = machine.get_state("session:1")
        assert state.phase == SessionPhase.IDLE
        assert state.reason == ""
        assert state.previous_phase is None
        assert state.transition_count == 0

    def test_reset_nonexistent_session_does_not_create_it(self):
        """Test that reset() on nonexistent session doesn't create it."""
        machine = SessionStateMachine()

        assert not machine.has_session("session:new")

        machine.reset("session:new")

        # reset() only affects existing sessions, doesn't create new ones
        assert not machine.has_session("session:new")

    def test_clear_nonexistent_session_is_safe(self):
        """Test that clear() on nonexistent session is safe."""
        machine = SessionStateMachine()

        # Should not raise error
        machine.clear("session:nonexistent")

        assert not machine.has_session("session:nonexistent")

    def test_get_state_after_clear_creates_new(self):
        """Test that get_state() after clear() creates fresh state."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.clear("session:1")

        # get_state creates a new default state
        state = machine.get_state("session:1")
        assert state.phase == SessionPhase.IDLE
        assert state.transition_count == 0


class TestIsBusyForAllStates:
    """Tests for is_busy() covering all possible phases."""

    def test_is_busy_for_all_busy_states(self):
        """Test that is_busy returns True for all BUSY_STATES."""
        machine = SessionStateMachine()

        for phase in BUSY_STATES:
            machine.transition("session:test", phase, reason="test", force=True)
            assert machine.is_busy("session:test") is True, f"Expected is_busy=True for {phase}"
            machine.reset("session:test")

    def test_is_busy_for_non_busy_states(self):
        """Test that is_busy returns False for non-busy states."""
        machine = SessionStateMachine()

        non_busy_phases = [SessionPhase.IDLE, SessionPhase.ERROR]

        for phase in non_busy_phases:
            if phase != SessionPhase.IDLE:
                machine.transition("session:test", phase, reason="test", force=True)
            assert machine.is_busy("session:test") is False, f"Expected is_busy=False for {phase}"
            machine.reset("session:test")


class TestCallbackEdgeCases:
    """Tests for callback edge cases."""

    def test_callback_on_same_phase_different_reason(self):
        """Test callback is called when reason changes in same phase."""
        transitions = []

        def on_transition(session_key, from_phase, to_phase, reason):
            transitions.append((session_key, from_phase, to_phase, reason))

        machine = SessionStateMachine(on_transition=on_transition)
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        machine.transition("session:1", SessionPhase.RUNNING, reason="processing")

        # Callback should be called for both
        assert len(transitions) == 2
        assert transitions[1] == ("session:1", SessionPhase.RUNNING, SessionPhase.RUNNING, "processing")

    def test_callback_not_called_on_same_phase_same_reason(self):
        """Test callback is not called when phase and reason unchanged."""
        transitions = []

        def on_transition(session_key, from_phase, to_phase, reason):
            transitions.append((session_key, from_phase, to_phase, reason))

        machine = SessionStateMachine(on_transition=on_transition)
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")

        count_before = len(transitions)

        # Multiple same transitions
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")

        assert len(transitions) == count_before

    def test_callback_on_invalid_transition_not_called(self):
        """Test callback is not called on invalid transition."""
        transitions = []

        def on_transition(session_key, from_phase, to_phase, reason):
            transitions.append((session_key, from_phase, to_phase, reason))

        machine = SessionStateMachine(on_transition=on_transition)
        machine.transition("session:1", SessionPhase.STOPPING, reason="stop")

        count_before = len(transitions)

        # Invalid transition
        machine.transition("session:1", SessionPhase.RUNNING, reason="invalid")

        assert len(transitions) == count_before


class TestWaitingStateTransitions:
    """Tests for WAITING_PERMISSION and WAITING_INTERACTION transitions."""

    def test_waiting_permission_to_resetting(self):
        """Test WAITING_PERMISSION can transition to RESETTING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="waiting")
        result = machine.transition("session:1", SessionPhase.RESETTING, reason="reset")
        assert result is True
        assert machine.get_phase("session:1") == SessionPhase.RESETTING

    def test_waiting_interaction_to_resetting(self):
        """Test WAITING_INTERACTION can transition to RESETTING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.WAITING_INTERACTION, reason="waiting")
        result = machine.transition("session:1", SessionPhase.RESETTING, reason="reset")
        assert result is True
        assert machine.get_phase("session:1") == SessionPhase.RESETTING

    def test_waiting_permission_cannot_go_to_deleting_directly(self):
        """Test WAITING_PERMISSION cannot go to DELETING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="waiting")
        result = machine.transition("session:1", SessionPhase.DELETING, reason="invalid")
        assert result is False

    def test_waiting_interaction_cannot_go_to_forking_directly(self):
        """Test WAITING_INTERACTION cannot go to FORKING."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.WAITING_INTERACTION, reason="waiting")
        result = machine.transition("session:1", SessionPhase.FORKING, reason="invalid")
        assert result is False


class TestIdleToErrorDirect:
    """Tests for IDLE -> ERROR direct transition."""

    def test_idle_to_error_direct_transition(self):
        """Test IDLE can transition directly to ERROR."""
        machine = SessionStateMachine()
        result = machine.transition("session:1", SessionPhase.ERROR, reason="initial_error")
        assert result is True
        assert machine.get_phase("session:1") == SessionPhase.ERROR
        assert machine.get_state("session:1").previous_phase == SessionPhase.IDLE

    def test_idle_to_error_then_recover(self):
        """Test IDLE -> ERROR -> IDLE recovery path."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.ERROR, reason="error")
        machine.transition("session:1", SessionPhase.IDLE, reason="recovered")
        assert machine.is_idle("session:1")


class TestForceTransitionEdgeCases:
    """Tests for force_transition edge cases."""

    def test_force_transition_same_phase_same_reason(self):
        """Test force_transition with same phase and reason."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        count_before = machine.get_state("session:1").transition_count

        result = machine.force_transition("session:1", SessionPhase.RUNNING, reason="start")
        assert result is True
        # Should not increment count for same phase + same reason
        assert machine.get_state("session:1").transition_count == count_before

    def test_force_transition_same_phase_different_reason(self):
        """Test force_transition with same phase but different reason."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        count_before = machine.get_state("session:1").transition_count

        result = machine.force_transition("session:1", SessionPhase.RUNNING, reason="processing")
        assert result is True
        # Should increment count for same phase + different reason
        assert machine.get_state("session:1").transition_count == count_before + 1
        assert machine.get_state("session:1").reason == "processing"

    def test_force_transition_preserves_previous_phase_for_same_phase(self):
        """Test force_transition does not update previous_phase for same phase."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        prev_before = machine.get_state("session:1").previous_phase

        machine.force_transition("session:1", SessionPhase.RUNNING, reason="processing")
        # previous_phase should not change for same-phase update
        assert machine.get_state("session:1").previous_phase == prev_before

    def test_force_transition_updates_previous_phase_for_different_phase(self):
        """Test force_transition updates previous_phase for different phase."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.STOPPING, reason="stop")

        machine.force_transition("session:1", SessionPhase.RUNNING, reason="forced")
        state = machine.get_state("session:1")
        assert state.previous_phase == SessionPhase.STOPPING


class TestLongTransitionChains:
    """Tests for long transition chains."""

    def test_complex_permission_flow_chain(self):
        """Test a complex permission flow with multiple cycles."""
        machine = SessionStateMachine()

        # IDLE -> RUNNING
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        # RUNNING -> WAITING_PERMISSION
        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="need bash")
        # WAITING_PERMISSION -> RUNNING
        machine.transition("session:1", SessionPhase.RUNNING, reason="granted")
        # RUNNING -> WAITING_PERMISSION again
        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="need write")
        # WAITING_PERMISSION -> RUNNING
        machine.transition("session:1", SessionPhase.RUNNING, reason="granted2")
        # RUNNING -> WAITING_INTERACTION
        machine.transition("session:1", SessionPhase.WAITING_INTERACTION, reason="ask user")
        # WAITING_INTERACTION -> RUNNING
        machine.transition("session:1", SessionPhase.RUNNING, reason="answered")
        # RUNNING -> IDLE
        machine.transition("session:1", SessionPhase.IDLE, reason="complete")

        assert machine.is_idle("session:1")
        assert machine.get_state("session:1").transition_count == 8
        assert machine.get_state("session:1").previous_phase == SessionPhase.RUNNING

    def test_stop_flow_with_multiple_states(self):
        """Test stop flow that goes through multiple states."""
        machine = SessionStateMachine()

        # IDLE -> RUNNING
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        # RUNNING -> WAITING_PERMISSION
        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="waiting")
        # WAITING_PERMISSION -> STOPPING (user requested stop during permission wait)
        machine.transition("session:1", SessionPhase.STOPPING, reason="user stop")
        # STOPPING -> IDLE
        machine.transition("session:1", SessionPhase.IDLE, reason="stopped")

        assert machine.is_idle("session:1")


class TestStateMachineIsolation:
    """Tests for state machine isolation properties."""

    def test_list_session_keys_returns_copy(self):
        """Test that list_session_keys returns a copy, not internal state."""
        machine = SessionStateMachine()
        machine.get_state("session:1")

        keys = machine.list_session_keys()
        keys.add("session:fake")

        assert "session:fake" not in machine.list_session_keys()
        assert len(machine.list_session_keys()) == 1

    def test_get_state_returns_same_object(self):
        """Test that get_state returns the same object for same session."""
        machine = SessionStateMachine()

        state1 = machine.get_state("session:1")
        state2 = machine.get_state("session:1")

        assert state1 is state2  # Same object reference

    def test_state_object_is_mutable(self):
        """Test that state object can be mutated (for transition updates)."""
        machine = SessionStateMachine()

        state = machine.get_state("session:1")
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")

        # The same state object should now have updated values
        assert state.phase == SessionPhase.RUNNING
        assert state.reason == "start"


class TestSessionKeyEdgeCases:
    """Tests for session key edge cases."""

    def test_empty_session_key(self):
        """Test that empty session key works (though probably not recommended)."""
        machine = SessionStateMachine()

        result = machine.transition("", SessionPhase.RUNNING, reason="start")
        assert result is True
        assert machine.has_session("")
        assert machine.get_phase("") == SessionPhase.RUNNING

    def test_session_key_with_special_characters(self):
        """Test session key with special characters."""
        machine = SessionStateMachine()

        special_keys = [
            "session:test-123",
            "session:test_456",
            "user@example.com",
            "session:with:colon",
            "session/with/slash",
        ]

        for key in special_keys:
            machine.transition(key, SessionPhase.RUNNING, reason="start")
            assert machine.get_phase(key) == SessionPhase.RUNNING
            machine.clear(key)

    def test_session_key_case_sensitivity(self):
        """Test that session keys are case-sensitive."""
        machine = SessionStateMachine()

        machine.transition("Session:1", SessionPhase.RUNNING, reason="start")
        machine.transition("session:1", SessionPhase.STOPPING, reason="stop")

        # Different keys, different states
        assert machine.get_phase("Session:1") == SessionPhase.RUNNING
        assert machine.get_phase("session:1") == SessionPhase.STOPPING


class TestIsWaitingForAllStates:
    """Tests for is_waiting() covering all states."""

    def test_is_waiting_true_for_waiting_permission(self):
        """Test is_waiting returns True for WAITING_PERMISSION."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="waiting")
        assert machine.is_waiting("session:1") is True

    def test_is_waiting_true_for_waiting_interaction(self):
        """Test is_waiting returns True for WAITING_INTERACTION."""
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.WAITING_INTERACTION, reason="waiting")
        assert machine.is_waiting("session:1") is True

    def test_is_waiting_false_for_other_states(self):
        """Test is_waiting returns False for non-waiting states."""
        machine = SessionStateMachine()

        for phase in SessionPhase:
            if phase == SessionPhase.IDLE:
                # IDLE is default, no transition needed
                pass
            elif phase in {SessionPhase.WAITING_PERMISSION, SessionPhase.WAITING_INTERACTION}:
                continue  # Skip waiting states
            else:
                machine.transition("session:1", phase, reason="test", force=True)

            assert machine.is_waiting("session:1") is False, f"is_waiting should be False for {phase}"
            machine.reset("session:1")


class TestIsFinalForAllStates:
    """Tests for is_final() covering all states."""

    def test_is_final_true_only_for_error(self):
        """Test is_final returns True only for ERROR."""
        machine = SessionStateMachine()

        for phase in SessionPhase:
            if phase == SessionPhase.IDLE:
                pass
            else:
                machine.transition("session:1", phase, reason="test", force=True)

            expected = phase == SessionPhase.ERROR
            actual = machine.is_final("session:1")
            assert actual == expected, f"is_final({phase}) should be {expected}, got {actual}"

            machine.reset("session:1")


class TestIsIdleForAllStates:
    """Tests for is_idle() covering all states."""

    def test_is_idle_true_only_for_idle(self):
        """Test is_idle returns True only for IDLE."""
        machine = SessionStateMachine()

        for phase in SessionPhase:
            if phase == SessionPhase.IDLE:
                pass
            else:
                machine.transition("session:1", phase, reason="test", force=True)

            expected = phase == SessionPhase.IDLE
            actual = machine.is_idle("session:1")
            assert actual == expected, f"is_idle({phase}) should be {expected}, got {actual}"

            machine.reset("session:1")


class TestIsActiveForAllStates:
    """Tests for is_active() covering all states."""

    def test_is_active_true_only_for_running(self):
        """Test is_active returns True only for RUNNING."""
        machine = SessionStateMachine()

        for phase in SessionPhase:
            if phase == SessionPhase.IDLE:
                pass
            else:
                machine.transition("session:1", phase, reason="test", force=True)

            expected = phase == SessionPhase.RUNNING
            actual = machine.is_active("session:1")
            assert actual == expected, f"is_active({phase}) should be {expected}, got {actual}"

            machine.reset("session:1")


class TestCanStartOperationForAllStates:
    """Tests for can_start_operation() covering all states."""

    def test_can_start_true_only_for_idle(self):
        """Test can_start_operation returns True only for IDLE."""
        machine = SessionStateMachine()

        for phase in SessionPhase:
            if phase == SessionPhase.IDLE:
                pass
            else:
                machine.transition("session:1", phase, reason="test", force=True)

            expected = phase == SessionPhase.IDLE
            actual = machine.can_start_operation("session:1")
            assert actual == expected, f"can_start_operation({phase}) should be {expected}, got {actual}"

            machine.reset("session:1")


class TestLogging:
    """Tests for logging behavior using mock."""

    def test_log_on_new_session_creation(self):
        """Test that a log is written when a new session is created."""
        from unittest.mock import patch

        with patch("xbot.agent.state.machine.logger") as mock_logger:
            machine = SessionStateMachine()
            machine.get_state("session:new")

            mock_logger.debug.assert_called()
            call_args = str(mock_logger.debug.call_args)
            assert "Creating new session state: session:new" in call_args

    def test_log_on_reset(self):
        """Test that a log is written when a session is reset."""
        from unittest.mock import patch

        with patch("xbot.agent.state.machine.logger") as mock_logger:
            machine = SessionStateMachine()
            machine.transition("session:1", SessionPhase.RUNNING, reason="test", force=True)
            mock_logger.reset_mock()
            machine.reset("session:1")

            mock_logger.debug.assert_called()
            call_args = str(mock_logger.debug.call_args)
            assert "Resetting session state: session:1" in call_args
            assert "was: running" in call_args

    def test_log_on_reset_nonexistent_session(self):
        """Test that a log is written when trying to reset a non-existent session."""
        from unittest.mock import patch

        with patch("xbot.agent.state.machine.logger") as mock_logger:
            machine = SessionStateMachine()
            machine.reset("session:nonexistent")

            mock_logger.debug.assert_called()
            call_args = str(mock_logger.debug.call_args)
            assert "Reset skipped for non-existent session" in call_args

    def test_log_on_clear(self):
        """Test that a log is written when a session is cleared."""
        from unittest.mock import patch

        with patch("xbot.agent.state.machine.logger") as mock_logger:
            machine = SessionStateMachine()
            machine.transition("session:1", SessionPhase.RUNNING, reason="test", force=True)
            mock_logger.reset_mock()
            machine.clear("session:1")

            mock_logger.debug.assert_called()
            call_args = str(mock_logger.debug.call_args)
            assert "Clearing session state: session:1" in call_args

    def test_log_on_clear_nonexistent_session(self):
        """Test that a log is written when trying to clear a non-existent session."""
        from unittest.mock import patch

        with patch("xbot.agent.state.machine.logger") as mock_logger:
            machine = SessionStateMachine()
            machine.clear("session:nonexistent")

            mock_logger.debug.assert_called()
            call_args = str(mock_logger.debug.call_args)
            assert "Clear skipped for non-existent session" in call_args

    def test_log_on_valid_transition(self):
        """Test that a log is written on valid transition."""
        from unittest.mock import patch

        with patch("xbot.agent.state.machine.logger") as mock_logger:
            machine = SessionStateMachine()
            machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")

            mock_logger.debug.assert_called()
            call_args = str(mock_logger.debug.call_args)
            assert "session:1 idle -> running" in call_args

    def test_log_on_invalid_transition(self):
        """Test that a warning log is written on invalid transition."""
        from unittest.mock import patch

        with patch("xbot.agent.state.machine.logger") as mock_logger:
            machine = SessionStateMachine()
            machine.transition("session:1", SessionPhase.STOPPING, reason="stop", force=True)
            mock_logger.reset_mock()
            machine.transition("session:1", SessionPhase.RUNNING, reason="invalid")

            mock_logger.warning.assert_called()
            call_args = str(mock_logger.warning.call_args)
            assert "Invalid state transition rejected" in call_args

    def test_log_on_reason_update(self):
        """Test that a log is written on reason update."""
        from unittest.mock import patch

        with patch("xbot.agent.state.machine.logger") as mock_logger:
            machine = SessionStateMachine()
            machine.transition("session:1", SessionPhase.RUNNING, reason="start", force=True)
            mock_logger.reset_mock()
            machine.transition("session:1", SessionPhase.RUNNING, reason="processing")

            mock_logger.debug.assert_called()
            call_args = str(mock_logger.debug.call_args)
            assert "Session state reason update" in call_args

    def test_log_on_forced_transition(self):
        """Test that a log is written on forced transition."""
        from unittest.mock import patch

        with patch("xbot.agent.state.machine.logger") as mock_logger:
            machine = SessionStateMachine()
            machine.transition("session:1", SessionPhase.STOPPING, reason="stop", force=True)
            mock_logger.reset_mock()
            machine.force_transition("session:1", SessionPhase.RUNNING, reason="forced")

            mock_logger.debug.assert_called()
            call_args = str(mock_logger.debug.call_args)
            assert "forced" in call_args

    def test_log_includes_transition_count(self):
        """Test that transition logs include the transition count."""
        from unittest.mock import patch

        with patch("xbot.agent.state.machine.logger") as mock_logger:
            machine = SessionStateMachine()
            machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")

            call_args = str(mock_logger.debug.call_args)
            assert "count=1" in call_args

    def test_log_includes_previous_state_on_reset(self):
        """Test that reset log includes previous state info."""
        from unittest.mock import patch

        with patch("xbot.agent.state.machine.logger") as mock_logger:
            machine = SessionStateMachine()
            machine.transition("session:1", SessionPhase.RUNNING, reason="test", force=True)
            machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="waiting", force=True)
            mock_logger.reset_mock()
            machine.reset("session:1")

            call_args = str(mock_logger.debug.call_args)
            assert "was: waiting_permission" in call_args
            assert "transitions: 2" in call_args


class TestMultipleResetClear:
    """Tests for multiple reset and clear operations."""

    def test_multiple_resets(self):
        """Test that multiple resets work correctly."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.reset("session:1")
        machine.reset("session:1")  # Second reset
        machine.reset("session:1")  # Third reset

        assert machine.is_idle("session:1")
        assert machine.get_state("session:1").transition_count == 0

    def test_multiple_clears(self):
        """Test that multiple clears work correctly."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.clear("session:1")
        machine.clear("session:1")  # Second clear (no-op)
        machine.clear("session:1")  # Third clear (no-op)

        assert not machine.has_session("session:1")

    def test_clear_then_reset(self):
        """Test clear followed by reset."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.clear("session:1")
        machine.reset("session:1")  # Reset on non-existent session

        assert not machine.has_session("session:1")

    def test_reset_then_clear(self):
        """Test reset followed by clear."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.reset("session:1")
        machine.clear("session:1")

        assert not machine.has_session("session:1")

    def test_operations_on_cleared_session(self):
        """Test operations on a cleared session."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.clear("session:1")

        # get_state should create new
        state = machine.get_state("session:1")
        assert state.phase == SessionPhase.IDLE
        assert state.transition_count == 0

        # Transition should work
        result = machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        assert result is True


class TestTransitionCountAfterReset:
    """Tests for transition count after reset."""

    def test_transition_count_resets_to_zero(self):
        """Test that transition count is zero after reset."""
        machine = SessionStateMachine()

        # Multiple transitions
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="waiting")
        machine.transition("session:1", SessionPhase.RUNNING, reason="granted")
        machine.transition("session:1", SessionPhase.IDLE, reason="complete")

        assert machine.get_state("session:1").transition_count == 4

        machine.reset("session:1")

        assert machine.get_state("session:1").transition_count == 0

    def test_transitions_after_reset(self):
        """Test that transitions work correctly after reset."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.reset("session:1")

        # Should be able to transition again
        result = machine.transition("session:1", SessionPhase.RUNNING, reason="new_dispatch")
        assert result is True
        assert machine.get_state("session:1").transition_count == 1


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_reason(self):
        """Test transition with empty reason."""
        machine = SessionStateMachine()

        result = machine.transition("session:1", SessionPhase.RUNNING, reason="")
        assert result is True
        assert machine.get_state("session:1").reason == ""

    def test_long_reason(self):
        """Test transition with very long reason."""
        machine = SessionStateMachine()

        long_reason = "a" * 1000
        result = machine.transition("session:1", SessionPhase.RUNNING, reason=long_reason)
        assert result is True
        assert machine.get_state("session:1").reason == long_reason

    def test_reason_with_special_characters(self):
        """Test transition with special characters in reason."""
        machine = SessionStateMachine()

        special_reason = "test\nwith\nnewlines\tand\ttabs"
        result = machine.transition("session:1", SessionPhase.RUNNING, reason=special_reason)
        assert result is True
        assert machine.get_state("session:1").reason == special_reason

    def test_reason_with_unicode(self):
        """Test transition with unicode in reason."""
        machine = SessionStateMachine()

        unicode_reason = "测试 🎉 emoji"
        result = machine.transition("session:1", SessionPhase.RUNNING, reason=unicode_reason)
        assert result is True
        assert machine.get_state("session:1").reason == unicode_reason

    def test_reason_update_to_empty(self):
        """Test updating reason to empty string."""
        machine = SessionStateMachine()

        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        machine.transition("session:1", SessionPhase.RUNNING, reason="")

        assert machine.get_state("session:1").reason == ""

    def test_transition_count_large_value(self):
        """Test that transition count works with large values."""
        machine = SessionStateMachine()

        # Set a high transition count
        state = machine.get_state("session:1")
        state.transition_count = 1000000

        # Should still work
        result = machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        assert result is True
        assert machine.get_state("session:1").transition_count == 1000001


class TestStateMachineWithNoneCallback:
    """Tests for state machine behavior with None callback."""

    def test_none_callback_does_not_crash(self):
        """Test that None callback doesn't cause crash."""
        machine = SessionStateMachine(on_transition=None)

        result = machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        assert result is True

    def test_none_callback_on_force_transition(self):
        """Test that None callback doesn't cause crash on force transition."""
        machine = SessionStateMachine(on_transition=None)

        result = machine.force_transition("session:1", SessionPhase.RUNNING, reason="forced")
        assert result is True


class TestAllInvalidTransitions:
    """Parametrized tests for all invalid transitions."""

    @pytest.mark.parametrize(
        "from_phase,to_phase",
        [
            (SessionPhase.STOPPING, SessionPhase.RUNNING),
            (SessionPhase.STOPPING, SessionPhase.WAITING_PERMISSION),
            (SessionPhase.STOPPING, SessionPhase.WAITING_INTERACTION),
            (SessionPhase.STOPPING, SessionPhase.RESETTING),
            (SessionPhase.STOPPING, SessionPhase.DELETING),
            (SessionPhase.STOPPING, SessionPhase.FORKING),
            (SessionPhase.RESETTING, SessionPhase.RUNNING),
            (SessionPhase.RESETTING, SessionPhase.WAITING_PERMISSION),
            (SessionPhase.RESETTING, SessionPhase.WAITING_INTERACTION),
            (SessionPhase.RESETTING, SessionPhase.STOPPING),
            (SessionPhase.RESETTING, SessionPhase.DELETING),
            (SessionPhase.RESETTING, SessionPhase.FORKING),
            (SessionPhase.DELETING, SessionPhase.RUNNING),
            (SessionPhase.DELETING, SessionPhase.WAITING_PERMISSION),
            (SessionPhase.DELETING, SessionPhase.WAITING_INTERACTION),
            (SessionPhase.DELETING, SessionPhase.STOPPING),
            (SessionPhase.DELETING, SessionPhase.RESETTING),
            (SessionPhase.DELETING, SessionPhase.FORKING),
            (SessionPhase.FORKING, SessionPhase.RUNNING),
            (SessionPhase.FORKING, SessionPhase.WAITING_PERMISSION),
            (SessionPhase.FORKING, SessionPhase.WAITING_INTERACTION),
            (SessionPhase.FORKING, SessionPhase.STOPPING),
            (SessionPhase.FORKING, SessionPhase.RESETTING),
            (SessionPhase.FORKING, SessionPhase.DELETING),
            (SessionPhase.ERROR, SessionPhase.RUNNING),
            (SessionPhase.ERROR, SessionPhase.WAITING_PERMISSION),
            (SessionPhase.ERROR, SessionPhase.WAITING_INTERACTION),
            (SessionPhase.ERROR, SessionPhase.STOPPING),
            (SessionPhase.ERROR, SessionPhase.DELETING),
            (SessionPhase.ERROR, SessionPhase.FORKING),
        ],
    )
    def test_invalid_transition_rejected(self, from_phase, to_phase):
        """Test that invalid transitions are rejected."""
        machine = SessionStateMachine()
        machine.transition("session:1", from_phase, reason="setup", force=True)

        result = machine.transition("session:1", to_phase, reason="invalid")
        assert result is False
        assert machine.get_phase("session:1") == from_phase


class TestAllValidTransitions:
    """Parametrized tests for all valid transitions."""

    @pytest.mark.parametrize(
        "from_phase,to_phase",
        [
            (SessionPhase.IDLE, SessionPhase.RUNNING),
            (SessionPhase.IDLE, SessionPhase.WAITING_PERMISSION),
            (SessionPhase.IDLE, SessionPhase.WAITING_INTERACTION),
            (SessionPhase.IDLE, SessionPhase.STOPPING),
            (SessionPhase.IDLE, SessionPhase.RESETTING),
            (SessionPhase.IDLE, SessionPhase.DELETING),
            (SessionPhase.IDLE, SessionPhase.FORKING),
            (SessionPhase.IDLE, SessionPhase.ERROR),
            (SessionPhase.RUNNING, SessionPhase.IDLE),
            (SessionPhase.RUNNING, SessionPhase.WAITING_PERMISSION),
            (SessionPhase.RUNNING, SessionPhase.WAITING_INTERACTION),
            (SessionPhase.RUNNING, SessionPhase.STOPPING),
            (SessionPhase.RUNNING, SessionPhase.RESETTING),
            (SessionPhase.RUNNING, SessionPhase.ERROR),
            (SessionPhase.WAITING_PERMISSION, SessionPhase.RUNNING),
            (SessionPhase.WAITING_PERMISSION, SessionPhase.IDLE),
            (SessionPhase.WAITING_PERMISSION, SessionPhase.STOPPING),
            (SessionPhase.WAITING_PERMISSION, SessionPhase.RESETTING),
            (SessionPhase.WAITING_PERMISSION, SessionPhase.ERROR),
            (SessionPhase.WAITING_INTERACTION, SessionPhase.RUNNING),
            (SessionPhase.WAITING_INTERACTION, SessionPhase.IDLE),
            (SessionPhase.WAITING_INTERACTION, SessionPhase.STOPPING),
            (SessionPhase.WAITING_INTERACTION, SessionPhase.RESETTING),
            (SessionPhase.WAITING_INTERACTION, SessionPhase.ERROR),
            (SessionPhase.STOPPING, SessionPhase.IDLE),
            (SessionPhase.STOPPING, SessionPhase.ERROR),
            (SessionPhase.RESETTING, SessionPhase.IDLE),
            (SessionPhase.RESETTING, SessionPhase.ERROR),
            (SessionPhase.DELETING, SessionPhase.IDLE),
            (SessionPhase.DELETING, SessionPhase.ERROR),
            (SessionPhase.FORKING, SessionPhase.IDLE),
            (SessionPhase.FORKING, SessionPhase.ERROR),
            (SessionPhase.ERROR, SessionPhase.IDLE),
            (SessionPhase.ERROR, SessionPhase.RESETTING),
        ],
    )
    def test_valid_transition_accepted(self, from_phase, to_phase):
        """Test that valid transitions are accepted."""
        machine = SessionStateMachine()
        if from_phase != SessionPhase.IDLE:
            machine.transition("session:1", from_phase, reason="setup", force=True)

        result = machine.transition("session:1", to_phase, reason="test")
        assert result is True
        assert machine.get_phase("session:1") == to_phase


class TestSessionStateDataclass:
    """Tests for SessionState dataclass behavior."""

    def test_equality(self):
        """Test SessionState equality."""
        state1 = SessionState(phase=SessionPhase.RUNNING, reason="test")
        state2 = SessionState(phase=SessionPhase.RUNNING, reason="test")
        state3 = SessionState(phase=SessionPhase.IDLE, reason="test")

        assert state1 == state2
        assert state1 != state3

    def test_repr(self):
        """Test SessionState repr."""
        state = SessionState(phase=SessionPhase.RUNNING, reason="test")
        repr_str = repr(state)

        assert "SessionState" in repr_str
        assert "running" in repr_str
        assert "test" in repr_str

    def test_default_values_are_correct(self):
        """Test that default values match expectations."""
        state = SessionState()

        assert state.phase == SessionPhase.IDLE
        assert state.reason == ""
        assert state.previous_phase is None
        assert state.transition_count == 0


class TestConcurrentSessionOperations:
    """Tests for concurrent operations on multiple sessions."""

    def test_many_sessions(self):
        """Test that many sessions can be managed."""
        machine = SessionStateMachine()

        # Create 100 sessions
        for i in range(100):
            machine.transition(f"session:{i}", SessionPhase.RUNNING, reason="dispatch")

        assert len(machine.list_session_keys()) == 100

        # Verify each session is independent
        for i in range(100):
            assert machine.get_phase(f"session:{i}") == SessionPhase.RUNNING

        # Clear all
        for i in range(100):
            machine.clear(f"session:{i}")

        assert len(machine.list_session_keys()) == 0

    def test_interleaved_operations(self):
        """Test interleaved operations on multiple sessions."""
        machine = SessionStateMachine()

        # Interleaved operations
        machine.transition("session:1", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:2", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:1", SessionPhase.WAITING_PERMISSION, reason="waiting")
        machine.transition("session:3", SessionPhase.RUNNING, reason="dispatch")
        machine.transition("session:2", SessionPhase.IDLE, reason="complete")
        machine.transition("session:1", SessionPhase.RUNNING, reason="granted")

        assert machine.get_phase("session:1") == SessionPhase.RUNNING
        assert machine.get_phase("session:2") == SessionPhase.IDLE
        assert machine.get_phase("session:3") == SessionPhase.RUNNING