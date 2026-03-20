"""Agent protocol definitions.

This module defines the abstract interface for Agent backends,
enabling interchangeable implementations (LiteLLM, Claude SDK, etc.).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class AgentResponse:
    """Unified Agent response format."""

    content: str
    progress_texts: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str = "stop"  # stop | tool_use | error | max_iterations
    usage: dict[str, Any] | None = None
    raw_message: Any = None

    # For streaming support
    is_delta: bool = False
    delta_content: str = ""


@dataclass
class AgentContext:
    """Context for agent processing."""

    session_key: str
    prompt: str
    history: list[dict[str, Any]] = field(default_factory=list)
    media: list[Any] | None = None
    channel: str = ""
    chat_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentBackend(ABC):
    """Abstract base class for Agent backends.

    Agent backends are responsible for:
    - Processing messages using LLM
    - Managing tools and their execution
    - Streaming responses

    Implementations:
    - LiteLLMBackend: Uses the existing AgentLoop with LiteLLM
    - ClaudeSDKBackend: Uses the Claude Agent SDK
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend name identifier."""
        pass

    @abstractmethod
    async def initialize(self, config: Any, shared_resources: dict[str, Any]) -> None:
        """Initialize the agent backend.

        Args:
            config: Agent configuration (AgentsConfig)
            shared_resources: Shared resources (bus, config, workspace, etc.)

        Raises:
            ConfigurationError: If configuration is invalid
        """
        pass

    @abstractmethod
    async def process(self, context: AgentContext) -> AsyncIterator[AgentResponse]:
        """Process a message and yield responses.

        Args:
            context: Processing context with session info and prompt

        Yields:
            AgentResponse objects (streaming)
        """
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        """Shutdown the agent backend and release resources."""
        pass

    async def execute_tool(self, tool_name: str, args: dict[str, Any]) -> str | None:
        """Execute a tool directly (optional).

        Args:
            tool_name: Name of the tool
            args: Tool arguments

        Returns:
            Tool result string, or None if not supported
        """
        return None

    async def reset_session(self, session_key: str) -> None:
        """Reset backend state for a session (optional)."""
        return None

    async def cancel_session(self, session_key: str) -> int:
        """Cancel active backend-managed work for one session (optional)."""
        return 0

    def get_tools_summary(self) -> str:
        """Get a summary of available tools (optional).

        Returns:
            Summary string, or empty string if not implemented
        """
        return ""
