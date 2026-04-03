"""Tests for trace module."""

from unittest.mock import MagicMock

import pytest

from xbot.agent.monitoring.trace import append_session_trace
from xbot.session.manager import Session


class TestAppendSessionTrace:
    """Tests for append_session_trace function."""

    def test_none_sessions(self) -> None:
        """Test that None sessions is handled gracefully."""
        # Should not raise
        append_session_trace(None, "test_key", "test_event", {"data": "value"})

    def test_appends_event(self) -> None:
        """Test that event is appended to trace."""
        session = Session(key="test", metadata={})
        sessions = MagicMock()
        sessions.get_or_create.return_value = session

        append_session_trace(sessions, "test_key", "start", {"tool": "exec"})

        assert "runtime_trace" in session.metadata
        assert len(session.metadata["runtime_trace"]) == 1
        entry = session.metadata["runtime_trace"][0]
        assert entry["event"] == "start"
        assert entry["tool"] == "exec"
        assert "timestamp" in entry

    def test_appends_multiple_events(self) -> None:
        """Test that multiple events are appended."""
        session = Session(key="test", metadata={})
        sessions = MagicMock()
        sessions.get_or_create.return_value = session

        append_session_trace(sessions, "test", "start", {"step": 1})
        append_session_trace(sessions, "test", "middle", {"step": 2})
        append_session_trace(sessions, "test", "end", {"step": 3})

        assert len(session.metadata["runtime_trace"]) == 3
        events = [e["event"] for e in session.metadata["runtime_trace"]]
        assert events == ["start", "middle", "end"]

    def test_respects_limit(self) -> None:
        """Test that trace is limited to specified size."""
        session = Session(key="test", metadata={})
        sessions = MagicMock()
        sessions.get_or_create.return_value = session

        # Append 60 events with limit of 50
        for i in range(60):
            append_session_trace(sessions, "test", f"event_{i}", {}, limit=50)

        # Should only keep last 50
        assert len(session.metadata["runtime_trace"]) == 50
        first_event = session.metadata["runtime_trace"][0]["event"]
        assert first_event == "event_10"  # First 10 should be dropped

    def test_preserves_existing_trace(self) -> None:
        """Test that existing trace is preserved."""
        session = Session(
            key="test",
            metadata={"runtime_trace": [{"event": "old", "timestamp": "2024-01-01"}]},
        )
        sessions = MagicMock()
        sessions.get_or_create.return_value = session

        append_session_trace(sessions, "test", "new", {})

        assert len(session.metadata["runtime_trace"]) == 2
        assert session.metadata["runtime_trace"][0]["event"] == "old"
        assert session.metadata["runtime_trace"][1]["event"] == "new"

    def test_updates_session_without_forcing_immediate_save(self) -> None:
        session = Session(key="test", metadata={})
        sessions = MagicMock()
        sessions.get_or_create.return_value = session

        append_session_trace(sessions, "test", "event", {})

        sessions.save.assert_not_called()
