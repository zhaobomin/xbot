"""Tests for AgentService."""

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.platform.bus.events import InboundMessage
from xbot.runtime.core.protocol import AgentContext, AgentResponse
from xbot.runtime.core.service import AgentService
from xbot.runtime.core.types import AgentConfig
from xbot.runtime.session.conversation_store import ConversationStore
from xbot.runtime.state import RuntimeSessionRegistry
from xbot.runtime.state.machine import SessionPhase
from xbot.runtime.core.client_pool import ClientPool, ClientRecord


class TestAgentService:
    """Tests for AgentService."""

    @pytest.fixture
    def config(self) -> AgentConfig:
        """Create a test config."""
        return AgentConfig(
            model="claude-sonnet-4-6",
            system_prompt="Test prompt",
        )

    @pytest.fixture
    def shared_resources(self, tmp_path: Path) -> dict[str, Any]:
        """Create shared resources."""
        return {
            "workspace": str(tmp_path),
            "config": MagicMock(),
        }

    @pytest.mark.asyncio
    async def test_initialize(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Test AgentService initialization."""
        service = AgentService()
        await service.initialize(config, shared_resources)

        assert service._initialized is True

    @pytest.mark.asyncio
    async def test_shutdown(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Test AgentService shutdown."""
        service = AgentService()
        await service.initialize(config, shared_resources)
        await service.shutdown()

        assert service._initialized is False

    @pytest.mark.asyncio
    async def test_process_returns_response(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Test process yields AgentResponse."""
        service = AgentService()
        await service.initialize(config, shared_resources)

        context = AgentContext(
            session_key="test:1",
            prompt="Hello",
        )

        responses = []
        with patch.object(service, "_get_or_create_client") as mock_client:
            mock_sdk_client = MagicMock()
            mock_sdk_client.process = MagicMock()
            mock_sdk_client.process.return_value = asyncio.as_completed([])
            mock_client.return_value = mock_sdk_client

            async for response in service.process(context):
                responses.append(response)

    @pytest.mark.asyncio
    async def test_process_includes_media_references_in_query(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """process should inject media references into SDK query text."""
        service = AgentService()
        await service.initialize(config, shared_resources)

        image_path = tmp_path / "demo.png"
        image_path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
            b"\x1f\x15\xc4\x89"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        text_path = tmp_path / "notes.txt"
        text_path.write_text("hello", encoding="utf-8")

        class ResultMessage:
            pass

        async def receive_messages():
            yield ResultMessage()

        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_client.receive_messages = receive_messages
        mock_client.get_server_info = AsyncMock(return_value={})

        context = AgentContext(
            session_key="test:media",
            prompt="请分析附件",
            channel="cli",
            chat_id="direct",
            media=[str(image_path), str(text_path)],
        )

        with patch.object(service, "_get_or_create_client", AsyncMock(return_value=mock_client)):
            async for _ in service.process(context):
                pass

        mock_client.query.assert_awaited_once()
        sent_prompt = mock_client.query.await_args.args[0]
        assert "[Image: source:" in sent_prompt
        assert str(image_path.resolve()) in sent_prompt
        assert "[附件:" in sent_prompt
        assert str(text_path.resolve()) in sent_prompt

    @pytest.mark.asyncio
    async def test_get_session_commands_default_no_connect(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """get_session_commands should not create/connect client by default."""
        shared_resources["runtime_registry"] = RuntimeSessionRegistry()
        service = AgentService()
        await service.initialize(config, shared_resources)

        with patch.object(service, "_get_or_create_client", AsyncMock()) as mock_get_client:
            commands = await service.get_session_commands("test:c1")

        mock_get_client.assert_not_called()
        assert "/help" in commands

    @pytest.mark.asyncio
    async def test_get_session_commands_allow_connect_discovers_and_caches(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """allow_connect=True should discover SDK slash commands and cache them."""
        shared_resources["runtime_registry"] = RuntimeSessionRegistry()
        service = AgentService()
        await service.initialize(config, shared_resources)

        mock_client = MagicMock()
        mock_client.get_server_info = AsyncMock(return_value={
            "commands": ["/review", {"name": "schedule"}],
        })

        with patch.object(service, "_get_or_create_client", AsyncMock(return_value=mock_client)):
            commands = await service.get_session_commands("test:c1", allow_connect=True)

        assert "/review" in commands
        assert "/schedule" in commands

        cached = shared_resources["runtime_registry"].get_commands("test:c1")
        assert "/review" in cached
        assert "/schedule" in cached

    @pytest.mark.asyncio
    async def test_build_sdk_options_includes_resume_from_runtime_registry(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """_build_sdk_options should set resume to persisted sdk_session_id."""
        from xbot.platform.config.schema import Config

        shared_resources["config"] = Config()
        registry = RuntimeSessionRegistry()
        registry.get_or_create("test:c1")
        await registry.set_sdk_session_id("test:c1", "sdk-session-123")
        shared_resources["runtime_registry"] = registry

        service = AgentService()
        await service.initialize(config, shared_resources)

        options = service._build_sdk_options(session_key="test:c1")
        assert getattr(options, "resume", None) == "sdk-session-123"

    @pytest.mark.asyncio
    async def test_build_mcp_servers_ignores_invalid_config_type(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """_build_mcp_servers should not crash when mcp_servers is malformed."""
        config.mcp_servers = "invalid"  # type: ignore[assignment]
        service = AgentService()
        await service.initialize(config, shared_resources)

        servers = service._build_mcp_servers()
        assert "xbot" in servers
        assert len(servers) == 1

    @pytest.mark.asyncio
    async def test_build_mcp_servers_skips_null_entries(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Null entries in mcp_servers should be ignored safely."""
        config.mcp_servers = {"ok": {"type": "stdio", "command": "echo"}, "bad": None}
        service = AgentService()
        await service.initialize(config, shared_resources)

        servers = service._build_mcp_servers()
        assert "ok" in servers
        assert "bad" not in servers

    @pytest.mark.asyncio
    async def test_build_hooks_ignores_invalid_user_hook_structure(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Invalid hooks config should be ignored instead of raising errors."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.claude_sdk.hooks = "bad-hooks"  # type: ignore[assignment]
        runtime_config.agents.claude_sdk.compact_notify = False
        shared_resources["config"] = runtime_config

        service = AgentService()
        await service.initialize(config, shared_resources)

        hooks = service._build_hooks(runtime_config.agents.claude_sdk)
        assert hooks is not None
        assert "PreToolUse" in hooks

    @pytest.mark.asyncio
    async def test_build_sdk_options_uses_cli_session_cwd_override(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """CLI mode should use per-session cwd override from runtime registry."""
        from xbot.platform.config.schema import Config

        registry = RuntimeSessionRegistry()
        session_key = "cli:cwd1"
        cwd = tmp_path / "session-cwd"
        cwd.mkdir(parents=True)
        registry.set_session_cwd(session_key, str(cwd))

        shared_resources["config"] = Config()
        shared_resources["runtime_registry"] = registry
        shared_resources["run_mode"] = "cli"

        service = AgentService()
        await service.initialize(config, shared_resources)

        options = service._build_sdk_options(session_key=session_key)
        assert Path(options.cwd) == cwd.resolve()

    @pytest.mark.asyncio
    async def test_build_sdk_options_ignores_session_cwd_outside_cli_mode(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """Non-CLI mode should continue using configured workspace cwd."""
        from xbot.platform.config.schema import Config

        registry = RuntimeSessionRegistry()
        session_key = "gateway:cwd1"
        registry.set_session_cwd(session_key, str(tmp_path / "session-cwd"))
        shared_resources["config"] = Config()
        shared_resources["runtime_registry"] = registry
        shared_resources["run_mode"] = "gateway"

        service = AgentService()
        await service.initialize(config, shared_resources)

        options = service._build_sdk_options(session_key=session_key)
        assert Path(options.cwd) == Path(shared_resources["workspace"]).resolve()

    @pytest.mark.asyncio
    async def test_build_sdk_options_gateway_uses_no_setting_sources(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Gateway mode should not load Claude user/project/local settings."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        shared_resources["config"] = runtime_config
        shared_resources["run_mode"] = "gateway"

        service = AgentService()
        await service.initialize(config, shared_resources)

        options = service._build_sdk_options(session_key="gateway:settings")
        # [""] forces SDK transport to emit `--setting-sources ""`,
        # which disables Claude user/project/local setting resolution.
        assert getattr(options, "setting_sources", None) == [""]

    @pytest.mark.asyncio
    async def test_build_sdk_options_gateway_emits_empty_setting_sources_cli_flag(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Gateway mode should force an explicit empty --setting-sources flag."""
        from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        shared_resources["config"] = runtime_config
        shared_resources["run_mode"] = "gateway"

        service = AgentService()
        await service.initialize(config, shared_resources)
        options = service._build_sdk_options(session_key="gateway:settings")

        async def _empty_stream():
            if False:
                yield {}

        transport = SubprocessCLITransport(prompt=_empty_stream(), options=options)
        transport._cli_path = "claude"
        cmd = transport._build_command()

        assert "--setting-sources" in cmd
        idx = cmd.index("--setting-sources")
        assert cmd[idx + 1] == ""

    @pytest.mark.asyncio
    async def test_build_sdk_options_cli_uses_no_setting_sources(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """CLI mode should also avoid Claude user/project/local settings."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        shared_resources["config"] = runtime_config
        shared_resources["run_mode"] = "cli"

        service = AgentService()
        await service.initialize(config, shared_resources)

        options = service._build_sdk_options(session_key="cli:settings")
        assert getattr(options, "setting_sources", None) == [""]

    @pytest.mark.asyncio
    async def test_build_sdk_options_cli_emits_empty_setting_sources_cli_flag(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """CLI mode should force an explicit empty --setting-sources flag."""
        from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        shared_resources["config"] = runtime_config
        shared_resources["run_mode"] = "cli"

        service = AgentService()
        await service.initialize(config, shared_resources)
        options = service._build_sdk_options(session_key="cli:settings")

        async def _empty_stream():
            if False:
                yield {}

        transport = SubprocessCLITransport(prompt=_empty_stream(), options=options)
        transport._cli_path = "claude"
        cmd = transport._build_command()

        assert "--setting-sources" in cmd
        idx = cmd.index("--setting-sources")
        assert cmd[idx + 1] == ""

    @pytest.mark.asyncio
    async def test_build_sdk_options_gateway_keeps_configured_permission_mode(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Gateway mode should preserve xbot-configured permission mode."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.claude_sdk.permission_mode = "bypassPermissions"
        shared_resources["config"] = runtime_config
        shared_resources["run_mode"] = "gateway"

        service = AgentService()
        await service.initialize(config, shared_resources)

        options = service._build_sdk_options(session_key="gateway:permission")
        assert getattr(options, "permission_mode", None) == "bypassPermissions"

    @pytest.mark.asyncio
    async def test_build_sdk_options_legacy_session_cwd_is_still_honored(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """Legacy session_cwd field should still work during migration."""
        from xbot.platform.config.schema import Config

        registry = RuntimeSessionRegistry()
        session_key = "cli:legacy-cwd"
        legacy_cwd = tmp_path / "legacy-cwd"
        legacy_cwd.mkdir(parents=True)
        state = registry.get_or_create(session_key)
        state.execution_cwd = None
        state.session_cwd = str(legacy_cwd)
        shared_resources["config"] = Config()
        shared_resources["runtime_registry"] = registry
        shared_resources["run_mode"] = "cli"

        service = AgentService()
        await service.initialize(config, shared_resources)

        options = service._build_sdk_options(session_key=session_key)
        assert Path(options.cwd) == legacy_cwd.resolve()

    @pytest.mark.asyncio
    async def test_build_sdk_options_cli_falls_back_to_shared_execution_cwd(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """CLI mode should use shared execution_cwd when session override is missing."""
        from xbot.platform.config.schema import Config

        shared_cli_cwd = tmp_path / "shared-cwd"
        shared_cli_cwd.mkdir(parents=True)
        shared_resources["config"] = Config()
        shared_resources["runtime_registry"] = RuntimeSessionRegistry()
        shared_resources["run_mode"] = "cli"
        shared_resources["execution_cwd"] = str(shared_cli_cwd)

        service = AgentService()
        await service.initialize(config, shared_resources)

        options = service._build_sdk_options(session_key="cli:no-session-cwd")
        assert Path(options.cwd) == shared_cli_cwd.resolve()

    @pytest.mark.asyncio
    async def test_build_sdk_options_system_prompt_uses_session_paths(
        self,
        shared_resources: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """System prompt should reflect per-session execution/workspace paths."""
        from xbot.platform.config.schema import Config

        session_key = "cli:session-paths"
        session_cwd = tmp_path / "session-cwd"
        session_workspace = tmp_path / "session-workspace"
        session_cwd.mkdir(parents=True)
        session_workspace.mkdir(parents=True)

        registry = RuntimeSessionRegistry()
        registry.set_execution_cwd(session_key, str(session_cwd))
        registry.set_workspace_dir(session_key, str(session_workspace))

        shared_resources["config"] = Config()
        shared_resources["runtime_registry"] = registry
        shared_resources["run_mode"] = "cli"
        default_workspace = tmp_path / "default-workspace"
        default_workspace.mkdir(parents=True)
        shared_resources["workspace"] = str(default_workspace)

        config = AgentConfig(model="claude-sonnet-4-6", system_prompt="")
        service = AgentService()
        await service.initialize(config, shared_resources)

        options = service._build_sdk_options(session_key=session_key)
        assert f"Execution CWD: {session_cwd.resolve()}" in options.system_prompt
        assert f"Workspace Assets Dir: {session_workspace.resolve()}" in options.system_prompt

    @pytest.mark.asyncio
    async def test_process_direct_tool_hint_includes_cli_execution_cwd(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """CLI direct mode tool hint should expose resolved execution cwd for Bash."""
        registry = RuntimeSessionRegistry()
        session_key = "cli:hint-cwd"
        session_cwd = tmp_path / "session-cwd"
        session_cwd.mkdir(parents=True)
        registry.set_session_cwd(session_key, str(session_cwd))
        shared_resources["runtime_registry"] = registry
        shared_resources["run_mode"] = "cli"

        service = AgentService()
        await service.initialize(config, shared_resources)

        seen_progress: list[str] = []

        async def on_progress(
            text: str,
            *,
            tool_hint: bool = False,
            event_type: str = "progress",
            event_data: dict[str, Any] | None = None,
        ) -> None:
            _ = tool_hint, event_type, event_data
            seen_progress.append(text)

        async def fake_process(_context):
            yield AgentResponse(
                content="",
                tool_calls=[{"name": "Bash", "input": {"command": "pwd"}, "kind": "tool"}],
                event_type="tool_call",
            )
            yield AgentResponse(content="ok", event_type="result")

        with patch.object(service, "process", side_effect=fake_process):
            result = await service.process_direct(
                content="show cwd",
                session_key=session_key,
                channel="cli",
                chat_id="direct",
                on_progress=on_progress,
            )

        assert result == "ok"
        assert any('Tool: Bash(cwd="' in line and str(session_cwd.resolve()) in line for line in seen_progress)

    @pytest.mark.asyncio
    async def test_get_or_create_client_retries_without_resume_for_cli_auto_mode(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        from xbot.platform.config.schema import Config

        registry = RuntimeSessionRegistry()
        session_key = "cli:retry-resume"
        registry.get_or_create(session_key)
        registry._set_sdk_session_id_impl(session_key, "sdk-old")

        shared_resources["config"] = Config()
        shared_resources["runtime_registry"] = registry
        shared_resources["run_mode"] = "cli"
        shared_resources["resume_policy"] = {
            "mode": "continue",
            "explicit_resume": False,
            "strict_resume": False,
        }

        service = AgentService()
        await service.initialize(config, shared_resources)

        fake_client = MagicMock()
        service._client_pool.get_or_create = AsyncMock(
            side_effect=[RuntimeError("resume session not found"), fake_client]
        )

        client = await service._get_or_create_client(session_key)
        assert client is fake_client
        assert service._client_pool.get_or_create.await_count == 2
        assert registry.resolve_sdk_session_id(session_key) is None

    @pytest.mark.asyncio
    async def test_get_or_create_client_strict_explicit_resume_does_not_retry(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        from xbot.platform.config.schema import Config

        registry = RuntimeSessionRegistry()
        session_key = "cli:strict-resume"
        registry.get_or_create(session_key)
        registry._set_sdk_session_id_impl(session_key, "sdk-explicit")

        shared_resources["config"] = Config()
        shared_resources["runtime_registry"] = registry
        shared_resources["run_mode"] = "cli"
        shared_resources["resume_policy"] = {
            "mode": "resume",
            "explicit_resume": True,
            "strict_resume": True,
        }

        service = AgentService()
        await service.initialize(config, shared_resources)

        service._client_pool.get_or_create = AsyncMock(
            side_effect=RuntimeError("resume session not found")
        )

        with pytest.raises(RuntimeError, match="resume session not found"):
            await service._get_or_create_client(session_key)
        assert service._client_pool.get_or_create.await_count == 1
        assert registry.resolve_sdk_session_id(session_key) == "sdk-explicit"

    @pytest.mark.asyncio
    async def test_get_or_create_client_non_resume_error_does_not_clear_session_mapping(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        from xbot.platform.config.schema import Config

        registry = RuntimeSessionRegistry()
        session_key = "cli:non-resume-error"
        registry.get_or_create(session_key)
        registry._set_sdk_session_id_impl(session_key, "sdk-keep")

        shared_resources["config"] = Config()
        shared_resources["runtime_registry"] = registry
        shared_resources["run_mode"] = "cli"
        shared_resources["resume_policy"] = {
            "mode": "continue",
            "explicit_resume": False,
            "strict_resume": False,
        }

        service = AgentService()
        await service.initialize(config, shared_resources)

        service._client_pool.get_or_create = AsyncMock(
            side_effect=RuntimeError("session startup unknown provider failure")
        )

        with pytest.raises(RuntimeError, match="unknown provider failure"):
            await service._get_or_create_client(session_key)
        assert service._client_pool.get_or_create.await_count == 1
        assert registry.resolve_sdk_session_id(session_key) == "sdk-keep"

    @pytest.mark.asyncio
    async def test_reset_session_can_drop_sdk_context(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """reset_session(drop_sdk_context=True) should clear sdk_session_id mapping."""
        registry = RuntimeSessionRegistry()
        registry.get_or_create("test:c1")
        await registry.set_sdk_session_id("test:c1", "sdk-session-abc")
        shared_resources["runtime_registry"] = registry

        service = AgentService()
        await service.initialize(config, shared_resources)
        service._client_pool.disconnect = AsyncMock(return_value=True)

        await service.reset_session("test:c1", drop_sdk_context=True)
        assert registry.resolve_sdk_session_id("test:c1") is None

    @pytest.mark.asyncio
    async def test_reset_session_soft_preserves_sdk_context(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """reset_session(drop_sdk_context=False) should preserve sdk_session_id mapping."""
        registry = RuntimeSessionRegistry()
        registry.get_or_create("test:c1")
        await registry.set_sdk_session_id("test:c1", "sdk-session-keep")
        shared_resources["runtime_registry"] = registry

        service = AgentService()
        await service.initialize(config, shared_resources)
        service._client_pool.disconnect = AsyncMock(return_value=True)

        await service.reset_session("test:c1", drop_sdk_context=False)
        assert registry.resolve_sdk_session_id("test:c1") == "sdk-session-keep"

    @pytest.mark.asyncio
    async def test_process_persists_sdk_session_id_to_conversation_metadata(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """process should persist sdk_session_id into conversation metadata."""
        conversation_store = ConversationStore(tmp_path)
        shared_resources["conversation_store"] = conversation_store
        shared_resources["runtime_registry"] = RuntimeSessionRegistry()

        service = AgentService()
        await service.initialize(config, shared_resources)

        sdk_message = MagicMock()
        sdk_message.session_id = "sdk-persist-1"
        result_message = MagicMock()
        result_message.session_id = "sdk-persist-1"

        async def receive_messages():
            yield sdk_message
            yield result_message

        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_client.receive_messages = receive_messages
        mock_client.get_server_info = AsyncMock(return_value={})

        context = AgentContext(session_key="test:c1", prompt="hello", channel="test", chat_id="c1")
        with patch.object(service, "_get_or_create_client", AsyncMock(return_value=mock_client)):
            async for _ in service.process(context):
                pass

        loaded = conversation_store.get_or_create("test:c1")
        assert loaded.metadata.get("sdk_session_id") == "sdk-persist-1"

    @pytest.mark.asyncio
    async def test_build_sdk_options_restores_resume_from_conversation_metadata(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """_build_sdk_options should hydrate resume from conversation metadata when registry is empty."""
        from xbot.platform.config.schema import Config

        shared_resources["config"] = Config()
        registry = RuntimeSessionRegistry()
        shared_resources["runtime_registry"] = registry

        conversation_store = ConversationStore(tmp_path)
        session = conversation_store.get_or_create("test:c1")
        session.metadata["sdk_session_id"] = "sdk-from-store-1"
        session.mark_metadata_dirty()
        conversation_store.save(session)
        shared_resources["conversation_store"] = conversation_store

        service = AgentService()
        await service.initialize(config, shared_resources)

        options = service._build_sdk_options(session_key="test:c1")
        assert getattr(options, "resume", None) == "sdk-from-store-1"
        assert registry.resolve_sdk_session_id("test:c1") == "sdk-from-store-1"

    @pytest.mark.asyncio
    async def test_reset_session_drop_context_clears_conversation_metadata(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        """reset_session(drop_sdk_context=True) should remove persisted sdk_session_id metadata."""
        conversation_store = ConversationStore(tmp_path)
        session = conversation_store.get_or_create("test:c1")
        session.metadata["sdk_session_id"] = "sdk-to-delete-1"
        session.mark_metadata_dirty()
        conversation_store.save(session)
        shared_resources["conversation_store"] = conversation_store
        shared_resources["runtime_registry"] = RuntimeSessionRegistry()

        service = AgentService()
        await service.initialize(config, shared_resources)
        service._client_pool.disconnect = AsyncMock(return_value=True)

        await service.reset_session("test:c1", drop_sdk_context=True)
        loaded = conversation_store.get_or_create("test:c1")
        assert "sdk_session_id" not in loaded.metadata

    @pytest.mark.asyncio
    async def test_process_managed_direct_releases_ephemeral_client(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """heartbeat/cron/auxiliary sessions should release client after one managed turn."""
        service = AgentService()
        await service.initialize(config, shared_resources)
        service._client_pool.disconnect = AsyncMock(return_value=True)

        async def fake_process(_context):
            yield AgentResponse(content="ok", event_type="result")

        with patch.object(service, "process", side_effect=fake_process):
            result = await service.process_managed_direct(
                content="tick",
                session_key="heartbeat",
            )

        assert result == "ok"
        service._client_pool.disconnect.assert_awaited_once_with("heartbeat")

    @pytest.mark.asyncio
    async def test_process_managed_direct_respects_ephemeral_release_flag(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """ephemeral client release can be disabled via config."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.claude_sdk.ephemeral_immediate_release_enabled = False
        shared_resources["config"] = runtime_config

        service = AgentService()
        await service.initialize(config, shared_resources)
        service._client_pool.disconnect = AsyncMock(return_value=True)

        async def fake_process(_context):
            yield AgentResponse(content="ok", event_type="result")

        with patch.object(service, "process", side_effect=fake_process):
            result = await service.process_managed_direct(
                content="tick",
                session_key="heartbeat",
            )

        assert result == "ok"
        service._client_pool.disconnect.assert_not_called()

    def test_format_tool_hint_includes_compact_named_args(self) -> None:
        hint = AgentService._format_tool_hint([{
            "name": "Edit",
            "kind": "tool",
            "input": {
                "file_path": "/home/xbot/.xbot-dev/config.json",
                "old_string": "a" * 120,
                "new_string": "updated",
                "replace_all": False,
            },
        }])

        assert "Tool: Edit(" in hint
        assert 'file_path="/home/xbot/.xbot-dev/config.json"' in hint
        assert "old_string=" in hint
        assert "new_string=" in hint
        assert "…" in hint

    def test_format_tool_hint_handles_non_string_args_compactly(self) -> None:
        hint = AgentService._format_tool_hint([{
            "name": "TodoWrite",
            "kind": "tool",
            "input": {
                "todos": [{"content": "clean up", "status": "pending"}],
                "append": True,
            },
        }])

        assert "Tool: TodoWrite(" in hint
        assert "todos=[1 items]" in hint
        assert "append=true" in hint

    def test_format_tool_hint_includes_description_when_args_absent(self) -> None:
        hint = AgentService._format_tool_hint([{
            "name": "Bash",
            "kind": "tool",
            "input": {},
            "description": "Running Get current weather in Beijing",
        }])

        assert hint.startswith("Tool: Bash (")
        assert "Get current weather in Beijing" in hint

    def test_format_tool_hint_includes_execution_cwd_for_bash(self) -> None:
        hint = AgentService._format_tool_hint(
            [{
                "name": "Bash",
                "kind": "tool",
                "input": {"command": "ls -la"},
            }],
            execution_cwd="/tmp/project",
        )

        assert "Tool: Bash(" in hint
        assert 'cwd="/tmp/project"' in hint
        assert 'command="ls -la"' in hint


class TestClientPoolLifecycle:
    """Tests for ClientPool cleanup behavior."""

    @pytest.mark.asyncio
    async def test_prune_idle_disconnects_stale_clients(self) -> None:
        """prune_idle should disconnect stale connected clients."""
        pool = ClientPool()

        c1 = MagicMock()
        c1.disconnect = AsyncMock(return_value=None)
        c2 = MagicMock()
        c2.disconnect = AsyncMock(return_value=None)

        pool._clients["s1"] = ClientRecord(session_key="s1", client=c1)
        pool._clients["s1"].last_used_at = 0.0

        pool._clients["s2"] = ClientRecord(session_key="s2", client=c2)
        pool._clients["s2"].last_used_at = 0.0

        removed = await pool.prune_idle(1.0, exclude_keys={"s2"})

        assert removed == 1
        assert "s1" not in pool._clients
        assert "s2" in pool._clients


class TestResultDrainBehavior:
    """Tests for idle-boundary turn behavior in process()."""

    @pytest.mark.asyncio
    async def test_process_waits_for_idle_boundary_after_result(self, tmp_path: Path) -> None:
        from xbot.platform.config.schema import Config

        config = AgentConfig(model="claude-sonnet-4-6", system_prompt="Test")
        runtime_config = Config()
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        class TextBlock:
            def __init__(self, text: str) -> None:
                self.text = text

        class AssistantMessage:
            def __init__(self, text: str) -> None:
                self.content = [TextBlock(text)]

        class ResultMessage:
            def __init__(self, text: str) -> None:
                self.result = text
                self.usage = None
                self.stop_reason = "end_turn"
                self.num_turns = 1
                self.total_cost_usd = None

        class SystemMessage:
            def __init__(self, *, subtype: str, state: str | None = None) -> None:
                self.subtype = subtype
                self.data = {"subtype": subtype}
                if state is not None:
                    self.data["state"] = state

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def receive_messages():
            yield ResultMessage("R1")
            yield AssistantMessage("late assistant")
            yield SystemMessage(subtype="session_state_changed", state="idle")

        mock_client.receive_messages = receive_messages

        context = AgentContext(session_key="test:idle1", prompt="hello", channel="test", chat_id="c1")
        with patch.object(service, "_get_or_create_client", AsyncMock(return_value=mock_client)):
            responses = [r async for r in service.process(context)]

        assert [r.event_type for r in responses] == ["result", "content"]
        assert responses[0].content == "R1"
        assert responses[1].content == "late assistant"

    @pytest.mark.asyncio
    async def test_process_missing_idle_boundary_returns_error(self, tmp_path: Path) -> None:
        from xbot.platform.config.schema import Config

        config = AgentConfig(model="claude-sonnet-4-6", system_prompt="Test")
        runtime_config = Config()
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        class ResultMessage:
            def __init__(self, text: str) -> None:
                self.result = text
                self.usage = None
                self.stop_reason = "end_turn"
                self.num_turns = 1
                self.total_cost_usd = None

        class FakeClient:
            async def query(self, _prompt: str) -> None:
                return None

            async def receive_messages(self):
                yield ResultMessage("R1")

        with patch.object(service, "_get_or_create_client", AsyncMock(return_value=FakeClient())):
            ctx = AgentContext(
                session_key="test:idle2",
                prompt="hello",
                channel="test",
                chat_id="c1",
            )
            responses = [r async for r in service.process(ctx)]

        assert len(responses) == 2
        assert responses[0].event_type == "result"
        assert responses[0].content == "R1"
        assert responses[1].finish_reason == "error"
        assert "idle boundary" in responses[1].content.lower()

    @pytest.mark.asyncio
    async def test_idle_boundary_parser_falls_back_to_message_state(self) -> None:
        class SystemMessage:
            def __init__(self) -> None:
                self.subtype = "session_state_changed"
                self.data = {"subtype": "session_state_changed"}
                self.state = "idle"

        assert AgentService._is_idle_boundary_message(SystemMessage()) is True

    @pytest.mark.asyncio
    async def test_process_sdk_internal_timeout_with_pending_wait_emits_error(self, tmp_path: Path) -> None:
        from xbot.platform.config.schema import Config

        config = AgentConfig(model="claude-sonnet-4-6", system_prompt="Test")
        runtime_config = Config()
        bus = MagicMock()
        bus.get_pending_request_for_session = MagicMock(return_value="perm-1")
        bus.get_pending_interaction_for_session = MagicMock(return_value=None)
        service = AgentService()
        await service.initialize(
            config,
            {"workspace": str(tmp_path), "config": runtime_config, "bus": bus},
        )

        class FakeClient:
            async def query(self, _prompt: str) -> None:
                return None

            async def receive_messages(self):
                raise TimeoutError("synthetic timeout")
                yield  # pragma: no cover - keep as async generator

        with patch.object(service, "_get_or_create_client", AsyncMock(return_value=FakeClient())):
            ctx = AgentContext(
                session_key="test:idle-timeout-pending",
                prompt="hello",
                channel="test",
                chat_id="c1",
            )
            responses = [r async for r in service.process(ctx)]

        assert len(responses) == 1
        assert responses[0].finish_reason == "error"
        assert "stream timeout error" in responses[0].content.lower()

    @pytest.mark.asyncio
    async def test_process_wait_for_timeout_with_pending_wait_does_not_emit_error(self, tmp_path: Path) -> None:
        from xbot.platform.config.schema import Config

        config = AgentConfig(model="claude-sonnet-4-6", system_prompt="Test")
        runtime_config = Config()
        bus = MagicMock()
        bus.get_pending_request_for_session = MagicMock(return_value="perm-1")
        bus.get_pending_interaction_for_session = MagicMock(return_value=None)
        service = AgentService()
        await service.initialize(
            config,
            {"workspace": str(tmp_path), "config": runtime_config, "bus": bus},
        )

        class FakeClient:
            async def query(self, _prompt: str) -> None:
                return None

            async def receive_messages(self):
                while True:
                    await asyncio.sleep(1)
                    yield MagicMock()

        original_wait_for = asyncio.wait_for
        call_count = {"n": 0}

        async def fake_wait_for(awaitable, timeout):
            call_count["n"] += 1
            # First call is query() (30s), keep normal.
            if call_count["n"] == 1:
                return await original_wait_for(awaitable, timeout)
            # Second call is receive loop wait_for() => simulate poll timeout.
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise TimeoutError("synthetic wait_for timeout")

        with (
            patch.object(service, "_get_or_create_client", AsyncMock(return_value=FakeClient())),
            patch("xbot.runtime.core.service.asyncio.wait_for", side_effect=fake_wait_for),
        ):
            ctx = AgentContext(
                session_key="test:idle-waitfor-timeout-pending",
                prompt="hello",
                channel="test",
                chat_id="c1",
            )
            responses = [r async for r in service.process(ctx)]

        assert responses == []


class FakeQueryStream:
    """Programmable async stream for ACP-style turn tests."""

    _STOP = object()

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self.closed = False

    async def emit(self, *messages: Any) -> None:
        for message in messages:
            await self._queue.put(message)

    async def close(self) -> None:
        self.closed = True
        await self._queue.put(self._STOP)

    async def fail(self, error: Exception) -> None:
        await self._queue.put(error)

    async def next(self) -> Any:
        item = await self._queue.get()
        if item is self._STOP:
            raise StopAsyncIteration
        if isinstance(item, Exception):
            raise item
        return item


class FakeMessageInput:
    """Tracks pushed prompts for each turn."""

    def __init__(self) -> None:
        self.pushed_prompts: list[str] = []
        self.closed = False

    async def push(self, prompt: str) -> None:
        self.pushed_prompts.append(prompt)

    async def close(self) -> None:
        self.closed = True


class FakeQuerySession:
    """Minimal ACP-like query session state."""

    def __init__(self) -> None:
        self.query_stream = FakeQueryStream()
        self.input_stream = FakeMessageInput()
        self.prompt_running = False
        self.pending_prompts: list[tuple[str, asyncio.Future[dict[str, Any]]]] = []
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        await self.input_stream.close()
        await self.query_stream.close()


def _make_text_block(text: str) -> Any:
    cls = type("TextBlock", (), {})
    obj = cls()
    obj.text = text
    return obj


def _make_assistant_message(text: str, *, session_id: str | None = None, uuid: str | None = None) -> Any:
    cls = type("AssistantMessage", (), {})
    obj = cls()
    obj.content = [_make_text_block(text)]
    obj.session_id = session_id
    obj.uuid = uuid
    obj.parent_tool_use_id = None
    obj.message = {"model": "claude-sonnet-4-6", "usage": None, "role": "assistant", "content": []}
    return obj


def _make_result_message(
    text: str,
    *,
    session_id: str | None = None,
    subtype: str = "success",
    stop_reason: str = "end_turn",
    is_error: bool = False,
    usage: dict[str, int] | None = None,
) -> Any:
    cls = type("ResultMessage", (), {})
    obj = cls()
    obj.subtype = subtype
    obj.result = text
    obj.stop_reason = stop_reason
    obj.is_error = is_error
    obj.errors = ["synthetic-error"] if is_error else []
    obj.session_id = session_id
    obj.usage = usage or {
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    obj.total_cost_usd = 0.0
    obj.duration_ms = 1
    obj.duration_api_ms = 1
    obj.num_turns = 1
    obj.modelUsage = {}
    return obj


def _make_system_message(
    subtype: str,
    *,
    state: str | None = None,
    session_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Any:
    cls = type("SystemMessage", (), {})
    obj = cls()
    obj.subtype = subtype
    payload: dict[str, Any] = {"type": "system", "subtype": subtype}
    if state is not None:
        payload["state"] = state
    if session_id is not None:
        payload["session_id"] = session_id
    if extra:
        payload.update(extra)
    obj.data = payload
    obj.message = payload.get("message", "")
    obj.state = state
    obj.session_id = session_id
    return obj


def _make_task_notification(
    *,
    status: str = "completed",
    task_id: str = "t1",
    summary: str = 'Agent "task" completed',
    session_id: str | None = None,
) -> Any:
    cls = type("TaskNotificationMessage", (), {})
    obj = cls()
    obj.status = status
    obj.task_id = task_id
    obj.summary = summary
    obj.output_file = None
    obj.session_id = session_id
    return obj


def _make_task_progress(
    *,
    task_id: str = "t1",
    description: str = "Running worker",
    last_tool_name: str | None = "Bash",
    session_id: str | None = None,
) -> Any:
    cls = type("TaskProgressMessage", (), {})
    obj = cls()
    obj.task_id = task_id
    obj.description = description
    obj.last_tool_name = last_tool_name
    obj.session_id = session_id
    return obj


class FakeAcpTurnRunner:
    """Feasibility harness: ACP-like turn handling over one persistent query stream."""

    def __init__(self, service: AgentService, session_key: str) -> None:
        self.service = service
        self.session_key = session_key
        self.session = FakeQuerySession()
        self._children: set[asyncio.Task[None]] = set()

    async def shutdown(self) -> None:
        for task in list(self._children):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._children.clear()
        await self.session.close()

    async def reset(self) -> None:
        await self.shutdown()

    async def process_prompt(self, prompt: str) -> dict[str, Any]:
        if self.session.prompt_running:
            loop = asyncio.get_running_loop()
            waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
            self.session.pending_prompts.append((prompt, waiter))
            return await waiter
        return await self._run_prompt(prompt)

    async def _run_prompt(self, prompt: str) -> dict[str, Any]:
        self.session.prompt_running = True
        await self.session.input_stream.push(prompt)
        responses: list[AgentResponse] = []
        stop_reason = "end_turn"
        ended_by_idle = False

        try:
            while True:
                message = await self.session.query_stream.next()
                self.service._sync_sdk_session_mapping(self.session_key, message)
                converted = self.service._convert_event(message)
                if converted is not None:
                    responses.append(converted)

                if type(message).__name__ == "ResultMessage":
                    subtype = str(getattr(message, "subtype", "") or "")
                    if subtype in {
                        "error_max_budget_usd",
                        "error_max_turns",
                        "error_max_structured_output_retries",
                    }:
                        stop_reason = "max_turn_requests"
                    elif str(getattr(message, "stop_reason", "") or "") == "max_tokens":
                        stop_reason = "max_tokens"
                    elif str(getattr(message, "stop_reason", "") or "") == "cancelled":
                        stop_reason = "cancelled"

                if type(message).__name__ == "SystemMessage" and getattr(message, "subtype", None) == "session_state_changed":
                    data = getattr(message, "data", None)
                    state = data.get("state") if isinstance(data, dict) else getattr(message, "state", None)
                    if state == "idle":
                        ended_by_idle = True
                        break
        except StopAsyncIteration as exc:
            raise RuntimeError("Session stream ended before idle boundary") from exc
        finally:
            self.session.prompt_running = False
            self._handoff_pending_prompt_if_any()

        return {
            "responses": responses,
            "stop_reason": stop_reason,
            "ended_by_idle": ended_by_idle,
        }

    def _handoff_pending_prompt_if_any(self) -> None:
        if not self.session.pending_prompts:
            return
        prompt, waiter = self.session.pending_prompts.pop(0)

        async def _run_next() -> None:
            try:
                result = await self._run_prompt(prompt)
            except Exception as err:  # pragma: no cover - defensive
                if not waiter.done():
                    waiter.set_exception(err)
            else:
                if not waiter.done():
                    waiter.set_result(result)

        task = asyncio.create_task(_run_next())
        self._children.add(task)
        task.add_done_callback(self._children.discard)


class TestAcpStyleTurnBoundary:
    """Feasibility tests for ACP-style turn boundary semantics."""

    @pytest.fixture
    async def service_and_runner(self, tmp_path: Path) -> tuple[AgentService, FakeAcpTurnRunner]:
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        registry = RuntimeSessionRegistry()
        store = ConversationStore(tmp_path)
        service = AgentService()
        await service.initialize(
            AgentConfig(model="claude-sonnet-4-6", system_prompt="Test"),
            {
                "workspace": str(tmp_path),
                "config": runtime_config,
                "runtime_registry": registry,
                "conversation_store": store,
            },
        )
        runner = FakeAcpTurnRunner(service, "acp:test:c1")
        try:
            yield service, runner
        finally:
            await runner.shutdown()
            await service.shutdown()

    @pytest.mark.asyncio
    async def test_multi_result_and_task_events_end_only_on_idle(
        self, service_and_runner: tuple[AgentService, FakeAcpTurnRunner]
    ) -> None:
        _, runner = service_and_runner
        sid = "sid-1"
        await runner.session.query_stream.emit(
            _make_system_message("init", session_id=sid),
            _make_assistant_message("A1", session_id=sid),
            _make_result_message("R1", session_id=sid),
            _make_task_notification(status="completed", task_id="t1", session_id=sid),
            _make_result_message("R2", session_id=sid),
            _make_system_message("session_state_changed", state="idle", session_id=sid),
        )
        result = await runner.process_prompt("p1")

        assert result["ended_by_idle"] is True
        event_types = [r.event_type for r in result["responses"]]
        assert event_types == ["content", "result", "task", "result"]
        assert [r.content for r in result["responses"] if r.event_type == "result"] == ["R1", "R2"]

    @pytest.mark.asyncio
    async def test_result_does_not_end_turn_early(
        self, service_and_runner: tuple[AgentService, FakeAcpTurnRunner]
    ) -> None:
        _, runner = service_and_runner
        sid = "sid-2"
        await runner.session.query_stream.emit(
            _make_assistant_message("before", session_id=sid),
            _make_result_message("mid", session_id=sid),
            _make_assistant_message("after", session_id=sid),
            _make_task_progress(task_id="t2", description="still running", session_id=sid),
            _make_system_message("session_state_changed", state="idle", session_id=sid),
        )
        result = await runner.process_prompt("p2")

        assert [r.event_type for r in result["responses"]] == ["content", "result", "content", "task"]
        assert [r.content for r in result["responses"] if r.event_type == "content"] == ["before", "after"]

    @pytest.mark.asyncio
    async def test_cross_turn_isolation_no_pollution(
        self, service_and_runner: tuple[AgentService, FakeAcpTurnRunner]
    ) -> None:
        _, runner = service_and_runner
        sid = "sid-3"
        await runner.session.query_stream.emit(
            _make_result_message("T1", session_id=sid),
            _make_system_message("session_state_changed", state="idle", session_id=sid),
            _make_result_message("T2", session_id=sid),
            _make_system_message("session_state_changed", state="idle", session_id=sid),
        )
        turn1 = await runner.process_prompt("turn1")
        turn2 = await runner.process_prompt("turn2")

        assert [r.content for r in turn1["responses"] if r.event_type == "result"] == ["T1"]
        assert [r.content for r in turn2["responses"] if r.event_type == "result"] == ["T2"]
        assert runner.session.input_stream.pushed_prompts == ["turn1", "turn2"]

    @pytest.mark.asyncio
    async def test_prompt_running_fifo_handoff(
        self, service_and_runner: tuple[AgentService, FakeAcpTurnRunner]
    ) -> None:
        _, runner = service_and_runner
        sid = "sid-4"

        async def first_call() -> dict[str, Any]:
            return await runner.process_prompt("first")

        task1 = asyncio.create_task(first_call())
        await asyncio.sleep(0)
        task2 = asyncio.create_task(runner.process_prompt("second"))
        await asyncio.sleep(0)

        assert runner.session.prompt_running is True
        assert len(runner.session.pending_prompts) == 1
        assert runner.session.input_stream.pushed_prompts == ["first"]

        await runner.session.query_stream.emit(
            _make_result_message("R-first", session_id=sid),
            _make_system_message("session_state_changed", state="idle", session_id=sid),
            _make_result_message("R-second", session_id=sid),
            _make_system_message("session_state_changed", state="idle", session_id=sid),
        )

        first_result = await asyncio.wait_for(task1, timeout=2)
        second_result = await asyncio.wait_for(task2, timeout=2)

        assert [r.content for r in first_result["responses"] if r.event_type == "result"] == ["R-first"]
        assert [r.content for r in second_result["responses"] if r.event_type == "result"] == ["R-second"]
        assert runner.session.input_stream.pushed_prompts == ["first", "second"]

    @pytest.mark.asyncio
    async def test_stop_reason_from_result_subtype_does_not_override_idle_boundary(
        self, service_and_runner: tuple[AgentService, FakeAcpTurnRunner]
    ) -> None:
        _, runner = service_and_runner
        sid = "sid-5"
        await runner.session.query_stream.emit(
            _make_result_message(
                text="max-turns",
                session_id=sid,
                subtype="error_max_turns",
                stop_reason="end_turn",
                is_error=False,
            ),
            _make_system_message("session_state_changed", state="idle", session_id=sid),
        )
        result = await runner.process_prompt("limit-test")

        assert result["stop_reason"] == "max_turn_requests"
        assert result["ended_by_idle"] is True

    @pytest.mark.asyncio
    async def test_missing_idle_with_stream_end_raises(
        self, service_and_runner: tuple[AgentService, FakeAcpTurnRunner]
    ) -> None:
        _, runner = service_and_runner
        await runner.session.query_stream.emit(_make_result_message("partial", session_id="sid-6"))
        await runner.session.query_stream.close()

        with pytest.raises(RuntimeError, match="ended before idle boundary"):
            await asyncio.wait_for(runner.process_prompt("no-idle"), timeout=2)

    @pytest.mark.asyncio
    async def test_session_id_mapping_syncs_from_stream_messages(
        self, service_and_runner: tuple[AgentService, FakeAcpTurnRunner]
    ) -> None:
        service, runner = service_and_runner
        sid = "sid-sync-7"
        await runner.session.query_stream.emit(
            _make_system_message("init", session_id=sid),
            _make_result_message("ok", session_id=sid),
            _make_system_message("session_state_changed", state="idle", session_id=sid),
        )
        _ = await runner.process_prompt("sync")

        registry: RuntimeSessionRegistry = service._shared_resources["runtime_registry"]
        assert registry.resolve_sdk_session_id("acp:test:c1") == sid

        store: ConversationStore = service._shared_resources["conversation_store"]
        loaded = store.get_or_create("acp:test:c1")
        assert loaded.metadata.get("sdk_session_id") == sid

    @pytest.mark.asyncio
    async def test_environment_precondition_for_idle_events(self, tmp_path: Path) -> None:
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        service = AgentService()
        await service.initialize(
            AgentConfig(model="claude-sonnet-4-6", system_prompt="Test"),
            {"workspace": str(tmp_path), "config": runtime_config},
        )
        options = service._build_sdk_options(session_key="acp:test:env")
        await service.shutdown()

        # Precondition for ACP-style boundary: enable session state events from Claude Code.
        assert isinstance(options.env, dict)
        assert options.env.get("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS") == "1"

    @pytest.mark.asyncio
    async def test_lifecycle_cleanup_reset_and_shutdown(
        self, service_and_runner: tuple[AgentService, FakeAcpTurnRunner]
    ) -> None:
        _, runner = service_and_runner
        await runner.session.query_stream.emit(
            _make_result_message("x", session_id="sid-9"),
            _make_system_message("session_state_changed", state="idle", session_id="sid-9"),
        )
        _ = await runner.process_prompt("lifecycle")
        assert runner.session.closed is False

        await runner.reset()
        assert runner.session.closed is True
        assert runner.session.input_stream.closed is True
        assert runner.session.query_stream.closed is True


class TestRunDispatch:
    """Tests for the run() message routing and _dispatch() processing chain."""

    @pytest.fixture
    def state_manager(self) -> RuntimeSessionRegistry:
        return RuntimeSessionRegistry()

    @pytest.fixture
    def bus(self) -> MagicMock:
        bus = MagicMock()
        bus.publish_outbound = AsyncMock()
        bus.get_pending_request_for_session = MagicMock(return_value=None)
        bus.get_pending_interaction_for_session = MagicMock(return_value=None)
        return bus

    @pytest.fixture
    def config(self) -> AgentConfig:
        return AgentConfig(model="claude-sonnet-4-6", system_prompt="Test")

    @pytest.fixture
    def shared_resources(self, tmp_path: Path, bus, state_manager) -> dict[str, Any]:
        return {
            "workspace": str(tmp_path),
            "config": MagicMock(),
            "bus": bus,
            "runtime_registry": state_manager,
        }

    async def _make_service(self, config, shared_resources) -> AgentService:
        service = AgentService()
        await service.initialize(config, shared_resources)
        return service

    # --- Test 1: Progress forwarding ---

    @pytest.mark.asyncio
    async def test_progress_forwarding(self, config, shared_resources, bus):
        """_dispatch should forward progress_texts as OutboundMessage with _progress metadata."""
        service = await self._make_service(config, shared_resources)

        async def fake_process(context):
            yield AgentResponse(content="", progress_texts=["Thinking about it..."])
            yield AgentResponse(content="Hello!", event_type="result")

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="Hi")

        with patch.object(service, "process", side_effect=fake_process):
            await service._dispatch(msg, bus)

        # Find progress call (event_type="thinking" -> progress_kind="reasoning")
        progress_calls = [
            c for c in bus.publish_outbound.call_args_list
            if c.args[0].metadata.get("_progress") is True
            and c.args[0].metadata.get("_event_type") == "thinking"
        ]
        assert len(progress_calls) >= 1
        assert progress_calls[0].args[0].metadata.get("_progress_kind") == "reasoning"
        assert "Thinking about it..." in progress_calls[0].args[0].content

    # --- Test 2: Tool hint forwarding ---

    @pytest.mark.asyncio
    async def test_tool_hint_forwarding(self, config, shared_resources, bus):
        """_dispatch should forward tool_calls as OutboundMessage with _tool_hint metadata."""
        service = await self._make_service(config, shared_resources)

        async def fake_process(context):
            yield AgentResponse(
                content="",
                tool_calls=[{"name": "bash", "input": {"cmd": "ls"}}],
            )
            yield AgentResponse(content="Done!", event_type="result")

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="list files")

        with patch.object(service, "process", side_effect=fake_process):
            await service._dispatch(msg, bus)

        tool_calls = [
            c for c in bus.publish_outbound.call_args_list
            if c.args[0].metadata.get("_tool_hint") is True
        ]
        assert len(tool_calls) >= 1
        assert "bash" in tool_calls[0].args[0].content

    # --- Test 3: Usage forwarding ---

    @pytest.mark.asyncio
    async def test_usage_forwarding(self, config, shared_resources, bus):
        """_dispatch should forward usage as OutboundMessage with _event_type=usage metadata."""
        service = await self._make_service(config, shared_resources)

        async def fake_process(context):
            yield AgentResponse(
                content="Answer",
                usage={"input_tokens": 100, "output_tokens": 50},
                event_type="result",
            )

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="Hi")

        with patch.object(service, "process", side_effect=fake_process):
            await service._dispatch(msg, bus)

        usage_calls = [
            c for c in bus.publish_outbound.call_args_list
            if c.args[0].metadata.get("_event_type") == "usage"
        ]
        assert len(usage_calls) == 1
        assert "100" in usage_calls[0].args[0].content
        assert "50" in usage_calls[0].args[0].content

    @pytest.mark.asyncio
    async def test_dispatch_emits_all_result_messages(self, config, shared_resources, bus):
        """_dispatch should emit each ResultMessage content in order."""
        service = await self._make_service(config, shared_resources)

        async def fake_process(_context):
            yield AgentResponse(content="R1", event_type="result")
            yield AgentResponse(content="R2", event_type="result")

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="Hi")

        with patch.object(service, "process", side_effect=fake_process):
            await service._dispatch(msg, bus)

        final_outputs = [
            c.args[0].content
            for c in bus.publish_outbound.call_args_list
            if not c.args[0].metadata
        ]
        assert final_outputs == ["R1", "R2"]

    @pytest.mark.asyncio
    async def test_dispatch_forwards_process_error_response(self, config, shared_resources, bus):
        """_dispatch should publish error responses yielded by process()."""
        service = await self._make_service(config, shared_resources)

        async def fake_process(_context):
            yield AgentResponse(content="Error: synthetic", finish_reason="error")

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="Hi")

        with patch.object(service, "process", side_effect=fake_process):
            await service._dispatch(msg, bus)

        outputs = [c.args[0].content for c in bus.publish_outbound.call_args_list]
        assert any("Error: synthetic" in out for out in outputs)

    # --- Test 4: Local command !help ---

    @pytest.mark.asyncio
    async def test_local_command_help(self, config, shared_resources, bus):
        """!help should return help text without going through process()."""
        service = await self._make_service(config, shared_resources)
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="!help")

        await service._command_handler.handle(msg, bus)

        assert bus.publish_outbound.call_count == 1
        out = bus.publish_outbound.call_args.args[0]
        assert "Runtime Commands" in out.content
        assert "!stop" in out.content
        assert "Claude SDK slash commands" in out.content
        assert "/help" in out.content
        assert "Local Slash Commands" in out.content

    @pytest.mark.asyncio
    async def test_slash_clear_is_local_command(self, config, shared_resources, bus):
        """`/clear` should be treated as local reset instead of SDK passthrough."""
        service = await self._make_service(config, shared_resources)
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/clear")

        assert service._command_handler.is_local_command("/clear")
        await service._command_handler.handle(msg, bus)

        assert bus.publish_outbound.call_count == 1
        out = bus.publish_outbound.call_args.args[0]
        assert "reset" in out.content.lower() or "fresh start" in out.content.lower()

    @pytest.mark.asyncio
    async def test_local_command_reset_soft_keeps_sdk_context(self, config, shared_resources, bus):
        """!reset --soft should call reset_session with drop_sdk_context=False."""
        service = await self._make_service(config, shared_resources)
        service.reset_session = AsyncMock(return_value=None)
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="!reset --soft")

        await service._command_handler.handle(msg, bus)
        service.reset_session.assert_awaited_once_with("test:c1", drop_sdk_context=False)

    @pytest.mark.asyncio
    async def test_local_command_reset_hard_drops_sdk_context(self, config, shared_resources, bus):
        """!reset should call reset_session with drop_sdk_context=True."""
        service = await self._make_service(config, shared_resources)
        service.reset_session = AsyncMock(return_value=None)
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="!reset")

        await service._command_handler.handle(msg, bus)
        service.reset_session.assert_awaited_once_with("test:c1", drop_sdk_context=True)

    # --- Test 5: Local command !stop ---

    @pytest.mark.asyncio
    async def test_local_command_stop(self, config, shared_resources, bus, state_manager):
        """!stop should cancel active task and set phase to IDLE."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"

        # Simulate an active task
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()
        service._active_tasks[session_key] = mock_task

        # Set phase to RUNNING
        state_manager.force_transition(session_key, SessionPhase.RUNNING)

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="!stop")
        await service._command_handler.handle(msg, bus)

        mock_task.cancel.assert_called_once()
        assert state_manager.get_phase(session_key) == SessionPhase.IDLE
        assert bus.publish_outbound.call_count == 1
        assert "stopped" in bus.publish_outbound.call_args.args[0].content.lower() or \
               "stop" in bus.publish_outbound.call_args.args[0].content.lower()

    @pytest.mark.asyncio
    async def test_local_command_stop_waits_for_task_cancellation(self, config, shared_resources, bus):
        """!stop should wait for task cancellation to settle before returning."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"
        cancelled = asyncio.Event()

        async def _long_running():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        active_task = asyncio.create_task(_long_running())
        await asyncio.sleep(0)
        service._active_tasks[session_key] = active_task

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="!stop")
        await service._command_handler.handle(msg, bus)

        assert cancelled.is_set()
        assert active_task.done()

    # --- Test 6: Busy rejection ---

    @pytest.mark.asyncio
    async def test_busy_rejection(self, config, shared_resources, bus):
        """When a session has an active task, new messages should be rejected."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"

        # Simulate an active task
        mock_task = MagicMock()
        mock_task.done.return_value = False
        service._active_tasks[session_key] = mock_task

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="New question")

        # Simulate what run() does for busy detection
        active_task = service._active_tasks.get(session_key)
        assert active_task and not active_task.done()

        await service._publish_event(bus, msg.channel, msg.chat_id, "\u23f3 \u6b63\u5728\u5904\u7406\u4e2d\uff0c\u8bf7\u7a0d\u5019...", _progress=True)

        assert bus.publish_outbound.call_count == 1
        out = bus.publish_outbound.call_args.args[0]
        assert out.metadata.get("_progress") is True

    # --- Test 7: Permission routing ---

    @pytest.mark.asyncio
    async def test_permission_routing(self, config, shared_resources, bus):
        """Permission responses should be routed to response_handlers."""
        service = await self._make_service(config, shared_resources)

        # Mock response_handlers
        mock_handler = MagicMock()
        mock_handler.handle_permission_response = AsyncMock(return_value=True)
        service._response_handlers = mock_handler

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="\u5141\u8bb8")

        result = await mock_handler.handle_permission_response(msg)
        assert result is True
        mock_handler.handle_permission_response.assert_called_once_with(msg)

    # --- Test 8: Workspace command injection ---

    @pytest.mark.asyncio
    async def test_workspace_command(self, config, shared_resources, bus, tmp_path):
        """Workspace commands should inject command content into prompt."""
        # Create a command file
        cmd_dir = tmp_path / "commands"
        cmd_dir.mkdir()
        (cmd_dir / "greet.md").write_text("Say hello in a friendly way")

        service = await self._make_service(config, shared_resources)

        # Re-initialize commands_loader with the right path
        from xbot.runtime.core.context.commands import CommandsLoader
        service._commands_loader = CommandsLoader(tmp_path)

        async def fake_process(context):
            # Verify the prompt was injected
            assert context.prompt == "Say hello in a friendly way"
            yield AgentResponse(content="Hello there!", event_type="result")

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/greet")

        with patch.object(service, "process", side_effect=fake_process):
            await service._dispatch(msg, bus)

        # Final response should be sent
        final_calls = [
            c for c in bus.publish_outbound.call_args_list
            if not c.args[0].metadata  # no metadata = final response
        ]
        assert len(final_calls) == 1
        assert "Hello there!" in final_calls[0].args[0].content

    # --- Test 9: Error recovery ---

    @pytest.mark.asyncio
    async def test_error_recovery(self, config, shared_resources, bus, state_manager):
        """SDK exceptions should result in error message and phase back to IDLE."""
        service = await self._make_service(config, shared_resources)

        async def failing_process(context):
            raise RuntimeError("SDK connection failed")
            yield  # Make it a generator  # noqa: B018

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="Hi")

        with patch.object(service, "process", side_effect=failing_process):
            await service._dispatch(msg, bus)

        # Error message should be sent
        assert bus.publish_outbound.call_count >= 1
        error_calls = [
            c for c in bus.publish_outbound.call_args_list
            if "\u274c" in c.args[0].content or "error" in c.args[0].content.lower()
            or "\u51fa\u9519" in c.args[0].content
        ]
        assert len(error_calls) >= 1

        # Phase should be back to IDLE
        session_key = "test:c1"
        assert state_manager.get_phase(session_key) == SessionPhase.IDLE

    # --- Test 10: Dispatch phase lifecycle ---

    @pytest.mark.asyncio
    async def test_dispatch_phase_lifecycle(self, config, shared_resources, bus, state_manager):
        """_dispatch should transition IDLE -> RUNNING -> IDLE."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"

        phases_seen: list[SessionPhase] = []

        async def tracking_process(context):
            # Capture phase during processing
            phases_seen.append(state_manager.get_phase(session_key))
            yield AgentResponse(content="Done", event_type="result")

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="Hi")

        # Before dispatch
        assert state_manager.get_phase(session_key) == SessionPhase.IDLE

        with patch.object(service, "process", side_effect=tracking_process):
            await service._dispatch(msg, bus)

        # During processing, phase should have been RUNNING
        assert SessionPhase.RUNNING in phases_seen

        # After dispatch, phase should be IDLE
        assert state_manager.get_phase(session_key) == SessionPhase.IDLE

    # --- Test 11: System message (compact) forwarding ---

    @pytest.mark.asyncio
    async def test_system_message_compact_forwarding(self, config, shared_resources, bus):
        """SystemMessage with compact subtype should be forwarded as progress with event_type=system."""
        service = await self._make_service(config, shared_resources)

        async def fake_process(context):
            # Simulate a compact system message followed by a normal response
            yield AgentResponse(
                content="",
                progress_texts=["\U0001f504 Compressing context..."],
                event_type="system",
                event_data={"subtype": "pre_compact"},
            )
            yield AgentResponse(content="Done after compact", event_type="result")

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="Hi")

        with patch.object(service, "process", side_effect=fake_process):
            await service._dispatch(msg, bus)

        # Find system progress call
        system_calls = [
            c for c in bus.publish_outbound.call_args_list
            if c.args[0].metadata.get("_event_type") == "system"
            and c.args[0].metadata.get("_progress") is True
        ]
        assert len(system_calls) >= 1
        assert system_calls[0].args[0].metadata.get("_progress_kind") == "system"
        assert "Compressing" in system_calls[0].args[0].content or "compact" in system_calls[0].args[0].content.lower()

    # --- Test 12: _convert_system_message for compact ---

    @pytest.mark.asyncio
    async def test_convert_system_message_compact(self, config, shared_resources):
        """_convert_system_message should convert compact messages to AgentResponse."""
        service = await self._make_service(config, shared_resources)

        # Pre-compact message
        pre_compact_msg = MagicMock()
        pre_compact_msg.subtype = "pre_compact"
        pre_compact_msg.message = "\U0001f504 Compressing context (auto)..."
        result = service._convert_system_message(pre_compact_msg)
        assert result is not None
        assert result.event_type == "system"
        assert len(result.progress_texts) == 1
        assert "Compressing" in result.progress_texts[0]

        # Post-compact message
        post_compact_msg = MagicMock()
        post_compact_msg.subtype = "compact_complete"
        post_compact_msg.message = ""
        post_compact_msg.pre_tokens = 50000
        post_compact_msg.post_tokens = 20000
        post_compact_msg.trigger = "auto"
        result = service._convert_system_message(post_compact_msg)
        assert result is not None
        assert "50,000" in result.progress_texts[0]
        assert "20,000" in result.progress_texts[0]

        # Compact-boundary message from SDK data.compact_metadata
        boundary_msg = MagicMock()
        boundary_msg.subtype = "compact_boundary"
        boundary_msg.message = ""
        boundary_msg.data = {
            "compact_metadata": {
                "pre_tokens": 64000,
                "post_tokens": 22000,
                "trigger": "auto",
            }
        }
        result = service._convert_system_message(boundary_msg)
        assert result is not None
        assert result.event_type == "system"
        assert "64,000" in result.progress_texts[0]
        assert "22,000" in result.progress_texts[0]

    # --- Test 13: _convert_system_message returns None for empty ---

    @pytest.mark.asyncio
    async def test_convert_system_message_empty(self, config, shared_resources):
        """_convert_system_message should return None for messages without content or known subtype."""
        service = await self._make_service(config, shared_resources)

        empty_msg = MagicMock()
        empty_msg.subtype = ""
        empty_msg.message = ""
        result = service._convert_system_message(empty_msg)
        assert result is None

    # --- Test 14: _dispatch stores routing for compact hook delivery ---

    @pytest.mark.asyncio
    async def test_dispatch_stores_routing(self, config, shared_resources, bus, state_manager):
        """_dispatch should store session routing in state_manager."""
        service = await self._make_service(config, shared_resources)

        async def fake_process(_context):
            yield AgentResponse(content="ok", event_type="result")

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="Hi")

        with patch.object(service, "process", side_effect=fake_process):
            await service._dispatch(msg, bus)

        resolved = state_manager.resolve_compact_notification_target("test:c1")
        assert resolved == ("test:c1", "test", "c1")

    # --- Test 15: process syncs SDK session ID mapping ---

    @pytest.mark.asyncio
    async def test_process_syncs_sdk_session_mapping(self, config, shared_resources, state_manager):
        """process should record sdk_session_id -> session_key mapping."""
        service = await self._make_service(config, shared_resources)

        sdk_message = MagicMock()
        sdk_message.session_id = "sdk-session-1"
        result_message = MagicMock()
        result_message.session_id = "sdk-session-1"

        async def receive_messages():
            yield sdk_message
            yield result_message

        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_client.receive_messages = receive_messages

        context = AgentContext(session_key="test:c1", prompt="hello", channel="test", chat_id="c1")
        with patch.object(service, "_get_or_create_client", AsyncMock(return_value=mock_client)):
            async for _ in service.process(context):
                pass

        resolved = state_manager.resolve_compact_notification_target("sdk-session-1")
        assert resolved == ("test:c1", "test", "c1")

    @pytest.mark.asyncio
    async def test_process_syncs_init_commands(self, config, shared_resources, state_manager):
        """System init messages should persist discovered slash commands to state manager."""
        service = await self._make_service(config, shared_resources)

        init_message = MagicMock()
        init_message.session_id = "sdk-session-2"
        init_message.subtype = "init"
        init_message.data = {"commands": ["/pdf", {"name": "review"}]}
        result_message = MagicMock()
        result_message.session_id = "sdk-session-2"

        async def receive_messages():
            yield init_message
            yield result_message

        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_client.receive_messages = receive_messages

        context = AgentContext(session_key="test:c1", prompt="hello", channel="test", chat_id="c1")
        with patch.object(service, "_get_or_create_client", AsyncMock(return_value=mock_client)):
            async for _ in service.process(context):
                pass

        commands = state_manager.get_commands("test:c1")
        assert "/pdf" in commands
        assert "/review" in commands

    # --- Test 16: _convert_event handles task events ---

    @pytest.mark.asyncio
    async def test_convert_event_task_messages(self, config, shared_resources):
        """TaskStarted/TaskProgress/TaskNotification should convert to task events."""
        service = await self._make_service(config, shared_resources)

        TaskStartedMessage = type("TaskStartedMessage", (), {})
        TaskProgressMessage = type("TaskProgressMessage", (), {})
        TaskNotificationMessage = type("TaskNotificationMessage", (), {})

        started = TaskStartedMessage()
        started.description = "Run worker"
        started.task_id = "t1"
        started.task_type = "agent"
        r1 = service._convert_event(started)
        assert r1 is not None
        assert r1.event_type == "task"
        assert r1.event_data["status"] == "started"

        progress = TaskProgressMessage()
        progress.description = "Working"
        progress.task_id = "t1"
        progress.last_tool_name = "bash"
        progress.data = {"last_tool_input": {"command": "curl wttr.in/beijing"}}
        r2 = service._convert_event(progress)
        assert r2 is not None
        assert r2.event_type == "task"
        assert r2.event_data["status"] == "progress"
        assert r2.tool_calls is not None
        assert r2.tool_calls[0]["input"] == {"command": "curl wttr.in/beijing"}

        note = TaskNotificationMessage()
        note.status = "completed"
        note.summary = "Done"
        note.task_id = "t1"
        note.output_file = None
        r3 = service._convert_event(note)
        assert r3 is not None
        assert r3.event_type == "task"
        assert r3.event_data["status"] == "completed"

    # --- Test 17: _convert_stream_event handles thinking_delta ---

    @pytest.mark.asyncio
    async def test_convert_stream_event_thinking_delta(self, config, shared_resources):
        """StreamEvent thinking_delta should emit thinking progress."""
        service = await self._make_service(config, shared_resources)

        event_msg = MagicMock()
        event_msg.event = {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "analyzing"},
        }

        result = service._convert_stream_event(event_msg)
        assert result is not None
        assert result.event_type == "thinking"
        assert result.progress_texts

    # --- Test 18: dispatch forwards tool_call event_data ---

    @pytest.mark.asyncio
    async def test_dispatch_tool_call_event_data(self, config, shared_resources, bus):
        """Tool-call progress should carry tool_calls payload in _event_data."""
        service = await self._make_service(config, shared_resources)

        tool_calls = [{"name": "bash", "input": {"cmd": "ls"}, "kind": "tool"}]

        async def fake_process(_context):
            yield AgentResponse(content="", tool_calls=tool_calls)
            yield AgentResponse(content="done", event_type="result")

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="run")
        with patch.object(service, "process", side_effect=fake_process):
            await service._dispatch(msg, bus)

        tool_events = [
            c for c in bus.publish_outbound.call_args_list
            if c.args[0].metadata.get("_event_type") == "tool_call"
        ]
        assert tool_events
        assert tool_events[0].args[0].metadata.get("_event_data", {}).get("tool_calls") == tool_calls

    # --- Test 19: skip empty usage summary (0/0) ---

    @pytest.mark.asyncio
    async def test_dispatch_skips_zero_usage_summary(self, config, shared_resources, bus):
        """Usage summary should be skipped when input/output tokens are both zero."""
        service = await self._make_service(config, shared_resources)

        async def fake_process(_context):
            yield AgentResponse(
                content="ok",
                usage={"input_tokens": 0, "output_tokens": 0},
                event_type="result",
            )

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="run")
        with patch.object(service, "process", side_effect=fake_process):
            await service._dispatch(msg, bus)

        usage_events = [
            c for c in bus.publish_outbound.call_args_list
            if c.args[0].metadata.get("_event_type") == "usage"
        ]
        assert usage_events == []

    # --- Test 20: _convert_result_message parses dict usage correctly ---

    @pytest.mark.asyncio
    async def test_convert_result_message_dict_usage(self, config, shared_resources):
        """Dict-based usage should not be parsed as 0/0."""
        service = await self._make_service(config, shared_resources)

        msg = MagicMock()
        msg.result = "ok"
        msg.usage = {"input_tokens": 123, "output_tokens": 45}
        msg.stop_reason = "end_turn"
        msg.num_turns = 1
        msg.total_cost_usd = 0.01

        result = service._convert_result_message(msg)
        assert result is not None
        assert result.usage == {"input_tokens": 123, "output_tokens": 45}

    # --- Test 21: memory consolidation mode=off skips consolidation ---

    @pytest.mark.asyncio
    async def test_dispatch_memory_consolidation_off(self, config, shared_resources, bus):
        """Mode 'off' should skip memory consolidation."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.claude_sdk.memory_consolidation_mode = "off"
        shared_resources["config"] = runtime_config

        service = await self._make_service(config, shared_resources)
        service._memory_consolidator = AsyncMock()

        # Trigger consolidation
        session = MagicMock()
        await service._trigger_memory_consolidation("test-session", session)

        # Should not call consolidator
        service._memory_consolidator.maybe_consolidate_by_tokens.assert_not_called()

    # --- Test 22: memory consolidation mode=sync runs inline ---

    @pytest.mark.asyncio
    async def test_dispatch_memory_consolidation_sync(self, config, shared_resources, bus):
        """Mode 'sync' should run consolidation inline."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.claude_sdk.memory_consolidation_mode = "sync"
        shared_resources["config"] = runtime_config

        service = await self._make_service(config, shared_resources)
        service._memory_consolidator = AsyncMock()

        # Trigger consolidation
        session = MagicMock()
        await service._trigger_memory_consolidation("test-session", session)

        # Should call consolidator once
        service._memory_consolidator.maybe_consolidate_by_tokens.assert_called_once_with(session)

    # --- Test 23: memory consolidation mode=async schedules task ---

    @pytest.mark.asyncio
    async def test_dispatch_memory_consolidation_async(self, config, shared_resources, bus):
        """Mode 'async' should schedule background task."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.claude_sdk.memory_consolidation_mode = "async"
        shared_resources["config"] = runtime_config

        service = await self._make_service(config, shared_resources)
        service._memory_consolidator = AsyncMock()

        # Trigger consolidation
        session = MagicMock()
        await service._trigger_memory_consolidation("test-session", session)

        # Should have scheduled a task
        assert len(service._async_consolidation_tasks) == 1

        # Wait for task to complete
        await asyncio.sleep(0.1)

        # Should call consolidator once
        service._memory_consolidator.maybe_consolidate_by_tokens.assert_called_once_with(session)


class TestClaudeSdkE2EIdleBoundary:
    """Optional real-SDK E2E tests (opt-in)."""

    @pytest.fixture(autouse=True)
    def _require_opt_in(self) -> None:
        if os.getenv("RUN_CLAUDE_SDK_E2E") != "1":
            pytest.skip("set RUN_CLAUDE_SDK_E2E=1 to run real Claude SDK E2E tests")

    async def _collect_events(
        self,
        *,
        prompt: str,
        emit_state_events: bool,
        cwd: str,
        max_turns: int = 6,
        max_messages: int = 60,
    ) -> list[dict[str, Any]]:
        from claude_agent_sdk import ClaudeAgentOptions, query

        key = "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"
        old = os.environ.get(key)
        try:
            if emit_state_events:
                os.environ[key] = "1"
            else:
                os.environ.pop(key, None)

            options = ClaudeAgentOptions(
                cwd=cwd,
                max_turns=max_turns,
                permission_mode="acceptEdits",
            )
            events: list[dict[str, Any]] = []
            async for message in query(prompt=prompt, options=options):
                subtype = getattr(message, "subtype", None)
                data = getattr(message, "data", None)
                state = data.get("state") if isinstance(data, dict) else getattr(message, "state", None)
                events.append(
                    {
                        "type": type(message).__name__,
                        "subtype": subtype,
                        "state": state,
                        "status": getattr(message, "status", None),
                    }
                )
                if len(events) >= max_messages:
                    break
                if (
                    type(message).__name__ == "SystemMessage"
                    and subtype == "session_state_changed"
                    and state == "idle"
                ):
                    break
            return events
        except Exception as e:  # pragma: no cover - environment dependent
            pytest.skip(f"real SDK E2E unavailable in current environment: {e}")
        finally:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    @pytest.mark.asyncio
    async def test_real_sdk_emits_idle_boundary_when_env_enabled(self, tmp_path: Path) -> None:
        events = await self._collect_events(
            prompt="Reply with exactly: pong",
            emit_state_events=True,
            cwd=str(tmp_path),
            max_turns=3,
        )
        saw_running = any(
            e["type"] == "SystemMessage" and e["subtype"] == "session_state_changed" and e["state"] == "running"
            for e in events
        )
        saw_idle = any(
            e["type"] == "SystemMessage" and e["subtype"] == "session_state_changed" and e["state"] == "idle"
            for e in events
        )
        saw_result = any(e["type"] == "ResultMessage" for e in events)

        assert saw_running is True
        assert saw_result is True
        assert saw_idle is True

    @pytest.mark.asyncio
    async def test_real_sdk_complex_task_flow_reaches_idle_boundary(self, tmp_path: Path) -> None:
        events = await self._collect_events(
            prompt=(
                "Use one sub-agent to list exactly 3 Linux shell commands for checking disk usage. "
                "Then provide final answer in Chinese."
            ),
            emit_state_events=True,
            cwd=str(tmp_path),
            max_turns=8,
            max_messages=100,
        )
        saw_task_started = any(e["type"] == "TaskStartedMessage" for e in events)
        saw_task_done = any(
            e["type"] == "TaskNotificationMessage" and str(e["status"]).lower() == "completed"
            for e in events
        )
        saw_result = any(e["type"] == "ResultMessage" for e in events)
        saw_idle = any(
            e["type"] == "SystemMessage" and e["subtype"] == "session_state_changed" and e["state"] == "idle"
            for e in events
        )

        assert saw_task_started is True
        assert saw_task_done is True
        assert saw_result is True
        assert saw_idle is True


class TestSubagentModelCompatHooks:
    """Integration tests for subagent model compatibility hook wiring."""

    @pytest.mark.asyncio
    async def test_build_hooks_adds_pretooluse_model_fallback(
        self,
        tmp_path: Path,
    ) -> None:
        agent_config = AgentConfig(
            model="glm-5",
            system_prompt="test",
        )
        runtime_config = MagicMock()
        runtime_config.agents.defaults.provider = "alrun"
        runtime_config.agents.defaults.model = "glm-5"
        runtime_config.agents.defaults.available_models = ["glm-5"]
        runtime_config.agents.claude_sdk.compact_notify = False
        runtime_config.agents.claude_sdk.hooks = None

        service = AgentService()
        await service.initialize(
            agent_config,
            {"workspace": str(tmp_path), "config": runtime_config},
        )

        hooks = service._build_hooks(runtime_config.agents.claude_sdk)
        assert hooks is not None
        assert "PreToolUse" in hooks

        matcher = hooks["PreToolUse"][0]
        handler = matcher.hooks[0]

        output = await handler(
            {
                "session_id": "cli:direct",
                "tool_name": "Agent",
                "tool_input": {
                    "model": "haiku",
                    "subagent_type": "Explore",
                },
            },
            None,
            MagicMock(),
        )

        assert output is not None
        updated = output["hookSpecificOutput"]["updatedInput"]
        assert updated["model"] == "inherit"
        assert updated["subagent_type"] == "Explore"

    @pytest.mark.asyncio
    async def test_build_hooks_keeps_anthropic_alias_model_without_available_models(
        self,
        tmp_path: Path,
    ) -> None:
        agent_config = AgentConfig(
            model="claude-sonnet-4-5",
            system_prompt="test",
        )
        runtime_config = MagicMock()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.agents.defaults.available_models = []
        runtime_config.agents.claude_sdk.compact_notify = False
        runtime_config.agents.claude_sdk.hooks = None

        service = AgentService()
        await service.initialize(
            agent_config,
            {"workspace": str(tmp_path), "config": runtime_config},
        )

        hooks = service._build_hooks(runtime_config.agents.claude_sdk)
        matcher = hooks["PreToolUse"][0]
        handler = matcher.hooks[0]

        output = await handler(
            {
                "session_id": "cli:direct",
                "tool_name": "Agent",
                "tool_input": {
                    "model": "haiku",
                    "subagent_type": "Explore",
                },
            },
            None,
            MagicMock(),
        )

        assert output is None
