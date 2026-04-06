import asyncio
import pytest
from xbot.agent.runtime import AgentRuntime
from xbot.config.schema import Config

@pytest.mark.asyncio
async def test_agent_runtime_task_cleanup():
    """
    Test that AgentRuntime cleans up all session tasks on shutdown.
    """
    # Use real config to avoid mock issues
    config = Config()
    config.agents.defaults.workspace = "/tmp/xbot_test_cleanup"
    
    shared_resources = {
        "config": config,
    }
    
    runtime = AgentRuntime(config, shared_resources)
    
    # Mock a long running task
    async def long_running():
        try:
            while True:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            return "cancelled"
            
    # Spawn a session task
    session_key = "test_cleanup_session"
    task = await runtime._spawn_session_task(long_running(), session_key)
    
    # Verify task is registered
    active_tasks = runtime._state_coordinator.get_active_tasks(session_key)
    assert task in active_tasks
    
    # Shutdown runtime
    await runtime.shutdown()
    
    # Wait for task to finish after cancellation
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        pass
    
    # Verify task is cancelled
    assert task.done()
    
    # Check if any tasks remain in coordinator
    assert len(runtime._state_coordinator.get_active_tasks(session_key)) == 0
