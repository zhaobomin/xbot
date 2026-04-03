"""Tests for backend session operations with lock protection."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestDeleteSdkSessionLockProtection:
    """Tests for delete_sdk_session lock protection."""

    @pytest.mark.asyncio
    async def test_delete_uses_lock_for_state_access(self):
        """delete_sdk_session should acquire lock before accessing state."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._clients_lock = asyncio.Lock()
        backend._shared_resources = {"_session_contexts": {"test_session": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "test_session"}
        backend._clients = {}
        backend._client_last_used = {}
        backend._client_models = {}
        backend._client_skills_versions = {}
        backend._session_commands = {}
        backend._active_task_ids = {}
        backend._active_request_ids = {}
        backend.sessions = None

        # Track if mappings were cleared (this happens under lock)
        with patch("claude_agent_sdk.delete_session") as mock_delete:
            mock_delete.return_value = None
            result = await backend.delete_sdk_session("test_session")

            # If lock was properly used, mappings should be cleared
            assert "sdk_123" not in backend._sdk_session_ids
            assert result["deleted"] is True

    @pytest.mark.asyncio
    async def test_delete_clears_sdk_session_ids_under_lock(self):
        """delete_sdk_session should clear _sdk_session_ids under lock."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._clients_lock = asyncio.Lock()
        backend._shared_resources = {"_session_contexts": {"test_session": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "test_session"}
        backend._clients = {}
        backend._client_last_used = {}
        backend._client_models = {}
        backend._client_skills_versions = {}
        backend._session_commands = {}
        backend._active_task_ids = {}
        backend._active_request_ids = {}
        backend.sessions = None

        with patch("claude_agent_sdk.delete_session") as mock_delete:
            mock_delete.return_value = None
            result = await backend.delete_sdk_session("test_session")

            # _sdk_session_ids should be cleared
            assert "sdk_123" not in backend._sdk_session_ids
            assert result["deleted"] is True

    @pytest.mark.asyncio
    async def test_delete_no_sdk_session_returns_early(self):
        """delete_sdk_session should return early if no SDK session found."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._clients_lock = asyncio.Lock()
        backend._shared_resources = {"_session_contexts": {}}
        backend._sdk_session_ids = {}
        backend.sessions = None

        result = await backend.delete_sdk_session("unknown_session")

        assert result["deleted"] is False
        assert result["error"] == "No SDK session found"

    @pytest.mark.asyncio
    async def test_delete_clears_session_store_tasks_and_index(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
        from xbot.agent.state.store import SessionStore

        backend = ClaudeSDKBackend()
        backend._clients_lock = asyncio.Lock()
        backend._session_store = SessionStore()
        backend._use_session_store = True
        entry = backend._session_store.get_or_create("test_session")
        entry.sdk_session_id = "sdk_123"
        backend._session_store.set_sdk_session_id("test_session", "sdk_123")
        entry.tasks = [MagicMock(), MagicMock()]
        backend._shared_resources = {"_session_contexts": {"test_session": ("telegram", "1"), "sdk_123": ("telegram", "1")}}
        backend.sessions = None

        with patch("claude_agent_sdk.delete_session") as mock_delete:
            mock_delete.return_value = None
            result = await backend.delete_sdk_session("test_session")

        assert result["deleted"] is True
        assert backend._session_store.get("test_session").tasks == []
        assert backend._session_store.get_by_sdk_id("sdk_123") is None


class TestForkSdkSessionLockProtection:
    """Tests for fork_sdk_session lock protection."""

    @pytest.mark.asyncio
    async def test_fork_uses_lock_for_mapping_updates(self):
        """fork_sdk_session should acquire lock for mapping updates."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._clients_lock = asyncio.Lock()
        backend._shared_resources = {"_session_contexts": {"test_session": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "test_session"}
        backend.sessions = None

        # Mock the SDK fork function
        mock_fork_result = MagicMock()
        mock_fork_result.session_id = "sdk_new_456"

        with patch("claude_agent_sdk.fork_session") as mock_fork:
            mock_fork.return_value = mock_fork_result
            result = await backend.fork_sdk_session("test_session")

            # If lock was properly used, mappings should be set
            assert result["forked"] is True
            assert "test_session_fork_" in result["new_session_key"]
            # Verify bidirectional mappings exist
            new_key = result["new_session_key"]
            session_contexts = backend._shared_resources.get("_session_contexts", {})
            assert session_contexts.get(new_key) == "sdk_new_456"
            assert backend._sdk_session_ids.get("sdk_new_456") == new_key

    @pytest.mark.asyncio
    async def test_fork_rollback_on_metadata_failure(self):
        """fork_sdk_session should rollback mappings if metadata save fails."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._clients_lock = asyncio.Lock()
        backend._shared_resources = {"_session_contexts": {"test_session": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "test_session"}

        # Mock sessions that will fail on save
        mock_sessions = MagicMock()
        mock_session = MagicMock()
        mock_session.metadata = {}
        mock_sessions.get_or_create = MagicMock(return_value=mock_session)
        mock_sessions.save = MagicMock(side_effect=PermissionError("Cannot write"))
        backend.sessions = mock_sessions

        # Mock the SDK fork function
        mock_fork_result = MagicMock()
        mock_fork_result.session_id = "sdk_new_456"

        with patch("claude_agent_sdk.fork_session") as mock_fork:
            mock_fork.return_value = mock_fork_result
            result = await backend.fork_sdk_session("test_session")

            # Should have rolled back - mappings cleared
            session_contexts = backend._shared_resources.get("_session_contexts", {})
            # Original mapping should still exist
            assert session_contexts.get("test_session") == "sdk_123"
            # New mapping should NOT exist (rolled back)
            new_key = result.get("new_session_key")
            if new_key:
                assert new_key not in session_contexts

            assert result["forked"] is False
            assert "metadata" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_fork_no_sdk_session_returns_early(self):
        """fork_sdk_session should return early if no SDK session found."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._clients_lock = asyncio.Lock()
        backend._shared_resources = {"_session_contexts": {}}
        backend._sdk_session_ids = {}
        backend.sessions = None

        result = await backend.fork_sdk_session("unknown_session")

        assert result["forked"] is False
        assert result["error"] == "No SDK session found"

    @pytest.mark.asyncio
    async def test_fork_sdk_failure_returns_early(self):
        """fork_sdk_session should return early if SDK fork fails."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._clients_lock = asyncio.Lock()
        backend._shared_resources = {"_session_contexts": {"test_session": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "test_session"}
        backend.sessions = None

        with patch("claude_agent_sdk.fork_session") as mock_fork:
            mock_fork.side_effect = FileNotFoundError("SDK session file not found")
            result = await backend.fork_sdk_session("test_session")

            assert result["forked"] is False
            # Should NOT have added new mappings
            session_contexts = backend._shared_resources.get("_session_contexts", {})
            assert "test_session" in session_contexts  # Original still there
            assert len(session_contexts) == 1  # No new mapping added


class TestBidirectionalMappingConsistency:
    """Tests for bidirectional mapping consistency."""

    @pytest.mark.asyncio
    async def test_setting_sdk_session_id_does_not_overwrite_session_context_tuple(self):
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend()
        backend._use_session_store = False
        backend._shared_resources = {}
        backend._sdk_session_ids = {}
        backend.sessions = None

        backend._set_context_in_entry("telegram:1", "telegram", "chat-1")
        await backend._set_sdk_session_id_in_entry("telegram:1", "sdk-1")
        backend._set_sdk_context_mapping("sdk-1", "telegram", "chat-1")

        assert backend._get_context_by_session_key("telegram:1") == ("telegram", "chat-1")
        assert backend._resolve_compact_notification_target("sdk-1") == ("telegram:1", "telegram", "chat-1")

    @pytest.mark.asyncio
    async def test_delete_clears_both_mappings(self):
        """delete_sdk_session should clear both session_key and sdk_session_id mappings."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._clients_lock = asyncio.Lock()
        backend._shared_resources = {"_session_contexts": {"session_a": "sdk_1", "session_b": "sdk_2"}}
        backend._sdk_session_ids = {"sdk_1": "session_a", "sdk_2": "session_b"}
        backend._clients = {}
        backend._client_last_used = {}
        backend._client_models = {}
        backend._client_skills_versions = {}
        backend._session_commands = {}
        backend._active_task_ids = {}
        backend._active_request_ids = {}
        backend.sessions = None

        with patch("claude_agent_sdk.delete_session") as mock_delete:
            mock_delete.return_value = None
            result = await backend.delete_sdk_session("session_a")

            # Check both mappings were cleared for session_a
            session_contexts = backend._shared_resources.get("_session_contexts", {})
            assert "session_a" not in session_contexts
            assert "sdk_1" not in backend._sdk_session_ids
            # session_b should remain untouched
            assert session_contexts.get("session_b") == "sdk_2"
            assert backend._sdk_session_ids.get("sdk_2") == "session_b"

    @pytest.mark.asyncio
    async def test_fork_adds_both_mappings(self):
        """fork_sdk_session should add both forward and reverse mappings."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._clients_lock = asyncio.Lock()
        backend._shared_resources = {"_session_contexts": {"session_a": "sdk_1"}}
        backend._sdk_session_ids = {"sdk_1": "session_a"}
        backend.sessions = None

        mock_fork_result = MagicMock()
        mock_fork_result.session_id = "sdk_new"

        with patch("claude_agent_sdk.fork_session") as mock_fork:
            mock_fork.return_value = mock_fork_result
            result = await backend.fork_sdk_session("session_a")

            new_key = result["new_session_key"]
            session_contexts = backend._shared_resources.get("_session_contexts", {})

            # Both mappings should exist
            assert session_contexts.get(new_key) == "sdk_new"
            assert backend._sdk_session_ids.get("sdk_new") == new_key


class TestSessionStoreSdkIndexHelpers:
    def test_set_sdk_session_id_updates_reverse_index(self):
        from xbot.agent.state.store import SessionStore

        store = SessionStore()
        store.get_or_create("session_a")

        store.set_sdk_session_id("session_a", "sdk_1")

        assert store.get("session_a").sdk_session_id == "sdk_1"
        assert store.get_by_sdk_id("sdk_1") is store.get("session_a")

    def test_clear_sdk_session_id_removes_reverse_index(self):
        from xbot.agent.state.store import SessionStore

        store = SessionStore()
        store.get_or_create("session_a")
        store.set_sdk_session_id("session_a", "sdk_1")

        store.clear_sdk_session_id("session_a")

        assert store.get("session_a").sdk_session_id is None
        assert store.get_by_sdk_id("sdk_1") is None
