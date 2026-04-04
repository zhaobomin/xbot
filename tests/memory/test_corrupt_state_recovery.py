"""Tests for corrupt state file recovery in both workers (Bug A + Bug B fix)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from xbot.memory.workers.auto_dream import AutoDreamWorker
from xbot.memory.workers.extract_memories import ExtractMemoriesWorker


# ---- AutoDreamWorker corrupt state recovery ----


@pytest.mark.asyncio
async def test_auto_dream_load_state_recovers_from_corrupt_json(tmp_path: Path) -> None:
    """Corrupt .auto-dream-state.json should not crash maybe_run."""
    worker = AutoDreamWorker(tmp_path, min_hours=0, min_sessions=0)
    state_path = tmp_path / "memory" / ".auto-dream-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{broken json", encoding="utf-8")

    # Should not raise, should reset to default state
    state = worker._load_state()
    assert state == {"seen_sessions": []}


@pytest.mark.asyncio
async def test_auto_dream_load_state_recovers_from_empty_file(tmp_path: Path) -> None:
    """Empty state file should not crash."""
    worker = AutoDreamWorker(tmp_path, min_hours=0, min_sessions=0)
    state_path = tmp_path / "memory" / ".auto-dream-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("", encoding="utf-8")

    state = worker._load_state()
    assert state == {"seen_sessions": []}


@pytest.mark.asyncio
async def test_auto_dream_maybe_run_survives_corrupt_state(tmp_path: Path) -> None:
    """Full maybe_run should work even with a corrupt state file."""
    runs: list[str] = []

    async def runner(session_key: str) -> bool:
        runs.append(session_key)
        return True

    worker = AutoDreamWorker(tmp_path, runner=runner, min_hours=0, min_sessions=0)
    state_path = tmp_path / "memory" / ".auto-dream-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("<<<corrupt>>>", encoding="utf-8")

    # First call with corrupt state: recovers, records session, but needs min_sessions
    await worker.maybe_run("s1")
    # min_sessions=0 means len(seen - {current}) >= 0, which is always true for seen={s1}-{s1}={}
    # len({}) = 0 >= 0 is true
    # But wait - with min_sessions=0, we need len(seen - {session_key}) >= 0
    # After first call, seen={s1}, seen-{s1}={}, len=0 >= 0 -> True
    # So runner should fire
    # Actually let me recheck: the condition is `len(seen - {session_key}) < self.min_sessions`
    # With min_sessions=0: len({}) < 0 is False -> it proceeds
    # Actually 0 < 0 is False, so it passes. Good.

    # Runner should have been called
    assert len(runs) >= 1


# ---- ExtractMemoriesWorker corrupt state recovery ----


@pytest.mark.asyncio
async def test_extract_worker_load_state_recovers_from_corrupt_json(tmp_path: Path) -> None:
    """Corrupt .extract-state.json should not crash request_run."""
    worker = ExtractMemoriesWorker(tmp_path)
    state_path = tmp_path / "memory" / ".extract-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("not valid json!", encoding="utf-8")

    state = worker._load_state()
    assert state == {"sessions": {}}


@pytest.mark.asyncio
async def test_extract_worker_load_state_recovers_from_partial_write(tmp_path: Path) -> None:
    """Partial JSON (simulating crash during write) should not crash."""
    worker = ExtractMemoriesWorker(tmp_path)
    state_path = tmp_path / "memory" / ".extract-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # Simulate partial write: opening brace but no closing
    state_path.write_text('{"sessions": {"k1": {"cursor"', encoding="utf-8")

    state = worker._load_state()
    assert state == {"sessions": {}}


@pytest.mark.asyncio
async def test_extract_worker_request_run_survives_corrupt_state(tmp_path: Path) -> None:
    """Full request_run should work even with a corrupt state file."""
    calls: list[list[str]] = []

    async def runner(session_key: str, messages: list[dict], direct: bool) -> bool:
        calls.append([m["content"] for m in messages])
        return True

    worker = ExtractMemoriesWorker(tmp_path, runner=runner)
    state_path = tmp_path / "memory" / ".extract-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("CORRUPT", encoding="utf-8")

    messages = [{"role": "user", "content": "hello"}]
    await worker.request_run("s1", messages=messages)

    # Should have processed the message despite corrupt state
    assert calls == [["hello"]]
    # State should now be valid
    state = worker._load_state()
    assert state["sessions"]["s1"]["cursor"] == 1
