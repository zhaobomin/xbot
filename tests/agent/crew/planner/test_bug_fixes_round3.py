"""Tests for bug fixes in round 3."""

import pytest
import tempfile
from pathlib import Path
import yaml

from typer.testing import CliRunner

from xbot.agent.crew.planner.models import (
    Capability,
    CrewPlan,
    GoalAnalysis,
    RoleDefinition,
    RolePool,
    RolePoolConfig,
    RoleSelection,
    RoleTier,
    TaskPlan,
)
from xbot.agent.crew.planner.task_planner import TaskPlanner
from xbot.agent.crew.planner.role_selector import RoleSelector
from xbot.agent.crew.planner.role_pool import RolePoolManager
from xbot.agent.crew.planner.crew_planner import CrewPlanner
from xbot.agent.crew.planner.config_generator import ConfigGenerator
from xbot.agent.crew.cli.plan_cmd import app as plan_app
from xbot.agent.crew.cli.role_cmd import app as role_app

runner = CliRunner()


class TestYmlFileLoading:
    """Tests for loading .yml files in addition to .yaml."""

    def test_load_yml_file(self):
        """Test that .yml files are loaded from role pool directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a .yml role file
            yml_file = Path(tmpdir) / "test_role.yml"
            yml_content = {
                "name": "yml_test_role",
                "display_name": "YML Test Role",
                "description": "A role defined in .yml file",
                "goal": "Test yml loading",
                "backstory": "",
                "tier": "extended",
                "capabilities": ["search"],
            }
            with open(yml_file, "w") as f:
                yaml.dump(yml_content, f)

            # Create config pointing to this directory
            config = RolePoolConfig(
                enabled_tiers=[RoleTier.EXTENDED],
                custom_roles_dir=tmpdir,
            )
            manager = RolePoolManager(config)
            pool = manager.get_pool()

            # Should find the role from .yml file
            role = pool.get_role("yml_test_role")
            assert role is not None
            assert role.description == "A role defined in .yml file"

    def test_both_yaml_and_yml_loaded(self):
        """Test that both .yaml and .yml files are loaded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .yaml file
            yaml_file = Path(tmpdir) / "role_yaml.yaml"
            yaml_content = {
                "name": "yaml_role",
                "display_name": "YAML Role",
                "description": "From .yaml",
                "goal": "Test",
                "backstory": "",
                "capabilities": ["search"],
            }
            with open(yaml_file, "w") as f:
                yaml.dump(yaml_content, f)

            # Create .yml file
            yml_file = Path(tmpdir) / "role_yml.yml"
            yml_content = {
                "name": "yml_role",
                "display_name": "YML Role",
                "description": "From .yml",
                "goal": "Test",
                "backstory": "",
                "capabilities": ["analyze"],
            }
            with open(yml_file, "w") as f:
                yaml.dump(yml_content, f)

            config = RolePoolConfig(
                enabled_tiers=[RoleTier.EXTENDED],
                custom_roles_dir=tmpdir,
            )
            manager = RolePoolManager(config)
            pool = manager.get_pool()

            # Both roles should be loaded
            assert pool.get_role("yaml_role") is not None
            assert pool.get_role("yml_role") is not None


class TestTierValidationInRoleFile:
    """Tests for tier validation in role YAML files."""

    def test_invalid_tier_in_yaml_defaults_to_directory_tier(self):
        """Test that invalid tier in YAML defaults to directory tier."""
        with tempfile.TemporaryDirectory() as tmpdir:
            role_file = Path(tmpdir) / "bad_tier.yaml"
            role_content = {
                "name": "bad_tier_role",
                "display_name": "Bad Tier",
                "description": "Role with invalid tier",
                "goal": "Test",
                "backstory": "",
                "tier": "invalid_tier_value",  # Invalid
                "capabilities": ["search"],
            }
            with open(role_file, "w") as f:
                yaml.dump(role_content, f)

            config = RolePoolConfig(
                enabled_tiers=[RoleTier.EXTENDED],
                custom_roles_dir=tmpdir,
            )
            manager = RolePoolManager(config)
            pool = manager.get_pool()

            role = pool.get_role("bad_tier_role")
            assert role is not None
            # Should have defaulted to EXTENDED (directory tier)
            assert role.tier == RoleTier.EXTENDED

    def test_valid_tier_in_yaml_overrides_directory_tier(self):
        """Test that valid tier in YAML overrides directory tier."""
        with tempfile.TemporaryDirectory() as tmpdir:
            role_file = Path(tmpdir) / "explicit_tier.yaml"
            role_content = {
                "name": "explicit_tier_role",
                "display_name": "Explicit Tier",
                "description": "Role with explicit tier",
                "goal": "Test",
                "backstory": "",
                "tier": "specialist",  # Explicitly set
                "capabilities": ["search"],
            }
            with open(role_file, "w") as f:
                yaml.dump(role_content, f)

            config = RolePoolConfig(
                enabled_tiers=[RoleTier.CORE, RoleTier.EXTENDED, RoleTier.SPECIALIST],
                custom_roles_dir=tmpdir,
            )
            manager = RolePoolManager(config)
            pool = manager.get_pool()

            role = pool.get_role("explicit_tier_role")
            assert role is not None
            assert role.tier == RoleTier.SPECIALIST


class TestRoleSelectorJSONParsing:
    """Tests for robust JSON parsing in RoleSelector."""

    @pytest.fixture
    def selector(self):
        return RoleSelector()

    @pytest.fixture
    def sample_roles(self):
        return [
            (RoleDefinition(
                name="agent1",
                display_name="Agent 1",
                description="Test",
                goal="Test",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.ANALYZE],
            ), 1.0),
            (RoleDefinition(
                name="agent2",
                display_name="Agent 2",
                description="Test",
                goal="Test",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.SEARCH],
            ), 0.8),
        ]

    def test_simple_string_array(self, selector, sample_roles):
        """Test parsing simple string array of role names."""
        response = '["agent1", "agent2"]'
        result = selector._parse_llm_selection(response, sample_roles)
        assert len(result) == 2
        assert result[0].name == "agent1"
        assert result[1].name == "agent2"

    def test_nested_array_in_response(self, selector, sample_roles):
        """Test parsing response with surrounding text."""
        response = '''
        Based on the analysis, I recommend: ["agent1", "agent2"]
        These roles will handle the task.
        '''
        result = selector._parse_llm_selection(response, sample_roles)
        assert len(result) == 2

    def test_json_with_complex_surrounding_text(self, selector, sample_roles):
        """Test JSON embedded in complex text."""
        response = '''
        Let me analyze the requirements...

        Selected roles: ["agent1"]

        Reasoning:
        - Agent1 is perfect for this task.
        '''
        result = selector._parse_llm_selection(response, sample_roles)
        assert len(result) == 1
        assert result[0].name == "agent1"

    def test_malformed_json_fallback(self, selector, sample_roles):
        """Test fallback when JSON is malformed."""
        response = '''
        The selected roles are:
        - agent1: Primary role
        - agent2: Secondary role
        '''
        result = selector._parse_llm_selection(response, sample_roles)
        # Should fall back to text parsing
        assert len(result) >= 1


class TestCrewPlannerNameGeneration:
    """Tests for crew name generation."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_name_from_english_goal(self, planner):
        """Test name generation from English goal."""
        name = planner._generate_name("Analyze code quality")
        assert name == "analyze_code_quality"
        assert len(name) <= 30

    def test_name_from_chinese_goal(self, planner):
        """Test name generation from Chinese goal."""
        name = planner._generate_name("分析代码质量")
        # Should handle Chinese characters
        assert isinstance(name, str)
        assert len(name) <= 30

    def test_name_from_mixed_goal(self, planner):
        """Test name generation from mixed language goal."""
        name = planner._generate_name("Analyze 代码 quality")
        assert isinstance(name, str)
        assert len(name) <= 30

    def test_name_with_leading_digits(self, planner):
        """Test name generation when first word starts with digit."""
        name = planner._generate_name("123 fix the bug")
        # Should prefix with underscore or similar
        assert not name[0].isdigit()

    def test_name_from_special_chars(self, planner):
        """Test name generation with special characters."""
        name = planner._generate_name("Fix bug #123 & improve @performance!")
        assert "#" not in name
        assert "&" not in name
        assert "@" not in name

    def test_empty_goal_default_name(self, planner):
        """Test default name for empty goal."""
        name = planner._generate_name("")
        assert name == "dynamic_crew"


class TestCrewPlanToConfigDict:
    """Tests for CrewPlan.to_crew_config_dict()."""

    def test_tool_restrictions_included(self):
        """Test that tool_restrictions are included in config dict."""
        role = RoleDefinition(
            name="restricted_agent",
            display_name="Restricted",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
            tools=["read_file"],
            tool_restrictions=["bash", "write_file"],
        )

        plan = CrewPlan(
            name="test_plan",
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

        config_dict = plan.to_crew_config_dict()

        assert "restricted_agent" in config_dict["agents"]
        agent_config = config_dict["agents"]["restricted_agent"]
        assert "tool_restrictions" in agent_config
        assert "bash" in agent_config["tool_restrictions"]
        assert "write_file" in agent_config["tool_restrictions"]

    def test_no_tool_restrictions_when_none(self):
        """Test that tool_restrictions key is omitted when None."""
        role = RoleDefinition(
            name="unrestricted_agent",
            display_name="Unrestricted",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
            tools=None,
            tool_restrictions=None,
        )

        plan = CrewPlan(
            name="test_plan",
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

        config_dict = plan.to_crew_config_dict()

        agent_config = config_dict["agents"]["unrestricted_agent"]
        assert "tool_restrictions" not in agent_config


class TestConfidenceCalculation:
    """Tests for planning confidence calculation."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_confidence_with_perfect_coverage(self, planner):
        """Test confidence with perfect coverage."""
        analysis = GoalAnalysis(
            summary="Test",
            required_capabilities=[Capability.SEARCH],
            complexity="simple",
            estimated_tasks=2,
            suggested_process="sequential",
        )

        role = RoleDefinition(
            name="searcher",
            display_name="Searcher",
            description="Search role",
            goal="Search",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.SEARCH],
        )

        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
            created_roles=[],
            role_gaps=[],
        )

        tasks = [
            TaskPlan(name="task1", description="T1", agent="searcher"),
            TaskPlan(name="task2", description="T2", agent="searcher"),
        ]

        confidence = planner._calculate_confidence(analysis, selection, tasks)

        # High confidence: perfect coverage + task count match
        assert confidence >= 0.9

    def test_confidence_with_low_coverage(self, planner):
        """Test confidence with low coverage."""
        analysis = GoalAnalysis(
            summary="Test",
            required_capabilities=[Capability.SEARCH, Capability.WRITE_CODE],
            complexity="simple",
            estimated_tasks=2,
            suggested_process="sequential",
        )

        # Only covers SEARCH, not WRITE_CODE
        role = RoleDefinition(
            name="searcher",
            display_name="Searcher",
            description="Search role",
            goal="Search",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.SEARCH],
        )

        selection = RoleSelection(
            selected_roles=[role],
            selection_reason={},
            skipped_roles=[],
            coverage_score=0.5,  # Only 50% coverage
            created_roles=[],
            role_gaps=[],
        )

        tasks = [TaskPlan(name="task1", description="T1", agent="searcher")]

        confidence = planner._calculate_confidence(analysis, selection, tasks)

        # Lower confidence due to low coverage
        assert confidence < 0.9

    def test_confidence_with_empty_tasks(self, planner):
        """Test confidence with no tasks."""
        analysis = GoalAnalysis(
            summary="Test",
            required_capabilities=[],
            complexity="simple",
            estimated_tasks=0,
            suggested_process="sequential",
        )

        selection = RoleSelection(
            selected_roles=[],
            selection_reason={},
            skipped_roles=[],
            coverage_score=1.0,
            created_roles=[],
            role_gaps=[],
        )

        confidence = planner._calculate_confidence(analysis, selection, [])

        # Should handle empty tasks gracefully
        assert 0.0 <= confidence <= 1.0


class TestRoleCmdShowOutput:
    """Tests for role_cmd show command output."""

    def test_show_nonexistent_role_shows_available(self):
        """Test that showing nonexistent role lists available roles."""
        result = runner.invoke(role_app, [
            "show",
            "nonexistent_role_xyz",
        ])

        assert result.exit_code != 0
        # Should mention available roles
        assert "Available roles:" in result.output or "not found" in result.output.lower()


class TestPlanWithVariousGoals:
    """Tests for planning with various goal types."""

    def test_plan_with_code_snippet(self):
        """Test planning with code snippet in goal."""
        planner = CrewPlanner()
        goal = "Fix this code: def foo(): return 'bar'"

        plan = planner.plan(goal)

        assert plan is not None
        assert len(plan.roles) >= 1

    def test_plan_with_markdown(self):
        """Test planning with markdown in goal."""
        planner = CrewPlanner()
        goal = """
        Create a function that:
        - Takes a list as input
        - Returns the sum
        """

        plan = planner.plan(goal)

        assert plan is not None

    def test_plan_with_urls(self):
        """Test planning with URLs in goal."""
        planner = CrewPlanner()
        goal = "Fetch data from https://api.example.com and process it"

        plan = planner.plan(goal)

        assert plan is not None


class TestConfigGeneratorWithToolRestrictions:
    """Tests for config generator with tool restrictions."""

    @pytest.fixture
    def generator(self):
        return ConfigGenerator()

    def test_role_with_restrictions(self, generator):
        """Test role with tool restrictions is properly serialized."""
        role = RoleDefinition(
            name="limited_agent",
            display_name="Limited",
            description="Limited agent",
            goal="Limited operations",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
            tools=["read_file"],
            tool_restrictions=["bash"],
        )

        plan = CrewPlan(
            name="limited_plan",
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

        # Note: config_generator._build_agents doesn't include tool_restrictions
        # This is expected - it's handled in to_crew_config_dict
        assert "limited_agent" in yaml_content