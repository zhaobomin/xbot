"""Feishu/Lark channel implementation using lark-oapi SDK with WebSocket long connection."""

import asyncio
import contextlib
import importlib.util
import json
import multiprocessing as mp
import os
import queue
import re
import threading
import time
import uuid
from collections import OrderedDict
from types import SimpleNamespace
from typing import Any, Callable, Literal

from pydantic import Field

from xbot.channels.base import BaseChannel
from xbot.channels.feishu_content import (
    MSG_TYPE_MAP,
    _extract_post_content,
    _extract_post_mention_ids,
    _extract_share_card_content,
)
from xbot.platform.bus.events import OutboundMessage
from xbot.platform.bus.queue import MessageBus
from xbot.platform.config.paths import get_media_dir
from xbot.platform.config.schema import Base
from xbot.platform.logging.core import get_logger
from xbot.platform.utils.helpers import sanitize_download_filename

logger = get_logger(__name__)

FEISHU_AVAILABLE = importlib.util.find_spec("lark_oapi") is not None


class FeishuConfig(Base):
    """Feishu/Lark channel configuration using WebSocket long connection."""

    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    react_emoji: str = "THUMBSUP"
    group_policy: Literal["open", "mention"] = "mention"
    reply_to_message: bool = False  # If True, bot replies quote the user's original message
    bot_open_id: str = ""


class FeishuChannel(BaseChannel):
    """
    Feishu/Lark channel using WebSocket long connection.

    Uses WebSocket to receive events - no public IP or webhook required.

    Requires:
    - App ID and App Secret from Feishu Open Platform
    - Bot capability enabled
    - Event subscription enabled (im.message.receive_v1)
    """

    name = "feishu"
    display_name = "Feishu"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return FeishuConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = FeishuConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: FeishuConfig = config
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_process: mp.Process | None = None
        self._ws_reader_task: asyncio.Task | None = None
        self._ws_event_queue: Any = None
        self._ws_stop_event: Any = None
        self._main_loop: asyncio.AbstractEventLoop | None = None  # Main async loop
        self._stop_event = threading.Event()  # Thread-safe stop signal
        self._processed_message_ids: OrderedDict[str, float] = OrderedDict()  # message_id -> timestamp
        self._message_dedup_ttl = 300  # 5 minutes TTL for dedup cache
        self._dedup_lock = threading.Lock()  # Protects _processed_message_ids from concurrent WebSocket callbacks
        self._dedup_cleanup_interval = 100  # Run cleanup every N messages
        self._dedup_message_counter = 0  # Counter for periodic cleanup
        self._ws_reconnect_delay = 5  # seconds between reconnect attempts
        self._ws_max_reconnect_delay = 60  # max delay with exponential backoff
        self._pending_messages: asyncio.Queue[tuple[Any, asyncio.Future]] | None = None
        self._bot_open_id = config.bot_open_id

    @staticmethod
    def _register_optional_event(builder: Any, method_name: str, handler: Any) -> Any:
        """Register an event handler only when the SDK supports it."""
        method = getattr(builder, method_name, None)
        return method(handler) if callable(method) else builder

    async def start(self) -> None:
        """Start the Feishu bot with WebSocket long connection."""
        if not FEISHU_AVAILABLE:
            logger.error("Feishu SDK not installed. Run: pip install lark-oapi")
            return

        if not self.config.app_id or not self.config.app_secret:
            logger.error("Feishu app_id and app_secret not configured")
            return

        import lark_oapi as lark
        self._running = True
        self._stop_event.clear()  # Reset stop signal for new start
        self._main_loop = asyncio.get_running_loop()
        self._pending_messages = asyncio.Queue()

        # Create Lark client for sending messages
        self._client = lark.Client.builder() \
            .app_id(self.config.app_id) \
            .app_secret(self.config.app_secret) \
            .log_level(lark.LogLevel.INFO) \
            .build()
        self._start_ws_worker()
        self._ws_reader_task = self._create_tracked_task(
            self._run_ws_event_reader(),
            name="feishu-ws-reader",
        )

        logger.info("Feishu bot started with WebSocket long connection")
        logger.info("No public IP required - using WebSocket to receive events")

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """
        Stop the Feishu bot.

        Notice: lark.ws.Client does not expose stop method，
        simply exiting the program will close the client.

        Reference: https://github.com/larksuite/oapi-sdk-python/blob/v2_main/lark_oapi/ws/client.py#L86
        """
        self._running = False
        self._stop_event.set()  # Signal WebSocket thread to stop

        if self._ws_stop_event is not None:
            self._ws_stop_event.set()

        if self._ws_reader_task is not None:
            self._ws_reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_reader_task

        if self._ws_process is not None:
            process = self._ws_process
            await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                logger.warning("Feishu WebSocket worker did not stop gracefully; terminating")
                process.terminate()
                await asyncio.to_thread(process.join, 2)
            if process.is_alive():
                logger.warning("Feishu WebSocket worker still alive after terminate; killing")
                process.kill()
                await asyncio.to_thread(process.join, 2)

        # Clean up references
        self._main_loop = None
        self._ws_process = None
        self._ws_reader_task = None
        self._ws_event_queue = None
        self._ws_stop_event = None
        self._pending_messages = None

        logger.info("Feishu bot stopped")

    def check_health(self) -> tuple[bool, str]:
        """Check Feishu channel health: WS process alive + client initialized."""
        if not self._running:
            return False, "channel stopped"
        if self._ws_process is None or not self._ws_process.is_alive():
            return False, "websocket worker process dead"
        if self._client is None:
            return False, "lark client not initialized"
        return True, "ok"

    def _start_ws_worker(self) -> None:
        from xbot.channels.feishu_ws_worker import run_feishu_ws_worker

        # Clean up old resources before starting new worker
        self._cleanup_ws_resources()

        ctx = mp.get_context("spawn")
        self._ws_event_queue = ctx.Queue()
        self._ws_stop_event = ctx.Event()
        self._ws_process = ctx.Process(
            target=run_feishu_ws_worker,
            args=(
                self.config.model_dump(),
                self._ws_event_queue,
                self._ws_stop_event,
                self._ws_reconnect_delay,
                self._ws_max_reconnect_delay,
            ),
            daemon=True,
            name="feishu-ws-worker",
        )
        self._ws_process.start()

    def _cleanup_ws_resources(self) -> None:
        """Clean up old WebSocket resources before restart."""
        # Signal old worker to stop
        if self._ws_stop_event:
            self._ws_stop_event.set()

        # Drain old queue to prevent stale events from being processed after restart.
        # Feishu WebSocket will re-deliver unacknowledged messages on reconnection.
        if self._ws_event_queue:
            drained_count = 0
            while True:
                try:
                    self._ws_event_queue.get_nowait()
                    drained_count += 1
                except queue.Empty:
                    break
            if drained_count > 0:
                logger.warning(
                    "Feishu: drained and discarded %d pending events from old queue",
                    drained_count,
                )

    @staticmethod
    def _namespace_from_dict(value: Any) -> Any:
        if isinstance(value, dict):
            return SimpleNamespace(**{k: FeishuChannel._namespace_from_dict(v) for k, v in value.items()})
        if isinstance(value, list):
            return [FeishuChannel._namespace_from_dict(item) for item in value]
        return value

    def _extract_message_id_from_event(self, data: Any) -> str | None:
        try:
            return data.event.message.message_id
        except (AttributeError, TypeError):
            return None

    def _mark_message_seen(self, message_id: str | None) -> bool:
        if not message_id:
            return True
        with self._dedup_lock:
            now = time.time()
            if message_id in self._processed_message_ids:
                return False
            self._processed_message_ids[message_id] = now
        return True

    async def _run_with_dedup_lock(self, fn: "Callable[[], None]") -> None:
        def _locked() -> None:
            with self._dedup_lock:
                fn()
        await asyncio.to_thread(_locked)

    async def _dispatch_worker_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type != "message":
            if event_type == "error":
                logger.warning("Feishu WebSocket worker error: %s", event.get("error", "unknown"))
            return
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        data = self._namespace_from_dict(payload)
        if not self._mark_message_seen(self._extract_message_id_from_event(data)):
            return
        await self._on_message(data)

    async def _run_ws_event_reader(self) -> None:
        ws_restart_count = 0
        max_ws_restarts = 10
        while self._running:
            if self._ws_event_queue is None:
                return
            try:
                event = await asyncio.to_thread(self._ws_event_queue.get, True, 0.5)
            except queue.Empty:
                if self._ws_process is not None and not self._ws_process.is_alive() and self._running:
                    ws_restart_count += 1
                    if ws_restart_count > max_ws_restarts:
                        logger.error("Feishu WebSocket worker exceeded max restart attempts (%d), giving up", max_ws_restarts)
                        return
                    backoff = min(2 ** ws_restart_count, 60)
                    logger.error(
                        "Feishu WebSocket worker exited unexpectedly, restarting in %ds (attempt %d/%d)",
                        backoff, ws_restart_count, max_ws_restarts,
                    )
                    await asyncio.sleep(backoff)
                    if self._running:
                        self._start_ws_worker()
                continue
            ws_restart_count = 0  # Reset on successful event
            await self._dispatch_worker_event(event)

    def _is_bot_mentioned(self, message: Any) -> bool:
        """Check if the bot is @mentioned in the message."""
        raw_content = message.content or ""
        if "@_all" in raw_content:
            return True

        for mention in getattr(message, "mentions", None) or []:
            mid = getattr(mention, "id", None)
            if not mid:
                continue
            mention_open_id = getattr(mid, "open_id", None) or ""
            if self._bot_open_id and mention_open_id == self._bot_open_id:
                return True
            if not self._bot_open_id and not getattr(mid, "user_id", None) and mention_open_id.startswith("ou_"):
                return True
        if self._bot_open_id and getattr(message, "message_type", "") == "post":
            try:
                content_json = json.loads(raw_content) if raw_content else {}
            except json.JSONDecodeError:
                content_json = {}
            if self._bot_open_id in _extract_post_mention_ids(content_json):
                return True
        return False

    def _is_group_message_for_bot(self, message: Any) -> bool:
        """Allow group messages when policy is open or bot is @mentioned."""
        if self.config.group_policy == "open":
            return True
        return self._is_bot_mentioned(message)

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> None:
        """Sync helper for adding reaction (runs in thread pool)."""
        from lark_oapi.api.im.v1 import (
            CreateMessageReactionRequest,
            CreateMessageReactionRequestBody,
            Emoji,
        )
        try:
            request = CreateMessageReactionRequest.builder() \
                .message_id(message_id) \
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                ).build()

            response = self._client.im.v1.message_reaction.create(request)

            if not response.success():
                logger.warning("Failed to add reaction: code=%s, msg=%s", response.code, response.msg)
            else:
                logger.debug("Added %s reaction to message %s", emoji_type, message_id)
        except Exception as e:
            logger.warning("Error adding reaction: %s", e)

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """
        Add a reaction emoji to a message (non-blocking).

        Common emoji types: THUMBSUP, OK, EYES, DONE, OnIt, HEART
        """
        if not self._client:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._add_reaction_sync, message_id, emoji_type)

    # Regex to match markdown tables (header + separator + data rows)
    _TABLE_RE = re.compile(
        r"((?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-:\s|]+\|[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+)",
        re.MULTILINE,
    )

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    _CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)

    # Markdown formatting patterns that should be stripped from plain-text
    # surfaces like table cells and heading text.
    _MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
    _MD_BOLD_UNDERSCORE_RE = re.compile(r"__(.+?)__")
    _MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
    _MD_STRIKE_RE = re.compile(r"~~(.+?)~~")

    @classmethod
    def _strip_md_formatting(cls, text: str) -> str:
        """Strip markdown formatting markers from text for plain display.

        Feishu table cells do not support markdown rendering, so we remove
        the formatting markers to keep the text readable.
        """
        # Remove bold markers
        text = cls._MD_BOLD_RE.sub(r"\1", text)
        text = cls._MD_BOLD_UNDERSCORE_RE.sub(r"\1", text)
        # Remove italic markers
        text = cls._MD_ITALIC_RE.sub(r"\1", text)
        # Remove strikethrough markers
        text = cls._MD_STRIKE_RE.sub(r"\1", text)
        return text

    @classmethod
    def _parse_md_table(cls, table_text: str) -> dict | None:
        """Parse a markdown table into a Feishu table element."""
        lines = [_line.strip() for _line in table_text.strip().split("\n") if _line.strip()]
        if len(lines) < 3:
            return None
        def split(_line: str) -> list[str]:
            return [c.strip() for c in _line.strip("|").split("|")]
        headers = [cls._strip_md_formatting(h) for h in split(lines[0])]
        rows = [[cls._strip_md_formatting(c) for c in split(_line)] for _line in lines[2:]]
        columns = [{"tag": "column", "name": f"c{i}", "display_name": h, "width": "auto"}
                   for i, h in enumerate(headers)]
        return {
            "tag": "table",
            "page_size": len(rows) + 1,
            "columns": columns,
            "rows": [{f"c{i}": r[i] if i < len(r) else "" for i in range(len(headers))} for r in rows],
        }

    def _build_card_elements(self, content: str) -> list[dict]:
        """Split content into div/markdown + table elements for Feishu card."""
        elements, last_end = [], 0
        for m in self._TABLE_RE.finditer(content):
            before = content[last_end:m.start()]
            if before.strip():
                elements.extend(self._split_headings(before))
            elements.append(self._parse_md_table(m.group(1)) or {"tag": "markdown", "content": m.group(1)})
            last_end = m.end()
        remaining = content[last_end:]
        if remaining.strip():
            elements.extend(self._split_headings(remaining))
        return elements or [{"tag": "markdown", "content": content}]

    @staticmethod
    def _build_interactive_card(elements: list[dict]) -> dict:
        """Build a Feishu Card Kit 2.0 interactive card."""
        return {
            "schema": "2.0",
            "config": {"wide_screen_mode": True},
            "body": {"elements": elements},
        }

    @classmethod
    def _card_payload_len(cls, elements: list[dict]) -> int:
        return len(json.dumps(cls._build_interactive_card(elements), ensure_ascii=False))

    @classmethod
    def _largest_fitting_prefix(cls, element: dict, text: str, max_chars_per_card: int) -> int:
        if not text:
            return 0
        one_char = {**element, "content": text[:1]}
        if cls._card_payload_len([one_char]) > max_chars_per_card:
            return len(text)
        low, high, best = 1, len(text), 1
        while low <= high:
            mid = (low + high) // 2
            candidate = {**element, "content": text[:mid]}
            if cls._card_payload_len([candidate]) <= max_chars_per_card:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        return best

    @classmethod
    def _split_markdown_element_to_fit(cls, element: dict, max_chars_per_card: int) -> list[dict]:
        content = str(element.get("content", ""))
        if cls._card_payload_len([element]) <= max_chars_per_card:
            return [element]

        pieces = [part for part in cls._CODE_BLOCK_RE.split(content) if part]
        refined: list[str] = []
        for piece in pieces:
            if piece.startswith("```"):
                refined.append(piece)
                continue
            refined.extend(part for part in re.split(r"(\n{2,})", piece) if part)

        chunks: list[dict] = []
        current = ""
        for piece in refined or [content]:
            candidate = f"{current}{piece}" if current else piece
            candidate_el = {**element, "content": candidate}
            if cls._card_payload_len([candidate_el]) <= max_chars_per_card:
                current = candidate
                continue
            if current:
                chunks.append({**element, "content": current})
                current = ""

            remaining = piece
            while remaining:
                fit = cls._largest_fitting_prefix(element, remaining, max_chars_per_card)
                if fit >= len(remaining):
                    current = remaining
                    remaining = ""
                else:
                    chunks.append({**element, "content": remaining[:fit]})
                    remaining = remaining[fit:]

        if current:
            chunks.append({**element, "content": current})

        return chunks or [element]

    @classmethod
    def _split_table_element_to_fit(cls, element: dict, max_chars_per_card: int) -> list[dict]:
        if cls._card_payload_len([element]) <= max_chars_per_card:
            return [element]
        rows = element.get("rows", [])
        if not isinstance(rows, list) or not rows:
            return [element]

        columns = element.get("columns", [])
        column_labels: dict[str, str] = {}
        if isinstance(columns, list):
            for col in columns:
                if not isinstance(col, dict):
                    continue
                name = col.get("name")
                if not isinstance(name, str) or not name:
                    continue
                display_name = col.get("display_name")
                column_labels[name] = str(display_name or name)

        def _row_to_markdown_chunks(row: dict) -> list[dict]:
            lines: list[str] = []
            if isinstance(row, dict):
                for key, value in row.items():
                    label = column_labels.get(key, key)
                    lines.append(f"**{label}:** {value}")
            else:
                lines.append(str(row))
            return cls._split_markdown_element_to_fit(
                {"tag": "markdown", "content": "\n".join(lines)},
                max_chars_per_card,
            )

        chunks: list[dict] = []
        current_rows: list[dict] = []
        for row in rows:
            single_row = {**element, "rows": [row], "page_size": 2}
            if cls._card_payload_len([single_row]) > max_chars_per_card:
                if current_rows:
                    chunks.append({**element, "rows": current_rows, "page_size": len(current_rows) + 1})
                    current_rows = []
                chunks.extend(_row_to_markdown_chunks(row))
                continue

            candidate_rows = [*current_rows, row]
            candidate = {**element, "rows": candidate_rows, "page_size": len(candidate_rows) + 1}
            if current_rows and cls._card_payload_len([candidate]) > max_chars_per_card:
                chunks.append({**element, "rows": current_rows, "page_size": len(current_rows) + 1})
                current_rows = [row]
                continue
            current_rows = candidate_rows

        if current_rows:
            chunks.append({**element, "rows": current_rows, "page_size": len(current_rows) + 1})

        return chunks or [element]

    @classmethod
    def _split_oversized_element(cls, element: dict, max_chars_per_card: int) -> list[dict]:
        if cls._card_payload_len([element]) <= max_chars_per_card:
            return [element]
        if element.get("tag") == "markdown":
            return cls._split_markdown_element_to_fit(element, max_chars_per_card)
        if element.get("tag") == "table":
            return cls._split_table_element_to_fit(element, max_chars_per_card)
        return [element]

    @classmethod
    def _split_elements_by_table_limit(
        cls,
        elements: list[dict],
        max_tables: int = 1,
        max_chars_per_card: int = 3500,
    ) -> list[list[dict]]:
        """Split card elements into groups respecting table table and length limits.

        Feishu cards have:
        - A hard limit of one table per card (API error 11310)
        - A content length limit of ~4096 characters (API error 230025)

        When the rendered content contains multiple markdown tables or exceeds
        the character limit, elements are split across multiple card messages.

        Args:
            elements: List of card elements (div, markdown, table, etc.)
            max_tables: Maximum tables per card (default 1)
            max_chars_per_card: Maximum characters per card (default 3500,
                                leaving headroom under the 4096 limit)

        Returns:
            List of element groups, each suitable for a single card.
        """
        if not elements:
            return [[]]

        split_elements: list[dict] = []
        for element in elements:
            split_elements.extend(cls._split_oversized_element(element, max_chars_per_card))

        groups: list[list[dict]] = []
        current: list[dict] = []
        table_count = 0

        for el in split_elements:
            # Check if we need to start a new card
            need_new_card = False

            # Table limit check
            if el.get("tag") == "table":
                if table_count >= max_tables:
                    need_new_card = True

            # Length limit check - always check before adding any element
            if current and cls._card_payload_len([*current, el]) > max_chars_per_card:
                need_new_card = True

            if need_new_card:
                if current:
                    groups.append(current)
                current = []
                table_count = 0

            current.append(el)
            if el.get("tag") == "table":
                table_count += 1

        if current:
            groups.append(current)

        return groups or [[]]

    def _split_headings(self, content: str) -> list[dict]:
        """Split content by headings, converting headings to div elements."""
        protected = content
        code_blocks = []
        for m in self._CODE_BLOCK_RE.finditer(content):
            code_blocks.append(m.group(1))
            protected = protected.replace(m.group(1), f"\x00CODE{len(code_blocks)-1}\x00", 1)

        elements = []
        last_end = 0
        for m in self._HEADING_RE.finditer(protected):
            before = protected[last_end:m.start()].strip()
            if before:
                elements.append({"tag": "markdown", "content": before})
            text = self._strip_md_formatting(m.group(2).strip())
            display_text = f"**{text}**" if text else ""
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": display_text,
                },
            })
            last_end = m.end()
        remaining = protected[last_end:].strip()
        if remaining:
            elements.append({"tag": "markdown", "content": remaining})

        for i, cb in enumerate(code_blocks):
            for el in elements:
                if el.get("tag") == "markdown":
                    el["content"] = el["content"].replace(f"\x00CODE{i}\x00", cb)

        return elements or [{"tag": "markdown", "content": content}]

    # ── Smart format detection ──────────────────────────────────────────
    # Patterns that indicate "complex" markdown needing card rendering
    _COMPLEX_MD_RE = re.compile(
        r"```"                        # fenced code block
        r"|^\|.+\|.*\n\s*\|[-:\s|]+\|"  # markdown table (header + separator)
        r"|^#{1,6}\s+"                # headings
        , re.MULTILINE,
    )

    # Simple markdown patterns (bold, italic, strikethrough)
    _SIMPLE_MD_RE = re.compile(
        r"\*\*.+?\*\*"               # **bold**
        r"|__.+?__"                   # __bold__
        r"|(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"  # *italic* (single *)
        r"|~~.+?~~"                   # ~~strikethrough~~
        , re.DOTALL,
    )

    # Markdown link: [text](url)
    _MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")

    # Unordered list items
    _LIST_RE = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)

    # Ordered list items
    _OLIST_RE = re.compile(r"^[\s]*\d+\.\s+", re.MULTILINE)

    # Max length for plain text format
    _TEXT_MAX_LEN = 200

    # Max length for post (rich text) format; beyond this, use card
    _POST_MAX_LEN = 2000

    @classmethod
    def _detect_msg_format(cls, content: str) -> str:
        """Determine the optimal Feishu message format for *content*.

        Returns one of:
        - ``"text"``        – plain text, short and no markdown
        - ``"post"``        – rich text (links only, moderate length)
        - ``"interactive"`` – card with full markdown rendering
        """
        stripped = content.strip()

        # Complex markdown (code blocks, tables, headings) → always card
        if cls._COMPLEX_MD_RE.search(stripped):
            return "interactive"

        # Long content → card (better readability with card layout)
        if len(stripped) > cls._POST_MAX_LEN:
            return "interactive"

        # Has bold/italic/strikethrough → card (post format can't render these)
        if cls._SIMPLE_MD_RE.search(stripped):
            return "interactive"

        # Has list items → card (post format can't render list bullets well)
        if cls._LIST_RE.search(stripped) or cls._OLIST_RE.search(stripped):
            return "interactive"

        # Has links → post format (supports <a> tags)
        if cls._MD_LINK_RE.search(stripped):
            return "post"

        # Short plain text → text format
        if len(stripped) <= cls._TEXT_MAX_LEN:
            return "text"

        # Medium plain text without any formatting → post format
        return "post"

    @classmethod
    def _markdown_to_post(cls, content: str) -> str:
        """Convert markdown content to Feishu post message JSON.

        Handles links ``[text](url)`` as ``a`` tags; everything else as ``text`` tags.
        Each line becomes a paragraph (row) in the post body.
        """
        lines = content.strip().split("\n")
        paragraphs: list[list[dict]] = []

        for line in lines:
            elements: list[dict] = []
            last_end = 0

            for m in cls._MD_LINK_RE.finditer(line):
                # Text before this link
                before = line[last_end:m.start()]
                if before:
                    elements.append({"tag": "text", "text": before})
                elements.append({
                    "tag": "a",
                    "text": m.group(1),
                    "href": m.group(2),
                })
                last_end = m.end()

            # Remaining text after last link
            remaining = line[last_end:]
            if remaining:
                elements.append({"tag": "text", "text": remaining})

            # Empty line → empty paragraph for spacing
            if not elements:
                elements.append({"tag": "text", "text": ""})

            paragraphs.append(elements)

        post_body = {
            "zh_cn": {
                "content": paragraphs,
            }
        }
        return json.dumps(post_body, ensure_ascii=False)

    _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif"}
    _AUDIO_EXTS = {".opus"}
    _VIDEO_EXTS = {".mp4", ".mov", ".avi"}
    _FILE_TYPE_MAP = {
        ".opus": "opus", ".mp4": "mp4", ".pdf": "pdf", ".doc": "doc", ".docx": "doc",
        ".xls": "xls", ".xlsx": "xls", ".ppt": "ppt", ".pptx": "ppt",
    }

    def _upload_image_sync(self, file_path: str) -> str | None:
        """Upload an image to Feishu and return the image_key."""
        from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody
        try:
            with open(file_path, "rb") as f:
                request = CreateImageRequest.builder() \
                    .request_body(
                        CreateImageRequestBody.builder()
                        .image_type("message")
                        .image(f)
                        .build()
                    ).build()
                response = self._client.im.v1.image.create(request)
                if response.success():
                    image_key = response.data.image_key
                    logger.debug("Uploaded image %s: %s", os.path.basename(file_path), image_key)
                    return image_key
                else:
                    logger.error("Failed to upload image: code=%s, msg=%s", response.code, response.msg)
                    return None
        except Exception as e:
            logger.error("Error uploading image %s: %s", file_path, e)
            return None

    def _upload_file_sync(self, file_path: str) -> str | None:
        """Upload a file to Feishu and return the file_key."""
        from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody
        ext = os.path.splitext(file_path)[1].lower()
        file_type = self._FILE_TYPE_MAP.get(ext, "stream")
        file_name = os.path.basename(file_path)
        try:
            with open(file_path, "rb") as f:
                request = CreateFileRequest.builder() \
                    .request_body(
                        CreateFileRequestBody.builder()
                        .file_type(file_type)
                        .file_name(file_name)
                        .file(f)
                        .build()
                    ).build()
                response = self._client.im.v1.file.create(request)
                if response.success():
                    file_key = response.data.file_key
                    logger.debug("Uploaded file %s: %s", file_name, file_key)
                    return file_key
                else:
                    logger.error("Failed to upload file: code=%s, msg=%s", response.code, response.msg)
                    return None
        except Exception as e:
            logger.error("Error uploading file %s: %s", file_path, e)
            return None

    def _download_image_sync(self, message_id: str, image_key: str) -> tuple[bytes | None, str | None]:
        """Download an image from Feishu message by message_id and image_key."""
        from lark_oapi.api.im.v1 import GetMessageResourceRequest
        try:
            request = GetMessageResourceRequest.builder() \
                .message_id(message_id) \
                .file_key(image_key) \
                .type("image") \
                .build()
            response = self._client.im.v1.message_resource.get(request)
            if response.success():
                file_data = response.file
                # GetMessageResourceRequest returns BytesIO, need to read bytes
                if hasattr(file_data, 'read'):
                    file_data = file_data.read()
                return file_data, response.file_name
            else:
                logger.error("Failed to download image: code=%s, msg=%s", response.code, response.msg)
                return None, None
        except Exception as e:
            logger.error("Error downloading image %s: %s", image_key, e)
            return None, None

    def _download_file_sync(
        self, message_id: str, file_key: str, resource_type: str = "file"
    ) -> tuple[bytes | None, str | None]:
        """Download a file/audio/media from a Feishu message by message_id and file_key."""
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        # Feishu API only accepts 'image' or 'file' as type parameter
        # Convert 'audio' to 'file' for API compatibility
        if resource_type == "audio":
            resource_type = "file"

        try:
            request = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(file_key)
                .type(resource_type)
                .build()
            )
            response = self._client.im.v1.message_resource.get(request)
            if response.success():
                file_data = response.file
                if hasattr(file_data, "read"):
                    file_data = file_data.read()
                return file_data, response.file_name
            else:
                logger.error("Failed to download %s: code=%s, msg=%s", resource_type, response.code, response.msg)
                return None, None
        except Exception:
            logger.exception("Error downloading %s %s", resource_type, file_key)
            return None, None

    async def _download_and_save_media(
        self,
        msg_type: str,
        content_json: dict,
        message_id: str | None = None
    ) -> tuple[str | None, str]:
        """
        Download media from Feishu and save to local disk.

        Returns:
            (file_path, content_text) - file_path is None if download failed
        """
        loop = asyncio.get_running_loop()
        media_dir = get_media_dir("feishu")

        data, filename = None, None

        if msg_type == "image":
            image_key = content_json.get("image_key")
            if image_key and message_id:
                data, filename = await loop.run_in_executor(
                    None, self._download_image_sync, message_id, image_key
                )
                if not filename:
                    filename = f"{image_key[:16]}.jpg"

        elif msg_type in ("audio", "file", "media"):
            file_key = content_json.get("file_key")
            if file_key and message_id:
                data, filename = await loop.run_in_executor(
                    None, self._download_file_sync, message_id, file_key, msg_type
                )
                if not filename:
                    filename = file_key[:16]
                if msg_type == "audio" and not filename.endswith(".opus"):
                    filename = f"{filename}.opus"

        if data and filename:
            safe_name = sanitize_download_filename(filename, file_key[:16] if 'file_key' in locals() and file_key else f"{msg_type}_download")
            file_path = media_dir / safe_name
            await asyncio.to_thread(file_path.write_bytes, data)
            logger.debug("Downloaded %s to %s", msg_type, file_path)
            return str(file_path), f"[{msg_type}: {safe_name}]"

        return None, f"[{msg_type}: download failed]"

    _REPLY_CONTEXT_MAX_LEN = 200

    def _get_message_content_sync(self, message_id: str) -> str | None:
        """Fetch the text content of a Feishu message by ID (synchronous).

        Returns a "[Reply to: ...]" context string, or None on failure.
        """
        from lark_oapi.api.im.v1 import GetMessageRequest
        try:
            request = GetMessageRequest.builder().message_id(message_id).build()
            response = self._client.im.v1.message.get(request)
            if not response.success():
                logger.debug(
                    "Feishu: could not fetch parent message %s: code=%s, msg=%s",
                    message_id, response.code, response.msg,
                )
                return None
            items = getattr(response.data, "items", None)
            if not items:
                return None
            msg_obj = items[0]
            raw_content = getattr(msg_obj, "body", None)
            raw_content = getattr(raw_content, "content", None) if raw_content else None
            if not raw_content:
                return None
            try:
                content_json = json.loads(raw_content)
            except (json.JSONDecodeError, TypeError):
                return None
            msg_type = getattr(msg_obj, "msg_type", "")
            if msg_type == "text":
                text = content_json.get("text", "").strip()
            elif msg_type == "post":
                text, _ = _extract_post_content(content_json)
                text = text.strip()
            else:
                text = ""
            if not text:
                return None
            if len(text) > self._REPLY_CONTEXT_MAX_LEN:
                text = text[: self._REPLY_CONTEXT_MAX_LEN] + "..."
            return f"[Reply to: {text}]"
        except Exception as e:
            logger.debug("Feishu: error fetching parent message %s: %s", message_id, e)
            return None

    def _reply_message_sync(self, parent_message_id: str, msg_type: str, content: str) -> bool:
        """Reply to an existing Feishu message using the Reply API (synchronous) with retry."""
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
        msg_uuid = uuid.uuid4().hex
        for attempt in range(self._SEND_MAX_RETRIES):
            try:
                request = ReplyMessageRequest.builder() \
                    .message_id(parent_message_id) \
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .msg_type(msg_type)
                        .content(content)
                        .uuid(msg_uuid)
                        .build()
                    ).build()
                response = self._client.im.v1.message.reply(request)
                if not response.success():
                    logger.error(
                        "Failed to reply to Feishu message %s: code=%s, msg=%s, log_id=%s",
                        parent_message_id, response.code, response.msg, response.get_log_id()
                    )
                    return False
                logger.debug("Feishu reply sent to message %s", parent_message_id)
                return True
            except (ConnectionError, OSError) as e:
                if attempt < self._SEND_MAX_RETRIES - 1:
                    delay = self._SEND_RETRY_BACKOFF[attempt]
                    logger.warning(
                        "Feishu reply retry %d/%d after network error: %s (backoff %.1fs)",
                        attempt + 1, self._SEND_MAX_RETRIES, e, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error("Feishu reply failed after %d retries: %s", self._SEND_MAX_RETRIES, e)
                    return False
            except Exception as e:
                logger.error("Error replying to Feishu message %s: %s", parent_message_id, e)
                return False
        return False

    _SEND_MAX_RETRIES = 3
    _SEND_RETRY_BACKOFF = (1.0, 2.0, 4.0)

    def _send_message_sync(self, receive_id_type: str, receive_id: str, msg_type: str, content: str) -> bool:
        """Send a single message (text/image/file/interactive) synchronously with retry."""
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
        msg_uuid = uuid.uuid4().hex
        for attempt in range(self._SEND_MAX_RETRIES):
            try:
                request = CreateMessageRequest.builder() \
                    .receive_id_type(receive_id_type) \
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(receive_id)
                        .msg_type(msg_type)
                        .content(content)
                        .uuid(msg_uuid)
                        .build()
                    ).build()
                response = self._client.im.v1.message.create(request)
                if not response.success():
                    logger.error(
                        "Failed to send Feishu %s message: code=%s, msg=%s, log_id=%s",
                        msg_type, response.code, response.msg, response.get_log_id()
                    )
                    return False
                logger.debug("Feishu %s message sent to %s", msg_type, receive_id)
                return True
            except (ConnectionError, OSError) as e:
                if attempt < self._SEND_MAX_RETRIES - 1:
                    delay = self._SEND_RETRY_BACKOFF[attempt]
                    logger.warning(
                        "Feishu send %s retry %d/%d after network error: %s (backoff %.1fs)",
                        msg_type, attempt + 1, self._SEND_MAX_RETRIES, e, delay,
                    )
                    time.sleep(delay)
                else:
                    logger.error("Feishu send %s failed after %d retries: %s", msg_type, self._SEND_MAX_RETRIES, e)
                    return False
            except Exception as e:
                logger.error("Error sending Feishu %s message: %s", msg_type, e)
                return False
        return False

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Feishu, including media (images/files) if present."""
        if not self._client:
            logger.warning("Feishu client not initialized")
            return

        try:
            receive_id_type = "chat_id" if msg.chat_id.startswith("oc_") else "open_id"
            loop = asyncio.get_running_loop()

            # Handle tool hint messages as code blocks in interactive cards.
            # These are progress-only messages and should bypass normal reply routing.
            if msg.metadata.get("_tool_hint"):
                if msg.content and msg.content.strip():
                    await self._send_tool_hint_card(
                        receive_id_type, msg.chat_id, msg.content.strip()
                    )
                return

            # Handle interaction requests with formatted post message.
            # Show options clearly, and tailor the reply hint based on validation mode.
            # Supports question, approval, and confirmation types.
            if msg.metadata.get("interaction_request") and msg.metadata.get("interaction_kind") in ("question", "approval", "confirmation"):
                suggestions = msg.metadata.get("suggestions", [])
                if suggestions:
                    # Build formatted prompt with options list
                    options_text = "\n".join(f"  • {opt}" for opt in suggestions)
                    validation_mode = msg.metadata.get("validation_mode", "strict")
                    if validation_mode == "suggested":
                        hint = f"可直接回复上面的建议项（如\"{suggestions[0]}\"），也可输入你自己的内容"
                    else:
                        hint = f"请回复以下选项之一（如\"{suggestions[0]}\"）"
                    formatted_content = f"{msg.content.strip()}\n\n{options_text}\n\n{hint}"
                    post_body = self._markdown_to_post(formatted_content)
                    await loop.run_in_executor(
                        None, self._send_message_sync,
                        receive_id_type, msg.chat_id, "post", post_body,
                    )
                else:
                    # No options, send as plain text
                    text_body = json.dumps({"text": msg.content.strip()}, ensure_ascii=False)
                    await loop.run_in_executor(
                        None, self._send_message_sync,
                        receive_id_type, msg.chat_id, "text", text_body,
                    )
                return

            # Determine whether the first message should quote the user's message.
            # Only the very first send (media or text) in this call uses reply; subsequent
            # chunks/media fall back to plain create to avoid redundant quote bubbles.
            reply_message_id: str | None = None
            if (
                self.config.reply_to_message
                and not msg.metadata.get("_progress", False)
            ):
                reply_message_id = msg.metadata.get("message_id") or None

            first_send = True  # tracks whether the reply has already been used

            def _do_send(m_type: str, content: str) -> None:
                """Send via reply (first message) or create (subsequent)."""
                nonlocal first_send
                if reply_message_id and first_send:
                    first_send = False
                    ok = self._reply_message_sync(reply_message_id, m_type, content)
                    if ok:
                        return
                    # Fall back to regular send if reply fails
                self._send_message_sync(receive_id_type, msg.chat_id, m_type, content)

            for file_path in msg.media:
                if not os.path.isfile(file_path):
                    logger.warning("Media file not found: %s", file_path)
                    continue
                ext = os.path.splitext(file_path)[1].lower()
                if ext in self._IMAGE_EXTS:
                    key = await loop.run_in_executor(None, self._upload_image_sync, file_path)
                    if key:
                        await loop.run_in_executor(
                            None, _do_send,
                            "image", json.dumps({"image_key": key}, ensure_ascii=False),
                        )
                else:
                    key = await loop.run_in_executor(None, self._upload_file_sync, file_path)
                    if key:
                        # Use msg_type "audio" for audio, "video" for video, "file" for documents.
                        # Feishu requires these specific msg_types for inline playback.
                        # Note: "media" is only valid as a tag inside "post" messages, not as a standalone msg_type.
                        if ext in self._AUDIO_EXTS:
                            media_type = "audio"
                        elif ext in self._VIDEO_EXTS:
                            media_type = "media"
                        else:
                            media_type = "file"
                        await loop.run_in_executor(
                            None, _do_send,
                            media_type, json.dumps({"file_key": key}, ensure_ascii=False),
                        )

            if msg.content and msg.content.strip():
                fmt = self._detect_msg_format(msg.content)

                if fmt == "text":
                    # Short plain text – send as simple text message
                    text_body = json.dumps({"text": msg.content.strip()}, ensure_ascii=False)
                    await loop.run_in_executor(None, _do_send, "text", text_body)

                elif fmt == "post":
                    # Medium content with links – send as rich-text post
                    post_body = self._markdown_to_post(msg.content)
                    await loop.run_in_executor(None, _do_send, "post", post_body)

                else:
                    # Complex / long content – send as interactive card
                    elements = self._build_card_elements(msg.content)
                    for chunk in self._split_elements_by_table_limit(elements):
                        card = self._build_interactive_card(chunk)
                        await loop.run_in_executor(
                            None, _do_send,
                            "interactive", json.dumps(card, ensure_ascii=False),
                        )

        except Exception:
            logger.exception("Error sending Feishu message")

    async def _on_message(self, data: Any) -> None:
        """Handle incoming message from Feishu."""
        try:
            event = data.event
            message = event.message
            sender = event.sender
            message_id = message.message_id
            now = time.time()

            # Periodic cleanup: run every N messages instead of every message
            self._dedup_message_counter += 1
            if self._dedup_message_counter % self._dedup_cleanup_interval == 0:
                def _cleanup_dedup_state() -> None:
                    expired_keys = [
                        key for key, ts in list(self._processed_message_ids.items())
                        if now - ts > self._message_dedup_ttl
                    ]
                    for key in expired_keys:
                        del self._processed_message_ids[key]

                    # Trim cache if still over limit after TTL cleanup
                    max_cache_size = 1000
                    while len(self._processed_message_ids) > max_cache_size:
                        self._processed_message_ids.popitem(last=False)
                await self._run_with_dedup_lock(_cleanup_dedup_state)

            # Mark message as seen
            def _mark_seen() -> None:
                if message_id in self._processed_message_ids:
                    return
                self._processed_message_ids[message_id] = now
            await self._run_with_dedup_lock(_mark_seen)

            # Skip bot messages
            if sender.sender_type == "bot":
                return

            sender_id = getattr(sender.sender_id, "open_id", None) if sender.sender_id else None
            sender_id = sender_id or "unknown"
            chat_id = message.chat_id
            chat_type = message.chat_type
            msg_type = message.message_type

            if chat_type == "group" and not self._is_group_message_for_bot(message):
                logger.debug("Feishu: skipping group message (not mentioned)")
                return

            # Add reaction
            await self._add_reaction(message_id, self.config.react_emoji)

            # Parse content
            content_parts = []
            media_paths = []

            try:
                content_json = json.loads(message.content) if message.content else {}
            except json.JSONDecodeError:
                content_json = {}

            if msg_type == "text":
                text = content_json.get("text", "")
                if text:
                    content_parts.append(text)

            elif msg_type == "post":
                text, image_keys = _extract_post_content(content_json)
                if text:
                    content_parts.append(text)
                # Download images embedded in post
                for img_key in image_keys:
                    file_path, content_text = await self._download_and_save_media(
                        "image", {"image_key": img_key}, message_id
                    )
                    if file_path:
                        media_paths.append(file_path)
                    content_parts.append(content_text)

            elif msg_type in ("image", "audio", "file", "media"):
                file_path, content_text = await self._download_and_save_media(msg_type, content_json, message_id)
                if file_path:
                    media_paths.append(file_path)

                if msg_type == "audio" and file_path:
                    transcription = await self.transcribe_audio(file_path)
                    if transcription:
                        content_text = f"[transcription: {transcription}]"

                content_parts.append(content_text)

            elif msg_type in ("share_chat", "share_user", "interactive", "share_calendar_event", "system", "merge_forward"):
                # Handle share cards and interactive messages
                text = _extract_share_card_content(content_json, msg_type)
                if text:
                    content_parts.append(text)

            else:
                content_parts.append(MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]"))

            # Extract reply context (parent/root message IDs)
            parent_id = getattr(message, "parent_id", None) or None
            root_id = getattr(message, "root_id", None) or None

            # Prepend quoted message text when the user replied to another message
            if parent_id and self._client:
                loop = asyncio.get_running_loop()
                reply_ctx = await loop.run_in_executor(
                    None, self._get_message_content_sync, parent_id
                )
                if reply_ctx:
                    content_parts.insert(0, reply_ctx)

            content = "\n".join(content_parts) if content_parts else ""

            if not content and not media_paths:
                return

            # Forward to message bus
            reply_to = chat_id if chat_type == "group" else sender_id
            await self._handle_message(
                sender_id=sender_id,
                chat_id=reply_to,
                content=content,
                media=media_paths,
                metadata={
                    "message_id": message_id,
                    "chat_type": chat_type,
                    "msg_type": msg_type,
                    "parent_id": parent_id,
                    "root_id": root_id,
                }
            )

        except Exception:
            logger.exception("Error processing Feishu message")

    def _on_reaction_created(self, data: Any) -> None:
        """Ignore reaction events so they do not generate SDK noise."""
        pass

    def _on_message_read(self, data: Any) -> None:
        """Ignore read events so they do not generate SDK noise."""
        pass

    def _on_bot_p2p_chat_entered(self, data: Any) -> None:
        """Ignore p2p-enter events when a user opens a bot chat."""
        logger.debug("Bot entered p2p chat (user opened chat window)")
        pass

    @staticmethod
    def _format_tool_hint_lines(tool_hint: str) -> str:
        """Split tool hints across lines on top-level call separators only."""
        parts: list[str] = []
        buf: list[str] = []
        depth = 0
        in_string = False
        quote_char = ""
        escaped = False

        for i, ch in enumerate(tool_hint):
            buf.append(ch)

            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote_char:
                    in_string = False
                continue

            if ch in {'"', "'"}:
                in_string = True
                quote_char = ch
                continue

            if ch == "(":
                depth += 1
                continue

            if ch == ")" and depth > 0:
                depth -= 1
                continue

            if ch == "," and depth == 0:
                next_char = tool_hint[i + 1] if i + 1 < len(tool_hint) else ""
                if next_char == " ":
                    parts.append("".join(buf).rstrip())
                    buf = []

        if buf:
            parts.append("".join(buf).strip())

        return "\n".join(part for part in parts if part)

    async def _send_tool_hint_card(self, receive_id_type: str, receive_id: str, tool_hint: str) -> None:
        """Send tool hint as an interactive card with formatted code block.

        Args:
            receive_id_type: "chat_id" or "open_id"
            receive_id: The target chat or user ID
            tool_hint: Formatted tool hint string (e.g., 'web_search("q"), read_file("path")')
        """
        loop = asyncio.get_running_loop()

        # Put each top-level tool call on its own line without altering commas inside arguments.
        formatted_code = self._format_tool_hint_lines(tool_hint)

        card = {
            "schema": "2.0",
            "config": {"wide_screen_mode": True},
            "body": {
                "elements": [
                {
                    "tag": "markdown",
                    "content": f"**Tool Calls**\n\n```text\n{formatted_code}\n```"
                }
                ]
            }
        }

        await loop.run_in_executor(
            None, self._send_message_sync,
            receive_id_type, receive_id, "interactive",
            json.dumps(card, ensure_ascii=False),
        )
