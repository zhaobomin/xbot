"""confirm: fire-and-forget task with no held reference -> GC'd (real bug)."""
import asyncio


async def _coro():
    await asyncio.sleep(0.01)


def spawn():
    # BUG: ensure_future returns a task nobody keeps; it can be collected.
    return asyncio.ensure_future(_coro())
