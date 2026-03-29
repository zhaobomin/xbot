"""测试 dispatch 功能。"""

import pytest
import asyncio
from unittest.mock import MagicMock

from xbot.agent.runtime import AgentRuntime, SessionPhase, SessionStateMachine
from xbot.agent.state_checker import StateConsistencyChecker
from xbot.agent.state_coordinator import SessionStateCoordinator
from xbot.agent.session_store import SessionStore
from xbot.bus.events import InboundMessage, OutboundMessage


class TestDispatchStateManagement:
    """测试 dispatch 状态管理"""

    @pytest.mark.asyncio
    async def test_dispatch_sets_running_phase(self, runtime_with_dispatch):
        """测试 dispatch 设置 RUNNING 阶段"""
        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        async def mock_handle(msg, on_progress=None):
            return OutboundMessage(channel="test", chat_id="chat1", content="ok")

        runtime_with_dispatch._handle_message = mock_handle

        await runtime_with_dispatch._dispatch(msg)

        phase = runtime_with_dispatch.get_session_phase(msg.session_key)
        assert phase == SessionPhase.IDLE  # Should end in IDLE after dispatch

    @pytest.mark.asyncio
    async def test_dispatch_error_handling(self, runtime_with_dispatch):
        """测试 dispatch 错误处理"""
        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        async def mock_handle(msg, on_progress=None):
            raise ValueError("Test error")

        runtime_with_dispatch._handle_message = mock_handle
        runtime_with_dispatch.bus = None  # Avoid outbound publish

        await runtime_with_dispatch._dispatch(msg)

        # Check that the session state exists (no crash)
        state = runtime_with_dispatch.get_session_state(msg.session_key)
        assert state is not None

    @pytest.mark.asyncio
    async def test_dispatch_creates_lock(self, runtime_with_dispatch):
        """测试 dispatch 创建锁"""
        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        async def mock_handle(msg, on_progress=None):
            return None

        runtime_with_dispatch._handle_message = mock_handle

        await runtime_with_dispatch._dispatch(msg)

        # Lock should have been created
        assert runtime_with_dispatch._state_coordinator.has_lock(msg.session_key)

    @pytest.mark.asyncio
    async def test_dispatch_with_pending_permission(self, runtime_with_dispatch):
        """测试 dispatch 有待处理权限请求"""
        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        async def mock_handle(msg, on_progress=None):
            return None

        runtime_with_dispatch._handle_message = mock_handle

        # Simulate pending permission request
        runtime_with_dispatch.bus._session_pending_permission_requests[msg.session_key] = "perm-1"

        await runtime_with_dispatch._dispatch(msg)

        phase = runtime_with_dispatch.get_session_phase(msg.session_key)
        assert phase == SessionPhase.WAITING_PERMISSION

    @pytest.mark.asyncio
    async def test_dispatch_with_pending_interaction(self, runtime_with_dispatch):
        """测试 dispatch 有待处理交互请求"""
        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        async def mock_handle(msg, on_progress=None):
            return None

        runtime_with_dispatch._handle_message = mock_handle

        # Simulate pending interaction request
        runtime_with_dispatch.bus._session_pending_interaction_requests[msg.session_key] = "inter-1"

        await runtime_with_dispatch._dispatch(msg)

        phase = runtime_with_dispatch.get_session_phase(msg.session_key)
        assert phase == SessionPhase.WAITING_INTERACTION


# === Fixtures ===

class MockRuntime:
    """Mock runtime for testing dispatch."""

    def __init__(self):
        self._state_machine = SessionStateMachine()
        self._active_tasks = {}
        self._session_locks = {}
        self._state_check_enabled = True
        self.sessions = None
        self.router = None
        self.bus = None

    def _sync_session_phase(self, session_key: str) -> None:
        pass

    def _set_session_phase(self, session_key: str, phase, reason: str = "") -> None:
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

    # Session store
    session_store = SessionStore()
    runtime._session_store = session_store

    # State coordinator
    runtime._state_coordinator = SessionStateCoordinator(runtime, session_store)

    # Bind methods from AgentRuntime
    runtime._bus_progress = AgentRuntime._bus_progress.__get__(runtime, MockRuntime)
    runtime.get_session_phase = AgentRuntime.get_session_phase.__get__(runtime, MockRuntime)
    runtime.get_session_state = AgentRuntime.get_session_state.__get__(runtime, MockRuntime)
    runtime._log_state_snapshot = AgentRuntime._log_state_snapshot.__get__(runtime, MockRuntime)
    runtime._dispatch = AgentRuntime._dispatch.__get__(runtime, MockRuntime)

    return runtime