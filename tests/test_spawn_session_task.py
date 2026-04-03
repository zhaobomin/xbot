"""Tests for _spawn_session_task and Phase 3 task lifecycle behavior.

Covers:
- Atomic task registration via _spawn_session_task
- _terminate_session relies on tracked tasks only
- gather(return_exceptions=True) does not swallow parent CancelledError
- No orphans produced when using the factory method
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from xbot.agent.runtime import AgentRuntime
from xbot.agent.state.checker import StateConsistencyChecker
from xbot.agent.state.coordinator import SessionStateCoordinator
from xbot.agent.state.machine import SessionPhase, SessionStateMachine
from xbot.agent.state.store import SessionStore


# ---------------------------------------------------------------------------
# Lightweight runtime shell (same pattern as test_runtime_run_dispatch_sequence)
# ---------------------------------------------------------------------------

class _MockRuntime:
    """Minimal runtime shell that carries only the state/task machinery."""

    def __init__(self):
        self.bus = None
        self._running = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_store = SessionStore()
        self._state_machine = SessionStateMachine()
        self._state_check_enabled = False
        self.sessions = None
        self.channels_config = None
        self.shared_resources = {}
        self.config = MagicMock()
        self.router = MagicMock()
        self.router.backend_type = "test"
        self.router._backend = MagicMock()
        self.router._backend._clients = {}
        self.router._backend._active_task_ids = {}
        self.router._backend._client_last_used = {}
        self._state_checker = StateConsistencyChecker(self)
        self._state_coordinator = SessionStateCoordinator(self, self._session_store)


def _bind_methods(runtime: _MockRuntime) -> None:
    """Bind real AgentRuntime methods onto the mock shell."""
    runtime._spawn_session_task = AgentRuntime._spawn_session_task.__get__(
        runtime, _MockRuntime
    )
    runtime._make_task_done_callback = AgentRuntime._make_task_done_callback.__get__(
        runtime, _MockRuntime
    )
    runtime._finalize_task_completion = AgentRuntime._finalize_task_completion.__get__(
        runtime, _MockRuntime
    )
    runtime._sync_session_phase = AgentRuntime._sync_session_phase.__get__(
        runtime, _MockRuntime
    )
    runtime._tag_task_for_session = AgentRuntime._tag_task_for_session
    runtime._task_belongs_to_session = AgentRuntime._task_belongs_to_session
    runtime._terminate_session = AgentRuntime._terminate_session.__get__(
        runtime, _MockRuntime
    )
    runtime._log_state_snapshot = AgentRuntime._log_state_snapshot.__get__(
        runtime, _MockRuntime
    )


def _make_runtime() -> _MockRuntime:
    rt = _MockRuntime()
    _bind_methods(rt)
    return rt


# ===========================================================================
# Test: Atomic registration
# ===========================================================================


class TestSpawnSessionTaskAtomicRegistration:
    """Verify _spawn_session_task performs atomic tag + register + callback."""

    @pytest.mark.asyncio
    async def test_task_is_tagged(self) -> None:
        """Task created via factory should carry _xbot_session_key."""
        runtime = _make_runtime()
        session_key = "test:atomic_tag"

        async def noop():
            pass

        task = runtime._spawn_session_task(noop(), session_key)
        assert getattr(task, "_xbot_session_key", None) == session_key
        await task

    @pytest.mark.asyncio
    async def test_task_is_registered(self) -> None:
        """Task created via factory should appear in coordinator's active tasks."""
        runtime = _make_runtime()
        session_key = "test:atomic_register"

        async def slow():
            await asyncio.sleep(5)

        task = runtime._spawn_session_task(slow(), session_key)
        active = runtime._state_coordinator.get_active_tasks(session_key)
        assert task in active

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_done_callback_unregisters(self) -> None:
        """Task should auto-unregister via done callback on completion."""
        runtime = _make_runtime()
        session_key = "test:atomic_callback"

        async def quick():
            return "done"

        task = runtime._spawn_session_task(quick(), session_key)
        await task
        # Allow one event-loop iteration for done callbacks
        await asyncio.sleep(0.05)

        active = runtime._state_coordinator.get_active_tasks(session_key)
        assert task not in active


# ===========================================================================
# Test: Termination uses tracked tasks only
# ===========================================================================


class TestTerminateSessionUsesTrackedTasksOnly:
    """Verify termination does not fall back to global task scanning."""

    @pytest.mark.asyncio
    async def test_terminate_session_does_not_scan_all_tasks(self) -> None:
        """Phase 3 removes the global all_tasks() scan entirely."""
        runtime = _make_runtime()
        session_key = "test:terminate_without_scan"

        runtime.router.backend.cancel_session = AsyncMock(return_value=0)
        runtime.router.backend.stop_active_task = AsyncMock(return_value=False)
        runtime.router.backend.interrupt_session = AsyncMock(
            return_value={"interrupted": False, "usage": None}
        )
        runtime.router.backend.reset_session = AsyncMock()

        original_all_tasks = asyncio.all_tasks

        def _boom():
            raise AssertionError("asyncio.all_tasks() should not be called in Phase 3")

        asyncio.all_tasks = _boom
        try:
            state = await runtime._terminate_session(session_key, hard_reset=False)
        finally:
            asyncio.all_tasks = original_all_tasks

        assert state["cancelled"] == 0

    @pytest.mark.asyncio
    async def test_terminate_session_cancels_tracked_task_only(self) -> None:
        """Tracked session tasks should be cancelled; unrelated tasks should be untouched."""
        runtime = _make_runtime()
        session_key = "test:tracked_only"
        unrelated_cancelled = False
        tracked_cancelled = False

        async def tracked_worker():
            nonlocal tracked_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                tracked_cancelled = True
                raise

        async def unrelated_worker():
            nonlocal unrelated_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                unrelated_cancelled = True
                raise

        tracked_task = runtime._spawn_session_task(tracked_worker(), session_key)
        unrelated_task = asyncio.create_task(unrelated_worker())
        await asyncio.sleep(0.01)

        runtime.router.backend.cancel_session = AsyncMock(return_value=0)
        runtime.router.backend.stop_active_task = AsyncMock(return_value=False)
        runtime.router.backend.interrupt_session = AsyncMock(
            return_value={"interrupted": False, "usage": None}
        )
        runtime.router.backend.reset_session = AsyncMock()

        state = await runtime._terminate_session(session_key, hard_reset=False)

        assert state["cancelled"] == 1
        assert tracked_cancelled is True
        assert tracked_task.done()
        assert unrelated_cancelled is False
        assert unrelated_task.done() is False

        unrelated_task.cancel()
        await asyncio.gather(unrelated_task, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_terminate_session_continues_backend_cleanup_when_task_errors(self) -> None:
        """Task exceptions should not prevent backend/session cleanup."""
        runtime = _make_runtime()
        session_key = "test:task_error_cleanup"

        async def failing_worker():
            raise RuntimeError("boom")

        task = runtime._spawn_session_task(failing_worker(), session_key)
        await asyncio.sleep(0)

        runtime.router.backend.cancel_session = AsyncMock(return_value=2)
        runtime.router.backend.stop_active_task = AsyncMock(return_value=True)
        runtime.router.backend.interrupt_session = AsyncMock(
            return_value={"interrupted": True, "usage": {"tokens": 1}}
        )
        runtime.router.backend.reset_session = AsyncMock()

        state = await runtime._terminate_session(session_key, hard_reset=False)

        assert task.done()
        assert state["cancelled"] == 0
        assert state["backend_cancelled"] == 2
        assert state["backend_task_stopped"] is True
        assert state["interrupted"] is True
        assert state["usage"] == {"tokens": 1}

    @pytest.mark.asyncio
    async def test_concurrent_terminate_session_calls_are_safe(self) -> None:
        """Concurrent terminate calls for one session should both complete safely."""
        runtime = _make_runtime()
        session_key = "test:concurrent_terminate"

        async def worker():
            await asyncio.sleep(10)

        task = runtime._spawn_session_task(worker(), session_key)
        await asyncio.sleep(0.01)

        runtime.router.backend.cancel_session = AsyncMock(return_value=0)
        runtime.router.backend.stop_active_task = AsyncMock(return_value=False)
        runtime.router.backend.interrupt_session = AsyncMock(
            return_value={"interrupted": False, "usage": None}
        )
        runtime.router.backend.reset_session = AsyncMock()

        result_a, result_b = await asyncio.gather(
            runtime._terminate_session(session_key, hard_reset=False),
            runtime._terminate_session(session_key, hard_reset=False),
        )

        assert task.done()
        assert sorted([result_a["cancelled"], result_b["cancelled"]]) == [0, 1]
        assert runtime._state_coordinator.get_phase(session_key) == SessionPhase.IDLE


# ===========================================================================
# Test: gather safety
# ===========================================================================


class TestGatherDoesNotSwallowParentCancel:
    """Verify asyncio.gather(return_exceptions=True) propagates parent cancel."""

    @pytest.mark.asyncio
    async def test_parent_cancel_propagates(self) -> None:
        """When the parent task is cancelled, gather should not block it."""

        async def child():
            await asyncio.sleep(10)

        async def parent():
            tasks = [asyncio.create_task(child()) for _ in range(3)]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        parent_task = asyncio.create_task(parent())
        await asyncio.sleep(0.05)
        parent_task.cancel()

        try:
            await parent_task
        except asyncio.CancelledError:
            pass

        assert parent_task.cancelled() or parent_task.done()

    @pytest.mark.asyncio
    async def test_terminate_session_propagates_parent_cancel(self) -> None:
        """Cancelling _terminate_session itself should propagate to its caller."""

        class _SlowBus:
            async def aclear_session_requests(self, session_key: str):
                await asyncio.sleep(10)
                return {"permission": False, "interaction": False}

        runtime = _make_runtime()
        runtime.bus = _SlowBus()
        runtime.router.backend.cancel_session = AsyncMock(return_value=0)
        runtime.router.backend.stop_active_task = AsyncMock(return_value=False)
        runtime.router.backend.interrupt_session = AsyncMock(
            return_value={"interrupted": False, "usage": None}
        )
        runtime.router.backend.reset_session = AsyncMock()

        terminate_task = asyncio.create_task(
            runtime._terminate_session("test:parent_cancel", hard_reset=False)
        )
        await asyncio.sleep(0.05)
        terminate_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await terminate_task

    @pytest.mark.asyncio
    async def test_child_exceptions_collected_not_raised(self) -> None:
        """Child task exceptions should be returned, not raised."""

        async def failing_child():
            raise ValueError("boom")

        async def sleeping_child():
            await asyncio.sleep(0.01)

        tasks = [
            asyncio.create_task(failing_child()),
            asyncio.create_task(sleeping_child()),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # First result should be the ValueError, second should be None
        assert isinstance(results[0], ValueError)
        assert results[1] is None


# ===========================================================================
# Test: No orphans with factory
# ===========================================================================


class TestNoOrphansWithFactory:
    """Verify the factory method prevents orphan creation entirely."""

    @pytest.mark.asyncio
    async def test_factory_task_found_in_pop(self) -> None:
        """Tasks from _spawn_session_task should always appear in pop_active_tasks."""
        runtime = _make_runtime()
        session_key = "test:no_orphan"

        async def worker():
            await asyncio.sleep(10)

        task = runtime._spawn_session_task(worker(), session_key)

        popped = runtime._state_coordinator.pop_active_tasks(session_key)
        assert task in popped

        # After pop, scanning all_tasks should find NO orphans with this session tag
        all_tasks = asyncio.all_tasks()
        orphans = [
            t
            for t in all_tasks
            if t not in popped
            and not t.done()
            and AgentRuntime._task_belongs_to_session(t, session_key)
        ]
        assert len(orphans) == 0

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_multiple_tasks_all_tracked(self) -> None:
        """Multiple tasks for the same session should all be tracked."""
        runtime = _make_runtime()
        session_key = "test:multi"

        async def worker():
            await asyncio.sleep(10)

        tasks = [runtime._spawn_session_task(worker(), session_key) for _ in range(5)]

        active = runtime._state_coordinator.get_active_tasks(session_key)
        assert len(active) == 5
        for t in tasks:
            assert t in active

        # Cleanup
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
