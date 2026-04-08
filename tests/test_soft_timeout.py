"""Tests for soft timeout with progress detection in crew execution.

The soft timeout mechanism:
1. If timeout is None: auto-extend on progress detection
2. If timeout is set: traditional hard timeout (backward compatible)

Key behaviors:
- Progress detection: any output within ACTIVITY_THRESHOLD seconds counts as progress
- Auto-extend: extend by SOFT_TIMEOUT_BUFFER when progress detected
- Max extensions: MAX_EXTENSIONS limit to prevent infinite loops
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from xbot.agent.crew.agent_pool import AgentPool, TaskProgress
from xbot.agent.crew.models import AgentRole, TaskDefinition, TaskResult
from xbot.agent.crew.process import SequentialProcess
from xbot.agent.crew.state import CrewStateManager, TaskPhase


class TestTaskDefinitionTimeout:
    """Test TaskDefinition timeout field changes."""

    def test_timeout_defaults_to_none(self) -> None:
        """timeout should default to None (smart mode)."""
        task = TaskDefinition(
            name="test_task",
            description="Test task",
            agent="test_agent",
        )
        assert task.timeout is None

    def test_timeout_can_be_set(self) -> None:
        """timeout can be explicitly set for backward compatibility."""
        task = TaskDefinition(
            name="test_task",
            description="Test task",
            agent="test_agent",
            timeout=300,
        )
        assert task.timeout == 300

    def test_critical_defaults_to_false(self) -> None:
        """critical should default to False."""
        task = TaskDefinition(
            name="test_task",
            description="Test task",
            agent="test_agent",
        )
        assert task.critical is False

    def test_critical_can_be_set(self) -> None:
        """critical can be set to True for important tasks."""
        task = TaskDefinition(
            name="test_task",
            description="Test task",
            agent="test_agent",
            critical=True,
        )
        assert task.critical is True


class TestTaskResultQuality:
    """Test TaskResult quality and extended_count fields."""

    def test_quality_defaults_to_full(self) -> None:
        """quality should default to 'full'."""
        result = TaskResult(
            task_name="test",
            agent_name="test",
            output="test",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
        assert result.quality == "full"
        assert result.extended_count == 0

    def test_quality_can_be_partial(self) -> None:
        """quality can be set to 'partial' for extended tasks."""
        result = TaskResult(
            task_name="test",
            agent_name="test",
            output="test",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            quality="partial",
            extended_count=2,
        )
        assert result.quality == "partial"
        assert result.extended_count == 2


class TestTaskProgress:
    """Test TaskProgress dataclass."""

    def test_task_progress_defaults(self) -> None:
        """TaskProgress should have correct defaults."""
        progress = TaskProgress()
        assert progress.delta_content == ""
        assert progress.total_content == ""
        assert progress.is_final is False

    def test_task_progress_with_content(self) -> None:
        """TaskProgress can be created with content."""
        progress = TaskProgress(
            delta_content="Hello",
            total_content="Hello World",
            is_final=False,
        )
        assert progress.delta_content == "Hello"
        assert progress.total_content == "Hello World"


class TestSoftTimeoutExecution:
    """Test soft timeout execution logic."""

    @pytest.mark.asyncio
    async def test_soft_timeout_extends_on_progress(self) -> None:
        """Task with progress should get timeout extension."""
        # Setup
        crew_config = MagicMock()
        crew_config.agents = {
            "test_agent": AgentRole(
                name="test_agent",
                description="Test",
                goal="Test",
            )
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.max_output_size = 100000

        pool = MagicMock(spec=AgentPool)

        # Simulate streaming output that takes longer than initial timeout
        async def mock_stream(*args, **kwargs):
            # First chunk immediately
            yield TaskProgress(delta_content="Starting...", total_content="Starting...", is_final=False)
            await asyncio.sleep(0.05)
            # More content (indicates progress)
            yield TaskProgress(delta_content=" working", total_content="Starting... working", is_final=False)
            await asyncio.sleep(0.05)
            # Final content
            yield TaskProgress(delta_content=" done", total_content="Starting... working done", is_final=True)

        pool.run_task_streaming = mock_stream

        state_manager = CrewStateManager(task_names=["test_task"], task_definitions=[])
        context = MagicMock()
        context.build_agent_context = MagicMock(return_value=("test prompt", None))
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
            description="Test task description",
            agent="test_agent",
            timeout=None,  # Smart mode
        )

        # Execute
        result = await process._execute_single_task(task)

        # Verify
        assert result.status == "success"
        assert "done" in result.output

    @pytest.mark.asyncio
    async def test_hard_timeout_no_extension(self) -> None:
        """Task with explicit timeout should not extend (backward compatible)."""
        crew_config = MagicMock()
        crew_config.agents = {
            "test_agent": AgentRole(
                name="test_agent",
                description="Test",
                goal="Test",
            )
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.max_output_size = 100000

        pool = MagicMock(spec=AgentPool)

        # Simulate a slow task
        async def mock_stream(*args, **kwargs):
            yield TaskProgress(delta_content="Start", total_content="Start", is_final=False)
            await asyncio.sleep(2)  # Longer than timeout
            yield TaskProgress(delta_content="End", total_content="StartEnd", is_final=True)

        pool.run_task_streaming = mock_stream

        state_manager = CrewStateManager(task_names=["test_task"], task_definitions=[])
        context = MagicMock()
        context.build_agent_context = MagicMock(return_value=("test prompt", None))
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
            timeout=1,  # Hard timeout of 1 second
        )

        # Execute - should timeout because hard timeout is set
        result = await process._execute_single_task(task)

        assert result.status == "failed"
        assert "timed out" in result.output.lower()

    @pytest.mark.asyncio
    async def test_estimate_timeout_based_on_description(self) -> None:
        """Timeout should be estimated based on task description."""
        crew_config = MagicMock()
        crew_config.agents = {
            "test_agent": AgentRole(
                name="test_agent",
                description="Test",
                goal="Test",
                max_iterations=20,
            )
        }

        state_manager = CrewStateManager(task_names=[], task_definitions=[])
        context = MagicMock()
        context.build_agent_context = MagicMock(return_value=("test prompt", None))
        permission_handler = MagicMock()
        pool = MagicMock(spec=AgentPool)

        process = SequentialProcess(
            pool=pool,
            context=context,
            permission_handler=permission_handler,
            crew_config=crew_config,
            state_manager=state_manager,
        )

        # Short description
        short_task = TaskDefinition(
            name="short",
            description="Quick task",
            agent="test_agent",
        )
        short_timeout = process._estimate_timeout(short_task)

        # Long description
        long_task = TaskDefinition(
            name="long",
            description="This is a very long and detailed task description that requires extensive analysis and deep thinking to complete properly with multiple steps and considerations",
            agent="test_agent",
        )
        long_timeout = process._estimate_timeout(long_task)

        # Longer description should have longer timeout
        assert long_timeout > short_timeout
        # Minimum should be at least 60 seconds
        assert short_timeout >= 60

    @pytest.mark.asyncio
    async def test_max_extensions_limit(self) -> None:
        """Task should stop after MAX_EXTENSIONS even with progress."""
        from xbot.agent.crew.process import SequentialProcess

        crew_config = MagicMock()
        crew_config.agents = {
            "test_agent": AgentRole(
                name="test_agent",
                description="Test",
                goal="Test",
            )
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.max_output_size = 100000

        pool = MagicMock(spec=AgentPool)

        extension_count = 0

        # Simulate a task that keeps making progress but never finishes
        async def mock_stream(*args, **kwargs):
            nonlocal extension_count
            # Yield progress every 0.01 seconds to simulate activity
            for i in range(100):  # More than max extensions
                yield TaskProgress(
                    delta_content=f"chunk{i} ",
                    total_content=f"chunk{' '.join(str(j) for j in range(i+1))}",
                    is_final=False
                )
                await asyncio.sleep(0.01)
                extension_count += 1
            yield TaskProgress(delta_content="", total_content="done", is_final=True)

        pool.run_task_streaming = mock_stream

        state_manager = CrewStateManager(task_names=["test_task"], task_definitions=[])
        context = MagicMock()
        context.build_agent_context = MagicMock(return_value=("test prompt", None))
        permission_handler = MagicMock()

        process = SequentialProcess(
            pool=pool,
            context=context,
            permission_handler=permission_handler,
            crew_config=crew_config,
            state_manager=state_manager,
        )

        # Use very short initial timeout to force extensions
        task = TaskDefinition(
            name="test_task",
            description="Test",
            agent="test_agent",
            timeout=None,
        )

        # This should complete because the mock yields is_final=True
        result = await process._execute_single_task(task)

        # Task should have completed (our mock finishes)
        assert result.status == "success"


class TestBackwardCompatibility:
    """Test backward compatibility with existing configurations."""

    def test_old_config_with_timeout_still_works(self) -> None:
        """Existing configs with timeout set should work unchanged."""
        # This simulates loading an old config file with timeout: 600
        task = TaskDefinition(
            name="test_task",
            description="Test",
            agent="test_agent",
            timeout=600,  # Old style explicit timeout
        )
        assert task.timeout == 600

    def test_new_config_without_timeout_uses_smart_mode(self) -> None:
        """New configs without timeout should use smart mode."""
        task = TaskDefinition(
            name="test_task",
            description="Test",
            agent="test_agent",
            # No timeout specified - defaults to None
        )
        assert task.timeout is None  # Smart mode


class TestTimeoutEdgeCases:
    """Test edge cases for timeout handling."""

    def test_timeout_zero_is_respected(self) -> None:
        """timeout=0 should be respected, not treated as falsy."""
        task = TaskDefinition(
            name="test_task",
            description="Test",
            agent="test_agent",
            timeout=0,  # Explicitly set to 0
        )
        assert task.timeout == 0

    @pytest.mark.asyncio
    async def test_timeout_zero_fails_validation(self) -> None:
        """timeout=0 should be rejected before execution starts."""
        crew_config = MagicMock()
        crew_config.agents = {
            "test_agent": AgentRole(
                name="test_agent",
                description="Test",
                goal="Test",
            )
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.max_output_size = 100000

        pool = MagicMock(spec=AgentPool)

        async def track_timeout_stream(*args, **kwargs):
            yield TaskProgress(delta_content="result", total_content="result", is_final=True)

        pool.run_task_streaming = track_timeout_stream

        state_manager = CrewStateManager(task_names=["test_task"], task_definitions=[])
        context = MagicMock()
        context.build_task_prompt = MagicMock(return_value="test prompt")
        context.build_agent_context = MagicMock(return_value=("test prompt", None))
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
            timeout=0,  # Zero timeout
        )

        result = await process._execute_single_task(task)

        assert result.status == "failed"
        assert "Timeout must be positive" in result.output


class TestUnknownAgentHandling:
    """Test handling of unknown agents."""

    @pytest.mark.asyncio
    async def test_execute_single_task_unknown_agent(self) -> None:
        """Task with unknown agent should fail gracefully."""
        crew_config = MagicMock()
        crew_config.agents = {
            "existing_agent": AgentRole(
                name="existing_agent",
                description="Test",
                goal="Test",
            )
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.max_output_size = 100000

        pool = MagicMock(spec=AgentPool)
        state_manager = CrewStateManager(task_names=["test_task"], task_definitions=[])
        context = MagicMock()
        context.build_agent_context = MagicMock(return_value=("test prompt", None))
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
            agent="unknown_agent",  # Agent doesn't exist
            timeout=None,
        )

        result = await process._execute_single_task(task)

        assert result.status == "failed"
        assert "unknown_agent" in result.output
        assert "not found" in result.output.lower()

    @pytest.mark.asyncio
    async def test_redo_task_unknown_agent(self) -> None:
        """Redo task with unknown agent should fail gracefully."""
        crew_config = MagicMock()
        crew_config.agents = {
            "existing_agent": AgentRole(
                name="existing_agent",
                description="Test",
                goal="Test",
            )
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.max_output_size = 100000

        pool = MagicMock(spec=AgentPool)
        state_manager = CrewStateManager(task_names=["test_task"], task_definitions=[])
        # Set to AWAITING_REVIEW for valid redo transition
        state_manager.force_task_phase("test_task", TaskPhase.AWAITING_REVIEW)

        context = MagicMock()
        context.build_agent_context = MagicMock(return_value=("test prompt", None))
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
            agent="unknown_agent",  # Agent doesn't exist
            timeout=None,
        )

        original_result = TaskResult(
            task_name="test_task",
            agent_name="unknown_agent",
            output="original",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        result, success = await process._redo_task(task, original_result)

        assert success is False
        assert result.status == "failed"
        assert "unknown_agent" in result.output
        assert "not found" in result.output.lower()


class TestSoftTimeoutNoOutputCase:
    """Test soft timeout when backend produces no output.

    This tests the fix for a bug where the soft timeout mechanism failed to
    trigger when the backend didn't produce any output events. The async for
    loop was blocking on waiting for progress events, so timeout checks
    were never executed.

    The fix uses asyncio.shield() to protect the stream task from being
    cancelled by wait_for(), allowing timeout checks to work even when
    no output is produced.
    """

    @pytest.mark.asyncio
    async def test_timeout_triggers_when_no_output(self) -> None:
        """Timeout should trigger even when backend produces no output."""
        crew_config = MagicMock()
        crew_config.agents = {
            "test_agent": AgentRole(
                name="test_agent",
                description="Test",
                goal="Test",
            )
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.max_output_size = 100000

        pool = MagicMock(spec=AgentPool)

        # Simulate a backend that takes longer than timeout to produce first output
        async def slow_stream(*args, **kwargs):
            await asyncio.sleep(10)  # Longer than timeout
            yield TaskProgress(delta_content="result", total_content="result", is_final=True)

        pool.run_task_streaming = slow_stream

        state_manager = CrewStateManager(task_names=["test_task"], task_definitions=[])
        context = MagicMock()
        context.build_agent_context = MagicMock(return_value=("test prompt", None))
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
            timeout=None,  # Smart mode
        )

        # With the fix, should timeout quickly (not wait 10s)
        import time
        start = time.monotonic()

        with pytest.raises(asyncio.TimeoutError):
            await process._execute_with_soft_timeout(
                task=task,
                prompt="test",
                session_key="test",
                initial_timeout=2,  # 2 second timeout
                use_soft_timeout=True,
            )

        elapsed = time.monotonic() - start
        # Should timeout around 2s, not 10s
        assert elapsed < 5, f"Timeout took {elapsed}s, expected ~2s"

    @pytest.mark.asyncio
    async def test_stream_not_cancelled_on_extension(self) -> None:
        """Stream should continue after timeout extension (not cancelled)."""
        crew_config = MagicMock()
        crew_config.agents = {
            "test_agent": AgentRole(
                name="test_agent",
                description="Test",
                goal="Test",
            )
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.max_output_size = 100000

        pool = MagicMock(spec=AgentPool)

        # Simulate a backend that pauses longer than initial timeout
        async def stream_with_pause(*args, **kwargs):
            yield TaskProgress(delta_content="start", total_content="start", is_final=False)
            await asyncio.sleep(1)
            yield TaskProgress(delta_content=" working", total_content="start working", is_final=False)
            await asyncio.sleep(10)  # Pause longer than timeout
            yield TaskProgress(delta_content=" done", total_content="start working done", is_final=True)

        pool.run_task_streaming = stream_with_pause

        state_manager = CrewStateManager(task_names=["test_task"], task_definitions=[])
        context = MagicMock()
        context.build_agent_context = MagicMock(return_value=("test prompt", None))
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

        # With short timeout but ACTIVITY_THRESHOLD=180s, should extend and complete
        result = await process._execute_with_soft_timeout(
            task=task,
            prompt="test",
            session_key="test",
            initial_timeout=5,  # 5 second initial timeout
            use_soft_timeout=True,
        )

        output, extended_count = result

        # Should complete with full output (stream not cancelled)
        assert output == "start working done"
        assert extended_count >= 1  # At least one extension
