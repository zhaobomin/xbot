"""Configuration merging for crew inheritance.

Merge rules:
- Scalar values: child overrides parent
- dict: deep merge (recursive)
- list: append (child list extends parent list)
"""

from __future__ import annotations

from typing import Any, Callable


class MergeError(Exception):
    """Raised when configuration merge fails."""

    def __init__(self, path: str, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"Merge error at '{path}': {reason}")


def merge_configs(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Merge child configuration into parent.

    Args:
        parent: The base configuration (from template or parent)
        child: The overriding configuration

    Returns:
        A new merged configuration dictionary

    Merge rules:
        - Scalar values (str, int, float, bool): child overrides parent
        - dict: deep merge (recursive)
        - list: append (child extends parent)
        - None: treated as scalar, child overrides parent
    """
    result = _deep_copy(parent)

    for key, child_value in child.items():
        if key not in result:
            # New key, just add it
            result[key] = _deep_copy(child_value)
        else:
            parent_value = result[key]
            result[key] = _merge_values(parent_value, child_value, key)

    return result


def _merge_values(parent: Any, child: Any, path: str) -> Any:
    """Merge two values according to their types."""
    if isinstance(parent, dict) and isinstance(child, dict):
        return merge_configs(parent, child)
    elif isinstance(parent, list) and isinstance(child, list):
        # Append: child list extends parent list
        return _deep_copy(parent) + _deep_copy(child)
    else:
        # Scalar override: child replaces parent
        return _deep_copy(child)


def _deep_copy(value: Any) -> Any:
    """Create a deep copy of a value.

    Uses recursion for nested structures.
    """
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_deep_copy(item) for item in value]
    else:
        # Immutable scalar, return as-is
        return value


def merge_agent_roles(
    parent_agents: dict[str, dict],
    child_agents: dict[str, dict],
) -> dict[str, dict]:
    """Merge agent role definitions.

    Args:
        parent_agents: Parent agent definitions
        child_agents: Child agent definitions (can override)

    Returns:
        Merged agent definitions

    Special handling:
        - Child can partially override parent agent
        - New agents are added
    """
    result = {}

    # Copy parent agents
    for name, role in parent_agents.items():
        result[name] = _deep_copy(role)

    # Merge child agents
    for name, role in child_agents.items():
        if name in result:
            # Deep merge existing agent
            result[name] = merge_configs(result[name], role)
        else:
            # New agent
            result[name] = _deep_copy(role)

    return result


def merge_tasks(
    parent_tasks: list[dict],
    child_tasks: list[dict],
) -> list[dict]:
    """Merge task lists.

    Args:
        parent_tasks: Parent task list
        child_tasks: Child task list (appended)

    Returns:
        Combined task list (parent + child)

    Note:
        Tasks are appended, not replaced.
        Use task names to check for duplicates if needed.
    """
    result = []

    # Copy parent tasks
    for task in parent_tasks:
        result.append(_deep_copy(task))

    # Append child tasks
    for task in child_tasks:
        result.append(_deep_copy(task))

    return result


def check_inheritance_cycle(
    config_path: str,
    resolve_extends: Callable[[str], str | None],
    visited: set[str] | None = None,
    path: list[str] | None = None,
) -> list[str]:
    """Check for circular inheritance.

    Args:
        config_path: Path to current config
        resolve_extends: Function to get the 'extends' value from a config
        visited: Set of already visited paths
        path: Current path stack for error reporting

    Returns:
        Empty list if no cycle, otherwise the cycle path

    Example:
        >>> check_inheritance_cycle("a.yaml", lambda p: "b.yaml" if p == "a.yaml" else "a.yaml")
        ['a.yaml', 'b.yaml', 'a.yaml']
    """
    if visited is None:
        visited = set()
    if path is None:
        path = []

    abs_path = config_path  # Assume caller provides absolute path

    if abs_path in visited:
        # Found cycle
        return path + [abs_path]

    visited.add(abs_path)
    path.append(abs_path)

    try:
        extends = resolve_extends(abs_path)
        if extends:
            return check_inheritance_cycle(extends, resolve_extends, visited, path)
    except Exception:
        pass

    path.pop()
    return []


def get_inheritance_chain(
    config_path: str,
    load_config: Callable[[str], dict[str, Any]],
) -> list[tuple[str, dict]]:
    """Get the full inheritance chain from root to leaf.

    Args:
        config_path: Path to the leaf config
        load_config: Function to load a config from path

    Returns:
        List of (path, config) tuples from root to leaf

    Example:
        For A extends B extends C, returns:
        [('C.yaml', C_config), ('B.yaml', B_config), ('A.yaml', A_config)]
    """
    from pathlib import Path

    chain = []
    visited = set()

    def walk(path: str, current_dir: Path | None = None):
        # Resolve path: if relative, resolve against current config's directory
        path_obj = Path(path)
        if not path_obj.is_absolute() and current_dir is not None:
            path_obj = (current_dir / path_obj).resolve()
        else:
            path_obj = path_obj.resolve()

        abs_path = str(path_obj)
        if abs_path in visited:
            raise MergeError(path, f"Circular inheritance detected: {abs_path}")

        visited.add(abs_path)
        config = load_config(abs_path)

        extends = config.get("extends")
        if extends:
            # Resolve extends relative to current config's directory
            walk(extends, path_obj.parent)

        chain.append((abs_path, config))

    walk(config_path)
    return chain


def flatten_inheritance(
    config_path: str,
    load_config: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """Flatten an inheritance chain into a single merged config.

    Args:
        config_path: Path to the leaf config
        load_config: Function to load a config from path

    Returns:
        Fully merged configuration dictionary
    """
    chain = get_inheritance_chain(config_path, load_config)

    if not chain:
        return {}

    # Start with root config
    _, result = chain[0]

    # Merge each subsequent config
    for _, config in chain[1:]:
        result = merge_configs(result, config)

    return result
