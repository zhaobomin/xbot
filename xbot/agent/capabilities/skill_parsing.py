"""Shared helpers for parsing skill markdown frontmatter and body."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from xbot.logging import get_logger

logger = get_logger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_ACTION_RE = re.compile(r"###\s+(\w+)\s*\n([^#]+)")
_NON_ACTION_HEADERS = {"overview", "description", "usage", "example", "note", "notes"}


@dataclass(frozen=True)
class ParsedSkillDocument:
    frontmatter: dict[str, Any]
    body: str
    description: str | None = None


def parse_skill_document(content: str) -> ParsedSkillDocument:
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


def strip_frontmatter(content: str) -> str:
    """Return body content with frontmatter removed."""
    return parse_skill_document(content).body


def extract_action_tool_names(skill_name: str, body: str) -> set[str]:
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


def iter_actions(body: str) -> list[tuple[str, str]]:
    """Return parsed action sections from a skill body."""
    actions: list[tuple[str, str]] = []
    for action_name, content in _ACTION_RE.findall(body):
        normalized = action_name.lower()
        if normalized in _NON_ACTION_HEADERS:
            continue
        actions.append((normalized, content.strip()))
    return actions


def _load_frontmatter(raw_frontmatter: str) -> dict[str, Any]:
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
    parsed: dict[str, Any] = {}
    for line in raw_frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = _coerce_scalar(value.strip().strip("\"'"))
    return parsed


def _coerce_scalar(value: str) -> Any:
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
