"""测试 runtime 与 coordinator 的集成。"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock

from xbot.agent.runtime import AgentRuntime, SessionPhase


class TestRuntimeCoordinatorIntegration:
    """测试 Runtime 与 Coordinator 集成"""

    def test_runtime_has_coordinator(self, mock_runtime_with_coordinator):
        """测试 runtime 有 coordinator"""
        assert hasattr(mock_runtime_with_coordinator, "_state_coordinator")
        assert mock_runtime_with_coordinator._state_coordinator is not None

    def test_coordinator_shadow_mode_enabled(self, mock_runtime_with_coordinator):
        """测试 coordinator 默认在 shadow mode"""
        assert mock_runtime_with_coordinator._coordinator_shadow_mode is True
        assert mock_runtime_with_coordinator._state_coordinator.is_shadow_mode is True

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


class TestCoordinatorFeatureFlag:
    """测试 Coordinator 功能开关"""

    def test_shadow_mode_can_be_disabled(self, mock_runtime_with_coordinator):
        """测试可以禁用 shadow mode"""
        mock_runtime_with_coordinator._state_coordinator.disable_shadow_mode()
        assert mock_runtime_with_coordinator._state_coordinator.is_shadow_mode is False

        mock_runtime_with_coordinator._state_coordinator.enable_shadow_mode()
        assert mock_runtime_with_coordinator._state_coordinator.is_shadow_mode is True


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
    runtime._coordinator_shadow_mode = True
    runtime._state_coordinator.enable_shadow_mode()

    # 绑定方法
    runtime.get_session_state = AgentRuntime.get_session_state.__get__(runtime, AgentRuntime)
    runtime.get_session_phase = AgentRuntime.get_session_phase.__get__(runtime, AgentRuntime)

    return runtime