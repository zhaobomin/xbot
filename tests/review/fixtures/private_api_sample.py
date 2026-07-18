import asyncio


def good():
    event = asyncio.Event()
    event.set()          # clean


def bad():
    event = asyncio.Event()
    x = event._waiters   # anti: private API
