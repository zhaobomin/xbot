"""WhatsApp channel implementation using Node.js bridge."""

import asyncio
import json
import mimetypes
import time
from collections import OrderedDict
from typing import Any

from pydantic import Field

from xbot.channels.base import BaseChannel
from xbot.platform.bus.events import OutboundMessage
from xbot.platform.bus.queue import MessageBus
from xbot.platform.config.schema import Base
from xbot.platform.logging.core import get_logger

logger = get_logger(__name__)
class WhatsAppConfig(Base):
    """WhatsApp channel configuration."""

    enabled: bool = False
    bridge_url: str = "ws://localhost:3001"
    bridge_token: str = ""
    allow_from: list[str] = Field(default_factory=list)


class WhatsAppChannel(BaseChannel):
    """
    WhatsApp channel that connects to a Node.js bridge.

    The bridge uses @whiskeysockets/baileys to handle the WhatsApp Web protocol.
    Communication between Python and Node.js is via WebSocket.
    """

    name = "whatsapp"
    display_name = "WhatsApp"
    _DEDUP_MAX_IDS = 1000
    _DEDUP_TTL_SECONDS = 6 * 60 * 60

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return WhatsAppConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = WhatsAppConfig.model_validate(config)
        super().__init__(config, bus)
        self._ws = None
        self._connected = False
        self._processed_message_ids: OrderedDict[str, float] = OrderedDict()

    def _cleanup_processed_message_ids(self, now: float | None = None) -> None:
        """Expire old message IDs and enforce bounded cache size."""
        now_ts = now if now is not None else time.time()
        cutoff = now_ts - self._DEDUP_TTL_SECONDS

        # OrderedDict preserves insertion order; remove stale entries from head.
        while self._processed_message_ids:
            _, ts = next(iter(self._processed_message_ids.items()))
            if ts >= cutoff:
                break
            self._processed_message_ids.popitem(last=False)

        while len(self._processed_message_ids) > self._DEDUP_MAX_IDS:
            self._processed_message_ids.popitem(last=False)

    def _is_duplicate_message_id(self, message_id: str, now: float | None = None) -> bool:
        """Return True if message_id was seen recently; otherwise record it."""
        now_ts = now if now is not None else time.time()
        self._cleanup_processed_message_ids(now_ts)
        seen_at = self._processed_message_ids.get(message_id)
        if seen_at is not None and now_ts - seen_at <= self._DEDUP_TTL_SECONDS:
            return True
        self._processed_message_ids[message_id] = now_ts
        self._cleanup_processed_message_ids(now_ts)
        return False

    async def start(self) -> None:
        """Start the WhatsApp channel by connecting to the bridge."""
        import websockets

        bridge_url = self.config.bridge_url

        logger.info("Connecting to WhatsApp bridge at %s...", bridge_url)

        self._running = True

        while self._running:
            try:
                async with websockets.connect(bridge_url) as ws:
                    self._ws = ws
                    # Send auth token if configured
                    if self.config.bridge_token:
                        await ws.send(json.dumps({"type": "auth", "token": self.config.bridge_token}))
                    self._connected = True
                    logger.info("Connected to WhatsApp bridge")

                    # Listen for messages
                    async for message in ws:
                        try:
                            await self._handle_bridge_message(message)
                        except Exception as e:
                            logger.error("Error handling bridge message: %s", e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                self._ws = None
                logger.warning("WhatsApp bridge connection error: %s", e)

                if self._running:
                    logger.info("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the WhatsApp channel."""
        self._running = False
        self._connected = False

        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through WhatsApp."""
        if not self._ws or not self._connected:
            logger.warning("WhatsApp bridge not connected")
            return

        try:
            payload = {
                "type": "send",
                "to": msg.chat_id,
                "text": msg.content,
                "media": list(msg.media or []),
            }
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.error("Error sending WhatsApp message: %s", e)

    async def _handle_bridge_message(self, raw: str) -> None:
        """Handle a message from the bridge."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from bridge: %s", raw[:100])
            return

        msg_type = data.get("type")

        if msg_type == "message":
            # Incoming message from WhatsApp
            # Deprecated by whatsapp: old phone number style typically: <phone>@s.whatspp.net
            pn = data.get("pn", "")
            # New LID sytle typically:
            sender = data.get("sender", "")
            content = data.get("content", "")
            message_id = data.get("id", "")

            if message_id:
                if self._is_duplicate_message_id(message_id):
                    return

            # Extract just the phone number or lid as chat_id
            user_id = pn if pn else sender
            sender_id = user_id.split("@")[0] if "@" in user_id else user_id
            logger.info("Sender %s", sender)

            # Handle voice transcription if it's a voice message
            if content == "[Voice Message]":
                logger.info("Voice message received from %s, but direct download from bridge is not yet supported.", sender_id)
                content = "[Voice Message: Transcription not available for WhatsApp yet]"

            # Extract media paths (images/documents/videos downloaded by the bridge)
            media_paths = data.get("media") or []

            # Build content tags matching Telegram's pattern: [image: /path] or [file: /path]
            if media_paths:
                for p in media_paths:
                    mime, _ = mimetypes.guess_type(p)
                    media_type = "image" if mime and mime.startswith("image/") else "file"
                    media_tag = f"[{media_type}: {p}]"
                    content = f"{content}\n{media_tag}" if content else media_tag

            await self._handle_message(
                sender_id=sender_id,
                chat_id=sender,  # Use full LID for replies
                content=content,
                media=media_paths,
                metadata={
                    "message_id": message_id,
                    "timestamp": data.get("timestamp"),
                    "is_group": data.get("isGroup", False)
                }
            )

        elif msg_type == "status":
            # Connection status update
            status = data.get("status")
            logger.info("WhatsApp status: %s", status)

            if status == "connected":
                self._connected = True
            elif status == "disconnected":
                self._connected = False

        elif msg_type == "qr":
            # QR code for authentication
            logger.info("Scan QR code in the bridge terminal to connect WhatsApp")

        elif msg_type == "error":
            logger.error("WhatsApp bridge error: %s", data.get('error'))
