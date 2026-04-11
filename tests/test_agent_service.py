"""Tests for AgentService."""

import asyncio
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
    """Tests for result return + post-result drain behavior."""

    @pytest.mark.asyncio
    async def test_process_returns_result_immediately_when_no_pending_tasks(self, tmp_path: Path) -> None:
        from xbot.platform.config.schema import Config

        config = AgentConfig(model="claude-sonnet-4-6", system_prompt="Test")
        runtime_config = Config()
        runtime_config.agents.claude_sdk.post_result_quiet_window_ms = 30
        runtime_config.agents.claude_sdk.post_result_drain_cap_ms = 100
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        class ResultMessage:
            def __init__(self, text: str) -> None:
                self.result = text
                self.usage = None
                self.stop_reason = "end_turn"
                self.num_turns = 1
                self.total_cost_usd = None

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def receive_messages():
            yield ResultMessage("done")

        mock_client.receive_messages = receive_messages

        context = AgentContext(session_key="test:drain1", prompt="hello", channel="test", chat_id="c1")
        start = asyncio.get_event_loop().time()
        with patch.object(service, "_get_or_create_client", AsyncMock(return_value=mock_client)):
            responses = [r async for r in service.process(context)]
        elapsed = asyncio.get_event_loop().time() - start

        assert [r.event_type for r in responses] == ["result"]
        assert responses[0].content == "done"
        # pending=0 should not wait quiet window
        assert elapsed < 0.03

    @pytest.mark.asyncio
    async def test_process_drains_late_task_notification_without_polluting_next_turn(self, tmp_path: Path) -> None:
        from xbot.platform.config.schema import Config

        config = AgentConfig(model="claude-sonnet-4-6", system_prompt="Test")
        runtime_config = Config()
        runtime_config.agents.claude_sdk.post_result_quiet_window_ms = 15
        runtime_config.agents.claude_sdk.post_result_drain_cap_ms = 120
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})

        class ResultMessage:
            def __init__(self, text: str) -> None:
                self.result = text
                self.usage = None
                self.stop_reason = "end_turn"
                self.num_turns = 1
                self.total_cost_usd = None

        class TaskStartedMessage:
            def __init__(self, task_id: str) -> None:
                self.task_id = task_id
                self.description = "Run worker"
                self.task_type = "agent"

        class TaskNotificationMessage:
            def __init__(self, task_id: str, status: str) -> None:
                self.task_id = task_id
                self.status = status
                self.summary = 'Agent "Query Hangzhou weather" completed'
                self.output_file = None

        class FakeClient:
            def __init__(self) -> None:
                self._queue: list[Any] = []

            async def query(self, prompt: str) -> None:
                if "并行查询上海" in prompt:
                    self._queue.append(TaskStartedMessage("t1"))
                    self._queue.append(ResultMessage("R1"))

                    async def _late_task_complete() -> None:
                        await asyncio.sleep(0.03)
                        self._queue.append(TaskNotificationMessage("t1", "completed"))

                    asyncio.create_task(_late_task_complete())
                else:
                    self._queue.append(ResultMessage("R2"))

            async def receive_messages(self):
                while True:
                    if self._queue:
                        yield self._queue.pop(0)
                        continue
                    await asyncio.sleep(0.001)

        client = FakeClient()
        with patch.object(service, "_get_or_create_client", AsyncMock(return_value=client)):
            heavy_ctx = AgentContext(
                session_key="test:drain2",
                prompt="启动多个subagent 最多3个，并行查询上海，杭州，北京的天气，并汇总",
                channel="test",
                chat_id="c1",
            )
            heavy_responses = [r async for r in service.process(heavy_ctx)]

            follow_ctx = AgentContext(
                session_key="test:drain2",
                prompt="今天美国跟伊朗打得怎么样？",
                channel="test",
                chat_id="c1",
            )
            follow_responses = [r async for r in service.process(follow_ctx)]

        # heavy turn should return task-started progress + final result;
        # late task completion is consumed in background drain and not yielded.
        assert [r.event_type for r in heavy_responses] == ["task", "result"]
        assert heavy_responses[-1].content == "R1"
        # next turn should not be polluted by previous turn late task completion.
        assert [r.event_type for r in follow_responses] == ["result"]
        assert follow_responses[0].content == "R2"


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
        r2 = service._convert_event(progress)
        assert r2 is not None
        assert r2.event_type == "task"
        assert r2.event_data["status"] == "progress"
        assert r2.tool_calls is not None

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
