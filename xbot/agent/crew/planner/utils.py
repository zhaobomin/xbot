"""
Proposal: Utility classes to solve architectural issues.

This module provides:
1. LLMResponseParser - Unified JSON parsing from LLM responses
2. RoleConverter - Unified role data conversion
3. PlannerValidator - Unified input validation
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, TypeVar

from xbot.agent.crew.planner.models import (
    Capability,
    RoleDefinition,
    RoleTier,
)


T = TypeVar('T')


class LLMResponseParser:
    """Unified parser for LLM JSON responses.

    This class solves the problem of duplicated JSON parsing logic
    across multiple modules (task_planner, role_selector, crew_planner).

    Usage:
        parser = LLMResponseParser()

        # Parse JSON array
        tasks = parser.parse_array(response)

        # Parse JSON object
        analysis = parser.parse_object(response)

        # Parse with model
        from dataclasses import dataclass
        @dataclass
        class TaskData:
            name: str
            description: str

        tasks = parser.parse_array_to_model(response, TaskData)
    """

    @staticmethod
    def find_json_start(response: str, is_array: bool = True) -> int:
        """Find the start index of JSON in response."""
        target = '[' if is_array else '{'
        return response.find(target)

    @staticmethod
    def parse_array(response: str) -> list[Any] | None:
        """Parse a JSON array from LLM response.

        Args:
            response: Raw LLM response text.

        Returns:
            Parsed list or None if parsing fails.
        """
        start_idx = response.find('[')
        if start_idx == -1:
            return None

        try:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(response[start_idx:])
            return data if isinstance(data, list) else None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def parse_object(response: str) -> dict[str, Any] | None:
        """Parse a JSON object from LLM response.

        Args:
            response: Raw LLM response text.

        Returns:
            Parsed dict or None if parsing fails.
        """
        start_idx = response.find('{')
        if start_idx == -1:
            return None

        try:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(response[start_idx:])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    @staticmethod
    def parse_string_list(response: str) -> list[str]:
        """Parse a JSON array of strings from LLM response.

        Falls back to text parsing if JSON parsing fails.

        Args:
            response: Raw LLM response text.

        Returns:
            List of strings (empty if parsing fails).
        """
        data = LLMResponseParser.parse_array(response)
        if data is not None:
            result = []
            for item in data:
                if isinstance(item, str):
                    result.append(item.strip())
                elif isinstance(item, dict):
                    # Try to extract name field
                    name = item.get('name')
                    if name is not None:
                        result.append(str(name).strip())
            return result

        # Fallback: extract names from text lines
        result = []
        for line in response.split("\n"):
            line = line.strip().strip("- ").strip()
            if line and not line.startswith("#"):
                # Extract name before colon or first word
                name = line.split(":")[0].strip()
                if name:
                    result.append(name)
        return result


class RoleConverter:
    """Unified converter for RoleDefinition data transformations.

    This class solves the problem of data conversion logic scattered
    across multiple methods (to_dict, to_agent_role, to_crew_config_dict,
    _build_agents).

    Usage:
        converter = RoleConverter()

        # Convert to YAML dict
        yaml_dict = converter.to_yaml_dict(role)

        # Convert to agent config
        agent_config = converter.to_agent_config(role)

        # Convert to AgentRole
        agent_role = converter.to_agent_role(role)
    """

    # Fields that should always be included in YAML output
    YAML_FIELDS = [
        'name', 'display_name', 'description', 'goal', 'backstory',
        'tier', 'capabilities', 'tools', 'tool_restrictions',
        'max_iterations', 'timeout_multiplier', 'tags', 'examples',
    ]

    # Fields for agent config (used in crew.yaml)
    AGENT_CONFIG_FIELDS = [
        'name', 'description', 'goal', 'backstory', 'tools',
        'tool_restrictions', 'max_iterations',
    ]

    @staticmethod
    def to_yaml_dict(role: RoleDefinition) -> dict[str, Any]:
        """Convert role to YAML-serializable dict.

        This is the SINGLE SOURCE OF TRUTH for role serialization.
        """
        result = {}
        for field in RoleConverter.YAML_FIELDS:
            value = getattr(role, field, None)
            if field == 'tier':
                result[field] = value.value if value else 'extended'
            elif field == 'capabilities':
                result[field] = [c.value for c in value] if value else []
            elif value is not None:
                result[field] = value

        return result

    @staticmethod
    def to_agent_config(role: RoleDefinition) -> dict[str, Any]:
        """Convert role to agent configuration dict.

        This is used for generating crew.yaml agents section.
        """
        result = {}

        for field in RoleConverter.AGENT_CONFIG_FIELDS:
            value = getattr(role, field, None)

            if field == 'description' or field == 'goal' or field == 'backstory':
                result[field] = value or ""
            elif field == 'tools':
                # None = all tools, only include if specified
                if value is not None:
                    result[field] = value
            elif field == 'tool_restrictions':
                # Only include if specified
                if value is not None:
                    result[field] = value
            elif value is not None:
                result[field] = value

        return result

    @staticmethod
    def to_agent_role(role: RoleDefinition) -> "AgentRole":
        """Convert to execution-time AgentRole."""
        from xbot.agent.crew.models import AgentRole

        return AgentRole(
            name=role.name,
            description=role.description,
            goal=role.goal,
            backstory=role.backstory,
            tools=role.tools,
            max_iterations=role.max_iterations,
        )


class PlannerValidator:
    """Unified validator for planner inputs.

    This class consolidates validation logic that was scattered
    across CLI commands and other modules.

    Usage:
        validator = PlannerValidator()

        # Validate goal
        errors = validator.validate_goal(goal)

        # Validate path
        errors = validator.validate_path(path, must_exist=True)

        # Validate role name
        errors = validator.validate_role_name(name)
    """

    # Validation constraints
    MAX_GOAL_LENGTH = 10000
    ROLE_NAME_PATTERN = r'^[a-z][a-z0-9_]*$'
    ROLE_NAME_MAX_LENGTH = 50

    @staticmethod
    def validate_goal(goal: str | None) -> list[str]:
        """Validate goal string.

        Returns:
            List of error messages (empty if valid).
        """
        errors = []

        if not goal:
            errors.append("Goal cannot be empty")
            return errors

        if not goal.strip():
            errors.append("Goal cannot be whitespace only")
            return errors

        if len(goal) > PlannerValidator.MAX_GOAL_LENGTH:
            errors.append(f"Goal too long (max {PlannerValidator.MAX_GOAL_LENGTH} chars)")

        return errors

    @staticmethod
    def validate_path(
        path: str | Path,
        must_exist: bool = True,
        check_is_dir: bool = False,
    ) -> list[str]:
        """Validate file/directory path.

        Returns:
            List of error messages (empty if valid).
        """
        errors = []
        path = Path(path)

        # Check for path traversal
        path_str = str(path)
        if '..' in path_str:
            errors.append("Path cannot contain '..'")

        # Check existence
        if must_exist and not path.exists():
            errors.append(f"Path does not exist: {path}")

        # Check is directory
        if check_is_dir and path.exists() and not path.is_dir():
            errors.append(f"Path is not a directory: {path}")

        return errors

    @staticmethod
    def validate_role_name(name: str | None) -> list[str]:
        """Validate role name.

        Returns:
            List of error messages (empty if valid).
        """
        errors = []

        if not name:
            errors.append("Role name is required")
            return errors

        # Check for path traversal
        if '/' in name or '\\' in name:
            errors.append("Role name cannot contain path separators")
            return errors

        if '..' in name:
            errors.append("Role name cannot contain '..'")
            return errors

        # Check pattern
        if not re.match(PlannerValidator.ROLE_NAME_PATTERN, name):
            errors.append(
                f"Role name must match pattern: {PlannerValidator.ROLE_NAME_PATTERN}"
            )

        # Check length
        if len(name) > PlannerValidator.ROLE_NAME_MAX_LENGTH:
            errors.append(
                f"Role name too long (max {PlannerValidator.ROLE_NAME_MAX_LENGTH} chars)"
            )

        return errors

    @staticmethod
    def validate_tier(tier: str | None) -> tuple[RoleTier | None, list[str]]:
        """Validate and parse tier string.

        Returns:
            Tuple of (RoleTier or None, list of errors).
        """
        if not tier:
            return RoleTier.CORE, []  # Default

        try:
            return RoleTier(tier.lower()), []
        except ValueError:
            valid = [t.value for t in RoleTier]
            return None, [f"Invalid tier '{tier}'. Valid options: {valid}"]

    @staticmethod
    def validate_capability(cap_str: str | None) -> tuple[Capability | None, list[str]]:
        """Validate and parse capability string.

        Returns:
            Tuple of (Capability or None, list of errors).
        """
        if not cap_str:
            return None, ["Capability is required"]

        try:
            return Capability(cap_str.lower()), []
        except ValueError:
            return None, [f"Unknown capability: {cap_str}"]


class DependencyTracker:
    """Track task dependencies with proper validation.

    This class solves the problem of hardcoded task name dependencies
    that can lead to broken dependency chains.
    """

    def __init__(self):
        self._tasks: dict[str, str] = {}  # task_name -> last_dependency
        self._order: list[str] = []

    def add_task(self, name: str, depends_on: str | None = None) -> None:
        """Add a task with optional dependency.

        Args:
            name: Task name.
            depends_on: Name of task this depends on (None for first task).
        """
        if depends_on and depends_on not in self._tasks:
            raise ValueError(f"Unknown dependency: {depends_on}")

        self._tasks[name] = depends_on or ""
        self._order.append(name)

    def get_dependencies(self, name: str) -> list[str]:
        """Get dependencies for a task."""
        dep = self._tasks.get(name, "")
        return [dep] if dep else []

    def get_last_task(self) -> str | None:
        """Get the name of the last added task."""
        return self._order[-1] if self._order else None

    def validate(self) -> list[str]:
        """Validate all dependencies exist.

        Returns:
            List of error messages (empty if valid).
        """
        errors = []
        for name, dep in self._tasks.items():
            if dep and dep not in self._tasks:
                errors.append(f"Task '{name}' has unknown dependency '{dep}'")

        # Check for cycles
        visited = set()
        path = set()

        def check_cycle(task: str) -> bool:
            if task in path:
                return True
            if task in visited:
                return False

            path.add(task)
            dep = self._tasks.get(task, "")
            if dep and check_cycle(dep):
                return True
            path.remove(task)
            visited.add(task)
            return False

        for task in self._tasks:
            if check_cycle(task):
                errors.append(f"Circular dependency detected involving '{task}'")
                break

        return errors