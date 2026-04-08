"""Discord channel implementation using Discord Gateway websocket."""

import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import Any, Literal

import httpx
import websockets
from pydantic import Field

from xbot.platform.bus.events import OutboundMessage
from xbot.platform.bus.queue import MessageBus
from xbot.channels.base import BaseChannel
from xbot.platform.config.paths import get_media_dir
from xbot.platform.config.schema import Base
from xbot.platform.logging.core import get_logger
from xbot.platform.utils.helpers import split_message

logger = get_logger(__name__)
DISCORD_API_BASE = "https://discord.com/api/v10"
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20MB
MAX_MESSAGE_LEN = 2000  # Discord message character limit
DEDUP_TTL_SECONDS = 300  # 5 minutes TTL for message deduplication
MAX_RECONNECT_ATTEMPTS = 10  # Maximum reconnect attempts before circuit breaker
CIRCUIT_BREAKER_TIMEOUT = 300  # 5 minutes cooldown after max reconnect attempts


class DiscordConfig(Base):
    """Discord channel configuration."""

    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377
    group_policy: Literal["mention", "open"] = "mention"


class DiscordChannel(BaseChannel):
    """Discord channel using Gateway websocket."""

    name = "discord"
    display_name = "Discord"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return DiscordConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = DiscordConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: DiscordConfig = config
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._seq: int | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._http: httpx.AsyncClient | None = None
        self._bot_user_id: str | None = None
        # Message deduplication
        self._processed_message_ids: dict[str, float] = {}
        self._dedup_lock = asyncio.Lock()
        self._heartbeat_failed = asyncio.Event()  # Signal heartbeat failure
        self._reconnect_attempts = 0

    async def start(self) -> None:
        """Start the Discord gateway connection."""
        if not self.config.token:
            logger.error("Discord bot token not configured")
            return

        self._running = True
        self._http = httpx.AsyncClient(timeout=30.0)

        max_reconnect_delay = 60  # seconds
        initial_reconnect_delay = 1

        while self._running:
            try:
                logger.info("Connecting to Discord gateway...")
                async with websockets.connect(self.config.gateway_url) as ws:
                    self._ws = ws
                    self._reconnect_attempts = 0  # Reset on successful connection
                    await self._gateway_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Discord gateway error: %s", e)
                if not self._running:
                    break

                # Circuit breaker: if max reconnect attempts reached, wait longer
                if self._reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                    logger.error(
                        "Discord: max reconnect attempts (%d) reached, entering circuit breaker for %ds",
                        MAX_RECONNECT_ATTEMPTS,
                        CIRCUIT_BREAKER_TIMEOUT,
                    )
                    await asyncio.sleep(CIRCUIT_BREAKER_TIMEOUT)
                    self._reconnect_attempts = 0  # Reset after circuit breaker
                    continue

                # Exponential backoff: 1, 2, 4, 8, 16, 32, 60, 60, ...
                delay = min(initial_reconnect_delay * (2 ** self._reconnect_attempts), max_reconnect_delay)
                self._reconnect_attempts += 1
                logger.info("Reconnecting to Discord gateway in %ds (attempt %d/%d)...", delay, self._reconnect_attempts, MAX_RECONNECT_ATTEMPTS)
                await asyncio.sleep(delay)

    async def stop(self) -> None:
        """Stop the Discord channel."""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        typing_tasks = list(self._typing_tasks.values())
        for task in typing_tasks:
            task.cancel()
        if typing_tasks:
            await asyncio.gather(*typing_tasks, return_exceptions=True)
        self._typing_tasks.clear()
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._http:
            await self._http.aclose()
            self._http = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Discord REST API, including file attachments."""
        if not self._http:
            logger.warning("Discord HTTP client not initialized")
            return

        url = f"{DISCORD_API_BASE}/channels/{msg.chat_id}/messages"
        headers = {"Authorization": f"Bot {self.config.token}"}

        try:
            sent_media = False
            failed_media: list[str] = []

            # Send file attachments first
            for media_path in msg.media or []:
                if await self._send_file(url, headers, media_path, reply_to=msg.reply_to):
                    sent_media = True
                else:
                    failed_media.append(Path(media_path).name)

            # Send text content
            chunks = split_message(msg.content or "", MAX_MESSAGE_LEN)
            if not chunks and failed_media and not sent_media:
                chunks = split_message(
                    "\n".join(f"[attachment: {name} - send failed]" for name in failed_media),
                    MAX_MESSAGE_LEN,
                )
            if not chunks:
                return

            for i, chunk in enumerate(chunks):
                payload: dict[str, Any] = {"content": chunk}

                # Let the first successful attachment carry the reply if present.
                if i == 0 and msg.reply_to and not sent_media:
                    payload["message_reference"] = {"message_id": msg.reply_to}
                    payload["allowed_mentions"] = {"replied_user": False}

                if not await self._send_payload(url, headers, payload):
                    break  # Abort remaining chunks on failure
        finally:
            await self._stop_typing(msg.chat_id)

    async def _send_payload(
        self, url: str, headers: dict[str, str], payload: dict[str, Any]
    ) -> bool:
        """Send a single Discord API payload with retry on rate-limit. Returns True on success."""
        for attempt in range(3):
            try:
                response = await self._http.post(url, headers=headers, json=payload)
                if response.status_code == 429:
                    data = response.json()
                    retry_after = float(data.get("retry_after", 1.0))
                    logger.warning("Discord rate limited, retrying in %ss", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                response.raise_for_status()
                return True
            except Exception:
                if attempt == 2:
                    logger.exception("Error sending Discord message")
                else:
                    await asyncio.sleep(1)
        return False

    async def _send_file(
        self,
        url: str,
        headers: dict[str, str],
        file_path: str,
        reply_to: str | None = None,
    ) -> bool:
        """Send a file attachment via Discord REST API using multipart/form-data."""
        path = Path(file_path)
        if not path.is_file():
            logger.warning("Discord file not found, skipping: %s", file_path)
            return False

        if path.stat().st_size > MAX_ATTACHMENT_BYTES:
            logger.warning("Discord file too large (>20MB), skipping: %s", path.name)
            return False

        payload_json: dict[str, Any] = {}
        if reply_to:
            payload_json["message_reference"] = {"message_id": reply_to}
            payload_json["allowed_mentions"] = {"replied_user": False}

        for attempt in range(3):
            try:
                with open(path, "rb") as f:
                    files = {"files[0]": (path.name, f, "application/octet-stream")}
                    data: dict[str, Any] = {}
                    if payload_json:
                        data["payload_json"] = json.dumps(payload_json)
                    response = await self._http.post(
                        url, headers=headers, files=files, data=data
                    )
                if response.status_code == 429:
                    resp_data = response.json()
                    retry_after = float(resp_data.get("retry_after", 1.0))
                    logger.warning("Discord rate limited, retrying in %ss", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                response.raise_for_status()
                logger.info("Discord file sent: %s", path.name)
                return True
            except Exception as e:
                if attempt == 2:
                    logger.error("Error sending Discord file %s: %s", path.name, e)
                else:
                    await asyncio.sleep(1)
        return False

    async def _gateway_loop(self) -> None:
        """Main gateway loop: identify, heartbeat, dispatch events."""
        if not self._ws:
            return

        async for raw in self._ws:
            # Check for heartbeat failure
            if self._heartbeat_failed.is_set():
                logger.warning("Discord heartbeat failed, triggering reconnect")
                self._heartbeat_failed.clear()
                break

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from Discord gateway: %s", raw[:100])
                continue

            op = data.get("op")
            event_type = data.get("t")
            seq = data.get("s")
            payload = data.get("d")

            if seq is not None:
                self._seq = seq

            if op == 10:
                # HELLO: start heartbeat and identify
                interval_ms = payload.get("heartbeat_interval", 45000)
                await self._start_heartbeat(interval_ms / 1000)
                await self._identify()
            elif op == 0 and event_type == "READY":
                logger.info("Discord gateway READY")
                # Capture bot user ID for mention detection
                user_data = payload.get("user") or {}
                self._bot_user_id = user_data.get("id")
                logger.info("Discord bot connected as user %s", self._bot_user_id)
            elif op == 0 and event_type == "MESSAGE_CREATE":
                await self._handle_message_create(payload)
            elif op == 7:
                # RECONNECT: exit loop to reconnect
                logger.info("Discord gateway requested reconnect")
                break
            elif op == 9:
                # INVALID_SESSION: reconnect
                logger.warning("Discord gateway invalid session")
                break

    async def _identify(self) -> None:
        """Send IDENTIFY payload."""
        if not self._ws:
            return

        identify = {
            "op": 2,
            "d": {
                "token": self.config.token,
                "intents": self.config.intents,
                "properties": {
                    "os": "xbot",
                    "browser": "xbot",
                    "device": "xbot",
                },
            },
        }
        await self._ws.send(json.dumps(identify))

    async def _start_heartbeat(self, interval_s: float) -> None:
        """Start or restart the heartbeat loop."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task

        async def heartbeat_loop() -> None:
            while self._running and self._ws:
                payload = {"op": 1, "d": self._seq}
                try:
                    await self._ws.send(json.dumps(payload))
                except Exception as e:
                    logger.warning("Discord heartbeat failed: %s", e)
                    self._heartbeat_failed.set()  # Signal main loop
                    break
                await asyncio.sleep(interval_s)

        self._heartbeat_task = self._create_tracked_task(heartbeat_loop(), name="discord-heartbeat")

    async def _is_duplicate_message(self, message_id: str) -> bool:
        """Check if message was already processed, with TTL cleanup."""
        async with self._dedup_lock:
            now = time.time()
            # Cleanup expired entries
            expired = [k for k, v in self._processed_message_ids.items()
                       if now - v > DEDUP_TTL_SECONDS]
            for k in expired:
                del self._processed_message_ids[k]

            # Check if duplicate
            if message_id in self._processed_message_ids:
                return True
            self._processed_message_ids[message_id] = now
            return False

    async def _handle_message_create(self, payload: dict[str, Any]) -> None:
        """Handle incoming Discord messages."""
        author = payload.get("author") or {}
        if author.get("bot"):
            return

        sender_id = str(author.get("id", ""))
        channel_id = str(payload.get("channel_id", ""))
        content = payload.get("content") or ""
        guild_id = payload.get("guild_id")
        message_id = str(payload.get("id", ""))

        if not sender_id or not channel_id:
            return

        # Check for duplicate message
        if message_id and await self._is_duplicate_message(message_id):
            logger.debug("Discord: skipping duplicate message %s", message_id)
            return

        if not self.is_allowed(sender_id):
            return

        # Check group channel policy (DMs always respond if is_allowed passes)
        if guild_id is not None:
            if not self._should_respond_in_group(payload, content):
                return

        content_parts = [content] if content else []
        media_paths: list[str] = []
        media_dir = get_media_dir("discord")

        for attachment in payload.get("attachments") or []:
            url = attachment.get("url")
            filename = attachment.get("filename") or "attachment"
            size = attachment.get("size") or 0
            if not url or not self._http:
                continue
            if size and size > MAX_ATTACHMENT_BYTES:
                content_parts.append(f"[attachment: {filename} - too large]")
                continue
            try:
                media_dir.mkdir(parents=True, exist_ok=True)
                file_path = media_dir / f"{attachment.get('id', 'file')}_{filename.replace('/', '_')}"
                resp = await self._http.get(url)
                resp.raise_for_status()
                file_path.write_bytes(resp.content)
                media_paths.append(str(file_path))
                content_parts.append(f"[attachment: {file_path}]")
            except Exception as e:
                logger.warning("Failed to download Discord attachment: %s", e)
                content_parts.append(f"[attachment: {filename} - download failed]")

        reply_to = (payload.get("referenced_message") or {}).get("id")

        await self._start_typing(channel_id)

        await self._handle_message(
            sender_id=sender_id,
            chat_id=channel_id,
            content="\n".join(p for p in content_parts if p) or "[empty message]",
            media=media_paths,
            metadata={
                "message_id": str(payload.get("id", "")),
                "guild_id": guild_id,
                "reply_to": reply_to,
            },
        )

    def _should_respond_in_group(self, payload: dict[str, Any], content: str) -> bool:
        """Check if bot should respond in a group channel based on policy."""
        if self.config.group_policy == "open":
            return True

        if self.config.group_policy == "mention":
            # Check if bot was mentioned in the message
            if self._bot_user_id:
                # Check mentions array
                mentions = payload.get("mentions") or []
                for mention in mentions:
                    if str(mention.get("id")) == self._bot_user_id:
                        return True
                # Also check content for mention format <@USER_ID>
                if f"<@{self._bot_user_id}>" in content or f"<@!{self._bot_user_id}>" in content:
                    return True
            logger.debug("Discord message in %s ignored (bot not mentioned)", payload.get("channel_id"))
            return False

        return True

    async def _start_typing(self, channel_id: str) -> None:
        """Start periodic typing indicator for a channel."""
        await self._stop_typing(channel_id)

        async def typing_loop() -> None:
            url = f"{DISCORD_API_BASE}/channels/{channel_id}/typing"
            headers = {"Authorization": f"Bot {self.config.token}"}
            while self._running:
                try:
                    if self._http is None:
                        return
                    await self._http.post(url, headers=headers)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.debug("Discord typing indicator failed for %s: %s", channel_id, e)
                    return
                await asyncio.sleep(8)

        task = self._create_tracked_task(typing_loop(), name=f"discord-typing:{channel_id}")

        def _cleanup(done_task: asyncio.Task) -> None:
            if self._typing_tasks.get(channel_id) is done_task:
                self._typing_tasks.pop(channel_id, None)

        task.add_done_callback(_cleanup)
        self._typing_tasks[channel_id] = task

    async def _stop_typing(self, channel_id: str) -> None:
        """Stop typing indicator for a channel."""
        task = self._typing_tasks.pop(channel_id, None)
        if task:
            task.cancel()
