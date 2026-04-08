"""Slack channel implementation using Socket Mode."""

import asyncio
import re
from typing import Any

from pydantic import Field
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.socket_mode.websockets import SocketModeClient
from slack_sdk.web.async_client import AsyncWebClient
from slackify_markdown import slackify_markdown

from xbot.platform.bus.events import OutboundMessage
from xbot.platform.bus.queue import MessageBus
from xbot.channels.base import BaseChannel
from xbot.platform.config.schema import Base
from xbot.platform.logging.core import get_logger

# Slack-specific retry configuration
logger = get_logger(__name__)
SLACK_MAX_RETRIES = 3
SLACK_RETRY_DELAYS = [1, 2, 4]  # Exponential backoff in seconds


class SlackDMConfig(Base):
    """Slack DM policy configuration."""

    enabled: bool = True
    policy: str = "open"
    allow_from: list[str] = Field(default_factory=list)


class SlackConfig(Base):
    """Slack channel configuration."""

    enabled: bool = False
    mode: str = "socket"
    webhook_path: str = "/slack/events"
    bot_token: str = ""
    app_token: str = ""
    user_token_read_only: bool = True
    reply_in_thread: bool = True
    react_emoji: str = "eyes"
    done_emoji: str = "white_check_mark"
    allow_from: list[str] = Field(default_factory=list)
    group_policy: str = "mention"
    group_allow_from: list[str] = Field(default_factory=list)
    dm: SlackDMConfig = Field(default_factory=SlackDMConfig)


class SlackChannel(BaseChannel):
    """Slack channel using Socket Mode."""

    name = "slack"
    display_name = "Slack"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return SlackConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = SlackConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: SlackConfig = config
        self._web_client: AsyncWebClient | None = None
        self._socket_client: SocketModeClient | None = None
        self._bot_user_id: str | None = None

    def is_allowed(self, sender_id: str) -> bool:
        """Slack uses channel-specific policy checks; top-level allow_from is optional."""
        allow_list = getattr(self.config, "allow_from", [])
        if not allow_list or "*" in allow_list:
            return True
        return str(sender_id) in allow_list

    async def start(self) -> None:
        """Start the Slack Socket Mode client."""
        if not self.config.bot_token or not self.config.app_token:
            logger.error("Slack bot/app token not configured")
            return
        if self.config.mode != "socket":
            logger.error("Unsupported Slack mode: %s", self.config.mode)
            return

        self._running = True

        self._web_client = AsyncWebClient(token=self.config.bot_token)
        self._socket_client = SocketModeClient(
            app_token=self.config.app_token,
            web_client=self._web_client,
        )

        self._socket_client.socket_mode_request_listeners.append(self._on_socket_request)

        # Resolve bot user ID for mention handling
        try:
            auth = await self._web_client.auth_test()
            self._bot_user_id = auth.get("user_id")
            logger.info("Slack bot connected as %s", self._bot_user_id)
        except Exception as e:
            logger.warning("Slack auth_test failed: %s", e)

        logger.info("Starting Slack Socket Mode client...")
        await self._socket_client.connect()

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the Slack client."""
        self._running = False
        if self._socket_client:
            try:
                await self._socket_client.close()
            except Exception as e:
                logger.warning("Slack socket close failed: %s", e)
            self._socket_client = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Slack with retry on transient failures."""
        if not self._web_client:
            logger.warning("Slack client not running")
            return

        slack_meta = msg.metadata.get("slack", {}) if msg.metadata else {}
        thread_ts = slack_meta.get("thread_ts")
        channel_type = slack_meta.get("channel_type")
        # Slack DMs don't use threads; channel/group replies may keep thread_ts.
        thread_ts_param = thread_ts if thread_ts and channel_type != "im" else None

        # Slack rejects empty text payloads. Keep media-only messages media-only,
        # but send a single blank message when the bot has no text or files to send.
        if msg.content or not (msg.media or []):
            await self._send_with_retry(
                self._web_client.chat_postMessage,
                channel=msg.chat_id,
                text=self._to_mrkdwn(msg.content) if msg.content else " ",
                thread_ts=thread_ts_param,
            )

        for media_path in msg.media or []:
            try:
                await self._send_with_retry(
                    self._web_client.files_upload_v2,
                    channel=msg.chat_id,
                    file=media_path,
                    thread_ts=thread_ts_param,
                )
            except Exception as e:
                logger.error("Failed to upload file %s after retries: %s", media_path, e)

        # Update reaction emoji when the final (non-progress) response is sent
        if not (msg.metadata or {}).get("_progress"):
            event = slack_meta.get("event", {})
            await self._update_react_emoji(msg.chat_id, event.get("ts"))

    async def _send_with_retry(self, api_call, **kwargs) -> Any:
        """Send API call with retry logic, handling rate limits."""
        last_error = None
        for attempt in range(SLACK_MAX_RETRIES):
            try:
                return await api_call(**kwargs)
            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # Don't retry on auth errors (401/403)
                if "401" in error_str or "403" in error_str or "invalid_auth" in error_str:
                    logger.error("Slack auth error, not retrying: %s", e)
                    raise

                # Handle rate limit (429) - use Retry-After header if available
                if "429" in error_str or "rate" in error_str:
                    delay = SLACK_RETRY_DELAYS[min(attempt, len(SLACK_RETRY_DELAYS) - 1)]
                    try:
                        resp = getattr(e, "response", None)
                        retry_after_val = None
                        if resp is not None and hasattr(resp, "get"):
                            retry_after_val = resp.get("headers", {}).get("Retry-After")
                        elif resp is not None and hasattr(resp, "headers"):
                            retry_after_val = getattr(resp.headers, "get", lambda _k: None)("Retry-After")
                        if retry_after_val is not None:
                            delay = int(retry_after_val)
                    except (ValueError, TypeError, AttributeError):
                        logger.debug("Could not parse Retry-After header, using default delay %ds", delay)
                    logger.warning("Slack rate limited, waiting %ds before retry", delay)
                    await asyncio.sleep(delay)
                    continue

                # Retry on transient errors
                if attempt < SLACK_MAX_RETRIES - 1:
                    delay = SLACK_RETRY_DELAYS[attempt]
                    logger.warning(
                        "Slack API call failed (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, SLACK_MAX_RETRIES, delay, e
                    )
                    await asyncio.sleep(delay)

        logger.error("Slack API call failed after %d attempts: %s", SLACK_MAX_RETRIES, last_error)
        raise last_error

    async def _on_socket_request(
        self,
        client: SocketModeClient,
        req: SocketModeRequest,
    ) -> None:
        """Handle incoming Socket Mode requests."""
        if req.type != "events_api":
            return

        # Acknowledge right away
        await client.send_socket_mode_response(
            SocketModeResponse(envelope_id=req.envelope_id)
        )

        payload = req.payload or {}
        event = payload.get("event") or {}
        event_type = event.get("type")

        # Handle app mentions or plain messages
        if event_type not in ("message", "app_mention"):
            return

        sender_id = event.get("user")
        chat_id = event.get("channel")

        # Ignore bot/system messages (any subtype = not a normal user message)
        if event.get("subtype"):
            return
        if self._bot_user_id and sender_id == self._bot_user_id:
            return

        # Avoid double-processing: Slack sends both `message` and `app_mention`
        # for mentions in channels. Prefer `app_mention`.
        text = event.get("text") or ""
        if event_type == "message" and self._bot_user_id and f"<@{self._bot_user_id}>" in text:
            return

        # Debug: log basic event shape
        logger.debug(
            "Slack event: type=%s subtype=%s user=%s channel=%s channel_type=%s text=%s",
            event_type,
            event.get("subtype"),
            sender_id,
            chat_id,
            event.get("channel_type"),
            text[:80],
        )
        if not sender_id or not chat_id:
            return

        channel_type = event.get("channel_type") or ""

        if not self._is_allowed(sender_id, chat_id, channel_type):
            return

        if channel_type != "im" and not self._should_respond_in_channel(event_type, text, chat_id):
            return

        text = self._strip_bot_mention(text)

        thread_ts = event.get("thread_ts")
        if self.config.reply_in_thread and not thread_ts:
            thread_ts = event.get("ts")
        # Add :eyes: reaction to the triggering message (best-effort)
        try:
            if self._web_client and event.get("ts"):
                await self._web_client.reactions_add(
                    channel=chat_id,
                    name=self.config.react_emoji,
                    timestamp=event.get("ts"),
                )
        except Exception as e:
            logger.debug("Slack reactions_add failed: %s", e)

        # Thread-scoped session key for channel/group messages
        session_key = f"slack:{chat_id}:{thread_ts}" if thread_ts and channel_type != "im" else None

        try:
            await self._handle_message(
                sender_id=sender_id,
                chat_id=chat_id,
                content=text,
                metadata={
                    "slack": {
                        "event": event,
                        "thread_ts": thread_ts,
                        "channel_type": channel_type,
                    },
                },
                session_key=session_key,
            )
        except Exception:
            logger.exception("Error handling Slack message from %s", sender_id)

    async def _update_react_emoji(self, chat_id: str, ts: str | None) -> None:
        """Remove the in-progress reaction and optionally add a done reaction."""
        if not self._web_client or not ts:
            return

        # Remove in-progress emoji (with retry for race conditions)
        for attempt in range(3):
            try:
                await self._web_client.reactions_remove(
                    channel=chat_id,
                    name=self.config.react_emoji,
                    timestamp=ts,
                )
                break
            except Exception as e:
                error_str = str(e).lower()
                if "no_reaction" in error_str:
                    break  # Already removed, that's fine
                if attempt < 2:
                    await asyncio.sleep(0.1)  # Small delay before retry
                else:
                    logger.debug("Slack reactions_remove failed after retries: %s", e)

        # Add done emoji if configured
        if self.config.done_emoji:
            try:
                await self._web_client.reactions_add(
                    channel=chat_id,
                    name=self.config.done_emoji,
                    timestamp=ts,
                )
            except Exception as e:
                logger.debug("Slack done reaction failed: %s", e)

    def _is_allowed(self, sender_id: str, chat_id: str, channel_type: str) -> bool:
        if channel_type == "im":
            if not self.config.dm.enabled:
                return False
            if self.config.dm.policy == "allowlist":
                return sender_id in self.config.dm.allow_from
            return True

        # Group / channel messages
        if self.config.group_policy == "allowlist":
            return chat_id in self.config.group_allow_from
        return True

    def _should_respond_in_channel(self, event_type: str, text: str, chat_id: str) -> bool:
        if self.config.group_policy == "open":
            return True
        if self.config.group_policy == "mention":
            if event_type == "app_mention":
                return True
            return self._bot_user_id is not None and f"<@{self._bot_user_id}>" in text
        if self.config.group_policy == "allowlist":
            return chat_id in self.config.group_allow_from
        return False

    def _strip_bot_mention(self, text: str) -> str:
        if not text or not self._bot_user_id:
            return text
        return re.sub(rf"<@{re.escape(self._bot_user_id)}>\s*", "", text).strip()

    _TABLE_RE = re.compile(r"(?m)^\|.*\|$(?:\n\|[\s:|-]*\|$)(?:\n\|.*\|$)*")
    _CODE_FENCE_RE = re.compile(r"```[\s\S]*?```")
    _INLINE_CODE_RE = re.compile(r"`[^`]+`")
    _LEFTOVER_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
    _LEFTOVER_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
    _BARE_URL_RE = re.compile(r"(?<![|<])(https?://\S+)")

    @classmethod
    def _to_mrkdwn(cls, text: str) -> str:
        """Convert Markdown to Slack mrkdwn, including tables."""
        if not text:
            return ""
        text = cls._TABLE_RE.sub(cls._convert_table, text)
        return cls._fixup_mrkdwn(slackify_markdown(text))

    @classmethod
    def _fixup_mrkdwn(cls, text: str) -> str:
        """Fix markdown artifacts that slackify_markdown misses."""
        code_blocks: list[str] = []

        def _save_code(m: re.Match) -> str:
            code_blocks.append(m.group(0))
            return f"\x00CB{len(code_blocks) - 1}\x00"

        text = cls._CODE_FENCE_RE.sub(_save_code, text)
        text = cls._INLINE_CODE_RE.sub(_save_code, text)
        text = cls._LEFTOVER_BOLD_RE.sub(r"*\1*", text)
        text = cls._LEFTOVER_HEADER_RE.sub(r"*\1*", text)
        text = cls._BARE_URL_RE.sub(lambda m: m.group(0).replace("&amp;", "&"), text)

        for i, block in enumerate(code_blocks):
            text = text.replace(f"\x00CB{i}\x00", block)
        return text

    @staticmethod
    def _convert_table(match: re.Match) -> str:
        """Convert a Markdown table to a Slack-readable list."""
        lines = [ln.strip() for ln in match.group(0).strip().splitlines() if ln.strip()]
        if len(lines) < 2:
            return match.group(0)
        headers = [h.strip() for h in lines[0].strip("|").split("|")]
        start = 2 if re.fullmatch(r"[|\s:\-]+", lines[1]) else 1
        rows: list[str] = []
        for line in lines[start:]:
            cells = [c.strip() for c in line.strip("|").split("|")]
            cells = (cells + [""] * len(headers))[: len(headers)]
            parts = [f"**{headers[i]}**: {cells[i]}" for i in range(len(headers)) if cells[i]]
            if parts:
                rows.append(" · ".join(parts))
        return "\n".join(rows)
