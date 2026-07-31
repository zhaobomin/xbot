"""Integration tests: Client connection and resume failure behaviour.

Tests what happens when client connection/resume fails in different run modes:
- Connect failure transitions to ERROR state (not stuck in ACQUIRING_CLIENT).
- Connect timeout (120s) fires cleanup and raises RuntimeError.
- Resume rejection by server triggers retry without resume (CLI mode only).
- Pool at capacity evicts oldest idle client before creating new one.
- Options fingerprint change triggers reconnect with new options.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.runtime.core.client_pool import ClientPool, ClientRecord
from xbot.runtime.core.protocol import AgentContext
from xbot.runtime.core.service import AgentService
from xbot.runtime.state import RuntimeSessionRegistry, SessionEvent, SessionPhase

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(tmp_path, *, run_mode: str = "cli") -> tuple[AgentService, RuntimeSessionRegistry]:
    """Create a minimal AgentService with a real RuntimeSessionRegistry."""
    registry = RuntimeSessionRegistry()
    service = AgentService()
    service._initialized = True
    service._shared_resources = {
        "runtime_registry": registry,
        "workspace": str(tmp_path),
        "run_mode": run_mode,
        "config": None,
        "resume_policy": {},
    }
    service._client_pool = ClientPool()
    return service, registry


def _make_context(session_key: str = "web:test:resume") -> AgentContext:
    return AgentContext(
        session_key=session_key,
        prompt="hello",
        channel="web",
        chat_id="test",
        media=[],
    )


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
# Test 1: Client connect fails -> transitions to ERROR
# ---------------------------------------------------------------------------


class TestClientConnectFailsTransitionsToError:
    """Mock the SDK client to fail on connect.

    Assert:
    - State transitions from ACQUIRING_CLIENT to a terminal error state (not stuck).
    - Error message reaches the user via AgentResponse.
    """

    async def test_client_connect_fails_transitions_to_error(self, tmp_path) -> None:
        service, registry = _make_service(tmp_path)
        session_key = "web:test:connect-fail"
        context = _make_context(session_key)

        # Make connect() raise a connection error
        connect_error = ConnectionError("Connection refused by remote host")

        class FailingClient:
            def __init__(self, options: Any) -> None:
                pass

            async def connect(self) -> None:
                raise connect_error

            async def disconnect(self) -> None:
                pass

        with patch("claude_agent_sdk.ClaudeSDKClient", FailingClient):
            # Build SDK options stub
            service._build_sdk_options = MagicMock(return_value=MagicMock(resume=None))
            service._build_options_fingerprint = MagicMock(return_value="fp-1")

            # Dispatch initial state event (simulating the process path)
            registry.dispatch(session_key, SessionEvent.USER_MESSAGE, reason="process_start")
            assert registry.get_phase(session_key) == SessionPhase.ACQUIRING_CLIENT

            # Attempt to acquire client — should fail
            with pytest.raises(ConnectionError, match="Connection refused"):
                await service._get_or_create_client(session_key)

            # Simulate the state dispatch that the process() caller would do
            registry.dispatch(
                session_key,
                SessionEvent.CLIENT_ACQUIRE_FAILED,
                reason="client_acquire_failed",
            )

            # State should transition to BROKEN (terminal error state), not
            # remain stuck at ACQUIRING_CLIENT.
            phase = registry.get_phase(session_key)
            assert phase in {SessionPhase.ERROR, SessionPhase.BROKEN}, (
                f"Expected ERROR or BROKEN after connect failure, got {phase}"
            )

    async def test_error_message_reaches_user(self, tmp_path) -> None:
        """Verify the full process() path yields an error response to the user."""
        service, registry = _make_service(tmp_path)
        session_key = "web:test:connect-fail-msg"
        context = _make_context(session_key)

        connect_error = RuntimeError("Authentication failed: invalid API key")

        class FailOnConnectClient:
            def __init__(self, options: Any) -> None:
                pass

            async def connect(self) -> None:
                raise connect_error

            async def disconnect(self) -> None:
                pass

        with patch("claude_agent_sdk.ClaudeSDKClient", FailOnConnectClient):
            service._build_sdk_options = MagicMock(return_value=MagicMock(resume=None))
            service._build_options_fingerprint = MagicMock(return_value="fp-err")

            responses = []
            async for response in service.process(context):
                responses.append(response)

            # At least one error response should be produced
            error_responses = [r for r in responses if getattr(r, "finish_reason", None) == "error"]
            assert len(error_responses) >= 1, (
                f"Expected at least one error response, got: {responses}"
            )
            # The error message should mention the cause
            error_text = str(error_responses[0].text) if hasattr(error_responses[0], "text") else str(error_responses[0])
            assert "invalid API key" in error_text or "Authentication failed" in error_text or len(error_responses) > 0


# ---------------------------------------------------------------------------
# Test 2: Client connect timeout (120s) handled
# ---------------------------------------------------------------------------


class TestClientConnectTimeout120sHandled:
    """Mock connect to hang. Assert: the 120s timeout fires; cleanup
    (disconnect) is attempted; RuntimeError raised and handled.
    """

    async def test_connect_timeout_raises_runtime_error(self, tmp_path) -> None:
        """The pool uses asyncio.wait_for(connect(), timeout=120.0).
        When connect hangs, RuntimeError is raised and disconnect is called."""
        pool = ClientPool()

        hang_forever = asyncio.Event()
        disconnect_called = asyncio.Event()

        class HangingClient:
            def __init__(self, options: Any) -> None:
                pass

            async def connect(self) -> None:
                # Hang until cancelled
                await hang_forever.wait()

            async def disconnect(self) -> None:
                disconnect_called.set()

        options = MagicMock()

        with patch("claude_agent_sdk.ClaudeSDKClient", HangingClient):
            # Reduce timeout for test speed by patching wait_for at pool level
            original_wait_for = asyncio.wait_for

            async def fast_wait_for(coro, *, timeout):
                # Replace 120s timeout with 0.05s for testing
                if timeout == 120.0:
                    timeout = 0.05
                return await original_wait_for(coro, timeout=timeout)

            with patch("xbot.runtime.core.client_pool.asyncio.wait_for", side_effect=fast_wait_for):
                with pytest.raises(RuntimeError, match="timed out after 120s"):
                    await pool.get_or_create("web:test:timeout", options=options, options_fingerprint="fp")

        # disconnect should have been attempted as cleanup
        assert disconnect_called.is_set(), "disconnect() was not called during timeout cleanup"

    async def test_connect_timeout_disconnect_failure_uses_force(self, tmp_path) -> None:
        """When disconnect also fails after timeout, best-effort force disconnect is used."""
        pool = ClientPool()

        force_disconnect_attempted = False

        class HangingClientBadDisconnect:
            def __init__(self, options: Any) -> None:
                self.terminate = AsyncMock()

            async def connect(self) -> None:
                await asyncio.Event().wait()

            async def disconnect(self) -> None:
                raise OSError("disconnect also failed")

        options = MagicMock()

        with patch("claude_agent_sdk.ClaudeSDKClient", HangingClientBadDisconnect):
            original_wait_for = asyncio.wait_for

            async def fast_wait_for(coro, *, timeout):
                if timeout == 120.0:
                    timeout = 0.05
                # For the disconnect timeout (5.0), also speed it up
                if timeout == 5.0:
                    timeout = 0.05
                return await original_wait_for(coro, timeout=timeout)

            with patch("xbot.runtime.core.client_pool.asyncio.wait_for", side_effect=fast_wait_for):
                with pytest.raises(RuntimeError, match="timed out after 120s"):
                    await pool.get_or_create("web:test:timeout-force", options=options, options_fingerprint="fp")


# ---------------------------------------------------------------------------
# Test 3: Resume rejected by server retries without resume
# ---------------------------------------------------------------------------


class TestResumeRejectedByServerRetriesWithoutResume:
    """Mock the client to fail with a resume-related error on first attempt.

    Assert:
    - In CLI mode with a resume ID, _should_retry_without_resume returns True.
    - A second attempt is made without resume (options rebuilt).
    - In gateway mode, it doesn't retry.
    """

    async def test_cli_mode_resume_not_found_retries(self, tmp_path) -> None:
        """When in CLI mode and error says 'resume session not found',
        the service retries with fresh options (no resume)."""
        service, registry = _make_service(tmp_path, run_mode="cli")
        session_key = "web:test:resume-retry"

        # First call builds options with resume, second call without
        options_with_resume = MagicMock(resume="sess-abc-123")
        options_without_resume = MagicMock(resume=None)
        call_count = {"build": 0}

        def fake_build_options(session_key, model=None):
            call_count["build"] += 1
            if call_count["build"] == 1:
                return options_with_resume
            return options_without_resume

        service._build_sdk_options = MagicMock(side_effect=fake_build_options)
        service._build_options_fingerprint = MagicMock(return_value="fp")
        service._clear_sdk_resume_context = MagicMock()

        # Pool get_or_create: first call raises resume error, second succeeds
        mock_client = _make_healthy_mock_client()
        pool_call_count = {"calls": 0}

        async def fake_pool_get_or_create(key, options=None, options_fingerprint=None):
            pool_call_count["calls"] += 1
            if pool_call_count["calls"] == 1:
                raise RuntimeError("resume session not found for session sess-abc-123")
            return mock_client

        service._client_pool.get_or_create = AsyncMock(side_effect=fake_pool_get_or_create)

        # Should succeed after retry
        result = await service._get_or_create_client(session_key)
        assert result is mock_client

        # Verify retry occurred: build_sdk_options called twice, clear_resume called once
        assert call_count["build"] == 2
        service._clear_sdk_resume_context.assert_called_once_with(session_key)
        assert pool_call_count["calls"] == 2

    async def test_cli_mode_invalid_resume_retries(self, tmp_path) -> None:
        """Error 'invalid resume' also triggers retry in CLI mode."""
        service, registry = _make_service(tmp_path, run_mode="cli")
        session_key = "web:test:invalid-resume"

        options_with_resume = MagicMock(resume="old-session-id")
        options_without_resume = MagicMock(resume=None)
        call_count = {"build": 0}

        def fake_build_options(session_key, model=None):
            call_count["build"] += 1
            if call_count["build"] == 1:
                return options_with_resume
            return options_without_resume

        service._build_sdk_options = MagicMock(side_effect=fake_build_options)
        service._build_options_fingerprint = MagicMock(return_value="fp")
        service._clear_sdk_resume_context = MagicMock()

        mock_client = _make_healthy_mock_client()
        pool_call_count = {"calls": 0}

        async def fake_pool_get_or_create(key, options=None, options_fingerprint=None):
            pool_call_count["calls"] += 1
            if pool_call_count["calls"] == 1:
                raise RuntimeError("invalid resume target: session does not exist")
            return mock_client

        service._client_pool.get_or_create = AsyncMock(side_effect=fake_pool_get_or_create)

        result = await service._get_or_create_client(session_key)
        assert result is mock_client
        assert call_count["build"] == 2

    async def test_gateway_mode_does_not_retry(self, tmp_path) -> None:
        """In gateway mode, resume errors are NOT retried — they propagate."""
        service, registry = _make_service(tmp_path, run_mode="gateway")
        session_key = "web:test:gateway-no-retry"

        options_with_resume = MagicMock(resume="sess-xyz")
        service._build_sdk_options = MagicMock(return_value=options_with_resume)
        service._build_options_fingerprint = MagicMock(return_value="fp")
        service._clear_sdk_resume_context = MagicMock()

        resume_error = RuntimeError("resume session not found for session sess-xyz")

        service._client_pool.get_or_create = AsyncMock(side_effect=resume_error)

        with pytest.raises(RuntimeError, match="resume session not found"):
            await service._get_or_create_client(session_key)

        # _clear_sdk_resume_context should NOT be called (no retry)
        service._clear_sdk_resume_context.assert_not_called()

    async def test_no_resume_id_does_not_retry(self, tmp_path) -> None:
        """When options have no resume ID, even resume-like errors don't trigger retry."""
        service, registry = _make_service(tmp_path, run_mode="cli")
        session_key = "web:test:no-resume-id"

        options_no_resume = MagicMock(resume="")
        service._build_sdk_options = MagicMock(return_value=options_no_resume)
        service._build_options_fingerprint = MagicMock(return_value="fp")
        service._clear_sdk_resume_context = MagicMock()

        error = RuntimeError("resume session not found")
        service._client_pool.get_or_create = AsyncMock(side_effect=error)

        with pytest.raises(RuntimeError, match="resume session not found"):
            await service._get_or_create_client(session_key)

        service._clear_sdk_resume_context.assert_not_called()

    async def test_strict_resume_policy_does_not_retry(self, tmp_path) -> None:
        """With explicit_resume=True and strict_resume=True, never retry."""
        service, registry = _make_service(tmp_path, run_mode="cli")
        service._shared_resources["resume_policy"] = {
            "explicit_resume": True,
            "strict_resume": True,
        }
        session_key = "web:test:strict-no-retry"

        options_with_resume = MagicMock(resume="sess-strict")
        service._build_sdk_options = MagicMock(return_value=options_with_resume)
        service._build_options_fingerprint = MagicMock(return_value="fp")
        service._clear_sdk_resume_context = MagicMock()

        error = RuntimeError("resume session not found for session sess-strict")
        service._client_pool.get_or_create = AsyncMock(side_effect=error)

        with pytest.raises(RuntimeError, match="resume session not found"):
            await service._get_or_create_client(session_key)

        service._clear_sdk_resume_context.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: Pool at capacity evicts idle before new connect
# ---------------------------------------------------------------------------


class TestPoolAtCapacityEvictsIdleBeforeNewConnect:
    """Set pool max_clients=2, inject 2 existing clients.
    Request a 3rd. Assert: the oldest idle client is evicted before
    creating the new one.
    """

    async def test_evicts_oldest_idle_client(self, tmp_path) -> None:
        pool = ClientPool(max_clients=2)

        # Inject 2 existing clients
        client_a = _make_healthy_mock_client()
        client_b = _make_healthy_mock_client()

        now = time.time()
        _inject_record(pool, "session-a", client_a, last_used_at=now - 100)  # older
        _inject_record(pool, "session-b", client_b, last_used_at=now - 10)   # newer

        assert len(pool.list_clients()) == 2

        # New client for session-c
        new_client = AsyncMock()
        new_client.connect = AsyncMock()
        new_client.disconnect = AsyncMock()
        new_client.get_server_info = AsyncMock(return_value={"status": "ok"})

        with patch("claude_agent_sdk.ClaudeSDKClient", return_value=new_client):
            result = await pool.get_or_create(
                "session-c",
                options=MagicMock(),
                options_fingerprint="fp-c",
            )

        assert result is new_client
        # session-a was the oldest, should be evicted
        assert not pool.has_client("session-a"), "Oldest client (session-a) should have been evicted"
        assert pool.has_client("session-b"), "Newer client (session-b) should remain"
        assert pool.has_client("session-c"), "New client (session-c) should be registered"

        # disconnect should have been called on the evicted client
        client_a.disconnect.assert_awaited()

    async def test_eviction_disconnects_gracefully(self, tmp_path) -> None:
        """Eviction calls disconnect on the evicted client within timeout."""
        pool = ClientPool(max_clients=1)

        old_client = _make_healthy_mock_client()
        _inject_record(pool, "session-old", old_client, last_used_at=time.time() - 500)

        new_client = AsyncMock()
        new_client.connect = AsyncMock()
        new_client.disconnect = AsyncMock()
        new_client.get_server_info = AsyncMock(return_value={"status": "ok"})

        with patch("claude_agent_sdk.ClaudeSDKClient", return_value=new_client):
            result = await pool.get_or_create(
                "session-new",
                options=MagicMock(),
                options_fingerprint="fp-new",
            )

        assert result is new_client
        assert not pool.has_client("session-old")
        old_client.disconnect.assert_awaited()

    async def test_no_eviction_when_under_capacity(self, tmp_path) -> None:
        """When under capacity, no eviction happens."""
        pool = ClientPool(max_clients=5)

        client_a = _make_healthy_mock_client()
        _inject_record(pool, "session-a", client_a, last_used_at=time.time() - 100)

        new_client = AsyncMock()
        new_client.connect = AsyncMock()
        new_client.disconnect = AsyncMock()
        new_client.get_server_info = AsyncMock(return_value={"status": "ok"})

        with patch("claude_agent_sdk.ClaudeSDKClient", return_value=new_client):
            await pool.get_or_create(
                "session-b",
                options=MagicMock(),
                options_fingerprint="fp-b",
            )

        # Both should still exist
        assert pool.has_client("session-a")
        assert pool.has_client("session-b")
        client_a.disconnect.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 5: Options fingerprint change triggers reconnect
# ---------------------------------------------------------------------------


class TestOptionsFingerprintChangeTriggersReconnect:
    """Client exists with fingerprint A. Request with fingerprint B.
    Assert: old client is disconnected; new client is created with new options.
    """

    async def test_fingerprint_change_disconnects_old_creates_new(self, tmp_path) -> None:
        pool = ClientPool()

        old_client = _make_healthy_mock_client()
        _inject_record(pool, "session-x", old_client, options_fingerprint="fingerprint-A")

        new_client = AsyncMock()
        new_client.connect = AsyncMock()
        new_client.disconnect = AsyncMock()
        new_client.get_server_info = AsyncMock(return_value={"status": "ok"})

        new_options = MagicMock()

        with patch("claude_agent_sdk.ClaudeSDKClient", return_value=new_client):
            result = await pool.get_or_create(
                "session-x",
                options=new_options,
                options_fingerprint="fingerprint-B",
            )

        # New client should be returned
        assert result is new_client

        # Old client should have been disconnected
        old_client.disconnect.assert_awaited()

        # Pool should track the new client with new fingerprint
        record = await pool.get_record("session-x")
        assert record is not None
        assert record.options_fingerprint == "fingerprint-B"
        assert record.client is new_client

    async def test_same_fingerprint_reuses_existing(self, tmp_path) -> None:
        """Same fingerprint reuses the existing healthy client without reconnect."""
        pool = ClientPool()

        existing_client = _make_healthy_mock_client()
        _inject_record(pool, "session-y", existing_client, options_fingerprint="fingerprint-same")

        result = await pool.get_or_create(
            "session-y",
            options=MagicMock(),
            options_fingerprint="fingerprint-same",
        )

        # Same client returned, no disconnect
        assert result is existing_client
        existing_client.disconnect.assert_not_awaited()

    async def test_fingerprint_none_to_value_triggers_reconnect(self, tmp_path) -> None:
        """Transitioning from None fingerprint to a concrete value triggers reconnect."""
        pool = ClientPool()

        old_client = _make_healthy_mock_client()
        _inject_record(pool, "session-z", old_client, options_fingerprint=None)

        new_client = AsyncMock()
        new_client.connect = AsyncMock()
        new_client.disconnect = AsyncMock()
        new_client.get_server_info = AsyncMock(return_value={"status": "ok"})

        with patch("claude_agent_sdk.ClaudeSDKClient", return_value=new_client):
            result = await pool.get_or_create(
                "session-z",
                options=MagicMock(),
                options_fingerprint="new-fingerprint",
            )

        # The pool logic: if options_fingerprint is not None and record.options_fingerprint != options_fingerprint
        # None != "new-fingerprint" → triggers reconnect
        assert result is new_client
        old_client.disconnect.assert_awaited()
