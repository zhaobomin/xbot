"""Unified session state management - single source of truth."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from xbot.agent.state.machine import SessionPhase, SessionState
from xbot.logging import get_logger

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

logger = get_logger(__name__)


class SessionManager:
    """Unified session state management.

    Replaces the 5-layer state management system:
    - StateMachine -> Store -> Adapter -> legacy dicts -> Coordinator

    Only manages what SDK doesn't:
    - Connection pooling (client instances)
    - Request routing (channel/chat_id)
    - Concurrency protection (phase state machine)
    - Task lifecycle (asyncio.Task tracking)
    """

    def __init__(self):
        self._sessions: dict[str, SessionState] = {}
        self._sdk_index: dict[str, str] = {}  # sdk_session_id -> session_key
        self._global_lock = asyncio.Lock()

    # === Lifecycle ===

    def get(self, session_key: str) -> SessionState | None:
        """Get session state by session_key, or None if not found."""
        return self._sessions.get(session_key)

    def get_or_create(self, session_key: str) -> SessionState:
        """Get existing session or create new one with defaults."""
        if session_key not in self._sessions:
            self._sessions[session_key] = SessionState(session_key=session_key)
        return self._sessions[session_key]

    def get_by_sdk_id(self, sdk_session_id: str) -> SessionState | None:
        """Get session state by SDK session UUID."""
        session_key = self._sdk_index.get(sdk_session_id)
        if session_key is None:
            return None
        return self._sessions.get(session_key)
