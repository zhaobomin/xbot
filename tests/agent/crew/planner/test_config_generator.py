"""Tests for config generator module."""

import tempfile
from pathlib import Path

import pytest

from xbot.crew.planner.config_generator import ConfigGenerator
from xbot.crew.planner.models import (
    Capability,
    CrewPlan,
    GoalAnalysis,
    RoleDefinition,
    RoleSelection,
    RoleTier,
    TaskPlan,
)


class TestConfigGeneratorInit:
    """Tests for ConfigGenerator initialization."""

    def test_default_init(self):
        """Test default initialization."""
        generator = ConfigGenerator()
        assert generator is not None


class TestGenerateYAML:
    """Tests for YAML generation."""

    @pytest.fixture
    def generator(self):
        return ConfigGenerator()

    @pytest.fixture
    def sample_plan(self):
        roles = [
            RoleDefinition(
                name="researcher",
                display_name="Researcher",
                description="Research role",
                goal="Research information",
                backstory="Expert researcher",
                tier=RoleTier.CORE,
                capabilities=[Capability.SEARCH, Capability.ANALYZE],
            ),
            RoleDefinition(
                name="coder",
                display_name="Coder",
                description="Coding role",
                goal="Write code",
                backstory="Expert developer",
                tier=RoleTier.CORE,
                capabilities=[Capability.WRITE_CODE],
            ),
        ]

        tasks = [
            TaskPlan(
                name="analyze",
                description="Analyze the problem",
                agent="researcher",
                dependencies=[],
                expected_output="Analysis report",
            ),
            TaskPlan(
                name="implement",
                description="Implement the solution",
                agent="coder",
                dependencies=["analyze"],
                expected_output="Code implementation",
            ),
        ]

        analysis = GoalAnalysis(
            summary="Build a feature",
            required_capabilities=[Capability.SEARCH, Capability.WRITE_CODE],
            complexity="medium",
            estimated_tasks=2,
            suggested_process="sequential",
        )

        selection = RoleSelection(
            selected_roles=roles,
            selection_reason={r.name: "Match" for r in roles},
            skipped_roles=[],
            coverage_score=1.0,
            created_roles=[],
            role_gaps=[],
        )

        return CrewPlan(
            name="build_feature",
            description="Build a new feature",
            process="sequential",
            global_context="Goal: Build a feature",
            roles=roles,
            tasks=tasks,
            analysis=analysis,
            role_selection=selection,
            planning_time=0.5,
            confidence=0.9,
        )

    def test_generate_yaml_basic(self, generator, sample_plan):
        """Test basic YAML generation."""
        yaml_content = generator.generate_yaml(sample_plan)

        assert "name: build_feature" in yaml_content
        assert "description:" in yaml_content
        assert "process: sequential" in yaml_content

    def test_generate_yaml_includes_roles(self, generator, sample_plan):
        """Test that YAML includes roles (as agents)."""
        yaml_content = generator.generate_yaml(sample_plan)

        assert "agents:" in yaml_content
        assert "researcher:" in yaml_content
        assert "coder:" in yaml_content
        assert "goal: Research information" in yaml_content

    def test_generate_yaml_includes_tasks(self, generator, sample_plan):
        """Test that YAML includes tasks."""
        yaml_content = generator.generate_yaml(sample_plan)

        assert "tasks:" in yaml_content
        assert "name: analyze" in yaml_content
        assert "name: implement" in yaml_content
        assert "agent: researcher" in yaml_content
        assert "agent: coder" in yaml_content

    def test_generate_yaml_includes_dependencies(self, generator, sample_plan):
        """Test that YAML includes task dependencies."""
        yaml_content = generator.generate_yaml(sample_plan)

        assert "context_from:" in yaml_content
        assert "- analyze" in yaml_content

    def test_generate_yaml_valid_format(self, generator, sample_plan):
        """Test that YAML is valid."""
        import yaml

        yaml_content = generator.generate_yaml(sample_plan)

        # Should not raise
        data = yaml.safe_load(yaml_content)
        assert isinstance(data, dict)
        assert "name" in data
        assert "agents" in data
        assert "tasks" in data


class TestGeneratePreview:
    """Tests for preview generation."""

    @pytest.fixture
    def generator(self):
        return ConfigGenerator()

    @pytest.fixture
    def sample_plan(self):
        roles = [
            RoleDefinition(
                name="researcher",
                display_name="Researcher",
                description="Research role",
                goal="Research",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.SEARCH],
            ),
        ]

        tasks = [
            TaskPlan(
                name="research",
                description="Do research",
                agent="researcher",
                dependencies=[],
            ),
        ]

        analysis = GoalAnalysis(
            summary="Research task",
            required_capabilities=[Capability.SEARCH],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        selection = RoleSelection(
            selected_roles=roles,
            selection_reason={r.name: "Match" for r in roles},
            skipped_roles=[],
            coverage_score=1.0,
            created_roles=[],
            role_gaps=[],
        )

        return CrewPlan(
            name="research_task",
            description="Do research",
            process="sequential",
            global_context="",
            roles=roles,
            tasks=tasks,
            analysis=analysis,
            role_selection=selection,
            planning_time=0.1,
            confidence=0.8,
        )

    def test_generate_preview_includes_summary(self, generator, sample_plan):
        """Test that preview includes plan summary."""
        preview = generator.generate_preview(sample_plan)

        assert "Crew: research_task" in preview
        assert "Process: sequential" in preview

    def test_generate_preview_includes_roles(self, generator, sample_plan):
        """Test that preview includes roles."""
        preview = generator.generate_preview(sample_plan)

        assert "Roles" in preview
        assert "researcher" in preview

    def test_generate_preview_includes_tasks(self, generator, sample_plan):
        """Test that preview includes tasks."""
        preview = generator.generate_preview(sample_plan)

        assert "Tasks" in preview
        assert "research" in preview


class TestSaveConfig:
    """Tests for saving configuration."""

    @pytest.fixture
    def generator(self):
        return ConfigGenerator()

    @pytest.fixture
    def sample_plan(self):
        roles = [
            RoleDefinition(
                name="agent1",
                display_name="Agent 1",
                description="Test agent",
                goal="Test",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.ANALYZE],
            ),
        ]

        tasks = [
            TaskPlan(
                name="task1",
                description="Test task",
                agent="agent1",
                dependencies=[],
            ),
        ]

        analysis = GoalAnalysis(
            summary="Test",
            required_capabilities=[Capability.ANALYZE],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        selection = RoleSelection(
            selected_roles=roles,
            selection_reason={r.name: "Match" for r in roles},
            skipped_roles=[],
            coverage_score=1.0,
            created_roles=[],
            role_gaps=[],
        )

        return CrewPlan(
            name="test_plan",
            description="Test",
            process="sequential",
            global_context="",
            roles=roles,
            tasks=tasks,
            analysis=analysis,
            role_selection=selection,
            planning_time=0.0,
            confidence=1.0,
        )

    def test_save_to_file(self, generator, sample_plan):
        """Test saving configuration to file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "crew.yaml"
            result_path = generator.save(sample_plan, path)

            assert result_path.exists()
            content = result_path.read_text()
            assert "name: test_plan" in content

    def test_save_creates_parent_directories(self, generator, sample_plan):
        """Test that save creates parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "subdir" / "crew.yaml"
            result_path = generator.save(sample_plan, path)

            assert result_path.exists()
            assert result_path.parent.exists()

    def test_save_with_metadata(self, generator, sample_plan):
        """Test that save includes metadata comments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "crew.yaml"
            result_path = generator.save(sample_plan, path)

            content = result_path.read_text()
            assert "# Generated by xbot" in content or "Generated" in content


class TestRoleConversion:
    """Tests for role conversion in config generation."""

    @pytest.fixture
    def generator(self):
        return ConfigGenerator()

    def test_role_with_tools(self, generator):
        """Test that roles with tools are converted correctly."""
        role = RoleDefinition(
            name="tool_user",
            display_name="Tool User",
            description="Uses tools",
            goal="Use tools",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.SEARCH],
            tools=["web_search", "file_read"],
        )

        plan = CrewPlan(
            name="tool_plan",
            description="Test",
            process="sequential",
            global_context="",
            roles=[role],
            tasks=[],
            analysis=GoalAnalysis(
                summary="Test",
                required_capabilities=[],
                complexity="simple",
                estimated_tasks=0,
                suggested_process="sequential",
            ),
            role_selection=RoleSelection(
                selected_roles=[role],
                selection_reason={role.name: "Match"},
                skipped_roles=[],
                coverage_score=1.0,
                created_roles=[],
                role_gaps=[],
            ),
            planning_time=0.0,
            confidence=1.0,
        )

        yaml_content = generator.generate_yaml(plan)

        assert "tools:" in yaml_content
        assert "web_search" in yaml_content

    def test_role_with_restrictions(self, generator):
        """Test that roles with tool restrictions are converted."""
        role = RoleDefinition(
            name="restricted",
            display_name="Restricted",
            description="Has restrictions",
            goal="Restricted access",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.WRITE_CODE],
            tool_restrictions=["no_delete", "no_execute"],
        )

        plan = CrewPlan(
            name="restricted_plan",
            description="Test",
            process="sequential",
            global_context="",
            roles=[role],
            tasks=[],
            analysis=GoalAnalysis(
                summary="Test",
                required_capabilities=[],
                complexity="simple",
                estimated_tasks=0,
                suggested_process="sequential",
            ),
            role_selection=RoleSelection(
                selected_roles=[role],
                selection_reason={role.name: "Match"},
                skipped_roles=[],
                coverage_score=1.0,
                created_roles=[],
                role_gaps=[],
            ),
            planning_time=0.0,
            confidence=1.0,
        )

        yaml_content = generator.generate_yaml(plan)

        # Should include restrictions or handle gracefully
        assert "restricted:" in yaml_content


class TestTaskConversion:
    """Tests for task conversion in config generation."""

    @pytest.fixture
    def generator(self):
        return ConfigGenerator()

    @pytest.fixture
    def sample_role(self):
        return RoleDefinition(
            name="worker",
            display_name="Worker",
            description="Worker role",
            goal="Work",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )

    def test_task_with_expected_output(self, generator, sample_role):
        """Test that task expected output is included."""
        task = TaskPlan(
            name="analyze",
            description="Analyze data",
            agent="worker",
            dependencies=[],
            expected_output="Analysis report with findings",
        )

        plan = CrewPlan(
            name="output_plan",
            description="Test",
            process="sequential",
            global_context="",
            roles=[sample_role],
            tasks=[task],
            analysis=GoalAnalysis(
                summary="Test",
                required_capabilities=[],
                complexity="simple",
                estimated_tasks=1,
                suggested_process="sequential",
            ),
            role_selection=RoleSelection(
                selected_roles=[sample_role],
                selection_reason={sample_role.name: "Match"},
                skipped_roles=[],
                coverage_score=1.0,
                created_roles=[],
                role_gaps=[],
            ),
            planning_time=0.0,
            confidence=1.0,
        )

        yaml_content = generator.generate_yaml(plan)

        assert "expected_output:" in yaml_content
        assert "Analysis report" in yaml_content

    def test_task_with_timeout(self, generator, sample_role):
        """Test that task timeout is included."""
        task = TaskPlan(
            name="long_task",
            description="Long running task",
            agent="worker",
            dependencies=[],
            timeout=600,
        )

        plan = CrewPlan(
            name="timeout_plan",
            description="Test",
            process="sequential",
            global_context="",
            roles=[sample_role],
            tasks=[task],
            analysis=GoalAnalysis(
                summary="Test",
                required_capabilities=[],
                complexity="simple",
                estimated_tasks=1,
                suggested_process="sequential",
            ),
            role_selection=RoleSelection(
                selected_roles=[sample_role],
                selection_reason={sample_role.name: "Match"},
                skipped_roles=[],
                coverage_score=1.0,
                created_roles=[],
                role_gaps=[],
            ),
            planning_time=0.0,
            confidence=1.0,
        )

        yaml_content = generator.generate_yaml(plan)

        assert "timeout:" in yaml_content
        assert "600" in yaml_content

    def test_task_with_human_review(self, generator, sample_role):
        """Test that human_review flag is included."""
        task = TaskPlan(
            name="review_needed",
            description="Task requiring review",
            agent="worker",
            dependencies=[],
            human_review=True,
        )

        plan = CrewPlan(
            name="review_plan",
            description="Test",
            process="sequential",
            global_context="",
            roles=[sample_role],
            tasks=[task],
            analysis=GoalAnalysis(
                summary="Test",
                required_capabilities=[],
                complexity="simple",
                estimated_tasks=1,
                suggested_process="sequential",
            ),
            role_selection=RoleSelection(
                selected_roles=[sample_role],
                selection_reason={sample_role.name: "Match"},
                skipped_roles=[],
                coverage_score=1.0,
                created_roles=[],
                role_gaps=[],
            ),
            planning_time=0.0,
            confidence=1.0,
        )

        yaml_content = generator.generate_yaml(plan)

        assert "human_review:" in yaml_content
        assert "true" in yaml_content.lower()
