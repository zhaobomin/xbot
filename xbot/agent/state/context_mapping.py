"""Session Context Manager for unified session-to-context mapping.

This module provides a centralized manager for mapping session identifiers
to their execution context (channel, chat_id). It handles both xbot's
session_key and SDK's sdk_session_id, ensuring bidirectional consistency.

Key Features:
- Unified mapping management for session_key and sdk_session_id
- Automatic cleanup of stale mappings
- Thread-safe operations
- Diagnostic logging for troubleshooting
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any

from xbot.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SessionContext:
    """Context information for a session."""

    channel: str
    chat_id: str

    def to_tuple(self) -> tuple[str, str]:
        """Convert to tuple for compatibility with existing code."""
        return (self.channel, self.chat_id)


class SessionContextManager:
    """Manages session-to-context mappings with bidirectional consistency.

    This class centralizes the management of session context mappings,
    ensuring that both session_key and sdk_session_id point to the same
    context, and that cleanup is performed consistently.

    Usage:
        manager = SessionContextManager()

        # Set mapping
        manager.set_context("telegram:12345", "sdk-uuid-abc", SessionContext("telegram", "12345"))

        # Get by session_key
        ctx = manager.get_by_session_key("telegram:12345")

        # Get by sdk_session_id
        ctx = manager.get_by_sdk_session_id("sdk-uuid-abc")

        # Clear mapping
        manager.clear("telegram:12345")
    """

    MAX_SESSIONS = 500  # Prevent unbounded growth

    def __init__(self):
        """Initialize the context manager."""
        self._lock = threading.RLock()
        # session_key -> SessionContext
        self._session_key_to_context: dict[str, SessionContext] = {}
        # sdk_session_id -> SessionContext
        self._sdk_session_id_to_context: dict[str, SessionContext] = {}
        # session_key -> sdk_session_id (for reverse lookup during cleanup)
        self._session_key_to_sdk_id: dict[str, str] = {}
        # sdk_session_id -> session_key (for reverse lookup during cleanup)
        self._sdk_id_to_session_key: dict[str, str] = {}

    def set_context(
        self,
        session_key: str,
        sdk_session_id: str | None,
        context: SessionContext,
    ) -> None:
        """Set context mapping for a session.

        Args:
            session_key: The xbot session identifier (e.g., "telegram:12345")
            sdk_session_id: The SDK session identifier (may be None initially)
            context: The session context containing channel and chat_id
        """
        with self._lock:
            # Clear any existing mappings for this session_key
            self._clear_session_key_mappings(session_key)

            # Set new mappings
            self._session_key_to_context[session_key] = context
            if sdk_session_id:
                self._sdk_session_id_to_context[sdk_session_id] = context
                self._session_key_to_sdk_id[session_key] = sdk_session_id
                self._sdk_id_to_session_key[sdk_session_id] = session_key

            logger.debug(
                f"[SessionContextManager] Set mapping: session_key={session_key}, "
                f"sdk_sid={sdk_session_id or 'none'}, context=({context.channel}, {context.chat_id})"
            )

            # Enforce size limit
            self._enforce_size_limit()

    def update_sdk_session_id(
        self,
        session_key: str,
        sdk_session_id: str,
    ) -> None:
        """Update SDK session ID for an existing session.

        This is called when SDK generates a new session_id during a turn.

        Args:
            session_key: The xbot session identifier
            sdk_session_id: The new SDK session identifier
        """
        with self._lock:
            context = self._session_key_to_context.get(session_key)
            if context is None:
                logger.warning(
                    f"[SessionContextManager] Cannot update sdk_sid: "
                    f"session_key={session_key} not found"
                )
                return

            # Clear old sdk_session_id mapping if exists
            old_sdk_id = self._session_key_to_sdk_id.get(session_key)
            if old_sdk_id:
                self._sdk_session_id_to_context.pop(old_sdk_id, None)
                self._sdk_id_to_session_key.pop(old_sdk_id, None)

            # Set new mapping
            self._sdk_session_id_to_context[sdk_session_id] = context
            self._session_key_to_sdk_id[session_key] = sdk_session_id
            self._sdk_id_to_session_key[sdk_session_id] = session_key

            logger.debug(
                f"[SessionContextManager] Updated sdk_sid: session_key={session_key}, "
                f"old_sdk_sid={old_sdk_id or 'none'}, new_sdk_sid={sdk_session_id}"
            )

    def get_by_session_key(self, session_key: str) -> SessionContext | None:
        """Get context by session_key.

        Args:
            session_key: The xbot session identifier

        Returns:
            SessionContext if found, None otherwise
        """
        with self._lock:
            return self._session_key_to_context.get(session_key)

    def get_by_sdk_session_id(self, sdk_session_id: str) -> SessionContext | None:
        """Get context by SDK session ID.

        Args:
            sdk_session_id: The SDK session identifier

        Returns:
            SessionContext if found, None otherwise
        """
        with self._lock:
            return self._sdk_session_id_to_context.get(sdk_session_id)

    def get_context(self, identifier: str) -> SessionContext | None:
        """Get context by either session_key or sdk_session_id.

        Args:
            identifier: Either session_key or sdk_session_id

        Returns:
            SessionContext if found, None otherwise
        """
        # Try session_key first
        with self._lock:
            ctx = self._session_key_to_context.get(identifier)
            if ctx:
                return ctx
            # Fall back to sdk_session_id
            return self._sdk_session_id_to_context.get(identifier)

    def get_session_key_by_sdk_id(self, sdk_session_id: str) -> str | None:
        """Get session_key by SDK session ID.

        Args:
            sdk_session_id: The SDK session identifier

        Returns:
            session_key if found, None otherwise
        """
        with self._lock:
            return self._sdk_id_to_session_key.get(sdk_session_id)

    def clear(self, session_key: str) -> bool:
        """Clear all mappings for a session.

        Args:
            session_key: The xbot session identifier

        Returns:
            True if any mappings were cleared, False otherwise
        """
        with self._lock:
            had_mappings = session_key in self._session_key_to_context

            # Clear session_key mapping
            self._session_key_to_context.pop(session_key, None)

            # Clear associated sdk_session_id mappings
            sdk_id = self._session_key_to_sdk_id.pop(session_key, None)
            if sdk_id:
                self._sdk_session_id_to_context.pop(sdk_id, None)
                self._sdk_id_to_session_key.pop(sdk_id, None)

            if had_mappings:
                logger.debug(
                    f"[SessionContextManager] Cleared mapping: session_key={session_key}, "
                    f"sdk_sid={sdk_id or 'none'}"
                )

            return had_mappings

    def clear_by_sdk_session_id(self, sdk_session_id: str) -> bool:
        """Clear all mappings by SDK session ID.

        Args:
            sdk_session_id: The SDK session identifier

        Returns:
            True if any mappings were cleared, False otherwise
        """
        with self._lock:
            session_key = self._sdk_id_to_session_key.get(sdk_session_id)
            if session_key:
                return self.clear(session_key)

            # Just clear the sdk_session_id mapping if no session_key found
            had_mapping = sdk_session_id in self._sdk_session_id_to_context
            self._sdk_session_id_to_context.pop(sdk_session_id, None)
            self._sdk_id_to_session_key.pop(sdk_session_id, None)

            if had_mapping:
                logger.debug(
                    f"[SessionContextManager] Cleared mapping by sdk_sid: {sdk_session_id}"
                )

            return had_mapping

    def _clear_session_key_mappings(self, session_key: str) -> None:
        """Clear existing mappings for a session_key (internal use)."""
        # Clear old sdk_session_id if exists
        old_sdk_id = self._session_key_to_sdk_id.get(session_key)
        if old_sdk_id:
            self._sdk_session_id_to_context.pop(old_sdk_id, None)
            self._sdk_id_to_session_key.pop(old_sdk_id, None)

        self._session_key_to_context.pop(session_key, None)
        self._session_key_to_sdk_id.pop(session_key, None)

    def _enforce_size_limit(self) -> None:
        """Enforce maximum session count by removing oldest entries."""
        total = len(self._session_key_to_context)
        if total <= self.MAX_SESSIONS:
            return

        # Remove oldest entries (dict preserves insertion order in Python 3.7+)
        excess = total - self.MAX_SESSIONS
        keys_to_remove = list(self._session_key_to_context.keys())[:excess]

        for key in keys_to_remove:
            self.clear(key)

        logger.warning(
            f"[SessionContextManager] Removed {excess} sessions to enforce size limit"
        )

    def list_session_keys(self) -> list[str]:
        """List all session keys."""
        with self._lock:
            return list(self._session_key_to_context.keys())

    def list_sdk_session_ids(self) -> list[str]:
        """List all SDK session IDs."""
        with self._lock:
            return list(self._sdk_session_id_to_context.keys())

    def size(self) -> int:
        """Get the number of sessions."""
        with self._lock:
            return len(self._session_key_to_context)

    def __len__(self) -> int:
        """Get the number of sessions."""
        return self.size()

    def __contains__(self, session_key: str) -> bool:
        """Check if a session exists."""
        with self._lock:
            return session_key in self._session_key_to_context


# Convenience functions for backward compatibility with dict-like access
def create_context_dict() -> dict[str, tuple[str, str]]:
    """Create a dict compatible with legacy _session_contexts usage.

    Note: This returns a regular dict for backward compatibility.
    For new code, use SessionContextManager directly.
    """
    return {}
