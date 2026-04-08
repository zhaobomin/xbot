"""Tests for the utility classes in utils.py."""

import tempfile

import pytest

from xbot.crew.planner.models import (
    Capability,
    RoleDefinition,
    RoleTier,
)
from xbot.crew.planner.utils import (
    DependencyTracker,
    LLMResponseParser,
    PlannerValidator,
    RoleConverter,
)


class TestLLMResponseParser:
    """Tests for LLMResponseParser."""

    def test_parse_array_simple(self):
        """Test parsing a simple JSON array."""
        response = '["item1", "item2", "item3"]'
        result = LLMResponseParser.parse_array(response)
        assert result == ["item1", "item2", "item3"]

    def test_parse_array_with_surrounding_text(self):
        """Test parsing JSON array embedded in text."""
        response = '''
        Here are the results:
        ["task1", "task2", "task3"]
        That's all.
        '''
        result = LLMResponseParser.parse_array(response)
        assert result == ["task1", "task2", "task3"]

    def test_parse_array_with_objects(self):
        """Test parsing JSON array of objects."""
        response = '''
        [
            {"name": "task1", "description": "First task"},
            {"name": "task2", "description": "Second task"}
        ]
        '''
        result = LLMResponseParser.parse_array(response)
        assert len(result) == 2
        assert result[0]["name"] == "task1"

    def test_parse_array_no_array_found(self):
        """Test when no JSON array is present."""
        response = "No JSON here, just text."
        result = LLMResponseParser.parse_array(response)
        assert result is None

    def test_parse_array_malformed_json(self):
        """Test handling malformed JSON."""
        response = '["item1", "item2"'
        result = LLMResponseParser.parse_array(response)
        assert result is None

    def test_parse_object_simple(self):
        """Test parsing a simple JSON object."""
        response = '{"key": "value", "number": 42}'
        result = LLMResponseParser.parse_object(response)
        assert result == {"key": "value", "number": 42}

    def test_parse_object_with_surrounding_text(self):
        """Test parsing JSON object embedded in text."""
        response = '''
        Analysis result:
        {
            "summary": "Test",
            "complexity": "medium"
        }
        End.
        '''
        result = LLMResponseParser.parse_object(response)
        assert result["summary"] == "Test"

    def test_parse_object_nested(self):
        """Test parsing nested JSON object."""
        response = '{"outer": {"inner": "value"}}'
        result = LLMResponseParser.parse_object(response)
        assert result["outer"]["inner"] == "value"

    def test_parse_string_list_from_json(self):
        """Test parsing string list from JSON."""
        response = '["name1", "name2", "name3"]'
        result = LLMResponseParser.parse_string_list(response)
        assert result == ["name1", "name2", "name3"]

    def test_parse_string_list_from_objects(self):
        """Test extracting names from object array."""
        response = '[{"name": "agent1"}, {"name": "agent2"}]'
        result = LLMResponseParser.parse_string_list(response)
        assert result == ["agent1", "agent2"]

    def test_parse_string_list_fallback_to_text(self):
        """Test fallback text parsing."""
        response = '''
        Selected roles:
        - researcher: Primary role
        - coder: Secondary role
        '''
        result = LLMResponseParser.parse_string_list(response)
        # Should extract names from text lines
        assert "researcher" in result or len(result) >= 1

    def test_parse_string_list_empty(self):
        """Test parsing empty response."""
        response = ''
        result = LLMResponseParser.parse_string_list(response)
        assert result == []


class TestRoleConverter:
    """Tests for RoleConverter."""

    @pytest.fixture
    def sample_role(self):
        return RoleDefinition(
            name="test_role",
            display_name="Test Role",
            description="A test role",
            goal="Test goal",
            backstory="Test backstory",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE, Capability.SEARCH],
            tools=["read_file", "web_search"],
            tool_restrictions=["bash"],
            max_iterations=20,
            timeout_multiplier=1.5,
            tags=["test"],
            examples=["example1"],
        )

    def test_to_yaml_dict_complete(self, sample_role):
        """Test complete YAML dict conversion."""
        result = RoleConverter.to_yaml_dict(sample_role)
        assert result["name"] == "test_role"
        assert result["display_name"] == "Test Role"
        assert result["tier"] == "core"
        assert result["capabilities"] == ["analyze", "search"]
        assert result["tools"] == ["read_file", "web_search"]
        assert result["tool_restrictions"] == ["bash"]
        assert result["max_iterations"] == 20

    def test_to_yaml_dict_minimal(self):
        """Test YAML dict with minimal role."""
        role = RoleDefinition(
            name="minimal",
            display_name="Minimal",
            description="",
            goal="",
            backstory="",
            tier=RoleTier.EXTENDED,
            capabilities=[],
        )
        result = RoleConverter.to_yaml_dict(role)
        assert result["name"] == "minimal"
        assert result["tier"] == "extended"
        assert result["capabilities"] == []

    def test_to_agent_config_complete(self, sample_role):
        """Test agent config conversion."""
        result = RoleConverter.to_agent_config(sample_role)
        assert result["name"] == "test_role"
        assert result["description"] == "A test role"
        assert result["goal"] == "Test goal"
        assert result["backstory"] == "Test backstory"
        assert result["tools"] == ["read_file", "web_search"]
        assert result["tool_restrictions"] == ["bash"]
        assert result["max_iterations"] == 20

    def test_to_agent_config_none_tools(self):
        """Test agent config with None tools (all available)."""
        role = RoleDefinition(
            name="all_tools",
            display_name="All Tools",
            description="",
            goal="",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
            tools=None,  # All tools available
        )
        result = RoleConverter.to_agent_config(role)
        # None tools should not be included in config
        assert "tools" not in result or result.get("tools") is None

    def test_to_agent_config_empty_tools(self):
        """Test agent config with empty tools list."""
        role = RoleDefinition(
            name="no_tools",
            display_name="No Tools",
            description="",
            goal="",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
            tools=[],  # No tools
        )
        result = RoleConverter.to_agent_config(role)
        # Empty list should be included
        assert result["tools"] == []

    def test_to_agent_role(self, sample_role):
        """Test conversion to AgentRole."""
        result = RoleConverter.to_agent_role(sample_role)
        assert result.name == "test_role"
        assert result.description == "A test role"
        assert result.goal == "Test goal"


class TestPlannerValidator:
    """Tests for PlannerValidator."""

    def test_validate_goal_valid(self):
        """Test valid goal."""
        errors = PlannerValidator.validate_goal("Analyze code quality")
        assert len(errors) == 0

    def test_validate_goal_empty(self):
        """Test empty goal."""
        errors = PlannerValidator.validate_goal("")
        assert len(errors) > 0
        assert "empty" in errors[0].lower()

    def test_validate_goal_whitespace(self):
        """Test whitespace-only goal."""
        errors = PlannerValidator.validate_goal("   ")
        assert len(errors) > 0

    def test_validate_goal_too_long(self):
        """Test goal exceeding max length."""
        long_goal = "x" * 15000
        errors = PlannerValidator.validate_goal(long_goal)
        assert len(errors) > 0
        assert "long" in errors[0].lower()

    def test_validate_path_exists(self):
        """Test path validation when path exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            errors = PlannerValidator.validate_path(tmpdir, must_exist=True)
            assert len(errors) == 0

    def test_validate_path_not_exists(self):
        """Test path validation when path doesn't exist."""
        errors = PlannerValidator.validate_path("/nonexistent/path", must_exist=True)
        assert len(errors) > 0

    def test_validate_path_traversal(self):
        """Test path traversal rejection."""
        errors = PlannerValidator.validate_path("/home/user/../etc/passwd")
        assert len(errors) > 0
        assert any(".." in e for e in errors)

    def test_validate_role_name_valid(self):
        """Test valid role name."""
        errors = PlannerValidator.validate_role_name("researcher")
        assert len(errors) == 0

    def test_validate_role_name_empty(self):
        """Test empty role name."""
        errors = PlannerValidator.validate_role_name("")
        assert len(errors) > 0

    def test_validate_role_name_invalid_pattern(self):
        """Test invalid role name pattern."""
        errors = PlannerValidator.validate_role_name("Researcher")  # Capital letter
        assert len(errors) > 0

    def test_validate_role_name_with_hyphen(self):
        """Test role name with hyphen."""
        errors = PlannerValidator.validate_role_name("my-role")
        assert len(errors) > 0  # Hyphens not allowed

    def test_validate_role_name_path_separator(self):
        """Test role name with path separator."""
        errors = PlannerValidator.validate_role_name("path/to/role")
        assert len(errors) > 0

    def test_validate_tier_valid(self):
        """Test valid tier."""
        tier, errors = PlannerValidator.validate_tier("core")
        assert len(errors) == 0
        assert tier == RoleTier.CORE

    def test_validate_tier_invalid(self):
        """Test invalid tier."""
        tier, errors = PlannerValidator.validate_tier("invalid_tier")
        assert len(errors) > 0
        assert tier is None

    def test_validate_tier_default(self):
        """Test default tier when None."""
        tier, errors = PlannerValidator.validate_tier(None)
        assert len(errors) == 0
        assert tier == RoleTier.CORE

    def test_validate_capability_valid(self):
        """Test valid capability."""
        cap, errors = PlannerValidator.validate_capability("search")
        assert len(errors) == 0
        assert cap == Capability.SEARCH

    def test_validate_capability_invalid(self):
        """Test invalid capability."""
        cap, errors = PlannerValidator.validate_capability("invalid_cap")
        assert len(errors) > 0
        assert cap is None


class TestDependencyTracker:
    """Tests for DependencyTracker."""

    def test_add_task_no_dependency(self):
        """Test adding task without dependency."""
        tracker = DependencyTracker()
        tracker.add_task("task1")
        deps = tracker.get_dependencies("task1")
        assert deps == []

    def test_add_task_with_dependency(self):
        """Test adding task with dependency."""
        tracker = DependencyTracker()
        tracker.add_task("task1")
        tracker.add_task("task2", depends_on="task1")
        deps = tracker.get_dependencies("task2")
        assert deps == ["task1"]

    def test_add_task_unknown_dependency(self):
        """Test adding task with unknown dependency."""
        tracker = DependencyTracker()
        with pytest.raises(ValueError):
            tracker.add_task("task2", depends_on="unknown")

    def test_get_last_task(self):
        """Test getting last added task."""
        tracker = DependencyTracker()
        tracker.add_task("task1")
        tracker.add_task("task2")
        tracker.add_task("task3")
        assert tracker.get_last_task() == "task3"

    def test_validate_valid(self):
        """Test validation of valid dependencies."""
        tracker = DependencyTracker()
        tracker.add_task("task1")
        tracker.add_task("task2", depends_on="task1")
        tracker.add_task("task3", depends_on="task2")
        errors = tracker.validate()
        assert len(errors) == 0

    def test_validate_cycle(self):
        """Test detection of circular dependency."""
        tracker = DependencyTracker()
        # Manually create a cycle by adding tasks then modifying internal state
        tracker.add_task("task1")
        tracker.add_task("task2", depends_on="task1")
        # Simulate cycle: task1 -> task2 -> task1
        tracker._tasks["task1"] = "task2"
        errors = tracker.validate()
        assert len(errors) > 0
        assert any("Circular" in e for e in errors)


class TestIntegration:
    """Integration tests for utility classes."""

    def test_parser_converter_workflow(self):
        """Test combined workflow of parser and converter."""
        # Parse LLM response
        response = '''
        {
            "roles": [
                {"name": "researcher", "description": "Research role"}
            ]
        }
        '''
        data = LLMResponseParser.parse_object(response)
        assert data is not None

        # Create role from parsed data
        role = RoleDefinition(
            name=data["roles"][0]["name"],
            display_name=data["roles"][0]["name"],
            description=data["roles"][0]["description"],
            goal="",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.SEARCH],
        )

        # Convert to YAML dict
        yaml_dict = RoleConverter.to_yaml_dict(role)
        assert yaml_dict["name"] == "researcher"

    def test_validator_tracker_workflow(self):
        """Test combined workflow of validator and tracker."""
        # Validate goal
        goal_errors = PlannerValidator.validate_goal("Test goal")
        assert len(goal_errors) == 0

        # Validate role names
        role_errors = PlannerValidator.validate_role_name("researcher")
        assert len(role_errors) == 0

        # Build dependency chain
        tracker = DependencyTracker()
        tracker.add_task("research")
        tracker.add_task("analyze", depends_on="research")
        tracker.add_task("report", depends_on="analyze")

        # Validate
        dep_errors = tracker.validate()
        assert len(dep_errors) == 0
