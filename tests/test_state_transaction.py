"""测试状态事务。"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from xbot.agent.state.transaction import (
    StateTransaction,
    TransactionState,
    TransactionOperation,
    TransactionResult,
)


class TestTransactionOperation:
    """测试 TransactionOperation 数据结构"""

    def test_create_operation(self):
        """测试创建操作"""
        op = TransactionOperation(
            operation="set_phase",
            args=(pytest,),
            kwargs={"reason": "test"},
        )

        assert op.operation == "set_phase"
        assert op.args == (pytest,)
        assert op.kwargs == {"reason": "test"}
        assert op.rollback_op is None

    def test_create_operation_with_rollback(self):
        """测试创建带回滚的操作"""
        op = TransactionOperation(
            operation="set_phase",
            args=("running",),
            rollback_op="set_phase",
            rollback_args=("idle",),
        )

        assert op.rollback_op == "set_phase"
        assert op.rollback_args == ("idle",)


class TestTransactionResult:
    """测试 TransactionResult 数据结构"""

    def test_success_result(self):
        """测试成功结果"""
        result = TransactionResult(
            success=True,
            state=TransactionState.COMMITTED,
            operations_count=3,
            duration_ms=10.5,
        )

        assert result.success is True
        assert result.state == TransactionState.COMMITTED
        assert result.operations_count == 3
        assert result.error is None

    def test_failure_result(self):
        """测试失败结果"""
        result = TransactionResult(
            success=False,
            state=TransactionState.ROLLED_BACK,
            error="Test error",
        )

        assert result.success is False
        assert result.error == "Test error"

    def test_summary(self):
        """测试摘要输出"""
        result = TransactionResult(
            success=True,
            state=TransactionState.COMMITTED,
            operations_count=5,
            duration_ms=15.0,
        )

        summary = result.summary()
        assert "✓" in summary
        assert "committed" in summary
        assert "ops=5" in summary


class TestStateTransactionInit:
    """测试事务初始化"""

    def test_init(self, mock_coordinator):
        """测试初始化"""
        tx = StateTransaction(mock_coordinator, "test:1")

        assert tx.session_key == "test:1"
        assert tx.state == TransactionState.PENDING
        assert len(tx.operations) == 0

    def test_init_with_options(self, mock_coordinator):
        """测试带选项初始化"""
        on_commit = MagicMock()
        on_rollback = MagicMock()

        tx = StateTransaction(
            mock_coordinator,
            "test:1",
            validate_on_commit=False,
            on_commit=on_commit,
            on_rollback=on_rollback,
        )

        assert tx._validate_on_commit is False
        assert tx._on_commit is on_commit
        assert tx._on_rollback is on_rollback


class TestStateTransactionOperations:
    """测试事务操作"""

    @pytest.mark.asyncio
    async def test_set_phase(self, mock_coordinator):
        """测试设置阶段"""
        from xbot.agent.runtime import SessionPhase

        mock_coordinator.get_phase.return_value = SessionPhase.IDLE

        async with StateTransaction(mock_coordinator, "test:1") as tx:
            tx.set_phase(SessionPhase.RUNNING, reason="test")

        assert len(tx.operations) == 1
        assert tx.operations[0].operation == "set_phase"
        # force=False (default) 应该调用 transition 而非 force_transition
        mock_coordinator.transition.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_phase_force(self, mock_coordinator):
        """测试强制设置阶段"""
        from xbot.agent.runtime import SessionPhase

        mock_coordinator.get_phase.return_value = SessionPhase.IDLE

        async with StateTransaction(mock_coordinator, "test:1") as tx:
            tx.set_phase(SessionPhase.RUNNING, reason="test", force=True)

        assert len(tx.operations) == 1
        assert tx.operations[0].operation == "set_phase"
        # force=True 应该调用 force_transition
        mock_coordinator.force_transition.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_task(self, mock_coordinator):
        """测试注册任务"""
        task = MagicMock(spec=asyncio.Task)
        task.get_name.return_value = "test-task"

        async with StateTransaction(mock_coordinator, "test:1") as tx:
            tx.register_task(task)

        assert len(tx.operations) == 1
        mock_coordinator.register_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_unregister_task(self, mock_coordinator):
        """测试注销任务"""
        task = MagicMock(spec=asyncio.Task)
        task.get_name.return_value = "test-task"

        async with StateTransaction(mock_coordinator, "test:1") as tx:
            tx.unregister_task(task)

        assert len(tx.operations) == 1

    @pytest.mark.asyncio
    async def test_acquire_lock(self, mock_coordinator):
        """测试获取锁"""
        mock_coordinator.has_lock.return_value = False

        async with StateTransaction(mock_coordinator, "test:1") as tx:
            tx.acquire_lock()

        assert len(tx.operations) == 1
        mock_coordinator.get_lock.assert_called_once()

    @pytest.mark.asyncio
    async def test_release_lock(self, mock_coordinator):
        """测试释放锁"""
        mock_coordinator.has_lock.return_value = True

        async with StateTransaction(mock_coordinator, "test:1") as tx:
            tx.release_lock()

        assert len(tx.operations) == 1
        mock_coordinator.release_lock.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_operations(self, mock_coordinator):
        """测试多个操作"""
        from xbot.agent.runtime import SessionPhase

        mock_coordinator.get_phase.return_value = SessionPhase.IDLE
        mock_coordinator.has_lock.return_value = False

        task = MagicMock(spec=asyncio.Task)
        task.get_name.return_value = "test-task"

        async with StateTransaction(mock_coordinator, "test:1") as tx:
            tx.set_phase(SessionPhase.RUNNING, reason="start")
            tx.register_task(task)
            tx.acquire_lock()

        assert len(tx.operations) == 3
        assert tx.operations[0].operation == "set_phase"
        assert tx.operations[1].operation == "register_task"
        assert tx.operations[2].operation == "acquire_lock"


class TestStateTransactionCommit:
    """测试事务提交"""

    @pytest.mark.asyncio
    async def test_commit_success(self, mock_coordinator):
        """测试成功提交"""
        from xbot.agent.runtime import SessionPhase

        mock_coordinator.get_phase.return_value = SessionPhase.IDLE
        mock_coordinator.check_consistency.return_value = (True, [])

        tx = StateTransaction(mock_coordinator, "test:1")
        async with tx:
            tx.set_phase(SessionPhase.RUNNING, reason="test")

        assert tx.state == TransactionState.COMMITTED

    @pytest.mark.asyncio
    async def test_commit_with_callback(self, mock_coordinator):
        """测试提交回调"""
        from xbot.agent.runtime import SessionPhase

        mock_coordinator.get_phase.return_value = SessionPhase.IDLE
        callback = MagicMock()

        tx = StateTransaction(mock_coordinator, "test:1", on_commit=callback)
        async with tx:
            tx.set_phase(SessionPhase.RUNNING, reason="test")

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_manual_commit(self, mock_coordinator):
        """测试手动提交"""
        from xbot.agent.runtime import SessionPhase

        mock_coordinator.get_phase.return_value = SessionPhase.IDLE
        mock_coordinator.check_consistency.return_value = (True, [])

        tx = StateTransaction(mock_coordinator, "test:1")
        async with tx:
            tx.set_phase(SessionPhase.RUNNING, reason="test")
            result = await tx.commit()

        assert result.success is True
        assert result.state == TransactionState.COMMITTED


class TestStateTransactionRollback:
    """测试事务回滚"""

    @pytest.mark.asyncio
    async def test_rollback_on_exception(self, mock_coordinator):
        """测试异常时回滚"""
        from xbot.agent.runtime import SessionPhase

        mock_coordinator.get_phase.return_value = SessionPhase.IDLE

        tx = StateTransaction(mock_coordinator, "test:1")
        try:
            async with tx:
                tx.set_phase(SessionPhase.RUNNING, reason="test")
                raise ValueError("Test error")
        except ValueError:
            pass

        assert tx.state == TransactionState.ROLLED_BACK
        mock_coordinator.force_transition.assert_called()

    @pytest.mark.asyncio
    async def test_rollback_callback(self, mock_coordinator):
        """测试回滚回调"""
        from xbot.agent.runtime import SessionPhase

        mock_coordinator.get_phase.return_value = SessionPhase.IDLE
        callback = MagicMock()

        tx = StateTransaction(mock_coordinator, "test:1", on_rollback=callback)
        try:
            async with tx:
                tx.set_phase(SessionPhase.RUNNING, reason="test")
                raise ValueError("Test error")
        except ValueError:
            pass

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_manual_rollback(self, mock_coordinator):
        """测试手动回滚"""
        from xbot.agent.runtime import SessionPhase

        mock_coordinator.get_phase.return_value = SessionPhase.IDLE

        tx = StateTransaction(mock_coordinator, "test:1")
        async with tx:
            tx.set_phase(SessionPhase.RUNNING, reason="test")
            result = await tx.rollback()

        assert result.success is False
        assert result.state == TransactionState.ROLLED_BACK

    @pytest.mark.asyncio
    async def test_rollback_operations_in_reverse(self, mock_coordinator):
        """测试回滚操作逆序执行"""
        from xbot.agent.runtime import SessionPhase

        mock_coordinator.get_phase.return_value = SessionPhase.IDLE

        operations_called = []

        def mock_force_transition(session_key, phase, reason=""):
            operations_called.append(("force_transition", phase.value))

        mock_coordinator.force_transition.side_effect = mock_force_transition

        tx = StateTransaction(mock_coordinator, "test:1")
        try:
            async with tx:
                tx.set_phase(SessionPhase.RUNNING, reason="first")
                tx.set_phase(SessionPhase.WAITING_PERMISSION, reason="second")
                raise ValueError("Test error")
        except ValueError:
            pass

        # 验证回滚操作是逆序的
        assert tx.state == TransactionState.ROLLED_BACK


class TestStateTransactionState:
    """测试事务状态"""

    def test_operation_outside_active_raises(self, mock_coordinator):
        """测试非活跃状态下操作抛出异常"""
        tx = StateTransaction(mock_coordinator, "test:1")

        with pytest.raises(RuntimeError, match="not active"):
            tx.set_phase(pytest, reason="test")

    @pytest.mark.asyncio
    async def test_cannot_commit_twice(self, mock_coordinator):
        """测试不能重复提交"""
        from xbot.agent.runtime import SessionPhase

        mock_coordinator.get_phase.return_value = SessionPhase.IDLE

        async with StateTransaction(mock_coordinator, "test:1") as tx:
            tx.set_phase(SessionPhase.RUNNING, reason="test")

        # 事务已提交，再次提交应该失败
        result = await tx.commit()
        assert result.success is False
        assert "Cannot commit" in result.error


class TestStateTransactionValidation:
    """测试事务验证"""

    @pytest.mark.asyncio
    async def test_validate_on_commit(self, mock_coordinator):
        """测试提交时验证"""
        from xbot.agent.runtime import SessionPhase

        mock_coordinator.get_phase.return_value = SessionPhase.IDLE
        mock_coordinator.check_consistency.return_value = (False, ["Issue 1"])

        tx = StateTransaction(mock_coordinator, "test:1", validate_on_commit=True)
        async with tx:
            tx.set_phase(SessionPhase.RUNNING, reason="test")

        # 验证应该被调用
        mock_coordinator.check_consistency.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_validation(self, mock_coordinator):
        """测试跳过验证"""
        from xbot.agent.runtime import SessionPhase

        mock_coordinator.get_phase.return_value = SessionPhase.IDLE

        tx = StateTransaction(mock_coordinator, "test:1", validate_on_commit=False)
        async with tx:
            tx.set_phase(SessionPhase.RUNNING, reason="test")

        # 验证不应该被调用
        mock_coordinator.check_consistency.assert_not_called()


# === Fixtures ===

@pytest.fixture
def mock_coordinator():
    """创建模拟的 SessionStateCoordinator"""
    coordinator = MagicMock()

    coordinator.get_phase = MagicMock(return_value="idle")
    coordinator.force_transition = MagicMock()
    coordinator.register_task = MagicMock()
    coordinator.unregister_task = MagicMock()
    coordinator.get_lock = MagicMock()
    coordinator.release_lock = MagicMock(return_value=True)
    coordinator.has_lock = MagicMock(return_value=False)
    coordinator.get_active_tasks = MagicMock(return_value=[])
    coordinator.check_consistency = MagicMock(return_value=(True, []))

    return coordinator