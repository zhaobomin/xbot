"""Shared capability catalog for skills, tools, and MCP-backed agent features."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xbot.agent.capabilities.skills_loader import _parse_skill_document, _strip_frontmatter, SkillsLoader

_TOOL_ALIASES = {
    "shell": "exec",
}

# Action extraction constants
_ACTION_RE = re.compile(r"###\s+(\w+)\s*\n([^#]+)")
_NON_ACTION_HEADERS = {"overview", "description", "usage", "example", "note", "notes"}


def _extract_action_tool_names(skill_name: str, body: str) -> set[str]:
    """Extract tool/action names from a skill body."""
    names: set[str] = set()
    for action_name, _content in _ACTION_RE.findall(body):
        normalized = action_name.lower()
        if normalized in _NON_ACTION_HEADERS:
            continue
        names.add(f"{skill_name}_{normalized}")
    if not names:
        names.add(f"skill_{skill_name.replace('-', '_')}")
    return names

_BUILTIN_TOOL_SPECS = (
    ("read_file", ()),
    ("write_file", ()),
    ("edit_file", ()),
    ("list_dir", ()),
    ("exec", ("shell",)),
    ("web_search", ()),
    ("web_fetch", ()),
    ("message", ()),
    ("cron", ()),
    ("memory", ()),
)


def canonical_tool_name(name: str) -> str:
    """Normalize tool aliases to a single canonical name."""
    return _TOOL_ALIASES.get(name, name)


@dataclass(frozen=True)
class SkillCapability:
    name: str
    path: str
    source: str
    tool_exposable: bool = False


@dataclass(frozen=True)
class BuiltinToolCapability:
    name: str
    aliases: tuple[str, ...] = ()
    source: str = "builtin"


@dataclass(frozen=True)
class MCPServerCapability:
    name: str
    transport: str
    enabled_tools: tuple[str, ...]
    source: str = "external_mcp"


class CapabilityCatalog:
    """Central shared view of agent capabilities across backends."""

    def __init__(self, workspace: str | Path, builtin_skills_dir: Path | None = None):
        self.workspace = Path(workspace)
        self.skills = SkillsLoader(self.workspace, builtin_skills_dir=builtin_skills_dir)
        self._skill_tool_name_cache: dict[tuple[bool, tuple[tuple[str, float], ...]], set[str]] = {}

    def list_skills(self, *, include_unavailable: bool = False) -> list[SkillCapability]:
        records = self.skills.list_skills(filter_unavailable=not include_unavailable)
        return [
            SkillCapability(
                name=record["name"],
                path=record["path"],
                source=record["source"],
                tool_exposable=self.skills.is_tool_exposable(record["name"]),
            )
            for record in records
        ]

    @staticmethod
    def list_builtin_tools() -> list[BuiltinToolCapability]:
        return [
            BuiltinToolCapability(name=name, aliases=aliases)
            for name, aliases in _BUILTIN_TOOL_SPECS
        ]

    @staticmethod
    def builtin_tool_names() -> set[str]:
        return {capability.name for capability in CapabilityCatalog.list_builtin_tools()}

    @staticmethod
    def list_external_mcp_servers(mcp_servers: dict[str, Any] | None) -> list[MCPServerCapability]:
        capabilities: list[MCPServerCapability] = []
        for name, cfg in (mcp_servers or {}).items():
            transport = getattr(cfg, "type", None)
            if not transport:
                if getattr(cfg, "command", ""):
                    transport = "stdio"
                elif getattr(cfg, "url", ""):
                    transport = "sse" if str(cfg.url).rstrip("/").endswith("/sse") else "streamableHttp"
                else:
                    transport = "unknown"
            enabled_tools = tuple(getattr(cfg, "enabled_tools", []) or [])
            capabilities.append(
                MCPServerCapability(
                    name=name,
                    transport=str(transport),
                    enabled_tools=enabled_tools,
                )
            )
        return capabilities

    @staticmethod
    def normalize_tool_names(names: list[str] | None) -> list[str] | None:
        if names is None:
            return None
        normalized: list[str] = []
        for name in names:
            canonical = canonical_tool_name(name)
            if canonical not in normalized:
                normalized.append(canonical)
        return normalized

    def classify_tool_name(self, name: str, *, assume_unknown_mcp: bool = False) -> str:
        normalized = canonical_tool_name(name)
        if normalized.startswith("mcp_"):
            return "mcp"
        if normalized in self.builtin_tool_names():
            return "tool"
        if normalized in self.skill_tool_names(include_unavailable=True):
            return "skill"
        if normalized.startswith("skill_"):
            return "skill"
        if assume_unknown_mcp:
            return "mcp"
        return "tool"

    def skill_tool_names(self, *, include_unavailable: bool = False) -> set[str]:
        records = self.skills.list_skills(filter_unavailable=not include_unavailable)
        fingerprint = tuple(
            sorted(
                (
                    record["path"],
                    self._safe_skill_mtime(Path(record["path"])),
                )
                for record in records
            )
        )
        cache_key = (include_unavailable, fingerprint)
        cached = self._skill_tool_name_cache.get(cache_key)
        if cached is not None:
            return set(cached)

        names: set[str] = set()
        for record in records:
            path = Path(record["path"])
            try:
                parsed = _parse_skill_document(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            tool_exposable = parsed.frontmatter.get("tool_exposable")
            if isinstance(tool_exposable, str):
                tool_exposable = tool_exposable.strip().lower() in {"true", "1", "yes", "on"}
            if not tool_exposable:
                continue
            names.update(_extract_action_tool_names(record["name"], parsed.body))

        self._skill_tool_name_cache = {cache_key: set(names)}
        return names

    @staticmethod
    def _safe_skill_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except FileNotFoundError:
            return -1.0

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        return _strip_frontmatter(content)

    def build_summary(self, *, mcp_servers: dict[str, Any] | None = None) -> str:
        skills = self.list_skills(include_unavailable=True)
        tool_skills = sorted(self.skill_tool_names(include_unavailable=True))
        builtin = sorted(self.builtin_tool_names())
        mcp = self.list_external_mcp_servers(mcp_servers)

        lines = [
            f"builtin_tools={len(builtin)}",
            f"skills={len(skills)}",
            f"tool_exposable_skills={len(tool_skills)}",
            f"mcp_servers={len(mcp)}",
        ]
        if tool_skills:
            lines.append("skill_tools=" + ", ".join(tool_skills))
        if mcp:
            lines.append(
                "mcp=" + ", ".join(
                    f"{server.name}[{server.transport}]"
                    for server in mcp
                )
            )
        return " | ".join(lines)
