"""Shared service container for the WebUI adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
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

    def reload_channel(self, name: str) -> dict[str, Any]:
        manager = self.metadata.get("channel_manager")
        if manager is None:
            raise RuntimeError("Channel manager unavailable")
        if hasattr(manager, "reload_channel"):
            result = manager.reload_channel(name)
            return result if isinstance(result, dict) else {"name": name, "reloaded": True}
        raise RuntimeError("Channel reload not supported by this manager")

    def reload_all_channels(self) -> dict[str, Any]:
        manager = self.metadata.get("channel_manager")
        if manager is None:
            raise RuntimeError("Channel manager unavailable")
        if hasattr(manager, "reload_all"):
            result = manager.reload_all()
            return result if isinstance(result, dict) else {"ok": True}
        raise RuntimeError("Channel reload not supported by this manager")

    def mcp_runtime_status(self) -> dict[str, dict[str, Any]]:
        tool_registry = getattr(self.agent, "tools", None)
        tool_names = list(getattr(tool_registry, "tool_names", []) or [])
        runtime: dict[str, dict[str, Any]] = {}
        for name, config in self.config.tools.mcp_servers.items():
            prefix = f"mcp_{name}_"
            tools = sorted(tool_name for tool_name in tool_names if tool_name.startswith(prefix))
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

    def list_skills(self) -> list[dict[str, Any]]:
        skills_root = Path(self.config.workspace_path) / "skills"
        if not skills_root.exists():
            return []

        results: list[dict[str, Any]] = []
        for skill_file in sorted(skills_root.glob("*/SKILL.md")):
            skill_dir = skill_file.parent
            results.append(
                {
                    "name": skill_dir.name,
                    "path": str(skill_file),
                    "source": "workspace",
                    "type": "skill",
                }
            )
        return results
