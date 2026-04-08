"""Tests for CrewResourceManager: unified cleanup flow.

This module tests the async context manager that ensures all cleanup
runs even when CancelledError or other exceptions occur.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.crew.models import AgentRole, TaskResult
from xbot.crew.resource_manager import CrewResourceManager
from xbot.crew.state import CrewPhase, CrewStateManager


class TestCrewResourceManagerBasics:
    """Basic tests for CrewResourceManager."""

    def test_init_stores_config(self):
        """ResourceManager should store configuration."""
        crew_config = MagicMock()
        crew_config.name = "test_crew"
        xbot_config = MagicMock()
        permission_handler = MagicMock()
        state_manager = MagicMock()
        started_at = datetime.now()

        manager = CrewResourceManager(
            crew_config=crew_config,
            xbot_config=xbot_config,
            permission_handler=permission_handler,
            state_manager=state_manager,
            started_at=started_at,
        )

        assert manager.crew_config == crew_config
        assert manager.xbot_config == xbot_config
        assert manager.permission_handler == permission_handler
        assert manager.state_manager == state_manager
        assert manager.pool is None
        assert manager.process is None
        assert manager.final_status == "completed"
        assert manager.cancelled_error is None

    @pytest.mark.asyncio
    async def test_context_manager_enter_returns_self(self):
        """__aenter__ should return self."""
        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=MagicMock(),
            started_at=datetime.now(),
        )

        async with manager as m:
            assert m is manager


class TestCrewResourceManagerCleanup:
    """Test cleanup guarantees on various exit conditions."""

    @pytest.mark.asyncio
    async def test_cleanup_on_normal_exit(self):
        """Cleanup should run on normal exit."""
        state_manager = CrewStateManager(
            task_names=["task1"],
            task_definitions=[],
        )

        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=state_manager,
            started_at=datetime.now(),
        )

        # Mock pool and process
        mock_pool = MagicMock()
        mock_pool.shutdown = AsyncMock()
        manager.pool = mock_pool
        manager._pool_initialized = True

        mock_process = MagicMock()
        mock_process.finalize_output = MagicMock()
        manager.process = mock_process

        async with manager:
            # Normal execution
            manager.set_results([
                TaskResult(
                    task_name="task1",
                    agent_name="agent1",
                    output="done",
                    status="success",
                    started_at=datetime.now(),
                    finished_at=datetime.now(),
                )
            ])

        # Cleanup should have run
        mock_pool.shutdown.assert_called_once()
        mock_process.finalize_output.assert_called_once()
        assert manager.final_status == "completed"

    @pytest.mark.asyncio
    async def test_cleanup_on_cancelled_error(self):
        """Cleanup should run even when CancelledError occurs."""
        state_manager = CrewStateManager(
            task_names=["task1"],
            task_definitions=[],
        )

        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=state_manager,
            started_at=datetime.now(),
        )

        # Mock pool and process
        mock_pool = MagicMock()
        mock_pool.shutdown = AsyncMock()
        manager.pool = mock_pool
        manager._pool_initialized = True

        mock_process = MagicMock()
        mock_process.finalize_output = MagicMock()
        manager.process = mock_process

        # Execute with CancelledError
        with pytest.raises(asyncio.CancelledError):
            async with manager:
                raise asyncio.CancelledError()

        # Cleanup should have run despite cancellation
        mock_pool.shutdown.assert_called_once()
        mock_process.finalize_output.assert_called_once_with("aborted")
        assert manager.final_status == "aborted"
        assert manager.cancelled_error is not None
        assert manager.should_re_raise_cancelled()

    @pytest.mark.asyncio
    async def test_cleanup_on_keyboard_interrupt(self):
        """Cleanup should run on KeyboardInterrupt."""
        state_manager = CrewStateManager(
            task_names=["task1"],
            task_definitions=[],
        )

        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=state_manager,
            started_at=datetime.now(),
        )

        # Mock pool and process
        mock_pool = MagicMock()
        mock_pool.shutdown = AsyncMock()
        manager.pool = mock_pool
        manager._pool_initialized = True

        mock_process = MagicMock()
        mock_process.finalize_output = MagicMock()
        manager.process = mock_process

        with pytest.raises(KeyboardInterrupt):
            async with manager:
                raise KeyboardInterrupt()

        # Cleanup should have run
        mock_pool.shutdown.assert_called_once()
        mock_process.finalize_output.assert_called_once_with("aborted")
        assert manager.final_status == "aborted"

    @pytest.mark.asyncio
    async def test_cleanup_on_exception(self):
        """Cleanup should run on generic exception."""
        state_manager = CrewStateManager(
            task_names=["task1"],
            task_definitions=[],
        )

        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=state_manager,
            started_at=datetime.now(),
        )

        # Mock pool and process
        mock_pool = MagicMock()
        mock_pool.shutdown = AsyncMock()
        manager.pool = mock_pool
        manager._pool_initialized = True

        mock_process = MagicMock()
        mock_process.finalize_output = MagicMock()
        manager.process = mock_process

        with pytest.raises(RuntimeError):
            async with manager:
                raise RuntimeError("Task execution failed")

        # Cleanup should have run
        mock_pool.shutdown.assert_called_once()
        mock_process.finalize_output.assert_called_once_with("failed")
        assert manager.final_status == "failed"

    @pytest.mark.asyncio
    async def test_cleanup_continues_on_cleanup_error(self):
        """Cleanup should continue even if cleanup itself fails."""
        state_manager = CrewStateManager(
            task_names=["task1"],
            task_definitions=[],
        )

        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=state_manager,
            started_at=datetime.now(),
        )

        # Mock pool that raises on shutdown
        mock_pool = MagicMock()
        mock_pool.shutdown = AsyncMock(side_effect=RuntimeError("Shutdown error"))
        manager.pool = mock_pool
        manager._pool_initialized = True

        mock_process = MagicMock()
        mock_process.finalize_output = MagicMock()
        manager.process = mock_process

        # Should not raise cleanup error, should raise original exception
        with pytest.raises(RuntimeError, match="Task execution failed"):
            async with manager:
                raise RuntimeError("Task execution failed")

        # Both cleanup attempts should have been made
        mock_pool.shutdown.assert_called_once()
        mock_process.finalize_output.assert_called_once()


class TestCrewResourceManagerPoolInit:
    """Test pool initialization within context."""

    @pytest.mark.asyncio
    async def test_initialize_pool_success(self):
        """Pool initialization should work correctly."""
        crew_config = MagicMock()
        crew_config.name = "test_crew"
        crew_config.agents = {
            "agent1": AgentRole(name="agent1", description="Test", goal="Test")
        }

        state_manager = CrewStateManager(
            task_names=["task1"],
            task_definitions=[],
        )
        state_manager.transition_crew(CrewPhase.INITIALIZING)

        manager = CrewResourceManager(
            crew_config=crew_config,
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=state_manager,
            started_at=datetime.now(),
        )

        with patch("xbot.crew.agent_pool.AgentPool") as mock_pool_cls:
            mock_pool = MagicMock()
            mock_pool.initialize = AsyncMock()
            mock_pool_cls.return_value = mock_pool

            async with manager:
                await manager.initialize_pool()

                assert manager.pool is mock_pool
                assert manager._pool_initialized
                assert state_manager.crew_phase == CrewPhase.RUNNING

    @pytest.mark.asyncio
    async def test_initialize_pool_with_only_roles(self):
        """Pool init should pass only_roles for checkpoint resume."""
        crew_config = MagicMock()

        state_manager = CrewStateManager(
            task_names=["task1"],
            task_definitions=[],
        )
        state_manager.transition_crew(CrewPhase.INITIALIZING)

        manager = CrewResourceManager(
            crew_config=crew_config,
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=state_manager,
            started_at=datetime.now(),
        )

        with patch("xbot.crew.agent_pool.AgentPool") as mock_pool_cls:
            mock_pool = MagicMock()
            mock_pool.initialize = AsyncMock()
            mock_pool_cls.return_value = mock_pool

            async with manager:
                await manager.initialize_pool(only_roles={"agent1"})

                mock_pool.initialize.assert_called_once_with(only_roles={"agent1"})


class TestCrewResourceManagerStatus:
    """Test final status determination."""

    @pytest.mark.asyncio
    async def test_status_completed_on_success(self):
        """Status should be 'completed' when all tasks succeed."""
        state_manager = CrewStateManager(
            task_names=["task1"],
            task_definitions=[],
        )
        state_manager.transition_crew(CrewPhase.INITIALIZING)

        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=state_manager,
            started_at=datetime.now(),
        )

        mock_pool = MagicMock()
        mock_pool.shutdown = AsyncMock()
        manager.pool = mock_pool
        manager._pool_initialized = True

        mock_process = MagicMock()
        mock_process.finalize_output = MagicMock()
        manager.process = mock_process

        async with manager:
            manager.set_results([
                TaskResult(
                    task_name="task1",
                    agent_name="agent1",
                    output="done",
                    status="success",
                    started_at=datetime.now(),
                    finished_at=datetime.now(),
                )
            ])

        assert manager.final_status == "completed"

    @pytest.mark.asyncio
    async def test_status_failed_on_task_failure(self):
        """Status should be 'failed' when a task fails."""
        state_manager = CrewStateManager(
            task_names=["task1"],
            task_definitions=[],
        )
        state_manager.transition_crew(CrewPhase.INITIALIZING)

        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=state_manager,
            started_at=datetime.now(),
        )

        mock_pool = MagicMock()
        mock_pool.shutdown = AsyncMock()
        manager.pool = mock_pool
        manager._pool_initialized = True

        mock_process = MagicMock()
        mock_process.finalize_output = MagicMock()
        manager.process = mock_process

        async with manager:
            manager.set_results([
                TaskResult(
                    task_name="task1",
                    agent_name="agent1",
                    output="error",
                    status="failed",
                    started_at=datetime.now(),
                    finished_at=datetime.now(),
                )
            ])

        assert manager.final_status == "failed"

    @pytest.mark.asyncio
    async def test_status_aborted_on_human_rejected(self):
        """Status should be 'aborted' when human rejects."""
        state_manager = CrewStateManager(
            task_names=["task1"],
            task_definitions=[],
        )
        state_manager.transition_crew(CrewPhase.INITIALIZING)

        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=state_manager,
            started_at=datetime.now(),
        )

        mock_pool = MagicMock()
        mock_pool.shutdown = AsyncMock()
        manager.pool = mock_pool
        manager._pool_initialized = True

        mock_process = MagicMock()
        mock_process.finalize_output = MagicMock()
        manager.process = mock_process

        async with manager:
            manager.set_results([
                TaskResult(
                    task_name="task1",
                    agent_name="agent1",
                    output="rejected",
                    status="human_rejected",
                    started_at=datetime.now(),
                    finished_at=datetime.now(),
                )
            ])

        assert manager.final_status == "aborted"


class TestCrewResourceManagerStateTransitions:
    """Test state transitions during cleanup."""

    @pytest.mark.asyncio
    async def test_cancelled_transitions_to_aborted(self):
        """CancelledError should transition to ABORTING -> ABORTED."""
        state_manager = CrewStateManager(
            task_names=["task1"],
            task_definitions=[],
        )
        state_manager.transition_crew(CrewPhase.INITIALIZING)
        state_manager.transition_crew(CrewPhase.RUNNING)

        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=state_manager,
            started_at=datetime.now(),
        )

        mock_pool = MagicMock()
        mock_pool.shutdown = AsyncMock()
        manager.pool = mock_pool
        manager._pool_initialized = True

        mock_process = MagicMock()
        mock_process.finalize_output = MagicMock()
        manager.process = mock_process

        with pytest.raises(asyncio.CancelledError):
            async with manager:
                raise asyncio.CancelledError()

        assert state_manager.crew_phase == CrewPhase.ABORTED

    @pytest.mark.asyncio
    async def test_exception_transitions_to_failed(self):
        """Exception should transition to FAILED."""
        state_manager = CrewStateManager(
            task_names=["task1"],
            task_definitions=[],
        )
        state_manager.transition_crew(CrewPhase.INITIALIZING)
        state_manager.transition_crew(CrewPhase.RUNNING)

        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=state_manager,
            started_at=datetime.now(),
        )

        mock_pool = MagicMock()
        mock_pool.shutdown = AsyncMock()
        manager.pool = mock_pool
        manager._pool_initialized = True

        mock_process = MagicMock()
        mock_process.finalize_output = MagicMock()
        manager.process = mock_process

        with pytest.raises(RuntimeError):
            async with manager:
                raise RuntimeError("Task failed")

        assert state_manager.crew_phase == CrewPhase.FAILED


class TestCrewResourceManagerHelpers:
    """Test helper methods."""

    def test_set_process(self):
        """set_process should store the process."""
        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=MagicMock(),
            started_at=datetime.now(),
        )

        mock_process = MagicMock()
        manager.set_process(mock_process)

        assert manager.process == mock_process

    def test_set_results(self):
        """set_results should store results."""
        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=MagicMock(),
            started_at=datetime.now(),
        )

        results = [
            TaskResult(
                task_name="task1",
                agent_name="agent1",
                output="done",
                status="success",
                started_at=datetime.now(),
                finished_at=datetime.now(),
            )
        ]
        manager.set_results(results)

        assert manager.results == results

    def test_get_cancelled_error(self):
        """get_cancelled_error should return captured error."""
        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=MagicMock(),
            started_at=datetime.now(),
        )

        error = asyncio.CancelledError()
        manager.cancelled_error = error

        assert manager.get_cancelled_error() == error

    def test_should_re_raise_cancelled(self):
        """should_re_raise_cancelled should return True if error captured."""
        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=MagicMock(),
            started_at=datetime.now(),
        )

        assert manager.should_re_raise_cancelled() is False

        manager.cancelled_error = asyncio.CancelledError()
        assert manager.should_re_raise_cancelled() is True


class TestCrewResourceManagerNoResources:
    """Test cleanup when no resources were initialized."""

    @pytest.mark.asyncio
    async def test_no_pool_no_cleanup(self):
        """No pool shutdown should be attempted if pool not initialized."""
        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=MagicMock(),
            started_at=datetime.now(),
        )

        # Enter and exit without initializing pool
        async with manager:
            pass

        # No exception should occur
        assert manager.final_status == "completed"

    @pytest.mark.asyncio
    async def test_pool_init_failed_no_shutdown(self):
        """If pool init fails, shutdown should not be called."""
        manager = CrewResourceManager(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
            state_manager=MagicMock(),
            started_at=datetime.now(),
        )

        mock_pool = MagicMock()
        mock_pool.shutdown = AsyncMock()
        manager.pool = mock_pool
        manager._pool_initialized = False  # Not initialized

        mock_process = MagicMock()
        mock_process.finalize_output = MagicMock()
        manager.process = mock_process

        async with manager:
            # Pool exists but wasn't initialized
            pass

        # Shutdown should NOT be called since _pool_initialized is False
        mock_pool.shutdown.assert_not_called()
