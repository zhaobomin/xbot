"""Unified data types for agent system.

This module consolidates type definitions from various modules
to provide a single source of truth for agent-related types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Re-export from protocol.py for backward compatibility
from xbot.runtime.core.protocol import AgentContext, AgentResponse

# Re-export from state/machine.py for backward compatibility
from xbot.runtime.state.machine import SessionPhase, SessionState


@dataclass
class AgentConfig:
    """Configuration for an Agent instance.

    Attributes:
        model: Model identifier (e.g., "claude-sonnet-4-6")
        system_prompt: System prompt for the agent
        tools: List of tool configurations
        mcp_servers: MCP server configurations
        agents: SDK agent definitions for subagent support
    """

    model: str
    system_prompt: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    agents: list[dict[str, Any]] | None = None


@dataclass
class SessionConfig:
    """Configuration for a session.

    Attributes:
        workspace: Workspace directory path
        permissions: Permission settings for this session
    """

    workspace: str
    permissions: dict[str, Any] = field(default_factory=dict)


__all__ = [
    # From this module
    "AgentConfig",
    "SessionConfig",
    # Re-exported for convenience
    "AgentResponse",
    "AgentContext",
    "SessionPhase",
    "SessionState",
]
