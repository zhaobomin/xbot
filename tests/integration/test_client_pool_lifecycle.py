"""Integration tests: ClientPool lifecycle race conditions.

These tests exercise the ClientPool under concurrent access patterns to verify:
- Concurrent get_or_create for the same key connects only once (no double-connect).
- Disconnect during an in-progress connect leaves no leak.
- disconnect_all racing with a new get_or_create produces no orphan.
- prune_idle continues processing after one client's disconnect raises.
- Health probe failure triggers client recycle with proper old-client teardown.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from xbot.runtime.core.client_pool import ClientPool, ClientRecord

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inject_record(
    pool: ClientPool,
    key: str,
    client: Any,
    *,
    last_used_at: float | None = None,
    options_fingerprint: str | None = None,
) -> ClientRecord:
    """Directly inject a ClientRecord into the pool (bypassing get_or_create)."""
    now = time.time()
    record = ClientRecord(
        session_key=key,
        client=client,
        options_fingerprint=options_fingerprint,
        created_at=last_used_at or now,
        last_used_at=last_used_at or now,
        state="connected",
    )
    pool._clients[key] = record
    return record


def _make_healthy_mock_client() -> AsyncMock:
    """Create a mock client that passes health checks."""
    client = AsyncMock()
    client.disconnect = AsyncMock()
    client.get_server_info = AsyncMock(return_value={"status": "ok"})
    return client


# ---------------------------------------------------------------------------
# Test 1: Concurrent get_or_create same key — no double connect
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestConcurrentGetOrCreateSameKeyNoDoubleConnect:
    """Mock ClaudeSDKClient so connect() is slow. Launch 5 concurrent
    get_or_create for the same session_key. Assert: all callers get the
    same client instance; only one record exists in pool; extra clients
    that lost the race had disconnect() called (no connection leak).
    This tests the inner while-True deduplication logic after connect()."""

    async def test_concurrent_get_or_create_same_key_no_double_connect(self):
        pool = ClientPool(max_clients=10)

        # Track all created client instances and their disconnect calls
        created_clients: list[Any] = []

        class SlowConnectClient:
            def __init__(self, options: Any):
                self._options = options
                self._disconnected = False
                created_clients.append(self)

            async def connect(self):
                await asyncio.sleep(0.1)

            async def disconnect(self):
                self._disconnected = True

            async def get_server_info(self):
                return {"status": "ok"}

        with patch("claude_agent_sdk.ClaudeSDKClient", SlowConnectClient):
            # Launch 5 concurrent calls for the same key
            tasks = [
                asyncio.create_task(
                    pool.get_or_create(
                        session_key="shared-key",
                        options={"model": "test"},
                        options_fingerprint="fp1",
                    )
                )
                for _ in range(5)
            ]
            results = await asyncio.gather(*tasks)

        # All callers should get the same client instance
        first = results[0]
        for r in results[1:]:
            assert r is first, "All callers must receive the same client instance"

        # Only one record should exist in pool for this key
        async with pool._lock:
            assert "shared-key" in pool._clients
            assert pool._clients["shared-key"].state == "connected"
            assert pool._clients["shared-key"].client is first

        # All extra clients (those that lost the race) must have been
        # disconnected — no leaked connections
        losers = [c for c in created_clients if c is not first]
        for loser in losers:
            assert loser._disconnected, (
                "A client that lost the store-race must have disconnect() called"
            )


# ---------------------------------------------------------------------------
# Test 2: Disconnect during connect — no leak
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDisconnectDuringConnectNoLeak:
    """Start get_or_create("k") with a slow connect. Fire disconnect("k")
    shortly after connect completes (record just stored). Assert: after both
    settle, pool._clients has no "k"; the connected client had disconnect()
    called. This verifies that disconnect immediately after creation leaves
    no leaked connections."""

    async def test_disconnect_during_connect_no_leak(self):
        pool = ClientPool(max_clients=10)

        connect_done = asyncio.Event()
        client_instance = None

        class SlowConnectClient:
            def __init__(self, options: Any):
                nonlocal client_instance
                client_instance = self
                self._disconnected = False

            async def connect(self):
                # Simulate a connect that takes some time
                await asyncio.sleep(0.05)
                connect_done.set()

            async def disconnect(self):
                self._disconnected = True

            async def get_server_info(self):
                return {"status": "ok"}

        async def delayed_disconnect():
            """Wait until connect signals completion, then fire disconnect."""
            await connect_done.wait()
            # Small delay to let get_or_create store the record
            await asyncio.sleep(0.02)
            return await pool.disconnect("k")

        with patch("claude_agent_sdk.ClaudeSDKClient", SlowConnectClient):
            # Start get_or_create in background
            create_task = asyncio.create_task(
                pool.get_or_create(
                    session_key="k",
                    options={"model": "test"},
                    options_fingerprint="fp1",
                )
            )

            # Disconnect fires shortly after connect completes
            disconnect_task = asyncio.create_task(delayed_disconnect())

            # Wait for both to finish
            results = await asyncio.gather(
                create_task, disconnect_task, return_exceptions=True
            )

        # Neither should have raised an unhandled exception
        for r in results:
            assert not isinstance(r, Exception), f"Unexpected exception: {r!r}"

        # After both settle, pool should not have "k"
        async with pool._lock:
            assert "k" not in pool._clients, (
                "Key 'k' should not remain in pool after disconnect"
            )

        # The client that was created and connected should have been disconnected
        assert client_instance is not None, "A client should have been created"
        assert client_instance._disconnected, (
            "The connected client must have had disconnect() called"
        )


# ---------------------------------------------------------------------------
# Test 3: disconnect_all concurrent with new create — no orphaned connections
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDisconnectAllConcurrentWithNewCreate:
    """Set up pool with 2 existing records. Start disconnect_all(). Concurrently
    start get_or_create("new_key"). Assert: the new connection either succeeds
    (and is in pool) or is properly tracked. No orphaned connections."""

    async def test_disconnect_all_concurrent_with_new_create(self):
        pool = ClientPool(max_clients=10)

        # Inject 2 existing records
        existing_clients = []
        for i in range(2):
            client = _make_healthy_mock_client()
            existing_clients.append(client)
            async with pool._lock:
                _inject_record(pool, f"existing-{i}", client)

        class QuickConnectClient:
            def __init__(self, options: Any):
                pass

            async def connect(self):
                await asyncio.sleep(0.02)

            async def disconnect(self):
                pass

            async def get_server_info(self):
                return {"status": "ok"}

        with patch("claude_agent_sdk.ClaudeSDKClient", QuickConnectClient):
            # Race: disconnect_all vs get_or_create for a new key
            disconnect_all_task = asyncio.create_task(pool.disconnect_all())
            create_task = asyncio.create_task(
                pool.get_or_create(
                    session_key="new_key",
                    options={"model": "test"},
                    options_fingerprint="fp-new",
                )
            )

            results = await asyncio.gather(
                disconnect_all_task, create_task, return_exceptions=True
            )

        # Neither should have raised
        for r in results:
            assert not isinstance(r, Exception), f"Unexpected exception: {r!r}"

        # The existing clients should have been disconnected
        for client in existing_clients:
            client.disconnect.assert_called()

        # The new key should either be in the pool (create succeeded after
        # disconnect_all processed its snapshot) or not be an orphan
        async with pool._lock:
            # If new_key is in pool, it must be in connected state
            if "new_key" in pool._clients:
                assert pool._clients["new_key"].state == "connected"
            # The old keys should not be in the pool
            assert "existing-0" not in pool._clients
            assert "existing-1" not in pool._clients

        # Verify no orphaned connections: everything in pool should be tracked
        async with pool._lock:
            for key, record in pool._clients.items():
                assert record.state == "connected", (
                    f"Record {key} has unexpected state: {record.state}"
                )


# ---------------------------------------------------------------------------
# Test 4: prune_idle continues after disconnect exception
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPruneIdleContinuesAfterDisconnectException:
    """Inject 3 idle ClientRecords. Mock the middle one's client.disconnect()
    to raise RuntimeError. Call prune_idle(idle_ttl_seconds=1, now=time.time()+100).
    Assert: the other 2 are still cleaned up; return value reflects actual count
    of successful prunes."""

    async def test_prune_idle_continues_after_disconnect_exception(self):
        pool = ClientPool(max_clients=10)

        old_time = time.time() - 200  # Well past any TTL

        # Client 1: normal disconnect
        client_1 = AsyncMock()
        client_1.disconnect = AsyncMock()

        # Client 2: disconnect raises RuntimeError
        client_2 = AsyncMock()
        client_2.disconnect = AsyncMock(side_effect=RuntimeError("boom"))

        # Client 3: normal disconnect
        client_3 = AsyncMock()
        client_3.disconnect = AsyncMock()

        async with pool._lock:
            _inject_record(pool, "idle-a", client_1, last_used_at=old_time)
            _inject_record(pool, "idle-b", client_2, last_used_at=old_time)
            _inject_record(pool, "idle-c", client_3, last_used_at=old_time)

        # prune_idle uses time.time() internally for the "now" comparison,
        # but the records have last_used_at far in the past, so any
        # idle_ttl_seconds=1 should flag them as stale.
        removed = await pool.prune_idle(idle_ttl_seconds=1)

        # Client 1 and 3 should have been disconnected successfully
        client_1.disconnect.assert_called()
        client_3.disconnect.assert_called()

        # Client 2's disconnect was also attempted (it just failed)
        client_2.disconnect.assert_called()

        # The return value counts only graceful disconnects.
        # disconnect() in the pool returns False when the client's disconnect raises,
        # but the record is still removed from the pool (after best-effort force cleanup).
        # So the pool's disconnect returns False for the failing one.
        # prune_idle counts only True returns.
        assert removed == 2, (
            f"Expected 2 successful prunes (middle one fails), got {removed}"
        )

        # All 3 records should be removed from the pool regardless
        async with pool._lock:
            assert "idle-a" not in pool._clients
            assert "idle-b" not in pool._clients
            assert "idle-c" not in pool._clients


# ---------------------------------------------------------------------------
# Test 5: health probe timeout recycles client
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestHealthProbeTimeoutRecyclesClient:
    """Inject a ClientRecord. Mock _is_client_healthy to return False
    (simulating timeout/unhealthy). Call get_or_create for that session_key
    (with options so it can recreate). Assert: the old client gets disconnected;
    a new client is created and returned."""

    async def test_health_probe_timeout_recycles_client(self):
        pool = ClientPool(max_clients=10)

        # Inject an existing "connected" record with an unhealthy client
        old_client = AsyncMock()
        old_client.disconnect = AsyncMock()
        old_client.get_server_info = AsyncMock(side_effect=RuntimeError("timeout"))

        async with pool._lock:
            _inject_record(
                pool, "recycle-key", old_client, options_fingerprint="fp1"
            )

        new_client_instance = None

        class FreshClient:
            def __init__(self, options: Any):
                nonlocal new_client_instance
                new_client_instance = self

            async def connect(self):
                await asyncio.sleep(0.01)

            async def disconnect(self):
                pass

            async def get_server_info(self):
                return {"status": "ok"}

        with patch("claude_agent_sdk.ClaudeSDKClient", FreshClient):
            result = await pool.get_or_create(
                session_key="recycle-key",
                options={"model": "test"},
                options_fingerprint="fp1",
            )

        # The old client should have been disconnected
        old_client.disconnect.assert_called()

        # A new client should have been created and returned
        assert new_client_instance is not None, "A new client should have been created"
        assert result is new_client_instance, (
            "get_or_create should return the newly created client"
        )

        # The pool should contain the new record
        async with pool._lock:
            record = pool._clients.get("recycle-key")
            assert record is not None, "recycle-key should be in the pool"
            assert record.client is new_client_instance
            assert record.state == "connected"
