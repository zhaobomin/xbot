"""Tests for Claude SDK Backend."""

import asyncio
from typing import Any
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

    @pytest.mark.asyncio
    async def test_get_or_create_client_different_sessions_do_not_block_on_slow_connect(self):
        """Slow connect for one session should not serialize other sessions."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()

            def build_options(session_key, **kwargs):
                return MagicMock(session_key=session_key, model="test-model")

            backend._build_options = build_options
            backend._refresh_session_commands = AsyncMock()

            async def mock_connect(self):
                if self.session_key == "slow":
                    await asyncio.sleep(0.1)

            def create_mock_client(*args, **kwargs):
                options = kwargs["options"]
                mock = MagicMock()
                mock.session_key = options.session_key
                mock.connect = lambda: mock_connect(mock)
                return mock

            with patch(
                "xbot.agent.backends.claude_sdk_backend._ClaudeSDKClient",
                side_effect=create_mock_client,
            ):
                slow_task = asyncio.create_task(backend._get_or_create_client("slow"))
                await asyncio.sleep(0.01)
                fast_task = asyncio.create_task(backend._get_or_create_client("fast"))

                fast_client = await asyncio.wait_for(fast_task, timeout=0.05)
                assert fast_client.session_key == "fast"
                await slow_task


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


class TestClaudeSDKBackendMessageBoundary:
    """Tests for stale-message boundary detection."""

    @staticmethod
    def _patch_boundary_types(monkeypatch):
        from xbot.agent.backends import claude_sdk_backend as backend_module

        class FakeSystemMessage:
            def __init__(self, subtype: str, data=None):
                self.subtype = subtype
                self.data = data or {}

        class FakeAssistantMessage:
            def __init__(self, label: str):
                self.label = label

        class FakeUserMessage:
            def __init__(self, parent_tool_use_id=None):
                self.parent_tool_use_id = parent_tool_use_id

        class FakeResultMessage:
            pass

        monkeypatch.setattr(backend_module, "SystemMessage", FakeSystemMessage)
        monkeypatch.setattr(backend_module, "AssistantMessage", FakeAssistantMessage)
        monkeypatch.setattr(backend_module, "UserMessage", FakeUserMessage)
        monkeypatch.setattr(backend_module, "ResultMessage", FakeResultMessage)
        return (
            backend_module,
            FakeSystemMessage,
            FakeAssistantMessage,
            FakeUserMessage,
            FakeResultMessage,
        )

    @staticmethod
    async def _collect_boundary_messages(backend, session_key: str, messages: list[object]) -> list[object]:
        class FakeClient:
            def __init__(self, queued_messages: list[object]):
                self._queued_messages = queued_messages

            async def receive_messages(self):
                for message in self._queued_messages:
                    yield message

        return [
            message
            async for message in backend._receive_with_boundary(FakeClient(messages), session_key)
        ]

    @pytest.mark.asyncio
    async def test_receive_with_boundary_discards_stale_before_init_and_yields_rest(
        self, monkeypatch
    ):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        (
            _backend_module,
            FakeSystemMessage,
            FakeAssistantMessage,
            FakeUserMessage,
            FakeResultMessage,
        ) = self._patch_boundary_types(monkeypatch)
        backend = ClaudeSDKBackend()

        stale_assistant = FakeAssistantMessage("stale")
        init = FakeSystemMessage("init", {"session_id": "sid-1"})
        fresh_assistant = FakeAssistantMessage("fresh")
        result = FakeResultMessage()

        seen = await self._collect_boundary_messages(
            backend,
            "feishu:test-user",
            [stale_assistant, init, fresh_assistant, result],
        )

        assert seen == [init, fresh_assistant, result]

    @pytest.mark.asyncio
    async def test_receive_with_boundary_init_yields_content_and_filters_user_message(
        self, monkeypatch
    ):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        (
            _backend_module,
            FakeSystemMessage,
            FakeAssistantMessage,
            FakeUserMessage,
            FakeResultMessage,
        ) = self._patch_boundary_types(monkeypatch)
        backend = ClaudeSDKBackend()

        init = FakeSystemMessage("init", {"session_id": "sid-1"})
        user_msg = FakeUserMessage(parent_tool_use_id=None)
        fresh_assistant = FakeAssistantMessage("fresh")
        result = FakeResultMessage()

        seen = await self._collect_boundary_messages(
            backend,
            "feishu:test-user",
            [init, user_msg, fresh_assistant, result],
        )

        # UserMessage is a protocol echo and must be filtered out
        assert seen == [init, fresh_assistant, result]

    @pytest.mark.asyncio
    async def test_receive_with_boundary_discards_all_stale_before_init(
        self, monkeypatch
    ):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        (
            _backend_module,
            FakeSystemMessage,
            FakeAssistantMessage,
            FakeUserMessage,
            FakeResultMessage,
        ) = self._patch_boundary_types(monkeypatch)
        backend = ClaudeSDKBackend()

        stale_one = FakeAssistantMessage("stale-1")
        stale_two = FakeAssistantMessage("stale-2")
        stale_user = FakeUserMessage(parent_tool_use_id=None)
        init = FakeSystemMessage("init", {"session_id": "sid-1"})
        fresh_assistant = FakeAssistantMessage("fresh")
        result = FakeResultMessage()

        seen = await self._collect_boundary_messages(
            backend,
            "feishu:test-user",
            [stale_one, stale_two, stale_user, init, fresh_assistant, result],
        )

        assert seen == [init, fresh_assistant, result]

    @pytest.mark.asyncio
    async def test_receive_with_boundary_filters_replay_user_after_assistant(
        self, monkeypatch
    ):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        (
            _backend_module,
            FakeSystemMessage,
            FakeAssistantMessage,
            FakeUserMessage,
            FakeResultMessage,
        ) = self._patch_boundary_types(monkeypatch)
        backend = ClaudeSDKBackend()

        init = FakeSystemMessage("init", {"session_id": "sid-1"})
        assistant_one = FakeAssistantMessage("assistant-1")
        replay_user = FakeUserMessage(parent_tool_use_id=None)
        assistant_two = FakeAssistantMessage("assistant-2")
        result = FakeResultMessage()

        seen = await self._collect_boundary_messages(
            backend,
            "feishu:test-user",
            [init, assistant_one, replay_user, assistant_two, result],
        )

        assert seen == [init, assistant_one, assistant_two, result]

    @pytest.mark.asyncio
    async def test_receive_with_boundary_discards_result_before_init_as_stale(
        self, monkeypatch
    ):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        (
            _backend_module,
            FakeSystemMessage,
            FakeAssistantMessage,
            FakeUserMessage,
            FakeResultMessage,
        ) = self._patch_boundary_types(monkeypatch)
        backend = ClaudeSDKBackend()

        stale_result = FakeResultMessage()
        init = FakeSystemMessage("init", {"session_id": "sid-1"})
        fresh_assistant = FakeAssistantMessage("fresh")
        fresh_result = FakeResultMessage()

        seen = await self._collect_boundary_messages(
            backend,
            "feishu:test-user",
            [stale_result, init, fresh_assistant, fresh_result],
        )

        assert seen == [init, fresh_assistant, fresh_result]

    @pytest.mark.asyncio
    async def test_receive_with_boundary_discards_stale_user_before_init_and_keeps_active_stream(
        self, monkeypatch
    ):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        (
            _backend_module,
            FakeSystemMessage,
            FakeAssistantMessage,
            FakeUserMessage,
            FakeResultMessage,
        ) = self._patch_boundary_types(monkeypatch)
        backend = ClaudeSDKBackend()

        stale_user = FakeUserMessage(parent_tool_use_id=None)
        stale_result = FakeResultMessage()
        init = FakeSystemMessage("init", {"session_id": "sid-1"})
        fresh_assistant = FakeAssistantMessage("fresh")
        replay_user = FakeUserMessage(parent_tool_use_id=None)
        fresh_result = FakeResultMessage()

        seen = await self._collect_boundary_messages(
            backend,
            "feishu:test-user",
            [stale_user, stale_result, init, fresh_assistant, replay_user, fresh_result],
        )

        assert seen == [init, fresh_assistant, fresh_result]


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

    @pytest.mark.asyncio
    async def test_force_kill_process_does_not_block_event_loop_for_sync_wait(self):
        """Synchronous process.wait should not block unrelated coroutines."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend._client_lifecycle = MagicMock()
        backend._client_lifecycle.get = AsyncMock(
            return_value=MagicMock(
                process_handle=MagicMock(
                    terminate=MagicMock(),
                    wait=MagicMock(side_effect=lambda: time.sleep(0.2)),
                )
            )
        )
        backend._client_lifecycle.mark_killed = AsyncMock()

        kill_task = asyncio.create_task(backend._force_kill_process("session-1"))
        start = time.perf_counter()
        await asyncio.sleep(0.02)
        elapsed = time.perf_counter() - start
        result = await kill_task

        assert elapsed < 0.1
        assert result is True


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
    async def test_release_client_preserves_sdk_session_for_non_ephemeral_session(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.state.store import SessionStore

        backend = ClaudeSDKBackend()
        backend._session_store = SessionStore()
        backend._use_session_store = True
        entry = backend._session_store.get_or_create("cli:keep-context")
        entry.client = MagicMock(disconnect=AsyncMock())
        backend._session_store.set_sdk_session_id("cli:keep-context", "sdk-keep")

        backend.sessions = MagicMock()
        session = MagicMock()
        session.metadata = {"sdk_session_id": "sdk-keep"}
        backend.sessions.get = MagicMock(return_value=session)

        released = await backend.release_client("cli:keep-context", reason="test-preserve")

        assert released is True
        assert backend._session_store.get("cli:keep-context").sdk_session_id == "sdk-keep"
        diagnostics = backend.get_client_lifecycle_diagnostics()
        assert diagnostics["clients"]["cli:keep-context"]["sdk_session_id"] == "sdk-keep"

    @pytest.mark.asyncio
    async def test_release_client_clears_sdk_session_for_ephemeral_session(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.state.store import SessionStore

        backend = ClaudeSDKBackend()
        backend._session_store = SessionStore()
        backend._use_session_store = True
        entry = backend._session_store.get_or_create("cron:job-1")
        entry.client = MagicMock(disconnect=AsyncMock())
        backend._session_store.set_sdk_session_id("cron:job-1", "sdk-ephemeral")

        released = await backend.release_client("cron:job-1", reason="ephemeral_turn_end")

        assert released is True
        assert backend._session_store.get("cron:job-1").sdk_session_id is None

    @pytest.mark.asyncio
    async def test_release_client_force_kills_and_preserves_sdk_session_on_disconnect_failure(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.state.store import SessionStore

        backend = ClaudeSDKBackend()
        backend._session_store = SessionStore()
        backend._use_session_store = True
        entry = backend._session_store.get_or_create("cli:killed")

        async def _hang_disconnect():
            await asyncio.sleep(10)

        process = MagicMock()
        process.pid = 12345
        process.wait = AsyncMock(return_value=None)

        client = MagicMock()
        client.disconnect = _hang_disconnect
        client.process = process
        entry.client = client
        backend._session_store.set_sdk_session_id("cli:killed", "sdk-killed")
        backend.sdk_config = MagicMock(
            client_lifecycle_enabled=True,
            client_scavenger_enabled=True,
            client_idle_ttl_seconds=3600,
            client_cleanup_interval_seconds=3600,
            client_disconnect_timeout_seconds=0.01,
            client_disconnect_max_retries=0,
            client_force_kill_enabled=True,
            strict_process_tracking_required=False,
            ephemeral_immediate_release_enabled=True,
        )

        released = await backend.release_client("cli:killed", reason="test-timeout")

        assert released is True
        assert backend._session_store.get("cli:killed").sdk_session_id == "sdk-killed"
        diagnostics = backend.get_client_lifecycle_diagnostics()
        assert diagnostics["clients"]["cli:killed"]["disconnect_state"] == "killed"

    @pytest.mark.asyncio
    async def test_release_client_records_fallback_diagnostics_when_lifecycle_disabled(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.state.store import SessionStore

        backend = ClaudeSDKBackend()
        backend._session_store = SessionStore()
        backend._use_session_store = True
        entry = backend._session_store.get_or_create("cli:fallback-leak")

        async def _hang_disconnect():
            await asyncio.sleep(10)

        client = MagicMock()
        client.disconnect = _hang_disconnect
        entry.client = client
        backend._session_store.set_sdk_session_id("cli:fallback-leak", "sdk-fallback")
        backend.sdk_config = MagicMock(
            client_lifecycle_enabled=False,
            client_scavenger_enabled=False,
            client_idle_ttl_seconds=3600,
            client_cleanup_interval_seconds=3600,
            client_disconnect_timeout_seconds=0.01,
            client_disconnect_max_retries=0,
            client_force_kill_enabled=False,
            strict_process_tracking_required=False,
            ephemeral_immediate_release_enabled=True,
        )

        released = await backend.release_client("cli:fallback-leak", reason="test-timeout")

        assert released is False
        assert backend._session_store.get("cli:fallback-leak").sdk_session_id == "sdk-fallback"
        diagnostics = backend.get_client_lifecycle_diagnostics()
        assert diagnostics["fallback"]["counts"]["leaked"] == 1
        assert diagnostics["fallback"]["last_failure"]["session_key"] == "cli:fallback-leak"

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
        )
        backend._tool_adapter = MagicMock()
        backend._tool_adapter._tools = {}
        backend._message_converter = MagicMock()
        backend._message_converter.convert.return_value = AgentResponse(content="done")

        client = MagicMock()
        client.query = AsyncMock()

        async def _receive():
            from claude_agent_sdk.types import UserMessage
            yield UserMessage(content="hello", parent_tool_use_id=None)
            yield MagicMock()

        client.receive_messages = _receive
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
            mock_session.metadata = {"sdk_session_id": "sdk-reset"}
            backend.sessions.get_or_create = MagicMock(return_value=mock_session)
            backend.sessions.save = MagicMock()
            backend.sessions.invalidate = MagicMock()

            await backend.reset_session("test_session")

            mock_client.disconnect.assert_called_once()
            assert "test_session" not in backend._clients
            mock_session.clear.assert_called_once()
            assert "sdk_session_id" not in mock_session.metadata


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

            mock_client.receive_messages = _empty_receive
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

    def test_build_omits_sdk_agents_when_include_agents_false(self):
        class _FakeClaudeAgentOptions:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(ClaudeAgentOptions=_FakeClaudeAgentOptions),
                "claude_agent_sdk.types": MagicMock(HookMatcher=MagicMock()),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder
            from xbot.agent.capabilities.handoff import HandoffPolicy
            from xbot.config.schema import Config

            sdk_config = MagicMock()
            sdk_config.agents = {
                "coder": {
                    "description": "Coding assistant",
                    "when": "code-related tasks",
                    "prompt": "Base prompt",
                }
            }
            sdk_config.max_turns = 3
            sdk_config.permission_mode = "acceptEdits"
            sdk_config.hooks = {}
            sdk_config.compact_notify = False
            sdk_config.extra_args = {}
            sdk_config.disallowed_tools = []
            sdk_config.mcp_servers = {}
            sdk_config.model = "claude-sonnet-4-5"
            sdk_config.provider = "anthropic"

            builder = OptionsBuilder(
                shared_resources={"config": Config()},
                sdk_config=sdk_config,
                skill_converter=None,
                tool_adapter=None,
                sessions=None,
                context_builder=None,
                handoff_policy=HandoffPolicy(sdk_config.agents),
                capability_policy=None,
            )
            builder._build_env_config = MagicMock(return_value={})
            builder._get_model_name = MagicMock(return_value="claude-sonnet-4-5")
            builder._build_mcp_servers = MagicMock(return_value={})
            builder._get_resume_session = MagicMock(return_value=None)
            builder._build_hooks = MagicMock(return_value=None)
            builder._build_system_prompt = MagicMock(return_value="base prompt")

            options = builder.build(include_agents=False)

            assert options.agents is None
            assert options.setting_sources == ["local"]

    def test_build_limits_sdk_setting_sources_to_local_only(self):
        class _FakeClaudeAgentOptions:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(ClaudeAgentOptions=_FakeClaudeAgentOptions),
                "claude_agent_sdk.types": MagicMock(HookMatcher=MagicMock()),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder
            from xbot.config.schema import Config

            sdk_config = MagicMock()
            sdk_config.max_turns = 3
            sdk_config.permission_mode = "acceptEdits"
            sdk_config.hooks = {}
            sdk_config.compact_notify = False
            sdk_config.extra_args = {}
            sdk_config.disallowed_tools = []
            sdk_config.mcp_servers = {}

            builder = OptionsBuilder(
                shared_resources={"config": Config()},
                sdk_config=sdk_config,
                skill_converter=None,
                tool_adapter=None,
                sessions=None,
                context_builder=None,
                handoff_policy=None,
                capability_policy=None,
            )
            builder._build_env_config = MagicMock(return_value={"ANTHROPIC_BASE_URL": "https://coding.dashscope.aliyuncs.com/apps/anthropic"})
            builder._get_model_name = MagicMock(return_value="glm-5")
            builder._build_mcp_servers = MagicMock(return_value={})
            builder._get_resume_session = MagicMock(return_value=None)
            builder._build_hooks = MagicMock(return_value=None)
            builder._build_system_prompt = MagicMock(return_value="base prompt")

            options = builder.build()

            assert options.setting_sources == ["local"]

    def test_build_system_prompt_does_not_include_delegation_policy(self):
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder
            from xbot.agent.capabilities.handoff import HandoffPolicy
            from xbot.config.schema import Config

            config = Config()
            policy = HandoffPolicy(
                {
                    "coder": {
                        "description": "Coding assistant",
                        "when": "code-related tasks",
                        "prompt": "You are a coder.",
                    }
                }
            )

            builder = OptionsBuilder(
                shared_resources={"config": config},
                sdk_config=MagicMock(agents={}),
                skill_converter=None,
                tool_adapter=None,
                sessions=None,
                context_builder=None,
                handoff_policy=policy,
                capability_policy=None,
            )

            prompt = builder._build_system_prompt()

            assert "Delegation Policy" not in prompt

    def test_build_sdk_agents_preserves_raw_agent_prompt(self):
        class _FakeAgentDefinition:
            def __init__(self, description, prompt, tools, model):
                self.description = description
                self.prompt = prompt
                self.tools = tools
                self.model = model

        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(AgentDefinition=_FakeAgentDefinition),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder
            from xbot.agent.capabilities.handoff import HandoffPolicy

            sdk_config = MagicMock()
            sdk_config.agents = {
                "coder": {
                    "description": "Coding assistant",
                    "when": "code-related tasks",
                    "prompt": "Base prompt",
                    "tools": ["Read"],
                    "model": "claude-sonnet-4-5",
                }
            }

            builder = OptionsBuilder(
                shared_resources={},
                sdk_config=sdk_config,
                skill_converter=None,
                tool_adapter=None,
                sessions=None,
                context_builder=None,
                handoff_policy=HandoffPolicy(sdk_config.agents),
                capability_policy=None,
            )

            agents = builder._build_sdk_agents()

            assert agents is not None
            assert agents["coder"].prompt == "Base prompt"
            assert "specialist agent invoked by the main xbot agent" not in agents["coder"].prompt

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
        """Test that MCPServerConfig objects are converted to Claude CLI-safe dicts."""
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
            assert "tool_timeout" not in mcp_servers["test_server"]

            # Critical: Verify JSON serialization works
            json_str = json.dumps(mcp_servers)
            assert "test_server" in json_str
            assert "npx" in json_str

    def test_build_mcp_servers_maps_streamable_http_to_http_and_strips_xbot_only_fields(self):
        """HTTP MCP config should be adapted to the Claude CLI schema."""
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder
            from xbot.config.schema import MCPServerConfig

            server_config = MCPServerConfig(
                type="streamableHttp",
                url="http://127.0.0.1:8766/mcp/xbot/http/aliu",
                headers={"X-Test": "1"},
                tool_timeout=45,
                enabled_tools=["search_memory", "list_memories"],
            )

            mock_tools = MagicMock()
            mock_tools.mcp_servers = {"openmemory": server_config}

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

            assert mcp_servers["openmemory"] == {
                "type": "http",
                "url": "http://127.0.0.1:8766/mcp/xbot/http/aliu",
                "headers": {"X-Test": "1"},
            }

    def test_build_mcp_servers_expands_env_headers_and_skips_unresolved_servers(self, monkeypatch):
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import OptionsBuilder
            from xbot.config.schema import MCPServerConfig

            monkeypatch.setenv("MEM0_API_KEY", "m0-test")

            mock_tools = MagicMock()
            mock_tools.mcp_servers = {
                "mem0": MCPServerConfig(
                    type="streamableHttp",
                    url="https://mcp.mem0.ai/mcp",
                    headers={"Authorization": "Token ${MEM0_API_KEY}"},
                    enabled_tools=["search_memories", "get_memories"],
                ),
                "broken": MCPServerConfig(
                    type="streamableHttp",
                    url="https://example.com/${MISSING_TOKEN}",
                ),
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

            assert mcp_servers["mem0"] == {
                "type": "http",
                "url": "https://mcp.mem0.ai/mcp",
                "headers": {"Authorization": "Token m0-test"},
            }
            assert "broken" not in mcp_servers

    @pytest.mark.asyncio
    async def test_build_hooks_compact_notification_prefers_backend_context_helper(self):
        """Compact notification should resolve context via backend helper before legacy dict."""
        from claude_agent_sdk.types import HookMatcher
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
        assert isinstance(hooks["PreCompact"][0], HookMatcher)
        compact_handler = hooks["PreCompact"][0].hooks[0]

        compact_handler.message_callback("session:1", "compacted")
        await asyncio.sleep(0)

        assert len(outbound_calls) == 1
        assert outbound_calls[0].channel == "feishu"
        assert outbound_calls[0].chat_id == "chat-1"
        assert outbound_calls[0].content == "compacted"

    @pytest.mark.asyncio
    async def test_build_hooks_compact_notification_resolves_sdk_session_id_via_backend_helper(self):
        from claude_agent_sdk.types import HookMatcher
        from xbot.agent.backends.claude_sdk_backend import OptionsBuilder

        outbound_calls = []

        class _Bus:
            async def publish_outbound(self, message):
                outbound_calls.append(message)

        class _Backend:
            def _resolve_compact_notification_target(self, session_ref: str):
                if session_ref == "sdk-123":
                    return ("session:1", "feishu", "chat-1")
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
                "_session_contexts": {"session:1": ("feishu", "chat-1")},
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
        assert isinstance(hooks["PreCompact"][0], HookMatcher)
        compact_handler = hooks["PreCompact"][0].hooks[0]

        compact_handler.message_callback("sdk-123", "compacted")
        await asyncio.sleep(0)

        assert len(outbound_calls) == 1
        assert outbound_calls[0].channel == "feishu"
        assert outbound_calls[0].chat_id == "chat-1"
        assert outbound_calls[0].content == "compacted"

    @pytest.mark.asyncio
    async def test_build_hooks_compact_notification_ignores_string_session_context_entries(self):
        from claude_agent_sdk.types import HookMatcher
        from xbot.agent.backends.claude_sdk_backend import OptionsBuilder

        outbound_calls = []

        class _Bus:
            async def publish_outbound(self, message):
                outbound_calls.append(message)

        sdk_config = MagicMock()
        sdk_config.hooks = {}
        sdk_config.compact_notify = True

        builder = OptionsBuilder(
            shared_resources={
                "bus": _Bus(),
                "runtime": MagicMock(),
                "_session_contexts": {
                    "session:1": ("feishu", "chat-1"),
                    "session:2": "sdk_123",
                },
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
        assert isinstance(hooks["PreCompact"][0], HookMatcher)
        compact_handler = hooks["PreCompact"][0].hooks[0]

        compact_handler.message_callback("session:2", "compacted")
        await asyncio.sleep(0)

        assert outbound_calls == []

    @pytest.mark.asyncio
    async def test_build_hooks_compact_notification_prefers_direct_progress_callback(self):
        from claude_agent_sdk.types import HookMatcher
        from xbot.agent.backends.claude_sdk_backend import OptionsBuilder

        outbound_calls = []
        progress_calls = []

        class _Bus:
            async def publish_outbound(self, message):
                outbound_calls.append(message)

        class _Runtime:
            def _resolve_compact_notification_target(self, session_ref: str):
                if session_ref == "sdk-123":
                    return ("cli:direct", "cli", "direct")
                return None

            async def _emit_direct_progress_for_session(
                self,
                session_key: str,
                content: str,
                *,
                event_type: str,
                event_data: dict[str, Any] | None = None,
            ) -> bool:
                progress_calls.append((session_key, content, event_type, event_data))
                return True

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
        assert isinstance(hooks["PreCompact"][0], HookMatcher)
        compact_handler = hooks["PreCompact"][0].hooks[0]

        compact_handler.message_callback("sdk-123", "compacting")
        await asyncio.sleep(0)

        assert progress_calls == [
            ("cli:direct", "compacting", "system", {"subtype": "pre_compact"})
        ]
        assert outbound_calls == []

    def test_build_hooks_precompact_survives_sdk_internal_conversion(self):
        from claude_agent_sdk.client import ClaudeSDKClient
        from xbot.agent.backends.claude_sdk_backend import OptionsBuilder

        sdk_config = MagicMock()
        sdk_config.hooks = {}
        sdk_config.compact_notify = True

        builder = OptionsBuilder(
            shared_resources={
                "bus": MagicMock(),
                "runtime": MagicMock(),
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
        internal = ClaudeSDKClient(options=MagicMock())._convert_hooks_to_internal_format(hooks)

        assert len(internal["PreCompact"][0]["hooks"]) == 1

    def test_build_enables_replay_user_messages_extra_arg(self):
        from xbot.agent.backends.claude_sdk_backend import OptionsBuilder
        from xbot.config.schema import Config

        config = Config()
        config.agents.defaults.provider = "anthropic"
        config.agents.defaults.model = "claude-sonnet-4-5"
        config.providers.anthropic.api_key = "test-key"

        sdk_config = MagicMock()
        sdk_config.hooks = {}
        sdk_config.compact_notify = False
        sdk_config.max_turns = 4
        sdk_config.permission_mode = "acceptEdits"
        sdk_config.include_partial_messages = False
        sdk_config.disallowed_tools = ["WebFetch", "WebSearch"]
        sdk_config.extra_args = {}

        builder = OptionsBuilder(
            shared_resources={"config": config},
            sdk_config=sdk_config,
            skill_converter=None,
            tool_adapter=None,
            sessions=None,
            context_builder=None,
            handoff_policy=None,
            capability_policy=None,
        )

        options = builder.build()

        assert options.extra_args["replay-user-messages"] is None

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
    """Tests for Claude-style memory configuration wiring."""

    @pytest.mark.asyncio
    async def test_initialize_wires_memory_context_and_turn_hooks(self, tmp_path):
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
            config.tools.memory.extract_memories_enabled = True
            config.tools.memory.auto_dream_enabled = False

            captured = {}

            class _FakeContextBuilder:
                def __init__(self, workspace, **kwargs):
                    captured["workspace"] = workspace
                    captured["context_kwargs"] = kwargs
                    self.memory = object()
                    self.skills = None  # Skills loader (None for test)

                def build_messages(self, *args, **kwargs):
                    return []

            class _FakeTurnHooks:
                def __init__(self, workspace, **kwargs):
                    captured["hooks_workspace"] = workspace
                    captured["extract_enabled"] = kwargs["extract_enabled"]
                    captured["auto_dream_enabled"] = kwargs["auto_dream_enabled"]

            backend = ClaudeSDKBackend()

            with (
                patch("xbot.agent.backends.claude_sdk_backend.ContextBuilder", _FakeContextBuilder),
                patch("xbot.agent.backends.claude_sdk_backend.MemoryTurnHooks", _FakeTurnHooks),
            ):
                await backend.initialize(
                    config.agents,
                    {
                        "workspace": tmp_path,
                        "config": config,
                        "tools_config": config.tools,
                    },
                )

            assert captured["workspace"] == tmp_path
            assert captured["context_kwargs"] == {}
            assert captured["hooks_workspace"] == tmp_path
            assert captured["extract_enabled"] is True
            assert captured["auto_dream_enabled"] is False

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
    """Legacy delegation tracing tests removed with native handoff behavior."""

    def test_delegation_tracing_is_removed(self):
        with patch.dict(
            "sys.modules",
            {
                "claude_agent_sdk": MagicMock(),
                "claude_agent_sdk.types": MagicMock(),
            },
        ):
            from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

            backend = ClaudeSDKBackend()

            assert not hasattr(backend, "_record_delegation_trace")


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
        assert backend._session_store.get_by_sdk_id("sdk_1") is entry

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
            from claude_agent_sdk.types import UserMessage
            yield UserMessage(content="hello", parent_tool_use_id=None)
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

        mock_client.receive_messages = _receive
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
            from claude_agent_sdk.types import UserMessage
            yield UserMessage(content="hello", parent_tool_use_id=None)
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

        mock_client.receive_messages = _receive
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
            from claude_agent_sdk.types import SystemMessage, UserMessage
            yield SystemMessage(subtype="init", data={"session_id": "s1"})
            yield UserMessage(content="hello", parent_tool_use_id=None)
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
            raise AssertionError("receive_messages should stop after ResultMessage")

        mock_client.receive_messages = _receive
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        context = AgentContext(session_key="test_session", prompt="hello")
        responses = [msg async for msg in backend.process(context)]

        # Backend should stop reading stream after ResultMessage and not hit sentinel error.
        assert responses == []

    @pytest.mark.asyncio
    async def test_process_plain_text_query_uses_streaming_user_message_with_request_uuid(self):
        from claude_agent_sdk.types import ResultMessage, SystemMessage
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.protocol import AgentContext

        backend = ClaudeSDKBackend()
        backend.sessions = None
        backend._message_converter = None

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def _receive():
            yield SystemMessage(subtype="init", data={"session_id": "s1"})
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="s1",
            )

        mock_client.receive_messages = _receive
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        context = AgentContext(session_key="test_session", prompt="hello world")
        _ = [msg async for msg in backend.process(context)]

        mock_client.query.assert_awaited_once()
        streamed_prompt = mock_client.query.await_args.args[0]
        session_id = mock_client.query.await_args.kwargs["session_id"]
        assert not isinstance(streamed_prompt, str)

        streamed_messages = []
        async for item in streamed_prompt:
            streamed_messages.append(item)

        assert len(streamed_messages) == 1
        assert streamed_messages[0]["type"] == "user"
        assert streamed_messages[0]["message"] == {"role": "user", "content": "hello world"}
        assert streamed_messages[0]["parent_tool_use_id"] is None
        assert streamed_messages[0]["session_id"] == session_id
        assert isinstance(streamed_messages[0]["uuid"], str)
        assert backend._get_request_id_from_entry("test_session") is None

    @pytest.mark.asyncio
    async def test_process_releases_client_after_long_running_turn_and_preserves_sdk_context(self):
        from claude_agent_sdk.types import ResultMessage, SystemMessage, TaskStartedMessage
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.protocol import AgentContext

        backend = ClaudeSDKBackend()
        backend.sessions = None
        backend._message_converter = None

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def _receive():
            yield SystemMessage(subtype="init", data={"session_id": "s1"})
            yield TaskStartedMessage(
                subtype="task_started",
                data={},
                task_id="task-1",
                description="long task",
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

        mock_client.receive_messages = _receive
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]
        backend.release_client = AsyncMock(return_value=True)  # type: ignore[method-assign]

        context = AgentContext(session_key="cli:direct", prompt="hello")
        _ = [msg async for msg in backend.process(context)]

        # release_client is now fire-and-forget via create_task;
        # yield control so the event loop can execute the background task
        await asyncio.sleep(0)

        backend.release_client.assert_awaited_once_with(
            "cli:direct",
            reason="post_long_task",
            preserve_sdk_context=True,
        )


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
        mock_sessions.get = MagicMock(return_value=mock_session)
        mock_sessions.save = MagicMock()
        backend.sessions = mock_sessions

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def _receive():
            yield SystemMessage(subtype="init", data={})
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
                usage={"input_tokens": 12, "output_tokens": 3},
            )

        mock_client.receive_response = _receive
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        result = await backend.compact_session("test_session")

        mock_client.query.assert_awaited_once_with("/compact", session_id="test_session")
        assert result["success"] is True
        assert result["tokens_before"] == 1200
        assert result["tokens_after"] == 450
        assert result["usage"] == {"input_tokens": 12, "output_tokens": 3}
        assert mock_session.metadata["sdk_session_id"] == "sdk-session-1"
        mock_sessions.save.assert_called()

    @pytest.mark.asyncio
    async def test_compact_session_without_boundary_keeps_default_stats(self):
        """compact_session should still succeed when SDK returns no compact boundary event."""
        from claude_agent_sdk.types import ResultMessage, SystemMessage
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend.sessions = None

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def _receive():
            yield SystemMessage(subtype="init", data={})
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
        from claude_agent_sdk.types import ResultMessage, SystemMessage
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend.sessions = None

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def _receive():
            yield SystemMessage(subtype="init", data={})
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
            yield SystemMessage(subtype="init", data={})
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


class TestClaudeSDKBackendNativeHandoff:
    @pytest.mark.asyncio
    async def test_process_does_not_inject_runtime_policy_prefix(self):
        from claude_agent_sdk.types import ResultMessage, SystemMessage
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.capabilities.handoff import HandoffPolicy
        from xbot.agent.protocol import AgentContext

        backend = ClaudeSDKBackend()
        backend.sessions = None
        backend._message_converter = None
        backend._handoff_policy = HandoffPolicy(
            {"coder": {"description": "Coding assistant", "when": "code-related tasks", "prompt": "Be helpful"}}
        )

        mock_client = MagicMock()
        mock_client.query = AsyncMock()

        async def _receive():
            yield SystemMessage(subtype="init", data={"session_id": "s1"})
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="s1",
                result="done",
            )

        mock_client.receive_messages = _receive
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        context = AgentContext(session_key="test_session", prompt="Help me with coding")
        responses = [msg async for msg in backend.process(context)]

        assert responses == []
        mock_client.query.assert_awaited_once()
        streamed_prompt = mock_client.query.await_args.args[0]
        streamed_messages = []
        async for item in streamed_prompt:
            streamed_messages.append(item)

        assert streamed_messages[0]["message"]["content"] == "Help me with coding"
        assert "[Runtime Policy]" not in streamed_messages[0]["message"]["content"]

    @pytest.mark.asyncio
    async def test_process_error_does_not_fallback_to_main_agent(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.capabilities.handoff import HandoffPolicy
        from xbot.agent.protocol import AgentContext

        backend = ClaudeSDKBackend()
        backend.sessions = None
        backend._handoff_policy = HandoffPolicy(
            {"coder": {"description": "Coding assistant", "when": "code-related tasks", "prompt": "Be helpful"}}
        )
        backend.release_client = AsyncMock(return_value=True)  # type: ignore[method-assign]
        backend._create_temp_client = AsyncMock()  # type: ignore[method-assign]

        mock_client = MagicMock()
        mock_client.query = AsyncMock(side_effect=ConnectionError("boom"))
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        context = AgentContext(session_key="test_session", prompt="Help me with coding")
        responses = [msg async for msg in backend.process(context)]

        backend._create_temp_client.assert_not_called()
        backend.release_client.assert_awaited()
        assert len(responses) == 1
        assert "fallback to main agent" not in (responses[0].content or "")

    @pytest.mark.asyncio
    async def test_process_recoverable_error_marks_reconnect_pending_without_fresh_start(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.protocol import AgentContext

        backend = ClaudeSDKBackend()
        mock_session = MagicMock()
        mock_session.metadata = {"sdk_session_id": "sdk-1"}
        mock_session.add_message = MagicMock()
        backend.sessions = MagicMock()
        backend.sessions.get_or_create = MagicMock(return_value=mock_session)
        backend.sessions.save = MagicMock()
        backend.release_client = AsyncMock(return_value=True)  # type: ignore[method-assign]

        mock_client = MagicMock()
        mock_client.query = AsyncMock(side_effect=ConnectionError("boom"))
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        context = AgentContext(session_key="test_session", prompt="Help me with coding")
        responses = [msg async for msg in backend.process(context)]

        assert len(responses) == 1
        assert mock_session.metadata["_reconnect_pending"] is True
        assert mock_session.metadata["_last_error"] == "boom"
        assert "_fresh_start_required" not in mock_session.metadata
        backend.sessions.save.assert_called()

    @pytest.mark.asyncio
    async def test_process_nonrecoverable_error_marks_fresh_start_required(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.protocol import AgentContext

        backend = ClaudeSDKBackend()
        mock_session = MagicMock()
        mock_session.metadata = {"sdk_session_id": "sdk-1"}
        mock_session.add_message = MagicMock()
        backend.sessions = MagicMock()
        backend.sessions.get_or_create = MagicMock(return_value=mock_session)
        backend.sessions.save = MagicMock()
        backend.release_client = AsyncMock(return_value=True)  # type: ignore[method-assign]

        mock_client = MagicMock()
        mock_client.query = AsyncMock(side_effect=ValueError("bad state"))
        backend._get_or_create_client = AsyncMock(return_value=mock_client)  # type: ignore[method-assign]

        context = AgentContext(session_key="test_session", prompt="Help me with coding")
        responses = [msg async for msg in backend.process(context)]

        assert len(responses) == 1
        assert mock_session.metadata["_reconnect_pending"] is True
        assert mock_session.metadata["_fresh_start_required"] is True
        assert mock_session.metadata["_last_error"] == "bad state"
        backend.sessions.save.assert_called()


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
            from claude_agent_sdk.types import SystemMessage, UserMessage
            yield SystemMessage(subtype="init", data={"session_id": "s1"})
            yield UserMessage(content="first task", parent_tool_use_id=None)
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

        client1.receive_messages = _receive_1
        backend._clients["telegram:456"] = client1

        client2 = MagicMock()
        client2.query = AsyncMock(return_value=None)
        client2.stop_task = AsyncMock(return_value=None)
        client2.disconnect = AsyncMock(return_value=None)

        async def _receive_2():
            from claude_agent_sdk.types import SystemMessage, UserMessage
            yield SystemMessage(subtype="init", data={"session_id": "s2"})
            yield UserMessage(content="second task", parent_tool_use_id=None)
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="s2",
                result="next task done",
            )

        client2.receive_messages = _receive_2

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

    def test_convert_task_started_adds_handoff_trace_only_for_factual_sdk_events(self):
        from claude_agent_sdk.types import TaskStartedMessage
        from xbot.agent.backends.claude_sdk_backend import MessageConverter
        from xbot.agent.capabilities.handoff import HandoffPolicy

        converter = MessageConverter(
            handoff_policy=HandoffPolicy({"coder": {"description": "Coding assistant", "when": "", "prompt": ""}}),
            capabilities=None,
            config=None,
        )
        msg = TaskStartedMessage(
            subtype="task_started",
            data={},
            task_id="task-2",
            description="handoff to coder",
            uuid="u2",
            session_id="s2",
        )

        response = converter.convert(msg)

        assert response is not None
        assert response.progress_texts == ["Running: handoff to coder", "Handoff: handoff to coder"]

    def test_convert_task_started_does_not_add_handoff_trace_for_generic_agent_text(self):
        from claude_agent_sdk.types import TaskStartedMessage
        from xbot.agent.backends.claude_sdk_backend import MessageConverter
        from xbot.agent.capabilities.handoff import HandoffPolicy

        converter = MessageConverter(
            handoff_policy=HandoffPolicy({"coder": {"description": "Coding assistant", "when": "", "prompt": ""}}),
            capabilities=None,
            config=None,
        )
        msg = TaskStartedMessage(
            subtype="task_started",
            data={},
            task_id="task-3",
            description="main agent continuing request",
            uuid="u3",
            session_id="s3",
        )

        response = converter.convert(msg)

        assert response is not None
        assert response.progress_texts == ["Running: main agent continuing request"]

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
    @pytest.mark.skipif(
        not hasattr(__import__("claude_agent_sdk"), "RateLimitEvent"),
        reason="RateLimitEvent not available in installed claude_agent_sdk",
    )
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


class TestClaudeSDKBackendAuxiliaryCall:
    """Tests for direct auxiliary API call behavior."""

    @pytest.mark.asyncio
    async def test_call_for_auxiliary_resolves_provider_and_parses_tool_result(self):
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
            response = await backend.call_for_auxiliary(
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
    async def test_call_for_auxiliary_retries_retryable_http_status(self):
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
                response = await backend.call_for_auxiliary(
                    messages=[{"role": "user", "content": "hello"}],
                )

        assert state["calls"] == 2
        sleep_mock.assert_awaited_once()
        assert response.finish_reason == "stop"
        assert response.content == "ok"
        assert response.content == "ok"
