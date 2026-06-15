#!/usr/bin/env python3
"""Create a minimal xbot skill directory."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MAX_SKILL_NAME_LENGTH = 64
ALLOWED_RESOURCES = {"scripts", "references", "assets"}

SKILL_TEMPLATE = """---
name: {skill_name}
description: [TODO: Complete and informative explanation of what this skill does and when to use it.]
---

# {skill_title}

## Overview

[TODO: Explain the workflow this skill enables.]
"""

EXAMPLE_SCRIPT = """#!/usr/bin/env python3
\"\"\"Example helper script for {skill_name}.\"\"\"


def main() -> None:
    print("This is an example script for {skill_name}")


if __name__ == "__main__":
    main()
"""

EXAMPLE_REFERENCE = """# API Reference

Replace this placeholder with reference material for the skill.
"""

EXAMPLE_ASSET = """Example asset placeholder.
"""


def normalize_skill_name(skill_name: str) -> str:
    normalized = skill_name.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return normalized


def title_case_skill_name(skill_name: str) -> str:
    return " ".join(part.capitalize() for part in skill_name.split("-"))


def parse_resources(raw_resources: str | None) -> list[str]:
    if not raw_resources:
        return []
    resources = [item.strip() for item in raw_resources.split(",") if item.strip()]
    invalid = sorted(set(resources) - ALLOWED_RESOURCES)
    if invalid:
        allowed = ", ".join(sorted(ALLOWED_RESOURCES))
        raise ValueError(f"Unknown resource type(s): {', '.join(invalid)}. Allowed: {allowed}")
    deduped: list[str] = []
    for resource in resources:
        if resource not in deduped:
            deduped.append(resource)
    return deduped


def _create_resource_dirs(
    skill_dir: Path,
    skill_name: str,
    resources: list[str],
    include_examples: bool,
) -> None:
    for resource in resources:
        resource_dir = skill_dir / resource
        resource_dir.mkdir(exist_ok=True)
        if not include_examples:
            continue
        if resource == "scripts":
            script = resource_dir / "example.py"
            script.write_text(EXAMPLE_SCRIPT.format(skill_name=skill_name), encoding="utf-8")
            script.chmod(0o755)
        elif resource == "references":
            (resource_dir / "api_reference.md").write_text(EXAMPLE_REFERENCE, encoding="utf-8")
        elif resource == "assets":
            (resource_dir / "example_asset.txt").write_text(EXAMPLE_ASSET, encoding="utf-8")


def init_skill(
    skill_name: str,
    path: str | Path,
    resources: list[str] | None = None,
    include_examples: bool = False,
) -> Path | None:
    skill_name = normalize_skill_name(skill_name)
    if not skill_name or len(skill_name) > MAX_SKILL_NAME_LENGTH:
        return None

    skill_dir = Path(path).resolve() / skill_name
    if skill_dir.exists():
        return None

    skill_dir.mkdir(parents=True)
    skill_title = title_case_skill_name(skill_name)
    (skill_dir / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(skill_name=skill_name, skill_title=skill_title),
        encoding="utf-8",
    )
    _create_resource_dirs(skill_dir, skill_name, resources or [], include_examples)
    return skill_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a new xbot skill directory.")
    parser.add_argument("skill_name")
    parser.add_argument("--path", required=True)
    parser.add_argument("--resources", default="")
    parser.add_argument("--examples", action="store_true")
    args = parser.parse_args()

    try:
        resources = parse_resources(args.resources)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    result = init_skill(args.skill_name, args.path, resources, args.examples)
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
