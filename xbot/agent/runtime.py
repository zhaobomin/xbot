"""Unified router-backed agent runtime."""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from xbot.agent.capabilities import CapabilityCatalog, canonical_tool_name
from xbot.agent.commands import CommandsLoader
from xbot.agent.event_formatter import format_usage_summary
from xbot.agent.protocol import AgentContext
from xbot.agent.router import AgentRouter, register_default_backends
from xbot.agent.state_checker import StateConsistencyChecker
from xbot.agent.state_coordinator import SessionStateCoordinator
from xbot.agent.trace import append_session_trace
from xbot.bus.events import InboundMessage, OutboundMessage


class SessionPhase(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_PERMISSION = "waiting_permission"
    WAITING_INTERACTION = "waiting_interaction"
    STOPPING = "stopping"
    RESETTING = "resetting"
    ERROR = "error"


# Valid state transitions: {from_phase: {to_phase1, to_phase2, ...}}
# Note: IDLE -> WAITING_* is allowed for edge cases where a task ends but
# pending requests remain (e.g., agent requests permission then finishes)
VALID_TRANSITIONS: dict[SessionPhase, set[SessionPhase]] = {
    SessionPhase.IDLE: {
        SessionPhase.RUNNING,
        SessionPhase.WAITING_PERMISSION,  # Edge case: handling stale pending request
        SessionPhase.WAITING_INTERACTION,  # Edge case: handling stale pending request
        SessionPhase.STOPPING,
        SessionPhase.RESETTING,
        SessionPhase.ERROR,
    },
    SessionPhase.RUNNING: {
        SessionPhase.IDLE,
        SessionPhase.WAITING_PERMISSION,
        SessionPhase.WAITING_INTERACTION,
        SessionPhase.STOPPING,
        SessionPhase.RESETTING,
        SessionPhase.ERROR,
    },
    SessionPhase.WAITING_PERMISSION: {
        SessionPhase.RUNNING,
        SessionPhase.IDLE,
        SessionPhase.STOPPING,
        SessionPhase.RESETTING,
        SessionPhase.ERROR,
    },
    SessionPhase.WAITING_INTERACTION: {
        SessionPhase.RUNNING,
        SessionPhase.IDLE,
        SessionPhase.STOPPING,
        SessionPhase.RESETTING,
        SessionPhase.ERROR,
    },
    SessionPhase.STOPPING: {
        SessionPhase.IDLE,
        SessionPhase.ERROR,
    },
    SessionPhase.RESETTING: {
        SessionPhase.IDLE,
        SessionPhase.ERROR,
    },
    SessionPhase.ERROR: {
        SessionPhase.IDLE,
        SessionPhase.RESETTING,
    },
}


@dataclass
class SessionState:
    phase: SessionPhase = SessionPhase.IDLE
    reason: str = ""
    previous_phase: SessionPhase | None = None
    transition_count: int = 0


class SessionStateMachine:
    """Manages session state transitions with validation and logging."""

    def __init__(
        self,
        on_transition: Callable[[str, SessionPhase, SessionPhase, str], None] | None = None,
    ):
        self._states: dict[str, SessionState] = {}
        self._on_transition = on_transition

    def get_state(self, session_key: str) -> SessionState:
        """Get or create state for a session."""
        if session_key not in self._states:
            self._states[session_key] = SessionState()
        return self._states[session_key]

    def get_phase(self, session_key: str) -> SessionPhase:
        """Get current phase for a session."""
        return self.get_state(session_key).phase

    def transition(
        self,
        session_key: str,
        to_phase: SessionPhase,
        *,
        reason: str = "",
        force: bool = False,
    ) -> bool:
        """Attempt a state transition.

        Args:
            session_key: Session identifier
            to_phase: Target phase
            reason: Reason for transition
            force: If True, bypass validation (for error recovery)

        Returns:
            True if transition succeeded, False otherwise
        """
        state = self.get_state(session_key)
        from_phase = state.phase

        # Skip if already in target phase with same reason
        if from_phase == to_phase and state.reason == reason:
            return True

        # Same phase with different reason: allow update without transition validation
        if from_phase == to_phase:
            state.reason = reason
            state.transition_count += 1
            logger.debug(
                f"Session state reason update: {session_key} {from_phase.value} (reason: {reason})"
            )
            if self._on_transition:
                self._on_transition(session_key, from_phase, to_phase, reason)
            return True

        # Validate transition
        if not force:
            # VALID_TRANSITIONS[from_phase] contains all valid target phases from from_phase
            allowed_targets = VALID_TRANSITIONS.get(from_phase, set())
            if to_phase not in allowed_targets:
                logger.warning(
                    f"Invalid state transition: {from_phase.value} -> {to_phase.value} "
                    f"(session={session_key}, reason={reason})"
                )
                return False

        # Perform transition
        state.previous_phase = from_phase
        state.phase = to_phase
        state.reason = reason
        state.transition_count += 1

        # Log transition
        logger.debug(
            f"Session state transition: {session_key} {from_phase.value} -> {to_phase.value} ({reason})"
        )

        # Callback
        if self._on_transition:
            self._on_transition(session_key, from_phase, to_phase, reason)

        return True

    def force_transition(self, session_key: str, to_phase: SessionPhase, *, reason: str = "") -> bool:
        """Force a state transition, bypassing validation.

        Returns:
            Always True (for consistency with transition())
        """
        state = self.get_state(session_key)
        from_phase = state.phase

        state.previous_phase = from_phase
        state.phase = to_phase
        state.reason = reason
        state.transition_count += 1

        logger.debug(
            f"Session state transition (forced): {session_key} {from_phase.value} -> {to_phase.value} ({reason})"
        )

        if self._on_transition:
            self._on_transition(session_key, from_phase, to_phase, reason)

        return True

    def reset(self, session_key: str) -> None:
        """Reset a session to IDLE state."""
        if session_key in self._states:
            self._states[session_key] = SessionState()

    def clear(self, session_key: str) -> None:
        """Remove session state entirely."""
        self._states.pop(session_key, None)

    def is_idle(self, session_key: str) -> bool:
        """Check if session is idle."""
        return self.get_phase(session_key) == SessionPhase.IDLE

    def is_waiting(self, session_key: str) -> bool:
        """Check if session is waiting for user input."""
        phase = self.get_phase(session_key)
        return phase in {SessionPhase.WAITING_PERMISSION, SessionPhase.WAITING_INTERACTION}

    def is_active(self, session_key: str) -> bool:
        """Check if session has active work."""
        return self.get_phase(session_key) == SessionPhase.RUNNING


class AgentRuntime:
    """Single runtime entrypoint for gateway and CLI."""
    LOCAL_RUNTIME_COMMANDS = {
        "!help", "!restart", "!stop", "!reset", "!state",
        "/help", "/restart", "/stop", "/reset", "/state",
    }
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
        # Use state machine for session state management
        self._state_machine = SessionStateMachine(
            on_transition=self._on_state_transition
        )

        # State consistency checker (for debugging and monitoring)
        self._state_checker = StateConsistencyChecker(self)
        self._state_check_enabled = True  # Feature flag for state checking

        # Session state coordinator (unified state management)
        self._state_coordinator = SessionStateCoordinator(self)

        # Register backend state sync callbacks
        self.shared_resources["on_backend_client_cleanup"] = self._on_backend_client_cleanup

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

            # Dispatch message with atomic state management
            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(msg.session_key, []).append(task)
            task.add_done_callback(self._make_task_done_callback(msg.session_key))

    async def _handle_permission_response(self, msg: InboundMessage) -> bool:
        """Check if the message is a permission response and handle it.

        Uses coordinator transactions for state changes.

        Returns:
            True if the message was handled as a permission response, False otherwise
        """
        if self.bus is None:
            return False

        # Parse the user's response
        content = msg.content.strip().lower()
        decision = None
        reason = ""

        allow_variations = {"允许", "allow", "yes", "y", "是", "ok", "同意", "确认"}
        deny_variations = {"拒绝", "deny", "no", "n", "否", "取消"}

        is_permission_keyword = content in allow_variations or content in deny_variations

        if content in allow_variations:
            decision = "allow"
        elif content in deny_variations:
            decision = "deny"
            reason = "User denied"
        else:
            return False

        request_id = self.bus.get_pending_request_for_session(msg.session_key)
        if not request_id:
            if is_permission_keyword:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="⚠️ 没有待处理的权限请求，可能已超时过期。请重新发起操作。",
                    )
                )
                return True
            return False

        # Check current state - only process if waiting for permission or idle/running
        # (backend may already be processing the response)
        current_phase = self._state_coordinator.get_phase(msg.session_key)
        if current_phase not in {
            SessionPhase.WAITING_PERMISSION,
            SessionPhase.IDLE,
            SessionPhase.RUNNING,
        }:
            # Session is in STOPPING/RESETTING/ERROR - ignore the response
            logger.debug(
                f"Ignoring permission response for session in {current_phase.value} state"
            )
            return True

        # Atomic state transition: set WAITING_PERMISSION (if not already)
        if current_phase != SessionPhase.WAITING_PERMISSION:
            async with self._state_coordinator.transaction(
                msg.session_key, validate_on_commit=False
            ) as tx:
                tx.set_phase(SessionPhase.WAITING_PERMISSION, reason="pending_permission_detected")

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
            # Reset state to IDLE since response was not submitted
            async with self._state_coordinator.transaction(
                msg.session_key, validate_on_commit=False
            ) as tx:
                tx.set_phase(SessionPhase.IDLE, reason="permission_response_expired")
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="⚠️ 权限请求已过期或被取消，请重新发起操作。",
                )
            )
            return True

        logger.info(f"Permission response submitted: {decision} for request {request_id}")

        # Atomic state transition: set RUNNING
        async with self._state_coordinator.transaction(
            msg.session_key, validate_on_commit=False
        ) as tx:
            tx.set_phase(SessionPhase.RUNNING, reason="permission_response_submitted")

        return True

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Dispatch message with transaction-based state management.

        Uses coordinator transactions for atomic state changes.
        """
        # Note: Task registration happens in run() when creating the task.
        # When _dispatch is called directly (tests), there's no task registration.

        # Log state snapshot at dispatch start
        self._log_state_snapshot(msg.session_key, "dispatch_start")

        try:
            # Start atomic dispatch session
            async with self._state_coordinator.transaction(
                msg.session_key, validate_on_commit=False
            ) as tx:
                tx.set_phase(SessionPhase.RUNNING, reason="dispatch_start")
                tx.acquire_lock()

            # Get the lock (should exist after transaction)
            lock = self._session_locks.get(msg.session_key)
            if lock is None:
                # Fallback if transaction didn't create lock
                lock = self._session_locks.setdefault(msg.session_key, asyncio.Lock())

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
            # Check current state - don't override ERROR state
            current_phase = self._state_coordinator.get_phase(msg.session_key)
            if current_phase == SessionPhase.ERROR:
                # Error occurred, don't override ERROR state
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
                registered_tasks = self._active_tasks.get(msg.session_key, [])

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
        """Handle pending generic interaction replies for a session.

        Uses coordinator transactions for state changes.
        """
        if self.bus is None:
            return False

        if self._is_local_runtime_command(msg.content):
            return False

        content = msg.content.strip()
        normalized = content.lower()

        allow_variations = {"允许", "allow", "yes", "y", "是", "ok", "同意", "确认"}
        deny_variations = {"拒绝", "deny", "no", "n", "否", "取消"}
        is_interaction_keyword = normalized in allow_variations or normalized in deny_variations

        request_id = self.bus.get_pending_interaction_for_session(msg.session_key)
        if not request_id:
            if is_interaction_keyword:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="⚠️ 没有待处理的交互请求，可能已超时过期。请重新发起操作。",
                    )
                )
                return True
            return False

        # Check current state - only process if waiting for interaction or idle/running
        current_phase = self._state_coordinator.get_phase(msg.session_key)
        if current_phase not in {
            SessionPhase.WAITING_INTERACTION,
            SessionPhase.IDLE,
            SessionPhase.RUNNING,
        }:
            # Session is in STOPPING/RESETTING/ERROR - ignore the response
            logger.debug(
                f"Ignoring interaction response for session in {current_phase.value} state"
            )
            return True

        # Atomic state transition: set WAITING_INTERACTION (if not already)
        if current_phase != SessionPhase.WAITING_INTERACTION:
            async with self._state_coordinator.transaction(
                msg.session_key, validate_on_commit=False
            ) as tx:
                tx.set_phase(SessionPhase.WAITING_INTERACTION, reason="pending_interaction_detected")

        req = self.bus.get_interaction_request(request_id)
        if req is None:
            # Reset state to IDLE since request expired
            async with self._state_coordinator.transaction(
                msg.session_key, validate_on_commit=False
            ) as tx:
                tx.set_phase(SessionPhase.IDLE, reason="interaction_request_expired")
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="⚠️ 交互请求已过期或被取消，请重新发起操作。",
                )
            )
            return True

        action = "reply"

        if req.kind in {"confirmation", "approval"}:
            if normalized in allow_variations:
                action = "confirm" if req.kind == "confirmation" else "allow"
            elif normalized in deny_variations:
                action = "cancel" if req.kind == "confirmation" else "deny"
            else:
                action = "reply"

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
            # Reset state to IDLE since response was not submitted
            async with self._state_coordinator.transaction(
                msg.session_key, validate_on_commit=False
            ) as tx:
                tx.set_phase(SessionPhase.IDLE, reason="interaction_response_expired")
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="⚠️ 交互请求已过期或被取消，请重新发起操作。",
                )
            )
            return True

        logger.info(f"Interaction response submitted: action={action}, request={request_id}")

        # Atomic state transition: set RUNNING
        async with self._state_coordinator.transaction(
            msg.session_key, validate_on_commit=False
        ) as tx:
            tx.set_phase(SessionPhase.RUNNING, reason="interaction_response_submitted")

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
        if cmd in {"!reset", "/reset"}:
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

            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="\n".join(parts))
        if cmd in {"!state", "/state"}:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self._session_diagnostics_text(msg.session_key),
                metadata=msg.metadata or {},
            )
        if cmd in {"!coord", "/coord"}:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self._coord_status_text(),
                metadata=msg.metadata or {},
            )

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
            tasks = self._active_tasks.pop(session_key, [])
            cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            # Backend cleanup (async I/O)
            backend_cancelled = await self.router.backend.cancel_session(session_key)
            backend_task_stopped = await self.router.backend.stop_active_task(session_key)
            interrupt_result = await self.router.backend.interrupt_session(session_key)
            if hard_reset:
                await self.router.backend.reset_session(session_key)

            # Clear pending requests
            if self.bus is not None and hasattr(self.bus, "clear_session_requests"):
                cleared_requests = self.bus.clear_session_requests(session_key)
        except Exception as e:
            logger.warning(f"Error during terminate_session cleanup: {e}")
            # Continue to final cleanup even if backend operations fail

        # Atomic cleanup transaction - always run, even if cleanup failed
        async with self._state_coordinator.transaction(
            session_key, validate_on_commit=False
        ) as tx:
            # Release lock
            tx.release_lock()

            # Set final phase (always IDLE, even for hard_reset)
            tx.set_phase(SessionPhase.IDLE, reason="terminate_session_completed")

        # Remove lock from dict (coordinator handles state)
        self._session_locks.pop(session_key, None)

        # For hard_reset, reset state to fresh IDLE (instead of clear which deletes)
        if hard_reset:
            self._state_machine.reset(session_key)

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

    def _make_task_done_callback(self, session_key: str):
        """Create a done callback for task cleanup.

        Returns a callback that removes the task from _active_tasks when done.
        This avoids the lambda capture issue where the task references itself.
        Also syncs the session phase after task removal to ensure correct state.
        """
        def _on_task_done(task: asyncio.Task) -> None:
            tasks = self._active_tasks.get(session_key)
            if tasks and task in tasks:
                tasks.remove(task)
                # Clean up empty lists
                if not tasks:
                    self._active_tasks.pop(session_key, None)

            # Sync phase after task is done to ensure correct state
            # This is critical: _sync_session_phase in _dispatch's finally runs
            # before this task is marked done, so we need to sync again here
            self._sync_session_phase(session_key)

        return _on_task_done

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
        active = [t for t in self._active_tasks.get(session_key, []) if not t.done()]
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

    def _on_backend_client_cleanup(self, session_key: str) -> None:
        """Callback when backend cleans up a client (TTL/LRU eviction).

        This ensures runtime state is synchronized when backend resources
        are cleaned up independently.

        Args:
            session_key: The session whose client was cleaned up
        """
        current_phase = self._state_coordinator.get_phase(session_key)

        # Only update if session is active, waiting, or in error state
        active_phases = {
            SessionPhase.RUNNING,
            SessionPhase.WAITING_PERMISSION,
            SessionPhase.WAITING_INTERACTION,
            SessionPhase.ERROR,  # Also clean up ERROR state sessions
        }

        if current_phase in active_phases:
            logger.debug(
                f"Backend client cleaned up for session: {session_key} "
                f"(phase={current_phase.value})"
            )

            # Clear pending requests if any
            if self.bus is not None and hasattr(self.bus, "clear_session_requests"):
                self.bus.clear_session_requests(session_key)

            # Transition to IDLE
            self._state_coordinator.force_transition(
                session_key, SessionPhase.IDLE, reason="backend_client_cleanup"
            )

            # Clean up related runtime state
            self._active_tasks.pop(session_key, None)
            self._session_locks.pop(session_key, None)

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
        active_tasks = sum(1 for t in self._active_tasks.get(session_key, []) if not t.done())
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
            "!help or /help — Show available commands",
            "!stop or /stop — Stop the current task",
            "!reset or /reset — Hard reset current session state",
            "!state or /state — Show runtime session diagnostics",
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
