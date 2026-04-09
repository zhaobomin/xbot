"""Message tool for sending messages to users."""

from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from xbot.platform.bus.events import OutboundMessage
from xbot.tools.base import Tool


class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
    ):
        self._send_callback = send_callback
        self._default_channel = default_channel
        self._default_chat_id = default_chat_id
        self._default_message_id = default_message_id
        self._sent_in_turn: bool = False
        self._contexts: dict[str, tuple[str, str, str | None]] = {
            "_global": (default_channel, default_chat_id, default_message_id),
        }
        self._active_session_key: ContextVar[str] = ContextVar(
            "message_tool_active_session",
            default="_global",
        )

    def set_context(
        self,
        channel: str,
        chat_id: str,
        message_id: str | None = None,
        session_key: str | None = None,
    ) -> None:
        """Set the current message context."""
        key = session_key or "_global"
        self._contexts[key] = (channel, chat_id, message_id)
        if key == "_global":
            self._default_channel = channel
            self._default_chat_id = chat_id
            self._default_message_id = message_id

    def set_active_session(self, session_key: str | None) -> None:
        """Select the task-local session context for subsequent execute calls."""
        self._active_session_key.set(session_key or "_global")

    def clear_context(self, session_key: str) -> None:
        """Remove per-session context to prevent unbounded growth."""
        self._contexts.pop(session_key, None)

    def _resolve_context(self) -> tuple[str, str, str | None]:
        key = self._active_session_key.get()
        return self._contexts.get(
            key,
            self._contexts.get(
                "_global",
                (self._default_channel, self._default_chat_id, self._default_message_id),
            ),
        )

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    def start_turn(self) -> None:
        """Reset per-turn send tracking."""
        self._sent_in_turn = False

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return "Send a message to the user. Use this when you want to communicate something."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The message content to send"
                },
                "channel": {
                    "type": "string",
                    "description": "Optional: target channel (telegram, discord, etc.)"
                },
                "chat_id": {
                    "type": "string",
                    "description": "Optional: target chat/user ID"
                },
                "media": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: list of file paths to attach (images, audio, documents)"
                }
            },
            "required": ["content"]
        }

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        media: list[str] | None = None,
        **kwargs: Any
    ) -> str:
        default_channel, default_chat_id, default_message_id = self._resolve_context()
        channel = channel or default_channel
        chat_id = chat_id or default_chat_id
        message_id = message_id or default_message_id

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=media or [],
            metadata={
                "message_id": message_id,
            },
        )

        try:
            await self._send_callback(msg)
            if channel == default_channel and chat_id == default_chat_id:
                self._sent_in_turn = True
            media_info = f" with {len(media)} attachments" if media else ""
            return f"Message sent to {channel}:{chat_id}{media_info}"
        except Exception as e:
            return f"Error sending message: {str(e)}"
