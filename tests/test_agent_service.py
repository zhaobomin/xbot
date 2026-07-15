"""Tests for AgentService."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.platform.bus.events import InboundMessage
from xbot.platform.config.schema import Config, ProviderConfig
from xbot.runtime.core.protocol import AgentContext, AgentResponse
from xbot.runtime.core.service import AgentService
from xbot.runtime.core.types import AgentConfig
from xbot.runtime.state import RuntimeSessionRegistry
from xbot.runtime.state.machine import SessionPhase


class SystemMessage:
    """Fake SDK SystemMessage for idle-boundary tests."""

    def __init__(self, *, state: str = "idle", session_id: str = "sdk-session") -> None:
        self.subtype = "session_state_changed"
        self.data = {"state": state}
        self.session_id = session_id


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

    def test_effective_model_uses_runtime_config_with_agent_config(self, tmp_path: Path) -> None:
        """AgentConfig constructor path should still read provider defaults from runtime Config."""
        runtime_config = Config()
        runtime_config.agents.defaults.provider = "alrun"
        runtime_config.agents.defaults.model = ""
        runtime_config.providers.custom_providers["alrun"] = ProviderConfig(models=["qwen3-coder-next"])

        service = AgentService(
            AgentConfig(model="", system_prompt=""),
            {"workspace": str(tmp_path), "config": runtime_config},
        )
        service._shared_resources = {"workspace": str(tmp_path), "config": runtime_config}

        assert service._get_effective_model() == "qwen3-coder-next"

    @pytest.mark.asyncio
    async def test_model_command_overrides_only_its_session_and_reset_restores_default(
        self, tmp_path: Path
    ) -> None:
        """!model must affect the next SDK options for just the addressed session."""
        runtime_config = Config()
        runtime_config.agents.defaults.provider = "anthropic"
        runtime_config.agents.defaults.model = "claude-sonnet-4-5"
        runtime_config.providers.anthropic.models = [
            "claude-sonnet-4-5",
            "claude-3-haiku-20240307",
        ]
        service = AgentService()
        await service.initialize(
            AgentConfig(model="", system_prompt="Test"),
            {"workspace": str(tmp_path), "config": runtime_config},
        )
        bus = MagicMock()
        bus.publish_outbound = AsyncMock()
        msg = InboundMessage(
            channel="test", sender_id="u1", chat_id="c1", content="!model claude-3-haiku-20240307"
        )

        await service._command_handler.handle(msg, bus)

        assert service._get_effective_model("test:c1") == "claude-3-haiku-20240307"
        assert service._build_sdk_options(session_key="test:c1").model == "claude-3-haiku-20240307"
        assert service._get_effective_model("test:other") == "claude-sonnet-4-5"

        await service.reset_session("test:c1")

        assert service._get_effective_model("test:c1") == "claude-sonnet-4-5"

    @pytest.mark.asyncio
    async def test_call_for_auxiliary_passes_model_override_to_context(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Auxiliary calls must preserve a per-call model override."""
        service = AgentService()
        await service.initialize(config, shared_resources)
        observed_context: AgentContext | None = None

        async def fake_process(context: AgentContext):
            nonlocal observed_context
            observed_context = context
            yield AgentResponse(content="ok")

        with patch.object(service, "process", side_effect=fake_process):
            assert await service.call_for_auxiliary("ping", model="claude-haiku") == "ok"

        assert observed_context is not None
        assert observed_context.model == "claude-haiku"

    @pytest.mark.asyncio
    async def test_process_passes_model_override_to_client(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """A context model override must reach SDK client construction."""
        service = AgentService()
        await service.initialize(config, shared_resources)
        client = MagicMock()
        client.query = AsyncMock()

        async def receive_messages():
            yield SystemMessage(state="idle")

        client.receive_messages = receive_messages
        get_client = AsyncMock(return_value=client)

        with (
            patch.object(service, "_get_or_create_client", get_client),
            patch.object(service, "_refresh_session_commands_from_client", AsyncMock()),
        ):
            responses = [
                response
                async for response in service.process(
                    AgentContext(
                        session_key="auxiliary",
                        prompt="ping",
                        model="claude-haiku",
                    )
                )
            ]

        assert responses == []
        get_client.assert_awaited_once_with("auxiliary", model="claude-haiku")

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
    async def test_sync_sdk_session_mapping_tracks_async_registry_errors(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Async registry fallback failures should be consumed and logged."""

        class AsyncOnlyRegistry:
            async def set_sdk_session_id(self, session_key: str, sdk_id: str | None) -> None:
                raise RuntimeError(f"failed to persist {session_key}:{sdk_id}")

        class SdkMessage:
            session_id = "sdk-1"

        service = AgentService()
        shared_resources = dict(shared_resources)
        shared_resources["runtime_registry"] = AsyncOnlyRegistry()
        await service.initialize(config, shared_resources)

        service._sync_sdk_session_mapping("session-1", SdkMessage())
        assert service._async_registry_tasks

        await asyncio.gather(*list(service._async_registry_tasks), return_exceptions=True)

        assert not service._async_registry_tasks
        assert "Async sdk_session_id update failed for session-1" in caplog.text

    def test_build_env_config_does_not_log_api_key_length(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """API key logs should not expose key length metadata."""
        runtime_config = MagicMock()
        runtime_config.agents.defaults.provider = "anthropic"
        provider_config = MagicMock()
        provider_config.api_key = "sk-test-secret-value"
        provider_config.api_base = None
        runtime_config.providers.anthropic = provider_config

        service = AgentService()
        service._config = config
        service._shared_resources = {**shared_resources, "config": runtime_config}

        caplog.set_level("INFO")
        env = service._build_env_config()

        assert env["ANTHROPIC_API_KEY"] == "sk-test-secret-value"
        assert "Set ANTHROPIC_API_KEY" in caplog.text
        assert "length" not in caplog.text
        assert "20" not in caplog.text

    def test_build_env_config_uses_normalized_custom_provider_name(
        self,
        config: AgentConfig,
        tmp_path: Path,
    ) -> None:
        """Hyphenated provider names should find migrated custom provider config."""
        runtime_config = Config()
        runtime_config.agents.defaults.provider = "aliyun-coding-plan"
        runtime_config.providers.custom_providers["aliyun_coding_plan"] = ProviderConfig(
            api_key="sk-test-secret-value",
            api_base="https://example.test/v1",
        )

        service = AgentService()
        service._config = config
        service._shared_resources = {"workspace": str(tmp_path), "config": runtime_config}

        env = service._build_env_config()

        assert env["ANTHROPIC_API_KEY"] == "sk-test-secret-value"
        assert env["ANTHROPIC_BASE_URL"] == "https://example.test/v1"

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
    async def test_process_logs_unexpected_exception_with_traceback(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        caplog,
    ) -> None:
        service = AgentService()
        await service.initialize(config, shared_resources)
        context = AgentContext(session_key="test:traceback", prompt="Hello")
        mock_client = MagicMock()
        mock_client.query = AsyncMock(side_effect=RuntimeError("boom"))

        with patch.object(service, "_get_or_create_client", AsyncMock(return_value=mock_client)):
            responses = [response async for response in service.process(context)]

        assert responses[-1].finish_reason == "error"
        assert any(
            record.exc_info and "Error processing" in record.message
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_process_uses_configured_sdk_query_timeout(
        self,
        config: AgentConfig,
        tmp_path: Path,
    ) -> None:
        runtime_config = Config()
        runtime_config.tools.timeouts.sdk_query = 0.01
        service = AgentService()
        await service.initialize(config, {"workspace": str(tmp_path), "config": runtime_config})
        context = AgentContext(session_key="test:query-timeout", prompt="Hello")

        async def slow_query(_prompt: str) -> None:
            await asyncio.sleep(60)

        mock_client = MagicMock()
        mock_client.query = slow_query
        service._client_pool.disconnect = AsyncMock(return_value=True)

        with patch.object(service, "_get_or_create_client", AsyncMock(return_value=mock_client)):
            responses = [response async for response in service.process(context)]

        assert responses[-1].finish_reason == "error"
        assert "timed out" in responses[-1].content
        service._client_pool.disconnect.assert_awaited_once_with("test:query-timeout")

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
        sent_gen = mock_client.query.await_args.args[0]
        # Multimodal: query receives an async iterator of user frames whose
        # message content is a list of Anthropic content blocks.
        frames = [frame async for frame in sent_gen]
        assert len(frames) == 1
        frame = frames[0]
        assert frame["type"] == "user"
        assert frame["message"]["role"] == "user"
        content = frame["message"]["content"]
        assert isinstance(content, list)
        image_blocks = [b for b in content if b.get("type") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["source"]["type"] == "base64"
        assert image_blocks[0]["source"]["media_type"] == "image/png"
        text_blocks = [b for b in content if b.get("type") == "text"]
        assert text_blocks
        combined_text = "".join(b["text"] for b in text_blocks)
        assert str(text_path.resolve()) in combined_text
        assert "请分析附件" in combined_text

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
    async def test_process_direct_ignores_final_content_after_deltas(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """process_direct should not duplicate text when both deltas and final content exist."""
        service = AgentService()
        await service.initialize(config, shared_resources)

        async def fake_process(context):
            yield AgentResponse(content="", is_delta=True, delta_content="Hello")
            yield AgentResponse(content="", is_delta=True, delta_content=" world")
            yield AgentResponse(content="Hello world", event_type="result")

        with patch.object(service, "process", side_effect=fake_process):
            text = await service.process_direct("hi", session_key="test:dup")

        assert text == "Hello world"


class TestRunDispatch:
    """Tests for the run() message routing and worker processing chain."""

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

    # --- Worker event forwarding (replaces legacy _dispatch tests) ---

    @pytest.mark.asyncio
    async def test_worker_forwards_progress(self, config, shared_resources, bus):
        """_publish_worker_response should forward progress_texts with _progress metadata."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"
        worker = service._create_detached_session_worker(
            session_key, client=MagicMock(), channel="test", chat_id="c1",
        )
        service._session_workers[session_key] = worker

        await service._publish_worker_response(
            worker,
            AgentResponse(content="", progress_texts=["Thinking about it..."]),
            bus,
        )

        progress_calls = [
            c for c in bus.publish_outbound.call_args_list
            if c.args[0].metadata.get("_progress") is True
            and c.args[0].metadata.get("_event_type") == "thinking"
        ]
        assert len(progress_calls) >= 1
        assert progress_calls[0].args[0].metadata.get("_progress_kind") == "reasoning"
        assert "Thinking about it..." in progress_calls[0].args[0].content

    @pytest.mark.asyncio
    async def test_worker_forwards_tool_hints(self, config, shared_resources, bus):
        """_publish_worker_response should forward tool_calls with _tool_hint metadata."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"
        worker = service._create_detached_session_worker(
            session_key, client=MagicMock(), channel="test", chat_id="c1",
        )
        service._session_workers[session_key] = worker

        await service._publish_worker_response(
            worker,
            AgentResponse(content="", tool_calls=[{"name": "bash", "input": {"cmd": "ls"}}]),
            bus,
        )

        tool_hint_calls = [
            c for c in bus.publish_outbound.call_args_list
            if c.args[0].metadata.get("_tool_hint") is True
        ]
        assert len(tool_hint_calls) >= 1
        assert "bash" in tool_hint_calls[0].args[0].content

    @pytest.mark.asyncio
    async def test_worker_forwards_usage(self, config, shared_resources, bus):
        """_publish_worker_response should forward usage with _event_type=usage metadata."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"
        worker = service._create_detached_session_worker(
            session_key, client=MagicMock(), channel="test", chat_id="c1",
        )
        service._session_workers[session_key] = worker

        await service._publish_worker_response(
            worker,
            AgentResponse(
                content="Answer",
                usage={"input_tokens": 100, "output_tokens": 50},
            ),
            bus,
        )

        usage_calls = [
            c for c in bus.publish_outbound.call_args_list
            if c.args[0].metadata.get("_event_type") == "usage"
        ]
        assert len(usage_calls) == 1
        assert "100" in usage_calls[0].args[0].content
        assert "50" in usage_calls[0].args[0].content

    @pytest.mark.asyncio
    async def test_worker_publishes_result_after_deltas(self, config, shared_resources, bus):
        """Result should be published as a non-progress message after delta forwarding."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"
        worker = service._create_detached_session_worker(
            session_key, client=MagicMock(), channel="test", chat_id="c1",
        )
        service._session_workers[session_key] = worker

        await service._publish_worker_response(
            worker, AgentResponse(content="", is_delta=True, delta_content="A"), bus,
        )
        await service._publish_worker_response(
            worker, AgentResponse(content="", is_delta=True, delta_content="B"), bus,
        )
        await service._publish_worker_response(
            worker, AgentResponse(content="AB", event_type="result"), bus,
        )

        final_messages = [
            c.args[0]
            for c in bus.publish_outbound.call_args_list
            if not c.args[0].metadata.get("_progress")
            and not c.args[0].metadata.get("_tool_hint")
        ]
        assert final_messages
        assert final_messages[-1].content == "AB"

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

    # --- Test 5: Local command !stop ---

    @pytest.mark.asyncio
    async def test_local_command_stop(self, config, shared_resources, bus, state_manager):
        """!stop should interrupt the session worker and preserve it."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"

        mock_client = MagicMock()
        mock_client.interrupt = AsyncMock()
        worker = service._create_detached_session_worker(
            session_key=session_key,
            client=mock_client,
            channel="test",
            chat_id="c1",
        )
        await worker.input_queue.put({"type": "user", "message": {"role": "user", "content": "queued"}})
        service._session_workers[session_key] = worker

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="!stop")
        await service._command_handler.handle(msg, bus)

        mock_client.interrupt.assert_awaited_once()
        assert session_key in service._session_workers
        assert worker.input_queue.empty()
        assert state_manager.get_phase(session_key) == SessionPhase.IDLE
        assert bus.publish_outbound.call_count == 1
        assert "stopped" in bus.publish_outbound.call_args.args[0].content.lower() or \
               "stop" in bus.publish_outbound.call_args.args[0].content.lower()

    # --- Test 6: Native worker enqueue ---

    @pytest.mark.asyncio
    async def test_run_enqueues_same_session_messages_without_busy_reject(self, config, shared_resources, bus):
        """Same-session messages should enter the worker FIFO instead of being busy-rejected."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"
        worker = service._create_detached_session_worker(
            session_key=session_key,
            client=MagicMock(),
            channel="test",
            chat_id="c1",
        )
        service._session_workers[session_key] = worker

        first = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="first")
        second = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="second")

        await service._enqueue_worker_message(first, bus)
        await service._enqueue_worker_message(second, bus)

        queued = [await worker.input_queue.get(), await worker.input_queue.get()]
        assert [item["message"]["content"] for item in queued] == ["first", "second"]
        assert all(item["session_id"] == session_key for item in queued)
        assert not any(
            call.args[0].metadata.get("busy_reject")
            for call in bus.publish_outbound.call_args_list
        )

    @pytest.mark.asyncio
    async def test_worker_input_frame_includes_media_references(self, config, shared_resources, tmp_path):
        """Worker input frames should use the same media prompt injection as process()."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"
        bus = MagicMock()
        bus.publish_outbound = AsyncMock()
        image_path = tmp_path / "demo.png"
        image_path.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
            b"\x1f\x15\xc4\x89"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        worker = service._create_detached_session_worker(
            session_key=session_key,
            client=MagicMock(),
            channel="test",
            chat_id="c1",
        )
        service._session_workers[session_key] = worker

        msg = InboundMessage(
            channel="test",
            sender_id="u1",
            chat_id="c1",
            content="describe",
            media=[str(image_path)],
        )
        await service._enqueue_worker_message(msg, bus)

        frame = await worker.input_queue.get()
        assert frame["type"] == "user"
        assert frame["message"]["role"] == "user"
        assert frame["parent_tool_use_id"] is None
        assert frame["session_id"] == session_key
        content = frame["message"]["content"]
        # Multimodal: images become base64 content blocks instead of text refs.
        assert isinstance(content, list)
        image_blocks = [b for b in content if b.get("type") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["source"]["type"] == "base64"
        assert image_blocks[0]["source"]["media_type"] == "image/png"
        text_blocks = [b for b in content if b.get("type") == "text"]
        assert text_blocks
        assert text_blocks[-1]["text"] == "describe"

    @pytest.mark.asyncio
    async def test_reset_session_removes_session_worker(self, config, shared_resources, bus):
        """Reset should destroy the session worker instead of keeping runtime state."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"
        mock_client = MagicMock()
        mock_client.disconnect = AsyncMock()
        worker = service._create_detached_session_worker(
            session_key=session_key,
            client=mock_client,
            channel="test",
            chat_id="c1",
        )
        service._session_workers[session_key] = worker
        await service._enqueue_worker_message(
            InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="queued"),
            bus,
        )

        await service.reset_session(session_key)

        assert session_key not in service._session_workers
        mock_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_idle_boundary_keeps_worker_alive(self, config, shared_resources, bus, state_manager):
        """Idle boundaries should mark the session idle without closing the worker."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"
        worker = service._create_detached_session_worker(
            session_key=session_key,
            client=MagicMock(),
            channel="test",
            chat_id="c1",
        )
        service._session_workers[session_key] = worker
        idle_msg = SystemMessage()

        await service._handle_worker_sdk_message(worker, idle_msg, bus)

        assert service._session_workers[session_key] is worker
        assert worker.closed is False
        assert state_manager.get_phase(session_key) == SessionPhase.IDLE

    @pytest.mark.asyncio
    async def test_worker_client_ready_does_not_regress_active_turn(self, config, shared_resources, bus, state_manager):
        """Late worker client-ready event should not move a queued turn back to sending_query."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"
        worker = service._create_detached_session_worker(
            session_key=session_key,
            client=MagicMock(),
            channel="test",
            chat_id="c1",
        )
        service._session_workers[session_key] = worker
        await service._enqueue_worker_message(
            InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="queued"),
            bus,
        )

        service._dispatch_worker_client_ready(session_key)
        idle_msg = SystemMessage()

        await service._handle_worker_sdk_message(worker, idle_msg, bus)

        assert state_manager.get_phase(session_key) == SessionPhase.IDLE

    @pytest.mark.asyncio
    async def test_worker_stream_error_removes_worker(self, config, shared_resources, bus):
        """A failed worker should be removed so the next message can recreate it."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.receive_messages.side_effect = RuntimeError("stream failed")
        mock_client.disconnect = AsyncMock()
        worker = service._create_detached_session_worker(
            session_key=session_key,
            client=mock_client,
            channel="test",
            chat_id="c1",
        )
        service._session_workers[session_key] = worker

        await service._run_session_worker(worker, bus)

        assert worker.closed is True
        assert session_key not in service._session_workers
        mock_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_worker_publishes_multiple_results(self, config, shared_resources, bus):
        """A single worker should publish results for multiple SDK result events."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"
        worker = service._create_detached_session_worker(
            session_key,
            client=MagicMock(),
            channel="test",
            chat_id="c1",
        )
        service._session_workers[session_key] = worker
        first = AgentResponse(content="first result", event_type="result")
        second = AgentResponse(content="second result", event_type="result")

        await service._publish_worker_response(worker, first, bus)
        await service._publish_worker_response(worker, second, bus)

        contents = [call.args[0].content for call in bus.publish_outbound.call_args_list]
        assert "first result" in contents
        assert "second result" in contents

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
    async def test_workspace_command(self, config, shared_resources, tmp_path):
        """Workspace commands should inject command content into prompt via _prepare_prompt_from_message."""
        # Create a command file
        cmd_dir = tmp_path / "commands"
        cmd_dir.mkdir()
        (cmd_dir / "greet.md").write_text("Say hello in a friendly way")

        service = await self._make_service(config, shared_resources)

        # Re-initialize commands_loader with the right path
        from xbot.runtime.core.context.commands import CommandsLoader
        service._commands_loader = CommandsLoader(tmp_path)

        msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/greet")
        prompt = service._prepare_prompt_from_message(msg)
        assert prompt == "Say hello in a friendly way"

    # --- Test 11: System message (compact) forwarding ---

    @pytest.mark.asyncio
    async def test_system_message_compact_forwarding(self, config, shared_resources, bus):
        """_publish_worker_response should forward compact system messages as progress with event_type=system."""
        service = await self._make_service(config, shared_resources)
        session_key = "test:c1"
        worker = service._create_detached_session_worker(
            session_key, client=MagicMock(), channel="test", chat_id="c1",
        )
        service._session_workers[session_key] = worker

        await service._publish_worker_response(
            worker,
            AgentResponse(
                content="",
                progress_texts=["\U0001f504 Compressing context..."],
                event_type="system",
                event_data={"subtype": "pre_compact"},
            ),
            bus,
        )

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

    @pytest.mark.asyncio
    async def test_convert_event_handles_sdk_system_subclasses(self, config, shared_resources):
        """SDK SystemMessage subclasses should still be routed through system conversion."""
        service = await self._make_service(config, shared_resources)

        MirrorErrorMessage = type("MirrorErrorMessage", (), {})

        msg = MirrorErrorMessage()
        msg.subtype = "mirror_error"
        msg.error = "store unavailable"
        msg.data = {"error": "store unavailable"}

        result = service._convert_event(msg)

        assert result is not None
        assert result.event_type == "system"
        assert result.event_data == {"subtype": "mirror_error"}
        assert result.progress_texts == ["Session mirror failed: store unavailable"]

    @pytest.mark.asyncio
    async def test_convert_assistant_message_includes_server_tool_blocks(self, config, shared_resources):
        """Server-side tool blocks from newer SDK versions should surface as tool progress."""
        service = await self._make_service(config, shared_resources)

        AssistantMessage = type("AssistantMessage", (), {})
        ServerToolUseBlock = type("ServerToolUseBlock", (), {})
        ServerToolResultBlock = type("ServerToolResultBlock", (), {})

        use_block = ServerToolUseBlock()
        use_block.id = "srv-1"
        use_block.name = "web_search"
        use_block.input = {"query": "claude-agent-sdk"}

        result_block = ServerToolResultBlock()
        result_block.tool_use_id = "srv-1"
        result_block.content = {"type": "web_search_result", "total_results": 3}

        msg = AssistantMessage()
        msg.content = [use_block, result_block]

        result = service._convert_assistant_message(msg)

        assert result is not None
        assert result.event_type == "tool_call"
        assert result.finish_reason == "tool_use"
        assert result.tool_calls == [
            {
                "id": "srv-1",
                "name": "web_search",
                "input": {"query": "claude-agent-sdk"},
                "kind": "server_tool",
            },
            {
                "id": "srv-1",
                "name": "server_tool_result",
                "input": {"type": "web_search_result", "total_results": 3},
                "kind": "server_tool_result",
            },
        ]

    @pytest.mark.asyncio
    async def test_convert_assistant_message_ignores_empty_content(self, config, shared_resources):
        """Empty AssistantMessage frames should not surface as blank user-visible replies."""
        service = await self._make_service(config, shared_resources)
        AssistantMessage = type("AssistantMessage", (), {})

        msg = AssistantMessage()
        msg.content = []

        assert service._convert_assistant_message(msg) is None

    @pytest.mark.asyncio
    async def test_convert_actual_claude_agent_sdk_new_message_types(self, config, shared_resources):
        """Actual claude-agent-sdk 0.1.73 message classes should convert without adapters."""
        from claude_agent_sdk import (
            AssistantMessage,
            MirrorErrorMessage,
            ServerToolResultBlock,
            ServerToolUseBlock,
        )

        service = await self._make_service(config, shared_resources)

        mirror = MirrorErrorMessage(
            subtype="mirror_error",
            data={"error": "store unavailable"},
            error="store unavailable",
        )
        mirror_result = service._convert_event(mirror)
        assert mirror_result is not None
        assert mirror_result.event_type == "system"
        assert mirror_result.progress_texts == ["Session mirror failed: store unavailable"]

        assistant = AssistantMessage(
            content=[
                ServerToolUseBlock(
                    id="srv-1",
                    name="web_search",
                    input={"query": "claude-agent-sdk"},
                ),
                ServerToolResultBlock(
                    tool_use_id="srv-1",
                    content={"type": "web_search_result", "total_results": 3},
                ),
            ],
            model="claude-sonnet-4-6",
        )
        assistant_result = service._convert_event(assistant)
        assert assistant_result is not None
        assert assistant_result.event_type == "tool_call"
        assert assistant_result.tool_calls is not None
        assert [tc["kind"] for tc in assistant_result.tool_calls] == [
            "server_tool",
            "server_tool_result",
        ]

    @pytest.mark.asyncio
    async def test_process_converts_new_sdk_events_until_idle(self, config, shared_resources, state_manager):
        """process() should yield new SDK event types and still stop on the idle boundary."""
        from claude_agent_sdk import (
            AssistantMessage,
            MirrorErrorMessage,
            ResultMessage,
            ServerToolUseBlock,
            SystemMessage,
        )

        service = await self._make_service(config, shared_resources)

        async def receive_messages():
            yield MirrorErrorMessage(
                subtype="mirror_error",
                data={"error": "store unavailable"},
                error="store unavailable",
            )
            yield AssistantMessage(
                content=[
                    ServerToolUseBlock(
                        id="srv-1",
                        name="web_search",
                        input={"query": "claude-agent-sdk"},
                    )
                ],
                model="claude-sonnet-4-6",
                session_id="sdk-session-3",
            )
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="sdk-session-3",
                result="done",
            )
            yield SystemMessage(
                subtype="session_state_changed",
                data={"state": "idle", "session_id": "sdk-session-3"},
            )

        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_client.receive_messages = receive_messages
        mock_client.get_server_info = AsyncMock(return_value={})

        context = AgentContext(session_key="test:c1", prompt="hello", channel="test", chat_id="c1")
        with patch.object(service, "_get_or_create_client", AsyncMock(return_value=mock_client)):
            responses = [response async for response in service.process(context)]

        assert [r.event_type for r in responses] == ["system", "tool_call", "result"]
        assert responses[0].progress_texts == ["Session mirror failed: store unavailable"]
        assert responses[1].tool_calls is not None
        assert responses[1].tool_calls[0]["kind"] == "server_tool"
        assert responses[2].content == "done"
        assert state_manager.resolve_compact_notification_target("sdk-session-3") == ("test:c1", "test", "c1")

    def test_format_tool_hint_labels_server_tools(self):
        """Server-side tools should be distinguishable in progress hints."""
        hint = AgentService._format_tool_hint([
            {
                "id": "srv-1",
                "name": "web_search",
                "input": {"query": "claude-agent-sdk"},
                "kind": "server_tool",
            },
            {
                "id": "srv-1",
                "name": "server_tool_result",
                "input": {"type": "web_search_result"},
                "kind": "server_tool_result",
            },
        ])

        assert "Server tool: web_search" in hint
        assert "Server tool result: server_tool_result" in hint

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

    # --- Test 24: async consolidation task self-removes via done_callback ---

    @pytest.mark.asyncio
    async def test_dispatch_memory_consolidation_async_removes_task_on_done(
        self, config, shared_resources, bus
    ) -> None:
        """Async consolidation task must self-discard from the set once completed."""
        from xbot.platform.config.schema import Config

        runtime_config = Config()
        runtime_config.agents.claude_sdk.memory_consolidation_mode = "async"
        shared_resources["config"] = runtime_config

        service = await self._make_service(config, shared_resources)
        service._memory_consolidator = AsyncMock()

        session = MagicMock()
        await service._trigger_memory_consolidation("test-session-done", session)

        assert len(service._async_consolidation_tasks) == 1
        tracked_task = next(iter(service._async_consolidation_tasks))

        # Wait for task and its done_callback to run.
        await asyncio.gather(tracked_task, return_exceptions=True)
        await asyncio.sleep(0)

        assert not service._async_consolidation_tasks

    # --- Test 25-27: hook notification fire-and-forget task tracking ---

    @pytest.mark.asyncio
    async def test_track_hook_notification_task_success_cleanup(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Hook notification task should be tracked and discarded on completion."""
        service = AgentService()
        await service.initialize(config, shared_resources)

        completed = asyncio.Event()

        async def _ok() -> None:
            completed.set()

        task = service._track_hook_notification_task(
            _ok(), hook_type="pre_compact", session_ref="s1"
        )

        assert task is not None
        assert task in service._async_hook_notification_tasks

        await completed.wait()
        # Give the done_callback a chance to run.
        await asyncio.sleep(0)

        assert task not in service._async_hook_notification_tasks
        assert not service._async_hook_notification_tasks

    @pytest.mark.asyncio
    async def test_track_hook_notification_task_exception_consumed(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Hook notification exceptions should be logged and set cleared."""
        service = AgentService()
        await service.initialize(config, shared_resources)

        async def _boom() -> None:
            raise RuntimeError("boom-hook")

        task = service._track_hook_notification_task(
            _boom(), hook_type="subagent_compat", session_ref="s2"
        )

        assert task is not None
        await asyncio.gather(task, return_exceptions=True)
        # Give the done_callback a chance to run.
        await asyncio.sleep(0)

        assert not service._async_hook_notification_tasks
        assert "Hook notification (subagent_compat) failed for s2" in caplog.text
        assert "boom-hook" in caplog.text

    def test_track_hook_notification_task_no_running_loop(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Without a running loop, coroutine must be closed and warning logged."""
        import inspect as _inspect

        service = AgentService()

        called = False

        async def _never() -> None:
            nonlocal called
            called = True

        coro = _never()

        result = service._track_hook_notification_task(
            coro, hook_type="pre_compact", session_ref="s3"
        )

        assert result is None
        assert not service._async_hook_notification_tasks
        assert not called  # coro was closed, not awaited
        assert _inspect.getcoroutinestate(coro) == _inspect.CORO_CLOSED
        assert "Cannot schedule hook notification (pre_compact) for s3" in caplog.text
