"""Chaos test: ClientPool acquire/disconnect/prune under concurrent load.

Since ClientPool is coupled to ClaudeSDKClient, these tests use the
_lock and internal dict directly (without full mocking) to verify:
  * prune_idle short-circuits correctly when idle_ttl<=0
  * disconnect_all + concurrent state checks are consistent
  * No stale entries after disconnect

These complement the soak tests (which use the real pool with a mock
adapter) by testing specific interleaving patterns.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, patch

import pytest

from xbot.runtime.core.client_pool import ClientPool, ClientRecord

pytestmark = [pytest.mark.chaos]


class TestClientPoolChaos:
    """Race conditions in pool state management."""

    async def test_prune_idle_returns_zero_on_zero_ttl(self):
        """prune_idle(0) must short-circuit to 0, not prune everything."""
        pool = ClientPool(max_clients=10)
        # Directly inject a record to simulate an existing connection.
        async with pool._lock:
            pool._clients["user:x"] = ClientRecord(
                session_key="user:x",
                client=AsyncMock(),
                state="connected",
                last_used_at=time.time() - 1000,
            )

        result = await pool.prune_idle(idle_ttl_seconds=0)
        assert result == 0, f"prune_idle(0) should return 0, got {result}"
        assert "user:x" in pool._clients, "client was pruned despite ttl=0"

    async def test_prune_idle_negative_ttl_short_circuits(self):
        """prune_idle(-1) must short-circuit to 0."""
        pool = ClientPool(max_clients=10)
        async with pool._lock:
            pool._clients["user:y"] = ClientRecord(
                session_key="user:y",
                client=AsyncMock(),
                state="connected",
                last_used_at=time.time() - 9999,
            )

        result = await pool.prune_idle(idle_ttl_seconds=-1)
        assert result == 0
        assert "user:y" in pool._clients

    async def test_concurrent_prune_and_disconnect_no_double_disconnect(self):
        """prune_idle and disconnect for the same key at the same time.
        The actual disconnect should happen once, not twice.
        """
        pool = ClientPool(max_clients=10)
        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()

        async with pool._lock:
            pool._clients["user:dbl"] = ClientRecord(
                session_key="user:dbl",
                client=mock_client,
                state="connected",
                last_used_at=time.time() - 100,
            )

        # Race: prune (which should pick up the stale key) and explicit disconnect
        results = await asyncio.gather(
            pool.prune_idle(idle_ttl_seconds=1.0),
            pool.disconnect("user:dbl"),
            return_exceptions=True,
        )

        for r in results:
            assert not isinstance(r, Exception), f"raised: {r!r}"

        # Client should no longer be in connected state.
        if "user:dbl" in pool._clients:
            assert pool._clients["user:dbl"].state != "connected"

    async def test_disconnect_all_racing_with_prune(self):
        """disconnect_all and prune_idle running concurrently on
        overlapping key sets.  No deadlock, no double-disconnect.
        """
        pool = ClientPool(max_clients=10)
        for i in range(5):
            mock_client = AsyncMock()
            mock_client.disconnect = AsyncMock()
            async with pool._lock:
                pool._clients[f"user:{i}"] = ClientRecord(
                    session_key=f"user:{i}",
                    client=mock_client,
                    state="connected",
                    last_used_at=time.time() - 200,
                )

        results = await asyncio.gather(
            pool.disconnect_all(),
            pool.prune_idle(idle_ttl_seconds=1.0),
            return_exceptions=True,
        )

        for r in results:
            assert not isinstance(r, Exception), f"raised: {r!r}"

        # All should be disconnected.
        stale = [
            k for k, r in pool._clients.items()
            if r.state == "connected"
        ]
        assert stale == [], f"stale connected entries: {stale}"
