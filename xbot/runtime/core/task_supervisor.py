"""Shared helpers for tracking service-level background tasks."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable

from xbot.platform.logging.core import get_logger

logger = get_logger(__name__)


class ServiceTaskRegistry:
    """Track background tasks by owner and always consume task exceptions."""

    def __init__(
        self,
        *,
        error_reporter: Callable[[str, str, BaseException], None] | None = None,
        max_tasks_per_owner: int | None = None,
    ) -> None:
        self._tasks: dict[str, set[asyncio.Task]] = defaultdict(set)
        self._error_reporter = error_reporter
        self._max_tasks_per_owner = max_tasks_per_owner

    def spawn(self, owner: str, coro: Awaitable[object], *, name: str | None = None) -> asyncio.Task:
        if self._max_tasks_per_owner is not None and len(self._tasks[owner]) >= self._max_tasks_per_owner:
            logger.warning(
                "Task limit reached for owner=%s (%d/%d), rejecting task=%s",
                owner, len(self._tasks[owner]), self._max_tasks_per_owner, name or "unnamed",
            )
            raise RuntimeError(f"Task limit exceeded for owner '{owner}'")
        task = asyncio.create_task(coro, name=name)
        self._tasks[owner].add(task)

        def _done(done_task: asyncio.Task) -> None:
            self._tasks[owner].discard(done_task)
            if not self._tasks[owner]:
                self._tasks.pop(owner, None)
            try:
                done_task.result()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if self._error_reporter is not None:
                    self._error_reporter(owner, name or "unnamed-task", exc)
                else:
                    logger.warning(
                        "Background task failed for owner=%s task=%s: %s",
                        owner,
                        name or "unnamed-task",
                        exc,
                    )

        task.add_done_callback(_done)
        return task

    def get_tasks(self, owner: str) -> set[asyncio.Task]:
        return set(self._tasks.get(owner, set()))

    async def cancel_owner(self, owner: str) -> None:
        tasks = list(self._tasks.get(owner, set()))
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.pop(owner, None)

    async def cancel_all(self) -> None:
        owners = list(self._tasks.keys())
        for owner in owners:
            await self.cancel_owner(owner)
