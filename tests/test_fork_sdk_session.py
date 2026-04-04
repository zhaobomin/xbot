"""Tests for fork_sdk_session API.

Tests the forking of SDK sessions with rollback on failure.
"""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch


def _create_backend():
    """Create a minimally initialized backend for testing."""
    from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

    backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
    backend._clients_lock = asyncio.Lock()
    backend._shared_resources = {"_session_contexts": {}}
    backend._sdk_session_ids = {}
    backend._clients = {}
    backend._client_last_used = {}
    backend._client_models = {}
    backend._client_skills_versions = {}
    backend._session_commands = {}
    backend._active_task_ids = {}
    backend._active_request_ids = {}
    backend.sessions = None
    backend._session_store = None
    backend._use_session_store = False
    backend._get_sdk_session_id_from_entry = MagicMock(return_value=None)
    return backend


class TestForkSdkSession:
    """Tests for fork_sdk_session method."""

    @pytest.mark.asyncio
    async def test_fork_existing_session(self):
        """Fork an existing SDK session."""
        backend = _create_backend()
        backend._shared_resources = {"_session_contexts": {"session1": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "session1"}

        # Mock ForkSessionResult
        mock_result = MagicMock()
        mock_result.session_id = "sdk_fork_456"

        with patch("claude_agent_sdk.fork_session", create=True) as mock_fork:
            mock_fork.return_value = mock_result

            result = await backend.fork_sdk_session("session1")

            assert result["forked"] is True
            assert result["original_sdk_session_id"] == "sdk_123"
            assert result["new_sdk_session_id"] == "sdk_fork_456"
            mock_fork.assert_called_once_with("sdk_123", up_to_message_id=None, title=None)

    @pytest.mark.asyncio
    async def test_fork_with_message_id(self):
        """Fork from a specific message."""
        backend = _create_backend()
        backend._shared_resources = {"_session_contexts": {"session1": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "session1"}

        mock_result = MagicMock()
        mock_result.session_id = "sdk_fork_456"

        with patch("claude_agent_sdk.fork_session", create=True) as mock_fork:
            mock_fork.return_value = mock_result

            result = await backend.fork_sdk_session("session1", up_to_message_id="msg_005")

            assert result["forked"] is True
            mock_fork.assert_called_once_with("sdk_123", up_to_message_id="msg_005", title=None)

    @pytest.mark.asyncio
    async def test_fork_with_title(self):
        """Fork with a custom title."""
        backend = _create_backend()
        backend._shared_resources = {"_session_contexts": {"session1": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "session1"}

        mock_result = MagicMock()
        mock_result.session_id = "sdk_fork_456"

        with patch("claude_agent_sdk.fork_session", create=True) as mock_fork:
            mock_fork.return_value = mock_result

            result = await backend.fork_sdk_session("session1", title="Forked Session")

            assert result["forked"] is True
            mock_fork.assert_called_once_with("sdk_123", up_to_message_id=None, title="Forked Session")

    @pytest.mark.asyncio
    async def test_fork_nonexistent_session(self):
        """Fork a session that doesn't exist."""
        backend = _create_backend()

        result = await backend.fork_sdk_session("session1")

        assert result["forked"] is False
        assert "No SDK session" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_fork_creates_new_session_key(self):
        """Fork should create a new unique session key."""
        backend = _create_backend()
        backend._shared_resources = {"_session_contexts": {"session1": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "session1"}

        mock_result = MagicMock()
        mock_result.session_id = "sdk_fork_456"

        with patch("claude_agent_sdk.fork_session", create=True) as mock_fork:
            mock_fork.return_value = mock_result

            result = await backend.fork_sdk_session("session1")

            assert result["forked"] is True
            assert "new_session_key" in result
            assert result["new_session_key"].startswith("session1_fork_")

    @pytest.mark.asyncio
    async def test_fork_sets_bidirectional_mapping(self):
        """Fork should set bidirectional mapping for new session."""
        backend = _create_backend()
        backend._shared_resources = {"_session_contexts": {"session1": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "session1"}

        mock_result = MagicMock()
        mock_result.session_id = "sdk_fork_456"

        with patch("claude_agent_sdk.fork_session", create=True) as mock_fork:
            mock_fork.return_value = mock_result

            result = await backend.fork_sdk_session("session1")

            # Check bidirectional mapping was set
            new_session_key = result["new_session_key"]
            session_contexts = backend._shared_resources.get("_session_contexts", {})
            assert session_contexts.get(new_session_key) == "sdk_fork_456"
            assert backend._sdk_session_ids.get("sdk_fork_456") == new_session_key

    @pytest.mark.asyncio
    async def test_fork_preserves_original_session(self):
        """Fork should not modify the original session."""
        backend = _create_backend()
        backend._shared_resources = {"_session_contexts": {"session1": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "session1"}

        mock_result = MagicMock()
        mock_result.session_id = "sdk_fork_456"

        with patch("claude_agent_sdk.fork_session", create=True) as mock_fork:
            mock_fork.return_value = mock_result

            await backend.fork_sdk_session("session1")

            # Original session should still be mapped
            session_contexts = backend._shared_resources.get("_session_contexts", {})
            assert session_contexts.get("session1") == "sdk_123"
            assert backend._sdk_session_ids.get("sdk_123") == "session1"

    @pytest.mark.asyncio
    async def test_fork_with_session_metadata(self):
        """Fork should store metadata about the fork."""
        backend = _create_backend()
        backend._shared_resources = {"_session_contexts": {"session1": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "session1"}

        mock_session = MagicMock()
        mock_session.metadata = {}
        backend.sessions = MagicMock()
        backend.sessions.get_or_create = MagicMock(return_value=mock_session)
        backend.sessions.save = MagicMock()

        mock_result = MagicMock()
        mock_result.session_id = "sdk_fork_456"

        with patch("claude_agent_sdk.fork_session", create=True) as mock_fork:
            mock_fork.return_value = mock_result

            result = await backend.fork_sdk_session("session1", up_to_message_id="msg_005")

            # Check fork metadata was set
            assert mock_session.metadata.get("sdk_session_id") == "sdk_fork_456"
            assert mock_session.metadata.get("forked_from") == "session1"
            assert mock_session.metadata.get("forked_up_to") == "msg_005"
            assert "forked_at" in mock_session.metadata


class TestForkSdkSessionErrorHandling:
    """Error handling tests for fork_sdk_session."""

    @pytest.mark.asyncio
    async def test_fork_sdk_error(self):
        """Handle SDK fork error."""
        backend = _create_backend()
        backend._shared_resources = {"_session_contexts": {"session1": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "session1"}

        with patch("claude_agent_sdk.fork_session", create=True) as mock_fork:
            mock_fork.side_effect = RuntimeError("SDK fork error")

            result = await backend.fork_sdk_session("session1")

            assert result["forked"] is False
            assert "SDK fork error" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_fork_invalid_message_id(self):
        """Handle invalid message ID."""
        backend = _create_backend()
        backend._shared_resources = {"_session_contexts": {"session1": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "session1"}

        with patch("claude_agent_sdk.fork_session", create=True) as mock_fork:
            mock_fork.side_effect = ValueError("Message not found")

            result = await backend.fork_sdk_session("session1", up_to_message_id="invalid")

            assert result["forked"] is False
            assert "Message not found" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_fork_session_key_none(self):
        """Handle None session key."""
        backend = _create_backend()

        result = await backend.fork_sdk_session(None)

        assert result["forked"] is False
        assert "error" in result