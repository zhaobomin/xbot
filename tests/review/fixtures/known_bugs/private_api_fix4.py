def cleanup_pending(event):
    # Bug: touching asyncio.Event internals to detect waiters before set().
    waiters = getattr(event, "_waiters", None)
    if waiters and not event.is_set():
        event.set()


def cleanup_lock(session_key, lock):
    if lock.locked():
        return
    waiters = getattr(lock, "_waiters", None)
    if waiters:
        return
    return None
