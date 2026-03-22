"""Hook handlers for Claude SDK.

This module provides hook implementations for the Claude Agent SDK,
including context compaction notifications.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

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
    It sends a notification to the user via the provided callback.

    Note: The systemMessage returned by this hook is only displayed in the CLI
    and does not appear in the SDK message stream. To notify users on external
    channels (Telegram, Feishu, etc.), we use the message_callback.

    Usage:
        handler = CompactHookHandler(message_callback=my_callback)
        hooks = {
            "PreCompact": [{"hooks": [handler]}]
        }
    """

    def __init__(
        self,
        enabled: bool = True,
        message_callback: Callable[[str, str], None] | None = None,
    ):
        """Initialize the compact hook handler.

        Args:
            enabled: Whether to send notifications (default True)
            message_callback: Optional async callback(session_id, message) to send
                             notification to the user's channel. The callback receives
                             the session_id and the notification message.
        """
        self.enabled = enabled
        self.message_callback = message_callback
        self._recent_events: list[CompactEvent] = []

    async def __call__(
        self,
        input: "PreCompactHookInput",
        output: str | None,
        context: "HookContext",
    ) -> dict[str, str] | None:
        """Handle PreCompact hook event.

        Args:
            input: Hook input with compaction details
            output: Current output (usually None for PreCompact)
            context: Hook context with session info

        Returns:
            Hook output dict with systemMessage for CLI display, or None if disabled
        """
        if not self.enabled:
            return None

        # Extract information from hook input
        session_key = getattr(context, "session_id", "unknown")
        trigger = getattr(input, "trigger", "auto")

        event = CompactEvent(
            session_key=str(session_key),
            trigger=str(trigger),
            messages_count=0,  # PreCompact doesn't have messages count
            tokens_before=0,   # PreCompact doesn't have token count yet
            timestamp=datetime.now(),
        )

        # Keep recent events for debugging
        self._recent_events.append(event)
        if len(self._recent_events) > 50:
            self._recent_events = self._recent_events[-50:]

        # Log the event
        logger.info(
            "Context compaction triggered: session={}, trigger={}",
            event.session_key,
            event.trigger,
        )

        # Build notification message
        trigger_text = f" ({trigger})" if trigger else ""
        notification_msg = f"🔄 Compressing context{trigger_text}..."

        # Send notification to user's channel via callback
        if self.message_callback:
            try:
                self.message_callback(str(session_key), notification_msg)
                logger.debug(f"Sent compact notification for session {session_key}")
            except Exception as e:
                logger.warning(f"Failed to send compact notification: {e}")

        # Return notification message as systemMessage for CLI
        return {
            "systemMessage": notification_msg
        }

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