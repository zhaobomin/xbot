"""refute: task reference is held so it is not collected (false positive)."""
import asyncio

_held: list = []


async def _coro():
    await asyncio.sleep(0.01)


def spawn():
    # CLEAN: retain the task reference so it is not garbage-collected.
    t = asyncio.ensure_future(_coro())
    _held.append(t)
    return t
