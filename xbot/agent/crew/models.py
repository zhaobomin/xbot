"""Crew orchestration data models and YAML configuration loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ProcessType(str, Enum):
    """Crew execution strategy."""

    sequential = "sequential"
    hierarchical = "hierarchical"


class AgentRole(BaseModel):
    """Definition of a single crew member role."""

    name: str  # Role identifier, e.g. "bug_scout"
    description: str  # Role responsibilities
    goal: str  # Role objective (injected into prompt)
    backstory: str = ""  # Optional background context
    model: str = "inherit"  # "inherit" uses the global model
    tools: list[str] | None = None  # Available tools; None = all
    max_iterations: int = 30  # Max tool-call rounds per task


class TaskDefinition(BaseModel):
    """Definition of a single task within a crew."""

    name: str  # Unique task identifier
    description: str  # Task description (used as prompt body)
    agent: str  # Name of the executing role
    expected_output: str = ""  # Expected output format description
    context_from: list[str] = Field(default_factory=list)  # Upstream task names
    human_review: bool = False  # Require human review after completion
    human_briefing: bool = False  # Allow human to add instructions before execution
    timeout: int = 600  # Timeout in seconds


class CrewConfig(BaseModel):
    """Top-level crew configuration loaded from YAML."""

    name: str
    description: str = ""
    process: ProcessType = ProcessType.sequential
    agents: dict[str, AgentRole]  # Role name -> role definition
    tasks: list[TaskDefinition]  # Ordered task list
    workspace: str = "."  # Target project path
    verbose: bool = False
    global_context: str = ""  # Global context injected into all prompts
    manager_agent: str | None = None  # Manager role for hierarchical mode


@dataclass
class TaskResult:
    """Runtime result of a single task execution."""

    task_name: str
    agent_name: str
    output: str  # Agent's raw output
    status: str  # success | failed | skipped | human_rejected
    started_at: datetime
    finished_at: datetime
    human_edited_output: str | None = None  # Human-modified output (replaces output for downstream)
    human_annotations: list[str] = field(default_factory=list)  # Human review notes
    human_briefing_input: str | None = None  # Pre-execution human instructions
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_output(self) -> str:
        """Output for downstream tasks: human edit takes priority over raw output."""
        if self.human_edited_output is not None:
            return self.human_edited_output
        return self.output


@dataclass
class CrewResult:
    """Overall crew execution result."""

    crew_name: str
    task_results: list[TaskResult]
    status: str  # completed | failed | aborted
    total_time: float
    summary: str


def load_crew_config(path: Path) -> CrewConfig:
    """Load and validate a CrewConfig from a YAML file.

    The ``workspace`` field in the YAML is resolved relative to the YAML
    file's parent directory when it is a relative path.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        yaml.YAMLError: If the YAML is malformed.
        pydantic.ValidationError: If the data fails validation.
    """
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Crew config not found: {path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Expected a YAML mapping at top level, got {type(raw).__name__}")

    # Parse agents: support both dict-of-dicts and list-of-dicts formats
    raw_agents = raw.get("agents", {})
    if isinstance(raw_agents, list):
        agents = {}
        for item in raw_agents:
            name = item.get("name")
            if not name:
                raise ValueError("Each agent in the list must have a 'name' field")
            agents[name] = AgentRole(**item)
        raw["agents"] = agents
    elif isinstance(raw_agents, dict):
        agents = {}
        for name, definition in raw_agents.items():
            if isinstance(definition, dict):
                definition.setdefault("name", name)
                agents[name] = AgentRole(**definition)
            else:
                raise ValueError(f"Invalid agent definition for '{name}'")
        raw["agents"] = agents

    # Parse tasks
    raw_tasks = raw.get("tasks", [])
    if isinstance(raw_tasks, list):
        tasks = []
        for item in raw_tasks:
            if isinstance(item, dict):
                tasks.append(TaskDefinition(**item))
            else:
                raise ValueError(f"Invalid task definition: {item}")
        raw["tasks"] = tasks

    config = CrewConfig(**raw)

    # Resolve workspace relative to YAML location
    ws = Path(config.workspace)
    if not ws.is_absolute():
        config.workspace = str((path.parent / ws).resolve())

    # Validate: all task.agent references exist in agents
    for task in config.tasks:
        if task.agent not in config.agents:
            raise ValueError(
                f"Task '{task.name}' references unknown agent '{task.agent}'. "
                f"Available agents: {list(config.agents.keys())}"
            )

    # Validate: all context_from references exist in tasks
    task_names = {t.name for t in config.tasks}
    for task in config.tasks:
        for dep in task.context_from:
            if dep not in task_names:
                raise ValueError(
                    f"Task '{task.name}' has context_from '{dep}' "
                    f"which is not a defined task. Available: {task_names}"
                )

    # Validate: hierarchical mode has manager_agent if specified
    if config.process == ProcessType.hierarchical and config.manager_agent:
        if config.manager_agent not in config.agents:
            raise ValueError(
                f"manager_agent '{config.manager_agent}' not found in agents"
            )

    return config
