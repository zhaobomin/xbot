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

    # === Concurrency ===

    def can_start_request(self, session_key: str) -> bool:
        """Check if a new request can be started (phase must be IDLE)."""
        state = self.get(session_key)
        if state is None:
            return True  # New session can start
        return state.phase == SessionPhase.IDLE

    def start_request(self, session_key: str) -> bool:
        """Transition to RUNNING phase. Returns False if not IDLE."""
        state = self.get_or_create(session_key)
        if state.phase != SessionPhase.IDLE:
            logger.warning(
                f"start_request: session {session_key} not IDLE (phase={state.phase})"
            )
            return False
        state.phase = SessionPhase.RUNNING
        state.last_active = time.time()
        return True

    def end_request(self, session_key: str, phase: SessionPhase = SessionPhase.IDLE) -> None:
        """Transition to specified phase after request completes."""
        state = self.get(session_key)
        if state is None:
            logger.warning(f"end_request: session {session_key} not found")
            return
        state.phase = phase
        state.last_active = time.time()

    # === Connection ===

    def set_client(self, session_key: str, client: ClaudeSDKClient) -> None:
        """Set SDK client for session."""
        state = self.get_or_create(session_key)
        state.client = client
        state.last_active = time.time()

    def get_client(self, session_key: str) -> ClaudeSDKClient | None:
        """Get SDK client for session."""
        state = self.get(session_key)
        if state is None:
            return None
        return state.client

    def has_client(self, session_key: str) -> bool:
        """Check if session has an active client."""
        state = self.get(session_key)
        return state is not None and state.client is not None

    def list_client_sessions(self) -> list[str]:
        """List all sessions that have active clients."""
        return [
            key for key, state in self._sessions.items()
            if state.client is not None
        ]

    def set_process_info(
        self,
        session_key: str,
        pid: int | None,
        handle: Any | None
    ) -> None:
        """Set process tracking info for force kill capability."""
        state = self.get(session_key)
        if state is None:
            return
        state.client_pid = pid
        state.process_handle = handle

    # === Tasks ===

    def register_task(self, session_key: str, task: asyncio.Task) -> None:
        """Register an asyncio.Task for tracking."""
        state = self.get(session_key)
        if state is None:
            logger.warning(f"register_task: session {session_key} not found")
            return
        state.tasks.append(task)

    def get_active_tasks(self, session_key: str) -> list[asyncio.Task]:
        """Get all active asyncio.Tasks for session."""
        state = self.get(session_key)
        if state is None:
            return []
        return list(state.tasks)

    async def cancel_all_tasks(self, session_key: str) -> int:
        """Cancel all active tasks for session. Returns count cancelled."""
        state = self.get(session_key)
        if state is None:
            return 0

        count = 0
        for task in state.tasks:
            if not task.done():
                task.cancel()
                count += 1

        # Wait for all tasks to complete their cancellation
        if state.tasks:
            await asyncio.gather(*state.tasks, return_exceptions=True)

        state.tasks = []
        return count
