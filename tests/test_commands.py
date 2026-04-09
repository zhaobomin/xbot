import json
import re
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from xbot.interfaces.cli.commands import app
from xbot.platform.config.schema import Config
from xbot.platform.providers.registry import find_by_model


def _strip_ansi(text):
    """Remove ANSI escape codes from text."""
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    return ansi_escape.sub('', text)

runner = CliRunner()


class _StopGateway(RuntimeError):
    pass


@pytest.fixture
def mock_paths():
    """Mock config/workspace paths for test isolation."""
    with patch("xbot.platform.config.loader.get_config_path") as mock_cp, \
         patch("xbot.platform.config.loader.save_config") as mock_sc, \
         patch("xbot.platform.config.loader.load_config") as mock_lc, \
         patch("xbot.interfaces.cli.commands.get_workspace_path") as mock_ws:

        base_dir = Path("./test_onboard_data")
        if base_dir.exists():
            shutil.rmtree(base_dir)
        base_dir.mkdir()

        config_file = base_dir / "config.json"
        workspace_dir = base_dir / "workspace"

        mock_cp.return_value = config_file
        mock_ws.return_value = workspace_dir
        mock_lc.side_effect = lambda _config_path=None: Config()

        def _save_config(config: Config, config_path: Path | None = None):
            from pydantic import SecretStr

            target = config_path or config_file
            target.parent.mkdir(parents=True, exist_ok=True)

            def secret_str_encoder(obj):
                """Custom encoder for SecretStr to serialize actual values."""
                if isinstance(obj, SecretStr):
                    return obj.get_secret_value()
                raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

            target.write_text(
                json.dumps(config.model_dump(by_alias=True), default=secret_str_encoder),
                encoding="utf-8"
            )

        mock_sc.side_effect = _save_config

        yield config_file, workspace_dir, mock_ws

        if base_dir.exists():
            shutil.rmtree(base_dir)


def test_onboard_fresh_install(mock_paths):
    """No existing config — should create from scratch."""
    config_file, workspace_dir, mock_ws = mock_paths

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert "Created config" in result.stdout
    assert "Created workspace" in result.stdout
    assert "xbot is ready" in result.stdout
    assert config_file.exists()
    assert (workspace_dir / "AGENTS.md").exists()
    assert (workspace_dir / "memory" / "MEMORY.md").exists()
    expected_workspace = Config().workspace_path
    assert mock_ws.call_args.args == (expected_workspace,)


def test_onboard_existing_config_refresh(mock_paths):
    """Config exists, user declines overwrite — should refresh (load-merge-save)."""
    config_file, workspace_dir, _ = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "existing values preserved" in result.stdout
    assert workspace_dir.exists()
    assert (workspace_dir / "AGENTS.md").exists()


def test_onboard_existing_config_overwrite(mock_paths):
    """Config exists, user confirms overwrite — should reset to defaults."""
    config_file, workspace_dir, _ = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="y\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "Config reset to defaults" in result.stdout
    assert workspace_dir.exists()


def test_onboard_existing_workspace_safe_create(mock_paths):
    """Workspace exists — should not recreate, but still add missing templates."""
    config_file, workspace_dir, _ = mock_paths
    workspace_dir.mkdir(parents=True)
    config_file.write_text("{}")

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Created workspace" not in result.stdout
    assert "Created AGENTS.md" in result.stdout
    assert (workspace_dir / "AGENTS.md").exists()


def test_onboard_help_shows_workspace_and_config_options():
    result = runner.invoke(app, ["onboard", "--help"])

    assert result.exit_code == 0
    stripped_output = _strip_ansi(result.stdout)
    assert "--workspace" in stripped_output
    assert "-w" in stripped_output
    assert "--config" in stripped_output
    assert "-c" in stripped_output
    assert "--dir" not in stripped_output


def test_init_help_shows_pack_options():
    result = runner.invoke(app, ["init", "--help"])

    assert result.exit_code == 0
    stripped_output = _strip_ansi(result.stdout)
    assert "--workspace" in stripped_output
    assert "--config" in stripped_output
    assert "--skill-pack" in stripped_output
    assert "--command-pack" in stripped_output
    assert "--no-skill-pack" in stripped_output
    assert "--no-command-pack" in stripped_output


def test_init_installs_default_packs(mock_paths, monkeypatch):
    monkeypatch.setattr("xbot.channels.registry.discover_all", lambda: {})
    calls: dict[str, tuple[Path, str] | None] = {"skills": None, "commands": None}

    def _fake_skills(workspace: Path, pack_name: str = "default") -> list[str]:
        calls["skills"] = (workspace, pack_name)
        return ["skills/memory"]

    def _fake_commands(workspace: Path, pack_name: str = "default") -> list[str]:
        calls["commands"] = (workspace, pack_name)
        return ["commands/review.md"]

    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_skill_pack", _fake_skills)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_command_pack", _fake_commands)

    _config_file, workspace_dir, _mock_ws = mock_paths
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert calls["skills"] == (workspace_dir, "default")
    assert calls["commands"] == (workspace_dir, "default")
    assert "Installed skill pack 'default'" in result.stdout
    assert "Installed command pack 'default'" in result.stdout


def test_init_can_skip_pack_installation(mock_paths, monkeypatch):
    monkeypatch.setattr("xbot.channels.registry.discover_all", lambda: {})
    skill_called = False
    command_called = False

    def _fake_skills(_workspace: Path, pack_name: str = "default") -> list[str]:
        nonlocal skill_called
        skill_called = True
        return []

    def _fake_commands(_workspace: Path, pack_name: str = "default") -> list[str]:
        nonlocal command_called
        command_called = True
        return []

    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_skill_pack", _fake_skills)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_command_pack", _fake_commands)

    result = runner.invoke(app, ["init", "--no-skill-pack", "--no-command-pack"])

    assert result.exit_code == 0
    assert not skill_called
    assert not command_called


def test_onboard_uses_explicit_config_and_workspace_paths(tmp_path, monkeypatch):
    config_path = tmp_path / "instance" / "config.json"
    workspace_path = tmp_path / "workspace"

    monkeypatch.setattr("xbot.channels.registry.discover_all", lambda: {})

    result = runner.invoke(
        app,
        ["onboard", "--config", str(config_path), "--workspace", str(workspace_path)],
    )

    assert result.exit_code == 0
    saved = Config.model_validate(json.loads(config_path.read_text(encoding="utf-8")))
    assert saved.workspace_path == workspace_path
    assert (workspace_path / "AGENTS.md").exists()
    stripped_output = _strip_ansi(result.stdout)
    compact_output = stripped_output.replace("\n", "")
    resolved_config = str(config_path.resolve())
    assert resolved_config in compact_output
    assert f"--config {resolved_config}" in compact_output


def test_config_matches_anthropic_provider():
    config = Config()
    config.agents.defaults.model = "claude-3-opus"
    # Set API key so provider matching works
    config.providers.anthropic.api_key = "test-key"

    assert config.get_provider_name() == "anthropic"


def test_find_by_model_matches_claude():
    spec = find_by_model("claude-3-opus")

    assert spec is not None
    assert spec.name == "anthropic"


def test_config_api_base_resolves_for_claude_model():
    config = Config()
    config.agents.defaults.model = "claude-3-opus"
    config.providers.anthropic.api_key = "test-key"

    assert config.get_api_base("claude-3-opus") is None or isinstance(config.get_api_base("claude-3-opus"), str)




def test_agent_help_shows_workspace_and_config_options():
    result = runner.invoke(app, ["agent", "--help"])

    assert result.exit_code == 0
    stripped_output = _strip_ansi(result.stdout)
    assert "--workspace" in stripped_output
    assert "-w" in stripped_output
    assert "--config" in stripped_output
    assert "-c" in stripped_output













def test_gateway_uses_workspace_from_config_by_default(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    seen: dict[str, Path] = {}

    monkeypatch.setattr(
        "xbot.platform.config.loader.set_config_path",
        lambda path: seen.__setitem__("config_path", path),
    )
    monkeypatch.setattr("xbot.platform.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr(
        "xbot.interfaces.cli.commands.sync_workspace_templates",
        lambda path: seen.__setitem__("workspace", path),
    )
    monkeypatch.setattr(
        "xbot.platform.bus.queue.MessageBus",
        lambda: (_ for _ in ()).throw(_StopGateway("stop")),
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGateway)
    assert seen["config_path"] == config_file.resolve()
    assert seen["workspace"] == Path(config.agents.defaults.workspace)


def test_gateway_workspace_option_overrides_config(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    override = tmp_path / "override-workspace"
    seen: dict[str, Path] = {}

    monkeypatch.setattr("xbot.platform.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("xbot.platform.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr(
        "xbot.interfaces.cli.commands.sync_workspace_templates",
        lambda path: seen.__setitem__("workspace", path),
    )
    monkeypatch.setattr(
        "xbot.platform.bus.queue.MessageBus",
        lambda: (_ for _ in ()).throw(_StopGateway("stop")),
    )

    result = runner.invoke(
        app,
        ["gateway", "--config", str(config_file), "--workspace", str(override)],
    )

    assert isinstance(result.exception, _StopGateway)
    assert seen["workspace"] == override
    assert config.workspace_path == override


def test_gateway_warns_about_deprecated_memory_window(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.memory_window = 100

    monkeypatch.setattr("xbot.platform.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("xbot.platform.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr(
        "xbot.platform.bus.queue.MessageBus",
        lambda: (_ for _ in ()).throw(_StopGateway("stop")),
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGateway)
    assert "memoryWindow" in result.stdout
    assert "contextWindowTokens" in result.stdout

def test_gateway_uses_config_directory_for_cron_store(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "config-workspace")
    seen: dict[str, Path] = {}

    monkeypatch.setattr("xbot.platform.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("xbot.platform.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("xbot.platform.config.paths.get_cron_dir", lambda: config_file.parent / "cron")
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("xbot.runtime.session.conversation_store.ConversationStore", lambda _workspace: object())

    class _StopCron:
        def __init__(self, store_path: Path) -> None:
            seen["cron_store"] = store_path
            raise _StopGateway("stop")

    monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", _StopCron)

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGateway)
    assert seen["cron_store"] == config_file.parent / "cron" / "jobs.json"




def test_resolve_heartbeat_target_prefers_explicit_configured_target(tmp_path: Path) -> None:
    from xbot.interfaces.cli.commands import _resolve_heartbeat_target

    config = Config()
    config.gateway.heartbeat.channel = "telegram"
    config.gateway.heartbeat.chat_id = "chat-123"

    conversation_store = MagicMock(list_sessions=lambda: [
        {"key": "slack:older", "updated_at": "2026-01-01T00:00:00"},
    ])

    assert _resolve_heartbeat_target(
        config=config,
        enabled_channels=["telegram", "slack"],
        conversation_store=conversation_store,
    ) == ("telegram", "chat-123")


def test_resolve_heartbeat_target_falls_back_to_startup_session_snapshot() -> None:
    from xbot.interfaces.cli.commands import _resolve_heartbeat_target

    config = Config()
    conversation_store = MagicMock(list_sessions=lambda: [
        {"key": "cli:direct", "updated_at": "2026-01-03T00:00:00"},
        {"key": "telegram:newer", "updated_at": "2026-01-02T00:00:00"},
        {"key": "slack:older", "updated_at": "2026-01-01T00:00:00"},
    ])

    assert _resolve_heartbeat_target(
        config=config,
        enabled_channels=["telegram", "slack"],
        conversation_store=conversation_store,
    ) == ("telegram", "newer")


def test_resolve_heartbeat_target_returns_none_for_invalid_explicit_target() -> None:
    from xbot.interfaces.cli.commands import _resolve_heartbeat_target

    config = Config()
    config.gateway.heartbeat.channel = "telegram"
    config.gateway.heartbeat.chat_id = "chat-123"

    conversation_store = MagicMock(list_sessions=lambda: [
        {"key": "telegram:newer", "updated_at": "2026-01-02T00:00:00"},
    ])

    assert _resolve_heartbeat_target(
        config=config,
        enabled_channels=["slack"],
        conversation_store=conversation_store,
    ) is None


def test_gateway_uses_configured_port_when_cli_flag_is_missing(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.gateway.port = 18791

    monkeypatch.setattr("xbot.platform.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("xbot.platform.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr(
        "xbot.platform.bus.queue.MessageBus",
        lambda: (_ for _ in ()).throw(_StopGateway("stop")),
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])

    assert isinstance(result.exception, _StopGateway)
    assert "port 18791" in result.stdout


def test_gateway_cli_port_overrides_configured_port(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.gateway.port = 18791

    monkeypatch.setattr("xbot.platform.config.loader.set_config_path", lambda _path: None)
    monkeypatch.setattr("xbot.platform.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr(
        "xbot.platform.bus.queue.MessageBus",
        lambda: (_ for _ in ()).throw(_StopGateway("stop")),
    )

    result = runner.invoke(app, ["gateway", "--config", str(config_file), "--port", "18792"])

    assert isinstance(result.exception, _StopGateway)
    assert "port 18792" in result.stdout
