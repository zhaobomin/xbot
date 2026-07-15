"""refute: task reference is held and awaited (false positive)."""
import asyncio


async def _coro():
    await asyncio.sleep(0.01)


async def spawn():
    # CLEAN: keep the reference and await completion.
    t = asyncio.ensure_future(_coro())
    await t
    return t
