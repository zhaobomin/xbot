"""Unified router-backed agent runtime."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from loguru import logger

from xbot.agent.capabilities import canonical_tool_name
from xbot.agent.protocol import AgentContext
from xbot.agent.router import AgentRouter, register_default_backends
from xbot.bus.events import InboundMessage, OutboundMessage


class AgentRuntime:
    """Single runtime entrypoint for gateway and CLI."""

    def __init__(self, config: Any, shared_resources: dict[str, Any]):
        register_default_backends()
        self.config = config
        self.shared_resources = dict(shared_resources)
        self.bus = self.shared_resources.get("bus")
        self.router = AgentRouter(config.agents, self.shared_resources)
        self.model = config.agents.defaults.model
        self.channels_config = config.channels
        self._running = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}

    @property
    def backend(self):
        return self.router.backend

    @property
    def tools(self):
        backend = self.router._backend
        if backend is None:
            return None
        return getattr(backend, "tools", None)

    async def initialize(self) -> None:
        await self.router.initialize()

    async def run(self) -> None:
        if self.bus is None:
            raise RuntimeError("AgentRuntime requires a bus for run()")

        self._running = True
        await self.initialize()
        logger.info("Agent runtime started with backend {}", self.router.backend_type)

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}", e)
                continue

            if msg.content.strip().lower() in {"/help", "/restart", "/stop", "/new"}:
                response = await self._handle_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                continue

            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(msg.session_key, []).append(task)
            task.add_done_callback(
                lambda t, k=msg.session_key: self._active_tasks.get(k, [])
                and self._active_tasks[k].remove(t)
                if t in self._active_tasks.get(k, [])
                else None
            )

    async def _dispatch(self, msg: InboundMessage) -> None:
        try:
            response = await self._handle_message(msg, on_progress=self._bus_progress(msg))
            if response is not None and self.bus is not None:
                await self.bus.publish_outbound(response)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error processing message for session {}", msg.session_key)
            if self.bus is not None:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="Sorry, I encountered an error.",
                    )
                )

    async def _handle_message(self, msg: InboundMessage, on_progress=None) -> OutboundMessage | None:
        cmd = msg.content.strip().lower()
        if cmd == "/help":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self._help_text(),
                metadata=msg.metadata or {},
            )
        if cmd == "/restart":
            asyncio.create_task(self._do_restart())
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="Restarting...")
        if cmd == "/stop":
            await self.initialize()
            tasks = self._active_tasks.pop(msg.session_key, [])
            cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            backend_cancelled = await self.router.backend.cancel_session(msg.session_key)
            parts = []
            if cancelled:
                parts.append(f"{cancelled} task(s)")
            if backend_cancelled:
                parts.append(f"{backend_cancelled} subagent(s)")
            content = f"Stopped {' and '.join(parts)}." if parts else "No active task to stop."
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)
        if cmd == "/new":
            await self.initialize()
            await self.router.backend.reset_session(msg.session_key)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="New session started.")

        context = AgentContext(
            session_key=msg.session_key,
            prompt=msg.content,
            media=msg.media or None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            metadata=msg.metadata or {},
        )

        final = ""
        async for response in self.router.process(context):
            if on_progress and response.progress_texts:
                for text in response.progress_texts:
                    if text:
                        await on_progress(text)
            if on_progress and response.is_delta and response.delta_content:
                await on_progress(response.delta_content)
            if on_progress and response.tool_calls:
                await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)
            if response.is_delta:
                final += response.delta_content
            else:
                final = response.content or final

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final,
            metadata=msg.metadata or {},
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress=None,
    ) -> str:
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            session_key_override=session_key,
        )
        if content.strip().lower() not in {"/help", "/restart", "/stop", "/new"}:
            await self.initialize()
        response = await self._handle_message(msg, on_progress=on_progress)
        return response.content if response else ""

    def _bus_progress(self, msg: InboundMessage):
        async def _publish(content: str, *, tool_hint: bool = False) -> None:
            if self.bus is None:
                return
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        return _publish

    async def close_mcp(self) -> None:
        await self.router.shutdown()

    def stop(self) -> None:
        self._running = False

    @staticmethod
    def _help_text() -> str:
        return "\n".join(
            [
                "🐈 xbot commands:",
                "/new — Start a new conversation",
                "/stop — Stop the current task",
                "/restart — Restart the bot",
                "/help — Show available commands",
            ]
        )

    @staticmethod
    def _tool_hint(tool_calls: list[dict[str, Any]]) -> str:
        def _kind_label(kind: str) -> str:
            return {
                "tool": "Tool",
                "skill": "Skill",
                "mcp": "MCP",
            }.get(kind, "Tool")

        def _infer_kind(tc: dict[str, Any]) -> str:
            if kind := tc.get("kind"):
                return str(kind)
            name = canonical_tool_name(str(tc.get("name", "")))
            builtin = {
                "read_file",
                "write_file",
                "edit_file",
                "list_dir",
                "exec",
                "web_search",
                "web_fetch",
                "message",
                "spawn",
                "cron",
            }
            if name.startswith("mcp_"):
                return "mcp"
            if name.startswith("skill_"):
                return "skill"
            if name in builtin:
                return "tool"
            return "tool"

        def _fmt(tc: dict[str, Any]) -> str:
            args = tc.get("input") or tc.get("arguments") or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            name = str(tc.get("name", "tool"))
            prefix = f"{_kind_label(_infer_kind(tc))}: "
            if not isinstance(val, str):
                return prefix + name
            body = f'{name}("{val[:40]}…")' if len(val) > 40 else f'{name}("{val}")'
            return prefix + body

        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _do_restart(self) -> None:
        await asyncio.sleep(1)
        os.execv(sys.executable, [sys.executable, "-m", "xbot"] + sys.argv[1:])
