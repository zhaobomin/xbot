"""MCP client: connects to MCP servers and wraps their tools as native xbot tools."""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx

from xbot.runtime.core.mcp_config import resolve_mcp_server_config
from xbot.tools.base import Tool
from xbot.tools.registry import ToolRegistry
from xbot.platform.logging.core import get_logger

logger = get_logger(__name__)
class MCPToolTimeoutError(Exception):
    """Raised when an MCP tool call times out."""
    def __init__(self, tool_name: str, timeout: float):
        self.tool_name = tool_name
        self.timeout = timeout
        super().__init__(f"MCP tool '{tool_name}' timed out after {timeout}s")


@dataclass
class MCPServerConnection:
    """Represents a connection to an MCP server with its own lifecycle."""
    name: str
    session: Any
    stack: AsyncExitStack = field(default_factory=AsyncExitStack)
    tools_registered: int = 0
    connected: bool = False
    error: str | None = None


class MCPToolWrapper(Tool):
    """Wraps a single MCP server tool as an xbot Tool."""

    def __init__(self, session, server_name: str, tool_def, tool_timeout: int | None = None):
        from xbot.platform.config.schema import TimeoutsConfig
        self._session = session
        self._original_name = tool_def.name
        self._name = f"mcp_{server_name}_{tool_def.name}"
        self._description = tool_def.description or tool_def.name
        self._parameters = tool_def.inputSchema or {"type": "object", "properties": {}}
        self._tool_timeout = tool_timeout or int(TimeoutsConfig().mcp_tool)

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        from mcp import types

        try:
            result = await asyncio.wait_for(
                self._session.call_tool(self._original_name, arguments=kwargs),
                timeout=self._tool_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("MCP tool '%s' timed out after %ss", self._name, self._tool_timeout)
            return f"(MCP tool '{self._name}' timed out after {self._tool_timeout}s. Try again or increase timeout.)"
        except asyncio.CancelledError:
            # MCP SDK's anyio cancel scopes can leak CancelledError on timeout/failure.
            # Re-raise only if our task was externally cancelled (e.g. /stop).
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            logger.warning("MCP tool '%s' was cancelled by server/SDK", self._name)
            return "(MCP tool call was cancelled)"
        except Exception as exc:
            logger.exception(
                "MCP tool '%s' failed: %s: %s",
                self._name,
                type(exc).__name__,
                exc,
            )
            return f"(MCP tool call failed: {type(exc).__name__})"

        parts = []
        for block in result.content:
            if isinstance(block, types.TextContent):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts) or "(no output)"


def _is_ignorable_mcp_close_error(exc: BaseException) -> bool:
    """Return True for known SSE/anyio shutdown bugs in the MCP Python client."""
    message = str(exc)
    if (
        "Attempted to exit cancel scope in a different task" in message
        or "generator didn't stop after athrow()" in message
    ):
        return True

    if isinstance(exc, BaseExceptionGroup):
        return all(_is_ignorable_mcp_close_error(sub) for sub in exc.exceptions)

    return False


async def _safe_close_stack(stack: AsyncExitStack, server_name: str) -> None:
    """Close an MCP server stack while suppressing known SSE cleanup bugs."""
    try:
        await stack.aclose()
    except Exception as exc:
        if _is_ignorable_mcp_close_error(exc):
            logger.warning(
                "MCP server '%s': suppressed known SSE cleanup error: %s",
                server_name,
                exc,
            )
            return
        raise


async def _connect_single_mcp_server(
    name: str,
    cfg: Any,
    registry: ToolRegistry,
) -> MCPServerConnection:
    """Connect to a single MCP server with isolated error handling.

    Each server gets its own AsyncExitStack, so failures don't affect other servers.
    """
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    from mcp import ClientSession, StdioServerParameters

    conn = MCPServerConnection(name=name, session=None)

    try:
        # Create isolated stack for this server
        conn.stack = AsyncExitStack()

        raw_cfg = {
            "type": getattr(cfg, "type", None),
            "command": getattr(cfg, "command", ""),
            "args": list(getattr(cfg, "args", []) or []),
            "env": dict(getattr(cfg, "env", {}) or {}),
            "url": getattr(cfg, "url", ""),
            "headers": dict(getattr(cfg, "headers", {}) or {}),
        }
        resolved_cfg, unresolved = resolve_mcp_server_config(raw_cfg)
        if unresolved:
            conn.error = f"Unresolved env vars: {', '.join(unresolved)}"
            logger.warning("MCP server '%s': %s", name, conn.error)
            return conn

        transport_type = resolved_cfg["type"]
        if not transport_type:
            if resolved_cfg["command"]:
                transport_type = "stdio"
            elif resolved_cfg["url"]:
                transport_type = "sse" if resolved_cfg["url"].rstrip("/").endswith("/sse") else "streamableHttp"
            else:
                logger.warning("MCP server '%s': no command or url configured, skipping", name)
                conn.error = "No command or url configured"
                return conn

        # Setup transport based on type
        if transport_type == "stdio":
            params = StdioServerParameters(
                command=resolved_cfg["command"],
                args=resolved_cfg["args"],
                env=resolved_cfg["env"] or None,
            )
            read, write = await conn.stack.enter_async_context(stdio_client(params))
        elif transport_type == "sse":
            def httpx_client_factory(
                headers: dict[str, str] | None = None,
                timeout: httpx.Timeout | None = None,
                auth: httpx.Auth | None = None,
            ) -> httpx.AsyncClient:
                merged_headers = {**(resolved_cfg["headers"] or {}), **(headers or {})}
                return httpx.AsyncClient(
                    headers=merged_headers or None,
                    follow_redirects=True,
                    timeout=timeout,
                    auth=auth,
                )
            read, write = await conn.stack.enter_async_context(
                sse_client(resolved_cfg["url"], httpx_client_factory=httpx_client_factory)
            )
        elif transport_type == "streamableHttp":
            http_client = await conn.stack.enter_async_context(
                httpx.AsyncClient(
                    headers=resolved_cfg["headers"] or None,
                    follow_redirects=True,
                    timeout=None,
                )
            )
            read, write, _ = await conn.stack.enter_async_context(
                streamable_http_client(resolved_cfg["url"], http_client=http_client)
            )
        else:
            logger.warning("MCP server '%s': unknown transport type '%s'", name, transport_type)
            conn.error = f"Unknown transport type: {transport_type}"
            return conn

        # Create and initialize session
        conn.session = await conn.stack.enter_async_context(ClientSession(read, write))
        await conn.session.initialize()

        # List and register tools
        tools = await conn.session.list_tools()
        enabled_tools = set(cfg.enabled_tools)
        allow_all_tools = "*" in enabled_tools
        registered_count = 0
        matched_enabled_tools: set[str] = set()
        available_raw_names = [tool_def.name for tool_def in tools.tools]
        available_wrapped_names = [f"mcp_{name}_{tool_def.name}" for tool_def in tools.tools]

        for tool_def in tools.tools:
            wrapped_name = f"mcp_{name}_{tool_def.name}"
            if (
                not allow_all_tools
                and tool_def.name not in enabled_tools
                and wrapped_name not in enabled_tools
            ):
                logger.debug(
                    "MCP: skipping tool '%s' from server '%s' (not in enabledTools)",
                    wrapped_name,
                    name,
                )
                continue
            wrapper = MCPToolWrapper(conn.session, name, tool_def, tool_timeout=cfg.tool_timeout)
            registry.register(wrapper)
            logger.debug("MCP: registered tool '%s' from server '%s'", wrapper.name, name)
            registered_count += 1
            if enabled_tools:
                if tool_def.name in enabled_tools:
                    matched_enabled_tools.add(tool_def.name)
                if wrapped_name in enabled_tools:
                    matched_enabled_tools.add(wrapped_name)

        if enabled_tools and not allow_all_tools:
            unmatched_enabled_tools = sorted(enabled_tools - matched_enabled_tools)
            if unmatched_enabled_tools:
                logger.warning(
                    "MCP server '%s': enabledTools entries not found: %s. Available raw names: %s. "
                    "Available wrapped names: %s",
                    name,
                    ", ".join(unmatched_enabled_tools),
                    ", ".join(available_raw_names) or "(none)",
                    ", ".join(available_wrapped_names) or "(none)",
                )

        conn.tools_registered = registered_count
        conn.connected = True
        logger.info("MCP server '%s': connected, %s tools registered", name, registered_count)

    except Exception as e:
        conn.error = f"{type(e).__name__}: {str(e)}"
        logger.error("MCP server '%s': failed to connect: %s", name, conn.error)
        # Cleanup stack if connection failed
        try:
            await _safe_close_stack(conn.stack, name)
        except Exception:
            pass

    return conn


async def connect_mcp_servers(
    mcp_servers: dict, registry: ToolRegistry, stack: AsyncExitStack
) -> None:
    """Connect to configured MCP servers and register their tools.

    Deprecated:
        AgentService now uses ClaudeAgentOptions.mcp_servers as the primary
        MCP wiring path. This helper is kept for compatibility tests and
        legacy runtime integrations.

    Each server is connected in parallel with isolated error handling.
    A failure in one server does not affect other servers.
    """
    if not mcp_servers:
        return

    # Connect to all servers in parallel
    tasks = [
        _connect_single_mcp_server(name, cfg, registry)
        for name, cfg in mcp_servers.items()
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    connected_count = 0
    failed_count = 0
    failed_servers = []

    for i, result in enumerate(results):
        server_name = list(mcp_servers.keys())[i]

        if isinstance(result, MCPServerConnection) and result.connected:
            connected_count += 1
            stack.push_async_callback(_safe_close_stack, result.stack, result.name)
        elif isinstance(result, Exception):
            failed_count += 1
            failed_servers.append((server_name, str(result)))
            logger.error("MCP server '%s' connection failed: %s", server_name, result)
        else:
            failed_count += 1
            failed_servers.append((server_name, "Unknown result type"))
            logger.warning("MCP server '%s' returned unexpected result: %s", server_name, type(result))

    if failed_count > 0:
        logger.warning(
            "MCP: %d servers connected, %d servers failed: %s",
            connected_count, failed_count, [s[0] for s in failed_servers]
        )
