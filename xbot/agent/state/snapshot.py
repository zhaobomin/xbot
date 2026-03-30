"""状态快照数据结构，用于一致性检查和日志记录。

此模块定义了 StateSnapshot 数据类，用于在某一时刻捕获完整的会话状态，
以便进行一致性检查、日志记录和问题排查。

使用场景:
1. 在关键操作前后捕获状态快照
2. 检测状态不一致问题
3. 记录到 session trace 用于调试
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import time


@dataclass
class StateSnapshot:
    """某一时刻的完整状态快照。

    捕获单个 session 在某一时刻的所有相关状态，包括:
    - Runtime 状态 (phase, tasks, lock)
    - Backend 状态 (client, task_id)
    - Bus 状态 (pending requests)

    Attributes:
        session_key: 会话标识符
        timestamp: 快照时间戳
        runtime_phase: Runtime 当前阶段
        runtime_phase_reason: 进入当前阶段的原因
        runtime_active_tasks: 活跃任务数量
        runtime_has_lock: 是否持有会话锁
        backend_has_client: 是否有 backend client
        backend_task_id: backend 活跃任务 ID
        backend_last_used: client 最后使用时间
        bus_pending_permission: 是否有待处理的权限请求
        bus_pending_permission_id: 待处理权限请求 ID
        bus_pending_interaction: 是否有待处理的交互请求
        bus_pending_interaction_id: 待处理交互请求 ID
        inconsistencies: 检测到的不一致问题列表
    """

    # 基本信息
    session_key: str
    timestamp: float = field(default_factory=time.time)

    # Runtime 状态
    runtime_phase: str = "idle"
    runtime_phase_reason: str = ""
    runtime_active_tasks: int = 0
    runtime_has_lock: bool = False

    # Backend 状态
    backend_has_client: bool = False
    backend_task_id: str | None = None
    backend_last_used: float | None = None

    # Bus 状态
    bus_pending_permission: bool = False
    bus_pending_permission_id: str | None = None
    bus_pending_interaction: bool = False
    bus_pending_interaction_id: str | None = None

    # 检查结果
    inconsistencies: list[str] = field(default_factory=list)

    def is_consistent(self) -> bool:
        """检查状态是否一致。

        Returns:
            True 如果状态一致（无不一致问题），False 否则
        """
        return len(self.inconsistencies) == 0

    def to_dict(self) -> dict[str, Any]:
        """转换为字典，用于日志和序列化。

        Returns:
            包含所有状态信息的字典
        """
        return {
            "session_key": self.session_key,
            "timestamp": self.timestamp,
            "runtime": {
                "phase": self.runtime_phase,
                "phase_reason": self.runtime_phase_reason,
                "active_tasks": self.runtime_active_tasks,
                "has_lock": self.runtime_has_lock,
            },
            "backend": {
                "has_client": self.backend_has_client,
                "task_id": self.backend_task_id,
                "last_used": self.backend_last_used,
            },
            "bus": {
                "pending_permission": self.bus_pending_permission,
                "pending_permission_id": self.bus_pending_permission_id,
                "pending_interaction": self.bus_pending_interaction,
                "pending_interaction_id": self.bus_pending_interaction_id,
            },
            "inconsistencies": self.inconsistencies,
            "is_consistent": self.is_consistent(),
        }

    def summary(self) -> str:
        """生成状态摘要字符串。

        Returns:
            状态摘要，如 "telegram:123:running:2tasks:has_client"
        """
        parts = [
            self.session_key,
            self.runtime_phase,
            f"{self.runtime_active_tasks}tasks",
            "has_client" if self.backend_has_client else "no_client",
        ]

        if self.inconsistencies:
            parts.append(f"{len(self.inconsistencies)}issues")

        return ":".join(parts)

    @classmethod
    def create_empty(cls, session_key: str) -> StateSnapshot:
        """创建空的快照实例。

        Args:
            session_key: 会话标识符

        Returns:
            新的空快照实例
        """
        return cls(session_key=session_key)