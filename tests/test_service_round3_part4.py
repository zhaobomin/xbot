"""Final coverage tests for AgentService - Round 3 Part 4.

Target remaining uncovered branches to reach ~90% coverage.
"""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.platform.bus.events import InboundMessage, OutboundMessage
from xbot.runtime.core.protocol import AgentContext, AgentResponse
from xbot.runtime.core.service import AgentService, SessionWorker
from xbot.runtime.core.types import AgentConfig
from xbot.runtime.state import SessionEvent, SessionPhase


class TestRunLoopErrorHandling:
    """Test run() loop error handling branches."""

    @pytest.mark.asyncio
    async def test_run_consume_exception(self, tmp_path: Path):
        """Should handle exception during consume_inbound."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        bus = MagicMock()
        # Raise exception, then stop the service
        call_count = [0]
        async def consume_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Consume error")
            else:
                service.stop()
                raise asyncio.TimeoutError()

        bus.consume_inbound = consume_side_effect
        service._shared_resources["bus"] = bus
        service._command_handler = MagicMock()
        service._command_handler.is_local_command.return_value = False

        # Run will exit after stop() is called
        await service.run()

        # Should have attempted to consume
        assert call_count[0] == 2


class TestGetOrStartSessionWorker:
    """Test _get_or_start_session_worker."""

    @pytest.mark.asyncio
    async def test_create_new_worker(self, tmp_path: Path):
        """Should create new worker when none exists."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        bus = MagicMock()
        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="hello")

        with patch("claude_agent_sdk.ClaudeSDKClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock()
            mock_client.receive_messages = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            worker = await service._get_or_start_session_worker(msg, bus)

            assert worker is not None
            assert worker.session_key == "test:c1"


class TestRunSessionWorkerErrorHandling:
    """Test _run_session_worker error handling."""

    @pytest.mark.asyncio
    async def test_worker_stream_error(self, tmp_path: Path):
        """Should handle stream error during worker execution."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.receive_messages = MagicMock(side_effect=Exception("Stream error"))
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

        bus = MagicMock()
        bus.publish_outbound = AsyncMock()

        await service._run_session_worker(worker, bus)

        assert worker.closed is True
        bus.publish_outbound.assert_awaited()


class TestStopSessionWorkerTaskCancellation:
    """Test _stop_session_worker task cancellation."""

    @pytest.mark.asyncio
    async def test_stop_worker_task_timeout(self):
        """Should handle task cancellation timeout."""
        service = AgentService()

        async def long_task():
            await asyncio.sleep(10)

        task = asyncio.create_task(long_task())
        worker = SessionWorker(
            session_key="s1",
            client=MagicMock(),
            input_queue=asyncio.Queue(),
            task=task,
            channel="test",
            chat_id="c1",
        )
        service._session_workers["s1"] = worker

        # Patch wait_for to raise TimeoutError
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await service._stop_session_worker("s1", disconnect=True)

        assert result is True


class TestPersistMessagesWithMarkDirty:
    """Test message persistence with mark_metadata_dirty."""

    def test_persist_sdk_session_id_with_mark_dirty(self):
        """Should call mark_metadata_dirty when available."""
        service = AgentService()
        session = MagicMock()
        session.metadata = {}
        session.mark_metadata_dirty = MagicMock()
        store = MagicMock()
        store.get_or_create.return_value = session
        service._shared_resources = {"conversation_store": store}

        service._persist_sdk_session_id_to_store("s1", "sdk-123")

        session.mark_metadata_dirty.assert_called_once()


class TestMemoryConsolidationAsyncCancellation:
    """Test async memory consolidation cancellation."""

    @pytest.mark.asyncio
    async def test_async_consolidation_handles_cancellation(self, tmp_path: Path):
        """Should handle cancellation during async consolidation."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"
        runtime_config.agents.claude_sdk.memory_consolidation_mode = "async"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        consolidator = AsyncMock()
        consolidator.maybe_consolidate_by_tokens = AsyncMock(side_effect=asyncio.CancelledError())
        service._memory_consolidator = consolidator

        session = MagicMock()

        # Should not raise
        await service._trigger_memory_consolidation("s1", session)

        # Wait for task to complete
        await asyncio.sleep(0.01)


class TestHydrateSdkSessionIdWithImplException:
    """Test _hydrate_sdk_session_id_from_store_if_missing with exceptions."""

    @pytest.mark.asyncio
    async def test_hydrate_with_impl_exception(self, tmp_path: Path):
        """Should handle exception from _set_sdk_session_id_impl."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.resolve_sdk_session_id.return_value = None
        sm._set_sdk_session_id_impl = MagicMock(side_effect=Exception("Impl error"))
        sm.set_sdk_session_id = MagicMock(side_effect=Exception("Set error"))
        sm.get_or_create = MagicMock()
        service._shared_resources["runtime_registry"] = sm

        session = MagicMock()
        session.metadata = {"sdk_session_id": "stored-id"}
        store = MagicMock()
        store.get.return_value = session
        service._shared_resources["conversation_store"] = store

        # Should not raise
        service._hydrate_sdk_session_id_from_store_if_missing("s1")


class TestSetSessionRoutingException:
    """Test _set_session_routing exception handling."""

    def test_set_routing_with_exception(self):
        """Should handle exception from set_routing."""
        service = AgentService()
        sm = MagicMock()
        sm.set_routing.side_effect = Exception("Routing error")
        service._shared_resources = {"runtime_registry": sm}

        # Should not raise
        service._set_session_routing("s1", "ch", "c1")


class TestSyncSdkSessionMappingWithSetException:
    """Test _sync_sdk_session_mapping with set exceptions."""

    def test_sync_with_set_exception(self):
        """Should handle exception from set_sdk_session_id."""
        service = AgentService()
        sm = MagicMock()
        sm._set_sdk_session_id_impl = MagicMock(side_effect=Exception("Impl error"))
        sm.set_sdk_session_id = MagicMock(side_effect=Exception("Set error"))
        service._shared_resources = {"runtime_registry": sm}

        msg = MagicMock()
        msg.subtype = "other"
        msg.session_id = "sdk-123"
        msg.data = None

        # Should not raise
        service._sync_sdk_session_mapping("s1", msg)


class TestExtractSlashCommandsEdgeCases:
    """Test _extract_slash_commands edge cases."""

    def test_extract_with_whitespace_only(self):
        """Should skip whitespace-only commands."""
        info = {
            "slash_commands": ["  ", "/valid"],
            "commands": ["\t", "/clear"],
        }

        result = AgentService._extract_slash_commands(info)

        assert "/valid" in result
        assert "/clear" in result
        assert "  " not in result
        assert "\t" not in result


class TestBuildSdkOptionsWithResumeSession:
    """Test _build_sdk_options with resume session."""

    @pytest.mark.asyncio
    async def test_build_options_with_resume(self, tmp_path: Path):
        """Should include resume session when available."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.resolve_sdk_session_id.return_value = "resume-session-id"
        service._shared_resources["runtime_registry"] = sm

        options = service._build_sdk_options(session_key="s1")

        assert options.resume == "resume-session-id"


class TestMapAgentToolsToSdkNamesWithUnknown:
    """Test _map_agent_tools_to_sdk_names with unknown tools."""

    def test_map_unknown_tools(self):
        """Should map unknown tools to xbot MCP namespace."""
        service = AgentService()

        result = service._map_agent_tools_to_sdk_names(["unknown_tool"])

        assert "mcp__xbot__unknown_tool" in result


class TestShouldReleaseEphemeralClientDisabled:
    """Test _should_release_ephemeral_client when disabled."""

    def test_should_release_disabled(self):
        """Should return False when ephemeral release disabled."""
        service = AgentService()
        config = MagicMock()
        config.agents.claude_sdk.ephemeral_immediate_release_enabled = False
        service._shared_resources = {"config": config}

        result = service._should_release_ephemeral_client("heartbeat")

        assert result is False


class TestConvertSystemMessageCompactCompleteFallback:
    """Test _convert_system_message compact_complete with fallback to metadata."""

    @pytest.mark.asyncio
    async def test_compact_complete_with_metadata_fallback(self, tmp_path: Path):
        """Should fallback to compact_metadata for tokens."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        msg = MagicMock()
        msg.subtype = "compact_complete"
        msg.message = ""
        msg.pre_tokens = None
        msg.post_tokens = None
        msg.trigger = None
        msg.data = {
            "compact_metadata": {
                "pre_tokens": 50000,
                "post_tokens": 20000,
                "trigger": "auto",
            }
        }

        result = service._convert_system_message(msg)

        assert result is not None
        assert "50,000" in result.progress_texts[0]
        assert "20,000" in result.progress_texts[0]


class TestGetSdkQueryTimeoutEdgeCases:
    """Test _get_sdk_query_timeout edge cases."""

    def test_get_timeout_with_negative_value(self):
        """Should return default for negative timeout."""
        service = AgentService()
        config = MagicMock()
        config.tools.timeouts.sdk_query = -10.0
        service._shared_resources = {"config": config}

        result = service._get_sdk_query_timeout()

        assert result == 30.0
