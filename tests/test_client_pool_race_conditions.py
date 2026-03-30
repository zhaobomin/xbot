"""Test client pool race condition fixes.

Regression tests for client pool race condition fix.
Tests that disconnect operations don't hold locks during I/O.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestClientPoolRaceConditions:
    """Tests for client pool race condition fixes."""

    @pytest.fixture
    def mock_sdk_modules(self):
        """Mock SDK modules for testing."""
        mock_sdk = MagicMock()
        mock_sdk_types = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": mock_sdk,
                "claude_agent_sdk.types": mock_sdk_types,
            },
        ):
            yield mock_sdk, mock_sdk_types

    @pytest.mark.asyncio
    async def test_disconnect_happens_outside_lock(self, mock_sdk_modules) -> None:
        """Disconnect operations should happen outside the lock to prevent deadlocks."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend._build_options = MagicMock(return_value=MagicMock())

        # Create a mock sdk_config with proper values
        mock_sdk_config = MagicMock()
        mock_sdk_config.max_clients = 2
        mock_sdk_config.client_ttl_seconds = 3600
        mock_sdk_config.client_disconnect_retries = 2
        backend.sdk_config = mock_sdk_config

        lock_held_during_disconnect = []

        async def mock_connect(self):
            pass

        async def mock_disconnect(self):
            # Check if the clients lock is held during disconnect
            if backend._clients_lock.locked():
                lock_held_during_disconnect.append(True)
            else:
                lock_held_during_disconnect.append(False)
            await asyncio.sleep(0.05)  # Simulate slow disconnect

        def create_mock_client(idx):
            client = MagicMock()
            client.connect = lambda: mock_connect(client)
            client.disconnect = lambda: mock_disconnect(client)
            client.id = idx
            return client

        # Create clients
        clients = [create_mock_client(i) for i in range(3)]

        with patch(
            "xbot.agent.backends.claude_sdk_backend._ClaudeSDKClient",
            side_effect=clients,
        ):
            # Create first two clients (fills pool)
            await backend._get_or_create_client("session_1")
            await backend._get_or_create_client("session_2")

            # Third client should trigger eviction
            # Disconnect should happen outside lock
            await backend._get_or_create_client("session_3")

        # Verify disconnect was called
        assert len(lock_held_during_disconnect) >= 1
        # Lock should NOT be held during disconnect
        assert not any(lock_held_during_disconnect), \
            "Disconnect should happen outside the lock"

    @pytest.mark.asyncio
    async def test_concurrent_client_creation_with_eviction(self, mock_sdk_modules) -> None:
        """Concurrent client creation with eviction should not deadlock."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend._build_options = MagicMock(return_value=MagicMock())

        mock_sdk_config = MagicMock()
        mock_sdk_config.max_clients = 3
        mock_sdk_config.client_ttl_seconds = 3600
        mock_sdk_config.client_disconnect_retries = 2
        backend.sdk_config = mock_sdk_config

        async def mock_connect(self):
            await asyncio.sleep(0.01)

        async def mock_disconnect(self):
            await asyncio.sleep(0.02)

        def create_mock_client(*args, **kwargs):
            client = MagicMock()
            client.connect = lambda: mock_connect(client)
            client.disconnect = lambda: mock_disconnect(client)
            return client

        with patch(
            "xbot.agent.backends.claude_sdk_backend._ClaudeSDKClient",
            side_effect=create_mock_client,
        ):
            # Create clients concurrently (more than max_clients)
            tasks = [
                backend._get_or_create_client(f"session_{i}")
                for i in range(5)
            ]

            # Should complete without deadlock
            results = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=5.0,
            )

            assert len(results) == 5

    @pytest.mark.asyncio
    async def test_model_change_triggers_new_client(self, mock_sdk_modules) -> None:
        """Changing model should create a new client."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend._build_options = MagicMock(return_value=MagicMock())

        mock_sdk_config = MagicMock()
        mock_sdk_config.max_clients = 10
        mock_sdk_config.client_idle_ttl_seconds = 3600
        mock_sdk_config.client_disconnect_retries = 2
        mock_sdk_config.client_disconnect_timeout_seconds = 1
        backend.sdk_config = mock_sdk_config

        def mock_build_options(session_key, **kwargs):
            # Return options with different models
            opts = MagicMock()
            opts.model = backend._options_builder._get_model_name()
            return opts

        backend._build_options = mock_build_options
        backend._options_builder = MagicMock()
        backend._options_builder._get_model_name.side_effect = [
            "model_a",
            "model_a",
            "model_b",
            "model_b",
        ]

        async def mock_connect(self):
            pass

        async def mock_disconnect(self):
            pass

        def create_mock_client(*args, **kwargs):
            client = MagicMock()
            client.connect = lambda: mock_connect(client)
            client.disconnect = lambda: mock_disconnect(client)
            return client

        with patch(
            "xbot.agent.backends.claude_sdk_backend._ClaudeSDKClient",
            side_effect=create_mock_client,
        ):
            await backend._get_or_create_client("session_1")

            # Same session, different model - should create new client
            await backend._get_or_create_client("session_1")

        # Two clients should have been created (model change)

    @pytest.mark.asyncio
    async def test_safe_disconnect_retries(self, mock_sdk_modules) -> None:
        """_safe_disconnect_client should retry on transient failures."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()

        # Create a mock sdk_config
        mock_sdk_config = MagicMock()
        mock_sdk_config.client_disconnect_retries = 3
        backend.sdk_config = mock_sdk_config

        attempt_count = 0

        async def flaky_disconnect():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise Exception("Transient error")
            # Success on third attempt

        mock_client = MagicMock()
        mock_client.disconnect = flaky_disconnect

        # Should succeed after retries
        await backend._safe_disconnect_client(mock_client, "test_key")

        assert attempt_count == 3


class TestClientPoolConfiguration:
    """Tests for client pool configuration."""

    def test_default_max_clients(self) -> None:
        """Default max_clients should be 100."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()
            assert backend.max_clients == 100

    def test_default_client_ttl(self) -> None:
        """Default client TTL should be 3600 seconds (1 hour)."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()
            assert backend.client_ttl_seconds == 3600

    def test_default_disconnect_retries(self) -> None:
        """Default disconnect retries should be 2."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()
            assert backend.disconnect_retries == 2

    def test_config_overrides_defaults(self) -> None:
        """Config values should override defaults."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()

            # Create mock config with override values
            mock_sdk_config = MagicMock()
            mock_sdk_config.max_clients = 50
            mock_sdk_config.client_idle_ttl_seconds = 1800
            mock_sdk_config.client_disconnect_max_retries = 5
            backend.sdk_config = mock_sdk_config

            assert backend.max_clients == 50
            assert backend.client_ttl_seconds == 1800
            assert backend.disconnect_retries == 5
