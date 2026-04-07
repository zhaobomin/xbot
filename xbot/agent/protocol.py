"""Agent protocol definitions.

This module defines data classes for agent communication.
The AgentBackend abstract class has been removed since only
ClaudeSDKBackend exists (now AgentService).
"""

from dataclasses import dataclass, field
from typing import Any


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


__all__ = [
    "AgentResponse",
    "AgentContext",
]
