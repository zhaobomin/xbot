"""Crew template management and loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CrewTemplate:
    """A crew configuration template."""

    name: str
    description: str
    config_path: Path
    readme_path: Path | None = None

    def load_config(self) -> dict[str, Any]:
        """Load the template's crew configuration."""
        with open(self.config_path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def load_readme(self) -> str | None:
        """Load the template's README content."""
        if self.readme_path and self.readme_path.exists():
            with open(self.readme_path, encoding="utf-8") as f:
                return f.read()
        return None


# Built-in templates directory
TEMPLATES_DIR = Path(__file__).parent / "templates"

# Template registry
BUILTIN_TEMPLATES: dict[str, str] = {
    "code-review": "Code quality review and improvement suggestions",
    "doc-generator": "Generate documentation from code",
    "data-pipeline": "Design and implement data processing pipelines",
    "bug-hunter": "Find bugs and suggest fixes",
    "test-writer": "Write comprehensive tests for code",
}


def list_templates() -> list[CrewTemplate]:
    """List all available built-in templates."""
    templates = []

    for name, description in BUILTIN_TEMPLATES.items():
        template_dir = TEMPLATES_DIR / name
        config_path = template_dir / "crew_config.yaml"
        readme_path = template_dir / "README.md"

        if config_path.exists():
            templates.append(CrewTemplate(
                name=name,
                description=description,
                config_path=config_path,
                readme_path=readme_path if readme_path.exists() else None,
            ))

    return templates


def get_template(name: str) -> CrewTemplate | None:
    """Get a template by name."""
    if name not in BUILTIN_TEMPLATES:
        return None

    template_dir = TEMPLATES_DIR / name
    config_path = template_dir / "crew_config.yaml"

    if not config_path.exists():
        return None

    return CrewTemplate(
        name=name,
        description=BUILTIN_TEMPLATES[name],
        config_path=config_path,
        readme_path=template_dir / "README.md",
    )


def init_project(
    project_dir: Path,
    template_name: str | None = None,
    project_name: str | None = None,
) -> Path:
    """Initialize a new crew project.

    Args:
        project_dir: Directory to create the project in.
        template_name: Name of the template to use.
        project_name: Name for the project (defaults to directory name).

    Returns:
        Path to the created crew_config.yaml.

    Raises:
        ValueError: If template_name is specified but not found.
    """
    project_dir = project_dir.resolve()
    project_name = project_name or project_dir.name

    # Determine config content BEFORE creating directories
    if template_name:
        template = get_template(template_name)
        if template:
            config = template.load_config()
            # Update project name
            config["name"] = project_name
        else:
            raise ValueError(f"Unknown template: {template_name}")
    else:
        # Create minimal default config
        config = {
            "name": project_name,
            "description": f"Crew project: {project_name}",
            "process": "sequential",
            "workspace": "./workspace",
            "agents": {
                "worker": {
                    "description": "General worker",
                    "goal": "Complete assigned tasks",
                    "max_iterations": 30,
                }
            },
            "tasks": [
                {
                    "name": "example_task",
                    "description": "An example task - replace with your own",
                    "agent": "worker",
                    "timeout": 300,
                }
            ],
        }

    # Create project directory
    project_dir.mkdir(parents=True, exist_ok=True)

    # Create workspace subdirectory
    workspace_dir = project_dir / "workspace"
    workspace_dir.mkdir(exist_ok=True)

    # Create checkpoints directory
    checkpoints_dir = project_dir / ".xbot" / "crew_checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)

    # Write config file
    config_path = project_dir / "crew_config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    # Create a basic README
    readme_path = project_dir / "README.md"
    readme_content = f"""# {project_name}

A crew project for multi-agent orchestration.

## Usage

```bash
# Run the crew
xbot crew run crew_config.yaml

# Validate configuration
xbot crew validate crew_config.yaml

# View configuration
xbot crew show crew_config.yaml
```

## Structure

- `crew_config.yaml` - Crew configuration
- `workspace/` - Working directory for tasks
- `.xbot/crew_checkpoints/` - Execution checkpoints
"""
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_content)

    return config_path
