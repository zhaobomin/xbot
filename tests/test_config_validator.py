"""Tests for config validator."""

import pytest
from unittest.mock import MagicMock, patch

from xbot.config.validator import (
    ConfigurationError,
    validate_config,
    validate_provider_for_agent,
    get_all_provider_names_safe,
)
from xbot.config.schema import Config


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
        """Test validation fails for SDK-incompatible provider."""
        config = Config()
        config.agents.type = "claude_sdk"
        config.agents.defaults.provider = "openrouter"
        config.providers.openrouter.api_key = "test_key"
        
        with pytest.raises(ConfigurationError) as exc_info:
            validate_config(config)
        
        assert "not compatible" in str(exc_info.value)
        assert "Claude SDK" in str(exc_info.value)

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
        validate_provider_for_agent("auto", "litellm")

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
        """Test SDK-incompatible provider fails for claude_sdk."""
        with pytest.raises(ConfigurationError) as exc_info:
            validate_provider_for_agent("openrouter", "claude_sdk")
        
        assert "not compatible" in str(exc_info.value)

    def test_validate_litellm_agent_accepts_all_providers(self):
        """Test that litellm agent accepts all providers."""
        # litellm should accept any provider
        validate_provider_for_agent("openrouter", "litellm")
        validate_provider_for_agent("anthropic", "litellm")


class TestGetAllProviderNamesSafe:
    """Tests for get_all_provider_names_safe function."""

    def test_returns_list(self):
        """Test that function returns a list."""
        result = get_all_provider_names_safe()
        
        assert isinstance(result, list)

    def test_includes_known_providers(self):
        """Test that known providers are included."""
        result = get_all_provider_names_safe()
        
        # Should include at least these providers
        assert "anthropic" in result
        assert "openrouter" in result

    def test_all_items_are_strings(self):
        """Test that all items are strings."""
        result = get_all_provider_names_safe()
        
        assert all(isinstance(name, str) for name in result)