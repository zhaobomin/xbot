"""LiteLLM Agent Backend.

This backend wraps the existing AgentLoop implementation,
providing zero-modification compatibility with the current system.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, AsyncIterator

from xbot.agent.protocol import AgentBackend, AgentContext, AgentResponse
from xbot.agent.capabilities import CapabilityCatalog
from xbot.agent.capability_policy import CapabilityPolicy
from xbot.agent.loop import AgentLoop
from xbot.bus.events import InboundMessage
from xbot.config.schema import AgentsConfig

logger = logging.getLogger(__name__)


class LiteLLMBackend(AgentBackend):
    """LiteLLM Agent backend - wraps existing AgentLoop.

    This backend provides full compatibility with the existing
    xbot architecture by delegating to AgentLoop.

    Features:
    - Zero modification to existing AgentLoop
    - Full LiteLLM multi-provider support
    - All existing tools and memory management
    """

    name = "litellm"

    def __init__(self):
        """Initialize the backend."""
        self.agent_loop: AgentLoop | None = None
        self._shared_resources: dict[str, Any] = {}
        self.tools = None

    async def initialize(self, config: AgentsConfig, shared_resources: dict[str, Any]) -> None:
        """Initialize the backend by creating an AgentLoop.

        Args:
            config: Agent configuration
            shared_resources: Shared resources from gateway
        """
        self._shared_resources = shared_resources

        # Get required resources
        bus = shared_resources.get("bus")
        provider = shared_resources.get("provider")
        workspace = Path(shared_resources.get("workspace", config.defaults.workspace))
        sessions = shared_resources.get("session_manager")
        cron_service = shared_resources.get("cron_service")
        full_config = shared_resources.get("config")

        # Create AgentLoop with existing configuration
        self.agent_loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=workspace,
            model=config.defaults.model,
            max_iterations=config.defaults.max_tool_iterations,
            context_window_tokens=config.defaults.context_window_tokens,
            web_search_config=full_config.tools.web.search if full_config else None,
            web_tools_config=full_config.tools.web if full_config else None,
            web_proxy=(full_config.tools.web.proxy if full_config else None) or None,
            exec_config=full_config.tools.exec if full_config else None,
            cron_service=cron_service,
            restrict_to_workspace=full_config.tools.restrict_to_workspace if full_config else False,
            session_manager=sessions,
            mcp_servers=full_config.tools.mcp_servers if full_config else None,
            channels_config=full_config.channels if full_config else None,
        )
        self.tools = self.agent_loop.tools

        # Initialize the loop
        await self.agent_loop._connect_mcp()

        logger.info(f"LiteLLM backend initialized with model: {config.defaults.model}")
        logger.info("LiteLLM capabilities: {}", self.get_tools_summary())

    async def process(self, context: AgentContext) -> AsyncIterator[AgentResponse]:
        """Process a message by delegating to AgentLoop.

        Args:
            context: Processing context

        Yields:
            AgentResponse objects
        """
        if not self.agent_loop:
            raise RuntimeError("Backend not initialized")

        # Create an InboundMessage from context
        msg = InboundMessage(
            channel=context.channel or "cli",
            sender_id="user",
            chat_id=context.chat_id or context.session_key.split(":")[-1],
            content=context.prompt,
            media=context.media,
            metadata=context.metadata,
        )

        # Use the existing message processing
        # Note: AgentLoop._process_message returns OutboundMessage or None
        # We need to adapt it for streaming

        try:
            queue: asyncio.Queue[AgentResponse | None] = asyncio.Queue()

            async def _on_progress(content: str, *, tool_hint: bool = False) -> None:
                if tool_hint:
                    await queue.put(
                        AgentResponse(
                            content="",
                            tool_hint_text=content,
                        )
                    )
                    return
                await queue.put(
                    AgentResponse(
                        content="",
                        progress_texts=[content],
                    )
                )

            async def _run_loop():
                return await self.agent_loop._process_message(
                    msg,
                    session_key=context.session_key,
                    on_progress=_on_progress,
                )

            task = asyncio.create_task(_run_loop())

            while True:
                if task.done() and queue.empty():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue
                if item is not None:
                    yield item

            response = await task

            if response:
                yield AgentResponse(
                    content=response.content,
                    finish_reason="stop",
                    raw_message=response,
                )
            else:
                yield AgentResponse(
                    content="",
                    finish_reason="stop",
                )

        except Exception as e:
            logger.exception("Error processing message in LiteLLM backend")
            yield AgentResponse(
                content=f"Error: {str(e)}",
                finish_reason="error",
            )

    async def shutdown(self) -> None:
        """Shutdown the backend."""
        if self.agent_loop:
            await self.agent_loop.close_mcp()
            self.agent_loop.stop()
            self.agent_loop = None
            logger.info("LiteLLM backend shutdown complete")

    async def execute_tool(self, tool_name: str, args: dict[str, Any]) -> str | None:
        """Execute a tool directly.

        Args:
            tool_name: Tool name
            args: Tool arguments

        Returns:
            Tool result
        """
        if self.agent_loop and self.agent_loop.tools:
            return await self.agent_loop.tools.execute(tool_name, args)
        return None

    async def reset_session(self, session_key: str) -> None:
        if not self.agent_loop:
            return
        session = self.agent_loop.sessions.get_or_create(session_key)
        session.clear()
        self.agent_loop.sessions.save(session)
        self.agent_loop.sessions.invalidate(session.key)

    async def cancel_session(self, session_key: str) -> int:
        if not self.agent_loop:
            return 0
        return await self.agent_loop.subagents.cancel_by_session(session_key)

    def get_tools_summary(self) -> str:
        workspace = self._shared_resources.get("workspace")
        full_config = self._shared_resources.get("config")
        if not workspace:
            return "capabilities=unavailable"
        catalog = CapabilityCatalog(workspace)
        policy = CapabilityPolicy(
            catalog,
            mcp_servers=getattr(getattr(full_config, "tools", None), "mcp_servers", None)
            if full_config
            else None,
        )
        configured = catalog.build_summary(
            mcp_servers=getattr(getattr(full_config, "tools", None), "mcp_servers", None)
            if full_config
            else None
        )
        policy_summary = policy.build_backend_trace("litellm")
        runtime = ""
        if self.agent_loop is not None:
            runtime = (
                f"registered_tools={len(self.agent_loop.tools)} | "
                f"mcp_connected={self.agent_loop._mcp_connected}"
            )
        result = f"{configured} | {policy_summary}"
        return f"{result} | {runtime}" if runtime else result
