"""Agent protocol definitions.

This module defines the abstract interface for Agent backends,
enabling interchangeable implementations (Claude SDK and custom backends).
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
    tool_hint_text: str = ""
    finish_reason: str = "stop"  # stop | tool_use | error | max_iterations
    usage: dict[str, Any] | None = None
    raw_message: Any = None
    event_type: str = ""
    event_data: dict[str, Any] | None = None

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
    - ClaudeSDKBackend: Uses the Claude Agent SDK
    - Custom backends implementing this protocol
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

    async def stop_active_task(self, session_key: str) -> bool:
        """Stop active backend task for one session (optional).

        Returns:
            True if a task stop was requested, False otherwise
        """
        return False

    async def interrupt_session(self, session_key: str) -> dict[str, Any]:
        """Interrupt any ongoing LLM request for a session (optional).

        Returns:
            Dict with 'interrupted' bool and optional 'usage' dict
        """
        return {"interrupted": False, "usage": None}

    async def compact_session(self, session_key: str) -> dict[str, Any]:
        """Force context compaction for a session (optional).

        Returns:
            Dict with compaction stats
        """
        return {
            "messages_consolidated": 0,
            "tokens_before": 0,
            "tokens_after": 0,
            "success": True,
            "message": "Compaction not supported",
        }

    async def get_session_commands(self, session_key: str) -> list[str]:
        """Get available slash commands for a session (optional)."""
        return []

    def get_tools_summary(self) -> str:
        """Get a summary of available tools (optional).

        Returns:
            Summary string, or empty string if not implemented
        """
        return ""
