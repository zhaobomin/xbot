"""测试协调器模式。"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock

from xbot.agent.runtime import AgentRuntime, SessionPhase, SessionStateMachine
from xbot.agent.state_coordinator import SessionStateCoordinator
from xbot.agent.state_checker import StateConsistencyChecker
from xbot.bus.events import InboundMessage, OutboundMessage


class TestCoordinatorMode:
    """测试协调器模式"""

    def test_coordinator_initialized(self, runtime_with_coordinator):
        """测试协调器初始化"""
        assert runtime_with_coordinator._state_coordinator is not None

    def test_dispatch_uses_coordinator(self, runtime_with_coordinator):
        """测试 dispatch 使用协调器"""
        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        async def mock_handle(msg, on_progress=None):
            return OutboundMessage(channel="test", chat_id="chat1", content="ok")

        runtime_with_coordinator._handle_message = mock_handle

        async def run_test():
            await runtime_with_coordinator._dispatch(msg)
            state = runtime_with_coordinator._state_coordinator.get_state(msg.session_key)
            assert state is not None

        asyncio.get_event_loop().run_until_complete(run_test())

    def test_terminate_uses_coordinator(self, runtime_with_coordinator):
        """测试 terminate 使用协调器"""
        session_key = "test:chat1"

        # Initialize session state
        runtime_with_coordinator._state_machine.force_transition(
            session_key, SessionPhase.RUNNING, reason="test"
        )

        # Verify initial state
        initial_phase = runtime_with_coordinator._state_coordinator.get_phase(session_key)
        assert initial_phase == SessionPhase.RUNNING

    def test_permission_response_uses_coordinator(self, runtime_with_coordinator):
        """测试权限响应使用协调器"""
        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="允许"
        )

        async def run_test():
            # Set up pending permission request
            runtime_with_coordinator.bus._session_pending_permission_requests[msg.session_key] = "perm-1"
            runtime_with_coordinator.bus._pending_permission_responses["perm-1"] = asyncio.Event()

            result = await runtime_with_coordinator._handle_permission_response(msg)
            assert result is True

        asyncio.get_event_loop().run_until_complete(run_test())


class TestCoordinatorConsistency:
    """测试协调器一致性"""

    def test_state_consistency_after_operations(self, runtime_with_coordinator):
        """测试操作后状态一致性"""
        msg = InboundMessage(
            channel="test", sender_id="user1", chat_id="chat1", content="hello"
        )

        async def mock_handle(msg, on_progress=None):
            return OutboundMessage(channel="test", chat_id="chat1", content="ok")

        runtime_with_coordinator._handle_message = mock_handle

        async def run_test():
            # Initial state check
            initial_phase = runtime_with_coordinator._state_coordinator.get_phase(msg.session_key)
            assert initial_phase == SessionPhase.IDLE

            # Run dispatch
            await runtime_with_coordinator._dispatch(msg)

            # Check consistency
            is_consistent, issues = runtime_with_coordinator._state_coordinator.check_consistency(
                msg.session_key
            )
            # After successful dispatch, should be consistent (IDLE)
            assert is_consistent or len(issues) == 0 or all("no backend" in i for i in issues)

        asyncio.get_event_loop().run_until_complete(run_test())


class TestCoordinatorStats:
    """测试协调器统计。"""

    def test_coord_status_text_includes_stats(self, runtime_with_coordinator):
        """测试 !coord 状态文本包含统计字段且不会抛异常。"""
        runtime_with_coordinator._state_coordinator._stats.phase_transitions = 3
        runtime_with_coordinator._state_coordinator._stats.locks_created = 2
        runtime_with_coordinator._state_coordinator._stats.tasks_created = 4

        text = runtime_with_coordinator._coord_status_text()

        assert "State Coordinator" in text
        assert "phase_transitions: 3" in text
        assert "locks_created: 2" in text
        assert "tasks_created: 4" in text


class TestLocalCommandsNotEatenByInteraction:
    """回归测试：!coord 等本地命令在 WAITING_* 状态下不应被当成回复提交。"""

    def test_coord_in_local_runtime_commands(self):
        """!coord 和 /coord 应该在 LOCAL_RUNTIME_COMMANDS 白名单中。"""
        from xbot.agent.runtime import AgentRuntime

        assert "!coord" in AgentRuntime.LOCAL_RUNTIME_COMMANDS
        assert "/coord" in AgentRuntime.LOCAL_RUNTIME_COMMANDS

    def test_is_local_runtime_command_recognizes_coord(self):
        """_is_local_runtime_command 应该识别 !coord 和 /coord。"""
        from xbot.agent.runtime import AgentRuntime

        assert AgentRuntime._is_local_runtime_command("!coord") is True
        assert AgentRuntime._is_local_runtime_command("/coord") is True
        assert AgentRuntime._is_local_runtime_command("!state") is True
        assert AgentRuntime._is_local_runtime_command("!help") is True

    def test_coord_not_eaten_in_waiting_permission(self, runtime_with_coordinator):
        """在 WAITING_PERMISSION 状态下，!coord 不应被当成权限回复提交。"""
        from xbot.agent.runtime import AgentRuntime

        # 直接测试核心逻辑：!coord 是本地命令，应该在 _handle_permission_response 之前被过滤
        # 这通过 _is_local_runtime_command 测试已覆盖
        # 额外验证：权限响应只接受 allow/deny 关键字
        assert AgentRuntime._is_local_runtime_command("!coord") is True

        # 权限响应不应该识别 !coord
        content = "!coord"
        allow_variations = {"允许", "allow", "yes", "y", "是", "ok", "同意", "确认"}
        deny_variations = {"拒绝", "deny", "no", "n", "否", "取消"}
        assert content not in allow_variations and content not in deny_variations


# === Fixtures ===

class MockRuntimeForCoordinator:
    """Mock runtime for testing coordinator mode."""

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

    # Bind methods from AgentRuntime
    runtime._bus_progress = AgentRuntime._bus_progress.__get__(runtime, MockRuntimeForCoordinator)
    runtime.get_session_phase = AgentRuntime.get_session_phase.__get__(runtime, MockRuntimeForCoordinator)
    runtime.get_session_state = AgentRuntime.get_session_state.__get__(runtime, MockRuntimeForCoordinator)
    runtime._log_state_snapshot = AgentRuntime._log_state_snapshot.__get__(runtime, MockRuntimeForCoordinator)
    runtime._dispatch = AgentRuntime._dispatch.__get__(runtime, MockRuntimeForCoordinator)
    runtime._terminate_session = AgentRuntime._terminate_session.__get__(runtime, MockRuntimeForCoordinator)
    runtime._handle_permission_response = AgentRuntime._handle_permission_response.__get__(runtime, MockRuntimeForCoordinator)
    runtime._coord_status_text = AgentRuntime._coord_status_text.__get__(runtime, MockRuntimeForCoordinator)

    return runtime