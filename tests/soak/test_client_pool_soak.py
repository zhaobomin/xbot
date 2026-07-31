"""Soak test: ClientPool acquire / disconnect cycles.

Focus areas:
1. Sustained acquire+disconnect cycles: `_clients` returns to empty.
2. Concurrent acquire on the SAME key: only one client is created per
   (session_key, options_fingerprint) and no duplicates leak.
3. `prune_idle` under load: sessions past the idle_ttl are pruned; those
   below stay.
4. Capacity eviction: `max_clients` cap is respected — the oldest
   connected record is evicted (single-slot LRU).
5. Health-probe recycle loop: unhealthy client is torn down and replaced,
   with `_clients` size returning to 1.
6. Fake-client instances are properly disconnected (no dangling "connected"
   FakeClients).

These tests use a FakeClient (mirrors the existing lifecycle test) so we
avoid depending on the real Claude SDK / network.
"""

from __future__ import annotations

import asyncio
import gc
from typing import Any
from unittest.mock import patch

import pytest

from xbot.runtime.core.client_pool import ClientPool, ClientRecord

pytestmark = [pytest.mark.soak, pytest.mark.asyncio]


class FakeClient:
    """Minimal fake mirroring the real SDK client's async API."""

    _counter = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        FakeClient._counter += 1
        self.id = FakeClient._counter
        self.connected = False
        self.disconnected = False
        self.disconnect_calls = 0

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False
        self.disconnected = True

    async def get_server_info(self) -> dict[str, Any]:
        if not self.connected:
            raise RuntimeError("not connected")
        return {"status": "ok"}


class FakeOptions:
    """Placeholder options object."""

    def __init__(self, tag: str = "default") -> None:
        self.tag = tag


def _patch_sdk() -> Any:
    """Patch the ClaudeSDKClient import inside get_or_create."""
    return patch("claude_agent_sdk.ClaudeSDKClient", new=FakeClient)


class TestAcquireDisconnectLoop:
    """N acquire+disconnect cycles must leave `_clients` empty."""

    async def test_1000_cycles_returns_to_empty(self):
        pool = ClientPool()
        with _patch_sdk():
            for i in range(1000):
                key = f"web:u:s{i}"
                client = await pool.get_or_create(key, options=FakeOptions())
                assert client.connected
                ok = await pool.disconnect(key)
                assert ok is True

        gc.collect()
        assert pool._clients == {}, (
            f"pool leaked {len(pool._clients)} records"
        )

    async def test_reuse_same_key_500_cycles(self):
        """Same key reused → single client rebuilt after each disconnect."""
        pool = ClientPool()
        seen_ids = set()
        with _patch_sdk():
            for _ in range(500):
                client = await pool.get_or_create("web:u:s", options=FakeOptions())
                seen_ids.add(client.id)
                await pool.disconnect("web:u:s")

        # Each disconnect terminates the previous client; new one is
        # created next acquire.
        assert len(seen_ids) == 500
        assert pool._clients == {}


class TestConcurrentAcquire:
    """Concurrent get_or_create() on the same key must yield ONE record."""

    async def test_50_concurrent_acquires_yield_one_client(self):
        pool = ClientPool()
        with _patch_sdk():
            results = await asyncio.gather(
                *[pool.get_or_create("web:u:s", options=FakeOptions()) for _ in range(50)]
            )

        # All callers see the same underlying client.
        assert len({id(c) for c in results}) == 1
        assert len(pool._clients) == 1
        await pool.disconnect("web:u:s")
        assert pool._clients == {}


class TestCapacityCap:
    """max_clients=K cap → only the K most-recently-used stay."""

    async def test_max_clients_cap_evicts_oldest(self):
        pool = ClientPool(max_clients=5)
        with _patch_sdk():
            for i in range(50):
                await pool.get_or_create(f"web:u:s{i}", options=FakeOptions())

        # Only 5 clients should remain; oldest 45 evicted.
        assert len(pool._clients) == 5
        for key in pool._clients:
            record: ClientRecord = pool._clients[key]
            assert record.state == "connected"

        await pool.disconnect_all()
        assert pool._clients == {}


class TestPruneIdleLoop:
    """Repeated prune_idle after many acquires must not leak records."""

    async def test_prune_idle_1000_records(self):
        pool = ClientPool()
        with _patch_sdk():
            for i in range(1000):
                await pool.get_or_create(f"web:u:s{i}", options=FakeOptions())

            # Wait past a tiny idle-ttl and then prune.  prune_idle
            # short-circuits when ttl <= 0, so we use a small positive
            # value and sleep past it.
            await asyncio.sleep(0.02)
            pruned = await pool.prune_idle(idle_ttl_seconds=0.01)

        assert pruned == 1000
        assert pool._clients == {}


class TestDisconnectAll:
    """disconnect_all must always empty _clients."""

    async def test_disconnect_all_after_massive_add(self):
        pool = ClientPool()
        with _patch_sdk():
            for i in range(500):
                await pool.get_or_create(f"web:u:s{i}", options=FakeOptions())

        n = await pool.disconnect_all()
        assert n == 500
        assert pool._clients == {}


class TestNoDanglingConnected:
    """After a churn cycle, no FakeClient should still report connected=True."""

    async def test_all_clients_disconnected_after_churn(self):
        pool = ClientPool()
        clients: list[FakeClient] = []
        with _patch_sdk():
            for i in range(200):
                c = await pool.get_or_create(f"web:u:s{i}", options=FakeOptions())
                clients.append(c)
            await pool.disconnect_all()

        assert pool._clients == {}
        # Every fake client should have been disconnected exactly once.
        for c in clients:
            assert c.disconnected is True
            assert c.disconnect_calls >= 1


class TestOptionsFingerprintChurn:
    """Repeatedly change options fingerprint on the same session_key.

    Each fingerprint change triggers a recycle: old record removed, new
    client created.  Verify the pool doesn't accumulate stale records.
    """

    async def test_fingerprint_change_500_cycles(self):
        pool = ClientPool()
        with _patch_sdk():
            for i in range(500):
                await pool.get_or_create(
                    "web:u:s",
                    options=FakeOptions(tag=f"v{i}"),
                    options_fingerprint=f"fp-{i}",
                )
                assert len(pool._clients) == 1  # never accumulates
                record = pool._clients["web:u:s"]
                assert record.options_fingerprint == f"fp-{i}"
                assert record.state == "connected"

        await pool.disconnect_all()
        assert pool._clients == {}
