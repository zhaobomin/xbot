from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from xbot_codex.channels.base import BaseChannel
from xbot_codex.events import InboundMessage
from xbot_codex.events import OutboundMessage

TELEGRAM_MAX_MESSAGE_LEN = 4000


def split_telegram_text(text: str) -> list[str]:
    if len(text) <= TELEGRAM_MAX_MESSAGE_LEN:
        return [text]
    return [
        text[i:i + TELEGRAM_MAX_MESSAGE_LEN]
        for i in range(0, len(text), TELEGRAM_MAX_MESSAGE_LEN)
    ]


class TelegramChannel(BaseChannel):
    name = "telegram"

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
        self._app: Application | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        if not getattr(self.config, "token", ""):
            logger.warning("Telegram token not configured")
            return
        self._app = Application.builder().token(self.config.token).build()
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_update))
        await self._app.initialize()
        await self._app.start()
        if self._app.updater is not None:
            await self._app.updater.start_polling()

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._app is not None:
            if self._app.updater is not None:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None

    async def send(self, msg: OutboundMessage) -> None:
        if self._send_impl is not None:
            await self._send_impl(msg)
            return
        if self._app is None:
            return
        for part in split_telegram_text(msg.content):
            await self._app.bot.send_message(chat_id=msg.chat_id, text=part)

    def should_accept_text(self, *, chat_type: str, text: str) -> bool:
        if chat_type == "private":
            return True
        if self.config.group_policy == "open":
            return True
        return "@" in text

    async def handle_text_message(self, sender_id: str, chat_id: str, content: str) -> None:
        allow_from = getattr(self.config, "allow_from", [])
        if allow_from and "*" not in allow_from and str(sender_id) not in allow_from:
            return
        if self._on_message is not None:
            await self._on_message(
                InboundMessage(
                    channel=self.name,
                    sender_id=str(sender_id),
                    chat_id=str(chat_id),
                    content=content,
                )
            )

    async def _handle_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if message is None or chat is None or user is None or not message.text:
            return
        if not self.should_accept_text(chat_type=getattr(chat, "type", "private"), text=message.text):
            return
        await self.handle_text_message(
            sender_id=str(user.id),
            chat_id=str(chat.id),
            content=message.text,
        )
