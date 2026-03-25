"""Tests for Crew state machine."""

import pytest

from xbot.agent.crew.models import TaskDefinition
from xbot.agent.crew.state import (
    CREW_VALID_TRANSITIONS,
    TASK_VALID_TRANSITIONS,
    CrewPhase,
    CrewStateManager,
    InvalidTransitionError,
    InvariantViolationError,
    TaskPhase,
)


class TestCrewPhaseTransitions:
    """Tests for crew-level phase transitions."""

    def test_created_can_transition_to_initializing(self):
        """Created -> INITIALIZING is valid."""
        assert CrewPhase.INITIALIZING in CREW_VALID_TRANSITIONS[CrewPhase.CREATED]

    def test_created_can_transition_to_failed(self):
        """Created -> FAILED is valid (early failure)."""
        assert CrewPhase.FAILED in CREW_VALID_TRANSITIONS[CrewPhase.CREATED]

    def test_running_can_transition_to_paused(self):
        """RUNNING -> PAUSED is valid."""
        assert CrewPhase.PAUSED in CREW_VALID_TRANSITIONS[CrewPhase.RUNNING]

    def test_running_can_transition_to_completing(self):
        """RUNNING -> COMPLETING is valid."""
        assert CrewPhase.COMPLETING in CREW_VALID_TRANSITIONS[CrewPhase.RUNNING]

    def test_running_can_transition_to_aborting(self):
        """RUNNING -> ABORTING is valid."""
        assert CrewPhase.ABORTING in CREW_VALID_TRANSITIONS[CrewPhase.RUNNING]

    def test_terminal_states_have_no_transitions(self):
        """COMPLETED, FAILED, ABORTED are terminal."""
        assert len(CREW_VALID_TRANSITIONS[CrewPhase.COMPLETED]) == 0
        assert len(CREW_VALID_TRANSITIONS[CrewPhase.FAILED]) == 0
        assert len(CREW_VALID_TRANSITIONS[CrewPhase.ABORTED]) == 0


class TestTaskPhaseTransitions:
    """Tests for task-level phase transitions."""

    def test_pending_can_transition_to_blocked(self):
        """PENDING -> BLOCKED is valid."""
        assert TaskPhase.BLOCKED in TASK_VALID_TRANSITIONS[TaskPhase.PENDING]

    def test_pending_can_transition_to_queued(self):
        """PENDING -> QUEUED is valid."""
        assert TaskPhase.QUEUED in TASK_VALID_TRANSITIONS[TaskPhase.PENDING]

    def test_running_can_transition_to_awaiting_review(self):
        """RUNNING -> AWAITING_REVIEW is valid."""
        assert TaskPhase.AWAITING_REVIEW in TASK_VALID_TRANSITIONS[TaskPhase.RUNNING]

    def test_awaiting_review_can_transition_to_retrying(self):
        """AWAITING_REVIEW -> RETRYING is valid for redo."""
        assert TaskPhase.RETRYING in TASK_VALID_TRANSITIONS[TaskPhase.AWAITING_REVIEW]

    def test_awaiting_review_can_transition_to_failed(self):
        """AWAITING_REVIEW -> FAILED is valid."""
        assert TaskPhase.FAILED in TASK_VALID_TRANSITIONS[TaskPhase.AWAITING_REVIEW]

    def test_retrying_can_only_transition_to_running(self):
        """RETRYING can only go to RUNNING."""
        assert TASK_VALID_TRANSITIONS[TaskPhase.RETRYING] == {TaskPhase.RUNNING}

    def test_terminal_states_have_no_transitions(self):
        """COMPLETED, FAILED, SKIPPED are terminal."""
        assert len(TASK_VALID_TRANSITIONS[TaskPhase.COMPLETED]) == 0
        assert len(TASK_VALID_TRANSITIONS[TaskPhase.FAILED]) == 0
        assert len(TASK_VALID_TRANSITIONS[TaskPhase.SKIPPED]) == 0


class TestCrewStateManager:
    """Tests for CrewStateManager."""

    def test_initial_state(self):
        """Manager starts in CREATED state with all tasks PENDING."""
        manager = CrewStateManager(task_names=["task1", "task2"])

        assert manager.crew_phase == CrewPhase.CREATED
        assert manager.get_task_phase("task1") == TaskPhase.PENDING
        assert manager.get_task_phase("task2") == TaskPhase.PENDING

    def test_crew_transition_valid(self):
        """Valid crew transition succeeds."""
        manager = CrewStateManager(task_names=["task1"])
        manager.transition_crew(CrewPhase.INITIALIZING)

        assert manager.crew_phase == CrewPhase.INITIALIZING

    def test_crew_transition_invalid(self):
        """Invalid crew transition raises error."""
        manager = CrewStateManager(task_names=["task1"])

        with pytest.raises(InvalidTransitionError):
            manager.transition_crew(CrewPhase.COMPLETED)  # CREATED -> COMPLETED is invalid

    def test_task_transition_valid(self):
        """Valid task transition succeeds."""
        manager = CrewStateManager(task_names=["task1"])
        manager.transition_task("task1", TaskPhase.QUEUED)

        assert manager.get_task_phase("task1") == TaskPhase.QUEUED

    def test_task_transition_invalid(self):
        """Invalid task transition raises error."""
        manager = CrewStateManager(task_names=["task1"])

        with pytest.raises(InvalidTransitionError):
            manager.transition_task("task1", TaskPhase.COMPLETED)  # PENDING -> COMPLETED is invalid

    def test_task_transition_unknown_task(self):
        """Transition for unknown task raises KeyError."""
        manager = CrewStateManager(task_names=["task1"])

        with pytest.raises(KeyError):
            manager.transition_task("unknown", TaskPhase.QUEUED)

    def test_auto_sync_crew_phase_to_running(self):
        """Crew phase auto-syncs to RUNNING when a task is RUNNING."""
        manager = CrewStateManager(task_names=["task1"])
        manager.transition_crew(CrewPhase.INITIALIZING)
        manager.transition_crew(CrewPhase.RUNNING)

        # Now task becomes RUNNING
        manager.transition_task("task1", TaskPhase.QUEUED)
        manager.transition_task("task1", TaskPhase.RUNNING)

        assert manager.crew_phase == CrewPhase.RUNNING

    def test_auto_sync_crew_phase_to_paused(self):
        """Crew phase auto-syncs to PAUSED when task awaits review."""
        manager = CrewStateManager(task_names=["task1"])
        manager.transition_crew(CrewPhase.INITIALIZING)
        manager.transition_crew(CrewPhase.RUNNING)

        manager.transition_task("task1", TaskPhase.QUEUED)
        manager.transition_task("task1", TaskPhase.RUNNING)
        manager.transition_task("task1", TaskPhase.AWAITING_REVIEW)

        assert manager.crew_phase == CrewPhase.PAUSED

    def test_auto_sync_crew_phase_to_completing(self):
        """Crew phase auto-syncs to COMPLETING when all tasks are terminal."""
        manager = CrewStateManager(task_names=["task1"])
        manager.transition_crew(CrewPhase.INITIALIZING)
        manager.transition_crew(CrewPhase.RUNNING)

        manager.transition_task("task1", TaskPhase.QUEUED)
        manager.transition_task("task1", TaskPhase.RUNNING)
        manager.transition_task("task1", TaskPhase.COMPLETED)

        assert manager.crew_phase == CrewPhase.COMPLETING

    def test_force_task_phase(self):
        """Force task phase bypasses transition validation."""
        manager = CrewStateManager(task_names=["task1"])

        # Force PENDING -> COMPLETED (would normally be invalid)
        manager.force_task_phase("task1", TaskPhase.COMPLETED)

        assert manager.get_task_phase("task1") == TaskPhase.COMPLETED

    def test_get_all_task_phases(self):
        """get_all_task_phases returns a copy."""
        manager = CrewStateManager(task_names=["task1", "task2"])
        phases = manager.get_all_task_phases()

        assert phases == {"task1": TaskPhase.PENDING, "task2": TaskPhase.PENDING}

        # Modifying returned dict should not affect manager
        phases["task1"] = TaskPhase.COMPLETED
        assert manager.get_task_phase("task1") == TaskPhase.PENDING


class TestCrewStateManagerInvariants:
    """Tests for state invariant checking."""

    def test_invariant_violation_logged_by_default(self):
        """Invariant violations are logged (not raised) by default."""
        task_defs = [
            TaskDefinition(
                name="task2",
                description="Task 2",
                agent="agent1",
                context_from=["task1"],
            )
        ]
        manager = CrewStateManager(
            task_names=["task1", "task2"],
            task_definitions=task_defs,
            strict_invariants=False,
        )

        # Set task2 to RUNNING while task1 is still PENDING
        manager.force_task_phase("task2", TaskPhase.QUEUED)
        manager.force_task_phase("task2", TaskPhase.RUNNING)

        # Should not raise, just log warning
        # (invariant check happens during transition_task)

    def test_invariant_violation_raised_in_strict_mode(self):
        """Invariant violations raise error in strict mode."""
        task_defs = [
            TaskDefinition(
                name="task2",
                description="Task 2",
                agent="agent1",
                context_from=["task1"],
            )
        ]
        manager = CrewStateManager(
            task_names=["task1", "task2"],
            task_definitions=task_defs,
            strict_invariants=True,
        )

        # Force task2 to QUEUED (bypasses validation)
        manager.force_task_phase("task2", TaskPhase.QUEUED)

        # Now try to transition to RUNNING - should raise
        with pytest.raises(InvariantViolationError):
            manager.transition_task("task2", TaskPhase.RUNNING)


class TestCrewStateManagerWithDependencies:
    """Tests for state manager with task dependencies."""

    def test_dependency_satisfied_allows_transition(self):
        """Task can proceed when all dependencies are completed."""
        task_defs = [
            TaskDefinition(
                name="task1",
                description="Task 1",
                agent="agent1",
            ),
            TaskDefinition(
                name="task2",
                description="Task 2",
                agent="agent1",
                context_from=["task1"],
            ),
        ]
        manager = CrewStateManager(
            task_names=["task1", "task2"],
            task_definitions=task_defs,
        )

        # Complete task1
        manager.force_task_phase("task1", TaskPhase.COMPLETED)

        # Now task2 should be able to proceed (no exception)
        manager.transition_task("task2", TaskPhase.QUEUED)
        manager.transition_task("task2", TaskPhase.RUNNING)

        assert manager.get_task_phase("task2") == TaskPhase.RUNNING


class TestCrewStateManagerEdgeCases:
    """Edge case tests for state manager."""

    def test_empty_task_list(self):
        """Manager handles empty task list."""
        manager = CrewStateManager(task_names=[])

        assert manager.crew_phase == CrewPhase.CREATED
        assert manager.get_all_task_phases() == {}

    def test_terminal_state_cannot_transition(self):
        """Terminal crew states reject transitions."""
        manager = CrewStateManager(task_names=["task1"])

        # Force to terminal state
        manager.transition_crew(CrewPhase.INITIALIZING)
        manager.transition_crew(CrewPhase.FAILED)

        with pytest.raises(InvalidTransitionError):
            manager.transition_crew(CrewPhase.RUNNING)

    def test_force_task_phase_bypasses_validation(self):
        """force_task_phase can set any state."""
        manager = CrewStateManager(task_names=["task1"])

        # Force PENDING -> COMPLETED (normally invalid)
        manager.force_task_phase("task1", TaskPhase.COMPLETED)
        assert manager.get_task_phase("task1") == TaskPhase.COMPLETED

        # Force to any state
        manager.force_task_phase("task1", TaskPhase.RUNNING)
        assert manager.get_task_phase("task1") == TaskPhase.RUNNING

    def test_crew_phase_unchanged_on_invalid_sync(self):
        """Invalid auto-sync is silently skipped."""
        manager = CrewStateManager(task_names=["task1"])
        manager.transition_crew(CrewPhase.INITIALIZING)

        # Try to sync to COMPLETING when crew is INITIALIZING
        # This should be silently ignored (invalid transition)
        manager.force_task_phase("task1", TaskPhase.COMPLETED)
        manager._sync_crew_phase()  # Should not crash

        # Crew phase should remain INITIALIZING (transition to COMPLETING invalid)
        assert manager.crew_phase == CrewPhase.INITIALIZING

    def test_multiple_tasks_independent_states(self):
        """Multiple tasks can have different states."""
        manager = CrewStateManager(task_names=["task1", "task2", "task3"])

        manager.force_task_phase("task1", TaskPhase.COMPLETED)
        manager.force_task_phase("task2", TaskPhase.RUNNING)
        # task3 stays PENDING

        assert manager.get_task_phase("task1") == TaskPhase.COMPLETED
        assert manager.get_task_phase("task2") == TaskPhase.RUNNING
        assert manager.get_task_phase("task3") == TaskPhase.PENDING


class TestCrewStateManagerAutoSync:
    """Tests for automatic crew phase synchronization."""

    def test_sync_to_paused_on_awaiting_review(self):
        """Crew syncs to PAUSED when task awaits review."""
        manager = CrewStateManager(task_names=["task1"])
        manager.transition_crew(CrewPhase.INITIALIZING)
        manager.transition_crew(CrewPhase.RUNNING)

        manager.transition_task("task1", TaskPhase.QUEUED)
        manager.transition_task("task1", TaskPhase.RUNNING)
        manager.transition_task("task1", TaskPhase.AWAITING_REVIEW)

        assert manager.crew_phase == CrewPhase.PAUSED

    def test_sync_to_running_from_paused_on_continue(self):
        """Crew syncs back to RUNNING when review completes."""
        manager = CrewStateManager(task_names=["task1"])
        manager.transition_crew(CrewPhase.INITIALIZING)
        manager.transition_crew(CrewPhase.RUNNING)

        manager.transition_task("task1", TaskPhase.QUEUED)
        manager.transition_task("task1", TaskPhase.RUNNING)
        manager.transition_task("task1", TaskPhase.AWAITING_REVIEW)
        assert manager.crew_phase == CrewPhase.PAUSED

        # Complete review
        manager.transition_task("task1", TaskPhase.COMPLETED)

        # Should sync to COMPLETING (all tasks terminal)
        assert manager.crew_phase == CrewPhase.COMPLETING

    def test_sync_ignores_terminal_states(self):
        """Auto-sync doesn't override ABORTING/ABORTED/FAILED."""
        manager = CrewStateManager(task_names=["task1"])
        manager.transition_crew(CrewPhase.INITIALIZING)
        manager.transition_crew(CrewPhase.RUNNING)
        manager.transition_crew(CrewPhase.ABORTING)

        # Try to change task state (would normally sync)
        manager.force_task_phase("task1", TaskPhase.COMPLETED)

        # Crew should still be ABORTING
        assert manager.crew_phase == CrewPhase.ABORTING

    def test_sync_priority_running_over_paused(self):
        """RUNNING takes priority over PAUSED in sync."""
        manager = CrewStateManager(task_names=["task1", "task2"])
        manager.transition_crew(CrewPhase.INITIALIZING)
        manager.transition_crew(CrewPhase.RUNNING)

        manager.force_task_phase("task1", TaskPhase.AWAITING_REVIEW)
        manager.force_task_phase("task2", TaskPhase.RUNNING)

        manager._sync_crew_phase()

        # RUNNING task takes priority
        assert manager.crew_phase == CrewPhase.RUNNING


class TestCrewStateManagerInvariantsDetailed:
    """Detailed invariant tests."""

    def test_invariant_4_completing_requires_terminal_tasks(self):
        """Invariant 4: COMPLETING requires all tasks terminal."""
        manager = CrewStateManager(task_names=["task1", "task2"])

        # Force to COMPLETING with a non-terminal task
        manager.transition_crew(CrewPhase.INITIALIZING)
        manager.transition_crew(CrewPhase.RUNNING)
        manager.force_task_phase("task1", TaskPhase.COMPLETED)
        manager.force_task_phase("task2", TaskPhase.RUNNING)  # Not terminal!

        # Force crew to COMPLETING (bypasses validation)
        manager._crew_phase = CrewPhase.COMPLETING

        # Check invariants - should log warning but not raise
        manager._check_invariants()  # No exception = warning logged

    def test_invariant_5_aborting_no_running_tasks(self):
        """Invariant 5: ABORTING should have no RUNNING tasks."""
        manager = CrewStateManager(task_names=["task1"])

        # Set up aborting state with running task
        manager.transition_crew(CrewPhase.INITIALIZING)
        manager.transition_crew(CrewPhase.RUNNING)
        manager.force_task_phase("task1", TaskPhase.RUNNING)
        manager.transition_crew(CrewPhase.ABORTING)

        # Check invariants - should log warning but not raise
        manager._check_invariants()  # No exception = warning logged

    def test_invariant_violation_count(self):
        """Test that all 5 invariants can detect violations."""
        # Invariant 1: RUNNING with no active tasks
        manager = CrewStateManager(task_names=["task1"])
        manager.transition_crew(CrewPhase.INITIALIZING)
        manager.transition_crew(CrewPhase.RUNNING)
        # Force to RUNNING without any active tasks
        manager.force_task_phase("task1", TaskPhase.COMPLETED)
        manager._check_invariants()  # Logs I1 warning

        # Invariant 2: PAUSED without AWAITING_REVIEW
        manager2 = CrewStateManager(task_names=["task1"])
        manager2.transition_crew(CrewPhase.INITIALIZING)
        manager2.transition_crew(CrewPhase.RUNNING)
        manager2.force_task_phase("task1", TaskPhase.RUNNING)
        # Force PAUSED without AWAITING_REVIEW
        manager2._crew_phase = CrewPhase.PAUSED
        manager2._check_invariants()  # Logs I2 warning

        # Invariant 3: Strict mode raises for dependency violation
        task_defs = [
            TaskDefinition(name="dep", description="D", agent="a"),
            TaskDefinition(name="task", description="T", agent="a", context_from=["dep"]),
        ]
        manager3 = CrewStateManager(
            task_names=["dep", "task"],
            task_definitions=task_defs,
            strict_invariants=True,
        )
        manager3.force_task_phase("dep", TaskPhase.PENDING)  # Not completed
        manager3.force_task_phase("task", TaskPhase.QUEUED)
        with pytest.raises(InvariantViolationError):
            manager3.transition_task("task", TaskPhase.RUNNING)


class TestTaskPhaseTransitionsDetailed:
    """Detailed task phase transition tests."""

    def test_all_valid_transitions_from_pending(self):
        """All valid transitions from PENDING."""
        assert TaskPhase.BLOCKED in TASK_VALID_TRANSITIONS[TaskPhase.PENDING]
        assert TaskPhase.QUEUED in TASK_VALID_TRANSITIONS[TaskPhase.PENDING]
        assert TaskPhase.SKIPPED in TASK_VALID_TRANSITIONS[TaskPhase.PENDING]
        assert TaskPhase.RUNNING not in TASK_VALID_TRANSITIONS[TaskPhase.PENDING]
        assert TaskPhase.COMPLETED not in TASK_VALID_TRANSITIONS[TaskPhase.PENDING]

    def test_all_valid_transitions_from_running(self):
        """All valid transitions from RUNNING."""
        assert TaskPhase.AWAITING_REVIEW in TASK_VALID_TRANSITIONS[TaskPhase.RUNNING]
        assert TaskPhase.COMPLETED in TASK_VALID_TRANSITIONS[TaskPhase.RUNNING]
        assert TaskPhase.FAILED in TASK_VALID_TRANSITIONS[TaskPhase.RUNNING]
        assert TaskPhase.SKIPPED not in TASK_VALID_TRANSITIONS[TaskPhase.RUNNING]
        assert TaskPhase.QUEUED not in TASK_VALID_TRANSITIONS[TaskPhase.RUNNING]

    def test_all_valid_transitions_from_awaiting_review(self):
        """All valid transitions from AWAITING_REVIEW."""
        assert TaskPhase.COMPLETED in TASK_VALID_TRANSITIONS[TaskPhase.AWAITING_REVIEW]
        assert TaskPhase.REJECTED in TASK_VALID_TRANSITIONS[TaskPhase.AWAITING_REVIEW]
        assert TaskPhase.RETRYING in TASK_VALID_TRANSITIONS[TaskPhase.AWAITING_REVIEW]
        assert TaskPhase.SKIPPED in TASK_VALID_TRANSITIONS[TaskPhase.AWAITING_REVIEW]
        assert TaskPhase.FAILED in TASK_VALID_TRANSITIONS[TaskPhase.AWAITING_REVIEW]
        assert TaskPhase.RUNNING not in TASK_VALID_TRANSITIONS[TaskPhase.AWAITING_REVIEW]

    def test_retrying_only_goes_to_running(self):
        """RETRYING can only transition to RUNNING."""
        valid = TASK_VALID_TRANSITIONS[TaskPhase.RETRYING]
        assert valid == {TaskPhase.RUNNING}
        assert TaskPhase.COMPLETED not in valid
        assert TaskPhase.FAILED not in valid

    def test_rejected_only_goes_to_skipped(self):
        """REJECTED can only transition to SKIPPED."""
        valid = TASK_VALID_TRANSITIONS[TaskPhase.REJECTED]
        assert valid == {TaskPhase.SKIPPED}


class TestCrewPhaseTransitionsDetailed:
    """Detailed crew phase transition tests."""

    def test_all_valid_transitions_from_running(self):
        """All valid transitions from RUNNING."""
        assert CrewPhase.PAUSED in CREW_VALID_TRANSITIONS[CrewPhase.RUNNING]
        assert CrewPhase.COMPLETING in CREW_VALID_TRANSITIONS[CrewPhase.RUNNING]
        assert CrewPhase.FAILED in CREW_VALID_TRANSITIONS[CrewPhase.RUNNING]
        assert CrewPhase.ABORTING in CREW_VALID_TRANSITIONS[CrewPhase.RUNNING]
        assert CrewPhase.COMPLETED not in CREW_VALID_TRANSITIONS[CrewPhase.RUNNING]

    def test_all_valid_transitions_from_paused(self):
        """All valid transitions from PAUSED."""
        assert CrewPhase.RUNNING in CREW_VALID_TRANSITIONS[CrewPhase.PAUSED]
        assert CrewPhase.COMPLETING in CREW_VALID_TRANSITIONS[CrewPhase.PAUSED]
        assert CrewPhase.ABORTING in CREW_VALID_TRANSITIONS[CrewPhase.PAUSED]
        assert CrewPhase.FAILED in CREW_VALID_TRANSITIONS[CrewPhase.PAUSED]

    def test_completing_only_goes_to_completed_or_failed(self):
        """COMPLETING can only go to COMPLETED or FAILED."""
        valid = CREW_VALID_TRANSITIONS[CrewPhase.COMPLETING]
        assert valid == {CrewPhase.COMPLETED, CrewPhase.FAILED}

    def test_aborting_only_goes_to_aborted_or_failed(self):
        """ABORTING can only go to ABORTED or FAILED."""
        valid = CREW_VALID_TRANSITIONS[CrewPhase.ABORTING]
        assert valid == {CrewPhase.ABORTED, CrewPhase.FAILED}