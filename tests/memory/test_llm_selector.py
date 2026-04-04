"""Tests for xbot.memory.recall.llm_selector."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from xbot.memory.models import MemoryHeader
from xbot.memory.recall.llm_selector import select_relevant_memories_llm


# ---- Fake backend helpers ----


@dataclass
class _FakeToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class _FakeLLMResponse:
    content: str = ""
    tool_calls: list[_FakeToolCall] | None = None


class _FakeBackend:
    """Simulates call_for_auxiliary with configurable return."""

    def __init__(
        self,
        *,
        selected: list[str] | None = None,
        raise_error: bool = False,
        delay: float = 0.0,
    ) -> None:
        self._selected = selected or []
        self._raise_error = raise_error
        self._delay = delay
        self.calls: list[dict[str, Any]] = []

    async def call_for_auxiliary(self, **kwargs: Any) -> _FakeLLMResponse:
        self.calls.append(kwargs)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raise_error:
            raise RuntimeError("API error")
        return _FakeLLMResponse(
            tool_calls=[
                _FakeToolCall(
                    name="select_memories",
                    arguments={"selected_filenames": self._selected},
                )
            ]
        )


def _make_headers(names: list[str]) -> list[MemoryHeader]:
    return [
        MemoryHeader(
            filename=n,
            file_path=Path(f"/fake/{n}"),
            mtime_ms=0.0,
            name=n.removesuffix(".md"),
            description=f"desc of {n}",
            memory_type="reference",
        )
        for n in names
    ]


# ---- Tests ----


async def test_llm_selector_returns_matched_headers() -> None:
    headers = _make_headers(["a.md", "b.md", "c.md"])
    backend = _FakeBackend(selected=["a.md", "c.md"])
    result = await select_relevant_memories_llm("query", headers, backend)
    assert result is not None
    assert [h.filename for h in result] == ["a.md", "c.md"]


async def test_llm_selector_ignores_unknown_filenames() -> None:
    headers = _make_headers(["a.md", "b.md"])
    backend = _FakeBackend(selected=["a.md", "nonexistent.md"])
    result = await select_relevant_memories_llm("query", headers, backend)
    assert result is not None
    assert [h.filename for h in result] == ["a.md"]


async def test_llm_selector_returns_none_on_api_error() -> None:
    headers = _make_headers(["a.md"])
    backend = _FakeBackend(raise_error=True)
    result = await select_relevant_memories_llm("query", headers, backend)
    assert result is None


async def test_llm_selector_returns_none_on_timeout() -> None:
    headers = _make_headers(["a.md"])
    backend = _FakeBackend(selected=["a.md"], delay=10.0)
    result = await select_relevant_memories_llm("query", headers, backend, timeout=0.05)
    assert result is None


async def test_llm_selector_returns_empty_for_blank_query() -> None:
    headers = _make_headers(["a.md"])
    backend = _FakeBackend(selected=["a.md"])
    result = await select_relevant_memories_llm("", headers, backend)
    assert result == []


async def test_llm_selector_returns_empty_for_no_headers() -> None:
    backend = _FakeBackend(selected=[])
    result = await select_relevant_memories_llm("query", [], backend)
    assert result == []


async def test_llm_selector_caps_at_max_relevant() -> None:
    from xbot.memory.models import MAX_RELEVANT_MEMORIES
    many = _make_headers([f"m{i}.md" for i in range(10)])
    backend = _FakeBackend(selected=[f"m{i}.md" for i in range(10)])
    result = await select_relevant_memories_llm("query", many, backend)
    assert result is not None
    assert len(result) <= MAX_RELEVANT_MEMORIES
    assert len(result) <= MAX_RELEVANT_MEMORIES
