"""Unified session state coordinator.

Single write-path state machine for all runtime sessions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SessionPhase(str, Enum):
    """Unified runtime session phases."""

    IDLE = "idle"
    # Legacy aliases kept for older integration tests and response handlers
    # that still use the pre-coordinator phase vocabulary.
    RUNNING = "running"
    ACQUIRING_CLIENT = "acquiring_client"
    SENDING_QUERY = "sending_query"
    RECEIVING_STREAM = "receiving_stream"
    WAITING_PERMISSION = "waiting_permission"
    WAITING_INTERACTION = "waiting_interaction"
    DRAINING = "draining"
    STOPPING = "stopping"
    ERROR = "error"
    RELEASING_CLIENT = "releasing_client"
    BROKEN = "broken"


class SessionEvent(str, Enum):
    """Domain events accepted by SessionCoordinator.dispatch()."""

    USER_MESSAGE = "user_message"
    CLIENT_ACQUIRED = "client_acquired"
    CLIENT_ACQUIRE_FAILED = "client_acquire_failed"
    QUERY_SENT = "query_sent"
    QUERY_FAILED = "query_failed"
    STREAM_IDLE_BOUNDARY = "stream_idle_boundary"
    STREAM_TIMEOUT = "stream_timeout"
    STREAM_ENDED_UNEXPECTEDLY = "stream_ended_unexpectedly"
    STREAM_ERROR = "stream_error"
    PERMISSION_PENDING = "permission_pending"
    INTERACTION_PENDING = "interaction_pending"
    PERMISSION_RESOLVED = "permission_resolved"
    INTERACTION_RESOLVED = "interaction_resolved"
    PERMISSION_EXPIRED = "permission_expired"
    INTERACTION_EXPIRED = "interaction_expired"
    DISCONNECT_OK = "disconnect_ok"
    DISCONNECT_FAILED = "disconnect_failed"
    TURN_COMPLETED = "turn_completed"
    RECOVER = "recover"
    INTERRUPT = "interrupt"
    SHUTDOWN = "shutdown"


@dataclass
class SessionTransition:
    """Transition audit record."""

    ts: float
    event: SessionEvent
    from_phase: SessionPhase
    to_phase: SessionPhase
    reason: str = ""


@dataclass
class SessionState:
    """Runtime session state + metadata.

    State is coordinated by SessionCoordinator. Auxiliary metadata stays colocated
    so callers have one canonical runtime state container.
    """

    session_key: str
    phase: SessionPhase = SessionPhase.IDLE
    channel: str = ""
    chat_id: str = ""
    sdk_session_id: str | None = None
    execution_cwd: str | None = None
    workspace_dir: str | None = None
    commands: list[str] = field(default_factory=list)
    sdk_skills: list[str] = field(default_factory=list)
    sdk_tools: list[str] = field(default_factory=list)
    sdk_slash_commands: list[str] = field(default_factory=list)
    skill_source: str = "sdk_only"
    client: Any | None = None
    task_id: str | None = None
    request_id: str | None = None
    last_active: float = field(default_factory=time.time)
    recovery_fail_count: int = 0
    transition_count: int = 0
    illegal_transition_count: int = 0
    transitions: list[SessionTransition] = field(default_factory=list)


_EVENT_TARGET: dict[SessionEvent, SessionPhase] = {
    SessionEvent.USER_MESSAGE: SessionPhase.ACQUIRING_CLIENT,
    SessionEvent.CLIENT_ACQUIRED: SessionPhase.SENDING_QUERY,
    SessionEvent.CLIENT_ACQUIRE_FAILED: SessionPhase.BROKEN,
    SessionEvent.QUERY_SENT: SessionPhase.RECEIVING_STREAM,
    SessionEvent.QUERY_FAILED: SessionPhase.BROKEN,
    SessionEvent.STREAM_IDLE_BOUNDARY: SessionPhase.DRAINING,
    SessionEvent.STREAM_TIMEOUT: SessionPhase.RELEASING_CLIENT,
    SessionEvent.STREAM_ENDED_UNEXPECTEDLY: SessionPhase.RELEASING_CLIENT,
    SessionEvent.STREAM_ERROR: SessionPhase.RELEASING_CLIENT,
    SessionEvent.PERMISSION_PENDING: SessionPhase.WAITING_PERMISSION,
    SessionEvent.INTERACTION_PENDING: SessionPhase.WAITING_INTERACTION,
    SessionEvent.PERMISSION_RESOLVED: SessionPhase.RECEIVING_STREAM,
    SessionEvent.INTERACTION_RESOLVED: SessionPhase.RECEIVING_STREAM,
    SessionEvent.PERMISSION_EXPIRED: SessionPhase.IDLE,
    SessionEvent.INTERACTION_EXPIRED: SessionPhase.IDLE,
    SessionEvent.DISCONNECT_OK: SessionPhase.IDLE,
    SessionEvent.DISCONNECT_FAILED: SessionPhase.BROKEN,
    SessionEvent.TURN_COMPLETED: SessionPhase.IDLE,
    SessionEvent.RECOVER: SessionPhase.ACQUIRING_CLIENT,
    SessionEvent.INTERRUPT: SessionPhase.RELEASING_CLIENT,
    SessionEvent.SHUTDOWN: SessionPhase.RELEASING_CLIENT,
}


VALID_TRANSITIONS: dict[SessionPhase, set[SessionPhase]] = {
    SessionPhase.IDLE: {
        SessionPhase.RUNNING,
        SessionPhase.ACQUIRING_CLIENT,
        SessionPhase.WAITING_PERMISSION,
        SessionPhase.WAITING_INTERACTION,
        SessionPhase.RELEASING_CLIENT,
        SessionPhase.BROKEN,
    },
    SessionPhase.RUNNING: {
        SessionPhase.IDLE,
        SessionPhase.WAITING_PERMISSION,
        SessionPhase.WAITING_INTERACTION,
        SessionPhase.STOPPING,
        SessionPhase.ERROR,
        SessionPhase.BROKEN,
    },
    SessionPhase.ACQUIRING_CLIENT: {
        SessionPhase.SENDING_QUERY,
        SessionPhase.RELEASING_CLIENT,
        SessionPhase.BROKEN,
    },
    SessionPhase.SENDING_QUERY: {
        SessionPhase.IDLE,
        SessionPhase.RECEIVING_STREAM,
        SessionPhase.RELEASING_CLIENT,
        SessionPhase.BROKEN,
    },
    SessionPhase.RECEIVING_STREAM: {
        SessionPhase.IDLE,
        SessionPhase.DRAINING,
        SessionPhase.RELEASING_CLIENT,
        SessionPhase.WAITING_PERMISSION,
        SessionPhase.WAITING_INTERACTION,
        SessionPhase.BROKEN,
    },
    SessionPhase.WAITING_PERMISSION: {
        SessionPhase.RECEIVING_STREAM,
        SessionPhase.IDLE,
        SessionPhase.RELEASING_CLIENT,
        SessionPhase.BROKEN,
    },
    SessionPhase.WAITING_INTERACTION: {
        SessionPhase.RECEIVING_STREAM,
        SessionPhase.IDLE,
        SessionPhase.RELEASING_CLIENT,
        SessionPhase.BROKEN,
    },
    SessionPhase.DRAINING: {
        SessionPhase.IDLE,
        SessionPhase.RELEASING_CLIENT,
        SessionPhase.WAITING_PERMISSION,
        SessionPhase.WAITING_INTERACTION,
        SessionPhase.BROKEN,
    },
    SessionPhase.STOPPING: {
        SessionPhase.IDLE,
        SessionPhase.ERROR,
        SessionPhase.BROKEN,
    },
    SessionPhase.ERROR: {
        SessionPhase.RUNNING,
        SessionPhase.IDLE,
        SessionPhase.BROKEN,
    },
    SessionPhase.RELEASING_CLIENT: {
        SessionPhase.IDLE,
        SessionPhase.BROKEN,
    },
    SessionPhase.BROKEN: {
        SessionPhase.ACQUIRING_CLIENT,
        SessionPhase.RELEASING_CLIENT,
    },
}


class SessionCoordinator:
    """Single-write runtime session coordinator."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def get_or_create(self, session_key: str) -> SessionState:
        state = self._sessions.get(session_key)
        if state is None:
            state = SessionState(session_key=session_key)
            self._sessions[session_key] = state
        return state

    def get(self, session_key: str) -> SessionState | None:
        return self._sessions.get(session_key)

    def remove(self, session_key: str) -> None:
        self._sessions.pop(session_key, None)

    def list_keys(self) -> list[str]:
        return list(self._sessions.keys())

    def get_phase(self, session_key: str) -> SessionPhase:
        state = self.get(session_key)
        if state is None:
            return SessionPhase.IDLE
        return state.phase

    def dispatch(
        self,
        session_key: str,
        event: SessionEvent,
        *,
        reason: str = "",
        strict: bool = True,
    ) -> bool:
        state = self.get_or_create(session_key)
        from_phase = state.phase
        to_phase = _EVENT_TARGET[event]

        if strict and to_phase != from_phase:
            allowed = VALID_TRANSITIONS.get(from_phase, set())
            if to_phase not in allowed:
                state.illegal_transition_count += 1
                state.last_active = time.time()
                return False

        state.phase = to_phase
        state.last_active = time.time()
        state.transition_count += 1
        state.transitions.append(
            SessionTransition(
                ts=state.last_active,
                event=event,
                from_phase=from_phase,
                to_phase=to_phase,
                reason=reason,
            )
        )
        if len(state.transitions) > 50:
            state.transitions = state.transitions[-50:]
        return True

    def snapshot(self) -> dict[str, Any]:
        by_phase: dict[str, int] = {}
        illegal = 0
        for state in self._sessions.values():
            by_phase[state.phase.value] = by_phase.get(state.phase.value, 0) + 1
            illegal += state.illegal_transition_count
        return {
            "sessions": len(self._sessions),
            "by_phase": by_phase,
            "illegal_transition_total": illegal,
        }
