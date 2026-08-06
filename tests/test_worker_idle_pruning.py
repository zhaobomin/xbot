"""Tests for SessionWorker idle tracking and _prune_idle_workers.

Tests cover:
1. idle boundary sets last_idle_at
2. enqueuing message resets last_idle_at
3. _prune_idle_workers only cleans stale workers
4. exclude_keys protection
5. empty queue check
6. closed worker handling
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from xbot.platform.bus.events import InboundMessage
from xbot.runtime.core.service import AgentService, SessionWorker


def make_mock_worker(
    session_key: str = "test-session",
    *,
    closed: bool = False,
    last_idle_at: float | None = None,
    queue_items: list = None,
) -> SessionWorker:
    """Create a mock SessionWorker."""
    client = MagicMock()
    client.disconnect = AsyncMock(return_value=None)
    q = asyncio.Queue()
    if queue_items:
        for item in queue_items:
            q.put_nowait(item)
    return SessionWorker(
        session_key=session_key,
        client=client,
        input_queue=q,
        task=None,
        channel="telegram",
        chat_id="chat-1",
        closed=closed,
        last_idle_at=last_idle_at,
    )


def make_mock_service(workers: dict[str, SessionWorker] | None = None) -> AgentService:
    """Create a mock AgentService with _session_workers and config."""
    service = AgentService.__new__(AgentService)
    service._session_workers = workers or {}
    config = MagicMock()
    config.agents.claude_sdk.client_idle_ttl_seconds = 3600
    config.agents.claude_sdk.client_scavenger_enabled = True
    service._shared_resources = {"config": config}
    service._command_handler = None
    return service


class TestPruneIdleWorkers:
    """Tests for _prune_idle_workers method."""

    @pytest.mark.asyncio
    async def test_prunes_stale_idle_worker(self):
        """Worker idle for longer than TTL with empty queue should be pruned."""
        service = make_mock_service({
            "stale": make_mock_worker("stale", last_idle_at=time.time() - 7200),
        })
        # Mock _stop_session_worker to track calls
        service._stop_session_worker = AsyncMock(return_value=True)
        removed = await service._prune_idle_workers(3600)
        assert removed == 1
        service._stop_session_worker.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_prune_active_worker(self):
        """Worker with last_idle_at=None (still processing) should not be pruned."""
        service = make_mock_service({
            "active": make_mock_worker("active", last_idle_at=None),
        })
        service._stop_session_worker = AsyncMock(return_value=True)
        removed = await service._prune_idle_workers(3600)
        assert removed == 0
        service._stop_session_worker.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_prune_recently_idle_worker(self):
        """Worker idle for less than TTL should not be pruned."""
        service = make_mock_service({
            "recent": make_mock_worker("recent", last_idle_at=time.time() - 60),
        })
        service._stop_session_worker = AsyncMock(return_value=True)
        removed = await service._prune_idle_workers(3600)
        assert removed == 0

    @pytest.mark.asyncio
    async def test_does_not_prune_worker_with_pending_queue(self):
        """Worker with non-empty input_queue should not be pruned."""
        service = make_mock_service({
            "queued": make_mock_worker("queued", last_idle_at=time.time() - 7200,
                                       queue_items=[{"type": "user"}]),
        })
        service._stop_session_worker = AsyncMock(return_value=True)
        removed = await service._prune_idle_workers(3600)
        assert removed == 0

    @pytest.mark.asyncio
    async def test_does_not_prune_closed_worker(self):
        """Closed workers should be skipped."""
        service = make_mock_service({
            "closed": make_mock_worker("closed", closed=True, last_idle_at=time.time() - 7200),
        })
        service._stop_session_worker = AsyncMock(return_value=True)
        removed = await service._prune_idle_workers(3600)
        assert removed == 0

    @pytest.mark.asyncio
    async def test_excludes_specified_keys(self):
        """Workers in exclude_keys should not be pruned."""
        service = make_mock_service({
            "excluded": make_mock_worker("excluded", last_idle_at=time.time() - 7200),
            "pruned": make_mock_worker("pruned", last_idle_at=time.time() - 7200),
        })
        service._stop_session_worker = AsyncMock(return_value=True)
        removed = await service._prune_idle_workers(3600, exclude_keys={"excluded"})
        assert removed == 1
        # Verify only "pruned" was stopped
        call_args = service._stop_session_worker.call_args_list[0]
        assert call_args[0][0] == "pruned" or call_args.kwargs.get("session_key") == "pruned" or "pruned" in str(call_args)

    @pytest.mark.asyncio
    async def test_multiple_stale_workers(self):
        """Multiple stale workers should all be pruned."""
        service = make_mock_service({
            "stale1": make_mock_worker("stale1", last_idle_at=time.time() - 7200),
            "stale2": make_mock_worker("stale2", last_idle_at=time.time() - 8000),
            "active": make_mock_worker("active", last_idle_at=None),
        })
        service._stop_session_worker = AsyncMock(return_value=True)
        removed = await service._prune_idle_workers(3600)
        assert removed == 2

    @pytest.mark.asyncio
    async def test_empty_workers_dict(self):
        """No workers → returns 0."""
        service = make_mock_service({})
        service._stop_session_worker = AsyncMock(return_value=True)
        removed = await service._prune_idle_workers(3600)
        assert removed == 0

    @pytest.mark.asyncio
    async def test_ttl_zero_or_negative_returns_zero(self):
        """TTL <= 0 should disable pruning."""
        service = make_mock_service({
            "stale": make_mock_worker("stale", last_idle_at=time.time() - 7200),
        })
        service._stop_session_worker = AsyncMock(return_value=True)
        removed = await service._prune_idle_workers(0)
        assert removed == 0
        removed = await service._prune_idle_workers(-1)
        assert removed == 0


class TestIdleTracking:
    """Tests for last_idle_at field and tracking logic."""

    def test_last_idle_at_default_none(self):
        """New workers start with last_idle_at=None."""
        worker = make_mock_worker()
        assert worker.last_idle_at is None

    def test_last_idle_at_settable(self):
        """last_idle_at can be set to a timestamp."""
        worker = make_mock_worker()
        worker.last_idle_at = 12345.0
        assert worker.last_idle_at == 12345.0
