from __future__ import annotations

from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class ForkedRunner:
    """Thin async runner seam for memory background tasks."""

    async def run(self, task: Callable[[], Awaitable[T]]) -> T:
        return await task()
