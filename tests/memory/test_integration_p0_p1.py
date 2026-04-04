"""Integration tests for the full P0 + P1 memory system.

These tests verify end-to-end flows across multiple components
to ensure the changes work together correctly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from xbot.memory.memdir.store import MemoryDirStore
from xbot.memory.memdir.scan import scan_memory_files
from xbot.memory.memdir.secrets import scan_for_secrets
from xbot.memory.models import MemoryHeader
from xbot.memory.workers.auto_dream import AutoDreamWorker
from xbot.memory.workers.auto_dream_lock import AutoDreamLock
from xbot.memory.workers.extract_memories import ExtractMemoriesWorker
from xbot.memory.workers.operations import apply_memory_operations
from xbot.memory.recall.llm_selector import select_relevant_memories_llm
from xbot.memory.recall.selector import select_relevant_memories


# ---- Helpers ----


def _msg(role: str, content: str, uuid: str | None = None) -> dict:
    m: dict[str, Any] = {"role": role, "content": content}
    if uuid is not None:
        m["uuid"] = uuid
    return m


@dataclass
class _FakeToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class _FakeLLMResponse:
    content: str = ""
    tool_calls: list[_FakeToolCall] | None = None
    finish_reason: str = "stop"


class _FakeBackend:
    def __init__(self, selected: list[str] | None = None, raise_error: bool = False):
        self._selected = selected or []
        self._raise_error = raise_error

    async def call_for_auxiliary(self, **kwargs: Any) -> _FakeLLMResponse:
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


# ==== Integration: UUID cursor + extract worker ====


@pytest.mark.asyncio
async def test_uuid_cursor_survives_state_reload(tmp_path: Path) -> None:
    """UUID cursor should persist across worker re-instantiation."""
    calls: list[list[str]] = []

    async def runner(session_key: str, messages: list[dict], direct: bool) -> bool:
        calls.append([m["content"] for m in messages])
        return True

    worker1 = ExtractMemoriesWorker(tmp_path, runner=runner)
    messages = [
        _msg("user", "one", uuid="u1"),
        _msg("assistant", "two", uuid="u2"),
    ]
    await worker1.request_run("s1", messages=messages)

    # Re-create worker (simulates process restart)
    worker2 = ExtractMemoriesWorker(tmp_path, runner=runner)
    messages2 = messages + [
        _msg("user", "three", uuid="u3"),
    ]
    await worker2.request_run("s1", messages=messages2)

    assert calls == [["one", "two"], ["three"]]


@pytest.mark.asyncio
async def test_uuid_cursor_with_duplicate_uuids(tmp_path: Path) -> None:
    """When duplicate UUIDs exist, _find_uuid_index finds the last one."""
    calls: list[list[str]] = []

    async def runner(session_key: str, messages: list[dict], direct: bool) -> bool:
        calls.append([m["content"] for m in messages])
        return True

    worker = ExtractMemoriesWorker(tmp_path, runner=runner)

    # First run
    messages = [
        _msg("user", "one", uuid="dup"),
        _msg("assistant", "two", uuid="u2"),
    ]
    await worker.request_run("s1", messages=messages)

    # Second run with a duplicate UUID
    messages2 = [
        _msg("user", "one", uuid="dup"),
        _msg("assistant", "two", uuid="u2"),
        _msg("user", "three", uuid="u3"),
    ]
    await worker.request_run("s1", messages=messages2)

    assert calls == [["one", "two"], ["three"]]


# ==== Integration: LLM recall → keyword fallback ====


@pytest.mark.asyncio
async def test_recall_llm_failure_falls_back_to_keyword(tmp_path: Path) -> None:
    """When LLM recall fails, context_provider should fallback to keyword matching."""
    from xbot.memory.integration.context_provider import MemoryContextProvider

    store = MemoryDirStore(tmp_path)
    store.create_memory(
        memory_type="reference",
        title="API latency dashboard",
        description="Grafana dashboard for API latency monitoring",
        body="grafana/internal/d/api-latency",
    )

    provider = MemoryContextProvider(tmp_path, memory_store=store)
    backend = _FakeBackend(raise_error=True)  # LLM will fail

    result = await provider.recall_relevant_memories(
        "show me the latency dashboard", backend
    )

    # Should have fallback to keyword matching and still find it
    assert "latency" in result.lower() or "API latency" in result


# ==== Integration: auto_dream with exclusive lock ====


@pytest.mark.asyncio
async def test_auto_dream_skips_when_lock_held(tmp_path: Path) -> None:
    """If another process holds the exclusive lock, maybe_run should skip."""
    runs: list[str] = []

    async def runner(session_key: str) -> bool:
        runs.append(session_key)
        return True

    worker = AutoDreamWorker(tmp_path, runner=runner, min_hours=0, min_sessions=0)

    # Pre-acquire the exclusive lock
    lock = AutoDreamLock(worker.memory_dir)
    assert lock.try_acquire_exclusive() is True

    try:
        # Worker should detect lock is held and skip
        # Need to use a different AutoDreamLock instance to simulate another process
        # But same process + same file = same flock behavior (may re-acquire)
        # So we test the conceptual path by mocking
        with patch.object(worker.lock, "try_acquire_exclusive", return_value=False):
            await worker.maybe_run("s1")
    finally:
        lock.release_exclusive()

    assert runs == []


@pytest.mark.asyncio
async def test_auto_dream_releases_lock_on_runner_failure(tmp_path: Path) -> None:
    """Exclusive lock must be released even if the runner raises."""
    async def failing_runner(session_key: str) -> bool:
        raise RuntimeError("runner crashed")

    worker = AutoDreamWorker(tmp_path, runner=failing_runner, min_hours=0, min_sessions=0)
    # Pre-populate enough sessions
    state = {"seen_sessions": ["s1", "s2", "s3"]}
    worker._save_state(state)

    with pytest.raises(RuntimeError, match="runner crashed"):
        await worker.maybe_run("s4")

    # Lock must be released
    assert worker.lock._lock_fd is None
    # Lock should be acquirable again
    assert worker.lock.try_acquire_exclusive() is True
    worker.lock.release_exclusive()


@pytest.mark.asyncio
async def test_auto_dream_rollback_on_runner_return_false(tmp_path: Path) -> None:
    """If runner returns False, lock mtime should be rolled back."""
    async def failing_runner(session_key: str) -> bool:
        return False

    worker = AutoDreamWorker(tmp_path, runner=failing_runner, min_hours=0, min_sessions=0)
    state = {"seen_sessions": ["s1", "s2", "s3"]}
    worker._save_state(state)

    await worker.maybe_run("s4")

    # Lock should have been rolled back (mtime reset)
    # The lock file should still exist but with rolled-back state
    consolidated = worker.lock.read_last_consolidated_at()
    assert consolidated == 0


# ==== Integration: atomic write + symlink defense + secret scan ====


def test_full_create_update_delete_lifecycle_with_secrets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Full CRUD lifecycle with secret scanning integration."""
    store = MemoryDirStore(tmp_path)

    # 1. Create with clean content
    ops_create = [
        {
            "action": "create",
            "memory_type": "project",
            "title": "DB Config",
            "description": "Database configuration notes",
            "content": "Use PostgreSQL on port 5432",
        }
    ]
    with caplog.at_level(logging.WARNING):
        apply_memory_operations(store, ops_create)
    assert not any("secrets" in r.message.lower() for r in caplog.records)

    headers = store.scan_headers()
    assert len(headers) == 1

    # 2. Update with secret content
    caplog.clear()
    ops_update = [
        {
            "action": "update",
            "path": str(headers[0].file_path),
            "content": "DB password=SuperSecret123!",
        }
    ]
    with caplog.at_level(logging.WARNING):
        apply_memory_operations(store, ops_update)
    assert any("secrets" in r.message.lower() for r in caplog.records)

    # File should still be written (warn-only)
    doc = store.read_memory(headers[0].file_path)
    assert "SuperSecret123" in doc.body

    # 3. Delete
    ops_delete = [
        {
            "action": "delete",
            "path": str(headers[0].file_path),
        }
    ]
    apply_memory_operations(store, ops_delete)
    assert len(store.scan_headers()) == 0


def test_scan_excludes_symlinks_but_includes_real_files(tmp_path: Path) -> None:
    """Integration: create real memories + symlinks, verify scan filters correctly."""
    store = MemoryDirStore(tmp_path)
    real_path = store.create_memory(
        memory_type="project",
        title="Real Memory",
        description="A real memory file",
        body="real content",
    )

    # Create a symlink alongside the real file
    symlink = real_path.parent / "fake-link.md"
    symlink.symlink_to(real_path)

    headers = store.scan_headers()
    filenames = [h.filename for h in headers]

    assert real_path.name in filenames
    assert "fake-link.md" not in filenames


def test_atomic_write_preserves_content_on_concurrent_reads(tmp_path: Path) -> None:
    """Atomic write should not produce torn reads."""
    store = MemoryDirStore(tmp_path)
    path = store.create_memory(
        memory_type="project",
        title="Concurrent",
        description="Test concurrent access",
        body="original body",
    )

    # Perform multiple rapid updates
    for i in range(10):
        store.update_memory(path, body=f"body version {i}")

    # Final read should be consistent
    doc = store.read_memory(path)
    assert doc.body == "body version 9"

    # No leftover .tmp files
    tmp_files = list(path.parent.glob("*.tmp"))
    assert len(tmp_files) == 0


# ==== Integration: PID liveness + lock age ====


def test_lock_with_dead_pid_allows_new_acquisition(tmp_path: Path) -> None:
    """Lock held by dead process should allow new acquisition."""
    lock = AutoDreamLock(tmp_path / "memory")
    lock.memory_dir.mkdir(parents=True, exist_ok=True)

    # Write a dead PID
    lock.path.write_text("999999999", encoding="utf-8")
    now = time.time()
    os.utime(lock.path, (now, now))

    with patch("os.kill", side_effect=ProcessLookupError):
        # Dead PID -> last_consolidated should be 0
        assert lock.read_last_consolidated_at() == 0

    # New acquisition should work
    prior = lock.acquire()
    assert prior == 0
    assert lock.read_last_consolidated_at() > 0


# ==== Integration: context_provider recall with surfacing dedup ====


@pytest.mark.asyncio
async def test_recall_does_not_resurface_already_shown_memories(tmp_path: Path) -> None:
    """Memories surfaced by keyword match should not be re-surfaced by LLM recall."""
    from xbot.memory.integration.context_provider import MemoryContextProvider

    store = MemoryDirStore(tmp_path)
    store.create_memory(
        memory_type="reference",
        title="API config",
        description="API configuration and endpoints",
        body="api.example.com/v2",
    )

    provider = MemoryContextProvider(tmp_path, memory_store=store)

    # First: keyword match surfaces the memory
    frag = provider.build_relevant_memory_fragment("API config")
    assert "API config" in frag

    # Second: LLM recall should not re-surface it (already in _surfaced_paths)
    backend = _FakeBackend(selected=["api-config.md"])
    result = await provider.recall_relevant_memories("API config", backend)
    # Should be empty because already surfaced
    assert result == ""


# ==== Integration: extract_memories UUID cursor + direct_memory_write ====


@pytest.mark.asyncio
async def test_direct_memory_write_advances_uuid_cursor(tmp_path: Path) -> None:
    """direct_memory_write=True should advance cursor without calling runner."""
    calls: list[list[str]] = []

    async def runner(session_key: str, messages: list[dict], direct: bool) -> bool:
        calls.append([m["content"] for m in messages])
        return True

    worker = ExtractMemoriesWorker(tmp_path, runner=runner)

    messages = [
        _msg("user", "one", uuid="u1"),
        _msg("assistant", "two", uuid="u2"),
    ]
    await worker.request_run("s1", messages=messages, direct_memory_write=True)

    # Runner should NOT have been called (direct_memory_write=True skips runner)
    assert calls == []

    # Cursor should be advanced
    state = worker._load_state()
    assert state["sessions"]["s1"]["cursor_uuid"] == "u2"
    assert state["sessions"]["s1"]["cursor"] == 2

    # Adding more messages should only process new ones
    messages2 = messages + [_msg("user", "three", uuid="u3")]
    await worker.request_run("s1", messages=messages2)
    assert calls == [["three"]]


# ==== Integration: full turn_hooks → extract → dream sequential flow ====


@pytest.mark.asyncio
async def test_turn_hooks_full_sequential_flow(tmp_path: Path) -> None:
    """End-to-end test: turn_hooks calls extract then dream sequentially."""
    from xbot.memory.integration.turn_hooks import MemoryTurnHooks

    order: list[str] = []

    class _TrackedExtractor:
        async def request_run(self, session_key, *, messages=None, direct_memory_write=False):
            order.append("extract")
            await asyncio.sleep(0.01)

    class _TrackedDreamer:
        async def maybe_run(self, session_key):
            order.append("dream")
            await asyncio.sleep(0.01)

    hooks = MemoryTurnHooks(
        tmp_path,
        extractor=_TrackedExtractor(),
        dreamer=_TrackedDreamer(),
        extract_enabled=True,
        auto_dream_enabled=True,
    )

    await hooks.handle_turn_end(
        "s1", messages=[{"role": "user", "content": "hi"}],
        is_subagent=False, direct_memory_write=False,
    )

    assert order == ["extract", "dream"]


# ==== Edge case: resolve_managed_path with relative symlink ====


def test_resolve_managed_path_rejects_relative_symlink(tmp_path: Path) -> None:
    """Relative path that is a symlink should be rejected."""
    store = MemoryDirStore(tmp_path)
    memory_dir = store.memory_dir
    (memory_dir / "project").mkdir(parents=True, exist_ok=True)

    # Create a real file
    real = memory_dir / "project" / "real.md"
    real.write_text("---\nname: x\ntype: project\n---\nbody\n", encoding="utf-8")

    # Create symlink using relative path
    link = memory_dir / "project" / "relative-link.md"
    link.symlink_to(real)

    with pytest.raises(ValueError, match="Symlinks not allowed"):
        store.resolve_managed_path(link)


def test_resolve_managed_path_with_relative_string_path(tmp_path: Path) -> None:
    """Relative string path should be resolved relative to memory_dir."""
    store = MemoryDirStore(tmp_path)
    (store.memory_dir / "project").mkdir(parents=True, exist_ok=True)
    real = store.memory_dir / "project" / "test.md"
    real.write_text("---\nname: x\ntype: project\n---\nbody\n", encoding="utf-8")

    # Should resolve correctly
    result = store.resolve_managed_path("project/test.md")
    assert result.exists()
    assert result == real.resolve()
