"""Claude SDK Agent Loop - gateway-compatible wrapper for Claude SDK.

This module provides an AgentLoop-compatible interface for the Claude SDK,
allowing seamless integration with the existing gateway architecture.
"""

from __future__ import annotations

import asyncio
import logging
import os
import warnings
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from xbot.agent.tools.cron import CronTool
from xbot.agent.tools.message import MessageTool
from xbot.agent.tools.registry import ToolRegistry
from xbot.bus.events import InboundMessage, OutboundMessage
from xbot.bus.queue import MessageBus
from xbot.session.manager import SessionManager

if TYPE_CHECKING:
    from xbot.config.schema import Config
    from xbot.cron.service import CronService

# Try to import Claude SDK
try:
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    logger.warning("claude-agent-sdk not installed. Claude SDK agent will not be available.")


class ClaudeSDKAgentLoop:
    """Claude SDK Agent that implements AgentLoop-compatible interface.

    This class wraps the Claude Agent SDK and provides the same interface
    as AgentLoop for seamless gateway integration.

    Key differences from AgentLoop:
    - Uses Claude SDK instead of LiteLLM
    - Tools are provided via MCP instead of internal registry
    - Sessions managed differently (SDK handles conversation history)
    """

    def __init__(
        self,
        bus: MessageBus,
        config: Config,
        workspace: Path,
        cron_service: CronService | None = None,
        session_manager: SessionManager | None = None,
    ):
        """Initialize the Claude SDK Agent.

        Args:
            bus: Message bus for communication
            config: Full configuration
            workspace: Workspace directory
            cron_service: Optional cron service
            session_manager: Optional session manager
        """
        if not SDK_AVAILABLE:
            raise ImportError(
                "claude-agent-sdk is not installed. "
                "Install it with: pip install claude-agent-sdk"
            )

        self.bus = bus
        self.config = config
        self.workspace = workspace
        self.cron_service = cron_service
        self.sessions = session_manager or SessionManager(workspace)

        # Agent config
        self.model = config.agents.defaults.model
        self.max_turns = config.agents.claude_sdk.max_turns
        self.permission_mode = config.agents.claude_sdk.permission_mode

        # Tool registry (for compatibility with gateway's cron/message tools)
        self.tools = ToolRegistry()
        self._setup_builtin_tools()

        self._running = False
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_servers: dict = config.tools.mcp_servers or {}
        self._clients: dict[str, ClaudeSDKClient] = {}  # session_id -> client

        # Tool adapter for MCP tools
        self._tool_adapter = None
        self._setup_tool_adapter()

    def _setup_builtin_tools(self) -> None:
        """Set up built-in tools for compatibility."""
        # Cron tool - for scheduled tasks
        if self.cron_service:
            cron_tool = CronTool(cron_service=self.cron_service)
            self.tools.register(cron_tool)

        # Message tool - for sending messages through channels
        async def send_callback(msg: OutboundMessage) -> None:
            await self.bus.publish_outbound(msg)

        message_tool = MessageTool(send_callback=send_callback)
        self.tools.register(message_tool)

    def _setup_tool_adapter(self) -> None:
        """Set up tool adapter for MCP tools."""
        try:
            from xbot.agent.tool_adapter import ToolAdapter
            from xbot.agent.skill_to_mcp import SkillToMCPConverter

            shared_resources = {
                "bus": self.bus,
                "cron_service": self.cron_service,
            }

            self._tool_adapter = ToolAdapter(
                workspace=str(self.workspace),
                tools_config=self.config.tools,
                shared_resources=shared_resources,
            )

            # Set up skill converter
            self._skill_converter = SkillToMCPConverter(str(self.workspace))

        except Exception as e:
            logger.warning(f"Failed to set up tool adapter: {e}")
            self._skill_converter = None

    def _get_provider_config(self) -> tuple[str, str]:
        """Get provider API key and base URL.

        Returns:
            Tuple of (api_key, base_url)
        """
        from xbot.config.provider_registry import get_provider_spec

        provider_name = self.config.agents.defaults.provider
        if provider_name == "auto":
            # Auto-detect from model
            model_lower = self.model.lower()
            if "claude" in model_lower:
                provider_name = "anthropic"
            elif "qwen" in model_lower or "glm" in model_lower:
                provider_name = "aliyun_coding_plan"
            else:
                provider_name = "anthropic"

        # Get provider spec
        spec = get_provider_spec(provider_name)
        if not spec:
            raise ValueError(f"Unknown provider: {provider_name}")

        # Get provider config
        provider_attr = provider_name.replace("-", "_")
        provider_config = getattr(self.config.providers, provider_attr, None)

        if not provider_config or not provider_config.api_key:
            raise ValueError(
                f"API key not configured for provider '{provider_name}'. "
                f"Please set providers.{provider_attr}.api_key in config.json"
            )

        api_key = provider_config.api_key
        base_url = provider_config.api_base or spec.default_base_url

        return api_key, base_url

    def _build_options(self, session_id: str) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions for a session.

        Args:
            session_id: Session identifier

        Returns:
            ClaudeAgentOptions instance
        """
        api_key, base_url = self._get_provider_config()

        # Handle model name transformations
        model = self.model
        provider = self.config.agents.defaults.provider
        if provider == "alrun" and model.startswith("alrun-"):
            model = model[len("alrun-"):]

        # Build environment for SDK
        env = {}
        if os.path.exists("/etc/ssl/cert.pem"):
            env["NODE_EXTRA_CA_CERTS"] = "/etc/ssl/cert.pem"
        env["ANTHROPIC_API_KEY"] = api_key
        if base_url:
            # Normalize base URL (SDK will append /v1/messages)
            base_url = base_url.rstrip("/")
            if base_url.endswith("/v1/messages"):
                base_url = base_url[:-len("/v1/messages")]
            elif base_url.endswith("/v1"):
                base_url = base_url[:-len("/v1")]
            env["ANTHROPIC_BASE_URL"] = base_url

        # Build MCP servers dict
        mcp_servers = {}
        if self._mcp_servers:
            mcp_servers.update(self._mcp_servers)

        # Add xbot tools as MCP server
        if self._tool_adapter:
            try:
                tools_mcp = self._tool_adapter.create_mcp_server()
                if tools_mcp:
                    mcp_servers.update(tools_mcp)
                    logger.debug(f"Added {len(tools_mcp)} MCP tool servers")
            except Exception as e:
                logger.warning(f"Failed to create MCP tools: {e}")

        # Add skills as MCP server
        if hasattr(self, '_skill_converter') and self._skill_converter:
            try:
                skills_mcp = self._skill_converter.convert_all_skills()
                if skills_mcp:
                    mcp_servers.update(skills_mcp)
                    logger.debug(f"Added skills MCP server")
            except Exception as e:
                logger.warning(f"Failed to convert skills: {e}")

        return ClaudeAgentOptions(
            cwd=str(self.workspace),
            model=model,
            max_turns=self.max_turns,
            permission_mode=self.permission_mode,
            mcp_servers=mcp_servers if mcp_servers else None,
            system_prompt=self._build_system_prompt(),
            env=env,
            include_partial_messages=True,
        )

    def _build_system_prompt(self) -> str:
        """Build the system prompt."""
        return """你是 xbot，一个智能助手。

你可以使用以下工具：
- web_search: 搜索网络获取信息
- web_fetch: 获取网页内容
- read_file: 读取文件
- write_file: 写入文件
- edit_file: 编辑文件
- list_dir: 列出目录内容
- shell: 执行命令
- cron: 设置定时任务
- message: 发送消息

请根据用户需求选择合适的工具完成任务。"""

    async def _create_client(self, session_id: str) -> ClaudeSDKClient:
        """Create a new SDK client for a session.

        Args:
            session_id: Session identifier

        Returns:
            Connected ClaudeSDKClient
        """
        options = self._build_options(session_id)
        client = ClaudeSDKClient(options=options)
        await client.connect()
        self._clients[session_id] = client
        return client

    async def _get_or_create_client(self, session_id: str) -> ClaudeSDKClient:
        """Get existing client or create new one.

        Args:
            session_id: Session identifier

        Returns:
            Connected ClaudeSDKClient
        """
        if session_id in self._clients:
            return self._clients[session_id]

        return await self._create_client(session_id)

    async def run(self) -> None:
        """Run the agent loop - listen for messages from bus.

        This is the main entry point called by gateway.
        """
        self._running = True
        logger.info("Claude SDK agent loop started")

        try:
            while self._running:
                try:
                    # Wait for inbound message
                    msg = await asyncio.wait_for(
                        self.bus.inbound.get(),
                        timeout=1.0
                    )

                    # Process the message
                    await self._process_message(msg)

                except asyncio.TimeoutError:
                    # No message, continue loop
                    continue
                except Exception as e:
                    logger.exception(f"Error processing message: {e}")
                    # Continue running despite errors

        except asyncio.CancelledError:
            logger.info("Claude SDK agent loop cancelled")
        finally:
            self._running = False
            logger.info("Claude SDK agent loop stopped")

    async def _process_message(self, msg: InboundMessage) -> None:
        """Process an inbound message.

        Args:
            msg: Inbound message
        """
        from contextlib import aclosing
        from claude_agent_sdk.types import StreamEvent

        session_key = f"{msg.channel}:{msg.chat_id}"
        logger.info(f"Processing message from {session_key}")

        try:
            # Get or create client for this session
            logger.debug("Getting/creating SDK client...")
            client = await self._get_or_create_client(session_key)
            logger.debug("SDK client ready")

            # Send "thinking" status
            if self.config.channels.send_progress:
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="💭 正在思考...",
                ))

            # Send the message
            logger.debug("Sending query to SDK...")
            await client.query(msg.content, session_id=session_key)
            logger.debug("Query sent, receiving response...")

            # Collect response with proper stream handling
            full_response = ""
            tool_calls = []  # Track tool calls for progress display

            async with aclosing(client.receive_response()) as response_gen:
                async for message in response_gen:
                    # Handle StreamEvent for incremental text and tool calls
                    if isinstance(message, StreamEvent):
                        event_data = message.event
                        event_type = event_data.get('type', 'unknown')

                        if event_type == 'content_block_start':
                            block = event_data.get('content_block', {})
                            block_type = block.get('type', 'unknown')
                            if block_type == 'tool_use':
                                tool_name = block.get('name', 'unknown')
                                tool_calls.append(tool_name)
                                # Send tool call progress
                                if self.config.channels.send_progress:
                                    await self.bus.publish_outbound(OutboundMessage(
                                        channel=msg.channel,
                                        chat_id=msg.chat_id,
                                        content=f"🔧 调用工具: {tool_name}...",
                                    ))

                        elif event_type == 'content_block_delta':
                            delta = event_data.get('delta', {})
                            delta_type = delta.get('type', 'unknown')

                            if delta_type == 'text_delta':
                                text = delta.get('text', '')
                                if text:
                                    full_response += text

            # Send final complete response (always send, regardless of progress setting)
            if full_response:
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=full_response,
                ))
                logger.info(f"Response to {session_key}: {full_response[:100]}...")

        except Exception as e:
            logger.exception(f"Error in Claude SDK agent: {e}")
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"❌ 错误: {str(e)}",
            ))

    async def process_direct(
        self,
        message: str,
        session_key: str = "direct:cli",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable | None = None,
    ) -> str:
        """Process a message directly (for cron, heartbeat, etc).

        Args:
            message: Message to process
            session_key: Session identifier
            channel: Channel name
            chat_id: Chat ID
            on_progress: Optional progress callback

        Returns:
            Response content
        """
        try:
            # Get or create client
            client = await self._get_or_create_client(session_key)

            # Send query
            await client.query(message, session_id=session_key)

            # Collect response
            response_text = ""
            async for message_obj in client.receive_response():
                from claude_agent_sdk import AssistantMessage, TextBlock

                if hasattr(message_obj, 'content'):
                    for block in message_obj.content if hasattr(message_obj.content, '__iter__') else []:
                        if hasattr(block, 'text'):
                            response_text += block.text
                            if on_progress:
                                on_progress(block.text)

            return response_text

        except Exception as e:
            logger.exception(f"Error in process_direct: {e}")
            return f"Error: {str(e)}"

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Claude SDK agent stop requested")

    async def close_mcp(self) -> None:
        """Close MCP connections and cleanup clients."""
        # Disconnect all clients
        for session_id, client in list(self._clients.items()):
            try:
                await client.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting client {session_id}: {e}")
        self._clients.clear()

        # Close MCP stack if exists
        if self._mcp_stack:
            await self._mcp_stack.__aexit__(None, None, None)
            self._mcp_stack = None

        logger.info("Claude SDK agent cleanup complete")


def create_agent(
    bus: MessageBus,
    config: Config,
    workspace: Path,
    provider: Any,  # LLMProvider - kept for compatibility
    cron_service: CronService | None = None,
    session_manager: SessionManager | None = None,
) -> Any:  # Returns AgentLoop or ClaudeSDKAgentLoop
    """Legacy factory kept for backward compatibility only.

    Args:
        bus: Message bus
        config: Configuration
        workspace: Workspace path
        provider: LLM provider (for LiteLLM agent)
        cron_service: Optional cron service
        session_manager: Optional session manager

    Returns:
        AgentLoop (for litellm) or ClaudeSDKAgentLoop (for claude_sdk)
    """
    warnings.warn(
        "xbot.agent.claude_sdk_loop.create_agent() is deprecated; "
        "use AgentRuntime/AgentRouter as the runtime entrypoint.",
        DeprecationWarning,
        stacklevel=2,
    )

    from xbot.agent.loop import AgentLoop

    agent_type = config.agents.type

    if agent_type == "claude_sdk":
        if not SDK_AVAILABLE:
            raise ImportError(
                "claude-agent-sdk is not installed. "
                "Install it with: pip install claude-agent-sdk"
            )
        logger.info("Creating Claude SDK Agent")
        return ClaudeSDKAgentLoop(
            bus=bus,
            config=config,
            workspace=workspace,
            cron_service=cron_service,
            session_manager=session_manager,
        )
    else:
        # Default to LiteLLM AgentLoop
        logger.info("Creating LiteLLM AgentLoop")
        return AgentLoop(
            bus=bus,
            provider=provider,
            workspace=workspace,
            model=config.agents.defaults.model,
            max_iterations=config.agents.defaults.max_tool_iterations,
            context_window_tokens=config.agents.defaults.context_window_tokens,
            web_search_config=config.tools.web.search,
            web_proxy=config.tools.web.proxy or None,
            exec_config=config.tools.exec,
            cron_service=cron_service,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            session_manager=session_manager,
            mcp_servers=config.tools.mcp_servers,
            channels_config=config.channels,
        )
