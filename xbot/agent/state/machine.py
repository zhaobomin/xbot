"""Session state machine for managing agent session phases.

This module provides the state machine for tracking and validating
session state transitions in the agent runtime.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

from xbot.logging import get_logger

logger = get_logger(__name__)


class SessionPhase(str, Enum):
    """Session lifecycle phases.

    The session progresses through these phases during its lifecycle:
    - IDLE: No active work, ready to accept new requests
    - RUNNING: Agent is processing a request
    - WAITING_PERMISSION: Agent is waiting for user permission
    - WAITING_INTERACTION: Agent is waiting for user input
    - STOPPING: Session is being stopped
    - RESETTING: Session is being reset
    - DELETING: Session SDK file is being deleted
    - FORKING: Session is being forked
    - ERROR: Session encountered an error
    """

    IDLE = "idle"
    RUNNING = "running"
    WAITING_PERMISSION = "waiting_permission"
    WAITING_INTERACTION = "waiting_interaction"
    STOPPING = "stopping"
    RESETTING = "resetting"
    DELETING = "deleting"
    FORKING = "forking"
    ERROR = "error"


# Terminal states that cannot transition to normal operational states
FINAL_STATES: set[SessionPhase] = {
    SessionPhase.ERROR,
}

# States that indicate an ongoing operation (not safe to start new work)
BUSY_STATES: set[SessionPhase] = {
    SessionPhase.RUNNING,
    SessionPhase.WAITING_PERMISSION,
    SessionPhase.WAITING_INTERACTION,
    SessionPhase.STOPPING,
    SessionPhase.RESETTING,
    SessionPhase.DELETING,
    SessionPhase.FORKING,
}


# Valid state transitions: {from_phase: {to_phase1, to_phase2, ...}}
# Note: IDLE -> WAITING_* is allowed for edge cases where a task ends but
# pending requests remain (e.g., agent requests permission then finishes)
VALID_TRANSITIONS: dict[SessionPhase, set[SessionPhase]] = {
    SessionPhase.IDLE: {
        SessionPhase.RUNNING,
        SessionPhase.WAITING_PERMISSION,  # Edge case: handling stale pending request
        SessionPhase.WAITING_INTERACTION,  # Edge case: handling stale pending request
        SessionPhase.STOPPING,
        SessionPhase.RESETTING,
        SessionPhase.DELETING,  # Delete SDK session from idle
        SessionPhase.FORKING,  # Fork SDK session from idle
        SessionPhase.ERROR,
    },
    SessionPhase.RUNNING: {
        SessionPhase.IDLE,
        SessionPhase.WAITING_PERMISSION,
        SessionPhase.WAITING_INTERACTION,
        SessionPhase.STOPPING,
        SessionPhase.RESETTING,
        SessionPhase.ERROR,
    },
    SessionPhase.WAITING_PERMISSION: {
        SessionPhase.RUNNING,
        SessionPhase.IDLE,
        SessionPhase.STOPPING,
        SessionPhase.RESETTING,
        SessionPhase.ERROR,
    },
    SessionPhase.WAITING_INTERACTION: {
        SessionPhase.RUNNING,
        SessionPhase.IDLE,
        SessionPhase.STOPPING,
        SessionPhase.RESETTING,
        SessionPhase.ERROR,
    },
    SessionPhase.STOPPING: {
        SessionPhase.IDLE,
        SessionPhase.ERROR,
    },
    SessionPhase.RESETTING: {
        SessionPhase.IDLE,
        SessionPhase.ERROR,
    },
    SessionPhase.DELETING: {
        SessionPhase.IDLE,  # Delete succeeded
        SessionPhase.ERROR,  # Delete failed
    },
    SessionPhase.FORKING: {
        SessionPhase.IDLE,  # Fork succeeded
        SessionPhase.ERROR,  # Fork failed
    },
    SessionPhase.ERROR: {
        SessionPhase.IDLE,
        SessionPhase.RESETTING,
    },
}


@dataclass
class SessionState:
    """Minimal session state - only what SDK doesn't manage.

    This dataclass holds session-specific state that the Claude SDK doesn't
    manage, including routing information, client connections, and concurrency
    control.

    Attributes:
        session_key: xbot's session ID (e.g., "slack:C12345")
        sdk_session_id: SDK's session UUID
        channel: Channel type (slack, feishu, telegram, etc.)
        chat_id: Chat ID within channel
        client: ClaudeSDKClient instance for this session
        last_active: Timestamp of last activity
        client_pid: PID of SDK subprocess
        process_handle: Process handle for force kill
        lock: Async lock for preventing concurrent queries
        phase: Current session phase
        tasks: List of asyncio tasks for this session
        reason: Reason for the current phase (legacy)
        previous_phase: The phase before the current one (legacy, for rollback)
        transition_count: Number of transitions this session has made (legacy)
    """

    # Identity
    session_key: str  # xbot's session ID (e.g., "slack:C12345")
    sdk_session_id: str | None = None  # SDK's session UUID

    # Routing (required - SDK doesn't know channel/chat_id)
    channel: str = ""  # Channel type (slack, feishu, telegram, etc.)
    chat_id: str = ""  # Chat ID within channel

    # Connection (required - SDK doesn't pool clients)
    client: ClaudeSDKClient | None = field(default=None, compare=False)
    last_active: float = field(default_factory=time.time, compare=False)

    # Process tracking (required - for force kill orphan processes)
    client_pid: int | None = field(default=None, compare=False)
    process_handle: Any | None = field(default=None, compare=False)

    # Concurrency (required - SDK doesn't prevent concurrent queries)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, compare=False)
    phase: SessionPhase = SessionPhase.IDLE

    # Tasks (required - for asyncio task cancellation on session terminate)
    tasks: list[asyncio.Task] = field(default_factory=list, compare=False)

    # Backend metadata (TODO: Remove in next major version - SDK manages these internally)
    model: str | None = None  # Model name for this session
    skills_version: str | None = None  # Skills version for this session
    commands: list[str] = field(default_factory=list, compare=False)  # Commands for this session
    task_id: str | None = None  # Active task ID
    request_id: str | None = None  # Current request ID

    # Legacy fields (for SessionStateMachine compatibility)
    reason: str = ""
    previous_phase: SessionPhase | None = None
    transition_count: int = 0


class SessionStateMachine:
    """Manages session state transitions with validation and logging.

    This class provides a state machine for tracking session lifecycle
    and validating that transitions follow allowed paths.

    Example:
        machine = SessionStateMachine()
        machine.transition("session:1", SessionPhase.RUNNING, reason="start")
        phase = machine.get_phase("session:1")  # RUNNING
    """

    def __init__(
        self,
        on_transition: Callable[[str, SessionPhase, SessionPhase, str], None] | None = None,
    ):
        """Initialize the state machine.

        Args:
            on_transition: Optional callback called on each transition
                with (session_key, from_phase, to_phase, reason)
        """
        self._states: dict[str, SessionState] = {}
        self._on_transition = on_transition

    def get_state(self, session_key: str) -> SessionState:
        """Get current state for a session without creating a tracked entry.

        Args:
            session_key: Session identifier

        Returns:
            SessionState for this session. Missing sessions are treated as IDLE
            but are not added to the state table.
        """
        state = self._states.get(session_key)
        if state is None:
            return SessionState(session_key=session_key)
        return state

    def get_or_create_state(self, session_key: str) -> SessionState:
        """Get or create state for a session."""
        if session_key not in self._states:
            logger.debug(f"Creating new session state: {session_key}")
            self._states[session_key] = SessionState(session_key=session_key)
        return self._states[session_key]

    def get_phase(self, session_key: str) -> SessionPhase:
        """Get current phase for a session.

        Args:
            session_key: Session identifier

        Returns:
            Current SessionPhase
        """
        state = self._states.get(session_key)
        if state is None:
            return SessionPhase.IDLE
        return state.phase

    def transition(
        self,
        session_key: str,
        to_phase: SessionPhase,
        *,
        reason: str = "",
        force: bool = False,
    ) -> bool:
        """Attempt a state transition.

        Args:
            session_key: Session identifier
            to_phase: Target phase
            reason: Reason for transition
            force: If True, bypass validation (for error recovery)

        Returns:
            True if transition succeeded, False otherwise
        """
        state = self.get_or_create_state(session_key)
        from_phase = state.phase

        # Skip if already in target phase with same reason
        if from_phase == to_phase and state.reason == reason:
            return True

        # Same phase with different reason: allow update without transition validation
        if from_phase == to_phase:
            state.reason = reason
            state.transition_count += 1
            logger.debug(
                f"Session state reason update: {session_key} "
                f"{from_phase.value} (reason={reason}, count={state.transition_count})"
            )
            if self._on_transition:
                self._on_transition(session_key, from_phase, to_phase, reason)
            return True

        # Validate transition
        if not force:
            # VALID_TRANSITIONS[from_phase] contains all valid target phases from from_phase
            allowed_targets = VALID_TRANSITIONS.get(from_phase, set())
            if to_phase not in allowed_targets:
                logger.warning(
                    f"Invalid state transition rejected: {session_key} "
                    f"{from_phase.value} -> {to_phase.value} "
                    f"(reason={reason}, current_count={state.transition_count})"
                )
                return False

        # Perform transition
        state.previous_phase = from_phase
        state.phase = to_phase
        state.reason = reason
        state.transition_count += 1

        # Log transition
        logger.debug(
            f"Session state transition: {session_key} "
            f"{from_phase.value} -> {to_phase.value} "
            f"(reason={reason}, count={state.transition_count})"
        )

        # Callback
        if self._on_transition:
            self._on_transition(session_key, from_phase, to_phase, reason)

        return True

    def force_transition(
        self, session_key: str, to_phase: SessionPhase, *, reason: str = ""
    ) -> bool:
        """Force a state transition, bypassing validation.

        Args:
            session_key: Session identifier
            to_phase: Target phase
            reason: Reason for transition

        Returns:
            Always True (for consistency with transition())
        """
        return self.transition(session_key, to_phase, reason=reason, force=True)

    def reset(self, session_key: str) -> None:
        """Reset a session to IDLE state.

        Args:
            session_key: Session identifier
        """
        if session_key in self._states:
            old_state = self._states[session_key]
            logger.debug(
                f"Resetting session state: {session_key} "
                f"(was: {old_state.phase.value}, transitions: {old_state.transition_count})"
            )
            self._states[session_key] = SessionState(session_key=session_key)
        else:
            logger.debug(f"Reset skipped for non-existent session: {session_key}")

    def clear(self, session_key: str) -> None:
        """Remove session state entirely.

        Args:
            session_key: Session identifier
        """
        if session_key in self._states:
            old_state = self._states[session_key]
            logger.debug(
                f"Clearing session state: {session_key} "
                f"(was: {old_state.phase.value}, transitions: {old_state.transition_count})"
            )
            self._states.pop(session_key, None)
        else:
            logger.debug(f"Clear skipped for non-existent session: {session_key}")

    def has_session(self, session_key: str) -> bool:
        """Check whether a session state already exists.

        Unlike get_state() / get_or_create_state(), this method does not create a new state entry.
        """
        return session_key in self._states

    def list_session_keys(self) -> set[str]:
        """Return all session keys currently tracked by the state machine."""
        return set(self._states.keys())

    def is_idle(self, session_key: str) -> bool:
        """Check if session is idle.

        Args:
            session_key: Session identifier

        Returns:
            True if session is in IDLE phase
        """
        return self.get_phase(session_key) == SessionPhase.IDLE

    def is_waiting(self, session_key: str) -> bool:
        """Check if session is waiting for user input.

        Args:
            session_key: Session identifier

        Returns:
            True if session is waiting for permission or interaction
        """
        phase = self.get_phase(session_key)
        return phase in {SessionPhase.WAITING_PERMISSION, SessionPhase.WAITING_INTERACTION}

    def is_active(self, session_key: str) -> bool:
        """Check if session has active work.

        Args:
            session_key: Session identifier

        Returns:
            True if session is in RUNNING phase
        """
        return self.get_phase(session_key) == SessionPhase.RUNNING

    def is_busy(self, session_key: str) -> bool:
        """Check if session is in a busy state (not safe to start new work).

        Args:
            session_key: Session identifier

        Returns:
            True if session is in any busy phase (running, stopping, resetting, etc.)
        """
        return self.get_phase(session_key) in BUSY_STATES

    def is_final(self, session_key: str) -> bool:
        """Check if session is in a final state.

        Args:
            session_key: Session identifier

        Returns:
            True if session is in a final state (error)
        """
        return self.get_phase(session_key) in FINAL_STATES

    def can_start_operation(self, session_key: str) -> bool:
        """Check if a new operation can be started.

        Args:
            session_key: Session identifier

        Returns:
            True if session is IDLE and can start a new operation
        """
        return self.is_idle(session_key)
