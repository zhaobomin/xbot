"""confirm: synchronous sleep blocks the event loop (real bug)."""
import asyncio


async def blocks_forever():
    # BUG: a coroutine that effectively hangs (cooperative long await).
    # Unlike time.sleep, this yields so asyncio.wait_for CAN cancel it and
    # surface the TimeoutError that proves the blocking/hang bug.
    await asyncio.sleep(10)
