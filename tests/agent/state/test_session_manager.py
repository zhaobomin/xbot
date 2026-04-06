"""Tests for SessionManager."""

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
