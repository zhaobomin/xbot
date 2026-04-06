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
