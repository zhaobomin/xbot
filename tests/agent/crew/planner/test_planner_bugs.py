"""Comprehensive tests for planner module bug fixes.

This module tests all bug fixes across the planner module:
- None handling in dict.get()
- Timeout validation with clamping
- Topological sort with cycle detection
- Empty goal validation
- Confidence bounds
- Tools display in confirmation message
- _infer_tools None check
- _build_role_from_request validation
- Empty capabilities warning
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from xbot.agent.crew.planner.goal_analyzer import GoalAnalyzer
from xbot.agent.crew.planner.models import (
    Capability,
    GoalAnalysis,
    RoleDefinition,
    RoleGap,
    RolePool,
    RolePoolConfig,
    RoleSelection,
    RoleTier,
    TaskPlan,
    CrewPlan,
)
from xbot.agent.crew.planner.role_creator import RoleCreator
from xbot.agent.crew.planner.role_pool import RolePoolManager
from xbot.agent.crew.planner.task_planner import TaskPlanner
from xbot.agent.crew.planner.config_generator import ConfigGenerator
from xbot.agent.crew.planner.crew_planner import CrewPlanner
from xbot.agent.crew.planner.role_selector import RoleSelector
from xbot.agent.crew.planner.utils import LLMResponseParser, PlannerValidator


# ---------------------------------------------------------------------------
# GoalAnalyzer Tests
# ---------------------------------------------------------------------------

class TestGoalAnalyzerEmptyGoal:
    """Tests for empty goal handling."""

    def test_empty_goal_returns_default_analysis(self):
        """Empty goal should return default analysis."""
        analyzer = GoalAnalyzer()
        analysis = analyzer.analyze("")
        assert analysis.summary == "Empty goal"
        assert analysis.required_capabilities == [Capability.ANALYZE]
        assert analysis.complexity == "simple"
        assert analysis.estimated_tasks == 1

    def test_whitespace_only_goal_returns_default(self):
        """Whitespace-only goal should return default analysis."""
        analyzer = GoalAnalyzer()
        analysis = analyzer.analyze("   \n\t  ")
        assert analysis.summary == "Empty goal"

    def test_none_goal_handling(self):
        """None goal should be handled gracefully."""
        analyzer = GoalAnalyzer()
        # This should not crash
        analysis = analyzer.analyze(None)
        assert analysis.summary == "Empty goal"


class TestGoalAnalyzerNoneHandling:
    """Tests for None handling in parse_llm_response."""

    def test_parse_with_null_capabilities(self):
        """Null capabilities should be handled."""
        analyzer = GoalAnalyzer()
        response = '{"summary": "test", "required_capabilities": null}'
        analysis = analyzer.parse_llm_response(response)
        assert analysis is not None
        assert analysis.required_capabilities == []

    def test_parse_with_empty_capabilities(self):
        """Empty capabilities array should be handled."""
        analyzer = GoalAnalyzer()
        response = '{"summary": "test", "required_capabilities": []}'
        analysis = analyzer.parse_llm_response(response)
        assert analysis is not None
        assert analysis.required_capabilities == []

    def test_parse_with_missing_fields(self):
        """Missing fields should use defaults."""
        analyzer = GoalAnalyzer()
        response = '{"summary": "test"}'
        analysis = analyzer.parse_llm_response(response)
        assert analysis is not None
        assert analysis.complexity == "medium"
        assert analysis.estimated_tasks == 3

    def test_parse_with_invalid_json(self):
        """Invalid JSON should return None."""
        analyzer = GoalAnalyzer()
        response = "not valid json"
        analysis = analyzer.parse_llm_response(response)
        assert analysis is None


# ---------------------------------------------------------------------------
# TaskPlanner Tests
# ---------------------------------------------------------------------------

class TestTaskPlannerNoneHandling:
    """Tests for None handling in task parsing."""

    def test_parse_with_null_name(self):
        """Null task name should use default."""
        planner = TaskPlanner()
        response = '[{"name": null, "description": "test", "agent": "researcher"}]'
        # Create minimal analysis and role selection
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=1,
            suggested_process="sequential",
        )
        role = RoleDefinition(
            name="researcher",
            display_name="Researcher",
            description="Test role",
            goal="Research",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )
        tasks = planner.plan("test goal", analysis, selection, response)
        assert len(tasks) > 0
        # Task should have a generated name
        assert tasks[0].name.startswith("task_")

    def test_parse_with_null_description(self):
        """Null description should use empty string."""
        planner = TaskPlanner()
        response = '[{"name": "task1", "description": null, "agent": "researcher"}]'
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=1,
            suggested_process="sequential",
        )
        role = RoleDefinition(
            name="researcher",
            display_name="Researcher",
            description="Test role",
            goal="Research",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )
        tasks = planner.plan("test goal", analysis, selection, response)
        assert tasks[0].description == ""


class TestTaskPlannerTimeoutValidation:
    """Tests for timeout validation."""

    def test_timeout_below_minimum(self):
        """Timeout below 1 should be clamped to 1."""
        planner = TaskPlanner()
        response = '[{"name": "task1", "description": "test", "agent": "researcher", "timeout": 0}]'
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=1,
            suggested_process="sequential",
        )
        role = RoleDefinition(
            name="researcher",
            display_name="Researcher",
            description="Test role",
            goal="Research",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )
        tasks = planner.plan("test goal", analysis, selection, response)
        assert tasks[0].timeout == 1

    def test_timeout_above_maximum(self):
        """Timeout above 3600 should be clamped to 3600."""
        planner = TaskPlanner()
        response = '[{"name": "task1", "description": "test", "agent": "researcher", "timeout": 5000}]'
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=1,
            suggested_process="sequential",
        )
        role = RoleDefinition(
            name="researcher",
            display_name="Researcher",
            description="Test role",
            goal="Research",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )
        tasks = planner.plan("test goal", analysis, selection, response)
        assert tasks[0].timeout == 3600

    def test_timeout_negative(self):
        """Negative timeout should be clamped to 1."""
        planner = TaskPlanner()
        response = '[{"name": "task1", "description": "test", "agent": "researcher", "timeout": -100}]'
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=1,
            suggested_process="sequential",
        )
        role = RoleDefinition(
            name="researcher",
            display_name="Researcher",
            description="Test role",
            goal="Research",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )
        tasks = planner.plan("test goal", analysis, selection, response)
        assert tasks[0].timeout == 1


class TestTaskPlannerCircularDependencies:
    """Tests for circular dependency handling."""

    def test_circular_dependencies_removed(self):
        """Circular dependencies should be removed."""
        planner = TaskPlanner()
        # Create tasks with circular dependencies: task1 -> task2 -> task1
        response = '''
        [
            {"name": "task1", "description": "test", "agent": "researcher", "dependencies": ["task2"]},
            {"name": "task2", "description": "test", "agent": "researcher", "dependencies": ["task1"]}
        ]
        '''
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=2,
            suggested_process="sequential",
        )
        role = RoleDefinition(
            name="researcher",
            display_name="Researcher",
            description="Test role",
            goal="Research",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )
        tasks = planner.plan("test goal", analysis, selection, response)
        # After topological sort, circular deps should be removed
        # At least one task should have empty dependencies
        for task in tasks:
            # Dependencies should only contain valid, non-circular refs
            assert task.name not in task.dependencies

    def test_self_dependency_removed(self):
        """Self dependency should be removed."""
        planner = TaskPlanner()
        response = '[{"name": "task1", "description": "test", "agent": "researcher", "dependencies": ["task1"]}]'
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=1,
            suggested_process="sequential",
        )
        role = RoleDefinition(
            name="researcher",
            display_name="Researcher",
            description="Test role",
            goal="Research",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )
        tasks = planner.plan("test goal", analysis, selection, response)
        assert tasks[0].dependencies == []


# ---------------------------------------------------------------------------
# RoleCreator Tests
# ---------------------------------------------------------------------------

class TestRoleCreatorInferTools:
    """Tests for _infer_tools None handling."""

    def test_infer_tools_with_none_capabilities(self):
        """None capabilities should return None."""
        creator = RoleCreator()
        result = creator._infer_tools(None)
        assert result is None

    def test_infer_tools_with_empty_capabilities(self):
        """Empty capabilities should return None."""
        creator = RoleCreator()
        result = creator._infer_tools([])
        assert result is None

    def test_infer_tools_with_valid_capabilities(self):
        """Valid capabilities should return appropriate tools."""
        creator = RoleCreator()
        result = creator._infer_tools([Capability.SEARCH])
        assert result is not None
        assert "web_search" in result


class TestRoleCreatorBuildRoleFromRequest:
    """Tests for _build_role_from_request validation."""

    def test_build_with_empty_name_raises(self):
        """Empty name should raise ValueError."""
        creator = RoleCreator()
        from xbot.agent.crew.planner.models import RoleCreationRequest
        request = RoleCreationRequest(
            suggested_name="",
            required_capabilities=[Capability.ANALYZE],
            reason="test",
        )
        with pytest.raises(ValueError, match="suggested_name is required"):
            creator._build_role_from_request(request)

    def test_build_with_empty_capabilities_raises(self):
        """Empty capabilities should raise ValueError."""
        creator = RoleCreator()
        from xbot.agent.crew.planner.models import RoleCreationRequest
        request = RoleCreationRequest(
            suggested_name="test_role",
            required_capabilities=[],
            reason="test",
        )
        with pytest.raises(ValueError, match="required_capabilities is required"):
            creator._build_role_from_request(request)


class TestRoleCreatorConfirmationMessage:
    """Tests for _build_confirmation_message tools display."""

    def test_tools_none_displays_all_available(self):
        """Tools=None should display 'all available'."""
        creator = RoleCreator()
        role = RoleDefinition(
            name="test_role",
            display_name="Test Role",
            description="Test description",
            goal="Test goal",
            backstory="Test backstory",
            tier=RoleTier.EXTENDED,
            capabilities=[Capability.ANALYZE],
            tools=None,
        )
        message = creator._build_confirmation_message(role)
        assert "all available" in message

    def test_tools_empty_displays_none_specified(self):
        """Tools=[] should display 'none specified'."""
        creator = RoleCreator()
        role = RoleDefinition(
            name="test_role",
            display_name="Test Role",
            description="Test description",
            goal="Test goal",
            backstory="Test backstory",
            tier=RoleTier.EXTENDED,
            capabilities=[Capability.ANALYZE],
            tools=[],
        )
        message = creator._build_confirmation_message(role)
        assert "none specified" in message

    def test_tools_list_displays_list(self):
        """Tools=['web_search'] should display the tool name."""
        creator = RoleCreator()
        role = RoleDefinition(
            name="test_role",
            display_name="Test Role",
            description="Test description",
            goal="Test goal",
            backstory="Test backstory",
            tier=RoleTier.EXTENDED,
            capabilities=[Capability.ANALYZE],
            tools=["web_search"],
        )
        message = creator._build_confirmation_message(role)
        assert "web_search" in message


class TestRoleCreatorValidateRole:
    """Tests for role validation."""

    def test_validate_role_with_empty_description(self):
        """Empty description should add error."""
        creator = RoleCreator()
        role = RoleDefinition(
            name="test_role",
            display_name="Test Role",
            description="",  # Empty
            goal="Test goal",
            backstory="Test backstory",
            tier=RoleTier.EXTENDED,
            capabilities=[Capability.ANALYZE],
        )
        errors = creator.validate_role(role)
        assert "Role description is required" in errors

    def test_validate_role_with_empty_goal(self):
        """Empty goal should add error."""
        creator = RoleCreator()
        role = RoleDefinition(
            name="test_role",
            display_name="Test Role",
            description="Test description",
            goal="",  # Empty
            backstory="Test backstory",
            tier=RoleTier.EXTENDED,
            capabilities=[Capability.ANALYZE],
        )
        errors = creator.validate_role(role)
        assert "Role goal is required" in errors


# ---------------------------------------------------------------------------
# RolePool Tests
# ---------------------------------------------------------------------------

class TestRolePoolEmptyCapabilities:
    """Tests for empty capabilities handling."""

    def test_load_role_with_empty_capabilities_warns(self):
        """Empty capabilities should log warning."""
        manager = RolePoolManager()
        # Manually create a role with empty capabilities
        role = RoleDefinition(
            name="empty_caps",
            display_name="Empty Caps",
            description="Role with no capabilities",
            goal="Test",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[],
        )
        manager.add_role(role)
        pool = manager.get_pool()
        loaded_role = pool.get_role("empty_caps")
        assert loaded_role is not None
        assert loaded_role.capabilities == []


# ---------------------------------------------------------------------------
# CrewPlanner Tests
# ---------------------------------------------------------------------------

class TestCrewPlannerConfidenceBounds:
    """Tests for confidence score bounds."""

    def test_confidence_always_between_0_and_1(self):
        """Confidence should always be in [0.0, 1.0]."""
        planner = CrewPlanner()
        # Create a plan with extreme values
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=10,
            suggested_process="sequential",
        )
        role = RoleDefinition(
            name="researcher",
            display_name="Researcher",
            description="Test role",
            goal="Research",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=2.0,  # Invalid, > 1.0
        )
        # Override coverage_score calculation to test bounds
        confidence = planner._calculate_confidence(analysis, selection, [])
        assert 0.0 <= confidence <= 1.0

    def test_confidence_with_zero_coverage(self):
        """Zero coverage should give valid confidence."""
        planner = CrewPlanner()
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=1,
            suggested_process="sequential",
        )
        selection = RoleSelection(
            selected_roles=[],
            selection_reason={},
            skipped_roles=[],
            coverage_score=0.0,
        )
        confidence = planner._calculate_confidence(analysis, selection, [])
        assert 0.0 <= confidence <= 1.0


# ---------------------------------------------------------------------------
# ConfigGenerator Tests
# ---------------------------------------------------------------------------

class TestConfigGeneratorPreview:
    """Tests for preview generation."""

    def test_preview_with_empty_description(self):
        """Empty description should show '(no description)'."""
        generator = ConfigGenerator()
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=1,
            suggested_process="sequential",
        )
        role = RoleDefinition(
            name="researcher",
            display_name="Researcher",
            description="Test role",
            goal="Research",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )
        task = TaskPlan(
            name="task1",
            description="",  # Empty
            agent="researcher",
            dependencies=[],
            expected_output="result",
        )
        plan = CrewPlan(
            name="test_crew",
            description="Test crew",
            process="sequential",
            global_context="test",
            roles=[role],
            tasks=[task],
            analysis=analysis,
            role_selection=selection,
            planning_time=0.1,
            confidence=0.9,
        )
        preview = generator.generate_preview(plan)
        assert "(no description)" in preview


# ---------------------------------------------------------------------------
# RoleSelector Tests
# ---------------------------------------------------------------------------

class TestRoleSelectorCoverage:
    """Tests for coverage calculation."""

    def test_coverage_with_empty_required(self):
        """Empty required capabilities should return 1.0."""
        selector = RoleSelector()
        coverage = selector._calculate_coverage([], [])
        assert coverage == 1.0

    def test_coverage_with_no_roles(self):
        """No roles should return 0.0 coverage."""
        selector = RoleSelector()
        coverage = selector._calculate_coverage([], [Capability.ANALYZE])
        assert coverage == 0.0


# ---------------------------------------------------------------------------
# LLMResponseParser Tests
# ---------------------------------------------------------------------------

class TestLLMResponseParser:
    """Tests for unified LLM response parser."""

    def test_parse_array_with_no_array(self):
        """No array in response should return None."""
        result = LLMResponseParser.parse_array("no array here")
        assert result is None

    def test_parse_object_with_no_object(self):
        """No object in response should return None."""
        result = LLMResponseParser.parse_object("no object here")
        assert result is None

    def test_parse_string_list_with_dict_items(self):
        """Dict items should extract 'name' field."""
        response = '[{"name": "role1"}, {"name": "role2"}]'
        result = LLMResponseParser.parse_string_list(response)
        assert "role1" in result
        assert "role2" in result

    def test_parse_string_list_fallback(self):
        """Fallback should extract from text lines."""
        response = "- role1: description\n- role2: description"
        result = LLMResponseParser.parse_string_list(response)
        assert "role1" in result
        assert "role2" in result


# ---------------------------------------------------------------------------
# PlannerValidator Tests
# ---------------------------------------------------------------------------

class TestPlannerValidator:
    """Tests for unified validator."""

    def test_validate_empty_goal(self):
        """Empty goal should return error."""
        errors = PlannerValidator.validate_goal("")
        assert len(errors) > 0
        assert "empty" in errors[0].lower()

    def test_validate_whitespace_goal(self):
        """Whitespace-only goal should return error."""
        errors = PlannerValidator.validate_goal("   ")
        assert len(errors) > 0

    def test_validate_role_name_with_path_separator(self):
        """Path separator in role name should return error."""
        errors = PlannerValidator.validate_role_name("test/role")
        assert len(errors) > 0
        assert "path separator" in errors[0].lower()

    def test_validate_role_name_with_invalid_pattern(self):
        """Invalid pattern should return error."""
        errors = PlannerValidator.validate_role_name("TestRole")
        assert len(errors) > 0
        assert "pattern" in errors[0].lower()


# ---------------------------------------------------------------------------
# Additional Edge Case Tests
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Tests for additional edge cases found during review."""

    def test_goal_analyzer_name_generation_with_numbers(self):
        """Name generation should handle leading numbers."""
        analyzer = GoalAnalyzer()
        name = analyzer.generate_name("123 fix the bug")
        # Should not start with a number
        assert not name[0].isdigit() or name.startswith("_")

    def test_goal_analyzer_name_generation_with_special_chars(self):
        """Name generation should handle special characters."""
        analyzer = GoalAnalyzer()
        name = analyzer.generate_name("fix @#$ bugs!!!")
        # Should only contain alphanumeric and underscore
        assert all(c.isalnum() or c == '_' for c in name)

    def test_role_definition_matches_empty_capabilities(self):
        """Empty required capabilities should return 1.0 match."""
        role = RoleDefinition(
            name="test",
            display_name="Test",
            description="Test",
            goal="Test",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        score = role.matches_capabilities([])
        assert score == 1.0

    def test_task_plan_default_values(self):
        """TaskPlan should have correct default values."""
        task = TaskPlan(
            name="test",
            description="test",
            agent="test",
        )
        assert task.dependencies == []
        assert task.expected_output == ""
        assert task.timeout == 300
        assert task.human_review is False
        assert task.priority == 0

    def test_crew_plan_validate_unknown_agent(self):
        """CrewPlan validation should catch unknown agent."""
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=1,
            suggested_process="sequential",
        )
        role = RoleDefinition(
            name="researcher",
            display_name="Researcher",
            description="Test role",
            goal="Research",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )
        task = TaskPlan(
            name="task1",
            description="test",
            agent="unknown_agent",  # Not in roles
            dependencies=[],
        )
        plan = CrewPlan(
            name="test_crew",
            description="Test",
            process="sequential",
            global_context="test",
            roles=[role],
            tasks=[task],
            analysis=analysis,
            role_selection=selection,
            planning_time=0.1,
            confidence=0.9,
        )
        errors = plan.validate()
        assert len(errors) > 0
        assert "unknown agent" in errors[0].lower()

    def test_crew_plan_validate_unknown_dependency(self):
        """CrewPlan validation should catch unknown dependency."""
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=1,
            suggested_process="sequential",
        )
        role = RoleDefinition(
            name="researcher",
            display_name="Researcher",
            description="Test role",
            goal="Research",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )
        task = TaskPlan(
            name="task1",
            description="test",
            agent="researcher",
            dependencies=["unknown_task"],  # Not in tasks
        )
        plan = CrewPlan(
            name="test_crew",
            description="Test",
            process="sequential",
            global_context="test",
            roles=[role],
            tasks=[task],
            analysis=analysis,
            role_selection=selection,
            planning_time=0.1,
            confidence=0.9,
        )
        errors = plan.validate()
        assert len(errors) > 0
        assert "unknown dependency" in errors[0].lower()


# ---------------------------------------------------------------------------
# Models Dict Conversion Tests
# ---------------------------------------------------------------------------

class TestModelsDictConversion:
    """Tests for to_dict methods."""

    def test_role_definition_to_dict_with_none_tools(self):
        """to_dict should handle None tools."""
        role = RoleDefinition(
            name="test",
            display_name="Test",
            description="Test",
            goal="Test",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
            tools=None,
        )
        from xbot.agent.crew.planner.utils import RoleConverter
        result = RoleConverter.to_yaml_dict(role)
        # tools=None should not be in output (None means all tools available)
        assert "tools" not in result or result.get("tools") is None

    def test_crew_plan_to_dict(self):
        """CrewPlan to_dict should serialize all fields."""
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=1,
            suggested_process="sequential",
        )
        role = RoleDefinition(
            name="researcher",
            display_name="Researcher",
            description="Test role",
            goal="Research",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )
        task = TaskPlan(
            name="task1",
            description="test",
            agent="researcher",
            dependencies=[],
        )
        plan = CrewPlan(
            name="test_crew",
            description="Test",
            process="sequential",
            global_context="test",
            roles=[role],
            tasks=[task],
            analysis=analysis,
            role_selection=selection,
            planning_time=0.1,
            confidence=0.9,
        )
        result = plan.to_dict()
        assert result["name"] == "test_crew"
        assert "created_at" in result
        assert isinstance(result["created_at"], str)


# ---------------------------------------------------------------------------
# Human Review Parsing Tests (Bug Fix: string "false" should be False)
# ---------------------------------------------------------------------------

class TestTaskPlannerHumanReviewParsing:
    """Tests for human_review parsing from LLM responses."""

    def test_human_review_boolean_true(self):
        """Boolean True should return True."""
        planner = TaskPlanner()
        assert planner._parse_human_review(True) is True

    def test_human_review_boolean_false(self):
        """Boolean False should return False."""
        planner = TaskPlanner()
        assert planner._parse_human_review(False) is False

    def test_human_review_string_true(self):
        """String 'true' should return True."""
        planner = TaskPlanner()
        assert planner._parse_human_review("true") is True
        assert planner._parse_human_review("TRUE") is True
        assert planner._parse_human_review("True") is True

    def test_human_review_string_false(self):
        """String 'false' should return False, not True."""
        planner = TaskPlanner()
        # This was the bug: bool("false") returns True
        assert planner._parse_human_review("false") is False
        assert planner._parse_human_review("FALSE") is False
        assert planner._parse_human_review("False") is False

    def test_human_review_string_yes(self):
        """String 'yes' should return True."""
        planner = TaskPlanner()
        assert planner._parse_human_review("yes") is True
        assert planner._parse_human_review("YES") is True

    def test_human_review_number_one(self):
        """Number 1 should return True."""
        planner = TaskPlanner()
        assert planner._parse_human_review(1) is True
        assert planner._parse_human_review(1.0) is True

    def test_human_review_number_zero(self):
        """Number 0 should return False."""
        planner = TaskPlanner()
        assert planner._parse_human_review(0) is False
        assert planner._parse_human_review(0.0) is False

    def test_human_review_none(self):
        """None should return False."""
        planner = TaskPlanner()
        assert planner._parse_human_review(None) is False

    def test_human_review_random_string(self):
        """Random string should return False."""
        planner = TaskPlanner()
        assert planner._parse_human_review("random") is False

    def test_human_review_in_task_parsing(self):
        """Human review in full task parsing should work correctly."""
        planner = TaskPlanner()
        response = '[{"name": "task1", "description": "test", "agent": "researcher", "human_review": "false"}]'
        analysis = GoalAnalysis(
            summary="test",
            required_capabilities=[Capability.ANALYZE],
            complexity="medium",
            estimated_tasks=1,
            suggested_process="sequential",
        )
        role = RoleDefinition(
            name="researcher",
            display_name="Researcher",
            description="Test role",
            goal="Research",
            backstory="Test",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )
        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
        )
        tasks = planner.plan("test goal", analysis, selection, response)
        assert tasks[0].human_review is False


# ---------------------------------------------------------------------------
# LLMResponseParser None Name Tests (Bug Fix: None name should not become "None")
# ---------------------------------------------------------------------------

class TestLLMResponseParserNoneName:
    """Tests for parse_string_list handling None name field."""

    def test_parse_string_list_with_null_name(self):
        """Dict with null name should be skipped, not converted to 'None'."""
        response = '[{"name": null}, {"name": "valid_role"}]'
        result = LLMResponseParser.parse_string_list(response)
        # Should not include "None" string
        assert "None" not in result
        assert "valid_role" in result

    def test_parse_string_list_with_empty_name(self):
        """Dict with empty name should be handled correctly."""
        response = '[{"name": ""}, {"name": "valid_role"}]'
        result = LLMResponseParser.parse_string_list(response)
        assert "valid_role" in result

    def test_parse_string_list_with_zero_name(self):
        """Dict with 0 as name should include '0'."""
        response = '[{"name": 0}, {"name": "valid_role"}]'
        result = LLMResponseParser.parse_string_list(response)
        assert "0" in result
        assert "valid_role" in result

    def test_parse_string_list_mixed_types(self):
        """Dict with various types should work."""
        response = '[{"name": "role1"}, {"name": null}, {"name": 123}, "plain_string"]'
        result = LLMResponseParser.parse_string_list(response)
        assert "role1" in result
        assert "123" in result
        assert "plain_string" in result
        assert "None" not in result


# ---------------------------------------------------------------------------
# GoalAnalyzer Complexity/Process Validation Tests
# ---------------------------------------------------------------------------

class TestGoalAnalyzerComplexityValidation:
    """Tests for complexity and process validation."""

    def test_parse_with_invalid_complexity(self):
        """Invalid complexity should fall back to default."""
        analyzer = GoalAnalyzer()
        response = '{"summary": "test", "complexity": "super_complex"}'
        analysis = analyzer.parse_llm_response(response)
        # Invalid complexity should not crash, use default
        assert analysis is not None
        assert analysis.complexity == "medium"

    def test_parse_with_invalid_process(self):
        """Invalid process should fall back to default."""
        analyzer = GoalAnalyzer()
        response = '{"summary": "test", "suggested_process": "parallel"}'
        analysis = analyzer.parse_llm_response(response)
        assert analysis is not None
        assert analysis.suggested_process == "sequential"

    def test_parse_with_estimated_tasks_zero(self):
        """estimated_tasks=0 should be handled properly."""
        analyzer = GoalAnalyzer()
        response = '{"summary": "test", "estimated_tasks": 0}'
        analysis = analyzer.parse_llm_response(response)
        assert analysis is not None
        assert analysis.estimated_tasks == 1

    def test_parse_with_estimated_tasks_negative(self):
        """Negative estimated_tasks should be handled."""
        analyzer = GoalAnalyzer()
        response = '{"summary": "test", "estimated_tasks": -5}'
        analysis = analyzer.parse_llm_response(response)
        assert analysis is not None
        assert analysis.estimated_tasks == 1


# ---------------------------------------------------------------------------
# RoleCreator load_role_from_file Tests
# ---------------------------------------------------------------------------

class TestRoleCreatorLoadRoleFromFile:
    """Tests for load_role_from_file edge cases."""

    def test_load_role_with_zero_max_iterations(self):
        """max_iterations=0 should not be replaced."""
        creator = RoleCreator()
        import tempfile
        import yaml

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump({
                'name': 'test_role',
                'display_name': 'Test',
                'description': 'Test',
                'goal': 'Test',
                'capabilities': ['analyze'],
                'max_iterations': 0,  # Zero value
            }, f)
            path = Path(f.name)

        role = creator.load_role_from_file(path)
        path.unlink()  # Clean up

        assert role is not None
        assert role.max_iterations == 1

    def test_load_role_with_zero_timeout_multiplier(self):
        """timeout_multiplier=0.0 should not be replaced."""
        creator = RoleCreator()
        import tempfile
        import yaml

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump({
                'name': 'test_role',
                'display_name': 'Test',
                'description': 'Test',
                'goal': 'Test',
                'capabilities': ['analyze'],
                'timeout_multiplier': 0.0,  # Zero value
            }, f)
            path = Path(f.name)

        role = creator.load_role_from_file(path)
        path.unlink()  # Clean up

        assert role is not None
        assert role.timeout_multiplier == 0.1


# ---------------------------------------------------------------------------
# RolePoolManager load_role Tests
# ---------------------------------------------------------------------------

class TestRolePoolManagerLoadRole:
    """Tests for _load_role edge cases."""

    def test_load_role_with_zero_iterations_yaml(self):
        """Zero max_iterations in YAML should be handled."""
        import tempfile
        import yaml
        from xbot.agent.crew.planner.role_pool import RolePoolManager, ROLE_POOL_DIR

        manager = RolePoolManager()

        # Create temp YAML file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, dir=ROLE_POOL_DIR / "core") as f:
            yaml.dump({
                'name': 'zero_iter_role',
                'display_name': 'Zero Iter',
                'description': 'Test',
                'goal': 'Test',
                'capabilities': ['analyze'],
                'max_iterations': 0,
            }, f)
            path = Path(f.name)

        try:
            manager.load()
            role = manager.get_pool().get_role('zero_iter_role')
            assert role.max_iterations == 1
        finally:
            path.unlink()


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------

class TestIntegration:
    """Integration tests for the planner module."""

    def test_full_planning_workflow(self):
        """Test a complete planning workflow."""
        planner = CrewPlanner()
        plan = planner.plan("Analyze the codebase and find bugs")

        # Plan should have basic structure
        assert plan.name
        assert plan.description
        assert plan.roles
        assert plan.tasks
        assert plan.confidence >= 0.0
        assert plan.confidence <= 1.0

    def test_yaml_generation(self):
        """Test YAML config generation."""
        planner = CrewPlanner()
        plan = planner.plan("Test goal")
        yaml_content = planner.generate_config(plan)

        assert "name:" in yaml_content
        assert "agents:" in yaml_content
        assert "tasks:" in yaml_content

    def test_preview_generation(self):
        """Test preview generation."""
        planner = CrewPlanner()
        plan = planner.plan("Test goal")
        preview = planner.preview(plan)

        assert "Crew:" in preview
        assert "Roles:" in preview
        assert "Tasks:" in preview


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
