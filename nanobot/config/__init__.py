"""Configuration module for nanobot."""

from nanobot.config.loader import get_config_path, load_config
from nanobot.config.paths import (
    get_bridge_install_dir,
    get_cli_history_path,
    get_cron_dir,
    get_data_dir,
    get_legacy_sessions_dir,
    get_logs_dir,
    get_media_dir,
    get_runtime_subdir,
    get_workspace_path,
)
from nanobot.config.schema import Config
from nanobot.config.validator import validate_config, ConfigurationError
from nanobot.config.provider_registry import (
    ProviderSpec,
    PROVIDER_REGISTRY,
    get_provider_spec,
    get_sdk_compatible_providers,
    is_provider_sdk_compatible,
)

__all__ = [
    "Config",
    "load_config",
    "get_config_path",
    "get_data_dir",
    "get_runtime_subdir",
    "get_media_dir",
    "get_cron_dir",
    "get_logs_dir",
    "get_workspace_path",
    "get_cli_history_path",
    "get_bridge_install_dir",
    "get_legacy_sessions_dir",
    # New exports for dual-agent architecture
    "validate_config",
    "ConfigurationError",
    "ProviderSpec",
    "PROVIDER_REGISTRY",
    "get_provider_spec",
    "get_sdk_compatible_providers",
    "is_provider_sdk_compatible",
]
