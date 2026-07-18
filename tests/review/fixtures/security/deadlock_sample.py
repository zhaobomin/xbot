import asyncio  # noqa: F401  # keeps line numbers stable


async def good():
    async with lock_a, lock_b:  # clean: consistent acquisition order
        pass


async def bad():
    async with lock_b, lock_a:  # anti: reversed order vs good()
        pass
