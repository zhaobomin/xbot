"""测试 runtime 与 coordinator 的集成。"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from xbot.agent.runtime import AgentRuntime, SessionPhase


class TestRuntimeCoordinatorIntegration:
    """测试 Runtime 与 Coordinator 集成"""

    def test_runtime_has_coordinator(self, mock_runtime_with_coordinator):
        """测试 runtime 有 coordinator"""
        assert hasattr(mock_runtime_with_coordinator, "_state_coordinator")
        assert mock_runtime_with_coordinator._state_coordinator is not None

    def test_get_session_state_returns_string(self, mock_runtime_with_coordinator):
        """测试 get_session_state 返回字符串"""
        mock_runtime_with_coordinator._state_machine.force_transition(
            "test:1", SessionPhase.RUNNING
        )

        state = mock_runtime_with_coordinator.get_session_state("test:1")
        assert state == "running"

    def test_get_session_phase_returns_enum(self, mock_runtime_with_coordinator):
        """测试 get_session_phase 返回枚举"""
        mock_runtime_with_coordinator._state_machine.force_transition(
            "test:1", SessionPhase.WAITING_PERMISSION
        )

        phase = mock_runtime_with_coordinator.get_session_phase("test:1")
        assert phase == SessionPhase.WAITING_PERMISSION
        assert isinstance(phase, SessionPhase)

    def test_get_session_phase_for_new_session(self, mock_runtime_with_coordinator):
        """测试新会话返回 IDLE"""
        phase = mock_runtime_with_coordinator.get_session_phase("new:session")
        assert phase == SessionPhase.IDLE

    def test_coordinator_tracks_phase_reads(self, mock_runtime_with_coordinator):
        """测试 coordinator 跟踪读取次数"""
        mock_runtime_with_coordinator.get_session_phase("test:1")
        mock_runtime_with_coordinator.get_session_phase("test:2")

        stats = mock_runtime_with_coordinator._state_coordinator.get_stats()
        assert stats.phase_reads == 2


# === Fixtures ===

@pytest.fixture
def mock_runtime_with_coordinator():
    """创建带有 coordinator 的 mock runtime"""
    from xbot.agent.runtime import SessionStateMachine
    from xbot.agent.state_checker import StateConsistencyChecker
    from xbot.agent.state_coordinator import SessionStateCoordinator

    runtime = MagicMock(spec=AgentRuntime)

    # 状态机
    runtime._state_machine = SessionStateMachine()

    # 活跃任务
    runtime._active_tasks = {}

    # Session locks
    runtime._session_locks = {}

    # Router 和 Backend
    router = MagicMock()
    backend = MagicMock()
    backend._clients = {}
    backend._active_task_ids = {}
    backend._client_last_used = {}
    router._backend = backend
    runtime.router = router

    # Bus
    bus = MagicMock()
    bus._pending_permission_requests = {}
    bus._pending_interaction_requests = {}
    bus._session_pending_permission_requests = {}
    bus._session_pending_interaction_requests = {}

    def get_pending_permission(session_key):
        return bus._session_pending_permission_requests.get(session_key)

    def get_pending_interaction(session_key):
        return bus._session_pending_interaction_requests.get(session_key)

    bus.get_pending_request_for_session = get_pending_permission
    bus.get_pending_interaction_for_session = get_pending_interaction
    runtime.bus = bus

    # State checker
    runtime._state_checker = StateConsistencyChecker(runtime)

    # State coordinator
    runtime._state_coordinator = SessionStateCoordinator(runtime)

    # 绑定方法
    runtime.get_session_state = AgentRuntime.get_session_state.__get__(runtime, AgentRuntime)
    runtime.get_session_phase = AgentRuntime.get_session_phase.__get__(runtime, AgentRuntime)

    return runtime


class TestStateConsistencyDuringDispatch:
    """Tests for state consistency during message dispatch.

    These tests verify the fix for the race condition bug:
    "State inconsistency at dispatch_start: ['IDLE but has active tasks']"

    Root cause: Task was registered in _active_tasks before phase was set to RUNNING.
    Fix: Call force_transition(RUNNING) before create_task() in run() method.
    """

    async def test_phase_set_before_task_registration(self, mock_runtime_with_coordinator):
        """Test that phase is set to RUNNING before task is registered.

        This is the key fix for the race condition:
        1. force_transition(RUNNING) must happen BEFORE create_task()
        2. This ensures state is consistent when register_task() is called
        """
        session_key = "test:session"

        # Initially IDLE
        phase = mock_runtime_with_coordinator.get_session_phase(session_key)
        assert phase == SessionPhase.IDLE

        # Simulate the fix: set phase BEFORE registering task
        mock_runtime_with_coordinator._state_coordinator.force_transition(
            session_key, SessionPhase.RUNNING, reason="dispatch_start"
        )

        # Now register task (simulating what happens in run())
        task = asyncio.create_task(asyncio.sleep(0.1))
        mock_runtime_with_coordinator._state_coordinator.register_task(session_key, task)

        # At this point, phase should be RUNNING (not IDLE)
        phase = mock_runtime_with_coordinator.get_session_phase(session_key)
        assert phase == SessionPhase.RUNNING

        # Clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_no_idle_with_active_tasks_warning(self, mock_runtime_with_coordinator):
        """Test that 'IDLE but has active tasks' inconsistency does not occur.

        Before the fix:
        1. create_task() → register_task() → phase still IDLE
        2. State checker sees: phase=IDLE but active_tasks=1 → warning

        After the fix:
        1. force_transition(RUNNING) → create_task() → register_task()
        2. State checker sees: phase=RUNNING and active_tasks=1 → consistent
        """
        from xbot.agent.state_checker import StateConsistencyChecker

        session_key = "test:session"
        checker = StateConsistencyChecker(mock_runtime_with_coordinator)

        # Apply the fix: set phase BEFORE creating task
        mock_runtime_with_coordinator._state_coordinator.force_transition(
            session_key, SessionPhase.RUNNING, reason="dispatch_start"
        )

        # Create and register task
        task = asyncio.create_task(asyncio.sleep(0.1))
        mock_runtime_with_coordinator._state_coordinator.register_task(session_key, task)

        try:
            # Check state consistency
            snapshot = checker.check_session(session_key)

            # Should NOT have "IDLE but has active tasks" inconsistency
            assert "IDLE but has active tasks" not in snapshot.inconsistencies
            # Phase should be RUNNING
            assert snapshot.runtime_phase == "running"
            # Should have active task
            assert snapshot.runtime_active_tasks == 1
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_task_callback_unregisters_and_sets_phase(self, mock_runtime_with_coordinator):
        """Test that task done callback properly cleans up state."""
        from xbot.agent.state_checker import StateConsistencyChecker

        session_key = "test:session"

        # Set up initial state
        mock_runtime_with_coordinator._state_coordinator.force_transition(
            session_key, SessionPhase.RUNNING, reason="dispatch_start"
        )
        task = asyncio.create_task(asyncio.sleep(0.01))
        mock_runtime_with_coordinator._state_coordinator.register_task(session_key, task)

        # Wait for task to complete
        await task

        # Manually unregister (normally done by callback)
        mock_runtime_with_coordinator._state_coordinator.unregister_task(session_key, task)

        # Check state
        snapshot = mock_runtime_with_coordinator._state_checker.check_session(session_key)

        # After task completes and unregisters, should have no active tasks
        assert snapshot.runtime_active_tasks == 0