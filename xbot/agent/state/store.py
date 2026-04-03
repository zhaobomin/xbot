"""Unified Session Store for managing all session-related state.

This module consolidates session state that was previously scattered across
11 dicts in multiple components (Backend, Runtime, StateMachine, SessionManager).

Architecture:
- SessionEntry: Single dataclass holding all state for one session
- SessionStore: Unified container with atomic operations

Migration Path:
- Phase 1: Implement SessionStore (standalone, no integration)
- Phase 2: Create BackendV2 adapter using SessionStore
- Phase 3: Feature flag gradual replacement
- Phase 4: Runtime integration
- Phase 5: Remove old dicts

See SESSION_STORE_REFACTOR_PLAN.md for details.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from xbot.logging import get_logger

logger = get_logger(__name__)

from xbot.agent.state.machine import SessionPhase

if TYPE_CHECKING:
    from xbot.agent.backends.claude_sdk_backend import ClaudeSDKClient
    from xbot.agent.session_manager import Session


@dataclass
class SessionEntry:
    """Single session's complete state.

    This dataclass consolidates all session-related data that was previously
    stored across multiple dicts:
    - Backend: _clients, _client_last_used, _client_models, _active_task_ids,
               _active_request_ids, _session_commands, _client_skills_versions,
               _sdk_session_ids
    - shared_resources: _session_contexts
    - Runtime: _active_tasks, _session_locks
    - StateMachine: _states
    """

    # === Identity ===
    session_key: str
    sdk_session_id: str | None = None

    # === Channel context ===
    channel: str = ""
    chat_id: str = ""

    # === SDK connection ===
    client: "ClaudeSDKClient | None" = None
    model: str = ""
    task_id: str | None = None
    request_id: str | None = None

    # === Metadata ===
    last_used: float = field(default_factory=time.time)
    skills_version: str | None = None
    commands: list[str] = field(default_factory=list)

    # === Runtime state ===
    tasks: list[asyncio.Task] = field(default_factory=list)
    lock: asyncio.Lock | None = field(default_factory=asyncio.Lock)

    # === State machine ===
    phase: SessionPhase = SessionPhase.IDLE

    # === Persistent session reference (not owned) ===
    persistent_session: "Session | None" = None

    def touch(self) -> None:
        """Update last_used timestamp."""
        self.last_used = time.time()

    def is_connected(self) -> bool:
        """Check if SDK client is connected."""
        return self.client is not None

    def has_sdk_session(self) -> bool:
        """Check if SDK session ID is set."""
        return self.sdk_session_id is not None


class SessionStore:
    """Unified session state management.

    Provides atomic operations for session lifecycle:
    - Create/delete/clear sessions
    - SDK connection management
    - Task tracking and cancellation
    - State machine integration

    All operations are thread-safe via global lock.
    """

    def __init__(self):
        self._entries: dict[str, SessionEntry] = {}
        self._sdk_id_index: dict[str, str] = {}  # sdk_session_id -> session_key
        self._lock = asyncio.Lock()

    # === Query operations (no lock needed for reads) ===

    def get(self, session_key: str) -> SessionEntry | None:
        """Get session entry by session_key."""
        return self._entries.get(session_key)

    def get_by_sdk_id(self, sdk_session_id: str) -> SessionEntry | None:
        """Get session entry by SDK session ID."""
        session_key = self._sdk_id_index.get(sdk_session_id)
        if session_key:
            return self._entries.get(session_key)
        return None

    def list_keys(self) -> set[str]:
        """List all session keys."""
        return set(self._entries.keys())

    def list_sdk_ids(self) -> set[str]:
        """List all SDK session IDs."""
        return set(self._sdk_id_index.keys())

    def set_sdk_session_id(self, session_key: str, sdk_session_id: str | None) -> bool:
        """Set SDK session ID and maintain reverse index."""
        entry = self._entries.get(session_key)
        if not entry:
            return False

        old_sdk_id = entry.sdk_session_id
        if old_sdk_id and old_sdk_id != sdk_session_id:
            self._sdk_id_index.pop(old_sdk_id, None)

        entry.sdk_session_id = sdk_session_id
        if sdk_session_id:
            self._sdk_id_index[sdk_session_id] = session_key
        return True

    def clear_sdk_session_id(self, session_key: str) -> bool:
        """Clear SDK session ID and reverse index."""
        return self.set_sdk_session_id(session_key, None)

    def exists(self, session_key: str) -> bool:
        """Check if session exists."""
        return session_key in self._entries

    def count(self) -> int:
        """Count total sessions."""
        return len(self._entries)

    def get_or_create(self, session_key: str) -> SessionEntry:
        """Get existing entry or create a new one (sync version).

        This is a simplified synchronous version for coordinator usage.
        Creates entry with empty channel/chat_id if not present.

        Args:
            session_key: Unique session identifier

        Returns:
            SessionEntry (existing or newly created)
        """
        if session_key not in self._entries:
            entry = SessionEntry(session_key=session_key)
            self._entries[session_key] = entry
            logger.debug(f"SessionStore: auto-created {session_key}")
        return self._entries[session_key]

    # === Lifecycle operations ===

    async def create(
        self,
        session_key: str,
        channel: str,
        chat_id: str,
    ) -> SessionEntry:
        """Create a new session entry.

        Args:
            session_key: Unique session identifier (e.g., "telegram:123456")
            channel: Channel type (e.g., "telegram", "discord")
            chat_id: Chat/user ID

        Returns:
            Newly created SessionEntry

        Raises:
            ValueError: If session_key already exists
        """
        async with self._lock:
            if session_key in self._entries:
                raise ValueError(f"Session already exists: {session_key}")

            entry = SessionEntry(
                session_key=session_key,
                channel=channel,
                chat_id=chat_id,
            )
            self._entries[session_key] = entry

            logger.debug(f"SessionStore: created {session_key}")
            return entry

    async def delete(
        self,
        session_key: str,
        delete_sdk_file: bool = True,
    ) -> dict[str, Any]:
        """Delete a session and optionally its SDK file.

        Args:
            session_key: Session to delete
            delete_sdk_file: Whether to delete SDK session file

        Returns:
            Result dict with keys: deleted, sdk_session_id, error
        """
        async with self._lock:
            entry = self._entries.get(session_key)
            if not entry:
                return {
                    "deleted": False,
                    "sdk_session_id": None,
                    "error": "Session not found",
                }

            sdk_session_id = entry.sdk_session_id

            # Disconnect SDK client if connected
            if entry.client is not None:
                try:
                    await entry.client.disconnect()
                    logger.debug(f"SessionStore: disconnected client for {session_key}")
                except Exception as e:
                    logger.warning(f"SessionStore: disconnect error: {e}")

            # Cancel all active tasks
            cancelled_count = await self._cancel_tasks_internal(entry)

            # Remove from indexes
            self._entries.pop(session_key, None)
            if sdk_session_id:
                self._sdk_id_index.pop(sdk_session_id, None)

            logger.debug(
                f"SessionStore: deleted {session_key}, "
                f"cancelled {cancelled_count} tasks"
            )

            return {
                "deleted": True,
                "sdk_session_id": sdk_session_id,
                "cancelled_tasks": cancelled_count,
                "error": None,
            }

    async def clear(self, session_key: str) -> bool:
        """Clear session state but keep entry.

        Used for reset_session behavior - clears SDK connection and state
        but preserves the SessionEntry for reuse.

        Args:
            session_key: Session to clear

        Returns:
            True if session was cleared, False if not found
        """
        async with self._lock:
            entry = self._entries.get(session_key)
            if not entry:
                return False

            # Disconnect SDK client
            if entry.client is not None:
                try:
                    await entry.client.disconnect()
                except Exception as e:
                    logger.warning(f"SessionStore: disconnect error: {e}")

            # Clear SDK session ID
            old_sdk_id = entry.sdk_session_id
            if old_sdk_id:
                self._sdk_id_index.pop(old_sdk_id, None)

            # Reset all state fields (consistent with _remove_client_state)
            entry.client = None
            entry.sdk_session_id = None
            entry.model = ""
            entry.skills_version = None
            entry.commands = []
            entry.task_id = None
            entry.request_id = None
            entry.phase = SessionPhase.IDLE

            # Cancel tasks
            await self._cancel_tasks_internal(entry)

            logger.debug(f"SessionStore: cleared {session_key}")
            return True

    # === SDK connection ===

    async def connect_sdk(
        self,
        session_key: str,
        client: "ClaudeSDKClient",
        sdk_session_id: str,
        model: str = "",
    ) -> bool:
        """Connect SDK client to session.

        Args:
            session_key: Target session
            client: SDK client instance
            sdk_session_id: SDK session ID
            model: Model name being used

        Returns:
            True if connected, False if session not found
        """
        async with self._lock:
            entry = self._entries.get(session_key)
            if not entry:
                return False

            # Clear old SDK ID index if exists
            old_sdk_id = entry.sdk_session_id
            if old_sdk_id and old_sdk_id != sdk_session_id:
                self._sdk_id_index.pop(old_sdk_id, None)

            # Store SDK connection
            entry.client = client
            entry.sdk_session_id = sdk_session_id
            entry.model = model
            entry.touch()

            # Create reverse index
            self._sdk_id_index[sdk_session_id] = session_key

            logger.debug(
                f"SessionStore: connected SDK {sdk_session_id} to {session_key}"
            )
            return True

    async def disconnect_sdk(self, session_key: str) -> bool:
        """Disconnect SDK client from session.

        Args:
            session_key: Session to disconnect

        Returns:
            True if disconnected, False if not found or not connected
        """
        async with self._lock:
            entry = self._entries.get(session_key)
            if not entry or entry.client is None:
                return False

            # Disconnect client
            try:
                await entry.client.disconnect()
            except Exception as e:
                logger.warning(f"SessionStore: disconnect error: {e}")

            # Clear SDK references
            old_sdk_id = entry.sdk_session_id
            if old_sdk_id:
                self._sdk_id_index.pop(old_sdk_id, None)

            entry.client = None
            entry.sdk_session_id = None

            logger.debug(f"SessionStore: disconnected SDK from {session_key}")
            return True

    # === Task management ===

    def register_task(self, session_key: str, task: asyncio.Task) -> bool:
        """Register an asyncio.Task for tracking.

        Args:
            session_key: Session to associate task with
            task: asyncio.Task to track

        Returns:
            True if registered, False if session not found
        """
        entry = self._entries.get(session_key)
        if not entry:
            return False

        setattr(task, "_xbot_session_key", session_key)
        entry.tasks.append(task)
        logger.debug(f"SessionStore: registered task for {session_key}")
        return True

    def unregister_task(self, session_key: str, task: asyncio.Task) -> bool:
        """Unregister a task.

        Args:
            session_key: Session the task belongs to
            task: Task to unregister

        Returns:
            True if unregistered, False if not found
        """
        entry = self._entries.get(session_key)
        if not entry:
            return False

        try:
            entry.tasks.remove(task)
            logger.debug(f"SessionStore: unregistered task for {session_key}")
            return True
        except ValueError:
            return False

    def get_active_tasks(self, session_key: str) -> list[asyncio.Task]:
        """Get active tasks for a session."""
        entry = self._entries.get(session_key)
        if not entry:
            return []
        return [t for t in entry.tasks if not t.done()]

    def get_lock(self, session_key: str) -> asyncio.Lock | None:
        """Get the current lock object for a session."""
        entry = self._entries.get(session_key)
        if not entry:
            return None
        return entry.lock

    def get_or_create_lock(self, session_key: str) -> asyncio.Lock:
        """Get or create the lock object for a session."""
        entry = self.get_or_create(session_key)
        if entry.lock is None:
            entry.lock = asyncio.Lock()
        return entry.lock

    def release_lock(self, session_key: str) -> bool:
        """Release lock ownership for a session while keeping the entry.

        Does NOT forcibly release a locked lock — that would break mutual
        exclusion for the current holder.  Instead it only nullifies the
        lock reference when no one is waiting, allowing GC to reclaim it.
        """
        entry = self._entries.get(session_key)
        if not entry or entry.lock is None:
            return False
        lock = entry.lock
        had_waiters = bool(getattr(lock, "_waiters", None))
        if not lock.locked() and not had_waiters:
            entry.lock = None
        return True

    async def cancel_all_tasks(self, session_key: str) -> int:
        """Cancel all active tasks for a session.

        Args:
            session_key: Session to cancel tasks for

        Returns:
            Number of tasks cancelled
        """
        async with self._lock:
            entry = self._entries.get(session_key)
            if not entry:
                return 0
            return await self._cancel_tasks_internal(entry)

    async def _cancel_tasks_internal(self, entry: SessionEntry) -> int:
        """Internal task cancellation (must be called with lock held)."""
        cancelled = 0
        for task in entry.tasks:
            if not task.done():
                task.cancel()
                cancelled += 1
        entry.tasks.clear()
        return cancelled

    # === State machine ===

    def get_phase(self, session_key: str) -> SessionPhase | None:
        """Get current phase for session."""
        entry = self._entries.get(session_key)
        if not entry:
            return None
        return entry.phase

    def set_phase(
        self,
        session_key: str,
        phase: SessionPhase,
        reason: str = "",
    ) -> bool:
        """Set session phase.

        Note: Transition validation is done by StateMachine, not here.
        This method only updates the phase field.

        Args:
            session_key: Session to update
            phase: New phase
            reason: Optional reason for transition

        Returns:
            True if updated, False if not found
        """
        entry = self._entries.get(session_key)
        if not entry:
            return False

        old_phase = entry.phase
        entry.phase = phase
        logger.debug(
            f"SessionStore: {session_key} phase {old_phase} -> {phase} ({reason})"
        )
        return True

    # === Cleanup ===

    async def cleanup_expired(self, ttl_seconds: float) -> int:
        """Clean up expired sessions.

        Sessions with last_used older than ttl_seconds are deleted.

        Args:
            ttl_seconds: TTL threshold

        Returns:
            Number of sessions cleaned
        """
        async with self._lock:
            now = time.time()
            expired_keys = []

            for key, entry in self._entries.items():
                if entry.last_used < now - ttl_seconds:
                    expired_keys.append(key)

            for key in expired_keys:
                entry = self._entries.pop(key, None)
                if entry:
                    if entry.sdk_session_id:
                        self._sdk_id_index.pop(entry.sdk_session_id, None)
                    if entry.client:
                        try:
                            await entry.client.disconnect()
                        except Exception:
                            pass
                    await self._cancel_tasks_internal(entry)

            if expired_keys:
                logger.info(f"SessionStore: cleaned {len(expired_keys)} expired sessions")

            return len(expired_keys)

    # === Utility ===

    def get_context(self, session_key: str) -> tuple[str, str] | None:
        """Get channel/chat_id context for session."""
        entry = self._entries.get(session_key)
        if not entry:
            return None
        return (entry.channel, entry.chat_id)

    def get_model(self, session_key: str) -> str | None:
        """Get model name for session."""
        entry = self._entries.get(session_key)
        if not entry:
            return None
        return entry.model

    def has_lock(self, session_key: str) -> bool:
        """Check if session currently has an attached lock object."""
        entry = self._entries.get(session_key)
        return entry is not None and entry.lock is not None
