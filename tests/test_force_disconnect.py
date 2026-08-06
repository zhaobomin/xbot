"""Tests for force_disconnect_client module-level function.

Tests cover:
1. First-layer fallback: client.terminate()/kill()/close()
2. Second-layer fallback: client._process.terminate()/kill()
3. Third-layer fallback: os.killpg with SIGKILL
4. All-failure graceful handling
5. Non-posix platforms (no SIGKILL)
"""

import asyncio
import os
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.runtime.core.client_pool import force_disconnect_client


def make_mock_client(
    *,
    terminate=None,
    kill=None,
    close=None,
    _process=None,
    _pid=None,
):
    """Create a mock ClaudeSDKClient-like object."""
    client = MagicMock()
    # Client-level methods
    client.terminate = terminate if terminate is not None else MagicMock(return_value=None)
    client.kill = kill if kill is not None else MagicMock(return_value=None)
    client.close = close if close is not None else MagicMock(return_value=None)
    # Nested process handle
    if _process is not None:
        client._process = _process
    elif _process is None and _pid is not None:
        proc = MagicMock()
        proc.terminate = MagicMock(return_value=None)
        proc.kill = MagicMock(return_value=None)
        proc.pid = _pid
        client._process = proc
    else:
        client._process = None
    return client


class TestForceDisconnectClientFirstLayer:
    """Tests for the first fallback layer: client-level terminate/kill/close."""

    @pytest.mark.asyncio
    async def test_terminate_succeeds(self):
        """client.terminate() succeeds → no other methods tried."""
        client = make_mock_client()
        await force_disconnect_client(client, "test-session")
        client.terminate.assert_called_once()
        client.kill.assert_not_called()
        client.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_terminate_fails_kill_succeeds(self):
        """client.terminate() raises → client.kill() tried."""
        client = make_mock_client()
        client.terminate = MagicMock(side_effect=RuntimeError("nope"))
        await force_disconnect_client(client, "test-session")
        client.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_terminate_kill_fail_close_succeeds(self):
        """Both terminate and kill fail → close() tried."""
        client = make_mock_client()
        client.terminate = MagicMock(side_effect=RuntimeError("nope"))
        client.kill = MagicMock(side_effect=RuntimeError("nope"))
        await force_disconnect_client(client, "test-session")
        client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_terminate_returns_false_if_not_callable(self):
        """If terminate is not callable, skip to next."""
        client = make_mock_client()
        client.terminate = None
        await force_disconnect_client(client, "test-session")
        client.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_terminate_supported(self):
        """If terminate() returns an awaitable, await it."""
        client = make_mock_client()
        client.terminate = AsyncMock(return_value=None)
        await force_disconnect_client(client, "test-session")
        client.terminate.assert_awaited_once()


class TestForceDisconnectClientSecondLayer:
    """Tests for the second fallback layer: _process.terminate()/kill()."""

    @pytest.mark.asyncio
    async def test_process_terminate_used_when_client_methods_fail(self):
        """When all client-level methods fail, try _process.terminate()."""
        proc = MagicMock()
        proc.terminate = MagicMock(return_value=None)
        proc.kill = MagicMock(return_value=None)
        client = make_mock_client(terminate=MagicMock(side_effect=Exception()),
                                   kill=MagicMock(side_effect=Exception()),
                                   close=MagicMock(side_effect=Exception()),
                                   _process=proc)
        await force_disconnect_client(client, "test-session")
        proc.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_kill_fallback(self):
        """When _process.terminate() fails, try _process.kill()."""
        proc = MagicMock()
        proc.terminate = MagicMock(side_effect=RuntimeError("nope"))
        proc.kill = MagicMock(return_value=None)
        client = make_mock_client(terminate=MagicMock(side_effect=Exception()),
                                   kill=MagicMock(side_effect=Exception()),
                                   close=MagicMock(side_effect=Exception()),
                                   _process=proc)
        await force_disconnect_client(client, "test-session")
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_process_handle_skips_second_layer(self):
        """If client has no _process attribute, skip to third layer."""
        client = make_mock_client(
            terminate=MagicMock(side_effect=Exception()),
            kill=MagicMock(side_effect=Exception()),
            close=MagicMock(side_effect=Exception()),
            _process=None,
        )
        client._pid = None
        # Should not raise, just log
        await force_disconnect_client(client, "test-session")


class TestForceDisconnectClientThirdLayer:
    """Tests for the third fallback layer: os.killpg with SIGKILL."""

    @pytest.mark.asyncio
    async def test_sigkill_used_when_all_else_fails(self):
        """When all client and process methods fail, use os.killpg."""
        proc = MagicMock()
        proc.terminate = MagicMock(side_effect=RuntimeError("nope"))
        proc.kill = MagicMock(side_effect=RuntimeError("nope"))
        proc.close = MagicMock(side_effect=RuntimeError("nope"))
        proc.pid = 12345
        client = make_mock_client(
            terminate=MagicMock(side_effect=Exception()),
            kill=MagicMock(side_effect=Exception()),
            close=MagicMock(side_effect=Exception()),
            _process=proc,
        )
        with patch("os.name", "posix"), \
             patch("os.getpgid", return_value=12345) as mock_getpgid, \
             patch("os.killpg") as mock_killpg:
            await force_disconnect_client(client, "test-session")
            mock_getpgid.assert_called_once_with(12345)
            mock_killpg.assert_called_once_with(12345, signal.SIGKILL)

    @pytest.mark.asyncio
    async def test_sigkill_skipped_on_non_posix(self):
        """On non-posix platforms, SIGKILL is not attempted."""
        proc = MagicMock()
        proc.terminate = MagicMock(side_effect=RuntimeError("nope"))
        proc.kill = MagicMock(side_effect=RuntimeError("nope"))
        proc.pid = 12345
        client = make_mock_client(
            terminate=MagicMock(side_effect=Exception()),
            kill=MagicMock(side_effect=Exception()),
            close=MagicMock(side_effect=Exception()),
            _process=proc,
        )
        with patch("os.name", "nt"):
            # Should not raise
            await force_disconnect_client(client, "test-session")

    @pytest.mark.asyncio
    async def test_sigkill_failure_handled_gracefully(self):
        """If os.killpg itself fails, no exception propagated."""
        proc = MagicMock()
        proc.terminate = MagicMock(side_effect=RuntimeError("nope"))
        proc.kill = MagicMock(side_effect=RuntimeError("nope"))
        proc.pid = 99999  # likely no such process
        client = make_mock_client(
            terminate=MagicMock(side_effect=Exception()),
            kill=MagicMock(side_effect=Exception()),
            close=MagicMock(side_effect=Exception()),
            _process=proc,
        )
        with patch("os.name", "posix"), \
             patch("os.getpgid", return_value=99999), \
             patch("os.killpg", side_effect=ProcessLookupError("no such process")):
            # Should not raise
            await force_disconnect_client(client, "test-session")

    @pytest.mark.asyncio
    async def test_no_pid_skips_sigkill(self):
        """If process has no pid, skip SIGKILL."""
        proc = MagicMock()
        proc.terminate = MagicMock(side_effect=RuntimeError("nope"))
        proc.kill = MagicMock(side_effect=RuntimeError("nope"))
        proc.pid = None
        client = make_mock_client(
            terminate=MagicMock(side_effect=Exception()),
            kill=MagicMock(side_effect=Exception()),
            close=MagicMock(side_effect=Exception()),
            _process=proc,
        )
        # Should not raise
        await force_disconnect_client(client, "test-session")


class TestForceDisconnectClientEdgeCases:
    """Edge cases for force_disconnect_client."""

    @pytest.mark.asyncio
    async def test_none_client_handled(self):
        """None client should not raise."""
        # Should not raise
        await force_disconnect_client(None, "test-session")

    @pytest.mark.asyncio
    async def test_client_without_process_attribute(self):
        """Client without _process or process attribute should use client methods only."""
        client = MagicMock()
        client.terminate = MagicMock(return_value=None)
        # No _process, no process, no _pid
        del client._process
        # 'process' property doesn't exist either
        await force_disconnect_client(client, "test-session")
        client.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_attribute_fallback(self):
        """If _process doesn't exist, 'process' attribute is tried."""
        proc = MagicMock()
        proc.terminate = MagicMock(return_value=None)
        client = MagicMock()
        client.terminate = MagicMock(side_effect=RuntimeError("nope"))
        client.kill = MagicMock(side_effect=RuntimeError("nope"))
        client.close = MagicMock(side_effect=RuntimeError("nope"))
        # _process raises AttributeError, process attribute exists
        client._process = None
        client.process = proc
        await force_disconnect_client(client, "test-session")
        proc.terminate.assert_called_once()
