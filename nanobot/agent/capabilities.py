"""Shared capability catalog for skills, tools, and MCP-backed agent features."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from nanobot.agent.skills import SkillsLoader

_TOOL_ALIASES = {
    "shell": "exec",
}


def canonical_tool_name(name: str) -> str:
    """Normalize tool aliases to a single canonical name."""
    return _TOOL_ALIASES.get(name, name)


@dataclass(frozen=True)
class SkillCapability:
    name: str
    path: str
    source: str


class CapabilityCatalog:
    """Central shared view of agent capabilities across backends."""

    def __init__(self, workspace: str | Path, builtin_skills_dir: Path | None = None):
        self.workspace = Path(workspace)
        self.skills = SkillsLoader(self.workspace, builtin_skills_dir=builtin_skills_dir)

    def list_skills(self, *, include_unavailable: bool = False) -> list[SkillCapability]:
        records = self.skills.list_skills(filter_unavailable=not include_unavailable)
        return [
            SkillCapability(
                name=record["name"],
                path=record["path"],
                source=record["source"],
            )
            for record in records
        ]

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

    def skill_tool_names(self, *, include_unavailable: bool = False) -> set[str]:
        names: set[str] = set()
        for capability in self.list_skills(include_unavailable=include_unavailable):
            content = Path(capability.path).read_text(encoding="utf-8")
            body = self._strip_frontmatter(content)
            action_names = re.findall(r"###\s+(\w+)\s*\n([^#]+)", body)
            added = False
            for action_name, _ in action_names:
                if action_name.lower() in {"overview", "description", "usage", "example", "note", "notes"}:
                    continue
                names.add(f"{capability.name}_{action_name.lower()}")
                added = True
            if not added:
                names.add(f"skill_{capability.name.replace('-', '_')}")
        return names

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content
