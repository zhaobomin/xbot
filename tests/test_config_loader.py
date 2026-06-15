"""Tests for config loader."""

import json
from contextvars import copy_context
from pathlib import Path

import pytest

from xbot.exceptions import ConfigurationError
from xbot.platform.config.loader import (
    _auto_detect_provider,
    _migrate_config,
    get_config_path,
    load_config,
    save_config,
    set_config_path,
)
from xbot.platform.config.schema import Config


class TestConfigPath:
    """Tests for config path management."""

    def test_get_config_path_default(self, tmp_path):
        """Test default config path."""
        # Reset the global path
        set_config_path(None)

        # Default should be ~/.xbot/config.json
        path = get_config_path()
        assert path == Path.home() / ".xbot" / "config.json"

    def test_set_config_path(self, tmp_path):
        """Test setting custom config path."""
        custom_path = tmp_path / "custom" / "config.json"
        set_config_path(custom_path)

        assert get_config_path() == custom_path

        # Reset
        set_config_path(None)

    def test_get_config_path_returns_set_path(self, tmp_path):
        """Test that get_config_path returns the set path."""
        custom_path = tmp_path / "instance" / "config.json"
        set_config_path(custom_path)

        result = get_config_path()
        assert result == custom_path

        # Reset
        set_config_path(None)

    def test_config_path_is_context_local(self, tmp_path):
        """Config path overrides should not leak across independent contexts."""
        first_path = tmp_path / "first" / "config.json"
        second_path = tmp_path / "second" / "config.json"

        set_config_path(first_path)
        context = copy_context()
        set_config_path(second_path)

        assert get_config_path() == second_path
        assert context.run(get_config_path) == first_path

        set_config_path(None)


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_config_creates_default_when_not_exists(self, tmp_path):
        """Test loading config when file doesn't exist returns default."""
        config_path = tmp_path / "nonexistent" / "config.json"

        config = load_config(config_path)

        assert isinstance(config, Config)
        # Should have default values
        assert config.agents.type == "claude_sdk"  # Default agent type

    def test_load_config_from_file(self, tmp_path):
        """Test loading config from existing file."""
        config_path = tmp_path / "config.json"
        config_data = {
            "agents": {
                "defaults": {
                    "model": "test-model",
                    "provider": "anthropic",
                }
            }
        }
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        config = load_config(config_path)

        assert config.agents.defaults.model == "test-model"
        assert config.agents.defaults.provider == "anthropic"

    def test_load_config_invalid_json_returns_default(self, tmp_path, caplog):
        """Test that invalid JSON returns default config."""
        config_path = tmp_path / "config.json"
        config_path.write_text("not valid json {", encoding="utf-8")

        with caplog.at_level("WARNING"):
            config = load_config(config_path)

        assert isinstance(config, Config)
        # Should log warning
        assert any("Failed" in record.message for record in caplog.records)

    def test_load_config_invalid_schema_returns_default(self, tmp_path, caplog):
        """Test that invalid schema raises a configuration error."""
        config_path = tmp_path / "config.json"
        # Invalid type for a field
        config_data = {
            "agents": {
                "defaults": {
                    "maxTokens": "not a number",  # Should be int
                }
            }
        }
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        with caplog.at_level("WARNING"):
            with pytest.raises(ConfigurationError, match="schema validation failed"):
                load_config(config_path)


class TestSaveConfig:
    """Tests for save_config function."""

    def test_save_config_creates_directory(self, tmp_path):
        """Test that save_config creates parent directories."""
        config_path = tmp_path / "nested" / "dir" / "config.json"
        config = Config()

        save_config(config, config_path)

        assert config_path.exists()
        assert config_path.parent.is_dir()

    def test_save_config_writes_valid_json(self, tmp_path):
        """Test that saved config is valid JSON."""
        config_path = tmp_path / "config.json"
        config = Config()
        config.agents.defaults.model = "my-model"

        save_config(config, config_path)

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["agents"]["defaults"]["model"] == "my-model"

    def test_save_config_uses_aliases(self, tmp_path):
        """Test that save_config uses camelCase aliases."""
        config_path = tmp_path / "config.json"
        config = Config()

        save_config(config, config_path)

        data = json.loads(config_path.read_text(encoding="utf-8"))
        # Check that camelCase aliases are used
        assert "maxTokens" in data["agents"]["defaults"]

    def test_save_config_restricts_file_permissions(self, tmp_path):
        """Config files contain secrets and should be owner-readable only."""
        config_path = tmp_path / "config.json"

        save_config(Config(), config_path)

        assert config_path.stat().st_mode & 0o777 == 0o600


class TestMigrateConfig:
    """Tests for config migration."""

    def test_migrate_restrict_to_workspace(self):
        """Test migration of restrictToWorkspace."""
        old_config = {
            "tools": {
                "exec": {
                    "restrictToWorkspace": True
                }
            }
        }

        result = _migrate_config(old_config)

        assert result["tools"]["restrictToWorkspace"] is True
        assert "restrictToWorkspace" not in result["tools"]["exec"]

    def test_migrate_does_not_override_existing(self):
        """Test that migration doesn't override existing values."""
        config = {
            "tools": {
                "restrictToWorkspace": False,
                "exec": {
                    "restrictToWorkspace": True
                }
            }
        }

        result = _migrate_config(config)

        # Existing value should be preserved
        assert result["tools"]["restrictToWorkspace"] is False

    def test_migrate_preserves_other_fields(self):
        """Test that migration preserves other config fields."""
        config = {
            "agents": {
                "type": "claude_sdk"
            },
            "tools": {
                "exec": {
                    "timeout": 120
                }
            }
        }

        result = _migrate_config(config)

        assert result["agents"]["type"] == "claude_sdk"
        assert result["tools"]["exec"]["timeout"] == 120

    def test_migrate_registry_provider_fields_to_custom_providers(self):
        """Non-fixed provider objects should survive schema validation."""
        config = {
            "agents": {
                "defaults": {
                    "provider": "aliyun_coding_plan",
                    "model": "glm-5",
                }
            },
            "providers": {
                "aliyun_coding_plan": {
                    "apiKey": "aliyun-key",
                    "models": ["glm-5"],
                }
            },
        }

        result = _migrate_config(config)
        loaded = Config.model_validate(result)

        assert "aliyun_coding_plan" not in result["providers"]
        assert loaded.providers.custom_providers["aliyun_coding_plan"].models == ["glm-5"]
        assert loaded.get_api_key("glm-5") == "aliyun-key"

    def test_load_config_auto_detects_migrated_custom_provider(self, tmp_path):
        """Auto provider detection should inspect migrated customProviders entries."""
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "agents": {"defaults": {"provider": "auto", "model": "glm-5"}},
                    "providers": {
                        "aliyun_coding_plan": {
                            "apiKey": "aliyun-key",
                            "apiBase": "https://coding.dashscope.aliyuncs.com/apps/anthropic",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        loaded = load_config(config_path)

        assert loaded.agents.defaults.provider == "aliyun_coding_plan"
        assert loaded.get_api_key("glm-5") == "aliyun-key"


class TestAutoDetectProvider:
    def test_auto_detect_provider_uses_api_base_without_api_key(self):
        data = {
            "agents": {"defaults": {"provider": "auto"}},
            "providers": {
                "custom_proxy": {
                    "apiBase": "http://localhost:8080/v1",
                    "apiKey": "",
                }
            },
        }
        result = _auto_detect_provider(data)
        assert result["agents"]["defaults"]["provider"] == "custom_proxy"
