"""Unified Agent Service.

This module provides the single entry point for all agent operations,
combining the core logic from ClaudeSDKBackend and AgentRuntime.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from xbot.capabilities.catalog import CapabilityCatalog, canonical_tool_name
from xbot.capabilities.handoff import HandoffPolicy
from xbot.capabilities.policy import CapabilityPolicy
from xbot.interaction.event_formatter import format_rate_limit_event, format_task_notification
from xbot.memory.store import MemoryConsolidator
from xbot.runtime.core.client_pool import ClientPool
from xbot.runtime.core.command_handlers import LocalCommandHandler
from xbot.runtime.core.context.builder import ContextBuilder
from xbot.runtime.core.protocol import AgentContext, AgentResponse, StructuredLLMResponse, ToolCall
from xbot.runtime.core.types import AgentConfig
from xbot.runtime.state.machine import SessionPhase
from xbot.platform.bus.events import InboundMessage, OutboundMessage
from xbot.platform.logging.core import get_logger
from xbot.platform.utils.file_reader import FileType, classify_file, format_file_reference

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

logger = get_logger(__name__)

# Type alias for progress callback
ProgressCallback = Callable[[str], Any]

# Event type -> progress kind mapping (matches v0.3.37)
_EVENT_TYPE_TO_KIND = {
    "thinking": "reasoning",
    "tool_call": "tool",
    "tool_hint": "tool",
    "task": "task",
    "system": "system",
    "usage": "usage",
    "content_delta": "content",
    "result": "result",
}

_XBOT_MCP_SERVER_NAME = "xbot"
_SDK_NATIVE_TOOL_NAME_MAP = {
    "exec": "Bash",
    "shell": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "list_dir": "LS",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "ls": "LS",
    "bash": "Bash",
    "glob": "Glob",
    "grep": "Grep",
    "multi_edit": "MultiEdit",
    "multiedit": "MultiEdit",
}
_XBOT_EXTENSION_TO_MCP_TOOL = {
    "web_search": f"mcp__{_XBOT_MCP_SERVER_NAME}__web_search",
    "web_fetch": f"mcp__{_XBOT_MCP_SERVER_NAME}__web_fetch",
    "message": f"mcp__{_XBOT_MCP_SERVER_NAME}__message",
    "cron": f"mcp__{_XBOT_MCP_SERVER_NAME}__cron",
    "memory": f"mcp__{_XBOT_MCP_SERVER_NAME}__memory",
    "load_skill_content": f"mcp__{_XBOT_MCP_SERVER_NAME}__load_skill_content",
}
_SDK_NATIVE_TOOL_NAMES = {
    "Bash",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "LS",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Task",
    "TodoWrite",
    "NotebookRead",
    "NotebookEdit",
    "AskUserQuestion",
}


def _progress_kind_from_event_type(event_type: str, *, tool_hint: bool = False) -> str:
    """Map event_type to progress_kind (matches v0.3.37)."""
    if tool_hint:
        return "tool"
    return _EVENT_TYPE_TO_KIND.get(event_type, "progress")


class _NoOpTransaction:
    """No-op transaction for when state_manager is not available."""

    def set_phase(self, phase: Any, reason: str = "") -> None:
        pass

    def set_sdk_session_id(self, sdk_session_id: Any) -> None:
        pass

    def clear_sdk_session_id(self) -> None:
        pass


class AgentService:
    """Unified agent service combining backend and runtime logic.

    This is the single entry point for all agent operations:
    - initialize(): Set up the agent
    - process(): Handle messages and yield responses
    - shutdown(): Clean up resources
    - reset_session(): Reset session state
    - get_session_commands(): Get available commands
    - interrupt_session(): Interrupt ongoing processing
    - call_for_auxiliary(): Execute standalone prompts

    No more router, no more backend abstraction - just direct SDK usage.
    """

    def __init__(self, config=None, shared_resources=None) -> None:
        """Initialize the agent service.

        Args:
            config: Optional agent configuration (for backward compatibility)
            shared_resources: Optional shared resources (for backward compatibility)
        """
        self._initialized = False
        self._running = False
        self._config: AgentConfig | None = None
        self._shared_resources: dict[str, Any] = {}
        self._client_pool = ClientPool()
        self._handoff_policy: HandoffPolicy | None = None
        self._tool_adapter: Any = None
        self._response_handlers: Any = None
        self._commands_loader: Any = None
        self._command_handler: LocalCommandHandler | None = None
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._direct_progress_callbacks: dict[str, ProgressCallback] = {}

        # ReMe integration: ContextBuilder and MemoryConsolidator
        self._context_builder: Any | None = None
        self._memory_consolidator: Any | None = None
        self._sdk_settings_file: str | None = None
        self._async_consolidation_tasks: set[asyncio.Task] = set()

        # For backward compatibility with AgentRuntime interface
        self._pending_config = config
        self._pending_resources = shared_resources

    @property
    def name(self) -> str:
        """Service name identifier."""
        return "agent_service"

    async def initialize(
        self,
        config: AgentConfig | None = None,
        shared_resources: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the agent service.

        Args:
            config: Agent configuration (optional if provided to constructor)
            shared_resources: Shared resources (optional if provided to constructor)
        """
        if self._initialized:
            return

        # Use config from constructor if not provided here
        if config is not None:
            self._config = config
        elif self._pending_config is not None:
            self._config = self._pending_config

        # Use resources from constructor if not provided here
        if shared_resources is not None:
            self._shared_resources = shared_resources
        elif self._pending_resources is not None:
            self._shared_resources = self._pending_resources

        if self._config is None:
            raise RuntimeError("AgentService requires a config (provide to __init__ or initialize())")

        # Initialize handoff policy for SDK subagent observability
        agents_config = getattr(self._config, "agents", None)
        self._handoff_policy = HandoffPolicy(agents_config)

        # Initialize ReMe main chain: ContextBuilder + MemoryConsolidator
        runtime_config = self._shared_resources.get("config")
        workspace_path = Path(self._shared_resources.get("workspace", ".")).expanduser().resolve()
        sessions = self._shared_resources.get("session_manager")
        if sessions is None:
            try:
                from xbot.runtime.state.session_manager import SessionManager
                sessions = SessionManager(workspace_path)
                self._shared_resources["session_manager"] = sessions
            except Exception as e:
                logger.warning("Failed to initialize SessionManager: %s", e)

        if runtime_config:
            memory_cfg = getattr(getattr(runtime_config, "tools", None), "memory", None)
            memory_provider = getattr(memory_cfg, "provider", "file")
            use_reme = memory_provider == "reme"
            enable_vector_search = bool(getattr(memory_cfg, "enable_vector_search", False))

            llm_model = getattr(memory_cfg, "llm_model", None)
            llm_config = {"model_name": llm_model} if llm_model else None

            # Read load_bootstrap_files from agents.defaults config
            agents_defaults = getattr(getattr(runtime_config, "agents", None), "defaults", None)
            load_bootstrap = getattr(agents_defaults, "load_bootstrap_files", True)

            self._context_builder = ContextBuilder(
                workspace=workspace_path,
                use_reme=use_reme,
                llm_config=llm_config,
                enable_vector_search=enable_vector_search,
                load_bootstrap_files=load_bootstrap,
            )

            # Ensure MemoryTool shares the same memory store as ContextBuilder.
            self._shared_resources["memory_store"] = self._context_builder.memory

            if sessions is not None:
                self._memory_consolidator = MemoryConsolidator(
                    workspace=workspace_path,
                    backend=self,
                    sessions=sessions,
                    context_window_tokens=getattr(runtime_config.agents.defaults, "context_window_tokens", 65_536),
                    build_messages=self._context_builder.build_messages,
                    get_tool_definitions=self._get_tool_definitions,
                    memory_store=self._context_builder.memory,
                )

            self._log_memory_runtime_config(runtime_config)

        # Initialize tool adapter for built-in tools (cron, message, etc.)
        self._init_tool_adapter()

        # Initialize response handlers for permission/interaction routing
        try:
            from xbot.interaction.response_handlers import RuntimeResponseHandlers
            self._response_handlers = RuntimeResponseHandlers(self)
        except Exception as e:
            logger.warning("Failed to initialize RuntimeResponseHandlers: %s", e)

        # Initialize commands loader for workspace slash commands
        workspace = self._shared_resources.get("workspace")
        if workspace:
            try:
                from xbot.runtime.core.context.commands import CommandsLoader
                self._commands_loader = CommandsLoader(Path(workspace))
            except Exception as e:
                logger.warning("Failed to initialize CommandsLoader: %s", e)

        # Initialize local command handler
        self._command_handler = LocalCommandHandler(self)

        self._initialized = True
        logger.info("AgentService initialized")

    async def process(
        self,
        context: AgentContext,
    ) -> AsyncIterator[AgentResponse]:
        """Process a message and yield responses.

        Args:
            context: Processing context with session info and prompt

        Yields:
            AgentResponse objects (streaming)
        """
        if not self._initialized:
            raise RuntimeError("AgentService not initialized")

        logger.info(
            f"[AgentService] Processing for session={context.session_key}, "
            f"prompt={context.prompt[:50]}..."
        )

        # Keep routing info fresh so compact hooks can always resolve targets.
        self._set_session_routing(context.session_key, context.channel, context.chat_id)
        self._set_runtime_tool_and_permission_context(context)

        # Get or create client
        client = await self._get_or_create_client(context.session_key)
        await self._refresh_session_commands_from_client(context.session_key, client)

        # Process through SDK using query + receive_messages pattern
        try:
            query_prompt = self._build_query_prompt(context.prompt, context.media)
            # Send the query - SDK accepts string directly
            logger.info(f"[AgentService] Sending query for {context.session_key}")
            await asyncio.wait_for(client.query(query_prompt), timeout=30.0)
            logger.info(f"[AgentService] Query sent, starting receive loop for {context.session_key}")

            # Receive messages with per-message idle timeout (300s)
            # NOTE: receive_messages() is a persistent stream that does NOT end
            # after a single query. We must break on ResultMessage to return
            # control to the caller after each query completes.
            msg_count = 0
            idle_timeout = 300.0
            try:
                async with asyncio.timeout(idle_timeout) as cm:
                    async for message in client.receive_messages():
                        self._sync_sdk_session_mapping(context.session_key, message)
                        # Reset idle timer on each message received
                        cm.reschedule(asyncio.get_event_loop().time() + idle_timeout)
                        msg_count += 1
                        msg_type = type(message).__name__
                        logger.debug(f"[AgentService] Received message #{msg_count}: {msg_type}")
                        response = self._convert_event(message)
                        if response:
                            yield response
                        # ResultMessage signals the end of the current query
                        if msg_type == "ResultMessage":
                            logger.info(
                                f"[AgentService] ResultMessage received, ending receive loop "
                                f"for {context.session_key} after {msg_count} messages"
                            )
                            break
            except TimeoutError:
                logger.warning(
                    f"[AgentService] Receive loop idle timeout ({idle_timeout}s) "
                    f"for {context.session_key} after {msg_count} messages"
                )

            logger.info(f"[AgentService] Receive loop completed, {msg_count} messages for {context.session_key}")

        except asyncio.CancelledError:
            logger.info(f"[AgentService] Processing cancelled for {context.session_key}")
            raise
        except Exception as e:
            logger.error(f"[AgentService] Error processing: {e}")
            yield AgentResponse(
                content=f"Error: {e}",
                finish_reason="error",
            )
        finally:
            self._clear_runtime_tool_and_permission_context(context.session_key)

    @staticmethod
    def _build_query_prompt(prompt: str, media: list[Any] | None) -> str:
        """Build SDK query payload by injecting media references into prompt text."""
        if not media:
            return prompt

        image_lines: list[str] = []
        audio_lines: list[str] = []
        file_lines: list[str] = []

        for raw_path in media:
            if not isinstance(raw_path, str):
                continue
            path_text = raw_path.strip()
            if not path_text:
                continue

            path_obj = Path(path_text).expanduser()
            if path_obj.is_file():
                abs_path = str(path_obj.resolve())
                file_type = classify_file(abs_path)
                if file_type is FileType.IMAGE:
                    image_lines.append(f"[Image: source: {abs_path}]")
                elif file_type is FileType.AUDIO:
                    audio_lines.append(f"[Audio: source: {abs_path}]")
                else:
                    file_lines.append(format_file_reference(abs_path))
                continue

            # Preserve unresolved references so model can still reason or ask follow-up.
            file_lines.append(f"[附件路径: {path_text}]")

        media_sections: list[str] = []
        if image_lines:
            media_sections.append("用户附加了以下图片:\n" + "\n".join(image_lines))
        if audio_lines:
            media_sections.append("用户附加了以下音频:\n" + "\n".join(audio_lines))
        if file_lines:
            media_sections.append("用户附加了以下文件:\n" + "\n".join(file_lines))

        if not media_sections:
            return prompt

        prefix = "\n\n".join(media_sections)
        if prompt.strip():
            return f"{prefix}\n\n用户请求:\n{prompt}"
        return prefix

    async def shutdown(self) -> None:
        """Shutdown the agent service and release resources."""
        if not self._initialized:
            return

        logger.info("AgentService shutting down...")

        # Cancel background consolidation tasks.
        if self._async_consolidation_tasks:
            for task in list(self._async_consolidation_tasks):
                task.cancel()
            await asyncio.gather(*self._async_consolidation_tasks, return_exceptions=True)
            self._async_consolidation_tasks.clear()

        # Disconnect all clients
        await self._client_pool.disconnect_all()

        self._initialized = False
        logger.info("AgentService shutdown complete")

    async def reset_session(self, session_key: str) -> None:
        """Reset session state.

        Args:
            session_key: Session identifier
        """
        logger.info(f"Resetting session {session_key}")
        await self._client_pool.disconnect(session_key)

    async def get_session_commands(
        self,
        session_key: str,
        *,
        include_live_connected: bool = True,
        allow_connect: bool = False,
    ) -> list[str]:
        """Get available SDK slash commands for a session."""
        # Baseline SDK commands that should always be visible.
        commands: set[str] = {"/help", "/clear", "/compact"}
        sdk_discovered: set[str] = set()

        # Commands discovered from state manager (if any).
        sm = self._shared_resources.get("state_manager")
        if sm and hasattr(sm, "get_commands"):
            try:
                for cmd in sm.get_commands(session_key) or []:
                    if not isinstance(cmd, str):
                        continue
                    c = cmd.strip()
                    if not c:
                        continue
                    normalized = c if c.startswith("/") else f"/{c}"
                    commands.add(normalized)
                    sdk_discovered.add(normalized)
            except Exception as e:
                logger.debug("Failed to read state-manager commands for %s: %s", session_key, e)

        # Optional live SDK discovery from connected client only.
        if include_live_connected:
            record = self._client_pool._clients.get(session_key) if hasattr(self._client_pool, "_clients") else None
            if record is not None and getattr(record, "state", "") == "connected":
                try:
                    info = await record.client.get_server_info()
                    discovered = set(self._extract_slash_commands(info))
                    commands.update(discovered)
                    sdk_discovered.update(discovered)
                except Exception as e:
                    logger.debug("Failed to get_server_info() for %s: %s", session_key, e)

        # Optional fallback discovery that may create/connect a client.
        if allow_connect:
            try:
                client = await self._get_or_create_client(session_key)
                info = await client.get_server_info()
                discovered = set(self._extract_slash_commands(info))
                commands.update(discovered)
                sdk_discovered.update(discovered)
            except Exception as e:
                logger.debug("SDK command discovery with allow_connect failed for %s: %s", session_key, e)

        if sm and hasattr(sm, "set_commands") and sdk_discovered:
            try:
                sm.set_commands(session_key, sorted(sdk_discovered))
            except Exception as e:
                logger.debug("Failed to cache state-manager commands for %s: %s", session_key, e)

        return sorted(commands)

    def get_workspace_commands_summary(self) -> str:
        """Return formatted workspace commands summary for help output."""
        if not self._commands_loader:
            return ""
        try:
            return self._commands_loader.build_commands_summary() or ""
        except Exception as e:
            logger.debug("Failed to build workspace commands summary: %s", e)
            return ""

    @staticmethod
    def _extract_slash_commands(info: Any) -> list[str]:
        """Extract slash commands from SDK server info payload."""
        if not isinstance(info, dict):
            return []

        result: set[str] = set()

        slash_commands = info.get("slash_commands")
        if isinstance(slash_commands, list):
            for item in slash_commands:
                if isinstance(item, str) and item.strip():
                    raw = item.strip()
                    result.add(raw if raw.startswith("/") else f"/{raw}")

        commands = info.get("commands")
        if isinstance(commands, list):
            for item in commands:
                if isinstance(item, str) and item.strip():
                    raw = item.strip()
                    result.add(raw if raw.startswith("/") else f"/{raw}")
                elif isinstance(item, dict):
                    name = item.get("name")
                    if isinstance(name, str) and name.strip():
                        raw = name.strip()
                        result.add(raw if raw.startswith("/") else f"/{raw}")

        return sorted(result)

    async def _refresh_session_commands_from_client(self, session_key: str, client: Any) -> None:
        """Refresh and cache SDK commands from an already-connected client."""
        sm = self._shared_resources.get("state_manager")
        if not sm or not hasattr(sm, "set_commands"):
            return
        if hasattr(sm, "get_commands"):
            try:
                cached = sm.get_commands(session_key) or []
                if cached:
                    return
            except Exception:
                pass
        try:
            info = await client.get_server_info()
            discovered = self._extract_slash_commands(info)
            if discovered:
                sm.set_commands(session_key, discovered)
        except Exception as e:
            logger.debug("Failed to refresh SDK commands for %s: %s", session_key, e)

    async def interrupt_session(self, session_key: str) -> dict[str, Any]:
        """Interrupt ongoing processing for a session."""
        task = self._active_tasks.pop(session_key, None)
        interrupted = False
        if task and not task.done():
            task.cancel()
            interrupted = True
        await self._client_pool.disconnect(session_key)
        # Reset phase to IDLE
        sm = self._shared_resources.get("state_manager")
        if sm:
            sm.force_transition(session_key, SessionPhase.IDLE, reason="interrupted")
        return {"interrupted": interrupted, "usage": None}

    # === State Delegation ===

    def get_phase(self, session_key: str) -> SessionPhase:
        """Get current session phase (delegates to state_manager)."""
        sm = self._shared_resources.get("state_manager")
        if sm:
            return sm.get_phase(session_key)
        return SessionPhase.IDLE

    @asynccontextmanager
    async def transaction(self, session_key: str, validate_on_commit: bool = True):
        """Async context manager for transactional state changes (delegates to state_manager)."""
        sm = self._shared_resources.get("state_manager")
        if sm:
            async with sm.transaction(session_key, validate_on_commit=validate_on_commit) as tx:
                yield tx
        else:
            yield _NoOpTransaction()

    # === Event Publishing ===

    @staticmethod
    async def _publish_event(
        bus: Any,
        channel: str,
        chat_id: str,
        content: str,
        *,
        source_metadata: dict[str, Any] | None = None,
        event_data: dict[str, Any] | None = None,
        **metadata: Any,
    ) -> None:
        """Publish an outbound message with metadata (matches v0.3.37 _bus_progress)."""
        meta = dict(source_metadata or {})
        meta.update(metadata)
        if event_data is not None:
            meta["_event_data"] = event_data
        # Normalize _progress_kind from _event_type
        if "_event_type" in meta and "_progress_kind" not in meta:
            meta["_progress_kind"] = _progress_kind_from_event_type(
                meta["_event_type"],
                tool_hint=meta.get("_tool_hint", False),
            )
        await bus.publish_outbound(OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            metadata=meta,
        ))

    @staticmethod
    def _format_tool_hint(tool_calls: list[dict[str, Any]]) -> str:
        """Format tool calls into a readable hint string."""
        def _kind_label(kind: str) -> str:
            return {
                "tool": "Tool",
                "skill": "Skill",
                "mcp": "MCP",
            }.get(kind, "Tool")

        def _fmt(tc: dict[str, Any]) -> str:
            args = tc.get("input") or tc.get("arguments") or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            name = str(tc.get("name", "tool"))
            kind = str(tc.get("kind", "tool"))
            prefix = f"{_kind_label(kind)}: "
            if not isinstance(val, str):
                return prefix + name
            body = f'{name}("{val[:40]}…")' if len(val) > 40 else f'{name}("{val}")'
            return prefix + body

        return ", ".join(_fmt(tc) for tc in tool_calls) if tool_calls else "Using tools..."

    # === Internal Methods ===

    def _init_tool_adapter(self) -> None:
        """Initialize the ToolAdapter for built-in tools access."""
        try:
            from xbot.capabilities.tool_adapter import ToolAdapter

            workspace = self._shared_resources.get("workspace", ".")
            tools_config = self._shared_resources.get("tools_config")
            adapter = ToolAdapter(
                workspace=str(workspace),
                tools_config=tools_config,
                shared_resources=self._shared_resources,
            )
            adapter._ensure_core_tools_registered()
            self._tool_adapter = adapter
        except Exception as e:
            logger.warning("Failed to initialize ToolAdapter: %s", e)
            self._tool_adapter = None

    def _resolve_setting_sources(self, sdk_config: Any, run_mode: str) -> list[str] | None:
        """Resolve SDK setting_sources based on memory integration mode and run mode."""
        memory_integration = getattr(sdk_config, "memory_integration", None) if sdk_config else None
        if isinstance(memory_integration, dict):
            mode = memory_integration.get("mode", "auto")
            sources_cfg = memory_integration.get("setting_sources", {})
            key = "gateway" if run_mode == "gateway" else "cli"
            if mode == "off":
                return None
            return list(sources_cfg.get(key, ["user", "project", "local"]))

        mode = getattr(memory_integration, "mode", "auto")
        if not isinstance(mode, str):
            mode = "auto"
        if mode == "off":
            return None

        sources_cfg = getattr(memory_integration, "setting_sources", None)
        if sources_cfg is None:
            return ["user", "project", "local"]

        key = "gateway" if run_mode == "gateway" else "cli"
        return list(getattr(sources_cfg, key, ["user", "project", "local"]))

    def _log_memory_runtime_config(self, runtime_config: Any) -> None:
        """Log memory wiring details for observability."""
        sdk_cfg = getattr(getattr(runtime_config, "agents", None), "claude_sdk", None)
        memory_cfg = getattr(getattr(runtime_config, "tools", None), "memory", None)
        memory_provider = getattr(memory_cfg, "provider", "file")
        consolidation_mode = getattr(sdk_cfg, "memory_consolidation_mode", "off")
        run_mode = str(self._shared_resources.get("run_mode", "cli")).lower()
        setting_sources = self._resolve_setting_sources(sdk_cfg, run_mode)
        strategy = getattr(sdk_cfg, "system_prompt_strategy", None)
        if isinstance(strategy, dict):
            preset = strategy.get("preset", "xbot")
            append_xbot_prompt = strategy.get("append_xbot_prompt", True)
        else:
            preset = getattr(strategy, "preset", "xbot")
            append_xbot_prompt = getattr(strategy, "append_xbot_prompt", True)
        logger.info(
            "[AgentService] Memory runtime: run_mode=%s, memory_provider=%s, "
            "memory_consolidation_mode=%s, setting_sources=%s, system_prompt_strategy=%s/%s",
            run_mode,
            memory_provider,
            consolidation_mode,
            setting_sources,
            preset,
            append_xbot_prompt,
        )

    def _build_system_prompt(self) -> str:
        """Build the system prompt for the agent.

        Restores the ContextBuilder → system prompt link from v0.3.35
        (formerly in OptionsBuilder._build_system_prompt).

        Priority:
          1. Explicit system_prompt from AgentConfig (non-empty) — preserves old behaviour.
          2. ContextBuilder.build_system_prompt() when available.
          3. Bare-minimum fallback string.

        Returns:
            The complete system prompt string.
        """
        # Priority 1: explicit system_prompt in AgentConfig wins
        if self._config and self._config.system_prompt:
            logger.debug("[AgentService] Using explicit system_prompt from AgentConfig")
            return self._config.system_prompt

        # Priority 2: build via ContextBuilder
        if self._context_builder is not None:
            base_prompt = self._context_builder.build_system_prompt()
            identity_section = self._build_runtime_identity_section()
            if identity_section:
                base_prompt = f"{base_prompt}\n\n{identity_section}"
            logger.debug(
                "[AgentService] Built system prompt via ContextBuilder (%d chars)",
                len(base_prompt),
            )
            return base_prompt

        # Fallback
        logger.warning("[AgentService] ContextBuilder not available, using default system prompt")
        return "你是 xbot，一个智能助手。"

    def _build_runtime_identity_section(self) -> str:
        """Build runtime identity section appended to the system prompt.

        Reports the configured model and provider so the agent can answer
        “which model are you running?” accurately even when wrapped by an SDK.
        """
        config = self._shared_resources.get("config")
        if config is None:
            return ""

        defaults = getattr(getattr(config, "agents", None), "defaults", None)
        if defaults is None:
            return ""

        lines = [
            "## Runtime Identity",
            "",
            "- Agent name: `xbot`",
            "- Agent backend: `claude_sdk`",
            f"- Configured model: `{defaults.model}`",
            f"- Configured provider: `{defaults.provider}`",
            "",
            "When the user asks which model, provider, or agent is running, "
            "report the configured values above exactly.",
            "Do not infer or substitute a different model name from the surrounding SDK or toolchain.",
        ]
        return "\n".join(lines)

    async def _get_or_create_client(
        self,
        session_key: str,
    ) -> ClaudeSDKClient:
        """Get or create SDK client for session.

        Args:
            session_key: Session identifier

        Returns:
            ClaudeSDKClient instance
        """

        # Build options
        options = self._build_sdk_options()

        return await self._client_pool.get_or_create(session_key, options=options)

    def _build_sdk_options(self) -> Any:
        """Build ClaudeAgentOptions from configuration."""
        from claude_agent_sdk import ClaudeAgentOptions

        if not self._config:
            raise RuntimeError("AgentService not configured")

        # Build environment
        env = self._build_env_config()
        logger.info(f"[AgentService] Built env config: {list(env.keys())}")

        # Build MCP servers
        mcp_servers = self._build_mcp_servers()

        # Build agents
        agents = self._build_sdk_agents()

        # Read SDK-specific config for additional parameters
        config = self._shared_resources.get("config")
        sdk_config = getattr(getattr(config, "agents", None), "claude_sdk", None) if config else None

        # Build hooks (compact notification, etc.)
        hooks = self._build_hooks(sdk_config)

        # Expand workspace path (resolve ~ to actual home directory)
        workspace_raw = self._shared_resources.get("workspace", ".")
        workspace_expanded = str(Path(workspace_raw).expanduser().resolve())

        # Read SDK-specific parameters
        max_turns = getattr(sdk_config, "max_turns", 40) if sdk_config else 40
        permission_mode = getattr(sdk_config, "permission_mode", "acceptEdits") if sdk_config else "acceptEdits"
        disallowed_tools = getattr(sdk_config, "disallowed_tools", ["WebFetch", "WebSearch"]) if sdk_config else ["WebFetch", "WebSearch"]

        permission_handler = self._shared_resources.get("permission_handler")
        can_use_tool = None
        if permission_handler and hasattr(permission_handler, "build_can_use_tool_callback"):
            try:
                can_use_tool = permission_handler.build_can_use_tool_callback()
            except Exception as e:
                logger.warning("Failed to build can_use_tool callback: %s", e)

        add_dirs = self._build_skill_add_dirs(workspace_expanded)
        plugins = self._build_plugin_configs(workspace_expanded)

        # Build system prompt via ContextBuilder (restores v0.3.35 behaviour)
        system_prompt = self._build_system_prompt()

        options = ClaudeAgentOptions(
            cwd=workspace_expanded,
            model=self._config.model,
            system_prompt=system_prompt,
            mcp_servers=mcp_servers if mcp_servers else None,
            agents=agents,
            env=env,
            max_turns=max_turns,
            permission_mode=permission_mode,
            disallowed_tools=disallowed_tools,
            can_use_tool=can_use_tool,
            add_dirs=add_dirs,
            plugins=plugins,
            hooks=hooks,
            # Capture CLI stderr for debugging
            stderr=lambda line: logger.warning(f"[CLI stderr] {line}"),
        )
        logger.info(
            f"[AgentService] SDK options: model={options.model}, cwd={workspace_expanded}, "
            f"max_turns={max_turns}, permission_mode={permission_mode}, "
            f"env keys={list(options.env.keys()) if options.env else 'None'}, "
            f"add_dirs={len(add_dirs)}, plugins={len(plugins)}, "
            f"mcp_servers={len(mcp_servers)}"
        )
        return options

    def _build_env_config(self) -> dict[str, str]:
        """Build environment configuration for SDK.

        Sets ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL based on provider config.
        """
        env = {}

        # Get provider config from shared resources
        config = self._shared_resources.get("config")
        if not config:
            logger.warning("[AgentService] No config in shared_resources")
            return env

        # Get the active provider name
        provider_name = getattr(config.agents.defaults, "provider", None)
        logger.info(f"[AgentService] Provider name: {provider_name}")
        if not provider_name:
            logger.warning("[AgentService] No provider name configured")
            return env

        # Get provider-specific config
        providers = getattr(config, "providers", None)
        if not providers:
            logger.warning("[AgentService] No providers in config")
            return env

        # Try to get provider config (handles both snake_case and camelCase)
        provider_config = None
        for attr_name in [provider_name, provider_name.replace("_", ""), provider_name.replace("-", "_")]:
            provider_config = getattr(providers, attr_name, None)
            if provider_config:
                logger.info(f"[AgentService] Found provider config via attr: {attr_name}")
                break

        if provider_config:
            # Handle SecretStr type for api_key
            api_key = getattr(provider_config, "api_key", None)
            if api_key is not None:
                # SecretStr needs get_secret_value() to extract actual string
                if hasattr(api_key, "get_secret_value"):
                    api_key = api_key.get_secret_value()
                if api_key:  # Only set if non-empty
                    env["ANTHROPIC_API_KEY"] = str(api_key)
                    logger.info(f"[AgentService] Set ANTHROPIC_API_KEY (length: {len(api_key)})")

            api_base = getattr(provider_config, "api_base", None)
            if api_base:
                env["ANTHROPIC_BASE_URL"] = str(api_base)
                logger.info(f"[AgentService] Set ANTHROPIC_BASE_URL: {api_base}")
        else:
            logger.warning(f"[AgentService] Provider config not found for: {provider_name}")

        return env

    def _build_mcp_servers(self) -> dict[str, Any]:
        """Build MCP servers configuration.

        Converts Pydantic MCPServerConfig to JSON-serializable dicts.
        """
        configured_servers: dict[str, Any] = {}
        if self._config and self._config.mcp_servers:
            configured_servers = {
                name: server.model_dump() if hasattr(server, "model_dump") else server
                for name, server in self._config.mcp_servers.items()
            }

        # Merge xbot extension tools exposed as an SDK MCP server.
        xbot_server: dict[str, Any] = {}
        if self._tool_adapter is not None and hasattr(self._tool_adapter, "create_mcp_server"):
            try:
                xbot_server = self._tool_adapter.create_mcp_server() or {}
            except Exception as e:
                logger.warning("Failed to create xbot SDK MCP server: %s", e)
                xbot_server = {}

        if not xbot_server:
            return configured_servers

        merged = dict(configured_servers)
        for name, server in xbot_server.items():
            if name in merged:
                logger.warning(
                    "Configured MCP server '%s' overridden by xbot SDK MCP server",
                    name,
                )
            merged[name] = server
        return merged

    def _build_options(self, context: AgentContext) -> Any:
        """Build processing options for a context."""
        return self._build_sdk_options()

    def _build_hooks(self, sdk_config: Any) -> dict[str, list] | None:
        """Build hooks configuration including compact notification.

        Restores v0.3.37 PreCompact hook that was lost during migration.
        """
        import copy

        from claude_agent_sdk.types import HookMatcher

        # Start with user-configured hooks
        try:
            hooks: dict[str, list] = copy.deepcopy(getattr(sdk_config, "hooks", None) or {})
        except Exception:
            hooks = dict(getattr(sdk_config, "hooks", None) or {})

        # Add PreCompact hook if compact_notify is enabled
        compact_notify = getattr(sdk_config, "compact_notify", True) if sdk_config else True
        logger.info("[Hooks] Building hooks, compact_notify=%s", compact_notify)

        if compact_notify:
            from xbot.runtime.core.hooks import CompactHookHandler

            def send_compact_notification(session_ref: str, message: str) -> None:
                """Send compact notification to direct callback and/or bus."""
                # Resolve target channel/chat_id from state_manager
                sm = self._shared_resources.get("state_manager")
                resolved_target = None
                if sm and hasattr(sm, "resolve_compact_notification_target"):
                    try:
                        resolved_target = sm.resolve_compact_notification_target(session_ref)
                    except Exception as e:
                        logger.debug("[Compact Notification] resolve failed for '%s': %s", session_ref, e)

                if not self._is_valid_compact_target(resolved_target):
                    resolved_target = None

                resolved_session_key = (
                    str(resolved_target[0]) if resolved_target else str(session_ref)
                )
                bus = self._shared_resources.get("bus")

                async def _send() -> None:
                    # First priority: direct CLI callback (works without bus).
                    try:
                        handled = await self._emit_direct_progress_for_session(
                            resolved_session_key,
                            message,
                            event_type="system",
                            event_data={"subtype": "pre_compact"},
                        )
                        if handled:
                            return
                    except Exception as e:
                        logger.debug("Direct compact notification failed for %s: %s", resolved_session_key, e)

                    # Fallback: publish to bus for channel/interactive mode.
                    if bus is None:
                        logger.warning("[Compact Notification] No bus available for session: %s", session_ref)
                        return
                    if resolved_target is None:
                        logger.warning(
                            "[Compact Notification] No routing info for session_ref='%s'. "
                            "Notification will NOT be delivered.",
                            session_ref,
                        )
                        return
                    session_key, channel, chat_id = resolved_target
                    try:
                        await bus.publish_outbound(
                            OutboundMessage(
                                channel=channel,
                                chat_id=chat_id,
                                content=message,
                                metadata={
                                    "_progress": True,
                                    "_event_type": "system",
                                    "_progress_kind": "system",
                                    "_event_data": {"subtype": "pre_compact"},
                                },
                            )
                        )
                        logger.debug("Sent compact notification to %s:%s (session=%s)", channel, chat_id, session_key)
                    except Exception as e:
                        logger.warning("Failed to send compact notification to %s:%s: %s", channel, chat_id, e)

                try:
                    loop = asyncio.get_running_loop()
                    asyncio.ensure_future(_send(), loop=loop)
                except RuntimeError as e:
                    logger.warning("Cannot send compact notification for %s: no event loop: %s", session_ref, e)

            compact_handler = CompactHookHandler(
                enabled=True,
                message_callback=send_compact_notification,
            )
            hooks.setdefault("PreCompact", []).append(HookMatcher(hooks=[compact_handler]))
            logger.info("[Hooks] Added PreCompact hook, keys=%s", list(hooks.keys()))

        return hooks if hooks else None

    def _convert_event(self, event: Any) -> AgentResponse | None:
        """Convert SDK event to AgentResponse."""
        event_type = type(event).__name__

        if event_type == "AssistantMessage":
            return self._convert_assistant_message(event)
        elif event_type == "StreamEvent":
            return self._convert_stream_event(event)
        elif event_type == "TaskStartedMessage":
            return self._convert_task_started(event)
        elif event_type == "TaskProgressMessage":
            return self._convert_task_progress(event)
        elif event_type == "TaskNotificationMessage":
            return self._convert_task_notification(event)
        elif event_type == "ResultMessage":
            return self._convert_result_message(event)
        elif event_type == "SystemMessage":
            return self._convert_system_message(event)
        elif event_type == "RateLimitEvent":
            return self._convert_rate_limit_event(event)

        return None

    def _convert_assistant_message(self, message: Any) -> AgentResponse | None:
        """Convert AssistantMessage to AgentResponse."""
        text = ""
        progress_texts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for block in message.content:
            block_type = type(block).__name__
            if block_type == "TextBlock":
                text += block.text
            elif block_type == "ThinkingBlock":
                if block.thinking:
                    progress_texts.append(f"Thinking: {block.thinking}")
            elif block_type == "ToolUseBlock":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                    "kind": self._classify_tool_name(block.name),
                })

        event_type = ""
        event_data: dict[str, Any] | None = None
        if progress_texts and not text and not tool_calls:
            event_type = "thinking"
            event_data = {"thinking_chunks": len(progress_texts)}
        elif tool_calls:
            event_type = "tool_call"
            event_data = {"tool_calls": len(tool_calls)}
        elif text:
            event_type = "content"

        return AgentResponse(
            content=text,
            progress_texts=progress_texts,
            tool_calls=tool_calls if tool_calls else None,
            finish_reason="tool_use" if tool_calls else "stop",
            raw_message=message,
            event_type=event_type,
            event_data=event_data,
        )

    def _convert_stream_event(self, message: Any) -> AgentResponse | None:
        """Convert StreamEvent to AgentResponse."""
        event = message.event or {}
        if event.get("type") != "content_block_delta":
            return None

        delta = event.get("delta", {})
        delta_type = delta.get("type")

        if delta_type == "text_delta":
            text = delta.get("text", "")
            if not text:
                return None
            return AgentResponse(
                content="",
                is_delta=True,
                delta_content=text,
                raw_message=message,
                event_type="content_delta",
            )

        if delta_type == "thinking_delta":
            thinking = delta.get("thinking", "") or delta.get("text", "")
            if thinking:
                return AgentResponse(
                    content="",
                    progress_texts=[f"Thinking: {thinking}"],
                    raw_message=message,
                    event_type="thinking",
                )

        return None

    def _convert_task_started(self, message: Any) -> AgentResponse:
        """Convert TaskStartedMessage to AgentResponse."""
        progress_texts = [f"Running: {message.description}"] if getattr(message, "description", None) else []
        if self._handoff_policy:
            if handoff_trace := self._handoff_policy.format_task_trace(
                getattr(message, "description", ""),
                getattr(message, "task_type", None),
            ):
                progress_texts.append(handoff_trace)
        return AgentResponse(
            content="",
            progress_texts=progress_texts,
            raw_message=message,
            event_type="task",
            event_data={
                "status": "started",
                "task_id": getattr(message, "task_id", None),
                "task_type": getattr(message, "task_type", None),
            },
        )

    def _convert_task_progress(self, message: Any) -> AgentResponse:
        """Convert TaskProgressMessage to AgentResponse."""
        tool_calls = None
        last_tool_name = getattr(message, "last_tool_name", None)
        if last_tool_name:
            tool_calls = [{
                "name": last_tool_name,
                "input": {},
                "kind": self._classify_tool_name(last_tool_name),
            }]
        return AgentResponse(
            content="",
            progress_texts=[f"Running: {message.description}"] if getattr(message, "description", None) else [],
            tool_calls=tool_calls,
            finish_reason="tool_use" if tool_calls else "stop",
            raw_message=message,
            event_type="task",
            event_data={
                "status": "progress",
                "task_id": getattr(message, "task_id", None),
                "last_tool_name": last_tool_name,
            },
        )

    def _convert_task_notification(self, message: Any) -> AgentResponse:
        """Convert TaskNotificationMessage to AgentResponse."""
        progress_texts = [
            format_task_notification(
                status=getattr(message, "status", ""),
                summary=getattr(message, "summary", None),
                task_id=getattr(message, "task_id", None),
                output_file=getattr(message, "output_file", None),
            )
        ]
        if self._handoff_policy:
            if handoff_trace := self._handoff_policy.format_task_trace(
                str(getattr(message, "summary", None) or getattr(message, "status", "")),
            ):
                progress_texts.append(handoff_trace)
        return AgentResponse(
            content="",
            progress_texts=progress_texts,
            raw_message=message,
            event_type="task",
            event_data={
                "status": getattr(message, "status", None),
                "task_id": getattr(message, "task_id", None),
                "output_file": getattr(message, "output_file", None),
            },
        )

    def _convert_result_message(self, message: Any) -> AgentResponse | None:
        """Convert ResultMessage to AgentResponse."""
        usage = None
        if hasattr(message, "usage") and message.usage:
            usage_obj = message.usage
            if isinstance(usage_obj, dict):
                input_raw = (
                    usage_obj.get("input_tokens")
                    if "input_tokens" in usage_obj
                    else usage_obj.get("inputTokens", usage_obj.get("total_input_tokens", 0))
                )
                output_raw = (
                    usage_obj.get("output_tokens")
                    if "output_tokens" in usage_obj
                    else usage_obj.get("outputTokens", usage_obj.get("total_output_tokens", 0))
                )
            else:
                input_raw = (
                    getattr(usage_obj, "input_tokens", None)
                    if getattr(usage_obj, "input_tokens", None) is not None
                    else getattr(usage_obj, "inputTokens", getattr(usage_obj, "total_input_tokens", 0))
                )
                output_raw = (
                    getattr(usage_obj, "output_tokens", None)
                    if getattr(usage_obj, "output_tokens", None) is not None
                    else getattr(usage_obj, "outputTokens", getattr(usage_obj, "total_output_tokens", 0))
                )
            usage = {
                "input_tokens": int(input_raw or 0),
                "output_tokens": int(output_raw or 0),
            }

        content = message.result if isinstance(message.result, str) else ""
        return AgentResponse(
            content=content,
            finish_reason="stop",
            usage=usage,
            raw_message=message,
            event_type="result",
            event_data={
                "stop_reason": getattr(message, "stop_reason", None),
                "num_turns": getattr(message, "num_turns", None),
                "total_cost_usd": getattr(message, "total_cost_usd", None),
            },
        )

    def _convert_system_message(self, message: Any) -> AgentResponse | None:
        """Convert SystemMessage to AgentResponse.

        Handles compact-related system messages so they propagate as progress
        events through _dispatch → _publish_event → ChannelManager.
        """
        # Extract subtype from the message
        subtype = getattr(message, "subtype", None) or ""
        message_text = getattr(message, "message", None) or ""

        if subtype in ("compact_start", "pre_compact"):
            text = message_text or "\U0001f504 Compressing context..."
            return AgentResponse(
                content="",
                progress_texts=[text],
                event_type="system",
                event_data={"subtype": subtype},
            )

        if subtype in ("compact_boundary",):
            from xbot.interaction.event_formatter import format_compact_event
            data = getattr(message, "data", None)
            compact_meta = data.get("compact_metadata", {}) if isinstance(data, dict) else {}
            pre_tokens = compact_meta.get("pre_tokens")
            post_tokens = compact_meta.get("post_tokens")
            trigger = compact_meta.get("trigger")
            text = format_compact_event(
                pre_tokens=pre_tokens if isinstance(pre_tokens, int) else None,
                post_tokens=post_tokens if isinstance(post_tokens, int) else None,
                trigger=trigger if isinstance(trigger, str) else None,
            )
            return AgentResponse(
                content="",
                progress_texts=[text],
                event_type="system",
                event_data={"subtype": subtype, "compact_metadata": compact_meta},
            )

        if subtype in ("compact_complete", "post_compact"):
            from xbot.interaction.event_formatter import format_compact_event
            data = getattr(message, "data", None)
            compact_meta = data.get("compact_metadata", {}) if isinstance(data, dict) else {}
            pre_tokens = getattr(message, "pre_tokens", None)
            post_tokens = getattr(message, "post_tokens", None)
            trigger = getattr(message, "trigger", None)
            if pre_tokens is None:
                pre_tokens = compact_meta.get("pre_tokens")
            if post_tokens is None:
                post_tokens = compact_meta.get("post_tokens")
            if trigger is None:
                trigger = compact_meta.get("trigger")
            text = format_compact_event(
                pre_tokens=pre_tokens, post_tokens=post_tokens, trigger=trigger,
            )
            return AgentResponse(
                content="",
                progress_texts=[text],
                event_type="system",
                event_data={"subtype": subtype, "compact_metadata": compact_meta if compact_meta else None},
            )

        # Other SystemMessage subtypes — log but don't discard silently
        if message_text:
            logger.debug("[SystemMessage] subtype=%s, message=%s", subtype, message_text[:120])
            return AgentResponse(
                content="",
                progress_texts=[message_text],
                event_type="system",
                event_data={"subtype": subtype} if subtype else None,
            )

        return None

    def _convert_rate_limit_event(self, message: Any) -> AgentResponse | None:
        """Convert RateLimitEvent to AgentResponse."""
        return AgentResponse(
            content="",
            progress_texts=[format_rate_limit_event(getattr(message, "rate_limit_info", None))],
            raw_message=message,
            event_type="rate_limit",
            event_data={
                "status": getattr(getattr(message, "rate_limit_info", None), "status", None),
                "rate_limit_type": getattr(getattr(message, "rate_limit_info", None), "rate_limit_type", None),
                "resets_at": getattr(getattr(message, "rate_limit_info", None), "resets_at", None),
                "utilization": getattr(getattr(message, "rate_limit_info", None), "utilization", None),
            },
        )

    def _classify_tool_name(self, name: str) -> str:
        """Classify a tool name into its kind."""
        normalized = canonical_tool_name(name)
        if normalized.startswith("mcp_"):
            return "mcp"
        if normalized.startswith("skill_"):
            return "skill"
        if normalized in CapabilityCatalog.builtin_tool_names():
            return "tool"
        return "tool"

    def _build_sdk_agents(self) -> dict[str, Any] | None:
        """Build SDK agent definitions from configuration.

        This method preserves the agents configuration for SDK subagent support.

        Returns:
            Dict of agent definitions or None
        """
        if not self._config or not self._config.agents:
            return None

        from claude_agent_sdk.types import AgentDefinition

        policy = CapabilityPolicy(
            CapabilityCatalog(self._shared_resources.get("workspace", ".")),
            mcp_servers=self._config.mcp_servers or {},
        )

        agents: dict[str, AgentDefinition] = {}
        for agent_def in self._config.agents:
            name = agent_def.get("name", "unknown")
            configured_tools = agent_def.get("tools")
            resolved = policy.resolve_agent_tools(configured_tools, backend="claude_sdk")
            mapped_tools = self._map_agent_tools_to_sdk_names(resolved.allowed)
            if resolved.dropped:
                logger.warning(
                    "Agent '%s' dropped unsupported tools: %s",
                    name,
                    resolved.dropped,
                )
            agents[name] = AgentDefinition(
                description=agent_def.get("description", ""),
                prompt=agent_def.get("prompt", ""),
                tools=mapped_tools if configured_tools is not None else None,
                model=agent_def.get("model"),
            )
        return agents

    def _set_runtime_tool_and_permission_context(self, context: AgentContext) -> None:
        """Propagate current session routing to tool adapter and permission handler."""
        if self._tool_adapter is not None and hasattr(self._tool_adapter, "set_tool_context"):
            try:
                self._tool_adapter.set_tool_context(
                    channel=context.channel,
                    chat_id=context.chat_id,
                    session_key=context.session_key,
                )
            except Exception as e:
                logger.debug("Failed to set tool adapter context for %s: %s", context.session_key, e)

        permission_handler = self._shared_resources.get("permission_handler")
        if not permission_handler:
            return
        try:
            if hasattr(permission_handler, "set_session_context"):
                permission_handler.set_session_context(
                    context.session_key,
                    context.channel,
                    context.chat_id,
                    context.metadata or {},
                )
            if hasattr(permission_handler, "set_current_session"):
                permission_handler.set_current_session(context.session_key)
        except Exception as e:
            logger.debug("Failed to set permission context for %s: %s", context.session_key, e)

    def _clear_runtime_tool_and_permission_context(self, session_key: str) -> None:
        """Best-effort cleanup for per-session tool/permission contexts."""
        if self._tool_adapter is not None:
            for method_name in ("clear_context",):
                method = getattr(self._tool_adapter, method_name, None)
                if callable(method):
                    try:
                        method(session_key)
                    except Exception as e:
                        logger.debug("Failed to clear tool adapter context for %s: %s", session_key, e)

        permission_handler = self._shared_resources.get("permission_handler")
        if permission_handler and hasattr(permission_handler, "clear_session_context"):
            try:
                permission_handler.clear_session_context(session_key)
            except Exception as e:
                logger.debug("Failed to clear permission context for %s: %s", session_key, e)

    def _build_skill_add_dirs(self, workspace: str) -> list[str]:
        """Build SDK add_dirs from config.skills."""
        config = self._shared_resources.get("config")
        skills_config = getattr(config, "skills", None) if config else None
        if not skills_config or getattr(skills_config, "enabled", True) is False:
            return []

        values = []
        values.extend(self._coerce_str_list(getattr(skills_config, "dirs", None)))
        values.extend(self._coerce_str_list(getattr(skills_config, "additional_dirs", None)))

        resolved: list[str] = []
        for value in values:
            path = self._resolve_config_path(value, workspace)
            if path not in resolved:
                resolved.append(path)
        return resolved

    def _build_plugin_configs(self, workspace: str) -> list[dict[str, str]]:
        """Build SDK local plugin configs from config.plugins."""
        config = self._shared_resources.get("config")
        plugins_config = getattr(config, "plugins", None) if config else None
        if not plugins_config or getattr(plugins_config, "enabled", True) is False:
            return []

        plugin_dirs = [
            Path(self._resolve_config_path(value, workspace))
            for value in self._coerce_str_list(getattr(plugins_config, "dirs", None))
        ]
        if not plugin_dirs:
            return []

        enabled_plugins = set(self._coerce_str_list(getattr(plugins_config, "enabled_plugins", None)))
        disabled_plugins = set(self._coerce_str_list(getattr(plugins_config, "disabled_plugins", None)))

        plugin_paths: list[dict[str, str]] = []
        for base_dir in plugin_dirs:
            if not base_dir.exists() or not base_dir.is_dir():
                continue
            for child in sorted(base_dir.iterdir()):
                if not child.is_dir():
                    continue
                name = child.name
                if enabled_plugins and name not in enabled_plugins:
                    continue
                if name in disabled_plugins:
                    continue
                plugin_paths.append({"type": "local", "path": str(child.resolve())})
        return plugin_paths

    @staticmethod
    def _coerce_str_list(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        result: list[str] = []
        for value in values:
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    result.append(normalized)
        return result

    @staticmethod
    def _resolve_config_path(raw_path: str, workspace: str) -> str:
        path_value = raw_path.replace("$workspace", workspace)
        path_obj = Path(path_value).expanduser()
        if not path_obj.is_absolute():
            path_obj = Path(workspace) / path_obj
        return str(path_obj.resolve())

    def _map_agent_tools_to_sdk_names(self, tools: list[str]) -> list[str]:
        mapped: list[str] = []
        for tool_name in tools:
            mapped_name = self._map_tool_name_to_sdk(tool_name)
            if mapped_name and mapped_name not in mapped:
                mapped.append(mapped_name)
        return mapped

    def _map_tool_name_to_sdk(self, tool_name: str) -> str:
        canonical = canonical_tool_name(tool_name)
        if canonical in _XBOT_EXTENSION_TO_MCP_TOOL:
            return _XBOT_EXTENSION_TO_MCP_TOOL[canonical]
        if canonical.startswith("mcp__"):
            return canonical
        if canonical.startswith("mcp_"):
            return canonical
        if canonical in _SDK_NATIVE_TOOL_NAME_MAP:
            return _SDK_NATIVE_TOOL_NAME_MAP[canonical]
        if canonical in _SDK_NATIVE_TOOL_NAMES:
            return canonical
        # Unknown non-MCP tools default to xbot MCP namespace.
        return f"mcp__{_XBOT_MCP_SERVER_NAME}__{canonical}"

    # === CLI-compatible methods (migrated from AgentRuntime) ===

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: ProgressCallback | None = None,
        media: list[str] | None = None,
    ) -> str:
        """Process a message directly (no bus required).

        This is the CLI-compatible version that replaces AgentRuntime.process_direct().

        Args:
            content: Message content
            session_key: Session identifier
            channel: Channel name
            chat_id: Chat identifier
            on_progress: Optional progress callback
            media: Optional media attachments

        Returns:
            Response content as string
        """
        if on_progress is not None:
            self._register_direct_progress_callback(session_key, on_progress)

        context = AgentContext(
            session_key=session_key,
            prompt=content,
            channel=channel,
            chat_id=chat_id,
            media=media or [],
        )

        try:
            result = []
            async for response in self.process(context):
                if on_progress and response.progress_texts:
                    for text in response.progress_texts:
                        await self._emit_progress(
                            on_progress,
                            text,
                            tool_hint=False,
                            event_type=response.event_type or "progress",
                            event_data=response.event_data,
                        )
                if on_progress and response.tool_hint_text:
                    await self._emit_progress(
                        on_progress,
                        response.tool_hint_text,
                        tool_hint=True,
                        event_type="tool_hint",
                    )
                if on_progress and response.tool_calls:
                    await self._emit_progress(
                        on_progress,
                        self._format_tool_hint(response.tool_calls),
                        tool_hint=True,
                        event_type="tool_call",
                        event_data={"tool_calls": response.tool_calls},
                    )
                if on_progress and response.is_delta and response.delta_content:
                    await self._emit_progress(
                        on_progress,
                        response.delta_content,
                        event_type=response.event_type or "content_delta",
                        event_data=response.event_data,
                    )
                if response.is_delta:
                    result.append(response.delta_content)
                elif response.content:
                    result.append(response.content)

            return "".join(result)
        finally:
            self._unregister_direct_progress_callback(session_key, on_progress)

    async def run(self) -> None:
        """Run the service with message bus integration.

        Message routing loop:
        1. Permission/interaction responses → response_handlers
        2. Local commands (!help, !stop, etc.) → _handle_local_command
        3. Busy sessions → reject with "processing" hint
        4. Normal messages → _dispatch() as background task
        """
        bus = self._shared_resources.get("bus")
        if bus is None:
            raise RuntimeError("AgentService.run() requires a bus in shared_resources")

        self._running = True
        logger.info("Agent service started")

        while self._running:
            try:
                msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: %s", e)
                continue

            try:
                session_key = msg.session_key or f"{msg.channel}:{msg.chat_id}"

                # 1. Permission response routing
                if self._response_handlers:
                    try:
                        if await self._response_handlers.handle_permission_response(msg):
                            continue
                    except Exception as e:
                        logger.warning("Error in handle_permission_response: %s", e)

                # 2. Interaction response routing
                if self._response_handlers:
                    try:
                        if await self._response_handlers.handle_interaction_response(msg):
                            continue
                    except Exception as e:
                        logger.warning("Error in handle_interaction_response: %s", e)

                # 3. Local command handling
                if self._command_handler and self._command_handler.is_local_command(msg.content):
                    await self._command_handler.handle(msg, bus)
                    continue

                # 4. Busy session detection
                active_task = self._active_tasks.get(session_key)
                if active_task and not active_task.done():
                    await self._publish_event(
                        bus, msg.channel, msg.chat_id,
                        "\u23f3 \u6b63\u5728\u5904\u7406\u4e2d\uff0c\u8bf7\u7a0d\u5019...",
                        _progress=True,
                    )
                    continue

                # 5. Dispatch as background task
                task = asyncio.create_task(self._dispatch(msg, bus))
                self._active_tasks[session_key] = task

            except Exception as e:
                logger.exception("Error in run loop: %s", e)

    async def _dispatch(self, msg: InboundMessage, bus: Any) -> None:
        """Complete processing chain for a single inbound message."""
        session_key = msg.session_key or f"{msg.channel}:{msg.chat_id}"

        try:
            # Ensure routing exists before processing so hooks can resolve targets.
            self._set_session_routing(session_key, msg.channel, msg.chat_id)

            # Transition to RUNNING
            sm = self._shared_resources.get("state_manager")
            if sm:
                sm.force_transition(session_key, SessionPhase.RUNNING, reason="dispatch_start")

            # Workspace command injection
            prompt = msg.content
            if self._commands_loader and self._commands_loader.is_command(prompt):
                cmd_name = self._commands_loader.get_command_from_text(prompt)
                if cmd_name:
                    cmd_content = self._commands_loader.load_command(cmd_name)
                    if cmd_content:
                        prompt = cmd_content

            context = AgentContext(
                session_key=session_key,
                prompt=prompt,
                channel=msg.channel,
                chat_id=msg.chat_id,
                media=msg.media or [],
            )

            response_text: list[str] = []
            last_usage: dict[str, Any] | None = None

            async for response in self.process(context):
                # Forward thinking/progress
                if response.progress_texts:
                    evt_type = response.event_type or "thinking"
                    evt_data = response.event_data
                    for text in response.progress_texts:
                        await self._publish_event(
                            bus, msg.channel, msg.chat_id, text,
                            source_metadata=msg.metadata,
                            event_data=evt_data,
                            _progress=True, _event_type=evt_type,
                        )

                # Forward explicit tool-hint text
                if response.tool_hint_text:
                    await self._publish_event(
                        bus, msg.channel, msg.chat_id, response.tool_hint_text,
                        source_metadata=msg.metadata,
                        _tool_hint=True, _progress=True, _event_type="tool_hint",
                    )

                # Forward tool hints
                if response.tool_calls:
                    hint = self._format_tool_hint(response.tool_calls)
                    await self._publish_event(
                        bus, msg.channel, msg.chat_id, hint,
                        source_metadata=msg.metadata,
                        event_data={"tool_calls": response.tool_calls},
                        _tool_hint=True, _progress=True, _event_type="tool_call",
                    )

                # Forward content deltas to progress stream
                if response.is_delta and response.delta_content:
                    await self._publish_event(
                        bus, msg.channel, msg.chat_id, response.delta_content,
                        source_metadata=msg.metadata,
                        event_data=response.event_data,
                        _progress=True,
                        _event_type=response.event_type or "content_delta",
                    )

                # Accumulate final content
                if response.is_delta:
                    response_text.append(response.delta_content)
                elif response.content:
                    response_text.append(response.content)

                # Track usage
                if response.usage:
                    last_usage = response.usage

            # Send usage summary
            if last_usage:
                from xbot.interaction.event_formatter import format_usage_summary
                usage_text = format_usage_summary(last_usage)
                if usage_text:
                    await self._publish_event(
                        bus, msg.channel, msg.chat_id, usage_text,
                        source_metadata=msg.metadata,
                        _event_type="usage", _progress=True,
                    )

            # Send final response
            if response_text:
                await bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="".join(response_text),
                ))

            # Session persistence
            sess_mgr = self._shared_resources.get("session_manager")
            if sess_mgr and hasattr(sess_mgr, "get_or_create"):
                try:
                    session = sess_mgr.get_or_create(session_key)
                    session.add_message("user", msg.content)
                    if response_text:
                        session.add_message("assistant", "".join(response_text))
                    sess_mgr.save(session)
                except Exception as e:
                    logger.warning("Failed to persist session: %s", e)

        except asyncio.CancelledError:
            logger.info(f"Dispatch cancelled for {session_key}")
            raise
        except Exception as e:
            logger.exception("Error in dispatch for %s: %s", session_key, e)
            try:
                await bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"\u274c \u5904\u7406\u51fa\u9519: {e}",
                ))
            except Exception:
                pass
        finally:
            # Restore phase (matches v0.3.37 _dispatch finally logic)
            sm = self._shared_resources.get("state_manager")
            if sm:
                bus_obj = self._shared_resources.get("bus")
                has_pending_permission = False
                has_pending_interaction = False
                if bus_obj:
                    has_pending_permission = bool(
                        bus_obj.get_pending_request_for_session(session_key)
                    )
                    if hasattr(bus_obj, "get_pending_interaction_for_session"):
                        has_pending_interaction = bool(
                            bus_obj.get_pending_interaction_for_session(session_key)
                        )
                if has_pending_permission:
                    target = SessionPhase.WAITING_PERMISSION
                    reason = "pending_permission"
                elif has_pending_interaction:
                    target = SessionPhase.WAITING_INTERACTION
                    reason = "pending_interaction"
                else:
                    target = SessionPhase.IDLE
                    reason = "dispatch_end"
                sm.force_transition(session_key, target, reason=reason)
            self._active_tasks.pop(session_key, None)

    @staticmethod
    def _is_valid_compact_target(resolved_target: Any) -> bool:
        return (
            isinstance(resolved_target, tuple)
            and len(resolved_target) == 3
            and all(isinstance(part, str) and part for part in resolved_target)
        )

    async def _trigger_memory_consolidation(self, session_key: str, session: Any) -> None:
        """Trigger memory consolidation according to configured mode."""
        if self._memory_consolidator is None:
            return

        runtime_config = self._shared_resources.get("config")
        sdk_cfg = getattr(getattr(runtime_config, "agents", None), "claude_sdk", None)
        mode = getattr(sdk_cfg, "memory_consolidation_mode", "off")
        if mode == "off":
            return

        if mode == "sync":
            await self._memory_consolidator.maybe_consolidate_by_tokens(session)
            return

        if mode == "async":
            async def _safe_consolidate() -> None:
                try:
                    await self._memory_consolidator.maybe_consolidate_by_tokens(session)
                except asyncio.CancelledError:
                    logger.debug("Memory consolidation cancelled for session %s", session_key)
                    raise
                except Exception as e:
                    logger.warning("Async memory consolidation failed for %s: %s", session_key, e)

            task = asyncio.create_task(_safe_consolidate(), name=f"memory-consolidation:{session_key}")
            self._async_consolidation_tasks.add(task)
            task.add_done_callback(lambda t: self._async_consolidation_tasks.discard(t))

    def _set_session_routing(self, session_key: str, channel: str, chat_id: str) -> None:
        """Persist runtime routing for compact hook delivery."""
        sm = self._shared_resources.get("state_manager")
        if sm and hasattr(sm, "set_routing"):
            try:
                sm.set_routing(session_key, channel, chat_id)
            except Exception as e:
                logger.debug("Failed to set routing for %s: %s", session_key, e)

    def _sync_sdk_session_mapping(self, session_key: str, message: Any) -> None:
        """Capture SDK session UUID from stream messages for hook routing."""
        sm = self._shared_resources.get("state_manager")
        if sm is None:
            return

        # Cache slash commands from SDK init messages.
        try:
            subtype = getattr(message, "subtype", None)
            data = getattr(message, "data", None)
            if subtype == "init" and isinstance(data, dict):
                discovered = self._extract_slash_commands(data)
                if discovered and hasattr(sm, "set_commands"):
                    sm.set_commands(session_key, discovered)
        except Exception as e:
            logger.debug("Failed to sync init commands for %s: %s", session_key, e)

        sdk_session_id = getattr(message, "session_id", None)
        if not sdk_session_id:
            data = getattr(message, "data", None)
            if isinstance(data, dict):
                sdk_session_id = data.get("session_id")
        if not sdk_session_id:
            return

        set_sdk_impl = getattr(sm, "_set_sdk_session_id_impl", None)
        if callable(set_sdk_impl):
            try:
                set_sdk_impl(session_key, str(sdk_session_id))
                return
            except Exception as e:
                logger.debug("Failed to sync sdk_session_id for %s: %s", session_key, e)

        set_sdk = getattr(sm, "set_sdk_session_id", None)
        if callable(set_sdk):
            try:
                result = set_sdk(session_key, str(sdk_session_id))
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as e:
                logger.debug("Failed to sync sdk_session_id for %s: %s", session_key, e)

    def _register_direct_progress_callback(
        self,
        session_key: str,
        callback: ProgressCallback,
    ) -> None:
        self._direct_progress_callbacks[session_key] = callback

    def _unregister_direct_progress_callback(
        self,
        session_key: str,
        callback: ProgressCallback | None,
    ) -> None:
        if callback is None:
            return
        current = self._direct_progress_callbacks.get(session_key)
        if current is callback:
            self._direct_progress_callbacks.pop(session_key, None)

    async def _emit_progress(
        self,
        on_progress: ProgressCallback | None,
        text: str,
        *,
        tool_hint: bool = False,
        event_type: str = "progress",
        event_data: dict[str, Any] | None = None,
    ) -> None:
        if on_progress is None:
            return
        kwargs = {
            "tool_hint": tool_hint,
            "event_type": event_type,
            "event_data": event_data,
        }
        if asyncio.iscoroutinefunction(on_progress):
            try:
                await on_progress(text, **kwargs)
            except TypeError:
                await on_progress(text)
            return
        try:
            await asyncio.to_thread(on_progress, text, **kwargs)
        except TypeError:
            await asyncio.to_thread(on_progress, text)

    async def _emit_direct_progress_for_session(
        self,
        session_key: str,
        content: str,
        *,
        event_type: str,
        event_data: dict[str, Any] | None = None,
    ) -> bool:
        callback = self._direct_progress_callbacks.get(session_key)
        if callback is None:
            return False
        await self._emit_progress(
            callback,
            content,
            event_type=event_type,
            event_data=event_data,
        )
        return True

    def stop(self) -> None:
        """Stop the service."""
        self._running = False
        logger.info("Agent service stopping...")

    async def close_mcp(self) -> None:
        """Close MCP connections and cleanup resources."""
        logger.info("Closing MCP connections...")
        await self._client_pool.disconnect_all()

    @property
    def channels_config(self) -> Any:
        """Get channels configuration (for CLI compatibility)."""
        config = self._shared_resources.get("config")
        if config and hasattr(config, "channels"):
            return config.channels
        return None

    @property
    def tools(self) -> Any:
        """Get tool adapter for accessing built-in tools (cron, message, etc.).

        Returns:
            ToolAdapter instance with .get(name) method, or empty dict fallback
        """
        if self._tool_adapter is not None:
            return self._tool_adapter
        return {}

    @property
    def backend(self) -> "AgentService":
        """Return self as backend for CLI compatibility.

        This allows agent.backend.call_for_auxiliary() to work.
        """
        return self

    async def process_managed_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: ProgressCallback | None = None,
        media: list[str] | None = None,
    ) -> str:
        """Process a message with managed state (for CLI compatibility).

        This is similar to process_direct but with state management.
        For now, delegates to process_direct.

        Args:
            content: Message content
            session_key: Session identifier
            channel: Channel name
            chat_id: Chat identifier
            on_progress: Optional progress callback
            media: Optional media attachments

        Returns:
            Response content as string
        """
        return await self.process_direct(
            content=content,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
            on_progress=on_progress,
            media=media,
        )

    async def call_for_structured(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> StructuredLLMResponse:
        """Execute a structured LLM call with messages and tools.

        Used by heartbeat, evaluator, and memory consolidation for
        single-turn tool-use calls that bypass the interactive SDK session.

        Args:
            messages: Chat messages (system/user/assistant roles)
            tools: Tool definitions (OpenAI-style or Anthropic-style)
            tool_choice: Tool choice strategy
            max_tokens: Max output tokens
            temperature: Sampling temperature

        Returns:
            StructuredLLMResponse with content and optional tool calls
        """
        import httpx

        env = self._build_env_config()
        api_key = env.get("ANTHROPIC_API_KEY")
        base_url = env.get("ANTHROPIC_BASE_URL")

        if not api_key:
            return StructuredLLMResponse(
                content="Error: No API key configured",
                finish_reason="error",
            )

        # Separate system messages from conversation messages
        system_parts: list[str] = []
        non_system: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system":
                system_parts.append(msg.get("content", ""))
            else:
                non_system.append(msg)

        # Build request payload
        payload: dict[str, Any] = {
            "model": self._config.model if self._config else "claude-sonnet-4-5",
            "messages": non_system,
            "max_tokens": max_tokens or 4096,
        }

        if system_parts:
            payload["system"] = "\n".join(system_parts)

        if tools:
            payload["tools"] = tools

        if tool_choice:
            payload["tool_choice"] = tool_choice

        if temperature is not None:
            payload["temperature"] = temperature

        # Build headers
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        # Build URL
        api_base = base_url or "https://api.anthropic.com"
        url = f"{api_base.rstrip('/')}/v1/messages"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()

            # Parse response
            content_blocks = data.get("content", [])
            text_parts: list[str] = []
            tool_calls = []

            for block in content_blocks:
                block_type = block.get("type")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "input": block.get("input", {}),
                    })

            return StructuredLLMResponse(
                content="\n".join(text_parts) if text_parts else "",
                tool_calls=tool_calls if tool_calls else None,
                finish_reason=data.get("stop_reason", "end_turn"),
            )
        except Exception as e:
            logger.error("Structured LLM call failed: %s", e)
            return StructuredLLMResponse(
                content=f"Error: {e}",
                finish_reason="error",
            )

    async def call_for_consolidation(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
    ) -> StructuredLLMResponse:
        """Execute a structured LLM call for memory consolidation.

        Delegates to call_for_structured with consolidation defaults.

        Args:
            messages: Chat messages
            tools: Tool definitions
            tool_choice: Tool choice strategy

        Returns:
            StructuredLLMResponse with content and optional tool calls
        """
        return await self.call_for_structured(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=2048,
            temperature=0.0,
        )

    async def call_for_auxiliary(
        self,
        prompt: str,
        *,
        session_key: str = "auxiliary",
        model: str | None = None,
    ) -> str:
        """Execute a standalone prompt (for CLI compatibility).

        Args:
            prompt: Prompt to execute
            session_key: Session identifier
            model: Optional model override

        Returns:
            Response content as string
        """
        context = AgentContext(
            session_key=session_key,
            prompt=prompt,
        )

        result = []
        async for response in self.process(context):
            if response.content:
                result.append(response.content)

        return "".join(result)

    async def call_for_structured(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> StructuredLLMResponse:
        """Execute a structured LLM call with messages and tools.

        Used by heartbeat, evaluator, and memory consolidation for
        single-turn tool-use calls that bypass the interactive SDK session.

        Args:
            messages: Chat messages (system/user/assistant roles)
            tools: Tool definitions (OpenAI-style or Anthropic-style)
            tool_choice: Tool choice strategy
            max_tokens: Max output tokens
            temperature: Sampling temperature

        Returns:
            StructuredLLMResponse with content and optional tool calls
        """
        import httpx

        env = self._build_env_config()
        api_key = env.get("ANTHROPIC_API_KEY")
        base_url = env.get("ANTHROPIC_BASE_URL")

        if not api_key:
            return StructuredLLMResponse(
                content="Error: No API key configured",
                finish_reason="error",
            )

        # Separate system messages from conversation messages
        system_parts: list[str] = []
        non_system: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system":
                system_parts.append(msg.get("content", ""))
            else:
                non_system.append(msg)

        # Convert OpenAI-style tools to Anthropic format
        anthropic_tools = None
        if tools:
            anthropic_tools = []
            for tool in tools:
                if "function" in tool:
                    func = tool["function"]
                    anthropic_tools.append({
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {}),
                    })
                else:
                    anthropic_tools.append(tool)

        # Convert tool_choice to Anthropic format
        anthropic_tool_choice = None
        if tool_choice:
            if isinstance(tool_choice, str):
                anthropic_tool_choice = {"type": tool_choice}
            elif isinstance(tool_choice, dict):
                if "function" in tool_choice:
                    anthropic_tool_choice = {
                        "type": "tool",
                        "name": tool_choice["function"]["name"],
                    }
                else:
                    anthropic_tool_choice = tool_choice

        model = self._config.model if self._config else "claude-sonnet-4-20250514"

        payload: dict[str, Any] = {
            "model": model,
            "messages": non_system,
            "max_tokens": max_tokens or 1024,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if anthropic_tools:
            payload["tools"] = anthropic_tools
        if anthropic_tool_choice:
            payload["tool_choice"] = anthropic_tool_choice
        if temperature is not None:
            payload["temperature"] = temperature

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        base = base_url or "https://api.anthropic.com"

        try:
            async with httpx.AsyncClient(
                base_url=base, headers=headers, timeout=60.0,
            ) as client:
                resp = await client.post("/v1/messages", json=payload)
                resp.raise_for_status()
                data = resp.json()

            content_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            for block in data.get("content", []):
                if block.get("type") == "text":
                    content_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tool_calls.append(ToolCall(
                        name=block.get("name", ""),
                        arguments=block.get("input", {}),
                    ))

            return StructuredLLMResponse(
                content="".join(content_parts),
                finish_reason="tool_use" if tool_calls else (data.get("stop_reason") or "stop"),
                tool_calls=tool_calls,
            )
        except httpx.HTTPStatusError as e:
            error_body = ""
            try:
                error_body = e.response.json().get("error", {}).get("message", str(e))
            except Exception:
                error_body = str(e)
            logger.error("Structured LLM call HTTP error: %s", error_body)
            return StructuredLLMResponse(
                content=f"Error: {error_body}",
                finish_reason="error",
            )
        except Exception as e:
            logger.error("Structured LLM call failed: %s", e)
            return StructuredLLMResponse(
                content=f"Error: {e}",
                finish_reason="error",
            )

    async def call_for_consolidation(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
    ) -> StructuredLLMResponse:
        """Execute a structured LLM call for memory consolidation.

        Delegates to call_for_structured with consolidation defaults.

        Args:
            messages: Chat messages
            tools: Tool definitions
            tool_choice: Tool choice strategy

        Returns:
            StructuredLLMResponse with content and optional tool calls
        """
        return await self.call_for_structured(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=2048,
            temperature=0.0,
        )
