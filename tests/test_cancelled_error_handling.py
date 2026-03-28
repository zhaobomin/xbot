"""Tests for asyncio.CancelledError handling in crew execution.

CancelledError is a BaseException subclass, not Exception, so it won't be
caught by `except Exception` blocks. These tests verify proper handling.

See: https://docs.python.org/3/library/asyncio-exceptions.html#asyncio.CancelledError
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from xbot.agent.crew.agent_pool import AgentPool
from xbot.agent.crew.orchestrator import CrewOrchestrator
from xbot.agent.crew.process import SequentialProcess
from xbot.agent.crew.state import CrewPhase, CrewStateManager
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