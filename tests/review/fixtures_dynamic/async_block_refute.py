"""refute: cooperative await, does not block the loop (false positive)."""
import asyncio


async def yields_quickly():
    # CLEAN: yields control back to the loop and completes promptly.
    await asyncio.sleep(0.01)
