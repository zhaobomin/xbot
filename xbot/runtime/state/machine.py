"""State model definitions and compatibility exports."""

from __future__ import annotations

import time
from collections.abc import Callable

from xbot.runtime.state.coordinator import SessionEvent, SessionPhase, SessionState, VALID_TRANSITIONS


_LEGACY_VALID_TRANSITIONS: dict[SessionPhase, set[SessionPhase]] = {
    SessionPhase.IDLE: {
        SessionPhase.RUNNING,
        SessionPhase.WAITING_PERMISSION,
        SessionPhase.WAITING_INTERACTION,
    },
    SessionPhase.RUNNING: {
        SessionPhase.IDLE,
        SessionPhase.WAITING_PERMISSION,
        SessionPhase.WAITING_INTERACTION,
        SessionPhase.STOPPING,
        SessionPhase.ERROR,
    },
    SessionPhase.WAITING_PERMISSION: {
        SessionPhase.RUNNING,
        SessionPhase.IDLE,
        SessionPhase.STOPPING,
        SessionPhase.ERROR,
    },
    SessionPhase.WAITING_INTERACTION: {
        SessionPhase.RUNNING,
        SessionPhase.IDLE,
        SessionPhase.STOPPING,
        SessionPhase.ERROR,
    },
    SessionPhase.STOPPING: {
        SessionPhase.IDLE,
        SessionPhase.ERROR,
    },
    SessionPhase.ERROR: {
        SessionPhase.RUNNING,
        SessionPhase.IDLE,
    },
}


class SessionStateMachine:
    """Backward-compatible direct-transition session state machine.

    New runtime code should use ``RuntimeSessionRegistry`` and event-based
    ``dispatch()``. This adapter preserves the older ``transition()`` API for
    integration tests and callers that have not migrated yet.
    """

    def __init__(
        self,
        on_transition: Callable[[str, SessionPhase, SessionPhase, str], None] | None = None,
    ) -> None:
        self._sessions: dict[str, SessionPhase] = {}
        self._on_transition = on_transition

    def get_phase(self, session_key: str) -> SessionPhase:
        return self._sessions.get(session_key, SessionPhase.IDLE)

    def transition(
        self,
        session_key: str,
        to_phase: SessionPhase,
        *,
        reason: str = "",
        force: bool = False,
    ) -> bool:
        from_phase = self.get_phase(session_key)
        if not force and to_phase != from_phase:
            allowed = _LEGACY_VALID_TRANSITIONS.get(from_phase, set())
            if to_phase not in allowed:
                return False

        self._sessions[session_key] = to_phase
        state = self._get_state_meta().get(session_key)
        if state is not None:
            state.phase = to_phase
            state.last_active = time.time()
        if self._on_transition and to_phase != from_phase:
            self._on_transition(session_key, from_phase, to_phase, reason)
        return True

    def dispatch(
        self,
        session_key: str,
        event: SessionEvent,
        *,
        reason: str = "",
        strict: bool = True,
    ) -> bool:
        event_targets = {
            SessionEvent.USER_MESSAGE: SessionPhase.RUNNING,
            SessionEvent.CLIENT_ACQUIRED: SessionPhase.RUNNING,
            SessionEvent.QUERY_SENT: SessionPhase.RUNNING,
            SessionEvent.STREAM_IDLE_BOUNDARY: SessionPhase.STOPPING,
            SessionEvent.TURN_COMPLETED: SessionPhase.IDLE,
            SessionEvent.DISCONNECT_OK: SessionPhase.IDLE,
            SessionEvent.PERMISSION_PENDING: SessionPhase.WAITING_PERMISSION,
            SessionEvent.INTERACTION_PENDING: SessionPhase.WAITING_INTERACTION,
            SessionEvent.PERMISSION_RESOLVED: SessionPhase.RUNNING,
            SessionEvent.INTERACTION_RESOLVED: SessionPhase.RUNNING,
            SessionEvent.CLIENT_ACQUIRE_FAILED: SessionPhase.ERROR,
            SessionEvent.QUERY_FAILED: SessionPhase.ERROR,
            SessionEvent.STREAM_TIMEOUT: SessionPhase.STOPPING,
            SessionEvent.STREAM_ENDED_UNEXPECTEDLY: SessionPhase.STOPPING,
            SessionEvent.STREAM_ERROR: SessionPhase.STOPPING,
            SessionEvent.INTERRUPT: SessionPhase.STOPPING,
            SessionEvent.SHUTDOWN: SessionPhase.STOPPING,
            SessionEvent.RECOVER: SessionPhase.RUNNING,
        }
        to_phase = event_targets.get(event)
        if to_phase is None:
            return True
        return self.transition(session_key, to_phase, reason=reason, force=not strict)

    def is_idle(self, session_key: str) -> bool:
        return self.get_phase(session_key) == SessionPhase.IDLE

    def is_busy(self, session_key: str) -> bool:
        return not self.is_idle(session_key)

    def set_routing(self, session_key: str, channel: str, chat_id: str) -> None:
        state = self._get_or_create_state(session_key)
        state.channel = channel
        state.chat_id = chat_id
        state.last_active = time.time()

    def resolve_compact_notification_target(self, session_ref: str) -> tuple[str, str, str] | None:
        state = getattr(self, "_state_meta", {}).get(session_ref)
        if state is None:
            return None
        return (state.session_key, state.channel, state.chat_id)

    def set_commands(self, session_key: str, commands: list[str]) -> None:
        state = self._get_or_create_state(session_key)
        state.commands = list(commands)

    def get_commands(self, session_key: str) -> list[str]:
        state = getattr(self, "_state_meta", {}).get(session_key)
        return list(state.commands) if state else []

    def set_sdk_capabilities(
        self,
        session_key: str,
        *,
        skills: list[str] | None = None,
        tools: list[str] | None = None,
        slash_commands: list[str] | None = None,
        skill_source: str = "sdk_only",
    ) -> None:
        state = self._get_or_create_state(session_key)
        if skills is not None:
            state.sdk_skills = list(skills)
        if tools is not None:
            state.sdk_tools = list(tools)
        if slash_commands is not None:
            state.sdk_slash_commands = list(slash_commands)
        state.skill_source = skill_source

    def _set_sdk_session_id_impl(self, session_key: str, sdk_id: str | None) -> None:
        state = self._get_or_create_state(session_key)
        state.sdk_session_id = sdk_id
        if sdk_id:
            self._get_state_meta()[sdk_id] = state

    def resolve_sdk_session_id(self, session_key: str) -> str | None:
        state = self._get_state_meta().get(session_key)
        return state.sdk_session_id if state else None

    def _get_or_create_state(self, session_key: str) -> SessionState:
        state_meta = self._get_state_meta()
        state = state_meta.get(session_key)
        if state is None:
            state = SessionState(session_key=session_key, phase=self.get_phase(session_key))
            state_meta[session_key] = state
        return state

    def _get_state_meta(self) -> dict[str, SessionState]:
        state_meta = getattr(self, "_state_meta", None)
        if state_meta is None:
            state_meta = {}
            self._state_meta = state_meta
        return state_meta


__all__ = [
    "SessionEvent",
    "SessionPhase",
    "SessionState",
    "SessionStateMachine",
    "VALID_TRANSITIONS",
]
