"""测试状态一致性检查器。"""

import pytest
import asyncio

from xbot.agent.state_checker import (
    StateConsistencyChecker,
    CONSISTENCY_RULES,
)
from xbot.agent.runtime import SessionPhase, AgentRuntime
from xbot.agent.state_snapshot import StateSnapshot


class TestConsistencyRules:
    """测试一致性规则定义"""

    def test_rules_have_required_fields(self):
        """验证所有规则都有必要字段"""
        for rule in CONSISTENCY_RULES:
            assert "id" in rule, f"Rule missing 'id': {rule}"
            assert "condition" in rule, f"Rule missing 'condition': {rule}"
            assert "requirement" in rule, f"Rule missing 'requirement': {rule}"
            assert "message" in rule, f"Rule missing 'message': {rule}"

    def test_rules_are_callable(self):
        """验证规则的条件和要求是可调用的"""
        for rule in CONSISTENCY_RULES:
            assert callable(rule["condition"]), f"Rule {rule['id']} condition not callable"
            assert callable(rule["requirement"]), f"Rule {rule['id']} requirement not callable"

    def test_rule_count(self):
        """验证规则数量"""
        assert len(CONSISTENCY_RULES) == 6


class TestStateConsistencyCheckerInit:
    """测试检查器初始化"""

    def test_init_with_runtime(self, mock_runtime):
        """测试用 runtime 初始化"""
        checker = StateConsistencyChecker(mock_runtime)

        assert checker._runtime is mock_runtime
        assert checker._rules == CONSISTENCY_RULES

    def test_get_rule_count(self, mock_runtime):
        """测试获取规则数量"""
        checker = StateConsistencyChecker(mock_runtime)

        assert checker.get_rule_count() == 6

    def test_get_rules_info(self, mock_runtime):
        """测试获取规则信息"""
        checker = StateConsistencyChecker(mock_runtime)
        info = checker.get_rules_info()

        assert len(info) == 6
        assert all("id" in r for r in info)
        assert all("message" in r for r in info)


class TestStateConsistencyCheckerCapture:
    """测试状态快照捕获"""

    def test_capture_snapshot_idle_state(self, mock_runtime):
        """测试捕获 IDLE 状态"""
        checker = StateConsistencyChecker(mock_runtime)
        snapshot = checker._capture_snapshot("test:session")

        assert snapshot.session_key == "test:session"
        assert snapshot.runtime_phase == "idle"
        assert snapshot.runtime_active_tasks == 0
        assert snapshot.backend_has_client == False

    def test_capture_snapshot_with_client(self, mock_runtime):
        """测试捕获有 client 的状态"""
        # 模拟有 client
        mock_runtime.router._backend._clients["test:session"] = "mock_client"

        checker = StateConsistencyChecker(mock_runtime)
        snapshot = checker._capture_snapshot("test:session")

        assert snapshot.backend_has_client == True

    async def test_capture_snapshot_with_active_task(self, mock_runtime):
        """测试捕获有活跃任务的状态"""
        # 模拟活跃任务
        task = asyncio.create_task(asyncio.sleep(100))
        mock_runtime._active_tasks["test:session"] = [task]

        try:
            checker = StateConsistencyChecker(mock_runtime)
            snapshot = checker._capture_snapshot("test:session")

            assert snapshot.runtime_active_tasks == 1
        finally:
            # 清理
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def test_capture_snapshot_with_pending_permission(self, mock_runtime):
        """测试捕获有待处理权限请求的状态"""
        # 模拟 pending permission
        mock_runtime.bus._pending_permission_requests["req-123"] = asyncio.Event()
        mock_runtime.bus._session_pending_permission_requests["test:session"] = "req-123"

        checker = StateConsistencyChecker(mock_runtime)
        snapshot = checker._capture_snapshot("test:session")

        assert snapshot.bus_pending_permission == True
        assert snapshot.bus_pending_permission_id == "req-123"


class TestStateConsistencyCheckerDetection:
    """测试不一致检测"""

    def test_detect_running_without_client(self, mock_runtime):
        """测试检测 RUNNING 状态但没有 client（此规则已移除）

        注意：running_requires_client 规则已移除
        原因：backend client 是懒加载的，RUNNING 状态可能还没有 client
        现在只检查 task_without_client: 有 backend_task_id 时必须有 client
        """
        # 设置 RUNNING 状态
        mock_runtime._state_machine.force_transition(
            "test:session", SessionPhase.RUNNING
        )

        checker = StateConsistencyChecker(mock_runtime)
        snapshot = checker.check_session("test:session")

        # RUNNING without client 现在是一致的状态（规则已移除）
        assert snapshot.is_consistent()

    def test_detect_task_without_client(self, mock_runtime):
        """测试检测有 backend_task_id 但没有 client"""
        mock_runtime._state_machine.force_transition(
            "test:session", SessionPhase.RUNNING
        )
        # 设置有 task_id 但没有 client
        mock_runtime.router._backend._active_task_ids["test:session"] = "task-123"

        checker = StateConsistencyChecker(mock_runtime)
        snapshot = checker.check_session("test:session")

        assert not snapshot.is_consistent()
        assert "Has backend task_id but no client" in snapshot.inconsistencies

    def test_detect_waiting_permission_without_request(self, mock_runtime):
        """测试检测 WAITING_PERMISSION 状态但没有 pending request"""
        mock_runtime._state_machine.force_transition(
            "test:session", SessionPhase.WAITING_PERMISSION
        )

        checker = StateConsistencyChecker(mock_runtime)
        snapshot = checker.check_session("test:session")

        assert not snapshot.is_consistent()
        assert "WAITING_PERMISSION but no pending request" in snapshot.inconsistencies

    def test_detect_waiting_interaction_without_request(self, mock_runtime):
        """测试检测 WAITING_INTERACTION 状态但没有 pending request"""
        mock_runtime._state_machine.force_transition(
            "test:session", SessionPhase.WAITING_INTERACTION
        )

        checker = StateConsistencyChecker(mock_runtime)
        snapshot = checker.check_session("test:session")

        assert not snapshot.is_consistent()
        assert "WAITING_INTERACTION but no pending request" in snapshot.inconsistencies

    async def test_detect_idle_with_active_tasks(self, mock_runtime):
        """测试检测 IDLE 状态但有活跃任务"""
        task = asyncio.create_task(asyncio.sleep(100))
        mock_runtime._active_tasks["test:session"] = [task]

        try:
            checker = StateConsistencyChecker(mock_runtime)
            snapshot = checker.check_session("test:session")

            assert not snapshot.is_consistent()
            assert "IDLE but has active tasks" in snapshot.inconsistencies
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def test_detect_client_without_lock(self, mock_runtime):
        """测试检测有 client 但没有锁"""
        mock_runtime.router._backend._clients["test:session"] = "mock_client"
        # 不设置 lock

        checker = StateConsistencyChecker(mock_runtime)
        snapshot = checker.check_session("test:session")

        assert not snapshot.is_consistent()
        assert "Has client but no session lock" in snapshot.inconsistencies

    def test_consistent_state_passes(self, mock_runtime):
        """测试一致状态通过检查"""
        # IDLE 状态，无 client，无任务 - 应该是一致的
        checker = StateConsistencyChecker(mock_runtime)
        snapshot = checker.check_session("test:session")

        assert snapshot.is_consistent()
        assert len(snapshot.inconsistencies) == 0


class TestStateConsistencyCheckerAllSessions:
    """测试检查所有 session"""

    def test_check_all_sessions_empty(self, mock_runtime):
        """测试没有 session 时"""
        checker = StateConsistencyChecker(mock_runtime)
        results = checker.check_all_sessions()

        assert len(results) == 0

    def test_check_all_sessions_with_inconsistency(self, mock_runtime):
        """测试有不一致的 session"""
        # 创建两个 session，其中一个不一致
        mock_runtime._state_machine.force_transition("test:1", SessionPhase.IDLE)
        mock_runtime._state_machine.force_transition("test:2", SessionPhase.WAITING_PERMISSION)  # 不一致：没有 pending request

        checker = StateConsistencyChecker(mock_runtime)
        results = checker.check_all_sessions()

        assert len(results) == 1
        assert results[0].session_key == "test:2"

    def test_get_all_session_keys(self, mock_runtime):
        """测试获取所有 session key"""
        # 添加一些 session
        mock_runtime._state_machine._states["state:1"] = mock_runtime._state_machine.get_state("state:1")
        mock_runtime._active_tasks["task:1"] = []
        mock_runtime._session_locks["lock:1"] = asyncio.Lock()
        mock_runtime.router._backend._clients["client:1"] = "mock"

        checker = StateConsistencyChecker(mock_runtime)
        keys = checker._get_all_session_keys()

        assert "state:1" in keys
        assert "task:1" in keys
        assert "lock:1" in keys
        assert "client:1" in keys


# === Fixtures ===

@pytest.fixture
def mock_runtime():
    """创建模拟的 AgentRuntime"""
    from unittest.mock import MagicMock, AsyncMock
    import asyncio

    runtime = MagicMock(spec=AgentRuntime)

    # 状态机
    from xbot.agent.runtime import SessionStateMachine
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

    return runtime