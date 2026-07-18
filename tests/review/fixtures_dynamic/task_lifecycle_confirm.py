"""confirm: fire-and-forget task with no held reference -> GC'd (real bug)."""
import asyncio

_held: list = []


async def _coro():
    await asyncio.sleep(0.01)


def spawn():
    # BUG: ensure_future returns a task nobody keeps; the reference is dropped
    # immediately so _held stays empty and the task is eligible for collection.
    asyncio.ensure_future(_coro())
