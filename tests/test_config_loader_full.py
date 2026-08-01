"""Comprehensive tests for xbot.platform.config.loader and schema."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from xbot.platform.config import loader as loader_mod
from xbot.platform.config.loader import (
    _apply_env_overrides,
    _auto_detect_provider,
    _infer_provider_name,
    _load_split_config,
    _migrate_config,
    _migrate_provider_fields,
    _provider_name_to_snake,
    get_config_dir,
    get_config_path,
    load_config,
    save_config,
    set_config_path,
)
from xbot.platform.config.schema import (
    AgentDefaults,
    ChannelsConfig,
    Config,
    PermissionConfig,
    ProviderConfig,
    ProvidersConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_config_path():
    """Reset context-local config path between tests."""
    set_config_path(None)
    yield
    set_config_path(None)


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------------
# loader._provider_name_to_snake
# ---------------------------------------------------------------------------


class TestProviderNameToSnake:
    def test_lowercase(self):
        assert _provider_name_to_snake("anthropic") == "anthropic"

    def test_camel_case(self):
        assert _provider_name_to_snake("aliyunCodingPlan") == "aliyun_coding_plan"

    def test_with_dash(self):
        assert _provider_name_to_snake("my-provider") == "my_provider"

    def test_all_upper(self):
        # First char upper not prefixed with underscore
        assert _provider_name_to_snake("Anthropic") == "anthropic"

    def test_consecutive_upper(self):
        assert _provider_name_to_snake("openAIChat") == "open_a_i_chat"

    def test_already_snake(self):
        assert _provider_name_to_snake("my_provider") == "my_provider"


# ---------------------------------------------------------------------------
# loader._infer_provider_name
# ---------------------------------------------------------------------------


class TestInferProviderName:
    def test_anthropic(self):
        assert _infer_provider_name("https://api.anthropic.com/v1") == "anthropic"

    def test_aliyun(self):
        assert (
            _infer_provider_name("https://dashscope.aliyuncs.com/compatible-mode/v1")
            == "aliyun_coding_plan"
        )

    def test_alrun(self):
        assert _infer_provider_name("http://alrun.local/api") == "alrun"

    def test_custom(self):
        assert _infer_provider_name("https://my-llm.example.com/v1") == "custom"

    def test_case_insensitive(self):
        assert _infer_provider_name("https://API.ANTHROPIC.COM") == "anthropic"


# ---------------------------------------------------------------------------
# loader._apply_env_overrides
# ---------------------------------------------------------------------------


class TestApplyEnvOverrides:
    def test_api_key(self, monkeypatch):
        monkeypatch.setenv("XBOT_API_KEY", "env-key")
        data = _apply_env_overrides({})
        assert data["providers"]["anthropic"]["apiKey"] == "env-key"

    def test_api_key_respects_provider(self, monkeypatch):
        monkeypatch.setenv("XBOT_API_KEY", "k")
        data = {"agents": {"defaults": {"provider": "alrun"}}}
        data = _apply_env_overrides(data)
        assert data["providers"]["alrun"]["apiKey"] == "k"

    def test_base_url(self, monkeypatch):
        monkeypatch.setenv("XBOT_BASE_URL", "https://base.example.com")
        data = _apply_env_overrides({})
        assert data["providers"]["anthropic"]["apiBase"] == "https://base.example.com"

    def test_model(self, monkeypatch):
        monkeypatch.setenv("XBOT_MODEL", "claude-3-5-sonnet")
        data = _apply_env_overrides({})
        assert data["agents"]["defaults"]["model"] == "claude-3-5-sonnet"

    def test_workspace(self, monkeypatch):
        monkeypatch.setenv("XBOT_WORKSPACE", "/tmp/ws")
        data = _apply_env_overrides({})
        assert data["agents"]["defaults"]["workspace"] == "/tmp/ws"

    def test_empty_env_ignored(self, monkeypatch):
        monkeypatch.setenv("XBOT_API_KEY", "")
        data = _apply_env_overrides({})
        assert "providers" not in data


# ---------------------------------------------------------------------------
# loader._auto_detect_provider
# ---------------------------------------------------------------------------


class TestAutoDetectProvider:
    def test_no_agents_section(self):
        data: dict[str, Any] = {}
        out = _auto_detect_provider(data)
        assert out == {}

    def test_explicit_provider_unchanged(self):
        data = {"agents": {"defaults": {"provider": "anthropic"}}}
        out = _auto_detect_provider(data)
        assert out["agents"]["defaults"]["provider"] == "anthropic"

    def test_detect_from_base_url(self):
        data = {
            "agents": {"defaults": {"provider": "auto"}},
            "providers": {
                "myProvider": {"apiBase": "https://api.anthropic.com/v1", "apiKey": "k"}
            },
        }
        out = _auto_detect_provider(data)
        assert out["agents"]["defaults"]["provider"] == "anthropic"

    def test_detect_custom_base_keeps_name(self):
        data = {
            "agents": {"defaults": {"provider": "auto"}},
            "providers": {"foo": {"apiBase": "https://x.example.com", "apiKey": "k"}},
        }
        out = _auto_detect_provider(data)
        assert out["agents"]["defaults"]["provider"] == "foo"

    def test_detect_from_api_key_only(self):
        data = {
            "agents": {"defaults": {"provider": "auto"}},
            "providers": {"bar": {"apiKey": "k"}},
        }
        out = _auto_detect_provider(data)
        assert out["agents"]["defaults"]["provider"] == "bar"

    def test_empty_providers_skipped(self):
        data = {
            "agents": {"defaults": {"provider": "auto"}},
            "providers": {"bar": {}},
        }
        out = _auto_detect_provider(data)
        assert out["agents"]["defaults"]["provider"] == "auto"


# ---------------------------------------------------------------------------
# loader._migrate_config / _migrate_provider_fields
# ---------------------------------------------------------------------------


class TestMigrateConfig:
    def test_restrict_to_workspace_migration(self):
        data = {"tools": {"exec": {"restrictToWorkspace": True}}}
        out = _migrate_config(data)
        assert out["tools"]["restrictToWorkspace"] is True
        assert "restrictToWorkspace" not in out["tools"]["exec"]

    def test_restrict_to_workspace_no_overwrite(self):
        data = {
            "tools": {
                "exec": {"restrictToWorkspace": True},
                "restrictToWorkspace": False,
            }
        }
        out = _migrate_config(data)
        # Existing value preserved
        assert out["tools"]["restrictToWorkspace"] is False

    def test_available_models_migration_custom(self):
        data = {
            "agents": {
                "defaults": {
                    "provider": "foo",
                    "availableModels": ["m1", "m2"],
                }
            },
        }
        out = _migrate_config(data)
        assert "availableModels" not in out["agents"]["defaults"]
        assert out["providers"]["customProviders"]["foo"]["models"] == ["m1", "m2"]

    def test_available_models_migration_anthropic(self):
        data = {
            "agents": {"defaults": {"provider": "anthropic", "availableModels": ["x"]}},
            "providers": {"anthropic": {"apiKey": "k"}},
        }
        out = _migrate_config(data)
        assert out["providers"]["anthropic"]["models"] == ["x"]

    def test_available_models_auto_provider(self):
        data = {
            "agents": {"defaults": {"provider": "auto", "availableModels": ["m"]}},
            "providers": {"customProviders": {"alrun": {"apiKey": "k"}}},
        }
        out = _migrate_config(data)
        assert out["providers"]["customProviders"]["alrun"]["models"] == ["m"]

    def test_available_models_auto_provider_fixed(self):
        data = {
            "agents": {"defaults": {"provider": "auto", "availableModels": ["m"]}},
            "providers": {"alrun": {"apiKey": "k"}},
        }
        out = _migrate_config(data)
        assert out["providers"]["customProviders"]["alrun"]["models"] == ["m"]

    def test_available_models_empty_list_ignored(self):
        data = {"agents": {"defaults": {"availableModels": []}}}
        out = _migrate_config(data)
        # No providers section created
        assert "providers" not in out

    def test_migrate_provider_fields_moves_unknown(self):
        data = {
            "providers": {
                "anthropic": {"apiKey": "k"},
                "myCustom": {"apiKey": "k2"},
            }
        }
        _migrate_provider_fields(data)
        assert "myCustom" not in data["providers"]
        assert data["providers"]["customProviders"]["my_custom"]["apiKey"] == "k2"

    def test_migrate_provider_fields_preserves_dash_name(self):
        data = {"providers": {"my-custom": {"apiKey": "k"}}}
        _migrate_provider_fields(data)
        assert data["providers"]["customProviders"]["my_custom"]["apiKey"] == "k"

    def test_migrate_provider_fields_ignores_non_dict(self):
        data = {"providers": {"foo": "not-a-dict"}}
        _migrate_provider_fields(data)
        # Not a dict => left alone at top-level (but not moved to customProviders)
        # The loop `if not isinstance(value, dict): continue` so it remains.
        assert data["providers"]["foo"] == "not-a-dict"


# ---------------------------------------------------------------------------
# loader._load_split_config
# ---------------------------------------------------------------------------


class TestLoadSplitConfig:
    def test_providers_default_json(self, config_dir: Path):
        prov_dir = config_dir / "providers"
        prov_dir.mkdir()
        (prov_dir / "default.json").write_text(
            json.dumps({"api_key": "k", "base_url": "https://api.anthropic.com/v1"})
        )
        data: dict[str, Any] = {}
        out = _load_split_config(config_dir, data)
        assert out["providers"]["anthropic"]["apiKey"] == "k"
        assert out["agents"]["defaults"]["provider"] == "anthropic"

    def test_providers_default_with_explicit_name(self, config_dir: Path):
        prov_dir = config_dir / "providers"
        prov_dir.mkdir()
        (prov_dir / "default.json").write_text(
            json.dumps({"name": "alrun", "api_key": "k"})
        )
        out = _load_split_config(config_dir, {})
        assert out["providers"]["alrun"]["apiKey"] == "k"

    def test_channels_json(self, config_dir: Path):
        ch_dir = config_dir / "channels"
        ch_dir.mkdir()
        (ch_dir / "telegram.json").write_text(json.dumps({"token": "t"}))
        out = _load_split_config(config_dir, {})
        assert out["channels"]["telegram"]["token"] == "t"

    def test_tools_json_deep_merge(self, config_dir: Path):
        (config_dir / "tools.json").write_text(
            json.dumps({"web": {"proxy": "p"}, "exec": {"timeout": 120}})
        )
        data = {"tools": {"web": {"base": "b"}}}
        out = _load_split_config(config_dir, data)
        # web was deep-merged
        assert out["tools"]["web"]["proxy"] == "p"
        assert out["tools"]["web"]["base"] == "b"
        assert out["tools"]["exec"]["timeout"] == 120

    def test_gateway_json(self, config_dir: Path):
        (config_dir / "gateway.json").write_text(json.dumps({"port": 9999}))
        out = _load_split_config(config_dir, {})
        assert out["gateway"]["port"] == 9999

    def test_corrupted_json_logs_and_skips(self, config_dir: Path, caplog):
        (config_dir / "tools.json").write_text("{not valid")
        # Should not raise
        out = _load_split_config(config_dir, {})
        assert "tools" not in out


# ---------------------------------------------------------------------------
# loader.load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_default_config_when_no_file(self, tmp_path: Path):
        path = tmp_path / "config.json"
        cfg = load_config(path)
        assert isinstance(cfg, Config)
        assert cfg.providers.anthropic.api_key.get_secret_value() == ""

    def test_load_from_file(self, tmp_path: Path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"gateway": {"port": 1234}}))
        cfg = load_config(path)
        assert cfg.gateway.port == 1234

    def test_json_decode_error_fallback(self, tmp_path: Path, caplog):
        path = tmp_path / "config.json"
        path.write_text("{bad json")
        cfg = load_config(path)
        assert isinstance(cfg, Config)

    def test_env_overrides_win(self, tmp_path: Path, monkeypatch):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"agents": {"defaults": {"model": "file-model"}}}))
        monkeypatch.setenv("XBOT_MODEL", "env-model")
        cfg = load_config(path)
        assert cfg.agents.defaults.model == "env-model"


# ---------------------------------------------------------------------------
# loader.save_config
# ---------------------------------------------------------------------------


class TestSaveConfig:
    def test_roundtrip(self, tmp_path: Path):
        path = tmp_path / "config.json"
        cfg = Config()
        cfg.gateway.port = 7777
        save_config(cfg, path)
        loaded = load_config(path)
        assert loaded.gateway.port == 7777

    def test_secret_str_encoded(self, tmp_path: Path):
        path = tmp_path / "config.json"
        cfg = Config()
        cfg.providers.anthropic.api_key = SecretStr("super-secret")
        save_config(cfg, path)
        raw = json.loads(path.read_text())
        assert raw["providers"]["anthropic"]["apiKey"] == "super-secret"

    def test_atomic_temp_file_cleanup(self, tmp_path: Path):
        path = tmp_path / "config.json"
        save_config(Config(), path)
        temps = list(tmp_path.glob(".config.json.*.tmp"))
        assert temps == []

    def test_permissions_set(self, tmp_path: Path):
        path = tmp_path / "config.json"
        save_config(Config(), path)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_parent_dirs_created(self, tmp_path: Path):
        path = tmp_path / "sub" / "dir" / "config.json"
        save_config(Config(), path)
        assert path.exists()


# ---------------------------------------------------------------------------
# loader.get_config_path / get_config_dir / set_config_path
# ---------------------------------------------------------------------------


class TestConfigPathAccessors:
    def test_default_path(self):
        p = get_config_path()
        assert p == Path.home() / ".xbot" / "config.json"

    def test_set_config_path(self, tmp_path: Path):
        custom = tmp_path / "custom.json"
        set_config_path(custom)
        assert get_config_path() == custom
        assert get_config_dir() == tmp_path

    def test_config_dir_default(self):
        assert get_config_dir() == (Path.home() / ".xbot")


# ---------------------------------------------------------------------------
# schema.ChannelsConfig
# ---------------------------------------------------------------------------


class TestChannelsConfig:
    def test_defaults(self):
        c = ChannelsConfig()
        assert c.send_progress is True
        assert c.send_tool_hints is True
        assert c.send_usage_summary is True

    def test_extra_fields_allowed(self):
        c = ChannelsConfig(telegram={"token": "x"}, discord={"token": "y"})
        assert c.model_extra["telegram"] == {"token": "x"}

    def test_camel_case_alias(self):
        c = ChannelsConfig(**{"sendProgress": False})
        assert c.send_progress is False


# ---------------------------------------------------------------------------
# schema.AgentDefaults
# ---------------------------------------------------------------------------


class TestAgentDefaults:
    def test_defaults(self):
        a = AgentDefaults()
        assert a.workspace == "~/.xbot/workspace"
        assert a.model == ""
        assert a.provider == "auto"
        assert a.max_tokens == 8192
        assert a.context_window_tokens == 65_536
        assert a.temperature == 0.1
        assert a.max_tool_iterations == 40
        assert a.memory_window is None
        assert a.load_bootstrap_files is True

    def test_deprecated_memory_window_accepted(self):
        a = AgentDefaults(**{"memoryWindow": 5})
        assert a.memory_window == 5
        # Not in model_fields_set because exclude=True
        assert a.should_warn_deprecated_memory_window is True

    def test_warn_deprecated_false_when_context_window_set(self):
        a = AgentDefaults(**{"memoryWindow": 5, "contextWindowTokens": 32000})
        assert a.should_warn_deprecated_memory_window is False


# ---------------------------------------------------------------------------
# schema.PermissionConfig
# ---------------------------------------------------------------------------


class TestPermissionConfig:
    def test_defaults(self):
        p = PermissionConfig()
        assert p.enabled is True
        assert p.timeout == 300.0
        assert p.auto_approve_safe_tools is True
        assert "read_file" in p.safe_tools
        assert "Read" in p.safe_tools
        assert "mcp__xbot__cron" in p.safe_tools


# ---------------------------------------------------------------------------
# schema.ProviderConfig / ProvidersConfig
# ---------------------------------------------------------------------------


class TestProviderConfig:
    def test_secret_str_default(self):
        p = ProviderConfig()
        assert p.api_key.get_secret_value() == ""

    def test_secret_str_set(self):
        p = ProviderConfig(api_key=SecretStr("k"))
        assert p.api_key.get_secret_value() == "k"


class TestProvidersConfig:
    def test_custom_property_default(self):
        pc = ProvidersConfig()
        # custom property returns empty ProviderConfig if missing
        assert pc.custom.api_key.get_secret_value() == ""

    def test_custom_property_setter(self):
        pc = ProvidersConfig()
        pc.custom = ProviderConfig(api_key=SecretStr("x"))
        assert pc.custom_providers["custom"].api_key.get_secret_value() == "x"

    def test_get_provider_config_fixed(self):
        pc = ProvidersConfig()
        result = pc.get_provider_config("anthropic")
        assert isinstance(result, ProviderConfig)

    def test_get_provider_config_custom(self):
        pc = ProvidersConfig(
            custom_providers={"my_provider": ProviderConfig(api_key=SecretStr("k"))}
        )
        result = pc.get_provider_config("my-provider")
        assert result is not None
        assert result.api_key.get_secret_value() == "k"

    def test_get_provider_config_none(self):
        pc = ProvidersConfig()
        assert pc.get_provider_config(None) is None
        assert pc.get_provider_config("") is None
        assert pc.get_provider_config("nonexistent") is None

    def test_camel_case_aliases(self):
        pc = ProvidersConfig(
            **{
                "customProviders": {
                    "foo": {"apiKey": "k", "apiBase": "https://x"}
                }
            }
        )
        assert pc.custom_providers["foo"].api_key.get_secret_value() == "k"


# ---------------------------------------------------------------------------
# schema.Config — root config defaults
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    def test_all_default_sections_present(self):
        cfg = Config()
        assert cfg.agents is not None
        assert cfg.channels is not None
        assert cfg.providers is not None
        assert cfg.gateway is not None
        assert cfg.tools is not None
        assert cfg.skills is not None
        assert cfg.plugins is not None

    def test_workspace_path(self, monkeypatch):
        cfg = Config()
        cfg.agents.defaults.workspace = "/tmp/ws"
        assert cfg.workspace_path == Path("/tmp/ws")

    def test_workspace_path_expands_user(self):
        cfg = Config()
        cfg.agents.defaults.workspace = "~/ws"
        assert cfg.workspace_path.name == "ws"
        assert str(cfg.workspace_path).startswith(str(Path.home()))


# ---------------------------------------------------------------------------
# schema.Config._match_provider / get_provider / get_api_key / get_api_base / get_provider_name
# ---------------------------------------------------------------------------


class TestConfigMatchProvider:
    """Test _match_provider without depending on the real PROVIDERS registry.

    We patch PROVIDERS to a controlled list of stubs.
    """

    @staticmethod
    def _make_spec(
        name: str,
        keywords: list[str] | None = None,
        is_oauth: bool = False,
        is_local: bool = False,
        is_gateway: bool = False,
        default_api_base: str | None = None,
        detect_by_base_keyword: str | None = None,
    ):
        from unittest.mock import MagicMock

        s = MagicMock()
        s.name = name
        s.keywords = keywords or [name]
        s.is_oauth = is_oauth
        s.is_local = is_local
        s.is_gateway = is_gateway
        s.default_api_base = default_api_base
        s.detect_by_base_keyword = detect_by_base_keyword
        return s

    def test_forced_provider(self, monkeypatch):
        cfg = Config()
        cfg.agents.defaults.provider = "anthropic"
        cfg.providers.anthropic.api_key = SecretStr("k")
        monkeypatch.setattr(
            "xbot.platform.config.schema.Config._match_provider",
            Config._match_provider,
        )
        p, name = cfg._match_provider()
        assert name == "anthropic"
        assert p is not None
        assert p.api_key.get_secret_value() == "k"

    def test_forced_provider_missing(self):
        cfg = Config()
        cfg.agents.defaults.provider = "nonexistent"
        p, name = cfg._match_provider()
        assert p is None
        assert name is None

    def test_keyword_match(self, monkeypatch):
        from xbot.platform.providers.registry import PROVIDERS as real_PROVIDERS

        spec = self._make_spec("deepseek", keywords=["deepseek"])
        monkeypatch.setattr(
            "xbot.platform.providers.registry.PROVIDERS",
            [spec],
        )
        cfg = Config()
        cfg.providers.custom_providers["deepseek"] = ProviderConfig(api_key=SecretStr("k"))
        p, name = cfg._match_provider("deepseek-coder")
        assert name == "deepseek"

    def test_local_fallback(self, monkeypatch):
        spec = self._make_spec(
            "ollama", keywords=["ollama"], is_local=True,
            detect_by_base_keyword="11434",
        )
        monkeypatch.setattr(
            "xbot.platform.providers.registry.PROVIDERS",
            [spec],
        )
        cfg = Config()
        cfg.providers.custom_providers["ollama"] = ProviderConfig(
            api_base="http://localhost:11434"
        )
        p, name = cfg._match_provider("llama3")
        assert name == "ollama"

    def test_gateway_fallback(self, monkeypatch):
        spec = self._make_spec("openrouter", keywords=["openrouter"], is_gateway=True)
        monkeypatch.setattr(
            "xbot.platform.providers.registry.PROVIDERS",
            [spec],
        )
        cfg = Config()
        cfg.providers.custom_providers["openrouter"] = ProviderConfig(api_key=SecretStr("k"))
        p, name = cfg._match_provider("unknown-model")
        assert name == "openrouter"

    def test_oauth_not_valid_fallback(self, monkeypatch):
        spec = self._make_spec("copilot", keywords=["copilot"], is_oauth=True)
        monkeypatch.setattr(
            "xbot.platform.providers.registry.PROVIDERS",
            [spec],
        )
        cfg = Config()
        cfg.providers.custom_providers["copilot"] = ProviderConfig(api_key=SecretStr("k"))
        p, name = cfg._match_provider("unknown")
        assert p is None

    def test_get_provider(self, monkeypatch):
        spec = self._make_spec("anthropic", keywords=["claude"])
        monkeypatch.setattr(
            "xbot.platform.providers.registry.PROVIDERS",
            [spec],
        )
        cfg = Config()
        cfg.providers.anthropic.api_key = SecretStr("k")
        p = cfg.get_provider("claude-3")
        assert p is not None

    def test_get_api_key(self, monkeypatch):
        spec = self._make_spec("anthropic", keywords=["claude"])
        monkeypatch.setattr(
            "xbot.platform.providers.registry.PROVIDERS",
            [spec],
        )
        cfg = Config()
        cfg.providers.anthropic.api_key = SecretStr("k")
        assert cfg.get_api_key("claude-3") == "k"

    def test_get_api_key_none(self):
        cfg = Config()
        assert cfg.get_api_key("xxx") is None

    def test_get_provider_name(self, monkeypatch):
        spec = self._make_spec("anthropic", keywords=["claude"])
        monkeypatch.setattr(
            "xbot.platform.providers.registry.PROVIDERS",
            [spec],
        )
        cfg = Config()
        cfg.providers.anthropic.api_key = SecretStr("k")
        assert cfg.get_provider_name("claude-3") == "anthropic"

    def test_get_api_base_gateway_default(self, monkeypatch):
        from xbot.platform.providers import registry as reg_mod

        spec = self._make_spec(
            "openrouter", keywords=["openrouter"], is_gateway=True,
            default_api_base="https://openrouter.ai/api/v1",
        )
        monkeypatch.setattr(reg_mod, "PROVIDERS", [spec])
        monkeypatch.setattr(reg_mod, "find_by_name", lambda n: spec if n == "openrouter" else None)
        cfg = Config()
        cfg.providers.custom_providers["openrouter"] = ProviderConfig(api_key=SecretStr("k"))
        base = cfg.get_api_base("openrouter/model")
        assert base == "https://openrouter.ai/api/v1"

    def test_get_api_base_explicit(self):
        cfg = Config()
        cfg.providers.anthropic.api_key = SecretStr("k")
        cfg.providers.anthropic.api_base = "https://my-base"
        base = cfg.get_api_base()
        assert base == "https://my-base"


# ---------------------------------------------------------------------------
# camelCase alias support
# ---------------------------------------------------------------------------


class TestCamelCaseAliases:
    def test_config_from_camel_case(self):
        cfg = Config.model_validate(
            {
                "agents": {
                    "defaults": {
                        "maxTokens": 4096,
                        "contextWindowTokens": 32000,
                    }
                }
            }
        )
        assert cfg.agents.defaults.max_tokens == 4096
        assert cfg.agents.defaults.context_window_tokens == 32000

    def test_provider_config_camel_case(self):
        p = ProviderConfig.model_validate(
            {"apiKey": "k", "apiBase": "https://x", "extraHeaders": {"A": "B"}}
        )
        assert p.api_key.get_secret_value() == "k"
        assert p.api_base == "https://x"
        assert p.extra_headers == {"A": "B"}

    def test_tools_config_camel_case(self):
        from xbot.platform.config.schema import ToolsConfig

        t = ToolsConfig.model_validate(
            {"restrictToWorkspace": True, "web": {"disableSecurityChecks": True}}
        )
        assert t.restrict_to_workspace is True
        assert t.web.disable_security_checks is True
