from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_service_task_registry_unregisters_completed_tasks_and_reports_errors() -> None:
    from xbot.runtime.core.task_supervisor import ServiceTaskRegistry

    seen: list[tuple[str, str, str]] = []
    registry = ServiceTaskRegistry(
        error_reporter=lambda owner, task_name, exc: seen.append((owner, task_name, str(exc)))
    )

    async def boom() -> None:
        raise RuntimeError("boom")

    task = registry.spawn("backend", boom(), name="release-task")
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert registry.get_tasks("backend") == set()
    assert seen == [("backend", "release-task", "boom")]


@pytest.mark.asyncio
async def test_service_task_registry_cancels_owner_tasks() -> None:
    from xbot.runtime.core.task_supervisor import ServiceTaskRegistry

    registry = ServiceTaskRegistry()
    cancelled = False

    async def worker() -> None:
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled = True
            raise

    registry.spawn("channels", worker(), name="dispatch")
    await asyncio.sleep(0)

    await registry.cancel_owner("channels")

    assert cancelled is True
    assert registry.get_tasks("channels") == set()


@pytest.mark.asyncio
async def test_service_task_registry_error_reporter_receives_all_exception_types() -> None:
    """Test that error reporter receives various exception types correctly."""
    from xbot.runtime.core.task_supervisor import ServiceTaskRegistry

    seen: list[tuple[str, str, str]] = []
    registry = ServiceTaskRegistry(
        error_reporter=lambda owner, task_name, exc: seen.append((owner, task_name, type(exc).__name__))
    )

    async def value_error() -> None:
        raise ValueError("invalid value")

    async def key_error() -> None:
        raise KeyError("missing key")

    async def type_error() -> None:
        raise TypeError("wrong type")

    task1 = registry.spawn("test-owner", value_error(), name="value-task")
    task2 = registry.spawn("test-owner", key_error(), name="key-task")
    task3 = registry.spawn("test-owner", type_error(), name="type-task")

    await asyncio.gather(task1, task2, task3, return_exceptions=True)
    await asyncio.sleep(0)

    assert len(seen) == 3
    exception_types = {exc_type for _, _, exc_type in seen}
    assert "ValueError" in exception_types
    assert "KeyError" in exception_types
    assert "TypeError" in exception_types


@pytest.mark.asyncio
async def test_service_task_registry_cancel_all_terminates_all_owners() -> None:
    """Test that cancel_all cancels tasks for all owners."""
    from xbot.runtime.core.task_supervisor import ServiceTaskRegistry

    registry = ServiceTaskRegistry()
    cancelled_counts = {"owner-a": 0, "owner-b": 0}

    async def worker(owner: str) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled_counts[owner] += 1
            raise

    registry.spawn("owner-a", worker("owner-a"), name="task-a1")
    registry.spawn("owner-a", worker("owner-a"), name="task-a2")
    registry.spawn("owner-b", worker("owner-b"), name="task-b1")

    await asyncio.sleep(0)
    assert len(registry.get_tasks("owner-a")) == 2
    assert len(registry.get_tasks("owner-b")) == 1

    await registry.cancel_all()

    assert cancelled_counts["owner-a"] == 2
    assert cancelled_counts["owner-b"] == 1
    assert registry.get_tasks("owner-a") == set()
    assert registry.get_tasks("owner-b") == set()


@pytest.mark.asyncio
async def test_service_task_registry_concurrent_spawn_and_cancel() -> None:
    """Test concurrent spawning and cancellation doesn't cause race conditions."""
    from xbot.runtime.core.task_supervisor import ServiceTaskRegistry

    registry = ServiceTaskRegistry()
    spawned_count = 0
    cancelled_count = 0

    async def quick_task() -> None:
        nonlocal spawned_count, cancelled_count
        spawned_count += 1
        try:
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            cancelled_count += 1
            raise

    async def spawn_many():
        for i in range(20):
            registry.spawn("concurrent", quick_task(), name=f"task-{i}")
            await asyncio.sleep(0.01)

    async def cancel_after_delay():
        await asyncio.sleep(0.05)
        await registry.cancel_owner("concurrent")

    await asyncio.gather(spawn_many(), cancel_after_delay())

    # Wait for all task callbacks to complete
    await asyncio.sleep(0.1)

    # All spawned tasks should be either completed or cancelled
    assert registry.get_tasks("concurrent") == set()


@pytest.mark.asyncio
async def test_service_task_registry_cancelled_error_not_reported() -> None:
    """Test that CancelledError is not reported to error_reporter."""
    from xbot.runtime.core.task_supervisor import ServiceTaskRegistry

    reported: list[str] = []
    registry = ServiceTaskRegistry(
        error_reporter=lambda owner, task_name, exc: reported.append(task_name)
    )

    async def cancellable() -> None:
        await asyncio.sleep(10)

    task = registry.spawn("test", cancellable(), name="cancel-test")
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    # CancelledError should not be reported
    assert reported == []
    assert registry.get_tasks("test") == set()


@pytest.mark.asyncio
async def test_service_task_registry_cancel_nonexistent_owner_is_noop() -> None:
    """Test that cancelling a non-existent owner doesn't raise."""
    from xbot.runtime.core.task_supervisor import ServiceTaskRegistry

    registry = ServiceTaskRegistry()

    # Should not raise
    await registry.cancel_owner("nonexistent")

    assert registry.get_tasks("nonexistent") == set()


@pytest.mark.asyncio
async def test_service_task_registry_multiple_owners_isolated() -> None:
    """Test that tasks from different owners are properly isolated."""
    from xbot.runtime.core.task_supervisor import ServiceTaskRegistry

    registry = ServiceTaskRegistry()
    results = {"owner-a": [], "owner-b": []}

    async def task(owner: str, value: int) -> None:
        await asyncio.sleep(0.01)
        results[owner].append(value)

    for i in range(5):
        registry.spawn("owner-a", task("owner-a", i), name=f"a-{i}")
        registry.spawn("owner-b", task("owner-b", i), name=f"b-{i}")

    # Cancel only owner-a
    await asyncio.sleep(0.02)  # Let some tasks complete
    await registry.cancel_owner("owner-a")

    # owner-b tasks should continue
    await asyncio.sleep(0.05)

    # owner-a should have fewer or equal results (some may have completed before cancel)
    # owner-b should have all results
    assert len(results["owner-b"]) == 5
    assert registry.get_tasks("owner-a") == set()
    assert registry.get_tasks("owner-b") == set()
