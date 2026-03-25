"""Crew orchestration data models and YAML configuration loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class ProcessType(str, Enum):
    """Crew execution strategy."""

    sequential = "sequential"
    hierarchical = "hierarchical"


class UserAction(str, Enum):
    """Human review actions for task intervention."""

    CONTINUE = "continue"
    ANNOTATE = "annotate"
    EDIT = "edit"
    REDO = "redo"
    SKIP = "skip"
    ABORT = "abort"


class OutputFormat(str, Enum):
    """Supported output formats for tasks."""

    RAW = "raw"
    JSON = "json"
    MARKDOWN = "markdown"
    STRUCTURED = "structured"


class OutputConfig(BaseModel):
    """Configuration for output management."""

    enabled: bool = True
    formats: list[str] = ["json"]
    artifacts_dir: str | None = None
    retention_days: int = 30
    max_output_size: int = 100000  # Max characters per task output


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

    # Output format configuration
    output_format: OutputFormat = OutputFormat.RAW
    output_schema: dict[str, Any] | None = None  # JSON schema for validation
    output_template: str | None = None  # Template for structured output


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
    manager_timeout: int = 120  # Timeout in seconds for manager plan generation
    max_context_length: int = 4000  # Max chars for upstream output in prompts

    # Configuration inheritance
    extends: str | None = None  # Template or parent config to extend
    variables: dict[str, str] = Field(default_factory=dict)  # Config variables

    # Output management
    output: OutputConfig = Field(default_factory=OutputConfig)


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

    # Output format information
    output_format: OutputFormat = OutputFormat.RAW
    structured_output: dict[str, Any] | None = None  # Parsed structured data
    artifacts: list[str] = field(default_factory=list)  # Generated file paths
    truncated: bool = False  # Whether output was truncated
    repaired: bool = False  # Whether output was repaired by LLM

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

    return parse_crew_config(raw, path)


def parse_crew_config(raw: dict[str, Any], config_path: Path | None = None) -> CrewConfig:
    """Parse and validate a CrewConfig from a raw dictionary.

    This is useful when you have already loaded/resolved the config dict
    (e.g., from CrewConfigLoader) and need to convert it to a CrewConfig.

    Args:
        raw: Raw configuration dictionary
        config_path: Optional path to the config file (for workspace resolution)

    Returns:
        Validated CrewConfig instance

    Raises:
        ValueError: If the config is invalid
        pydantic.ValidationError: If validation fails
    """
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
                definition = definition.copy()  # Don't modify original
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

    # Resolve workspace relative to config file location
    if config_path:
        ws = Path(config.workspace)
        if not ws.is_absolute():
            config.workspace = str((config_path.parent / ws).resolve())

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
                f"manager_agent '{config.manager_agent}' not found in agents. "
                f"Available: {list(config.agents.keys())}"
            )

    return config
