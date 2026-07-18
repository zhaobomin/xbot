import asyncio  # noqa: F401  # keeps line numbers stable
import time  # noqa: F401  # keeps line numbers stable
import httpx  # noqa: F401  # keeps line numbers stable
import requests  # noqa: F401  # keeps line numbers stable


async def good():
    await httpx.get("http://x")  # clean: awaited async call


async def bad():
    requests.get("http://x")     # anti: sync HTTP call in async function
    time.sleep(1)                # anti: blocking sleep in async function
