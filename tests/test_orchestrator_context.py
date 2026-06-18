"""Tests for orchestrator helper methods and context building."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from xbot.crew.context import CrewExecutionContext
from xbot.crew.models import (
    AgentRole,
    TaskDefinition,
    TaskResult,
)
from xbot.crew.orchestrator import CrewOrchestrator
from xbot.crew.state import CrewStateManager, TaskPhase


class MockPermissionHandler:
    """Mock permission handler for testing."""

    async def request_interaction(self, kind: str, prompt: str, **kwargs):
        return MagicMock(content="continue")


class TestOrchestratorHelpers:
    """Test orchestrator helper methods."""

    def test_build_summary_success(self) -> None:
        """Summary should show success markers."""
        crew_config = MagicMock()
        crew_config.name = "test_crew"

        orchestrator = CrewOrchestrator(
            crew_config, MagicMock(), MockPermissionHandler()
        )

        results = [
            TaskResult(
                task_name="task1",
                agent_name="agent1",
                output="done",
                status="success",
                started_at=datetime.now(),
                finished_at=datetime.now(),
            ),
            TaskResult(
                task_name="task2",
                agent_name="agent2",
                output="done",
                status="completed",
                started_at=datetime.now(),
                finished_at=datetime.now(),
            ),
        ]

        summary = orchestrator._build_summary(results, 10.5)

        assert "test_crew" in summary
        assert "10.5s" in summary
        assert "[+]" in summary  # Success marker
        assert "task1" in summary
        assert "task2" in summary

    def test_build_summary_with_failures(self) -> None:
        """Summary should show failure markers."""
        crew_config = MagicMock()
        crew_config.name = "test_crew"

        orchestrator = CrewOrchestrator(
            crew_config, MagicMock(), MockPermissionHandler()
        )

        results = [
            TaskResult(
                task_name="task1",
                agent_name="agent1",
                output="done",
                status="success",
                started_at=datetime.now(),
                finished_at=datetime.now(),
            ),
            TaskResult(
                task_name="task2",
                agent_name="agent2",
                output="error",
                status="failed",
                started_at=datetime.now(),
                finished_at=datetime.now(),
            ),
            TaskResult(
                task_name="task3",
                agent_name="agent1",
                output="skipped",
                status="skipped",
                started_at=datetime.now(),
                finished_at=datetime.now(),
            ),
        ]

        summary = orchestrator._build_summary(results, 5.0)

        assert "[+]" in summary  # Success
        assert "[x]" in summary  # Failed
        assert "[-]" in summary  # Skipped

    def test_build_summary_human_rejected(self) -> None:
        """Summary should show human rejected marker."""
        crew_config = MagicMock()
        crew_config.name = "test_crew"

        orchestrator = CrewOrchestrator(
            crew_config, MagicMock(), MockPermissionHandler()
        )

        results = [
            TaskResult(
                task_name="task1",
                agent_name="agent1",
                output="rejected",
                status="human_rejected",
                started_at=datetime.now(),
                finished_at=datetime.now(),
            ),
        ]

        summary = orchestrator._build_summary(results, 2.0)

        assert "[!]" in summary  # Human rejected marker

    def test_progress_with_callback(self) -> None:
        """Progress should call on_progress callback."""
        progress_calls = []

        def on_progress(message: str, **kwargs):
            progress_calls.append(message)

        crew_config = MagicMock()
        orchestrator = CrewOrchestrator(
            crew_config, MagicMock(), MockPermissionHandler(), on_progress=on_progress
        )

        orchestrator._progress("Test message")

        assert len(progress_calls) == 1
        assert progress_calls[0] == "Test message"

    def test_progress_without_callback(self) -> None:
        """Progress should work without callback."""
        crew_config = MagicMock()
        orchestrator = CrewOrchestrator(
            crew_config, MagicMock(), MockPermissionHandler(), on_progress=None
        )

        # Should not raise
        orchestrator._progress("Test message")


class TestCrewExecutionContext:
    """Test CrewExecutionContext functionality."""

    def test_add_and_get_result(self) -> None:
        """Results should be stored and retrieved."""
        context = CrewExecutionContext()

        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="done",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        context.add_result(result)

        assert context.get_result("task1") == result
        assert context.get_result("nonexistent") is None

    def test_get_upstream_results(self) -> None:
        """Should return results for upstream dependencies."""
        context = CrewExecutionContext()

        result1 = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="done1",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        result2 = TaskResult(
            task_name="task2",
            agent_name="agent2",
            output="done2",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        context.add_result(result1)
        context.add_result(result2)

        task = TaskDefinition(
            name="task3",
            description="Test",
            agent="agent3",
            context_from=["task1", "task2"],
        )

        upstream = context.get_upstream_results(task)

        assert len(upstream) == 2
        assert "task1" in upstream
        assert "task2" in upstream
        assert upstream["task1"].output == "done1"

    def test_get_upstream_results_partial(self) -> None:
        """Should only return results that exist."""
        context = CrewExecutionContext()

        result1 = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="done1",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        context.add_result(result1)
        # task2 result not added

        task = TaskDefinition(
            name="task3",
            description="Test",
            agent="agent3",
            context_from=["task1", "task2"],  # task2 doesn't exist
        )

        upstream = context.get_upstream_results(task)

        assert len(upstream) == 1
        assert "task1" in upstream
        assert "task2" not in upstream

    def test_get_all_results(self) -> None:
        """Should return all results."""
        context = CrewExecutionContext()

        result1 = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="done1",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        result2 = TaskResult(
            task_name="task2",
            agent_name="agent2",
            output="done2",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        context.add_result(result1)
        context.add_result(result2)

        all_results = context.get_all_results()

        assert len(all_results) == 2


class TestContextBuildPrompt:
    """Test prompt building in CrewExecutionContext."""

    def test_build_basic_prompt(self) -> None:
        """Should build a basic prompt."""
        context = CrewExecutionContext()

        role = AgentRole(
            name="Developer",
            description="A software developer",
            goal="Write clean code",
            backstory="Experienced in Python",
        )

        task = TaskDefinition(
            name="implement_feature",
            description="Implement user authentication",
            agent="Developer",
        )

        prompt = context.build_task_prompt(task, role)

        assert "Developer" in prompt
        assert "Write clean code" in prompt
        assert "Experienced in Python" in prompt
        assert "Implement user authentication" in prompt

    def test_build_prompt_with_global_context(self) -> None:
        """Should include global context."""
        context = CrewExecutionContext()

        role = AgentRole(
            name="Developer",
            description="Developer",
            goal="Write code",
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="Developer",
        )

        prompt = context.build_task_prompt(
            task, role, global_context="This is a web application"
        )

        assert "This is a web application" in prompt

    def test_build_prompt_with_human_briefing(self) -> None:
        """Should include human briefing."""
        context = CrewExecutionContext()

        role = AgentRole(
            name="Developer",
            description="Developer",
            goal="Write code",
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="Developer",
        )

        prompt = context.build_task_prompt(
            task, role, human_briefing="Focus on edge cases"
        )

        assert "Focus on edge cases" in prompt
        assert "Additional Instructions" in prompt

    def test_build_prompt_with_upstream_context(self) -> None:
        """Should include upstream results."""
        context = CrewExecutionContext()

        # Add upstream result
        upstream_result = TaskResult(
            task_name="design_phase",
            agent_name="Designer",
            output="Design document",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
        context.add_result(upstream_result)

        role = AgentRole(
            name="Developer",
            description="Developer",
            goal="Write code",
        )

        task = TaskDefinition(
            name="implementation",
            description="Implement the design",
            agent="Developer",
            context_from=["design_phase"],
        )

        prompt = context.build_task_prompt(task, role)

        assert "design_phase" in prompt
        assert "Design document" in prompt
        assert "Context from Previous Tasks" in prompt

    def test_build_prompt_with_expected_output(self) -> None:
        """Should include expected output."""
        context = CrewExecutionContext()

        role = AgentRole(
            name="Developer",
            description="Developer",
            goal="Write code",
        )

        task = TaskDefinition(
            name="task1",
            description="Test",
            agent="Developer",
            expected_output="A Python module with tests",
        )

        prompt = context.build_task_prompt(task, role)

        assert "A Python module with tests" in prompt
        assert "Expected Output" in prompt

    def test_build_prompt_with_annotations(self) -> None:
        """Should include human annotations."""
        context = CrewExecutionContext()

        # Add upstream result with annotations
        upstream_result = TaskResult(
            task_name="design_phase",
            agent_name="Designer",
            output="Design document",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
        upstream_result.human_annotations.append("Consider scalability")
        upstream_result.human_annotations.append("Add error handling")
        context.add_result(upstream_result)

        role = AgentRole(
            name="Developer",
            description="Developer",
            goal="Write code",
        )

        task = TaskDefinition(
            name="implementation",
            description="Implement the design",
            agent="Developer",
            context_from=["design_phase"],
        )

        prompt = context.build_task_prompt(task, role)

        assert "Consider scalability" in prompt
        assert "Add error handling" in prompt
        assert "Team Lead Review Notes" in prompt


class TestOrchestratorCheckpoint:
    """Test orchestrator checkpoint handling."""

    def test_apply_checkpoint_success(self) -> None:
        """Should apply successful tasks from checkpoint."""
        crew_config = MagicMock()
        crew_config.name = "test_crew"

        orchestrator = CrewOrchestrator(
            crew_config, MagicMock(), MockPermissionHandler()
        )

        context = CrewExecutionContext()
        state_manager = CrewStateManager(task_names=["task1", "task2"], task_definitions=[])

        checkpoint = {
            "completed_tasks": [
                {
                    "name": "task1",
                    "agent": "agent1",
                    "status": "success",
                    "output": "done",
                    "started_at": datetime.now().isoformat(),
                    "finished_at": datetime.now().isoformat(),
                }
            ]
        }

        orchestrator._apply_checkpoint(checkpoint, context, state_manager)

        # task1 should be in context
        assert context.get_result("task1") is not None
        assert state_manager.get_task_phase("task1") == TaskPhase.COMPLETED

    def test_apply_checkpoint_failed_task_not_restored(self) -> None:
        """Failed tasks should not be restored."""
        crew_config = MagicMock()
        crew_config.name = "test_crew"

        orchestrator = CrewOrchestrator(
            crew_config, MagicMock(), MockPermissionHandler()
        )

        context = CrewExecutionContext()
        state_manager = CrewStateManager(task_names=["task1"], task_definitions=[])

        checkpoint = {
            "completed_tasks": [
                {
                    "name": "task1",
                    "agent": "agent1",
                    "status": "failed",  # Failed - should not be restored
                    "output": "error",
                    "started_at": datetime.now().isoformat(),
                    "finished_at": datetime.now().isoformat(),
                }
            ]
        }

        orchestrator._apply_checkpoint(checkpoint, context, state_manager)

        # task1 should NOT be in context (failed status)
        assert context.get_result("task1") is None
        # Task phase should still be PENDING (not completed)
        assert state_manager.get_task_phase("task1") == TaskPhase.PENDING

    def test_apply_checkpoint_with_human_input(self) -> None:
        """Should restore human-edited output and annotations."""
        crew_config = MagicMock()
        crew_config.name = "test_crew"

        orchestrator = CrewOrchestrator(
            crew_config, MagicMock(), MockPermissionHandler()
        )

        context = CrewExecutionContext()
        state_manager = CrewStateManager(task_names=["task1"], task_definitions=[])

        checkpoint = {
            "completed_tasks": [
                {
                    "name": "task1",
                    "agent": "agent1",
                    "status": "success",
                    "output": "original",
                    "human_edited_output": "edited version",
                    "human_annotations": ["Note 1", "Note 2"],
                    "human_briefing_input": "Extra instructions",
                    "started_at": datetime.now().isoformat(),
                    "finished_at": datetime.now().isoformat(),
                }
            ]
        }

        orchestrator._apply_checkpoint(checkpoint, context, state_manager)

        result = context.get_result("task1")
        assert result is not None
        assert result.human_edited_output == "edited version"
        assert "Note 1" in result.human_annotations
        assert result.human_briefing_input == "Extra instructions"


class TestLLMRepairCallable:
    """Test LLM repair callable retrieval."""

    def test_get_llm_repair_returns_none(self) -> None:
        """Should return None by default (no repair capability)."""
        crew_config = MagicMock()
        orchestrator = CrewOrchestrator(
            crew_config, MagicMock(), MockPermissionHandler()
        )

        result = orchestrator._get_llm_repair_callable()
        assert result is None

    def test_llm_repair_timeout_raises_instead_of_returning_empty_string(self, monkeypatch) -> None:
        """If the repair worker is still alive after join, return a timeout error."""
        from xbot.platform.config.schema import Config

        crew_config = MagicMock()
        crew_config.workspace = "/tmp/test_crew"
        orchestrator = CrewOrchestrator(
            crew_config,
            Config(),
            MockPermissionHandler(),
        )

        class FakeThread:
            def __init__(self, target, daemon=False):
                self.target = target
                self.daemon = daemon

            def start(self):
                return None

            def join(self, timeout=None):
                return None

            def is_alive(self):
                return True

        monkeypatch.setattr("threading.Thread", FakeThread)
        monkeypatch.setattr(
            "xbot.crew.orchestrator._LLMRepairRunner._STARTUP_TIMEOUT_SECONDS",
            0.01,
        )

        repair = orchestrator._get_llm_repair_callable()
        assert repair is not None

        with pytest.raises(TimeoutError, match="timed out"):
            repair("repair prompt")

    def test_llm_repair_reuses_agent_service_for_multiple_attempts(self, monkeypatch) -> None:
        """Repair retries should not rebuild the service lifecycle for each prompt."""
        from types import SimpleNamespace

        from xbot.platform.config.schema import Config

        crew_config = MagicMock()
        crew_config.workspace = "/tmp/test_crew"
        orchestrator = CrewOrchestrator(
            crew_config,
            Config(),
            MockPermissionHandler(),
        )

        class FakeService:
            initialize_count = 0
            shutdown_count = 0

            def __init__(self, agent_config, shared_resources):
                self.agent_config = agent_config
                self.shared_resources = shared_resources

            async def initialize(self):
                type(self).initialize_count += 1

            async def shutdown(self):
                type(self).shutdown_count += 1

            async def process(self, context):
                yield SimpleNamespace(content=f"fixed:{context.prompt}", delta_content="")

        monkeypatch.setattr("xbot.runtime.core.service.AgentService", FakeService)

        repair = orchestrator._get_llm_repair_callable()
        assert repair is not None

        try:
            assert repair("first") == "fixed:first"
            assert repair("second") == "fixed:second"
            assert FakeService.initialize_count == 1
        finally:
            close = getattr(repair, "close", None)
            if callable(close):
                close()

        assert FakeService.shutdown_count == 1


class TestOrchestratorInit:
    """Test orchestrator initialization."""

    def test_init_stores_config(self) -> None:
        """Should store configuration."""
        crew_config = MagicMock()
        crew_config.name = "test_crew"
        xbot_config = MagicMock()
        permission_handler = MockPermissionHandler()

        orchestrator = CrewOrchestrator(
            crew_config,
            xbot_config,
            permission_handler,
            config_path="/path/to/config",
            on_progress=lambda m: None,
        )

        assert orchestrator.crew_config == crew_config
        assert orchestrator.xbot_config == xbot_config
        assert orchestrator.permission_handler == permission_handler
        assert orchestrator.config_path == "/path/to/config"
        assert orchestrator.on_progress is not None
