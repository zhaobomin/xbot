"""Capability policy built on top of the shared capability catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from xbot.capabilities.catalog import CapabilityCatalog, canonical_tool_name


@dataclass(frozen=True)
class CapabilityResolution:
    allowed: list[str]
    dropped: list[str]


class CapabilityPolicy:
    """Backend-aware capability policy."""

    def __init__(
        self,
        catalog: CapabilityCatalog,
        *,
        mcp_servers: dict[str, Any] | None = None,
    ):
        self.catalog = catalog
        self.mcp_servers = mcp_servers or {}

    def available_tool_names(self, backend: str) -> set[str]:
        return set(self.catalog.builtin_tool_names())

    def resolve_agent_tools(self, names: list[str] | None, *, backend: str) -> CapabilityResolution:
        normalized = CapabilityCatalog.normalize_tool_names(names) or []
        allowed: list[str] = []
        dropped: list[str] = []
        available = self.available_tool_names(backend)
        has_mcp = bool(self.mcp_servers)

        for name in normalized:
            canonical = canonical_tool_name(name)
            if canonical in available:
                allowed.append(canonical)
                continue
            if canonical.startswith("mcp_") or (has_mcp and canonical not in self.catalog.builtin_tool_names()):
                allowed.append(canonical)
                continue
            dropped.append(canonical)

        return CapabilityResolution(allowed=allowed, dropped=dropped)

    def build_backend_trace(self, backend: str) -> str:
        return (
            f"builtin_tools={len(self.catalog.builtin_tool_names())} | "
            f"mcp_servers={len(self.mcp_servers)}"
        )
