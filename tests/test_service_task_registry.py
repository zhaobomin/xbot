from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_service_task_registry_unregisters_completed_tasks_and_reports_errors() -> None:
    from xbot.agent.task_supervisor import ServiceTaskRegistry

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
    from xbot.agent.task_supervisor import ServiceTaskRegistry

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
