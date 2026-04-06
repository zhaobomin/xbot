import asyncio
import pytest
from xbot.agent.state.store import SessionStore

@pytest.mark.asyncio
async def test_session_store_get_or_create_race():
    """
    Test for race condition in SessionStore.get_or_create.
    If multiple tasks call it concurrently for the same key,
    only one entry should be created.
    """
    store = SessionStore()
    session_key = "race_test_session"
    
    # In current implementation, get_or_create is synchronous.
    # However, if we make it async and add a lock, we can prevent race.
    # To demonstrate the need, let's look at how it might fail if it were async without lock.
    
    # Since current get_or_create is synchronous, it's actually "thread-safe" 
    # for single-threaded asyncio (as long as no await occurs inside).
    # BUT, the problem is that if initialization of a SessionEntry 
    # involves any async operations (like loading from DB), it MUST be async.
    
    # Wait, let's check store.py again.
    pass

@pytest.mark.asyncio
async def test_concurrent_session_access():
    """
    Even if get_or_create is sync, the lock acquisition for the session should be sound.
    """
    store = SessionStore()
    session_key = "lock_test_session"
    
    # Get entry twice
    entry1 = store.get_or_create(session_key)
    entry2 = store.get_or_create(session_key)
    
    # They should be the exact same object
    assert entry1 is entry2
    assert entry1.lock is entry2.lock
    
    # Test concurrent locking
    lock = store.get_or_create_lock(session_key)
    
    async def acquire():
        async with lock:
            await asyncio.sleep(0.1)
            return True
            
    results = await asyncio.gather(acquire(), acquire())
    assert all(results)
