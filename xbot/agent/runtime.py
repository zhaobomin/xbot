"""Unified router-backed agent runtime."""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine, TypeAlias

from loguru import logger

if TYPE_CHECKING:
    from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
    from xbot.agent.tools.base import ToolRegistry

from xbot.agent.capabilities.catalog import CapabilityCatalog, canonical_tool_name
from xbot.agent.context.commands import CommandsLoader
from xbot.agent.interaction.event_formatter import format_usage_summary
from xbot.agent.context.model_manager import ModelManager
from xbot.agent.protocol import AgentContext
from xbot.agent.interaction.response_handlers import RuntimeResponseHandlers
from xbot.agent.router import AgentRouter, register_default_backends
from xbot.agent.state.store import SessionStore
from xbot.agent.state.checker import StateConsistencyChecker
from xbot.agent.state.coordinator import SessionStateCoordinator
from xbot.agent.state.machine import (
    SessionPhase,
    SessionState,
    SessionStateMachine,
    VALID_TRANSITIONS,
)
from xbot.agent.monitoring.trace import append_session_trace
from xbot.bus.events import InboundMessage, OutboundMessage


# Type alias for progress callback
ProgressCallback: TypeAlias = Callable[
    [str],
    Coroutine[None, None, None],
]


# Re-export for backward compatibility
__all__ = [
    "SessionPhase",
    "SessionState",
    "SessionStateMachine",
    "VALID_TRANSITIONS",
    "AgentRuntime",
]


class AgentRuntime:
    """Single runtime entrypoint for gateway and CLI."""
    # Only intercept ! commands - all / commands go to SDK
    LOCAL_RUNTIME_COMMANDS = {
        "!help", "!restart", "!stop", "!reset", "!state", "!coord", "!ver",
    }
    # 前缀匹配的命令（支持参数）
    LOCAL_RUNTIME_COMMAND_PREFIXES = ("!model",)
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
        self.model_manager = ModelManager(config)  # 模型管理器
        self.shared_resources["runtime"] = self  # 让 backend 可以访问 runtime.model_manager
        self.channels_config = config.channels
        self.capabilities = CapabilityCatalog(
            self.shared_resources.get("workspace", config.agents.defaults.workspace)
        )
        self.commands = CommandsLoader(
            Path(self.shared_resources.get("workspace", config.agents.defaults.workspace))
        )
        self._running = False

        # Use state machine for session state management
        self._state_machine = SessionStateMachine(
            on_transition=self._on_state_transition
        )

        # Session store for unified task and lock management
        self._session_store = SessionStore()
        self.shared_resources["session_store"] = self._session_store  # 让 backend 可以访问

        # State consistency checker (for debugging and monitoring)
        self._state_checker = StateConsistencyChecker(self)
        self._state_check_enabled = True  # Feature flag for state checking

        # Session state coordinator (unified state management)
        self._state_coordinator = SessionStateCoordinator(self, self._session_store)
        self._response_handlers = RuntimeResponseHandlers(self)

        # Retry count tracking for interaction responses (max 3 retries for invalid answers)
        self._interaction_retry_counts: dict[str, int] = {}

        # Register backend state sync callbacks
        self.shared_resources["on_backend_client_cleanup"] = self._on_backend_client_cleanup

    @property
    def backend(self) -> "ClaudeSDKBackend | None":
        return self.router.backend

    @property
    def tools(self) -> "ToolRegistry | None":
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

            # Dispatch message with atomic state management
            # IMPORTANT: Set phase to RUNNING BEFORE creating task to avoid race condition.
            # This ensures state is consistent when task is registered (fixes "IDLE but has active tasks" warning).
            self._state_coordinator.force_transition(
                msg.session_key, SessionPhase.RUNNING, reason="dispatch_start"
            )
            task = asyncio.create_task(self._dispatch(msg))
            AgentRuntime._tag_task_for_session(task, msg.session_key)
            self._state_coordinator.register_task(msg.session_key, task)
            task.add_done_callback(self._make_task_done_callback(msg.session_key))

    async def _handle_permission_response(self, msg: InboundMessage) -> bool:
        """Delegate permission-response handling."""
        handler = None
        try:
            handler = self._response_handlers
        except AttributeError:
            handler = None
        if handler is None:
            handler = RuntimeResponseHandlers(self)
        return await handler.handle_permission_response(msg)

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Dispatch message with transaction-based state management.

        Uses coordinator transactions for atomic state changes.
        """
        # Note: Task registration happens in run() when creating the task.
        # When _dispatch is called directly (tests), there's no task registration.

        # === 诊断日志: 请求入口 ===
        current_phase = self._state_coordinator.get_phase(msg.session_key)
        has_pending_permission = False
        has_pending_interaction = False
        if self.bus is not None:
            has_pending_permission = bool(
                self.bus.get_pending_request_for_session(msg.session_key)
            )
            has_pending_interaction = bool(
                self.bus.get_pending_interaction_for_session(msg.session_key)
            )
        prompt_preview = msg.content[:50].replace('\n', ' ') if msg.content else ""
        logger.info(
            f"[Dispatch] session={msg.session_key}, phase={current_phase.value}, "
            f"pending_i={has_pending_interaction}, pending_p={has_pending_permission}, "
            f'prompt="{prompt_preview}..."'
        )

        try:
            # Start atomic dispatch session
            async with self._state_coordinator.transaction(
                msg.session_key, validate_on_commit=False
            ) as tx:
                tx.set_phase(SessionPhase.RUNNING, reason="dispatch_start")
                tx.acquire_lock()

            # Log state snapshot after state transition (avoids false "IDLE but has active tasks" warning)
            self._log_state_snapshot(msg.session_key, "dispatch_start")

            # Get the lock via coordinator
            lock = self._state_coordinator.get_lock_object(msg.session_key)

            # Execute message handling
            async with lock:
                response = await self._handle_message(
                    msg, on_progress=self._bus_progress(msg)
                )

            if response is not None and self.bus is not None:
                await self.bus.publish_outbound(response)

        except asyncio.CancelledError:
            raise
        except Exception:
            # Atomic error handling with transaction
            async with self._state_coordinator.transaction(
                msg.session_key, validate_on_commit=False
            ) as tx:
                tx.set_phase(SessionPhase.ERROR, reason="dispatch_error")

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
        finally:
            # Check current state - don't override protected states
            # ERROR: should be explicitly cleared
            # STOPPING/RESETTING: in progress by _terminate_session
            current_phase = self._state_coordinator.get_phase(msg.session_key)
            protected_phases = {
                SessionPhase.ERROR,
                SessionPhase.STOPPING,
                SessionPhase.RESETTING,
            }
            if current_phase in protected_phases:
                # Just log state snapshot and return
                self._log_state_snapshot(msg.session_key, "dispatch_end")
                return

            # Sync phase based on pending requests
            has_pending_permission = False
            has_pending_interaction = False
            if self.bus is not None:
                has_pending_permission = bool(
                    self.bus.get_pending_request_for_session(msg.session_key)
                )
                has_pending_interaction = bool(
                    self.bus.get_pending_interaction_for_session(msg.session_key)
                )

            if has_pending_permission:
                async with self._state_coordinator.transaction(
                    msg.session_key, validate_on_commit=False
                ) as tx:
                    tx.set_phase(SessionPhase.WAITING_PERMISSION, reason="pending_permission")
            elif has_pending_interaction:
                async with self._state_coordinator.transaction(
                    msg.session_key, validate_on_commit=False
                ) as tx:
                    tx.set_phase(SessionPhase.WAITING_INTERACTION, reason="pending_interaction")
            else:
                # Check if this _dispatch was called from run() (task registered)
                # or directly (no task registered)
                current_task = asyncio.current_task()
                registered_tasks = self._state_coordinator.get_active_tasks(msg.session_key)

                # If current task is registered, callback will handle cleanup
                # If not registered (or no current task), this is a direct call
                is_registered_task = (
                    current_task is not None and current_task in registered_tasks
                )

                if not is_registered_task:
                    # Direct call - set IDLE now (no callback will run)
                    async with self._state_coordinator.transaction(
                        msg.session_key, validate_on_commit=False
                    ) as tx:
                        tx.set_phase(SessionPhase.IDLE, reason="dispatch_end")
                # else: callback will set IDLE after task completes

            # Log state snapshot at dispatch end
            self._log_state_snapshot(msg.session_key, "dispatch_end")

    async def _handle_interaction_response(self, msg: InboundMessage) -> bool:
        """Delegate interaction-response handling."""
        handler = None
        try:
            handler = self._response_handlers
        except AttributeError:
            handler = None
        if handler is None:
            handler = RuntimeResponseHandlers(self)

        # Get retry count for this session (handler manages the count internally)
        retry_count = getattr(self, '_interaction_retry_counts', {}).get(msg.session_key, 0)
        return await handler.handle_interaction_response(msg, retry_count=retry_count)

    async def _handle_message(
        self,
        msg: InboundMessage,
        on_progress: Callable[..., Coroutine[None, None, None]] | None = None,
    ) -> OutboundMessage | None:
        cmd = msg.content.strip().lower()

        # Handle local slash aliases without forwarding to the SDK.
        if cmd in ("/clear", "/new", "/reset"):
            logger.info(f"[Local Command] Clearing session locally: {cmd!r} (session={msg.session_key})")
            await self._do_clear_session(msg.session_key)
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="♻️ Session cleared. Starting fresh!\n📌 Use `!stop` to stop tasks without clearing context.",
                metadata=msg.metadata or {},
            )
        if cmd == "/help":
            await self.initialize()
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=await self._help_text(msg.session_key),
                metadata=msg.metadata or {},
            )
        if cmd == "/state":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self._session_diagnostics_text(msg.session_key),
                metadata=msg.metadata or {},
            )
        if cmd == "/restart":
            self._spawn_background_task(self._do_restart(), "restart")
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="Restarting...")

        # Debug: Log slash commands going to SDK
        if cmd.startswith("/"):
            logger.info(f"[Slash Command] Forwarding to SDK: {cmd!r} (session={msg.session_key})")

        # Only handle ! commands locally - other / commands go to SDK
        if cmd == "!ver":
            from xbot import version_text
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=version_text(),
                metadata=msg.metadata or {},
            )
        if cmd == "!help":
            await self.initialize()
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=await self._help_text(msg.session_key),
                metadata=msg.metadata or {},
            )
        if cmd == "!restart":
            self._spawn_background_task(self._do_restart(), "restart")
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="Restarting...")
        if cmd == "!stop":
            # !stop: 停止当前任务，保留上下文
            await self.initialize()
            state = await self._terminate_session(msg.session_key, hard_reset=False)

            parts = []
            if state["cancelled"]:
                parts.append(f"{state['cancelled']} task(s)")
            if state["backend_cancelled"]:
                parts.append(f"{state['backend_cancelled']} background task(s)")
            if state["backend_task_stopped"]:
                parts.append("SDK task")
            if state["interrupted"]:
                parts.append("LLM request")
            if state["cleared_requests"].get("permission"):
                parts.append("pending permission")
            if state["cleared_requests"].get("interaction"):
                parts.append("pending interaction")

            # Build response message
            content_parts = []
            if parts:
                content_parts.append(f"🛑 Stopped {' and '.join(parts)}.")
                content_parts.append("📌 Context preserved. Continue conversation or use `!reset` to clear.")
            else:
                content_parts.append("No active task to stop.")

            # Add usage info if available
            usage = state["usage"]
            if usage:
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                total_tokens = input_tokens + output_tokens
                if total_tokens > 0:
                    content_parts.append(
                        f"\n📊 Session usage: {input_tokens:,} input + {output_tokens:,} output = {total_tokens:,} tokens"
                    )

            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="\n".join(content_parts))
        if cmd == "!reset" or cmd.startswith("!reset "):
            # !reset: 完全重置，删除所有状态和 SDK 文件
            # --soft: 仅清理状态，保留 SDK 上下文
            reset_args = msg.content.strip().lower().split()
            soft_reset = "--soft" in reset_args or "-s" in reset_args

            await self.initialize()
            state = await self._terminate_session(msg.session_key, hard_reset=True)

            parts = ["♻️ Session reset completed."]
            details = []
            if state["cancelled"]:
                details.append(f"{state['cancelled']} runtime task(s)")
            if state["backend_cancelled"]:
                details.append(f"{state['backend_cancelled']} background task(s)")
            if state["backend_task_stopped"]:
                details.append("SDK task")
            if state["interrupted"]:
                details.append("LLM request")
            if state["cleared_requests"].get("permission"):
                details.append("pending permission")
            if state["cleared_requests"].get("interaction"):
                details.append("pending interaction")
            if details:
                parts.append(f"Cleared: {', '.join(details)}.")

            # Default: delete SDK session file (unless --soft)
            if not soft_reset:
                if hasattr(self.router.backend, "delete_sdk_session"):
                    delete_result = await self.router.backend.delete_sdk_session(msg.session_key)
                    if delete_result["deleted"]:
                        parts.append("🗑️ SDK context deleted. Fresh start!")
                    elif delete_result.get("error") and delete_result["error"] != "No SDK session found":
                        parts.append(f"⚠️ SDK delete: {delete_result['error']}")
                else:
                    parts.append("📌 Backend does not expose SDK session deletion; state reset only.")
            else:
                parts.append("📌 SDK context preserved (--soft).")

            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="\n".join(parts))
        if cmd.startswith("!session"):
            return await self._handle_session_command(msg)
        if cmd == "!state":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self._session_diagnostics_text(msg.session_key),
                metadata=msg.metadata or {},
            )
        if cmd == "!coord":
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self._coord_status_text(),
                metadata=msg.metadata or {},
            )

        # 处理 !model 命令
        if cmd.startswith("!model"):
            return self._handle_model_command(msg, msg.content.strip())

        logger.info(f"[Runtime] After !model check, cmd={cmd!r}")

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
        logger.info(f"[Runtime] After request_start trace for session={msg.session_key}")

        final = ""
        usage: dict[str, Any] | None = None
        logger.info(f"[Runtime] Starting router.process for session={msg.session_key}, prompt={normalized_prompt[:50]!r}")
        async for response in self.router.process(context):
            logger.debug(f"[Runtime] Received response from router for session={msg.session_key}")
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

    async def _terminate_session(self, session_key: str, *, hard_reset: bool) -> dict[str, Any]:
        """Cancel runtime/backend activity and clear pending requests for a session.

        Uses coordinator transactions for atomic state changes.
        """
        # Start atomic terminate session
        async with self._state_coordinator.transaction(
            session_key, validate_on_commit=False
        ) as tx:
            tx.set_phase(
                SessionPhase.RESETTING if hard_reset else SessionPhase.STOPPING,
                reason="terminate_session",
            )

        # Initialize result variables
        cancelled = 0
        backend_cancelled = 0
        backend_task_stopped = False
        interrupt_result: dict[str, Any] = {"interrupted": False, "usage": None}
        cleared_requests = {"permission": False, "interaction": False}

        try:
            # Cancel and wait for tasks (outside transaction as it's async I/O)
            tasks = self._state_coordinator.pop_active_tasks(session_key)
            cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            # Extra cleanup: scan for any orphaned tasks that might have been missed
            # This handles edge cases where tasks were created but not properly registered
            all_current_tasks = asyncio.all_tasks()
            orphaned_tasks = []
            for task in all_current_tasks:
                if task in tasks or task.done():
                    continue
                if AgentRuntime._task_belongs_to_session(task, session_key):
                    orphaned_tasks.append(task)

            if orphaned_tasks:
                logger.warning(
                    f"Found {len(orphaned_tasks)} orphaned task(s) for session {session_key}, cancelling..."
                )
                for task in orphaned_tasks:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):
                            pass
                        cancelled += 1

            # Backend cleanup (async I/O)
            backend_cancelled = await self.router.backend.cancel_session(session_key)
            backend_task_stopped = await self.router.backend.stop_active_task(session_key)
            interrupt_result = await self.router.backend.interrupt_session(session_key)
            if hard_reset:
                await self.router.backend.reset_session(session_key)

            # Clear pending requests
            if self.bus is not None:
                if hasattr(self.bus, "aclear_session_requests"):
                    cleared_requests = await self.bus.aclear_session_requests(session_key)
        except asyncio.CancelledError:
            logger.warning(f"terminate_session cancelled for {session_key}, continuing cleanup")
            # Continue to final cleanup even if cancelled
        except Exception as e:
            logger.warning(f"Error during terminate_session cleanup: {e}")
            # Continue to final cleanup even if backend operations fail

        # Atomic cleanup transaction - always run, even if cleanup failed
        async with self._state_coordinator.transaction(
            session_key, validate_on_commit=False
        ) as tx:
            # Release lock (coordinator handles lock cleanup)
            tx.release_lock()

            # Set final phase (always IDLE, even for hard_reset)
            tx.set_phase(SessionPhase.IDLE, reason="terminate_session_completed")

        # For hard_reset, reset state to fresh IDLE (instead of clear which deletes)
        if hard_reset:
            self._state_coordinator.reset_session(session_key)

        return {
            "cancelled": cancelled,
            "backend_cancelled": backend_cancelled,
            "backend_task_stopped": backend_task_stopped,
            "interrupted": bool(interrupt_result.get("interrupted")),
            "usage": interrupt_result.get("usage"),
            "cleared_requests": cleared_requests,
        }

    def _on_state_transition(
        self, session_key: str, from_phase: SessionPhase, to_phase: SessionPhase, reason: str
    ) -> None:
        """Callback for state machine transitions - logs to session trace."""
        append_session_trace(
            self.sessions,
            session_key,
            "session_state",
            {
                "from": from_phase.value,
                "to": to_phase.value,
                "reason": reason,
            },
        )

    def _make_task_done_callback(self, session_key: str) -> Callable[[asyncio.Task], None]:
        """Create a done callback for task cleanup.

        Returns a callback that removes the task from session store when done.
        This avoids the lambda capture issue where the task references itself.
        Also syncs the session phase after task removal to ensure correct state.
        """
        def _on_task_done(task: asyncio.Task) -> None:
            # Unregister via coordinator for accurate stats
            self._state_coordinator.unregister_task(session_key, task)

            # Clean up empty task lists via coordinator
            self._state_coordinator.cleanup_empty_task_list(session_key)

            # Sync phase after task is done to ensure correct state
            # This is critical: _sync_session_phase in _dispatch's finally runs
            # before this task is marked done, so we need to sync again here
            self._sync_session_phase(session_key)

        return _on_task_done

    @staticmethod
    def _tag_task_for_session(task: asyncio.Task, session_key: str) -> None:
        """Attach stable session metadata to a task for later cleanup."""
        setattr(task, "_xbot_session_key", session_key)

    @staticmethod
    def _task_belongs_to_session(task: asyncio.Task, session_key: str) -> bool:
        """Check whether a task belongs to a session based on explicit metadata."""
        return getattr(task, "_xbot_session_key", None) == session_key

    def _set_session_phase(self, session_key: str, phase: SessionPhase, *, reason: str = "") -> None:
        """Set session phase using coordinator."""
        self._state_coordinator.transition(session_key, phase, reason=reason, force=True)

    def _sync_session_phase(self, session_key: str) -> None:
        """Synchronize session phase based on current state using coordinator."""
        # Don't override ERROR, STOPPING, or RESETTING states
        # - ERROR should be explicitly cleared
        # - STOPPING/RESETTING are in progress and will be finalized by _terminate_session
        current_phase = self._state_coordinator.get_phase(session_key)
        protected_phases = {
            SessionPhase.ERROR,
            SessionPhase.STOPPING,
            SessionPhase.RESETTING,
        }
        if current_phase in protected_phases:
            return

        if self.bus is not None:
            if self.bus.get_pending_request_for_session(session_key):
                self._state_coordinator.force_transition(
                    session_key, SessionPhase.WAITING_PERMISSION, reason="sync_pending_permission"
                )
                return
            if self.bus.get_pending_interaction_for_session(session_key):
                self._state_coordinator.force_transition(
                    session_key, SessionPhase.WAITING_INTERACTION, reason="sync_pending_interaction"
                )
                return
        active = self._state_coordinator.get_active_tasks(session_key)
        if active:
            self._state_coordinator.force_transition(session_key, SessionPhase.RUNNING, reason="sync_active_tasks")
        else:
            self._state_coordinator.force_transition(session_key, SessionPhase.IDLE, reason="sync_idle")

    def _log_state_snapshot(self, session_key: str, event: str) -> None:
        """Log state snapshot to session trace for debugging.

        Captures current state and checks for inconsistencies.
        If inconsistencies are found, logs a warning.

        Args:
            session_key: Session to check
            event: Event name for trace (e.g., "dispatch_start", "dispatch_end")
        """
        if not self._state_check_enabled:
            return

        try:
            snapshot = self._state_checker.check_session(session_key)

            # Record to session trace
            append_session_trace(
                self.sessions,
                session_key,
                f"state_snapshot_{event}",
                snapshot.to_dict(),
            )

            # Warn if inconsistencies detected
            if not snapshot.is_consistent():
                logger.warning(
                    f"State inconsistency at {event} for {session_key}: "
                    f"{snapshot.inconsistencies}"
                )
        except Exception as e:
            logger.debug(f"State snapshot logging failed: {e}")

    async def _on_backend_client_cleanup(self, session_key: str) -> None:
        """Callback when backend cleans up a client (TTL/LRU eviction).

        This ensures runtime state is synchronized when backend resources
        are cleaned up independently.

        Args:
            session_key: The session whose client was cleaned up
        """
        current_phase = self._state_coordinator.get_phase(session_key)

        # Only update if session is active, waiting, or in error state
        # Note: STOPPING/RESETTING are handled by _terminate_session
        active_phases = {
            SessionPhase.RUNNING,
            SessionPhase.WAITING_PERMISSION,
            SessionPhase.WAITING_INTERACTION,
            SessionPhase.ERROR,  # Also clean up ERROR state sessions
        }

        # Log if in STOPPING/RESETTING state for debugging
        if current_phase in {SessionPhase.STOPPING, SessionPhase.RESETTING}:
            logger.debug(
                f"Backend client cleanup for session in {current_phase.value} state: {session_key} "
                "(will be handled by _terminate_session)"
            )
            return

        if current_phase in active_phases:
            logger.debug(
                f"Backend client cleaned up for session: {session_key} "
                f"(phase={current_phase.value})"
            )

            # Clear pending requests if any (async with lock protection)
            if self.bus is not None:
                if hasattr(self.bus, "aclear_session_requests"):
                    await self.bus.aclear_session_requests(session_key)

            # Transition to IDLE
            self._state_coordinator.force_transition(
                session_key, SessionPhase.IDLE, reason="backend_client_cleanup"
            )

            # Clean up related runtime state via coordinator
            self._state_coordinator.clear_task_list(session_key)
            self._state_coordinator.release_lock(session_key)

    def get_session_state(self, session_key: str) -> str:
        """Return current runtime session phase for diagnostics.

        Uses coordinator for unified state access.
        """
        return self._state_coordinator.get_phase(session_key).value

    def get_session_phase(self, session_key: str) -> SessionPhase:
        """Return current session phase as enum.

        This is the recommended method for getting session phase.

        Args:
            session_key: Session identifier

        Returns:
            Current SessionPhase enum value
        """
        return self._state_coordinator.get_phase(session_key)

    def _session_diagnostics_text(self, session_key: str) -> str:
        phase = self.get_session_state(session_key)
        active_tasks = len(self._state_coordinator.get_active_tasks(session_key))
        pending_permission = None
        pending_interaction = None
        if self.bus is not None:
            pending_permission = self.bus.get_pending_request_for_session(session_key)
            pending_interaction = self.bus.get_pending_interaction_for_session(session_key)
        sdk_session_id = ""
        if self.sessions is not None:
            session = self.sessions.get_or_create(session_key)
            sdk_session_id = str(session.metadata.get("sdk_session_id") or "")

        lines = [
            f"Session: {session_key}",
            f"Phase: {phase}",
            f"Active tasks: {active_tasks}",
            f"Pending permission: {pending_permission or 'none'}",
            f"Pending interaction: {pending_interaction or 'none'}",
            f"SDK session id: {sdk_session_id or 'none'}",
            f"Backend: {self.router.backend_type}",
        ]
        return "\n".join(lines)

    def _coord_status_text(self) -> str:
        """Generate coordinator status text with statistics."""
        lines = [
            "🔧 State Coordinator",
            "",
        ]

        # Add stats if coordinator has any
        if hasattr(self._state_coordinator, '_stats'):
            stats = self._state_coordinator._stats
            lines.append("Stats:")
            lines.append(f"  phase_transitions: {stats.phase_transitions}")
            lines.append(f"  locks_created: {stats.locks_created}")
            lines.append(f"  tasks_created: {stats.tasks_created}")

        return "\n".join(lines)

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
        media: list[str] | None = None,
    ) -> str:
        msg = InboundMessage(
            channel=channel,
            sender_id="user",
            chat_id=chat_id,
            content=content,
            session_key_override=session_key,
            media=media or [],
        )
        if not self._is_local_runtime_command(content):
            await self.initialize()
        response = await self._handle_message(msg, on_progress=on_progress)
        return response.content if response else ""

    @classmethod
    def _is_local_runtime_command(cls, content: str) -> bool:
        stripped = content.strip().lower()
        # 检查精确匹配
        if stripped in cls.LOCAL_RUNTIME_COMMANDS:
            return True
        # 检查前缀匹配（如 !model, !model glm-4-flash）
        return any(stripped.startswith(prefix.lower()) for prefix in cls.LOCAL_RUNTIME_COMMAND_PREFIXES)

    @classmethod
    def _normalize_command_prompt(cls, content: str) -> str:
        stripped = content.strip()
        alias = cls.COMMAND_ALIASES.get(stripped.lower())
        return alias if alias else content

    def _handle_model_command(self, msg: InboundMessage, content: str) -> OutboundMessage:
        """处理 !model 命令。

        格式:
        - !model: 显示当前状态
        - !model <模型id>: 切换模型

        Args:
            msg: 入站消息
            content: 消息内容

        Returns:
            出站消息
        """
        parts = content.split(maxsplit=1)

        if len(parts) == 1:
            # !model - 显示状态
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self.model_manager.get_status_text(),
                metadata=msg.metadata or {},
            )

        # !model <模型id> - 切换模型
        model_id = parts[1].strip()
        success, message = self.model_manager.switch_model(model_id)

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=message,
            metadata=msg.metadata or {},
        )

    def _bus_progress(self, msg: InboundMessage) -> Callable[..., Coroutine[None, None, None]]:
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
        on_progress: Callable[..., Coroutine[None, None, None]] | None,
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
    def _supports_extended_progress_callback(
        on_progress: Callable[..., Coroutine[None, None, None]] | None,
    ) -> bool:
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
        # Keep baseline runtime-compatible commands visible even when SDK discovery
        # returns only a partial command list (regression guard for "/help incomplete").
        discovered = set(await self.router.backend.get_session_commands(session_key))
        baseline = set(self.SDK_HELP_FALLBACK_COMMANDS)
        sdk_commands = sorted(discovered | baseline)

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
            "!help — Show this help",
            "/help — Show this help",
            "!stop — Stop current task (preserves context)",
            "!reset — Full reset, delete all context and SDK session",
            "/reset — Local reset alias",
            "        --soft: Keep SDK context, only clear state",
            "!state — Show runtime session diagnostics",
            "/state — Local diagnostics alias",
            "!restart — Restart the bot process",
            "/restart — Runtime restart alias",
            "!ver — Show version and build info",
            "",
            "Context controls:",
            "/clear — Clear conversation context (start fresh)",
            "/new — Clear session locally and start fresh",
            "/compact — Compact context (summarize history)",
            "",
            "SDK commands (forwarded):",
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

    async def _handle_session_command(self, msg: InboundMessage) -> OutboundMessage:
        """Handle !session commands for SDK session management.

        Commands:
            !session list [limit] [offset]  - List SDK sessions
            !session delete [session_key]   - Delete SDK session file
            !session info [session_key]     - Show session info
        """
        parts = msg.content.strip().split()
        subcmd = parts[1] if len(parts) > 1 else ""

        if subcmd == "list":
            if not hasattr(self.router.backend, "list_sdk_sessions"):
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="❌ Current backend does not support SDK session listing.",
                )
            # !session list [limit=10] [offset=0]
            limit = 10
            offset = 0
            try:
                if len(parts) > 2:
                    limit = int(parts[2])
                if len(parts) > 3:
                    offset = int(parts[3])
            except ValueError:
                pass

            # Cap limits
            limit = min(max(limit, 1), 100)
            offset = max(offset, 0)

            result = await self.router.backend.list_sdk_sessions(limit, offset)

            if result.get("error"):
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"❌ Failed to list sessions: {result['error']}",
                )

            sessions = result.get("sessions", [])
            if not sessions:
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="📋 No SDK sessions found.",
                )

            lines = [f"📋 SDK Sessions ({len(sessions)} shown):"]
            for i, s in enumerate(sessions, 1):
                session_id = s.get("session_id", "unknown")[:12]
                title = s.get("title", "Untitled")[:30]
                msg_count = s.get("message_count", 0)
                lines.append(f"  {i}. `{session_id}...` \"{title}\" ({msg_count} msgs)")

            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="\n".join(lines),
            )

        elif subcmd == "delete":
            if not hasattr(self.router.backend, "delete_sdk_session"):
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="❌ Current backend does not support SDK session deletion.",
                )
            # !session delete [session_key]
            target = parts[2] if len(parts) > 2 else msg.session_key

            # Check if session is busy before deleting
            phase = self._state_coordinator.get_phase(target)
            if self._state_coordinator.is_busy(target):
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"❌ Cannot delete session in {phase.value} state. Wait for it to finish.",
                )

            # Transition to DELETING state before operation
            if not self._state_coordinator.transition(target, SessionPhase.DELETING, reason="delete_sdk_session"):
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"❌ Failed to transition to deleting state.",
                )

            try:
                # Delete SDK session
                result = await self.router.backend.delete_sdk_session(target)

                if result["deleted"]:
                    sdk_id = result.get("sdk_session_id", "unknown")
                    self._state_coordinator.transition(
                        target,
                        SessionPhase.IDLE,
                        reason="delete_complete",
                        force=True,
                    )
                    return OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"✅ SDK session deleted.\n🗑️ SDK ID: `{sdk_id}`",
                    )
                else:
                    error = result.get("error", "Unknown error")
                    # Return to IDLE on failure
                    self._state_coordinator.transition(target, SessionPhase.ERROR, reason=f"delete_failed: {error}", force=True)
                    self._state_coordinator.transition(target, SessionPhase.IDLE, reason="delete_failed_recovery", force=True)
                    return OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"❌ Failed to delete SDK session: {error}",
                    )
            except Exception as e:
                # Return to ERROR then IDLE on exception
                self._state_coordinator.transition(target, SessionPhase.ERROR, reason=f"delete_exception: {e}", force=True)
                self._state_coordinator.transition(target, SessionPhase.IDLE, reason="delete_exception_recovery", force=True)
                raise

        elif subcmd == "info":
            # !session info - show current session's SDK info
            target = parts[2] if len(parts) > 2 else msg.session_key
            sdk_session_id = None
            resolver = getattr(self.router.backend, "_resolve_sdk_session_id", None)
            if callable(resolver):
                sdk_session_id = resolver(target)

            if not sdk_session_id and self.sessions:
                session = self.sessions.get(target)
                if session:
                    sdk_session_id = session.metadata.get("sdk_session_id")

            lines = [f"📋 Session Info: `{target}`"]
            if sdk_session_id:
                lines.append(f"  SDK Session ID: `{sdk_session_id}`")
            else:
                lines.append("  SDK Session ID: (none)")

            # Show current phase
            phase = self._state_coordinator.get_phase(target)
            lines.append(f"  Phase: {phase.value}")

            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="\n".join(lines),
            )

        elif subcmd == "fork":
            if not hasattr(self.router.backend, "fork_sdk_session"):
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="❌ Current backend does not support SDK session forking.",
                )
            # !session fork [msg_id] [title]
            # Check if session is busy before forking
            phase = self._state_coordinator.get_phase(msg.session_key)
            if self._state_coordinator.is_busy(msg.session_key):
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"❌ Cannot fork session in {phase.value} state. Wait for it to finish.",
                )

            # Transition to FORKING state before operation
            if not self._state_coordinator.transition(msg.session_key, SessionPhase.FORKING, reason="fork_sdk_session"):
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"❌ Failed to transition to forking state.",
                )

            msg_id = None
            title = None

            # Parse arguments
            for part in parts[2:]:
                if part.startswith('"') or part.startswith("'"):
                    # Title in quotes
                    title = part.strip('"\'')
                elif not msg_id:
                    msg_id = part

            try:
                # Fork SDK session
                result = await self.router.backend.fork_sdk_session(
                    msg.session_key,
                    up_to_message_id=msg_id,
                    title=title,
                )

                if result["forked"]:
                    new_key = result.get("new_session_key", "unknown")
                    new_sdk = result.get("new_sdk_session_id", "unknown")
                    # Return to IDLE after successful fork
                    self._state_coordinator.transition(msg.session_key, SessionPhase.IDLE, reason="fork_complete")
                    return OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"✅ Session forked!\n"
                                f"📌 New session: `{new_key}`\n"
                                f"🆔 SDK ID: `{new_sdk}`\n"
                                f"Continue with: `/continue {new_sdk}`",
                    )
                else:
                    error = result.get("error", "Unknown error")
                    # Return to IDLE on failure
                    self._state_coordinator.transition(msg.session_key, SessionPhase.ERROR, reason=f"fork_failed: {error}", force=True)
                    self._state_coordinator.transition(msg.session_key, SessionPhase.IDLE, reason="fork_failed_recovery", force=True)
                    return OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"❌ Failed to fork session: {error}",
                    )
            except Exception as e:
                # Return to ERROR then IDLE on exception
                self._state_coordinator.transition(msg.session_key, SessionPhase.ERROR, reason=f"fork_exception: {e}", force=True)
                self._state_coordinator.transition(msg.session_key, SessionPhase.IDLE, reason="fork_exception_recovery", force=True)
                raise

        else:
            # Show help
            help_text = """📋 Session Commands:
  !session list [limit] [offset]  - List SDK sessions
  !session delete [key]           - Delete SDK session file
  !session fork [msg_id] [title]  - Fork session from message
  !session info                   - Show current session info

  !reset --hard                   - Reset and delete SDK file"""
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=help_text,
            )

    async def _do_clear_session(self, session_key: str) -> None:
        """Clear session context by resetting backend session.

        This is used for /clear, /new, /reset commands when using non-Claude models
        that don't support SDK's built-in slash commands.
        """
        try:
            # Delete SDK session file completely (including ~/.claude/projects/.../*.jsonl)
            # This ensures the context is truly cleared, not just disconnected
            if hasattr(self.router.backend, "delete_sdk_session"):
                result = await self.router.backend.delete_sdk_session(session_key)
                if result.get("deleted") or result.get("error") == "No SDK session found":
                    logger.info(f"Session SDK file deleted: {session_key}")
                else:
                    logger.warning(f"Failed to delete SDK session: {result.get('error')}")

            # Reset backend session (disconnects SDK client, clears session data)
            if hasattr(self.router.backend, "reset_session"):
                await self.router.backend.reset_session(session_key)
                logger.info(f"Session cleared via backend.reset_session: {session_key}")
            else:
                # Fallback: terminate session locally
                await self._terminate_session(session_key, hard_reset=True)
                logger.info(f"Session cleared via terminate_session: {session_key}")
        except Exception as e:
            logger.warning(f"Error clearing session {session_key}: {e}")

    async def _do_restart(self) -> None:
        """Gracefully clean up resources before restarting the process."""
        await asyncio.sleep(1)  # Allow the "Restarting..." response to be sent

        # Best-effort cleanup before exec
        try:
            logger.info("Restart: cleaning up before exec...")
            self.stop()
            await self.close_mcp()
        except Exception as e:
            logger.warning("Restart cleanup error (continuing): {}", e)

        os.execv(sys.executable, [sys.executable, "-m", "xbot"] + sys.argv[1:])

    def _record_background_task_error(self, task_name: str, exc: BaseException) -> None:
        """Record background task failure without leaking unhandled-task warnings."""
        logger.warning("Background task '{}' failed: {}", task_name, exc)

    def _spawn_background_task(self, coro: Coroutine[Any, Any, Any], task_name: str) -> asyncio.Task:
        """Create a background task and always retrieve exceptions."""
        task = asyncio.create_task(coro)

        def _done(done_task: asyncio.Task) -> None:
            try:
                done_task.result()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._record_background_task_error(task_name, exc)

        task.add_done_callback(_done)
        return task
