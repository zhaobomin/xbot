from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from xbot_codex.channels.base import BaseChannel
from xbot_codex.channels.feishu_content import MSG_TYPE_MAP
from xbot_codex.channels.feishu_content import extract_post_content
from xbot_codex.channels.feishu_content import extract_share_card_content
from xbot_codex.events import InboundMessage
from xbot_codex.events import OutboundMessage


class FeishuChannel(BaseChannel):
    name = "feishu"

    _COMPLEX_MD_RE = re.compile(r"```|^\|.+\|.*\n\s*\|[-:\s|]+\||^#{1,6}\s+", re.MULTILINE)
    _SIMPLE_MD_RE = re.compile(r"\*\*.+?\*\*|__.+?__|~~.+?~~", re.DOTALL)
    _MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
    _LIST_RE = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)
    _OLIST_RE = re.compile(r"^[\s]*\d+\.\s+", re.MULTILINE)
    _TEXT_MAX_LEN = 200
    _POST_MAX_LEN = 2000

    def __init__(
        self,
        config: Any,
        *,
        on_message: Callable[[InboundMessage], Awaitable[None]] | None = None,
        send_impl: Callable[[OutboundMessage], Awaitable[None]] | None = None,
    ):
        self.config = config
        self._on_message = on_message
        self._send_impl = send_impl
        self._running = False
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._processed_message_ids: OrderedDict[str, float] = OrderedDict()
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_reconnect_delay = 5
        self._ws_max_reconnect_delay = 60

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_flag.clear()
        self._main_loop = asyncio.get_running_loop()
        try:
            import lark_oapi as lark
        except ImportError:
            logger.warning("lark_oapi not installed; Feishu channel disabled")
            return
        if not self.config.app_id or not self.config.app_secret:
            logger.warning("Feishu credentials not configured")
            return
        self._client = lark.Client.builder().app_id(self.config.app_id).app_secret(self.config.app_secret).build()

        def _run_ws() -> None:
            try:
                import lark_oapi.ws.client as lark_ws_client

                self._ws_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._ws_loop)
                lark_ws_client.loop = self._ws_loop
                dispatcher = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(
                    self._on_message_sync
                ).build()
                self._ws_client = lark.ws.Client(
                    self.config.app_id,
                    self.config.app_secret,
                    event_handler=dispatcher,
                )
                reconnect_delay = self._ws_reconnect_delay
                while not self._stop_flag.is_set():
                    try:
                        logger.info("Feishu WebSocket connecting...")
                        self._ws_client.start()
                        reconnect_delay = self._ws_reconnect_delay
                    except Exception as exc:
                        logger.warning("Feishu WebSocket error: {}", exc)
                    if self._stop_flag.wait(timeout=reconnect_delay):
                        break
                    reconnect_delay = min(reconnect_delay * 2, self._ws_max_reconnect_delay)
            except Exception as exc:
                logger.warning("Feishu WebSocket stopped: {}", exc)
            finally:
                if self._ws_loop is not None:
                    self._ws_loop.close()
                    self._ws_loop = None

        self._ws_thread = threading.Thread(target=_run_ws, daemon=True, name="xbot-codex-feishu")
        self._ws_thread.start()

    async def stop(self) -> None:
        self._running = False
        self._stop_flag.set()
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)
        self._ws_thread = None
        self._main_loop = None
        self._ws_loop = None

    async def send(self, msg: OutboundMessage) -> None:
        if self._send_impl is not None:
            await self._send_impl(msg)
            return
        if self._client is None:
            return

        receive_id_type = "chat_id" if msg.chat_id.startswith("oc_") else "open_id"
        content = (msg.content or "").strip()
        if not content:
            return
        reply_message_id = None
        if self.config.reply_to_message and not msg.metadata.get("_progress", False):
            reply_message_id = msg.metadata.get("message_id") or None

        fmt = self._detect_msg_format(content)
        logger.info("Feishu outbound: receive_id_type={} chat_id={} format={} chars={}", receive_id_type, msg.chat_id, fmt, len(content))
        first_send = True

        def _do_send(msg_type: str, body: str) -> bool:
            nonlocal first_send
            if reply_message_id and first_send:
                first_send = False
                ok = self._reply_message_sync(reply_message_id, msg_type, body)
                if ok:
                    return True
            return self._send_message_sync(receive_id_type, msg.chat_id, msg_type, body)

        if fmt == "text":
            body = json.dumps({"text": content}, ensure_ascii=False)
            await asyncio.get_running_loop().run_in_executor(
                None, _do_send, "text", body
            )
            return

        if fmt == "post":
            body = self._markdown_to_post(content)
            await asyncio.get_running_loop().run_in_executor(
                None, _do_send, "post", body
            )
            return

        card = {
            "config": {"wide_screen_mode": True},
            "elements": [{"tag": "markdown", "content": content}],
        }
        await asyncio.get_running_loop().run_in_executor(
            None,
            _do_send,
            "interactive",
            json.dumps(card, ensure_ascii=False),
        )

    def _reply_message_sync(self, parent_message_id: str, msg_type: str, content: str) -> bool:
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

        try:
            request = (
                ReplyMessageRequest.builder()
                .message_id(parent_message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .msg_type(msg_type)
                    .content(content)
                    .build()
                )
                .build()
            )
            response = self._client.im.v1.message.reply(request)
            logger.info(
                "Feishu reply result: success={} type={} parent_message_id={}",
                bool(response.success()),
                msg_type,
                parent_message_id,
            )
            return bool(response.success())
        except Exception as exc:
            logger.warning("Feishu reply failed: {}", exc)
            return False

    def _send_message_sync(self, receive_id_type: str, receive_id: str, msg_type: str, content: str) -> bool:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        try:
            request = (
                CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type(msg_type)
                    .content(content)
                    .build()
                )
                .build()
            )
            response = self._client.im.v1.message.create(request)
            logger.info(
                "Feishu send result: success={} type={} receive_id_type={} receive_id={}",
                bool(response.success()),
                msg_type,
                receive_id_type,
                receive_id,
            )
            return bool(response.success())
        except Exception as exc:
            logger.warning("Feishu send failed: {}", exc)
            return False

    def should_accept_text(self, *, is_group: bool, mentioned: bool) -> bool:
        if not is_group:
            return True
        if self.config.group_policy == "open":
            return True
        return mentioned

    def extract_text(self, raw_content: str, msg_type: str = "text") -> str:
        try:
            payload = json.loads(raw_content)
        except json.JSONDecodeError:
            return raw_content

        if msg_type == "text":
            return str(payload.get("text", raw_content))
        if msg_type == "post":
            return extract_post_content(payload)
        if msg_type in ("share_chat", "share_user", "interactive", "share_calendar_event", "system", "merge_forward"):
            return extract_share_card_content(payload, msg_type)
        return MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")

    def seen_message(self, message_id: str) -> bool:
        if message_id in self._processed_message_ids:
            return True
        self._processed_message_ids[message_id] = time.monotonic()
        while len(self._processed_message_ids) > 2048:
            self._processed_message_ids.popitem(last=False)
        return False

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        if not self._client or not message_id:
            return

        from lark_oapi.api.im.v1 import CreateMessageReactionRequest, CreateMessageReactionRequestBody, Emoji

        def _sync() -> None:
            try:
                request = (
                    CreateMessageReactionRequest.builder()
                    .message_id(message_id)
                    .request_body(
                        CreateMessageReactionRequestBody.builder()
                        .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                        .build()
                    )
                    .build()
                )
                self._client.im.v1.message_reaction.create(request)
            except Exception as exc:
                logger.debug("Feishu add reaction failed: {}", exc)

        await asyncio.get_running_loop().run_in_executor(None, _sync)

    async def handle_text_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        *,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        allow_from = getattr(self.config, "allow_from", [])
        if allow_from and "*" not in allow_from and str(sender_id) not in allow_from:
            return
        if message_id:
            await self._add_reaction(message_id, self.config.react_emoji)
        reply_target = chat_id
        if (metadata or {}).get("chat_type") == "p2p":
            reply_target = str(sender_id)
        logger.info(
            "Feishu inbound accepted: sender_id={} chat_id={} reply_target={} message_id={} chat_type={}",
            sender_id,
            chat_id,
            reply_target,
            message_id or "",
            (metadata or {}).get("chat_type", ""),
        )
        if self._on_message is not None:
            await self._on_message(
                InboundMessage(
                    channel=self.name,
                    sender_id=str(sender_id),
                    chat_id=str(reply_target),
                    content=content,
                    metadata=metadata or {},
                )
            )

    def _on_message_sync(self, data: Any) -> None:
        try:
            if self._main_loop is None or not self._main_loop.is_running():
                logger.warning("Feishu: cannot process message - main event loop not available")
                return
            event = getattr(data, "event", None)
            message = getattr(event, "message", None)
            sender = getattr(event, "sender", None)
            sender_id = getattr(getattr(sender, "sender_id", None), "open_id", "") or ""
            chat_id = getattr(message, "chat_id", "") or ""
            message_id = getattr(message, "message_id", "") or ""
            if message_id and self.seen_message(message_id):
                return
            msg_type = getattr(message, "message_type", "text") or "text"
            raw_content = getattr(message, "content", "") or ""
            content = self.extract_text(raw_content, msg_type)
            is_group = getattr(message, "chat_type", "") != "p2p"
            mentioned = "@_all" in raw_content or "@" in content
            if not self.should_accept_text(is_group=is_group, mentioned=mentioned):
                return
            if self._on_message is not None and self._main_loop is not None:
                future = asyncio.run_coroutine_threadsafe(
                    self.handle_text_message(
                        sender_id=sender_id,
                        chat_id=chat_id,
                        content=content,
                        message_id=message_id or None,
                        metadata={
                            "message_id": message_id,
                            "msg_type": msg_type,
                            "chat_type": getattr(message, "chat_type", ""),
                        },
                    ),
                    self._main_loop,
                )
                future.add_done_callback(self._handle_future_result)
        except Exception as exc:
            logger.warning("Feishu message handling failed: {}", exc)

    @staticmethod
    def _handle_future_result(future: Any) -> None:
        try:
            future.result()
        except Exception as exc:
            logger.warning("Feishu scheduled message handler failed: {}", exc)

    @classmethod
    def _detect_msg_format(cls, content: str) -> str:
        stripped = content.strip()
        if cls._COMPLEX_MD_RE.search(stripped):
            return "interactive"
        if len(stripped) > cls._POST_MAX_LEN:
            return "interactive"
        if cls._SIMPLE_MD_RE.search(stripped):
            return "interactive"
        if cls._LIST_RE.search(stripped) or cls._OLIST_RE.search(stripped):
            return "interactive"
        if cls._MD_LINK_RE.search(stripped):
            return "post"
        if len(stripped) <= cls._TEXT_MAX_LEN:
            return "text"
        return "post"

    @classmethod
    def _markdown_to_post(cls, content: str) -> str:
        lines = content.strip().split("\n")
        paragraphs: list[list[dict[str, str]]] = []

        for line in lines:
            elements: list[dict[str, str]] = []
            last_end = 0
            for match in cls._MD_LINK_RE.finditer(line):
                before = line[last_end:match.start()]
                if before:
                    elements.append({"tag": "text", "text": before})
                elements.append({"tag": "a", "text": match.group(1), "href": match.group(2)})
                last_end = match.end()
            remaining = line[last_end:]
            if remaining:
                elements.append({"tag": "text", "text": remaining})
            if not elements:
                elements.append({"tag": "text", "text": ""})
            paragraphs.append(elements)

        return json.dumps({"zh_cn": {"content": paragraphs}}, ensure_ascii=False)
