"""Tests for xbot.memory.workers.session_summary."""

from __future__ import annotations

import json
import pytest
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from xbot.memory.workers.session_summary import generate_session_summary


@dataclass
class FakeSession:
    messages: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        return self.messages[-max_messages:]


def _make_backend(response_text: str) -> MagicMock:
    backend = MagicMock()
    resp = MagicMock()
    resp.content = response_text
    backend.call_for_auxiliary = AsyncMock(return_value=resp)
    return backend


GOOD_SUMMARY = json.dumps({
    "current_goals": ["Build a web app"],
    "key_decisions": ["Use React"],
    "in_progress": ["Authentication module"],
    "important_facts": ["Deadline is next Friday"],
})


@pytest.mark.asyncio
async def test_returns_parsed_summary():
    backend = _make_backend(GOOD_SUMMARY)
    session = FakeSession(
        messages=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
    )
    result = await generate_session_summary(backend, session)
    assert result is not None
    assert result["current_goals"] == ["Build a web app"]
    assert result["in_progress"] == ["Authentication module"]
    assert session.metadata["working_summary"] is result


@pytest.mark.asyncio
async def test_returns_none_on_empty_messages():
    backend = _make_backend(GOOD_SUMMARY)
    session = FakeSession(messages=[])
    result = await generate_session_summary(backend, session)
    assert result is None
    backend.call_for_auxiliary.assert_not_called()


@pytest.mark.asyncio
async def test_returns_none_on_invalid_json():
    backend = _make_backend("this is not json")
    session = FakeSession(
        messages=[{"role": "user", "content": "test"}]
    )
    result = await generate_session_summary(backend, session)
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_on_api_error():
    backend = MagicMock()
    backend.call_for_auxiliary = AsyncMock(side_effect=RuntimeError("API down"))
    session = FakeSession(
        messages=[{"role": "user", "content": "test"}]
    )
    result = await generate_session_summary(backend, session)
    assert result is None


@pytest.mark.asyncio
async def test_strips_markdown_fences():
    fenced = f"```json\n{GOOD_SUMMARY}\n```"
    backend = _make_backend(fenced)
    session = FakeSession(
        messages=[{"role": "user", "content": "test"}]
    )
    result = await generate_session_summary(backend, session)
    assert result is not None
    assert result["key_decisions"] == ["Use React"]


@pytest.mark.asyncio
async def test_truncates_long_messages():
    long_content = "x" * 1000
    backend = _make_backend(GOOD_SUMMARY)
    session = FakeSession(
        messages=[{"role": "user", "content": long_content}]
    )
    result = await generate_session_summary(backend, session)
    assert result is not None
    call_args = backend.call_for_auxiliary.call_args
    transcript = call_args.kwargs["messages"][1]["content"]
    assert len(transcript) < 1000


@pytest.mark.asyncio
async def test_max_messages_limit():
    messages = [{"role": "user", "content": f"msg {i}"} for i in range(100)]
    backend = _make_backend(GOOD_SUMMARY)
    session = FakeSession(messages=messages)
    result = await generate_session_summary(backend, session, max_messages=10)
    assert result is not None
    call_args = backend.call_for_auxiliary.call_args
    transcript = call_args.kwargs["messages"][1]["content"]
    assert "msg 90" in transcript
    assert "msg 0" not in transcript


@pytest.mark.asyncio
async def test_handles_multimodal_content():
    backend = _make_backend(GOOD_SUMMARY)
    session = FakeSession(
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Look at this image"},
                {"type": "image", "source": {"data": "..."}},
            ],
        }]
    )
    result = await generate_session_summary(backend, session)
    assert result is not None


@pytest.mark.asyncio
async def test_returns_none_on_non_dict_response():
    backend = _make_backend('["not", "a", "dict"]')
    session = FakeSession(
        messages=[{"role": "user", "content": "test"}]
    )
    result = await generate_session_summary(backend, session)
    assert result is None


@pytest.mark.asyncio
async def test_session_without_get_history():
    backend = _make_backend(GOOD_SUMMARY)
    session = MagicMock(spec=[])
    session.messages = [{"role": "user", "content": "hello"}]
    session.metadata = {}
    result = await generate_session_summary(backend, session)
    assert result is not None
    assert session.metadata["working_summary"] is result
