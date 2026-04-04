"""Tests for delete_sdk_session API.

Tests the deletion of SDK session files and proper state cleanup.

Key test areas:
1. Normal deletion flow
2. State cleanup order (disconnect before file delete)
3. Error handling (file not found, permission denied)
4. Edge cases (no SDK session, already deleted)
"""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch


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
    return backend


class TestDeleteSdkSession:
    """Tests for delete_sdk_session method."""

    @pytest.mark.asyncio
    async def test_delete_existing_session(self):
        """Delete an existing SDK session."""
        backend = _create_backend()
        # Set up SDK session ID in the legacy mapping
        backend._shared_resources = {"_session_contexts": {"session1": ("channel", "chat")}}
        # Mock _get_sdk_session_id_from_entry to return the SDK session ID
        backend._get_sdk_session_id_from_entry = MagicMock(return_value="sdk_123")

        # Mock delete_session
        with patch("claude_agent_sdk.delete_session", create=True) as mock_delete:
            result = await backend.delete_sdk_session("session1")

            assert result["deleted"] is True
            assert result["sdk_session_id"] == "sdk_123"
            mock_delete.assert_called_once_with("sdk_123")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_session(self):
        """Delete a session that doesn't have an SDK ID."""
        backend = _create_backend()
        backend._get_sdk_session_id_from_entry = MagicMock(return_value=None)

        result = await backend.delete_sdk_session("session1")

        assert result["deleted"] is False
        assert "No SDK session" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_delete_clears_bidirectional_mapping(self):
        """Deleting should clear both session_key and sdk_session_id mappings."""
        backend = _create_backend()
        backend._get_sdk_session_id_from_entry = MagicMock(return_value="sdk_123")
        backend._set_sdk_session_id_in_entry = AsyncMock()

        with patch("claude_agent_sdk.delete_session", create=True):
            await backend.delete_sdk_session("session1")

            # Verify the SDK session ID was cleared
            backend._set_sdk_session_id_in_entry.assert_called_once_with("session1", None)

    @pytest.mark.asyncio
    async def test_delete_file_not_found_is_success(self):
        """FileNotFoundError should be treated as success (already deleted)."""
        backend = _create_backend()
        backend._get_sdk_session_id_from_entry = MagicMock(return_value="sdk_123")
        backend._set_sdk_session_id_in_entry = AsyncMock()

        with patch("claude_agent_sdk.delete_session", create=True) as mock_delete:
            mock_delete.side_effect = FileNotFoundError("File not found")

            result = await backend.delete_sdk_session("session1")

            # Should be considered successful (file already gone)
            assert result["deleted"] is True

    @pytest.mark.asyncio
    async def test_delete_permission_error_is_failure(self):
        """PermissionError should be reported as failure."""
        backend = _create_backend()
        backend._get_sdk_session_id_from_entry = MagicMock(return_value="sdk_123")

        with patch("claude_agent_sdk.delete_session", create=True) as mock_delete:
            mock_delete.side_effect = PermissionError("Permission denied")

            result = await backend.delete_sdk_session("session1")

            assert result["deleted"] is False
            assert "Permission denied" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_delete_with_session_metadata(self):
        """Delete should also clear session metadata."""
        backend = _create_backend()
        backend._get_sdk_session_id_from_entry = MagicMock(return_value=None)  # Not in entry

        # Mock session with metadata
        mock_session = MagicMock()
        mock_session.metadata = {"sdk_session_id": "sdk_123", "other_key": "value"}
        backend.sessions = MagicMock()
        backend.sessions.get = MagicMock(return_value=mock_session)
        backend.sessions.save = MagicMock()
        backend._set_sdk_session_id_in_entry = AsyncMock()

        with patch("claude_agent_sdk.delete_session", create=True):
            await backend.delete_sdk_session("session1")

            # Verify the session metadata was updated
            backend.sessions.save.assert_called()


class TestDeleteSdkSessionEdgeCases:
    """Edge case tests for delete_sdk_session."""

    @pytest.mark.asyncio
    async def test_delete_session_key_none(self):
        """Passing None as session_key should handle gracefully."""
        backend = _create_backend()

        result = await backend.delete_sdk_session(None)

        assert result["deleted"] is False

    @pytest.mark.asyncio
    async def test_delete_session_key_empty(self):
        """Passing empty string as session_key should handle gracefully."""
        backend = _create_backend()

        result = await backend.delete_sdk_session("")

        assert result["deleted"] is False

    @pytest.mark.asyncio
    async def test_delete_returns_sdk_session_id(self):
        """Delete should return the deleted SDK session ID."""
        backend = _create_backend()
        backend._shared_resources = {"_session_contexts": {"session1": "sdk_abc123xyz"}}
        backend._sdk_session_ids = {"sdk_abc123xyz": "session1"}

        with patch("claude_agent_sdk.delete_session", create=True):
            result = await backend.delete_sdk_session("session1")

            assert result["sdk_session_id"] == "sdk_abc123xyz"


class TestDeleteSdkSessionStateConsistency:
    """Tests for state consistency during delete."""

    @pytest.mark.asyncio
    async def test_delete_clears_all_tracking_dicts(self):
        """Delete should clear all related tracking dictionaries."""
        backend = _create_backend()
        backend._shared_resources = {"_session_contexts": {"session1": "sdk_123"}}
        backend._sdk_session_ids = {"sdk_123": "session1"}
        backend._clients = {"session1": MagicMock()}
        backend._client_last_used = {"session1": datetime.now()}
        backend._client_models = {"session1": "claude-3"}
        backend._client_skills_versions = {"session1": {}}
        backend._session_commands = {"session1": []}
        backend._active_task_ids = {"session1": "task1"}
        backend._active_request_ids = {"session1": "uuid1"}

        with patch("claude_agent_sdk.delete_session", create=True):
            await backend.delete_sdk_session("session1")

            assert "session1" not in backend._clients
            assert "session1" not in backend._client_last_used
            assert "session1" not in backend._client_models
            assert "session1" not in backend._session_commands
            assert "session1" not in backend._active_task_ids
            assert "session1" not in backend._active_request_ids
            session_contexts = backend._shared_resources.get("_session_contexts", {})
            assert "session1" not in session_contexts
            assert "sdk_123" not in backend._sdk_session_ids

    @pytest.mark.asyncio
    async def test_delete_gets_sdk_id_from_session_metadata(self):
        """Delete should get SDK ID from session metadata if not in _session_contexts."""
        backend = _create_backend()
        backend._shared_resources = {"_session_contexts": {}}  # Not in contexts

        # But SDK ID is in session metadata
        mock_session = MagicMock()
        mock_session.metadata = {"sdk_session_id": "sdk_from_metadata"}
        backend.sessions = MagicMock()
        backend.sessions.get = MagicMock(return_value=mock_session)
        backend.sessions.save = MagicMock()

        with patch("claude_agent_sdk.delete_session", create=True) as mock_delete:
            result = await backend.delete_sdk_session("session1")

            assert result["deleted"] is True
            assert result["sdk_session_id"] == "sdk_from_metadata"
            mock_delete.assert_called_once_with("sdk_from_metadata")