"""Tests for Claude SDK Backend."""

import asyncio
import time
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
        """Backend logger should be captured through the unified logging bridge."""
        import logging
        from xbot.logging import configure_logging

        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import logger

            configure_logging(level=logging.INFO)
            caplog.set_level(logging.INFO)
            test_msg = "test message 123"
            logger.info(f"Test log: {test_msg}")
            assert f"Test log: {test_msg}" in caplog.text


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

    @pytest.mark.asyncio
    async def test_shutdown_clears_session_store_clients(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.state.store import SessionStore

        backend = ClaudeSDKBackend()
        backend._session_store = SessionStore()
        backend._use_session_store = True
        entry1 = backend._session_store.get_or_create("s1")
        entry2 = backend._session_store.get_or_create("s2")
        client1 = MagicMock()
        client1.disconnect = AsyncMock()
        client2 = MagicMock()
        client2.disconnect = AsyncMock()
        entry1.client = client1
        entry2.client = client2

        await backend.shutdown()

        client1.disconnect.assert_awaited_once()
        client2.disconnect.assert_awaited_once()
        assert backend._session_store.get("s1").client is None
        assert backend._session_store.get("s2").client is None

    @pytest.mark.asyncio
    async def test_shutdown_cancels_client_scavenger(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend._client_scavenger_task = asyncio.create_task(asyncio.sleep(10))

        await backend.shutdown()

        assert backend._client_scavenger_task is None


class TestClaudeSDKBackendLifecycle:
    @pytest.mark.asyncio
    async def test_scavenger_cleans_idle_client_without_new_requests(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.state.store import SessionStore

        backend = ClaudeSDKBackend()
        backend._session_store = SessionStore()
        backend._use_session_store = True
        entry = backend._session_store.get_or_create("cli:idle")
        client = MagicMock()
        client.disconnect = AsyncMock()
        entry.client = client
        entry.last_used = time.time() - 100
        backend.sdk_config = MagicMock(
            client_lifecycle_enabled=True,
            client_scavenger_enabled=True,
            client_idle_ttl_seconds=1,
            client_cleanup_interval_seconds=3600,
            client_disconnect_timeout_seconds=1,
            client_disconnect_max_retries=1,
            client_force_kill_enabled=False,
            strict_process_tracking_required=False,
            ephemeral_immediate_release_enabled=True,
        )

        await backend._run_client_scavenger_iteration()

        assert entry.client is None
        client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_release_client_marks_leaked_on_disconnect_timeout(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.state.store import SessionStore

        backend = ClaudeSDKBackend()
        backend._session_store = SessionStore()
        backend._use_session_store = True
        entry = backend._session_store.get_or_create("cli:leak")

        async def _hang_disconnect():
            await asyncio.sleep(10)

        client = MagicMock()
        client.disconnect = _hang_disconnect
        entry.client = client
        backend.sdk_config = MagicMock(
            client_lifecycle_enabled=True,
            client_scavenger_enabled=True,
            client_idle_ttl_seconds=3600,
            client_cleanup_interval_seconds=3600,
            client_disconnect_timeout_seconds=0.01,
            client_disconnect_max_retries=0,
            client_force_kill_enabled=False,
            strict_process_tracking_required=False,
            ephemeral_immediate_release_enabled=True,
        )

        released = await backend.release_client("cli:leak", reason="test-timeout")

        assert released is False
        diagnostics = backend.get_client_lifecycle_diagnostics()
        assert diagnostics["counts"]["leaked"] == 1
        assert diagnostics["clients"]["cli:leak"]["disconnect_state"] == "leaked"

    @pytest.mark.asyncio
    async def test_release_client_is_idempotent(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.state.store import SessionStore

        backend = ClaudeSDKBackend()
        backend._session_store = SessionStore()
        backend._use_session_store = True
        entry = backend._session_store.get_or_create("cli:idempotent")
        client = MagicMock()
        client.disconnect = AsyncMock()
        entry.client = client
        backend.sdk_config = MagicMock(
            client_lifecycle_enabled=True,
            client_scavenger_enabled=True,
            client_idle_ttl_seconds=3600,
            client_cleanup_interval_seconds=3600,
            client_disconnect_timeout_seconds=1,
            client_disconnect_max_retries=0,
            client_force_kill_enabled=False,
            strict_process_tracking_required=False,
            ephemeral_immediate_release_enabled=True,
        )

        first = await backend.release_client("cli:idempotent", reason="first")
        second = await backend.release_client("cli:idempotent", reason="second")

        assert first is True
        assert second is True
        client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_or_create_client_returns_new_client_even_if_old_cleanup_mutates_state(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.state.store import SessionStore

        backend = ClaudeSDKBackend()
        backend._session_store = SessionStore()
        backend._use_session_store = True
        backend._build_options = MagicMock(return_value=MagicMock())
        backend._skill_manager = MagicMock(version="skills-v1")
        backend._options_builder = MagicMock()
        backend._options_builder._get_model_name.side_effect = ["model-b"]

        old_client = MagicMock(name="old-client")
        entry = backend._session_store.get_or_create("s1")
        entry.client = old_client
        entry.model = "model-a"
        entry.skills_version = "skills-v1"

        new_client = MagicMock(name="new-client")
        new_client.connect = AsyncMock()

        backend._refresh_session_commands = AsyncMock()
        backend._register_managed_client = AsyncMock()

        async def _cleanup(client_key: str, _client: object, *, reason: str) -> None:
            backend._remove_client_state(client_key)

        backend._finalize_detached_client_cleanup = AsyncMock(side_effect=_cleanup)

        with patch(
            "xbot.agent.backends.claude_sdk_backend._ClaudeSDKClient",
            return_value=new_client,
        ):
            client = await backend._get_or_create_client("s1")

        assert client is new_client

    @pytest.mark.asyncio
    async def test_lru_eviction_updates_lifecycle_state_for_evicted_session(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.state.store import SessionStore

        backend = ClaudeSDKBackend()
        backend._session_store = SessionStore()
        backend._use_session_store = True
        backend.sdk_config = MagicMock(
            max_clients=1,
            client_lifecycle_enabled=True,
            client_scavenger_enabled=False,
            client_idle_ttl_seconds=3600,
            client_cleanup_interval_seconds=3600,
            client_disconnect_timeout_seconds=1,
            client_disconnect_max_retries=0,
            client_force_kill_enabled=False,
            strict_process_tracking_required=False,
            ephemeral_immediate_release_enabled=True,
        )
        backend._build_options = MagicMock(return_value=MagicMock())
        backend._refresh_session_commands = AsyncMock()

        created: list[MagicMock] = []

        def _make_client(*_args, **_kwargs):
            client = MagicMock()
            client.connect = AsyncMock()
            client.disconnect = AsyncMock()
            created.append(client)
            return client

        with patch(
            "xbot.agent.backends.claude_sdk_backend._ClaudeSDKClient",
            side_effect=_make_client,
        ):
            first = await backend._get_or_create_client("cli:first")
            second = await backend._get_or_create_client("cli:second")

        assert first is created[0]
        assert second is created[1]
        diagnostics = backend.get_client_lifecycle_diagnostics()
        assert diagnostics["clients"]["cli:first"]["disconnect_state"] == "disconnected"
        assert diagnostics["clients"]["cli:second"]["disconnect_state"] == "connected"

    @pytest.mark.asyncio
    async def test_client_recreation_keeps_new_lifecycle_state_connected(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.state.store import SessionStore

        backend = ClaudeSDKBackend()
        backend._session_store = SessionStore()
        backend._use_session_store = True
        backend.sdk_config = MagicMock(
            max_clients=10,
            client_lifecycle_enabled=True,
            client_scavenger_enabled=False,
            client_idle_ttl_seconds=3600,
            client_cleanup_interval_seconds=3600,
            client_disconnect_timeout_seconds=1,
            client_disconnect_max_retries=0,
            client_force_kill_enabled=False,
            strict_process_tracking_required=False,
            ephemeral_immediate_release_enabled=True,
        )
        backend._refresh_session_commands = AsyncMock()
        backend._build_options = MagicMock(return_value=MagicMock())

        created: list[MagicMock] = []

        def _make_client(*_args, **_kwargs):
            client = MagicMock()
            client.connect = AsyncMock()
            client.disconnect = AsyncMock()
            created.append(client)
            return client

        current_model = {"value": "model-a"}
        backend._options_builder = MagicMock()
        backend._options_builder._get_model_name.side_effect = lambda: current_model["value"]

        with patch(
            "xbot.agent.backends.claude_sdk_backend._ClaudeSDKClient",
            side_effect=_make_client,
        ):
            first = await backend._get_or_create_client("cli:recreate")
            current_model["value"] = "model-b"
            second = await backend._get_or_create_client("cli:recreate")

        assert first is created[0]
        assert second is created[1]
        diagnostics = backend.get_client_lifecycle_diagnostics()
        assert diagnostics["clients"]["cli:recreate"]["disconnect_state"] == "connected"

    @pytest.mark.asyncio
    async def test_ephemeral_session_releases_client_after_process(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.protocol import AgentContext, AgentResponse

        backend = ClaudeSDKBackend()
        backend.sdk_config = MagicMock(
            client_lifecycle_enabled=True,
            client_scavenger_enabled=False,
            client_idle_ttl_seconds=3600,
            client_cleanup_interval_seconds=3600,
            client_disconnect_timeout_seconds=1,
            client_disconnect_max_retries=1,
            client_force_kill_enabled=False,
            strict_process_tracking_required=False,
            ephemeral_immediate_release_enabled=True,
            compact_notify=False,
            include_partial_messages=False,
            max_turns=4,
            memory_consolidation_mode="off",
        )
        backend._tool_adapter = MagicMock()
        backend._tool_adapter._tools = {}
        backend._message_converter = MagicMock()
        backend._message_converter.convert.return_value = AgentResponse(content="done")

        client = MagicMock()
        client.query = AsyncMock()

        async def _receive():
            yield MagicMock()

        client.receive_response = _receive
        client.disconnect = AsyncMock()
        backend._get_or_create_client = AsyncMock(return_value=client)  # type: ignore[method-assign]
        backend.sessions = None
        backend._shared_resources = {}

        context = AgentContext(
            session_key="cron:job-release",
            prompt="hello",
            channel="cli",
            chat_id="direct",
            metadata={},
        )

        _ = [resp async for resp in backend.process(context)]

        client.disconnect.assert_awaited_once()
        diagnostics = backend.get_client_lifecycle_diagnostics()
        assert diagnostics["clients"]["cron:job-release"]["disconnect_state"] == "disconnected"


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

    @pytest.mark.asyncio
    async def test_build_hooks_compact_notification_prefers_backend_context_helper(self):
        """Compact notification should resolve context via backend helper before legacy dict."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder

            outbound_calls = []

            class _Bus:
                async def publish_outbound(self, message):
                    outbound_calls.append(message)

            class _Backend:
                def _get_context_by_session_key(self, session_key: str):
                    if session_key == "session:1":
                        return ("feishu", "chat-1")
                    return None

            class _Runtime:
                backend = _Backend()

            sdk_config = MagicMock()
            sdk_config.hooks = {}
            sdk_config.compact_notify = True

            builder = OptionsBuilder(
                shared_resources={
                    "bus": _Bus(),
                    "runtime": _Runtime(),
                    "_session_contexts": {},
                },
                sdk_config=sdk_config,
                skill_converter=None,
                tool_adapter=None,
                sessions=None,
                context_builder=None,
                handoff_policy=None,
                capability_policy=None,
            )

            hooks = builder._build_hooks()
            compact_handler = hooks["PreCompact"][0]["hooks"][0]

            compact_handler.message_callback("session:1", "compacted")
            await asyncio.sleep(0)

            assert len(outbound_calls) == 1
            assert outbound_calls[0].channel == "feishu"
            assert outbound_calls[0].chat_id == "chat-1"
            assert outbound_calls[0].content == "compacted"

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
                    self.skills = None  # Skills loader (None for test)

                def build_messages(self, *args, **kwargs):
                    return []

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
            from xbot.agent.capabilities.handoff import HandoffDecision

            backend = ClaudeSDKBackend()
            backend._delegation_traces = []
            backend._delegation_traces_lock = asyncio.Lock()

            decision = HandoffDecision(
                mode="native_handoff",
                reason="test",
                candidate_agents=("agent1",),
            )

            await backend._record_delegation_trace("test_session", decision)

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
            from xbot.agent.capabilities.handoff import HandoffDecision

            backend = ClaudeSDKBackend()
            backend._delegation_traces = []
            backend._delegation_traces_lock = asyncio.Lock()

            decision = HandoffDecision(mode="main", reason="test", candidate_agents=())

            # Add 150 traces
            for i in range(150):
                await backend._record_delegation_trace(f"session_{i}", decision)

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
        """Test interrupt_session returns dict with interrupted=False when no client exists."""
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
            assert result["interrupted"] is False
            assert result["usage"] is None

    @pytest.mark.asyncio
    async def test_interrupt_session_success(self):
        """Test interrupt_session calls client.interrupt() and returns dict with interrupted=True."""
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
            mock_client.disconnect = AsyncMock()
            # Mock receive_messages to return empty async iterator
            async def mock_receive_messages():
                return
                yield  # Makes it an async generator that yields nothing
            mock_client.receive_messages = mock_receive_messages
            backend._clients["test_session"] = mock_client

            result = await backend.interrupt_session("test_session")
            assert result["interrupted"] is True
            mock_client.interrupt.assert_awaited_once()
            # Client should be removed after interrupt
            assert "test_session" not in backend._clients
            mock_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_interrupt_session_clears_session_store_state(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.state.store import SessionStore

        backend = ClaudeSDKBackend()
        backend._session_store = SessionStore()
        backend._use_session_store = True
        entry = backend._session_store.get_or_create("test_session")
        mock_client = MagicMock()
        mock_client.interrupt = AsyncMock()
        mock_client.disconnect = AsyncMock()

        async def mock_receive_messages():
            return
            yield

        mock_client.receive_messages = mock_receive_messages
        entry.client = mock_client
        backend._session_store.set_sdk_session_id("test_session", "sdk_1")
        entry.tasks = [MagicMock()]
        entry.task_id = "task_1"
        entry.request_id = "req_1"

        result = await backend.interrupt_session("test_session")

        assert result["interrupted"] is True
        assert entry.client is None
        assert entry.tasks == []
        assert entry.task_id is None
        assert entry.request_id is None
        assert backend._session_store.get_by_sdk_id("sdk_1") is None

    @pytest.mark.asyncio
    async def test_interrupt_session_exception(self):
        """Test interrupt_session returns dict with interrupted=False on exception."""
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
            assert result["interrupted"] is False
            assert result["usage"] is None


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

    @pytest.mark.asyncio
    async def test_process_stops_stream_after_result_message(self):
        from claude_agent_sdk.types import ResultMessage
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.protocol import AgentContext

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
                session_id="s1",
                result="done",
            )
            # Must never be reached once ResultMessage is handled.
            raise AssertionError("receive_response should stop after ResultMessage")

        mock_client.receive_response = _receive
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        context = AgentContext(session_key="test_session", prompt="hello")
        responses = [msg async for msg in backend.process(context)]

        # Backend should stop reading stream after ResultMessage and not hit sentinel error.
        assert responses == []


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

    @pytest.mark.asyncio
    async def test_get_session_commands_refreshes_when_cache_is_empty_list(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend._session_commands = {"s1": []}

        mock_client = MagicMock()
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        async def _fake_refresh(session_key: str, client):
            assert session_key == "s1"
            assert client is mock_client
            backend._session_commands[session_key] = ["/debug", "/compact"]

        backend._refresh_session_commands = AsyncMock(side_effect=_fake_refresh)  # type: ignore[method-assign]

        commands = await backend.get_session_commands("s1")

        assert commands == ["/debug", "/compact"]
        backend._get_or_create_client.assert_awaited_once_with("s1")
        backend._refresh_session_commands.assert_awaited_once_with("s1", mock_client)


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

    @pytest.mark.asyncio
    async def test_compact_session_collects_boundary_after_result_message(self):
        from claude_agent_sdk.types import ResultMessage, SystemMessage
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
                session_id="s2",
            )
            # Boundary may arrive after result; stats should still capture it.
            yield SystemMessage(
                subtype="compact_boundary",
                data={"compact_metadata": {"pre_tokens": 2000, "post_tokens": 800}},
            )

        mock_client.receive_response = _receive
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        result = await backend.compact_session("test_session")

        assert result["success"] is True
        assert result["tokens_before"] == 2000
        assert result["tokens_after"] == 800


class TestSessionStateReset:
    @pytest.mark.asyncio
    async def test_reset_session_client_state_stops_task_and_disconnects(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        mock_client = MagicMock()
        mock_client.stop_task = AsyncMock(return_value=None)
        mock_client.disconnect = AsyncMock(return_value=None)

        backend._clients = {"s1": mock_client}
        backend._active_task_ids = {"s1": "task-1"}

        await backend._reset_session_client_state("s1")

        mock_client.stop_task.assert_awaited_once_with("task-1")
        mock_client.disconnect.assert_awaited_once()
        assert "s1" not in backend._clients
        assert "s1" not in backend._active_task_ids


class TestInputRequiredRecoveryFlow:
    @pytest.mark.asyncio
    async def test_new_turn_retries_after_non_terminal_notification_without_old_input_flow(self):
        from claude_agent_sdk.types import ResultMessage, TaskNotificationMessage, TaskStartedMessage
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.protocol import AgentContext

        backend = ClaudeSDKBackend()
        backend.sessions = None
        backend._message_converter = None
        backend._permission_handler = MagicMock()
        backend._permission_handler.clear_session_context = MagicMock()

        client1 = MagicMock()
        client1.query = AsyncMock(return_value=None)
        client1.stop_task = AsyncMock(return_value=None)
        client1.disconnect = AsyncMock(return_value=None)

        async def _receive_1():
            yield TaskStartedMessage(
                subtype="task_started",
                data={},
                task_id="task-stuck",
                description="long task",
                uuid="u1",
                session_id="s1",
            )
            yield TaskNotificationMessage(
                subtype="task_notification",
                data={},
                task_id="task-stuck",
                status="failed",
                output_file="",
                summary="stale task notification",
                uuid="u2",
                session_id="s1",
            )

        client1.receive_response = _receive_1
        backend._clients["telegram:456"] = client1

        client2 = MagicMock()
        client2.query = AsyncMock(return_value=None)
        client2.stop_task = AsyncMock(return_value=None)
        client2.disconnect = AsyncMock(return_value=None)

        async def _receive_2():
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="s2",
                result="next task done",
            )

        client2.receive_response = _receive_2

        backend._get_or_create_client = AsyncMock(side_effect=[client1, client2, client2])  # type: ignore[method-assign]

        context = AgentContext(session_key="telegram:456", prompt="first task")
        _ = [msg async for msg in backend.process(context)]

        client1.stop_task.assert_not_awaited()

        context2 = AgentContext(session_key="telegram:456", prompt="second task")
        _ = [msg async for msg in backend.process(context2)]

        assert backend._get_or_create_client.await_count == 3
        assert client2.query.await_count == 2


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
    """Tests for the removed TaskNotification input-required flow."""

    @pytest.mark.asyncio
    async def test_wait_for_user_input_is_explicitly_unsupported(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.protocol import AgentContext

        backend = ClaudeSDKBackend()
        context = AgentContext(session_key="s1", prompt="p", channel="telegram", chat_id="c1", metadata={})
        msg = type(
            "TaskNotificationMessage",
            (),
            {"summary": "需要确认", "task_id": "task-1", "status": "approval_required"},
        )()

        with pytest.raises(NotImplementedError):
            await backend._wait_for_user_input(context, msg)


class TestMessageConverterRateLimit:
    def test_convert_rate_limit_event_is_visible(self):
        from claude_agent_sdk import RateLimitEvent, RateLimitInfo
        from xbot.agent.backends.claude_sdk_backend import MessageConverter

        converter = MessageConverter(handoff_policy=None, capabilities=None, config=None)
        msg = RateLimitEvent(
            rate_limit_info=RateLimitInfo(
                status="rejected",
                resets_at=int(datetime(2026, 3, 30, 12, 0, 0).timestamp()),
                rate_limit_type="five_hour",
                utilization=0.95,
            ),
            uuid="u1",
            session_id="s1",
        )

        response = converter.convert(msg)

        assert response is not None
        assert response.event_type == "rate_limit"
        assert response.progress_texts
        assert "rate limited" in response.progress_texts[0].lower()


class TestClaudeSDKBackendConsolidationCall:
    """Tests for direct consolidation API call behavior."""

    @pytest.mark.asyncio
    async def test_call_for_consolidation_resolves_provider_and_parses_tool_result(self):
        import httpx

        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.config.schema import Config

        backend = ClaudeSDKBackend()
        config = Config()
        config.agents.defaults.provider = "anthropic"
        config.agents.defaults.model = "anthropic/claude-sonnet-4-5"
        config.providers.anthropic.api_key = "test-key"
        config.providers.anthropic.api_base = "https://api.example.com/v1"
        config.providers.anthropic.extra_headers = {"x-extra-header": "abc"}
        backend._shared_resources = {"config": config}

        captured: dict[str, object] = {}

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                captured["timeout"] = kwargs.get("timeout")

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, headers=None, json=None):
                captured["url"] = url
                captured["headers"] = headers
                captured["json"] = json
                req = httpx.Request("POST", url)
                return httpx.Response(
                    200,
                    request=req,
                    json={
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "tool_1",
                                "name": "save_memory",
                                "input": {"history_entry": "h", "memory_update": "m"},
                            }
                        ],
                        "stop_reason": "tool_use",
                        "usage": {"input_tokens": 11, "output_tokens": 7},
                    },
                )

        with patch("httpx.AsyncClient", _FakeClient):
            response = await backend.call_for_consolidation(
                messages=[
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hello"},
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "save_memory",
                            "description": "desc",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
                tool_choice={"type": "function", "function": {"name": "save_memory"}},
            )

        assert captured["url"] == "https://api.example.com/v1/messages"
        assert captured["headers"]["x-api-key"] == "test-key"
        assert captured["headers"]["x-extra-header"] == "abc"
        assert captured["json"]["model"] == "claude-sonnet-4-5"
        assert response.finish_reason == "tool_calls"
        assert response.tool_calls
        assert response.tool_calls[0].name == "save_memory"
        assert response.usage["input_tokens"] == 11
        assert response.usage["output_tokens"] == 7

    @pytest.mark.asyncio
    async def test_call_for_consolidation_retries_retryable_http_status(self):
        import httpx

        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.config.schema import Config

        backend = ClaudeSDKBackend()
        config = Config()
        config.agents.defaults.provider = "anthropic"
        config.agents.defaults.model = "claude-sonnet-4-5"
        config.providers.anthropic.api_key = "test-key"
        backend._shared_resources = {"config": config}

        state = {"calls": 0}

        class _FlakyClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, headers=None, json=None):
                state["calls"] += 1
                req = httpx.Request("POST", url)
                if state["calls"] == 1:
                    resp = httpx.Response(503, request=req, json={"error": "busy"})
                    raise httpx.HTTPStatusError("service unavailable", request=req, response=resp)
                return httpx.Response(
                    200,
                    request=req,
                    json={
                        "content": [{"type": "text", "text": "ok"}],
                        "stop_reason": "end_turn",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                )

        sleep_mock = AsyncMock()
        with patch("httpx.AsyncClient", return_value=_FlakyClient()):
            with patch("xbot.agent.backends.claude_sdk_backend.asyncio.sleep", sleep_mock):
                response = await backend.call_for_consolidation(
                    messages=[{"role": "user", "content": "hello"}],
                )

        assert state["calls"] == 2
        sleep_mock.assert_awaited_once()
        assert response.finish_reason == "stop"
        assert response.content == "ok"
