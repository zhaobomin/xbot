from __future__ import annotations

import asyncio
import importlib
import sys
import tempfile
import time
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from xbot.agent.backends.client_lifecycle import ClientLifecycleManager, ManagedClientRecord
from xbot.agent.context.builder import ContextBuilder
from xbot.agent.context.commands import CommandsLoader
from xbot.agent.crew.output.format import OutputParser
from xbot.agent.crew.planner.models import Capability, RolePoolConfig, RoleTier
from xbot.agent.crew.planner.role_pool import RolePoolManager
from xbot.agent.hooks import CompactHookHandler
from xbot.agent.memory.store import MemoryConsolidator
from xbot.agent.monitoring.alerting import AlertConfig, AlertService
from xbot.agent.state.machine import SessionPhase, SessionStateMachine
from xbot.agent.tools.filesystem import WriteFileTool
from xbot.agent.tools.registry import ToolRegistry
from xbot.agent.tools.shell import ExecTool
from xbot.agent.tools.base import Tool
from xbot.agent.tools.memory import MemoryTool
from xbot.agent.tools.skill_loader import LoadSkillContentTool
from xbot.channels.email import EmailChannel, EmailConfig
from xbot.channels.feishu import FeishuChannel, FeishuConfig
from xbot.channels.feishu_content import _extract_interactive_content
from xbot.channels.mochat import MochatChannel, MochatConfig
from xbot.channels.registry import discover_channel_names
from xbot.channels.slack import SlackChannel, SlackConfig
from xbot.agent.router import AgentRouter
from xbot.config.sdk_resolver import _has_api_key
from xbot.config.schema import Config, MCPServerConfig
from xbot.session.manager import SessionManager


def test_runtime_backend_property_raises_until_initialized() -> None:
    from xbot.agent.runtime import AgentRuntime

    runtime = AgentRuntime(config=Config(), shared_resources={})

    with pytest.raises(RuntimeError, match="Backend not initialized"):
        _ = runtime.backend


def test_runtime_tools_property_returns_none_until_initialized() -> None:
    from xbot.agent.runtime import AgentRuntime

    runtime = AgentRuntime(config=Config(), shared_resources={})

    assert runtime.tools is None


def test_runtime_backend_property_returns_backend_after_initialization() -> None:
    from xbot.agent.runtime import AgentRuntime

    runtime = AgentRuntime(config=Config(), shared_resources={})
    backend = MagicMock()
    runtime.router._backend = backend

    assert runtime.backend is backend


@pytest.mark.asyncio
async def test_runtime_interaction_response_falls_back_when_handler_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from xbot.agent.runtime import AgentRuntime

    runtime = AgentRuntime(config=Config(), shared_resources={})
    runtime._response_handlers = None
    delegated = AsyncMock(return_value=True)

    class _FakeHandlers:
        def __init__(self, owner):
            self.owner = owner

        async def handle_interaction_response(self, msg, retry_count: int = 0):
            return await delegated(msg, retry_count=retry_count)

    monkeypatch.setattr("xbot.agent.runtime.RuntimeResponseHandlers", _FakeHandlers)

    handled = await runtime._handle_interaction_response(
        SimpleNamespace(session_key="session:1", content="hello")
    )

    assert handled is True
    delegated.assert_awaited_once()
    assert delegated.await_args.kwargs["retry_count"] == 0


def test_config_uses_xbot_env_prefix() -> None:
    assert Config.model_config.get("env_prefix") == "XBOT_"


def _make_mock_backend() -> object:
    return MagicMock()


class _FakeSkillLoader:
    def load_skill(self, skill_name: str) -> str | None:
        return f"---\ntitle: {skill_name}\n---\n\nhello"

    def _strip_frontmatter(self, content: str) -> str:
        return content.split("---\n\n", 1)[-1]


def test_commands_loader_rejects_path_traversal(tmp_path: Path) -> None:
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    (commands_dir / "safe.md").write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")

    loader = CommandsLoader(tmp_path)

    assert loader.load_command("../../outside") is None
    assert loader.load_command("safe") == "safe"


def test_extract_interactive_content_reads_card_elements() -> None:
    content = {
        "title": {"content": "Root"},
        "elements": [
            {"tag": "markdown", "content": "hello"},
            {"tag": "div", "text": {"content": "world"}},
        ],
    }

    parts = _extract_interactive_content(content)

    assert "title: Root" in parts
    assert "hello" in parts
    assert "world" in parts


def test_session_manager_file_lock_works_without_fcntl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    monkeypatch.setattr("xbot.session.manager.fcntl", None)
    session = manager.get_or_create("test:lockless")
    session.add_message("user", "hi")
    manager.save(session)
    loaded = manager.get("test:lockless")
    assert loaded is not None
    assert loaded.messages[0]["content"] == "hi"


def test_sdk_resolver_empty_secret_str_is_not_configured() -> None:
    config = Config()
    config.providers.openai.api_key = SecretStr("")
    assert _has_api_key(config, "openai") is False


@pytest.mark.asyncio
async def test_load_skill_content_tool_accepts_extra_kwargs() -> None:
    tool = LoadSkillContentTool(skills_loader=_FakeSkillLoader())
    result = await tool.execute(skill_name="demo", unexpected="value")
    assert "# Skill: demo" in result


def test_memory_consolidator_lock_is_stable(tmp_path: Path) -> None:
    consolidator = MemoryConsolidator(
        workspace=tmp_path,
        backend=_make_mock_backend(),
        sessions=SessionManager(tmp_path),
        context_window_tokens=10000,
        build_messages=lambda **kwargs: [],
        get_tool_definitions=lambda: [],
    )
    assert consolidator.get_lock("session:1") is consolidator.get_lock("session:1")


def test_feishu_channel_checks_only_current_bot_mentions() -> None:
    channel = FeishuChannel(FeishuConfig(enabled=True, bot_open_id="ou_bot"), MagicMock())
    mention = SimpleNamespace(id=SimpleNamespace(user_id=None, open_id="ou_other"))
    message = SimpleNamespace(content="<at user_id='ou_other'></at>", mentions=[mention])
    assert channel._is_bot_mentioned(message) is False

    channel_message = SimpleNamespace(
        content="<at user_id='ou_bot'></at>",
        mentions=[SimpleNamespace(id=SimpleNamespace(user_id=None, open_id="ou_bot"))],
    )
    assert channel._is_bot_mentioned(channel_message) is True


@pytest.mark.asyncio
async def test_mochat_panel_watch_payload_uses_panel_identifiers() -> None:
    channel = MochatChannel(MochatConfig(), MagicMock())
    channel._process_inbound_event = AsyncMock()
    payload = {
        "panelId": "panel-1",
        "events": [{"type": "message.add", "payload": {"author": "u1", "messageId": "m1", "content": "hi", "authorInfo": {}}}],
    }
    await channel._handle_watch_payload(payload, "panel")
    channel._process_inbound_event.assert_awaited_once()
    assert channel._process_inbound_event.await_args.args[0] == "panel-1"


def test_role_pool_overrides_preserve_capability_types() -> None:
    manager = RolePoolManager(
        RolePoolConfig(
            enabled_tiers=[RoleTier.CORE],
            role_overrides={"architect": {"capabilities": ["review"]}},
        )
    )
    pool = manager.get_pool()
    role = pool.get_role("architect")
    assert role is not None
    assert role.capabilities == [Capability.REVIEW]


@pytest.mark.asyncio
async def test_compact_hook_handler_awaits_async_callback() -> None:
    calls: list[tuple[str, str]] = []

    async def callback(session_key: str, message: str) -> None:
        calls.append((session_key, message))

    handler = CompactHookHandler(enabled=True, message_callback=callback)
    await handler(SimpleNamespace(session_id="s1", trigger="auto"), None, SimpleNamespace(signal=None))
    assert calls == [("s1", "🔄 Compressing context (auto)...")]


def test_state_machine_get_state_returns_default_without_tracking() -> None:
    machine = SessionStateMachine()
    state = machine.get_state("missing")
    assert state.phase == SessionPhase.IDLE
    assert machine.has_session("missing") is False
    created = machine.get_or_create_state("missing")
    assert created is not state


def test_state_machine_queries_do_not_create_missing_sessions() -> None:
    machine = SessionStateMachine()

    assert machine.get_phase("missing") == SessionPhase.IDLE
    assert machine.is_idle("missing") is True
    assert machine.is_busy("missing") is False
    assert machine.has_session("missing") is False


def test_router_instances_snapshot_backend_registry() -> None:
    original = AgentRouter._backends.copy()
    try:
        AgentRouter._backends = {"claude_sdk": MagicMock()}
        first = AgentRouter(Config().agents, {})
        AgentRouter._backends["other"] = MagicMock()
        second = AgentRouter(Config().agents, {})
        assert "other" not in first._backends
        assert "other" in second._backends
    finally:
        AgentRouter._backends = original


def test_write_file_reports_true_byte_count(tmp_path: Path) -> None:
    tool = WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path)
    result = asyncio.run(tool.execute(path="demo.txt", content="中"))
    assert "3 bytes" in result


def test_output_parser_integer_validation_rejects_bool() -> None:
    parser = OutputParser()
    errors = parser._validate_schema(True, {"type": "integer"})
    assert errors


def test_exec_tool_blocks_long_rm_flags() -> None:
    tool = ExecTool()
    error = asyncio.run(tool.execute("rm --recursive --force demo"))
    assert "blocked" in error.lower()


class _LiteralTool(Tool):
    name = "literal"
    description = "Returns the provided message."
    parameters = {"type": "object", "properties": {"message": {"type": "string"}}}

    async def execute(self, message: str, **kwargs) -> str:
        return message


def test_tool_registry_does_not_treat_error_prefix_as_failure() -> None:
    registry = ToolRegistry()
    registry.register(_LiteralTool())

    result = asyncio.run(registry.execute("literal", {"message": "Error budget healthy"}))

    assert result == "Error budget healthy"


def test_memory_search_short_results_do_not_append_fake_ellipsis(tmp_path: Path) -> None:
    tool = MemoryTool(tmp_path)

    class _Store:
        async def search_memory(self, query: str, max_results: int):
            return [{"source": "memo", "score": 0.9, "memory": "short text"}]

    tool._get_memory_store = lambda: _Store()

    result = asyncio.run(tool._search(query="short", max_results=5))

    assert "short text..." not in result
    assert "short text" in result


def test_email_channel_processed_uids_fifo_eviction() -> None:
    channel = EmailChannel(EmailConfig(allow_from=["*"]), MagicMock())
    channel._MAX_PROCESSED_UIDS = 4
    for uid in ["1", "2", "3", "4", "5"]:
        channel._processed_uids[uid] = None
        if len(channel._processed_uids) > channel._MAX_PROCESSED_UIDS:
            trim_count = len(channel._processed_uids) // 2
            for _ in range(trim_count):
                channel._processed_uids.popitem(last=False)
    assert list(channel._processed_uids.keys()) == ["3", "4", "5"]


def test_slack_channel_open_dm_policy_is_not_blocked_by_empty_allowlist() -> None:
    channel = SlackChannel(SlackConfig(enabled=True, allow_from=[]), MagicMock())
    assert channel.is_allowed("U123") is True


def test_context_builder_skills_catalog_only_lists_skills_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    builder = ContextBuilder(tmp_path, use_reme=False)
    skills = [{"name": "demo", "description": "desc", "available": True}]
    calls = {"list_skills": 0}
    monkeypatch.setattr(builder.skills, "list_available_skills", lambda: skills)

    def _list_skills(filter_unavailable: bool = False):
        calls["list_skills"] += 1
        return [{"name": "demo", "path": "/tmp/demo/SKILL.md"}]

    monkeypatch.setattr(builder.skills, "list_skills", _list_skills)
    catalog = builder._build_skills_catalog()
    assert "demo" in catalog
    assert calls["list_skills"] == 1


def test_channel_discovery_excludes_feishu_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "pkgutil.iter_modules",
        lambda _path: iter([
            (None, "telegram", False),
            (None, "feishu_content", False),
            (None, "registry", False),
        ]),
    )

    assert discover_channel_names() == ["telegram"]


def test_planner_validate_path_allows_double_dot_in_filename() -> None:
    from xbot.agent.crew.planner.utils import PlannerValidator

    errors = PlannerValidator.validate_path("my..file")

    assert "Path cannot contain '..'" not in errors


def test_xbot_module_defers_git_commands_until_version_text(monkeypatch: pytest.MonkeyPatch) -> None:
    import xbot as xbot_module

    calls: list[list[str]] = []

    def _fake_check_output(cmd, **kwargs):
        calls.append(cmd)
        return b"mocked"

    monkeypatch.setattr("subprocess.check_output", _fake_check_output)

    reloaded = importlib.reload(xbot_module)
    assert calls == []

    reloaded.version_text()
    assert calls


@pytest.mark.asyncio
async def test_alert_service_rolls_back_rate_slot_on_publish_failure() -> None:
    bus = MagicMock()
    bus.publish_outbound = AsyncMock(side_effect=RuntimeError("boom"))
    service = AlertService(bus, AlertConfig(chat_id="c1", cooldown_seconds=300))
    assert await service.send_alert("title", "msg") is False
    assert await service._reserve_alert_slot("title") is True


@pytest.mark.asyncio
async def test_client_lifecycle_idle_count_uses_real_idle_window() -> None:
    manager = ClientLifecycleManager()
    await manager.register("s1", object())
    await manager.register("s2", object())
    manager._records["s1"].last_used_at = time.time() - 5
    manager._records["s2"].last_used_at = time.time()
    snapshot = await manager.snapshot()
    assert snapshot["counts"]["connected"] == 2
    assert snapshot["counts"]["idle"] == 1


@pytest.mark.asyncio
async def test_client_lifecycle_snapshot_sync_takes_locked_snapshot_inside_running_loop() -> None:
    manager = ClientLifecycleManager()
    await manager.register("s1", object())

    original = manager._snapshot_unlocked

    def _guarded_snapshot():
        assert manager._lock.locked()
        return original()

    manager._snapshot_unlocked = _guarded_snapshot  # type: ignore[method-assign]

    snapshot = manager.snapshot_sync()

    assert snapshot["counts"]["connected"] == 1


@pytest.mark.asyncio
async def test_connect_mcp_servers_registers_child_stack_with_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    from xbot.agent.tools import mcp as mcp_module

    closed: list[str] = []

    async def fake_connect(name: str, cfg: MCPServerConfig, registry: ToolRegistry):
        conn = mcp_module.MCPServerConnection(name=name, session=None)
        conn.connected = True
        await conn.stack.__aenter__()
        conn.stack.push_async_callback(lambda: _async_mark_closed(closed, name))
        return conn

    async def _async_mark_closed(target: list[str], value: str) -> None:
        target.append(value)

    monkeypatch.setattr(mcp_module, "_connect_single_mcp_server", fake_connect)

    stack = AsyncExitStack()
    await stack.__aenter__()
    registry = ToolRegistry()
    await mcp_module.connect_mcp_servers({"demo": MCPServerConfig(command="fake")}, registry, stack)
    await stack.aclose()

    assert closed == ["demo"]


def test_health_status_endpoint_uses_utc_timestamp() -> None:
    from xbot.agent.monitoring.health import HealthCheckService

    service = HealthCheckService(port=18080, host="127.0.0.1")
    service._start_time = 0
    response = asyncio.run(service._handle_status(SimpleNamespace()))
    assert response.text is not None
    assert "1970-01-01T00:00:00Z" in response.text
