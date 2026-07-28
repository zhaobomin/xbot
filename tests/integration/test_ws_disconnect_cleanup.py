"""Integration tests: WebSocket disconnect task cleanup.

Verifies that _cancel_tasks_and_wait correctly cancels and awaits tasks,
preventing ghost tasks from blocking session reconnection.

Covers:
- MED-1 hypothesis: cancelled tasks not awaited on WS disconnect
- PYTHONASYNCIODEBUG=1 would also catch un-awaited tasks as warnings
"""

from __future__ import annotations

import asyncio

import pytest

from xbot.interfaces.gateway.app import _cancel_tasks_and_wait


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Tests: _cancel_tasks_and_wait
# ---------------------------------------------------------------------------


class TestCancelTasksAndWait:
    """_cancel_tasks_and_wait must cancel all tasks and await their completion."""

    async def test_cancels_running_task(self):
        """A running async task must be cancelled."""
        cancelled = asyncio.Event()

        async def long_running():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(long_running())
        await asyncio.sleep(0)  # Let task start

        await _cancel_tasks_and_wait([task])

        assert task.done()
        assert task.cancelled()
        assert cancelled.is_set()

    async def test_handles_already_done_tasks(self):
        """Tasks that finished before cancel should be skipped gracefully."""

        async def quick():
            return 42

        task = asyncio.create_task(quick())
        await task  # Let it finish

        # Should not raise
        await _cancel_tasks_and_wait([task])
        assert task.done()

    async def test_handles_empty_list(self):
        """Empty task list should return immediately."""
        await _cancel_tasks_and_wait([])

    async def test_multiple_tasks_all_cancelled(self):
        """All tasks in list must be cancelled."""
        cancel_count = 0

        async def sleeper():
            nonlocal cancel_count
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancel_count += 1
                raise

        tasks = [asyncio.create_task(sleeper()) for _ in range(5)]
        await asyncio.sleep(0)

        await _cancel_tasks_and_wait(tasks)

        assert cancel_count == 5
        assert all(t.done() for t in tasks)

    async def test_task_with_cleanup_in_finally(self):
        """Task's finally block must execute before _cancel_tasks_and_wait returns."""
        cleanup_done = asyncio.Event()

        async def task_with_cleanup():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                # Simulate async cleanup
                cleanup_done.set()
                raise

        task = asyncio.create_task(task_with_cleanup())
        await asyncio.sleep(0)

        await _cancel_tasks_and_wait([task])

        # Cleanup must have happened BEFORE _cancel_tasks_and_wait returned
        assert cleanup_done.is_set()

    async def test_task_that_ignores_cancellation(self):
        """A task that swallows CancelledError should still be handled."""
        # Such a task won't be cancelled() but will be done()
        finished = asyncio.Event()

        async def stubborn_task():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                # Swallow — this is bad practice but shouldn't crash cleanup
                finished.set()
                return "I survived"

        task = asyncio.create_task(stubborn_task())
        await asyncio.sleep(0)

        await _cancel_tasks_and_wait([task])

        assert task.done()
        assert finished.is_set()
        # Task completed normally (didn't re-raise CancelledError)
        assert not task.cancelled()
        assert task.result() == "I survived"

    async def test_mixed_done_and_running(self):
        """Mix of already-done and still-running tasks."""

        async def quick():
            return "done"

        async def slow():
            await asyncio.sleep(100)

        done_task = asyncio.create_task(quick())
        await done_task

        running_task = asyncio.create_task(slow())
        await asyncio.sleep(0)

        await _cancel_tasks_and_wait([done_task, running_task])

        assert done_task.done()
        assert running_task.done()


# ---------------------------------------------------------------------------
# Tests: Session slot release simulation
# ---------------------------------------------------------------------------


class TestSessionSlotRelease:
    """Simulate the owned_task_keys + active_tasks pattern from ws_chat."""

    async def test_disconnect_releases_all_owned_slots(self):
        """After disconnect cleanup, session keys must be removed from active_tasks."""
        active_tasks: dict[str, asyncio.Task] = {}
        active_tasks_lock = asyncio.Lock()
        owned_task_keys: set[str] = set()

        async def agent_turn(key: str):
            try:
                await asyncio.sleep(100)
            finally:
                async with active_tasks_lock:
                    active_tasks.pop(key, None)
                    owned_task_keys.discard(key)

        # Simulate 3 active sessions owned by this connection
        for i in range(3):
            key = f"web:user:session{i}"
            task = asyncio.create_task(agent_turn(key))
            active_tasks[key] = task
            owned_task_keys.add(key)

        await asyncio.sleep(0)  # Let tasks start

        # Simulate ws_chat finally block
        owned_tasks_to_cancel: list[asyncio.Task] = []
        for key in list(owned_task_keys):
            async with active_tasks_lock:
                task = active_tasks.pop(key, None)
            if task is not None and not task.done():
                owned_tasks_to_cancel.append(task)

        await _cancel_tasks_and_wait(owned_tasks_to_cancel)

        # All slots must be freed
        assert len(active_tasks) == 0

    async def test_other_connections_tasks_not_affected(self):
        """Cleanup for one connection must NOT cancel tasks owned by another."""
        active_tasks: dict[str, asyncio.Task] = {}
        active_tasks_lock = asyncio.Lock()

        async def agent_turn():
            await asyncio.sleep(100)

        # Connection A owns session1
        task_a = asyncio.create_task(agent_turn())
        active_tasks["session1"] = task_a
        owned_by_a = {"session1"}

        # Connection B owns session2
        task_b = asyncio.create_task(agent_turn())
        active_tasks["session2"] = task_b

        await asyncio.sleep(0)

        # Connection A disconnects — only cancel owned keys
        owned_tasks_to_cancel: list[asyncio.Task] = []
        for key in list(owned_by_a):
            async with active_tasks_lock:
                task = active_tasks.pop(key, None)
            if task is not None and not task.done():
                owned_tasks_to_cancel.append(task)

        await _cancel_tasks_and_wait(owned_tasks_to_cancel)

        # session1 cancelled, session2 still running
        assert task_a.done()
        assert not task_b.done()
        assert "session2" in active_tasks

        # Cleanup task_b
        task_b.cancel()
        try:
            await task_b
        except asyncio.CancelledError:
            pass
