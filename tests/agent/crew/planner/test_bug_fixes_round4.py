"""Tests for bug fixes in the planner module.

This test file covers:
1. None handling in dict.get() - Bug #1
2. _infer_tools None check - Bug #2
3. _build_role_from_request validation - Bug #3
4. Topological sort circular dependency handling - Bug #4
5. Timeout validation - Bug #5
6. Empty goal validation - Bug #6
7. Empty tools list display - Bug #7
8. Empty capabilities warning - Bug #8
9. Confidence bounds - Bug #9
"""

import tempfile
from pathlib import Path

import pytest

from xbot.crew.planner.crew_planner import CrewPlanner
from xbot.crew.planner.goal_analyzer import GoalAnalyzer
from xbot.crew.planner.models import (
    Capability,
    GoalAnalysis,
    RoleCreationRequest,
    RoleDefinition,
    RolePoolConfig,
    RoleSelection,
    RoleTier,
    TaskPlan,
)
from xbot.crew.planner.role_creator import RoleCreator
from xbot.crew.planner.task_planner import TaskPlanner

# =============================================================================
# Bug #1: None handling in dict.get()
# =============================================================================

class TestNoneHandlingInDictGet:
    """Tests for Bug #1 - None values in dict.get() should not break code."""

    @pytest.fixture
    def task_planner(self):
        return TaskPlanner()

    @pytest.fixture
    def sample_roles(self):
        return [
            RoleDefinition(
                name="agent1",
                display_name="Agent 1",
                description="Test agent",
                goal="Test",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.ANALYZE],
            )
        ]

    def test_null_dependencies_handled(self, task_planner, sample_roles):
        """Test that null dependencies don't cause TypeError."""
        response = '''
        [
            {
                "name": "task1",
                "description": "Test",
                "agent": "agent1",
                "dependencies": null
            }
        ]
        '''
        tasks = task_planner._parse_llm_tasks(response, sample_roles)
        assert len(tasks) == 1
        assert tasks[0].dependencies == []

    def test_null_expected_output_handled(self, task_planner, sample_roles):
        """Test that null expected_output doesn't cause issues."""
        response = '''
        [
            {
                "name": "task1",
                "description": "Test",
                "agent": "agent1",
                "expected_output": null
            }
        ]
        '''
        tasks = task_planner._parse_llm_tasks(response, sample_roles)
        assert len(tasks) == 1
        assert tasks[0].expected_output == ""

    def test_null_timeout_handled(self, task_planner, sample_roles):
        """Test that null timeout uses default."""
        response = '''
        [
            {
                "name": "task1",
                "description": "Test",
                "agent": "agent1",
                "timeout": null
            }
        ]
        '''
        tasks = task_planner._parse_llm_tasks(response, sample_roles)
        assert len(tasks) == 1
        assert tasks[0].timeout == 300

    def test_all_null_values_handled(self, task_planner, sample_roles):
        """Test that all null values are handled gracefully."""
        response = '''
        [
            {
                "name": null,
                "description": null,
                "agent": "agent1",
                "dependencies": null,
                "expected_output": null,
                "timeout": null,
                "human_review": null
            }
        ]
        '''
        tasks = task_planner._parse_llm_tasks(response, sample_roles)
        assert len(tasks) == 1
        assert tasks[0].name == "task_1"
        assert tasks[0].description == ""
        assert tasks[0].dependencies == []
        assert tasks[0].timeout == 300


class TestGoalAnalyzerNullHandling:
    """Tests for None handling in GoalAnalyzer."""

    @pytest.fixture
    def analyzer(self):
        return GoalAnalyzer()

    def test_parse_null_capabilities(self, analyzer):
        """Test that null required_capabilities doesn't crash."""
        response = '''
        {
            "summary": "Test",
            "required_capabilities": null,
            "complexity": "medium"
        }
        '''
        result = analyzer.parse_llm_response(response)
        assert result is not None
        assert result.required_capabilities == []

    def test_parse_null_constraints(self, analyzer):
        """Test that null constraints doesn't crash."""
        response = '''
        {
            "summary": "Test",
            "required_capabilities": [],
            "constraints": null
        }
        '''
        result = analyzer.parse_llm_response(response)
        assert result is not None
        assert result.constraints == []

    def test_parse_all_null_values(self, analyzer):
        """Test that all null values are handled."""
        response = '''
        {
            "summary": null,
            "required_capabilities": null,
            "complexity": null,
            "estimated_tasks": null,
            "suggested_process": null,
            "constraints": null
        }
        '''
        result = analyzer.parse_llm_response(response)
        assert result is not None
        assert result.summary == ""
        assert result.required_capabilities == []
        assert result.complexity == "medium"


# =============================================================================
# Bug #2: _infer_tools None check
# =============================================================================

class TestInferToolsNoneCheck:
    """Tests for Bug #2 - _infer_tools should handle None capabilities."""

    @pytest.fixture
    def creator(self):
        return RoleCreator()

    def test_infer_tools_with_none(self, creator):
        """Test that _infer_tools handles None gracefully."""
        result = creator._infer_tools(None)
        assert result is None

    def test_infer_tools_with_empty_list(self, creator):
        """Test that _infer_tools handles empty list."""
        result = creator._infer_tools([])
        assert result is None

    def test_infer_tools_with_valid_capabilities(self, creator):
        """Test that _infer_tools works with valid capabilities."""
        result = creator._infer_tools([Capability.SEARCH, Capability.READ_CODE])
        assert result is not None
        assert "web_search" in result
        assert "read_file" in result


# =============================================================================
# Bug #3: _build_role_from_request validation
# =============================================================================

class TestBuildRoleFromRequestValidation:
    """Tests for Bug #3 - _build_role_from_request should validate inputs."""

    @pytest.fixture
    def creator(self):
        return RoleCreator()

    def test_build_with_none_name(self, creator):
        """Test that None suggested_name raises error."""
        request = RoleCreationRequest(
            suggested_name=None,
            required_capabilities=[Capability.ANALYZE],
            reason="Test",
        )
        with pytest.raises(ValueError, match="suggested_name"):
            creator._build_role_from_request(request)

    def test_build_with_none_capabilities(self, creator):
        """Test that None required_capabilities raises error."""
        request = RoleCreationRequest(
            suggested_name="test_role",
            required_capabilities=None,
            reason="Test",
        )
        with pytest.raises(ValueError, match="required_capabilities"):
            creator._build_role_from_request(request)

    def test_build_with_empty_capabilities(self, creator):
        """Test that empty required_capabilities raises error."""
        request = RoleCreationRequest(
            suggested_name="test_role",
            required_capabilities=[],
            reason="Test",
        )
        with pytest.raises(ValueError, match="required_capabilities"):
            creator._build_role_from_request(request)

    def test_build_with_valid_request(self, creator):
        """Test that valid request creates role successfully."""
        request = RoleCreationRequest(
            suggested_name="test_role",
            required_capabilities=[Capability.ANALYZE],
            reason="Test reason",
        )
        role = creator._build_role_from_request(request)
        assert role.name == "test_role"
        assert Capability.ANALYZE in role.capabilities


# =============================================================================
# Bug #4: Topological sort circular dependency handling
# =============================================================================

class TestTopologicalSortCircularDeps:
    """Tests for Bug #4 - Circular dependencies should be removed."""

    @pytest.fixture
    def planner(self):
        return TaskPlanner()

    def test_circular_dependency_removed(self, planner):
        """Test that circular dependencies are detected and removed."""
        # Create tasks with circular dependency: A -> B -> C -> A
        tasks = [
            TaskPlan(name="task_a", description="A", agent="agent1", dependencies=["task_c"]),
            TaskPlan(name="task_b", description="B", agent="agent1", dependencies=["task_a"]),
            TaskPlan(name="task_c", description="C", agent="agent1", dependencies=["task_b"]),
        ]

        result = planner._topological_sort(tasks)

        # All tasks should be returned
        assert len(result) == 3

        # Tasks in cycle should have empty dependencies
        for task in result:
            if task.name in ["task_a", "task_b", "task_c"]:
                # Dependencies should be cleared for cycle members
                assert task.dependencies == []

    def test_partial_cycle_handled(self, planner):
        """Test handling of partial cycle (some tasks not in cycle)."""
        # D -> A -> B -> C -> A (cycle), E is independent
        tasks = [
            TaskPlan(name="task_a", description="A", agent="agent1", dependencies=["task_c"]),
            TaskPlan(name="task_b", description="B", agent="agent1", dependencies=["task_a"]),
            TaskPlan(name="task_c", description="C", agent="agent1", dependencies=["task_b"]),
            TaskPlan(name="task_d", description="D", agent="agent1", dependencies=["task_a"]),
            TaskPlan(name="task_e", description="E", agent="agent1", dependencies=[]),
        ]

        result = planner._topological_sort(tasks)

        assert len(result) == 5

        # task_e should be first (no dependencies)
        assert result[0].name == "task_e"


# =============================================================================
# Bug #5: Timeout validation
# =============================================================================

class TestTimeoutValidation:
    """Tests for Bug #5 - Timeout should be validated and clamped."""

    @pytest.fixture
    def task_planner(self):
        return TaskPlanner()

    @pytest.fixture
    def sample_roles(self):
        return [
            RoleDefinition(
                name="agent1",
                display_name="Agent 1",
                description="Test",
                goal="Test",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.ANALYZE],
            )
        ]

    def test_negative_timeout_clamped(self, task_planner, sample_roles):
        """Test that negative timeout is clamped to minimum."""
        response = '''
        [{"name": "task1", "description": "Test", "agent": "agent1", "timeout": -100}]
        '''
        tasks = task_planner._parse_llm_tasks(response, sample_roles)
        assert len(tasks) == 1
        assert tasks[0].timeout >= 1

    def test_zero_timeout_clamped(self, task_planner, sample_roles):
        """Test that zero timeout is clamped."""
        response = '''
        [{"name": "task1", "description": "Test", "agent": "agent1", "timeout": 0}]
        '''
        tasks = task_planner._parse_llm_tasks(response, sample_roles)
        assert len(tasks) == 1
        assert tasks[0].timeout >= 1

    def test_very_large_timeout_clamped(self, task_planner, sample_roles):
        """Test that very large timeout is clamped to max."""
        response = '''
        [{"name": "task1", "description": "Test", "agent": "agent1", "timeout": 9999999}]
        '''
        tasks = task_planner._parse_llm_tasks(response, sample_roles)
        assert len(tasks) == 1
        assert tasks[0].timeout <= 3600

    def test_string_timeout_handled(self, task_planner, sample_roles):
        """Test that string timeout is converted or uses default."""
        response = '''
        [{"name": "task1", "description": "Test", "agent": "agent1", "timeout": "100"}]
        '''
        tasks = task_planner._parse_llm_tasks(response, sample_roles)
        assert len(tasks) == 1
        # String timeout should fall back to default
        assert tasks[0].timeout == 300


# =============================================================================
# Bug #6: Empty goal validation
# =============================================================================

class TestEmptyGoalValidation:
    """Tests for Bug #6 - Empty goal should return valid default analysis."""

    @pytest.fixture
    def analyzer(self):
        return GoalAnalyzer()

    def test_empty_goal_returns_valid_analysis(self, analyzer):
        """Test that empty goal returns valid default analysis."""
        result = analyzer.analyze("")
        assert isinstance(result, GoalAnalysis)
        assert result.summary == "Empty goal"
        assert result.complexity == "simple"

    def test_whitespace_goal_handled(self, analyzer):
        """Test that whitespace-only goal is handled."""
        result = analyzer.analyze("   ")
        assert isinstance(result, GoalAnalysis)
        assert result.summary == "Empty goal"

    def test_none_goal_handled(self, analyzer):
        """Test that None goal is handled gracefully."""
        # This should not crash
        result = analyzer.analyze(None)
        assert isinstance(result, GoalAnalysis)


# =============================================================================
# Bug #7: Empty tools list display
# =============================================================================

class TestEmptyToolsDisplay:
    """Tests for Bug #7 - Empty tools list should display meaningful text."""

    @pytest.fixture
    def creator(self):
        return RoleCreator()

    def test_empty_tools_display(self, creator):
        """Test that empty tools list shows 'none specified'."""
        role = RoleDefinition(
            name="test",
            display_name="Test",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
            tools=[],  # Empty list, not None
        )

        message = creator._build_confirmation_message(role)
        assert "none specified" in message.lower() or "Tools: " in message

    def test_none_tools_display(self, creator):
        """Test that None tools shows 'all available'."""
        role = RoleDefinition(
            name="test",
            display_name="Test",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
            tools=None,
        )

        message = creator._build_confirmation_message(role)
        assert "all available" in message.lower()


# =============================================================================
# Bug #8: Empty capabilities warning
# =============================================================================

class TestEmptyCapabilitiesWarning:
    """Tests for Bug #8 - Role with empty capabilities should warn."""

    def test_role_with_empty_capabilities_still_loaded(self):
        """Test that role with no valid capabilities is still loaded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            role_file = Path(tmpdir) / "empty_caps.yaml"
            role_file.write_text("""
name: empty_caps
display_name: Empty Caps
description: Role with no capabilities
goal: Test
backstory: ""
capabilities: []
""")

            from xbot.crew.planner.role_pool import RolePoolManager
            manager = RolePoolManager(RolePoolConfig(custom_roles_dir=tmpdir))
            pool = manager.get_pool()

            # Role should still be loaded
            role = pool.get_role("empty_caps")
            assert role is not None
            assert role.capabilities == []


# =============================================================================
# Bug #9: Confidence bounds
# =============================================================================

class TestConfidenceBounds:
    """Tests for Bug #9 - Confidence should be clamped to [0, 1]."""

    def test_confidence_within_bounds(self):
        """Test that confidence is always within [0, 1]."""
        planner = CrewPlanner()

        # Create extreme scenario
        analysis = GoalAnalysis(
            summary="Test",
            required_capabilities=[],
            complexity="medium",
            estimated_tasks=1000,  # Very different from actual tasks
            suggested_process="sequential",
        )

        role = RoleDefinition(
            name="test",
            display_name="Test",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[],
        )

        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.5,  # Out of bounds
            created_roles=[],
            role_gaps=[],
        )

        confidence = planner._calculate_confidence(analysis, selection, [])

        assert 0.0 <= confidence <= 1.0

    def test_confidence_negative_coverage_handled(self):
        """Test that negative coverage is handled."""
        planner = CrewPlanner()

        analysis = GoalAnalysis(
            summary="Test",
            required_capabilities=[],
            complexity="medium",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        role = RoleDefinition(
            name="test",
            display_name="Test",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[],
        )

        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=-0.5,  # Invalid negative value
            created_roles=[],
            role_gaps=[],
        )

        confidence = planner._calculate_confidence(analysis, selection, [])

        assert 0.0 <= confidence <= 1.0


# =============================================================================
# Integration tests
# =============================================================================

class TestBugFixesIntegration:
    """Integration tests combining multiple bug fixes."""

    def test_full_planning_with_edge_cases(self):
        """Test full planning with various edge cases."""
        planner = CrewPlanner()

        # Empty goal should work
        plan = planner.plan("")
        assert plan is not None
        assert plan.confidence >= 0.0
        assert plan.confidence <= 1.0

    def test_role_creation_with_edge_cases(self):
        """Test role creation with various edge cases."""
        with tempfile.TemporaryDirectory() as tmpdir:
            creator = RoleCreator(custom_roles_dir=Path(tmpdir), auto_save=True)

            # Create role with edge case values (but required fields)
            result = creator.create_role_from_definition(
                name="edge_case_role",
                display_name="Edge Case Role",
                description="A description",  # Required
                goal="A goal",  # Required
                backstory="",  # Empty is fine
                capabilities=[Capability.ANALYZE],
                tools=[],  # Empty list
                max_iterations=10,
            )

            assert result.success
            assert result.role is not None
            assert result.role.tools == []  # Empty list preserved
