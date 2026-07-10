"""Tests for simplified client pool."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.runtime.core.client_pool import ClientPool


class TestClientPool:
    """Tests for ClientPool."""

    @pytest.fixture
    def pool(self) -> ClientPool:
        """Create a client pool for testing."""
        return ClientPool()

    @pytest.mark.asyncio
    async def test_get_or_create_new_client(self, pool: ClientPool) -> None:
        """Test creating a new client."""
        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client_class.return_value = mock_client

            client = await pool.get_or_create("session:1", options=MagicMock())

            assert client == mock_client
            mock_client_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_or_create_existing_client(self, pool: ClientPool) -> None:
        """Test getting an existing client."""
        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client_class.return_value = mock_client

            # First call creates
            client1 = await pool.get_or_create("session:1", options=MagicMock())
            # Second call returns existing
            client2 = await pool.get_or_create("session:1")

            assert client1 == client2
            mock_client_class.assert_called_once()  # Only created once

    @pytest.mark.asyncio
    async def test_get_or_create_reconnects_when_options_fingerprint_changes(self, pool: ClientPool) -> None:
        """The next turn must not reuse a client created with stale SDK options."""
        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class:
            first_client = MagicMock()
            first_client.connect = AsyncMock()
            first_client.disconnect = AsyncMock()
            second_client = MagicMock()
            second_client.connect = AsyncMock()
            mock_client_class.side_effect = [first_client, second_client]

            await pool.get_or_create(
                "session:1", options=MagicMock(), options_fingerprint="model-a:40"
            )
            client = await pool.get_or_create(
                "session:1", options=MagicMock(), options_fingerprint="model-a:12"
            )

            assert client is second_client
            first_client.disconnect.assert_awaited_once()
            assert mock_client_class.call_count == 2

    @pytest.mark.asyncio
    async def test_disconnect(self, pool: ClientPool) -> None:
        """Test disconnecting a client."""
        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client.disconnect = AsyncMock()
            mock_client_class.return_value = mock_client

            await pool.get_or_create("session:1", options=MagicMock())
            await pool.disconnect("session:1")

            mock_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_returns_false_after_force_disconnect_failure(self, pool: ClientPool) -> None:
        """Graceful disconnect failure surfaces as False even after best-effort force cleanup.

        The force-disconnect fallback still runs (close is awaited) and the
        record is removed from the pool, but the return value is False so
        prune_idle/disconnect_all counts don't mask the graceful-failure trend.
        """
        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client.disconnect = AsyncMock(side_effect=RuntimeError("stuck"))
            mock_client.terminate = None
            mock_client.kill = None
            mock_client.close = AsyncMock()
            mock_client_class.return_value = mock_client

            await pool.get_or_create("session:force", options=MagicMock())

            assert await pool.disconnect("session:force") is False
            assert "session:force" not in pool.snapshot()["clients"]
            mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_all(self, pool: ClientPool) -> None:
        """Test disconnecting all clients."""
        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class:
            mock_client1 = MagicMock()
            mock_client1.connect = AsyncMock()
            mock_client1.disconnect = AsyncMock()
            mock_client2 = MagicMock()
            mock_client2.connect = AsyncMock()
            mock_client2.disconnect = AsyncMock()

            # Create two clients
            mock_client_class.return_value = mock_client1
            await pool.get_or_create("session:1", options=MagicMock())
            mock_client_class.return_value = mock_client2
            await pool.get_or_create("session:2", options=MagicMock())

            await pool.disconnect_all()

            mock_client1.disconnect.assert_called_once()
            mock_client2.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_snapshot(self, pool: ClientPool) -> None:
        """Test getting pool snapshot."""
        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client_class.return_value = mock_client

            await pool.get_or_create("session:1", options=MagicMock())
            snapshot = pool.snapshot()

            assert "session:1" in snapshot["clients"]
            assert snapshot["counts"]["connected"] == 1

    @pytest.mark.asyncio
    async def test_get_record_returns_connected_record(self, pool: ClientPool) -> None:
        """Callers should not need to inspect the private _clients dict."""
        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client_class.return_value = mock_client

            await pool.get_or_create("session:1", options=MagicMock())

            record = await pool.get_record("session:1")

            assert record is not None
            assert record.client is mock_client

    @pytest.mark.asyncio
    async def test_get_or_create_cleans_up_on_connect_timeout(self, pool: ClientPool) -> None:
        """Connect timeout should trigger client cleanup."""
        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_client.disconnect = AsyncMock()
            mock_client_class.return_value = mock_client

            with pytest.raises(RuntimeError, match="timed out"):
                await pool.get_or_create("session:timeout", options=MagicMock())

            mock_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_or_create_evicts_oldest_client_at_capacity(self) -> None:
        """Capacity should be enforced even when idle pruning has not run yet."""
        pool = ClientPool(max_clients=1)
        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class:
            first_client = MagicMock()
            first_client.connect = AsyncMock()
            first_client.disconnect = AsyncMock()
            second_client = MagicMock()
            second_client.connect = AsyncMock()
            second_client.disconnect = AsyncMock()
            mock_client_class.side_effect = [first_client, second_client]

            await pool.get_or_create("session:1", options=MagicMock())
            await pool.get_or_create("session:2", options=MagicMock())

        first_client.disconnect.assert_awaited_once()
        assert pool.list_clients() == ["session:2"]

    @pytest.mark.asyncio
    async def test_connect_does_not_block_unrelated_disconnect(self) -> None:
        """A slow SDK connect should not hold the pool lock for unrelated operations."""
        pool = ClientPool()
        connect_started = asyncio.Event()
        allow_connect_to_finish = asyncio.Event()

        async def slow_connect() -> None:
            connect_started.set()
            await allow_connect_to_finish.wait()

        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.connect = slow_connect
            mock_client.disconnect = AsyncMock()
            mock_client_class.return_value = mock_client

            create_task = asyncio.create_task(
                pool.get_or_create("session:slow", options=MagicMock())
            )
            await connect_started.wait()
            try:
                result = await asyncio.wait_for(pool.disconnect("session:other"), timeout=0.05)
                assert result is True
            finally:
                allow_connect_to_finish.set()
                await create_task
