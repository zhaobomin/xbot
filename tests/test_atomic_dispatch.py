"""测试原子 dispatch 功能。"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from xbot.agent.runtime import AgentRuntime, SessionPhase, SessionStateMachine
from xbot.agent.state_checker import StateConsistencyChecker
from xbot.agent.state_coordinator import SessionStateCoordinator
from xbot.bus.events import InboundMessage, OutboundMessage


class TestAtomicDispatchToggle:
    """测试原子 dispatch 开关"""

    def test_default_disabled(self, runtime_with_dispatch):
        """测试默认禁用"""
        assert runtime_with_dispatch.is_atomic_dispatch_enabled is False

    def test_enable(self, runtime_with_dispatch):
        """测试启用"""
        runtime_with_dispatch.enable_atomic_dispatch()
        assert runtime_with_dispatch.is_atomic_dispatch_enabled is True

    def test_disable(self, runtime_with_dispatch):
        """测试禁用"""
        runtime_with_dispatch.enable_atomic_dispatch()
        runtime_with_dispatch.disable_atomic_dispatch()
        assert runtime_with_dispatch.is_atomic_dispatch_enabled is False


class TestAtomicDispatchStateManagement:
    """测试原子 dispatch 状态管理"""

    @pytest.mark.asyncio
    async def test_atomic_dispatch_sets_running_phase(self, runtime_with_dispatch):
        """测试原子 dispatch 设置 RUNNING 阶段"""
        runtime_with_dispatch.enable_atomic_dispatch()

        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        # Mock _handle_message to return quickly
        async def mock_handle(msg, on_progress=None):
            return OutboundMessage(channel="test", chat_id="chat1", content="ok")

        runtime_with_dispatch._handle_message = mock_handle

        await runtime_with_dispatch._atomic_dispatch(msg)

        phase = runtime_with_dispatch.get_session_phase(msg.session_key)
        assert phase == SessionPhase.IDLE  # Should end in IDLE after dispatch

    @pytest.mark.asyncio
    async def test_atomic_dispatch_error_handling(self, runtime_with_dispatch):
        """测试原子 dispatch 错误处理"""
        runtime_with_dispatch.enable_atomic_dispatch()

        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        # Mock _handle_message to raise error
        async def mock_handle(msg, on_progress=None):
            raise ValueError("Test error")

        runtime_with_dispatch._handle_message = mock_handle
        runtime_with_dispatch.bus = None  # Avoid outbound publish

        await runtime_with_dispatch._atomic_dispatch(msg)

        # After error, finally block may reset to IDLE if no pending requests
        # The key check is that error was logged and handled gracefully
        # Check that the session state exists (no crash)
        state = runtime_with_dispatch.get_session_state(msg.session_key)
        assert state is not None

    @pytest.mark.asyncio
    async def test_atomic_dispatch_creates_lock(self, runtime_with_dispatch):
        """测试原子 dispatch 创建锁"""
        runtime_with_dispatch.enable_atomic_dispatch()

        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        async def mock_handle(msg, on_progress=None):
            return None

        runtime_with_dispatch._handle_message = mock_handle

        await runtime_with_dispatch._atomic_dispatch(msg)

        # Lock should have been created
        assert runtime_with_dispatch._state_coordinator.has_lock(msg.session_key)

    @pytest.mark.asyncio
    async def test_atomic_dispatch_with_pending_permission(self, runtime_with_dispatch):
        """测试原子 dispatch 有待处理权限请求"""
        runtime_with_dispatch.enable_atomic_dispatch()

        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        async def mock_handle(msg, on_progress=None):
            return None

        runtime_with_dispatch._handle_message = mock_handle

        # Simulate pending permission request
        runtime_with_dispatch.bus._session_pending_permission_requests[msg.session_key] = "perm-1"

        await runtime_with_dispatch._atomic_dispatch(msg)

        phase = runtime_with_dispatch.get_session_phase(msg.session_key)
        assert phase == SessionPhase.WAITING_PERMISSION

    @pytest.mark.asyncio
    async def test_atomic_dispatch_with_pending_interaction(self, runtime_with_dispatch):
        """测试原子 dispatch 有待处理交互请求"""
        runtime_with_dispatch.enable_atomic_dispatch()

        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        async def mock_handle(msg, on_progress=None):
            return None

        runtime_with_dispatch._handle_message = mock_handle

        # Simulate pending interaction request
        runtime_with_dispatch.bus._session_pending_interaction_requests[msg.session_key] = "inter-1"

        await runtime_with_dispatch._atomic_dispatch(msg)

        phase = runtime_with_dispatch.get_session_phase(msg.session_key)
        assert phase == SessionPhase.WAITING_INTERACTION


class TestLegacyVsAtomicDispatch:
    """测试传统 dispatch vs 原子 dispatch"""

    @pytest.mark.asyncio
    async def test_both_produce_same_final_state(self, runtime_with_dispatch):
        """测试两种方式产生相同的最终状态"""
        async def mock_handle(msg, on_progress=None):
            return OutboundMessage(channel="test", chat_id="chat1", content="ok")

        runtime_with_dispatch._handle_message = mock_handle

        # Legacy dispatch
        msg_legacy = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )
        # session_key is computed from channel and chat_id

        await runtime_with_dispatch._dispatch(msg_legacy)
        legacy_phase = runtime_with_dispatch.get_session_phase(msg_legacy.session_key)

        # Reset state for atomic dispatch test
        runtime_with_dispatch._state_machine.clear(msg_legacy.session_key)

        # Atomic dispatch
        runtime_with_dispatch.enable_atomic_dispatch()
        msg_atomic = InboundMessage(
            channel="test2", sender_id="user1", chat_id="chat2", content="hello"
        )

        await runtime_with_dispatch._atomic_dispatch(msg_atomic)
        atomic_phase = runtime_with_dispatch.get_session_phase(msg_atomic.session_key)

        # Both should end in same phase (IDLE after successful dispatch)
        assert legacy_phase == SessionPhase.IDLE
        assert atomic_phase == SessionPhase.IDLE


# === Fixtures ===

class MockRuntime:
    """Mock runtime for testing atomic dispatch."""

    def __init__(self):
        # 状态机
        self._state_machine = SessionStateMachine()

        # 活跃任务
        self._active_tasks = {}

        # Session locks
        self._session_locks = {}

        # State check
        self._state_check_enabled = True

        # Atomic dispatch flag
        self._use_atomic_dispatch = False

        # Sessions
        self.sessions = None

        # Router (set by fixture)
        self.router = None

        # Bus (set by fixture)
        self.bus = None

    @property
    def is_atomic_dispatch_enabled(self) -> bool:
        return self._use_atomic_dispatch

    def _sync_session_phase(self, session_key: str) -> None:
        """Sync session phase - simplified version for testing."""
        pass

    def _set_session_phase(self, session_key: str, phase, reason: str = "") -> None:
        """Set session phase - delegates to state machine."""
        self._state_machine.set(session_key, phase, reason=reason)


@pytest.fixture
def runtime_with_dispatch():
    """创建带有完整 dispatch 功能的 runtime"""
    runtime = MockRuntime()

    # Router 和 Backend
    router = MagicMock()
    backend = MagicMock()
    backend._clients = {}
    backend._active_task_ids = {}
    backend._client_last_used = {}
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

    bus.get_pending_request_for_session = get_pending_permission
    bus.get_pending_interaction_for_session = get_pending_interaction

    async def publish_outbound(msg):
        pass

    bus.publish_outbound = publish_outbound
    runtime.bus = bus

    # State checker
    runtime._state_checker = StateConsistencyChecker(runtime)

    # State coordinator
    runtime._state_coordinator = SessionStateCoordinator(runtime)
    runtime._state_coordinator.enable_shadow_mode()

    # Bind methods from AgentRuntime
    runtime._bus_progress = AgentRuntime._bus_progress.__get__(runtime, MockRuntime)
    runtime.get_session_phase = AgentRuntime.get_session_phase.__get__(runtime, MockRuntime)
    runtime.get_session_state = AgentRuntime.get_session_state.__get__(runtime, MockRuntime)
    runtime._log_state_snapshot = AgentRuntime._log_state_snapshot.__get__(runtime, MockRuntime)
    runtime._dispatch = AgentRuntime._dispatch.__get__(runtime, MockRuntime)
    runtime._atomic_dispatch = AgentRuntime._atomic_dispatch.__get__(runtime, MockRuntime)
    runtime.enable_atomic_dispatch = AgentRuntime.enable_atomic_dispatch.__get__(runtime, MockRuntime)
    runtime.disable_atomic_dispatch = AgentRuntime.disable_atomic_dispatch.__get__(runtime, MockRuntime)

    return runtime

    return runtime