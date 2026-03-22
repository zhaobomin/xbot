"""会话状态协调器。

此模块提供统一的状态管理入口，封装所有状态相关操作。

设计目标:
- 作为状态管理的单一入口点
- 封装状态机、活跃任务、会话锁的管理
- 提供事务支持（后续演进）
- Shadow Mode: 初始阶段只记录日志，不改变行为

使用方式:
    from xbot.agent.state_coordinator import SessionStateCoordinator

    coordinator = SessionStateCoordinator(runtime)
    coordinator.start_shadow_mode()

    # 读取状态
    phase = coordinator.get_phase("session:1")

    # 状态变更
    coordinator.transition("session:1", SessionPhase.RUNNING, reason="start")
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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

    # Shadow mode 统计
    shadow_inconsistencies: int = 0
    shadow_operations: int = 0


class SessionStateCoordinator:
    """会话状态协调器。

    统一管理会话状态、活跃任务和会话锁。

    Attributes:
        runtime: AgentRuntime 实例
        shadow_mode: 是否运行在 Shadow Mode
        stats: 统计信息
    """

    def __init__(self, runtime: AgentRuntime):
        """初始化协调器。

        Args:
            runtime: AgentRuntime 实例
        """
        self._runtime = runtime
        self._shadow_mode = False
        self._stats = CoordinatorStats()

        # 引用 runtime 的状态组件
        # 注意：初始阶段这些引用已经存在于 runtime 中
        # 协调器只是提供统一访问接口

    # === Shadow Mode 控制 ===

    def enable_shadow_mode(self) -> None:
        """启用 Shadow Mode。

        Shadow Mode 下，协调器记录所有操作并与实际行为对比，
        但不改变任何行为。
        """
        self._shadow_mode = True
        logger.info("SessionStateCoordinator: Shadow mode enabled")

    def disable_shadow_mode(self) -> None:
        """禁用 Shadow Mode。"""
        self._shadow_mode = False
        logger.info("SessionStateCoordinator: Shadow mode disabled")

    @property
    def is_shadow_mode(self) -> bool:
        """检查是否在 Shadow Mode。"""
        return self._shadow_mode

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

        if self._shadow_mode:
            logger.trace(
                f"[Shadow] get_phase: {session_key} -> {phase.value}"
            )

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

            if self._shadow_mode:
                logger.trace(
                    f"[Shadow] transition: {session_key} "
                    f"{old_phase.value} -> {to_phase.value} (reason: {reason})"
                )

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

        if self._shadow_mode:
            old_phase = self.get_phase(session_key)
            logger.trace(
                f"[Shadow] force_transition: {session_key} "
                f"{old_phase.value} -> {to_phase.value} (reason: {reason})"
            )

        return self._runtime._state_machine.force_transition(
            session_key, to_phase, reason
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

        if self._shadow_mode:
            logger.trace(
                f"[Shadow] register_task: {session_key} task={task.get_name()}"
            )

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

            if self._shadow_mode:
                logger.trace(
                    f"[Shadow] unregister_task: {session_key} task={task.get_name()}"
                )

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

        if self._shadow_mode and cancelled > 0:
            logger.trace(
                f"[Shadow] cancel_active_tasks: {session_key} count={cancelled}"
            )

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

        if self._shadow_mode:
            logger.trace(f"[Shadow] get_lock: {session_key} locked={lock.locked()}")

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

            if self._shadow_mode:
                logger.trace(f"[Shadow] release_lock: {session_key}")

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

        if self._shadow_mode:
            logger.trace(f"[Shadow] cleanup_session: {session_key} result={result}")

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

            if self._shadow_mode:
                self._stats.shadow_inconsistencies += 1
                logger.warning(
                    f"[Shadow] inconsistency detected: {session_key} "
                    f"issues={snapshot.inconsistencies}"
                )

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