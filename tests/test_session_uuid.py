"""Test that Session.add_message assigns a UUID to every message."""
from __future__ import annotations

from xbot.session.manager import Session


def test_add_message_generates_uuid() -> None:
    s = Session(key="test:1")
    s.add_message("user", "hello")
    assert len(s.messages) == 1
    msg = s.messages[0]
    assert "uuid" in msg
    assert isinstance(msg["uuid"], str)
    assert len(msg["uuid"]) == 36  # standard UUID4 string length


def test_add_message_preserves_caller_uuid() -> None:
    s = Session(key="test:2")
    s.add_message("user", "hello", uuid="custom-uuid-123")
    assert s.messages[0]["uuid"] == "custom-uuid-123"


def test_uuids_are_unique_across_messages() -> None:
    s = Session(key="test:3")
    for i in range(10):
        s.add_message("user" if i % 2 == 0 else "assistant", f"msg-{i}")
    uuids = [m["uuid"] for m in s.messages]
    assert len(set(uuids)) == 10
