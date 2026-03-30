"""Managed Claude client lifecycle tracking."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ManagedClientRecord:
    session_key: str
    client: Any | None = None
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    last_disconnect_attempt_at: float | None = None
    disconnect_state: str = "connected"
    sdk_session_id: str | None = None
    pid: int | None = None
    process_handle: Any | None = None
    process_tracking_available: bool = False
    is_ephemeral: bool = False
    disconnect_failures: int = 0
    force_kill_attempts: int = 0


class ClientLifecycleManager:
    """Track managed client state independently from the backend cache."""

    def __init__(self) -> None:
        self._records: dict[str, ManagedClientRecord] = {}
        self._lock = asyncio.Lock()
        self.disconnect_failures_total = 0
        self.force_kill_total = 0
        self._snapshot_cache: dict[str, Any] = self._snapshot_unlocked()

    async def register(
        self,
        session_key: str,
        client: Any,
        *,
        sdk_session_id: str | None = None,
        pid: int | None = None,
        process_handle: Any | None = None,
        process_tracking_available: bool = False,
        is_ephemeral: bool = False,
    ) -> ManagedClientRecord:
        async with self._lock:
            existing = self._records.get(session_key)
            if existing is None:
                existing = ManagedClientRecord(session_key=session_key)
                self._records[session_key] = existing
            existing.client = client
            existing.last_used_at = time.time()
            existing.disconnect_state = "connected"
            existing.sdk_session_id = sdk_session_id
            existing.pid = pid
            existing.process_handle = process_handle
            existing.process_tracking_available = process_tracking_available
            existing.is_ephemeral = is_ephemeral
            self._snapshot_cache = self._snapshot_unlocked()
            return existing

    async def touch(self, session_key: str) -> ManagedClientRecord | None:
        async with self._lock:
            record = self._records.get(session_key)
            if record is not None:
                record.last_used_at = time.time()
                self._snapshot_cache = self._snapshot_unlocked()
            return record

    async def update_sdk_session_id(self, session_key: str, sdk_session_id: str | None) -> None:
        async with self._lock:
            record = self._records.get(session_key)
            if record is not None:
                record.sdk_session_id = sdk_session_id
                self._snapshot_cache = self._snapshot_unlocked()

    async def begin_disconnect(self, session_key: str) -> ManagedClientRecord | None:
        async with self._lock:
            record = self._records.get(session_key)
            if record is None:
                return None
            if record.disconnect_state in {"disconnecting", "disconnected", "killed"}:
                return None
            record.disconnect_state = "disconnecting"
            record.last_disconnect_attempt_at = time.time()
            self._snapshot_cache = self._snapshot_unlocked()
            return record

    async def mark_disconnected(self, session_key: str) -> ManagedClientRecord | None:
        async with self._lock:
            record = self._records.get(session_key)
            if record is None:
                return None
            record.disconnect_state = "disconnected"
            record.client = None
            record.process_handle = None
            self._snapshot_cache = self._snapshot_unlocked()
            return record

    async def mark_disconnected_if_current(
        self,
        session_key: str,
        client: Any,
    ) -> ManagedClientRecord | None:
        async with self._lock:
            record = self._records.get(session_key)
            if record is None or record.client is not client:
                return None
            record.disconnect_state = "disconnected"
            record.client = None
            record.process_handle = None
            self._snapshot_cache = self._snapshot_unlocked()
            return record

    async def mark_leaked(self, session_key: str) -> ManagedClientRecord | None:
        async with self._lock:
            record = self._records.get(session_key)
            if record is None:
                return None
            record.disconnect_state = "leaked"
            record.disconnect_failures += 1
            self.disconnect_failures_total += 1
            self._snapshot_cache = self._snapshot_unlocked()
            return record

    async def mark_leaked_if_current(
        self,
        session_key: str,
        client: Any,
    ) -> ManagedClientRecord | None:
        async with self._lock:
            record = self._records.get(session_key)
            if record is None or record.client is not client:
                return None
            record.disconnect_state = "leaked"
            record.disconnect_failures += 1
            self.disconnect_failures_total += 1
            self._snapshot_cache = self._snapshot_unlocked()
            return record

    async def mark_killed(self, session_key: str) -> ManagedClientRecord | None:
        async with self._lock:
            record = self._records.get(session_key)
            if record is None:
                return None
            record.disconnect_state = "killed"
            record.client = None
            record.process_handle = None
            record.force_kill_attempts += 1
            self.force_kill_total += 1
            self._snapshot_cache = self._snapshot_unlocked()
            return record

    async def remove(self, session_key: str) -> ManagedClientRecord | None:
        async with self._lock:
            removed = self._records.pop(session_key, None)
            self._snapshot_cache = self._snapshot_unlocked()
            return removed

    async def get(self, session_key: str) -> ManagedClientRecord | None:
        async with self._lock:
            return self._records.get(session_key)

    async def records(self) -> dict[str, ManagedClientRecord]:
        async with self._lock:
            return dict(self._records)

    async def list_idle_candidates(
        self,
        *,
        idle_ttl_seconds: float,
        can_cleanup,
    ) -> list[str]:
        now = time.time()
        async with self._lock:
            return [
                session_key
                for session_key, record in self._records.items()
                if record.disconnect_state == "connected"
                and now - record.last_used_at > idle_ttl_seconds
                and can_cleanup(session_key)
            ]

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            self._snapshot_cache = self._snapshot_unlocked()
            return self._snapshot_cache.copy()

    def snapshot_sync(self) -> dict[str, Any]:
        if self._lock.locked():
            return self._snapshot_cache.copy()

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            async def _collect() -> dict[str, Any]:
                async with self._lock:
                    self._snapshot_cache = self._snapshot_unlocked()
                    return self._snapshot_cache.copy()

            return asyncio.run(_collect())

        return self._snapshot_cache.copy()

    def _snapshot_unlocked(self) -> dict[str, Any]:
        counts = {
            "connected": 0,
            "idle": 0,
            "disconnecting": 0,
            "disconnected": 0,
            "leaked": 0,
            "killed": 0,
        }
        clients: dict[str, Any] = {}
        now = time.time()
        for session_key, record in self._records.items():
            counts[record.disconnect_state] = counts.get(record.disconnect_state, 0) + 1
            if record.disconnect_state == "connected" and now - record.last_used_at >= 1.0:
                counts["idle"] += 1
            clients[session_key] = {
                "last_used_at": record.last_used_at,
                "disconnect_state": record.disconnect_state,
                "sdk_session_id": record.sdk_session_id,
                "pid": record.pid,
                "process_tracking_available": record.process_tracking_available,
                "is_ephemeral": record.is_ephemeral,
                "disconnect_failures": record.disconnect_failures,
                "force_kill_attempts": record.force_kill_attempts,
            }
        return {
            "counts": counts,
            "clients": clients,
            "disconnect_failures_total": self.disconnect_failures_total,
            "force_kill_total": self.force_kill_total,
        }
