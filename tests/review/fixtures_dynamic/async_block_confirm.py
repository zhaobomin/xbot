"""confirm: synchronous sleep blocks the event loop (real bug)."""
import time


async def blocks_forever():
    # BUG: a synchronous call inside a coroutine blocks the whole loop.
    time.sleep(10)
