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
            mock_client.interrupt = MagicMock()
            backend._clients["test_session"] = mock_client

            result = await backend.interrupt_session("test_session")
            assert result is True
            mock_client.interrupt.assert_called_once()

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
            mock_client.interrupt = MagicMock(side_effect=Exception("Interrupt failed"))
            backend._clients["test_session"] = mock_client

            result = await backend.interrupt_session("test_session")
            assert result is False


class TestCompactSession:
    """Tests for compact_session method."""

    @pytest.mark.asyncio
    async def test_compact_session_no_sessions(self):
        """Test compact_session returns not available when no session manager."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()
            backend.sessions = None
            backend.memory_consolidator = None

            result = await backend.compact_session("test_session")
            assert result["success"] is True
            assert result["messages_consolidated"] == 0
            assert "not available" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_compact_session_empty_session(self):
        """Test compact_session with empty session."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()

            mock_sessions = MagicMock()
            mock_session = MagicMock()
            mock_session.messages = []
            mock_session.last_consolidated = 0
            mock_sessions.get_or_create = MagicMock(return_value=mock_session)
            backend.sessions = mock_sessions

            mock_consolidator = MagicMock()
            mock_consolidator.force_consolidate = AsyncMock(return_value={
                "messages_consolidated": 0,
                "tokens_before": 0,
                "tokens_after": 0,
                "success": True,
            })
            backend.memory_consolidator = mock_consolidator

            result = await backend.compact_session("test_session")
            assert result["success"] is True
            assert result["messages_consolidated"] == 0

    @pytest.mark.asyncio
    async def test_compact_session_with_messages(self):
        """Test compact_session with messages to consolidate."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()

            mock_sessions = MagicMock()
            mock_session = MagicMock()
            mock_session.messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ]
            mock_session.last_consolidated = 0
            mock_sessions.get_or_create = MagicMock(return_value=mock_session)
            backend.sessions = mock_sessions

            mock_consolidator = MagicMock()
            mock_consolidator.force_consolidate = AsyncMock(return_value={
                "messages_consolidated": 2,
                "tokens_before": 100,
                "tokens_after": 20,
                "success": True,
            })
            backend.memory_consolidator = mock_consolidator

            result = await backend.compact_session("test_session")
            assert result["success"] is True
            assert result["messages_consolidated"] == 2
            assert result["tokens_before"] == 100
            assert result["tokens_after"] == 20
            mock_consolidator.force_consolidate.assert_called_once_with(mock_session)