"""Tests for Phase 3: sequential execution in turn_hooks."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from xbot.memory.integration.turn_hooks import MemoryTurnHooks


class _OrderTracker:
    """Tracks call order to verify sequential execution."""

    def __init__(self) -> None:
        self.order: list[str] = []


class _OrderedExtractor:
    def __init__(self, tracker: _OrderTracker) -> None:
        self.tracker = tracker

    async def request_run(
        self,
        session_key: str,
        *,
        messages: list[dict] | None = None,
        direct_memory_write: bool = False,
    ) -> None:
        self.tracker.order.append("extract_start")
        await asyncio.sleep(0.01)  # simulate async work
        self.tracker.order.append("extract_end")


class _OrderedDreamer:
    def __init__(self, tracker: _OrderTracker) -> None:
        self.tracker = tracker

    async def maybe_run(self, session_key: str) -> None:
        self.tracker.order.append("dream_start")
        await asyncio.sleep(0.01)
        self.tracker.order.append("dream_end")


@pytest.mark.asyncio
async def test_extract_runs_before_dream_sequentially(tmp_path: Path) -> None:
    """Extract must complete before dream starts (no concurrent execution)."""
    tracker = _OrderTracker()
    hooks = MemoryTurnHooks(
        tmp_path,
        extractor=_OrderedExtractor(tracker),
        dreamer=_OrderedDreamer(tracker),
        extract_enabled=True,
        auto_dream_enabled=True,
    )

    await hooks.handle_turn_end(
        "session:1", is_subagent=False, direct_memory_write=False
    )

    # With sequential execution, the order must be:
    # extract_start -> extract_end -> dream_start -> dream_end
    assert tracker.order == [
        "extract_start",
        "extract_end",
        "dream_start",
        "dream_end",
    ]


@pytest.mark.asyncio
async def test_dream_runs_alone_when_extract_disabled(tmp_path: Path) -> None:
    """When extract is disabled, only dream should run."""
    tracker = _OrderTracker()
    hooks = MemoryTurnHooks(
        tmp_path,
        extractor=_OrderedExtractor(tracker),
        dreamer=_OrderedDreamer(tracker),
        extract_enabled=False,
        auto_dream_enabled=True,
    )

    await hooks.handle_turn_end(
        "session:1", is_subagent=False, direct_memory_write=False
    )

    assert tracker.order == ["dream_start", "dream_end"]


@pytest.mark.asyncio
async def test_extract_runs_alone_when_dream_disabled(tmp_path: Path) -> None:
    """When dream is disabled, only extract should run."""
    tracker = _OrderTracker()
    hooks = MemoryTurnHooks(
        tmp_path,
        extractor=_OrderedExtractor(tracker),
        dreamer=_OrderedDreamer(tracker),
        extract_enabled=True,
        auto_dream_enabled=False,
    )

    await hooks.handle_turn_end(
        "session:1", is_subagent=False, direct_memory_write=False
    )

    assert tracker.order == ["extract_start", "extract_end"]
