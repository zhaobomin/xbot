"""状态事务支持。

此模块提供原子性的状态更新能力，解决多组件状态更新的原子性问题。

设计目标:
- 提供事务语义：要么全部成功，要么全部回滚
- 支持状态变更的验证
- 记录事务历史用于调试
- Shadow Mode 兼容

使用方式:
    async with coordinator.transaction("session:1") as tx:
        tx.set_phase(SessionPhase.RUNNING)
        tx.register_task(task)
        tx.acquire_lock()
        # 提交时自动应用，异常时自动回滚
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

if TYPE_CHECKING:
    from xbot.agent.state.coordinator import SessionStateCoordinator
    from xbot.agent.state.machine import SessionPhase


class TransactionState(str, Enum):
    """事务状态。"""

    PENDING = "pending"  # 等待开始
    ACTIVE = "active"  # 进行中
    COMMITTED = "committed"  # 已提交
    ROLLED_BACK = "rolled_back"  # 已回滚


@dataclass
class TransactionOperation:
    """事务操作记录。"""

    operation: str  # 操作类型：set_phase, register_task, acquire_lock 等
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    # 回滚信息
    rollback_op: str | None = None
    rollback_args: tuple = field(default_factory=tuple)
    rollback_kwargs: dict = field(default_factory=dict)


@dataclass
class TransactionResult:
    """事务执行结果。"""

    success: bool
    state: TransactionState
    operations_count: int = 0
    duration_ms: float = 0
    error: str | None = None
    operations: list[TransactionOperation] = field(default_factory=list)

    def summary(self) -> str:
        """生成结果摘要。"""
        status = "✓" if self.success else "✗"
        return (
            f"Transaction({status}, state={self.state.value}, "
            f"ops={self.operations_count}, duration={self.duration_ms:.1f}ms)"
        )


class StateTransaction:
    """状态事务。

    提供原子性的状态更新能力。使用 async with 语法：

    ```python
    async with coordinator.transaction("session:1") as tx:
        tx.set_phase(SessionPhase.RUNNING)
        tx.register_task(task)
    ```

    Attributes:
        coordinator: SessionStateCoordinator 实例
        session_key: 会话标识
        state: 当前事务状态
        operations: 操作列表
    """

    def __init__(
        self,
        coordinator: SessionStateCoordinator,
        session_key: str,
        *,
        validate_on_commit: bool = True,
        on_commit: Callable[[], None] | None = None,
        on_rollback: Callable[[], None] | None = None,
    ):
        """初始化事务。

        Args:
            coordinator: SessionStateCoordinator 实例
            session_key: 会话标识
            validate_on_commit: 提交时是否验证一致性
            on_commit: 提交回调
            on_rollback: 回滚回调
        """
        self._coordinator = coordinator
        self._session_key = session_key
        self._validate_on_commit = validate_on_commit
        self._on_commit = on_commit
        self._on_rollback = on_rollback

        self._state = TransactionState.PENDING
        self._operations: list[TransactionOperation] = []
        self._snapshot: dict[str, Any] | None = None
        self._start_time: float | None = None

        # 待应用的变更（延迟到提交时）
        self._pending_phase: SessionPhase | None = None
        self._pending_phase_reason: str = ""
        self._pending_phase_force: bool = False
        self._pending_tasks: list[asyncio.Task] = []
        self._pending_unregister_tasks: list[asyncio.Task] = []
        self._pending_lock_acquire: bool = False
        self._pending_lock_release: bool = False

    @property
    def session_key(self) -> str:
        """获取会话标识。"""
        return self._session_key

    @property
    def state(self) -> TransactionState:
        """获取事务状态。"""
        return self._state

    @property
    def operations(self) -> list[TransactionOperation]:
        """获取操作列表。"""
        return list(self._operations)

    # === 上下文管理器 ===

    async def __aenter__(self) -> StateTransaction:
        """进入事务上下文。"""
        self._start_time = time.time()
        self._state = TransactionState.ACTIVE

        # 保存快照用于回滚
        self._snapshot = self._capture_snapshot()

        logger.trace(f"Transaction started: {self._session_key}")
        return self

    async def __aexit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        """退出事务上下文。"""
        if exc_type is not None:
            # 发生异常，执行回滚
            await self._rollback()
            return False  # 让异常继续传播

        # 正常退出，执行提交
        await self._commit()
        return False

    # === 操作方法 ===

    def set_phase(
        self,
        phase: SessionPhase,
        *,
        reason: str = "",
        force: bool = False,
    ) -> None:
        """设置会话阶段。

        Args:
            phase: 目标阶段
            reason: 原因
            force: 是否强制
        """
        self._assert_active()

        old_phase = self._coordinator.get_phase(self._session_key)

        op = TransactionOperation(
            operation="set_phase",
            args=(phase,),
            kwargs={"reason": reason, "force": force},
            rollback_op="set_phase",
            rollback_args=(old_phase,),
            rollback_kwargs={"reason": "transaction_rollback", "force": True},
        )
        self._operations.append(op)

        self._pending_phase = phase
        self._pending_phase_reason = reason
        self._pending_phase_force = force

        logger.trace(
            f"Transaction[{self._session_key}]: set_phase({phase.value}, reason={reason}, force={force})"
        )

    def register_task(self, task: asyncio.Task) -> None:
        """注册任务。

        Args:
            task: 异步任务
        """
        self._assert_active()

        op = TransactionOperation(
            operation="register_task",
            args=(task,),
            rollback_op="unregister_task",
            rollback_args=(task,),
        )
        self._operations.append(op)

        self._pending_tasks.append(task)

        logger.trace(
            f"Transaction[{self._session_key}]: register_task({task.get_name()})"
        )

    def unregister_task(self, task: asyncio.Task) -> None:
        """注销任务。

        Args:
            task: 异步任务
        """
        self._assert_active()

        op = TransactionOperation(
            operation="unregister_task",
            args=(task,),
            rollback_op="register_task",
            rollback_args=(task,),
        )
        self._operations.append(op)

        # 从待注册列表中移除（如果存在）
        if task in self._pending_tasks:
            self._pending_tasks.remove(task)

        # 添加到待注销列表
        self._pending_unregister_tasks.append(task)

        logger.trace(
            f"Transaction[{self._session_key}]: unregister_task({task.get_name()})"
        )

    def acquire_lock(self) -> None:
        """获取会话锁。"""
        self._assert_active()

        has_lock = self._coordinator.has_lock(self._session_key)

        op = TransactionOperation(
            operation="acquire_lock",
            args=(),
            rollback_op="release_lock" if not has_lock else None,
            rollback_args=(),
        )
        self._operations.append(op)

        self._pending_lock_acquire = True

        logger.trace(f"Transaction[{self._session_key}]: acquire_lock()")

    def release_lock(self) -> None:
        """释放会话锁。"""
        self._assert_active()

        had_lock = self._coordinator.has_lock(self._session_key)

        op = TransactionOperation(
            operation="release_lock",
            args=(),
            rollback_op="acquire_lock" if had_lock else None,
            rollback_args=(),
        )
        self._operations.append(op)

        self._pending_lock_acquire = False
        self._pending_lock_release = True

        logger.trace(f"Transaction[{self._session_key}]: release_lock()")

    # === 提交与回滚 ===

    async def commit(self) -> TransactionResult:
        """手动提交事务。

        Returns:
            TransactionResult 结果
        """
        if self._state != TransactionState.ACTIVE:
            return TransactionResult(
                success=False,
                state=self._state,
                error=f"Cannot commit transaction in state {self._state.value}",
            )

        return await self._commit()

    async def rollback(self) -> TransactionResult:
        """手动回滚事务。

        Returns:
            TransactionResult 结果
        """
        if self._state != TransactionState.ACTIVE:
            return TransactionResult(
                success=False,
                state=self._state,
                error=f"Cannot rollback transaction in state {self._state.value}",
            )

        return await self._rollback()

    async def _commit(self) -> TransactionResult:
        """执行提交。"""
        duration_ms = (time.time() - (self._start_time or time.time())) * 1000

        try:
            # 应用所有待处理的变更
            if self._pending_phase is not None:
                if self._pending_phase_force:
                    self._coordinator.force_transition(
                        self._session_key,
                        self._pending_phase,
                        reason=self._pending_phase_reason,
                    )
                else:
                    self._coordinator.transition(
                        self._session_key,
                        self._pending_phase,
                        reason=self._pending_phase_reason,
                    )

            for task in self._pending_tasks:
                self._coordinator.register_task(self._session_key, task)

            for task in self._pending_unregister_tasks:
                self._coordinator.unregister_task(self._session_key, task)

            if self._pending_lock_acquire:
                self._coordinator.get_lock(self._session_key)

            if self._pending_lock_release:
                self._coordinator.release_lock(self._session_key)

            # 验证一致性（可选）
            if self._validate_on_commit:
                is_consistent, issues = self._coordinator.check_consistency(
                    self._session_key
                )
                if not is_consistent:
                    logger.warning(
                        f"Transaction commit resulted in inconsistent state: {issues}"
                    )

            self._state = TransactionState.COMMITTED

            # 调用回调
            if self._on_commit:
                try:
                    self._on_commit()
                except Exception as e:
                    logger.debug(f"Transaction on_commit callback error: {e}")

            logger.trace(
                f"Transaction committed: {self._session_key} "
                f"({len(self._operations)} operations)"
            )

            return TransactionResult(
                success=True,
                state=self._state,
                operations_count=len(self._operations),
                duration_ms=duration_ms,
                operations=self._operations,
            )

        except Exception as e:
            # 提交失败，尝试回滚
            logger.error(f"Transaction commit failed: {e}, attempting rollback")
            await self._rollback()
            return TransactionResult(
                success=False,
                state=self._state,
                operations_count=len(self._operations),
                duration_ms=duration_ms,
                error=str(e),
                operations=self._operations,
            )

    async def _rollback(self) -> TransactionResult:
        """执行回滚。"""
        duration_ms = (time.time() - (self._start_time or time.time())) * 1000

        logger.trace(
            f"Transaction rolling back: {self._session_key} "
            f"({len(self._operations)} operations)"
        )

        # 逆序执行回滚操作
        for op in reversed(self._operations):
            if op.rollback_op is None:
                continue

            try:
                if op.rollback_op == "set_phase":
                    from xbot.agent.state.machine import SessionPhase

                    if not op.rollback_args:
                        logger.warning("Rollback set_phase: missing args")
                        continue
                    phase = op.rollback_args[0]
                    self._coordinator.force_transition(
                        self._session_key,
                        phase,
                        reason=op.rollback_kwargs.get("reason", "rollback"),
                    )
                elif op.rollback_op == "unregister_task":
                    if not op.rollback_args:
                        logger.warning("Rollback unregister_task: missing args")
                        continue
                    task = op.rollback_args[0]
                    self._coordinator.unregister_task(self._session_key, task)
                elif op.rollback_op == "register_task":
                    if not op.rollback_args:
                        logger.warning("Rollback register_task: missing args")
                        continue
                    task = op.rollback_args[0]
                    self._coordinator.register_task(self._session_key, task)
                elif op.rollback_op == "release_lock":
                    self._coordinator.release_lock(self._session_key)
                elif op.rollback_op == "acquire_lock":
                    self._coordinator.get_lock(self._session_key)

            except Exception as e:
                logger.warning(
                    f"Rollback operation {op.rollback_op} failed: {e}"
                )

        self._state = TransactionState.ROLLED_BACK

        # 调用回调
        if self._on_rollback:
            try:
                self._on_rollback()
            except Exception as e:
                logger.debug(f"Transaction on_rollback callback error: {e}")

        return TransactionResult(
            success=False,
            state=self._state,
            operations_count=len(self._operations),
            duration_ms=duration_ms,
            operations=self._operations,
        )

    # === 辅助方法 ===

    def _assert_active(self) -> None:
        """断言事务处于活跃状态。"""
        if self._state != TransactionState.ACTIVE:
            raise RuntimeError(
                f"Transaction is not active (state={self._state.value})"
            )

    def _capture_snapshot(self) -> dict[str, Any]:
        """捕获当前状态快照。"""
        return {
            "phase": self._coordinator.get_phase(self._session_key),
            "has_lock": self._coordinator.has_lock(self._session_key),
            "active_tasks": len(self._coordinator.get_active_tasks(self._session_key)),
        }