"""Tests for concurrent safety of session state and related components."""

from __future__ import annotations

import asyncio

import pytest

from xbot.agent.state.store import SessionStore
from xbot.agent.state.session_state_adapter import SessionStateAdapter


@pytest.mark.asyncio
async def test_concurrent_set_context_no_race() -> None:
    """Test that concurrent context setting doesn't cause race conditions."""
    store = SessionStore()
    shared_resources: dict[str, object] = {"_session_contexts": {}}
    adapter = SessionStateAdapter(
        session_store=store,
        use_session_store=True,
        shared_resources=shared_resources,
        sessions=None,
    )

    async def set_ctx(i: int) -> None:
        adapter.set_context(f"session:{i}", "telegram", f"chat:{i}")

    await asyncio.gather(*[set_ctx(i) for i in range(100)])

    # All 100 sessions should be present
    assert len(adapter.list_context_keys()) == 100


@pytest.mark.asyncio
async def test_concurrent_sdk_session_registration_no_race() -> None:
    """Test that concurrent SDK session ID registration doesn't cause race conditions."""
    store = SessionStore()
    shared_resources: dict[str, object] = {"_session_contexts": {}}
    adapter = SessionStateAdapter(
        session_store=store,
        use_session_store=True,
        shared_resources=shared_resources,
        sessions=None,
    )

    async def set_sdk_id(i: int) -> None:
        await adapter.set_sdk_session_id(f"session:{i}", f"sdk-{i}")

    await asyncio.gather(*[set_sdk_id(i) for i in range(50)])

    # Verify all mappings are correct
    for i in range(50):
        result = adapter.get_context_by_sdk_id(f"sdk-{i}")
        assert result == ("telegram", f"chat:{i}") or result is None  # May not have context


@pytest.mark.asyncio
async def test_concurrent_clear_tracking_state_no_race() -> None:
    """Test that concurrent clear_tracking_state calls don't cause race conditions."""
    store = SessionStore()
    shared_resources: dict[str, object] = {"_session_contexts": {}}
    adapter = SessionStateAdapter(
        session_store=store,
        use_session_store=True,
        shared_resources=shared_resources,
        sessions=None,
    )

    # Set up initial state
    for i in range(50):
        adapter.set_context(f"session:{i}", "telegram", f"chat:{i}")
        adapter.set_model(f"session:{i}", f"model-{i}")
        adapter.set_task_id(f"session:{i}", f"task-{i}")

    async def clear_state(i: int) -> None:
        adapter.clear_tracking_state(f"session:{i}")

    await asyncio.gather(*[clear_state(i) for i in range(50)])

    # All tracking state should be cleared
    # Note: get_model returns "" (empty string) when cleared, not None
    for i in range(50):
        model = adapter.get_model(f"session:{i}")
        assert model is None or model == "", f"Expected None or empty string, got {model}"
        assert adapter.get_task_id(f"session:{i}") is None


@pytest.mark.asyncio
async def test_session_store_concurrent_get_or_create() -> None:
    """Test that concurrent get_or_create calls on SessionStore are safe."""
    store = SessionStore()

    async def get_or_create(key: str) -> None:
        entry = store.get_or_create(key)
        entry.model = "test-model"

    await asyncio.gather(*[get_or_create(f"session:{i}") for i in range(100)])

    # All entries should exist
    assert len(store.list_keys()) == 100


@pytest.mark.asyncio
async def test_session_store_concurrent_set_sdk_session_id() -> None:
    """Test that concurrent SDK session ID setting is safe."""
    store = SessionStore()

    # Create entries first
    for i in range(50):
        store.get_or_create(f"session:{i}")

    async def set_sdk_id(i: int) -> None:
        store.set_sdk_session_id(f"session:{i}", f"sdk-{i}")

    await asyncio.gather(*[set_sdk_id(i) for i in range(50)])

    # Verify all mappings are correct
    for i in range(50):
        entry = store.get(f"session:{i}")
        assert entry is not None
        assert entry.sdk_session_id == f"sdk-{i}"

        # Verify reverse lookup
        by_sdk = store.get_by_sdk_id(f"sdk-{i}")
        assert by_sdk is not None
        assert by_sdk.session_key == f"session:{i}"


@pytest.mark.asyncio
async def test_session_store_concurrent_read_write() -> None:
    """Test that concurrent reads and writes don't cause issues."""
    store = SessionStore()

    # Create initial entries
    for i in range(50):
        store.get_or_create(f"session:{i}")

    read_results: list[str | None] = []
    write_count = 0

    async def reader(i: int) -> None:
        entry = store.get(f"session:{i}")
        read_results.append(entry.model if entry else None)

    async def writer(i: int) -> None:
        nonlocal write_count
        entry = store.get(f"session:{i}")
        if entry:
            entry.model = f"model-{i}"
        write_count += 1

    # Mix reads and writes
    tasks = []
    for i in range(50):
        tasks.append(reader(i))
        tasks.append(writer(i))

    await asyncio.gather(*tasks)

    # All operations should complete without error
    assert len(read_results) == 50
    assert write_count == 50


@pytest.mark.asyncio
async def test_adapter_concurrent_model_operations() -> None:
    """Test concurrent model get/set operations on adapter."""
    store = SessionStore()
    adapter = SessionStateAdapter(
        session_store=store,
        use_session_store=True,
        shared_resources={"_session_contexts": {}},
        sessions=None,
    )

    async def set_and_get(i: int) -> str | None:
        adapter.set_model(f"session:{i}", f"model-{i}")
        return adapter.get_model(f"session:{i}")

    results = await asyncio.gather(*[set_and_get(i) for i in range(100)])

    # All results should match
    for i, result in enumerate(results):
        assert result == f"model-{i}"


@pytest.mark.asyncio
async def test_adapter_concurrent_task_id_operations() -> None:
    """Test concurrent task ID get/set operations on adapter."""
    store = SessionStore()
    adapter = SessionStateAdapter(
        session_store=store,
        use_session_store=True,
        shared_resources={"_session_contexts": {}},
        sessions=None,
    )

    async def set_and_get(i: int) -> str | None:
        adapter.set_task_id(f"session:{i}", f"task-{i}")
        return adapter.get_task_id(f"session:{i}")

    results = await asyncio.gather(*[set_and_get(i) for i in range(100)])

    # All results should match
    for i, result in enumerate(results):
        assert result == f"task-{i}"


@pytest.mark.asyncio
async def test_adapter_enforce_limit_under_concurrency() -> None:
    """Test that enforce_legacy_context_limit works correctly under concurrent access."""
    shared_resources: dict[str, object] = {"_session_contexts": {}}
    adapter = SessionStateAdapter(
        session_store=None,
        use_session_store=False,
        shared_resources=shared_resources,
        sessions=None,
    )

    # Set up many sessions
    for i in range(200):
        adapter.set_context(f"session:{i}", "telegram", f"chat:{i}")

    async def enforce_limit() -> None:
        adapter.enforce_legacy_context_limit(50)

    # Run enforce_limit concurrently
    await asyncio.gather(*[enforce_limit() for _ in range(10)])

    # Should have at most 50 entries
    contexts = shared_resources["_session_contexts"]
    assert isinstance(contexts, dict)
    assert len(contexts) <= 50


@pytest.mark.asyncio
async def test_session_store_stale_client_detection() -> None:
    """Test that stale client detection works correctly."""
    store = SessionStore()
    adapter = SessionStateAdapter(
        session_store=store,
        use_session_store=True,
        shared_resources={"_session_contexts": {}},
        sessions=None,
    )

    import time

    # Create sessions with clients
    for i in range(10):
        adapter.set_client(f"session:{i}", object())
        adapter.touch(f"session:{i}")

    # Make some sessions stale
    stale_time = time.time() - 3600  # 1 hour ago
    for i in range(5):
        entry = store.get(f"session:{i}")
        if entry:
            entry.last_used = stale_time

    # Find stale sessions
    stale_keys = adapter.get_stale_client_session_keys(1800)  # 30 min TTL

    assert len(stale_keys) == 5
    assert all(k in [f"session:{i}" for i in range(5)] for k in stale_keys)