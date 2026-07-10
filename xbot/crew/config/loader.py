"""Configuration loader with inheritance and variable support.

Loading flow:
1. Load base YAML
2. Resolve inheritance (extends)
3. Merge configs (parent -> child)
4. Resolve variables
5. Validate and return CrewConfig
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from xbot.crew.config.merger import (
    MergeError,
    get_inheritance_chain,
    merge_configs,
)
from xbot.crew.config.variables import (
    VariableError,
    VariableResolver,
)


class ConfigLoadError(Exception):
    """Raised when configuration loading fails."""

    def __init__(self, path: str, reason: str, suggestions: list[str] | None = None):
        self.path = path
        self.reason = reason
        self.suggestions = suggestions or []
        message = f"Failed to load config '{path}': {reason}"
        if suggestions:
            message += "\n  Suggestions:\n    - " + "\n    - ".join(suggestions)
        super().__init__(message)


class CrewConfigLoader:
    """Loads crew configuration with inheritance and variable resolution.

    Usage:
        loader = CrewConfigLoader()
        config = loader.load("crew_config.yaml")
        config = loader.load("crew_config.yaml", cli_vars={"NAME": "value"})
    """

    def __init__(
        self,
        cli_vars: dict[str, str] | None = None,
        templates_dir: Path | None = None,
    ):
        """Initialize the loader.

        Args:
            cli_vars: Variables from CLI --var arguments
            templates_dir: Directory containing template configs
        """
        self.cli_vars = cli_vars or {}
        self.templates_dir = templates_dir
        self._loaded_paths: dict[str, dict] = {}  # Cache for loaded configs

    def load(self, path: Path | str) -> dict[str, Any]:
        """Load and fully resolve a crew configuration.

        Args:
            path: Path to the configuration file

        Returns:
            Fully resolved configuration dictionary

        Raises:
            ConfigLoadError: If loading or resolution fails
        """
        path = Path(path).expanduser().resolve()

        if not path.exists():
            raise ConfigLoadError(
                str(path),
                "File not found",
                suggestions=[
                    "Check the file path is correct",
                    "Run 'xbot crew init' to create a new project",
                ],
            )

        # Load the inheritance chain
        try:
            chain = get_inheritance_chain(
                str(path),
                lambda p: self._load_raw_yaml(Path(p)),
            )
        except MergeError as e:
            raise ConfigLoadError(str(path), f"Inheritance error: {e.reason}")

        if not chain:
            raise ConfigLoadError(str(path), "Empty configuration")

        # Merge all configs in the chain
        merged = {}
        for _config_path, config in chain:
            merged = merge_configs(merged, config)

        # Extract variables from the merged config
        config_vars = merged.pop("variables", {})

        # Build builtin variables
        builtin_vars = {
            "WORKSPACE": merged.get("workspace", "."),
            "CREW_NAME": merged.get("name", ""),
            "CONFIG_DIR": str(path.parent),
            "CONFIG_NAME": path.stem,
        }

        # Resolve all variables
        try:
            resolver = VariableResolver(
                cli_vars=self.cli_vars,
                config_vars=config_vars,
                builtin_vars=builtin_vars,
            )
            resolved = resolver.resolve(merged)
        except VariableError as e:
            raise ConfigLoadError(
                str(path),
                str(e),
                suggestions=[
                    f"Define the variable in your config: variables: {e.var_name}: value",
                    f"Or set an environment variable: export {e.var_name}=value",
                    f"Or use a default value: ${{{e.var_name}:-default_value}}",
                ],
            )

        # Store the resolved config path
        resolved["_resolved_path"] = str(path)

        return resolved

    def _load_raw_yaml(self, path: Path) -> dict[str, Any]:
        """Load a YAML file without any processing.

        Args:
            path: Path to the YAML file

        Returns:
            Raw dictionary from YAML

        Raises:
            ConfigLoadError: If file cannot be loaded
        """
        path = Path(path).expanduser().resolve()

        # Check cache
        cache_key = str(path)
        if cache_key in self._loaded_paths:
            return self._loaded_paths[cache_key]

        if not path.exists():
            raise ConfigLoadError(str(path), "File not found")

        try:
            with open(path, encoding="utf-8") as f:
                content = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigLoadError(
                str(path),
                f"YAML syntax error: {e}",
                suggestions=[
                    "Check YAML syntax at https://yaml-online-parser.appspot.com/",
                    "Ensure proper indentation (use spaces, not tabs)",
                ],
            )

        if not isinstance(content, dict):
            raise ConfigLoadError(
                str(path),
                f"Expected a YAML mapping, got {type(content).__name__}",
            )

        # Cache the result
        self._loaded_paths[cache_key] = content
        return content

    def load_with_metadata(self, path: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Load config and return both config and metadata.

        Args:
            path: Path to the configuration file

        Returns:
            Tuple of (resolved_config, metadata)
            metadata includes: inheritance_chain, variables_used, source_files
        """
        path = Path(path).expanduser().resolve()

        # Get inheritance chain for metadata
        chain = get_inheritance_chain(
            str(path),
            lambda p: self._load_raw_yaml(Path(p)),
        )

        config = self.load(path)

        metadata = {
            "source_files": [p for p, _ in chain],
            "variables": {
                "cli": self.cli_vars,
                "config": config.get("variables", {}),
                "builtin": ["WORKSPACE", "CREW_NAME", "CONFIG_DIR", "CONFIG_NAME"],
            },
        }

        return config, metadata


def load_crew_config_with_inheritance(
    path: Path | str,
    cli_vars: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Convenience function to load a crew config.

    Args:
        path: Path to the configuration file
        cli_vars: Variables from CLI --var arguments

    Returns:
        Fully resolved configuration dictionary
    """
    loader = CrewConfigLoader(cli_vars=cli_vars)
    return loader.load(path)
