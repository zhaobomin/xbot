"""Configuration generator for creating crew_config.yaml from plans."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import yaml

from xbot.agent.crew.planner.models import CrewPlan, RoleDefinition, TaskPlan
from xbot.agent.crew.planner.utils import RoleConverter

logger = logging.getLogger(__name__)


class ConfigGenerator:
    """Generates crew configuration files from CrewPlan.

    This class handles:
    - Converting CrewPlan to YAML format
    - Writing configuration files
    - Adding metadata and comments
    """

    def generate_yaml(self, plan: CrewPlan) -> str:
        """Generate YAML configuration string from a crew plan.

        Args:
            plan: The crew plan to convert.

        Returns:
            YAML string ready to be written to a file.
        """
        config = {
            "name": plan.name,
            "description": plan.description,
            "process": plan.process,
            "workspace": ".",
            "global_context": plan.global_context,
            "agents": self._build_agents(plan.roles),
            "tasks": self._build_tasks(plan.tasks),
        }

        return yaml.dump(
            config,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    def _build_agents(self, roles: list[RoleDefinition]) -> dict:
        """Build agents section of the config using RoleConverter."""
        agents = {}
        for role in roles:
            # Use unified converter for consistency
            agent_config = RoleConverter.to_agent_config(role)
            agents[role.name] = agent_config
        return agents

    def _build_tasks(self, tasks: list[TaskPlan]) -> list[dict]:
        """Build tasks section of the config."""
        result = []
        for task in tasks:
            task_config = {
                "name": task.name,
                "description": task.description,
                "agent": task.agent,
                "expected_output": task.expected_output,
                "timeout": task.timeout,
            }
            if task.dependencies:
                task_config["context_from"] = task.dependencies
            if task.human_review:
                task_config["human_review"] = True
            result.append(task_config)
        return result

    def save(
        self,
        plan: CrewPlan,
        path: Path,
        include_metadata: bool = True,
    ) -> Path:
        """Save the crew plan to a YAML file.

        Args:
            plan: The crew plan to save.
            path: Destination file path.
            include_metadata: Whether to include generation metadata as comments.

        Returns:
            Path to the saved file.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        lines = []

        if include_metadata:
            lines.append("# Auto-generated Crew Configuration")
            lines.append(f"# Generated at: {datetime.now().isoformat()}")
            lines.append(f"# Planning time: {plan.planning_time:.2f}s")
            lines.append(f"# Confidence: {plan.confidence:.0%}")
            lines.append(f"# Roles: {len(plan.roles)}")
            lines.append(f"# Tasks: {len(plan.tasks)}")
            lines.append("")

        yaml_content = self.generate_yaml(plan)
        lines.append(yaml_content)

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.info(f"Saved crew config to {path}")
        return path

    def generate_preview(self, plan: CrewPlan) -> str:
        """Generate a human-readable preview of the plan.

        Args:
            plan: The crew plan to preview.

        Returns:
            Formatted string preview.
        """
        lines = [
            f"Crew: {plan.name}",
            f"Description: {plan.description}",
            "",
            f"Process: {plan.process}",
            f"Confidence: {plan.confidence:.0%}",
            "",
            "Roles:",
        ]

        for role in plan.roles:
            caps = ", ".join(c.value for c in role.capabilities)
            lines.append(f"  - {role.display_name} ({role.name})")
            lines.append(f"    Capabilities: {caps}")

        lines.append("")
        lines.append("Tasks:")

        for i, task in enumerate(plan.tasks, 1):
            deps = f" (depends on: {', '.join(task.dependencies)})" if task.dependencies else ""
            lines.append(f"  {i}. {task.name}")
            lines.append(f"     Agent: {task.agent}")
            # Handle None/empty description gracefully
            desc = task.description or "(no description)"
            if len(desc) > 60:
                desc = desc[:60] + "..."
            lines.append(f"     Description: {desc}")
            if deps:
                lines.append(f"     {deps}")

        return "\n".join(lines)