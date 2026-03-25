"""Tests for Crew data models and configuration loading."""

import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from xbot.agent.crew.models import (
    AgentRole,
    CrewConfig,
    ProcessType,
    TaskDefinition,
    TaskResult,
    UserAction,
    load_crew_config,
)


class TestAgentRole:
    """Tests for AgentRole model."""

    def test_minimal_role(self):
        """Create role with minimal required fields."""
        role = AgentRole(name="scout", description="Bug finder", goal="Find bugs")

        assert role.name == "scout"
        assert role.model == "inherit"
        assert role.max_iterations == 30
        assert role.tools is None

    def test_role_with_custom_settings(self):
        """Create role with custom settings."""
        role = AgentRole(
            name="custom",
            description="Custom role",
            goal="Custom goal",
            model="claude-3-opus",
            max_iterations=50,
            tools=["read", "write"],
        )

        assert role.model == "claude-3-opus"
        assert role.max_iterations == 50
        assert role.tools == ["read", "write"]


class TestTaskDefinition:
    """Tests for TaskDefinition model."""

    def test_minimal_task(self):
        """Create task with minimal required fields."""
        task = TaskDefinition(
            name="find_bugs",
            description="Find all bugs in the code",
            agent="scout",
        )

        assert task.name == "find_bugs"
        assert task.context_from == []
        assert task.human_review is False
        assert task.human_briefing is False
        assert task.timeout == 600

    def test_task_with_dependencies(self):
        """Create task with upstream dependencies."""
        task = TaskDefinition(
            name="fix_bugs",
            description="Fix the bugs",
            agent="fixer",
            context_from=["find_bugs", "analyze_bugs"],
            human_review=True,
            timeout=1200,
        )

        assert task.context_from == ["find_bugs", "analyze_bugs"]
        assert task.human_review is True
        assert task.timeout == 1200


class TestCrewConfig:
    """Tests for CrewConfig model."""

    def test_minimal_config(self):
        """Create config with minimal required fields."""
        config = CrewConfig(
            name="test_crew",
            agents={
                "scout": AgentRole(
                    name="scout",
                    description="Bug finder",
                    goal="Find bugs",
                )
            },
            tasks=[
                TaskDefinition(
                    name="find_bugs",
                    description="Find bugs",
                    agent="scout",
                )
            ],
        )

        assert config.name == "test_crew"
        assert config.process == ProcessType.sequential
        assert config.max_context_length == 4000
        assert config.manager_timeout == 120

    def test_config_with_custom_settings(self):
        """Create config with custom settings."""
        config = CrewConfig(
            name="hierarchical_crew",
            process=ProcessType.hierarchical,
            agents={
                "manager": AgentRole(
                    name="manager",
                    description="Team lead",
                    goal="Coordinate team",
                ),
                "worker": AgentRole(
                    name="worker",
                    description="Worker",
                    goal="Do work",
                ),
            },
            tasks=[
                TaskDefinition(
                    name="task1",
                    description="Task 1",
                    agent="worker",
                )
            ],
            manager_agent="manager",
            manager_timeout=300,
            max_context_length=8000,
        )

        assert config.process == ProcessType.hierarchical
        assert config.manager_agent == "manager"
        assert config.manager_timeout == 300
        assert config.max_context_length == 8000


class TestTaskResult:
    """Tests for TaskResult dataclass."""

    def test_effective_output_returns_original(self):
        """effective_output returns original when no edit."""
        from datetime import datetime

        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="Original output",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        assert result.effective_output == "Original output"

    def test_effective_output_returns_edited(self):
        """effective_output returns edited version when present."""
        from datetime import datetime

        result = TaskResult(
            task_name="task1",
            agent_name="agent1",
            output="Original output",
            status="success",
            started_at=datetime.now(),
            finished_at=datetime.now(),
            human_edited_output="Edited output",
        )

        assert result.effective_output == "Edited output"


class TestUserAction:
    """Tests for UserAction enum."""

    def test_all_actions_defined(self):
        """All expected actions are defined."""
        assert UserAction.CONTINUE == "continue"
        assert UserAction.ANNOTATE == "annotate"
        assert UserAction.EDIT == "edit"
        assert UserAction.REDO == "redo"
        assert UserAction.SKIP == "skip"
        assert UserAction.ABORT == "abort"


class TestLoadCrewConfig:
    """Tests for load_crew_config function."""

    def test_load_minimal_yaml(self):
        """Load a minimal valid YAML config."""
        yaml_content = """
name: test_crew
agents:
  scout:
    description: Bug finder
    goal: Find bugs
tasks:
  - name: find_bugs
    description: Find all bugs
    agent: scout
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            config = load_crew_config(Path(f.name))

        assert config.name == "test_crew"
        assert "scout" in config.agents
        assert len(config.tasks) == 1
        assert config.tasks[0].name == "find_bugs"

    def test_load_with_dict_agents(self):
        """Load config with dict-style agent definitions."""
        yaml_content = """
name: test_crew
agents:
  scout:
    description: Bug finder
    goal: Find bugs
  fixer:
    description: Bug fixer
    goal: Fix bugs
tasks:
  - name: find_bugs
    description: Find bugs
    agent: scout
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            config = load_crew_config(Path(f.name))

        assert len(config.agents) == 2
        assert "scout" in config.agents
        assert "fixer" in config.agents

    def test_load_with_list_agents(self):
        """Load config with list-style agent definitions."""
        yaml_content = """
name: test_crew
agents:
  - name: scout
    description: Bug finder
    goal: Find bugs
  - name: fixer
    description: Bug fixer
    goal: Fix bugs
tasks:
  - name: find_bugs
    description: Find bugs
    agent: scout
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            config = load_crew_config(Path(f.name))

        assert len(config.agents) == 2
        assert "scout" in config.agents
        assert "fixer" in config.agents

    def test_invalid_task_agent_reference(self):
        """Error when task references unknown agent."""
        yaml_content = """
name: test_crew
agents:
  scout:
    description: Bug finder
    goal: Find bugs
tasks:
  - name: find_bugs
    description: Find bugs
    agent: unknown_agent
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with pytest.raises(ValueError, match="unknown agent"):
                load_crew_config(Path(f.name))

    def test_invalid_context_from_reference(self):
        """Error when context_from references unknown task."""
        yaml_content = """
name: test_crew
agents:
  scout:
    description: Bug finder
    goal: Find bugs
tasks:
  - name: find_bugs
    description: Find bugs
    agent: scout
    context_from:
      - unknown_task
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()

            with pytest.raises(ValueError, match="not a defined task"):
                load_crew_config(Path(f.name))

    def test_file_not_found(self):
        """Error when config file doesn't exist."""
        with pytest.raises(FileNotFoundError):
            load_crew_config(Path("/nonexistent/config.yaml"))

    def test_workspace_resolution_relative(self):
        """Relative workspace is resolved relative to config file."""
        yaml_content = """
name: test_crew
workspace: ./subdir
agents:
  scout:
    description: Bug finder
    goal: Find bugs
tasks:
  - name: find_bugs
    description: Find bugs
    agent: scout
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "configs"
            config_dir.mkdir()
            config_file = config_dir / "crew.yaml"
            config_file.write_text(yaml_content)

            config = load_crew_config(config_file)

            # Workspace should be resolved to configs/subdir
            assert "subdir" in config.workspace

    def test_workspace_resolution_absolute(self):
        """Absolute workspace is kept as-is."""
        yaml_content = """
name: test_crew
workspace: /absolute/path
agents:
  scout:
    description: Bug finder
    goal: Find bugs
tasks:
  - name: find_bugs
    description: Find bugs
    agent: scout
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            config = load_crew_config(Path(f.name))

            assert config.workspace == "/absolute/path"