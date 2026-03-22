"""Tests for Claude SDK Backend."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class TestClaudeSDKBackendConcurrency:
    """Tests for concurrent client creation."""

    @pytest.mark.asyncio
    async def test_get_or_create_client_concurrent_requests(self):
        """Test that concurrent requests for the same session only create one client."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()
            backend._build_options = MagicMock(return_value=MagicMock())

            connect_call_count = 0

            async def mock_connect():
                nonlocal connect_call_count
                connect_call_count += 1
                await asyncio.sleep(0.01)

            mock_client = MagicMock()
            mock_client.connect = mock_connect

            with patch(
                "xbot.agent.backends.claude_sdk_backend._ClaudeSDKClient",
                return_value=mock_client,
            ):
                tasks = [
                    backend._get_or_create_client("test_session")
                    for _ in range(10)
                ]
                results = await asyncio.gather(*tasks)

                assert all(r is results[0] for r in results)
                assert connect_call_count == 1

    @pytest.mark.asyncio
    async def test_get_or_create_client_different_sessions(self):
        """Test that different sessions get different clients."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()
            backend._build_options = MagicMock(return_value=MagicMock())

            client_count = 0

            async def mock_connect():
                pass

            def create_mock_client(*args, **kwargs):
                nonlocal client_count
                client_count += 1
                mock = MagicMock()
                mock.connect = mock_connect
                mock.id = client_count
                return mock

            with patch(
                "xbot.agent.backends.claude_sdk_backend._ClaudeSDKClient",
                side_effect=create_mock_client,
            ):
                tasks = [
                    backend._get_or_create_client(f"session_{i}")
                    for i in range(5)
                ]
                results = await asyncio.gather(*tasks)

                client_ids = [r.id for r in results]
                assert len(set(client_ids)) == 5


class TestClaudeSDKBackendLogging:
    """Tests for logging format."""

    def test_logger_uses_correct_format(self, caplog):
        """Test that logger uses f-string format correctly."""
        import logging

        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import logger

            assert isinstance(logger, logging.Logger)

            with caplog.at_level(logging.INFO):
                test_msg = "test message 123"
                logger.info(f"Test log: {test_msg}")

            assert test_msg in caplog.text


class TestClaudeSDKBackendShutdown:
    """Tests for backend shutdown behavior."""

    @pytest.mark.asyncio
    async def test_shutdown_clears_clients(self):
        """Test that shutdown properly disconnects and clears all clients."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()

            mock_clients = {}
            for i in range(3):
                mock_client = MagicMock()
                mock_client.disconnect = AsyncMock()
                mock_clients[f"session_{i}"] = mock_client

            backend._clients = mock_clients

            await backend.shutdown()

            for client in mock_clients.values():
                client.disconnect.assert_called_once()

            assert len(backend._clients) == 0


class TestClaudeSDKBackendResetSession:
    """Tests for session reset behavior."""

    @pytest.mark.asyncio
    async def test_reset_session_disconnects_client(self):
        """Test that reset_session disconnects the client for that session."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()

            mock_client = MagicMock()
            mock_client.disconnect = AsyncMock()
            backend._clients["test_session"] = mock_client

            backend.sessions = MagicMock()
            mock_session = MagicMock()
            mock_session.messages = []
            mock_session.last_consolidated = 0
            mock_session.metadata = {}
            backend.sessions.get_or_create = MagicMock(return_value=mock_session)
            backend.sessions.save = MagicMock()
            backend.sessions.invalidate = MagicMock()

            await backend.reset_session("test_session")

            mock_client.disconnect.assert_called_once()
            assert "test_session" not in backend._clients
            mock_session.clear.assert_called_once()


class TestClaudeSDKBackendToolContext:
    """Tests for tool context wiring in process()."""

    @pytest.mark.asyncio
    async def test_process_sets_tool_context_with_session_key_and_resets_message_turn(self):
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
            from xbot.agent.protocol import AgentContext

            backend = ClaudeSDKBackend()

            message_tool = MagicMock()
            tool_adapter = MagicMock()
            tool_adapter._tools = {"message": message_tool}
            tool_adapter.get_tool.return_value = message_tool
            backend._tool_adapter = tool_adapter

            mock_client = MagicMock()
            mock_client.query = AsyncMock()

            async def _empty_receive():
                if False:
                    yield None

            mock_client.receive_response = _empty_receive
            backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]
            backend.sessions = None

            context = AgentContext(
                session_key="slack:C123:thread:1700.1",
                prompt="hello",
                channel="slack",
                chat_id="C123",
                metadata={"message_id": "m-1"},
            )

            _ = [resp async for resp in backend.process(context)]

            tool_adapter.set_tool_context.assert_called_once_with(
                channel="slack",
                chat_id="C123",
                session_key="slack:C123:thread:1700.1",
                message_id="m-1",
            )
            message_tool.start_turn.assert_called_once()


class TestMessageConverter:
    """Tests for MessageConverter class."""

    def test_message_converter_initialization(self):
        """Test MessageConverter can be initialized."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import MessageConverter

            converter = MessageConverter(
                handoff_policy=None,
                capabilities=None,
                config=None,
            )
            assert converter is not None


class TestOptionsBuilder:
    """Tests for OptionsBuilder class."""

    def test_options_builder_detect_provider_from_model(self):
        """Test provider detection from model name."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder

            builder = OptionsBuilder(
                shared_resources={},
                sdk_config=None,
                skill_converter=None,
                tool_adapter=None,
                sessions=None,
                context_builder=None,
                handoff_policy=None,
                capability_policy=None,
            )

            # Test Claude detection
            assert builder._detect_provider_from_model("claude-3-opus") == "anthropic"
            assert builder._detect_provider_from_model("Claude-3-Sonnet") == "anthropic"

            # Test Qwen/GLM detection
            assert builder._detect_provider_from_model("qwen-turbo") == "aliyun_coding_plan"
            assert builder._detect_provider_from_model("glm-4") == "aliyun_coding_plan"

            # Test Alrun detection (must start with "alrun-")
            assert builder._detect_provider_from_model("alrun-qwen") == "alrun"
            assert builder._detect_provider_from_model("alrun-model") == "alrun"

            # Test default
            assert builder._detect_provider_from_model("unknown-model") == "anthropic"

    def test_build_mcp_servers_with_pydantic_config(self):
        """Test that MCPServerConfig Pydantic objects are converted to dicts for JSON serialization."""
        import json

        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder
            from xbot.config.schema import MCPServerConfig

            # Create Pydantic MCPServerConfig objects
            server_config = MCPServerConfig(
                command="npx",
                args=["-y", "@example/mcp-server"],
                env={"API_KEY": "test123"},
                tool_timeout=60,
            )

            # Mock config with mcp_servers
            mock_tools = MagicMock()
            mock_tools.mcp_servers = {
                "test_server": server_config,
            }

            mock_config = MagicMock()
            mock_config.tools = mock_tools

            builder = OptionsBuilder(
                shared_resources={"config": mock_config},
                sdk_config=None,
                skill_converter=None,
                tool_adapter=None,
                sessions=None,
                context_builder=None,
                handoff_policy=None,
                capability_policy=None,
            )

            mcp_servers = builder._build_mcp_servers()

            # Verify the server config was converted to dict
            assert "test_server" in mcp_servers
            assert isinstance(mcp_servers["test_server"], dict)
            assert mcp_servers["test_server"]["command"] == "npx"
            assert mcp_servers["test_server"]["args"] == ["-y", "@example/mcp-server"]
            assert mcp_servers["test_server"]["env"] == {"API_KEY": "test123"}
            assert mcp_servers["test_server"]["tool_timeout"] == 60

            # Critical: Verify JSON serialization works
            json_str = json.dumps(mcp_servers)
            assert "test_server" in json_str
            assert "npx" in json_str

    def test_get_model_name_normalizes_legacy_prefix(self):
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder
            from xbot.config.schema import Config

            config = Config()
            config.agents.defaults.provider = "anthropic"
            config.agents.defaults.model = "anthropic/claude-sonnet-4-5"
            config.providers.anthropic.api_key = "test-key"

            builder = OptionsBuilder(
                shared_resources={"config": config},
                sdk_config=None,
                skill_converter=None,
                tool_adapter=None,
                sessions=None,
                context_builder=None,
                handoff_policy=None,
                capability_policy=None,
            )

            assert builder._get_model_name() == "claude-sonnet-4-5"


class TestClaudeSDKBackendMemoryConfig:
    """Tests for memory backend configuration wiring."""

    @pytest.mark.asyncio
    async def test_initialize_uses_file_memory_provider(self, tmp_path):
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
            from xbot.config.schema import Config

            config = Config()
            config.agents.defaults.provider = "auto"
            config.tools.memory.provider = "file"
            config.tools.memory.enable_vector_search = True
            config.tools.memory.llm_model = "gpt-4.1-nano"

            captured = {}

            class _FakeContextBuilder:
                def __init__(self, workspace, use_reme=True, llm_config=None, enable_vector_search=False):
                    captured["workspace"] = workspace
                    captured["use_reme"] = use_reme
                    captured["llm_config"] = llm_config
                    captured["enable_vector_search"] = enable_vector_search
                    self.memory = object()

            backend = ClaudeSDKBackend()

            with patch("xbot.agent.backends.claude_sdk_backend.ContextBuilder", _FakeContextBuilder):
                await backend.initialize(
                    config.agents,
                    {
                        "workspace": tmp_path,
                        "config": config,
                        "tools_config": config.tools,
                    },
                )

            assert captured["workspace"] == tmp_path
            assert captured["use_reme"] is False
            assert captured["enable_vector_search"] is True
            assert captured["llm_config"] == {"model_name": "gpt-4.1-nano"}

    def test_build_mcp_servers_with_dict_config(self):
        """Test that dict configs are passed through unchanged."""
        import json

        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder

            # Dict config (non-Pydantic)
            dict_config = {
                "command": "python",
                "args": ["server.py"],
                "env": {},
            }

            mock_tools = MagicMock()
            mock_tools.mcp_servers = {
                "dict_server": dict_config,
            }

            mock_config = MagicMock()
            mock_config.tools = mock_tools

            builder = OptionsBuilder(
                shared_resources={"config": mock_config},
                sdk_config=None,
                skill_converter=None,
                tool_adapter=None,
                sessions=None,
                context_builder=None,
                handoff_policy=None,
                capability_policy=None,
            )

            mcp_servers = builder._build_mcp_servers()

            # Dict should be passed through unchanged
            assert "dict_server" in mcp_servers
            assert mcp_servers["dict_server"]["command"] == "python"

            # JSON serialization should work
            json_str = json.dumps(mcp_servers)
            assert "python" in json_str

    def test_build_mcp_servers_mixed_configs(self):
        """Test mixed Pydantic and dict configs."""
        import json

        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder
            from xbot.config.schema import MCPServerConfig

            pydantic_config = MCPServerConfig(
                command="npx",
                args=["-y", "@pydantic/server"],
            )

            dict_config = {
                "command": "python",
                "args": ["dict_server.py"],
            }

            mock_tools = MagicMock()
            mock_tools.mcp_servers = {
                "pydantic_server": pydantic_config,
                "dict_server": dict_config,
            }

            mock_config = MagicMock()
            mock_config.tools = mock_tools

            builder = OptionsBuilder(
                shared_resources={"config": mock_config},
                sdk_config=None,
                skill_converter=None,
                tool_adapter=None,
                sessions=None,
                context_builder=None,
                handoff_policy=None,
                capability_policy=None,
            )

            mcp_servers = builder._build_mcp_servers()

            # Both should be dicts
            assert isinstance(mcp_servers["pydantic_server"], dict)
            assert isinstance(mcp_servers["dict_server"], dict)

            # JSON serialization should work for entire result
            json_str = json.dumps(mcp_servers)
            assert "pydantic_server" in json_str
            assert "dict_server" in json_str

    def test_build_mcp_servers_empty(self):
        """Test empty mcp_servers returns empty dict."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder

            mock_tools = MagicMock()
            mock_tools.mcp_servers = {}

            mock_config = MagicMock()
            mock_config.tools = mock_tools

            builder = OptionsBuilder(
                shared_resources={"config": mock_config},
                sdk_config=None,
                skill_converter=None,
                tool_adapter=None,
                sessions=None,
                context_builder=None,
                handoff_policy=None,
                capability_policy=None,
            )

            mcp_servers = builder._build_mcp_servers()
            assert mcp_servers == {}

    def test_build_mcp_servers_excludes_none_values(self):
        """Test that None values are excluded from serialized config."""
        import json

        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder
            from xbot.config.schema import MCPServerConfig

            # Create config with some None/default values
            server_config = MCPServerConfig(
                command="npx",
                args=["server"],
                url="",  # empty string (default)
                headers={},  # empty dict (default)
            )

            mock_tools = MagicMock()
            mock_tools.mcp_servers = {"server": server_config}

            mock_config = MagicMock()
            mock_config.tools = mock_tools

            builder = OptionsBuilder(
                shared_resources={"config": mock_config},
                sdk_config=None,
                skill_converter=None,
                tool_adapter=None,
                sessions=None,
                context_builder=None,
                handoff_policy=None,
                capability_policy=None,
            )

            mcp_servers = builder._build_mcp_servers()

            # Verify serialization works
            json_str = json.dumps(mcp_servers)
            parsed = json.loads(json_str)

            # None values should be excluded
            assert "command" in parsed["server"]
            # url is empty string, not None, so it should be present
            # but type would be excluded if it was None


class TestDelegationTrace:
    """Tests for delegation tracing."""

    def test_delegation_trace_creation(self):
        """Test DelegationTrace dataclass."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import DelegationTrace

            trace = DelegationTrace(
                timestamp="2026-03-21T01:00:00",
                session_key="test_session",
                decision_mode="native_handoff",
                reason="specialist matched",
                candidates=["agent1", "agent2"],
            )

            assert trace.session_key == "test_session"
            assert trace.decision_mode == "native_handoff"
            assert trace.candidates == ["agent1", "agent2"]

            # Test to_dict
            trace_dict = trace.to_dict()
            assert trace_dict["session_key"] == "test_session"
            assert trace_dict["mode"] == "native_handoff"

    @pytest.mark.asyncio
    async def test_backend_records_delegation_trace(self):
        """Test that backend records delegation traces."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
            from xbot.agent.handoff_policy import HandoffDecision

            backend = ClaudeSDKBackend()
            backend._delegation_traces = []
            backend._delegation_traces_lock = asyncio.Lock()

            decision = HandoffDecision(
                mode="native_handoff",
                reason="test",
                candidate_agents=("agent1",),
            )

            backend._record_delegation_trace("test_session", decision)

            # Wait for async task
            await asyncio.sleep(0.1)

            traces = backend.get_delegation_traces()
            assert len(traces) == 1
            assert traces[0]["session_key"] == "test_session"
            assert traces[0]["mode"] == "native_handoff"

    def test_get_delegation_traces_filtered(self):
        """Test getting filtered delegation traces."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend, DelegationTrace

            backend = ClaudeSDKBackend()
            backend._delegation_traces = [
                DelegationTrace("2026-03-21T01:00:00", "session1", "main", "reason1", []),
                DelegationTrace("2026-03-21T01:01:00", "session2", "handoff", "reason2", ["agent1"]),
                DelegationTrace("2026-03-21T01:02:00", "session1", "background", "reason3", []),
            ]

            # Get all traces
            all_traces = backend.get_delegation_traces()
            assert len(all_traces) == 3

            # Get filtered traces
            session1_traces = backend.get_delegation_traces("session1")
            assert len(session1_traces) == 2

    @pytest.mark.asyncio
    async def test_delegation_traces_limit(self):
        """Test that delegation traces are limited to 100."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
            from xbot.agent.handoff_policy import HandoffDecision

            backend = ClaudeSDKBackend()
            backend._delegation_traces = []
            backend._delegation_traces_lock = asyncio.Lock()

            decision = HandoffDecision(mode="main", reason="test", candidate_agents=())

            # Add 150 traces
            for i in range(150):
                backend._record_delegation_trace(f"session_{i}", decision)

            # Wait for all async tasks
            await asyncio.sleep(0.5)

            # Should be limited to 100
            assert len(backend._delegation_traces) == 100
            # Should keep the most recent
            assert backend._delegation_traces[-1].session_key == "session_149"


class TestTypeAnnotations:
    """Tests for type annotations."""

    def test_backend_has_proper_type_hints(self):
        """Test that backend has proper type hints."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
            import typing

            # Check that _clients is properly typed
            backend = ClaudeSDKBackend()
            assert isinstance(backend._clients, dict)
            assert isinstance(backend._clients_lock, asyncio.Lock)


class TestInterruptSession:
    """Tests for interrupt_session method."""

    @pytest.mark.asyncio
    async def test_interrupt_session_no_client(self):
        """Test interrupt_session returns False when no client exists."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()
            backend._clients = {}

            result = await backend.interrupt_session("nonexistent_session")
            assert result is False

    @pytest.mark.asyncio
    async def test_interrupt_session_success(self):
        """Test interrupt_session calls client.interrupt() and returns True."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()

            mock_client = MagicMock()
            mock_client.interrupt = AsyncMock()
            backend._clients["test_session"] = mock_client

            result = await backend.interrupt_session("test_session")
            assert result is True
            mock_client.interrupt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_interrupt_session_exception(self):
        """Test interrupt_session returns False on exception."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()

            mock_client = MagicMock()
            mock_client.interrupt = AsyncMock(side_effect=Exception("Interrupt failed"))
            backend._clients["test_session"] = mock_client

            result = await backend.interrupt_session("test_session")
            assert result is False


class TestStopActiveTask:
    """Tests for stop_active_task method."""

    @pytest.mark.asyncio
    async def test_stop_active_task_no_task(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend._active_task_ids = {}
        backend._clients = {}

        result = await backend.stop_active_task("test_session")
        assert result is False

    @pytest.mark.asyncio
    async def test_stop_active_task_success(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend._active_task_ids["test_session"] = "task-1"

        mock_client = MagicMock()
        mock_client.stop_task = AsyncMock()
        backend._clients["test_session"] = mock_client

        result = await backend.stop_active_task("test_session")

        assert result is True
        mock_client.stop_task.assert_awaited_once_with("task-1")
        assert "test_session" not in backend._active_task_ids

    @pytest.mark.asyncio
    async def test_stop_active_task_exception(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend._active_task_ids["test_session"] = "task-1"

        mock_client = MagicMock()
        mock_client.stop_task = AsyncMock(side_effect=Exception("stop failed"))
        backend._clients["test_session"] = mock_client

        result = await backend.stop_active_task("test_session")
        assert result is False

    @pytest.mark.asyncio
    async def test_terminal_task_notification_clears_active_task(self):
        from claude_agent_sdk.types import ResultMessage, TaskNotificationMessage, TaskStartedMessage
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.protocol import AgentContext

        backend = ClaudeSDKBackend()
        backend._message_converter = None  # Force legacy convert path still records task lifecycle

        mock_session = MagicMock()
        mock_session.metadata = {}
        mock_session.add_message = MagicMock()
        backend.sessions = MagicMock()
        backend.sessions.get_or_create = MagicMock(return_value=mock_session)
        backend.sessions.save = MagicMock()

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def _receive():
            yield TaskStartedMessage(
                subtype="task_started",
                data={},
                task_id="task-1",
                description="work",
                uuid="u1",
                session_id="s1",
            )
            yield TaskNotificationMessage(
                subtype="task_notification",
                data={},
                task_id="task-1",
                status="completed",
                output_file="",
                summary="done",
                uuid="u2",
                session_id="s1",
            )
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="s1",
            )

        mock_client.receive_response = _receive
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        context = AgentContext(session_key="test_session", prompt="hello")
        _ = [msg async for msg in backend.process(context)]

        assert "test_session" not in backend._active_task_ids

    @pytest.mark.asyncio
    async def test_result_message_clears_active_task_without_session_store(self):
        from claude_agent_sdk.types import ResultMessage, TaskStartedMessage
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.protocol import AgentContext

        backend = ClaudeSDKBackend()
        backend._message_converter = None
        backend.sessions = None

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def _receive():
            yield TaskStartedMessage(
                subtype="task_started",
                data={},
                task_id="task-2",
                description="work",
                uuid="u1",
                session_id="s1",
            )
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="s1",
            )

        mock_client.receive_response = _receive
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        context = AgentContext(session_key="test_session", prompt="hello")
        _ = [msg async for msg in backend.process(context)]

        assert "test_session" not in backend._active_task_ids


class TestSessionCommands:
    """Tests for SDK slash command discovery/cache."""

    def test_extract_slash_commands_from_slash_commands_field(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        commands = ClaudeSDKBackend._extract_slash_commands(
            {"slash_commands": ["/compact", "/clear", "not-slash", "/help"]}
        )
        assert commands == ["/clear", "/compact", "/help"]

    def test_extract_slash_commands_from_commands_field(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        commands = ClaudeSDKBackend._extract_slash_commands(
            {
                "commands": [
                    {"name": "/compact"},
                    {"name": "plain"},
                    "/clear",
                ]
            }
        )
        assert commands == ["/clear", "/compact", "/plain"]

    def test_extract_slash_commands_normalizes_sdk_command_names(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        commands = ClaudeSDKBackend._extract_slash_commands(
            {
                "commands": [
                    {"name": "help"},
                    {"name": "compact"},
                    {"name": "clear"},
                ]
            }
        )
        assert commands == ["/clear", "/compact", "/help"]

    @pytest.mark.asyncio
    async def test_get_session_commands_fetches_on_cache_miss(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend._session_commands = {}

        async def _fake_get_or_create_client(session_key: str):
            backend._session_commands[session_key] = ["/compact", "/clear"]
            return MagicMock()

        backend._get_or_create_client = AsyncMock(side_effect=_fake_get_or_create_client)  # type: ignore[method-assign]

        commands = await backend.get_session_commands("s1")

        assert commands == ["/compact", "/clear"]


class TestCompactSession:
    """Tests for compact_session method."""

    @pytest.mark.asyncio
    async def test_compact_session_uses_sdk_compact_command(self):
        """compact_session should request SDK-native /compact and parse compact stats."""
        from claude_agent_sdk.types import ResultMessage, SystemMessage
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()

        mock_sessions = MagicMock()
        mock_session = MagicMock()
        mock_session.metadata = {}
        mock_sessions.get_or_create = MagicMock(return_value=mock_session)
        mock_sessions.save = MagicMock()
        backend.sessions = mock_sessions

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def _receive():
            yield SystemMessage(
                subtype="compact_boundary",
                data={"compact_metadata": {"pre_tokens": 1200, "post_tokens": 450, "trigger": "manual"}},
            )
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="sdk-session-1",
            )

        mock_client.receive_response = _receive
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        result = await backend.compact_session("test_session")

        mock_client.query.assert_awaited_once_with("/compact", session_id="test_session")
        assert result["success"] is True
        assert result["tokens_before"] == 1200
        assert result["tokens_after"] == 450
        assert mock_session.metadata["sdk_session_id"] == "sdk-session-1"
        mock_sessions.save.assert_called()

    @pytest.mark.asyncio
    async def test_compact_session_without_boundary_keeps_default_stats(self):
        """compact_session should still succeed when SDK returns no compact boundary event."""
        from claude_agent_sdk.types import ResultMessage
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend.sessions = None

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def _receive():
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="sdk-session-2",
            )

        mock_client.receive_response = _receive
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        result = await backend.compact_session("test_session")

        mock_client.query.assert_awaited_once_with("/compact", session_id="test_session")
        assert result["success"] is True
        assert result["tokens_before"] == 0
        assert result["tokens_after"] == 0

    @pytest.mark.asyncio
    async def test_compact_session_result_error_marks_failure(self):
        """compact_session should return failed status when SDK ResultMessage is_error is true."""
        from claude_agent_sdk.types import ResultMessage
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend.sessions = None

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def _receive():
            yield ResultMessage(
                subtype="error",
                duration_ms=1,
                duration_api_ms=1,
                is_error=True,
                num_turns=1,
                session_id="sdk-session-3",
                result="compact failed",
            )

        mock_client.receive_response = _receive
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        result = await backend.compact_session("test_session")

        assert result["success"] is False
        assert result["message"] == "compact failed"


class TestMessageConverterEnhancements:
    """Tests for compact/system/result conversion improvements."""

    def test_convert_compact_boundary_system_message(self):
        from claude_agent_sdk.types import SystemMessage
        from xbot.agent.backends.claude_sdk_backend import MessageConverter

        converter = MessageConverter(handoff_policy=None, capabilities=None, config=None)
        msg = SystemMessage(
            subtype="compact_boundary",
            data={"compact_metadata": {"pre_tokens": 5000, "post_tokens": 3100, "trigger": "manual"}},
        )

        response = converter.convert(msg)

        assert response is not None
        assert response.progress_texts
        assert "Context compacted" in response.progress_texts[0]
        assert "5,000" in response.progress_texts[0]
        assert "3,100" in response.progress_texts[0]

    def test_convert_result_message_preserves_result_text(self):
        from claude_agent_sdk.types import ResultMessage
        from xbot.agent.backends.claude_sdk_backend import MessageConverter

        converter = MessageConverter(handoff_policy=None, capabilities=None, config=None)
        msg = ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s1",
            result="final answer",
            usage={"input_tokens": 10, "output_tokens": 20},
        )

        response = converter.convert(msg)

        assert response is not None
        assert response.content == "final answer"
        assert response.usage is not None
        assert response.usage["input_tokens"] == 10
        assert response.usage["output_tokens"] == 20

    def test_convert_assistant_thinking_only_sets_thinking_event_type(self):
        from claude_agent_sdk.types import AssistantMessage, ThinkingBlock
        from xbot.agent.backends.claude_sdk_backend import MessageConverter

        converter = MessageConverter(handoff_policy=None, capabilities=None, config=None)
        msg = AssistantMessage(
            content=[ThinkingBlock(thinking="analyzing", signature="sig")],
            model="claude-3-5-sonnet",
        )

        response = converter.convert(msg)

        assert response is not None
        assert response.event_type == "thinking"
        assert response.event_data == {"thinking_chunks": 1}
        assert response.progress_texts == ["Thinking: analyzing"]

    def test_convert_task_notification_includes_status_and_task_metadata(self):
        from claude_agent_sdk.types import TaskNotificationMessage
        from xbot.agent.backends.claude_sdk_backend import MessageConverter

        converter = MessageConverter(handoff_policy=None, capabilities=None, config=None)
        msg = TaskNotificationMessage(
            subtype="task_notification",
            data={},
            task_id="task-1",
            status="failed",
            output_file="/tmp/out.txt",
            summary="Tool crashed",
            uuid="u1",
            session_id="s1",
        )

        response = converter.convert(msg)

        assert response is not None
        assert response.progress_texts
        text = response.progress_texts[0]
        assert "Task failed" in text
        assert "Tool crashed" in text
        assert "task-1" in text

    def test_convert_stream_event_thinking_delta_is_visible(self):
        from claude_agent_sdk.types import StreamEvent
        from xbot.agent.backends.claude_sdk_backend import MessageConverter

        converter = MessageConverter(handoff_policy=None, capabilities=None, config=None)
        msg = StreamEvent(
            uuid="u1",
            session_id="s1",
            event={
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": "analyzing"},
            },
        )

        response = converter.convert(msg)

        assert response is not None
        assert response.progress_texts == ["Thinking: analyzing"]


class TestBackendInputRequiredInteraction:
    """Tests for SDK input_required interaction behavior."""

    @pytest.mark.asyncio
    async def test_wait_for_user_input_maps_approval_required_to_approval_kind(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.protocol import AgentContext

        backend = ClaudeSDKBackend()
        backend.sdk_config = MagicMock()
        backend.sdk_config.permission = MagicMock(timeout=3.0)
        backend._shared_resources = {}
        backend._clients = {}
        backend._active_task_ids = {}

        handler = MagicMock()
        handler.request_interaction = AsyncMock(
            return_value=type("Resp", (), {"action": "allow", "content": ""})()
        )
        backend._permission_handler = handler

        msg = type(
            "TaskNotificationMessage",
            (),
            {"summary": "请确认授权", "task_id": "t1", "status": "approval_required"},
        )()
        context = AgentContext(session_key="s1", prompt="p", channel="telegram", chat_id="c1", metadata={})

        result = await backend._wait_for_user_input(context, msg)

        assert result == "allow"
        handler.request_interaction.assert_awaited_once()
        kwargs = handler.request_interaction.await_args.kwargs
        assert kwargs["kind"] == "approval"
        assert kwargs["suggestions"] == ["允许", "拒绝"]

    @pytest.mark.asyncio
    async def test_wait_for_user_input_uses_handler_without_bus(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.protocol import AgentContext

        backend = ClaudeSDKBackend()
        backend.sdk_config = MagicMock()
        backend.sdk_config.permission = MagicMock(timeout=3.0)
        backend._shared_resources = {}  # no bus
        backend._clients = {}
        backend._active_task_ids = {}

        handler = MagicMock()
        handler.request_interaction = AsyncMock(
            return_value=type("Resp", (), {"action": "reply", "content": "继续"})()
        )
        backend._permission_handler = handler

        msg = type(
            "TaskNotificationMessage",
            (),
            {"summary": "需要你输入", "task_id": "t2", "status": "input_required"},
        )()
        context = AgentContext(session_key="s2", prompt="p", channel="cli", chat_id="direct", metadata={})

        result = await backend._wait_for_user_input(context, msg)

        assert result == "继续"
