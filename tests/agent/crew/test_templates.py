"""Tests for crew templates module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from xbot.agent.crew.templates import (
    BUILTIN_TEMPLATES,
    CrewTemplate,
    get_template,
    init_project,
    list_templates,
)


class TestCrewTemplate:
    """Tests for CrewTemplate dataclass."""

    def test_load_config_returns_dict(self):
        """Test that load_config returns a valid dict."""
        template = get_template("code-review")
        assert template is not None

        config = template.load_config()
        assert isinstance(config, dict)
        assert "name" in config
        assert "agents" in config
        assert "tasks" in config

    def test_load_readme_returns_content(self):
        """Test that load_readme returns README content."""
        template = get_template("code-review")
        assert template is not None

        readme = template.load_readme()
        assert readme is not None
        assert "Code Review" in readme

    def test_load_readme_returns_none_if_not_exists(self, tmp_path: Path):
        """Test that load_readme returns None if README doesn't exist."""
        template = CrewTemplate(
            name="test",
            description="Test",
            config_path=tmp_path / "config.yaml",
            readme_path=tmp_path / "nonexistent.md",
        )
        # Create config file but not README
        (tmp_path / "config.yaml").write_text("name: test\n")

        assert template.load_readme() is None


class TestListTemplates:
    """Tests for list_templates function."""

    def test_returns_all_builtin_templates(self):
        """Test that all built-in templates are returned."""
        templates = list_templates()
        template_names = {t.name for t in templates}

        assert template_names == set(BUILTIN_TEMPLATES.keys())

    def test_all_templates_have_valid_config(self):
        """Test that all templates have valid YAML configs."""
        templates = list_templates()

        for template in templates:
            config = template.load_config()
            assert "name" in config, f"Template {template.name} missing 'name'"
            assert "agents" in config, f"Template {template.name} missing 'agents'"
            assert "tasks" in config, f"Template {template.name} missing 'tasks'"

    def test_all_template_configs_are_valid_crew_configs(self):
        """Test that all template configs can be loaded as CrewConfig."""
        from xbot.agent.crew import load_crew_config

        templates = list_templates()

        for template in templates:
            # Use load_crew_config which handles the name field injection
            crew_config = load_crew_config(template.config_path)
            assert crew_config.name
            assert len(crew_config.agents) > 0
            assert len(crew_config.tasks) > 0


class TestGetTemplate:
    """Tests for get_template function."""

    def test_returns_template_for_valid_name(self):
        """Test that get_template returns template for valid name."""
        template = get_template("code-review")
        assert template is not None
        assert template.name == "code-review"

    def test_returns_none_for_invalid_name(self):
        """Test that get_template returns None for invalid name."""
        template = get_template("nonexistent-template")
        assert template is None

    def test_all_builtin_templates_accessible(self):
        """Test that all built-in templates are accessible."""
        for name in BUILTIN_TEMPLATES:
            template = get_template(name)
            assert template is not None, f"Template {name} not found"
            assert template.name == name
            assert template.config_path.exists()


class TestInitProject:
    """Tests for init_project function."""

    def test_creates_project_with_default_config(self, tmp_path: Path):
        """Test creating project without template."""
        project_dir = tmp_path / "test_project"
        config_path = init_project(project_dir)

        assert config_path.exists()
        assert config_path.name == "crew_config.yaml"
        assert (project_dir / "workspace").exists()
        assert (project_dir / ".xbot" / "crew_checkpoints").exists()
        assert (project_dir / "README.md").exists()

    def test_creates_project_with_template(self, tmp_path: Path):
        """Test creating project with a template."""
        project_dir = tmp_path / "code_review_project"
        config_path = init_project(project_dir, template_name="code-review")

        assert config_path.exists()

        # Verify config is from template
        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Project name should be derived from directory
        assert config["name"] == "code_review_project"

        # Should have template's agents and tasks
        assert "reviewer" in config["agents"]
        assert len(config["tasks"]) == 3

    def test_custom_project_name(self, tmp_path: Path):
        """Test creating project with custom name."""
        project_dir = tmp_path / "my_dir"
        config_path = init_project(project_dir, project_name="custom_name")

        with open(config_path) as f:
            config = yaml.safe_load(f)

        assert config["name"] == "custom_name"

    def test_raises_for_unknown_template(self, tmp_path: Path):
        """Test that init_project raises for unknown template."""
        project_dir = tmp_path / "test_project"

        with pytest.raises(ValueError, match="Unknown template"):
            init_project(project_dir, template_name="nonexistent")

        # Directory should NOT be created when template is invalid
        assert not project_dir.exists()

    def test_default_config_has_valid_structure(self, tmp_path: Path):
        """Test that default config is valid CrewConfig."""

        project_dir = tmp_path / "test_project"
        config_path = init_project(project_dir)

        # Should load without errors
        from xbot.agent.crew import load_crew_config
        crew_config = load_crew_config(config_path)

        assert crew_config.name == "test_project"
        assert len(crew_config.agents) == 1
        assert len(crew_config.tasks) == 1

    def test_template_config_is_valid(self, tmp_path: Path):
        """Test that template configs are valid."""
        from xbot.agent.crew import load_crew_config

        for template_name in BUILTIN_TEMPLATES:
            project_dir = tmp_path / f"project_{template_name}"
            config_path = init_project(project_dir, template_name=template_name)

            # Should load without errors
            crew_config = load_crew_config(config_path)
            assert crew_config.name == f"project_{template_name}"


class TestTemplateConfigs:
    """Tests for individual template configurations."""

    @pytest.mark.parametrize("template_name", list(BUILTIN_TEMPLATES.keys()))
    def test_template_has_agents(self, template_name: str):
        """Test that each template has at least one agent."""
        template = get_template(template_name)
        assert template is not None

        config = template.load_config()
        assert len(config.get("agents", {})) >= 1

    @pytest.mark.parametrize("template_name", list(BUILTIN_TEMPLATES.keys()))
    def test_template_has_tasks(self, template_name: str):
        """Test that each template has at least one task."""
        template = get_template(template_name)
        assert template is not None

        config = template.load_config()
        assert len(config.get("tasks", [])) >= 1

    @pytest.mark.parametrize("template_name", list(BUILTIN_TEMPLATES.keys()))
    def test_template_task_agent_references_valid(self, template_name: str):
        """Test that all task agent references exist in agents."""
        template = get_template(template_name)
        assert template is not None

        config = template.load_config()
        agents = set(config.get("agents", {}).keys())

        for task in config.get("tasks", []):
            assert task["agent"] in agents, (
                f"Template {template_name}: task '{task['name']}' "
                f"references unknown agent '{task['agent']}'"
            )

    @pytest.mark.parametrize("template_name", list(BUILTIN_TEMPLATES.keys()))
    def test_template_dependencies_valid(self, template_name: str):
        """Test that all task dependencies reference existing tasks."""
        template = get_template(template_name)
        assert template is not None

        config = template.load_config()
        task_names = {t["name"] for t in config.get("tasks", [])}

        for task in config.get("tasks", []):
            for dep in task.get("context_from", []):
                assert dep in task_names, (
                    f"Template {template_name}: task '{task['name']}' "
                    f"has invalid dependency '{dep}'"
                )
