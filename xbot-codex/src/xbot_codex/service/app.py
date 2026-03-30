from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from xbot_codex.bus import MessageBus
from xbot_codex.channels.manager import ChannelManager
from xbot_codex.config import ServiceConfig
from xbot_codex.events import OutboundMessage
from xbot_codex.runtime import CodexRuntime


class CodexService:
    def __init__(
        self,
        config: ServiceConfig,
        runtime: CodexRuntime,
        bus: MessageBus | None = None,
        channel_manager: ChannelManager | None = None,
    ):
        self.config = config
        self.runtime = runtime
        self.bus = bus or MessageBus()
        self.channel_manager = channel_manager
        self._tasks: list[asyncio.Task] = []
        self._session_tasks: dict[str, asyncio.Task] = {}

    async def _process_inbound_message(self, inbound) -> None:
        logger.info(
            "Service inbound: channel={} chat_id={} sender_id={} content={!r}",
            inbound.channel,
            inbound.chat_id,
            inbound.sender_id,
            inbound.content[:120],
        )
        async for outbound in self.runtime.handle_message(inbound):
            logger.info(
                "Service outbound queued: channel={} chat_id={} event_type={} chars={}",
                outbound.channel,
                outbound.chat_id,
                outbound.metadata.get("event_type", ""),
                len(outbound.content or ""),
            )
            await self.bus.publish_outbound(outbound)

    async def _publish_busy(self, inbound) -> None:
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                content="Codex is still working on your previous message. Please wait or send !stop.",
                metadata={
                    "event_type": "busy",
                    "message_id": inbound.metadata.get("message_id"),
                    "chat_type": inbound.metadata.get("chat_type"),
                },
            )
        )

    async def process_next_message(self) -> None:
        inbound = await self.bus.consume_inbound()
        existing = self._session_tasks.get(inbound.session_key)
        if existing is not None and not existing.done():
            await self._publish_busy(inbound)
            return

        task = asyncio.create_task(self._process_inbound_message(inbound))
        self._session_tasks[inbound.session_key] = task

        def _cleanup(done_task: asyncio.Task, session_key: str = inbound.session_key) -> None:
            current = self._session_tasks.get(session_key)
            if current is done_task:
                self._session_tasks.pop(session_key, None)
            try:
                done_task.result()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("xbot-codex session task failed: session_key={}", session_key)

        task.add_done_callback(_cleanup)

    async def _inbound_loop(self) -> None:
        while True:
            try:
                await self.process_next_message()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("xbot-codex inbound loop failed")

    async def publish_many(self, messages: list[OutboundMessage]) -> None:
        for message in messages:
            await self.bus.publish_outbound(message)

    async def start(self) -> None:
        self._bootstrap_codex_home()
        if self.channel_manager is not None:
            await self.channel_manager.start_all()
            self._tasks.append(asyncio.create_task(self.channel_manager.dispatch_loop(self.bus.outbound)))
        self._tasks.append(asyncio.create_task(self._inbound_loop()))

    async def shutdown(self) -> None:
        session_store = getattr(self.runtime, "session_store", None)
        transport = getattr(self.runtime, "transport", None)
        if session_store is not None and transport is not None:
            for session_key in session_store.active_session_keys():
                await transport.interrupt(session_key)
        for task in list(self._session_tasks.values()):
            if not task.done():
                task.cancel()
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._session_tasks.values(), return_exceptions=True)
        self._session_tasks.clear()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.channel_manager is not None:
            await self.channel_manager.stop_all()
        close = getattr(transport, "close", None)
        if close is not None:
            await close()

    def _bootstrap_codex_home(self) -> None:
        transport = getattr(self.runtime, "transport", None)
        transport_env = getattr(transport, "env", {}) or {}
        codex_home = self.config.codex.home or transport_env.get("CODEX_HOME") or transport_env.get("HOME")
        if not codex_home:
            return
        home_path = Path(codex_home)
        home_path.mkdir(parents=True, exist_ok=True)
        config_path = home_path / "config.toml"
        config_path.write_text(self._render_codex_config(), encoding="utf-8")

    def _render_codex_config(self) -> str:
        lines = [
            f'model = "{self.config.codex.default_model or "gpt-5.4"}"',
            'model_reasoning_effort = "medium"',
            'personality = "pragmatic"',
            'sandbox_mode = "workspace-write"',
            "",
            f'[projects."{Path.cwd()}"]',
            'trust_level = "trusted"',
            "",
            "[sandbox_workspace_write]",
            "network_access = true",
            "",
        ]
        return "\n".join(lines)
