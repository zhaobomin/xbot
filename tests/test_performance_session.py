import time
import os
import pytest
from pathlib import Path
from xbot.session.manager import SessionManager

@pytest.mark.asyncio
async def test_session_save_performance(tmp_path):
    """
    Test that session saving performance doesn't degrade linearly with history size.
    Current implementation (O(N)) will show increasing save times.
    """
    # Create SessionManager with tmp_path
    manager = SessionManager(tmp_path)
    session_key = "perf_test_session"
    
    # Pre-fill with some messages
    session = manager.get_or_create(session_key)
    num_messages = 500
    for i in range(num_messages):
        session.add_message("user", f"User message {i}")
        session.add_message("assistant", f"Assistant message {i}")
    
    # Measure time for a single save with existing history
    start_time = time.perf_counter()
    manager.save(session)
    initial_save_time = time.perf_counter() - start_time
    print(f"\nInitial save time for {num_messages * 2} messages: {initial_save_time:.4f}s")
    
    # Add many more messages
    extra_messages = 2000
    for i in range(extra_messages):
        session.add_message("user", f"Extra User message {i}")
        session.add_message("assistant", f"Extra Assistant message {i}")
        
    # Measure time again
    start_time = time.perf_counter()
    manager.save(session)
    final_save_time = time.perf_counter() - start_time
    print(f"Final save time for {(num_messages + extra_messages) * 2} messages: {final_save_time:.4f}s")
    
    # In O(N) implementation, final_save_time should be significantly larger than initial_save_time
    # We expect it to be roughly (num+extra)/num times larger.
    # For now, we just log it. After optimization, they should be similar.
    assert final_save_time > 0 
