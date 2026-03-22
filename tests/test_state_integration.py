"""状态管理系统集成测试。

测试各组件协同工作的端到端场景。
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from xbot.agent.runtime import AgentRuntime, SessionPhase, SessionStateMachine
from xbot.agent.state_checker import StateConsistencyChecker, CONSISTENCY_RULES
from xbot.agent.state_snapshot import StateSnapshot
from xbot.agent.state_metrics import StateMetricsCollector, StateMetrics
from xbot.agent.state_coordinator import SessionStateCoordinator
from xbot.agent.state_transaction import StateTransaction, TransactionState


class TestStateManagementIntegration:
    """状态管理系统集成测试"""

    def test_full_stack_initialization(self, real_runtime):
        """测试完整初始化"""
        # Runtime 有所有组件
        assert hasattr(real_runtime, "_state_machine")
        assert hasattr(real_runtime, "_state_checker")
        assert hasattr(real_runtime, "_state_coordinator")

        # Coordinator 在 shadow mode
        assert real_runtime._state_coordinator.is_shadow_mode is True

    def test_state_flow_through_components(self, real_runtime):
        """测试状态流经各组件"""
        session_key = "test:integration:1"

        # 1. 通过 coordinator 设置状态
        real_runtime._state_coordinator.force_transition(
            session_key, SessionPhase.RUNNING, reason="test"
        )

        # 2. 通过 coordinator 读取状态
        phase = real_runtime._state_coordinator.get_phase(session_key)
        assert phase == SessionPhase.RUNNING

        # 3. 通过 runtime 的公共 API 读取
        state_str = real_runtime.get_session_state(session_key)
        assert state_str == "running"

        # 4. 检查一致性
        is_consistent, issues = real_runtime._state_coordinator.check_consistency(
            session_key
        )
        # RUNNING 但没有 client，应该不一致
        assert is_consistent is False
        assert any("client" in i.lower() for i in issues)

    def test_metrics_collection_integration(self, real_runtime):
        """测试指标收集集成"""
        # 创建几个 session
        real_runtime._state_coordinator.force_transition(
            "test:1", SessionPhase.IDLE, reason="test"
        )
        real_runtime._state_coordinator.force_transition(
            "test:2", SessionPhase.RUNNING, reason="test"
        )

        # 收集指标
        collector = StateMetricsCollector(real_runtime)
        metrics = collector.collect()

        assert metrics.total_sessions >= 2
        assert "idle" in metrics.sessions_by_phase
        assert "running" in metrics.sessions_by_phase

    @pytest.mark.asyncio
    async def test_transaction_integration(self, real_runtime):
        """测试事务集成"""
        session_key = "test:tx:1"

        # 使用事务设置多个状态
        async with real_runtime._state_coordinator.transaction(session_key) as tx:
            tx.set_phase(SessionPhase.RUNNING, reason="transaction_test")
            tx.acquire_lock()

        # 验证状态已更改
        phase = real_runtime._state_coordinator.get_phase(session_key)
        assert phase == SessionPhase.RUNNING

        # 验证锁已创建
        assert real_runtime._state_coordinator.has_lock(session_key)

    @pytest.mark.asyncio
    async def test_transaction_rollback_integration(self, real_runtime):
        """测试事务回滚集成"""
        session_key = "test:tx:rollback:1"

        # 先设置一个已知状态
        real_runtime._state_coordinator.force_transition(
            session_key, SessionPhase.IDLE, reason="initial"
        )

        # 开始事务并触发异常
        try:
            async with real_runtime._state_coordinator.transaction(session_key) as tx:
                tx.set_phase(SessionPhase.RUNNING, reason="will_rollback")
                raise ValueError("Intentional error for testing")
        except ValueError:
            pass

        # 验证状态已回滚
        phase = real_runtime._state_coordinator.get_phase(session_key)
        # 回滚会恢复到 IDLE
        assert phase == SessionPhase.IDLE


class TestCoordinatorRuntimeSync:
    """测试 Coordinator 与 Runtime 同步"""

    def test_coordinator_reflects_runtime_changes(self, real_runtime):
        """测试 Coordinator 反映 Runtime 变更"""
        session_key = "test:sync:1"

        # 直接通过状态机变更
        real_runtime._state_machine.force_transition(
            session_key, SessionPhase.RUNNING, reason="direct"
        )

        # Coordinator 应该能看到
        phase = real_runtime._state_coordinator.get_phase(session_key)
        assert phase == SessionPhase.RUNNING

    def test_runtime_reflects_coordinator_changes(self, real_runtime):
        """测试 Runtime 反映 Coordinator 变更"""
        session_key = "test:sync:2"

        # 通过 Coordinator 变更
        real_runtime._state_coordinator.force_transition(
            session_key, SessionPhase.WAITING_PERMISSION, reason="via_coordinator"
        )

        # Runtime 状态机应该能看到
        phase = real_runtime._state_machine.get_phase(session_key)
        assert phase == SessionPhase.WAITING_PERMISSION


class TestConsistencyCheckerIntegration:
    """测试一致性检查器集成"""

    def test_checker_detects_inconsistencies(self, real_runtime):
        """测试检查器检测不一致"""
        session_key = "test:consistency:1"

        # 设置一个不一致状态：RUNNING 但没有 backend client
        real_runtime._state_coordinator.force_transition(
            session_key, SessionPhase.RUNNING, reason="test"
        )

        # 检查
        snapshot = real_runtime._state_checker.check_session(session_key)

        assert not snapshot.is_consistent()
        assert len(snapshot.inconsistencies) > 0
        assert snapshot.runtime_phase == "running"
        assert snapshot.backend_has_client is False

    def test_checker_passes_consistent_state(self, real_runtime):
        """测试检查器通过一致状态"""
        session_key = "test:consistency:2"

        # IDLE 状态是一致的
        real_runtime._state_coordinator.force_transition(
            session_key, SessionPhase.IDLE, reason="test"
        )

        snapshot = real_runtime._state_checker.check_session(session_key)
        assert snapshot.is_consistent()

    def test_all_rules_have_valid_checks(self, real_runtime):
        """测试所有规则都能正确执行"""
        session_key = "test:rules:1"

        # 为每个规则创建测试状态
        for rule in CONSISTENCY_RULES:
            snapshot = real_runtime._state_checker._capture_snapshot(session_key)

            # 规则条件应该能执行
            try:
                condition = rule["condition"](snapshot)
                requirement = rule["requirement"](snapshot)
                # 不应该抛出异常
            except Exception as e:
                pytest.fail(f"Rule {rule['id']} raised exception: {e}")


class TestHealthCheckIntegration:
    """测试健康检查服务集成"""

    @pytest.mark.asyncio
    async def test_health_service_with_real_components(self, real_runtime):
        """测试健康服务与真实组件"""
        from xbot.agent.state_health import StateHealthCheckService

        # 创建指标收集器
        metrics_collector = StateMetricsCollector(real_runtime)

        # 创建健康检查服务
        service = StateHealthCheckService(
            real_runtime, metrics_collector, check_interval=0.1, alert_threshold=2
        )

        # 启动服务
        await service.start()

        # 创建不一致状态
        real_runtime._state_coordinator.force_transition(
            "test:health:1", SessionPhase.RUNNING, reason="test"
        )

        # 等待检查执行
        await asyncio.sleep(0.2)

        # 获取结果
        result = service.get_last_result()
        assert result is not None
        assert result["inconsistent_count"] >= 1

        # 停止服务
        await service.stop()

    @pytest.mark.asyncio
    async def test_health_alert_callback(self, real_runtime):
        """测试健康告警回调"""
        from xbot.agent.state_health import StateHealthCheckService

        metrics_collector = StateMetricsCollector(real_runtime)
        service = StateHealthCheckService(
            real_runtime, metrics_collector, check_interval=0.05, alert_threshold=1
        )

        # 设置回调
        alert_data = []

        def on_alert(data):
            alert_data.append(data)

        service.set_alert_callback(on_alert)

        # 启动并创建不一致
        await service.start()
        real_runtime._state_coordinator.force_transition(
            "test:alert:1", SessionPhase.RUNNING, reason="test"
        )

        await asyncio.sleep(0.15)

        # 应该触发告警
        assert len(alert_data) >= 1
        assert alert_data[0]["inconsistent_count"] >= 1

        await service.stop()


class TestMetricsExport:
    """测试指标导出"""

    def test_prometheus_format_export(self, real_runtime):
        """测试 Prometheus 格式导出"""
        # 创建一些状态
        real_runtime._state_coordinator.force_transition(
            "test:prom:1", SessionPhase.IDLE, reason="test"
        )
        real_runtime._state_coordinator.force_transition(
            "test:prom:2", SessionPhase.RUNNING, reason="test"
        )

        # 收集并导出
        collector = StateMetricsCollector(real_runtime)
        metrics = collector.collect()
        prom_output = metrics.to_prometheus_format()

        # 验证格式
        assert "xbot_sessions_total" in prom_output
        assert "xbot_sessions_inconsistent" in prom_output
        assert "xbot_sessions_by_phase" in prom_output

    def test_dict_export(self, real_runtime):
        """测试字典格式导出"""
        collector = StateMetricsCollector(real_runtime)
        metrics = collector.collect()
        d = metrics.to_dict()

        assert "total_sessions" in d
        assert "sessions_by_phase" in d
        assert "collected_at" in d


class TestShadowModeBehavior:
    """测试 Shadow Mode 行为"""

    def test_shadow_mode_logs_but_doesnt_change(self, real_runtime):
        """测试 Shadow Mode 只记录不改变行为"""
        session_key = "test:shadow:1"

        # Coordinator 在 shadow mode
        assert real_runtime._state_coordinator.is_shadow_mode is True

        # 操作应该正常执行（shadow mode 只记录日志）
        real_runtime._state_coordinator.force_transition(
            session_key, SessionPhase.RUNNING, reason="test"
        )

        # 状态应该已更改
        phase = real_runtime._state_coordinator.get_phase(session_key)
        assert phase == SessionPhase.RUNNING

        # 统计应该记录
        stats = real_runtime._state_coordinator.get_stats()
        assert stats.phase_transitions >= 1

    def test_shadow_mode_can_be_disabled(self, real_runtime):
        """测试可以禁用 Shadow Mode"""
        real_runtime._state_coordinator.disable_shadow_mode()
        assert real_runtime._state_coordinator.is_shadow_mode is False

        real_runtime._state_coordinator.enable_shadow_mode()
        assert real_runtime._state_coordinator.is_shadow_mode is True


class TestEndToEndScenarios:
    """端到端场景测试"""

    @pytest.mark.asyncio
    async def test_session_lifecycle(self, real_runtime):
        """测试完整会话生命周期"""
        session_key = "test:lifecycle:1"

        # 1. 初始状态
        phase = real_runtime.get_session_phase(session_key)
        assert phase == SessionPhase.IDLE

        # 2. 开始运行
        async with real_runtime._state_coordinator.transaction(session_key) as tx:
            tx.set_phase(SessionPhase.RUNNING, reason="start")

        assert real_runtime.get_session_phase(session_key) == SessionPhase.RUNNING

        # 3. 等待权限
        real_runtime._state_coordinator.force_transition(
            session_key, SessionPhase.WAITING_PERMISSION, reason="awaiting_permission"
        )
        assert real_runtime.get_session_phase(session_key) == SessionPhase.WAITING_PERMISSION

        # 4. 恢复运行
        real_runtime._state_coordinator.force_transition(
            session_key, SessionPhase.RUNNING, reason="permission_granted"
        )
        assert real_runtime.get_session_phase(session_key) == SessionPhase.RUNNING

        # 5. 结束
        real_runtime._state_coordinator.force_transition(
            session_key, SessionPhase.IDLE, reason="completed"
        )
        assert real_runtime.get_session_phase(session_key) == SessionPhase.IDLE

    @pytest.mark.asyncio
    async def test_error_recovery_scenario(self, real_runtime):
        """测试错误恢复场景"""
        session_key = "test:error:1"

        # 进入错误状态
        real_runtime._state_coordinator.force_transition(
            session_key, SessionPhase.ERROR, reason="simulated_error",
        )

        # 从错误恢复
        real_runtime._state_coordinator.force_transition(
            session_key, SessionPhase.IDLE, reason="recovery"
        )

        assert real_runtime.get_session_phase(session_key) == SessionPhase.IDLE

    @pytest.mark.asyncio
    async def test_concurrent_sessions(self, real_runtime):
        """测试并发会话"""
        sessions = [f"test:concurrent:{i}" for i in range(5)]

        # 并发设置状态
        for session_key in sessions:
            real_runtime._state_coordinator.force_transition(
                session_key, SessionPhase.RUNNING, reason="concurrent_test"
            )

        # 验证所有会话状态正确
        for session_key in sessions:
            phase = real_runtime.get_session_phase(session_key)
            assert phase == SessionPhase.RUNNING

        # 收集指标
        collector = StateMetricsCollector(real_runtime)
        metrics = collector.collect()
        assert metrics.total_sessions >= 5


# === Fixtures ===

@pytest.fixture
def real_runtime():
    """创建真实的 AgentRuntime 组件（不依赖完整 runtime）"""
    from unittest.mock import MagicMock

    # 创建一个最小化的 runtime 对象
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
    runtime._state_coordinator.enable_shadow_mode()

    # 绑定方法
    runtime.get_session_state = AgentRuntime.get_session_state.__get__(runtime, AgentRuntime)
    runtime.get_session_phase = AgentRuntime.get_session_phase.__get__(runtime, AgentRuntime)

    return runtime