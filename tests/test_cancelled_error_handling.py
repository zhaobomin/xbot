"""Tests for asyncio.CancelledError handling in crew execution.

CancelledError is a BaseException subclass, not Exception, so it won't be
caught by `except Exception` blocks. These tests verify proper handling.

See: https://docs.python.org/3/library/asyncio-exceptions.html#asyncio.CancelledError
"""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from xbot.agent.crew.agent_pool import AgentPool
from xbot.agent.crew.orchestrator import CrewOrchestrator
from xbot.agent.crew.process import SequentialProcess
from xbot.agent.crew.state import CrewPhase, CrewStateManager, TaskPhase
from xbot.agent.crew.models import CrewConfig, TaskDefinition, TaskResult, ProcessType


class MockBackend:
    """Mock backend for testing."""

    async def shutdown(self) -> None:
        pass


class MockPermissionHandler:
    """Mock permission handler."""

    async def request_interaction(
        self, kind: str, prompt: str, suggestions: list = None, session_key: str = ""
    ):
        return MagicMock(content="continue")


class TestOrchestratorCancelledError:
    """Test orchestrator handles CancelledError correctly."""

    def _make_minimal_crew_config(self) -> CrewConfig:
        """Create minimal crew config for testing."""
        return CrewConfig(
            name="test_crew",
            process=ProcessType.sequential,
            agents={},
            tasks=[],
            workspace="/tmp/test_crew",
        )

    @pytest.mark.asyncio
    async def test_orchestrator_catches_cancelled_error(self) -> None:
        """CancelledError should be caught, logged, and re-raised."""
        crew_config = self._make_minimal_crew_config()
        xbot_config = MagicMock()
        permission_handler = MockPermissionHandler()

        orchestrator = CrewOrchestrator(
            crew_config, xbot_config, permission_handler
        )

        # Mock the process to raise CancelledError
        with patch.object(orchestrator, "_get_llm_repair_callable", return_value=None):
            with patch("xbot.agent.crew.orchestrator.AgentPool") as mock_pool_cls:
                mock_pool = MagicMock()
                mock_pool.initialize = AsyncMock()
                mock_pool.shutdown = AsyncMock()

                # Make execute raise CancelledError
                mock_process = MagicMock()
                mock_process.execute = AsyncMock(side_effect=asyncio.CancelledError())
                mock_process.finalize_output = MagicMock()

                mock_pool_cls.return_value = mock_pool

                with patch("xbot.agent.crew.orchestrator.SequentialProcess", return_value=mock_process):
                    with patch("xbot.agent.crew.orchestrator.CrewStateManager") as mock_state_cls:
                        mock_state = MagicMock()
                        mock_state.crew_phase = CrewPhase.ABORTED
                        mock_state_cls.return_value = mock_state

                        # Should catch CancelledError and re-raise
                        with pytest.raises(asyncio.CancelledError):
                            await orchestrator.run()

                        # Verify shutdown was called (finally block)
                        mock_pool.shutdown.assert_called_once()

                        # Verify state transition was attempted
                        mock_state.transition_crew.assert_called()

    @pytest.mark.asyncio
    async def test_orchestrator_state_transitions_on_cancel(self) -> None:
        """State should transition to ABORTING and ABORTED on cancellation."""
        crew_config = self._make_minimal_crew_config()
        xbot_config = MagicMock()
        permission_handler = MockPermissionHandler()

        orchestrator = CrewOrchestrator(
            crew_config, xbot_config, permission_handler
        )

        state_manager = CrewStateManager(task_names=[], task_definitions=[])

        with patch.object(orchestrator, "_get_llm_repair_callable", return_value=None):
            with patch("xbot.agent.crew.orchestrator.AgentPool") as mock_pool_cls:
                mock_pool = MagicMock()
                mock_pool.initialize = AsyncMock()
                mock_pool.shutdown = AsyncMock()

                mock_process = MagicMock()
                mock_process.execute = AsyncMock(side_effect=asyncio.CancelledError())
                mock_process.finalize_output = MagicMock()

                mock_pool_cls.return_value = mock_pool

                with patch("xbot.agent.crew.orchestrator.SequentialProcess", return_value=mock_process):
                    with patch("xbot.agent.crew.orchestrator.CrewStateManager", return_value=state_manager):
                        with pytest.raises(asyncio.CancelledError):
                            await orchestrator.run()

                        # Verify state transitions
                        assert state_manager.crew_phase == CrewPhase.ABORTED


class TestProcessCancelledError:
    """Test process handles CancelledError in task execution."""

    @pytest.mark.asyncio
    async def test_execute_single_task_catches_cancelled(self) -> None:
        """Task execution should catch CancelledError and re-raise."""
        from xbot.agent.crew.agent_pool import TaskProgress

        pool = MagicMock()
        # Make run_task_streaming raise CancelledError
        async def mock_stream_with_cancel(*args, **kwargs):
            yield TaskProgress(delta_content="start", total_content="start", is_final=False)
            raise asyncio.CancelledError()

        pool.run_task_streaming = mock_stream_with_cancel

        context = MagicMock()
        context.build_task_prompt = MagicMock(return_value="test prompt")

        permission_handler = MockPermissionHandler()
        crew_config = MagicMock()
        crew_config.output.enabled = False
        crew_config.output.max_output_size = 100000
        crew_config.max_context_length = 4000
        crew_config.agents = {
            "test_agent": MagicMock(max_iterations=20)
        }

        state_manager = CrewStateManager(
            task_names=["test_task"],
            task_definitions=[],
        )

        process = SequentialProcess(
            pool=pool,
            context=context,
            permission_handler=permission_handler,
            crew_config=crew_config,
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="test_task",
            agent="test_agent",
            description="Test task",
            timeout=60,
        )

        # Should re-raise CancelledError
        with pytest.raises(asyncio.CancelledError):
            await process._execute_single_task(task)


class TestAgentPoolCancelledError:
    """Test agent pool handles CancelledError during shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_continues_on_cancelled_error(self) -> None:
        """Shutdown should complete all backends even if some are cancelled."""
        pool = AgentPool(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
        )

        # Create mock backends
        backend1 = AsyncMock()
        backend1.shutdown = AsyncMock(side_effect=asyncio.CancelledError())

        backend2 = AsyncMock()
        backend2.shutdown = AsyncMock()

        backend3 = AsyncMock()
        backend3.shutdown = AsyncMock()

        pool._backends = {
            "role1": backend1,
            "role2": backend2,
            "role3": backend3,
        }

        # Should complete shutdown of all backends, then re-raise
        with pytest.raises(asyncio.CancelledError):
            await pool.shutdown()

        # All backends should have been called
        backend1.shutdown.assert_called_once()
        backend2.shutdown.assert_called_once()
        backend3.shutdown.assert_called_once()

        # Pool should be cleared
        assert len(pool._backends) == 0

    @pytest.mark.asyncio
    async def test_shutdown_multiple_cancelled_errors(self) -> None:
        """Shutdown should handle multiple CancelledError, re-raise first one."""
        pool = AgentPool(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
        )

        first_cancel = asyncio.CancelledError("first")

        backend1 = AsyncMock()
        backend1.shutdown = AsyncMock(side_effect=first_cancel)

        backend2 = AsyncMock()
        backend2.shutdown = AsyncMock(side_effect=asyncio.CancelledError("second"))

        pool._backends = {
            "role1": backend1,
            "role2": backend2,
        }

        # Should re-raise the first CancelledError
        with pytest.raises(asyncio.CancelledError, match="first"):
            await pool.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_no_cancelled_error(self) -> None:
        """Normal shutdown should work without CancelledError."""
        pool = AgentPool(
            crew_config=MagicMock(),
            xbot_config=MagicMock(),
            permission_handler=MagicMock(),
        )

        backend1 = AsyncMock()
        backend1.shutdown = AsyncMock()

        backend2 = AsyncMock()
        backend2.shutdown = AsyncMock()

        pool._backends = {
            "role1": backend1,
            "role2": backend2,
        }

        # Should complete without raising
        await pool.shutdown()

        # All backends should have been called
        backend1.shutdown.assert_called_once()
        backend2.shutdown.assert_called_once()

        # Pool should be cleared
        assert len(pool._backends) == 0


class TestCancelledErrorHierarchy:
    """Verify CancelledError hierarchy assumptions."""

    def test_cancelled_error_not_exception(self) -> None:
        """CancelledError is BaseException, not Exception."""
        cancel_err = asyncio.CancelledError()

        # Should be BaseException
        assert isinstance(cancel_err, BaseException)

        # Should NOT be Exception
        assert not isinstance(cancel_err, Exception)

    def test_exception_handler_does_not_catch_cancelled(self) -> None:
        """Verify that `except Exception` does not catch CancelledError."""
        caught_by_exception = False
        caught_by_base = False

        try:
            raise asyncio.CancelledError("test")
        except Exception:
            caught_by_exception = True
        except BaseException:
            caught_by_base = True

        # Exception handler should NOT catch it
        assert not caught_by_exception

        # BaseException handler should catch it
        assert caught_by_base


class TestRedoTaskBugFixes:
    """Test fixes for bugs found in _redo_task method."""

    @pytest.mark.asyncio
    async def test_redo_task_timeout_uses_correct_extended_count(self) -> None:
        """When redo times out, extended_count should be 0 (not undefined)."""
        from xbot.agent.crew.agent_pool import TaskProgress
        from xbot.agent.crew.models import AgentRole

        pool = MagicMock()

        # Simulate a stream that takes longer than timeout
        async def slow_stream(*args, **kwargs):
            await asyncio.sleep(10)
            yield TaskProgress(delta_content="result", total_content="result", is_final=True)

        pool.run_task_streaming = slow_stream

        crew_config = MagicMock()
        crew_config.agents = {
            "test_agent": AgentRole(name="test_agent", description="Test", goal="Test")
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.max_output_size = 100000

        state_manager = CrewStateManager(task_names=["test_task"], task_definitions=[])
        # Set task to AWAITING_REVIEW first (required for redo)
        state_manager.force_task_phase("test_task", TaskPhase.AWAITING_REVIEW)

        context = MagicMock()
        context.build_task_prompt = MagicMock(return_value="test prompt")

        permission_handler = MagicMock()
        permission_handler.request_interaction = AsyncMock(return_value=MagicMock(content="feedback"))

        process = SequentialProcess(
            pool=pool,
            context=context,
            permission_handler=permission_handler,
            crew_config=crew_config,
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="test_task",
            description="Test",
            agent="test_agent",
            timeout=2,  # Hard timeout
        )

        original_result = TaskResult(
            task_name="test_task",
            agent_name="test_agent",
            output="original output",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        # Call _redo_task (which will timeout)
        result, success = await process._redo_task(task, original_result)

        # Verify result is valid (no UnboundLocalError)
        assert success is False
        assert result.status == "failed"
        assert result.extended_count == 0  # Should be 0, not undefined
        assert result.quality == "full"  # extended_count == 0 means full

    @pytest.mark.asyncio
    async def test_redo_task_exception_uses_correct_extended_count(self) -> None:
        """When redo raises exception, extended_count should be 0 (not undefined)."""
        from xbot.agent.crew.models import AgentRole

        pool = MagicMock()

        # Simulate a stream that raises an exception
        async def failing_stream(*args, **kwargs):
            raise RuntimeError("Test error")
            yield  # Never reached, but needed for async generator

        pool.run_task_streaming = failing_stream

        crew_config = MagicMock()
        crew_config.agents = {
            "test_agent": AgentRole(name="test_agent", description="Test", goal="Test")
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.max_output_size = 100000

        state_manager = CrewStateManager(task_names=["test_task"], task_definitions=[])
        # Set task to AWAITING_REVIEW first (required for redo)
        state_manager.force_task_phase("test_task", TaskPhase.AWAITING_REVIEW)

        context = MagicMock()
        context.build_task_prompt = MagicMock(return_value="test prompt")

        permission_handler = MagicMock()
        permission_handler.request_interaction = AsyncMock(return_value=MagicMock(content="feedback"))

        process = SequentialProcess(
            pool=pool,
            context=context,
            permission_handler=permission_handler,
            crew_config=crew_config,
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="test_task",
            description="Test",
            agent="test_agent",
            timeout=None,
        )

        original_result = TaskResult(
            task_name="test_task",
            agent_name="test_agent",
            output="original output",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        # Call _redo_task (which will fail with exception)
        result, success = await process._redo_task(task, original_result)

        # Verify result is valid (no UnboundLocalError)
        assert success is False
        assert result.status == "failed"
        assert result.extended_count == 0
        assert result.quality == "full"


class TestExecuteWithSoftTimeoutEdgeCases:
    """Test edge cases in _execute_with_soft_timeout."""

    @pytest.mark.asyncio
    async def test_cancelled_error_clean_up_stream_task(self) -> None:
        """CancelledError should properly clean up the stream task."""
        from xbot.agent.crew.agent_pool import TaskProgress
        from xbot.agent.crew.models import AgentRole

        pool = MagicMock()

        # Track whether stream was cancelled
        stream_cancelled = False

        async def cancellable_stream(*args, **kwargs):
            nonlocal stream_cancelled
            try:
                yield TaskProgress(delta_content="start", total_content="start", is_final=False)
                await asyncio.sleep(100)  # Long sleep
                yield TaskProgress(delta_content="end", total_content="start end", is_final=True)
            except asyncio.CancelledError:
                stream_cancelled = True
                raise

        pool.run_task_streaming = cancellable_stream

        crew_config = MagicMock()
        crew_config.agents = {
            "test_agent": AgentRole(name="test_agent", description="Test", goal="Test")
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.max_output_size = 100000

        state_manager = CrewStateManager(task_names=["test_task"], task_definitions=[])
        context = MagicMock()
        context.build_task_prompt = MagicMock(return_value="test prompt")

        permission_handler = MagicMock()

        process = SequentialProcess(
            pool=pool,
            context=context,
            permission_handler=permission_handler,
            crew_config=crew_config,
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="test_task",
            description="Test",
            agent="test_agent",
            timeout=None,
        )

        # Create a task that will be cancelled
        async def run_and_cancel():
            task_coro = process._execute_with_soft_timeout(
                task=task,
                prompt="test",
                session_key="test",
                initial_timeout=60,
                use_soft_timeout=True,
            )
            exec_task = asyncio.create_task(task_coro)
            await asyncio.sleep(0.1)  # Let it start
            exec_task.cancel()
            try:
                await exec_task
            except asyncio.CancelledError:
                pass

        await run_and_cancel()

        # Give time for cleanup
        await asyncio.sleep(0.1)

        # Stream should have been cancelled
        assert stream_cancelled

    @pytest.mark.asyncio
    async def test_stop_async_iteration_handled(self) -> None:
        """StopAsyncIteration should be handled correctly."""
        from xbot.agent.crew.agent_pool import TaskProgress
        from xbot.agent.crew.models import AgentRole

        pool = MagicMock()

        # Stream that ends early without is_final
        async def early_end_stream(*args, **kwargs):
            yield TaskProgress(delta_content="partial", total_content="partial", is_final=False)
            # Raises StopAsyncIteration implicitly when generator ends

        pool.run_task_streaming = early_end_stream

        crew_config = MagicMock()
        crew_config.agents = {
            "test_agent": AgentRole(name="test_agent", description="Test", goal="Test")
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.max_output_size = 100000

        state_manager = CrewStateManager(task_names=["test_task"], task_definitions=[])
        context = MagicMock()
        context.build_task_prompt = MagicMock(return_value="test prompt")

        permission_handler = MagicMock()

        process = SequentialProcess(
            pool=pool,
            context=context,
            permission_handler=permission_handler,
            crew_config=crew_config,
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="test_task",
            description="Test",
            agent="test_agent",
            timeout=None,
        )

        # Should complete without error
        output, extended_count = await process._execute_with_soft_timeout(
            task=task,
            prompt="test",
            session_key="test",
            initial_timeout=60,
            use_soft_timeout=True,
        )

        assert output == "partial"
        assert extended_count == 0


class TestStateTransitions:
    """Test state machine transitions."""

    def test_retrying_to_running_transition(self) -> None:
        """RETRYING should only transition to RUNNING."""
        state_manager = CrewStateManager(
            task_names=["test_task"],
            task_definitions=[],
        )

        # Force to RETRYING (simulating redo)
        state_manager.force_task_phase("test_task", TaskPhase.RETRYING)

        # Should be able to transition to RUNNING
        state_manager.transition_task("test_task", TaskPhase.RUNNING)
        assert state_manager.get_task_phase("test_task") == TaskPhase.RUNNING

    def test_running_to_failed_after_retrying(self) -> None:
        """After RETRYING -> RUNNING, should be able to transition to FAILED."""
        state_manager = CrewStateManager(
            task_names=["test_task"],
            task_definitions=[],
        )

        # Simulate redo flow
        state_manager.force_task_phase("test_task", TaskPhase.RETRYING)
        state_manager.transition_task("test_task", TaskPhase.RUNNING)
        state_manager.transition_task("test_task", TaskPhase.FAILED)

        assert state_manager.get_task_phase("test_task") == TaskPhase.FAILED

    def test_invalid_transition_raises(self) -> None:
        """Invalid state transitions should raise."""
        from xbot.agent.crew.state import InvalidTransitionError

        state_manager = CrewStateManager(
            task_names=["test_task"],
            task_definitions=[],
        )

        # COMPLETED is terminal, can't transition
        state_manager.force_task_phase("test_task", TaskPhase.COMPLETED)

        with pytest.raises(InvalidTransitionError):
            state_manager.transition_task("test_task", TaskPhase.RUNNING)