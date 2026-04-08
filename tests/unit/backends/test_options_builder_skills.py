"""Regression tests for current SDK options building flow."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from xbot.interaction.permission import CLIPermissionHandler
from xbot.runtime.core.service import AgentService
from xbot.runtime.core.types import AgentConfig


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
async def test_build_sdk_options_merges_config_mcp_and_xbot_mcp(agent_config: AgentConfig, tmp_path: Path) -> None:
    service = AgentService()
    agent_config.mcp_servers = {"docs": {"type": "stdio", "command": "uvx", "args": ["mcp-docs"]}}
    resources = {
        "workspace": str(tmp_path),
        "config": _make_config_mock(claude_sdk=None),
    }
    await service.initialize(agent_config, resources)
    service._tool_adapter = MagicMock()
    service._tool_adapter.create_mcp_server.return_value = {"xbot": {"type": "sdk"}}

    options = service._build_sdk_options()

    assert options.mcp_servers is not None
    assert "docs" in options.mcp_servers
    assert "xbot" in options.mcp_servers


@pytest.mark.asyncio
async def test_build_sdk_options_injects_permission_callback(agent_config: AgentConfig, tmp_path: Path) -> None:
    service = AgentService()
    resources = {
        "workspace": str(tmp_path),
        "config": _make_config_mock(claude_sdk=None),
        "permission_handler": CLIPermissionHandler(auto_approve_safe_tools=True),
    }
    await service.initialize(agent_config, resources)

    options = service._build_sdk_options()

    assert options.can_use_tool is not None


@pytest.mark.asyncio
async def test_build_sdk_options_maps_skills_and_plugins_to_sdk_fields(
    agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    service = AgentService()
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    plugins_dir = tmp_path / "plugins"
    (plugins_dir / "alpha").mkdir(parents=True, exist_ok=True)
    (plugins_dir / "beta").mkdir(parents=True, exist_ok=True)

    config = _make_config_mock(claude_sdk=None)
    config.skills = SimpleNamespace(
        enabled=True,
        dirs=["$workspace/.claude/skills"],
        additional_dirs=[],
    )
    config.plugins = SimpleNamespace(
        enabled=True,
        dirs=["$workspace/plugins"],
        enabled_plugins=["alpha"],
        disabled_plugins=["beta"],
    )

    resources = {
        "workspace": str(tmp_path),
        "config": config,
    }
    await service.initialize(agent_config, resources)

    options = service._build_sdk_options()

    assert str(skills_dir.resolve()) in options.add_dirs
    assert options.plugins == [{"type": "local", "path": str((plugins_dir / "alpha").resolve())}]


@pytest.mark.asyncio
async def test_build_sdk_agents_maps_tools_to_sdk_first(agent_config: AgentConfig, tmp_path: Path) -> None:
    service = AgentService()
    agent_config.agents = [
        {
            "name": "worker",
            "description": "worker",
            "prompt": "do work",
            "tools": ["exec", "read_file", "web_search", "message", "unknown_tool"],
            "model": "inherit",
        }
    ]
    resources = {
        "workspace": str(tmp_path),
        "config": _make_config_mock(claude_sdk=None),
    }
    await service.initialize(agent_config, resources)

    agents = service._build_sdk_agents()

    assert agents is not None
    worker = agents["worker"]
    assert worker.tools == [
        "Bash",
        "Read",
        "mcp__xbot__web_search",
        "mcp__xbot__message",
    ]
