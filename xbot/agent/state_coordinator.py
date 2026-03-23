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
    from xbot.agent.runtime import AgentRuntime, SessionPhase


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
        return session_key in self._runtime._state_machine._states

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
        from xbot.agent.runtime import SessionPhase

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
            "state_cleared": session_key in self._runtime._state_machine._states,
        }

        # 清理状态机
        if session_key in self._runtime._state_machine._states:
            del self._runtime._state_machine._states[session_key]

        return result

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
        from xbot.agent.runtime import SessionPhase

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

    # === 原子操作 ===

    async def atomic_start_dispatch(
        self,
        session_key: str,
        task: asyncio.Task,
    ) -> bool:
        """原子性地开始 dispatch。

        在单个事务中设置 RUNNING 状态并注册任务。

        Args:
            session_key: 会话标识
            task: 要注册的任务

        Returns:
            操作是否成功
        """
        async with self.transaction(session_key, validate_on_commit=False) as tx:
            tx.set_phase(
                self._runtime._state_machine.get_phase(session_key).__class__.RUNNING,
                reason="dispatch_start",
            )
            tx.register_task(task)
            tx.acquire_lock()

        return True

    async def end_dispatch(
        self,
        session_key: str,
        task: asyncio.Task,
    ) -> bool:
        """结束 dispatch，注销任务并更新状态。

        注意：此方法执行同步操作序列，不是原子事务。
        如果需要原子性，请使用 transaction() 方法。

        Args:
            session_key: 会话标识
            task: 要注销的任务

        Returns:
            操作是否成功
        """
        SessionPhase = self._runtime._state_machine.get_phase(session_key).__class__

        # First unregister the task (this will increment tasks_completed)
        self.unregister_task(session_key, task)

        # Then check remaining tasks (after unregistration)
        remaining = [t for t in self._runtime._active_tasks.get(session_key, []) if not t.done()]
        if not remaining:
            self.force_transition(session_key, SessionPhase.IDLE, reason="dispatch_end")

        return True

    # Deprecated alias for backward compatibility
    async def atomic_end_dispatch(
        self,
        session_key: str,
        task: asyncio.Task,
        success: bool = True,  # Keep for backward compatibility
    ) -> bool:
        """Deprecated: Use end_dispatch() instead."""
        return await self.end_dispatch(session_key, task)

    async def atomic_cleanup_session(
        self,
        session_key: str,
        cancel_tasks: bool = True,
    ) -> dict[str, Any]:
        """原子性地清理会话状态。

        在单个事务中清理所有会话相关的状态。

        Args:
            session_key: 会话标识
            cancel_tasks: 是否取消活跃任务

        Returns:
            清理结果
        """
        from xbot.agent.runtime import SessionPhase

        cancelled = 0
        if cancel_tasks:
            cancelled = self.cancel_active_tasks(session_key)

        async with self.transaction(session_key, validate_on_commit=False) as tx:
            tx.set_phase(SessionPhase.IDLE, reason="cleanup")
            if self.has_lock(session_key):
                tx.release_lock()

        return {
            "tasks_cancelled": cancelled,
            "lock_released": not self.has_lock(session_key),
        }

    async def atomic_wait_permission(
        self,
        session_key: str,
        permission_id: str,
    ) -> bool:
        """原子性地进入等待权限状态。

        Args:
            session_key: 会话标识
            permission_id: 权限请求 ID

        Returns:
            操作是否成功
        """
        from xbot.agent.runtime import SessionPhase

        async with self.transaction(session_key, validate_on_commit=False) as tx:
            tx.set_phase(SessionPhase.WAITING_PERMISSION, reason=f"awaiting:{permission_id}")

        return True

    async def atomic_wait_interaction(
        self,
        session_key: str,
        interaction_id: str,
    ) -> bool:
        """原子性地进入等待交互状态。

        Args:
            session_key: 会话标识
            interaction_id: 交互请求 ID

        Returns:
            操作是否成功
        """
        from xbot.agent.runtime import SessionPhase

        async with self.transaction(session_key, validate_on_commit=False) as tx:
            tx.set_phase(SessionPhase.WAITING_INTERACTION, reason=f"awaiting:{interaction_id}")

        return True

    async def atomic_resume_from_wait(
        self,
        session_key: str,
    ) -> bool:
        """原子性地从等待状态恢复到运行。

        Args:
            session_key: 会话标识

        Returns:
            操作是否成功
        """
        from xbot.agent.runtime import SessionPhase

        async with self.transaction(session_key, validate_on_commit=False) as tx:
            tx.set_phase(SessionPhase.RUNNING, reason="resume_from_wait")

        return True