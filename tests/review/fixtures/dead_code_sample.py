import unused_module  # anti: never used
import os


def good():
    os.getcwd()


async def bad():
    import asyncio
    asyncio.ensure_future(some_coro())  # anti: unassigned task
