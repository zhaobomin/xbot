"""Two-layer state machine for Crew and Task lifecycle management.

CrewPhase tracks the overall crew lifecycle.
TaskPhase tracks individual task progression.
CrewStateManager enforces valid transitions and consistency invariants.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable

from loguru import logger

from xbot.agent.crew.models import TaskDefinition

# ---------------------------------------------------------------------------
# Crew-level states
# ---------------------------------------------------------------------------

class CrewPhase(str, Enum):
    CREATED = "created"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"  # Waiting for human review
    COMPLETING = "completing"  # All tasks done, generating summary
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTING = "aborting"  # User requested abort, cleaning up
    ABORTED = "aborted"


CREW_VALID_TRANSITIONS: dict[CrewPhase, set[CrewPhase]] = {
    CrewPhase.CREATED: {CrewPhase.INITIALIZING, CrewPhase.FAILED},
    CrewPhase.INITIALIZING: {CrewPhase.RUNNING, CrewPhase.FAILED},
    CrewPhase.RUNNING: {CrewPhase.PAUSED, CrewPhase.COMPLETING, CrewPhase.FAILED, CrewPhase.ABORTING},
    CrewPhase.PAUSED: {CrewPhase.RUNNING, CrewPhase.COMPLETING, CrewPhase.ABORTING, CrewPhase.FAILED},
    CrewPhase.COMPLETING: {CrewPhase.COMPLETED, CrewPhase.FAILED},
    CrewPhase.ABORTING: {CrewPhase.ABORTED, CrewPhase.FAILED},
    # Terminal states
    CrewPhase.COMPLETED: set(),
    CrewPhase.FAILED: set(),
    CrewPhase.ABORTED: set(),
}


# ---------------------------------------------------------------------------
# Task-level states
# ---------------------------------------------------------------------------

class TaskPhase(str, Enum):
    PENDING = "pending"
    BLOCKED = "blocked"  # Upstream dependencies not yet completed
    QUEUED = "queued"  # Dependencies satisfied, waiting to execute
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"  # Waiting for human review
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # Skipped due to upstream failure
    REJECTED = "rejected"  # Human rejected
    RETRYING = "retrying"  # Re-executing with feedback


TASK_VALID_TRANSITIONS: dict[TaskPhase, set[TaskPhase]] = {
    TaskPhase.PENDING: {TaskPhase.BLOCKED, TaskPhase.QUEUED, TaskPhase.SKIPPED},
    TaskPhase.BLOCKED: {TaskPhase.QUEUED, TaskPhase.SKIPPED},
    TaskPhase.QUEUED: {TaskPhase.RUNNING, TaskPhase.SKIPPED},
    TaskPhase.RUNNING: {TaskPhase.AWAITING_REVIEW, TaskPhase.COMPLETED, TaskPhase.FAILED},
    TaskPhase.AWAITING_REVIEW: {TaskPhase.COMPLETED, TaskPhase.REJECTED, TaskPhase.RETRYING, TaskPhase.SKIPPED, TaskPhase.FAILED},
    TaskPhase.RETRYING: {TaskPhase.RUNNING},
    TaskPhase.REJECTED: {TaskPhase.SKIPPED},
    # Terminal states
    TaskPhase.COMPLETED: set(),
    TaskPhase.FAILED: set(),
    TaskPhase.SKIPPED: set(),
}


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""


class InvariantViolationError(Exception):
    """Raised when a state invariant is violated."""


# ---------------------------------------------------------------------------
# State Manager
# ---------------------------------------------------------------------------

class CrewStateManager:
    """Manages the two-layer Crew + Task state machine with invariant checking.

    After every task state transition the manager automatically synchronises
    the crew-level phase and checks consistency invariants.
    """

    def __init__(
        self,
        task_names: list[str],
        task_definitions: list[TaskDefinition] | None = None,
        on_transition: Callable[[str, str, str, str, str], None] | None = None,
        strict_invariants: bool = False,
    ):
        """Initialise state manager.

        Args:
            task_names: Ordered list of task names.
            task_definitions: Optional task definitions for dependency checking.
            on_transition: Callback(layer, name, from_phase, to_phase, reason).
            strict_invariants: If True, raise InvariantViolationError on critical
                violations instead of just logging warnings.
        """
        self._crew_phase = CrewPhase.CREATED
        self._task_phases: dict[str, TaskPhase] = {
            name: TaskPhase.PENDING for name in task_names
        }
        self._task_defs: dict[str, TaskDefinition] = {}
        if task_definitions:
            for td in task_definitions:
                self._task_defs[td.name] = td
        self._on_transition = on_transition
        self._strict_invariants = strict_invariants

    @property
    def crew_phase(self) -> CrewPhase:
        return self._crew_phase

    def get_task_phase(self, task_name: str) -> TaskPhase:
        return self._task_phases[task_name]

    def get_all_task_phases(self) -> dict[str, TaskPhase]:
        return dict(self._task_phases)

    # -- Crew transitions ---------------------------------------------------

    def transition_crew(self, to_phase: CrewPhase, reason: str = "") -> None:
        """Explicitly transition the crew phase."""
        from_phase = self._crew_phase
        if to_phase not in CREW_VALID_TRANSITIONS[from_phase]:
            raise InvalidTransitionError(
                f"Crew: {from_phase.value} -> {to_phase.value} is not allowed"
            )
        self._crew_phase = to_phase
        self._log_transition("crew", "crew", from_phase.value, to_phase.value, reason)

    # -- Task transitions ---------------------------------------------------

    def transition_task(self, task_name: str, to_phase: TaskPhase, reason: str = "") -> None:
        """Transition a task phase with automatic crew sync and invariant check."""
        if task_name not in self._task_phases:
            raise KeyError(f"Unknown task: {task_name}")

        from_phase = self._task_phases[task_name]
        if to_phase not in TASK_VALID_TRANSITIONS[from_phase]:
            raise InvalidTransitionError(
                f"Task '{task_name}': {from_phase.value} -> {to_phase.value} is not allowed"
            )
        self._task_phases[task_name] = to_phase
        self._log_transition("task", task_name, from_phase.value, to_phase.value, reason)

        # Auto-sync crew phase based on aggregate task state
        self._sync_crew_phase()
        self._check_invariants()

    def force_task_phase(self, task_name: str, phase: TaskPhase) -> None:
        """Force a task to a specific phase (for checkpoint recovery)."""
        if task_name not in self._task_phases:
            raise KeyError(f"Unknown task: {task_name}")
        self._task_phases[task_name] = phase

    # -- Internal -----------------------------------------------------------

    def _sync_crew_phase(self) -> None:
        """Derive crew phase from aggregate task phases."""
        if self._crew_phase in {CrewPhase.ABORTING, CrewPhase.ABORTED, CrewPhase.FAILED}:
            return  # Don't override terminal/aborting states

        phases = set(self._task_phases.values())
        terminal = {TaskPhase.COMPLETED, TaskPhase.SKIPPED, TaskPhase.FAILED, TaskPhase.REJECTED}
        active = {TaskPhase.RUNNING, TaskPhase.QUEUED, TaskPhase.RETRYING}

        if phases & active:
            # Active tasks take priority — crew should keep running
            self._set_crew_phase(CrewPhase.RUNNING)
        elif TaskPhase.AWAITING_REVIEW in phases:
            # No active tasks but at least one awaiting review — pause
            self._set_crew_phase(CrewPhase.PAUSED)
        elif all(p in terminal for p in phases):
            self._set_crew_phase(CrewPhase.COMPLETING)

    def _set_crew_phase(self, to_phase: CrewPhase) -> None:
        """Set crew phase if transition is valid; silently skip otherwise."""
        from_phase = self._crew_phase
        if from_phase == to_phase:
            return
        if to_phase in CREW_VALID_TRANSITIONS.get(from_phase, set()):
            self._crew_phase = to_phase
            self._log_transition("crew", "crew", from_phase.value, to_phase.value, "auto-sync")

    def _check_invariants(self) -> None:
        """Validate state consistency invariants after transitions.

        Critical violations (Invariant 3) will raise InvariantViolationError
        if strict_invariants is enabled, otherwise log a warning.
        """
        # Invariant 1: Crew RUNNING -> at least one task active
        if self._crew_phase == CrewPhase.RUNNING:
            active = {TaskPhase.RUNNING, TaskPhase.QUEUED, TaskPhase.AWAITING_REVIEW, TaskPhase.RETRYING}
            if not any(p in active for p in self._task_phases.values()):
                msg = "Crew RUNNING but no active tasks"
                logger.warning(f"Invariant violation: {msg}")

        # Invariant 2: Crew PAUSED -> at least one AWAITING_REVIEW, no RUNNING
        if self._crew_phase == CrewPhase.PAUSED:
            if not any(p == TaskPhase.AWAITING_REVIEW for p in self._task_phases.values()):
                msg = "Crew PAUSED but no tasks awaiting review"
                logger.warning(f"Invariant violation: {msg}")
            if any(p == TaskPhase.RUNNING for p in self._task_phases.values()):
                msg = "Crew PAUSED but tasks still RUNNING"
                logger.warning(f"Invariant violation: {msg}")

        # Invariant 3: Task RUNNING/QUEUED -> all context_from deps are COMPLETED
        # This is a critical invariant - violates data integrity for downstream tasks
        for task_name, phase in self._task_phases.items():
            if phase in {TaskPhase.RUNNING, TaskPhase.QUEUED}:
                task_def = self._task_defs.get(task_name)
                if task_def:
                    for dep in task_def.context_from:
                        dep_phase = self._task_phases.get(dep)
                        if dep_phase and dep_phase != TaskPhase.COMPLETED:
                            msg = (
                                f"Task '{task_name}' is {phase.value} "
                                f"but dependency '{dep}' is {dep_phase.value}"
                            )
                            if self._strict_invariants:
                                raise InvariantViolationError(
                                    f"Critical invariant violation: {msg}"
                                )
                            logger.warning(f"Invariant violation: {msg}")

        # Invariant 4: Crew COMPLETING -> all tasks terminal
        if self._crew_phase == CrewPhase.COMPLETING:
            terminal = {TaskPhase.COMPLETED, TaskPhase.SKIPPED, TaskPhase.FAILED, TaskPhase.REJECTED}
            if not all(p in terminal for p in self._task_phases.values()):
                msg = "Crew COMPLETING but not all tasks are terminal"
                logger.warning(f"Invariant violation: {msg}")

        # Invariant 5: Crew ABORTING -> no RUNNING tasks
        if self._crew_phase == CrewPhase.ABORTING:
            if any(p == TaskPhase.RUNNING for p in self._task_phases.values()):
                msg = "Crew ABORTING but tasks still RUNNING"
                logger.warning(f"Invariant violation: {msg}")

    def _log_transition(
        self, layer: str, name: str, from_phase: str, to_phase: str, reason: str
    ) -> None:
        logger.debug(f"[crew-state] {layer}/{name}: {from_phase} -> {to_phase}" + (f" ({reason})" if reason else ""))
        if self._on_transition:
            self._on_transition(layer, name, from_phase, to_phase, reason)
