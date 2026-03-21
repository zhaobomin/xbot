"""Tests for config loader."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from xbot.config.loader import (
    load_config,
    save_config,
    set_config_path,
    get_config_path,
    _migrate_config,
)
from xbot.config.schema import Config


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


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_config_creates_default_when_not_exists(self, tmp_path):
        """Test loading config when file doesn't exist returns default."""
        config_path = tmp_path / "nonexistent" / "config.json"
        
        config = load_config(config_path)
        
        assert isinstance(config, Config)
        # Should have default values
        assert config.agents.type == "litellm"  # Default agent type

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

    def test_load_config_invalid_json_returns_default(self, tmp_path, capsys):
        """Test that invalid JSON returns default config."""
        config_path = tmp_path / "config.json"
        config_path.write_text("not valid json {", encoding="utf-8")
        
        config = load_config(config_path)
        
        assert isinstance(config, Config)
        # Should print warning
        captured = capsys.readouterr()
        assert "Warning" in captured.out or "Failed" in captured.out

    def test_load_config_invalid_schema_returns_default(self, tmp_path, capsys):
        """Test that invalid schema returns default config."""
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
        
        config = load_config(config_path)
        
        assert isinstance(config, Config)


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
                "type": "litellm"
            },
            "tools": {
                "exec": {
                    "timeout": 120
                }
            }
        }
        
        result = _migrate_config(config)
        
        assert result["agents"]["type"] == "litellm"
        assert result["tools"]["exec"]["timeout"] == 120