"""Event types for the message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

IM_CHANNELS = {
    "dingtalk",
    "discord",
    "feishu",
    "matrix",
    "mochat",
    "qq",
    "slack",
    "telegram",
    "wecom",
    "whatsapp",
}


def to_canonical_session_key(channel: str, chat_id: str, override: str | None = None) -> str:
    """Return the shared session namespace for an inbound channel message."""
    normalized_channel = (channel or "").strip()
    normalized_chat_id = str(chat_id)
    normalized_override = (override or "").strip()

    if normalized_override:
        if normalized_override.startswith("im:"):
            return normalized_override
        if normalized_channel in IM_CHANNELS and normalized_override.startswith(f"{normalized_channel}:"):
            return f"im:{normalized_override}"
        return normalized_override

    if normalized_channel in IM_CHANNELS:
        return f"im:{normalized_channel}:{normalized_chat_id}"
    return f"{normalized_channel}:{normalized_chat_id}"


@dataclass
class InboundMessage:
    """Message received from a chat channel."""

    channel: str  # telegram, discord, slack, whatsapp
    sender_id: str  # User identifier
    chat_id: str  # Chat/channel identifier
    content: str  # Message text
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    session_key_override: str | None = None  # Optional override for thread-scoped sessions

    @property
    def session_key(self) -> str:
        """Unique key for session identification."""
        return to_canonical_session_key(self.channel, self.chat_id, self.session_key_override)


@dataclass
class OutboundMessage:
    """Message to send to a chat channel."""

    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

