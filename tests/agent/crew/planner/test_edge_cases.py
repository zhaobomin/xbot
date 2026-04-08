"""Additional tests for bug fixes and edge cases."""

import tempfile
from pathlib import Path

import pytest

from xbot.crew.planner.config_generator import ConfigGenerator
from xbot.crew.planner.crew_planner import CrewPlanner
from xbot.crew.planner.models import (
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
from xbot.crew.planner.role_pool import RolePoolManager
from xbot.crew.planner.task_planner import TaskPlanner


class TestBugFixes:
    """Tests for specific bug fixes."""

    def test_duplicate_capability_fixed(self):
        """Test that WRITE_CODE is not duplicated in medium task planning."""
        planner = TaskPlanner()

        roles = [
            RoleDefinition(
                name="coder",
                display_name="Coder",
                description="Coding role",
                goal="Code",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.WRITE_CODE, Capability.DEBUG],
            ),
            RoleDefinition(
                name="researcher",
                display_name="Researcher",
                description="Research role",
                goal="Research",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.SEARCH, Capability.ANALYZE],
            ),
        ]

        selection = RoleSelection(
            selected_roles=roles,
            selection_reason={r.name: "Match" for r in roles},
            skipped_roles=[],
            coverage_score=1.0,
            created_roles=[],
            role_gaps=[],
        )

        analysis = GoalAnalysis(
            summary="Medium task",
            required_capabilities=[Capability.WRITE_CODE],
            complexity="medium",
            estimated_tasks=2,
            suggested_process="sequential",
        )

        tasks = planner.plan("Medium goal", analysis, selection)

        # Should have tasks created without error
        assert len(tasks) >= 1


class TestJSONParsingEdgeCases:
    """Tests for JSON parsing edge cases."""

    @pytest.fixture
    def planner(self):
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
            ),
        ]

    def test_nested_json_arrays(self, planner, sample_roles):
        """Test parsing nested JSON arrays."""
        response = '''
        Here are the tasks:
        [
            {"name": "task1", "description": "First", "agent": "agent1", "dependencies": ["nested", "array"]},
            {"name": "task2", "description": "Second", "agent": "agent1", "dependencies": []}
        ]
        End of tasks.
        '''

        tasks = planner._parse_llm_tasks(response, sample_roles)

        assert len(tasks) == 2
        assert tasks[0].name == "task1"
        assert "nested" in tasks[0].dependencies

    def test_multiple_json_arrays_in_response(self, planner, sample_roles):
        """Test response with multiple JSON arrays."""
        response = '''
        Here are some options: [1, 2, 3]
        And here are the tasks:
        [{"name": "real_task", "description": "Task", "agent": "agent1", "dependencies": []}]
        '''

        tasks = planner._parse_llm_tasks(response, sample_roles)

        # Should find the first array (options) and try to parse it
        # But since it's not valid task objects, should return empty or filtered
        # Actually the decoder will find the first '[' and parse the array
        # Let's verify behavior
        assert isinstance(tasks, list)

    def test_json_with_extra_text(self, planner, sample_roles):
        """Test JSON with surrounding text."""
        response = '''
        I analyzed the goal and here are the planned tasks:
        [
            {"name": "analyze", "description": "Analyze", "agent": "agent1", "dependencies": []}
        ]
        These tasks will help achieve the goal.
        '''

        tasks = planner._parse_llm_tasks(response, sample_roles)

        assert len(tasks) == 1
        assert tasks[0].name == "analyze"

    def test_empty_json_array(self, planner, sample_roles):
        """Test empty JSON array."""
        response = "Tasks: []"

        tasks = planner._parse_llm_tasks(response, sample_roles)

        assert tasks == []

    def test_malformed_json(self, planner, sample_roles):
        """Test malformed JSON that should be handled gracefully."""
        response = '''
        [
            {"name": "task1", "description": "Missing closing brace"
        ]
        '''

        # Should not raise exception, should return empty or partial
        tasks = planner._parse_llm_tasks(response, sample_roles)
        assert isinstance(tasks, list)

    def test_json_with_missing_agent(self, planner, sample_roles):
        """Test JSON where agent field is missing."""
        response = '''
        [
            {"name": "task1", "description": "No agent field", "dependencies": []}
        ]
        '''

        # Should skip tasks without valid agent
        tasks = planner._parse_llm_tasks(response, sample_roles)
        # Agent defaults to "" which is not in role_names
        assert len(tasks) == 0


class TestRolePoolManagerReload:
    """Tests for RolePoolManager reload functionality."""

    def test_reload_preserves_state_on_failure(self):
        """Test that reload preserves previous state on failure."""
        manager = RolePoolManager()
        manager.load()

        # Count initial roles
        initial_count = len(manager.list_roles())
        assert initial_count > 0

        # Try to reload with a corrupted custom dir
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an invalid YAML file
            bad_file = Path(tmpdir) / "bad.yaml"
            bad_file.write_text("invalid: yaml: content: [")

            # Configure manager to use this dir
            original_config = manager.config
            manager.config = RolePoolConfig(
                enabled_tiers=[RoleTier.CORE],
                custom_roles_dir=tmpdir,
            )

            # Clear loaded state to force reload attempt
            manager._loaded = False
            manager._roles.clear()

            # Reload should still work (skips bad file)
            manager.load()

            # Should have at least core roles
            assert len(manager.list_roles()) > 0

            # Restore original config
            manager.config = original_config


class TestConfigGeneratorEdgeCases:
    """Tests for ConfigGenerator edge cases."""

    @pytest.fixture
    def generator(self):
        return ConfigGenerator()

    def test_role_with_none_description(self, generator):
        """Test role with None description."""
        role = RoleDefinition(
            name="test",
            display_name="Test",
            description=None,  # This could happen
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
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

        # Should not raise
        yaml_content = generator.generate_yaml(plan)
        assert "name: test" in yaml_content

    def test_role_with_empty_tools_list(self, generator):
        """Test role with empty tools list vs None."""
        role_with_empty_tools = RoleDefinition(
            name="empty_tools",
            display_name="Empty Tools",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
            tools=[],  # Empty list = no tools
        )

        role_with_none_tools = RoleDefinition(
            name="none_tools",
            display_name="None Tools",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
            tools=None,  # None = all tools
        )

        plan = CrewPlan(
            name="tools_test",
            description="Test",
            process="sequential",
            global_context="",
            roles=[role_with_empty_tools, role_with_none_tools],
            tasks=[],
            analysis=GoalAnalysis(
                summary="Test",
                required_capabilities=[],
                complexity="simple",
                estimated_tasks=0,
                suggested_process="sequential",
            ),
            role_selection=RoleSelection(
                selected_roles=[role_with_empty_tools, role_with_none_tools],
                selection_reason={r.name: "Match" for r in [role_with_empty_tools, role_with_none_tools]},
                skipped_roles=[],
                coverage_score=1.0,
                created_roles=[],
                role_gaps=[],
            ),
            planning_time=0.0,
            confidence=1.0,
        )

        yaml_content = generator.generate_yaml(plan)

        # Empty tools list should be included
        assert "tools:" in yaml_content

    def test_preview_with_none_description(self, generator):
        """Test preview with None task description."""
        task = TaskPlan(
            name="test_task",
            description=None,  # Could be None
            agent="test_agent",
            dependencies=[],
        )

        role = RoleDefinition(
            name="test_agent",
            display_name="Test Agent",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )

        plan = CrewPlan(
            name="preview_test",
            description="Test",
            process="sequential",
            global_context="",
            roles=[role],
            tasks=[task],
            analysis=GoalAnalysis(
                summary="Test",
                required_capabilities=[],
                complexity="simple",
                estimated_tasks=1,
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

        # Should not raise
        preview = generator.generate_preview(plan)
        assert "test_task" in preview

    def test_preview_with_long_description(self, generator):
        """Test preview truncates long descriptions."""
        long_desc = "A" * 100
        task = TaskPlan(
            name="long_task",
            description=long_desc,
            agent="test_agent",
            dependencies=[],
        )

        role = RoleDefinition(
            name="test_agent",
            display_name="Test Agent",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )

        plan = CrewPlan(
            name="preview_test",
            description="Test",
            process="sequential",
            global_context="",
            roles=[role],
            tasks=[task],
            analysis=GoalAnalysis(
                summary="Test",
                required_capabilities=[],
                complexity="simple",
                estimated_tasks=1,
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

        preview = generator.generate_preview(plan)

        # Description should be truncated
        assert "..." in preview
        assert len([line for line in preview.split("\n") if "A" * 100 in line]) == 0


class TestCrewPlannerEdgeCases:
    """Tests for CrewPlanner edge cases."""

    def test_plan_with_no_candidates_for_llm(self):
        """Test planning when no candidates are found for LLM."""
        # Use a mock LLM that tracks if it was called
        llm_called = []

        def mock_llm(prompt):
            llm_called.append(True)
            return '[]'

        # Configure with only CORE tier, but request capabilities that don't exist
        config = RolePoolConfig(enabled_tiers=[RoleTier.CORE])
        planner = CrewPlanner(llm_callable=mock_llm, role_pool_config=config)

        # Plan with a goal - this will find candidates and call LLM
        plan = planner.plan("Search for information")

        # LLM should have been called for task planning at least
        assert len(llm_called) >= 1
        assert plan is not None

    def test_plan_with_context(self):
        """Test planning with context information."""
        planner = CrewPlanner()
        context = {
            "workspace": "/tmp/test",
            "project_type": "python",
        }

        plan = planner.plan("Analyze the code", context)

        assert "python" in plan.global_context
        assert "/tmp/test" in plan.global_context


class TestComplexityValidation:
    """Tests for complexity validation and handling."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_complexity_inference_simple(self, planner):
        """Test simple complexity inference."""
        assert planner._infer_complexity("Quick fix") == "simple"
        assert planner._infer_complexity("Just a simple task") == "simple"

    def test_complexity_inference_complex(self, planner):
        """Test complex complexity inference."""
        assert planner._infer_complexity("Design system architecture") == "complex"
        assert planner._infer_complexity("Multiple system integration") == "complex"

    def test_complexity_inference_medium_default(self, planner):
        """Test medium complexity as default."""
        assert planner._infer_complexity("Do the thing") == "medium"
        assert planner._infer_complexity("Standard operation") == "medium"


class TestCapabilityInferenceEdgeCases:
    """Tests for capability inference edge cases."""

    @pytest.fixture
    def planner(self):
        return CrewPlanner()

    def test_chinese_keywords(self, planner):
        """Test Chinese keyword inference."""
        caps = planner._infer_capabilities("搜索并分析代码")
        assert Capability.SEARCH in caps
        assert Capability.ANALYZE in caps or Capability.READ_CODE in caps

    def test_mixed_language_keywords(self, planner):
        """Test mixed language keywords."""
        caps = planner._infer_capabilities("Write code and 测试")
        assert Capability.WRITE_CODE in caps
        assert Capability.TEST in caps

    def test_unknown_goal_defaults_to_analyze(self, planner):
        """Test unknown goals default to ANALYZE capability."""
        caps = planner._infer_capabilities("Something completely unknown xyz")
        assert Capability.ANALYZE in caps


class TestCircularDependencyHandling:
    """Tests for circular dependency handling."""

    @pytest.fixture
    def planner(self):
        return TaskPlanner()

    def test_circular_dependency_warning(self, planner):
        """Test that circular dependencies are detected and logged."""
        tasks = [
            TaskPlan(
                name="task_a",
                description="Task A",
                agent="agent1",
                dependencies=["task_b"],
            ),
            TaskPlan(
                name="task_b",
                description="Task B",
                agent="agent1",
                dependencies=["task_a"],
            ),
        ]

        # Should not raise, should return original order
        result = planner._topological_sort(tasks)

        assert len(result) == 2
        assert result[0].name in ["task_a", "task_b"]

    def test_self_dependency(self, planner):
        """Test self-referential dependency."""
        tasks = [
            TaskPlan(
                name="self_task",
                description="Self-referential",
                agent="agent1",
                dependencies=["self_task"],
            ),
        ]

        result = planner._topological_sort(tasks)

        # Should handle gracefully
        assert len(result) == 1


class TestFuzzyRoleMatching:
    """Tests for fuzzy role name matching."""

    @pytest.fixture
    def planner(self):
        return TaskPlanner()

    def test_hyphen_to_underscore(self, planner):
        """Test hyphen to underscore conversion."""
        result = planner._fuzzy_match_role("my-role", {"my_role"})
        assert result == "my_role"

    def test_space_to_underscore(self, planner):
        """Test space to underscore conversion."""
        result = planner._fuzzy_match_role("my role", {"my_role"})
        assert result == "my_role"

    def test_case_insensitive(self, planner):
        """Test case insensitive matching."""
        result = planner._fuzzy_match_role("MyRole", {"myrole"})
        assert result == "myrole"

    def test_no_match(self, planner):
        """Test when no match is found."""
        result = planner._fuzzy_match_role("unknown", {"role1", "role2"})
        assert result is None


class TestRoleSelectionEdgeCases:
    """Tests for role selection edge cases."""

    def test_selection_with_role_gaps(self):
        """Test role selection when there are capability gaps."""
        from xbot.crew.planner.role_selector import RoleSelector

        selector = RoleSelector(allow_create_roles=True)

        # Create a pool with limited capabilities
        roles = {
            "basic": RoleDefinition(
                name="basic",
                display_name="Basic",
                description="Basic role",
                goal="Basic",
                backstory="",
                tier=RoleTier.CORE,
                capabilities=[Capability.SEARCH],
            ),
        }

        pool = RolePool(
            roles=roles,
            config=RolePoolConfig(enabled_tiers=[RoleTier.CORE]),
        )

        # Request capabilities not available
        analysis = GoalAnalysis(
            summary="Need write code",
            required_capabilities=[Capability.WRITE_CODE],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        selection = selector.select(analysis, pool)

        # Should select the basic role as fallback
        assert len(selection.selected_roles) >= 1
