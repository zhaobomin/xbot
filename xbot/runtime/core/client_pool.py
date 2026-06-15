"""Simplified client pool for single-user scenarios.

This module provides a simplified client connection manager
without TTL/Scavenger/LRU complexity.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from xbot.platform.logging.core import get_logger

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

logger = get_logger(__name__)


@dataclass
class ClientRecord:
    """Record for tracking a connected client."""

    session_key: str
    client: ClaudeSDKClient
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    state: str = "connected"


class ClientPool:
    """Simplified client pool for single-user scenarios.

    Unlike the original ClientLifecycleManager, this class:
    - Removes TTL-based cleanup (not needed for single user)
    - Removes Scavenger process (no background cleanup needed)
    - Removes LRU eviction (single user won't hit capacity limits)
    - Keeps basic lifecycle tracking for observability

    Use this when you don't need multi-tenant client management.
    """

    def __init__(self) -> None:
        """Initialize the client pool."""
        self._clients: dict[str, ClientRecord] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        session_key: str,
        options: Any | None = None,
    ) -> ClaudeSDKClient:
        """Get an existing client or create a new one.

        Args:
            session_key: Session identifier
            options: Optional ClaudeAgentOptions for client creation

        Returns:
            ClaudeSDKClient instance
        """
        async with self._lock:
            record = self._clients.get(session_key)
            if record is not None and record.state == "connected":
                if await self._is_client_healthy(record.client, session_key):
                    record.last_used_at = time.time()
                    return record.client

                logger.warning("Recycling unhealthy SDK client for session %s", session_key)
                try:
                    await asyncio.wait_for(record.client.disconnect(), timeout=3.0)
                except Exception as e:
                    logger.debug("Best-effort disconnect failed for unhealthy client %s: %s", session_key, e)
                    await self._best_effort_force_disconnect(record.client, session_key)
                record.state = "disconnected"
                del self._clients[session_key]

            # Create new client
            from claude_agent_sdk import ClaudeSDKClient

            if options is None:
                raise ValueError("Options required to create client")

            client = ClaudeSDKClient(options)

            # Connect the client (required before use)
            try:
                await asyncio.wait_for(client.connect(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.error(f"SDK client connect timed out after 120s for session {session_key}")
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=5.0)
                except Exception:
                    await self._best_effort_force_disconnect(client, session_key)
                raise RuntimeError(f"SDK client connect timed out after 120s for session {session_key}")

            self._clients[session_key] = ClientRecord(
                session_key=session_key,
                client=client,
            )
            logger.info(f"Created and connected client for session {session_key}")
            return client

    async def _is_client_healthy(self, client: Any, session_key: str) -> bool:
        """Perform a lightweight liveness check before reusing a connected client."""
        get_info = getattr(client, "get_server_info", None)
        if not callable(get_info):
            return True
        try:
            result = get_info()
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, timeout=2.0)
            return True
        except Exception as e:
            logger.warning("SDK client health check failed for %s: %s", session_key, e)
            return False

    async def disconnect(self, session_key: str) -> bool:
        """Disconnect a client.

        Args:
            session_key: Session identifier

        Returns:
            True if disconnected, False if not found
        """
        async with self._lock:
            record = self._clients.get(session_key)
            if record is None:
                return True

            try:
                await asyncio.wait_for(record.client.disconnect(), timeout=10.0)
                record.state = "disconnected"
                del self._clients[session_key]
                logger.info(f"Disconnected client for session {session_key}")
                return True
            except Exception as e:
                logger.warning(f"Failed to disconnect client for {session_key}: {e}")
                await self._best_effort_force_disconnect(record.client, session_key)
                del self._clients[session_key]
                return True

    async def _best_effort_force_disconnect(self, client: Any, session_key: str) -> None:
        """Best-effort fallback when graceful disconnect fails."""
        async def _maybe_call(target: Any) -> bool:
            if not callable(target):
                return False
            try:
                result = target()
                if inspect.isawaitable(result):
                    await result
                return True
            except Exception as e:
                logger.debug("Force-disconnect call failed for %s: %s", session_key, e)
                return False

        # Try common client-level shutdown methods first.
        for name in ("terminate", "kill", "close"):
            if await _maybe_call(getattr(client, name, None)):
                logger.warning("Force-disconnect fallback used: %s.%s()", session_key, name)
                return

        # Then try nested process handles if SDK exposes them.
        proc = getattr(client, "_process", None) or getattr(client, "process", None)
        if proc is not None:
            for name in ("terminate", "kill", "close"):
                if await _maybe_call(getattr(proc, name, None)):
                    logger.warning("Force-disconnect fallback used: %s.process.%s()", session_key, name)
                    return

    async def prune_idle(self, idle_ttl_seconds: float, *, exclude_keys: set[str] | None = None) -> int:
        """Disconnect clients idle for longer than idle_ttl_seconds."""
        if idle_ttl_seconds <= 0:
            return 0
        excluded = exclude_keys or set()
        now = time.time()
        async with self._lock:
            stale_keys = [
                key
                for key, record in self._clients.items()
                if key not in excluded
                and record.state == "connected"
                and (now - record.last_used_at) > idle_ttl_seconds
            ]
        removed = 0
        for key in stale_keys:
            if await self.disconnect(key):
                removed += 1
        if removed:
            logger.info("Pruned %s idle Claude clients (ttl=%ss)", removed, idle_ttl_seconds)
        return removed

    async def disconnect_all(self) -> int:
        """Disconnect all clients.

        Returns:
            Number of clients disconnected
        """
        async with self._lock:
            keys = list(self._clients.keys())
        count = 0
        for key in keys:
            if await self.disconnect(key):
                count += 1
        return count

    async def get_record(self, session_key: str) -> ClientRecord | None:
        """Return the client record for a session under the pool lock."""
        async with self._lock:
            return self._clients.get(session_key)

    def snapshot(self) -> dict[str, Any]:
        """Get current pool state for observability.

        Returns:
            Dict with counts and client details
        """
        counts = {"connected": 0, "disconnected": 0, "error": 0}
        clients: dict[str, Any] = {}

        for key, record in self._clients.items():
            counts[record.state] = counts.get(record.state, 0) + 1
            clients[key] = {
                "state": record.state,
                "created_at": record.created_at,
                "last_used_at": record.last_used_at,
            }

        return {"counts": counts, "clients": clients}

    def has_client(self, session_key: str) -> bool:
        """Check if a session has an active client."""
        record = self._clients.get(session_key)
        return record is not None and record.state == "connected"

    def list_clients(self) -> list[str]:
        """List all session keys with active clients."""
        return [
            key for key, record in self._clients.items()
            if record.state == "connected"
        ]
