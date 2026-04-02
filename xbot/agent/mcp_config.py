from __future__ import annotations

import os
import re
from typing import Any


_ENV_PATTERN = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def expand_env_placeholders(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [expand_env_placeholders(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env_placeholders(item) for key, item in value.items()}
    return value


def find_unresolved_env_vars(value: Any) -> list[str]:
    if isinstance(value, str):
        matches = []
        for match in _ENV_PATTERN.finditer(value):
            matches.append(match.group(1) or match.group(2) or "")
        return [item for item in matches if item]
    if isinstance(value, list):
        unresolved: list[str] = []
        for item in value:
            unresolved.extend(find_unresolved_env_vars(item))
        return unresolved
    if isinstance(value, dict):
        unresolved = []
        for item in value.values():
            unresolved.extend(find_unresolved_env_vars(item))
        return unresolved
    return []


def resolve_mcp_server_config(server_config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    resolved = expand_env_placeholders(server_config)
    unresolved = sorted(set(find_unresolved_env_vars(resolved)))
    return resolved, unresolved
