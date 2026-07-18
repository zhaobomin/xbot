import asyncio


def send_compact_notification(session_ref, channel, chat_id):
    async def _send():
        pass

    loop = asyncio.get_running_loop()
    asyncio.ensure_future(_send(), loop=loop)  # unassigned — task may be GC'd
