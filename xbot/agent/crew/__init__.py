"""Multi-agent crew orchestration for xbot.

This package implements crew-based task coordination where multiple
AI agent roles collaborate on complex workflows.

Modules:
- models: Core data models for crew configuration
- orchestrator: Crew execution engine
- planner: Dynamic crew planning (role pool, task planning)
"""

from xbot.agent.crew.models import (
    AgentRole,
    CrewConfig,
    CrewResult,
    ProcessType,
    TaskDefinition,
    TaskResult,
    load_crew_config,
)
from xbot.agent.crew.orchestrator import CrewOrchestrator

# Planner module - dynamic crew planning
from xbot.agent.crew.planner import (
    Capability,
    ConfigGenerator,
    CrewPlan,
    CrewPlanner,
    GoalAnalysis,
    RoleCreator,
    RoleDefinition,
    RolePool,
    RolePoolConfig,
    RolePoolManager,
    RoleSelection,
    RoleSelector,
    RoleTier,
    TaskPlan,
    TaskPlanner,
    parse_tier_list,
    validate_role_file,
)

__all__ = [
    # Core models
    "AgentRole",
    "CrewConfig",
    "CrewOrchestrator",
    "CrewResult",
    "ProcessType",
    "TaskDefinition",
    "TaskResult",
    "load_crew_config",
    # Planner
    "Capability",
    "ConfigGenerator",
    "CrewPlan",
    "CrewPlanner",
    "GoalAnalysis",
    "RoleCreator",
    "RoleDefinition",
    "RolePool",
    "RolePoolConfig",
    "RolePoolManager",
    "RoleSelector",
    "RoleSelection",
    "RoleTier",
    "TaskPlan",
    "TaskPlanner",
    "parse_tier_list",
    "validate_role_file",
]
