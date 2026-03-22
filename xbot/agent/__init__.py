"""Agent core module."""

from xbot.agent.context import ContextBuilder
from xbot.agent.capabilities import CapabilityCatalog
from xbot.agent.capability_policy import CapabilityPolicy
from xbot.agent.memory import MemoryStore
from xbot.agent.skills import SkillsLoader
from xbot.agent.protocol import AgentBackend, AgentResponse, AgentContext
from xbot.agent.router import AgentRouter
from xbot.agent.runtime import AgentRuntime

__all__ = [
    "CapabilityCatalog",
    "CapabilityPolicy",
    "ContextBuilder",
    "MemoryStore",
    "SkillsLoader",
    # New exports for dual-agent architecture
    "AgentBackend",
    "AgentResponse",
    "AgentContext",
    "AgentRouter",
    "AgentRuntime",
]
