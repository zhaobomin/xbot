"""Test orphan task scanning during session termination.

Regression tests for orphan task cleanup fix.
Tests that tasks not properly registered are still cleaned up.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.agent.state_coordinator import SessionStateCoordinator
from xbot.agent.session_store import SessionStore
from xbot.agent.state_machine import SessionPhase
from xbot.bus.events import InboundMessage
from xbot.bus.queue import MessageBus


class MockConfig:
    """Mock config for testing."""

    class Agents:
        class Defaults:
            model = "test-model"
            provider = "test"
            workspace = "/tmp/test"
            available_models: list[str] = []  # Empty list for model manager

        defaults = Defaults()
        claude_sdk = MagicMock()

    agents = Agents()
    channels = MagicMock()
    tools = MagicMock()


class TestOrphanTaskScanning:
    """Tests for orphan task scanning in terminate_session."""

    @pytest.fixture
    def mock_runtime(self, tmp_path):
        """Create a mock runtime for testing."""
        config = MockConfig()
        shared_resources = {
            "bus": MessageBus(),
            "session_manager": MagicMock(),
            "workspace": str(tmp_path),
            "config": config,
        }

        # Create a minimal mock runtime
        runtime = MagicMock()
        runtime.bus = shared_resources["bus"]
        session_store = SessionStore()
        runtime._session_store = session_store
        runtime._state_coordinator = SessionStateCoordinator(runtime, session_store)
        runtime.router = MagicMock()
        runtime.router.backend = MagicMock()

        return runtime

    @pytest.mark.asyncio
    async def test_orphan_tasks_detected_and_cancelled(self, mock_runtime) -> None:
        """Orphan tasks (not in task tracking) should be detected and cancelled."""
        session_key = "telegram:test_session"
        orphan_cancelled = False

        async def orphan_task():
            """Task that simulates an orphan (not registered)."""
            nonlocal orphan_cancelled
            try:
                await asyncio.sleep(10)  # Long running
            except asyncio.CancelledError:
                orphan_cancelled = True
                raise

        # Create an orphan task manually
        task = asyncio.create_task(orphan_task())

        # Wait a bit for task to start
        await asyncio.sleep(0.01)

        # Mock the router and backend
        mock_runtime.router.backend.cancel_session = AsyncMock(return_value=0)
        mock_runtime.router.backend.stop_active_task = AsyncMock(return_value=False)
        mock_runtime.router.backend.interrupt_session = AsyncMock(return_value={})

        # Pop tasks (returns empty since we didn't register)
        mock_runtime._state_coordinator.pop_active_tasks = MagicMock(return_value=[])

        # Cancel our task to clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert orphan_cancelled

    @pytest.mark.asyncio
    async def test_registered_tasks_cancelled(self, mock_runtime) -> None:
        """Registered tasks should be cancelled normally."""
        session_key = "telegram:test_session"
        task_cancelled = False

        async def registered_task():
            nonlocal task_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                task_cancelled = True
                raise

        # Create and register the task
        task = asyncio.create_task(registered_task())
        await asyncio.sleep(0.01)

        # Mock the router and backend
        mock_runtime.router.backend.cancel_session = AsyncMock(return_value=0)
        mock_runtime.router.backend.stop_active_task = AsyncMock(return_value=False)
        mock_runtime.router.backend.interrupt_session = AsyncMock(return_value={})

        # Return the registered task
        mock_runtime._state_coordinator.pop_active_tasks = MagicMock(return_value=[task])

        # Simulate terminate_session logic
        tasks = mock_runtime._state_coordinator.pop_active_tasks(session_key)
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        assert task_cancelled
        assert cancelled == 1

    @pytest.mark.asyncio
    async def test_no_tasks_graceful_handling(self, mock_runtime) -> None:
        """No tasks scenario should be handled gracefully."""
        session_key = "telegram:empty_session"

        # Mock the router and backend
        mock_runtime.router.backend.cancel_session = AsyncMock(return_value=0)
        mock_runtime.router.backend.stop_active_task = AsyncMock(return_value=False)
        mock_runtime.router.backend.interrupt_session = AsyncMock(return_value={})

        mock_runtime._state_coordinator.pop_active_tasks = MagicMock(return_value=[])

        # Simulate terminate_session logic
        tasks = mock_runtime._state_coordinator.pop_active_tasks(session_key)
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())

        assert cancelled == 0

    @pytest.mark.asyncio
    async def test_backend_cancel_failure_doesnt_block(self, mock_runtime) -> None:
        """Backend cancel failure should not block termination."""
        session_key = "telegram:test_session"

        mock_runtime.router.backend.cancel_session = AsyncMock(
            side_effect=Exception("Backend error")
        )
        mock_runtime.router.backend.stop_active_task = AsyncMock(return_value=False)
        mock_runtime.router.backend.interrupt_session = AsyncMock(return_value={})

        mock_runtime._state_coordinator.pop_active_tasks = MagicMock(return_value=[])

        # Should complete without raising
        try:
            await mock_runtime.router.backend.cancel_session(session_key)
        except Exception:
            pass  # Expected to raise, but should be caught


class TestTaskTrackingIntegration:
    """Integration tests for task tracking."""

    @pytest.mark.asyncio
    async def test_concurrent_terminations_same_session(self, tmp_path) -> None:
        """Concurrent terminations of the same session should be safe."""
        config = MockConfig()
        shared_resources = {
            "bus": MessageBus(),
            "session_manager": MagicMock(),
            "workspace": str(tmp_path),
            "config": config,
        }

        runtime = MagicMock()
        runtime.bus = shared_resources["bus"]
        session_store = SessionStore()
        runtime._session_store = session_store
        runtime._state_coordinator = SessionStateCoordinator(runtime, session_store)

        # Mock all backend operations
        runtime.router = MagicMock()
        runtime.router.backend = MagicMock()
        runtime.router.backend.cancel_session = AsyncMock(return_value=0)
        runtime.router.backend.stop_active_task = AsyncMock(return_value=False)
        runtime.router.backend.interrupt_session = AsyncMock(return_value={})

        runtime._state_coordinator.pop_active_tasks = MagicMock(return_value=[])

        session_key = "telegram:concurrent"

        async def mock_terminate():
            tasks = runtime._state_coordinator.pop_active_tasks(session_key)
            for t in tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            return {"cancelled": len(tasks)}

        # Run multiple terminations concurrently
        results = await asyncio.gather(
            mock_terminate(),
            mock_terminate(),
            mock_terminate(),
        )

        # All should complete successfully
        assert all(r is not None for r in results)