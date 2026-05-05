"""Local runtime command handlers for AgentService.

Handles !help, !stop, !reset, !restart, !state, !coord, !ver, !model
without going through the SDK.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from xbot.platform.bus.events import InboundMessage, OutboundMessage
from xbot.platform.logging.core import get_logger
from xbot.runtime.state import SessionEvent, SessionPhase

if TYPE_CHECKING:
    from xbot.runtime.core.service import AgentService

logger = get_logger(__name__)

# Local runtime commands (handled without going through SDK)
LOCAL_COMMANDS = {"!help", "!restart", "!stop", "!reset", "!state", "!coord", "!ver", "!skills"}
LOCAL_COMMAND_PREFIXES = ("!model",)
LOCAL_SLASH_COMMANDS = {"/help", "/clear", "/reset", "/restart", "/state", "/skills"}


class LocalCommandHandler:
    """Handles local runtime commands on behalf of AgentService."""

    def __init__(self, service: AgentService) -> None:
        self._service = service

    @staticmethod
    def is_local_command(content: str) -> bool:
        """Check if content is a local runtime command."""
        stripped = content.strip().lower()
        slash = stripped.split(maxsplit=1)[0]
        if slash in LOCAL_SLASH_COMMANDS:
            return True
        if stripped in LOCAL_COMMANDS:
            return True
        return any(stripped.startswith(p) for p in LOCAL_COMMAND_PREFIXES)

    async def handle(self, msg: InboundMessage, bus: Any) -> None:
        """Handle a local runtime command (matching v0.3.37 output fidelity)."""
        cmd = msg.content.strip()
        cmd_lower = cmd.lower()
        session_key = msg.session_key or f"{msg.channel}:{msg.chat_id}"
        response_text = ""

        # Slash aliases handled locally for parity with v0.3.37 runtime behavior.
        if cmd_lower.startswith("/"):
            slash = cmd_lower.split(maxsplit=1)[0]
            if slash == "/help":
                cmd_lower = "!help"
            elif slash == "/state":
                cmd_lower = "!state"
            elif slash == "/skills":
                cmd_lower = "!skills"
            elif slash == "/restart":
                cmd_lower = "!restart"
            elif slash == "/reset":
                # Keep optional --soft passthrough.
                cmd_lower = f"!reset{cmd_lower[len('/reset'):]}"
            elif slash == "/clear":
                # /clear = local fresh-start context reset.
                cmd_lower = "!reset"

        if cmd_lower == "!help":
            response_text = await self._build_help_text(session_key=session_key, channel=msg.channel)

        elif cmd_lower == "!ver":
            from xbot import version_text
            response_text = version_text()

        elif cmd_lower == "!stop":
            response_text = await self._do_stop(session_key, bus)

        elif cmd_lower.startswith("!reset") or cmd_lower == "!restart":
            soft_reset = "--soft" in cmd_lower or "-s" in cmd_lower
            response_text = await self._do_reset(session_key, bus, soft=soft_reset)

        elif cmd_lower == "!state":
            response_text = self._session_diagnostics_text(session_key)

        elif cmd_lower == "!skills":
            response_text = await self._skills_status_text(session_key)

        elif cmd_lower == "!coord":
            response_text = self._coord_status_text()

        elif cmd_lower.startswith("!model"):
            response_text = self._handle_model_command(cmd)

        else:
            response_text = f"Unknown command: {cmd}"

        if response_text:
            await bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=response_text,
                metadata=dict(msg.metadata or {}),
            ))

    async def _build_help_text(self, *, session_key: str, channel: str = "cli") -> str:
        """Build help text for all commands."""
        lines = ["**Runtime Commands:**"]
        lines.append("  !help — Show this help")
        lines.append("  !stop — Stop current processing (preserves context)")
        lines.append("  !reset — Reset session (deletes SDK context)")
        lines.append("  !reset --soft — Reset session (preserves SDK context)")
        lines.append("  !restart — Restart session")
        lines.append("  !state — Show session diagnostics")
        lines.append("  !skills — Show SDK skill snapshot for this session")
        lines.append("  !coord — Show global state overview")
        lines.append("  !ver — Show version info")
        lines.append("  !model — Show current model and available models")
        lines.append("  !model <id> — Switch to a different model")
        lines.append("")
        lines.append("**Local Slash Commands:**")
        lines.append("  /help — Show this help")
        lines.append("  /clear — Clear context and start fresh")
        lines.append("  /reset [--soft] — Reset session (local alias)")
        lines.append("  /state — Show session diagnostics")
        lines.append("  /skills — Show SDK skill snapshot for this session")
        lines.append("  /restart — Restart session")
        summary = self._service.get_workspace_commands_summary()
        if summary:
            lines.append("\n**Workspace Commands:**")
            lines.append(summary)
        sdk_commands = []
        try:
            sdk_commands = await asyncio.wait_for(
                self._service.get_session_commands(
                    session_key,
                    include_live_connected=True,
                    allow_connect=True,
                ),
                timeout=4.0,
            )
        except TimeoutError:
            logger.debug("Timed out loading SDK commands for help, using cached/fallback list")
            sdk_commands = await self._service.get_session_commands(
                session_key,
                include_live_connected=False,
                allow_connect=False,
            )
        except Exception as e:
            logger.debug("Failed to load SDK commands for help: %s", e)
        if sdk_commands:
            lines.append("\n**Claude SDK slash commands:**")
            lines.extend(f"  {cmd}" for cmd in sdk_commands if isinstance(cmd, str) and cmd.startswith("/"))
        return "\n".join(lines)

    async def _do_stop(self, session_key: str, bus: Any) -> str:
        """Stop current processing, preserving context (matches v0.3.37 !stop)."""
        svc = self._service
        result = await svc.interrupt_session(session_key)
        interrupted = bool(result.get("interrupted"))
        queued_cleared = int(result.get("queued_cleared") or 0)

        # Clear pending permission/interaction
        bus_obj = svc._shared_resources.get("bus")
        cleared_permission = False
        cleared_interaction = False
        if bus_obj:
            if bus_obj.get_pending_request_for_session(session_key):
                cleared_permission = True
            if hasattr(bus_obj, "get_pending_interaction_for_session"):
                if bus_obj.get_pending_interaction_for_session(session_key):
                    cleared_interaction = True

        # Build detail list
        details: list[str] = []
        if interrupted:
            details.append("current SDK turn")
        if queued_cleared:
            details.append(f"{queued_cleared} queued message(s)")
        if cleared_permission:
            details.append("pending permission")
        if cleared_interaction:
            details.append("pending interaction")

        # Set phase to IDLE
        sm = svc._shared_resources.get("runtime_registry")
        if sm:
            sm.dispatch(
                session_key,
                SessionEvent.TURN_COMPLETED,
                reason="user_stop",
            )

        # Build response
        content_parts: list[str] = []
        if details:
            content_parts.append(f"\U0001f6d1 Stopped {' and '.join(details)}.")
            content_parts.append("\U0001f4cc Context preserved. Continue conversation or use `!reset` to clear.")
        else:
            content_parts.append("No active task to stop.")

        return "\n".join(content_parts)

    async def _do_reset(self, session_key: str, bus: Any, *, soft: bool = False) -> str:
        """Reset session (matches v0.3.37 !reset with --soft support)."""
        svc = self._service

        had_worker = session_key in getattr(svc, "_session_workers", {})

        # Clear pending requests
        cleared_permission = False
        cleared_interaction = False
        bus_obj = svc._shared_resources.get("bus")
        if bus_obj:
            if bus_obj.get_pending_request_for_session(session_key):
                cleared_permission = True
            if hasattr(bus_obj, "get_pending_interaction_for_session"):
                if bus_obj.get_pending_interaction_for_session(session_key):
                    cleared_interaction = True

        # Reset SDK runtime and optionally drop SDK session context
        await svc.reset_session(session_key, drop_sdk_context=not soft)

        # Set phase to IDLE
        sm = svc._shared_resources.get("runtime_registry")
        if sm:
            sm.dispatch(
                session_key,
                SessionEvent.TURN_COMPLETED,
                reason="user_reset",
            )

        # Build response
        parts = ["\u267b\ufe0f Session reset completed."]
        details: list[str] = []
        if had_worker:
            details.append("session worker")
        if cleared_permission:
            details.append("pending permission")
        if cleared_interaction:
            details.append("pending interaction")
        if details:
            parts.append(f"Cleared: {', '.join(details)}.")

        if not soft:
            parts.append("\U0001f5d1\ufe0f SDK context deleted. Fresh start!")
        else:
            parts.append("\U0001f4cc SDK context preserved (--soft).")

        return "\n".join(parts)

    async def _cancel_task_if_running(self, task: Any, *, session_key: str, action: str) -> bool:
        """Cancel a running task and wait briefly for termination."""
        if task is None or not hasattr(task, "done") or task.done():
            return False

        task.cancel()
        if isinstance(task, asyncio.Task):
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                logger.warning("Timed out waiting for cancelled task during %s (%s)", action, session_key)
            except Exception as e:
                logger.debug("Cancelled task raised during %s (%s): %s", action, session_key, e)
        return True

    def _session_diagnostics_text(self, session_key: str) -> str:
        """Generate session diagnostics (matches v0.3.37 !state output)."""
        svc = self._service
        sm = svc._shared_resources.get("runtime_registry")
        phase = sm.get_phase(session_key) if sm else "N/A"

        # Active tasks
        worker = getattr(svc, "_session_workers", {}).get(session_key)
        has_worker = bool(worker and not getattr(worker, "closed", False))
        queue_size = 0
        if worker is not None:
            try:
                queue_size = worker.input_queue.qsize()
            except Exception:
                queue_size = 0

        # Pending permission / interaction
        bus_obj = svc._shared_resources.get("bus")
        pending_permission = None
        pending_interaction = None
        if bus_obj:
            pending_permission = bus_obj.get_pending_request_for_session(session_key)
            if hasattr(bus_obj, "get_pending_interaction_for_session"):
                pending_interaction = bus_obj.get_pending_interaction_for_session(session_key)

        # SDK client status
        has_client = bool(has_worker and getattr(worker, "client", None) is not None)

        # SDK session ID from state manager
        sdk_session_id = ""
        if sm and hasattr(sm, "get"):
            state = sm.get(session_key)
            if state:
                sdk_session_id = str(getattr(state, "sdk_session_id", "") or "")

        lines = [
            f"Session: {session_key}",
            f"Phase: {phase}",
            f"Session worker: {'active' if has_worker else 'none'}",
            f"Queued messages: {queue_size}",
            f"Pending permission: {pending_permission or 'none'}",
            f"Pending interaction: {pending_interaction or 'none'}",
            f"SDK session id: {sdk_session_id or 'none'}",
            f"SDK client: {'connected' if has_client else 'none'}",
        ]
        if sm and hasattr(sm, "get_sdk_capabilities"):
            caps = sm.get_sdk_capabilities(session_key)
            lines.append(f"Skill source: {caps.get('skill_source', 'sdk_only')}")
            lines.append(f"SDK skills: {len(caps.get('skills', []))}")
            lines.append(f"SDK tools: {len(caps.get('tools', []))}")
        return "\n".join(lines)

    async def _skills_status_text(self, session_key: str) -> str:
        """Show SDK skill snapshot for the current session."""
        sm = self._service._shared_resources.get("runtime_registry")
        if not sm or not hasattr(sm, "get_sdk_capabilities"):
            return "SDK skill snapshot unavailable."

        # If this is a fresh session with no cached init payload yet, try a live
        # SDK metadata refresh so !skills can work on first query.
        caps = sm.get_sdk_capabilities(session_key)
        if not caps.get("skills"):
            try:
                await asyncio.wait_for(
                    self._service.get_session_commands(
                        session_key,
                        include_live_connected=True,
                        allow_connect=True,
                    ),
                    timeout=6.0,
                )
            except Exception as e:
                logger.debug("Failed to refresh SDK capabilities for !skills: %s", e)

        caps = sm.get_sdk_capabilities(session_key)
        skills = caps.get("skills", [])
        source = caps.get("skill_source", "sdk_only")
        if not skills:
            return f"Skill source: {source}\nSDK skills: (none cached yet)"

        lines = [
            f"Skill source: {source}",
            f"SDK skills ({len(skills)}):",
        ]
        lines.extend(f"  - {name}" for name in skills)
        return "\n".join(lines)

    def _coord_status_text(self) -> str:
        """Generate coordinator status text (matches v0.3.37 !coord output)."""
        svc = self._service
        sm = svc._shared_resources.get("runtime_registry")
        lines = ["\U0001f527 State Coordinator", ""]

        if sm and hasattr(sm, "snapshot"):
            snap = sm.snapshot()
            total = int(snap.get("sessions", 0))
            by_phase = dict(snap.get("by_phase", {}) or {})
            lines.append(f"Sessions: {total}")
            for phase_name, count in sorted(by_phase.items()):
                lines.append(f"  {phase_name}: {count}")
            lines.append(f"Illegal transitions: {int(snap.get('illegal_transition_total', 0))}")

            # Active workers across all sessions
            workers = getattr(svc, "_session_workers", {})
            active_workers = sum(
                1
                for worker in workers.values()
                if not getattr(worker, "closed", False)
            )
            lines.append(f"\nActive session workers: {active_workers}")

            # Client pool stats
            pool_size = len(svc._client_pool._clients) if hasattr(svc._client_pool, "_clients") else 0
            lines.append(f"Client pool size: {pool_size}")
        else:
            lines.append("State manager not available.")

        return "\n".join(lines)

    def _handle_model_command(self, content: str) -> str:
        """Handle !model command (matches v0.3.37 model management)."""
        svc = self._service
        parts = content.split(maxsplit=1)

        # Try to use ModelManager if available
        if not hasattr(svc, "_model_manager"):
            svc._model_manager = None
            try:
                config = svc._shared_resources.get("config")
                if config:
                    from xbot.runtime.core.context.model_manager import ModelManager
                    svc._model_manager = ModelManager(config)
            except Exception as e:
                logger.debug("ModelManager init failed: %s", e)

        if svc._model_manager:
            if len(parts) == 1:
                return svc._model_manager.get_status_text()
            else:
                model_id = parts[1].strip()
                success, message = svc._model_manager.switch_model(model_id)
                return message
        else:
            model = svc._config.model if svc._config else "unknown"
            if len(parts) == 1:
                return f"Current model: {model}"
            else:
                return f"Model switching not available. Current: {model}"
