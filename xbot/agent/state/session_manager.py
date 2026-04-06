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

    # === SDK Session ID ===

    def set_sdk_session_id(self, session_key: str, sdk_id: str | None) -> None:
        """Set SDK session UUID and update bidirectional mapping.

        - If sdk_id is None, clears the mapping
        - If session already has sdk_id, removes old mapping before adding new
        """
        state = self.get(session_key)
        if state is None:
            logger.warning(f"set_sdk_session_id: session {session_key} not found")
            return

        # Remove old mapping if exists
        if state.sdk_session_id and state.sdk_session_id in self._sdk_index:
            del self._sdk_index[state.sdk_session_id]

        # Set new mapping
        state.sdk_session_id = sdk_id
        if sdk_id:
            self._sdk_index[sdk_id] = session_key

    # === Routing ===

    def set_routing(self, session_key: str, channel: str, chat_id: str) -> None:
        """Set channel and chat_id for routing."""
        state = self.get_or_create(session_key)
        state.channel = channel
        state.chat_id = chat_id

    def get_routing(self, session_key: str) -> tuple[str, str] | None:
        """Get channel and chat_id, or None if session not found."""
        state = self.get(session_key)
        if state is None:
            return None
        return (state.channel, state.chat_id)

    def resolve_routing(self, identifier: str) -> tuple[str, str, str] | None:
        """Resolve routing from either session_key or sdk_session_id.

        Returns: (session_key, channel, chat_id) or None
        """
        # Try as session_key first
        state = self.get(identifier)
        if state:
            return (state.session_key, state.channel, state.chat_id)

        # Try as sdk_session_id
        state = self.get_by_sdk_id(identifier)
        if state:
            return (state.session_key, state.channel, state.chat_id)

        return None
