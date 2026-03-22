"""状态健康检查服务。

此模块提供定期健康检查功能，用于监控状态一致性并在发现问题时告警。

功能:
- 定期检查所有 session 的状态一致性
- 连续多次检测到问题时发出告警
- 提供健康检查结果查询

使用方式:
    from xbot.agent.state_health import StateHealthCheckService

    service = StateHealthCheckService(runtime, metrics_collector)
    await service.start()

    # 检查结果
    result = service.get_last_result()

    # 停止服务
    await service.stop()
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Callable
from loguru import logger

if TYPE_CHECKING:
    from xbot.agent.runtime import AgentRuntime
    from xbot.agent.state_metrics import StateMetricsCollector


class StateHealthCheckService:
    """状态健康检查服务。

    定期检查状态一致性，连续检测到问题时发出告警。

    Attributes:
        runtime: AgentRuntime 实例
        metrics_collector: StateMetricsCollector 实例
        check_interval: 检查间隔（秒）
        alert_threshold: 告警阈值（连续多少次检测到问题才告警）
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        metrics_collector: StateMetricsCollector,
        check_interval: float = 60.0,
        alert_threshold: int = 3,
    ):
        """初始化健康检查服务。

        Args:
            runtime: AgentRuntime 实例
            metrics_collector: StateMetricsCollector 实例
            check_interval: 检查间隔（秒），默认 60 秒
            alert_threshold: 告警阈值，默认连续 3 次
        """
        self._runtime = runtime
        self._metrics_collector = metrics_collector
        self._check_interval = check_interval
        self._alert_threshold = alert_threshold

        self._running = False
        self._task: asyncio.Task | None = None
        self._consecutive_issues = 0
        self._last_check_result: dict | None = None
        self._check_count = 0
        self._alert_count = 0

        # 告警回调
        self._on_alert: Callable[[dict], None] | None = None

    async def start(self) -> None:
        """启动健康检查服务。"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._check_loop())
        logger.info(
            f"State health check service started "
            f"(interval={self._check_interval}s, threshold={self._alert_threshold})"
        )

    async def stop(self) -> None:
        """停止健康检查服务。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("State health check service stopped")

    async def _check_loop(self) -> None:
        """检查循环。"""
        while self._running:
            try:
                await asyncio.sleep(self._check_interval)
                if not self._running:
                    break
                await self._perform_check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check failed: {e}")

    async def _perform_check(self) -> dict:
        """执行一次健康检查。

        Returns:
            检查结果字典
        """
        self._check_count += 1

        # 收集指标
        metrics = self._metrics_collector.collect()

        # 检查不一致
        inconsistent_sessions = self._runtime._state_checker.check_all_sessions()

        result = {
            "check_count": self._check_count,
            "timestamp": time.time(),
            "total_sessions": metrics.total_sessions,
            "inconsistent_count": len(inconsistent_sessions),
            "inconsistent_sessions": [
                {
                    "session_key": s.session_key,
                    "phase": s.runtime_phase,
                    "issues": s.inconsistencies,
                }
                for s in inconsistent_sessions[:10]  # 最多返回 10 个
            ],
            "metrics_summary": metrics.summary(),
            "consecutive_issues": self._consecutive_issues,
        }

        self._last_check_result = result

        # 更新连续问题计数
        if inconsistent_sessions:
            self._consecutive_issues += 1

            # 检查是否达到告警阈值
            if self._consecutive_issues >= self._alert_threshold:
                self._alert_count += 1
                result["alert_triggered"] = True

                alert_data = {
                    "alert_count": self._alert_count,
                    "consecutive_issues": self._consecutive_issues,
                    "inconsistent_count": len(inconsistent_sessions),
                    "inconsistent_sessions": result["inconsistent_sessions"],
                }

                logger.error(
                    f"State health alert: {len(inconsistent_sessions)} sessions inconsistent "
                    f"(consecutive: {self._consecutive_issues}, alert #{self._alert_count})"
                )

                # 调用告警回调
                if self._on_alert:
                    try:
                        self._on_alert(alert_data)
                    except Exception as e:
                        logger.debug(f"Alert callback error: {e}")
        else:
            # 重置连续问题计数
            if self._consecutive_issues > 0:
                logger.info(
                    f"State health recovered after {self._consecutive_issues} consecutive issues"
                )
            self._consecutive_issues = 0

        return result

    def get_last_result(self) -> dict | None:
        """获取上次检查结果。

        Returns:
            检查结果字典，如果没有检查过则返回 None
        """
        return self._last_check_result

    def get_stats(self) -> dict:
        """获取统计信息。

        Returns:
            统计信息字典
        """
        return {
            "running": self._running,
            "check_count": self._check_count,
            "alert_count": self._alert_count,
            "consecutive_issues": self._consecutive_issues,
            "check_interval": self._check_interval,
            "alert_threshold": self._alert_threshold,
        }

    def set_alert_callback(self, callback: Callable[[dict], None]) -> None:
        """设置告警回调函数。

        Args:
            callback: 告警回调函数，接收告警数据字典
        """
        self._on_alert = callback

    def reset_alert_state(self) -> None:
        """重置告警状态。

        用于手动确认问题已解决后重置状态。
        """
        self._consecutive_issues = 0
        logger.info("State health alert state reset")

    @property
    def is_running(self) -> bool:
        """检查服务是否正在运行。"""
        return self._running

    @property
    def has_issues(self) -> bool:
        """检查是否有未解决的问题。"""
        return self._consecutive_issues > 0