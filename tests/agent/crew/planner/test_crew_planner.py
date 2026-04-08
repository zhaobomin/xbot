"""Tests for crew planner module."""

import pytest

from xbot.crew.planner.crew_planner import CrewPlanner
from xbot.crew.planner.models import (
    Capability,
    CrewPlan,
    GoalAnalysis,
    RolePoolConfig,
    RoleTier,
)


class TestCrewPlannerInit:
    """Tests for CrewPlanner initialization."""

    def test_default_init(self):
        """Test default initialization."""
        planner = CrewPlanner()
        assert planner.llm_callable is None
        assert planner.role_pool_config is not None

    def test_custom_init(self):
        """Test custom initialization."""
        def mock_llm(prompt):
            return '{"test": "response"}'

        config = RolePoolConfig(enabled_tiers=[RoleTier.CORE])
        planner = CrewPlanner(llm_callable=mock_llm, role_pool_config=config)

        assert planner.llm_callable is not None
        assert planner.role_pool_config.enabled_tiers == [RoleTier.CORE]


class TestCrewPlannerPlan:
    """Tests for the plan method."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_plan_returns_crew_plan(self, planner):
        """Test that plan returns a CrewPlan."""
        plan = planner.plan("Analyze code quality")

        assert isinstance(plan, CrewPlan)
        assert plan.name is not None
        assert plan.description == "Analyze code quality"

    def test_plan_includes_roles(self, planner):
        """Test that plan includes roles."""
        plan = planner.plan("Search for information")

        assert len(plan.roles) >= 1

    def test_plan_includes_tasks(self, planner):
        """Test that plan includes tasks."""
        plan = planner.plan("Write a simple function")

        assert len(plan.tasks) >= 1

    def test_plan_with_context(self, planner):
        """Test planning with additional context."""
        context = {
            "project_type": "python",
            "workspace": "/home/user/project",
        }

        plan = planner.plan("Fix the bug", context)

        assert plan.global_context is not None
        assert "python" in plan.global_context

    def test_plan_includes_analysis(self, planner):
        """Test that plan includes goal analysis."""
        plan = planner.plan("Test the codebase")

        assert plan.analysis is not None
        assert isinstance(plan.analysis, GoalAnalysis)

    def test_plan_includes_role_selection(self, planner):
        """Test that plan includes role selection."""
        plan = planner.plan("Review the code")

        assert plan.role_selection is not None

    def test_plan_has_confidence_score(self, planner):
        """Test that plan has confidence score."""
        plan = planner.plan("Simple task")

        assert 0.0 <= plan.confidence <= 1.0

    def test_plan_has_planning_time(self, planner):
        """Test that plan has planning time."""
        plan = planner.plan("Quick task")

        assert plan.planning_time >= 0.0


class TestGoalAnalysis:
    """Tests for goal analysis."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_analyze_infers_search_capability(self, planner):
        """Test that search-related goals infer SEARCH capability."""
        analysis = planner._analyze_goal("Search for documentation", None)

        assert Capability.SEARCH in analysis.required_capabilities

    def test_analyze_infers_code_capability(self, planner):
        """Test that code-related goals infer WRITE_CODE capability."""
        analysis = planner._analyze_goal("Write a function", None)

        assert Capability.WRITE_CODE in analysis.required_capabilities

    def test_analyze_infers_test_capability(self, planner):
        """Test that test-related goals infer TEST capability."""
        analysis = planner._analyze_goal("Test the module", None)

        assert Capability.TEST in analysis.required_capabilities

    def test_analyze_infers_complexity(self, planner):
        """Test that complexity is inferred correctly."""
        simple = planner._analyze_goal("Quick fix", None)
        complex_goal = planner._analyze_goal("Design and implement a distributed system", None)

        assert simple.complexity in ["simple", "medium"]
        assert complex_goal.complexity in ["medium", "complex"]

    def test_analyze_with_chinese_keywords(self, planner):
        """Test that Chinese keywords are recognized."""
        analysis = planner._analyze_goal("搜索相关信息", None)

        assert Capability.SEARCH in analysis.required_capabilities


class TestCapabilityInference:
    """Tests for capability inference from goal text."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_infer_multiple_capabilities(self, planner):
        """Test inferring multiple capabilities."""
        capabilities = planner._infer_capabilities("Search and analyze the code")

        assert Capability.SEARCH in capabilities
        assert Capability.ANALYZE in capabilities or Capability.READ_CODE in capabilities

    def test_infer_defaults_to_analyze(self, planner):
        """Test that unknown goals default to ANALYZE capability."""
        capabilities = planner._infer_capabilities("Something completely unknown")

        assert Capability.ANALYZE in capabilities

    def test_infer_debug_capability(self, planner):
        """Test inferring DEBUG capability."""
        capabilities = planner._infer_capabilities("Debug the error")

        assert Capability.DEBUG in capabilities

    def test_infer_document_capability(self, planner):
        """Test inferring DOCUMENT capability."""
        capabilities = planner._infer_capabilities("Write documentation")

        assert Capability.DOCUMENT in capabilities


class TestComplexityInference:
    """Tests for complexity inference."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_simple_indicators(self, planner):
        """Test simple complexity indicators."""
        assert planner._infer_complexity("Quick fix") == "simple"
        assert planner._infer_complexity("Simple task") == "simple"

    def test_complex_indicators(self, planner):
        """Test complex complexity indicators."""
        assert planner._infer_complexity("Design the system architecture") == "complex"
        assert planner._infer_complexity("Integrate multiple systems") == "complex"

    def test_medium_default(self, planner):
        """Test medium complexity as default."""
        assert planner._infer_complexity("Do the task") == "medium"


class TestGenerateConfig:
    """Tests for config generation."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_generate_config_returns_yaml(self, planner):
        """Test that generate_config returns YAML string."""
        plan = planner.plan("Test task")
        yaml_content = planner.generate_config(plan)

        assert isinstance(yaml_content, str)
        assert "name:" in yaml_content

    def test_plan_and_generate(self, planner):
        """Test plan_and_generate method."""
        plan, yaml_content = planner.plan_and_generate("Test goal")

        assert isinstance(plan, CrewPlan)
        assert isinstance(yaml_content, str)

    def test_save_config(self, planner):
        """Test saving config to file."""
        import tempfile
        from pathlib import Path

        plan = planner.plan("Test")

        with tempfile.TemporaryDirectory() as tmpdir:
            path = planner.save_config(plan, Path(tmpdir) / "test.yaml")

            assert Path(path).exists()
            content = Path(path).read_text()
            assert "name:" in content


class TestPreview:
    """Tests for plan preview."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_preview_returns_string(self, planner):
        """Test that preview returns a string."""
        plan = planner.plan("Test")
        preview = planner.preview(plan)

        assert isinstance(preview, str)
        assert len(preview) > 0

    def test_preview_includes_plan_name(self, planner):
        """Test that preview includes plan name."""
        plan = planner.plan("Test goal")
        preview = planner.preview(plan)

        assert plan.name in preview


class TestNameGeneration:
    """Tests for crew name generation."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_generate_name_from_goal(self, planner):
        """Test generating name from goal."""
        name = planner._generate_name("Build a new feature")

        assert "build" in name
        assert "feature" in name

    def test_generate_name_limits_length(self, planner):
        """Test that name is limited to 30 characters."""
        name = planner._generate_name("This is a very long goal description that should be truncated")

        assert len(name) <= 30

    def test_generate_name_handles_special_chars(self, planner):
        """Test that special characters are removed."""
        name = planner._generate_name("Test@#$%Goal")

        assert "@" not in name
        assert "#" not in name

    def test_generate_name_default(self, planner):
        """Test default name for empty goal."""
        name = planner._generate_name("")

        assert name == "dynamic_crew"


class TestConfidenceCalculation:
    """Tests for confidence score calculation."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_confidence_range(self, planner):
        """Test that confidence is in valid range."""
        plan = planner.plan("Test task")

        assert 0.0 <= plan.confidence <= 1.0

    def test_high_confidence_with_good_coverage(self, planner):
        """Test that good coverage leads to high confidence."""
        plan = planner.plan("Search for information")

        # Should have reasonable confidence since SEARCH is a common capability
        assert plan.confidence >= 0.3


class TestGlobalContext:
    """Tests for global context building."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_global_context_includes_goal(self, planner):
        """Test that global context includes goal."""
        context = planner._build_global_context("My goal", None)

        assert "My goal" in context

    def test_global_context_includes_context(self, planner):
        """Test that global context includes additional context."""
        context = planner._build_global_context("Goal", {"project": "test"})

        assert "project" in context
        assert "test" in context


class TestWithMockLLM:
    """Tests with mock LLM callable."""

    def test_plan_with_llm(self):
        """Test planning with LLM callable."""
        def mock_llm(prompt):
            if "analyze" in prompt.lower():
                return '''{
                    "summary": "Test analysis",
                    "required_capabilities": ["search", "analyze"],
                    "complexity": "medium",
                    "estimated_tasks": 2,
                    "suggested_process": "sequential"
                }'''
            elif "select" in prompt.lower():
                return '["researcher"]'
            else:
                return '[]'

        planner = CrewPlanner(llm_callable=mock_llm)
        plan = planner.plan("Test goal")

        assert plan is not None
        assert isinstance(plan, CrewPlan)

    def test_llm_failure_fallback(self):
        """Test that LLM failure falls back to heuristic."""
        def failing_llm(prompt):
            raise Exception("LLM error")

        planner = CrewPlanner(llm_callable=failing_llm)
        plan = planner.plan("Test goal")

        # Should still produce a plan using heuristics
        assert plan is not None
        assert isinstance(plan, CrewPlan)


class TestIntegration:
    """Integration tests for CrewPlanner."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_full_planning_workflow(self, planner):
        """Test complete planning workflow."""
        # Step 1: Plan
        plan = planner.plan("Analyze code quality and fix bugs")

        # Step 2: Verify plan structure
        assert plan.name is not None
        assert len(plan.roles) >= 1
        assert len(plan.tasks) >= 1

        # Step 3: Generate config
        yaml_content = planner.generate_config(plan)
        assert len(yaml_content) > 0

        # Step 4: Preview
        preview = planner.preview(plan)
        assert len(preview) > 0

    def test_different_goal_types(self, planner):
        """Test planning for different types of goals."""
        goals = [
            "Search for documentation",
            "Write a test",
            "Review the code",
            "Debug the error",
            "Deploy to production",
        ]

        for goal in goals:
            plan = planner.plan(goal)
            assert isinstance(plan, CrewPlan)
            assert len(plan.roles) >= 1
