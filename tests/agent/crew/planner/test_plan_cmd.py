"""Tests for plan_cmd CLI module."""

import pytest
from pathlib import Path
import tempfile
from unittest.mock import patch, MagicMock

from click.testing import CliRunner
from typer.testing import CliRunner as TyperCliRunner

from xbot.agent.crew.cli.plan_cmd import (
    app,
    crew_plan,
    crew_run_dynamic,
)


runner = TyperCliRunner()


class TestCrewPlanCommand:
    """Tests for the crew plan command."""

    def test_plan_basic(self):
        """Test basic plan command."""
        result = runner.invoke(app, ["plan", "Analyze code quality"])

        assert result.exit_code == 0
        assert "Planning crew for:" in result.output
        assert "Analyze code quality" in result.output

    def test_plan_with_preview(self):
        """Test plan command with preview flag."""
        result = runner.invoke(app, ["plan", "Test the module", "--preview"])

        assert result.exit_code == 0
        assert "Crew Plan Preview" in result.output

    def test_plan_with_tier(self):
        """Test plan command with tier option."""
        result = runner.invoke(app, ["plan", "Search for information", "--tier", "core"])

        assert result.exit_code == 0
        assert "Tier: core" in result.output

    def test_plan_with_invalid_tier(self):
        """Test plan command with invalid tier."""
        result = runner.invoke(app, ["plan", "Test goal", "--tier", "invalid"])

        assert result.exit_code != 0
        assert "Invalid tier" in result.output

    def test_plan_with_output(self):
        """Test plan command with output file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "crew.yaml"
            result = runner.invoke(app, [
                "plan", "Write a function",
                "--output", str(output_path),
            ])

            assert result.exit_code == 0
            assert output_path.exists()
            content = output_path.read_text()
            assert "name:" in content

    def test_plan_with_save(self):
        """Test plan command with save flag."""
        result = runner.invoke(app, ["plan", "Debug the error", "--save"])

        assert result.exit_code == 0
        # Should mention saved config
        assert "Saved" in result.output or "name:" in result.output

    def test_plan_json_output(self):
        """Test plan command with JSON output."""
        result = runner.invoke(app, ["plan", "Simple task", "--json"])

        assert result.exit_code == 0
        # Should contain JSON structure
        assert '"name"' in result.output or '"roles"' in result.output

    def test_plan_extended_tier(self):
        """Test plan command with extended tier."""
        result = runner.invoke(app, ["plan", "Document the code", "--tier", "extended"])

        assert result.exit_code == 0
        assert "Tier: extended" in result.output


class TestCrewRunDynamicCommand:
    """Tests for the crew run-dynamic command."""

    def test_run_dynamic_dry_run(self):
        """Test run-dynamic with dry-run flag."""
        result = runner.invoke(app, ["run-dynamic", "Test goal", "--dry-run"])

        assert result.exit_code == 0
        assert "Plan generated" in result.output
        assert "Dry Run" in result.output

    def test_run_dynamic_with_preview(self):
        """Test run-dynamic with preview flag."""
        result = runner.invoke(app, ["run-dynamic", "Analyze data", "--preview", "--dry-run"])

        assert result.exit_code == 0
        assert "Plan Preview" in result.output

    def test_run_dynamic_with_tier(self):
        """Test run-dynamic with tier option."""
        result = runner.invoke(app, ["run-dynamic", "Test task", "--tier", "core", "--dry-run"])

        assert result.exit_code == 0
        assert "Roles:" in result.output

    def test_run_dynamic_invalid_tier(self):
        """Test run-dynamic with invalid tier."""
        result = runner.invoke(app, ["run-dynamic", "Test goal", "--tier", "invalid"])

        assert result.exit_code != 0
        assert "Invalid tier" in result.output

    def test_run_dynamic_with_save_config(self):
        """Test run-dynamic with save-config flag (dry run)."""
        result = runner.invoke(app, ["run-dynamic", "Simple task", "--save-config", "--dry-run"])

        assert result.exit_code == 0
        # Should show the generated config
        assert "name:" in result.output


class TestPlanCommandIntegration:
    """Integration tests for plan commands."""

    def test_plan_complex_goal(self):
        """Test planning a complex goal."""
        result = runner.invoke(app, [
            "plan",
            "Design and implement a REST API with authentication",
            "--tier", "core",
            "--preview",
        ])

        assert result.exit_code == 0
        # Should have multiple roles for complex task
        assert "Roles:" in result.output

    def test_plan_with_workspace(self):
        """Test plan with custom workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = runner.invoke(app, [
                "plan",
                "Analyze the project",
                "--workspace", tmpdir,
            ])

            assert result.exit_code == 0
            # Workspace should be in context
            assert tmpdir in result.output or "workspace:" in result.output.lower()

    def test_plan_chinese_goal(self):
        """Test planning with Chinese goal."""
        result = runner.invoke(app, ["plan", "分析代码质量"])

        assert result.exit_code == 0
        assert "分析代码质量" in result.output


class TestPrintCrewResult:
    """Tests for _print_crew_result function."""

    def test_print_successful_result(self):
        """Test printing successful result."""
        from xbot.agent.crew.cli.plan_cmd import _print_crew_result
        from io import StringIO
        import sys

        # Create mock result
        result = MagicMock()
        result.success = True
        result.execution_time = 10.5
        result.task_results = []
        result.error = None

        # Capture output
        old_stdout = sys.stdout
        sys.stdout = StringIO()

        try:
            _print_crew_result(result)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        assert "completed successfully" in output

    def test_print_failed_result(self):
        """Test printing failed result."""
        from xbot.agent.crew.cli.plan_cmd import _print_crew_result
        from io import StringIO
        import sys

        # Create mock result
        result = MagicMock()
        result.success = False
        result.execution_time = 5.0
        result.task_results = []
        result.error = "Test error"

        # Capture output
        old_stdout = sys.stdout
        sys.stdout = StringIO()

        try:
            _print_crew_result(result)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        assert "failed" in output.lower()
        assert "Test error" in output


class TestCLIHelp:
    """Tests for CLI help messages."""

    def test_plan_help(self):
        """Test plan command help."""
        result = runner.invoke(app, ["plan", "--help"])

        assert result.exit_code == 0
        assert "Generate a crew configuration" in result.output
        assert "--workspace" in result.output
        assert "--tier" in result.output
        assert "--output" in result.output
        assert "--preview" in result.output

    def test_run_dynamic_help(self):
        """Test run-dynamic command help."""
        result = runner.invoke(app, ["run-dynamic", "--help"])

        assert result.exit_code == 0
        assert "Plan and run a crew dynamically" in result.output
        assert "--dry-run" in result.output
        assert "--save-config" in result.output
        assert "--preview" in result.output


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_goal(self):
        """Test with empty goal string."""
        # Empty goal should be rejected with error
        result = runner.invoke(app, ["plan", ""])

        # Should fail with validation error
        assert result.exit_code != 0
        assert "empty" in result.output.lower() or "error" in result.output.lower()

    def test_very_long_goal(self):
        """Test with very long goal."""
        long_goal = "Analyze and fix " + "multiple " * 50 + "issues"
        result = runner.invoke(app, ["plan", long_goal])

        assert result.exit_code == 0

    def test_special_characters_in_goal(self):
        """Test with special characters in goal."""
        result = runner.invoke(app, ["plan", "Fix bug #123 & improve @performance"])

        assert result.exit_code == 0

    def test_all_tiers(self):
        """Test with --tier all."""
        result = runner.invoke(app, ["plan", "Complex task", "--tier", "all"])

        assert result.exit_code == 0
        assert "Tier: all" in result.output