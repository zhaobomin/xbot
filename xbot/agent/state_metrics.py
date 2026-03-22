"""状态指标收集器。

此模块提供状态相关的指标收集功能，用于监控和告警。

收集的指标:
- total_sessions: 总会话数
- sessions_by_phase: 各阶段的会话数
- sessions_with_inconsistencies: 有不一致问题的会话数
- active_backend_clients: 活跃的 backend 客户端数
- pending_permissions: 待处理的权限请求数
- pending_interactions: 待处理的交互请求数

使用方式:
    from xbot.agent.state_metrics import StateMetricsCollector

    collector = StateMetricsCollector(runtime)
    metrics = collector.collect()
    print(metrics.to_prometheus_format())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
import time

if TYPE_CHECKING:
    from xbot.agent.runtime import AgentRuntime


@dataclass
class StateMetrics:
    """状态指标数据结构。

    Attributes:
        total_sessions: 总会话数
        sessions_by_phase: 各阶段的会话数
        sessions_with_inconsistencies: 有不一致问题的会话数
        active_backend_clients: 活跃的 backend 客户端数
        pending_permissions: 待处理的权限请求数
        pending_interactions: 待处理的交互请求数
        collected_at: 收集时间戳
    """

    # 会话统计
    total_sessions: int = 0
    sessions_by_phase: dict[str, int] = field(default_factory=dict)

    # 一致性统计
    sessions_with_inconsistencies: int = 0

    # 资源统计
    active_backend_clients: int = 0
    pending_permissions: int = 0
    pending_interactions: int = 0

    # 时间戳
    collected_at: float = field(default_factory=time.time)

    def to_prometheus_format(self) -> str:
        """转换为 Prometheus 格式输出。

        Returns:
            Prometheus 格式的指标字符串
        """
        lines = [
            f"xbot_sessions_total {self.total_sessions}",
            f"xbot_sessions_inconsistent {self.sessions_with_inconsistencies}",
            f"xbot_backend_clients_active {self.active_backend_clients}",
            f"xbot_permissions_pending {self.pending_permissions}",
            f"xbot_interactions_pending {self.pending_interactions}",
        ]

        for phase, count in self.sessions_by_phase.items():
            lines.append(f'xbot_sessions_by_phase{{phase="{phase}"}} {count}')

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """转换为字典格式。

        Returns:
            包含所有指标的字典
        """
        return {
            "total_sessions": self.total_sessions,
            "sessions_by_phase": dict(self.sessions_by_phase),
            "sessions_with_inconsistencies": self.sessions_with_inconsistencies,
            "active_backend_clients": self.active_backend_clients,
            "pending_permissions": self.pending_permissions,
            "pending_interactions": self.pending_interactions,
            "collected_at": self.collected_at,
        }

    def summary(self) -> str:
        """生成指标摘要。

        Returns:
            指标摘要字符串
        """
        return (
            f"StateMetrics("
            f"sessions={self.total_sessions}, "
            f"inconsistent={self.sessions_with_inconsistencies}, "
            f"clients={self.active_backend_clients})"
        )


class StateMetricsCollector:
    """状态指标收集器。

    用于收集 AgentRuntime 中各组件的指标信息。

    Attributes:
        runtime: AgentRuntime 实例
        _last_metrics: 上次收集的指标
    """

    def __init__(self, runtime: AgentRuntime):
        """初始化收集器。

        Args:
            runtime: AgentRuntime 实例
        """
        self._runtime = runtime
        self._last_metrics: StateMetrics | None = None

    def collect(self) -> StateMetrics:
        """收集当前指标。

        遍历所有 session，统计各项指标。

        Returns:
            StateMetrics 包含当前指标
        """
        from xbot.agent.runtime import SessionPhase

        # 初始化 phase 计数
        sessions_by_phase = {phase.value: 0 for phase in SessionPhase}

        total_sessions = 0
        sessions_with_inconsistencies = 0
        active_backend_clients = 0
        pending_permissions = 0
        pending_interactions = 0

        # 获取所有 session key
        session_keys = self._get_all_session_keys()

        for session_key in session_keys:
            total_sessions += 1

            # 获取状态快照
            snapshot = self._runtime._state_checker.check_session(session_key)

            # 统计 phase
            phase = snapshot.runtime_phase
            sessions_by_phase[phase] = sessions_by_phase.get(phase, 0) + 1

            # 统计不一致
            if not snapshot.is_consistent():
                sessions_with_inconsistencies += 1

            # 统计资源
            if snapshot.backend_has_client:
                active_backend_clients += 1

            if snapshot.bus_pending_permission:
                pending_permissions += 1

            if snapshot.bus_pending_interaction:
                pending_interactions += 1

        metrics = StateMetrics(
            total_sessions=total_sessions,
            sessions_by_phase=sessions_by_phase,
            sessions_with_inconsistencies=sessions_with_inconsistencies,
            active_backend_clients=active_backend_clients,
            pending_permissions=pending_permissions,
            pending_interactions=pending_interactions,
        )

        self._last_metrics = metrics
        return metrics

    def _get_all_session_keys(self) -> set[str]:
        """获取所有 session key。

        Returns:
            session key 集合
        """
        return self._runtime._state_checker._get_all_session_keys()

    def get_last_metrics(self) -> StateMetrics | None:
        """获取上次收集的指标。

        Returns:
            上次的指标，如果没有则返回 None
        """
        return self._last_metrics

    def is_healthy(self) -> tuple[bool, str]:
        """检查系统是否健康。

        Returns:
            (is_healthy, message) 元组
        """
        metrics = self.collect()

        # 检查是否有不一致
        if metrics.sessions_with_inconsistencies > 0:
            return (
                False,
                f"{metrics.sessions_with_inconsistencies} sessions with inconsistencies"
            )

        return (True, "All sessions consistent")