"""会话状态协调器。

此模块提供统一的状态管理入口，封装所有状态相关操作。

设计目标:
- 作为状态管理的单一入口点
- 封装状态机、活跃任务、会话锁的管理
- 提供事务支持

使用方式:
    from xbot.agent.state_coordinator import SessionStateCoordinator

    coordinator = SessionStateCoordinator(runtime)

    # 读取状态
    phase = coordinator.get_phase("session:1")

    # 状态变更
    coordinator.transition("session:1", SessionPhase.RUNNING, reason="start")
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

if TYPE_CHECKING:
    from xbot.agent.runtime import AgentRuntime
    from xbot.agent.state_machine import SessionPhase


@dataclass
class CoordinatorStats:
    """协调器统计信息。"""

    # 状态操作统计
    phase_transitions: int = 0
    phase_reads: int = 0

    # 任务统计
    tasks_created: int = 0
    tasks_completed: int = 0

    # 锁统计
    locks_created: int = 0
    locks_released: int = 0


class SessionStateCoordinator:
    """会话状态协调器。

    统一管理会话状态、活跃任务和会话锁。

    Attributes:
        runtime: AgentRuntime 实例
        stats: 统计信息
    """

    def __init__(self, runtime: AgentRuntime):
        """初始化协调器。

        Args:
            runtime: AgentRuntime 实例
        """
        self._runtime = runtime
        self._stats = CoordinatorStats()

        # 引用 runtime 的状态组件
        # 注意：初始阶段这些引用已经存在于 runtime 中
        # 协调器只是提供统一访问接口

    # === 状态读取操作 ===

    def get_phase(self, session_key: str) -> SessionPhase:
        """获取会话当前阶段。

        Args:
            session_key: 会话标识

        Returns:
            当前 SessionPhase
        """
        self._stats.phase_reads += 1

        # 委托给现有状态机
        phase = self._runtime._state_machine.get_phase(session_key)

        return phase

    def get_state(self, session_key: str) -> Any:
        """获取会话状态。

        Args:
            session_key: 会话标识

        Returns:
            SessionState 对象
        """
        return self._runtime._state_machine.get_state(session_key)

    def has_session(self, session_key: str) -> bool:
        """检查会话是否存在。

        Args:
            session_key: 会话标识

        Returns:
            会话是否存在
        """
        return self._runtime._state_machine.has_session(session_key)

    def list_state_session_keys(self) -> set[str]:
        """获取状态机中已存在的 session key（不触发创建）。"""
        return self._runtime._state_machine.list_session_keys()

    def list_tracked_session_keys(self) -> set[str]:
        """获取协调器已跟踪的全部 session key。"""
        keys = set(self.list_state_session_keys())
        keys.update(self._runtime._active_tasks.keys())
        keys.update(self._runtime._session_locks.keys())
        return keys

    # === 状态变更操作 ===

    def transition(
        self,
        session_key: str,
        to_phase: "SessionPhase",
        *,
        reason: str = "",
        force: bool = False,
    ) -> bool:
        """执行状态转换。

        Args:
            session_key: 会话标识
            to_phase: 目标阶段
            reason: 转换原因
            force: 是否强制转换（用于错误恢复）

        Returns:
            转换是否成功
        """
        from xbot.agent.state_machine import SessionPhase

        old_phase = self.get_phase(session_key)

        # 委托给现有状态机
        success = self._runtime._state_machine.transition(
            session_key, to_phase, reason=reason, force=force
        )

        if success:
            self._stats.phase_transitions += 1

        return success

    def force_transition(
        self,
        session_key: str,
        to_phase: "SessionPhase",
        reason: str = "",
    ) -> bool:
        """强制状态转换。

        Args:
            session_key: 会话标识
            to_phase: 目标阶段
            reason: 转换原因

        Returns:
            总是返回 True
        """
        self._stats.phase_transitions += 1

        return self._runtime._state_machine.force_transition(
            session_key, to_phase, reason=reason
        )

    # === 任务管理 ===

    def register_task(self, session_key: str, task: asyncio.Task) -> None:
        """注册活跃任务。

        Args:
            session_key: 会话标识
            task: 异步任务
        """
        self._stats.tasks_created += 1
        tasks = self._runtime._active_tasks.setdefault(session_key, [])
        tasks.append(task)

    def unregister_task(self, session_key: str, task: asyncio.Task) -> None:
        """注销活跃任务。

        Args:
            session_key: 会话标识
            task: 异步任务
        """
        tasks = self._runtime._active_tasks.get(session_key)
        if tasks and task in tasks:
            tasks.remove(task)
            self._stats.tasks_completed += 1

    def get_active_tasks(self, session_key: str) -> list[asyncio.Task]:
        """获取会话的活跃任务。

        Args:
            session_key: 会话标识

        Returns:
            活跃任务列表
        """
        tasks = self._runtime._active_tasks.get(session_key, [])
        return [t for t in tasks if not t.done()]

    def has_active_tasks(self, session_key: str) -> bool:
        """检查会话是否有活跃任务。

        Args:
            session_key: 会话标识

        Returns:
            是否有活跃任务
        """
        return len(self.get_active_tasks(session_key)) > 0

    def cancel_active_tasks(self, session_key: str) -> int:
        """取消会话的所有活跃任务。

        Args:
            session_key: 会话标识

        Returns:
            取消的任务数量
        """
        tasks = self._runtime._active_tasks.pop(session_key, [])
        cancelled = 0

        for task in tasks:
            if not task.done():
                task.cancel()
                cancelled += 1
                self._stats.tasks_completed += 1

        return cancelled

    def pop_active_tasks(self, session_key: str) -> list[asyncio.Task]:
        """取出并清除活跃任务列表。

        不取消任务，仅取出引用。用于需要手动处理任务的场景。

        Args:
            session_key: 会话标识

        Returns:
            活跃任务列表（可能包含已完成的任务）
        """
        return self._runtime._active_tasks.pop(session_key, [])

    def clear_task_list(self, session_key: str) -> list[asyncio.Task]:
        """清除任务列表条目。

        无条件移除任务列表条目（不取消任务）。

        Args:
            session_key: 会话标识

        Returns:
            被移除的任务列表
        """
        return self._runtime._active_tasks.pop(session_key, [])

    def cleanup_empty_task_list(self, session_key: str) -> bool:
        """清理空的任务列表。

        如果任务列表为空，则移除它。

        Args:
            session_key: 会话标识

        Returns:
            是否移除了空列表
        """
        tasks = self._runtime._active_tasks.get(session_key)
        if not tasks:  # Empty list or None
            self._runtime._active_tasks.pop(session_key, None)
            return True
        return False

    # === 锁管理 ===

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """获取或创建会话锁。

        Args:
            session_key: 会话标识

        Returns:
            会话锁
        """
        if session_key not in self._runtime._session_locks:
            self._stats.locks_created += 1

        lock = self._runtime._session_locks.setdefault(session_key, asyncio.Lock())

        return lock

    def release_lock(self, session_key: str) -> bool:
        """释放并移除会话锁。

        Args:
            session_key: 会话标识

        Returns:
            锁是否存在并被移除
        """
        if session_key in self._runtime._session_locks:
            del self._runtime._session_locks[session_key]
            self._stats.locks_released += 1

            return True
        return False

    def has_lock(self, session_key: str) -> bool:
        """检查会话是否有锁。

        Args:
            session_key: 会话标识

        Returns:
            是否有锁
        """
        return session_key in self._runtime._session_locks

    def get_lock_object(self, session_key: str) -> asyncio.Lock:
        """获取锁对象用于 async with。

        与 get_lock 不同，此方法用于获取已存在的锁对象，
        如果不存在则创建一个新锁。

        Args:
            session_key: 会话标识

        Returns:
            会话锁对象
        """
        return self.get_lock(session_key)

    # === 会话生命周期 ===

    def cleanup_session(self, session_key: str) -> dict[str, Any]:
        """清理会话的所有状态。

        Args:
            session_key: 会话标识

        Returns:
            清理信息字典
        """
        result = {
            "tasks_cancelled": self.cancel_active_tasks(session_key),
            "lock_released": self.release_lock(session_key),
            "state_cleared": self._runtime._state_machine.has_session(session_key),
        }

        # 清理状态机
        if self._runtime._state_machine.has_session(session_key):
            self._runtime._state_machine.clear(session_key)

        return result

    def reset_session(self, session_key: str) -> None:
        """重置会话状态到初始状态。

        用于 hard_reset 场景，重置状态机到 fresh IDLE。

        Args:
            session_key: 会话标识
        """
        self._runtime._state_machine.reset(session_key)

    # === 统计信息 ===

    def get_stats(self) -> CoordinatorStats:
        """获取统计信息。

        Returns:
            CoordinatorStats 对象
        """
        return self._stats

    def reset_stats(self) -> None:
        """重置统计信息。"""
        self._stats = CoordinatorStats()

    # === 一致性检查 ===

    def check_consistency(self, session_key: str) -> tuple[bool, list[str]]:
        """检查会话状态一致性。

        Args:
            session_key: 会话标识

        Returns:
            (is_consistent, issues) 元组
        """
        from xbot.agent.state_machine import SessionPhase

        issues = []

        # 获取状态快照
        snapshot = self._runtime._state_checker.check_session(session_key)

        if not snapshot.is_consistent():
            issues.extend(snapshot.inconsistencies)

        return (len(issues) == 0, issues)

    # === 导出功能 ===

    def export_state(self, session_key: str) -> dict[str, Any]:
        """导出会话状态。

        Args:
            session_key: 会话标识

        Returns:
            状态字典
        """
        snapshot = self._runtime._state_checker.check_session(session_key)
        return snapshot.to_dict()

    # === 事务支持 ===

    def transaction(
        self,
        session_key: str,
        *,
        validate_on_commit: bool = True,
        on_commit: Callable[[], None] | None = None,
        on_rollback: Callable[[], None] | None = None,
    ) -> StateTransaction:
        """创建状态事务。

        用于原子性地更新多个状态组件。

        Args:
            session_key: 会话标识
            validate_on_commit: 提交时是否验证一致性
            on_commit: 提交回调
            on_rollback: 回滚回调

        Returns:
            StateTransaction 实例

        Example:
            async with coordinator.transaction("session:1") as tx:
                tx.set_phase(SessionPhase.RUNNING)
                tx.register_task(task)
                tx.acquire_lock()
        """
        from xbot.agent.state_transaction import StateTransaction

        return StateTransaction(
            coordinator=self,
            session_key=session_key,
            validate_on_commit=validate_on_commit,
            on_commit=on_commit,
            on_rollback=on_rollback,
        )
