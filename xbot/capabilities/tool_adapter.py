"""Tool adapter for converting xbot tools to MCP format.

This module adapts xbot's Tool implementations to MCP tools
for use with Claude SDK backend.
"""

import threading
from pathlib import Path
from typing import Any

from xbot.capabilities.catalog import canonical_tool_name
from xbot.platform.logging.core import get_logger
from xbot.tools.cron import CronTool
from xbot.tools.memory import MemoryTool
from xbot.tools.message import MessageTool
from xbot.tools.web import WebFetchTool, WebSearchTool

logger = get_logger(__name__)

# Try to import SDK components
try:
    from claude_agent_sdk import create_sdk_mcp_server, tool

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    logger.debug("claude-agent-sdk not installed. Tool adapter will be limited.")


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
        # These locks protect synchronous registry/context mutations triggered by MCP plumbing.
        # They must never be held across an await.
        self._tools_lock = threading.Lock()
        self._context_lock = threading.Lock()
        # Flag to track if core tools are registered (for idempotency)
        self._core_tools_registered = False

    def create_mcp_server(self) -> dict[str, Any]:
        """Create an MCP server with all xbot tools.

        Returns:
            Dict mapping server name to MCP server config,
            or empty dict if SDK not available
        """
        if not SDK_AVAILABLE:
            logger.debug("SDK not available, returning empty tools")
            return {}

        # Ensure xbot core tools are always registered.
        self._ensure_core_tools_registered()

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
                name="xbot_tools",
                version="1.0.0",
                tools=mcp_tools,
            )
        }

    def _register_xbot_tools(self) -> None:
        """Register xbot extension tools.

        SDK-native tools (filesystem/shell) are intentionally excluded.
        """

        # Message tool - for sending messages to channels
        bus = self.shared_resources.get("bus")
        if bus:
            # Create async callback for message tool
            async def send_callback(msg):
                from xbot.platform.bus.events import OutboundMessage
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
        timeouts_config = self.tools_config.timeouts if self.tools_config else None
        proxy = web_config.proxy if web_config else None
        search_config = web_config.search if web_config else None
        web_search_timeout = timeouts_config.web_search if timeouts_config else None
        web_fetch_timeout = timeouts_config.web_fetch if timeouts_config else None

        self._tools["web_search"] = WebSearchTool(config=search_config, proxy=proxy, timeout=web_search_timeout)
        self._tools["web_fetch"] = WebFetchTool(proxy=proxy, web_config=self.tools_config.web if self.tools_config else None, timeout=web_fetch_timeout)

        # Memory tool - for reading, searching, and writing long-term memory
        memory_store = self.shared_resources.get("memory_store")
        self._tools["memory"] = MemoryTool(
            workspace=self.workspace,
            memory_store=memory_store,
        )

        # Note: LoadSkillContentTool has been removed
        # Skills are now loaded natively by Claude Code SDK via add_dirs parameter

    def _ensure_core_tools_registered(self) -> None:
        """Ensure core xbot tools are registered."""
        if self._core_tools_registered:
            logger.debug("[ToolAdapter] Core tools already registered")
            return

        # Check if web_search is registered (a reliable indicator of full registration)
        if "web_search" not in self._tools:
            logger.info("[ToolAdapter] Core tools not registered, calling _register_xbot_tools")
            self._register_xbot_tools()
            self._core_tools_registered = True
        else:
            # Core tools are already registered
            self._core_tools_registered = True

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
        session_key: str | None = None,
        message_id: str | None = None,
    ) -> None:
        """Set context for tools that need it (e.g., message tool).

        Args:
            channel: Channel name
            chat_id: Chat ID
            session_key: Session key for per-session context
            message_id: Message ID for reply

        Thread-safe: uses lock to protect against concurrent modifications.
        Uses per-session context to avoid race conditions in multi-session scenarios.
        """
        with self._context_lock:
            # Use per-session context to avoid race conditions
            if session_key:
                self._tool_context[session_key] = {
                    "channel": channel,
                    "chat_id": chat_id,
                    "session_key": session_key,
                    "message_id": message_id,
                }
            else:
                # Fallback to global context for backward compatibility
                self._tool_context["_global"] = {
                    "channel": channel,
                    "chat_id": chat_id,
                    "session_key": session_key,
                    "message_id": message_id,
                }

            # Update tool contexts
            for name, args in {
                "message": {"channel": channel, "chat_id": chat_id, "message_id": message_id, "session_key": session_key},
                "cron": {"channel": channel, "chat_id": chat_id, "session_key": session_key},
            }.items():
                tool = self._tools.get(name)
                if tool and hasattr(tool, "set_context"):
                    tool.set_context(**args)
                if tool and hasattr(tool, "set_active_session"):
                    tool.set_active_session(session_key)

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

    def clear_context(self, session_key: str) -> None:
        """Clear per-session context for tools that keep session-local state."""
        with self._context_lock:
            self._tool_context.pop(session_key, None)
            for tool_name in ("message", "cron"):
                tool = self._tools.get(tool_name)
                if tool and hasattr(tool, "clear_context"):
                    try:
                        tool.clear_context(session_key)
                    except Exception:
                        logger.debug("Failed to clear %s context for session %s", tool_name, session_key)
