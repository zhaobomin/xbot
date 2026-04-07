"""Skills loader for agent capabilities."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xbot.logging import get_logger

logger = get_logger(__name__)

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"

# Frontmatter parsing constants
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class ParsedSkillDocument:
    """Parsed skill document with frontmatter and body."""
    frontmatter: dict[str, Any]
    body: str
    description: str | None = None


def _parse_skill_document(content: str) -> ParsedSkillDocument:
    """Parse YAML frontmatter and body from a SKILL.md document."""
    if not content.startswith("---"):
        stripped = content.strip()
        return ParsedSkillDocument(frontmatter={}, body=stripped, description=None)

    match = _FRONTMATTER_RE.match(content)
    if not match:
        stripped = content.strip()
        return ParsedSkillDocument(frontmatter={}, body=stripped, description=None)

    raw_frontmatter = match.group(1)
    body = match.group(2).strip()
    frontmatter = _load_frontmatter(raw_frontmatter)
    description = frontmatter.get("description")
    if description is not None and not isinstance(description, str):
        description = str(description)
    return ParsedSkillDocument(frontmatter=frontmatter, body=body, description=description)


def strip_frontmatter_from_content(content: str) -> str:
    """Return body content with frontmatter removed."""
    return _parse_skill_document(content).body


def _load_frontmatter(raw_frontmatter: str) -> dict[str, Any]:
    """Load frontmatter from raw YAML string."""
    try:
        import yaml
        parsed = yaml.safe_load(raw_frontmatter) or {}
        return parsed if isinstance(parsed, dict) else {}
    except ImportError:
        logger.debug("PyYAML unavailable; falling back to simple frontmatter parsing")
    except Exception as exc:
        logger.debug("Failed to parse YAML frontmatter: %s", exc)

    return _parse_simple_frontmatter(raw_frontmatter)


def _parse_simple_frontmatter(raw_frontmatter: str) -> dict[str, Any]:
    """Simple frontmatter parser for basic key: value pairs."""
    parsed: dict[str, Any] = {}
    for line in raw_frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = _coerce_scalar(value.strip().strip("\"'"))
    return parsed


def _coerce_scalar(value: str) -> Any:
    """Coerce a string value to appropriate Python type."""
    lowered = value.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none"}:
        return None
    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except ValueError:
            return value
    if re.fullmatch(r"-?\d+\.\d+", value):
        try:
            return float(value)
        except ValueError:
            return value
    return value


class SkillsLoader:
    """
    Loader for agent skills.

    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """

    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.scoped_workspace_skills = workspace / ".xbot" / "skills"
        self.personal_skills = Path.home() / ".xbot" / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.

        Returns:
            List of skill info dicts with 'name', 'path', 'source', 'type'.
        """
        skills: list[dict[str, str]] = []

        def _scan_dir(base: Path, source: str) -> None:
            if not base.exists():
                return
            for skill_dir in base.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists() and not any(s["name"] == skill_dir.name for s in skills):
                        skill_type = "python" if (skill_dir / "tool.py").exists() else "markdown"
                        skills.append({
                            "name": skill_dir.name,
                            "path": str(skill_file),
                            "source": source,
                            "type": skill_type,
                        })

        # Priority: workspace > scoped_workspace > personal > builtin
        _scan_dir(self.workspace_skills, "workspace")
        _scan_dir(self.scoped_workspace_skills, "scoped_workspace")
        _scan_dir(self.personal_skills, "personal")
        if self.builtin_skills:
            _scan_dir(self.builtin_skills, "builtin")

        # Filter by requirements
        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills

    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill content or None if not found.
        """
        # Check workspace first
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill.read_text(encoding="utf-8")

        scoped_workspace_skill = self.scoped_workspace_skills / name / "SKILL.md"
        if scoped_workspace_skill.exists():
            return scoped_workspace_skill.read_text(encoding="utf-8")

        # Check personal skills
        personal_skill = self.personal_skills / name / "SKILL.md"
        if personal_skill.exists():
            return personal_skill.read_text(encoding="utf-8")

        # Check built-in
        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")

        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.

        Args:
            skill_names: List of skill names to load.

        Returns:
            Formatted skills content.
        """
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = strip_frontmatter_from_content(content)
                parts.append(f"### Skill: {name}\n\n{content}")

        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self) -> str:
        """
        Build a summary of all skills (name, description, path, availability).

        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.

        Skills with ``disable-model-invocation: true`` are excluded.

        Returns:
            XML-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        def escape_xml(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<skills>"]
        for s in all_skills:
            skill_name = s["name"]

            # Skip skills that opted out of model invocation
            if not self.is_model_invocable(skill_name):
                continue

            name = escape_xml(skill_name)
            path = s["path"]
            desc = escape_xml(self._get_skill_description(skill_name))
            skill_meta = self._get_skill_meta(skill_name)
            available = self._check_requirements(skill_meta)

            lines.append(f"  <skill available=\"{str(available).lower()}\">")
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{path}</location>")

            # Show missing requirements for unavailable skills
            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")

            lines.append("  </skill>")
        lines.append("</skills>")

        return "\n".join(lines)

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        missing = []
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)

    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # Fallback to skill name

    def strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content.

        Public method for external callers.
        """
        return strip_frontmatter_from_content(content)

    def _parse_xbot_metadata(self, raw: Any) -> dict:
        """Parse skill metadata JSON from frontmatter (supports xbot and openclaw keys)."""
        if isinstance(raw, dict):
            return raw.get("xbot", raw.get("openclaw", raw))
        try:
            data = json.loads(raw)
            return data.get("xbot", data.get("openclaw", {})) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True

    def _get_skill_meta(self, name: str) -> dict:
        """Get xbot metadata for a skill (cached in frontmatter)."""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_xbot_metadata(meta.get("metadata", ""))

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        result = []
        for s in self.list_skills(filter_unavailable=True):
            meta = self.get_skill_metadata(s["name"]) or {}
            skill_meta = self._parse_xbot_metadata(meta.get("metadata", ""))
            if skill_meta.get("always") or meta.get("always"):
                result.append(s["name"])
        return result

    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.

        Args:
            name: Skill name.

        Returns:
            Metadata dict or None.
        """
        content = self.load_skill(name)
        if not content:
            return None
        parsed = _parse_skill_document(content)
        return parsed.frontmatter or None

    def is_tool_exposable(self, name: str) -> bool:
        """Return True when a skill may be exposed as a tool/MCP capability."""
        metadata = self.get_skill_metadata(name) or {}
        value = metadata.get("tool_exposable")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "on"}
        return False

    def is_model_invocable(self, name: str) -> bool:
        """Return True when the model may auto-invoke this skill.

        Skills with ``disable-model-invocation: true`` in frontmatter are
        excluded from the skills catalog so the model never sees them.
        They can still be invoked manually via ``/name``.
        """
        metadata = self.get_skill_metadata(name) or {}
        value = metadata.get("disable-model-invocation", "false")
        if isinstance(value, bool):
            return not value
        if isinstance(value, str):
            return value.strip().lower() not in {"true", "1", "yes", "on"}
        return True

    def is_user_invocable(self, name: str) -> bool:
        """Return True when the skill should appear in the ``/`` slash menu.

        Skills with ``user-invocable: false`` are hidden from the menu but
        their description stays in the catalog for model auto-invocation.
        """
        metadata = self.get_skill_metadata(name) or {}
        value = metadata.get("user-invocable", "true")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in {"false", "0", "no", "off"}
        return True

    def list_available_skills(self) -> list[dict[str, Any]]:
        """List all available skills with lightweight metadata.

        This returns only essential information (name, description, availability)
        for the Skills Catalog, without loading full skill content.

        Skills with ``disable-model-invocation: true`` are excluded because
        their descriptions should NOT appear in the model's context (Claude Code
        spec: "Description not in context, full skill loads when you invoke").

        Returns:
            List of skill info dicts with:
            - name: Skill name
            - description: Skill description from frontmatter
            - available: Whether requirements are met
            - source: Source directory (workspace, scoped_workspace, personal, builtin)
            - type: "markdown" or "python"
            - user_invocable: Whether the skill appears in slash menu
            - requires: Missing requirements (if unavailable)
        """
        all_skills = self.list_skills(filter_unavailable=False)
        result = []

        for s in all_skills:
            name = s["name"]

            # Skip skills that opted out of model invocation
            if not self.is_model_invocable(name):
                continue

            skill_meta = self._get_skill_meta(name)
            available = self._check_requirements(skill_meta)

            skill_info: dict[str, Any] = {
                "name": name,
                "description": self._get_skill_description(name),
                "available": available,
                "source": s["source"],
                "type": s.get("type", "markdown"),
                "user_invocable": self.is_user_invocable(name),
            }

            # Add missing requirements for unavailable skills
            if not available:
                skill_info["requires"] = self._get_missing_requirements(skill_meta)

            result.append(skill_info)

        return result
