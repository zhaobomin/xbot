"""Tests for list_sdk_session API."""

from datetime import datetime
from unittest.mock import patch

import pytest
from claude_agent_sdk import SDKSessionInfo


def _create_backend():
    """Create a minimally initialized backend for testing."""
    from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend
    backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
    backend._shared_resources = {}
    backend._sdk_session_ids = {}
    backend._session_store = None
    backend._use_session_store = False
    return backend


class TestListSdkSessions:
    """Tests for list_sdk_sessions method."""

    @pytest.mark.asyncio
    async def test_list_sessions_basic(self):
        """List sessions with default parameters."""
        backend = _create_backend()

        created_at = int(datetime(2026, 3, 30, 10, 0, 0).timestamp())
        last_modified = int(datetime(2026, 3, 30, 11, 0, 0).timestamp())
        session = SDKSessionInfo(
            session_id="sdk_123",
            summary="Fallback Summary",
            custom_title="Test Session",
            created_at=created_at,
            last_modified=last_modified,
            file_size=2048,
        )

        with patch("claude_agent_sdk.list_sessions") as mock_list:
            mock_list.return_value = [session]

            result = await backend.list_sdk_sessions()

            assert result["error"] is None
            assert len(result["sessions"]) == 1
            assert result["sessions"][0]["session_id"] == "sdk_123"
            assert result["sessions"][0]["title"] == "Test Session"
            assert result["sessions"][0]["created_at"] == datetime.fromtimestamp(created_at).isoformat()
            assert result["sessions"][0]["updated_at"] == datetime.fromtimestamp(last_modified).isoformat()
            assert result["sessions"][0]["file_size"] == 2048
            mock_list.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_sessions_falls_back_to_summary_and_handles_missing_created_at(self):
        backend = _create_backend()
        last_modified = int(datetime(2026, 3, 30, 11, 30, 0).timestamp())
        session = SDKSessionInfo(
            session_id="sdk_456",
            summary="Summary Title",
            custom_title=None,
            created_at=None,
            last_modified=last_modified,
            file_size=None,
        )

        with patch("claude_agent_sdk.list_sessions") as mock_list:
            mock_list.return_value = [session]

            result = await backend.list_sdk_sessions()

            assert result["sessions"][0]["title"] == "Summary Title"
            assert result["sessions"][0]["created_at"] is None
            assert result["sessions"][0]["updated_at"] == datetime.fromtimestamp(last_modified).isoformat()
            assert result["sessions"][0]["file_size"] is None

    @pytest.mark.asyncio
    async def test_list_sessions_with_pagination(self):
        """List sessions with custom limit and offset."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._shared_resources = {}
        backend._sdk_session_ids = {}

        with patch("claude_agent_sdk.list_sessions") as mock_list:
            mock_list.return_value = []

            result = await backend.list_sdk_sessions(limit=20, offset=10)

            mock_list.assert_called_once_with(limit=21, offset=10)  # limit+1 for has_more
            assert result["limit"] == 20
            assert result["offset"] == 10

    @pytest.mark.asyncio
    async def test_list_sessions_caps_limit(self):
        """Limit should be capped at 100."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._shared_resources = {}
        backend._sdk_session_ids = {}

        with patch("claude_agent_sdk.list_sessions") as mock_list:
            mock_list.return_value = []

            result = await backend.list_sdk_sessions(limit=500)

            # Should cap at 100
            mock_list.assert_called_once_with(limit=101, offset=0)
            assert result["limit"] == 100

    @pytest.mark.asyncio
    async def test_list_sessions_handles_negative_offset(self):
        """Negative offset should be normalized to 0."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._shared_resources = {}
        backend._sdk_session_ids = {}

        with patch("claude_agent_sdk.list_sessions") as mock_list:
            mock_list.return_value = []

            result = await backend.list_sdk_sessions(offset=-5)

            mock_list.assert_called_once_with(limit=11, offset=0)
            assert result["offset"] == 0

    @pytest.mark.asyncio
    async def test_list_sessions_handles_zero_limit(self):
        """Zero limit should be normalized to 1."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._shared_resources = {}
        backend._sdk_session_ids = {}

        with patch("claude_agent_sdk.list_sessions") as mock_list:
            mock_list.return_value = []

            result = await backend.list_sdk_sessions(limit=0)

            mock_list.assert_called_once_with(limit=2, offset=0)  # limit=1+1
            assert result["limit"] == 1

    @pytest.mark.asyncio
    async def test_list_sessions_detects_has_more(self):
        """Should detect if more sessions exist."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._shared_resources = {}
        backend._sdk_session_ids = {}

        # Create more sessions than limit
        sessions = [
            SDKSessionInfo(
                session_id=f"sdk_{i}",
                summary=f"Session {i}",
                custom_title=None,
                created_at=None,
                last_modified=int(datetime(2026, 3, 30, 12, 0, 0).timestamp()) + i,
                file_size=i,
            )
            for i in range(15)
        ]

        with patch("claude_agent_sdk.list_sessions") as mock_list:
            mock_list.return_value = sessions

            result = await backend.list_sdk_sessions(limit=10)

            # Should detect has_more because we got more than limit
            assert result["has_more"] is True
            assert len(result["sessions"]) == 10

    @pytest.mark.asyncio
    async def test_list_sessions_handles_error(self):
        """Should handle errors gracefully."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._shared_resources = {}
        backend._sdk_session_ids = {}

        with patch("claude_agent_sdk.list_sessions") as mock_list:
            mock_list.side_effect = RuntimeError("SDK error")

            result = await backend.list_sdk_sessions()

            assert result["error"] == "SDK error"
            assert result["sessions"] == []

    @pytest.mark.asyncio
    async def test_list_sessions_empty_result(self):
        """Should handle empty result."""
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        backend = ClaudeSDKBackend.__new__(ClaudeSDKBackend)
        backend._shared_resources = {}
        backend._sdk_session_ids = {}

        with patch("claude_agent_sdk.list_sessions") as mock_list:
            mock_list.return_value = []

            result = await backend.list_sdk_sessions()

            assert result["sessions"] == []
            assert result["has_more"] is False
