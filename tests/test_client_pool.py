"""Tests for simplified client pool."""

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
