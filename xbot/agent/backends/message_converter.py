"""Message conversion for Claude SDK backend.

This module provides utilities for converting SDK message types to
xbot's AgentResponse format.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from xbot.logging import get_logger

logger = get_logger(__name__)

from xbot.agent.capabilities.catalog import canonical_tool_name
from xbot.agent.interaction.event_formatter import (
    format_compact_event,
    format_rate_limit_event,
    format_task_notification,
)
from xbot.agent.protocol import AgentResponse

if TYPE_CHECKING:
    from xbot.agent.capabilities.catalog import CapabilityCatalog
    from xbot.agent.capabilities.handoff import HandoffPolicy

# Try to import Claude SDK types
try:
    from claude_agent_sdk.types import (
        AssistantMessage,
        RateLimitEvent,
        ResultMessage,
        StreamEvent,
        SystemMessage,
        TaskNotificationMessage,
        TaskProgressMessage,
        TaskStartedMessage,
        TextBlock,
        ThinkingBlock,
        ToolUseBlock,
    )

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    logger.warning("claude-agent-sdk not installed. MessageConverter will not be available.")


class MessageConverter:
    """Converts SDK messages to AgentResponse objects.

    This class encapsulates all message type conversion logic,
    making it easier to test and maintain.

    Attributes:
        _handoff_policy: Optional handoff policy for formatting traces
        _capabilities: Optional capability catalog for tool classification
        _config: Agent configuration for MCP detection
    """

    def __init__(
        self,
        handoff_policy: HandoffPolicy | None,
        capabilities: CapabilityCatalog | None,
        config: Any,
    ):
        """Initialize the message converter.

        Args:
            handoff_policy: Optional handoff policy
            capabilities: Optional capability catalog
            config: Agent configuration
        """
        self._handoff_policy = handoff_policy
        self._capabilities = capabilities
        self._config = config

    def convert(self, message: Any) -> AgentResponse | None:
        """Convert SDK message to AgentResponse.

        Args:
            message: SDK message object

        Returns:
            AgentResponse or None if the message type is not relevant
        """
        if isinstance(message, AssistantMessage):
            return self._convert_assistant_message(message)
        elif isinstance(message, StreamEvent):
            return self._convert_stream_event(message)
        elif isinstance(message, TaskStartedMessage):
            return self._convert_task_started(message)
        elif isinstance(message, TaskProgressMessage):
            return self._convert_task_progress(message)
        elif isinstance(message, TaskNotificationMessage):
            return self._convert_task_notification(message)
        elif isinstance(message, SystemMessage):
            return self._convert_system_message(message)
        elif isinstance(message, ResultMessage):
            return self._convert_result_message(message)
        elif isinstance(message, RateLimitEvent):
            return self._convert_rate_limit_event(message)
        return None

    def _convert_system_message(self, message: "SystemMessage") -> AgentResponse | None:
        """Convert generic SystemMessage into user-visible progress when useful."""
        if message.subtype == "compact_boundary":
            compact_metadata = message.data.get("compact_metadata", {}) if isinstance(message.data, dict) else {}
            pre_tokens = compact_metadata.get("pre_tokens")
            post_tokens = compact_metadata.get("post_tokens")
            trigger = compact_metadata.get("trigger")
            text = format_compact_event(
                pre_tokens=pre_tokens if isinstance(pre_tokens, int) else None,
                post_tokens=post_tokens if isinstance(post_tokens, int) else None,
                trigger=trigger if isinstance(trigger, str) else None,
            )
            return AgentResponse(
                content="",
                progress_texts=[text],
                raw_message=message,
                event_type="system",
                event_data={
                    "subtype": "compact_boundary",
                    "compact_metadata": compact_metadata,
                },
            )

        # Keep other system events silent unless we have an explicit mapping.
        return None

    def _convert_assistant_message(self, message: "AssistantMessage") -> AgentResponse:
        """Convert AssistantMessage to AgentResponse."""
        text = ""
        progress_texts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for block in message.content:
            if isinstance(block, TextBlock):
                text += block.text
            elif isinstance(block, ThinkingBlock):
                if block.thinking:
                    progress_texts.append(f"Thinking: {block.thinking}")
            elif isinstance(block, ToolUseBlock):
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                        "kind": self._classify_tool_name(block.name),
                    }
                )

        event_type = ""
        event_data: dict[str, Any] | None = None
        if progress_texts and not text and not tool_calls:
            event_type = "thinking"
            event_data = {"thinking_chunks": len(progress_texts)}
        elif tool_calls:
            event_type = "tool_call"
            event_data = {"tool_calls": len(tool_calls)}
        elif text:
            event_type = "content"

        return AgentResponse(
            content=text,
            progress_texts=progress_texts,
            tool_calls=tool_calls if tool_calls else None,
            finish_reason="tool_use" if tool_calls else "stop",
            raw_message=message,
            event_type=event_type,
            event_data=event_data,
        )

    def _convert_stream_event(self, message: "StreamEvent") -> AgentResponse | None:
        """Convert StreamEvent to AgentResponse."""
        event = message.event or {}
        if event.get("type") != "content_block_delta":
            return None
        delta = event.get("delta", {})
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            text = delta.get("text", "")
            if not text:
                return None
            return AgentResponse(
                content="",
                is_delta=True,
                delta_content=text,
                raw_message=message,
                event_type="content_delta",
            )
        if delta_type == "thinking_delta":
            thinking = delta.get("thinking", "") or delta.get("text", "")
            if thinking:
                return AgentResponse(
                    content="",
                    progress_texts=[f"Thinking: {thinking}"],
                    raw_message=message,
                    event_type="thinking",
                )
        return None

    def _convert_task_started(self, message: "TaskStartedMessage") -> AgentResponse:
        """Convert TaskStartedMessage to AgentResponse."""
        progress_texts = [f"Running: {message.description}"] if message.description else []
        if self._handoff_policy:
            if handoff_trace := self._handoff_policy.format_task_trace(
                message.description, message.task_type
            ):
                progress_texts.append(handoff_trace)
        return AgentResponse(
            content="",
            progress_texts=progress_texts,
            raw_message=message,
            event_type="task",
            event_data={
                "status": "started",
                "task_id": message.task_id,
                "task_type": message.task_type,
            },
        )

    def _convert_task_progress(self, message: "TaskProgressMessage") -> AgentResponse:
        """Convert TaskProgressMessage to AgentResponse."""
        tool_calls = None
        if message.last_tool_name:
            tool_calls = [
                {
                    "name": message.last_tool_name,
                    "input": {},
                    "kind": self._classify_tool_name(message.last_tool_name),
                }
            ]
        return AgentResponse(
            content="",
            progress_texts=[f"Running: {message.description}"] if message.description else [],
            tool_calls=tool_calls,
            finish_reason="tool_use" if tool_calls else "stop",
            raw_message=message,
            event_type="task",
            event_data={
                "status": "progress",
                "task_id": message.task_id,
                "last_tool_name": message.last_tool_name,
            },
        )

    def _convert_task_notification(self, message: "TaskNotificationMessage") -> AgentResponse:
        """Convert TaskNotificationMessage to AgentResponse."""
        progress_texts = [
            format_task_notification(
                status=message.status,
                summary=message.summary,
                task_id=message.task_id,
                output_file=message.output_file,
            )
        ]
        if self._handoff_policy:
            if handoff_trace := self._handoff_policy.format_task_trace(
                str(message.summary or message.status)
            ):
                progress_texts.append(handoff_trace)
        return AgentResponse(
            content="",
            progress_texts=progress_texts,
            raw_message=message,
            event_type="task",
            event_data={
                "status": message.status,
                "task_id": message.task_id,
                "output_file": message.output_file,
            },
        )

    def _convert_result_message(self, message: "ResultMessage") -> AgentResponse:
        """Convert ResultMessage to AgentResponse."""
        usage = None
        if hasattr(message, "usage") and message.usage:
            if isinstance(message.usage, dict):
                usage = {
                    "input_tokens": int(message.usage.get("input_tokens", 0) or 0),
                    "output_tokens": int(message.usage.get("output_tokens", 0) or 0),
                }
            else:
                usage = {
                    "input_tokens": int(getattr(message.usage, "input_tokens", 0) or 0),
                    "output_tokens": int(getattr(message.usage, "output_tokens", 0) or 0),
                }
        content = message.result if isinstance(message.result, str) else ""
        return AgentResponse(
            content=content,
            finish_reason="stop",
            usage=usage,
            raw_message=message,
            event_type="result",
            event_data={
                "stop_reason": message.stop_reason,
                "num_turns": message.num_turns,
                "total_cost_usd": message.total_cost_usd,
            },
        )

    def _convert_rate_limit_event(self, message: "RateLimitEvent") -> AgentResponse:
        """Convert RateLimitEvent to AgentResponse."""
        return AgentResponse(
            content="",
            progress_texts=[format_rate_limit_event(message.rate_limit_info)],
            raw_message=message,
            event_type="rate_limit",
            event_data={
                "status": getattr(message.rate_limit_info, "status", None),
                "rate_limit_type": getattr(message.rate_limit_info, "rate_limit_type", None),
                "resets_at": getattr(message.rate_limit_info, "resets_at", None),
                "utilization": getattr(message.rate_limit_info, "utilization", None),
            },
        )

    def _classify_tool_name(self, name: str) -> str:
        """Classify a tool name into its kind (tool, skill, mcp).

        Args:
            name: Tool name to classify

        Returns:
            One of "tool", "skill", or "mcp"
        """
        normalized = canonical_tool_name(name)
        has_external_mcp = bool(
            getattr(getattr(self._config, "tools", None), "mcp_servers", None)
        ) if self._config else False
        if self._capabilities:
            kind = self._capabilities.classify_tool_name(
                normalized, assume_unknown_mcp=has_external_mcp
            )
            if kind != "tool" or normalized in self._capabilities.builtin_tool_names():
                return kind
        return "mcp" if normalized.startswith("mcp_") else "tool"
