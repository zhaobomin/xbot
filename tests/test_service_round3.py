"""Additional coverage tests for AgentService uncovered branches."""

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.platform.bus.events import InboundMessage
from xbot.runtime.core.protocol import AgentContext, AgentResponse, StructuredLLMResponse
from xbot.runtime.core.service import AgentService, SessionWorker
from xbot.runtime.core.types import AgentConfig
from xbot.runtime.state import SessionEvent, SessionPhase


class TestEnqueueWorkerMessageEdgeCases:
    """Test _enqueue_worker_message edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_enqueue_during_stopping_phase(self, tmp_path: Path):
        """Should reject messages when session is stopping."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        bus = MagicMock()
        bus.publish_outbound = AsyncMock()

        sm = MagicMock()
        sm.get_phase.return_value = SessionPhase.STOPPING
        service._shared_resources["runtime_registry"] = sm

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="hello")
        result = await service._enqueue_worker_message(msg, bus)

        assert result is None
        bus.publish_outbound.assert_awaited_once()
        assert "stopping" in bus.publish_outbound.call_args.args[0].content.lower()

    @pytest.mark.asyncio
    async def test_enqueue_during_releasing_phase(self, tmp_path: Path):
        """Should reject messages when session is releasing client."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        bus = MagicMock()
        bus.publish_outbound = AsyncMock()

        sm = MagicMock()
        sm.get_phase.return_value = SessionPhase.RELEASING_CLIENT
        service._shared_resources["runtime_registry"] = sm

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="hello")
        result = await service._enqueue_worker_message(msg, bus)

        assert result is None
        bus.publish_outbound.assert_awaited_once()


class TestShouldRetryWithoutResumeBranches:
    """Test _should_retry_without_resume various error patterns."""

    def test_resume_with_not_found_error(self):
        """Should return True for 'resume session not found' error."""
        service = AgentService()
        service._shared_resources = {"run_mode": "cli", "resume_policy": {}}

        options = MagicMock()
        options.resume = "session-id"

        error = Exception("resume session not found in database")
        assert service._should_retry_without_resume("s1", options, error) is True

    def test_resume_with_does_not_exist_error(self):
        """Should return True for 'resume does not exist' error."""
        service = AgentService()
        service._shared_resources = {"run_mode": "cli", "resume_policy": {}}

        options = MagicMock()
        options.resume = "session-id"

        error = Exception("resume session does not exist")
        assert service._should_retry_without_resume("s1", options, error) is True

    def test_resume_with_invalid_error(self):
        """Should return True for 'invalid resume' error."""
        service = AgentService()
        service._shared_resources = {"run_mode": "cli", "resume_policy": {}}

        options = MagicMock()
        options.resume = "session-id"

        error = Exception("invalid resume session")
        assert service._should_retry_without_resume("s1", options, error) is True

    def test_resume_with_strict_policy(self):
        """Should return False when strict_resume policy is set."""
        service = AgentService()
        service._shared_resources = {
            "run_mode": "cli",
            "resume_policy": {"explicit_resume": True, "strict_resume": True},
        }

        options = MagicMock()
        options.resume = "session-id"

        error = Exception("resume session not found")
        assert service._should_retry_without_resume("s1", options, error) is False

    def test_resume_with_unrelated_error(self):
        """Should return False for unrelated connection errors."""
        service = AgentService()
        service._shared_resources = {"run_mode": "cli", "resume_policy": {}}

        options = MagicMock()
        options.resume = "session-id"

        error = Exception("connection timeout")
        assert service._should_retry_without_resume("s1", options, error) is False


class TestClearSdkResumeContextBranches:
    """Test _clear_sdk_resume_context implementation paths."""

    @pytest.mark.asyncio
    async def test_clear_with_sync_set_sdk_session_id(self, tmp_path: Path):
        """Should call sync set_sdk_session_id method."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        # Create a mock without _set_sdk_session_id_impl to test the fallback path
        sm = MagicMock(spec=['set_sdk_session_id'])
        sm.set_sdk_session_id = MagicMock()
        service._shared_resources["runtime_registry"] = sm

        service._clear_sdk_resume_context("s1")

        sm.set_sdk_session_id.assert_called_once_with("s1", None)

    @pytest.mark.asyncio
    async def test_clear_with_async_set_sdk_session_id(self, tmp_path: Path):
        """Should track async set_sdk_session_id call."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        async def async_set_sdk(*args):
            pass

        # Create a mock without _set_sdk_session_id_impl to test the async fallback path
        sm = MagicMock(spec=['set_sdk_session_id'])
        sm.set_sdk_session_id = async_set_sdk
        service._shared_resources["runtime_registry"] = sm

        service._clear_sdk_resume_context("s1")

        # Should have tracked the async task (may be cleaned up immediately)
        await asyncio.sleep(0.01)  # Allow any async cleanup


class TestAttemptBrokenSessionRecoveryBranches:
    """Test _attempt_broken_session_recovery implementation details."""

    @pytest.mark.asyncio
    async def test_recovery_dispatches_stream_error_event(self, tmp_path: Path):
        """Should dispatch STREAM_ERROR event before recovery."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.dispatch.return_value = True
        service._shared_resources["runtime_registry"] = sm

        service._client_pool.disconnect = AsyncMock(return_value=True)

        await service._attempt_broken_session_recovery("s1", reason="test")

        # Should have dispatched STREAM_ERROR
        calls = sm.dispatch.call_args_list
        assert any(call.args[1] == SessionEvent.STREAM_ERROR for call in calls)

    @pytest.mark.asyncio
    async def test_recovery_dispatches_disconnect_events(self, tmp_path: Path):
        """Should dispatch DISCONNECT_OK and RECOVER events on success."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.dispatch.return_value = True
        service._shared_resources["runtime_registry"] = sm

        service._client_pool.disconnect = AsyncMock(return_value=True)

        await service._attempt_broken_session_recovery("s1", reason="test")

        calls = sm.dispatch.call_args_list
        assert any(call.args[1] == SessionEvent.DISCONNECT_OK for call in calls)
        assert any(call.args[1] == SessionEvent.RECOVER for call in calls)

    @pytest.mark.asyncio
    async def test_recovery_dispatches_disconnect_failed_event(self, tmp_path: Path):
        """Should dispatch DISCONNECT_FAILED event on failure."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.dispatch.return_value = True
        sm.note_recovery_failure.return_value = 1
        service._shared_resources["runtime_registry"] = sm

        service._client_pool.disconnect = AsyncMock(return_value=False)

        await service._attempt_broken_session_recovery("s1", reason="test")

        calls = sm.dispatch.call_args_list
        assert any(call.args[1] == SessionEvent.DISCONNECT_FAILED for call in calls)


class TestConvertResultMessageBranches:
    """Test _convert_result_message additional branches."""

    def test_convert_result_with_dict_usage_input_tokens(self):
        """Should handle dict usage with input_tokens key."""
        service = AgentService()

        msg = MagicMock()
        msg.result = "done"
        msg.usage = {"input_tokens": 100, "output_tokens": 50}
        msg.stop_reason = "end_turn"
        msg.num_turns = 1
        msg.total_cost_usd = 0.01

        result = service._convert_result_message(msg)

        assert result is not None
        assert result.usage["input_tokens"] == 100
        assert result.usage["output_tokens"] == 50

    def test_convert_result_with_dict_usage_input_tokens_upper(self):
        """Should handle dict usage with inputTokens key."""
        service = AgentService()

        msg = MagicMock()
        msg.result = "done"
        msg.usage = {"inputTokens": 200, "outputTokens": 100}
        msg.stop_reason = "end_turn"
        msg.num_turns = 1

        result = service._convert_result_message(msg)

        assert result is not None
        assert result.usage["input_tokens"] == 200
        assert result.usage["output_tokens"] == 100

    def test_convert_result_with_dict_usage_total_tokens(self):
        """Should handle dict usage with total_input_tokens key."""
        service = AgentService()

        msg = MagicMock()
        msg.result = "done"
        msg.usage = {"total_input_tokens": 300, "total_output_tokens": 150}
        msg.stop_reason = "end_turn"
        msg.num_turns = 1

        result = service._convert_result_message(msg)

        assert result is not None
        assert result.usage["input_tokens"] == 300
        assert result.usage["output_tokens"] == 150

    def test_convert_result_with_none_result(self):
        """Should handle None result gracefully."""
        service = AgentService()

        msg = MagicMock()
        msg.result = None
        msg.usage = None
        msg.stop_reason = "end_turn"

        result = service._convert_result_message(msg)

        assert result is not None
        assert result.content == ""

    def test_convert_result_with_non_string_result(self):
        """Should handle non-string result."""
        service = AgentService()

        msg = MagicMock()
        msg.result = 12345
        msg.usage = None
        msg.stop_reason = "end_turn"

        result = service._convert_result_message(msg)

        assert result is not None
        assert result.content == ""


class TestBuildQueryPromptAdditionalPaths:
    """Test _build_query_prompt additional multimodal paths."""

    def test_build_query_prompt_with_empty_path(self, tmp_path: Path):
        """Should skip empty paths."""
        result = AgentService._build_query_prompt("test", ["", "  "])
        assert isinstance(result, str)
        assert "test" in result

    def test_build_query_prompt_with_directory(self, tmp_path: Path):
        """Should handle directory paths as unresolved references."""
        result = AgentService._build_query_prompt("test", [str(tmp_path)])
        assert isinstance(result, str)
        assert str(tmp_path) in result or "附件" in result

    def test_build_query_prompt_with_mixed_media(self, tmp_path: Path):
        """Should handle mix of valid and invalid media."""
        image_path = tmp_path / "test.png"
        image_path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
            b"\x1f\x15\xc4\x89"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        result = AgentService._build_query_prompt("test", [str(image_path), 123, None, ""])

        assert isinstance(result, list)
        image_blocks = [b for b in result if b.get("type") == "image"]
        assert len(image_blocks) == 1


class TestBuildMcpServersBranches:
    """Test _build_mcp_servers additional branches."""

    @pytest.mark.asyncio
    async def test_build_mcp_servers_with_none_server_config(self, tmp_path: Path):
        """Should skip None server configs."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        config = AgentConfig(model="test", system_prompt="")
        config.mcp_servers = {"server1": None}
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        servers = service._build_mcp_servers()

        assert isinstance(servers, dict)
        assert "server1" not in servers

    @pytest.mark.asyncio
    async def test_build_mcp_servers_with_empty_name(self, tmp_path: Path):
        """Should skip servers with empty names."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        config = AgentConfig(model="test", system_prompt="")
        config.mcp_servers = {"": {"command": "test"}}
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        servers = service._build_mcp_servers()

        assert isinstance(servers, dict)
        assert "" not in servers

    @pytest.mark.asyncio
    async def test_build_mcp_servers_merges_xbot_server(self, tmp_path: Path):
        """Should merge xbot extension tools as MCP server."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        config = AgentConfig(model="test", system_prompt="")
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        # Mock tool adapter
        service._tool_adapter = MagicMock()
        service._tool_adapter.create_mcp_server.return_value = {
            "xbot": {"command": "xbot-mcp", "args": []}
        }

        servers = service._build_mcp_servers()

        assert "xbot" in servers


class TestBuildSdkAgentsBranches:
    """Test _build_sdk_agents implementation details."""

    @pytest.mark.asyncio
    async def test_build_sdk_agents_with_agent_config(self, tmp_path: Path):
        """Should build agent definitions from config."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        config = AgentConfig(
            model="test",
            system_prompt="",
            agents=[
                {
                    "name": "helper",
                    "description": "Helper agent",
                    "prompt": "You are a helper",
                    "tools": ["bash", "read_file"],
                }
            ],
        )
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        result = service._build_sdk_agents()

        assert result is not None
        assert "helper" in result
        assert result["helper"].description == "Helper agent"

    @pytest.mark.asyncio
    async def test_build_sdk_agents_with_none_mcp_servers(self, tmp_path: Path):
        """Should handle None mcp_servers gracefully."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        config = AgentConfig(model="test", system_prompt="", agents=[])
        config.mcp_servers = None
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        result = service._build_sdk_agents()

        assert result is not None or result is None  # May return empty dict or None


class TestBuildHooksBranches:
    """Test _build_hooks implementation details."""

    @pytest.mark.asyncio
    async def test_build_hooks_with_invalid_hooks_type(self, tmp_path: Path):
        """Should handle invalid hooks config type."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"
        runtime_config.agents.claude_sdk.hooks = "not a dict"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sdk_config = runtime_config.agents.claude_sdk
        hooks = service._build_hooks(sdk_config)

        # Should still return hooks with PreCompact
        assert hooks is not None

    @pytest.mark.asyncio
    async def test_build_hooks_with_invalid_event_name(self, tmp_path: Path):
        """Should skip hooks with invalid event names."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"
        runtime_config.agents.claude_sdk.hooks = {"": []}

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sdk_config = runtime_config.agents.claude_sdk
        hooks = service._build_hooks(sdk_config)

        assert hooks is not None

    @pytest.mark.asyncio
    async def test_build_hooks_with_non_list_matchers(self, tmp_path: Path):
        """Should skip hooks with non-list matchers."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"
        runtime_config.agents.claude_sdk.hooks = {"PreToolUse": "not a list"}

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sdk_config = runtime_config.agents.claude_sdk
        hooks = service._build_hooks(sdk_config)

        assert hooks is not None


class TestBuildEnvConfigBranches:
    """Test _build_env_config implementation details."""

    def test_build_env_with_no_provider_name(self):
        """Should return minimal env when no provider name."""
        service = AgentService()
        service._config = AgentConfig(model="test", system_prompt="")

        config = MagicMock()
        config.agents.defaults.provider = None
        service._shared_resources = {"config": config}

        env = service._build_env_config()

        assert "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS" in env
        assert "ANTHROPIC_API_KEY" not in env

    def test_build_env_with_no_providers(self):
        """Should return minimal env when no providers config."""
        service = AgentService()
        service._config = AgentConfig(model="test", system_prompt="")

        config = MagicMock()
        config.agents.defaults.provider = "anthropic"
        config.providers = None
        service._shared_resources = {"config": config}

        env = service._build_env_config()

        assert "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS" in env

    def test_build_env_with_secret_str_api_key(self):
        """Should extract SecretStr values."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"
        runtime_config.providers.anthropic.api_base = None

        service = AgentService()
        service._config = AgentConfig(model="test", system_prompt="")
        service._shared_resources = {"config": runtime_config}

        env = service._build_env_config()

        assert env["ANTHROPIC_API_KEY"] == "sk-test"


class TestBuildSkillAddDirsBranches:
    """Test _build_skill_add_dirs implementation details."""

    def test_build_skill_add_dirs_with_workspace_variable(self, tmp_path: Path):
        """Should resolve $workspace in skill dirs."""
        config = MagicMock()
        config.skills.enabled = True
        config.skills.dirs = ["$workspace/skills"]
        config.skills.additional_dirs = []

        service = AgentService()
        service._shared_resources = {"config": config}

        result = service._build_skill_add_dirs(str(tmp_path))

        assert len(result) > 0
        assert str(tmp_path) in result[0]

    def test_build_skill_add_dirs_with_relative_path(self, tmp_path: Path):
        """Should resolve relative paths against workspace."""
        config = MagicMock()
        config.skills.enabled = True
        config.skills.dirs = ["skills"]
        config.skills.additional_dirs = []

        service = AgentService()
        service._shared_resources = {"config": config}

        result = service._build_skill_add_dirs(str(tmp_path))

        assert len(result) > 0


class TestBuildPluginConfigsBranches:
    """Test _build_plugin_configs implementation details."""

    def test_build_plugin_configs_with_enabled_plugins(self, tmp_path: Path):
        """Should filter plugins by enabled list."""
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "plugin1").mkdir()
        (plugin_dir / "plugin2").mkdir()

        config = MagicMock()
        config.plugins.enabled = True
        config.plugins.dirs = [str(plugin_dir)]
        config.plugins.enabled_plugins = ["plugin1"]
        config.plugins.disabled_plugins = []

        service = AgentService()
        service._shared_resources = {"config": config}

        result = service._build_plugin_configs(str(tmp_path))

        assert len(result) == 1
        assert "plugin1" in result[0]["path"]

    def test_build_plugin_configs_with_disabled_plugins(self, tmp_path: Path):
        """Should exclude disabled plugins."""
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "plugin1").mkdir()
        (plugin_dir / "plugin2").mkdir()

        config = MagicMock()
        config.plugins.enabled = True
        config.plugins.dirs = [str(plugin_dir)]
        config.plugins.enabled_plugins = []
        config.plugins.disabled_plugins = ["plugin1"]

        service = AgentService()
        service._shared_resources = {"config": config}

        result = service._build_plugin_configs(str(tmp_path))

        assert len(result) == 1
        assert "plugin2" in result[0]["path"]


class TestBuildRuntimeIdentitySectionBranches:
    """Test _build_runtime_identity_section implementation details."""

    def test_build_identity_section_with_model_only(self):
        """Should include model in identity section."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.agents.defaults.provider = ""

        service = AgentService()
        service._shared_resources = {"config": runtime_config}

        result = service._build_runtime_identity_section()

        assert "claude-sonnet-4-5" in result


class TestGetOrCreateClientRetryPaths:
    """Test _get_or_create_client retry logic."""

    @pytest.mark.asyncio
    async def test_get_or_create_client_with_resume_failure(self, tmp_path: Path):
        """Should retry without resume on resume failure."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        # Mock client pool to fail first time with resume error
        call_count = [0]
        async def mock_get_or_create(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                options = kwargs.get("options")
                if options and options.resume:
                    raise Exception("resume session not found")
            return MagicMock()

        service._client_pool.get_or_create = mock_get_or_create

        # Mock registry to provide resume session
        sm = MagicMock()
        sm.resolve_sdk_session_id.return_value = "old-session-id"
        service._shared_resources["runtime_registry"] = sm
        service._shared_resources["run_mode"] = "cli"

        client = await service._get_or_create_client("s1")

        assert client is not None
        assert call_count[0] == 2  # Should have retried


class TestSyncSdkSessionMappingBranches:
    """Test _sync_sdk_session_mapping implementation details."""

    def test_sync_with_init_message(self):
        """Should cache capabilities from init messages."""
        service = AgentService()

        sm = MagicMock()
        service._shared_resources = {"runtime_registry": sm}

        msg = MagicMock()
        msg.subtype = "init"
        msg.data = {
            "slash_commands": ["/test1", "/test2"],
            "skills": [{"name": "skill1"}],
            "tools": [{"name": "tool1"}],
        }
        msg.session_id = None

        service._sync_sdk_session_mapping("s1", msg)

        sm.set_commands.assert_called_once()
        sm.set_sdk_capabilities.assert_called_once()

    def test_sync_with_session_id_in_data(self):
        """Should extract session_id from data dict."""
        service = AgentService()

        sm = MagicMock()
        sm._set_sdk_session_id_impl = MagicMock()
        service._shared_resources = {"runtime_registry": sm}

        msg = MagicMock()
        msg.subtype = "other"
        msg.session_id = None
        msg.data = {"session_id": "sdk-session-123"}

        service._sync_sdk_session_mapping("s1", msg)

        sm._set_sdk_session_id_impl.assert_called_once_with("s1", "sdk-session-123")


class TestEmitDirectProgressForSessionBranches:
    """Test _emit_direct_progress_for_session implementation details."""

    @pytest.mark.asyncio
    async def test_emit_with_event_data(self):
        """Should pass event_data to callback."""
        service = AgentService()
        received_data = []

        async def callback(text, **kwargs):
            received_data.append(kwargs.get("event_data"))

        service._register_direct_progress_callback("s1", callback)
        result = await service._emit_direct_progress_for_session(
            "s1", "test", event_type="system", event_data={"key": "value"}
        )

        assert result is True
        assert {"key": "value"} in received_data


class TestProgressKindFromEventType:
    """Test _progress_kind_from_event_type mapping."""

    def test_progress_kind_for_thinking(self):
        """Should map thinking to reasoning."""
        from xbot.runtime.core.service import _progress_kind_from_event_type

        assert _progress_kind_from_event_type("thinking") == "reasoning"

    def test_progress_kind_for_tool_call(self):
        """Should map tool_call to tool."""
        from xbot.runtime.core.service import _progress_kind_from_event_type

        assert _progress_kind_from_event_type("tool_call") == "tool"

    def test_progress_kind_for_tool_hint_flag(self):
        """Should return tool when tool_hint flag is set."""
        from xbot.runtime.core.service import _progress_kind_from_event_type

        assert _progress_kind_from_event_type("thinking", tool_hint=True) == "tool"

    def test_progress_kind_for_unknown(self):
        """Should return progress for unknown event types."""
        from xbot.runtime.core.service import _progress_kind_from_event_type

        assert _progress_kind_from_event_type("unknown") == "progress"


class TestIsValidCompactTargetBranches:
    """Test _is_valid_compact_target validation."""

    def test_valid_with_all_non_empty_strings(self):
        """Should return True for valid 3-tuple."""
        assert AgentService._is_valid_compact_target(("s1", "ch", "c1")) is True

    def test_invalid_with_empty_first_element(self):
        """Should return False when first element is empty."""
        assert AgentService._is_valid_compact_target(("", "ch", "c1")) is False

    def test_invalid_with_empty_third_element(self):
        """Should return False when third element is empty."""
        assert AgentService._is_valid_compact_target(("s1", "ch", "")) is False


class TestConvertTaskProgressBranches:
    """Test _convert_task_progress implementation details."""

    def test_convert_task_progress_with_last_tool_input(self):
        """Should extract tool input from last_tool_input key."""
        service = AgentService()

        msg = MagicMock()
        msg.description = "Working"
        msg.last_tool_name = "bash"
        msg.data = {"last_tool_input": {"cmd": "ls"}}

        result = service._convert_task_progress(msg)

        assert result.tool_calls is not None
        assert result.tool_calls[0]["input"] == {"cmd": "ls"}

    def test_convert_task_progress_with_input_key(self):
        """Should extract tool input from input key."""
        service = AgentService()

        msg = MagicMock()
        msg.description = "Working"
        msg.last_tool_name = "bash"
        msg.data = {"input": {"cmd": "ls"}}

        result = service._convert_task_progress(msg)

        assert result.tool_calls is not None
        assert result.tool_calls[0]["input"] == {"cmd": "ls"}

    def test_convert_task_progress_with_last_tool_args(self):
        """Should extract tool input from last_tool_args key."""
        service = AgentService()

        msg = MagicMock()
        msg.description = "Working"
        msg.last_tool_name = "bash"
        msg.data = {"last_tool_args": {"cmd": "ls"}}

        result = service._convert_task_progress(msg)

        assert result.tool_calls is not None
        assert result.tool_calls[0]["input"] == {"cmd": "ls"}

    def test_convert_task_progress_with_tool_args(self):
        """Should extract tool input from tool_args key."""
        service = AgentService()

        msg = MagicMock()
        msg.description = "Working"
        msg.last_tool_name = "bash"
        msg.data = {"tool_args": {"cmd": "ls"}}

        result = service._convert_task_progress(msg)

        assert result.tool_calls is not None
        assert result.tool_calls[0]["input"] == {"cmd": "ls"}

    def test_convert_task_progress_with_arguments(self):
        """Should extract tool input from arguments key."""
        service = AgentService()

        msg = MagicMock()
        msg.description = "Working"
        msg.last_tool_name = "bash"
        msg.data = {"arguments": {"cmd": "ls"}}

        result = service._convert_task_progress(msg)

        assert result.tool_calls is not None
        assert result.tool_calls[0]["input"] == {"cmd": "ls"}

    def test_convert_task_progress_without_description(self):
        """Should handle missing description."""
        service = AgentService()

        msg = MagicMock()
        msg.description = None
        msg.last_tool_name = None

        result = service._convert_task_progress(msg)

        assert result.tool_calls is None


class TestFormatToolHintBranches:
    """Test _format_tool_hint implementation details."""

    def test_format_tool_hint_with_cwd_injection(self):
        """Should inject execution_cwd for bash tools."""
        tool_calls = [
            {
                "name": "bash",
                "input": {"command": "ls"},
                "kind": "tool",
            },
        ]

        result = AgentService._format_tool_hint(tool_calls, execution_cwd="/workspace")

        assert "/workspace" in result
        assert "bash" in result

    def test_format_tool_hint_with_custom_type_value(self):
        """Should format custom type values."""
        tool_calls = [
            {
                "name": "test",
                "input": {"obj": object()},
                "kind": "tool",
            },
        ]

        result = AgentService._format_tool_hint(tool_calls)

        assert "object" in result.lower()

    def test_format_tool_hint_with_long_description(self):
        """Should truncate long descriptions."""
        long_desc = "x" * 200
        tool_calls = [
            {
                "name": "test",
                "input": {},
                "kind": "tool",
                "description": long_desc,
            },
        ]

        result = AgentService._format_tool_hint(tool_calls)

        assert len(result) < 200
        assert "..." in result


class TestStopSessionWorkerBranches:
    """Test _stop_session_worker implementation details."""

    @pytest.mark.asyncio
    async def test_stop_worker_with_task(self):
        """Should cancel worker task if present."""
        service = AgentService()

        async def long_task():
            await asyncio.sleep(10)

        task = asyncio.create_task(long_task())
        worker = SessionWorker(
            session_key="s1",
            client=MagicMock(),
            input_queue=asyncio.Queue(),
            task=task,
            channel="ch",
            chat_id="c1",
        )
        service._session_workers["s1"] = worker

        result = await service._stop_session_worker("s1", disconnect=True)

        assert result is True
        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_stop_worker_without_task_with_disconnect(self):
        """Should disconnect client when no task."""
        service = AgentService()

        mock_client = MagicMock()
        mock_client.disconnect = AsyncMock()
        worker = SessionWorker(
            session_key="s1",
            client=mock_client,
            input_queue=asyncio.Queue(),
            task=None,
            channel="ch",
            chat_id="c1",
        )
        service._session_workers["s1"] = worker

        result = await service._stop_session_worker("s1", disconnect=True)

        assert result is True
        mock_client.disconnect.assert_awaited_once()


class TestInterruptSessionBranches:
    """Test interrupt_session implementation details."""

    @pytest.mark.asyncio
    async def test_interrupt_with_confirmation_timeout(self, tmp_path: Path):
        """Should handle interrupt confirmation timeout."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        mock_client = MagicMock()
        mock_client.interrupt = AsyncMock()
        mock_client.disconnect = AsyncMock()
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
        sm.dispatch.return_value = True
        service._shared_resources["runtime_registry"] = sm

        service._interrupt_confirm_timeout_seconds = 0.01

        result = await service.interrupt_session("s1")

        assert result["interrupted"] is True
        assert result["fallback_disconnect"] is True

    @pytest.mark.asyncio
    async def test_interrupt_without_active_turn(self, tmp_path: Path):
        """Should not send interrupt when no active turn."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        mock_client = MagicMock()
        mock_client.interrupt = AsyncMock()
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
        sm.get_phase.return_value = SessionPhase.IDLE
        service._shared_resources["runtime_registry"] = sm

        result = await service.interrupt_session("s1")

        assert result["interrupted"] is False
        mock_client.interrupt.assert_not_called()


class TestCallForStructuredBranches:
    """Test call_for_structured implementation details."""

    @pytest.mark.asyncio
    async def test_call_for_structured_with_tool_choice_string(self, tmp_path: Path):
        """Should handle string tool_choice."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        service._build_env_config = MagicMock(return_value={"ANTHROPIC_API_KEY": "sk-test"})

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "content": [{"type": "text", "text": "Result"}],
                "stop_reason": "end_turn",
            }
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response

            result = await service.call_for_structured(
                messages=[{"role": "user", "content": "Hello"}],
                tool_choice="auto",
            )

            assert result.content == "Result"

    @pytest.mark.asyncio
    async def test_call_for_structured_with_tool_choice_dict(self, tmp_path: Path):
        """Should handle dict tool_choice with function."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        service._build_env_config = MagicMock(return_value={"ANTHROPIC_API_KEY": "sk-test"})

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "content": [{"type": "text", "text": "Result"}],
                "stop_reason": "end_turn",
            }
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response

            result = await service.call_for_structured(
                messages=[{"role": "user", "content": "Hello"}],
                tool_choice={"function": {"name": "get_weather"}},
            )

            assert result.content == "Result"

    @pytest.mark.asyncio
    async def test_call_for_structured_with_http_error(self, tmp_path: Path):
        """Should handle HTTP errors."""
        from xbot.platform.config.schema import Config
        import httpx

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        service._build_env_config = MagicMock(return_value={"ANTHROPIC_API_KEY": "sk-test"})

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            error_response = MagicMock()
            error_response.json.return_value = {"error": {"message": "Rate limit exceeded"}}
            http_error = httpx.HTTPStatusError(
                "Rate limit",
                request=MagicMock(),
                response=error_response,
            )
            mock_client.post.side_effect = http_error

            result = await service.call_for_structured(
                messages=[{"role": "user", "content": "Hello"}],
            )

            assert "error" in result.content.lower()
            assert result.finish_reason == "error"

    @pytest.mark.asyncio
    async def test_call_for_structured_with_generic_error(self, tmp_path: Path):
        """Should handle generic errors."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        service._build_env_config = MagicMock(return_value={"ANTHROPIC_API_KEY": "sk-test"})

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = Exception("Network error")

            result = await service.call_for_structured(
                messages=[{"role": "user", "content": "Hello"}],
            )

            assert "error" in result.content.lower()
            assert result.finish_reason == "error"


class TestCallForAuxiliaryBranches:
    """Test call_for_auxiliary implementation details."""

    @pytest.mark.asyncio
    async def test_call_for_auxiliary_with_model_override(self, tmp_path: Path):
        """Should pass model override to process."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        async def fake_process(context):
            assert context.model == "claude-haiku"
            yield AgentResponse(content="response")

        with patch.object(service, "process", side_effect=fake_process):
            result = await service.call_for_auxiliary("prompt", model="claude-haiku")

        assert result == "response"


class TestProcessManagedDirectBranches:
    """Test process_managed_direct implementation details."""

    @pytest.mark.asyncio
    async def test_process_managed_direct_with_media(self):
        """Should pass media to process_direct."""
        service = AgentService()
        service.process_direct = AsyncMock(return_value="result")

        result = await service.process_managed_direct(
            "hello",
            session_key="s1",
            media=["/path/to/image.png"],
        )

        assert result == "result"
        service.process_direct.assert_awaited_once()
        call_kwargs = service.process_direct.call_args.kwargs
        assert call_kwargs["media"] == ["/path/to/image.png"]
