"""Unified router-backed agent runtime."""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from xbot.agent.capabilities import CapabilityCatalog, canonical_tool_name
from xbot.agent.commands import CommandsLoader
from xbot.agent.event_formatter import format_usage_summary
from xbot.agent.protocol import AgentContext
from xbot.agent.router import AgentRouter, register_default_backends
from xbot.agent.trace import append_session_trace
from xbot.bus.events import InboundMessage, OutboundMessage


class AgentRuntime:
    """Single runtime entrypoint for gateway and CLI."""
    LOCAL_RUNTIME_COMMANDS = {"!help", "!restart", "!stop", "/help", "/restart", "/stop"}
    COMMAND_ALIASES: dict[str, str] = {}
    SDK_HELP_FALLBACK_COMMANDS = ["/help", "/clear", "/compact"]

    def __init__(self, config: Any, shared_resources: dict[str, Any]):
        register_default_backends()
        self.config = config
        self.shared_resources = dict(shared_resources)
        self.bus = self.shared_resources.get("bus")
        self.router = AgentRouter(config.agents, self.shared_resources)
        self.sessions = self.shared_resources.get("session_manager")
        self.model = config.agents.defaults.model
        self.channels_config = config.channels
        self.capabilities = CapabilityCatalog(
            self.shared_resources.get("workspace", config.agents.defaults.workspace)
        )
        self.commands = CommandsLoader(
            Path(self.shared_resources.get("workspace", config.agents.defaults.workspace))
        )
        self._running = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}

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
        logger.info("Agent runtime summary: {}", self.describe_runtime())

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}", e)
                continue

            # Check if this is a permission response
            if await self._handle_permission_response(msg):
                continue
            if await self._handle_interaction_response(msg):
                continue

            if self._is_local_runtime_command(msg.content):
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

    async def _handle_permission_response(self, msg: InboundMessage) -> bool:
        """Check if the message is a permission response and handle it.

        Returns:
            True if the message was handled as a permission response, False otherwise
        """
        if self.bus is None:
            return False

        # Check if there's a pending permission request for this session
        request_id = self.bus.get_pending_request_for_session(msg.session_key)
        if not request_id:
            return False

        # Parse the user's response
        content = msg.content.strip().lower()
        decision = None
        reason = ""

        # Allow variations: "允许", "allow", "yes", "y", "是", "ok"
        allow_variations = {"允许", "allow", "yes", "y", "是", "ok", "同意", "确认"}
        # Deny variations: "拒绝", "deny", "no", "n", "否"
        deny_variations = {"拒绝", "deny", "no", "n", "否", "取消"}

        if content in allow_variations:
            decision = "allow"
        elif content in deny_variations:
            decision = "deny"
            reason = "User denied"
        else:
            # Not a clear permission response, treat as normal message
            return False

        # Submit the response
        from xbot.bus.queue import PermissionResponse
        response = PermissionResponse(
            request_id=request_id,
            session_key=msg.session_key,
            decision=decision,
            reason=reason,
        )
        submitted = await self.bus.submit_permission_response(response)
        if not submitted:
            logger.warning(f"Permission response no longer pending: request={request_id}")
            return False
        logger.info(f"Permission response submitted: {decision} for request {request_id}")
        return True

    async def _dispatch(self, msg: InboundMessage) -> None:
        lock = self._session_locks.setdefault(msg.session_key, asyncio.Lock())
        try:
            async with lock:
                response = await self._handle_message(msg, on_progress=self._bus_progress(msg))
            if response is not None and self.bus is not None:
                await self.bus.publish_outbound(response)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error processing message for session {}", msg.session_key)
            append_session_trace(
                self.sessions,
                msg.session_key,
                "error",
                {"backend": self.router.backend_type, "message": "processing_error"},
            )
            if self.bus is not None:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="Sorry, I encountered an error.",
                    )
                )

    async def _handle_interaction_response(self, msg: InboundMessage) -> bool:
        """Handle pending generic interaction replies for a session."""
        if self.bus is None:
            return False

        if self._is_local_runtime_command(msg.content):
            return False

        request_id = self.bus.get_pending_interaction_for_session(msg.session_key)
        if not request_id:
            return False

        req = self.bus.get_interaction_request(request_id)
        if req is None:
            return False

        content = msg.content.strip()
        normalized = content.lower()
        action = "reply"

        if req.kind in {"confirmation", "approval"}:
            allow_variations = {"允许", "allow", "yes", "y", "是", "ok", "同意", "确认"}
            deny_variations = {"拒绝", "deny", "no", "n", "否", "取消"}
            if normalized in allow_variations:
                action = "confirm" if req.kind == "confirmation" else "allow"
            elif normalized in deny_variations:
                action = "cancel" if req.kind == "confirmation" else "deny"
            else:
                return False

        from xbot.bus.queue import InteractionResponse

        submitted = await self.bus.submit_interaction_response(
            InteractionResponse(
                request_id=request_id,
                session_key=msg.session_key,
                action=action,
                content=content,
            )
        )
        if not submitted:
            logger.warning(f"Interaction response no longer pending: request={request_id}")
            return False
        logger.info(f"Interaction response submitted: action={action}, request={request_id}")
        return True

    async def _handle_message(self, msg: InboundMessage, on_progress=None) -> OutboundMessage | None:
        cmd = msg.content.strip().lower()
        if cmd in {"!help", "/help"}:
            await self.initialize()
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=await self._help_text(msg.session_key),
                metadata=msg.metadata or {},
            )
        if cmd in {"!restart", "/restart"}:
            asyncio.create_task(self._do_restart())
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="Restarting...")
        if cmd in {"!stop", "/stop"}:
            await self.initialize()
            tasks = self._active_tasks.pop(msg.session_key, [])
            cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            backend_cancelled = await self.router.backend.cancel_session(msg.session_key)
            backend_task_stopped = await self.router.backend.stop_active_task(msg.session_key)
            interrupted = await self.router.backend.interrupt_session(msg.session_key)
            parts = []
            if cancelled:
                parts.append(f"{cancelled} task(s)")
            if backend_cancelled:
                parts.append(f"{backend_cancelled} subagent(s)")
            if backend_task_stopped:
                parts.append("SDK task")
            if interrupted:
                parts.append("LLM request")
            content = f"🛑 Stopped {' and '.join(parts)}." if parts else "No active task to stop."
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)

        # Check for workspace command
        command_prefix = ""
        cmd_name = self.commands.get_command_from_text(msg.content.strip())
        if cmd_name:
            cmd_content = self.commands.load_command(cmd_name)
            if cmd_content:
                command_prefix = f"[Workspace Command: /{cmd_name}]\n\n{cmd_content}\n\n---\n\n"
                logger.info(f"Loaded workspace command '/{cmd_name}' for session {msg.session_key}")

        normalized_prompt = self._normalize_command_prompt(msg.content)
        # Prepend command content if this is a workspace command
        if command_prefix:
            normalized_prompt = command_prefix + normalized_prompt

        context = AgentContext(
            session_key=msg.session_key,
            prompt=normalized_prompt,
            media=msg.media or None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            metadata=msg.metadata or {},
        )

        append_session_trace(
            self.sessions,
            msg.session_key,
            "request_start",
            {
                "backend": self.router.backend_type,
                "channel": msg.channel,
                "chat_id": msg.chat_id,
                "prompt_preview": msg.content[:120],
            },
        )

        final = ""
        usage: dict[str, Any] | None = None
        async for response in self.router.process(context):
            if response.progress_texts:
                for text in response.progress_texts:
                    if text:
                        append_session_trace(
                            self.sessions,
                            msg.session_key,
                            "progress",
                            {"text": text[:240]},
                        )
                        if on_progress:
                            await self._emit_progress(
                                on_progress,
                                text,
                                event_type=response.event_type or "progress",
                                event_data=response.event_data,
                            )
            if response.tool_hint_text:
                append_session_trace(
                    self.sessions,
                    msg.session_key,
                    "tool_hint",
                    {"text": response.tool_hint_text[:240]},
                )
                if on_progress:
                    await self._emit_progress(
                        on_progress,
                        response.tool_hint_text,
                        tool_hint=True,
                        event_type="tool_hint",
                    )
            if on_progress and response.is_delta and response.delta_content:
                await self._emit_progress(
                    on_progress,
                    response.delta_content,
                    event_type=response.event_type or "content_delta",
                    event_data=response.event_data,
                )
            if response.tool_calls:
                tool_hint = self._tool_hint(response.tool_calls, self.capabilities)
                append_session_trace(
                    self.sessions,
                    msg.session_key,
                    "tool_hint",
                    {"text": tool_hint[:240]},
                )
                if on_progress:
                    await self._emit_progress(
                        on_progress,
                        tool_hint,
                        tool_hint=True,
                        event_type="tool_call",
                        event_data={"tool_calls": response.tool_calls},
                    )
            if response.is_delta:
                final += response.delta_content
            else:
                final = response.content or final
            if response.usage:
                usage = response.usage

        usage_text = format_usage_summary(usage)
        if usage_text and on_progress and self._should_send_usage_summary():
            await self._emit_progress(
                on_progress,
                usage_text,
                event_type="usage",
                event_data={"usage": usage},
            )

        append_session_trace(
            self.sessions,
            msg.session_key,
            "response_complete",
            {
                "backend": self.router.backend_type,
                "content_preview": final[:240],
            },
        )

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final,
            metadata=msg.metadata or {},
        )

    def _should_send_usage_summary(self) -> bool:
        ch = self.channels_config
        if ch is None:
            return True
        return bool(getattr(ch, "send_usage_summary", True))

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
        if not self._is_local_runtime_command(content):
            await self.initialize()
        response = await self._handle_message(msg, on_progress=on_progress)
        return response.content if response else ""

    @classmethod
    def _is_local_runtime_command(cls, content: str) -> bool:
        return content.strip().lower() in cls.LOCAL_RUNTIME_COMMANDS

    @classmethod
    def _normalize_command_prompt(cls, content: str) -> str:
        stripped = content.strip()
        alias = cls.COMMAND_ALIASES.get(stripped.lower())
        return alias if alias else content

    def _bus_progress(self, msg: InboundMessage):
        async def _publish(
            content: str,
            *,
            tool_hint: bool = False,
            event_type: str = "progress",
            event_data: dict[str, Any] | None = None,
        ) -> None:
            if self.bus is None:
                return
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            meta["_event_type"] = event_type
            meta["_progress_kind"] = self._progress_kind_from_event_type(event_type, tool_hint=tool_hint)
            if event_data is not None:
                meta["_event_data"] = event_data
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        return _publish

    async def _emit_progress(
        self,
        on_progress,
        text: str,
        *,
        tool_hint: bool = False,
        event_type: str = "progress",
        event_data: dict[str, Any] | None = None,
    ) -> None:
        if self._supports_extended_progress_callback(on_progress):
            await on_progress(
                text,
                tool_hint=tool_hint,
                event_type=event_type,
                event_data=event_data,
            )
        else:
            await on_progress(text, tool_hint=tool_hint)

    @staticmethod
    def _supports_extended_progress_callback(on_progress) -> bool:
        try:
            signature = inspect.signature(on_progress)
        except (TypeError, ValueError):
            return False
        for param in signature.parameters.values():
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                return True
        return "event_type" in signature.parameters and "event_data" in signature.parameters

    @staticmethod
    def _progress_kind_from_event_type(event_type: str, *, tool_hint: bool = False) -> str:
        if tool_hint:
            return "tool"
        return {
            "thinking": "reasoning",
            "tool_call": "tool",
            "tool_hint": "tool",
            "task": "task",
            "system": "system",
            "usage": "usage",
            "content_delta": "content",
            "result": "result",
        }.get(event_type, "progress")

    async def close_mcp(self) -> None:
        await self.router.shutdown()

    def stop(self) -> None:
        self._running = False

    def describe_runtime(self) -> str:
        backend = self.router._backend
        backend_summary = ""
        if backend is not None and hasattr(backend, "get_tools_summary"):
            backend_summary = backend.get_tools_summary()
        return (
            f"backend={self.router.backend_type} | "
            f"workspace={self.shared_resources.get('workspace', self.config.agents.defaults.workspace)}"
            + (f" | {backend_summary}" if backend_summary else "")
        )

    async def _help_text(self, session_key: str) -> str:
        sdk_commands = sorted(set(await self.router.backend.get_session_commands(session_key)))
        if not sdk_commands:
            sdk_commands = list(self.SDK_HELP_FALLBACK_COMMANDS)

        # Get workspace commands
        workspace_commands = self.commands.list_commands()
        workspace_commands_lines = []
        for cmd in workspace_commands:
            desc = f" — {cmd['description']}" if cmd["description"] else ""
            workspace_commands_lines.append(f"/{cmd['name']}{desc}")

        lines = [
            "🐈 xbot command reference:",
            "",
            "Runtime controls:",
            "!help or /help — Show available commands",
            "!stop or /stop — Stop the current task",
            "!restart or /restart — Restart the bot process",
        ]

        # Add workspace commands if any
        if workspace_commands_lines:
            lines.append("")
            lines.append("Workspace commands:")
            lines.extend(workspace_commands_lines)

        # Add SDK commands
        lines.append("")
        lines.append("Claude SDK slash commands:")
        lines.extend(sdk_commands)

        return "\n".join(lines)

    @staticmethod
    def _tool_hint(
        tool_calls: list[dict[str, Any]],
        capabilities: CapabilityCatalog | None = None,
    ) -> str:
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
            if capabilities is not None:
                return capabilities.classify_tool_name(name)
            if name.startswith("mcp_"):
                return "mcp"
            if name.startswith("skill_"):
                return "skill"
            if name in CapabilityCatalog.builtin_tool_names():
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
