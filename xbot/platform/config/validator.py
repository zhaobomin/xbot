"""Configuration validation for xbot.

Validates configuration including:
- Provider compatibility with Claude SDK
- Required API keys
- Provider existence
"""

from xbot.exceptions import ConfigurationError
from xbot.platform.config.provider_registry import (
    get_provider_spec,
    get_sdk_compatible_providers,
)
from xbot.platform.config.schema import Config


def validate_config(config: Config) -> None:
    """Validate the configuration.

    Args:
        config: Configuration to validate

    Raises:
        ConfigurationError: If configuration is invalid
    """
    provider_name = config.agents.defaults.provider

    # Keep auto mode non-blocking at validation time.
    # Final provider/model resolution still happens at runtime.
    if provider_name == "auto":
        return

    # 1. Check if provider exists in registry
    spec = get_provider_spec(provider_name)
    if not spec:
        all_providers = ", ".join(get_all_provider_names_safe())
        raise ConfigurationError(
            f"Unknown provider: '{provider_name}'. "
            f"Available providers: {all_providers}"
        )

    # 2. Check provider compatibility with Claude SDK
    if not spec.supported_by_sdk:
        sdk_providers = ", ".join(get_sdk_compatible_providers())
        raise ConfigurationError(
            f"Provider '{provider_name}' is not compatible with Claude SDK Agent. "
            f"Claude SDK compatible providers: {sdk_providers}"
        )

    # 3. Check API key is configured
    provider_attr = provider_name.replace("-", "_")
    provider_config = getattr(config.providers, provider_attr, None)

    # Fallback: check custom_providers
    if not provider_config and hasattr(config.providers, 'custom_providers'):
        provider_config = config.providers.custom_providers.get(provider_attr)

    if not provider_config:
        raise ConfigurationError(
            f"API key not configured for provider '{provider_name}'. "
            f"Please set providers.{provider_name}.api_key in config.json"
        )

    # Handle SecretStr: bool(SecretStr("")) is True, so extract the real value
    api_key = provider_config.api_key
    api_key_value = (
        api_key.get_secret_value() if hasattr(api_key, "get_secret_value") else str(api_key or "")
    )
    if not api_key_value:
        raise ConfigurationError(
            f"API key not configured for provider '{provider_name}'. "
            f"Please set providers.{provider_name}.api_key in config.json"
        )


def get_all_provider_names_safe() -> list[str]:
    """Get all provider names safely (without circular import).

    Returns:
        List of provider names
    """
    from xbot.platform.config.provider_registry import PROVIDER_REGISTRY

    return list(PROVIDER_REGISTRY.keys())


def validate_provider_for_agent(provider_name: str, agent_type: str) -> None:
    """Validate that a provider is compatible with an agent type.

    Args:
        provider_name: Provider name
        agent_type: Agent type (currently "claude_sdk")

    Raises:
        ConfigurationError: If provider is not compatible
    """
    if provider_name == "auto":
        return

    spec = get_provider_spec(provider_name)
    if not spec:
        raise ConfigurationError(f"Unknown provider: '{provider_name}'")

    if agent_type == "claude_sdk" and not spec.supported_by_sdk:
        sdk_providers = ", ".join(get_sdk_compatible_providers())
        raise ConfigurationError(
            f"Provider '{provider_name}' is not compatible with Claude SDK Agent. "
            f"Claude SDK compatible providers: {sdk_providers}"
        )
