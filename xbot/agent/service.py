"""Unified Agent Service.

This module provides the single entry point for all agent operations,
combining the core logic from ClaudeSDKBackend and AgentRuntime.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from xbot.logging import get_logger
from xbot.agent.protocol import AgentContext, AgentResponse, StructuredLLMResponse, ToolCall
from xbot.agent.types import AgentConfig
from xbot.agent.client_pool import ClientPool
from xbot.agent.capabilities.handoff import HandoffPolicy
from xbot.agent.state.machine import SessionPhase
from xbot.agent.command_handlers import LocalCommandHandler, LOCAL_COMMANDS
from xbot.bus.events import InboundMessage, OutboundMessage

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

        # Initialize tool adapter for built-in tools (cron, message, etc.)
        self._init_tool_adapter()

        # Initialize response handlers for permission/interaction routing
        try:
            from xbot.agent.interaction.response_handlers import RuntimeResponseHandlers
            self._response_handlers = RuntimeResponseHandlers(self)
        except Exception as e:
            logger.warning("Failed to initialize RuntimeResponseHandlers: %s", e)

        # Initialize commands loader for workspace slash commands
        workspace = self._shared_resources.get("workspace")
        if workspace:
            try:
                from xbot.agent.context.commands import CommandsLoader
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

        # Get or create client
        client = await self._get_or_create_client(context.session_key)

        # Process through SDK using query + receive_messages pattern
        try:
            # Send the query - SDK accepts string directly
            logger.info(f"[AgentService] Sending query for {context.session_key}")
            await asyncio.wait_for(client.query(context.prompt), timeout=30.0)
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

    async def shutdown(self) -> None:
        """Shutdown the agent service and release resources."""
        if not self._initialized:
            return

        logger.info("AgentService shutting down...")

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

    async def get_session_commands(self, session_key: str) -> list[str]:
        """Get available slash commands for a session."""
        commands = sorted(LOCAL_COMMANDS)
        if self._commands_loader:
            commands.extend(self._commands_loader.get_command_names())
        return commands

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
        parts = []
        for tc in tool_calls:
            name = tc.get("name", "unknown")
            parts.append(f"\U0001f527 {name}")
        return ", ".join(parts) if parts else "Using tools..."

    # === Internal Methods ===

    def _init_tool_adapter(self) -> None:
        """Initialize the ToolAdapter for built-in tools access."""
        try:
            from xbot.agent.capabilities.tool_adapter import ToolAdapter

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
        from claude_agent_sdk import ClaudeAgentOptions

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

        options = ClaudeAgentOptions(
            cwd=workspace_expanded,
            model=self._config.model,
            system_prompt=self._config.system_prompt,
            mcp_servers=mcp_servers if mcp_servers else None,
            agents=agents,
            env=env,
            max_turns=max_turns,
            permission_mode=permission_mode,
            disallowed_tools=disallowed_tools,
            hooks=hooks,
            # Capture CLI stderr for debugging
            stderr=lambda line: logger.warning(f"[CLI stderr] {line}"),
        )
        logger.info(
            f"[AgentService] SDK options: model={options.model}, cwd={workspace_expanded}, "
            f"max_turns={max_turns}, permission_mode={permission_mode}, "
            f"env keys={list(options.env.keys()) if options.env else 'None'}"
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
        if not self._config or not self._config.mcp_servers:
            return {}

        # Convert each MCPServerConfig to dict for JSON serialization
        return {
            name: server.model_dump() if hasattr(server, 'model_dump') else server
            for name, server in self._config.mcp_servers.items()
        }

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
            from xbot.agent.hooks import CompactHookHandler

            def send_compact_notification(session_ref: str, message: str) -> None:
                """Send compact notification to the user's channel via bus."""
                bus = self._shared_resources.get("bus")
                if bus is None:
                    logger.warning("[Compact Notification] No bus available for session: %s", session_ref)
                    return

                # Resolve target channel/chat_id from state_manager
                sm = self._shared_resources.get("state_manager")
                resolved_target = None
                if sm and hasattr(sm, "resolve_compact_notification_target"):
                    try:
                        resolved_target = sm.resolve_compact_notification_target(session_ref)
                    except Exception as e:
                        logger.debug("[Compact Notification] resolve failed for '%s': %s", session_ref, e)

                if not resolved_target or len(resolved_target) < 3:
                    logger.warning(
                        "[Compact Notification] No routing info for session_ref='%s'. "
                        "Notification will NOT be delivered.",
                        session_ref,
                    )
                    return

                session_key, channel, chat_id = resolved_target
                logger.info(
                    "[Compact Notification] target: session_key='%s', channel='%s', chat_id='%s'",
                    session_key, channel, chat_id,
                )

                async def _send() -> None:
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
                        logger.debug("Sent compact notification to %s:%s", channel, chat_id)
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

        return AgentResponse(
            content=text,
            progress_texts=progress_texts,
            tool_calls=tool_calls if tool_calls else None,
            finish_reason="tool_use" if tool_calls else "stop",
            raw_message=message,
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
            )

        return None

    def _convert_result_message(self, message: Any) -> AgentResponse | None:
        """Convert ResultMessage to AgentResponse."""
        usage = None
        if hasattr(message, "usage") and message.usage:
            usage = {
                "input_tokens": int(getattr(message.usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(message.usage, "output_tokens", 0) or 0),
            }

        content = message.result if isinstance(message.result, str) else ""
        return AgentResponse(
            content=content,
            finish_reason="stop",
            usage=usage,
            raw_message=message,
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

        if subtype in ("compact_complete", "post_compact"):
            from xbot.agent.interaction.event_formatter import format_compact_event
            pre_tokens = getattr(message, "pre_tokens", None)
            post_tokens = getattr(message, "post_tokens", None)
            trigger = getattr(message, "trigger", None)
            text = format_compact_event(
                pre_tokens=pre_tokens, post_tokens=post_tokens, trigger=trigger,
            )
            return AgentResponse(
                content="",
                progress_texts=[text],
                event_type="system",
                event_data={"subtype": subtype},
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
            progress_texts=["Rate limit hit, waiting..."],
            raw_message=message,
        )

    def _classify_tool_name(self, name: str) -> str:
        """Classify a tool name into its kind."""
        normalized = name.replace("_", "-").lower()
        if normalized.startswith("mcp-"):
            return "mcp"
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

        agents: dict[str, AgentDefinition] = {}
        for agent_def in self._config.agents:
            name = agent_def.get("name", "unknown")
            agents[name] = AgentDefinition(
                description=agent_def.get("description", ""),
                prompt=agent_def.get("prompt", ""),
                tools=agent_def.get("tools"),
                model=agent_def.get("model"),
            )
        return agents

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
        context = AgentContext(
            session_key=session_key,
            prompt=content,
            channel=channel,
            chat_id=chat_id,
            media=media or [],
        )

        result = []
        async for response in self.process(context):
            if on_progress and response.progress_texts:
                for text in response.progress_texts:
                    if asyncio.iscoroutinefunction(on_progress):
                        await on_progress(text)
                    else:
                        await asyncio.to_thread(on_progress, text)
            if response.content:
                result.append(response.content)

        return "".join(result)

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

                # Forward tool hints
                if response.tool_calls:
                    hint = self._format_tool_hint(response.tool_calls)
                    await self._publish_event(
                        bus, msg.channel, msg.chat_id, hint,
                        source_metadata=msg.metadata,
                        _tool_hint=True, _progress=True, _event_type="tool_call",
                    )

                # Accumulate final content
                if response.content:
                    response_text.append(response.content)

                # Track usage
                if response.usage:
                    last_usage = response.usage

            # Send usage summary
            if last_usage:
                from xbot.agent.interaction.event_formatter import format_usage_summary
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