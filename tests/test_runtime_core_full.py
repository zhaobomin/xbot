"""Comprehensive tests for runtime core modules.

Covers:
- xbot.runtime.core.command_handlers (lines 130-420 focus)
- xbot.runtime.core.context.builder (lines 64-254 focus)
- xbot.runtime.core.context.model_manager (lines 113-220 focus)
- xbot.runtime.core.hooks (lines 75-278 focus)
- xbot.runtime.system.monitoring.health (lines 165-309 focus)
- xbot.runtime.system.monitoring.alerting (lines 81-242 focus)
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _make_inbound(
    content: str = "!help",
    channel: str = "telegram",
    chat_id: str = "chat-1",
    metadata: dict | None = None,
    session_key_override: str | None = None,
) -> MagicMock:
    """Create a mock InboundMessage with the essential attributes."""
    msg = MagicMock()
    msg.content = content
    msg.channel = channel
    msg.chat_id = chat_id
    msg.metadata = metadata or {}
    msg.session_key_override = session_key_override
    # session_key property
    msg.session_key = f"{channel}:{chat_id}"
    return msg


def _make_service(
    *,
    shared_resources: dict | None = None,
    config: Any = None,
    session_workers: dict | None = None,
) -> MagicMock:
    """Build a mock AgentService with the attributes used by command_handlers."""
    svc = MagicMock()
    svc._shared_resources = shared_resources or {}
    svc._config = config
    svc._session_workers = session_workers or {}
    svc._client_pool = MagicMock()
    svc._client_pool._clients = []
    svc.interrupt_session = AsyncMock(return_value={})
    svc.reset_session = AsyncMock()
    svc.get_session_commands = AsyncMock(return_value=[])
    svc.get_workspace_commands_summary = MagicMock(return_value="")
    svc.set_session_model = MagicMock()
    return svc


# ===========================================================================
# 1. command_handlers
# ===========================================================================


class TestLocalCommandHandlerDetection:
    """Tests for is_local_command static method and command dispatch logic."""

    def test_slash_commands_recognised(self) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        for cmd in ["/help", "/clear", "/reset", "/restart", "/state", "/skills"]:
            assert LocalCommandHandler.is_local_command(cmd) is True

    def test_bang_commands_recognised(self) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        for cmd in ["!help", "!restart", "!stop", "!reset", "!state", "!coord", "!ver", "!skills"]:
            assert LocalCommandHandler.is_local_command(cmd) is True

    def test_model_prefix_recognised(self) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        assert LocalCommandHandler.is_local_command("!model") is True
        assert LocalCommandHandler.is_local_command("!model claude-sonnet-4-5") is True

    def test_non_command_not_recognised(self) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        assert LocalCommandHandler.is_local_command("hello") is False
        assert LocalCommandHandler.is_local_command("/unknown") is False

    def test_case_insensitive(self) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        assert LocalCommandHandler.is_local_command("!HELP") is True
        assert LocalCommandHandler.is_local_command("/Help") is True


class TestLocalCommandHandlerDispatch:
    """Test the handle() dispatch for each command."""

    @pytest.fixture
    def bus(self) -> MagicMock:
        b = MagicMock()
        b.publish_outbound = AsyncMock()
        b.get_pending_request_for_session = MagicMock(return_value=None)
        return b

    # -- !help / /help -------------------------------------------------------

    @pytest.mark.asyncio
    async def test_help_command(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!help")

        await handler.handle(msg, bus)

        bus.publish_outbound.assert_called_once()
        outbound = bus.publish_outbound.call_args[0][0]
        assert "!help" in outbound.content
        assert "Runtime Commands" in outbound.content

    @pytest.mark.asyncio
    async def test_slash_help_aliases_bang_help(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("/help")

        await handler.handle(msg, bus)

        bus.publish_outbound.assert_called_once()
        content = bus.publish_outbound.call_args[0][0].content
        assert "Runtime Commands" in content

    @pytest.mark.asyncio
    async def test_help_includes_sdk_commands_when_available(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        svc.get_session_commands = AsyncMock(return_value=["/compact", "/status"])
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!help")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "/compact" in content

    @pytest.mark.asyncio
    async def test_help_handles_timeout_gracefully(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        # First call times out, second (fallback) succeeds
        svc.get_session_commands = AsyncMock(
            side_effect=[TimeoutError("timed out"), ["/cached_cmd"]],
        )
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!help")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "/cached_cmd" in content

    @pytest.mark.asyncio
    async def test_help_handles_exception_gracefully(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        svc.get_session_commands = AsyncMock(side_effect=RuntimeError("boom"))
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!help")

        await handler.handle(msg, bus)

        # Should still return help text without SDK commands
        bus.publish_outbound.assert_called_once()

    @pytest.mark.asyncio
    async def test_help_includes_workspace_commands(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        svc.get_workspace_commands_summary = MagicMock(return_value="/deploy — Deploy app")
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!help")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "Workspace Commands" in content
        assert "/deploy" in content

    # -- !ver ----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_ver_command(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!ver")

        with patch("xbot.version_text", return_value="xbot v9.9.9"):
            await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "v9.9.9" in content

    # -- !stop ---------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_stop_with_interrupt_sent(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        svc.interrupt_session = AsyncMock(return_value={
            "interrupt_sent": True,
            "confirmed": True,
            "queued_cleared": 2,
        })
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!stop")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "Stopped" in content
        assert "current SDK turn" in content
        assert "2 queued message(s)" in content

    @pytest.mark.asyncio
    async def test_stop_no_active_task(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        svc.interrupt_session = AsyncMock(return_value={})
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!stop")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "No active task to stop" in content

    @pytest.mark.asyncio
    async def test_stop_with_fallback_disconnect(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        svc.interrupt_session = AsyncMock(return_value={
            "interrupt_sent": True,
            "fallback_disconnect": True,
        })
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!stop")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "recycling the SDK session" in content

    @pytest.mark.asyncio
    async def test_stop_with_pending_permission_and_interaction(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        svc.interrupt_session = AsyncMock(return_value={"interrupt_sent": True, "confirmed": True})
        # Simulate pending permission and interaction
        bus.get_pending_request_for_session = MagicMock(return_value={"id": "perm-1"})
        bus.get_pending_interaction_for_session = MagicMock(return_value={"id": "interact-1"})
        bus.aclear_session_requests = MagicMock(return_value={
            "permission": True,
            "interaction": True,
        })
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!stop")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "pending permission" in content
        assert "pending interaction" in content

    @pytest.mark.asyncio
    async def test_stop_dispatches_turn_completed_when_no_interrupt(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler
        from xbot.runtime.state import SessionEvent

        sm = MagicMock()
        svc = _make_service(shared_resources={"runtime_registry": sm})
        svc.interrupt_session = AsyncMock(return_value={})
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!stop")

        await handler.handle(msg, bus)

        sm.dispatch.assert_called_once()
        call_args = sm.dispatch.call_args
        assert call_args[1].get("reason") == "user_stop" or call_args[0][1] == SessionEvent.TURN_COMPLETED

    # -- !reset / /clear -----------------------------------------------------

    @pytest.mark.asyncio
    async def test_reset_hard(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!reset")

        await handler.handle(msg, bus)

        svc.reset_session.assert_called_once()
        call_kwargs = svc.reset_session.call_args
        assert call_kwargs[1].get("drop_sdk_context") is True or call_kwargs[1].get("drop_sdk_context", True)
        content = bus.publish_outbound.call_args[0][0].content
        assert "reset completed" in content
        assert "SDK context deleted" in content

    @pytest.mark.asyncio
    async def test_reset_soft(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!reset --soft")

        await handler.handle(msg, bus)

        svc.reset_session.assert_called_once()
        call_kwargs = svc.reset_session.call_args
        assert call_kwargs[1].get("drop_sdk_context") is False
        content = bus.publish_outbound.call_args[0][0].content
        assert "SDK context preserved" in content

    @pytest.mark.asyncio
    async def test_slash_clear_aliases_reset(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("/clear")

        await handler.handle(msg, bus)

        svc.reset_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_reset_with_active_worker(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service(session_workers={"telegram:chat-1": MagicMock(closed=False)})
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!reset")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "session worker" in content

    # -- !restart ------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_restart_command(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!restart")

        await handler.handle(msg, bus)

        svc.reset_session.assert_called_once()

    # -- !state --------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_state_command(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        sm = MagicMock()
        sm.get_phase = MagicMock(return_value="idle")
        sm.get = MagicMock(return_value=MagicMock(sdk_session_id="sdk-123"))
        sm.get_sdk_capabilities = MagicMock(return_value={
            "skill_source": "sdk_only",
            "skills": ["/compact"],
            "tools": ["exec", "read"],
        })
        svc = _make_service(shared_resources={"runtime_registry": sm, "bus": bus})
        bus.get_pending_request_for_session = MagicMock(return_value=None)
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!state")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "Phase: idle" in content
        assert "sdk-123" in content
        assert "Skill source: sdk_only" in content
        assert "SDK skills: 1" in content

    @pytest.mark.asyncio
    async def test_state_no_state_manager(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service(shared_resources={"bus": bus})
        bus.get_pending_request_for_session = MagicMock(return_value=None)
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!state")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "Phase: N/A" in content

    # -- !skills -------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_skills_command_with_cached_skills(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        sm = MagicMock()
        sm.get_sdk_capabilities = MagicMock(return_value={
            "skill_source": "workspace",
            "skills": ["/deploy", "/test"],
        })
        svc = _make_service(shared_resources={"runtime_registry": sm})
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!skills")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "Skill source: workspace" in content
        assert "/deploy" in content
        assert "/test" in content

    @pytest.mark.asyncio
    async def test_skills_command_no_state_manager(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service(shared_resources={})
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!skills")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "unavailable" in content

    @pytest.mark.asyncio
    async def test_skills_command_refreshes_when_empty(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        sm = MagicMock()
        # First call returns empty, second returns populated
        sm.get_sdk_capabilities = MagicMock(side_effect=[
            {"skill_source": "sdk_only", "skills": []},
            {"skill_source": "sdk_only", "skills": ["/refreshed"]},
        ])
        svc = _make_service(shared_resources={"runtime_registry": sm})
        svc.get_session_commands = AsyncMock(return_value=["/refreshed"])
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!skills")

        await handler.handle(msg, bus)

        svc.get_session_commands.assert_called_once()
        content = bus.publish_outbound.call_args[0][0].content
        assert "/refreshed" in content

    # -- !coord --------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_coord_command(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        sm = MagicMock()
        sm.snapshot = MagicMock(return_value={
            "sessions": 3,
            "by_phase": {"idle": 2, "busy": 1},
            "illegal_transition_total": 0,
        })
        svc = _make_service(
            shared_resources={"runtime_registry": sm},
            session_workers={"s1": MagicMock(closed=False), "s2": MagicMock(closed=True)},
        )
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!coord")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "Sessions: 3" in content
        assert "idle: 2" in content
        assert "busy: 1" in content
        assert "Active session workers: 1" in content

    @pytest.mark.asyncio
    async def test_coord_no_state_manager(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!coord")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "not available" in content

    # -- !model --------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_model_show_current(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        mm = MagicMock()
        mm.get_status_text = MagicMock(return_value="**Provider**: `anthropic`")
        svc._model_manager = mm
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!model")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "Provider" in content

    @pytest.mark.asyncio
    async def test_model_switch(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        mm = MagicMock()
        mm.switch_model = MagicMock(return_value=(True, "✅ 已切换到模型 `claude-3-haiku`"))
        svc._model_manager = mm
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!model claude-3-haiku")

        await handler.handle(msg, bus)

        mm.switch_model.assert_called_once_with("claude-3-haiku")
        svc.set_session_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_model_without_model_manager(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        config = MagicMock()
        config.model = "claude-sonnet-4-5"
        svc = _make_service(config=config)
        # No _model_manager attribute → will try to init but fail
        del svc._model_manager
        svc._shared_resources = {"config": None}  # no config in shared resources either
        handler = LocalCommandHandler(svc)
        msg = _make_inbound("!model")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "Current model" in content or "unknown" in content

    # -- unknown command -----------------------------------------------------

    @pytest.mark.asyncio
    async def test_unknown_command(self, bus: MagicMock) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        handler = LocalCommandHandler(svc)
        # Force an unknown command through
        msg = _make_inbound("!nonexistent")

        await handler.handle(msg, bus)

        content = bus.publish_outbound.call_args[0][0].content
        assert "Unknown command" in content


class TestClearPendingRequests:
    """Tests for _clear_pending_requests edge cases."""

    @pytest.mark.asyncio
    async def test_bus_none(self) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        result = await LocalCommandHandler._clear_pending_requests(None, "key")
        assert result == (False, False)

    @pytest.mark.asyncio
    async def test_no_pending(self) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        bus = MagicMock()
        bus.get_pending_request_for_session = MagicMock(return_value=None)
        result = await LocalCommandHandler._clear_pending_requests(bus, "key")
        assert result == (False, False)

    @pytest.mark.asyncio
    async def test_no_clear_method(self) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        bus = MagicMock(spec=[])  # no methods at all
        bus.get_pending_request_for_session = MagicMock(return_value={"id": "p1"})
        result = await LocalCommandHandler._clear_pending_requests(bus, "key")
        assert result == (False, False)

    @pytest.mark.asyncio
    async def test_invalid_return_type(self) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        bus = MagicMock()
        bus.get_pending_request_for_session = MagicMock(return_value={"id": "p1"})
        bus.aclear_session_requests = MagicMock(return_value="not a dict")
        result = await LocalCommandHandler._clear_pending_requests(bus, "key")
        assert result == (False, False)

    @pytest.mark.asyncio
    async def test_async_clear(self) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        bus = MagicMock()
        bus.get_pending_request_for_session = MagicMock(return_value={"id": "p1"})
        bus.get_pending_interaction_for_session = MagicMock(return_value=None)

        async def _async_clear(key: str) -> dict:
            return {"permission": True}

        bus.aclear_session_requests = MagicMock(side_effect=_async_clear)
        result = await LocalCommandHandler._clear_pending_requests(bus, "key")
        assert result == (True, False)


class TestCancelTaskIfRunning:
    """Tests for _cancel_task_if_running helper."""

    @pytest.mark.asyncio
    async def test_none_task(self) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        handler = LocalCommandHandler(svc)
        result = await handler._cancel_task_if_running(None, session_key="k", action="test")
        assert result is False

    @pytest.mark.asyncio
    async def test_already_done_task(self) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        handler = LocalCommandHandler(svc)
        task = MagicMock()
        task.done.return_value = True
        result = await handler._cancel_task_if_running(task, session_key="k", action="test")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_running_task(self) -> None:
        from xbot.runtime.core.command_handlers import LocalCommandHandler

        svc = _make_service()
        handler = LocalCommandHandler(svc)

        async def _long_task() -> None:
            await asyncio.sleep(10)

        task = asyncio.create_task(_long_task())
        await asyncio.sleep(0)  # let task start
        result = await handler._cancel_task_if_running(task, session_key="k", action="test")
        assert result is True
        assert task.cancelled()


# ===========================================================================
# 2. context.builder
# ===========================================================================


class TestContextBuilderBootstrapDisabled:
    """Lines 170-182: bootstrap loading can be disabled."""

    def test_load_bootstrap_disabled(self, tmp_path: Path) -> None:
        from xbot.runtime.core.context.builder import ContextBuilder

        (tmp_path / "AGENTS.md").write_text("content")
        builder = ContextBuilder(tmp_path, use_reme=False, load_bootstrap_files=False)
        result = builder._load_bootstrap_files()
        assert result == ""

    def test_system_prompt_without_bootstrap(self, tmp_path: Path) -> None:
        from xbot.runtime.core.context.builder import ContextBuilder

        (tmp_path / "AGENTS.md").write_text("should not appear")
        builder = ContextBuilder(tmp_path, use_reme=False, load_bootstrap_files=False)
        prompt = builder.build_system_prompt()
        assert "should not appear" not in prompt
        assert "xbot" in prompt  # identity still present


class TestContextBuilderIdentity:
    """Lines 109-155: identity section platform policies."""

    def test_posix_platform_policy(self, tmp_path: Path) -> None:
        from xbot.runtime.core.context.builder import ContextBuilder

        builder = ContextBuilder(tmp_path, use_reme=False)
        with patch("platform.system", return_value="Linux"):
            identity = builder._get_identity()
        assert "POSIX" in identity

    def test_windows_platform_policy(self, tmp_path: Path) -> None:
        from xbot.runtime.core.context.builder import ContextBuilder

        builder = ContextBuilder(tmp_path, use_reme=False)
        with patch("platform.system", return_value="Windows"):
            identity = builder._get_identity()
        assert "Windows" in identity
        assert "GNU tools" in identity


class TestContextBuilderReMeInit:
    """Lines 56-81: ReMe initialization paths."""

    def test_reme_import_failure(self, tmp_path: Path) -> None:
        from xbot.runtime.core.context.builder import ContextBuilder

        with patch("xbot.memory.reme._REME_AVAILABLE", False):
            builder = ContextBuilder(tmp_path, use_reme=True)
        assert builder.using_reme is False

    def test_reme_disabled_uses_fallback(self, tmp_path: Path) -> None:
        """Test that use_reme=False always uses MemoryStore."""
        from xbot.runtime.core.context.builder import ContextBuilder

        builder = ContextBuilder(tmp_path, use_reme=False)
        assert builder.using_reme is False
        # Should have MemoryStore, not ReMeMemoryStore
        from xbot.memory.store import MemoryStore
        assert isinstance(builder.memory, MemoryStore)


class TestContextBuilderRuntimeContext:
    """Lines 158-163: _build_runtime_context."""

    def test_with_channel_and_chat_id(self) -> None:
        from xbot.runtime.core.context.builder import ContextBuilder

        ctx = ContextBuilder._build_runtime_context("feishu", "oc-123")
        assert "Channel: feishu" in ctx
        assert "Chat ID: oc-123" in ctx
        assert "Current Time:" in ctx

    def test_without_channel(self) -> None:
        from xbot.runtime.core.context.builder import ContextBuilder

        ctx = ContextBuilder._build_runtime_context(None, None)
        assert "Channel:" not in ctx
        assert "Chat ID:" not in ctx

    def test_runtime_context_tag(self) -> None:
        from xbot.runtime.core.context.builder import ContextBuilder

        ctx = ContextBuilder._build_runtime_context("cli", "direct")
        assert ContextBuilder._RUNTIME_CONTEXT_TAG in ctx


class TestContextBuilderMessages:
    """Lines 184-254: build_messages and _build_user_content."""

    @pytest.fixture
    def builder(self, tmp_path: Path) -> Any:
        from xbot.runtime.core.context.builder import ContextBuilder
        return ContextBuilder(tmp_path, use_reme=False)

    def test_build_messages_string_content_merge(self, builder: Any) -> None:
        messages = builder.build_messages(
            history=[],
            current_message="Hello",
            channel="telegram",
            chat_id="123",
        )
        user_msg = messages[-1]
        assert user_msg["role"] == "user"
        # Should be a merged string with runtime context
        assert isinstance(user_msg["content"], str)
        assert "Hello" in user_msg["content"]
        assert "Current Time:" in user_msg["content"]

    def test_build_messages_with_media_images(self, builder: Any, tmp_path: Path) -> None:
        # Create a small PNG file
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xfc\xff\xff?\x00\x05\xfe\x02"
            b"\xfe\xa3\x35\x80\x84\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        img_path = tmp_path / "test.png"
        img_path.write_bytes(png_data)

        with patch("xbot.runtime.core.context.builder.classify_file") as mock_classify, \
             patch("xbot.runtime.core.context.builder.detect_image_mime", return_value="image/png"):
            from xbot.platform.utils.file_reader import FileType
            mock_classify.return_value = FileType.IMAGE

            messages = builder.build_messages(
                history=[],
                current_message="Look at this",
                media=[str(img_path)],
            )

        user_msg = messages[-1]
        # With images, content becomes a list: [runtime_ctx_text, image, ..., final_text]
        assert isinstance(user_msg["content"], list)
        # First element is the runtime context text
        assert user_msg["content"][0]["type"] == "text"
        # Find the image element (after runtime context prefix)
        image_items = [item for item in user_msg["content"] if item.get("type") == "image_url"]
        assert len(image_items) >= 1
        assert "data:image/png;base64," in image_items[0]["image_url"]["url"]

    def test_build_messages_with_file_refs(self, builder: Any, tmp_path: Path) -> None:
        txt_path = tmp_path / "notes.txt"
        txt_path.write_text("some notes")

        with patch("xbot.runtime.core.context.builder.classify_file") as mock_classify, \
             patch("xbot.runtime.core.context.builder.format_file_reference", return_value="[File: notes.txt]"):
            from xbot.platform.utils.file_reader import FileType
            mock_classify.return_value = FileType.FILE

            messages = builder.build_messages(
                history=[],
                current_message="Check this file",
                media=[str(txt_path)],
            )

        user_msg = messages[-1]
        assert "notes.txt" in user_msg["content"]
        assert "Check this file" in user_msg["content"]

    def test_build_user_content_nonexistent_image_skipped(self, builder: Any, tmp_path: Path) -> None:
        with patch("xbot.runtime.core.context.builder.classify_file") as mock_classify, \
             patch("xbot.runtime.core.context.builder.detect_image_mime", return_value="image/png"):
            from xbot.platform.utils.file_reader import FileType
            mock_classify.return_value = FileType.IMAGE

            result = builder._build_user_content("text", [str(tmp_path / "nonexistent.png")])
        # No images → just text
        assert result == "text"

    def test_build_user_content_invalid_mime_skipped(self, builder: Any, tmp_path: Path) -> None:
        img_path = tmp_path / "bad.png"
        img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

        with patch("xbot.runtime.core.context.builder.classify_file") as mock_classify, \
             patch("xbot.runtime.core.context.builder.detect_image_mime", return_value=None), \
             patch("mimetypes.guess_type", return_value=("application/octet-stream", None)):
            from xbot.platform.utils.file_reader import FileType
            mock_classify.return_value = FileType.IMAGE

            result = builder._build_user_content("text", [str(img_path)])
        assert result == "text"

    def test_build_messages_custom_role(self, builder: Any) -> None:
        messages = builder.build_messages(
            history=[],
            current_message="I am assistant",
            current_role="assistant",
        )
        assert messages[-1]["role"] == "assistant"

    def test_add_tool_result_and_assistant(self, builder: Any) -> None:
        messages: list = []
        builder.add_tool_result(messages, "tc1", "exec", "output1")
        assert len(messages) == 1
        assert messages[0]["role"] == "tool"
        assert messages[0]["name"] == "exec"

        builder.add_assistant_message(messages, "response", reasoning_content="thinking")
        assert len(messages) == 2
        assert messages[1]["role"] == "assistant"

    def test_add_assistant_with_thinking_blocks(self, builder: Any) -> None:
        messages: list = []
        thinking = [{"type": "thinking", "thinking": "Let me think..."}]
        builder.add_assistant_message(messages, content="answer", thinking_blocks=thinking)
        assert messages[0]["role"] == "assistant"


# ===========================================================================
# 3. model_manager
# ===========================================================================


class TestModelManager:
    """Tests for ModelManager — model resolution and switching."""

    def _make_config(
        self,
        *,
        provider: str = "anthropic",
        model: str = "",
        provider_models: list[str] | None = None,
        api_base: str = "",
        get_provider_name_return: str | None = None,
    ) -> MagicMock:
        config = MagicMock()
        config.agents.defaults.provider = provider
        config.agents.defaults.model = model
        config.get_provider_name = MagicMock(return_value=get_provider_name_return)

        # Provider config
        provider_config = MagicMock()
        provider_config.models = provider_models
        provider_config.api_base = api_base or None  # None so falsy check works

        # Set up providers attribute
        if provider_models is not None or api_base:
            setattr(config.providers, "anthropic", provider_config)
            config.providers.custom_providers = {}
        else:
            # Remove the attribute to test fallback paths
            try:
                delattr(config.providers, "anthropic")
            except AttributeError:
                pass
            config.providers.custom_providers = {}

        return config

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_init_with_explicit_model(self, mock_snake: Any, mock_spec: Any) -> None:
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "anthropic"
        mock_spec.return_value = None

        config = self._make_config(model="claude-3-haiku")
        mm = ModelManager(config)

        assert mm.current_model == "claude-3-haiku"

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_init_with_provider_models(self, mock_snake: Any, mock_spec: Any) -> None:
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "anthropic"
        mock_spec.return_value = None

        config = self._make_config(provider_models=["model-a", "model-b"])
        mm = ModelManager(config)

        assert mm.available_models == ["model-a", "model-b"]
        assert mm.current_model == "model-a"  # first model

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_init_fallback_to_defaults(self, mock_snake: Any, mock_spec: Any) -> None:
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "anthropic"
        mock_spec.return_value = None

        config = self._make_config()
        mm = ModelManager(config)

        # Should use _DEFAULT_MODELS for anthropic
        assert "claude-sonnet-4-5" in mm.available_models

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_init_unknown_provider_fallback(self, mock_snake: Any, mock_spec: Any) -> None:
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "unknown_provider"
        mock_spec.return_value = None

        config = self._make_config(provider="unknown_provider")
        mm = ModelManager(config)

        # Falls through to ultimate fallback
        assert mm.current_model == "claude-sonnet-4-5"
        assert mm.available_models == ["claude-sonnet-4-5"]

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_auto_provider_resolution(self, mock_snake: Any, mock_spec: Any) -> None:
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "anthropic"
        mock_spec.return_value = None

        config = self._make_config(provider="auto", get_provider_name_return="anthropic")
        mm = ModelManager(config)

        # "auto" → get_provider_name → "anthropic"
        assert mm._provider_name == "anthropic"

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_auto_provider_fallback_to_anthropic(self, mock_snake: Any, mock_spec: Any) -> None:
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "anthropic"
        mock_spec.return_value = None

        config = self._make_config(provider="auto", get_provider_name_return=None)
        mm = ModelManager(config)

        assert mm._provider_name == "anthropic"

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_base_url_from_provider_config(self, mock_snake: Any, mock_spec: Any) -> None:
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "anthropic"

        config = self._make_config(api_base="https://custom.api.com/v1")
        mm = ModelManager(config)

        assert mm.base_url == "https://custom.api.com/v1"

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_base_url_from_spec_default(self, mock_snake: Any, mock_spec: Any) -> None:
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "anthropic"
        spec_mock = MagicMock()
        spec_mock.default_base_url = "https://api.anthropic.com"
        mock_spec.return_value = spec_mock

        config = self._make_config()
        mm = ModelManager(config)

        assert mm.base_url == "https://api.anthropic.com"

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_base_url_unknown_fallback(self, mock_snake: Any, mock_spec: Any) -> None:
        from types import SimpleNamespace
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "unknown"
        mock_spec.return_value = None

        # Use SimpleNamespace so getattr returns None for missing attributes
        config = MagicMock()
        config.agents.defaults.provider = "unknown"
        config.agents.defaults.model = ""
        config.get_provider_name = MagicMock(return_value=None)
        config.providers = SimpleNamespace(custom_providers={})
        mm = ModelManager(config)

        assert mm.base_url == "unknown"

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_switch_model_success(self, mock_snake: Any, mock_spec: Any) -> None:
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "anthropic"
        mock_spec.return_value = None

        config = self._make_config(provider_models=["model-a", "model-b"])
        mm = ModelManager(config)

        success, msg = mm.switch_model("model-b")
        assert success is True
        assert mm.current_model == "model-b"
        assert "model-b" in msg

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_switch_model_not_available(self, mock_snake: Any, mock_spec: Any) -> None:
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "anthropic"
        mock_spec.return_value = None

        config = self._make_config(provider_models=["model-a"])
        mm = ModelManager(config)

        success, msg = mm.switch_model("nonexistent-model")
        assert success is False
        assert "不在可用列表" in msg

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_get_status_text(self, mock_snake: Any, mock_spec: Any) -> None:
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "anthropic"
        mock_spec.return_value = None

        config = self._make_config(provider_models=["model-a", "model-b"])
        mm = ModelManager(config)

        text = mm.get_status_text()
        assert "anthropic" in text
        assert "model-a" in text
        assert "model-b" in text
        # Current model should have checkmark
        assert "✅" in text

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_custom_providers_fallback(self, mock_snake: Any, mock_spec: Any) -> None:
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "my_provider"
        mock_spec.return_value = None

        config = MagicMock()
        config.agents.defaults.provider = "my_provider"
        config.agents.defaults.model = ""
        config.get_provider_name = MagicMock(return_value=None)

        # No standard provider attr
        try:
            delattr(config.providers, "my_provider")
        except AttributeError:
            pass

        # Custom providers dict
        custom_config = MagicMock()
        custom_config.models = ["custom-model-1", "custom-model-2"]
        custom_config.api_base = "https://custom.example.com"
        config.providers.custom_providers = {"my_provider": custom_config}

        mm = ModelManager(config)

        assert mm.available_models == ["custom-model-1", "custom-model-2"]
        assert mm.base_url == "https://custom.example.com"

    @patch("xbot.platform.config.provider_registry.get_provider_spec")
    @patch("xbot.platform.config.loader._provider_name_to_snake")
    def test_properties_return_copies(self, mock_snake: Any, mock_spec: Any) -> None:
        from xbot.runtime.core.context.model_manager import ModelManager

        mock_snake.return_value = "anthropic"
        mock_spec.return_value = None

        config = self._make_config(provider_models=["a", "b"])
        mm = ModelManager(config)

        models = mm.available_models
        models.append("hacked")
        assert "hacked" not in mm.available_models  # original not mutated


# ===========================================================================
# 4. hooks
# ===========================================================================


class TestCompactHookHandlerEdgeCases:
    """Additional hook tests — lines 75-170 focus."""

    def test_get_input_field_dict(self) -> None:
        from xbot.runtime.core.hooks import CompactHookHandler

        result = CompactHookHandler._get_input_field({"session_id": "s1"}, "session_id", "default")
        assert result == "s1"

    def test_get_input_field_object(self) -> None:
        from xbot.runtime.core.hooks import CompactHookHandler

        obj = MagicMock()
        obj.session_id = "s2"
        result = CompactHookHandler._get_input_field(obj, "session_id", "default")
        assert result == "s2"

    def test_get_input_field_missing(self) -> None:
        from xbot.runtime.core.hooks import CompactHookHandler

        result = CompactHookHandler._get_input_field({}, "missing", "default")
        assert result == "default"

    def test_get_input_field_callable_attr_skipped(self) -> None:
        from xbot.runtime.core.hooks import CompactHookHandler

        obj = MagicMock()
        obj.some_attr = MagicMock()  # callable → skipped
        obj.some_attr.return_value = "value"
        # When attribute is callable, falls to .get() or default
        result = CompactHookHandler._get_input_field(obj, "some_attr", "default")
        assert result == "default"

    @pytest.mark.asyncio
    async def test_message_callback_called(self) -> None:
        from xbot.runtime.core.hooks import CompactHookHandler

        callback = AsyncMock()
        handler = CompactHookHandler(enabled=True, message_callback=callback)

        mock_input = {"session_id": "s1", "trigger": "auto"}
        mock_context = MagicMock()

        await handler(mock_input, None, mock_context)

        callback.assert_called_once()
        assert "Compressing context" in callback.call_args[0][1]

    @pytest.mark.asyncio
    async def test_sync_message_callback(self) -> None:
        from xbot.runtime.core.hooks import CompactHookHandler

        callback = MagicMock()
        handler = CompactHookHandler(enabled=True, message_callback=callback)

        mock_input = {"session_id": "s1", "trigger": "auto"}
        mock_context = MagicMock()

        await handler(mock_input, None, mock_context)

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_callback_exception_handled(self) -> None:
        from xbot.runtime.core.hooks import CompactHookHandler

        callback = AsyncMock(side_effect=RuntimeError("callback failed"))
        handler = CompactHookHandler(enabled=True, message_callback=callback)

        mock_input = {"session_id": "s1", "trigger": "auto"}
        mock_context = MagicMock()

        # Should not raise
        result = await handler(mock_input, None, mock_context)
        assert "systemMessage" in result

    @pytest.mark.asyncio
    async def test_trigger_in_message(self) -> None:
        from xbot.runtime.core.hooks import CompactHookHandler

        handler = CompactHookHandler(enabled=True)

        mock_input = {"session_id": "s1", "trigger": "token_limit"}
        mock_context = MagicMock()

        result = await handler(mock_input, None, mock_context)
        assert "token_limit" in result["systemMessage"]

    @pytest.mark.asyncio
    async def test_events_capped_at_50(self) -> None:
        from xbot.runtime.core.hooks import CompactHookHandler

        handler = CompactHookHandler(enabled=True)

        for i in range(60):
            mock_input = {"session_id": f"s{i}", "trigger": "auto"}
            mock_context = MagicMock()
            await handler(mock_input, None, mock_context)

        assert len(handler._recent_events) == 50


class TestSubagentModelCompatHookEdgeCases:
    """Additional tests for SubagentModelCompatHookHandler — lines 214-299."""

    def test_disabled_handler(self) -> None:
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        handler = SubagentModelCompatHookHandler(enabled=False)
        import asyncio
        result = asyncio.run(handler({"tool_name": "Agent", "tool_input": {"model": "x", "subagent_type": "t"}}, None, MagicMock()))
        assert result == {}

    def test_non_agent_tool(self) -> None:
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        handler = SubagentModelCompatHookHandler(enabled=True)
        import asyncio
        result = asyncio.run(handler({"tool_name": "Read", "tool_input": {}}, None, MagicMock()))
        assert result == {}

    def test_empty_subagent_type(self) -> None:
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        handler = SubagentModelCompatHookHandler(enabled=True, is_model_supported=lambda m: False)
        import asyncio
        result = asyncio.run(handler({
            "tool_name": "Agent",
            "tool_input": {"model": "haiku", "subagent_type": ""},
        }, None, MagicMock()))
        assert result == {}

    def test_model_inherit_skipped(self) -> None:
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        handler = SubagentModelCompatHookHandler(enabled=True, is_model_supported=lambda m: False)
        import asyncio
        result = asyncio.run(handler({
            "tool_name": "Agent",
            "tool_input": {"model": "inherit", "subagent_type": "Explore"},
        }, None, MagicMock()))
        assert result == {}

    def test_no_model_skipped(self) -> None:
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        handler = SubagentModelCompatHookHandler(enabled=True, is_model_supported=lambda m: False)
        import asyncio
        result = asyncio.run(handler({
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "Explore"},
        }, None, MagicMock()))
        assert result == {}

    def test_tool_input_not_dict(self) -> None:
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        handler = SubagentModelCompatHookHandler(enabled=True)
        import asyncio
        result = asyncio.run(handler({
            "tool_name": "Agent",
            "tool_input": "not a dict",
        }, None, MagicMock()))
        assert result == {}

    def test_model_support_check_exception_defaults_to_supported(self) -> None:
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        def _raises(model: str) -> bool:
            raise RuntimeError("check failed")

        handler = SubagentModelCompatHookHandler(enabled=True, is_model_supported=_raises)
        import asyncio
        result = asyncio.run(handler({
            "tool_name": "Agent",
            "tool_input": {"model": "haiku", "subagent_type": "Explore"},
        }, None, MagicMock()))
        # Exception in check → treated as supported → no rewrite
        assert result == {}

    def test_message_callback_called_on_rewrite(self) -> None:
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        callback = MagicMock()
        handler = SubagentModelCompatHookHandler(
            enabled=True,
            provider_name="test_provider",
            is_model_supported=lambda m: False,
            message_callback=callback,
        )
        import asyncio
        asyncio.run(handler({
            "tool_name": "Agent",
            "tool_input": {"model": "haiku", "subagent_type": "Explore"},
            "session_id": "s1",
        }, None, MagicMock()))
        callback.assert_called_once()

    def test_message_callback_exception_handled(self) -> None:
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        callback = MagicMock(side_effect=RuntimeError("cb fail"))
        handler = SubagentModelCompatHookHandler(
            enabled=True,
            provider_name="test_provider",
            is_model_supported=lambda m: False,
            message_callback=callback,
        )
        import asyncio
        # Should not raise
        result = asyncio.run(handler({
            "tool_name": "Agent",
            "tool_input": {"model": "haiku", "subagent_type": "Explore"},
            "session_id": "s1",
        }, None, MagicMock()))
        assert "hookSpecificOutput" in result

    def test_get_field_dict(self) -> None:
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        assert SubagentModelCompatHookHandler._get_field({"a": 1}, "a") == 1
        assert SubagentModelCompatHookHandler._get_field({"a": 1}, "b", "def") == "def"

    def test_get_field_object_with_get_method(self) -> None:
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        # Object with .get() method uses .get() path
        obj = MagicMock()
        obj.get = MagicMock(return_value="from_get")
        assert SubagentModelCompatHookHandler._get_field(obj, "name") == "from_get"

    def test_get_field_object_fallback_to_getattr(self) -> None:
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        # Object without .get() falls back to getattr
        class _Obj:
            name = "test"

        assert SubagentModelCompatHookHandler._get_field(_Obj(), "name") == "test"
        assert SubagentModelCompatHookHandler._get_field(_Obj(), "missing", "def") == "def"

    def test_full_rewrite_output_structure(self) -> None:
        from xbot.runtime.core.hooks import SubagentModelCompatHookHandler

        handler = SubagentModelCompatHookHandler(
            enabled=True,
            provider_name="alrun",
            is_model_supported=lambda m: False,
        )
        import asyncio
        result = asyncio.run(handler({
            "tool_name": "Agent",
            "tool_input": {"model": "haiku", "subagent_type": "Explore", "prompt": "test"},
            "session_id": "s1",
        }, None, MagicMock()))

        assert result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert result["hookSpecificOutput"]["updatedInput"]["model"] == "inherit"
        assert result["hookSpecificOutput"]["updatedInput"]["subagent_type"] == "Explore"
        assert result["hookSpecificOutput"]["updatedInput"]["prompt"] == "test"


class TestBuildCompactHookConfig:
    """Tests for build_compact_hook configuration builder."""

    def test_with_callback(self) -> None:
        from xbot.runtime.core.hooks import build_compact_hook

        cb = MagicMock()
        hooks = build_compact_hook(enabled=True, message_callback=cb)
        assert "PreCompact" in hooks
        handler = hooks["PreCompact"][0]["hooks"][0]
        assert handler.message_callback is cb

    def test_disabled_returns_empty(self) -> None:
        from xbot.runtime.core.hooks import build_compact_hook

        assert build_compact_hook(enabled=False) == {}


# ===========================================================================
# 5. health
# ===========================================================================


class TestIsAgentReady:
    """Tests for _is_agent_ready helper."""

    def test_running_string(self) -> None:
        from xbot.runtime.system.monitoring.health import _is_agent_ready

        assert _is_agent_ready("running") is True

    def test_unknown_string(self) -> None:
        from xbot.runtime.system.monitoring.health import _is_agent_ready

        assert _is_agent_ready("unknown") is False

    def test_initializing_string(self) -> None:
        from xbot.runtime.system.monitoring.health import _is_agent_ready

        assert _is_agent_ready("initializing") is False

    def test_dict_with_state_running(self) -> None:
        from xbot.runtime.system.monitoring.health import _is_agent_ready

        assert _is_agent_ready({"state": "running"}) is True

    def test_dict_with_status_running(self) -> None:
        from xbot.runtime.system.monitoring.health import _is_agent_ready

        assert _is_agent_ready({"status": "running"}) is True

    def test_dict_not_running(self) -> None:
        from xbot.runtime.system.monitoring.health import _is_agent_ready

        assert _is_agent_ready({"state": "unknown"}) is False

    def test_dict_empty(self) -> None:
        from xbot.runtime.system.monitoring.health import _is_agent_ready

        assert _is_agent_ready({}) is False


class TestHealthCheckServiceEndpoints:
    """Tests for HealthCheckService HTTP endpoints — lines 163-210 focus."""

    @pytest.fixture
    def service(self) -> Any:
        from xbot.runtime.system.monitoring.health import HealthCheckService
        return HealthCheckService(port=28080, host="127.0.0.1")

    @pytest.mark.asyncio
    async def test_handle_health_all_healthy(self, service: Any) -> None:
        from xbot.runtime.system.monitoring.health import HealthStatus

        service.register_checker("a", lambda: HealthStatus(name="a", healthy=True))
        request = MagicMock()
        response = await service._handle_health(request)
        assert response.status == 200
        body = json.loads(response.text)
        assert body["healthy"] is True

    @pytest.mark.asyncio
    async def test_handle_health_with_failure(self, service: Any) -> None:
        from xbot.runtime.system.monitoring.health import HealthStatus

        service.register_checker("a", lambda: HealthStatus(name="a", healthy=False, message="down"))
        request = MagicMock()
        response = await service._handle_health(request)
        assert response.status == 503
        body = json.loads(response.text)
        assert body["healthy"] is False

    @pytest.mark.asyncio
    async def test_handle_live(self, service: Any) -> None:
        request = MagicMock()
        response = await service._handle_live(request)
        body = json.loads(response.text)
        assert body["status"] == "alive"
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_handle_ready_with_running_agent_and_channels(self, service: Any) -> None:
        service.update_status("agent", "running")
        service.update_status("channels", ["telegram"])
        request = MagicMock()
        response = await service._handle_ready(request)
        body = json.loads(response.text)
        assert body["ready"] is True
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_handle_ready_agent_not_running(self, service: Any) -> None:
        service.update_status("agent", "initializing")
        service.update_status("channels", ["telegram"])
        request = MagicMock()
        response = await service._handle_ready(request)
        assert response.status == 503

    @pytest.mark.asyncio
    async def test_handle_ready_no_channels(self, service: Any) -> None:
        service.update_status("agent", "running")
        service.update_status("channels", [])
        request = MagicMock()
        response = await service._handle_ready(request)
        assert response.status == 503

    @pytest.mark.asyncio
    async def test_handle_ready_dict_agent_status(self, service: Any) -> None:
        service.update_status("agent", {"state": "running"})
        service.update_status("channels", ["telegram"])
        request = MagicMock()
        response = await service._handle_ready(request)
        body = json.loads(response.text)
        assert body["ready"] is True
        assert body["agent"] == "running"

    @pytest.mark.asyncio
    async def test_handle_status(self, service: Any) -> None:
        service.update_status("agent", "running")
        service.update_status("channels", ["telegram", "discord"])
        request = MagicMock()
        response = await service._handle_status(request)
        body = json.loads(response.text)
        assert "uptime_seconds" in body
        assert "start_time" in body
        assert body["agent"] == "running"
        assert body["channels"] == ["telegram", "discord"]

    @pytest.mark.asyncio
    async def test_check_component_async_checker(self, service: Any) -> None:
        from xbot.runtime.system.monitoring.health import HealthStatus

        async def async_checker() -> HealthStatus:
            return HealthStatus(name="async", healthy=True, message="ok")

        service.register_checker("async", async_checker)
        result = await service._check_component("async")
        assert result.healthy is True
        assert result.message == "ok"

    @pytest.mark.asyncio
    async def test_path_prefix(self) -> None:
        from xbot.runtime.system.monitoring.health import HealthCheckService

        svc = HealthCheckService(port=28081, path_prefix="/xbot")
        assert svc.path_prefix == "/xbot"

        app = svc._create_app()
        # Check routes exist with prefix
        routes = [r.resource.canonical for r in app.router.routes()]
        assert "/xbot/health" in routes

    @pytest.mark.asyncio
    async def test_start_already_running(self, service: Any) -> None:
        service._runner = MagicMock()  # Pretend already running
        await service.start()  # Should return immediately

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, service: Any) -> None:
        # _runner is None → should be no-op
        await service.stop()
        assert service._runner is None


class TestCreateHealthService:
    """Tests for factory function."""

    def test_create_health_service_defaults(self) -> None:
        from xbot.runtime.system.monitoring.health import create_health_service

        svc = create_health_service()
        assert svc.port == 8080
        assert svc.host == "127.0.0.1"

    def test_create_health_service_custom(self) -> None:
        from xbot.runtime.system.monitoring.health import create_health_service

        svc = create_health_service(port=9090, host="0.0.0.0")
        assert svc.port == 9090


class TestCreateHealthRouter:
    """Tests for FastAPI router — lines 256-309."""

    @pytest.mark.asyncio
    async def test_health_endpoint(self) -> None:
        from xbot.runtime.system.monitoring.health import HealthCheckService, HealthStatus, create_health_router

        svc = HealthCheckService()
        svc.register_checker("a", lambda: HealthStatus(name="a", healthy=True))
        router = create_health_router(svc)
        assert router is not None

    @pytest.mark.asyncio
    async def test_readiness_via_router(self) -> None:
        from xbot.runtime.system.monitoring.health import HealthCheckService, create_health_router

        svc = HealthCheckService()
        svc.update_status("agent", "running")
        svc.update_status("channels", ["telegram"])

        router = create_health_router(svc)
        # We can't easily call FastAPI routes directly without TestClient,
        # but we can verify the router was created with the right routes
        routes = [r.path for r in router.routes]
        assert "/health" in routes
        assert "/health/live" in routes
        assert "/health/ready" in routes
        assert "/status" in routes


class TestHealthCheckResultToDict:
    """Tests for HealthCheckResult serialization."""

    def test_rounding(self) -> None:
        from xbot.runtime.system.monitoring.health import HealthCheckResult, HealthStatus

        result = HealthCheckResult(
            healthy=True,
            timestamp="2026-01-01T00:00:00Z",
            uptime_seconds=100.567,
            components=[HealthStatus(name="x", healthy=True, details={"key": "val"})],
        )
        data = result.to_dict()
        assert data["uptime_seconds"] == 100.57
        assert data["components"][0]["details"] == {"key": "val"}


# ===========================================================================
# 6. alerting
# ===========================================================================


class TestAlertServiceRateLimiting:
    """Tests for alert rate limiting — lines 72-96 focus."""

    @pytest.fixture
    def bus(self) -> MagicMock:
        b = MagicMock()
        b.publish_outbound = AsyncMock()
        return b

    @pytest.mark.asyncio
    async def test_hourly_limit(self, bus: MagicMock) -> None:
        from xbot.runtime.system.monitoring.alerting import AlertConfig, AlertService

        config = AlertConfig(max_alerts_per_hour=2, cooldown_seconds=0)
        svc = AlertService(bus, config)

        assert await svc.send_alert("a1", "msg1") is True
        assert await svc.send_alert("a2", "msg2") is True
        assert await svc.send_alert("a3", "msg3") is False  # hourly limit

    @pytest.mark.asyncio
    async def test_hourly_counter_reset(self, bus: MagicMock) -> None:
        from xbot.runtime.system.monitoring.alerting import AlertConfig, AlertService

        config = AlertConfig(max_alerts_per_hour=1, cooldown_seconds=0)
        svc = AlertService(bus, config)

        assert await svc.send_alert("a1", "msg1") is True
        assert await svc.send_alert("a2", "msg2") is False

        # Simulate hour passing
        svc._hour_start = time.time() - 3601
        assert await svc.send_alert("a3", "msg3") is True

    @pytest.mark.asyncio
    async def test_per_rule_cooldown(self, bus: MagicMock) -> None:
        from xbot.runtime.system.monitoring.alerting import AlertConfig, AlertService

        config = AlertConfig(cooldown_seconds=300, max_alerts_per_hour=100)
        svc = AlertService(bus, config)

        assert await svc.send_alert("rule1", "first") is True
        assert await svc.send_alert("rule1", "second") is False  # cooldown
        # Different rule not in cooldown
        assert await svc.send_alert("rule2", "other") is True

    @pytest.mark.asyncio
    async def test_send_failure_reverts_count(self, bus: MagicMock) -> None:
        from xbot.runtime.system.monitoring.alerting import AlertConfig, AlertService

        config = AlertConfig(cooldown_seconds=0, max_alerts_per_hour=1)
        svc = AlertService(bus, config)

        bus.publish_outbound = AsyncMock(side_effect=RuntimeError("send failed"))
        result = await svc.send_alert("title", "msg")
        assert result is False
        # Count should have been reverted
        assert svc._alert_count == 0

    @pytest.mark.asyncio
    async def test_alert_with_details(self, bus: MagicMock) -> None:
        from xbot.runtime.system.monitoring.alerting import AlertConfig, AlertService

        config = AlertConfig(chat_id="test", cooldown_seconds=0)
        svc = AlertService(bus, config)

        await svc.send_alert("Title", "Body", severity="warning", details={"host": "server1"})

        call_args = bus.publish_outbound.call_args[0][0]
        assert "host: server1" in call_args.content
        assert "warning" in call_args.content

    @pytest.mark.asyncio
    async def test_severity_emoji_mapping(self, bus: MagicMock) -> None:
        from xbot.runtime.system.monitoring.alerting import AlertConfig, AlertService

        config = AlertConfig(chat_id="test", cooldown_seconds=0)
        svc = AlertService(bus, config)

        for severity, emoji in [("error", "❌"), ("warning", "⚠️"), ("critical", "🚨"), ("info", "ℹ️")]:
            bus.publish_outbound.reset_mock()
            svc._last_alert_time.clear()  # reset cooldown
            await svc.send_alert(f"t-{severity}", "msg", severity=severity)
            content = bus.publish_outbound.call_args[0][0].content
            assert emoji in content

    @pytest.mark.asyncio
    async def test_unknown_severity_emoji(self, bus: MagicMock) -> None:
        from xbot.runtime.system.monitoring.alerting import AlertConfig, AlertService

        config = AlertConfig(chat_id="test", cooldown_seconds=0)
        svc = AlertService(bus, config)

        await svc.send_alert("t", "msg", severity="weird")
        content = bus.publish_outbound.call_args[0][0].content
        assert "❗" in content

    @pytest.mark.asyncio
    async def test_alert_memory_warning(self, bus: MagicMock) -> None:
        from xbot.runtime.system.monitoring.alerting import AlertConfig, AlertService

        config = AlertConfig(chat_id="test", cooldown_seconds=0)
        svc = AlertService(bus, config)

        result = await svc.alert_memory_warning(85.3)
        assert result is True
        content = bus.publish_outbound.call_args[0][0].content
        assert "85.3%" in content
        assert "内存使用警告" in content


class TestGlobalAlertFunctions:
    """Tests for module-level alert functions — lines 207-242."""

    @pytest.mark.asyncio
    async def test_init_and_get_alert_service(self) -> None:
        from xbot.runtime.system.monitoring import alerting

        bus = MagicMock()
        svc = alerting.init_alert_service(bus)
        assert alerting.get_alert_service() is svc

    @pytest.mark.asyncio
    async def test_global_alert_error_no_service(self) -> None:
        from xbot.runtime.system.monitoring import alerting

        original = alerting._alert_service
        try:
            alerting._alert_service = None
            result = await alerting.alert_error(RuntimeError("test"))
            assert result is False
        finally:
            alerting._alert_service = original

    @pytest.mark.asyncio
    async def test_global_alert_critical_no_service(self) -> None:
        from xbot.runtime.system.monitoring import alerting

        original = alerting._alert_service
        try:
            alerting._alert_service = None
            result = await alerting.alert_critical(RuntimeError("test"))
            assert result is False
        finally:
            alerting._alert_service = original

    @pytest.mark.asyncio
    async def test_global_alert_error_with_service(self) -> None:
        from xbot.runtime.system.monitoring import alerting

        bus = MagicMock()
        bus.publish_outbound = AsyncMock()
        svc = alerting.init_alert_service(bus, alerting.AlertConfig(cooldown_seconds=0, chat_id="x"))
        result = await alerting.alert_error(RuntimeError("boom"), "ctx")
        assert result is True

    @pytest.mark.asyncio
    async def test_global_alert_critical_with_service(self) -> None:
        from xbot.runtime.system.monitoring import alerting

        bus = MagicMock()
        bus.publish_outbound = AsyncMock()
        svc = alerting.init_alert_service(bus, alerting.AlertConfig(cooldown_seconds=0, chat_id="x"))
        result = await alerting.alert_critical(RuntimeError("critical boom"), "ctx")
        assert result is True
        content = bus.publish_outbound.call_args[0][0].content
        assert "严重错误" in content


class TestAlertConfig:
    """Additional AlertConfig tests."""

    def test_custom_config(self) -> None:
        from xbot.runtime.system.monitoring.alerting import AlertConfig

        config = AlertConfig(
            enabled=False,
            channel="discord",
            chat_id="chan-123",
            max_alerts_per_hour=5,
            cooldown_seconds=60.0,
        )
        assert config.enabled is False
        assert config.channel == "discord"
        assert config.chat_id == "chan-123"
        assert config.max_alerts_per_hour == 5
        assert config.cooldown_seconds == 60.0


class TestAlertRule:
    """Tests for AlertRule dataclass."""

    def test_alert_rule_creation(self) -> None:
        from xbot.runtime.system.monitoring.alerting import AlertRule

        rule = AlertRule(
            name="high_memory",
            condition=lambda d: d.get("memory", 0) > 90,
            cooldown_seconds=120,
        )
        assert rule.name == "high_memory"
        assert rule.cooldown_seconds == 120
        assert rule.last_triggered == 0.0
        assert rule.condition({"memory": 95}) is True
        assert rule.condition({"memory": 50}) is False
