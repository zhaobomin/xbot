"""Tests for config validator."""

import pytest
from unittest.mock import MagicMock, patch
from pydantic import SecretStr

from xbot.config.validator import (
    ConfigurationError,
    validate_config,
    validate_provider_for_agent,
    get_all_provider_names_safe,
)
from xbot.config.schema import Config, ProviderConfig, ProvidersConfig


class TestConfigurationError:
    """Tests for ConfigurationError."""

    def test_is_exception(self):
        """Test that ConfigurationError is an Exception."""
        assert issubclass(ConfigurationError, Exception)

    def test_can_be_raised_with_message(self):
        """Test raising ConfigurationError with message."""
        with pytest.raises(ConfigurationError) as exc_info:
            raise ConfigurationError("Test error message")
        
        assert "Test error message" in str(exc_info.value)


class TestValidateConfig:
    """Tests for validate_config function."""

    def test_validate_config_with_auto_provider(self):
        """Test that 'auto' provider skips validation."""
        config = Config()
        config.agents.type = "claude_sdk"
        config.agents.defaults.provider = "auto"
        config.providers.anthropic.api_key = "sk-test"
        
        # Should not raise
        validate_config(config)

    def test_validate_config_with_auto_provider_missing_sdk_keys(self):
        """Auto provider remains non-blocking at validation time."""
        config = Config()
        config.agents.type = "claude_sdk"
        config.agents.defaults.provider = "auto"

        # Should not raise
        validate_config(config)

    def test_validate_config_unknown_provider(self):
        """Test validation fails for unknown provider."""
        config = Config()
        config.agents.type = "claude_sdk"
        config.agents.defaults.provider = "nonexistent_provider"
        
        with pytest.raises(ConfigurationError) as exc_info:
            validate_config(config)
        
        assert "Unknown provider" in str(exc_info.value)

    def test_validate_config_sdk_incompatible_provider(self):
        """Test validation fails for unknown provider (removed providers are unknown)."""
        config = Config()
        config.agents.type = "claude_sdk"
        config.agents.defaults.provider = "openrouter"  # No longer in registry
        config.providers.openrouter.api_key = "test_key"

        with pytest.raises(ConfigurationError) as exc_info:
            validate_config(config)

        assert "Unknown provider" in str(exc_info.value)

    def test_validate_config_missing_api_key(self):
        """Test validation fails for missing API key."""
        config = Config()
        config.agents.type = "claude_sdk"
        config.agents.defaults.provider = "anthropic"
        # No API key set
        
        with pytest.raises(ConfigurationError) as exc_info:
            validate_config(config)
        
        assert "API key not configured" in str(exc_info.value)

    def test_validate_config_success(self):
        """Test successful validation with valid config."""
        config = Config()
        config.agents.type = "claude_sdk"
        config.agents.defaults.provider = "anthropic"
        config.providers.anthropic.api_key = "sk-test-key"
        
        # Should not raise
        validate_config(config)

    def test_validate_config_aliyun_coding_plan(self):
        """Test validation with aliyun_coding_plan provider."""
        config = Config()
        config.agents.type = "claude_sdk"
        config.agents.defaults.provider = "aliyun_coding_plan"
        config.providers.aliyun_coding_plan.api_key = "test-key"
        
        # Should not raise
        validate_config(config)


class TestValidateProviderForAgent:
    """Tests for validate_provider_for_agent function."""

    def test_validate_auto_provider(self):
        """Test that 'auto' provider always passes."""
        validate_provider_for_agent("auto", "claude_sdk")

    def test_validate_unknown_provider(self):
        """Test validation fails for unknown provider."""
        with pytest.raises(ConfigurationError) as exc_info:
            validate_provider_for_agent("unknown_provider", "claude_sdk")
        
        assert "Unknown provider" in str(exc_info.value)

    def test_validate_sdk_compatible_provider(self):
        """Test SDK-compatible provider passes for claude_sdk."""
        # Should not raise
        validate_provider_for_agent("anthropic", "claude_sdk")
        validate_provider_for_agent("aliyun_coding_plan", "claude_sdk")
        validate_provider_for_agent("alrun", "claude_sdk")

    def test_validate_sdk_incompatible_provider(self):
        """Test unknown provider fails for claude_sdk."""
        with pytest.raises(ConfigurationError) as exc_info:
            validate_provider_for_agent("openrouter", "claude_sdk")  # No longer in registry

        assert "Unknown provider" in str(exc_info.value)

    def test_validate_non_sdk_agent_type_is_non_blocking(self):
        """Unknown/legacy agent type remains non-blocking for provider checks."""
        validate_provider_for_agent("anthropic", "custom")
        validate_provider_for_agent("aliyun_coding_plan", "custom")


class TestGetAllProviderNamesSafe:
    """Tests for get_all_provider_names_safe function."""

    def test_returns_list(self):
        """Test that function returns a list."""
        result = get_all_provider_names_safe()
        
        assert isinstance(result, list)

    def test_includes_known_providers(self):
        """Test that known providers are included."""
        result = get_all_provider_names_safe()

        # Should include SDK-compatible providers
        assert "anthropic" in result
        assert "aliyun_coding_plan" in result
        assert "alrun" in result

    def test_all_items_are_strings(self):
        """Test that all items are strings."""
        result = get_all_provider_names_safe()

        assert all(isinstance(name, str) for name in result)


class TestSecretStrApiKey:
    """Tests for SecretStr API key handling.

    These tests verify the fix for the SecretStr boolean evaluation bug.
    SecretStr objects are always truthy, so we must use .get_secret_value()
    to check if the API key is actually empty.
    """

    def test_empty_secret_str_is_not_valid_key(self):
        """Test that empty SecretStr is correctly identified as invalid.

        Bug: SecretStr("") is truthy in Python, but we should treat it as invalid.
        Fix: Use .get_secret_value() to check the actual string value.
        """
        config = Config()
        config.agents.type = "claude_sdk"
        config.agents.defaults.provider = "anthropic"
        # api_key defaults to SecretStr("") which should be invalid

        with pytest.raises(ConfigurationError) as exc_info:
            validate_config(config)

        assert "API key not configured" in str(exc_info.value)

    def test_non_empty_secret_str_is_valid_key(self):
        """Test that non-empty SecretStr is correctly identified as valid."""
        config = Config()
        config.agents.type = "claude_sdk"
        config.agents.defaults.provider = "anthropic"
        config.providers.anthropic.api_key = SecretStr("sk-test-key")

        # Should not raise
        validate_config(config)

    def test_get_api_key_returns_secret_value(self):
        """Test that get_api_key() returns the actual string, not SecretStr object."""
        config = Config()
        config.providers.anthropic.api_key = SecretStr("sk-my-secret-key")

        api_key = config.get_api_key("claude-3-opus")

        assert api_key == "sk-my-secret-key"
        assert isinstance(api_key, str)

    def test_get_api_key_returns_none_for_empty_secret_str(self):
        """Test that get_api_key() returns None for empty SecretStr."""
        config = Config()
        # api_key defaults to SecretStr("")

        api_key = config.get_api_key("claude-3-opus")

        assert api_key is None

    def test_get_provider_returns_none_for_empty_secret_str(self):
        """Test that get_provider() returns None when matched provider has empty API key.

        This tests the logic in _match_provider():
        when a provider matches by keyword but has empty API key,
        it should return None (not fallback to another provider).

        This is correct behavior: if user requests claude model but anthropic
        has no API key, we should not silently route to openai.
        """
        config = Config()
        # Set empty key for anthropic
        config.providers.anthropic.api_key = SecretStr("")
        # Set valid key for openai
        config.providers.openai.api_key = SecretStr("sk-openai-key")

        # When requesting claude model, should return None
        # because anthropic matches by keyword but has no key
        provider = config.get_provider("claude-3-opus")

        # Should return None (not fallback to openai)
        assert provider is None

    def test_get_provider_fallback_with_no_model_match(self):
        """Test that get_provider() can fallback when no specific provider matches.

        When no provider matches by keyword, the fallback logic kicks in
        and finds the first provider with a valid API key.
        """
        config = Config()
        # Set empty key for anthropic
        config.providers.anthropic.api_key = SecretStr("")
        # Set valid key for aliyun_coding_plan (in registry)
        config.providers.aliyun_coding_plan.api_key = SecretStr("aliyun-key")

        # When requesting a model with no keyword match
        # Should fallback to aliyun_coding_plan (first provider with valid key in registry order)
        provider = config.get_provider("unknown-model-xyz")

        # Should get aliyun_coding_plan provider as fallback (has valid key)
        assert provider is not None
        assert provider.api_key.get_secret_value() == "aliyun-key"

    def test_provider_config_secret_str_type(self):
        """Test that ProviderConfig.api_key is SecretStr type."""
        provider = ProviderConfig()

        assert isinstance(provider.api_key, SecretStr)
        assert provider.api_key.get_secret_value() == ""

    def test_provider_config_with_api_key_string(self):
        """Test that ProviderConfig can be created with string API key."""
        provider = ProviderConfig(api_key=SecretStr("test-key"))

        assert provider.api_key.get_secret_value() == "test-key"
