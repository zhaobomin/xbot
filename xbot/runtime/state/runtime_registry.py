"""Unified session state management - single source of truth."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from xbot.platform.logging.core import get_logger
from xbot.runtime.state.machine import VALID_TRANSITIONS, SessionPhase, SessionState

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

logger = get_logger(__name__)


class RuntimeSessionRegistry:
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

    def _set_sdk_session_id_impl(self, session_key: str, sdk_id: str | None) -> None:
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

    async def set_sdk_session_id(self, session_key: str, sdk_id: str | None) -> None:
        """Set SDK session UUID and update bidirectional mapping (async for compatibility)."""
        self._set_sdk_session_id_impl(session_key, sdk_id)

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

    # === Cleanup ===

    async def cleanup_session(self, session_key: str) -> None:
        """Clean up session: cancel tasks, remove from indices, delete state."""
        state = self.get(session_key)
        if state is None:
            return

        # Cancel active tasks
        await self.cancel_all_tasks(session_key)

        # Remove SDK index mapping
        if state.sdk_session_id and state.sdk_session_id in self._sdk_index:
            del self._sdk_index[state.sdk_session_id]

        # Delete session state
        del self._sessions[session_key]

        logger.info(f"cleanup_session: removed {session_key}")

    def list_stale_sessions(self, ttl_seconds: float) -> list[str]:
        """List sessions that have been inactive longer than TTL."""
        now = time.time()
        stale = []
        for key, state in self._sessions.items():
            if state.last_active < now - ttl_seconds:
                stale.append(key)
        return stale

    # === Store Compatibility ===

    def list_keys(self) -> list[str]:
        """List all session keys."""
        return list(self._sessions.keys())

    # Alias for compatibility
    list_sessions = list_keys

    async def delete(self, session_key: str, delete_sdk_file: bool = False) -> bool:
        """Delete a session."""
        await self.cleanup_session(session_key)
        return True

    # === State Checker Compatibility ===

    def check_session(self, session_key: str) -> dict[str, Any]:
        """Check session consistency (for debugging)."""
        state = self.get(session_key)
        if state is None:
            return {"exists": False}
        return {
            "exists": True,
            "phase": state.phase.value,
            "sdk_session_id": state.sdk_session_id,
            "channel": state.channel,
            "chat_id": state.chat_id,
            "has_client": state.client is not None,
            "active_tasks": len(state.tasks),
            "last_active": state.last_active,
        }

    # === State Machine Compatibility (delegates to SessionState) ===

    def get_phase(self, session_key: str) -> SessionPhase:
        """Get session current phase."""
        state = self.get(session_key)
        if state is None:
            return SessionPhase.IDLE
        return state.phase

    def get_state(self, session_key: str) -> SessionState | None:
        """Get session state."""
        return self.get(session_key)

    def has_session(self, session_key: str) -> bool:
        """Check if session exists."""
        return session_key in self._sessions

    def force_transition(
        self,
        session_key: str,
        to_phase: SessionPhase,
        reason: str = "",
    ) -> bool:
        """Force state transition."""
        state = self.get_or_create(session_key)
        old_phase = state.phase
        state.phase = to_phase
        state.reason = reason
        state.previous_phase = old_phase
        state.transition_count += 1
        logger.debug(f"force_transition: {session_key} {old_phase} -> {to_phase} ({reason})")
        return True

    def transition(
        self,
        session_key: str,
        to_phase: SessionPhase,
        *,
        reason: str = "",
        force: bool = False,
    ) -> bool:
        """State transition with validation."""
        state = self.get(session_key)
        if state is None:
            # Create new session if doesn't exist
            state = self.get_or_create(session_key)

        old_phase = state.phase

        # Check valid transition unless forced
        if not force:
            valid_transitions = VALID_TRANSITIONS.get(old_phase, set())
            if to_phase not in valid_transitions and to_phase != old_phase:
                logger.warning(f"transition: invalid {session_key} {old_phase} -> {to_phase}")
                return False

        state.phase = to_phase
        state.reason = reason
        state.previous_phase = old_phase
        state.transition_count += 1
        logger.debug(f"transition: {session_key} {old_phase} -> {to_phase} ({reason})")
        return True

    # === Task Management ===

    def unregister_task(self, session_key: str, task: asyncio.Task) -> None:
        """Unregister an active task."""
        state = self.get(session_key)
        if state and task in state.tasks:
            state.tasks.remove(task)

    def has_active_tasks(self, session_key: str) -> bool:
        """Check if session has active tasks."""
        return len(self.get_active_tasks(session_key)) > 0

    def pop_active_tasks(self, session_key: str) -> list[asyncio.Task]:
        """Pop and clear active tasks list."""
        state = self.get(session_key)
        if state:
            tasks = list(state.tasks)
            state.tasks.clear()
            return tasks
        return []

    def clear_task_list(self, session_key: str) -> list[asyncio.Task]:
        """Clear task list (unconditional)."""
        return self.pop_active_tasks(session_key)

    def cleanup_empty_task_list(self, session_key: str) -> bool:
        """Clean up empty task list."""
        state = self.get(session_key)
        if state and not state.tasks:
            state.tasks.clear()
            return True
        return False

    def register_task_sync(self, session_key: str, task: asyncio.Task) -> None:
        """Register task synchronously."""
        state = self.get_or_create(session_key)
        state.tasks.append(task)

    # === Lock Management ===

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """Get or create session lock."""
        state = self.get_or_create(session_key)
        return state.lock

    # Alias for compatibility
    get_lock_object = get_lock

    def release_lock(self, session_key: str) -> bool:
        """Release session lock."""
        # Lock release is not straightforward - just return True
        # The lock will be released when the context exits
        return True

    # === Transaction (async context manager) ===

    class _Transaction:
        """Transaction object for atomic state changes."""

        def __init__(self, manager: "RuntimeSessionRegistry", session_key: str, validate_on_commit: bool = True):
            self.manager = manager
            self.session_key = session_key
            self.validate_on_commit = validate_on_commit
            self._phase_set = False
            self._lock_acquired = False

        def set_phase(self, phase: SessionPhase, reason: str = "") -> None:
            """Set session phase within transaction."""
            self.manager.force_transition(self.session_key, phase, reason=reason)
            self._phase_set = True

        def acquire_lock(self) -> None:
            """Mark that lock should be acquired (no-op in new implementation)."""
            self._lock_acquired = True

        def release_lock(self) -> None:
            """Release lock (no-op in new implementation)."""
            self._lock_acquired = False

        def set_sdk_session_id(self, sdk_session_id: str | None) -> None:
            """Set SDK session ID within transaction."""
            self.manager._set_sdk_session_id_impl(self.session_key, sdk_session_id)

        def clear_sdk_session_id(self) -> None:
            """Clear SDK session ID within transaction."""
            self.manager._set_sdk_session_id_impl(self.session_key, None)

    @asynccontextmanager
    async def transaction(self, session_key: str, validate_on_commit: bool = True):
        """Async context manager for transactional state changes."""
        tx = self._Transaction(self, session_key, validate_on_commit)
        lock = self.get_lock(session_key)
        async with lock:
            yield tx

    # === Session Reset ===

    def reset_session(self, session_key: str) -> None:
        """Reset session to initial state."""
        state = self.get(session_key)
        if state:
            state.phase = SessionPhase.IDLE
            state.reason = ""
            state.previous_phase = None
            state.tasks.clear()

    # === Busy Check ===

    def is_busy(self, session_key: str) -> bool:
        """Check if session is busy (has active tasks or not IDLE)."""
        phase = self.get_phase(session_key)
        has_tasks = self.has_active_tasks(session_key)
        is_idle = phase == SessionPhase.IDLE
        return has_tasks or not is_idle

    # === Backend Metadata (model, skills, commands, etc.) ===

    def get_model(self, session_key: str) -> str | None:
        """Get model for session."""
        state = self.get(session_key)
        return state.model if state else None

    def set_model(self, session_key: str, model: str | None) -> None:
        """Set model for session."""
        state = self.get_or_create(session_key)
        state.model = model

    def get_commands(self, session_key: str) -> list[str]:
        """Get commands for session."""
        state = self.get(session_key)
        return list(state.commands) if state else []

    def set_commands(self, session_key: str, commands: list[str]) -> None:
        """Set commands for session."""
        state = self.get_or_create(session_key)
        state.commands = list(commands)

    def get_sdk_capabilities(self, session_key: str) -> dict[str, Any]:
        """Get SDK capability snapshot for session."""
        state = self.get(session_key)
        if not state:
            return {
                "skills": [],
                "tools": [],
                "slash_commands": [],
                "skill_source": "sdk_only",
            }
        return {
            "skills": list(state.sdk_skills),
            "tools": list(state.sdk_tools),
            "slash_commands": list(state.sdk_slash_commands),
            "skill_source": state.skill_source or "sdk_only",
        }

    def set_sdk_capabilities(
        self,
        session_key: str,
        *,
        skills: list[str] | None = None,
        tools: list[str] | None = None,
        slash_commands: list[str] | None = None,
        skill_source: str = "sdk_only",
    ) -> None:
        """Set SDK capability snapshot for session."""
        state = self.get_or_create(session_key)
        if skills is not None:
            state.sdk_skills = list(skills)
        if tools is not None:
            state.sdk_tools = list(tools)
        if slash_commands is not None:
            state.sdk_slash_commands = list(slash_commands)
        state.skill_source = skill_source

    def get_last_used(self, session_key: str) -> float | None:
        """Get last used timestamp for session."""
        state = self.get(session_key)
        return state.last_active if state else None

    def touch(self, session_key: str) -> None:
        """Update last used timestamp."""
        state = self.get(session_key)
        if state:
            state.last_active = time.time()

    def get_task_id(self, session_key: str) -> str | None:
        """Get task ID for session."""
        state = self.get(session_key)
        return state.task_id if state else None

    def set_task_id(self, session_key: str, task_id: str | None) -> None:
        """Set task ID for session."""
        state = self.get_or_create(session_key)
        state.task_id = task_id

    def get_request_id(self, session_key: str) -> str | None:
        """Get request ID for session."""
        state = self.get(session_key)
        return state.request_id if state else None

    def set_request_id(self, session_key: str, request_id: str | None) -> None:
        """Set request ID for session."""
        state = self.get_or_create(session_key)
        state.request_id = request_id

    # === Context Mapping (SDK session ID <-> session key) ===

    def resolve_sdk_session_id(self, session_key: str) -> str | None:
        """Resolve SDK session ID from session key."""
        state = self.get(session_key)
        return state.sdk_session_id if state else None

    def get_context_by_session_key(self, session_key: str) -> tuple[str, str] | None:
        """Get channel and chat_id by session key."""
        return self.get_routing(session_key)

    def get_context_by_sdk_id(self, sdk_session_id: str) -> tuple[str, str] | None:
        """Get channel and chat_id by SDK session ID."""
        state = self.get_by_sdk_id(sdk_session_id)
        if state:
            return (state.channel, state.chat_id)
        return None

    def resolve_compact_notification_target(self, session_ref: str) -> tuple[str, str, str] | None:
        """Resolve target for compact notification."""
        result = self.resolve_routing(session_ref)
        if result:
            session_key, channel, chat_id = result
            return (session_key, channel, chat_id)
        return None

    def set_context(self, session_key: str, channel: str, chat_id: str) -> None:
        """Set channel and chat_id for session."""
        self.set_routing(session_key, channel, chat_id)

    def set_sdk_context_mapping(self, sdk_session_id: str, channel: str, chat_id: str) -> None:
        """Set context mapping by SDK session ID."""
        state = self.get_by_sdk_id(sdk_session_id)
        if state:
            state.channel = channel
            state.chat_id = chat_id

    def list_context_keys(self) -> list[str]:
        """List all session keys with context."""
        return [k for k, v in self._sessions.items() if v.channel or v.chat_id]

    def get_client_last_used_map(self) -> dict[str, float]:
        """Get map of session key to last used time for sessions with clients."""
        return {
            k: v.last_active
            for k, v in self._sessions.items()
            if v.client is not None
        }

    def list_client_session_keys(self) -> list[str]:
        """List all session keys with clients."""
        return self.list_client_sessions()

    def clear_tracking_state(
        self,
        session_key: str,
        sdk_session_id: str | None = None,
        clear_sdk_session_id: bool = True,
        clear_context: bool = False,
    ) -> None:
        """Clear tracking state for session."""
        state = self.get(session_key)
        if state:
            state.client = None
            if clear_sdk_session_id:
                state.sdk_session_id = None
            if clear_context:
                state.channel = ""
                state.chat_id = ""
            state.tasks.clear()

    def detach_runtime_state(self, session_key: str, preserve_sdk_context: bool = False) -> dict[str, Any]:
        """Detach runtime state from session."""
        state = self.get(session_key)
        if not state:
            return {}

        client = state.client
        sdk_session_id = state.sdk_session_id if preserve_sdk_context else None
        channel = state.channel if preserve_sdk_context else ""
        chat_id = state.chat_id if preserve_sdk_context else ""
        model = state.model

        # Clear state
        state.client = None
        if not preserve_sdk_context:
            state.sdk_session_id = None
            self._sdk_index.pop(sdk_session_id, None)
            state.channel = ""
            state.chat_id = ""
        state.tasks.clear()
        state.task_id = None
        state.request_id = None

        return {
            "client": client,
            "sdk_session_id": sdk_session_id,
            "channel": channel,
            "chat_id": chat_id,
            "model": model,
        }

    def clear_all_contexts(self) -> None:
        """Clear all session contexts."""
        for state in self._sessions.values():
            state.channel = ""
            state.chat_id = ""

    def enforce_legacy_context_limit(self, limit: int) -> None:
        """Enforce a limit on the number of sessions stored (for legacy compatibility).

        This is a no-op in the new RuntimeSessionRegistry since we don't enforce limits here.
        The limit is managed by the backend's own scavenger process.
        """
        # No-op - the new RuntimeSessionRegistry doesn't enforce limits
        pass

    def clear_context(self, session_key: str) -> None:
        """Clear context for session."""
        state = self.get(session_key)
        if state:
            state.channel = ""
            state.chat_id = ""

    def get_stale_client_session_keys(self, ttl_seconds: float) -> list[str]:
        """Get stale session keys (clients that haven't been used recently)."""
        return self.list_stale_sessions(ttl_seconds)
