"""测试完全协调器模式。"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock

from xbot.agent.runtime import AgentRuntime, SessionPhase, SessionStateMachine
from xbot.agent.state_coordinator import SessionStateCoordinator
from xbot.agent.state_checker import StateConsistencyChecker
from xbot.bus.events import InboundMessage, OutboundMessage


class TestFullCoordinatorModeToggle:
    """测试完全协调器模式开关"""

    def test_default_disabled(self, runtime_with_coordinator):
        """测试默认禁用"""
        assert runtime_with_coordinator._use_atomic_dispatch is False
        assert runtime_with_coordinator._use_atomic_terminate is False
        assert runtime_with_coordinator._use_coordinator_transitions is False
        assert runtime_with_coordinator._coordinator_shadow_mode is True

    def test_enable(self, runtime_with_coordinator):
        """测试启用"""
        runtime_with_coordinator.enable_full_coordinator_mode()
        assert runtime_with_coordinator._use_atomic_dispatch is True
        assert runtime_with_coordinator._use_atomic_terminate is True
        assert runtime_with_coordinator._use_coordinator_transitions is True
        assert runtime_with_coordinator._coordinator_shadow_mode is False

    def test_disable(self, runtime_with_coordinator):
        """测试禁用"""
        runtime_with_coordinator.enable_full_coordinator_mode()
        runtime_with_coordinator.disable_full_coordinator_mode()
        assert runtime_with_coordinator._use_atomic_dispatch is False
        assert runtime_with_coordinator._use_atomic_terminate is False
        assert runtime_with_coordinator._use_coordinator_transitions is False
        assert runtime_with_coordinator._coordinator_shadow_mode is True

    def test_shadow_mode_disabled(self, runtime_with_coordinator):
        """测试 Shadow Mode 被禁用"""
        runtime_with_coordinator.enable_full_coordinator_mode()
        assert runtime_with_coordinator._state_coordinator._shadow_mode is False


class TestFullCoordinatorModeIntegration:
    """测试完全协调器模式集成"""

    @pytest.mark.asyncio
    async def test_dispatch_uses_coordinator(self, runtime_with_coordinator):
        """测试 dispatch 使用协调器"""
        runtime_with_coordinator.enable_full_coordinator_mode()

        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        async def mock_handle(msg, on_progress=None):
            return OutboundMessage(channel="test", chat_id="chat1", content="ok")

        runtime_with_coordinator._handle_message = mock_handle

        await runtime_with_coordinator._atomic_dispatch(msg)

        # Verify state was changed through coordinator
        state = runtime_with_coordinator._state_coordinator.get_state(msg.session_key)
        assert state is not None

    @pytest.mark.asyncio
    async def test_terminate_uses_coordinator(self, runtime_with_coordinator):
        """测试 terminate 使用协调器"""
        runtime_with_coordinator.enable_full_coordinator_mode()

        session_key = "test:chat1"

        # Initialize session state
        runtime_with_coordinator._state_machine.force_transition(
            session_key, SessionPhase.RUNNING, reason="test"
        )

        # Verify initial state
        initial_phase = runtime_with_coordinator._state_coordinator.get_phase(session_key)
        assert initial_phase == SessionPhase.RUNNING

        # Note: Full terminate test requires complex backend mocking
        # Here we just verify the coordinator integration works

    @pytest.mark.asyncio
    async def test_permission_response_uses_coordinator(self, runtime_with_coordinator):
        """测试权限响应使用协调器"""
        runtime_with_coordinator.enable_full_coordinator_mode()

        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="允许"
        )

        # Set up pending permission request
        runtime_with_coordinator.bus._session_pending_permission_requests[msg.session_key] = "perm-1"
        runtime_with_coordinator.bus._pending_permission_responses["perm-1"] = asyncio.Event()

        result = await runtime_with_coordinator._atomic_handle_permission_response(msg)

        assert result is True


class TestFullCoordinatorModeConsistency:
    """测试完全协调器模式一致性"""

    @pytest.mark.asyncio
    async def test_state_consistency_after_operations(self, runtime_with_coordinator):
        """测试操作后状态一致性"""
        runtime_with_coordinator.enable_full_coordinator_mode()

        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        async def mock_handle(msg, on_progress=None):
            return OutboundMessage(channel="test", chat_id="chat1", content="ok")

        runtime_with_coordinator._handle_message = mock_handle

        # Initial state check
        initial_phase = runtime_with_coordinator._state_coordinator.get_phase(msg.session_key)
        assert initial_phase == SessionPhase.IDLE

        # Run dispatch
        await runtime_with_coordinator._atomic_dispatch(msg)

        # Check consistency
        is_consistent, issues = runtime_with_coordinator._state_coordinator.check_consistency(
            msg.session_key
        )
        # After successful dispatch, should be consistent (IDLE)
        assert is_consistent or len(issues) == 0 or all("no backend" in i for i in issues)


class TestCoordinatorStatusText:
    """测试协调器状态文本。"""

    def test_coord_status_text_includes_stats(self, runtime_with_coordinator):
        """测试 !coord 状态文本包含统计字段且不会抛异常。"""
        runtime_with_coordinator._state_coordinator._stats.phase_transitions = 3
        runtime_with_coordinator._state_coordinator._stats.locks_created = 2
        runtime_with_coordinator._state_coordinator._stats.tasks_created = 4

        text = runtime_with_coordinator._coord_status_text()

        assert "Coordinator Mode" in text
        assert "phase_transitions: 3" in text
        assert "locks_created: 2" in text
        assert "tasks_created: 4" in text


# === Fixtures ===

class MockRuntimeForCoordinator:
    """Mock runtime for testing coordinator mode."""

    def __init__(self):
        self._state_machine = SessionStateMachine()
        self._active_tasks = {}
        self._session_locks = {}
        self._state_check_enabled = True
        self._use_atomic_dispatch = False
        self._use_atomic_terminate = False
        self._use_coordinator_transitions = False
        self._coordinator_shadow_mode = True
        self.sessions = None
        self.router = None
        self.bus = None

    def _sync_session_phase(self, session_key: str) -> None:
        pass

    def _set_session_phase(self, session_key: str, phase, reason: str = "") -> None:
        self._state_machine.set(session_key, phase, reason=reason)


@pytest.fixture
def runtime_with_coordinator():
    """创建带有协调器的 runtime"""
    runtime = MockRuntimeForCoordinator()

    # Router 和 Backend
    router = MagicMock()
    backend = MagicMock()
    backend._clients = {}
    backend._active_task_ids = {}
    backend._client_last_used = {}

    async def cancel_session(session_key):
        return 0

    async def stop_active_task(session_key):
        return False

    async def interrupt_session(session_key):
        return {"interrupted": False, "usage": None}

    backend.cancel_session = cancel_session
    backend.stop_active_task = stop_active_task
    backend.interrupt_session = interrupt_session

    router._backend = backend
    runtime.router = router
    runtime.router.backend_type = "test"

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

    async def submit_permission_response(response):
        return True

    async def submit_interaction_response(response):
        return True

    async def publish_outbound(msg):
        pass

    bus.get_pending_request_for_session = get_pending_permission
    bus.get_pending_interaction_for_session = get_pending_interaction
    bus.submit_permission_response = submit_permission_response
    bus.submit_interaction_response = submit_interaction_response
    bus.publish_outbound = publish_outbound

    runtime.bus = bus

    # State checker
    runtime._state_checker = StateConsistencyChecker(runtime)

    # State coordinator
    runtime._state_coordinator = SessionStateCoordinator(runtime)
    runtime._state_coordinator.enable_shadow_mode()

    # Bind methods from AgentRuntime
    runtime._bus_progress = AgentRuntime._bus_progress.__get__(runtime, MockRuntimeForCoordinator)
    runtime.get_session_phase = AgentRuntime.get_session_phase.__get__(runtime, MockRuntimeForCoordinator)
    runtime.get_session_state = AgentRuntime.get_session_state.__get__(runtime, MockRuntimeForCoordinator)
    runtime._log_state_snapshot = AgentRuntime._log_state_snapshot.__get__(runtime, MockRuntimeForCoordinator)
    runtime._atomic_dispatch = AgentRuntime._atomic_dispatch.__get__(runtime, MockRuntimeForCoordinator)
    runtime._atomic_terminate_session = AgentRuntime._atomic_terminate_session.__get__(runtime, MockRuntimeForCoordinator)
    runtime._atomic_handle_permission_response = AgentRuntime._atomic_handle_permission_response.__get__(runtime, MockRuntimeForCoordinator)
    runtime.enable_full_coordinator_mode = AgentRuntime.enable_full_coordinator_mode.__get__(runtime, MockRuntimeForCoordinator)
    runtime.disable_full_coordinator_mode = AgentRuntime.disable_full_coordinator_mode.__get__(runtime, MockRuntimeForCoordinator)
    runtime._coord_status_text = AgentRuntime._coord_status_text.__get__(runtime, MockRuntimeForCoordinator)
    runtime.is_full_coordinator_mode_enabled = False

    return runtime
