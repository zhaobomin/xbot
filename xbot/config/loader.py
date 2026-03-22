"""Enhanced configuration loading with multi-file support.

Supports two modes:
1. Legacy: Single config.json with all settings
2. Split: Multiple files organized by domain (config.json, providers/, channels/, etc.)

Loading priority (highest to lowest):
1. Environment variables
2. Split config files (providers/*.json, channels/*.json, tools.json, gateway.json)
3. Main config.json
4. Default values
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from xbot.config.schema import Config

# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """Get the configuration file path."""
    if _current_config_path:
        return _current_config_path
    return Path.home() / ".xbot" / "config.json"


def get_config_dir() -> Path:
    """Get the configuration directory."""
    return get_config_path().parent


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file(s) or create default.

    Supports both legacy (single file) and split (multi-file) modes.
    Environment variables have the highest priority.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()
    config_dir = path.parent

    # Start with default config
    data: dict[str, Any] = {}

    # 1. Load main config.json (if exists)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data = _migrate_config(data)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    # 2. Load split config files and merge
    data = _load_split_config(config_dir, data)

    # 3. Apply environment variable overrides
    data = _apply_env_overrides(data)

    # 4. Auto-detect provider from base_url (if needed)
    data = _auto_detect_provider(data)

    try:
        return Config.model_validate(data)
    except ValidationError as e:
        print(f"Warning: Invalid configuration: {e}")
        print("Using default configuration.")
        return Config()


def _load_split_config(config_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Load split config files and merge into data."""

    # Load providers/default.json
    provider_file = config_dir / "providers" / "default.json"
    if provider_file.exists():
        try:
            with open(provider_file, encoding="utf-8") as f:
                provider_data = json.load(f)
            # Merge into providers section
            if "providers" not in data:
                data["providers"] = {}
            # Use "default" as provider name, or infer from base_url
            provider_name = provider_data.get("name", "default")
            if provider_name == "default":
                # Infer provider name from base_url
                base_url = provider_data.get("base_url", "")
                provider_name = _infer_provider_name(base_url)
            data["providers"][provider_name] = {
                "apiKey": provider_data.get("api_key", ""),
                "apiBase": provider_data.get("base_url"),
            }
            # Set default provider if not set
            if "agents" not in data:
                data["agents"] = {}
            if "defaults" not in data.get("agents", {}):
                data["agents"]["defaults"] = {}
            if "provider" not in data["agents"].get("defaults", {}):
                data["agents"]["defaults"]["provider"] = provider_name
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load provider config: {e}")

    # Load channels/*.json
    channels_dir = config_dir / "channels"
    if channels_dir.exists():
        channel_files = list(channels_dir.glob("*.json"))
        if channel_files:
            if "channels" not in data:
                data["channels"] = {}
            for channel_file in channel_files:
                channel_name = channel_file.stem  # filename without extension
                try:
                    with open(channel_file, encoding="utf-8") as f:
                        channel_data = json.load(f)
                    data["channels"][channel_name] = channel_data
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"Warning: Failed to load channel config {channel_file}: {e}")

    # Load tools.json
    tools_file = config_dir / "tools.json"
    if tools_file.exists():
        try:
            with open(tools_file, encoding="utf-8") as f:
                tools_data = json.load(f)
            if "tools" not in data:
                data["tools"] = {}
            # Deep merge tools config
            for key, value in tools_data.items():
                if isinstance(value, dict) and key in data["tools"]:
                    data["tools"][key] = {**data["tools"][key], **value}
                else:
                    data["tools"][key] = value
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load tools config: {e}")

    # Load gateway.json
    gateway_file = config_dir / "gateway.json"
    if gateway_file.exists():
        try:
            with open(gateway_file, encoding="utf-8") as f:
                gateway_data = json.load(f)
            if "gateway" not in data:
                data["gateway"] = {}
            data["gateway"] = {**data["gateway"], **gateway_data}
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load gateway config: {e}")

    return data


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Apply environment variable overrides to config data."""

    # XBOT_API_KEY -> providers.{provider}.apiKey
    api_key = os.environ.get("XBOT_API_KEY")
    if api_key:
        if "providers" not in data:
            data["providers"] = {}
        # Get or infer provider name
        provider_name = "anthropic"
        if "agents" in data and "defaults" in data["agents"]:
            provider_name = data["agents"]["defaults"].get("provider", "anthropic")
        if provider_name not in data["providers"]:
            data["providers"][provider_name] = {}
        data["providers"][provider_name]["apiKey"] = api_key

    # XBOT_BASE_URL -> providers.{provider}.apiBase
    base_url = os.environ.get("XBOT_BASE_URL")
    if base_url:
        if "providers" not in data:
            data["providers"] = {}
        provider_name = "anthropic"
        if "agents" in data and "defaults" in data["agents"]:
            provider_name = data["agents"]["defaults"].get("provider", "anthropic")
        if provider_name not in data["providers"]:
            data["providers"][provider_name] = {}
        data["providers"][provider_name]["apiBase"] = base_url

    # XBOT_MODEL -> agents.defaults.model
    model = os.environ.get("XBOT_MODEL")
    if model:
        if "agents" not in data:
            data["agents"] = {}
        if "defaults" not in data["agents"]:
            data["agents"]["defaults"] = {}
        data["agents"]["defaults"]["model"] = model

    # XBOT_WORKSPACE -> agents.defaults.workspace
    workspace = os.environ.get("XBOT_WORKSPACE")
    if workspace:
        if "agents" not in data:
            data["agents"] = {}
        if "defaults" not in data["agents"]:
            data["agents"]["defaults"] = {}
        data["agents"]["defaults"]["workspace"] = workspace

    return data


def _infer_provider_name(base_url: str) -> str:
    """Infer provider name from base_url."""
    base_url_lower = base_url.lower()

    if "api.anthropic.com" in base_url_lower:
        return "anthropic"
    elif "dashscope.aliyuncs.com" in base_url_lower:
        return "aliyun_coding_plan"
    elif "alrun" in base_url_lower:
        return "alrun"
    else:
        return "custom"


def _auto_detect_provider(data: dict[str, Any]) -> dict[str, Any]:
    """Auto-detect provider from base_url if provider is 'auto' or not set."""

    # Check if provider needs auto-detection
    provider = None
    if "agents" in data and "defaults" in data["agents"]:
        provider = data["agents"]["defaults"].get("provider", "auto")

    if provider != "auto":
        return data

    # Get base_url from providers config
    base_url = None
    providers = data.get("providers", {})
    for provider_name, provider_config in providers.items():
        if isinstance(provider_config, dict) and provider_config.get("apiKey"):
            base_url = provider_config.get("apiBase", "")
            break

    if base_url:
        detected_provider = _infer_provider_name(base_url)
        if "agents" not in data:
            data["agents"] = {}
        if "defaults" not in data["agents"]:
            data["agents"]["defaults"] = {}
        data["agents"]["defaults"]["provider"] = detected_provider

    return data


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(by_alias=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data