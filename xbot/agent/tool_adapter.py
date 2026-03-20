"""Tool adapter for converting xbot tools to MCP format.

This module adapts xbot's Tool implementations to MCP tools
for use with Claude SDK backend.
"""

import logging
from pathlib import Path
from typing import Any

from xbot.agent.capabilities import canonical_tool_name
from xbot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from xbot.agent.tools.shell import ExecTool
from xbot.agent.tools.web import WebSearchTool, WebFetchTool
from xbot.agent.tools.message import MessageTool
from xbot.agent.tools.cron import CronTool
from xbot.agent.tools.spawn import SpawnTool
from xbot.agent.subagent import SubagentManager

logger = logging.getLogger(__name__)

# Try to import SDK components
try:
    from claude_agent_sdk import tool, create_sdk_mcp_server

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    logger.warning("claude-agent-sdk not installed. Tool adapter will be limited.")


class ToolAdapter:
    """Adapts xbot tools to MCP format.

    This class:
    - Creates MCP versions of xbot's built-in tools
    - Manages tool instances with proper context
    - Provides a unified MCP server for all tools
    """

    def __init__(
        self,
        workspace: str,
        tools_config: Any = None,
        shared_resources: dict[str, Any] | None = None,
    ):
        """Initialize the tool adapter.

        Args:
            workspace: Workspace path
            tools_config: Tools configuration
            shared_resources: Shared resources for tools that need them
        """
        self.workspace = Path(workspace)
        self.tools_config = tools_config
        self.shared_resources = shared_resources or {}
        self._tools: dict[str, Any] = {}
        self._tool_context: dict[str, Any] = {}

    def create_mcp_server(self) -> dict[str, Any]:
        """Create an MCP server with all xbot tools.

        Returns:
            Dict mapping server name to MCP server config,
            or empty dict if SDK not available
        """
        if not SDK_AVAILABLE:
            logger.debug("SDK not available, returning empty tools")
            return {}

        # Register xbot-specific tools
        if not self._tools:
            self._register_nanobot_tools()

        # Convert to MCP tools
        mcp_tools = []
        for tool_name, tool_instance in self._tools.items():
            try:
                mcp_tool = self._adapt_tool(tool_name, tool_instance)
                mcp_tools.append(mcp_tool)
            except Exception as e:
                logger.warning(f"Error adapting tool {tool_name}: {e}")

        if not mcp_tools:
            return {}

        logger.info(f"Created MCP server with {len(mcp_tools)} tools")
        return {
            "xbot": create_sdk_mcp_server(
                name="nanobot_tools",
                version="1.0.0",
                tools=mcp_tools,
            )
        }

    def _register_nanobot_tools(self) -> None:
        """Register xbot-specific tools."""
        allowed_dir = self.workspace if getattr(self.tools_config, "restrict_to_workspace", False) else None

        # Message tool - for sending messages to channels
        bus = self.shared_resources.get("bus")
        if bus:
            # Create async callback for message tool
            async def send_callback(msg):
                from xbot.bus.events import OutboundMessage
                if hasattr(msg, 'channel') and hasattr(msg, 'chat_id') and hasattr(msg, 'content'):
                    await bus.publish_outbound(msg)
                elif isinstance(msg, dict):
                    await bus.publish_outbound(OutboundMessage(
                        channel=msg.get('channel', ''),
                        chat_id=msg.get('chat_id', ''),
                        content=msg.get('content', ''),
                    ))

            self._tools["message"] = MessageTool(send_callback=send_callback)

        # Cron tool - for scheduled tasks
        cron_service = self.shared_resources.get("cron_service")
        if cron_service:
            self._tools["cron"] = CronTool(cron_service=cron_service)

        # Web tools
        web_config = self.tools_config.web if self.tools_config else None
        proxy = web_config.proxy if web_config else None
        search_config = web_config.search if web_config else None

        provider = self.shared_resources.get("provider")
        if provider and bus:
            self._tools["spawn"] = SpawnTool(
                manager=SubagentManager(
                    provider=provider,
                    workspace=self.workspace,
                    bus=bus,
                    model=self.shared_resources.get("model"),
                    web_search_config=search_config,
                    web_tools_config=self.tools_config.web if self.tools_config else None,
                    web_proxy=proxy,
                    exec_config=self.tools_config.exec if self.tools_config else None,
                    restrict_to_workspace=bool(getattr(self.tools_config, "restrict_to_workspace", False)),
                )
            )

        self._tools["web_search"] = WebSearchTool(config=search_config, proxy=proxy)
        self._tools["web_fetch"] = WebFetchTool(proxy=proxy, web_config=self.tools_config.web if self.tools_config else None)

        # File tools (with workspace restriction)
        self._tools["read_file"] = ReadFileTool(
            workspace=self.workspace,
            allowed_dir=allowed_dir,
        )
        self._tools["write_file"] = WriteFileTool(
            workspace=self.workspace,
            allowed_dir=allowed_dir,
        )
        self._tools["edit_file"] = EditFileTool(
            workspace=self.workspace,
            allowed_dir=allowed_dir,
        )
        self._tools["list_dir"] = ListDirTool(
            workspace=self.workspace,
            allowed_dir=allowed_dir,
        )

        # Shell tool
        exec_config = self.tools_config.exec if self.tools_config else None
        self._tools["exec"] = ExecTool(
            working_dir=str(self.workspace),
            timeout=exec_config.timeout if exec_config else 60,
            restrict_to_workspace=bool(getattr(self.tools_config, "restrict_to_workspace", False)),
            path_append=exec_config.path_append if exec_config else "",
        )

    def _adapt_tool(self, tool_name: str, tool_instance: Any) -> Any:
        """Adapt an xbot Tool to MCP format.

        Args:
            tool_name: Tool name
            tool_instance: Tool instance

        Returns:
            MCP tool
        """
        # Get tool schema
        name = tool_instance.name
        description = tool_instance.description
        parameters = tool_instance.parameters

        # Capture tool instance in closure
        tool_obj = tool_instance

        @tool(name, description, parameters)
        async def adapted_tool(args: dict) -> dict:
            try:
                result = await tool_obj.execute(**args)
                return {
                    "content": [{
                        "type": "text",
                        "text": result if isinstance(result, str) else str(result)
                    }]
                }
            except Exception as e:
                logger.exception(f"Error executing tool {name}")
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Error: {str(e)}"
                    }],
                    "is_error": True,
                }

        return adapted_tool

    def set_tool_context(
        self,
        channel: str = "",
        chat_id: str = "",
        message_id: str | None = None,
    ) -> None:
        """Set context for tools that need it (e.g., message tool).

        Args:
            channel: Channel name
            chat_id: Chat ID
            message_id: Message ID for reply
        """
        self._tool_context = {
            "channel": channel,
            "chat_id": chat_id,
            "message_id": message_id,
        }

        for name, args in {
            "message": (channel, chat_id, message_id),
            "spawn": (channel, chat_id),
            "cron": (channel, chat_id),
        }.items():
            tool = self._tools.get(name)
            if tool and hasattr(tool, "set_context"):
                tool.set_context(*args)

    def get_tool(self, name: str) -> Any | None:
        """Get a tool by name.

        Args:
            name: Tool name

        Returns:
            Tool instance or None
        """
        return self._tools.get(canonical_tool_name(name))

    def get(self, name: str) -> Any | None:
        """Registry-compatible alias used by gateway/runtime integrations."""
        return self.get_tool(name)
