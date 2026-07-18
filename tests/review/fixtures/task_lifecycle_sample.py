async def good():
    import asyncio
    t = asyncio.ensure_future(coro())  # clean: assigned


async def bad():
    import asyncio
    asyncio.ensure_future(coro())      # anti: unassigned, GC risk
