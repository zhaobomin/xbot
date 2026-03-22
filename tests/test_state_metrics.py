"""测试状态指标收集器。"""

import pytest

from xbot.agent.state_metrics import StateMetrics, StateMetricsCollector
from xbot.agent.runtime import SessionPhase


class TestStateMetrics:
    """测试 StateMetrics 数据结构"""

    def test_create_empty(self):
        """测试创建空指标"""
        metrics = StateMetrics()

        assert metrics.total_sessions == 0
        assert len(metrics.sessions_by_phase) == 0
        assert metrics.sessions_with_inconsistencies == 0

    def test_create_with_values(self):
        """测试用值创建"""
        metrics = StateMetrics(
            total_sessions=10,
            sessions_by_phase={"idle": 5, "running": 5},
            sessions_with_inconsistencies=2,
            active_backend_clients=5,
            pending_permissions=1,
            pending_interactions=2,
        )

        assert metrics.total_sessions == 10
        assert metrics.sessions_by_phase["idle"] == 5
        assert metrics.sessions_with_inconsistencies == 2

    def test_to_prometheus_format(self):
        """测试 Prometheus 格式输出"""
        metrics = StateMetrics(
            total_sessions=5,
            sessions_with_inconsistencies=1,
            active_backend_clients=3,
            pending_permissions=2,
            pending_interactions=1,
            sessions_by_phase={"idle": 2, "running": 3},
        )

        output = metrics.to_prometheus_format()

        assert "xbot_sessions_total 5" in output
        assert "xbot_sessions_inconsistent 1" in output
        assert "xbot_backend_clients_active 3" in output
        assert 'xbot_sessions_by_phase{phase="idle"} 2' in output
        assert 'xbot_sessions_by_phase{phase="running"} 3' in output

    def test_to_dict(self):
        """测试字典格式输出"""
        metrics = StateMetrics(
            total_sessions=3,
            sessions_by_phase={"idle": 3},
        )

        d = metrics.to_dict()

        assert d["total_sessions"] == 3
        assert d["sessions_by_phase"]["idle"] == 3
        assert "collected_at" in d

    def test_summary(self):
        """测试摘要输出"""
        metrics = StateMetrics(
            total_sessions=10,
            sessions_with_inconsistencies=2,
            active_backend_clients=5,
        )

        summary = metrics.summary()

        assert "sessions=10" in summary
        assert "inconsistent=2" in summary
        assert "clients=5" in summary


class TestStateMetricsCollector:
    """测试 StateMetricsCollector"""

    def test_init(self, mock_runtime):
        """测试初始化"""
        collector = StateMetricsCollector(mock_runtime)

        assert collector._runtime is mock_runtime
        assert collector._last_metrics is None

    def test_collect_empty(self, mock_runtime):
        """测试空状态收集"""
        collector = StateMetricsCollector(mock_runtime)
        metrics = collector.collect()

        assert metrics.total_sessions == 0
        assert metrics.active_backend_clients == 0

    def test_collect_with_sessions(self, mock_runtime):
        """测试有 session 的收集"""
        # 添加一些 session
        mock_runtime._state_machine.force_transition("test:1", SessionPhase.IDLE)
        mock_runtime._state_machine.force_transition("test:2", SessionPhase.RUNNING)
        mock_runtime.router._backend._clients["test:2"] = "mock_client"

        collector = StateMetricsCollector(mock_runtime)
        metrics = collector.collect()

        assert metrics.total_sessions == 2
        assert metrics.sessions_by_phase.get("idle", 0) == 1
        assert metrics.sessions_by_phase.get("running", 0) == 1
        assert metrics.active_backend_clients == 1

    def test_collect_with_pending_requests(self, mock_runtime):
        """测试有待处理请求的收集"""
        mock_runtime._state_machine.force_transition("test:1", SessionPhase.WAITING_PERMISSION)
        mock_runtime.bus._session_pending_permission_requests["test:1"] = "req-1"

        mock_runtime._state_machine.force_transition("test:2", SessionPhase.WAITING_INTERACTION)
        mock_runtime.bus._session_pending_interaction_requests["test:2"] = "int-1"

        collector = StateMetricsCollector(mock_runtime)
        metrics = collector.collect()

        assert metrics.pending_permissions == 1
        assert metrics.pending_interactions == 1

    def test_collect_with_inconsistencies(self, mock_runtime):
        """测试有不一致情况的收集"""
        # 创建不一致状态：RUNNING 但没有 client
        mock_runtime._state_machine.force_transition("test:1", SessionPhase.RUNNING)

        collector = StateMetricsCollector(mock_runtime)
        metrics = collector.collect()

        assert metrics.sessions_with_inconsistencies == 1

    def test_get_last_metrics(self, mock_runtime):
        """测试获取上次指标"""
        collector = StateMetricsCollector(mock_runtime)

        # 第一次收集
        assert collector.get_last_metrics() is None

        collector.collect()
        assert collector.get_last_metrics() is not None

    def test_is_healthy_no_issues(self, mock_runtime):
        """测试健康状态 - 无问题"""
        collector = StateMetricsCollector(mock_runtime)
        is_healthy, message = collector.is_healthy()

        assert is_healthy == True
        assert "consistent" in message.lower()

    def test_is_healthy_with_issues(self, mock_runtime):
        """测试健康状态 - 有问题"""
        # 创建不一致状态
        mock_runtime._state_machine.force_transition("test:1", SessionPhase.RUNNING)

        collector = StateMetricsCollector(mock_runtime)
        is_healthy, message = collector.is_healthy()

        assert is_healthy == False
        assert "inconsisten" in message.lower()


# === Fixtures ===

@pytest.fixture
def mock_runtime():
    """创建模拟的 AgentRuntime"""
    from unittest.mock import MagicMock
    import asyncio

    from xbot.agent.runtime import SessionStateMachine
    from xbot.agent.state_checker import StateConsistencyChecker

    runtime = MagicMock()

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

    return runtime