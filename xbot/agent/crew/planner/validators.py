"""LLM response validators for consistent input handling.

This module provides centralized validation functions to solve the recurring
bug pattern of using `or` operator with falsy values (0, False, [], "").

Usage:
    from xbot.agent.crew.planner.validators import LLMValidator

    # Instead of:
    timeout = data.get("timeout") or 300  # BUG: 0 becomes 300

    # Use:
    timeout = LLMValidator.validate_timeout(data.get("timeout"))
"""

from __future__ import annotations

from typing import Any

from xbot.agent.crew.planner.models import Capability


class LLMValidator:
    """Centralized validator for LLM response parsing.

    This class solves the design problem of inconsistent input validation
    across multiple modules. All validation logic should go through this
    class to ensure consistent behavior.

    Design Principles:
    1. Never use `value or default` with numeric values
    2. Always check `value is not None` explicitly
    3. Validate and clamp numeric ranges
    4. Validate enum values against allowed sets
    """

    # Valid enum values
    VALID_COMPLEXITIES = frozenset({"simple", "medium", "complex"})
    VALID_PROCESSES = frozenset({"sequential", "hierarchical"})

    # Numeric constraints
    MIN_TIMEOUT = 1
    MAX_TIMEOUT = 3600
    DEFAULT_TIMEOUT = 300

    MIN_ESTIMATED_TASKS = 1
    MAX_ESTIMATED_TASKS = 100
    DEFAULT_ESTIMATED_TASKS = 3

    MIN_MAX_ITERATIONS = 1
    MAX_MAX_ITERATIONS = 100
    DEFAULT_MAX_ITERATIONS = 30

    MIN_TIMEOUT_MULTIPLIER = 0.1
    MAX_TIMEOUT_MULTIPLIER = 10.0
    DEFAULT_TIMEOUT_MULTIPLIER = 1.0

    MIN_PRIORITY = 0
    MAX_PRIORITY = 100
    DEFAULT_PRIORITY = 0

    @classmethod
    def validate_timeout(cls, value: Any, default: int | None = None) -> int:
        """Validate and clamp timeout value.

        Args:
            value: Raw value from LLM response.
            default: Override default timeout.

        Returns:
            Validated timeout clamped to [MIN_TIMEOUT, MAX_TIMEOUT].
        """
        default = default if default is not None else cls.DEFAULT_TIMEOUT

        if value is None:
            return default
        if not isinstance(value, (int, float)):
            return default

        return max(cls.MIN_TIMEOUT, min(cls.MAX_TIMEOUT, int(value)))

    @classmethod
    def validate_estimated_tasks(cls, value: Any) -> int:
        """Validate and clamp estimated_tasks value."""
        if value is None:
            return cls.DEFAULT_ESTIMATED_TASKS
        if not isinstance(value, (int, float)):
            return cls.DEFAULT_ESTIMATED_TASKS

        return max(cls.MIN_ESTIMATED_TASKS, min(cls.MAX_ESTIMATED_TASKS, int(value)))

    @classmethod
    def validate_complexity(cls, value: Any) -> str:
        """Validate complexity enum value.

        Returns:
            Valid complexity string, defaulting to "medium".
        """
        if isinstance(value, str) and value.lower() in cls.VALID_COMPLEXITIES:
            return value.lower()
        return "medium"

    @classmethod
    def validate_process(cls, value: Any) -> str:
        """Validate suggested_process enum value.

        Returns:
            Valid process string, defaulting to "sequential".
        """
        if isinstance(value, str) and value.lower() in cls.VALID_PROCESSES:
            return value.lower()
        return "sequential"

    @classmethod
    def validate_boolean(cls, value: Any) -> bool:
        """Parse various representations of boolean values.

        Handles:
        - True/False boolean
        - "true"/"false"/"yes"/"no" strings (case-insensitive)
        - 1/0 numbers
        - None -> False

        Returns:
            Parsed boolean value.
        """
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "yes", "1")
        if isinstance(value, (int, float)):
            return bool(value)
        return False

    @classmethod
    def validate_max_iterations(cls, value: Any) -> int:
        """Validate and clamp max_iterations value."""
        if value is None:
            return cls.DEFAULT_MAX_ITERATIONS
        if not isinstance(value, (int, float)):
            return cls.DEFAULT_MAX_ITERATIONS

        return max(cls.MIN_MAX_ITERATIONS, min(cls.MAX_MAX_ITERATIONS, int(value)))

    @classmethod
    def validate_timeout_multiplier(cls, value: Any) -> float:
        """Validate and clamp timeout_multiplier value."""
        if value is None:
            return cls.DEFAULT_TIMEOUT_MULTIPLIER
        if not isinstance(value, (int, float)):
            return cls.DEFAULT_TIMEOUT_MULTIPLIER

        return max(cls.MIN_TIMEOUT_MULTIPLIER, min(cls.MAX_TIMEOUT_MULTIPLIER, float(value)))

    @classmethod
    def validate_priority(cls, value: Any) -> int:
        """Validate and clamp priority value."""
        if value is None:
            return cls.DEFAULT_PRIORITY
        if not isinstance(value, (int, float)):
            return cls.DEFAULT_PRIORITY

        return max(cls.MIN_PRIORITY, min(cls.MAX_PRIORITY, int(value)))

    @classmethod
    def validate_string(cls, value: Any, default: str = "") -> str:
        """Validate string value, returning default for None.

        Note: Empty string "" is a valid value and will be preserved.
        Only None is replaced with default.
        """
        if value is None:
            return default
        if isinstance(value, str):
            return value
        return default

    @classmethod
    def validate_string_list(cls, value: Any) -> list[str]:
        """Validate a list of strings.

        Returns:
            List of strings, or empty list if invalid.
        """
        if value is None:
            return []
        if not isinstance(value, list):
            return []

        result = []
        for item in value:
            if isinstance(item, str):
                result.append(item)
        return result

    @classmethod
    def validate_capabilities(cls, value: Any) -> list[Capability]:
        """Validate and parse capability list.

        Returns:
            List of valid Capability enums. Invalid values are logged and skipped.
        """
        if value is None:
            return []
        if not isinstance(value, list):
            return []

        import logging
        logger = logging.getLogger(__name__)

        capabilities = []
        for item in value:
            if isinstance(item, Capability):
                capabilities.append(item)
            elif isinstance(item, str):
                try:
                    capabilities.append(Capability(item.lower()))
                except ValueError:
                    logger.warning(f"Unknown capability: {item}")

        return capabilities


# Convenience functions for common use cases
def validate_timeout(value: Any) -> int:
    """Convenience function for timeout validation."""
    return LLMValidator.validate_timeout(value)


def validate_boolean(value: Any) -> bool:
    """Convenience function for boolean validation."""
    return LLMValidator.validate_boolean(value)


def validate_complexity(value: Any) -> str:
    """Convenience function for complexity validation."""
    return LLMValidator.validate_complexity(value)


def validate_process(value: Any) -> str:
    """Convenience function for process validation."""
    return LLMValidator.validate_process(value)