"""Hook handlers for Claude SDK.

This module provides hook implementations for the Claude Agent SDK,
including context compaction notifications.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from claude_agent_sdk import PreCompactHookInput, HookContext


@dataclass
class CompactEvent:
    """Information about a context compaction event."""

    session_key: str
    trigger: str  # "auto" | "token_limit"
    messages_count: int
    tokens_before: int
    timestamp: datetime

    # Filled after compaction
    tokens_after: int | None = None
    summary: str | None = None


class CompactHookHandler:
    """Handler for PreCompact hook events.

    When the SDK decides to compact the context, this hook is triggered.
    It logs the event and returns a notification message that will be
    displayed to the user via progress_texts.

    Usage:
        handler = CompactHookHandler()
        hooks = {
            "PreCompact": [{"hooks": [handler]}]
        }
    """

    def __init__(self, enabled: bool = True):
        """Initialize the compact hook handler.

        Args:
            enabled: Whether to send notifications (default True)
        """
        self.enabled = enabled
        self._recent_events: list[CompactEvent] = []

    async def __call__(
        self,
        input: "PreCompactHookInput",
        output: str | None,
        context: "HookContext",
    ) -> str | None:
        """Handle PreCompact hook event.

        Args:
            input: Hook input with compaction details
            output: Current output (usually None for PreCompact)
            context: Hook context with session info

        Returns:
            Notification message to display to user, or None if disabled
        """
        if not self.enabled:
            return None

        # Extract information from hook input
        session_key = getattr(context, "session_id", "unknown")
        messages = getattr(input, "messages", [])
        token_count = getattr(input, "token_count", 0)
        trigger = getattr(input, "trigger", "auto")

        event = CompactEvent(
            session_key=str(session_key),
            trigger=str(trigger),
            messages_count=len(messages) if messages else 0,
            tokens_before=token_count or 0,
            timestamp=datetime.now(),
        )

        # Keep recent events for debugging
        self._recent_events.append(event)
        if len(self._recent_events) > 50:
            self._recent_events = self._recent_events[-50:]

        # Log the event
        logger.info(
            "Context compaction triggered: session={}, messages={}, tokens={}",
            event.session_key,
            event.messages_count,
            event.tokens_before,
        )

        # Return notification message
        if event.tokens_before > 0:
            return (
                f"🔄 Compressing context "
                f"({event.messages_count} messages, ~{event.tokens_before:,} tokens)..."
            )
        else:
            return f"🔄 Compressing context ({event.messages_count} messages)..."

    def get_recent_events(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent compaction events for debugging.

        Args:
            limit: Maximum number of events to return

        Returns:
            List of event dictionaries
        """
        return [
            {
                "session_key": e.session_key,
                "trigger": e.trigger,
                "messages_count": e.messages_count,
                "tokens_before": e.tokens_before,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in self._recent_events[-limit:]
        ]


def build_compact_hook(enabled: bool = True) -> dict[str, list]:
    """Build the PreCompact hook configuration.

    Args:
        enabled: Whether to enable compaction notifications

    Returns:
        Hook configuration dict for ClaudeAgentOptions.hooks
    """
    if not enabled:
        return {}

    handler = CompactHookHandler(enabled=True)
    return {
        "PreCompact": [{"hooks": [handler]}]
    }