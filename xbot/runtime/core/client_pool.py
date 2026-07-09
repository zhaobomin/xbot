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

    def __init__(self, max_clients: int | None = None) -> None:
        """Initialize the client pool."""
        self._clients: dict[str, ClientRecord] = {}
        self._lock = asyncio.Lock()
        self._max_clients = max_clients if max_clients and max_clients > 0 else None

    def set_max_clients(self, max_clients: int | None) -> None:
        """Update capacity limit for future client creation."""
        self._max_clients = max_clients if max_clients and max_clients > 0 else None

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
        while True:
            async with self._lock:
                record = self._clients.get(session_key)

            if record is not None and record.state == "connected":
                if await self._is_client_healthy(record.client, session_key):
                    async with self._lock:
                        current = self._clients.get(session_key)
                        if current is record and current.state == "connected":
                            current.last_used_at = time.time()
                            return current.client
                    continue

                logger.warning("Recycling unhealthy SDK client for session %s", session_key)
                # Mark disconnecting and remove from the pool BEFORE the await
                # so a concurrent caller cannot observe this record in the
                # "connected" state and reuse a client that is being torn down.
                # Mirrors the capacity-eviction path in _pop_oldest_capacity_record_unlocked.
                async with self._lock:
                    if not (
                        self._clients.get(session_key) is record
                        and record.state == "connected"
                    ):
                        # Another caller already recycled/replaced this record.
                        continue
                    record.state = "disconnecting"
                    self._clients.pop(session_key, None)
                await self._disconnect_record(record, timeout=3.0)
                continue

            from claude_agent_sdk import ClaudeSDKClient

            if options is None:
                raise ValueError("Options required to create client")

            await self._evict_for_capacity(session_key)

            client = ClaudeSDKClient(options)

            try:
                await asyncio.wait_for(client.connect(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.error(f"SDK client connect timed out after 120s for session {session_key}")
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=5.0)
                except Exception:
                    await self._best_effort_force_disconnect(client, session_key)
                raise RuntimeError(f"SDK client connect timed out after 120s for session {session_key}")

            while True:
                async with self._lock:
                    existing = self._clients.get(session_key)
                    if existing is not None and existing.state == "connected":
                        existing.last_used_at = time.time()
                        existing_client = existing.client
                    else:
                        evicted = self._pop_oldest_capacity_record_unlocked(exclude_keys={session_key})
                        if evicted is None:
                            self._clients[session_key] = ClientRecord(
                                session_key=session_key,
                                client=client,
                            )
                            logger.info(f"Created and connected client for session {session_key}")
                            return client
                        existing_client = None

                if existing_client is not None:
                    await self._disconnect_client(client, session_key, timeout=5.0)
                    return existing_client
                await self._disconnect_record(evicted, timeout=10.0)

    def _pop_oldest_capacity_record_unlocked(
        self,
        *,
        exclude_keys: set[str] | None = None,
    ) -> ClientRecord | None:
        if self._max_clients is None:
            return None
        excluded = exclude_keys or set()
        connected = [
            record for record in self._clients.values()
            if record.state == "connected" and record.session_key not in excluded
        ]
        if len(connected) < self._max_clients:
            return None

        oldest = min(connected, key=lambda record: record.last_used_at)
        logger.info(
            "Evicting oldest SDK client %s to enforce max_clients=%s",
            oldest.session_key,
            self._max_clients,
        )
        oldest.state = "disconnecting"
        self._clients.pop(oldest.session_key, None)
        return oldest

    async def _evict_for_capacity(self, session_key: str) -> None:
        """Evict the oldest connected client before creating a new one if at capacity."""
        async with self._lock:
            evicted = self._pop_oldest_capacity_record_unlocked(exclude_keys={session_key})
        if evicted is not None:
            await self._disconnect_record(evicted, timeout=10.0)

    async def _disconnect_record(self, record: ClientRecord, *, timeout: float) -> None:
        try:
            await asyncio.wait_for(record.client.disconnect(), timeout=timeout)
        except Exception as e:
            logger.warning("Failed to disconnect client for %s: %s", record.session_key, e)
            await self._best_effort_force_disconnect(record.client, record.session_key)
        record.state = "disconnected"

    async def _disconnect_client(self, client: Any, session_key: str, *, timeout: float) -> None:
        try:
            await asyncio.wait_for(client.disconnect(), timeout=timeout)
        except Exception as e:
            logger.warning("Failed to disconnect unused client for %s: %s", session_key, e)
            await self._best_effort_force_disconnect(client, session_key)

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
            record = self._clients.pop(session_key, None)
            if record is None:
                return True

        # Network I/O moved outside the lock to avoid blocking other operations
        try:
            await asyncio.wait_for(record.client.disconnect(), timeout=10.0)
            record.state = "disconnected"
            logger.info(f"Disconnected client for session {session_key}")
            return True
        except Exception as e:
            logger.warning(f"Failed to disconnect client for {session_key}: {e}")
            await self._best_effort_force_disconnect(record.client, session_key)
            record.state = "disconnected"
            return False

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
