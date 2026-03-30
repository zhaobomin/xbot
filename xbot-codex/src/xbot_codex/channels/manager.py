from __future__ import annotations

import asyncio

from loguru import logger

from xbot_codex.channels.base import BaseChannel
from xbot_codex.channels.feishu import FeishuChannel
from xbot_codex.channels.telegram import TelegramChannel
from xbot_codex.config import ChannelsConfig
from xbot_codex.events import InboundMessage
from xbot_codex.events import OutboundMessage


class ChannelManager:
    def __init__(self, config: ChannelsConfig, on_message=None):
        self.channels: dict[str, BaseChannel] = {}
        if config.telegram.enabled:
            self.channels["telegram"] = TelegramChannel(config.telegram, on_message=on_message)
        if config.feishu.enabled:
            self.channels["feishu"] = FeishuChannel(config.feishu, on_message=on_message)

    async def start_all(self) -> None:
        for channel in self.channels.values():
            await channel.start()

    async def stop_all(self) -> None:
        for channel in self.channels.values():
            await channel.stop()

    async def dispatch(self, msg: OutboundMessage) -> None:
        channel = self.channels.get(msg.channel)
        if channel is None:
            return
        await channel.send(msg)

    async def dispatch_loop(self, queue: asyncio.Queue[OutboundMessage]) -> None:
        while True:
            msg = await queue.get()
            try:
                await self.dispatch(msg)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("xbot-codex outbound dispatch failed")
