"""Configuration module for crew orchestration.

Provides:
- Variable resolution (environment variables, defaults)
- Configuration inheritance (extends)
- Enhanced validation with detailed messages
"""

from xbot.agent.crew.config.loader import (
    ConfigLoadError,
    CrewConfigLoader,
    load_crew_config_with_inheritance,
)
from xbot.agent.crew.config.merger import (
    MergeError,
    merge_configs,
)
from xbot.agent.crew.config.validator import (
    CrewConfigValidator,
    ValidationResult,
    validate_crew_config,
)
from xbot.agent.crew.config.variables import (
    VariableError,
    VariableResolver,
    resolve_config_variables,
)

__all__ = [
    # Loader
    "CrewConfigLoader",
    "ConfigLoadError",
    "load_crew_config_with_inheritance",
    # Merger
    "merge_configs",
    "MergeError",
    # Validator
    "CrewConfigValidator",
    "ValidationResult",
    "validate_crew_config",
    # Variables
    "VariableResolver",
    "VariableError",
    "resolve_config_variables",
]
