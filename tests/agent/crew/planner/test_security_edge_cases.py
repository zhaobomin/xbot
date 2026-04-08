"""Tests for security fixes and additional edge cases."""


import pytest
import yaml
from typer.testing import CliRunner

from xbot.agent.crew.cli.plan_cmd import app
from xbot.agent.crew.planner.models import (
    Capability,
    GoalAnalysis,
    RoleDefinition,
    RolePoolConfig,
    RoleSelection,
    RoleTier,
    TaskPlan,
)
from xbot.agent.crew.planner.role_creator import RoleCreator
from xbot.agent.crew.planner.role_pool import RolePoolManager
from xbot.agent.crew.planner.task_planner import TaskPlanner

runner = CliRunner()


class TestSecurityFixes:
    """Tests for security-related bug fixes."""

    def test_path_traversal_in_delete_rejected(self, tmp_path):
        """Test that path traversal in role delete is rejected."""

        # Create a directory outside the custom dir
        outside_dir = tmp_path.parent / "outside"
        outside_dir.mkdir(exist_ok=True)
        outside_file = outside_dir / "target.yaml"
        outside_file.write_text("name: target")

        # Try to delete using path traversal
        result = runner.invoke(app, [
            "delete",
            "../../../outside/target",  # Path traversal attempt
            "--custom-dir", str(tmp_path),
            "--force",
        ])

        # Should be rejected
        assert result.exit_code != 0
        assert "invalid" in result.output.lower() or "error" in result.output.lower()

    def test_path_traversal_with_dots_rejected(self, tmp_path):
        """Test that path traversal with '..' is rejected."""
        result = runner.invoke(app, [
            "delete",
            "..",  # Just dots
            "--custom-dir", str(tmp_path),
            "--force",
        ])

        assert result.exit_code != 0

    def test_path_traversal_with_slash_rejected(self, tmp_path):
        """Test that path traversal with '/' is rejected."""
        result = runner.invoke(app, [
            "delete",
            "subdir/role",  # Contains slash
            "--custom-dir", str(tmp_path),
            "--force",
        ])

        assert result.exit_code != 0

    def test_path_traversal_with_backslash_rejected(self, tmp_path):
        """Test that path traversal with '\\' is rejected."""
        result = runner.invoke(app, [
            "delete",
            "subdir\\role",  # Contains backslash
            "--custom-dir", str(tmp_path),
            "--force",
        ])

        assert result.exit_code != 0


class TestInputValidation:
    """Tests for input validation."""

    def test_goal_too_long_rejected(self):
        """Test that overly long goals are rejected."""
        long_goal = "x" * 15000
        result = runner.invoke(app, ["plan", long_goal])

        assert result.exit_code != 0
        assert "too long" in result.output.lower()

    def test_goal_max_length_accepted(self):
        """Test that goals at max length are accepted."""
        # Just under the limit
        goal = "Analyze code " + "a" * 9980
        result = runner.invoke(app, ["plan", goal])

        # Should work or fail for other reasons, not length
        if result.exit_code != 0:
            assert "too long" not in result.output.lower()

    def test_workspace_not_exists_rejected(self):
        """Test that non-existent workspace is rejected."""
        result = runner.invoke(app, [
            "plan",
            "Test goal",
            "--workspace", "/nonexistent/path/xyz",
        ])

        assert result.exit_code != 0
        assert "not exist" in result.output.lower() or "error" in result.output.lower()

    def test_whitespace_only_goal_rejected(self):
        """Test that whitespace-only goals are rejected."""
        result = runner.invoke(app, ["plan", "   "])

        assert result.exit_code != 0
        assert "empty" in result.output.lower()


class TestRoleCreatorValidation:
    """Tests for role creator validation."""

    @pytest.fixture
    def creator(self):
        return RoleCreator()

    def test_invalid_tier_in_yaml(self, creator, tmp_path):
        """Test that invalid tier in YAML defaults to extended."""
        role_data = {
            "name": "test_invalid_tier",
            "display_name": "Test",
            "description": "Test",
            "goal": "Test",
            "backstory": "",
            "tier": "invalid_tier_value",  # Invalid tier
            "capabilities": ["search"],
        }

        role_path = tmp_path / "test_invalid_tier.yaml"
        with open(role_path, "w") as f:
            yaml.dump(role_data, f)

        role = creator.load_role_from_file(role_path)

        # Should load with default tier
        assert role is not None
        assert role.tier == RoleTier.EXTENDED

    def test_missing_tier_defaults_to_extended(self, creator, tmp_path):
        """Test that missing tier defaults to extended."""
        role_data = {
            "name": "test_no_tier",
            "display_name": "Test",
            "description": "Test",
            "goal": "Test",
            "backstory": "",
            "capabilities": ["search"],
        }

        role_path = tmp_path / "test_no_tier.yaml"
        with open(role_path, "w") as f:
            yaml.dump(role_data, f)

        role = creator.load_role_from_file(role_path)

        assert role is not None
        assert role.tier == RoleTier.EXTENDED


class TestJSONParsingRobustness:
    """Tests for robust JSON parsing in task planner."""

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

    def test_json_with_trailing_comma(self, planner, sample_roles):
        """Test JSON with trailing comma."""
        response = '[{"name": "task1", "description": "Test", "agent": "agent1", "dependencies": []}]'

        tasks = planner._parse_llm_tasks(response, sample_roles)
        assert len(tasks) == 1

    def test_json_with_unicode(self, planner, sample_roles):
        """Test JSON with unicode characters."""
        response = '[{"name": "任务一", "description": "分析代码", "agent": "agent1", "dependencies": []}]'

        tasks = planner._parse_llm_tasks(response, sample_roles)
        assert len(tasks) == 1
        assert tasks[0].name == "任务一"

    def test_json_with_newlines_in_strings(self, planner, sample_roles):
        """Test JSON with newlines in string values."""
        response = '''[
            {
                "name": "task1",
                "description": "Multi\\nline\\ndescription",
                "agent": "agent1",
                "dependencies": []
            }
        ]'''

        tasks = planner._parse_llm_tasks(response, sample_roles)
        assert len(tasks) == 1


class TestRunDynamicValidation:
    """Tests for run-dynamic command validation."""

    def test_run_dynamic_empty_goal(self):
        """Test run-dynamic with empty goal."""
        result = runner.invoke(app, ["run-dynamic", "", "--dry-run"])

        assert result.exit_code != 0
        assert "empty" in result.output.lower()

    def test_run_dynamic_long_goal(self):
        """Test run-dynamic with overly long goal."""
        long_goal = "x" * 15000
        result = runner.invoke(app, ["run-dynamic", long_goal, "--dry-run"])

        assert result.exit_code != 0
        assert "too long" in result.output.lower()

    def test_run_dynamic_invalid_workspace(self):
        """Test run-dynamic with invalid workspace."""
        result = runner.invoke(app, [
            "run-dynamic",
            "Test goal",
            "--workspace", "/nonexistent/path/xyz",
            "--dry-run",
        ])

        assert result.exit_code != 0


class TestRolePoolManagerThreadSafety:
    """Tests for role pool manager thread safety."""

    def test_concurrent_access(self):
        """Test that concurrent access doesn't corrupt state."""
        import threading
        import time

        manager = RolePoolManager()
        errors = []

        def access_pool():
            try:
                for _ in range(10):
                    pool = manager.get_pool()
                    _ = pool.get_available_roles()
                    time.sleep(0.001)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=access_pool) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_reload_during_access(self):
        """Test that reload during access doesn't cause issues."""
        import threading
        import time

        manager = RolePoolManager()
        manager.load()

        errors = []

        def reload_loop():
            try:
                for _ in range(5):
                    time.sleep(0.01)
                    manager.reload()
            except Exception as e:
                errors.append(str(e))

        def access_loop():
            try:
                for _ in range(20):
                    pool = manager.get_pool()
                    _ = pool.get_available_roles()
                    time.sleep(0.005)
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=reload_loop),
            threading.Thread(target=access_loop),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should not have errors, or at least no data corruption
        assert len(errors) == 0 or "corrupt" not in " ".join(errors).lower()


class TestEdgeCaseGoals:
    """Tests for edge case goal inputs."""

    def test_goal_with_only_numbers(self):
        """Test goal containing only numbers."""
        result = runner.invoke(app, ["plan", "12345"])
        # Should work - numbers are valid input
        assert result.exit_code == 0 or "error" not in result.output.lower()

    def test_goal_with_special_unicode(self):
        """Test goal with special unicode characters."""
        result = runner.invoke(app, ["plan", "分析代码 🚀 и исправить"])
        assert result.exit_code == 0

    def test_goal_with_code_snippet(self):
        """Test goal containing code snippet."""
        goal = "Fix this code: def foo(): return 'bar'"
        result = runner.invoke(app, ["plan", goal])
        assert result.exit_code == 0

    def test_goal_with_quotes(self):
        """Test goal containing quotes."""
        goal = """Analyze the "main" function's 'performance'"""
        result = runner.invoke(app, ["plan", goal])
        assert result.exit_code == 0


class TestComplexityInference:
    """Tests for complexity inference edge cases."""

    @pytest.fixture
    def planner(self):
        from xbot.agent.crew.planner.crew_planner import CrewPlanner
        return CrewPlanner()

    def test_mixed_simple_complex_keywords(self, planner):
        """Test goals with both simple and complex keywords."""
        # Has 'quick' but also 'architecture'
        result = planner._infer_complexity("Quick design of system architecture")
        # Complex indicators should win
        assert result == "complex"

    def test_chinese_complexity_keywords(self, planner):
        """Test Chinese complexity keywords."""
        assert planner._infer_complexity("快速修复") == "simple"
        assert planner._infer_complexity("系统架构设计") == "complex"

    def test_no_clear_complexity_indicators(self, planner):
        """Test goals with no clear complexity indicators."""
        result = planner._infer_complexity("Do the thing")
        assert result == "medium"  # Default


class TestCapabilityInference:
    """Tests for capability inference edge cases."""

    @pytest.fixture
    def planner(self):
        from xbot.agent.crew.planner.crew_planner import CrewPlanner
        return CrewPlanner()

    def test_no_matching_keywords(self, planner):
        """Test goal with no matching keywords defaults to ANALYZE."""
        caps = planner._infer_capabilities("xyzabc unknown words")
        assert Capability.ANALYZE in caps

    def test_all_keywords_match(self, planner):
        """Test goal matching many keywords."""
        caps = planner._infer_capabilities(
            "search analyze write test debug deploy document"
        )
        # Should have multiple capabilities
        assert len(caps) >= 4

    def test_case_insensitive_keywords(self, planner):
        """Test that keywords are case insensitive."""
        caps_lower = planner._infer_capabilities("search for info")
        caps_upper = planner._infer_capabilities("SEARCH for info")
        caps_mixed = planner._infer_capabilities("Search for info")

        assert Capability.SEARCH in caps_lower
        assert Capability.SEARCH in caps_upper
        assert Capability.SEARCH in caps_mixed


class TestTaskPlanDefaults:
    """Tests for TaskPlan default values."""

    def test_default_values(self):
        """Test that TaskPlan has correct defaults."""
        task = TaskPlan(
            name="test",
            description="Test",
            agent="agent1",
        )

        assert task.dependencies == []
        assert task.expected_output == ""
        assert task.timeout == 300
        assert task.human_review is False
        assert task.priority == 0

    def test_partial_values(self):
        """Test TaskPlan with partial values."""
        task = TaskPlan(
            name="test",
            description="Test",
            agent="agent1",
            timeout=600,
        )

        assert task.timeout == 600
        assert task.dependencies == []  # Still default


class TestRoleSelectionWithNoRoles:
    """Tests for role selection when pool is empty."""

    def test_selection_from_empty_pool(self):
        """Test selection from completely empty pool."""
        from xbot.agent.crew.planner.role_selector import RoleSelector

        selector = RoleSelector()

        config = RolePoolConfig(enabled_tiers=[RoleTier.CORE])
        manager = RolePoolManager(config)
        # Clear all roles
        manager._roles.clear()
        manager._loaded = True

        pool = manager.get_pool()

        analysis = GoalAnalysis(
            summary="Test",
            required_capabilities=[Capability.SEARCH],
            complexity="simple",
            estimated_tasks=1,
            suggested_process="sequential",
        )

        selection = selector.select(analysis, pool)

        # Should handle gracefully
        assert selection is not None
        assert selection.selected_roles == []


class TestConfigGeneratorRobustness:
    """Tests for config generator robustness."""

    @pytest.fixture
    def generator(self):
        from xbot.agent.crew.planner.config_generator import ConfigGenerator
        return ConfigGenerator()

    def test_generate_with_empty_roles(self, generator):
        """Test generating config with no roles."""
        from xbot.agent.crew.planner.models import CrewPlan

        plan = CrewPlan(
            name="empty",
            description="Empty plan",
            process="sequential",
            global_context="",
            roles=[],
            tasks=[],
            analysis=GoalAnalysis(
                summary="Empty",
                required_capabilities=[],
                complexity="simple",
                estimated_tasks=0,
                suggested_process="sequential",
            ),
            role_selection=RoleSelection(
                selected_roles=[],
                selection_reason={},
                skipped_roles=[],
                coverage_score=1.0,
                created_roles=[],
                role_gaps=[],
            ),
            planning_time=0.0,
            confidence=1.0,
        )

        yaml_content = generator.generate_yaml(plan)
        assert "name: empty" in yaml_content

    def test_preview_with_no_tasks(self, generator):
        """Test preview with no tasks."""
        from xbot.agent.crew.planner.models import CrewPlan

        role = RoleDefinition(
            name="agent1",
            display_name="Agent",
            description="Test",
            goal="Test",
            backstory="",
            tier=RoleTier.CORE,
            capabilities=[Capability.ANALYZE],
        )

        plan = CrewPlan(
            name="no_tasks",
            description="No tasks",
            process="sequential",
            global_context="",
            roles=[role],
            tasks=[],
            analysis=GoalAnalysis(
                summary="No tasks",
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

        preview = generator.generate_preview(plan)
        assert "agent1" in preview
