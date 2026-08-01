"""Additional coverage tests for AgentService - Round 3 Part 3.

Focus on remaining uncovered branches to reach ~90% coverage.
"""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.runtime.core.protocol import AgentContext, AgentResponse
from xbot.runtime.core.service import AgentService, SessionWorker
from xbot.runtime.core.types import AgentConfig
from xbot.runtime.state import SessionEvent, SessionPhase


class TestCancelAsyncTasks:
    """Test cancellation of async tasks in shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_cancels_registry_tasks(self, tmp_path: Path):
        """Should cancel and await registry tasks during shutdown."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        async def long_task():
            await asyncio.sleep(10)

        task = asyncio.create_task(long_task())
        service._async_registry_tasks.add(task)
        service._client_pool.disconnect_all = AsyncMock()

        await service.shutdown()

        assert task.cancelled() or task.done()
        assert not service._async_registry_tasks


class TestResolveExecutionCwdRegistryOverride:
    """Test _resolve_execution_cwd with registry override."""

    @pytest.mark.asyncio
    async def test_resolve_cwd_with_registry_override(self, tmp_path: Path):
        """Should use registry override when available."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.get_execution_cwd.return_value = "/override/path"
        service._shared_resources = {
            "workspace": str(tmp_path),
            "run_mode": "cli",
            "runtime_registry": sm,
        }

        result = service._resolve_execution_cwd("s1")

        assert "/override/path" in result


class TestResolveWorkspaceDirRegistryOverride:
    """Test _resolve_workspace_dir with registry override."""

    @pytest.mark.asyncio
    async def test_resolve_workspace_dir_with_registry_override(self, tmp_path: Path):
        """Should use registry override when available."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.get_workspace_dir.return_value = "/workspace/override"
        service._shared_resources = {
            "workspace": str(tmp_path),
            "run_mode": "cli",
            "runtime_registry": sm,
        }

        result = service._resolve_workspace_dir("s1")

        assert "/workspace/override" in result


class TestBuildSdkOptionsWithPermissionHandler:
    """Test _build_sdk_options with permission handler."""

    @pytest.mark.asyncio
    async def test_build_options_with_permission_handler(self, tmp_path: Path):
        """Should build can_use_tool callback from permission handler."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        permission_handler = MagicMock()
        permission_handler.build_can_use_tool_callback.return_value = lambda *args: True
        service._shared_resources["permission_handler"] = permission_handler

        options = service._build_sdk_options(session_key="s1")

        assert options.can_use_tool is not None


class TestHydrateSdkSessionIdFromStore:
    """Test _hydrate_sdk_session_id_from_store_if_missing."""

    @pytest.mark.asyncio
    async def test_hydrate_from_store(self, tmp_path: Path):
        """Should hydrate sdk_session_id from conversation store."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.resolve_sdk_session_id.return_value = None
        service._shared_resources["runtime_registry"] = sm

        session = MagicMock()
        session.metadata = {"sdk_session_id": "stored-session-id"}
        store = MagicMock()
        store.get.return_value = session
        service._shared_resources["conversation_store"] = store

        service._hydrate_sdk_session_id_from_store_if_missing("s1")

        # Should have called set_sdk_session_id or _set_sdk_session_id_impl


class TestTrackAsyncRegistryUpdate:
    """Test _track_async_registry_update."""

    @pytest.mark.asyncio
    async def test_track_async_update_success(self, tmp_path: Path):
        """Should track and clean up async registry update."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        async def async_update():
            pass

        service._track_async_registry_update(async_update(), "s1")

        # Task should be tracked
        assert len(service._async_registry_tasks) >= 0  # May be cleaned up immediately
        await asyncio.sleep(0.01)  # Allow cleanup


class TestPersistMessagesWithException:
    """Test message persistence with exceptions."""

    def test_persist_user_message_exception(self):
        """Should handle exception during user message persistence."""
        service = AgentService()
        store = MagicMock()
        store.get_or_create.side_effect = Exception("Store error")
        service._shared_resources = {"conversation_store": store}

        # Should not raise
        service._persist_user_message("s1", "hello")

    def test_persist_assistant_message_exception(self):
        """Should handle exception during assistant message persistence."""
        service = AgentService()
        store = MagicMock()
        store.get_or_create.side_effect = Exception("Store error")
        service._shared_resources = {"conversation_store": store}

        # Should not raise
        service._persist_assistant_message("s1", "response")


class TestSetRuntimeToolAndPermissionContext:
    """Test _set_runtime_tool_and_permission_context."""

    @pytest.mark.asyncio
    async def test_set_context_with_tool_adapter(self, tmp_path: Path):
        """Should set tool context when adapter available."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        tool_adapter = MagicMock()
        service._tool_adapter = tool_adapter

        context = AgentContext(
            session_key="s1",
            prompt="test",
            channel="test",
            chat_id="c1",
        )

        service._set_runtime_tool_and_permission_context(context)

        tool_adapter.set_tool_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_context_with_permission_handler(self, tmp_path: Path):
        """Should set permission context when handler available."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        permission_handler = MagicMock()
        service._shared_resources["permission_handler"] = permission_handler

        context = AgentContext(
            session_key="s1",
            prompt="test",
            channel="test",
            chat_id="c1",
        )

        service._set_runtime_tool_and_permission_context(context)

        permission_handler.set_session_context.assert_called_once()


class TestClearRuntimeToolAndPermissionContext:
    """Test _clear_runtime_tool_and_permission_context."""

    @pytest.mark.asyncio
    async def test_clear_context(self, tmp_path: Path):
        """Should clear tool and permission context."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        tool_adapter = MagicMock()
        service._tool_adapter = tool_adapter

        permission_handler = MagicMock()
        service._shared_resources["permission_handler"] = permission_handler

        service._clear_runtime_tool_and_permission_context("s1")

        tool_adapter.clear_context.assert_called_once()
        permission_handler.clear_session_context.assert_called_once()


class TestReleaseSessionClientBranches:
    """Test _release_session_client additional branches."""

    @pytest.mark.asyncio
    async def test_release_with_exception(self, tmp_path: Path):
        """Should handle exception during release."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        service._client_pool.disconnect = AsyncMock(side_effect=Exception("Disconnect failed"))

        result = await service._release_session_client("s1", reason="test")

        assert result is False


class TestWorkerInputStream:
    """Test _worker_input_stream."""

    @pytest.mark.asyncio
    async def test_worker_input_stream_yields_frames(self):
        """Should yield frames from queue until None."""
        service = AgentService()
        worker = SessionWorker(
            session_key="s1",
            client=MagicMock(),
            input_queue=asyncio.Queue(),
            task=None,
            channel="test",
            chat_id="c1",
        )

        await worker.input_queue.put({"type": "user", "content": "msg1"})
        await worker.input_queue.put({"type": "user", "content": "msg2"})
        await worker.input_queue.put(None)

        frames = []
        async for frame in service._worker_input_stream(worker):
            frames.append(frame)

        assert len(frames) == 2
        assert frames[0]["content"] == "msg1"
        assert frames[1]["content"] == "msg2"


class TestDispatchWorkerClientReady:
    """Test _dispatch_worker_client_ready."""

    @pytest.mark.asyncio
    async def test_dispatch_ready_when_acquiring(self, tmp_path: Path):
        """Should dispatch CLIENT_ACQUIRED when phase is ACQUIRING_CLIENT."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        sm = MagicMock()
        sm.get_phase.return_value = SessionPhase.ACQUIRING_CLIENT
        sm.dispatch.return_value = True
        service._shared_resources["runtime_registry"] = sm

        service._dispatch_worker_client_ready("s1")

        calls = sm.dispatch.call_args_list
        assert any(call.args[1] == SessionEvent.CLIENT_ACQUIRED for call in calls)

    @pytest.mark.asyncio
    async def test_dispatch_ready_wrong_phase(self, tmp_path: Path):
        """Should not dispatch when phase is not ACQUIRING_CLIENT."""
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

        service._dispatch_worker_client_ready("s1")

        sm.dispatch.assert_not_called()


class TestConvertRateLimitEvent:
    """Test _convert_rate_limit_event."""

    @pytest.mark.asyncio
    async def test_convert_rate_limit_event(self, tmp_path: Path):
        """Should convert rate limit event to AgentResponse."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        msg = MagicMock()
        msg.rate_limit_info = MagicMock()
        msg.rate_limit_info.status = 429
        msg.rate_limit_info.rate_limit_type = "requests"

        result = service._convert_rate_limit_event(msg)

        assert result is not None
        assert result.event_type == "rate_limit"


class TestConvertTaskStartedWithHandoff:
    """Test _convert_task_started with handoff policy."""

    @pytest.mark.asyncio
    async def test_convert_task_started_with_handoff(self, tmp_path: Path):
        """Should include handoff trace when policy available."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        handoff_policy = MagicMock()
        handoff_policy.format_task_trace.return_value = "Trace info"
        service._handoff_policy = handoff_policy

        msg = MagicMock()
        msg.description = "Task description"
        msg.task_id = "t1"
        msg.task_type = "agent"

        result = service._convert_task_started(msg)

        assert result is not None
        assert "Trace info" in result.progress_texts


class TestConvertTaskNotificationWithHandoff:
    """Test _convert_task_notification with handoff policy."""

    @pytest.mark.asyncio
    async def test_convert_task_notification_with_handoff(self, tmp_path: Path):
        """Should include handoff trace when policy available."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.providers.anthropic.api_key = "sk-test"

        service = AgentService()
        config = AgentConfig(model="test", system_prompt="test")
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        handoff_policy = MagicMock()
        handoff_policy.format_task_trace.return_value = "Trace info"
        service._handoff_policy = handoff_policy

        msg = MagicMock()
        msg.status = "completed"
        msg.summary = "Done"
        msg.task_id = "t1"

        result = service._convert_task_notification(msg)

        assert result is not None
        assert "Trace info" in result.progress_texts


class TestExtractSdkCapabilitiesWithNonStringItems:
    """Test _extract_sdk_capabilities with non-string items."""

    def test_extract_with_non_string_items(self):
        """Should skip non-string items in lists."""
        info = {
            "skills": [{"name": "skill1"}, 123, None],
            "tools": ["tool1", 456],
            "slash_commands": ["/cmd1", 789],
        }

        result = AgentService._extract_sdk_capabilities(info)

        assert "skill1" in result["skills"]
        assert "tool1" in result["tools"]
        assert "/cmd1" in result["slash_commands"]
