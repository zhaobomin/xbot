"""Tool registry for dynamic tool management."""

import threading
from typing import Any

from xbot.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    Uses a threading.Lock to protect dict mutations for safety.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._lock = threading.Lock()

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        with self._lock:
            self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        with self._lock:
            self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_schema() for tool in list(self._tools.values())]

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """Execute a tool by name with given parameters."""
        from xbot.exceptions import ToolExecutionError, ToolNotFoundError

        _HINT = "\n\n[Analyze the error above and try a different approach.]"

        tool = self._tools.get(name)
        if not tool:
            raise ToolNotFoundError(
                f"Tool '{name}' not found",
                details={
                    "requested_tool": name,
                    "available_tools": self.tool_names,
                },
            )

        try:
            # Attempt to cast parameters to match schema types
            params = tool.cast_params(params)

            # Validate parameters
            errors = tool.validate_params(params)
            if errors:
                raise ToolExecutionError(
                    f"Invalid parameters for tool '{name}'",
                    details={
                        "tool": name,
                        "validation_errors": errors,
                    },
                )
            result = await tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                return result + _HINT
            return result
        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(
                f"Error executing tool '{name}': {str(e)}",
                details={"tool": name, "params": params},
                cause=e,
            ) from e

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
