"""Multi-agent crew orchestration for xbot.

This package implements crew-based task coordination where multiple
AI agent roles collaborate on complex workflows.
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

__all__ = [
    "AgentRole",
    "CrewConfig",
    "CrewOrchestrator",
    "CrewResult",
    "ProcessType",
    "TaskDefinition",
    "TaskResult",
    "load_crew_config",
]
