"""Planner data models for dynamic crew planning.

This module defines the core data structures for:
- Role definitions (extended from AgentRole with planning capabilities)
- Role pool management
- Goal analysis
- Task planning
- Crew planning results
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xbot.agent.crew.models import AgentRole


class RoleTier(str, Enum):
    """Role tier for access control and filtering.

    - CORE: Always available, basic roles
    - EXTENDED: Optional, enabled by configuration
    - SPECIALIST: Requires explicit enablement, sensitive operations
    """

    CORE = "core"
    EXTENDED = "extended"
    SPECIALIST = "specialist"


class Capability(str, Enum):
    """Capability enumeration for role matching.

    These capabilities are used to match roles to task requirements
    during the planning phase.
    """

    # Information processing
    SEARCH = "search"
    ANALYZE = "analyze"
    SUMMARIZE = "summarize"

    # Code operations
    READ_CODE = "read_code"
    WRITE_CODE = "write_code"
    REFACTOR = "refactor"
    DEBUG = "debug"

    # Quality assurance
    REVIEW = "review"
    TEST = "test"
    VALIDATE = "validate"

    # Documentation
    DOCUMENT = "document"

    # Data
    DATA_ANALYSIS = "data_analysis"
    ML = "machine_learning"

    # DevOps
    DEPLOY = "deploy"
    MONITOR = "monitor"

    # Security
    SECURITY_AUDIT = "security_audit"


@dataclass
class RoleDefinition:
    """Role definition loaded from YAML.

    This is the planning-time representation of a role,
    which can be converted to an AgentRole for execution.
    """

    name: str
    display_name: str
    description: str
    goal: str
    backstory: str
    tier: RoleTier
    capabilities: list[Capability]

    # Tool configuration
    tools: list[str] | None = None  # None means all tools available
    tool_restrictions: list[str] | None = None  # Explicitly forbidden tools

    # Execution configuration
    max_iterations: int = 30
    timeout_multiplier: float = 1.0

    # Metadata
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)  # Usage examples

    def to_agent_role(self) -> "AgentRole":
        """Convert to execution-time AgentRole.

        Delegates to RoleConverter.to_agent_role for consistency.

        Returns:
            AgentRole instance for use with CrewOrchestrator.
        """
        from xbot.agent.crew.planner.utils import RoleConverter
        return RoleConverter.to_agent_role(self)

    def matches_capabilities(self, required: list[Capability]) -> float:
        """Calculate capability match score.

        Args:
            required: List of required capabilities.

        Returns:
            Match score between 0.0 and 1.0.
            Returns 1.0 if no requirements (any role matches).
        """
        if not required:
            return 1.0  # No requirements = full match
        matched = len(set(self.capabilities) & set(required))
        return matched / len(required)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for YAML serialization.

        Delegates to RoleConverter.to_yaml_dict for consistency.
        """
        from xbot.agent.crew.planner.utils import RoleConverter
        return RoleConverter.to_yaml_dict(self)


@dataclass
class RolePoolConfig:
    """Configuration for role pool loading."""

    enabled_tiers: list[RoleTier] = field(
        default_factory=lambda: [RoleTier.CORE]
    )
    custom_roles_dir: str | None = None
    role_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    disabled_roles: list[str] = field(default_factory=list)
    role_aliases: dict[str, str] = field(default_factory=dict)


@dataclass
class RolePool:
    """Role pool containing all available roles."""

    roles: dict[str, RoleDefinition]
    config: RolePoolConfig

    def get_role(self, name: str) -> RoleDefinition | None:
        """Get a role by name or alias.

        Args:
            name: Role name or alias.

        Returns:
            RoleDefinition if found, None otherwise.
        """
        # Check for alias first
        if name in self.config.role_aliases:
            name = self.config.role_aliases[name]
        return self.roles.get(name)

    def get_roles_by_tier(self, tier: RoleTier) -> list[RoleDefinition]:
        """Get all roles of a specific tier."""
        return [r for r in self.roles.values() if r.tier == tier]

    def get_available_roles(self) -> list[RoleDefinition]:
        """Get all roles available under current configuration."""
        return [
            r for r in self.roles.values()
            if r.tier in self.config.enabled_tiers
        ]

    def find_by_capabilities(
        self,
        required: list[Capability],
        min_score: float = 0.5,
    ) -> list[tuple[RoleDefinition, float]]:
        """Find roles matching required capabilities.

        Args:
            required: List of required capabilities.
            min_score: Minimum match score threshold.

        Returns:
            List of (role, score) tuples, sorted by score descending.
        """
        results = []
        for role in self.get_available_roles():
            score = role.matches_capabilities(required)
            if score >= min_score:
                results.append((role, score))
        return sorted(results, key=lambda x: x[1], reverse=True)

    def to_description(self) -> str:
        """Generate role pool description for LLM prompts."""
        lines = ["Available roles:\n"]
        for role in self.get_available_roles():
            caps = ", ".join(c.value for c in role.capabilities)
            lines.append(f"- {role.name}: {role.description}")
            lines.append(f"  Capabilities: {caps}")
            if role.examples:
                lines.append(f"  Use cases: {', '.join(role.examples[:3])}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Planning Models
# ---------------------------------------------------------------------------


@dataclass
class GoalAnalysis:
    """Result of goal analysis."""

    summary: str
    required_capabilities: list[Capability]
    complexity: str  # simple | medium | complex
    estimated_tasks: int
    suggested_process: str  # sequential | hierarchical
    constraints: list[str] = field(default_factory=list)


@dataclass
class RoleGap:
    """Describes missing capabilities that no existing role covers."""

    missing_capabilities: list[Capability]
    suggested_role_name: str
    suggested_role_description: str
    coverage_gap: float  # Fraction of required capabilities missing


@dataclass
class RoleCreationRequest:
    """Request to create a new role."""

    suggested_name: str
    required_capabilities: list[Capability]
    reason: str
    context: str = ""


@dataclass
class RoleCreationResult:
    """Result of role creation."""

    success: bool
    role: RoleDefinition | None
    errors: list[str]
    warnings: list[str]
    requires_confirmation: bool = False
    confirmation_message: str = ""


@dataclass
class RoleSelection:
    """Result of role selection for a goal."""

    selected_roles: list[RoleDefinition]
    selection_reason: dict[str, str]  # role_name -> reason
    skipped_roles: list[str]  # Considered but not selected
    coverage_score: float
    created_roles: list[RoleDefinition] = field(default_factory=list)
    role_gaps: list[RoleGap] = field(default_factory=list)


@dataclass
class TaskPlan:
    """Planned task definition."""

    name: str
    description: str
    agent: str  # Role name
    dependencies: list[str] = field(default_factory=list)
    expected_output: str = ""
    timeout: int = 300
    human_review: bool = False
    priority: int = 0


@dataclass
class CrewPlan:
    """Complete crew plan generated by CrewPlanner."""

    name: str
    description: str
    process: str  # sequential | hierarchical
    global_context: str
    roles: list[RoleDefinition]
    tasks: list[TaskPlan]

    # Planning metadata
    analysis: GoalAnalysis
    role_selection: RoleSelection
    planning_time: float
    confidence: float

    created_at: datetime = field(default_factory=datetime.now)

    def validate(self) -> list[str]:
        """Validate the plan and return any errors."""
        errors = []
        role_names = {r.name for r in self.roles}
        task_names = {t.name for t in self.tasks}

        for task in self.tasks:
            # Check agent exists
            if task.agent not in role_names:
                errors.append(
                    f"Task '{task.name}' references unknown agent '{task.agent}'"
                )

            # Check dependencies exist
            for dep in task.dependencies:
                if dep not in task_names:
                    errors.append(
                        f"Task '{task.name}' has unknown dependency '{dep}'"
                    )

        return errors

    def to_crew_config_dict(self) -> dict[str, Any]:
        """Convert to a dictionary compatible with CrewConfig.

        Uses RoleConverter for consistent agent configuration.
        """
        from xbot.agent.crew.planner.utils import RoleConverter

        agents_dict = {}
        for role in self.roles:
            # Use RoleConverter for consistency
            agents_dict[role.name] = RoleConverter.to_agent_config(role)

        return {
            "name": self.name,
            "description": self.description,
            "process": self.process,
            "workspace": ".",
            "global_context": self.global_context,
            "agents": agents_dict,
            "tasks": [
                {
                    "name": task.name,
                    "description": task.description,
                    "agent": task.agent,
                    "expected_output": task.expected_output,
                    "timeout": task.timeout,
                    "context_from": task.dependencies,
                    "human_review": task.human_review,
                }
                for task in self.tasks
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dictionary for JSON serialization."""
        return {
            "name": self.name,
            "description": self.description,
            "process": self.process,
            "global_context": self.global_context,
            "roles": [role.to_dict() for role in self.roles],
            "tasks": [
                {
                    "name": task.name,
                    "description": task.description,
                    "agent": task.agent,
                    "dependencies": task.dependencies,
                    "expected_output": task.expected_output,
                    "timeout": task.timeout,
                    "human_review": task.human_review,
                }
                for task in self.tasks
            ],
            "analysis": {
                "summary": self.analysis.summary,
                "required_capabilities": [c.value for c in self.analysis.required_capabilities],
                "complexity": self.analysis.complexity,
                "estimated_tasks": self.analysis.estimated_tasks,
                "suggested_process": self.analysis.suggested_process,
            },
            "role_selection": {
                "selected_roles": [r.name for r in self.role_selection.selected_roles],
                "coverage_score": self.role_selection.coverage_score,
            },
            "planning_time": self.planning_time,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
        }
