"""Shared service container for the WebUI adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from pathlib import Path
from typing import Any, Callable

from xbot.capabilities.catalog import CapabilityCatalog


@dataclass
class ServiceContainer:
    """All live services exposed to the WebUI adapter."""

    config: Any
    bus: Any
    agent: Any
    conversation_store: Any
    cron: Any
    heartbeat: Any
    save_config: Callable[[Any], None] | None = None
    data_dir: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def persist_config(self) -> None:
        if self.save_config is not None:
            self.save_config(self.config)

    def runtime_status(self) -> dict[str, Any]:
        describe = ""
        if hasattr(self.agent, "describe_runtime"):
            describe = self.agent.describe_runtime()
        backend_type = getattr(getattr(self.agent, "router", None), "backend_type", "unknown")
        return {
            "backend_type": backend_type,
            "model": getattr(self.agent, "model", self.config.agents.defaults.model),
            "description": describe,
            "workspace": str(self.config.workspace_path),
        }

    def heartbeat_status(self) -> dict[str, Any]:
        if hasattr(self.heartbeat, "status"):
            return self.heartbeat.status()
        return {
            "enabled": bool(getattr(self.heartbeat, "enabled", False)),
            "interval_s": int(getattr(self.heartbeat, "interval_s", 0)),
            "running": bool(getattr(self.heartbeat, "_running", False)),
        }

    def channel_runtime_status(self) -> dict[str, dict[str, Any]]:
        manager = self.metadata.get("channel_manager")
        if manager is None or not hasattr(manager, "get_status"):
            return {}
        status = manager.get_status()
        return status if isinstance(status, dict) else {}

    async def reload_channel(self, name: str) -> dict[str, Any]:
        manager = self.metadata.get("channel_manager")
        if manager is None:
            raise RuntimeError("Channel manager unavailable")
        if hasattr(manager, "reload_channel"):
            result = manager.reload_channel(name)
            if inspect.isawaitable(result):
                result = await result
            return result if isinstance(result, dict) else {"name": name, "reloaded": True}
        raise RuntimeError("Channel reload not supported by this manager")

    async def reload_all_channels(self) -> dict[str, Any]:
        manager = self.metadata.get("channel_manager")
        if manager is None:
            raise RuntimeError("Channel manager unavailable")
        if hasattr(manager, "reload_all"):
            result = manager.reload_all()
            if inspect.isawaitable(result):
                result = await result
            return result if isinstance(result, dict) else {"ok": True}
        raise RuntimeError("Channel reload not supported by this manager")

    def mcp_runtime_status(self) -> dict[str, dict[str, Any]]:
        tool_names: set[str] = set()

        # Legacy/local wrappers: mcp_<server>_<tool>
        tool_registry = getattr(self.agent, "tools", None)
        raw_tool_names = list(getattr(tool_registry, "tool_names", []) or [])
        for name in raw_tool_names:
            if isinstance(name, str) and name:
                tool_names.add(name)

        # SDK tool names discovered from active sessions: mcp__<server>__<tool>
        shared_resources = getattr(self.agent, "_shared_resources", {})
        runtime_registry = shared_resources.get("runtime_registry") if isinstance(shared_resources, dict) else None
        if runtime_registry is not None and hasattr(runtime_registry, "list_keys") and hasattr(runtime_registry, "get_sdk_capabilities"):
            for session_key in runtime_registry.list_keys():
                caps = runtime_registry.get_sdk_capabilities(session_key)
                for name in caps.get("tools", []):
                    if isinstance(name, str) and name:
                        tool_names.add(name)

        runtime: dict[str, dict[str, Any]] = {}
        for name, config in self.config.tools.mcp_servers.items():
            legacy_prefix = f"mcp_{name}_"
            sdk_prefix = f"mcp__{name}__"
            tools = sorted(
                tool_name
                for tool_name in tool_names
                if tool_name.startswith(legacy_prefix) or tool_name.startswith(sdk_prefix)
            )
            transport = next(
                (
                    capability.transport
                    for capability in CapabilityCatalog.list_external_mcp_servers({name: config})
                    if capability.name == name
                ),
                "unknown",
            )
            enabled = bool(getattr(config, "enabled", True))
            runtime[name] = {
                "running": bool(tools),
                "tools": tools,
                "tool_count": len(tools),
                "enabled": enabled,
                "transport": transport,
                "error": None if tools else ("disabled" if not enabled else "configured but disconnected"),
            }
        return runtime
