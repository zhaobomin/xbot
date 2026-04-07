"""Unified Agent Service.

This module provides the single entry point for all agent operations,
combining the core logic from ClaudeSDKBackend and AgentRuntime.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from xbot.logging import get_logger
from xbot.agent.protocol import AgentContext, AgentResponse
from xbot.agent.types import AgentConfig
from xbot.agent.client_pool import ClientPool
from xbot.agent.capabilities.handoff import HandoffPolicy

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

logger = get_logger(__name__)

# Type alias for progress callback
ProgressCallback = Callable[[str], Any]


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

        # Build SDK options
        options = self._build_options(context)

        # Process through SDK
        try:
            async for event in client.process(context.prompt, options=options):
                response = self._convert_event(event)
                if response:
                    yield response
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
        """Get available slash commands for a session.

        Args:
            session_key: Session identifier

        Returns:
            List of available commands
        """
        # Placeholder - will be enhanced later
        return []

    async def interrupt_session(self, session_key: str) -> dict[str, Any]:
        """Interrupt ongoing processing for a session.

        Args:
            session_key: Session identifier

        Returns:
            Dict with 'interrupted' bool and optional 'usage' dict
        """
        # Placeholder - will be enhanced later
        return {"interrupted": False, "usage": None}

    async def call_for_auxiliary(
        self,
        session_key: str,
        prompt: str,
    ) -> AgentResponse:
        """Execute a standalone prompt.

        Args:
            session_key: Session identifier
            prompt: Prompt to execute

        Returns:
            AgentResponse with result
        """
        context = AgentContext(
            session_key=session_key,
            prompt=prompt,
        )
        final = ""
        async for response in self.process(context):
            if response.is_delta:
                final += response.delta_content
            else:
                final = response.content or final

        return AgentResponse(content=final)

    # === Internal Methods ===

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

        # Build MCP servers
        mcp_servers = self._build_mcp_servers()

        # Build agents
        agents = self._build_sdk_agents()

        return ClaudeAgentOptions(
            cwd=self._shared_resources.get("workspace", "."),
            model=self._config.model,
            system_prompt=self._config.system_prompt,
            mcp_servers=mcp_servers if mcp_servers else None,
            agents=agents,
            env=env,
        )

    def _build_env_config(self) -> dict[str, str]:
        """Build environment configuration for SDK."""
        env = {}

        # Get provider config from shared resources
        config = self._shared_resources.get("config")
        if config and hasattr(config, "providers"):
            # TODO: Implement provider resolution in future iteration
            pass

        return env

    def _build_mcp_servers(self) -> dict[str, Any]:
        """Build MCP servers configuration."""
        if not self._config:
            return {}

        return self._config.mcp_servers.copy() if self._config.mcp_servers else {}

    def _build_options(self, context: AgentContext) -> Any:
        """Build processing options for a context."""
        return self._build_sdk_options()

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
        """Convert SystemMessage to AgentResponse."""
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
                    await asyncio.to_thread(on_progress, text)
            if response.content:
                result.append(response.content)

        return "".join(result)

    async def run(self) -> None:
        """Run the service with message bus integration.

        This replaces AgentRuntime.run() for gateway mode.
        Requires bus to be set in shared_resources.
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

            # Process the message
            try:
                context = AgentContext(
                    session_key=msg.session_key or f"{msg.channel}:{msg.chat_id}",
                    prompt=msg.content,
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    media=msg.media or [],
                )

                response_text = []
                async for response in self.process(context):
                    if response.content:
                        response_text.append(response.content)

                if response_text:
                    from xbot.bus.events import OutboundMessage

                    outbound = OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="".join(response_text),
                    )
                    await bus.publish_outbound(outbound)

            except Exception as e:
                logger.exception("Error processing message: %s", e)

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
    def tools(self) -> dict[str, Any]:
        """Get tool registry (for CLI compatibility).

        Returns:
            Dict of available tools
        """
        # Return empty dict for now - tools are managed by SDK client
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