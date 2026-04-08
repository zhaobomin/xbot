"""Dynamic crew planning module.

This module provides intelligent crew planning capabilities:
- Role pool management with predefined roles
- Dynamic role creation for missing capabilities
- Goal analysis and task planning
- Crew configuration generation
"""

from xbot.agent.crew.planner.config_generator import ConfigGenerator
from xbot.agent.crew.planner.crew_planner import CrewPlanner
from xbot.agent.crew.planner.models import (
    Capability,
    CrewPlan,
    GoalAnalysis,
    RoleCreationRequest,
    RoleCreationResult,
    RoleDefinition,
    RoleGap,
    RolePool,
    RolePoolConfig,
    RoleSelection,
    RoleTier,
    TaskPlan,
)
from xbot.agent.crew.planner.role_creator import RoleCreator, validate_role_file
from xbot.agent.crew.planner.role_pool import RolePoolManager, parse_tier_list
from xbot.agent.crew.planner.role_selector import RoleSelector
from xbot.agent.crew.planner.task_planner import TaskPlanner

__all__ = [
    # Models
    "Capability",
    "CrewPlan",
    "GoalAnalysis",
    "RoleCreationRequest",
    "RoleCreationResult",
    "RoleDefinition",
    "RoleGap",
    "RolePool",
    "RolePoolConfig",
    "RoleSelection",
    "RoleTier",
    "TaskPlan",
    # Core components
    "CrewPlanner",
    "ConfigGenerator",
    "RoleCreator",
    "RolePoolManager",
    "RoleSelector",
    "TaskPlanner",
    # Utility functions
    "parse_tier_list",
    "validate_role_file",
]
