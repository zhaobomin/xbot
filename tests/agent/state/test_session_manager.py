"""Tests for SessionManager."""

from unittest.mock import MagicMock

import pytest

from xbot.agent.state.session_manager import SessionManager
from xbot.agent.state.machine import SessionPhase


@pytest.mark.asyncio
async def test_get_or_create_creates_new_session():
    """Test that get_or_create creates a new session when it doesn't exist."""
    manager = SessionManager()
    state = manager.get_or_create("slack:C12345")

    assert state.session_key == "slack:C12345"
    assert state.phase == SessionPhase.IDLE
    assert state.channel == ""
    assert state.chat_id == ""


@pytest.mark.asyncio
async def test_get_or_create_retrieves_existing_session():
    """Test that get_or_create retrieves existing session."""
    manager = SessionManager()
    state1 = manager.get_or_create("slack:C12345")
    state1.channel = "slack"
    state1.chat_id = "C12345"

    state2 = manager.get_or_create("slack:C12345")
    assert state2 is state1
    assert state2.channel == "slack"
    assert state2.chat_id == "C12345"


@pytest.mark.asyncio
async def test_set_sdk_session_id_creates_mapping():
    """Test that set_sdk_session_id creates bidirectional mapping."""
    manager = SessionManager()
    state = manager.get_or_create("slack:C12345")

    manager.set_sdk_session_id("slack:C12345", "sdk-uuid-abc-123")

    assert state.sdk_session_id == "sdk-uuid-abc-123"
    assert manager.get_by_sdk_id("sdk-uuid-abc-123") is state


@pytest.mark.asyncio
async def test_set_sdk_session_id_updates_mapping():
    """Test that set_sdk_session_id updates existing mapping."""
    manager = SessionManager()
    state = manager.get_or_create("slack:C12345")
    manager.set_sdk_session_id("slack:C12345", "sdk-uuid-old")

    # Update to new SDK session ID
    manager.set_sdk_session_id("slack:C12345", "sdk-uuid-new")

    assert state.sdk_session_id == "sdk-uuid-new"
    assert manager.get_by_sdk_id("sdk-uuid-old") is None  # Old mapping removed
    assert manager.get_by_sdk_id("sdk-uuid-new") is state


@pytest.mark.asyncio
async def test_set_routing():
    """Test that set_routing updates channel and chat_id."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")

    manager.set_routing("slack:C12345", "slack", "C12345")

    state = manager.get("slack:C12345")
    assert state.channel == "slack"
    assert state.chat_id == "C12345"


@pytest.mark.asyncio
async def test_get_routing():
    """Test that get_routing returns channel and chat_id."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.set_routing("slack:C12345", "slack", "C12345")

    routing = manager.get_routing("slack:C12345")
    assert routing == ("slack", "C12345")


@pytest.mark.asyncio
async def test_get_routing_returns_none_for_unknown():
    """Test that get_routing returns None for unknown session."""
    manager = SessionManager()
    routing = manager.get_routing("unknown:session")
    assert routing is None


@pytest.mark.asyncio
async def test_resolve_routing_by_session_key():
    """Test resolve_routing accepts session_key."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.set_routing("slack:C12345", "slack", "C12345")

    result = manager.resolve_routing("slack:C12345")
    assert result == ("slack:C12345", "slack", "C12345")


@pytest.mark.asyncio
async def test_resolve_routing_by_sdk_session_id():
    """Test resolve_routing accepts sdk_session_id."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.set_routing("slack:C12345", "slack", "C12345")
    manager.set_sdk_session_id("slack:C12345", "sdk-uuid-abc")

    result = manager.resolve_routing("sdk-uuid-abc")
    assert result == ("slack:C12345", "slack", "C12345")


@pytest.mark.asyncio
async def test_resolve_routing_returns_none_for_unknown():
    """Test resolve_routing returns None for unknown identifier."""
    manager = SessionManager()
    result = manager.resolve_routing("unknown-id")
    assert result is None


@pytest.mark.asyncio
async def test_can_start_request_returns_true_for_idle():
    """Test can_start_request returns True for IDLE phase."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")

    assert manager.can_start_request("slack:C12345") is True


@pytest.mark.asyncio
async def test_can_start_request_returns_false_for_running():
    """Test can_start_request returns False for RUNNING phase."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.start_request("slack:C12345")

    assert manager.can_start_request("slack:C12345") is False


@pytest.mark.asyncio
async def test_start_request_transitions_to_running():
    """Test start_request transitions phase to RUNNING."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")

    result = manager.start_request("slack:C12345")
    assert result is True

    state = manager.get("slack:C12345")
    assert state.phase == SessionPhase.RUNNING


@pytest.mark.asyncio
async def test_start_request_returns_false_when_not_idle():
    """Test start_request returns False when phase is not IDLE."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.start_request("slack:C12345")  # Phase = RUNNING

    result = manager.start_request("slack:C12345")
    assert result is False


@pytest.mark.asyncio
async def test_end_request_transitions_to_idle():
    """Test end_request transitions phase back to IDLE."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.start_request("slack:C12345")

    manager.end_request("slack:C12345")

    state = manager.get("slack:C12345")
    assert state.phase == SessionPhase.IDLE


@pytest.mark.asyncio
async def test_end_request_can_set_custom_phase():
    """Test end_request can set custom phase (e.g., ERROR)."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.start_request("slack:C12345")

    manager.end_request("slack:C12345", SessionPhase.ERROR)

    state = manager.get("slack:C12345")
    assert state.phase == SessionPhase.ERROR


@pytest.mark.asyncio
async def test_set_client():
    """Test set_client stores client in session state."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    mock_client = MagicMock()
    manager.set_client("slack:C12345", mock_client)
    state = manager.get("slack:C12345")
    assert state.client is mock_client


@pytest.mark.asyncio
async def test_get_client():
    """Test get_client retrieves client."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    mock_client = MagicMock()
    manager.set_client("slack:C12345", mock_client)
    client = manager.get_client("slack:C12345")
    assert client is mock_client


@pytest.mark.asyncio
async def test_has_client():
    """Test has_client checks if session has client."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    assert manager.has_client("slack:C12345") is False
    mock_client = MagicMock()
    manager.set_client("slack:C12345", mock_client)
    assert manager.has_client("slack:C12345") is True


@pytest.mark.asyncio
async def test_list_client_sessions():
    """Test list_client_sessions returns sessions with clients."""
    manager = SessionManager()
    manager.get_or_create("slack:C1")
    manager.get_or_create("slack:C2")
    manager.get_or_create("slack:C3")
    mock_client = MagicMock()
    manager.set_client("slack:C1", mock_client)
    manager.set_client("slack:C2", mock_client)
    sessions = manager.list_client_sessions()
    assert set(sessions) == {"slack:C1", "slack:C2"}
