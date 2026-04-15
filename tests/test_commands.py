import json
import re
import shutil
import asyncio
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
    assert "--command-pack" in stripped_output
    assert "--no-command-pack" in stripped_output


def test_init_installs_default_command_pack(mock_paths, monkeypatch):
    monkeypatch.setattr("xbot.channels.registry.discover_all", lambda: {})
    calls: dict[str, tuple[Path, str] | None] = {"commands": None}

    def _fake_commands(workspace: Path, pack_name: str = "default") -> list[str]:
        calls["commands"] = (workspace, pack_name)
        return ["commands/review.md"]

    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_command_pack", _fake_commands)

    _config_file, workspace_dir, _mock_ws = mock_paths
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert calls["commands"] == (workspace_dir, "default")
    assert "Installed command pack 'default'" in result.stdout


def test_init_can_skip_command_pack_installation(mock_paths, monkeypatch):
    monkeypatch.setattr("xbot.channels.registry.discover_all", lambda: {})
    command_called = False

    def _fake_commands(_workspace: Path, pack_name: str = "default") -> list[str]:
        nonlocal command_called
        command_called = True
        return []

    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_command_pack", _fake_commands)

    result = runner.invoke(app, ["init", "--no-command-pack"])

    assert result.exit_code == 0
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
    assert "--cwd" in stripped_output
    assert "--continue" in stripped_output
    assert "--resume" in stripped_output
    assert "--new" in stripped_output
    assert "--config" in stripped_output
    assert "-c" in stripped_output













def test_agent_fails_cleanly_when_current_working_directory_unavailable(monkeypatch) -> None:
    config = Config()

    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr(
        "xbot.interfaces.cli.commands.Path.cwd",
        classmethod(lambda cls: (_ for _ in ()).throw(FileNotFoundError("cwd removed"))),
    )

    result = runner.invoke(app, ["agent", "--message", "ping"])
    stripped_output = _strip_ansi(result.stdout)

    assert result.exit_code == 1
    assert "Error: current working directory unavailable" in stripped_output


def test_agent_fails_cleanly_when_cwd_does_not_exist(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path)

    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)

    missing_cwd = tmp_path / "missing-dir"
    result = runner.invoke(app, ["agent", "--message", "ping", "--cwd", str(missing_cwd)])
    stripped_output = _strip_ansi(result.stdout)

    assert result.exit_code == 1
    assert "Error: cwd does not exist:" in stripped_output
    assert str(missing_cwd) in stripped_output.replace("\n", "")


def test_agent_fails_cleanly_when_cwd_is_not_directory(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path)

    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)

    not_dir = tmp_path / "not-dir.txt"
    not_dir.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["agent", "--message", "ping", "--cwd", str(not_dir)])
    stripped_output = _strip_ansi(result.stdout)

    assert result.exit_code == 1
    assert "Error: cwd is not a directory:" in stripped_output
    assert str(not_dir) in stripped_output.replace("\n", "")


def test_agent_single_message_initializes_service(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path)

    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
    monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _path: object())

    class _FakeService:
        def __init__(self) -> None:
            self.initialized = False
            self.channels_config = None

        async def initialize(self) -> None:
            self.initialized = True

        async def process_direct(self, *args, **kwargs) -> str:
            assert self.initialized is True
            return "ok-from-agent"

        async def close_mcp(self) -> None:
            return None

    fake_service = _FakeService()
    monkeypatch.setattr(
        "xbot.interfaces.cli.commands._make_agent_service",
        lambda **kwargs: fake_service,
    )

    result = runner.invoke(app, ["agent", "-m", "hi"])
    assert result.exit_code == 0
    assert fake_service.initialized is True
    assert "ok-from-agent" in _strip_ansi(result.stdout)


def test_agent_single_message_sets_session_cwd(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")

    explicit_cwd = tmp_path / "session-cwd"
    explicit_cwd.mkdir(parents=True)

    class _Registry:
        def __init__(self) -> None:
            self.session_cwds: dict[str, str] = {}

        def set_session_cwd(self, session_key: str, cwd: str | None) -> None:
            if cwd is None:
                self.session_cwds.pop(session_key, None)
            else:
                self.session_cwds[session_key] = cwd

        def get_session_cwd(self, session_key: str) -> str | None:
            return self.session_cwds.get(session_key)

    registry = _Registry()

    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: registry)
    monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _path: object())

    class _FakeService:
        def __init__(self) -> None:
            self.channels_config = None

        async def initialize(self) -> None:
            return None

        async def process_direct(self, *args, **kwargs) -> str:
            assert registry.get_session_cwd("cli:direct") == str(explicit_cwd.resolve())
            return "ok-cwd"

        async def close_mcp(self) -> None:
            return None

    monkeypatch.setattr(
        "xbot.interfaces.cli.commands._make_agent_service",
        lambda **kwargs: _FakeService(),
    )

    result = runner.invoke(app, ["agent", "-m", "hi", "--session", "cli:direct", "--cwd", str(explicit_cwd)])
    assert result.exit_code == 0
    assert "ok-cwd" in _strip_ansi(result.stdout)


def test_agent_default_creates_new_session_each_run(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")

    seen_session_keys: list[str] = []

    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
    monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _path: object())

    class _FakeService:
        def __init__(self) -> None:
            self.channels_config = None

        async def initialize(self) -> None:
            return None

        async def process_direct(self, *args, **kwargs) -> str:
            seen_session_keys.append(kwargs["session_key"])
            return "ok"

        async def close_mcp(self) -> None:
            return None

    monkeypatch.setattr(
        "xbot.interfaces.cli.commands._make_agent_service",
        lambda **kwargs: _FakeService(),
    )

    result1 = runner.invoke(app, ["agent", "-m", "hi"])
    result2 = runner.invoke(app, ["agent", "-m", "hi"])
    assert result1.exit_code == 0
    assert result2.exit_code == 0
    assert len(seen_session_keys) == 2
    assert seen_session_keys[0].startswith("cli:")
    assert seen_session_keys[1].startswith("cli:")
    assert seen_session_keys[0] != seen_session_keys[1]


def test_agent_rejects_conflicting_resume_flags(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)

    result = runner.invoke(app, ["agent", "-m", "hi", "--new", "--continue"])
    assert result.exit_code == 1
    assert "--new cannot be used with --continue or --resume" in _strip_ansi(result.stdout)


def test_agent_continue_selects_latest_session_for_cwd(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    workspace = tmp_path / "workspace"
    config.agents.defaults.workspace = str(workspace)
    target_cwd = tmp_path / "project-a"
    target_cwd.mkdir(parents=True)

    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
    monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _path: object())

    from xbot.runtime.session.conversation_store import ConversationStore

    store = ConversationStore(workspace)
    s1 = store.get_or_create("cli:older")
    s1.metadata.update({
        "execution_cwd": str(target_cwd.resolve()),
        "last_used_at": "2026-04-12T00:00:00Z",
        "run_mode": "cli",
    })
    s1.mark_metadata_dirty()
    store.save(s1)
    s2 = store.get_or_create("cli:newer")
    s2.metadata.update({
        "execution_cwd": str(target_cwd.resolve()),
        "last_used_at": "2026-04-13T00:00:00Z",
        "run_mode": "cli",
    })
    s2.mark_metadata_dirty()
    store.save(s2)

    seen: dict[str, str] = {}

    class _FakeService:
        def __init__(self) -> None:
            self.channels_config = None

        async def initialize(self) -> None:
            return None

        async def process_direct(self, *args, **kwargs) -> str:
            seen["session_key"] = kwargs["session_key"]
            return "ok"

        async def close_mcp(self) -> None:
            return None

    monkeypatch.setattr("xbot.interfaces.cli.commands._make_agent_service", lambda **kwargs: _FakeService())

    result = runner.invoke(app, ["agent", "-m", "hi", "--continue", "--cwd", str(target_cwd)])
    assert result.exit_code == 0
    assert seen["session_key"] == "cli:newer"


def test_sessions_list_and_show(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    workspace = tmp_path / "workspace"
    config.agents.defaults.workspace = str(workspace)
    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)

    from xbot.runtime.session.conversation_store import ConversationStore

    store = ConversationStore(workspace)
    s1 = store.get_or_create("cli:test-show")
    s1.metadata.update({
        "sdk_session_id": "sdk-123",
        "execution_cwd": str((tmp_path / "project").resolve()),
        "last_used_at": "2026-04-13T10:00:00Z",
        "run_mode": "cli",
    })
    s1.mark_metadata_dirty()
    store.save(s1)

    result_list = runner.invoke(app, ["sessions", "list"])
    assert result_list.exit_code == 0
    assert "cli:test-show" in _strip_ansi(result_list.stdout)
    assert "sdk-123" in _strip_ansi(result_list.stdout)

    result_show = runner.invoke(app, ["sessions", "show", "cli:test-show"])
    assert result_show.exit_code == 0
    plain = _strip_ansi(result_show.stdout)
    assert "Session Key: cli:test-show" in plain
    assert "SDK Session: sdk-123" in plain


def test_sessions_list_plain_outputs_full_ids(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    workspace = tmp_path / "workspace"
    config.agents.defaults.workspace = str(workspace)
    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)

    from xbot.runtime.session.conversation_store import ConversationStore

    long_session = "cli:20260413-141645-131992f3"
    long_sdk = "94001eda-9d69-41e0-9b4e-123456789abc"
    store = ConversationStore(workspace)
    s1 = store.get_or_create(long_session)
    s1.metadata.update({
        "sdk_session_id": long_sdk,
        "execution_cwd": str((tmp_path / "project").resolve()),
        "last_used_at": "2026-04-13T10:00:00Z",
        "run_mode": "cli",
    })
    s1.mark_metadata_dirty()
    store.save(s1)

    result = runner.invoke(app, ["sessions", "list", "--plain"])
    assert result.exit_code == 0
    plain = _strip_ansi(result.stdout)
    assert "session_key" in plain
    assert "sdk_session_id" in plain
    assert long_session in plain
    assert long_sdk in plain


def test_agent_single_message_tool_hint_shows_session_execution_cwd(
    monkeypatch, tmp_path: Path
) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")

    explicit_cwd = tmp_path / "session-cwd"
    explicit_cwd.mkdir(parents=True)

    class _Registry:
        def __init__(self) -> None:
            self.execution_cwds: dict[str, str] = {}

        def set_execution_cwd(self, session_key: str, cwd: str | None) -> None:
            if cwd is None:
                self.execution_cwds.pop(session_key, None)
            else:
                self.execution_cwds[session_key] = cwd

        def get_execution_cwd(self, session_key: str) -> str | None:
            return self.execution_cwds.get(session_key)

    registry = _Registry()

    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: registry)
    monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _path: object())

    class _FakeService:
        def __init__(self) -> None:
            self.channels_config = None

        async def initialize(self) -> None:
            return None

        async def process_direct(self, *args, **kwargs) -> str:
            on_progress = kwargs["on_progress"]
            session_key = kwargs["session_key"]
            cwd = registry.get_execution_cwd(session_key)
            await on_progress(
                f'Tool: Bash(cwd="{cwd}", command="pwd")',
                tool_hint=True,
                event_type="tool_call",
                event_data={"tool_calls": [{"name": "Bash"}]},
            )
            return "ok"

        async def close_mcp(self) -> None:
            return None

    monkeypatch.setattr(
        "xbot.interfaces.cli.commands._make_agent_service",
        lambda **kwargs: _FakeService(),
    )

    result = runner.invoke(app, ["agent", "-m", "hi", "--cwd", str(explicit_cwd)])
    assert result.exit_code == 0
    output = _strip_ansi(result.stdout)
    compact = output.replace("\n", "")
    assert "Tool:" in compact
    assert "Bash(cwd=" in compact
    assert str(explicit_cwd.resolve()) in compact
    assert 'command="pwd"' in compact


def test_gateway_passes_workspace_as_execution_cwd(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("{}")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "configured-workspace")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "xbot.platform.config.loader.set_config_path",
        lambda path: captured.__setitem__("config_path", path),
    )
    monkeypatch.setattr("xbot.platform.config.loader.load_config", lambda _path=None: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("xbot.runtime.session.conversation_store.ConversationStore", lambda _w: object())
    monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
    monkeypatch.setattr("xbot.runtime.system.monitoring.health.HealthCheckService", lambda **_k: object())
    monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _p: object())
    monkeypatch.setattr("xbot.interaction.permission.PermissionRequestHandler", lambda **_k: object())

    def _capture_make_agent_service(**kwargs):
        captured["execution_cwd"] = kwargs.get("execution_cwd")
        raise _StopGateway("captured")

    monkeypatch.setattr("xbot.interfaces.cli.commands._make_agent_service", _capture_make_agent_service)

    result = runner.invoke(app, ["gateway", "--config", str(config_file)])
    assert isinstance(result.exception, _StopGateway)
    assert captured["execution_cwd"] == config.workspace_path


def test_agent_interactive_reports_agent_loop_failure(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path)

    class _FakeBus:
        def __init__(self) -> None:
            self.inbound = []

        async def publish_inbound(self, msg) -> None:
            self.inbound.append(msg)

        async def consume_outbound(self):
            await asyncio.sleep(3600)

    fake_bus = _FakeBus()

    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: fake_bus)
    monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
    monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _path: object())
    monkeypatch.setattr("xbot.interfaces.cli.commands._init_prompt_session", lambda: None)
    monkeypatch.setattr("xbot.interfaces.cli.commands._flush_pending_tty_input", lambda: None)
    monkeypatch.setattr("xbot.interfaces.cli.commands._restore_terminal", lambda: None)

    inputs = iter(["hi", "exit"])

    async def _fake_read_input() -> str:
        return next(inputs)

    monkeypatch.setattr("xbot.interfaces.cli.commands._read_interactive_input_async", _fake_read_input)

    class _FailingService:
        channels_config = None

        async def initialize(self) -> None:
            return None

        async def run(self) -> None:
            raise RuntimeError("boom")

        def stop(self) -> None:
            return None

        async def close_mcp(self) -> None:
            return None

    monkeypatch.setattr(
        "xbot.interfaces.cli.commands._make_agent_service",
        lambda **kwargs: _FailingService(),
    )

    result = runner.invoke(app, ["agent"])
    assert result.exit_code == 0
    output = _strip_ansi(result.stdout)
    assert "Error: agent-loop failed: boom" in output


def test_agent_interactive_reports_outbound_consumer_failure(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    seen_lines: list[str] = []

    class _BrokenBus:
        async def publish_inbound(self, msg) -> None:
            return None

        async def consume_outbound(self):
            raise RuntimeError("queue broken")

    fake_bus = _BrokenBus()

    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: fake_bus)
    monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
    monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _path: object())
    monkeypatch.setattr("xbot.interfaces.cli.commands._init_prompt_session", lambda: None)
    monkeypatch.setattr("xbot.interfaces.cli.commands._flush_pending_tty_input", lambda: None)
    monkeypatch.setattr("xbot.interfaces.cli.commands._restore_terminal", lambda: None)

    async def _capture_line(text: str) -> None:
        seen_lines.append(text)

    monkeypatch.setattr("xbot.interfaces.cli.commands._print_interactive_line", _capture_line)

    inputs = iter(["hi", "exit"])

    async def _fake_read_input() -> str:
        return next(inputs)

    monkeypatch.setattr("xbot.interfaces.cli.commands._read_interactive_input_async", _fake_read_input)

    class _AliveService:
        channels_config = None

        async def initialize(self) -> None:
            return None

        async def run(self) -> None:
            while True:
                await asyncio.sleep(3600)

        def stop(self) -> None:
            return None

        async def close_mcp(self) -> None:
            return None

    monkeypatch.setattr(
        "xbot.interfaces.cli.commands._make_agent_service",
        lambda **kwargs: _AliveService(),
    )

    result = runner.invoke(app, ["agent"])
    assert result.exit_code == 0
    assert any("Error: outbound-consumer failed: queue broken" in line for line in seen_lines)


def test_agent_interactive_busy_reject_unblocks_wait_and_prints_hint(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    seen_lines: list[str] = []

    class _BusyBus:
        def __init__(self) -> None:
            self._published = 0
            self._queue: asyncio.Queue = asyncio.Queue()

        async def publish_inbound(self, msg) -> None:
            self._published += 1
            if self._published == 1:
                await self._queue.put(type("Msg", (), {
                    "channel": msg.channel,
                    "chat_id": msg.chat_id,
                    "content": "⏳ 正在处理中，请稍候...",
                    "metadata": {
                        "_progress": True,
                        "_event_type": "busy_reject",
                        "busy_reject": True,
                        "busy_reason": "active_task",
                    },
                })())

        async def consume_outbound(self):
            return await self._queue.get()

    fake_bus = _BusyBus()

    monkeypatch.setattr("xbot.interfaces.cli.commands._load_runtime_config", lambda _c, _w: config)
    monkeypatch.setattr("xbot.interfaces.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("xbot.platform.bus.queue.MessageBus", lambda: fake_bus)
    monkeypatch.setattr("xbot.runtime.state.RuntimeSessionRegistry", lambda: object())
    monkeypatch.setattr("xbot.runtime.system.cron.service.CronService", lambda _path: object())
    monkeypatch.setattr("xbot.interfaces.cli.commands._init_prompt_session", lambda: None)
    monkeypatch.setattr("xbot.interfaces.cli.commands._flush_pending_tty_input", lambda: None)
    monkeypatch.setattr("xbot.interfaces.cli.commands._restore_terminal", lambda: None)

    async def _capture_line(text: str) -> None:
        seen_lines.append(text)

    monkeypatch.setattr("xbot.interfaces.cli.commands._print_interactive_line", _capture_line)

    inputs = iter(["first", "exit"])

    async def _fake_read_input() -> str:
        return next(inputs)

    monkeypatch.setattr("xbot.interfaces.cli.commands._read_interactive_input_async", _fake_read_input)

    class _AliveService:
        channels_config = None

        async def initialize(self) -> None:
            return None

        async def run(self) -> None:
            while True:
                await asyncio.sleep(3600)

        def stop(self) -> None:
            return None

        async def close_mcp(self) -> None:
            return None

    monkeypatch.setattr(
        "xbot.interfaces.cli.commands._make_agent_service",
        lambda **kwargs: _AliveService(),
    )

    result = runner.invoke(app, ["agent"])
    assert result.exit_code == 0
    assert any("本次输入未执行" in line for line in seen_lines)


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
