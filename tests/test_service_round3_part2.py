"""Additional coverage tests for AgentService - Round 3 Part 2."""

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.platform.bus.events import InboundMessage
from xbot.runtime.core.protocol import AgentContext, AgentResponse
from xbot.runtime.core.service import AgentService, SessionWorker
from xbot.runtime.core.types import AgentConfig
from xbot.runtime.state import SessionEvent, SessionPhase


class TestHasPendingUserWaitExceptions:
    """Test _has_pending_user_wait exception handling."""

    def test_bus_get_pending_request_raises(self):
        """Should handle exception from get_pending_request_for_session."""
        service = AgentService()
        bus = MagicMock()
        bus.get_pending_request_for_session.side_effect = RuntimeError("bus error")
        bus.get_pending_interaction_for_session.return_value = None
        service._shared_resources = {"bus": bus}

        result = service._has_pending_user_wait("s1")

        assert result is False

    def test_bus_get_pending_interaction_raises(self):
        """Should handle exception from get_pending_interaction_for_session."""
        service = AgentService()
        bus = MagicMock()
        bus.get_pending_request_for_session.return_value = None
        bus.get_pending_interaction_for_session.side_effect = RuntimeError("bus error")
        service._shared_resources = {"bus": bus}

        result = service._has_pending_user_wait("s1")

        assert result is False


class TestDispatchTerminalStateExceptions:
    """Test _dispatch_terminal_state exception handling."""

    def test_bus_get_pending_request_raises(self):
        """Should handle exception from get_pending_request_for_session."""
        service = AgentService()
        bus = MagicMock()
        bus.get_pending_request_for_session.side_effect = RuntimeError("bus error")
        bus.get_pending_interaction_for_session = MagicMock(return_value=None)
        service._shared_resources = {"bus": bus}

        sm = MagicMock()
        sm.dispatch.return_value = True
        service._shared_resources["runtime_registry"] = sm

        service._dispatch_terminal_state("s1", sm=sm, reason="test")

        # Should still dispatch TURN_COMPLETED even if permission check failed
        calls = sm.dispatch.call_args_list
        assert any(call.args[1] == SessionEvent.TURN_COMPLETED for call in calls)

    def test_bus_get_pending_interaction_raises(self):
        """Should handle exception from get_pending_interaction_for_session."""
        service = AgentService()
        bus = MagicMock()
        bus.get_pending_request_for_session.return_value = None
        bus.get_pending_interaction_for_session.side_effect = RuntimeError("bus error")
        service._shared_resources = {"bus": bus}

        sm = MagicMock()
        sm.dispatch.return_value = True
        service._shared_resources["runtime_registry"] = sm

        service._dispatch_terminal_state("s1", sm=sm, reason="test")

        # Should still dispatch TURN_COMPLETED even if interaction check failed
        calls = sm.dispatch.call_args_list
        assert any(call.args[1] == SessionEvent.TURN_COMPLETED for call in calls)


class TestBuildQueryPromptOSError:
    """Test _build_query_prompt OSError handling."""

    def test_build_query_prompt_with_unreadable_image(self, tmp_path: Path):
        """Should handle OSError when reading image file."""
        image_path = tmp_path / "test.png"
        image_path.write_bytes(b"fake png data")

        with (
            patch("xbot.runtime.core.service.classify_file") as mock_classify,
            patch("xbot.runtime.core.service.detect_image_mime") as mock_mime,
            patch("pathlib.Path.read_bytes") as mock_read,
        ):
            from xbot.platform.utils.file_reader import FileType
            mock_classify.return_value = FileType.IMAGE
            mock_mime.return_value = "image/png"
            mock_read.side_effect = OSError("Permission denied")

            result = AgentService._build_query_prompt("test", [str(image_path)])

            assert isinstance(result, list)
            text_blocks = [b for b in result if b.get("type") == "text"]
            assert any("failed to read" in b["text"].lower() for b in text_blocks)


class TestResetSessionWithSdkDelete:
    """Test reset_session with SDK session deletion."""

    @pytest.mark.asyncio
    async def test_reset_session_deletes_sdk_session(self, tmp_path: Path):
        """Should delete SDK session when drop_sdk_context=True."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.resolve_sdk_session_id.return_value = "sdk-session-123"
        service._shared_resources["runtime_registry"] = sm
        service._client_pool.disconnect = AsyncMock(return_value=True)

        async def mock_delete(*args):
            return None

        with patch("claude_agent_sdk.delete_session", side_effect=mock_delete):
            await service.reset_session("s1", drop_sdk_context=True)

        sm.resolve_sdk_session_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_reset_session_handles_sdk_delete_exception(self, tmp_path: Path):
        """Should handle exception when deleting SDK session."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.resolve_sdk_session_id.return_value = "sdk-session-123"
        service._shared_resources["runtime_registry"] = sm
        service._client_pool.disconnect = AsyncMock(return_value=True)

        with patch("claude_agent_sdk.delete_session", side_effect=Exception("Delete failed")):
            # Should not raise
            await service.reset_session("s1", drop_sdk_context=True)

    @pytest.mark.asyncio
    async def test_reset_session_with_set_sdk_session_id_impl(self, tmp_path: Path):
        """Should use _set_sdk_session_id_impl when available."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm._set_sdk_session_id_impl = MagicMock()
        service._shared_resources["runtime_registry"] = sm
        service._client_pool.disconnect = AsyncMock(return_value=True)

        await service.reset_session("s1", drop_sdk_context=True)

        sm._set_sdk_session_id_impl.assert_called_once_with("s1", None)

    @pytest.mark.asyncio
    async def test_reset_session_with_async_set_sdk_session_id(self, tmp_path: Path):
        """Should handle async set_sdk_session_id."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        async def async_set_sdk(*args):
            pass

        sm = MagicMock(spec=['set_sdk_session_id'])
        sm.set_sdk_session_id = async_set_sdk
        service._shared_resources["runtime_registry"] = sm
        service._client_pool.disconnect = AsyncMock(return_value=True)

        await service.reset_session("s1", drop_sdk_context=True)

        # Should handle async call without error


class TestGetSessionCommandsBranches:
    """Test get_session_commands additional branches."""

    @pytest.mark.asyncio
    async def test_get_session_commands_with_non_string_commands(self, tmp_path: Path):
        """Should skip non-string commands."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.get_commands.return_value = ["/valid", 123, None, ""]
        service._shared_resources["runtime_registry"] = sm

        commands = await service.get_session_commands("s1")

        assert "/valid" in commands
        assert 123 not in commands

    @pytest.mark.asyncio
    async def test_get_session_commands_with_empty_string_commands(self, tmp_path: Path):
        """Should skip empty string commands."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.get_commands.return_value = ["", "  ", "/valid"]
        service._shared_resources["runtime_registry"] = sm

        commands = await service.get_session_commands("s1")

        assert "/valid" in commands

    @pytest.mark.asyncio
    async def test_get_session_commands_normalizes_without_slash(self, tmp_path: Path):
        """Should add / prefix to commands without it."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.get_commands.return_value = ["help", "/clear"]
        service._shared_resources["runtime_registry"] = sm

        commands = await service.get_session_commands("s1")

        assert "/help" in commands
        assert "/clear" in commands


class TestInterruptSessionExceptionHandling:
    """Test interrupt_session exception handling."""

    @pytest.mark.asyncio
    async def test_interrupt_exception_clears_waiter(self, tmp_path: Path):
        """Should clear waiter when interrupt raises exception."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        mock_client = MagicMock()
        mock_client.interrupt = AsyncMock(side_effect=Exception("Interrupt failed"))
        worker = SessionWorker(
            session_key="s1",
            client=mock_client,
            input_queue=asyncio.Queue(),
            task=None,
            channel="test",
            chat_id="c1",
        )
        service._session_workers["s1"] = worker

        sm = MagicMock()
        sm.get_phase.return_value = SessionPhase.RECEIVING_STREAM
        service._shared_resources["runtime_registry"] = sm

        result = await service.interrupt_session("s1")

        assert result["interrupted"] is False
        assert "s1" not in service._interrupt_waiters


class TestFormatToolHintLongBody:
    """Test _format_tool_hint with long body truncation."""

    def test_format_tool_hint_with_long_body(self):
        """Should truncate long tool call bodies."""
        long_cmd = "x" * 300
        tool_calls = [
            {
                "name": "bash",
                "input": {"command": long_cmd},
                "kind": "tool",
            },
        ]

        result = AgentService._format_tool_hint(tool_calls)

        assert len(result) < 250
        assert "…" in result or "..." in result


class TestRefreshSessionCommandsFromClient:
    """Test _refresh_session_commands_from_client."""

    @pytest.mark.asyncio
    async def test_refresh_with_cached_commands(self, tmp_path: Path):
        """Should skip refresh when commands already cached."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.get_commands.return_value = ["/cached1", "/cached2"]
        service._shared_resources["runtime_registry"] = sm

        mock_client = MagicMock()
        mock_client.get_server_info = AsyncMock(return_value={})

        await service._refresh_session_commands_from_client("s1", mock_client)

        # Should not call get_server_info since commands are cached
        mock_client.get_server_info.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_without_cache(self, tmp_path: Path):
        """Should refresh when no commands cached."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.get_commands.return_value = []
        sm.set_commands = MagicMock()
        service._shared_resources["runtime_registry"] = sm

        mock_client = MagicMock()
        mock_client.get_server_info = AsyncMock(return_value={
            "slash_commands": ["/new1", "/new2"]
        })

        await service._refresh_session_commands_from_client("s1", mock_client)

        sm.set_commands.assert_called_once()


class TestObserveSdkMessageIdleBoundary:
    """Test _observe_sdk_message idle boundary handling."""

    def test_observe_idle_boundary_wakes_waiter(self):
        """Should wake waiter on idle boundary message."""
        service = AgentService()

        waiter = asyncio.Event()
        service._interrupt_waiters["s1"] = waiter

        msg = MagicMock()
        type(msg).__name__ = "SystemMessage"
        msg.subtype = "session_state_changed"
        msg.data = {"state": "idle"}

        service._observe_sdk_message("s1", msg)

        assert waiter.is_set()


class TestGetWorkspaceCommandsSummary:
    """Test get_workspace_commands_summary."""

    def test_summary_without_commands_loader(self):
        """Should return empty string without commands loader."""
        service = AgentService()
        service._commands_loader = None

        result = service.get_workspace_commands_summary()

        assert result == ""

    def test_summary_with_commands_loader(self):
        """Should return summary from commands loader."""
        service = AgentService()
        loader = MagicMock()
        loader.build_commands_summary.return_value = "Commands: /help, /clear"
        service._commands_loader = loader

        result = service.get_workspace_commands_summary()

        assert "Commands" in result

    def test_summary_with_exception(self):
        """Should handle exception from commands loader."""
        service = AgentService()
        loader = MagicMock()
        loader.build_commands_summary.side_effect = Exception("Loader error")
        service._commands_loader = loader

        result = service.get_workspace_commands_summary()

        assert result == ""


class TestInitToolAdapter:
    """Test _init_tool_adapter."""

    def test_init_tool_adapter_success(self, tmp_path: Path):
        """Should initialize tool adapter successfully."""
        service = AgentService()
        service._shared_resources = {"workspace": str(tmp_path)}

        with patch("xbot.capabilities.tool_adapter.ToolAdapter") as mock_adapter:
            mock_instance = MagicMock()
            mock_adapter.return_value = mock_instance

            service._init_tool_adapter()

            assert service._tool_adapter is mock_instance
            mock_instance._ensure_core_tools_registered.assert_called_once()

    def test_init_tool_adapter_exception(self, tmp_path: Path):
        """Should handle exception during tool adapter initialization."""
        service = AgentService()
        service._shared_resources = {"workspace": str(tmp_path)}

        with patch("xbot.capabilities.tool_adapter.ToolAdapter", side_effect=Exception("Init failed")):
            service._init_tool_adapter()

            assert service._tool_adapter is None


class TestResolveSettingSourcesBranches:
    """Test _resolve_setting_sources additional branches."""

    def test_resolve_with_non_string_mode(self):
        """Should handle non-string mode."""
        service = AgentService()

        sdk_config = MagicMock()
        sdk_config.memory_integration = MagicMock()
        sdk_config.memory_integration.mode = 123  # Non-string
        sdk_config.memory_integration.setting_sources = None

        result = service._resolve_setting_sources(sdk_config, "cli")

        # Should default to "auto" mode and return default sources
        assert result == ["user", "project", "local"]

    def test_resolve_with_dict_off_mode(self):
        """Should return None for dict mode with off."""
        service = AgentService()

        sdk_config = MagicMock()
        sdk_config.memory_integration = {
            "mode": "off",
            "setting_sources": {},
        }

        result = service._resolve_setting_sources(sdk_config, "cli")

        assert result is None
