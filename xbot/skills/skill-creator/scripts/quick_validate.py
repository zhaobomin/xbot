#!/usr/bin/env python3
"""Quick validation for xbot skill directories."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

MAX_SKILL_NAME_LENGTH = 64
ALLOWED_FRONTMATTER_KEYS = {"name", "description", "license", "allowed-tools", "metadata"}
ALLOWED_ROOT_ENTRIES = {"SKILL.md", "agents", "scripts", "references", "assets"}


def validate_skill(skill_path: str | Path) -> tuple[bool, str]:
    skill_dir = Path(skill_path)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    for child in skill_dir.iterdir():
        if child.name not in ALLOWED_ROOT_ENTRIES:
            return False, f"Unexpected file or directory in skill root: {child.name}"

    content = skill_md.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"

    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return False, f"Invalid YAML in frontmatter: {exc}"
    if not isinstance(frontmatter, dict):
        return False, "Frontmatter must be a YAML dictionary"

    unexpected_keys = set(frontmatter) - ALLOWED_FRONTMATTER_KEYS
    if unexpected_keys:
        return False, f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected_keys))}"

    name = frontmatter.get("name")
    if not isinstance(name, str) or not name.strip():
        return False, "Missing 'name' in frontmatter"
    name = name.strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        return False, f"Name '{name}' should be hyphen-case"
    if "--" in name or len(name) > MAX_SKILL_NAME_LENGTH:
        return False, f"Name '{name}' is invalid"

    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        return False, "Missing 'description' in frontmatter"
    if "TODO" in description or "[TODO" in description:
        return False, "Description contains TODO placeholder"
    if "<" in description or ">" in description:
        return False, "Description cannot contain angle brackets"
    if len(description) > 1024:
        return False, "Description is too long"

    return True, "Skill is valid!"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an xbot skill directory.")
    parser.add_argument("skill_dir")
    args = parser.parse_args()

    valid, message = validate_skill(args.skill_dir)
    print(message)
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
