import httpx, asyncio  # noqa: E401, I001  # one line on purpose: pins bad() to L7-L8

async def good():
    await httpx.get("http://x")  # clean: awaited

async def bad():
    httpx.get("http://x")       # anti-pattern: sync call not awaited
    asyncio.sleep(1)            # anti-pattern: blocking sleep in async
