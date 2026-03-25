"""Variable resolution for crew configuration.

Supports:
- Environment variables: ${VAR}
- Default values: ${VAR:-default}
- Nested resolution: ${VAR_${SUB}}
- Built-in variables: ${CREW_NAME}, ${WORKSPACE}
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


class VariableError(Exception):
    """Raised when a variable cannot be resolved."""

    def __init__(self, var_name: str, context: str = ""):
        self.var_name = var_name
        self.context = context
        message = f"Undefined variable: ${{{var_name}}}"
        if context:
            message += f" (in {context})"
        super().__init__(message)


@dataclass
class VariableResolver:
    """Resolves ${VAR} and ${VAR:-default} patterns in configuration.

    Resolution priority (highest to lowest):
    1. CLI variables (--var name=value)
    2. Environment variables
    3. Configuration file variables
    4. Built-in variables
    """

    # Variables from CLI --var
    cli_vars: dict[str, str] = field(default_factory=dict)

    # Variables from config file 'variables' section
    config_vars: dict[str, str] = field(default_factory=dict)

    # Built-in variables (set during resolution)
    builtin_vars: dict[str, str] = field(default_factory=dict)

    # Pattern for ${VAR} and ${VAR:-default}
    VAR_PATTERN = re.compile(r'\$\{([^}]+)\}')

    def resolve(self, value: Any, context: str = "") -> Any:
        """Resolve variables in a value.

        Args:
            value: The value to resolve (str, dict, list, etc.)
            context: Context string for error messages

        Returns:
            The resolved value with all variables substituted.
        """
        if isinstance(value, str):
            return self._resolve_string(value, context)
        elif isinstance(value, dict):
            return {k: self.resolve(v, f"{context}.{k}" if context else k) for k, v in value.items()}
        elif isinstance(value, list):
            return [self.resolve(item, f"{context}[{i}]") for i, item in enumerate(value)]
        else:
            return value

    def _resolve_string(self, s: str, context: str) -> str:
        """Resolve all ${VAR} patterns in a string."""

        def replace_var(match: re.Match) -> str:
            var_expr = match.group(1)
            return self._resolve_var_expr(var_expr, context)

        # Keep resolving until no more changes (handles nested vars)
        prev = None
        current = s
        max_iterations = 10  # Prevent infinite loops
        iterations = 0

        while current != prev and iterations < max_iterations:
            prev = current
            current = self.VAR_PATTERN.sub(replace_var, current)
            iterations += 1

        # Warn if we hit max iterations (possible circular or deeply nested reference)
        if iterations >= max_iterations and current != prev:
            logger.warning(
                f"[config] Variable resolution hit max iterations ({max_iterations}) "
                f"for string in {context}. Result may be incomplete: {current[:100]}..."
            )

        return current

    def _resolve_var_expr(self, expr: str, context: str) -> str:
        """Resolve a single variable expression like VAR or VAR:-default.

        Args:
            expr: The variable expression (without ${ and })
            context: Context for error messages

        Returns:
            The resolved value

        Raises:
            VariableError: If variable is undefined and has no default
        """
        # Check for default value syntax: VAR:-default
        if ":-" in expr:
            var_name, default = expr.split(":-", 1)
            var_name = var_name.strip()
        else:
            var_name = expr.strip()
            default = None

        # Try to resolve in priority order
        value = self._lookup_var(var_name)

        if value is not None:
            return value

        if default is not None:
            return default

        raise VariableError(var_name, context)

    def _lookup_var(self, name: str) -> str | None:
        """Look up a variable name in priority order.

        Priority:
        1. CLI variables
        2. Environment variables
        3. Config variables
        4. Built-in variables
        """
        # 1. CLI variables (highest priority)
        if name in self.cli_vars:
            return self.cli_vars[name]

        # 2. Environment variables
        env_value = os.environ.get(name)
        if env_value is not None:
            return env_value

        # 3. Config variables
        if name in self.config_vars:
            return self.config_vars[name]

        # 4. Built-in variables
        if name in self.builtin_vars:
            return self.builtin_vars[name]

        return None

    def set_builtin(self, name: str, value: str) -> None:
        """Set a built-in variable."""
        self.builtin_vars[name] = value

    def set_cli_var(self, name: str, value: str) -> None:
        """Set a CLI variable."""
        self.cli_vars[name] = value

    def set_config_vars(self, vars: dict[str, str]) -> None:
        """Set config variables."""
        self.config_vars = vars.copy()

    @classmethod
    def parse_cli_vars(cls, var_args: list[str]) -> dict[str, str]:
        """Parse CLI --var arguments into a dictionary.

        Args:
            var_args: List of "name=value" strings

        Returns:
            Dictionary of variable name -> value

        Raises:
            ValueError: If argument format is invalid
        """
        result = {}
        for arg in var_args:
            if "=" not in arg:
                raise ValueError(f"Invalid --var format: '{arg}'. Expected 'name=value'")
            name, value = arg.split("=", 1)
            result[name.strip()] = value
        return result


def resolve_config_variables(
    config: dict[str, Any],
    variables: dict[str, str] | None = None,
    cli_vars: dict[str, str] | None = None,
    builtin_vars: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve all variables in a configuration dictionary.

    Args:
        config: The configuration dictionary to resolve
        variables: Variables from config file 'variables' section
        cli_vars: Variables from CLI --var arguments
        builtin_vars: Built-in variables (CREW_NAME, WORKSPACE, etc.)

    Returns:
        The configuration with all variables resolved

    Raises:
        VariableError: If an undefined variable is encountered
    """
    resolver = VariableResolver(
        cli_vars=cli_vars or {},
        config_vars=variables or {},
        builtin_vars=builtin_vars or {},
    )
    return resolver.resolve(config)