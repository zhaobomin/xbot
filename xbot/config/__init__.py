"""Configuration module for xbot."""

from xbot.config.loader import get_config_path, load_config
from xbot.config.paths import (
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
from xbot.config.schema import Config
from xbot.config.validator import validate_config, ConfigurationError
from xbot.config.provider_registry import (
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
