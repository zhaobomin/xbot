"""Tests for task planner module."""

import pytest

from xbot.agent.crew.planner.models import (
    Capability,
    GoalAnalysis,
    RoleDefinition,
    RolePool,
    RolePoolConfig,
    RoleSelection,
    RoleTier,
    TaskPlan,
)
from xbot.agent.crew.planner.task_planner import TaskPlanner


class TestTaskPlannerInit:
    """Tests for TaskPlanner initialization."""

    def test_default_init(self):
        """Test default initialization."""
        planner = TaskPlanner()
        assert planner is not None


class TestTaskPlannerPlan:
    """Tests for task planning."""

    @pytest.fixture
    def planner(self):
        return TaskPlanner()

    @pytest.fixture
    def sample_roles(self):
        return [
            RoleDefinition(
                name="researcher",
                display_name="Researcher",
                description="Research role",
                goal="Research",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.SEARCH, Capability.ANALYZE],
            ),
            RoleDefinition(
                name="coder",
                display_name="Coder",
                description="Coding role",
                goal="Code",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.WRITE_CODE, Capability.DEBUG],
            ),
        ]

    @pytest.fixture
    def sample_selection(self, sample_roles):
        return RoleSelection(
            selected_roles=sample_roles,
            selection_reason={r.name: "Match" for r in sample_roles},
            skipped_roles=[],
            coverage_score=1.0,
            created_roles=[],
            role_gaps=[],
        )

    def test_plan_simple_goal(self, planner, sample_selection):
        """Test planning for simple goal."""
        analysis = GoalAnalysis(
            summary="Search for information",
            required_capabilities=[Capability.SEARCH],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        tasks = planner.plan("Find information about Python", analysis, sample_selection)

        assert len(tasks) >= 1
        assert all(isinstance(t, TaskPlan) for t in tasks)
        # First task should have no dependencies
        assert tasks[0].dependencies == []

    def test_plan_medium_goal(self, planner, sample_selection):
        """Test planning for medium complexity goal."""
        analysis = GoalAnalysis(
            summary="Analyze and fix code",
            required_capabilities=[Capability.ANALYZE, Capability.WRITE_CODE],
            complexity="medium",
            estimated_tasks=2,
            suggested_process="sequential",
        )

        tasks = planner.plan("Analyze and fix the bug", analysis, sample_selection)

        assert len(tasks) >= 1
        # Check dependency chain
        for i, task in enumerate(tasks):
            if i > 0:
                # Tasks should have dependencies on previous tasks
                assert len(task.dependencies) >= 1

    def test_plan_complex_goal(self, planner, sample_roles):
        """Test planning for complex goal."""
        # Add reviewer and tester for complex tasks
        all_roles = sample_roles + [
            RoleDefinition(
                name="reviewer",
                display_name="Reviewer",
                description="Review role",
                goal="Review",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.REVIEW],
            ),
            RoleDefinition(
                name="tester",
                display_name="Tester",
                description="Test role",
                goal="Test",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.TEST],
            ),
        ]

        selection = RoleSelection(
            selected_roles=all_roles,
            selection_reason={r.name: "Match" for r in all_roles},
            skipped_roles=[],
            coverage_score=1.0,
            created_roles=[],
            role_gaps=[],
        )

        analysis = GoalAnalysis(
            summary="Build and test a feature",
            required_capabilities=[
                Capability.SEARCH,
                Capability.WRITE_CODE,
                Capability.REVIEW,
                Capability.TEST,
            ],
            complexity="complex",
            estimated_tasks=5,
            suggested_process="sequential",
        )

        tasks = planner.plan("Build and test a new feature", analysis, selection)

        assert len(tasks) >= 3
        # Check that tasks are sorted by dependencies
        task_names = {t.name for t in tasks}
        for task in tasks:
            for dep in task.dependencies:
                assert dep in task_names

    def test_plan_with_llm_response(self, planner, sample_selection):
        """Test planning with pre-computed LLM response."""
        analysis = GoalAnalysis(
            summary="Test goal",
            required_capabilities=[Capability.SEARCH],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        llm_response = '''[
            {"name": "task1", "description": "First task", "agent": "researcher", "dependencies": []},
            {"name": "task2", "description": "Second task", "agent": "coder", "dependencies": ["task1"]}
        ]'''

        tasks = planner.plan("Test goal", analysis, sample_selection, llm_response)

        assert len(tasks) == 2
        assert tasks[0].name == "task1"
        assert tasks[1].name == "task2"
        assert "task1" in tasks[1].dependencies

    def test_plan_empty_roles(self, planner):
        """Test planning with no roles."""
        analysis = GoalAnalysis(
            summary="Test goal",
            required_capabilities=[Capability.SEARCH],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        selection = RoleSelection(
            selected_roles=[],
            selection_reason={},
            skipped_roles=[],
            coverage_score=0.0,
            created_roles=[],
            role_gaps=[],
        )

        tasks = planner.plan("Test goal", analysis, selection)

        assert tasks == []


class TestLLMResponseParsing:
    """Tests for LLM response parsing."""

    @pytest.fixture
    def planner(self):
        return TaskPlanner()

    @pytest.fixture
    def sample_roles(self):
        return [
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

    def test_parse_json_response(self, planner, sample_roles):
        """Test parsing JSON LLM response."""
        response = '''[
            {"name": "analyze", "description": "Analyze the problem", "agent": "researcher", "dependencies": []}
        ]'''

        tasks = planner._parse_llm_tasks(response, sample_roles)

        assert len(tasks) == 1
        assert tasks[0].name == "analyze"
        assert tasks[0].agent == "researcher"

    def test_parse_json_with_invalid_role(self, planner, sample_roles):
        """Test parsing JSON with invalid role name."""
        response = '''[
            {"name": "task1", "description": "Task", "agent": "nonexistent", "dependencies": []}
        ]'''

        tasks = planner._parse_llm_tasks(response, sample_roles)

        # Should skip tasks with invalid roles
        assert len(tasks) == 0

    def test_parse_json_with_optional_fields(self, planner, sample_roles):
        """Test parsing JSON with optional fields."""
        response = '''[
            {
                "name": "task1",
                "description": "Task",
                "agent": "researcher",
                "dependencies": [],
                "expected_output": "Results",
                "timeout": 600,
                "human_review": true
            }
        ]'''

        tasks = planner._parse_llm_tasks(response, sample_roles)

        assert len(tasks) == 1
        assert tasks[0].expected_output == "Results"
        assert tasks[0].timeout == 600
        assert tasks[0].human_review is True

    def test_parse_invalid_json(self, planner, sample_roles):
        """Test parsing invalid JSON."""
        response = "This is not valid JSON"

        tasks = planner._parse_llm_tasks(response, sample_roles)

        # Should return empty list for invalid JSON
        assert tasks == []


class TestDependencyValidation:
    """Tests for dependency validation."""

    @pytest.fixture
    def planner(self):
        return TaskPlanner()

    def test_validate_dependencies(self, planner):
        """Test dependency validation."""
        tasks = [
            TaskPlan(
                name="task1",
                description="First",
                agent="agent1",
                dependencies=[],
            ),
            TaskPlan(
                name="task2",
                description="Second",
                agent="agent2",
                dependencies=["task1", "nonexistent"],
            ),
        ]

        validated = planner._validate_dependencies(tasks)

        # Invalid dependency should be removed
        assert "task1" in validated[1].dependencies
        assert "nonexistent" not in validated[1].dependencies

    def test_topological_sort(self, planner):
        """Test topological sort."""
        tasks = [
            TaskPlan(
                name="task3",
                description="Third",
                agent="agent3",
                dependencies=["task2"],
            ),
            TaskPlan(
                name="task1",
                description="First",
                agent="agent1",
                dependencies=[],
            ),
            TaskPlan(
                name="task2",
                description="Second",
                agent="agent2",
                dependencies=["task1"],
            ),
        ]

        sorted_tasks = planner._topological_sort(tasks)

        # Check order: task1 should come before task2, task2 before task3
        task_order = [t.name for t in sorted_tasks]
        assert task_order.index("task1") < task_order.index("task2")
        assert task_order.index("task2") < task_order.index("task3")

    def test_topological_sort_cycle_detection(self, planner):
        """Test that cycles are detected in topological sort."""
        tasks = [
            TaskPlan(
                name="task1",
                description="First",
                agent="agent1",
                dependencies=["task2"],
            ),
            TaskPlan(
                name="task2",
                description="Second",
                agent="agent2",
                dependencies=["task1"],
            ),
        ]

        # Should return original order when cycle is detected
        sorted_tasks = planner._topological_sort(tasks)
        assert len(sorted_tasks) == 2


class TestBuildPlanningPrompt:
    """Tests for building planning prompt."""

    @pytest.fixture
    def planner(self):
        return TaskPlanner()

    @pytest.fixture
    def sample_selection(self):
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
        return RoleSelection(
            selected_roles=roles,
            selection_reason={r.name: "Match" for r in roles},
            skipped_roles=[],
            coverage_score=1.0,
            created_roles=[],
            role_gaps=[],
        )

    def test_build_prompt_includes_goal(self, planner, sample_selection):
        """Test that prompt includes goal."""
        analysis = GoalAnalysis(
            summary="Test goal",
            required_capabilities=[Capability.SEARCH],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        prompt = planner.build_planning_prompt("My goal", analysis, sample_selection)

        assert "My goal" in prompt

    def test_build_prompt_includes_roles(self, planner, sample_selection):
        """Test that prompt includes role information."""
        analysis = GoalAnalysis(
            summary="Test goal",
            required_capabilities=[Capability.SEARCH],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        prompt = planner.build_planning_prompt("Goal", analysis, sample_selection)

        assert "researcher" in prompt

    def test_build_prompt_includes_complexity(self, planner, sample_selection):
        """Test that prompt includes complexity."""
        analysis = GoalAnalysis(
            summary="Test goal",
            required_capabilities=[Capability.SEARCH],
            complexity="complex",
            estimated_tasks=5,
            suggested_process="sequential",
        )

        prompt = planner.build_planning_prompt("Goal", analysis, sample_selection)

        assert "complex" in prompt


class TestFindRoleWithCapability:
    """Tests for finding roles with capabilities."""

    @pytest.fixture
    def planner(self):
        return TaskPlanner()

    @pytest.fixture
    def sample_roles(self):
        return [
            RoleDefinition(
                name="researcher",
                display_name="Researcher",
                description="Research role",
                goal="Research",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.SEARCH, Capability.ANALYZE],
            ),
            RoleDefinition(
                name="coder",
                display_name="Coder",
                description="Coding role",
                goal="Code",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.WRITE_CODE, Capability.DEBUG],
            ),
        ]

    def test_find_existing_capability(self, planner, sample_roles):
        """Test finding role with existing capability."""
        role = planner._find_role_with_capability(
            sample_roles, [Capability.WRITE_CODE]
        )

        assert role is not None
        assert role.name == "coder"

    def test_find_nonexistent_capability(self, planner, sample_roles):
        """Test finding role with non-existent capability."""
        role = planner._find_role_with_capability(
            sample_roles, [Capability.TEST]
        )

        assert role is None

    def test_find_multiple_capabilities(self, planner, sample_roles):
        """Test finding role matching any of multiple capabilities."""
        # Should return first matching role
        role = planner._find_role_with_capability(
            sample_roles, [Capability.TEST, Capability.SEARCH]
        )

        assert role is not None
        assert role.name == "researcher"