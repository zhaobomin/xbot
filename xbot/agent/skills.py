"""Skills loader for agent capabilities."""

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"


@dataclass
class TriggerCondition:
    """A single trigger condition for a skill.

    Attributes:
        kind: Type of trigger - 'code_contains', 'user_requests', 'file_pattern'
        patterns: List of patterns to match (strings or regex patterns)
        exclude: If True, this is an exclusion condition
    """
    kind: str
    patterns: list[str] = field(default_factory=list)
    exclude: bool = False


@dataclass
class SkillTriggers:
    """Trigger configuration for a skill.

    Attributes:
        triggers: List of TriggerCondition that activate this skill
        excludes: List of TriggerCondition that prevent activation
    """
    triggers: list[TriggerCondition] = field(default_factory=list)
    excludes: list[TriggerCondition] = field(default_factory=list)


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
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.

        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        skills = []

        # Workspace skills (highest priority)
        if self.workspace_skills.exists():
            for skill_dir in self.workspace_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists():
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "workspace"})

        # Scoped workspace-local skills
        if self.scoped_workspace_skills.exists():
            for skill_dir in self.scoped_workspace_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists() and not any(s["name"] == skill_dir.name for s in skills):
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "scoped_workspace"})

        # Built-in skills
        if self.builtin_skills and self.builtin_skills.exists():
            for skill_dir in self.builtin_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists() and not any(s["name"] == skill_dir.name for s in skills):
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "builtin"})

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
                content = self._strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{content}")

        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self) -> str:
        """
        Build a summary of all skills (name, description, path, availability).

        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.

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
            name = escape_xml(s["name"])
            path = s["path"]
            desc = escape_xml(self._get_skill_description(s["name"]))
            skill_meta = self._get_skill_meta(s["name"])
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

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content

    def _parse_xbot_metadata(self, raw: str) -> dict:
        """Parse skill metadata JSON from frontmatter (supports xbot and openclaw keys)."""
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

        if content.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if match:
                # Simple YAML parsing
                metadata = {}
                for line in match.group(1).split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        metadata[key.strip()] = value.strip().strip('"\'')
                return metadata

        return None

    def is_tool_exposable(self, name: str) -> bool:
        """Return True when a skill may be exposed as a tool/MCP capability."""
        metadata = self.get_skill_metadata(name) or {}
        value = metadata.get("tool_exposable")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "on"}
        return False

    def get_skill_triggers(self, name: str) -> SkillTriggers:
        """Get trigger configuration for a skill.

        Parses the 'triggers' and 'excludes' sections from frontmatter.

        Example frontmatter:
        ---
        name: simplify
        description: "Review code for quality"
        triggers:
          - when: code_contains
            patterns: ["anthropic", "claude_agent_sdk"]
        excludes:
          - when: user_requests
            patterns: ["skip review"]
        ---
        """
        meta = self._get_full_metadata(name)
        if not meta:
            return SkillTriggers()

        triggers = self._parse_trigger_list(meta.get("triggers", []))
        excludes = self._parse_trigger_list(meta.get("excludes", []))

        return SkillTriggers(triggers=triggers, excludes=excludes)

    def _parse_trigger_list(self, raw_list: Any) -> list[TriggerCondition]:
        """Parse a list of trigger definitions into TriggerCondition objects."""
        if not isinstance(raw_list, list):
            return []

        conditions = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue

            when = item.get("when", "")
            patterns = item.get("patterns", [])

            if isinstance(patterns, str):
                patterns = [patterns]

            if when and patterns:
                conditions.append(TriggerCondition(
                    kind=when,
                    patterns=list(patterns),
                    exclude=False
                ))

        return conditions

    def _get_full_metadata(self, name: str) -> dict[str, Any]:
        """Get full metadata including nested structures using YAML parsing."""
        content = self.load_skill(name)
        if not content:
            return {}

        if not content.startswith("---"):
            return {}

        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return {}

        yaml_content = match.group(1)

        # Try to use PyYAML if available
        try:
            import yaml
            return yaml.safe_load(yaml_content) or {}
        except ImportError:
            pass

        # Fallback: simple nested parsing for triggers/excludes
        return self._parse_yaml_simple(yaml_content)

    def _parse_yaml_simple(self, yaml_content: str) -> dict[str, Any]:
        """Simple YAML parser for basic nested structures.

        Handles:
        - key: value
        - key: [list, of, values]
        - nested:
            - key: value
        """
        result: dict[str, Any] = {}
        lines = yaml_content.split("\n")

        current_key = None
        current_list: list[Any] = []
        in_list = False
        in_nested_list = False
        nested_item: dict[str, Any] = {}

        for line in lines:
            stripped = line.rstrip()

            # Skip empty lines
            if not stripped:
                continue

            # Check for list item (- something)
            list_match = re.match(r"^(\s*)-\s+(.+)$", stripped)

            if list_match:
                indent = len(list_match.group(1))
                value = list_match.group(2).strip()

                if indent == 0:
                    # Top-level list item
                    if current_key and in_list:
                        if ":" in value:
                            # It's a nested dict in a list
                            nested_item = {}
                            sub_key, sub_val = value.split(":", 1)
                            nested_item[sub_key.strip()] = self._parse_yaml_value(sub_val.strip())
                            current_list.append(nested_item)
                            in_nested_list = True
                        else:
                            current_list.append(self._parse_yaml_value(value))
                            in_nested_list = False
                elif in_nested_list and nested_item:
                    # Continue parsing nested dict
                    if ":" in value:
                        sub_key, sub_val = value.split(":", 1)
                        nested_item[sub_key.strip()] = self._parse_yaml_value(sub_val.strip())
                continue

            # Check for key: value
            if ":" in stripped:
                # Save previous key's list if we were building one
                if current_key and in_list:
                    result[current_key] = current_list
                    current_list = []
                    in_list = False
                    in_nested_list = False

                key, value = stripped.split(":", 1)
                key = key.strip()
                value = value.strip()

                if value.startswith("[") and value.endswith("]"):
                    # Inline list: [a, b, c]
                    result[key] = self._parse_inline_list(value)
                elif value:
                    result[key] = self._parse_yaml_value(value)
                else:
                    # Key with no value - might be a list following
                    current_key = key
                    current_list = []
                    in_list = True

        # Don't forget the last list
        if current_key and in_list:
            result[current_key] = current_list

        return result

    def _parse_yaml_value(self, value: str) -> Any:
        """Parse a YAML value into appropriate Python type."""
        if not value:
            return ""

        # Remove quotes
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            return value[1:-1]

        # Boolean
        if value.lower() in ("true", "yes", "on"):
            return True
        if value.lower() in ("false", "no", "off"):
            return False

        # Number
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            pass

        return value

    def _parse_inline_list(self, value: str) -> list[str]:
        """Parse an inline YAML list like [a, b, c]."""
        inner = value[1:-1].strip()
        if not inner:
            return []
        items = [item.strip().strip('"\'') for item in inner.split(",")]
        return [item for item in items if item]

    def get_triggered_skills(
        self,
        user_message: str = "",
        code_context: str = "",
        file_paths: list[str] | None = None,
    ) -> list[str]:
        """Get skills that should be activated based on context.

        Args:
            user_message: The user's message/prompt
            code_context: Current code being worked on (imports, file contents)
            file_paths: List of file paths being accessed

        Returns:
            List of skill names that should be triggered
        """
        triggered = []

        for skill_info in self.list_skills(filter_unavailable=True):
            name = skill_info["name"]
            triggers = self.get_skill_triggers(name)

            # Check exclusions first
            excluded = False
            for exclude_cond in triggers.excludes:
                if self._check_trigger(exclude_cond, user_message, code_context, file_paths):
                    excluded = True
                    break

            if excluded:
                continue

            # Check activation triggers
            for trigger_cond in triggers.triggers:
                if self._check_trigger(trigger_cond, user_message, code_context, file_paths):
                    triggered.append(name)
                    break  # Only add once per skill

        return triggered

    def _check_trigger(
        self,
        condition: TriggerCondition,
        user_message: str,
        code_context: str,
        file_paths: list[str] | None,
    ) -> bool:
        """Check if a single trigger condition is met."""
        if condition.kind == "code_contains":
            return self._match_patterns(condition.patterns, code_context)

        if condition.kind == "user_requests":
            return self._match_patterns(condition.patterns, user_message)

        if condition.kind == "file_pattern":
            if not file_paths:
                return False
            combined_paths = " ".join(file_paths)
            return self._match_patterns(condition.patterns, combined_paths)

        return False

    def _match_patterns(self, patterns: list[str], text: str) -> bool:
        """Check if any pattern matches the text (case-insensitive substring)."""
        text_lower = text.lower()
        for pattern in patterns:
            if pattern.lower() in text_lower:
                return True
        return False

    def list_available_skills(self) -> list[dict[str, Any]]:
        """List all available skills with lightweight metadata.

        This returns only essential information (name, description, availability)
        for the Skills Catalog, without loading full skill content.

        Returns:
            List of skill info dicts with:
            - name: Skill name
            - description: Skill description from frontmatter (includes trigger keywords per AgentSkills spec)
            - available: Whether requirements are met
            - source: Source directory (workspace, scoped_workspace, builtin)
            - requires: Missing requirements (if unavailable)
        """
        all_skills = self.list_skills(filter_unavailable=False)
        result = []

        for s in all_skills:
            name = s["name"]
            skill_meta = self._get_skill_meta(name)
            available = self._check_requirements(skill_meta)

            skill_info = {
                "name": name,
                "description": self._get_skill_description(name),
                "available": available,
                "source": s["source"],
            }

            # Add missing requirements for unavailable skills
            if not available:
                skill_info["requires"] = self._get_missing_requirements(skill_meta)

            result.append(skill_info)

        return result
