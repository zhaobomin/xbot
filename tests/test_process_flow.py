"""Tests for process execution flow: human review, Sequential, and Hierarchical."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from xbot.agent.crew.agent_pool import TaskProgress
from xbot.agent.crew.models import (
    AgentRole,
    OutputFormat,
    TaskDefinition,
    TaskResult,
    UserAction,
)
from xbot.agent.crew.process import HierarchicalProcess, SequentialProcess
from xbot.agent.crew.state import CrewStateManager, TaskPhase


class MockPermissionHandler:
    """Mock permission handler for testing."""

    async def request_interaction(self, kind: str, prompt: str, **kwargs):
        return MagicMock(content="continue")


class TestHumanReviewActions:
    """Test human review action parsing and execution."""

    def test_parse_user_action_numeric(self) -> None:
        """Numeric shortcuts should be parsed correctly."""
        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=MagicMock(),
            crew_config=MagicMock(),
            state_manager=MagicMock(),
        )

        assert process._parse_user_action("1") == UserAction.CONTINUE
        assert process._parse_user_action("2") == UserAction.ANNOTATE
        assert process._parse_user_action("3") == UserAction.EDIT
        assert process._parse_user_action("4") == UserAction.REDO
        assert process._parse_user_action("5") == UserAction.SKIP
        assert process._parse_user_action("6") == UserAction.ABORT

    def test_parse_user_action_english(self) -> None:
        """English action names should be parsed correctly."""
        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=MagicMock(),
            crew_config=MagicMock(),
            state_manager=MagicMock(),
        )

        assert process._parse_user_action("continue") == UserAction.CONTINUE
        assert process._parse_user_action("annotate") == UserAction.ANNOTATE
        assert process._parse_user_action("edit") == UserAction.EDIT
        assert process._parse_user_action("redo") == UserAction.REDO
        assert process._parse_user_action("skip") == UserAction.SKIP
        assert process._parse_user_action("abort") == UserAction.ABORT

    def test_parse_user_action_chinese(self) -> None:
        """Chinese action names should be parsed correctly."""
        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=MagicMock(),
            crew_config=MagicMock(),
            state_manager=MagicMock(),
        )

        assert process._parse_user_action("继续") == UserAction.CONTINUE
        assert process._parse_user_action("批注") == UserAction.ANNOTATE
        assert process._parse_user_action("修改") == UserAction.EDIT
        assert process._parse_user_action("重做") == UserAction.REDO
        assert process._parse_user_action("跳过") == UserAction.SKIP
        assert process._parse_user_action("终止") == UserAction.ABORT

    def test_parse_user_action_unknown_defaults_continue(self) -> None:
        """Unknown action should default to CONTINUE."""
        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=MagicMock(),
            crew_config=MagicMock(),
            state_manager=MagicMock(),
        )

        assert process._parse_user_action("unknown") == UserAction.CONTINUE
        assert process._parse_user_action("") == UserAction.CONTINUE


class TestHumanBriefing:
    """Test human briefing (pre-execution instructions)."""

    @pytest.mark.asyncio
    async def test_human_briefing_disabled(self) -> None:
        """No briefing should be requested if human_briefing is False."""
        permission_handler = MockPermissionHandler()
        state_manager = CrewStateManager(task_names=["task1"], task_definitions=[])

        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=permission_handler,
            crew_config=MagicMock(),
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="agent1",
            human_briefing=False,
        )

        result = await process._human_briefing(task)
        assert result is None

    @pytest.mark.asyncio
    async def test_human_briefing_skip_response(self) -> None:
        """Briefing should return None if user says 'skip'."""
        permission_handler = MagicMock()
        permission_handler.request_interaction = AsyncMock(
            return_value=MagicMock(content="skip")
        )
        state_manager = CrewStateManager(task_names=["task1"], task_definitions=[])

        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=permission_handler,
            crew_config=MagicMock(),
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="agent1",
            human_briefing=True,
        )

        result = await process._human_briefing(task)
        assert result is None

    @pytest.mark.asyncio
    async def test_human_briefing_with_content(self) -> None:
        """Briefing should return user content."""
        permission_handler = MagicMock()
        permission_handler.request_interaction = AsyncMock(
            return_value=MagicMock(content="Focus on edge cases")
        )
        state_manager = CrewStateManager(task_names=["task1"], task_definitions=[])

        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=permission_handler,
            crew_config=MagicMock(),
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="agent1",
            human_briefing=True,
        )

        result = await process._human_briefing(task)
        assert result == "Focus on edge cases"


class TestHumanReviewFlow:
    """Test human review (post-execution) flow."""

    @pytest.mark.asyncio
    async def test_human_review_disabled(self) -> None:
        """No review should be requested if human_review is False."""
        permission_handler = MockPermissionHandler()
        state_manager = CrewStateManager(task_names=["task1"], task_definitions=[])

        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=permission_handler,
            crew_config=MagicMock(),
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="agent1",
            human_review=False,
        )

        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="done",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        # Should return unchanged result
        reviewed = await process._human_review(task, result)
        assert reviewed == result
        assert reviewed.status == "success"

    @pytest.mark.asyncio
    async def test_human_review_continue_action(self) -> None:
        """Continue action should return unchanged result."""
        permission_handler = MagicMock()
        permission_handler.request_interaction = AsyncMock(
            return_value=MagicMock(content="continue")
        )
        state_manager = CrewStateManager(task_names=["task1"], task_definitions=[])

        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=permission_handler,
            crew_config=MagicMock(),
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="agent1",
            human_review=True,
        )

        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="done",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        reviewed = await process._human_review(task, result)
        assert reviewed.status == "success"
        assert reviewed.output == "done"

    @pytest.mark.asyncio
    async def test_human_review_skip_action(self) -> None:
        """Skip action should mark result as skipped."""
        permission_handler = MagicMock()
        permission_handler.request_interaction = AsyncMock(
            return_value=MagicMock(content="skip")
        )
        state_manager = CrewStateManager(task_names=["task1"], task_definitions=[])

        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=permission_handler,
            crew_config=MagicMock(),
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="agent1",
            human_review=True,
        )

        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="done",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        reviewed = await process._human_review(task, result)
        assert reviewed.status == "skipped"

    @pytest.mark.asyncio
    async def test_human_review_abort_action(self) -> None:
        """Abort action should mark result as human_rejected."""
        permission_handler = MagicMock()
        permission_handler.request_interaction = AsyncMock(
            return_value=MagicMock(content="abort")
        )
        state_manager = CrewStateManager(task_names=["task1"], task_definitions=[])

        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=permission_handler,
            crew_config=MagicMock(),
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="agent1",
            human_review=True,
        )

        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="done",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        reviewed = await process._human_review(task, result)
        assert reviewed.status == "human_rejected"


class TestCheckUpstreamReady:
    """Test upstream dependency checking."""

    def test_no_dependencies(self) -> None:
        """Task with no dependencies should be ready."""
        context = MagicMock()
        context.get_result = MagicMock(return_value=None)
        state_manager = CrewStateManager(task_names=["task1"], task_definitions=[])

        process = SequentialProcess(
            pool=MagicMock(),
            context=context,
            permission_handler=MagicMock(),
            crew_config=MagicMock(),
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="agent1",
            context_from=[],  # No dependencies
        )

        assert process._check_upstream_ready(task) is True

    def test_dependencies_completed(self) -> None:
        """Task with completed dependencies should be ready."""
        context = MagicMock()
        context.get_result = MagicMock(
            return_value=TaskResult(
                task_name="dep1",
                agent_name="agent1",
                output="done",
                status="success",
                started_at=datetime.now(),
                finished_at=datetime.now(),
            )
        )
        state_manager = CrewStateManager(task_names=["task1", "dep1"], task_definitions=[])

        process = SequentialProcess(
            pool=MagicMock(),
            context=context,
            permission_handler=MagicMock(),
            crew_config=MagicMock(),
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="agent1",
            context_from=["dep1"],
        )

        assert process._check_upstream_ready(task) is True

    def test_dependencies_not_completed(self) -> None:
        """Task with incomplete dependencies should not be ready."""
        context = MagicMock()
        # No result for dep1
        context.get_result = MagicMock(return_value=None)
        state_manager = CrewStateManager(task_names=["task1", "dep1"], task_definitions=[])

        process = SequentialProcess(
            pool=MagicMock(),
            context=context,
            permission_handler=MagicMock(),
            crew_config=MagicMock(),
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="agent1",
            context_from=["dep1"],
        )

        assert process._check_upstream_ready(task) is False

    def test_dependencies_failed(self) -> None:
        """Task with failed dependency should not be ready."""
        context = MagicMock()
        context.get_result = MagicMock(
            return_value=TaskResult(
                task_name="dep1",
                agent_name="agent1",
                output="error",
                status="failed",
                started_at=datetime.now(),
                finished_at=datetime.now(),
            )
        )
        state_manager = CrewStateManager(task_names=["task1", "dep1"], task_definitions=[])

        process = SequentialProcess(
            pool=MagicMock(),
            context=context,
            permission_handler=MagicMock(),
            crew_config=MagicMock(),
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="agent1",
            context_from=["dep1"],
        )

        assert process._check_upstream_ready(task) is False


class TestTimeoutEstimation:
    """Test timeout estimation logic."""

    def test_estimate_timeout_base(self) -> None:
        """Base timeout should be at least 60 seconds."""
        crew_config = MagicMock()
        crew_config.agents = {
            "agent1": AgentRole(name="agent1", description="Test", goal="Test", max_iterations=20)
        }

        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=MagicMock(),
            crew_config=crew_config,
            state_manager=MagicMock(),
        )

        task = TaskDefinition(
            name="task1",
            description="",  # Empty description
            agent="agent1",
        )

        timeout = process._estimate_timeout(task)
        assert timeout >= 60  # Base minimum

    def test_estimate_timeout_with_description(self) -> None:
        """Longer description should increase timeout."""
        crew_config = MagicMock()
        crew_config.agents = {
            "agent1": AgentRole(name="agent1", description="Test", goal="Test", max_iterations=20)
        }

        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=MagicMock(),
            crew_config=crew_config,
            state_manager=MagicMock(),
        )

        task_short = TaskDefinition(
            name="task_short",
            description="Short task",
            agent="agent1",
        )

        task_long = TaskDefinition(
            name="task_long",
            description="A very long task description with many details and requirements that takes more time to process",
            agent="agent1",
        )

        timeout_short = process._estimate_timeout(task_short)
        timeout_long = process._estimate_timeout(task_long)

        assert timeout_long > timeout_short

    def test_estimate_timeout_with_iterations(self) -> None:
        """More iterations should increase timeout."""
        crew_config = MagicMock()
        crew_config.agents = {
            "agent1": AgentRole(name="agent1", description="Test", goal="Test", max_iterations=10),
            "agent2": AgentRole(name="agent2", description="Test", goal="Test", max_iterations=50),
        }

        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=MagicMock(),
            crew_config=crew_config,
            state_manager=MagicMock(),
        )

        task_low = TaskDefinition(
            name="task_low",
            description="Test",
            agent="agent1",
        )

        task_high = TaskDefinition(
            name="task_high",
            description="Test",
            agent="agent2",
        )

        timeout_low = process._estimate_timeout(task_low)
        timeout_high = process._estimate_timeout(task_high)

        assert timeout_high > timeout_low


class TestSequentialProcessExecute:
    """Test SequentialProcess execute method."""

    @pytest.mark.asyncio
    async def test_execute_single_task_success(self) -> None:
        """Single task should execute successfully."""
        crew_config = MagicMock()
        crew_config.name = "test_crew"
        crew_config.agents = {
            "agent1": AgentRole(name="agent1", description="Test", goal="Test")
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.enabled = False
        crew_config.output.max_output_size = 100000

        pool = MagicMock()

        async def mock_stream(*args, **kwargs):
            yield TaskProgress(delta_content="result", total_content="result", is_final=True)

        pool.run_task_streaming = mock_stream

        context = MagicMock()
        context.build_task_prompt = MagicMock(return_value="test prompt")
        context.build_agent_context = MagicMock(return_value=("test prompt", None))
        context.get_upstream_results = MagicMock(return_value={})

        permission_handler = MagicMock()
        permission_handler.request_interaction = AsyncMock(return_value=MagicMock(content=""))

        state_manager = CrewStateManager(
            task_names=["task1"],
            task_definitions=[
                TaskDefinition(name="task1", description="Test", agent="agent1")
            ],
        )

        process = SequentialProcess(
            pool=pool,
            context=context,
            permission_handler=permission_handler,
            crew_config=crew_config,
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="agent1",
            timeout=60,
        )

        result = await process._execute_single_task(task)

        assert result.status == "success"
        assert result.output == "result"
        assert result.task_name == "task1"

    @pytest.mark.asyncio
    async def test_execute_skips_terminal_tasks(self) -> None:
        """Tasks in terminal states should be skipped."""
        crew_config = MagicMock()
        crew_config.agents = {
            "agent1": AgentRole(name="agent1", description="Test", goal="Test")
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.enabled = False
        crew_config.output.max_output_size = 100000

        pool = MagicMock()
        pool.run_task_streaming = MagicMock()

        context = MagicMock()
        context.build_agent_context = MagicMock(return_value=("test prompt", None))
        existing_result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="previous",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
        context.get_result = MagicMock(return_value=existing_result)
        context.add_result = MagicMock()

        permission_handler = MagicMock()

        state_manager = CrewStateManager(
            task_names=["task1", "task2"],
            task_definitions=[
                TaskDefinition(name="task1", description="Test", agent="agent1"),
                TaskDefinition(name="task2", description="Test", agent="agent1"),
            ],
        )
        # Mark task1 as completed
        state_manager.force_task_phase("task1", TaskPhase.COMPLETED)

        process = SequentialProcess(
            pool=pool,
            context=context,
            permission_handler=permission_handler,
            crew_config=crew_config,
            state_manager=state_manager,
        )

        tasks = [
            TaskDefinition(name="task1", description="Test", agent="agent1"),
            TaskDefinition(name="task2", description="Test", agent="agent1"),
        ]

        results = await process.execute(tasks)

        # task1 should be skipped (returned existing result)
        assert len(results) == 2
        assert results[0].task_name == "task1"
        assert results[0].status == "success"


class TestHierarchicalProcessPlanParsing:
    """Test HierarchicalProcess plan parsing."""

    def test_parse_plan_valid_json(self) -> None:
        """Valid JSON array should be parsed correctly."""
        output = '["task1", "task2", "task3"]'
        result = HierarchicalProcess._parse_plan(output)
        assert result == ["task1", "task2", "task3"]

    def test_parse_plan_json_in_text(self) -> None:
        """JSON array embedded in text should be extracted."""
        output = 'Here is my plan: ["task1", "task2"]'
        result = HierarchicalProcess._parse_plan(output)
        assert result == ["task1", "task2"]

    def test_parse_plan_no_json(self) -> None:
        """No JSON array should return None."""
        output = "I could not generate a plan"
        result = HierarchicalProcess._parse_plan(output)
        assert result is None

    def test_parse_plan_invalid_json(self) -> None:
        """Invalid JSON should return None."""
        output = '["task1", "task2"'  # Missing closing bracket
        result = HierarchicalProcess._parse_plan(output)
        assert result is None

    def test_parse_plan_non_string_elements(self) -> None:
        """JSON with non-string elements should return None."""
        output = '[1, 2, 3]'
        result = HierarchicalProcess._parse_plan(output)
        assert result is None

    def test_parse_plan_mixed_elements(self) -> None:
        """JSON with mixed elements should return None."""
        output = '["task1", 2, "task3"]'
        result = HierarchicalProcess._parse_plan(output)
        assert result is None

    def test_parse_plan_recovers_after_invalid_array_prefix(self) -> None:
        """Parser should keep scanning after an invalid candidate array."""
        output = 'noise [1, 2] trailing ["task1", "task[2]"]'
        result = HierarchicalProcess._parse_plan(output)
        assert result == ["task1", "task[2]"]


class TestProgressHelper:
    """Test progress callback helper."""

    def test_progress_with_callback(self) -> None:
        """Progress should call the callback."""
        progress_calls = []

        def on_progress(message: str, **kwargs):
            progress_calls.append((message, kwargs))

        state_manager = CrewStateManager(task_names=[], task_definitions=[])

        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=MagicMock(),
            crew_config=MagicMock(),
            state_manager=state_manager,
            on_progress=on_progress,
        )

        process._progress("Test message", task_name="task1")

        assert len(progress_calls) == 1
        assert progress_calls[0][0] == "Test message"
        assert progress_calls[0][1] == {"task_name": "task1"}

    def test_progress_without_callback(self) -> None:
        """Progress should work without callback."""
        state_manager = CrewStateManager(task_names=[], task_definitions=[])

        process = SequentialProcess(
            pool=MagicMock(),
            context=MagicMock(),
            permission_handler=MagicMock(),
            crew_config=MagicMock(),
            state_manager=state_manager,
            on_progress=None,
        )

        # Should not raise
        process._progress("Test message")


class TestOutputProcessing:
    """Test output format processing."""

    @pytest.mark.asyncio
    async def test_raw_output_no_processing(self) -> None:
        """RAW output format should not be processed."""
        crew_config = MagicMock()
        crew_config.name = "test_crew"
        crew_config.agents = {
            "agent1": AgentRole(name="agent1", description="Test", goal="Test")
        }
        crew_config.global_context = ""
        crew_config.max_context_length = 4000
        crew_config.output.enabled = False
        crew_config.output.max_output_size = 100000

        pool = MagicMock()

        async def mock_stream(*args, **kwargs):
            yield TaskProgress(delta_content="raw output", total_content="raw output", is_final=True)

        pool.run_task_streaming = mock_stream

        context = MagicMock()
        context.build_task_prompt = MagicMock(return_value="test prompt")
        context.build_agent_context = MagicMock(return_value=("test prompt", None))

        permission_handler = MagicMock()
        permission_handler.request_interaction = AsyncMock(return_value=MagicMock(content=""))

        state_manager = CrewStateManager(task_names=["task1"], task_definitions=[])

        process = SequentialProcess(
            pool=pool,
            context=context,
            permission_handler=permission_handler,
            crew_config=crew_config,
            state_manager=state_manager,
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="agent1",
            output_format=OutputFormat.RAW,
            timeout=60,
        )

        result = await process._execute_single_task(task)

        # Should not have structured_output
        assert result.structured_output is None
        assert result.output == "raw output"


class TestTaskResultFields:
    """Test TaskResult field handling."""

    def test_effective_output_with_edit(self) -> None:
        """effective_output should return edited output if present."""
        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="original",
            human_edited_output="edited",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        assert result.effective_output == "edited"

    def test_effective_output_without_edit(self) -> None:
        """effective_output should return original output if no edit."""
        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="original",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        assert result.effective_output == "original"

    def test_quality_full_by_default(self) -> None:
        """quality should be 'full' by default."""
        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="done",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        assert result.quality == "full"

    def test_extended_count_zero_by_default(self) -> None:
        """extended_count should be 0 by default."""
        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="done",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        assert result.extended_count == 0
