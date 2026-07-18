import asyncio  # noqa: F401  # keeps line numbers stable

_cache = {}


async def good():
    async with asyncio.Lock():
        _cache["k"] = "v"  # clean: write under a lock


async def bad():
    _cache["k"] = "v"  # anti: shared dict written without a lock
