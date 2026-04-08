"""Configuration module for xbot."""

from xbot.platform.config.loader import get_config_path, load_config
from xbot.platform.config.paths import (
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
from xbot.platform.config.provider_registry import (
    PROVIDER_REGISTRY,
    ProviderSpec,
    get_provider_spec,
    get_sdk_compatible_providers,
    is_provider_sdk_compatible,
)
from xbot.platform.config.schema import Config
from xbot.platform.config.validator import ConfigurationError, validate_config

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
    "validate_config",
    "ConfigurationError",
    "ProviderSpec",
    "PROVIDER_REGISTRY",
    "get_provider_spec",
    "get_sdk_compatible_providers",
    "is_provider_sdk_compatible",
]
