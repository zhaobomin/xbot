"""Regression tests for current SDK options building flow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from xbot.agent.service import AgentService
from xbot.agent.types import AgentConfig
from xbot.config.schema import Config


def _make_config_mock(*, provider: str | None = None, api_key: str | None = None, claude_sdk=None):
    config = MagicMock()
    config.agents.defaults.provider = provider
    config.agents.claude_sdk = claude_sdk
    if provider and api_key:
        provider_config = MagicMock()
        provider_config.api_key = MagicMock(get_secret_value=MagicMock(return_value=api_key))
        provider_config.api_base = None
        setattr(config.providers, provider, provider_config)
    else:
        config.providers = None
    return config


@pytest.fixture
def agent_config() -> AgentConfig:
    return AgentConfig(model="test-model", system_prompt="You are a test assistant.")


@pytest.mark.asyncio
async def test_build_sdk_options_expands_workspace(agent_config: AgentConfig) -> None:
    service = AgentService()
    resources = {
        "workspace": "~/xbot_test_workspace",
        "config": _make_config_mock(claude_sdk=None),
    }
    await service.initialize(agent_config, resources)

    options = service._build_sdk_options()

    assert "~" not in options.cwd
    assert Path(options.cwd).is_absolute()


@pytest.mark.asyncio
async def test_build_sdk_options_propagates_sdk_fields(agent_config: AgentConfig, tmp_path: Path) -> None:
    service = AgentService()

    sdk_config = MagicMock()
    sdk_config.max_turns = 500
    sdk_config.permission_mode = "bypassPermissions"
    sdk_config.disallowed_tools = ["WebFetch"]

    resources = {
        "workspace": str(tmp_path),
        "config": _make_config_mock(claude_sdk=sdk_config),
    }
    await service.initialize(agent_config, resources)

    options = service._build_sdk_options()

    assert options.max_turns == 500
    assert options.permission_mode == "bypassPermissions"
    assert options.disallowed_tools == ["WebFetch"]


@pytest.mark.asyncio
async def test_build_sdk_options_sets_provider_env(agent_config: AgentConfig, tmp_path: Path) -> None:
    service = AgentService()

    resources = {
        "workspace": str(tmp_path),
        "config": _make_config_mock(provider="anthropic", api_key="test-key", claude_sdk=None),
    }
    await service.initialize(agent_config, resources)

    options = service._build_sdk_options()

    assert options.env is not None
    assert options.env.get("ANTHROPIC_API_KEY") == "test-key"


@pytest.mark.asyncio
async def test_build_sdk_options_memory_integration_auto_cli(agent_config: AgentConfig, tmp_path: Path) -> None:
    service = AgentService()
    config = Config()

    resources = {
        "workspace": str(tmp_path),
        "config": config,
        "run_mode": "cli",
    }
    await service.initialize(agent_config, resources)

    options = service._build_sdk_options()

    assert options.setting_sources == ["user", "project", "local"]


@pytest.mark.asyncio
async def test_build_sdk_options_memory_integration_auto_gateway(agent_config: AgentConfig, tmp_path: Path) -> None:
    service = AgentService()
    config = Config()

    resources = {
        "workspace": str(tmp_path),
        "config": config,
        "run_mode": "gateway",
    }
    await service.initialize(agent_config, resources)

    options = service._build_sdk_options()

    assert options.setting_sources == ["user", "project", "local"]


@pytest.mark.asyncio
async def test_build_sdk_options_memory_integration_off_disables_sources_and_settings(
    agent_config: AgentConfig, tmp_path: Path
) -> None:
    service = AgentService()
    config = Config()
    config.agents.claude_sdk.memory_integration.mode = "off"
    config.agents.claude_sdk.memory_integration.sdk_settings.auto_memory_enabled = True

    resources = {
        "workspace": str(tmp_path),
        "config": config,
        "run_mode": "gateway",
    }
    await service.initialize(agent_config, resources)

    options = service._build_sdk_options()

    assert options.setting_sources is None
    assert options.settings is None


@pytest.mark.asyncio
async def test_build_sdk_options_claude_code_preset_with_append(
    agent_config: AgentConfig, tmp_path: Path
) -> None:
    service = AgentService()
    config = Config()
    config.agents.claude_sdk.system_prompt_strategy.preset = "claude_code"
    config.agents.claude_sdk.system_prompt_strategy.append_xbot_prompt = True

    resources = {
        "workspace": str(tmp_path),
        "config": config,
    }
    await service.initialize(agent_config, resources)

    options = service._build_sdk_options()

    assert isinstance(options.system_prompt, dict)
    assert options.system_prompt["type"] == "preset"
    assert options.system_prompt["preset"] == "claude_code"
    assert options.system_prompt.get("append") == "You are a test assistant."


@pytest.mark.asyncio
async def test_build_sdk_options_writes_sdk_settings_file(agent_config: AgentConfig, tmp_path: Path) -> None:
    service = AgentService()
    config = Config()
    config.agents.claude_sdk.memory_integration.mode = "on"
    config.agents.claude_sdk.memory_integration.sdk_settings.auto_memory_enabled = True
    config.agents.claude_sdk.memory_integration.sdk_settings.auto_memory_directory = str(tmp_path / ".memory")

    resources = {
        "workspace": str(tmp_path),
        "config": config,
    }
    await service.initialize(agent_config, resources)

    options = service._build_sdk_options()

    assert options.settings is not None
    assert Path(options.settings).exists()
