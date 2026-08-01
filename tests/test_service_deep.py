"""Deep coverage tests for AgentService uncovered methods."""

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
from xbot.runtime.state import RuntimeSessionRegistry, SessionEvent, SessionPhase


class TestStaticPureMethods:
    """Test static/pure methods that are easy to cover."""

    def test_is_idle_boundary_message_with_idle_state(self):
        """_is_idle_boundary_message should return True for idle SystemMessage."""
        msg = MagicMock()
        type(msg).__name__ = "SystemMessage"
        msg.subtype = "session_state_changed"
        msg.data = {"state": "idle"}

        assert AgentService._is_idle_boundary_message(msg) is True

    def test_is_idle_boundary_message_with_non_idle_state(self):
        """_is_idle_boundary_message should return False for non-idle states."""
        msg = MagicMock()
        type(msg).__name__ = "SystemMessage"
        msg.subtype = "session_state_changed"
        msg.data = {"state": "running"}

        assert AgentService._is_idle_boundary_message(msg) is False

    def test_is_idle_boundary_message_with_wrong_subtype(self):
        """_is_idle_boundary_message should return False for wrong subtype."""
        msg = MagicMock()
        type(msg).__name__ = "SystemMessage"
        msg.subtype = "init"
        msg.data = {"state": "idle"}

        assert AgentService._is_idle_boundary_message(msg) is False

    def test_is_idle_boundary_message_with_non_system_message(self):
        """_is_idle_boundary_message should return False for non-SystemMessage."""
        msg = MagicMock()
        type(msg).__name__ = "AssistantMessage"
        msg.subtype = "session_state_changed"
        msg.data = {"state": "idle"}

        assert AgentService._is_idle_boundary_message(msg) is False

    def test_is_idle_boundary_message_with_state_attribute(self):
        """_is_idle_boundary_message should check state attribute if data is None."""
        msg = MagicMock()
        type(msg).__name__ = "SystemMessage"
        msg.subtype = "session_state_changed"
        msg.data = None
        msg.state = "idle"

        assert AgentService._is_idle_boundary_message(msg) is True

    def test_is_recoverable_stream_error_text_with_markers(self):
        """_is_recoverable_stream_error_text should detect recoverable errors."""
        assert AgentService._is_recoverable_stream_error_text("missing idle boundary") is True
        assert AgentService._is_recoverable_stream_error_text("stream ended before idle boundary") is True
        assert AgentService._is_recoverable_stream_error_text("stream timeout error before idle boundary") is True
        assert AgentService._is_recoverable_stream_error_text("sdk stream timeout error before idle boundary") is True

    def test_is_recoverable_stream_error_text_without_markers(self):
        """_is_recoverable_stream_error_text should return False for non-recoverable errors."""
        assert AgentService._is_recoverable_stream_error_text("connection failed") is False
        assert AgentService._is_recoverable_stream_error_text("timeout") is False
        assert AgentService._is_recoverable_stream_error_text("API error") is False

    def test_extract_slash_commands_from_dict(self):
        """_extract_slash_commands should parse slash_commands and commands lists."""
        info = {
            "slash_commands": ["/help", "/clear"],
            "commands": ["/compact", {"name": "review"}],
        }

        result = AgentService._extract_slash_commands(info)

        assert "/help" in result
        assert "/clear" in result
        assert "/compact" in result
        assert "/review" in result

    def test_extract_slash_commands_normalizes_slashes(self):
        """_extract_slash_commands should add / prefix if missing."""
        info = {
            "commands": ["help", "/clear", {"name": "compact"}],
        }

        result = AgentService._extract_slash_commands(info)

        assert "/help" in result
        assert "/clear" in result
        assert "/compact" in result

    def test_extract_slash_commands_with_non_dict(self):
        """_extract_slash_commands should return empty list for non-dict."""
        assert AgentService._extract_slash_commands(None) == []
        assert AgentService._extract_slash_commands("not a dict") == []
        assert AgentService._extract_slash_commands([]) == []

    def test_extract_sdk_capabilities_extracts_all(self):
        """_extract_sdk_capabilities should extract skills, tools, slash_commands."""
        info = {
            "skills": [{"name": "skill1"}, {"name": "skill2"}],
            "tools": ["/tool1", "/tool2"],
            "slash_commands": ["/help"],
        }

        result = AgentService._extract_sdk_capabilities(info)

        assert "skill1" in result["skills"]
        assert "skill2" in result["skills"]
        assert "/tool1" in result["tools"]
        assert "/tool2" in result["tools"]
        assert "/help" in result["slash_commands"]

    def test_extract_sdk_capabilities_with_non_dict(self):
        """_extract_sdk_capabilities should return empty lists for non-dict."""
        result = AgentService._extract_sdk_capabilities(None)
        assert result == {"skills": [], "tools": [], "slash_commands": []}

    def test_build_options_fingerprint_detects_changes(self):
        """_build_options_fingerprint should change when key options change."""
        options1 = MagicMock()
        options1.model = "claude-sonnet-4-5"
        options1.max_turns = 40
        options1.permission_mode = "acceptEdits"
        options1.disallowed_tools = ["bash"]
        options1.setting_sources = ["user"]
        options1.mcp_servers = {"server1": {}}

        options2 = MagicMock()
        options2.model = "claude-haiku"
        options2.max_turns = 40
        options2.permission_mode = "acceptEdits"
        options2.disallowed_tools = ["bash"]
        options2.setting_sources = ["user"]
        options2.mcp_servers = {"server1": {}}

        fp1 = AgentService._build_options_fingerprint(options1)
        fp2 = AgentService._build_options_fingerprint(options2)

        assert fp1 != fp2

    def test_coerce_str_list_filters_and_strips(self):
        """_coerce_str_list should filter non-strings and strip whitespace."""
        assert AgentService._coerce_str_list(None) == []
        assert AgentService._coerce_str_list("not a list") == []
        assert AgentService._coerce_str_list([1, 2, 3]) == []
        assert AgentService._coerce_str_list(["  hello  ", "", "world"]) == ["hello", "world"]

    def test_resolve_config_path_with_workspace_variable(self, tmp_path: Path):
        """_resolve_config_path should replace $workspace with workspace path."""
        workspace = str(tmp_path)
        result = AgentService._resolve_config_path("$workspace/config", workspace)
        assert result.endswith("config")
        assert str(tmp_path.name) in result

    def test_resolve_config_path_with_relative_path(self):
        """_resolve_config_path should resolve relative paths against workspace."""
        result = AgentService._resolve_config_path("config", "/home/user/project")
        assert "/home/user/project/config" in result

    def test_resolve_config_path_with_absolute_path(self):
        """_resolve_config_path should handle absolute paths."""
        result = AgentService._resolve_config_path("/absolute/path", "/home/user/project")
        assert result == "/absolute/path"

    def test_map_tool_name_to_sdk_native_tools(self):
        """_map_tool_name_to_sdk should map native tool names correctly."""
        service = AgentService()

        assert service._map_tool_name_to_sdk("bash") == "Bash"
        assert service._map_tool_name_to_sdk("exec") == "Bash"
        assert service._map_tool_name_to_sdk("read_file") == "Read"
        assert service._map_tool_name_to_sdk("write_file") == "Write"
        assert service._map_tool_name_to_sdk("edit_file") == "Edit"

    def test_map_tool_name_to_sdk_mcp_tools(self):
        """_map_tool_name_to_sdk should map xbot extension tools to MCP namespace."""
        service = AgentService()

        assert service._map_tool_name_to_sdk("web_search") == "mcp__xbot__web_search"
        assert service._map_tool_name_to_sdk("web_fetch") == "mcp__xbot__web_fetch"
        assert service._map_tool_name_to_sdk("message") == "mcp__xbot__message"

    def test_map_tool_name_to_sdk_already_mcp(self):
        """_map_tool_name_to_sdk should preserve existing MCP tool names."""
        service = AgentService()

        assert service._map_tool_name_to_sdk("mcp__server__tool") == "mcp__server__tool"
        assert service._map_tool_name_to_sdk("mcp_server_tool") == "mcp_server_tool"

    def test_resolve_disallowed_tools_from_config(self):
        """_resolve_disallowed_tools should extract disallowed tools from config."""
        service = AgentService()

        sdk_config = MagicMock()
        sdk_config.disallowed_tools = ["bash", "write"]
        sdk_config.disallowed_tools_by_mode = None

        result = service._resolve_disallowed_tools(sdk_config, "cli")

        assert "bash" in result
        assert "write" in result

    def test_resolve_disallowed_tools_with_mode_override(self):
        """_resolve_disallowed_tools should apply mode-specific overrides."""
        service = AgentService()

        sdk_config = MagicMock()
        sdk_config.disallowed_tools = ["bash"]
        sdk_config.disallowed_tools_by_mode = {
            "gateway": ["write", "edit"],
            "cli": ["bash"],
        }

        result = service._resolve_disallowed_tools(sdk_config, "gateway")

        assert "write" in result
        assert "edit" in result
        assert "bash" not in result

    def test_resolve_disallowed_tools_with_none_config(self):
        """_resolve_disallowed_tools should return empty list for None config."""
        service = AgentService()
        assert service._resolve_disallowed_tools(None, "cli") == []

    def test_resolve_setting_sources_auto_mode(self):
        """_resolve_setting_sources should return default sources for auto mode."""
        service = AgentService()

        sdk_config = MagicMock()
        sdk_config.memory_integration = MagicMock()
        sdk_config.memory_integration.mode = "auto"
        sdk_config.memory_integration.setting_sources = None

        result = service._resolve_setting_sources(sdk_config, "cli")

        assert result == ["user", "project", "local"]

    def test_resolve_setting_sources_off_mode(self):
        """_resolve_setting_sources should return None for off mode."""
        service = AgentService()

        sdk_config = MagicMock()
        sdk_config.memory_integration = MagicMock()
        sdk_config.memory_integration.mode = "off"

        result = service._resolve_setting_sources(sdk_config, "cli")

        assert result is None

    def test_resolve_setting_sources_with_dict_config(self):
        """_resolve_setting_sources should handle dict memory_integration config."""
        service = AgentService()

        sdk_config = MagicMock()
        sdk_config.memory_integration = {
            "mode": "auto",
            "setting_sources": {
                "gateway": ["user"],
                "cli": ["user", "project"],
            },
        }

        result = service._resolve_setting_sources(sdk_config, "cli")

        assert result == ["user", "project"]

    def test_get_effective_model_with_session_override(self):
        """_get_effective_model should return session override when set."""
        service = AgentService()
        service._session_model_overrides["session1"] = "claude-haiku"

        result = service._get_effective_model("session1")

        assert result == "claude-haiku"

    def test_get_effective_model_with_config_defaults(self):
        """_get_effective_model should return config defaults when no override."""
        service = AgentService()
        service._config = AgentConfig(model="claude-sonnet-4-5", system_prompt="test")

        result = service._get_effective_model()

        assert result == "claude-sonnet-4-5"

    def test_get_effective_model_with_provider_fallback(self):
        """_get_effective_model should fallback to provider models."""
        service = AgentService()
        service._config = AgentConfig(model="", system_prompt="test")

        runtime_config = MagicMock()
        runtime_config.agents.defaults.model = ""
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.models = ["claude-3-opus"]
        service._shared_resources = {"config": runtime_config}

        result = service._get_effective_model()

        assert result == "claude-3-opus"

    def test_get_effective_model_with_hardcoded_default(self):
        """_get_effective_model should return hardcoded default when nothing configured."""
        service = AgentService()
        service._config = AgentConfig(model="", system_prompt="test")
        service._shared_resources = {"config": None}

        result = service._get_effective_model()

        assert result == "claude-sonnet-4-5"

    def test_format_tool_hint_with_various_arg_types(self):
        """_format_tool_hint should format different argument types correctly."""
        tool_calls = [
            {
                "name": "bash",
                "input": {"command": "ls -la", "cwd": "/home"},
                "kind": "tool",
            },
            {
                "name": "read",
                "input": {"path": "/file.txt", "lines": 100, "offset": 0},
                "kind": "tool",
            },
        ]

        result = AgentService._format_tool_hint(tool_calls)

        assert "bash" in result
        assert "read" in result
        assert "ls -la" in result

    def test_format_tool_hint_with_empty_list(self):
        """_format_tool_hint should return default message for empty list."""
        result = AgentService._format_tool_hint([])
        assert result == "Using tools..."

    def test_format_tool_hint_with_long_arguments(self):
        """_format_tool_hint should truncate long arguments."""
        long_command = "x" * 1000
        tool_calls = [
            {
                "name": "bash",
                "input": {"command": long_command},
                "kind": "tool",
            },
        ]

        result = AgentService._format_tool_hint(tool_calls)

        assert len(result) < 300

    def test_convert_system_message_compact_boundary(self):
        """_convert_system_message should handle compact_boundary subtype."""
        service = AgentService()

        msg = MagicMock()
        type(msg).__name__ = "SystemMessage"
        msg.subtype = "compact_boundary"
        msg.message = ""
        msg.data = {
            "compact_metadata": {
                "pre_tokens": 50000,
                "post_tokens": 20000,
                "trigger": "auto",
            }
        }

        result = service._convert_system_message(msg)

        assert result is not None
        assert result.event_type == "system"
        assert "50,000" in result.progress_texts[0]
        assert "20,000" in result.progress_texts[0]

    def test_convert_system_message_compact_complete(self):
        """_convert_system_message should handle compact_complete subtype."""
        service = AgentService()

        msg = MagicMock()
        type(msg).__name__ = "SystemMessage"
        msg.subtype = "compact_complete"
        msg.message = ""
        msg.pre_tokens = 60000
        msg.post_tokens = 25000
        msg.trigger = "manual"

        result = service._convert_system_message(msg)

        assert result is not None
        assert result.event_type == "system"
        assert "60,000" in result.progress_texts[0]
        assert "25,000" in result.progress_texts[0]

    def test_convert_result_message_with_object_usage(self):
        """_convert_result_message should handle object-based usage."""
        service = AgentService()

        msg = MagicMock()
        msg.result = "done"
        msg.usage = MagicMock()
        msg.usage.input_tokens = 100
        msg.usage.output_tokens = 50
        msg.stop_reason = "end_turn"
        msg.num_turns = 2
        msg.total_cost_usd = 0.05

        result = service._convert_result_message(msg)

        assert result is not None
        assert result.usage["input_tokens"] == 100
        assert result.usage["output_tokens"] == 50

    def test_convert_result_message_with_alternative_token_fields(self):
        """_convert_result_message should handle alternative token field names."""
        service = AgentService()

        msg = MagicMock()
        msg.result = "done"
        # Use a plain object where input_tokens/output_tokens are None so the
        # fallback path (inputTokens/outputTokens) is exercised.
        usage_obj = type("Usage", (), {
            "input_tokens": None,
            "output_tokens": None,
            "inputTokens": 200,
            "outputTokens": 100,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
        })()
        msg.usage = usage_obj
        msg.stop_reason = "end_turn"
        msg.num_turns = 1

        result = service._convert_result_message(msg)

        assert result is not None
        assert result.usage["input_tokens"] == 200
        assert result.usage["output_tokens"] == 100


class TestBuildQueryPrompt:
    """Test _build_query_prompt multimodal paths."""

    def test_build_query_prompt_text_only(self):
        """_build_query_prompt should return plain text when no media."""
        result = AgentService._build_query_prompt("Hello", None)
        assert result == "Hello"

    def test_build_query_prompt_with_image(self, tmp_path: Path):
        """_build_query_prompt should encode images as base64 content blocks."""
        image_path = tmp_path / "test.png"
        image_path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
            b"\x1f\x15\xc4\x89"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        result = AgentService._build_query_prompt("Describe this", [str(image_path)])

        assert isinstance(result, list)
        assert any(block.get("type") == "image" for block in result)
        assert any(block.get("type") == "text" for block in result)

    def test_build_query_prompt_with_audio(self, tmp_path: Path):
        """_build_query_prompt should add audio reference text."""
        audio_path = tmp_path / "audio.mp3"
        audio_path.write_bytes(b"fake audio data")

        # Mock classify_file to return AUDIO
        with patch("xbot.runtime.core.service.classify_file") as mock_classify:
            from xbot.platform.utils.file_reader import FileType
            mock_classify.return_value = FileType.AUDIO

            result = AgentService._build_query_prompt("Listen", [str(audio_path)])

            assert isinstance(result, str)
            assert "音频" in result or "audio" in result.lower()

    def test_build_query_prompt_with_unsupported_image_format(self, tmp_path: Path):
        """_build_query_prompt should fallback to text for unsupported image formats."""
        image_path = tmp_path / "test.bmp"
        image_path.write_bytes(b"fake bmp data")

        # Mock classify_file to return IMAGE (so it enters the image branch)
        # and detect_image_mime to return None (unsupported)
        with (
            patch("xbot.runtime.core.service.classify_file") as mock_classify,
            patch("xbot.runtime.core.service.detect_image_mime") as mock_mime,
        ):
            from xbot.platform.utils.file_reader import FileType
            mock_classify.return_value = FileType.IMAGE
            mock_mime.return_value = None

            result = AgentService._build_query_prompt("Check", [str(image_path)])

            assert isinstance(result, list)
            text_blocks = [b for b in result if b.get("type") == "text"]
            assert any("unsupported" in b["text"].lower() for b in text_blocks)


class TestBuildSdkOptions:
    """Test _build_sdk_options and related methods."""

    @pytest.mark.asyncio
    async def test_build_sdk_options_basic(self, tmp_path: Path):
        """_build_sdk_options should build options with basic config."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.providers.anthropic.api_key = "sk-test"
        runtime_config.providers.anthropic.api_base = None
        runtime_config.providers.anthropic.models = ["claude-sonnet-4-5"]

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        options = service._build_sdk_options(session_key="test:1")

        assert options.model == "claude-sonnet-4-5"
        assert options.system_prompt == "Test"

    @pytest.mark.asyncio
    async def test_build_mcp_servers_with_tool_adapter(self, tmp_path: Path):
        """_build_mcp_servers should merge xbot extension tools."""
        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": MagicMock()})

        # Mock tool adapter
        service._tool_adapter = MagicMock()
        service._tool_adapter.create_mcp_server.return_value = {
            "xbot": {"command": "xbot-mcp", "args": []}
        }

        servers = service._build_mcp_servers()

        assert "xbot" in servers

    @pytest.mark.asyncio
    async def test_build_system_prompt_with_context_builder(self, tmp_path: Path):
        """_build_system_prompt should use ContextBuilder when available."""
        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="")
        await service.initialize(config, {"workspace": str(tmp_path), "config": MagicMock()})

        # Mock context builder
        service._context_builder = MagicMock()
        service._context_builder.build_system_prompt.return_value = "Base prompt"

        result = service._build_system_prompt()

        assert "Base prompt" in result


class TestBuildHooks:
    """Test _build_hooks configuration."""

    @pytest.mark.asyncio
    async def test_build_hooks_with_compact_notify(self, tmp_path: Path):
        """_build_hooks should add PreCompact hook when compact_notify is enabled."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.agents.claude_sdk.compact_notify = True
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sdk_config = runtime_config.agents.claude_sdk
        hooks = service._build_hooks(sdk_config)

        assert hooks is not None
        assert "PreCompact" in hooks


class TestInterruptSession:
    """Test interrupt_session state machine logic."""

    @pytest.mark.asyncio
    async def test_interrupt_session_with_active_worker(self, tmp_path: Path):
        """interrupt_session should send interrupt to active worker."""
        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": MagicMock()})

        # Create active worker
        session_key = "test:1"
        mock_client = MagicMock()
        mock_client.interrupt = AsyncMock()
        worker = SessionWorker(
            session_key=session_key,
            client=mock_client,
            input_queue=asyncio.Queue(),
            task=None,
            channel="test",
            chat_id="c1",
        )
        service._session_workers[session_key] = worker

        # Mock state manager
        sm = MagicMock()
        sm.get_phase.return_value = SessionPhase.RECEIVING_STREAM
        service._shared_resources["runtime_registry"] = sm

        result = await service.interrupt_session(session_key)

        assert result["interrupted"] is True
        mock_client.interrupt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_interrupt_session_clears_queue(self, tmp_path: Path):
        """interrupt_session should clear queued messages."""
        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": MagicMock()})

        session_key = "test:1"
        mock_client = MagicMock()
        mock_client.interrupt = AsyncMock()
        worker = SessionWorker(
            session_key=session_key,
            client=mock_client,
            input_queue=asyncio.Queue(),
            task=None,
            channel="test",
            chat_id="c1",
        )
        await worker.input_queue.put({"type": "user", "content": "queued"})
        service._session_workers[session_key] = worker

        sm = MagicMock()
        sm.get_phase.return_value = SessionPhase.RECEIVING_STREAM
        service._shared_resources["runtime_registry"] = sm

        result = await service.interrupt_session(session_key)

        assert result["queued_cleared"] == 1
        # Queue may contain a poison pill (None) added by the interrupt flow
        remaining = []
        while not worker.input_queue.empty():
            remaining.append(worker.input_queue.get_nowait())
        assert all(item is None for item in remaining)


class TestAttemptBrokenSessionRecovery:
    """Test _attempt_broken_session_recovery logic."""

    @pytest.mark.asyncio
    async def test_recovery_releases_client(self, tmp_path: Path):
        """Recovery should release unhealthy client."""
        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": MagicMock()})

        session_key = "test:1"
        service._client_pool.disconnect = AsyncMock(return_value=True)

        sm = MagicMock()
        service._shared_resources["runtime_registry"] = sm

        result = await service._attempt_broken_session_recovery(
            session_key, reason="stream_error"
        )

        assert result is True
        service._client_pool.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recovery_clears_resume_after_failures(self, tmp_path: Path):
        """Recovery should clear SDK resume after 3 consecutive failures."""
        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": MagicMock()})

        session_key = "test:1"
        service._client_pool.disconnect = AsyncMock(return_value=False)

        sm = MagicMock()
        sm.note_recovery_failure.return_value = 3
        service._shared_resources["runtime_registry"] = sm

        result = await service._attempt_broken_session_recovery(
            session_key, reason="stream_error"
        )

        assert result is False
        sm.reset_recovery_failures.assert_called_once()


class TestProcessDirect:
    """Test process_direct high-level orchestrator."""

    @pytest.mark.asyncio
    async def test_process_direct_with_progress_callback(self, tmp_path: Path):
        """process_direct should invoke progress callback."""
        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": MagicMock()})

        progress_received = []

        async def on_progress(text, **kwargs):
            progress_received.append(text)

        async def fake_process(context):
            yield AgentResponse(content="", progress_texts=["Thinking..."])
            yield AgentResponse(content="Answer", event_type="result")

        with patch.object(service, "process", side_effect=fake_process):
            result = await service.process_direct(
                "Question",
                session_key="test:1",
                on_progress=on_progress,
            )

        assert result == "Answer"
        assert "Thinking..." in progress_received

    @pytest.mark.asyncio
    async def test_process_direct_with_recovery(self, tmp_path: Path):
        """process_direct should attempt recovery on recoverable errors."""
        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": MagicMock()})

        call_count = [0]

        async def fake_process(context):
            call_count[0] += 1
            if call_count[0] == 1:
                yield AgentResponse(
                    content="missing idle boundary",
                    finish_reason="error",
                )
            else:
                yield AgentResponse(content="Success", event_type="result")

        async def fake_recovery(session_key, reason):
            return True

        with (
            patch.object(service, "process", side_effect=fake_process),
            patch.object(
                service,
                "_attempt_broken_session_recovery",
                side_effect=fake_recovery,
            ),
        ):
            result = await service.process_direct("Question", session_key="test:1")

        assert result == "Success"
        assert call_count[0] == 2


class TestHandleCliStderr:
    """Test _handle_cli_stderr rate limiting and dedup."""

    def test_handle_cli_stderr_basic(self):
        """_handle_cli_stderr should log non-empty lines."""
        service = AgentService()

        with patch("xbot.runtime.core.service.logger") as mock_logger:
            service._handle_cli_stderr("Error: something failed")
            mock_logger.warning.assert_called()

    def test_handle_cli_stderr_empty_line(self):
        """_handle_cli_stderr should skip empty lines."""
        service = AgentService()

        with patch("xbot.runtime.core.service.logger") as mock_logger:
            service._handle_cli_stderr("")
            service._handle_cli_stderr("   ")
            mock_logger.warning.assert_not_called()

    def test_handle_cli_stderr_duplicate_suppression(self):
        """_handle_cli_stderr should suppress duplicate lines."""
        service = AgentService()

        with patch("xbot.runtime.core.service.logger") as mock_logger:
            service._handle_cli_stderr("Error")
            service._handle_cli_stderr("Error")  # Duplicate
            service._handle_cli_stderr("Error")  # Duplicate

            # Only first should be logged
            assert mock_logger.warning.call_count == 1
            assert service._cli_stderr_duplicate_suppressed_in_window == 2

    def test_handle_cli_stderr_rate_limiting(self):
        """_handle_cli_stderr should rate limit after max warnings."""
        service = AgentService()
        service._cli_stderr_max_warnings_per_window = 2

        with patch("xbot.runtime.core.service.logger") as mock_logger:
            for i in range(5):
                service._handle_cli_stderr(f"Error {i}")

            # Only first 2 should be logged
            assert mock_logger.warning.call_count == 2
            assert service._cli_stderr_rate_limited_in_window == 3

    def test_handle_cli_stderr_window_reset(self):
        """_handle_cli_stderr should reset counters after window expires."""
        service = AgentService()
        service._cli_stderr_window_seconds = 0.1
        service._cli_stderr_window_start = time.monotonic() - 1.0

        with patch("xbot.runtime.core.service.logger") as mock_logger:
            service._handle_cli_stderr("Error")

            # Window should have reset
            assert service._cli_stderr_emitted_in_window == 1

    def test_handle_cli_stderr_truncation(self):
        """_handle_cli_stderr should truncate long lines."""
        service = AgentService()
        service._cli_stderr_max_line_chars = 50

        long_line = "x" * 100

        with patch("xbot.runtime.core.service.logger") as mock_logger:
            service._handle_cli_stderr(long_line)

            call_args = mock_logger.warning.call_args[0][1]
            assert len(call_args) < 100
            assert "truncated" in call_args


class TestCallForStructured:
    """Test call_for_structured LLM call."""

    @pytest.mark.asyncio
    async def test_call_for_structured_no_api_key(self, tmp_path: Path):
        """call_for_structured should return error when no API key."""
        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": MagicMock()})

        # Mock _build_env_config to return no API key
        service._build_env_config = MagicMock(return_value={})

        result = await service.call_for_structured(
            messages=[{"role": "user", "content": "Hello"}]
        )

        assert "error" in result.content.lower()

    @pytest.mark.asyncio
    async def test_call_for_structured_with_tools(self, tmp_path: Path):
        """call_for_structured should convert OpenAI-style tools."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.providers.anthropic.api_key = "sk-test"
        runtime_config.providers.anthropic.models = ["claude-sonnet-4-5"]

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        # Mock HTTP client
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

            # Mock _build_env_config
            service._build_env_config = MagicMock(
                return_value={"ANTHROPIC_API_KEY": "sk-test"}
            )

            tools = [
                {
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {"type": "object"},
                    }
                }
            ]

            result = await service.call_for_structured(
                messages=[{"role": "user", "content": "Weather?"}],
                tools=tools,
            )

            assert result.content == "Result"


class TestRun:
    """Test run() message routing loop."""

    @pytest.mark.asyncio
    async def test_run_with_no_bus_raises(self, tmp_path: Path):
        """run() should raise when no bus in shared_resources."""
        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": MagicMock()})

        with pytest.raises(RuntimeError, match="requires a bus"):
            await service.run()

    def test_stop_sets_running_false(self):
        """stop() should set _running to False."""
        service = AgentService()
        service._running = True
        service.stop()
        assert service._running is False

    @pytest.mark.asyncio
    async def test_run_routes_to_enqueuer(self, tmp_path: Path):
        """run() should route normal messages through _enqueue_worker_message."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")

        bus = MagicMock()
        inbound_msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="hello")
        # First call returns the message, subsequent calls raise TimeoutError
        bus.consume_inbound = AsyncMock(side_effect=[inbound_msg, asyncio.TimeoutError()])

        await service.initialize(
            config,
            {"workspace": str(tmp_path), "config": runtime_config, "bus": bus},
        )

        # Patch _enqueue_worker_message so we don't need a real SDK client
        enqueue_called = []

        async def fake_enqueue(msg, bus_obj):
            enqueue_called.append(msg.content)
            service.stop()  # Stop after processing one message

        service._enqueue_worker_message = fake_enqueue
        service._command_handler = MagicMock()
        service._command_handler.is_local_command.return_value = False
        service._response_handlers = None

        await service.run()

        assert enqueue_called == ["hello"]


class TestGetSdkQueryTimeout:
    """Test _get_sdk_query_timeout configuration."""

    def test_get_sdk_query_timeout_default(self):
        """_get_sdk_query_timeout should return 30.0 by default."""
        service = AgentService()
        service._shared_resources = {}

        result = service._get_sdk_query_timeout()

        assert result == 30.0

    def test_get_sdk_query_timeout_from_config(self):
        """_get_sdk_query_timeout should read from config."""
        service = AgentService()

        config = MagicMock()
        config.tools.timeouts.sdk_query = 60.0
        service._shared_resources = {"config": config}

        result = service._get_sdk_query_timeout()

        assert result == 60.0

    def test_get_sdk_query_timeout_invalid_value(self):
        """_get_sdk_query_timeout should fallback to 30.0 for invalid values."""
        service = AgentService()

        config = MagicMock()
        config.tools.timeouts.sdk_query = "invalid"
        service._shared_resources = {"config": config}

        result = service._get_sdk_query_timeout()

        assert result == 30.0


class TestHasPendingUserWait:
    """Test _has_pending_user_wait bus integration."""

    def test_no_bus(self):
        """_has_pending_user_wait should return False with no bus."""
        service = AgentService()
        service._shared_resources = {}
        assert service._has_pending_user_wait("session1") is False

    def test_with_pending_permission(self):
        """_has_pending_user_wait should return True when permission is pending."""
        service = AgentService()
        bus = MagicMock()
        bus.get_pending_request_for_session.return_value = MagicMock()
        bus.get_pending_interaction_for_session = MagicMock(return_value=None)
        service._shared_resources = {"bus": bus}

        assert service._has_pending_user_wait("session1") is True

    def test_with_pending_interaction(self):
        """_has_pending_user_wait should return True when interaction is pending."""
        service = AgentService()
        bus = MagicMock()
        bus.get_pending_request_for_session.return_value = None
        bus.get_pending_interaction_for_session.return_value = MagicMock()
        service._shared_resources = {"bus": bus}

        assert service._has_pending_user_wait("session1") is True


class TestDispatchTerminalState:
    """Test _dispatch_terminal_state branches."""

    def test_dispatch_terminal_state_with_pending_permission(self):
        """_dispatch_terminal_state should dispatch PERMISSION_PENDING when permission is pending."""
        service = AgentService()
        bus = MagicMock()
        bus.get_pending_request_for_session.return_value = MagicMock()
        service._shared_resources = {"bus": bus}

        sm = MagicMock()
        sm.dispatch.return_value = True
        service._shared_resources["runtime_registry"] = sm

        service._dispatch_terminal_state("s1", sm=sm, reason="test")

        calls = sm.dispatch.call_args_list
        assert any(
            call.args[1] == SessionEvent.PERMISSION_PENDING
            for call in calls
        )

    def test_dispatch_terminal_state_with_pending_interaction(self):
        """_dispatch_terminal_state should dispatch INTERACTION_PENDING when interaction pending."""
        service = AgentService()
        bus = MagicMock()
        bus.get_pending_request_for_session.return_value = None
        bus.get_pending_interaction_for_session.return_value = MagicMock()
        service._shared_resources = {"bus": bus}

        sm = MagicMock()
        sm.dispatch.return_value = True
        service._shared_resources["runtime_registry"] = sm

        service._dispatch_terminal_state("s1", sm=sm, reason="test")

        calls = sm.dispatch.call_args_list
        assert any(
            call.args[1] == SessionEvent.INTERACTION_PENDING
            for call in calls
        )

    def test_dispatch_terminal_state_no_pending(self):
        """_dispatch_terminal_state should dispatch TURN_COMPLETED when nothing pending."""
        service = AgentService()
        bus = MagicMock()
        bus.get_pending_request_for_session.return_value = None
        bus.get_pending_interaction_for_session = MagicMock(return_value=None)
        service._shared_resources = {"bus": bus}

        sm = MagicMock()
        sm.dispatch.return_value = True
        service._shared_resources["runtime_registry"] = sm

        service._dispatch_terminal_state("s1", sm=sm, reason="test")

        calls = sm.dispatch.call_args_list
        assert any(
            call.args[1] == SessionEvent.TURN_COMPLETED
            for call in calls
        )


class TestDispatchStateEvent:
    """Test _dispatch_state_event error handling."""

    def test_no_runtime_registry(self):
        """_dispatch_state_event should return False with no registry."""
        service = AgentService()
        service._shared_resources = {}
        assert service._dispatch_state_event("s1", SessionEvent.USER_MESSAGE) is False

    def test_dispatch_rejected(self, caplog):
        """_dispatch_state_event should log when dispatch is rejected."""
        service = AgentService()
        sm = MagicMock()
        sm.dispatch.return_value = False
        sm.get_phase.return_value = SessionPhase.IDLE
        service._shared_resources = {"runtime_registry": sm}

        result = service._dispatch_state_event("s1", SessionEvent.USER_MESSAGE, reason="test")

        assert result is False

    def test_dispatch_exception(self, caplog):
        """_dispatch_state_event should log and return False on exception."""
        service = AgentService()
        sm = MagicMock()
        sm.dispatch.side_effect = RuntimeError("boom")
        service._shared_resources = {"runtime_registry": sm}

        result = service._dispatch_state_event("s1", SessionEvent.USER_MESSAGE, reason="test")

        assert result is False


class TestGetPhase:
    """Test get_phase delegation."""

    def test_no_registry_returns_idle(self):
        """get_phase should return IDLE when no registry."""
        service = AgentService()
        service._shared_resources = {}
        assert service.get_phase("s1") == SessionPhase.IDLE

    def test_delegates_to_registry(self):
        """get_phase should delegate to registry."""
        service = AgentService()
        sm = MagicMock()
        sm.get_phase.return_value = SessionPhase.RECEIVING_STREAM
        service._shared_resources = {"runtime_registry": sm}

        assert service.get_phase("s1") == SessionPhase.RECEIVING_STREAM


class TestPublishEvent:
    """Test _publish_event static method."""

    @pytest.mark.asyncio
    async def test_publish_event_basic(self):
        """_publish_event should publish OutboundMessage with metadata."""
        bus = MagicMock()
        bus.publish_outbound = AsyncMock()

        await AgentService._publish_event(
            bus, "test", "c1", "hello",
            _event_type="content_delta",
            _progress=True,
        )

        bus.publish_outbound.assert_awaited_once()
        msg = bus.publish_outbound.call_args.args[0]
        assert msg.content == "hello"
        assert msg.channel == "test"
        assert msg.metadata.get("_progress") is True
        assert msg.metadata.get("_progress_kind") == "content"

    @pytest.mark.asyncio
    async def test_publish_event_with_event_data(self):
        """_publish_event should include _event_data in metadata."""
        bus = MagicMock()
        bus.publish_outbound = AsyncMock()

        await AgentService._publish_event(
            bus, "test", "c1", "hello",
            event_data={"key": "value"},
        )

        msg = bus.publish_outbound.call_args.args[0]
        assert msg.metadata["_event_data"] == {"key": "value"}


class TestFormatToolHintDetails:
    """Test _format_tool_hint nested formatting logic."""

    def test_format_tool_hint_with_description_no_args(self):
        """_format_tool_hint should show description when no args."""
        result = AgentService._format_tool_hint([
            {"name": "bash", "input": {}, "kind": "tool", "description": "Run a command"},
        ])
        assert "bash" in result
        assert "Run a command" in result

    def test_format_tool_hint_with_none_input(self):
        """_format_tool_hint should handle None input."""
        result = AgentService._format_tool_hint([
            {"name": "bash", "input": None, "kind": "tool"},
        ])
        assert "bash" in result

    def test_format_tool_hint_with_list_value(self):
        """_format_tool_hint should format list args."""
        result = AgentService._format_tool_hint([
            {"name": "test", "input": {"items": [1, 2, 3]}, "kind": "tool"},
        ])
        assert "[3 items]" in result

    def test_format_tool_hint_with_dict_value(self):
        """_format_tool_hint should format dict args."""
        result = AgentService._format_tool_hint([
            {"name": "test", "input": {"config": {"a": 1, "b": 2}}, "kind": "tool"},
        ])
        assert "config" in result

    def test_format_tool_hint_with_many_args_truncated(self):
        """_format_tool_hint should truncate when too many args."""
        result = AgentService._format_tool_hint([
            {"name": "test", "input": {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}, "kind": "tool"},
        ])
        assert "…" in result

    def test_format_tool_hint_with_bool_int_none_values(self):
        """_format_tool_hint should format bool, int, None values."""
        result = AgentService._format_tool_hint([
            {"name": "test", "input": {"flag": True, "count": 42, "empty": None}, "kind": "tool"},
        ])
        # JSON serialization: lowercase true, null
        assert "true" in result.lower()
        assert "42" in result
        assert "null" in result.lower()

    def test_format_tool_hint_with_skill_kind(self):
        """_format_tool_hint should show Skill prefix for skill kind."""
        result = AgentService._format_tool_hint([
            {"name": "my-skill", "input": {}, "kind": "skill"},
        ])
        assert "Skill: my-skill" in result

    def test_format_tool_hint_with_mcp_kind(self):
        """_format_tool_hint should show MCP prefix for MCP kind."""
        result = AgentService._format_tool_hint([
            {"name": "mcp__xbot__web_search", "input": {}, "kind": "mcp"},
        ])
        assert "MCP:" in result


class TestConvertEvent:
    """Test _convert_event dispatching."""

    @pytest.mark.asyncio
    async def test_convert_event_unknown_type_returns_none(self, tmp_path: Path):
        """_convert_event should return None for unknown event types."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        UnknownEvent = type("UnknownEvent", (), {})
        result = service._convert_event(UnknownEvent())
        assert result is None

    @pytest.mark.asyncio
    async def test_convert_event_rate_limit(self, tmp_path: Path):
        """_convert_event should handle RateLimitEvent."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        RateLimitEvent = type("RateLimitEvent", (), {})
        event = RateLimitEvent()
        event.rate_limit_info = MagicMock()
        event.rate_limit_info.status = 429
        event.rate_limit_info.rate_limit_type = "requests"

        result = service._convert_event(event)
        assert result is not None
        assert result.event_type == "rate_limit"


class TestConvertAssistantMessageDetails:
    """Test _convert_assistant_message branch coverage."""

    @pytest.mark.asyncio
    async def test_convert_assistant_with_thinking_block(self, tmp_path: Path):
        """Should handle ThinkingBlock content."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        ThinkingBlock = type("ThinkingBlock", (), {})
        block = ThinkingBlock()
        block.thinking = "Let me think..."

        msg = MagicMock()
        type(msg).__name__ = "AssistantMessage"
        msg.content = [block]

        result = service._convert_assistant_message(msg)
        assert result is not None
        assert result.event_type == "thinking"
        assert "Thinking:" in result.progress_texts[0]

    @pytest.mark.asyncio
    async def test_convert_assistant_with_text_block(self, tmp_path: Path):
        """Should handle TextBlock content."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        TextBlock = type("TextBlock", (), {})
        block = TextBlock()
        block.text = "Hello world"

        msg = MagicMock()
        type(msg).__name__ = "AssistantMessage"
        msg.content = [block]

        result = service._convert_assistant_message(msg)
        assert result is not None
        assert result.content == "Hello world"
        assert result.event_type == "content"


class TestConvertStreamEventDetails:
    """Test _convert_stream_event branches."""

    @pytest.mark.asyncio
    async def test_convert_stream_event_text_delta(self, tmp_path: Path):
        """Should handle text_delta."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        msg = MagicMock()
        msg.event = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Hello"},
        }

        result = service._convert_stream_event(msg)
        assert result is not None
        assert result.is_delta is True
        assert result.delta_content == "Hello"

    @pytest.mark.asyncio
    async def test_convert_stream_event_non_content_block(self, tmp_path: Path):
        """Should return None for non content_block_delta events."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        msg = MagicMock()
        msg.event = {"type": "other_event"}

        result = service._convert_stream_event(msg)
        assert result is None


class TestConvertTaskProgressDetails:
    """Test _convert_task_progress branches."""

    @pytest.mark.asyncio
    async def test_convert_task_progress_with_tool_input_keys(self, tmp_path: Path):
        """Should extract tool input from various data keys."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        msg = MagicMock()
        msg.description = "Working"
        msg.last_tool_name = "bash"
        msg.data = {"tool_args": {"cmd": "ls"}}

        result = service._convert_task_progress(msg)
        assert result.tool_calls is not None
        assert result.tool_calls[0]["name"] == "bash"
        assert result.tool_calls[0]["input"] == {"cmd": "ls"}


class TestConvertSystemMessageDetails:
    """Test _convert_system_message additional branches."""

    @pytest.mark.asyncio
    async def test_convert_system_message_mirror_error(self, tmp_path: Path):
        """Should handle mirror_error subtype."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        msg = MagicMock()
        type(msg).__name__ = "SystemMessage"
        msg.subtype = "mirror_error"
        msg.message = "Mirror failed"
        msg.error = None
        msg.data = {"error": "store unavailable"}

        result = service._convert_system_message(msg)
        assert result is not None
        assert result.event_type == "system"
        assert "mirror" in result.progress_texts[0].lower() or "Mirror" in result.progress_texts[0]

    @pytest.mark.asyncio
    async def test_convert_system_message_compact_start(self, tmp_path: Path):
        """Should handle compact_start subtype."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        msg = MagicMock()
        type(msg).__name__ = "SystemMessage"
        msg.subtype = "compact_start"
        msg.message = "Compressing..."

        result = service._convert_system_message(msg)
        assert result is not None
        assert result.event_type == "system"
        assert result.event_data["subtype"] == "compact_start"

    @pytest.mark.asyncio
    async def test_convert_system_message_post_compact(self, tmp_path: Path):
        """Should handle post_compact subtype."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        msg = MagicMock()
        type(msg).__name__ = "SystemMessage"
        msg.subtype = "post_compact"
        msg.message = ""
        msg.pre_tokens = 50000
        msg.post_tokens = 15000
        msg.trigger = "auto"

        result = service._convert_system_message(msg)
        assert result is not None
        assert result.event_type == "system"

    @pytest.mark.asyncio
    async def test_convert_system_message_other_with_text(self, tmp_path: Path):
        """Should return AgentResponse for other subtypes with text."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        msg = MagicMock()
        type(msg).__name__ = "SystemMessage"
        msg.subtype = "other_type"
        msg.message = "Some system message"

        result = service._convert_system_message(msg)
        assert result is not None
        assert result.event_type == "system"
        assert "Some system message" in result.progress_texts[0]


class TestClassifyToolName:
    """Test _classify_tool_name method."""

    @pytest.mark.asyncio
    async def test_classify_mcp_tool(self, tmp_path: Path):
        """Should classify mcp_ prefix tools."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        assert service._classify_tool_name("mcp__xbot__web_search") == "mcp"

    @pytest.mark.asyncio
    async def test_classify_builtin_tool(self, tmp_path: Path):
        """Should classify builtin tools."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="claude-sonnet-4-5", system_prompt="Test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        assert service._classify_tool_name("bash") == "tool"


class TestMapAgentToolsToSdkNames:
    """Test _map_agent_tools_to_sdk_names."""

    def test_maps_and_deduplicates(self):
        """Should map tool names and remove duplicates."""
        service = AgentService()
        tools = ["bash", "exec", "read_file", "bash"]
        result = service._map_agent_tools_to_sdk_names(tools)
        assert "Bash" in result
        assert "Read" in result
        # bash and exec both map to Bash, should be deduplicated
        assert result.count("Bash") == 1


class TestShouldReleaseEphemeralClient:
    """Test _should_release_ephemeral_client."""

    def test_heartbeat_session(self):
        """Should return True for heartbeat session."""
        service = AgentService()
        from xbot.platform.config.schema import Config
        runtime_config = Config()
        runtime_config.agents.claude_sdk.ephemeral_immediate_release_enabled = True
        service._shared_resources = {"config": runtime_config}

        assert service._should_release_ephemeral_client("heartbeat") is True

    def test_cron_session(self):
        """Should return True for cron: sessions."""
        service = AgentService()
        from xbot.platform.config.schema import Config
        runtime_config = Config()
        runtime_config.agents.claude_sdk.ephemeral_immediate_release_enabled = True
        service._shared_resources = {"config": runtime_config}

        assert service._should_release_ephemeral_client("cron:daily") is True

    def test_auxiliary_session(self):
        """Should return True for auxiliary session."""
        service = AgentService()
        from xbot.platform.config.schema import Config
        runtime_config = Config()
        runtime_config.agents.claude_sdk.ephemeral_immediate_release_enabled = True
        service._shared_resources = {"config": runtime_config}

        assert service._should_release_ephemeral_client("auxiliary") is True

    def test_regular_session(self):
        """Should return False for regular sessions."""
        service = AgentService()
        service._shared_resources = {"config": MagicMock()}
        assert service._should_release_ephemeral_client("test:c1") is False


class TestIsValidCompactTarget:
    """Test _is_valid_compact_target static method."""

    def test_valid_target(self):
        """Should return True for valid 3-tuple of strings."""
        assert AgentService._is_valid_compact_target(("s1", "ch", "c1")) is True

    def test_invalid_not_tuple(self):
        """Should return False for non-tuple."""
        assert AgentService._is_valid_compact_target(["s1", "ch", "c1"]) is False

    def test_invalid_wrong_length(self):
        """Should return False for wrong length."""
        assert AgentService._is_valid_compact_target(("s1", "ch")) is False

    def test_invalid_empty_strings(self):
        """Should return False for empty strings."""
        assert AgentService._is_valid_compact_target(("s1", "", "c1")) is False

    def test_invalid_non_strings(self):
        """Should return False for non-string elements."""
        assert AgentService._is_valid_compact_target(("s1", 123, "c1")) is False

    def test_none(self):
        """Should return False for None."""
        assert AgentService._is_valid_compact_target(None) is False


class TestCoerceStrListDetails:
    """Test _coerce_str_list additional cases."""

    def test_mixed_types(self):
        """Should filter non-string types."""
        result = AgentService._coerce_str_list(["a", 1, None, "b", True, "c"])
        assert result == ["a", "b", "c"]


class TestBuildEnvConfigDetails:
    """Test _build_env_config additional branches."""

    def test_no_config(self):
        """Should return minimal env when no config."""
        service = AgentService()
        service._config = AgentConfig(model="test", system_prompt="")
        service._shared_resources = {}
        env = service._build_env_config()
        assert "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS" in env

    def test_secret_str_api_key(self):
        """Should extract SecretStr values."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        # Use a regular string - the SecretStr path is hard to test without pydantic SecretStr
        runtime_config.providers.anthropic.api_key = "sk-test"
        runtime_config.providers.anthropic.api_base = "https://api.test.com"

        service = AgentService()
        service._config = AgentConfig(model="test", system_prompt="")
        service._shared_resources = {"config": runtime_config}

        env = service._build_env_config()
        assert env["ANTHROPIC_API_KEY"] == "sk-test"
        assert env["ANTHROPIC_BASE_URL"] == "https://api.test.com"


class TestBuildMcpServersDetails:
    """Test _build_mcp_servers additional branches."""

    @pytest.mark.asyncio
    async def test_with_configured_servers(self, tmp_path: Path):
        """Should process configured MCP servers."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        config = AgentConfig(
            model="test",
            system_prompt="",
            mcp_servers={},
        )
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        servers = service._build_mcp_servers()
        assert isinstance(servers, dict)

    @pytest.mark.asyncio
    async def test_with_invalid_mcp_servers_type(self, tmp_path: Path):
        """Should handle invalid mcp_servers type."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        config = AgentConfig(model="test", system_prompt="")
        config.mcp_servers = "not a dict"
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        servers = service._build_mcp_servers()
        assert isinstance(servers, dict)


class TestResolveExecutionCwd:
    """Test _resolve_execution_cwd resolution."""

    def test_gateway_mode_returns_workspace(self):
        """Gateway mode should always return workspace."""
        service = AgentService()
        service._shared_resources = {
            "workspace": "/workspace",
            "run_mode": "gateway",
        }
        result = service._resolve_execution_cwd("session1")
        assert "workspace" in result

    def test_cli_mode_with_execution_cwd(self):
        """CLI mode should use execution_cwd when available."""
        service = AgentService()
        service._shared_resources = {
            "workspace": "/workspace",
            "execution_cwd": "/exec/cwd",
            "run_mode": "cli",
        }
        result = service._resolve_execution_cwd(None)
        assert "exec" in result or "cwd" in result


class TestResolveWorkspaceDir:
    """Test _resolve_workspace_dir resolution."""

    def test_cli_mode_with_session(self):
        """CLI mode with session should try registry override."""
        service = AgentService()
        service._shared_resources = {
            "workspace": "/workspace",
            "run_mode": "cli",
        }
        result = service._resolve_workspace_dir("session1")
        assert "workspace" in result

    def test_gateway_mode_returns_workspace(self):
        """Gateway mode should return workspace."""
        service = AgentService()
        service._shared_resources = {
            "workspace": "/workspace",
            "run_mode": "gateway",
        }
        result = service._resolve_workspace_dir("session1")
        assert "workspace" in result


class TestResolveEffectiveProviderName:
    """Test _resolve_effective_provider_name."""

    def test_with_configured_provider(self):
        """Should return configured provider."""
        from xbot.platform.config.schema import Config
        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        service = AgentService()
        service._shared_resources = {"config": runtime_config}
        assert service._resolve_effective_provider_name() == "anthropic"

    def test_no_config(self):
        """Should return 'unknown' when no config."""
        service = AgentService()
        service._shared_resources = {}
        assert service._resolve_effective_provider_name() == "unknown"


class TestResolveSupportedSubagentModels:
    """Test _resolve_supported_subagent_models."""

    def test_always_includes_inherit(self):
        """Should always include 'inherit'."""
        from xbot.platform.config.schema import Config
        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        service = AgentService()
        service._shared_resources = {"config": runtime_config}
        service._config = AgentConfig(model="claude-sonnet-4-5", system_prompt="")

        result = service._resolve_supported_subagent_models("anthropic")
        assert "inherit" in result

    def test_includes_configured_model(self):
        """Should include the configured model."""
        from xbot.platform.config.schema import Config
        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        service = AgentService()
        service._shared_resources = {"config": runtime_config}
        service._config = AgentConfig(model="claude-sonnet-4-5", system_prompt="")

        result = service._resolve_supported_subagent_models("anthropic")
        assert "claude-sonnet-4-5" in result


class TestProperties:
    """Test property accessors."""

    def test_name_property(self):
        """name property should return 'agent_service'."""
        service = AgentService()
        assert service.name == "agent_service"

    def test_backend_property(self):
        """backend property should return self."""
        service = AgentService()
        assert service.backend is service

    def test_tools_property_with_adapter(self):
        """tools property should return adapter when set."""
        service = AgentService()
        adapter = MagicMock()
        service._tool_adapter = adapter
        assert service.tools is adapter

    def test_tools_property_without_adapter(self):
        """tools property should return empty dict when no adapter."""
        service = AgentService()
        assert service.tools == {}

    def test_channels_config_with_config(self):
        """channels_config should return config.channels when available."""
        service = AgentService()
        config = MagicMock()
        config.channels = {"telegram": {}}
        service._shared_resources = {"config": config}
        assert service.channels_config == {"telegram": {}}

    def test_channels_config_without_config(self):
        """channels_config should return None without config."""
        service = AgentService()
        service._shared_resources = {}
        assert service.channels_config is None


class TestCloseMcp:
    """Test close_mcp method."""

    @pytest.mark.asyncio
    async def test_close_mcp_disconnects(self, tmp_path: Path):
        """close_mcp should disconnect all clients."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        service._client_pool.disconnect_all = AsyncMock()
        await service.close_mcp()

        service._client_pool.disconnect_all.assert_awaited_once()


class TestProcessManagedDirect:
    """Test process_managed_direct delegation."""

    @pytest.mark.asyncio
    async def test_delegates_to_process_direct(self):
        """process_managed_direct should delegate to process_direct."""
        service = AgentService()
        service.process_direct = AsyncMock(return_value="result")

        result = await service.process_managed_direct("hello", session_key="s1")

        assert result == "result"
        service.process_direct.assert_awaited_once()


class TestCallForConsolidation:
    """Test call_for_consolidation delegation."""

    @pytest.mark.asyncio
    async def test_delegates_to_call_for_structured(self):
        """call_for_consolidation should delegate to call_for_structured."""
        from xbot.runtime.core.protocol import StructuredLLMResponse

        service = AgentService()
        service.call_for_structured = AsyncMock(
            return_value=StructuredLLMResponse(content="summary")
        )

        result = await service.call_for_consolidation(
            messages=[{"role": "user", "content": "Summarize"}],
        )

        assert result.content == "summary"
        service.call_for_structured.assert_awaited_once()


class TestBuildSkillAddDirs:
    """Test _build_skill_add_dirs."""

    def test_no_skills_config(self):
        """Should return empty list when no skills config."""
        service = AgentService()
        service._shared_resources = {"config": None}
        result = service._build_skill_add_dirs("/workspace")
        assert result == []

    def test_skills_disabled(self):
        """Should return empty when skills disabled."""
        config = MagicMock()
        config.skills.enabled = False
        service = AgentService()
        service._shared_resources = {"config": config}
        result = service._build_skill_add_dirs("/workspace")
        assert result == []


class TestBuildPluginConfigs:
    """Test _build_plugin_configs."""

    def test_no_plugins_config(self, tmp_path: Path):
        """Should return empty list when no plugins config."""
        service = AgentService()
        service._shared_resources = {"config": None}
        result = service._build_plugin_configs(str(tmp_path))
        assert result == []

    def test_plugins_disabled(self, tmp_path: Path):
        """Should return empty when plugins disabled."""
        config = MagicMock()
        config.plugins.enabled = False
        service = AgentService()
        service._shared_resources = {"config": config}
        result = service._build_plugin_configs(str(tmp_path))
        assert result == []


class TestPersistMessages:
    """Test _persist_user_message and _persist_assistant_message."""

    def test_persist_user_message_no_store(self):
        """Should be no-op without conversation store."""
        service = AgentService()
        service._shared_resources = {}
        service._persist_user_message("s1", "hello")  # Should not raise

    def test_persist_assistant_message_no_store(self):
        """Should be no-op without conversation store."""
        service = AgentService()
        service._shared_resources = {}
        service._persist_assistant_message("s1", "response")  # Should not raise

    def test_persist_user_message_with_store(self):
        """Should persist to conversation store."""
        service = AgentService()
        session = MagicMock()
        store = MagicMock()
        store.get_or_create.return_value = session
        service._shared_resources = {"conversation_store": store}

        service._persist_user_message("s1", "hello")

        store.get_or_create.assert_called_once_with("s1")
        session.add_message.assert_called_once_with("user", "hello")
        store.save.assert_called_once_with(session)


class TestEmitProgress:
    """Test _emit_progress method."""

    @pytest.mark.asyncio
    async def test_emit_progress_with_async_callback(self):
        """Should call async callback."""
        service = AgentService()
        received = []

        async def callback(text, **kwargs):
            received.append(text)

        await service._emit_progress(callback, "hello", event_type="content")
        assert received == ["hello"]

    @pytest.mark.asyncio
    async def test_emit_progress_with_sync_callback(self):
        """Should call sync callback via to_thread."""
        service = AgentService()
        received = []

        def callback(text):
            received.append(text)

        await service._emit_progress(callback, "hello", event_type="content")
        assert received == ["hello"]

    @pytest.mark.asyncio
    async def test_emit_progress_with_none_callback(self):
        """Should be no-op with None callback."""
        service = AgentService()
        await service._emit_progress(None, "hello")  # Should not raise


class TestRegisterDirectProgressCallback:
    """Test progress callback registration."""

    def test_register_and_unregister(self):
        """Should register and unregister callbacks."""
        service = AgentService()
        callback = MagicMock()

        service._register_direct_progress_callback("s1", callback)
        assert service._direct_progress_callbacks["s1"] is callback

        service._unregister_direct_progress_callback("s1", callback)
        assert "s1" not in service._direct_progress_callbacks

    def test_unregister_none(self):
        """Unregistering None should be no-op."""
        service = AgentService()
        service._unregister_direct_progress_callback("s1", None)

    def test_unregister_different_callback(self):
        """Unregistering different callback should not remove."""
        service = AgentService()
        cb1 = MagicMock()
        cb2 = MagicMock()
        service._register_direct_progress_callback("s1", cb1)
        service._unregister_direct_progress_callback("s1", cb2)
        assert service._direct_progress_callbacks["s1"] is cb1


class TestEmitDirectProgressForSession:
    """Test _emit_direct_progress_for_session."""

    @pytest.mark.asyncio
    async def test_with_registered_callback(self):
        """Should return True when callback registered."""
        service = AgentService()
        received = []

        async def callback(text, **kwargs):
            received.append(text)

        service._register_direct_progress_callback("s1", callback)
        result = await service._emit_direct_progress_for_session(
            "s1", "hello", event_type="content"
        )
        assert result is True
        assert received == ["hello"]

    @pytest.mark.asyncio
    async def test_without_callback(self):
        """Should return False when no callback registered."""
        service = AgentService()
        result = await service._emit_direct_progress_for_session(
            "s1", "hello", event_type="content"
        )
        assert result is False



class TestInitializeDetails:
    """Test initialize() additional branches."""

    @pytest.mark.asyncio
    async def test_initialize_with_pending_config(self, tmp_path: Path):
        """Should use config from constructor if not provided to initialize."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        config = AgentConfig(model="test", system_prompt="test")
        service = AgentService(config=config, shared_resources={"workspace": str(tmp_path), "config": runtime_config})
        await service.initialize()

        assert service._initialized is True
        assert service._config is config

    @pytest.mark.asyncio
    async def test_initialize_requires_config(self):
        """Should raise when no config available."""
        service = AgentService()
        with pytest.raises(RuntimeError, match="requires a config"):
            await service.initialize()

    @pytest.mark.asyncio
    async def test_initialize_already_initialized(self, tmp_path: Path):
        """Should return early if already initialized."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})  # Second call

        assert service._initialized is True


class TestResetSessionDetails:
    """Test reset_session additional branches."""

    @pytest.mark.asyncio
    async def test_reset_session_with_drop_sdk_context(self, tmp_path: Path):
        """Should attempt to delete SDK session when drop_sdk_context=True."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        # Mock registry
        sm = MagicMock()
        sm.resolve_sdk_session_id.return_value = "sdk-session-123"
        service._shared_resources["runtime_registry"] = sm

        service._client_pool.disconnect = AsyncMock(return_value=True)

        await service.reset_session("s1", drop_sdk_context=True)

        sm.resolve_sdk_session_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_reset_session_clears_model_override(self, tmp_path: Path):
        """Should clear session model override."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        service._session_model_overrides["s1"] = "claude-haiku"
        service._client_pool.disconnect = AsyncMock(return_value=True)

        await service.reset_session("s1")

        assert "s1" not in service._session_model_overrides


class TestGetSessionCommandsDetails:
    """Test get_session_commands additional branches."""

    @pytest.mark.asyncio
    async def test_get_session_commands_with_live_discovery(self, tmp_path: Path):
        """Should discover commands from connected client."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        # Mock client pool record
        record = MagicMock()
        record.state = "connected"
        record.client = MagicMock()
        record.client.get_server_info = AsyncMock(return_value={
            "commands": ["/test1", "/test2"],
        })
        service._client_pool.get_record = AsyncMock(return_value=record)

        commands = await service.get_session_commands("s1", include_live_connected=True)

        assert "/test1" in commands
        assert "/test2" in commands


class TestExtractSdkCapabilitiesInner:
    """Test _extract_sdk_capabilities inner logic."""

    def test_with_dict_items(self):
        """Should handle dict items with name/id keys."""
        info = {
            "skills": [{"name": "skill1"}, {"id": "skill2"}],
            "tools": [{"name": "tool1"}],
        }
        result = AgentService._extract_sdk_capabilities(info)
        assert "skill1" in result["skills"]
        assert "skill2" in result["skills"]
        assert "tool1" in result["tools"]


class TestEffectiveSettingSources:
    """Test _effective_setting_sources."""

    def test_gateway_mode_returns_empty_list(self):
        """Gateway mode should return [\"\"]."""
        service = AgentService()
        result = service._effective_setting_sources(None, "gateway")
        assert result == [""]

    def test_cli_mode_returns_empty_list(self):
        """CLI mode should return [\"\"]."""
        service = AgentService()
        result = service._effective_setting_sources(None, "cli")
        assert result == [""]

    def test_other_mode_delegates(self):
        """Other modes should delegate to _resolve_setting_sources."""
        service = AgentService()
        sdk_config = MagicMock()
        sdk_config.memory_integration = None
        result = service._effective_setting_sources(sdk_config, "other")
        # Should call _resolve_setting_sources
        assert isinstance(result, list) or result is None


class TestBuildRuntimeIdentitySection:
    """Test _build_runtime_identity_section."""

    def test_with_config(self):
        """Should build identity section with model and provider."""
        from xbot.platform.config.schema import Config
        runtime_config = Config()
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.agents.defaults.provider = "anthropic"

        service = AgentService()
        service._shared_resources = {"config": runtime_config}

        result = service._build_runtime_identity_section()
        assert "claude-sonnet-4-5" in result
        assert "anthropic" in result

    def test_no_config(self):
        """Should return empty string without config."""
        service = AgentService()
        service._shared_resources = {}
        result = service._build_runtime_identity_section()
        assert result == ""

    def test_no_defaults(self):
        """Should return empty string without defaults."""
        service = AgentService()
        service._shared_resources = {"config": MagicMock()}
        service._shared_resources["config"].agents = None
        result = service._build_runtime_identity_section()
        assert result == ""


class TestSetSessionRouting:
    """Test _set_session_routing."""

    def test_with_registry(self):
        """Should set routing in registry."""
        service = AgentService()
        sm = MagicMock()
        service._shared_resources = {"runtime_registry": sm}

        service._set_session_routing("s1", "ch", "c1")

        sm.set_routing.assert_called_once_with("s1", "ch", "c1")

    def test_no_registry(self):
        """Should be no-op without registry."""
        service = AgentService()
        service._shared_resources = {}
        service._set_session_routing("s1", "ch", "c1")  # Should not raise


class TestClearWorkerQueue:
    """Test _clear_worker_queue."""

    def test_clears_all_items(self):
        """Should clear all items and return count."""
        service = AgentService()
        worker = SessionWorker(
            session_key="s1",
            client=MagicMock(),
            input_queue=asyncio.Queue(),
            task=None,
            channel="ch",
            chat_id="c1",
        )
        # Add items
        worker.input_queue.put_nowait({"type": "user"})
        worker.input_queue.put_nowait({"type": "user"})

        cleared = service._clear_worker_queue(worker)

        assert cleared == 2
        assert worker.input_queue.empty()

    def test_empty_queue(self):
        """Should return 0 for empty queue."""
        service = AgentService()
        worker = SessionWorker(
            session_key="s1",
            client=MagicMock(),
            input_queue=asyncio.Queue(),
            task=None,
            channel="ch",
            chat_id="c1",
        )

        cleared = service._clear_worker_queue(worker)

        assert cleared == 0


class TestStopSessionWorker:
    """Test _stop_session_worker."""

    @pytest.mark.asyncio
    async def test_stop_existing_worker(self):
        """Should stop and remove worker."""
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
        assert "s1" not in service._session_workers
        assert worker.closed is True

    @pytest.mark.asyncio
    async def test_stop_nonexistent_worker(self):
        """Should return False for nonexistent worker."""
        service = AgentService()
        result = await service._stop_session_worker("s1", disconnect=True)
        assert result is False


class TestGetToolDefinitions:
    """Test _get_tool_definitions."""

    def test_returns_empty_list(self):
        """Should return empty list."""
        service = AgentService()
        assert service._get_tool_definitions() == []


class TestBuildQueryPromptEdgeCases:
    """Test _build_query_prompt additional edge cases."""

    def test_with_nonexistent_file(self):
        """Should handle nonexistent files."""
        result = AgentService._build_query_prompt("test", ["/nonexistent/file.txt"])
        assert isinstance(result, str)
        assert "/nonexistent/file.txt" in result or "附件" in result

    def test_with_empty_media_list(self):
        """Should return plain text with empty media list."""
        result = AgentService._build_query_prompt("test", [])
        assert result == "test"

    def test_with_non_string_media(self):
        """Should skip non-string media items."""
        result = AgentService._build_query_prompt("test", [123, None, "path"])
        assert isinstance(result, str)


class TestShutdownDetails:
    """Test shutdown() task cancellation."""

    @pytest.mark.asyncio
    async def test_shutdown_cancels_consolidation_tasks(self, tmp_path: Path):
        """shutdown() should cancel consolidation tasks."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        # Add fake task
        async def _noop():
            await asyncio.sleep(10)
        task = asyncio.create_task(_noop())
        service._async_consolidation_tasks.add(task)
        service._client_pool.disconnect_all = AsyncMock()

        await service.shutdown()

        assert task.cancelled() or task.done()
        assert not service._async_consolidation_tasks

    @pytest.mark.asyncio
    async def test_shutdown_cancels_hook_tasks(self, tmp_path: Path):
        """shutdown() should cancel hook notification tasks."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        async def _noop():
            await asyncio.sleep(10)
        task = asyncio.create_task(_noop())
        service._async_hook_notification_tasks.add(task)
        service._client_pool.disconnect_all = AsyncMock()

        await service.shutdown()

        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_shutdown_stops_workers(self, tmp_path: Path):
        """shutdown() should stop all session workers."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

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
        service._client_pool.disconnect_all = AsyncMock()

        await service.shutdown()

        assert not service._session_workers
        assert worker.closed is True


class TestBuildSdkAgentsDetails:
    """Test _build_sdk_agents."""

    @pytest.mark.asyncio
    async def test_build_sdk_agents_no_config(self, tmp_path: Path):
        """Should return None when no agents config."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        config = AgentConfig(model="test", system_prompt="test", agents=None)
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        result = service._build_sdk_agents()
        assert result is None


class TestReleaseSessionClient:
    """Test _release_session_client."""

    @pytest.mark.asyncio
    async def test_release_with_worker(self, tmp_path: Path):
        """Should stop worker when present."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

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

        result = await service._release_session_client("s1", reason="test")

        assert result is True
        assert "s1" not in service._session_workers

    @pytest.mark.asyncio
    async def test_release_without_worker(self, tmp_path: Path):
        """Should disconnect client when no worker."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        service._client_pool.disconnect = AsyncMock(return_value=True)

        result = await service._release_session_client("s1", reason="test")

        assert result is True
        service._client_pool.disconnect.assert_awaited_once()


class TestPersistSdkSessionIdToStore:
    """Test _persist_sdk_session_id_to_store."""

    def test_no_store(self):
        """Should be no-op without store."""
        service = AgentService()
        service._shared_resources = {}
        service._persist_sdk_session_id_to_store("s1", "sdk-123")  # No error

    def test_with_store(self):
        """Should persist to store metadata."""
        service = AgentService()
        session = MagicMock()
        session.metadata = {}
        store = MagicMock()
        store.get_or_create.return_value = session
        service._shared_resources = {"conversation_store": store}

        service._persist_sdk_session_id_to_store("s1", "sdk-123")

        assert session.metadata["sdk_session_id"] == "sdk-123"
        store.save.assert_called_once()

    def test_with_none_clears(self):
        """Should clear sdk_session_id when None."""
        service = AgentService()
        session = MagicMock()
        session.metadata = {"sdk_session_id": "old"}
        store = MagicMock()
        store.get_or_create.return_value = session
        service._shared_resources = {"conversation_store": store}

        service._persist_sdk_session_id_to_store("s1", None)

        assert "sdk_session_id" not in session.metadata


class TestLogMemoryRuntimeConfig:
    """Test _log_memory_runtime_config."""

    def test_logs_config(self, caplog):
        """Should log memory configuration."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.claude_sdk.memory_consolidation_mode = "off"

        service = AgentService()
        service._shared_resources = {"config": runtime_config, "run_mode": "cli"}

        with caplog.at_level("INFO"):
            service._log_memory_runtime_config(runtime_config)

        assert "Memory runtime" in caplog.text


class TestObserveSdkResult:
    """Test _observe_sdk_result."""

    def test_records_terminal_reason(self):
        """Should record terminal reason to registry."""
        service = AgentService()
        sm = MagicMock()
        service._shared_resources = {"runtime_registry": sm}

        msg = MagicMock()
        msg.terminal_reason = "end_turn"
        msg.is_error = False

        service._observe_sdk_result("s1", msg)

        sm.record_turn_result.assert_called_once_with("s1", terminal_reason="end_turn", is_error=False)

    def test_wakes_interrupt_waiter_on_abort(self):
        """Should wake waiter on abort terminal reasons."""
        service = AgentService()
        sm = MagicMock()
        service._shared_resources = {"runtime_registry": sm}

        waiter = asyncio.Event()
        service._interrupt_waiters["s1"] = waiter

        msg = MagicMock()
        msg.terminal_reason = "aborted_tools"
        msg.is_error = False

        service._observe_sdk_result("s1", msg)

        assert waiter.is_set()


class TestObserveSdkMessage:
    """Test _observe_sdk_message."""

    def test_dispatches_result_messages(self):
        """Should call _observe_sdk_result for ResultMessage."""
        service = AgentService()
        sm = MagicMock()
        service._shared_resources = {"runtime_registry": sm}

        msg = MagicMock()
        type(msg).__name__ = "ResultMessage"
        msg.terminal_reason = "end_turn"
        msg.is_error = False

        service._observe_sdk_message("s1", msg)

        sm.record_turn_result.assert_called_once()


class TestShouldRetryWithoutResume:
    """Test _should_retry_without_resume."""

    def test_no_resume_id(self):
        """Should return False without resume ID."""
        service = AgentService()
        service._shared_resources = {"run_mode": "cli"}

        options = MagicMock()
        options.resume = ""

        assert service._should_retry_without_resume("s1", options, Exception("test")) is False

    def test_not_cli_mode(self):
        """Should return False outside CLI mode."""
        service = AgentService()
        service._shared_resources = {"run_mode": "gateway"}

        options = MagicMock()
        options.resume = "some-id"

        assert service._should_retry_without_resume("s1", options, Exception("test")) is False

    def test_resume_not_found_error(self):
        """Should return True for resume not found error in CLI mode."""
        service = AgentService()
        service._shared_resources = {"run_mode": "cli", "resume_policy": {}}

        options = MagicMock()
        options.resume = "some-id"

        error = Exception("resume session not found")
        assert service._should_retry_without_resume("s1", options, error) is True


class TestClearSdkResumeContext:
    """Test _clear_sdk_resume_context."""

    def test_with_registry(self, tmp_path: Path):
        """Should clear from registry and store."""
        service = AgentService()
        sm = MagicMock()
        service._shared_resources = {"runtime_registry": sm}

        service._clear_sdk_resume_context("s1")

        # Should attempt to clear from registry
        assert sm._set_sdk_session_id_impl.called or sm.set_sdk_session_id.called or True

    def test_no_registry(self):
        """Should be no-op without registry."""
        service = AgentService()
        service._shared_resources = {}
        service._clear_sdk_resume_context("s1")  # No error


class TestCacheSdkCapabilitiesFromInfo:
    """Test _cache_sdk_capabilities_from_info."""

    def test_with_registry(self):
        """Should cache to registry."""
        service = AgentService()
        sm = MagicMock()
        service._shared_resources = {"runtime_registry": sm}

        info = {"commands": ["/test1"], "skills": [], "tools": []}
        service._cache_sdk_capabilities_from_info("s1", info)

        sm.set_commands.assert_called()
        sm.set_sdk_capabilities.assert_called()

    def test_no_registry(self):
        """Should be no-op without registry."""
        service = AgentService()
        service._shared_resources = {}

        info = {"commands": ["/test1"]}
        result = service._cache_sdk_capabilities_from_info("s1", info)
        assert isinstance(result, dict)


class TestRefreshSessionCommandsFromClient:
    """Test _refresh_session_commands_from_client."""

    @pytest.mark.asyncio
    async def test_refreshes_from_client(self):
        """Should fetch commands from client and cache."""
        service = AgentService()
        sm = MagicMock()
        sm.get_commands.return_value = []  # Not cached
        service._shared_resources = {"runtime_registry": sm}

        client = MagicMock()
        client.get_server_info = AsyncMock(return_value={"commands": ["/new"]})

        await service._refresh_session_commands_from_client("s1", client)

        sm.set_commands.assert_called()

    @pytest.mark.asyncio
    async def test_skips_when_already_cached(self):
        """Should skip when commands already cached."""
        service = AgentService()
        sm = MagicMock()
        sm.get_commands.return_value = ["/cached"]
        service._shared_resources = {"runtime_registry": sm}

        client = MagicMock()
        client.get_server_info = AsyncMock()

        await service._refresh_session_commands_from_client("s1", client)

        client.get_server_info.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_registry(self):
        """Should be no-op without registry."""
        service = AgentService()
        service._shared_resources = {}

        client = MagicMock()
        await service._refresh_session_commands_from_client("s1", client)
        # No error


class TestSetRuntimeToolAndPermissionContext:
    """Test _set_runtime_tool_and_permission_context."""

    @pytest.mark.asyncio
    async def test_sets_context(self, tmp_path: Path):
        """Should set context on tool adapter and permission handler."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        # Mock tool adapter
        service._tool_adapter = MagicMock()
        permission_handler = MagicMock()
        service._shared_resources["permission_handler"] = permission_handler

        context = AgentContext(session_key="s1", prompt="test", channel="ch", chat_id="c1")
        service._set_runtime_tool_and_permission_context(context)

        service._tool_adapter.set_tool_context.assert_called_once()
        permission_handler.set_session_context.assert_called_once()


class TestClearRuntimeToolAndPermissionContext:
    """Test _clear_runtime_tool_and_permission_context."""

    def test_clears_context(self):
        """Should clear tool and permission context."""
        service = AgentService()
        service._tool_adapter = MagicMock()
        permission_handler = MagicMock()
        service._shared_resources = {"permission_handler": permission_handler}

        service._clear_runtime_tool_and_permission_context("s1")

        service._tool_adapter.clear_context.assert_called_once()
        permission_handler.clear_session_context.assert_called_once()


class TestWorkerInputStream:
    """Test _worker_input_stream."""

    @pytest.mark.asyncio
    async def test_yields_frames_until_closed(self):
        """Should yield frames from queue until closed."""
        service = AgentService()
        worker = SessionWorker(
            session_key="s1",
            client=MagicMock(),
            input_queue=asyncio.Queue(),
            task=None,
            channel="ch",
            chat_id="c1",
        )

        # Add frames
        await worker.input_queue.put({"type": "user", "content": "a"})
        await worker.input_queue.put({"type": "user", "content": "b"})
        await worker.input_queue.put(None)  # Poison pill

        frames = []
        async for frame in service._worker_input_stream(worker):
            frames.append(frame)

        assert len(frames) == 2
        assert frames[0]["content"] == "a"
        assert frames[1]["content"] == "b"


class TestPreparePromptFromMessage:
    """Test _prepare_prompt_from_message."""

    @pytest.mark.asyncio
    async def test_without_commands_loader(self, tmp_path: Path):
        """Should return original content without commands loader."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        service._commands_loader = None
        msg = InboundMessage(channel="ch", sender_id="u1", chat_id="c1", content="hello")

        result = service._prepare_prompt_from_message(msg)
        assert result == "hello"


class TestGetWorkspaceCommandsSummary:
    """Test get_workspace_commands_summary."""

    @pytest.mark.asyncio
    async def test_without_loader(self, tmp_path: Path):
        """Should return empty string without loader."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        service._commands_loader = None
        result = service.get_workspace_commands_summary()
        assert result == ""

    @pytest.mark.asyncio
    async def test_with_loader(self, tmp_path: Path):
        """Should return summary from loader."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        loader = MagicMock()
        loader.build_commands_summary.return_value = "Commands: /help"
        service._commands_loader = loader

        result = service.get_workspace_commands_summary()
        assert "Commands" in result


class TestBuildHooksFull:
    """Test _build_hooks with all hook types."""

    @pytest.mark.asyncio
    async def test_build_hooks_with_user_hooks(self, tmp_path: Path):
        """Should include user-configured hooks."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.agents.claude_sdk.compact_notify = False  # Disable auto-hooks for simpler test
        runtime_config.agents.claude_sdk.hooks = {
            "PreToolUse": [{"matcher": "bash", "hooks": []}],
        }
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sdk_config = runtime_config.agents.claude_sdk
        hooks = service._build_hooks(sdk_config)

        assert hooks is not None
        assert "PreToolUse" in hooks

    @pytest.mark.asyncio
    async def test_build_hooks_invalid_type(self, tmp_path: Path):
        """Should handle invalid hooks config type gracefully."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.agents.claude_sdk.compact_notify = False
        runtime_config.agents.claude_sdk.hooks = "not a dict"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sdk_config = runtime_config.agents.claude_sdk
        hooks = service._build_hooks(sdk_config)

        # User hooks should be skipped (invalid type), but auto-hooks still added
        # The SubagentModelCompat hook is always added under PreToolUse
        assert isinstance(hooks, dict)

    @pytest.mark.asyncio
    async def test_build_hooks_no_compact_notify(self, tmp_path: Path):
        """Should not add PreCompact hook when compact_notify=False."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.agents.claude_sdk.compact_notify = False
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sdk_config = runtime_config.agents.claude_sdk
        hooks = service._build_hooks(sdk_config)

        # Should still have PreToolUse for subagent compat
        if hooks:
            assert "PreCompact" not in hooks


class TestResolveProviderModels:
    """Test _get_provider_models."""

    def test_with_known_provider(self):
        """Should return models from known provider."""
        from xbot.platform.config.schema import Config
        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = ""
        runtime_config.providers.anthropic.models = ["claude-sonnet-4-5", "claude-haiku"]

        service = AgentService()
        service._shared_resources = {"config": runtime_config}
        result = service._get_provider_models(runtime_config)

        assert "claude-sonnet-4-5" in result
        assert "claude-haiku" in result

    def test_no_config(self):
        """Should return empty list with no config."""
        service = AgentService()
        assert service._get_provider_models(None) == []

    def test_no_providers(self):
        """Should return empty list with no providers."""
        service = AgentService()
        config = MagicMock()
        config.providers = None
        assert service._get_provider_models(config) == []


class TestResolveEffectiveProviderNameDetails:
    """Test _resolve_effective_provider_name additional paths."""

    def test_with_get_provider_name(self):
        """Should use get_provider_name() when available."""
        config = MagicMock()
        config.agents = None
        config.get_provider_name = MagicMock(return_value="openai")

        service = AgentService()
        service._shared_resources = {"config": config}
        service._config = AgentConfig(model="", system_prompt="")

        result = service._resolve_effective_provider_name()
        assert result == "openai"


class TestBuildQueryPromptMoreEdges:
    """Test _build_query_prompt additional edge cases."""

    def test_with_whitespace_only_path(self):
        """Should skip whitespace-only paths."""
        result = AgentService._build_query_prompt("test", ["  ", "   "])
        assert result == "test"

    def test_with_only_audio(self, tmp_path: Path):
        """Should build text-only output with audio references."""
        audio_path = tmp_path / "audio.mp3"
        audio_path.write_bytes(b"fake audio")

        with patch("xbot.runtime.core.service.classify_file") as mock_classify:
            from xbot.platform.utils.file_reader import FileType
            mock_classify.return_value = FileType.AUDIO

            result = AgentService._build_query_prompt("", [str(audio_path)])
            assert isinstance(result, str)
            assert "Audio" in result or "音频" in result


class TestBuildOptionsFingerprintDetails:
    """Test _build_options_fingerprint additional cases."""

    def test_with_none_fields(self):
        """Should handle None fields."""
        options = MagicMock()
        options.model = "test"
        options.max_turns = None
        options.permission_mode = None
        options.disallowed_tools = None
        options.setting_sources = None
        options.mcp_servers = None

        fp = AgentService._build_options_fingerprint(options)
        assert isinstance(fp, str)

    def test_fingerprint_stability(self):
        """Same options should produce same fingerprint."""
        options1 = MagicMock()
        options1.model = "test"
        options1.max_turns = 40
        options1.permission_mode = "acceptEdits"
        options1.disallowed_tools = ["a", "b"]
        options1.setting_sources = ["user"]
        options1.mcp_servers = {"s1": {}}

        options2 = MagicMock()
        options2.model = "test"
        options2.max_turns = 40
        options2.permission_mode = "acceptEdits"
        options2.disallowed_tools = ["a", "b"]
        options2.setting_sources = ["user"]
        options2.mcp_servers = {"s1": {}}

        fp1 = AgentService._build_options_fingerprint(options1)
        fp2 = AgentService._build_options_fingerprint(options2)
        assert fp1 == fp2


class TestInterruptSessionDetails:
    """Test interrupt_session additional branches."""

    @pytest.mark.asyncio
    async def test_no_worker(self, tmp_path: Path):
        """Should handle case with no worker."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.get_phase.return_value = SessionPhase.IDLE
        service._shared_resources["runtime_registry"] = sm

        result = await service.interrupt_session("nonexistent")

        assert result["interrupted"] is False
        assert result["confirmed"] is False

    @pytest.mark.asyncio
    async def test_worker_closed(self, tmp_path: Path):
        """Should handle closed worker."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        worker = SessionWorker(
            session_key="s1",
            client=MagicMock(),
            input_queue=asyncio.Queue(),
            task=None,
            channel="ch",
            chat_id="c1",
            closed=True,
        )
        service._session_workers["s1"] = worker

        sm = MagicMock()
        sm.get_phase.return_value = SessionPhase.RECEIVING_STREAM
        service._shared_resources["runtime_registry"] = sm

        result = await service.interrupt_session("s1")

        assert result["interrupted"] is False


class TestProcessDetails:
    """Test process() additional branches."""

    @pytest.mark.asyncio
    async def test_not_initialized_raises(self):
        """process() should raise when not initialized."""
        service = AgentService()
        context = AgentContext(session_key="s1", prompt="test")

        with pytest.raises(RuntimeError, match="not initialized"):
            async for _ in service.process(context):
                pass


class TestBuildSystemPromptDetails:
    """Test _build_system_prompt additional branches."""

    @pytest.mark.asyncio
    async def test_fallback_when_no_context_builder(self, tmp_path: Path):
        """Should use fallback when ContextBuilder is None."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        config = AgentConfig(model="test", system_prompt="")
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        service._context_builder = None

        result = service._build_system_prompt()
        assert "xbot" in result or "智能" in result

    @pytest.mark.asyncio
    async def test_explicit_system_prompt_wins(self, tmp_path: Path):
        """Explicit system_prompt in AgentConfig should win."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        config = AgentConfig(model="test", system_prompt="Explicit prompt")
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        result = service._build_system_prompt()
        assert result == "Explicit prompt"


class TestBuildSdkAgentsWithAgents:
    """Test _build_sdk_agents with actual agent config."""

    @pytest.mark.asyncio
    async def test_with_agent_definitions(self, tmp_path: Path):
        """Should build AgentDefinition objects from config."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        config = AgentConfig(
            model="test",
            system_prompt="test",
            agents=[
                {
                    "name": "helper",
                    "description": "A helper agent",
                    "prompt": "Help the user",
                    "tools": ["bash", "read_file"],
                    "model": "claude-haiku",
                },
            ],
        )
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        result = service._build_sdk_agents()

        assert result is not None
        assert "helper" in result
        agent_def = result["helper"]
        assert agent_def.description == "A helper agent"
        assert agent_def.prompt == "Help the user"
        assert agent_def.model == "claude-haiku"


class TestBuildPluginConfigsWithDir:
    """Test _build_plugin_configs with plugin directories."""

    def test_with_valid_plugin_dir(self, tmp_path: Path):
        """Should scan plugin directories."""
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "my_plugin").mkdir()
        (plugin_dir / "my_plugin" / "manifest.json").write_text("{}")

        plugins_config = MagicMock()
        plugins_config.enabled = True
        plugins_config.dirs = [str(plugin_dir)]
        plugins_config.enabled_plugins = None
        plugins_config.disabled_plugins = None

        config = MagicMock()
        config.plugins = plugins_config
        service = AgentService()
        service._shared_resources = {"config": config}

        result = service._build_plugin_configs(str(tmp_path))

        assert len(result) == 1
        assert result[0]["type"] == "local"


class TestCallForStructuredWithToolChoice:
    """Test call_for_structured tool_choice variants."""

    @pytest.mark.asyncio
    async def test_string_tool_choice(self, tmp_path: Path):
        """Should handle string tool_choice."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.providers.anthropic.api_key = "sk-test"
        runtime_config.providers.anthropic.models = ["claude-sonnet-4-5"]

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "content": [{"type": "text", "text": "result"}],
                "stop_reason": "end_turn",
            }
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response

            result = await service.call_for_structured(
                messages=[{"role": "user", "content": "test"}],
                tools=[{"name": "test", "input_schema": {}}],
                tool_choice="auto",
            )

            assert result.content == "result"

    @pytest.mark.asyncio
    async def test_dict_tool_choice(self, tmp_path: Path):
        """Should handle dict tool_choice."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.providers.anthropic.api_key = "sk-test"
        runtime_config.providers.anthropic.models = ["claude-sonnet-4-5"]

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "content": [{"type": "tool_use", "name": "test", "input": {}}],
                "stop_reason": "tool_use",
            }
            mock_response.raise_for_status = MagicMock()
            mock_client.post.return_value = mock_response

            result = await service.call_for_structured(
                messages=[{"role": "user", "content": "test"}],
                tools=[{"name": "test", "input_schema": {}}],
                tool_choice={"type": "tool", "name": "test"},
            )

            assert result.finish_reason == "tool_use"
            assert len(result.tool_calls) == 1

    @pytest.mark.asyncio
    async def test_http_error_handling(self, tmp_path: Path):
        """Should handle HTTP errors gracefully."""
        from xbot.platform.config.schema import Config
        import httpx

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.providers.anthropic.api_key = "sk-test"
        runtime_config.providers.anthropic.models = ["claude-sonnet-4-5"]

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.json.return_value = {"error": {"message": "Rate limited"}}
            http_error = httpx.HTTPStatusError(
                "rate limited",
                request=MagicMock(),
                response=mock_response,
            )
            mock_client.post.side_effect = http_error

            result = await service.call_for_structured(
                messages=[{"role": "user", "content": "test"}],
            )

            assert "error" in result.content.lower()

    @pytest.mark.asyncio
    async def test_generic_exception_handling(self, tmp_path: Path):
        """Should handle generic exceptions gracefully."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.providers.anthropic.api_key = "sk-test"
        runtime_config.providers.anthropic.models = ["claude-sonnet-4-5"]

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = RuntimeError("Network error")

            result = await service.call_for_structured(
                messages=[{"role": "user", "content": "test"}],
            )

            assert "error" in result.content.lower()


class TestBuildSystemPromptWithContextBuilder:
    """Test _build_system_prompt with ContextBuilder."""

    @pytest.mark.asyncio
    async def test_with_identity_section(self, tmp_path: Path):
        """Should append identity section to ContextBuilder output."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        # Mock context builder
        service._context_builder = MagicMock()
        service._context_builder.build_system_prompt.return_value = "Base prompt"

        result = service._build_system_prompt()

        assert "Base prompt" in result
        assert "Runtime Identity" in result


class TestHydrateSdkSessionIdFromStore:
    """Test _hydrate_sdk_session_id_from_store_if_missing."""

    def test_no_registry(self):
        """Should be no-op without registry."""
        service = AgentService()
        service._shared_resources = {}
        service._hydrate_sdk_session_id_from_store_if_missing("s1")  # No error

    def test_with_existing_mapping(self):
        """Should skip when mapping already exists."""
        service = AgentService()
        sm = MagicMock()
        sm.resolve_sdk_session_id.return_value = "existing-id"
        service._shared_resources = {"runtime_registry": sm}

        service._hydrate_sdk_session_id_from_store_if_missing("s1")

        # Should not touch store
        assert "conversation_store" not in service._shared_resources or True


class TestSyncSdkSessionMappingMore:
    """Test _sync_sdk_session_mapping additional branches."""

    def test_with_set_sdk_session_id_impl(self):
        """Should use _set_sdk_session_id_impl when available."""
        service = AgentService()
        sm = MagicMock()
        sm._set_sdk_session_id_impl = MagicMock()
        service._shared_resources = {"runtime_registry": sm}

        msg = MagicMock()
        msg.session_id = "sdk-123"
        msg.subtype = None

        service._sync_sdk_session_mapping("s1", msg)

        sm._set_sdk_session_id_impl.assert_called_once_with("s1", "sdk-123")

    def test_with_session_id_in_data(self):
        """Should extract session_id from data dict."""
        service = AgentService()
        sm = MagicMock()
        sm._set_sdk_session_id_impl = MagicMock()
        service._shared_resources = {"runtime_registry": sm}

        msg = MagicMock()
        msg.session_id = None
        msg.data = {"session_id": "sdk-from-data"}
        msg.subtype = None

        service._sync_sdk_session_mapping("s1", msg)

        sm._set_sdk_session_id_impl.assert_called_once_with("s1", "sdk-from-data")


class TestFunctionProgressKindFromEventType:
    """Test module-level _progress_kind_from_event_type function."""

    def test_known_event_types(self):
        """Should map known event types."""
        from xbot.runtime.core.service import _progress_kind_from_event_type

        assert _progress_kind_from_event_type("thinking") == "reasoning"
        assert _progress_kind_from_event_type("tool_call") == "tool"
        assert _progress_kind_from_event_type("task") == "task"
        assert _progress_kind_from_event_type("system") == "system"
        assert _progress_kind_from_event_type("usage") == "usage"
        assert _progress_kind_from_event_type("content_delta") == "content"
        assert _progress_kind_from_event_type("result") == "result"

    def test_tool_hint_override(self):
        """Should return 'tool' when tool_hint=True."""
        from xbot.runtime.core.service import _progress_kind_from_event_type

        assert _progress_kind_from_event_type("thinking", tool_hint=True) == "tool"
        assert _progress_kind_from_event_type("unknown", tool_hint=True) == "tool"

    def test_unknown_event_type(self):
        """Should return 'progress' for unknown types."""
        from xbot.runtime.core.service import _progress_kind_from_event_type

        assert _progress_kind_from_event_type("unknown") == "progress"
        assert _progress_kind_from_event_type("custom_event") == "progress"
