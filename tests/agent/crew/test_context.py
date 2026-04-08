"""Tests for Crew execution context and checkpoint."""

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from xbot.crew.context import CrewExecutionContext, load_checkpoint, save_checkpoint
from xbot.crew.models import AgentRole, CrewConfig, TaskDefinition, TaskResult


class TestCrewExecutionContext:
    """Tests for CrewExecutionContext."""

    def test_add_and_get_result(self):
        """Add and retrieve a result."""
        ctx = CrewExecutionContext()
        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="Output",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        ctx.add_result(result)

        assert ctx.get_result("task1") == result
        assert ctx.get_result("unknown") is None

    def test_get_upstream_results(self):
        """Get upstream results based on context_from."""
        ctx = CrewExecutionContext()
        task1_result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="Task 1 output",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
        task2_result = TaskResult(
            task_name="task2",
            agent_name="agent2",
            output="Task 2 output",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        ctx.add_result(task1_result)
        ctx.add_result(task2_result)

        task = TaskDefinition(
            name="task3",
            description="Task 3",
            agent="agent3",
            context_from=["task1", "task2"],
        )

        upstream = ctx.get_upstream_results(task)

        assert len(upstream) == 2
        assert upstream["task1"] == task1_result
        assert upstream["task2"] == task2_result

    def test_get_upstream_results_partial(self):
        """Get upstream results when only some are available."""
        ctx = CrewExecutionContext()
        task1_result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="Task 1 output",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        ctx.add_result(task1_result)

        task = TaskDefinition(
            name="task3",
            description="Task 3",
            agent="agent3",
            context_from=["task1", "task2"],  # task2 not available
        )

        upstream = ctx.get_upstream_results(task)

        assert len(upstream) == 1
        assert "task1" in upstream
        assert "task2" not in upstream

    def test_get_all_results(self):
        """Get all results."""
        ctx = CrewExecutionContext()
        for i in range(3):
            result = TaskResult(
                task_name=f"task{i}",
                agent_name="agent1",
                output=f"Output {i}",
                status="success",
                started_at=datetime.now(),
                finished_at=datetime.now(),
            )
            ctx.add_result(result)

        all_results = ctx.get_all_results()

        assert len(all_results) == 3


class TestBuildTaskPrompt:
    """Tests for build_task_prompt."""

    def test_basic_prompt(self):
        """Build basic prompt without upstream context."""
        ctx = CrewExecutionContext()
        role = AgentRole(
            name="scout",
            description="Bug finder",
            goal="Find bugs",
            backstory="Expert debugger",
        )
        task = TaskDefinition(
            name="find_bugs",
            description="Find all bugs in the code",
            agent="scout",
            expected_output="List of bugs",
        )

        prompt = ctx.build_task_prompt(task, role)

        assert "scout" in prompt
        assert "Find bugs" in prompt
        assert "Expert debugger" in prompt
        assert "Find all bugs" in prompt
        assert "List of bugs" in prompt

    def test_prompt_with_global_context(self):
        """Build prompt with global context."""
        ctx = CrewExecutionContext()
        role = AgentRole(name="scout", description="Bug finder", goal="Find bugs")
        task = TaskDefinition(name="task1", description="Do something", agent="scout")

        prompt = ctx.build_task_prompt(
            task, role, global_context="This is a Python project"
        )

        assert "This is a Python project" in prompt

    def test_prompt_with_human_briefing(self):
        """Build prompt with human briefing."""
        ctx = CrewExecutionContext()
        role = AgentRole(name="scout", description="Bug finder", goal="Find bugs")
        task = TaskDefinition(name="task1", description="Do something", agent="scout")

        prompt = ctx.build_task_prompt(
            task, role, human_briefing="Focus on security bugs"
        )

        assert "Focus on security bugs" in prompt

    def test_prompt_with_upstream_context(self):
        """Build prompt with upstream task context."""
        ctx = CrewExecutionContext()
        upstream_result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="Found 5 bugs in authentication module",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
        ctx.add_result(upstream_result)

        role = AgentRole(name="fixer", description="Bug fixer", goal="Fix bugs")
        task = TaskDefinition(
            name="task2",
            description="Fix the bugs",
            agent="fixer",
            context_from=["task1"],
        )

        prompt = ctx.build_task_prompt(task, role)

        assert "Found 5 bugs" in prompt

    def test_prompt_with_human_annotations(self):
        """Build prompt with human annotations from upstream."""
        ctx = CrewExecutionContext()
        upstream_result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="Found 5 bugs",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            human_annotations=["Focus on the auth module first"],
        )
        ctx.add_result(upstream_result)

        role = AgentRole(name="fixer", description="Bug fixer", goal="Fix bugs")
        task = TaskDefinition(
            name="task2",
            description="Fix the bugs",
            agent="fixer",
            context_from=["task1"],
        )

        prompt = ctx.build_task_prompt(task, role)

        assert "Focus on the auth module first" in prompt

    def test_prompt_truncates_long_output(self):
        """Long upstream output is truncated."""
        ctx = CrewExecutionContext()
        long_output = "x" * 5000
        upstream_result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output=long_output,
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )
        ctx.add_result(upstream_result)

        role = AgentRole(name="fixer", description="Bug fixer", goal="Fix bugs")
        task = TaskDefinition(
            name="task2",
            description="Fix the bugs",
            agent="fixer",
            context_from=["task1"],
        )

        prompt = ctx.build_task_prompt(task, role, max_context_length=1000)

        assert len(prompt) < 6000  # Should be truncated
        assert "truncated" in prompt.lower()

    def test_prompt_uses_edited_output(self):
        """Prompt uses human_edited_output when available."""
        ctx = CrewExecutionContext()
        upstream_result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="Original output",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            human_edited_output="Edited output",
        )
        ctx.add_result(upstream_result)

        role = AgentRole(name="fixer", description="Bug fixer", goal="Fix bugs")
        task = TaskDefinition(
            name="task2",
            description="Fix the bugs",
            agent="fixer",
            context_from=["task1"],
        )

        prompt = ctx.build_task_prompt(task, role)

        assert "Edited output" in prompt
        assert "Original output" not in prompt


class TestCheckpoint:
    """Tests for checkpoint save and load."""

    def test_save_and_load_checkpoint(self):
        """Save and load a checkpoint."""
        config = CrewConfig(
            name="test_crew",
            agents={
                "agent1": AgentRole(
                    name="agent1",
                    description="Agent 1",
                    goal="Do work",
                )
            },
            tasks=[
                TaskDefinition(
                    name="task1",
                    description="Task 1",
                    agent="agent1",
                )
            ],
        )

        ctx = CrewExecutionContext()
        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="Task output",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            human_annotations=["Good job"],
        )
        ctx.add_result(result)

        with tempfile.TemporaryDirectory():
            checkpoint_path = save_checkpoint(
                crew_config=config,
                config_path="/path/to/config.yaml",
                context=ctx,
                crew_phase="running",
                next_task="task2",
                started_at=datetime.now(),
            )

            assert checkpoint_path.exists()

            loaded = load_checkpoint(checkpoint_path)

            assert loaded["crew_name"] == "test_crew"
            assert loaded["crew_phase"] == "running"
            assert loaded["next_task"] == "task2"
            assert len(loaded["completed_tasks"]) == 1
            assert loaded["completed_tasks"][0]["name"] == "task1"
            assert loaded["completed_tasks"][0]["human_annotations"] == ["Good job"]

    def test_load_nonexistent_checkpoint(self):
        """Loading nonexistent checkpoint raises error."""
        with pytest.raises(FileNotFoundError):
            load_checkpoint(Path("/nonexistent/checkpoint.json"))

    def test_checkpoint_atomic_write(self):
        """Checkpoint file is written atomically."""
        config = CrewConfig(
            name="test_crew",
            agents={
                "agent1": AgentRole(
                    name="agent1",
                    description="Agent 1",
                    goal="Do work",
                )
            },
            tasks=[TaskDefinition(name="task1", description="Task 1", agent="agent1")],
        )

        ctx = CrewExecutionContext()

        with tempfile.TemporaryDirectory():
            checkpoint_path = save_checkpoint(
                crew_config=config,
                config_path="",
                context=ctx,
                crew_phase="running",
                next_task=None,
                started_at=datetime.now(),
            )

            # File should exist and be valid JSON
            assert checkpoint_path.exists()
            with open(checkpoint_path) as f:
                data = json.load(f)
            assert data["crew_name"] == "test_crew"

            # No temp files should remain
            temp_files = list(checkpoint_path.parent.glob("*.tmp"))
            assert len(temp_files) == 0
