"""测试状态健康检查服务。"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from xbot.agent.state_health import StateHealthCheckService


class TestStateHealthCheckServiceInit:
    """测试服务初始化"""

    def test_init_default_params(self, mock_runtime, mock_metrics_collector):
        """测试默认参数初始化"""
        service = StateHealthCheckService(mock_runtime, mock_metrics_collector)

        assert service._runtime is mock_runtime
        assert service._metrics_collector is mock_metrics_collector
        assert service._check_interval == 60.0
        assert service._alert_threshold == 3
        assert service._running is False
        assert service._task is None
        assert service._consecutive_issues == 0
        assert service._last_check_result is None

    def test_init_custom_params(self, mock_runtime, mock_metrics_collector):
        """测试自定义参数初始化"""
        service = StateHealthCheckService(
            mock_runtime,
            mock_metrics_collector,
            check_interval=30.0,
            alert_threshold=5,
        )

        assert service._check_interval == 30.0
        assert service._alert_threshold == 5


class TestStateHealthCheckServiceLifecycle:
    """测试服务生命周期"""

    @pytest.mark.asyncio
    async def test_start(self, mock_runtime, mock_metrics_collector):
        """测试启动服务"""
        service = StateHealthCheckService(
            mock_runtime, mock_metrics_collector, check_interval=0.1
        )

        assert service.is_running is False

        await service.start()
        assert service.is_running is True
        assert service._task is not None

        # 清理
        await service.stop()

    @pytest.mark.asyncio
    async def test_start_idempotent(self, mock_runtime, mock_metrics_collector):
        """测试重复启动是幂等的"""
        service = StateHealthCheckService(
            mock_runtime, mock_metrics_collector, check_interval=0.1
        )

        await service.start()
        task = service._task
        await service.start()  # 再次启动
        assert service._task is task  # 任务不应该改变

        await service.stop()

    @pytest.mark.asyncio
    async def test_stop(self, mock_runtime, mock_metrics_collector):
        """测试停止服务"""
        service = StateHealthCheckService(
            mock_runtime, mock_metrics_collector, check_interval=0.1
        )

        await service.start()
        assert service.is_running is True

        await service.stop()
        assert service.is_running is False
        # 任务被取消，但对象仍然存在
        assert service._task is not None
        assert service._task.cancelled()

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, mock_runtime, mock_metrics_collector):
        """测试停止未运行的服务"""
        service = StateHealthCheckService(mock_runtime, mock_metrics_collector)

        # 应该不会抛出异常
        await service.stop()
        assert service.is_running is False


class TestStateHealthCheckServiceCheck:
    """测试健康检查"""

    @pytest.mark.asyncio
    async def test_perform_check_no_issues(self, mock_runtime, mock_metrics_collector):
        """测试检查 - 无问题"""
        # 配置 mock
        mock_metrics_collector.collect.return_value = MagicMock(
            total_sessions=5,
            summary=lambda: "StateMetrics(sessions=5, inconsistent=0, clients=3)",
        )
        mock_runtime._state_checker.check_all_sessions.return_value = []

        service = StateHealthCheckService(mock_runtime, mock_metrics_collector)
        result = await service._perform_check()

        assert result["check_count"] == 1
        assert result["total_sessions"] == 5
        assert result["inconsistent_count"] == 0
        assert result["consecutive_issues"] == 0
        assert "alert_triggered" not in result

    @pytest.mark.asyncio
    async def test_perform_check_with_issues(self, mock_runtime, mock_metrics_collector):
        """测试检查 - 有问题"""
        # 配置 mock
        mock_metrics_collector.collect.return_value = MagicMock(
            total_sessions=5,
            summary=lambda: "StateMetrics(sessions=5, inconsistent=1, clients=3)",
        )

        # 创建一个不一致的 session
        inconsistent_session = MagicMock()
        inconsistent_session.session_key = "test:1"
        inconsistent_session.runtime_phase = "running"
        inconsistent_session.inconsistencies = ["RUNNING but no backend client"]
        mock_runtime._state_checker.check_all_sessions.return_value = [inconsistent_session]

        service = StateHealthCheckService(mock_runtime, mock_metrics_collector)
        result = await service._perform_check()

        assert result["inconsistent_count"] == 1
        # result["consecutive_issues"] 是检查前的快照值（0）
        # 检查后 service._consecutive_issues 才增加
        assert service._consecutive_issues == 1
        assert len(result["inconsistent_sessions"]) == 1
        assert "alert_triggered" not in result  # 还没达到阈值

    @pytest.mark.asyncio
    async def test_perform_check_triggers_alert(
        self, mock_runtime, mock_metrics_collector
    ):
        """测试检查 - 触发告警"""
        mock_metrics_collector.collect.return_value = MagicMock(
            total_sessions=5,
            summary=lambda: "StateMetrics(sessions=5, inconsistent=1, clients=3)",
        )

        inconsistent_session = MagicMock()
        inconsistent_session.session_key = "test:1"
        inconsistent_session.runtime_phase = "running"
        inconsistent_session.inconsistencies = ["RUNNING but no backend client"]
        mock_runtime._state_checker.check_all_sessions.return_value = [inconsistent_session]

        service = StateHealthCheckService(
            mock_runtime, mock_metrics_collector, alert_threshold=2
        )

        # 第一次检查
        result1 = await service._perform_check()
        assert service._consecutive_issues == 1
        assert "alert_triggered" not in result1

        # 第二次检查 - 触发告警
        result2 = await service._perform_check()
        assert service._consecutive_issues == 2
        assert result2["alert_triggered"] is True
        assert service._alert_count == 1

    @pytest.mark.asyncio
    async def test_perform_check_resets_on_recovery(
        self, mock_runtime, mock_metrics_collector
    ):
        """测试检查 - 恢复后重置计数"""
        mock_metrics_collector.collect.return_value = MagicMock(
            total_sessions=5,
            summary=lambda: "StateMetrics(sessions=5, inconsistent=0, clients=3)",
        )

        service = StateHealthCheckService(mock_runtime, mock_metrics_collector)

        # 先设置一些连续问题
        service._consecutive_issues = 3

        # 检查 - 无问题
        result = await service._perform_check()

        assert result["inconsistent_count"] == 0
        assert service._consecutive_issues == 0

    @pytest.mark.asyncio
    async def test_alert_callback(self, mock_runtime, mock_metrics_collector):
        """测试告警回调"""
        mock_metrics_collector.collect.return_value = MagicMock(
            total_sessions=5,
            summary=lambda: "StateMetrics(sessions=5, inconsistent=1, clients=3)",
        )

        inconsistent_session = MagicMock()
        inconsistent_session.session_key = "test:1"
        inconsistent_session.runtime_phase = "running"
        inconsistent_session.inconsistencies = ["RUNNING but no backend client"]
        mock_runtime._state_checker.check_all_sessions.return_value = [inconsistent_session]

        # 创建回调
        callback_calls = []

        def alert_callback(data):
            callback_calls.append(data)

        service = StateHealthCheckService(
            mock_runtime, mock_metrics_collector, alert_threshold=1
        )
        service.set_alert_callback(alert_callback)

        # 触发告警
        await service._perform_check()

        assert len(callback_calls) == 1
        assert callback_calls[0]["alert_count"] == 1
        assert callback_calls[0]["consecutive_issues"] == 1
        assert callback_calls[0]["inconsistent_count"] == 1


class TestStateHealthCheckServiceGetters:
    """测试获取方法"""

    def test_get_last_result_none(self, mock_runtime, mock_metrics_collector):
        """测试获取上次结果 - 无结果"""
        service = StateHealthCheckService(mock_runtime, mock_metrics_collector)
        assert service.get_last_result() is None

    @pytest.mark.asyncio
    async def test_get_last_result(self, mock_runtime, mock_metrics_collector):
        """测试获取上次结果"""
        mock_metrics_collector.collect.return_value = MagicMock(
            total_sessions=3,
            summary=lambda: "StateMetrics(sessions=3, inconsistent=0, clients=2)",
        )
        mock_runtime._state_checker.check_all_sessions.return_value = []

        service = StateHealthCheckService(mock_runtime, mock_metrics_collector)
        await service._perform_check()

        result = service.get_last_result()
        assert result is not None
        assert result["total_sessions"] == 3

    def test_get_stats(self, mock_runtime, mock_metrics_collector):
        """测试获取统计信息"""
        service = StateHealthCheckService(
            mock_runtime,
            mock_metrics_collector,
            check_interval=30.0,
            alert_threshold=5,
        )
        service._check_count = 10
        service._alert_count = 2
        service._consecutive_issues = 1

        stats = service.get_stats()

        assert stats["running"] is False
        assert stats["check_count"] == 10
        assert stats["alert_count"] == 2
        assert stats["consecutive_issues"] == 1
        assert stats["check_interval"] == 30.0
        assert stats["alert_threshold"] == 5

    def test_has_issues(self, mock_runtime, mock_metrics_collector):
        """测试是否有问题"""
        service = StateHealthCheckService(mock_runtime, mock_metrics_collector)

        assert service.has_issues is False

        service._consecutive_issues = 1
        assert service.has_issues is True


class TestStateHealthCheckServiceReset:
    """测试重置功能"""

    def test_reset_alert_state(self, mock_runtime, mock_metrics_collector):
        """测试重置告警状态"""
        service = StateHealthCheckService(mock_runtime, mock_metrics_collector)
        service._consecutive_issues = 5

        service.reset_alert_state()

        assert service._consecutive_issues == 0


class TestStateHealthCheckServiceCheckLoop:
    """测试检查循环"""

    @pytest.mark.asyncio
    async def test_check_loop_runs_periodically(
        self, mock_runtime, mock_metrics_collector
    ):
        """测试检查循环定期运行"""
        mock_metrics_collector.collect.return_value = MagicMock(
            total_sessions=1,
            summary=lambda: "StateMetrics(sessions=1, inconsistent=0, clients=1)",
        )
        mock_runtime._state_checker.check_all_sessions.return_value = []

        service = StateHealthCheckService(
            mock_runtime, mock_metrics_collector, check_interval=0.05
        )

        await service.start()

        # 等待足够时间让检查运行几次
        await asyncio.sleep(0.2)

        await service.stop()

        # 应该至少检查了 2 次
        assert service._check_count >= 2

    @pytest.mark.asyncio
    async def test_check_loop_handles_exception(
        self, mock_runtime, mock_metrics_collector
    ):
        """测试检查循环处理异常"""
        # 让 collect 抛出异常
        mock_metrics_collector.collect.side_effect = RuntimeError("Test error")

        service = StateHealthCheckService(
            mock_runtime, mock_metrics_collector, check_interval=0.05
        )

        await service.start()
        await asyncio.sleep(0.15)
        await service.stop()

        # 服务应该仍然在运行（异常被捕获）
        assert service._check_count >= 1  # 至少尝试了一次


# === Fixtures ===

@pytest.fixture
def mock_runtime():
    """创建模拟的 AgentRuntime"""
    runtime = MagicMock()

    # State checker
    state_checker = MagicMock()
    state_checker.check_all_sessions = MagicMock(return_value=[])
    runtime._state_checker = state_checker

    return runtime


@pytest.fixture
def mock_metrics_collector():
    """创建模拟的 StateMetricsCollector"""
    collector = MagicMock()
    collector.collect = MagicMock(
        return_value=MagicMock(
            total_sessions=0,
            summary=lambda: "StateMetrics(sessions=0, inconsistent=0, clients=0)",
        )
    )
    return collector