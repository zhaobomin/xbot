"""测试会话状态协调器。"""

import pytest
import asyncio
from unittest.mock import MagicMock

from xbot.agent.state_coordinator import SessionStateCoordinator, CoordinatorStats
from xbot.agent.runtime import SessionPhase, SessionStateMachine


class TestCoordinatorStats:
    """测试 CoordinatorStats 数据结构"""

    def test_create_default(self):
        """测试创建默认统计"""
        stats = CoordinatorStats()

        assert stats.phase_transitions == 0
        assert stats.phase_reads == 0
        assert stats.tasks_created == 0
        assert stats.tasks_completed == 0
        assert stats.locks_created == 0
        assert stats.locks_released == 0

    def test_create_with_values(self):
        """测试用值创建"""
        stats = CoordinatorStats(
            phase_transitions=10,
            phase_reads=100,
            tasks_created=5,
            tasks_completed=3,
        )

        assert stats.phase_transitions == 10
        assert stats.phase_reads == 100
        assert stats.tasks_created == 5
        assert stats.tasks_completed == 3


class TestSessionStateCoordinatorInit:
    """测试协调器初始化"""

    def test_init(self, mock_runtime):
        """测试初始化"""
        coordinator = SessionStateCoordinator(mock_runtime)

        assert coordinator._runtime is mock_runtime
        assert isinstance(coordinator._stats, CoordinatorStats)


class TestSessionStateCoordinatorPhaseRead:
    """测试阶段读取操作"""

    def test_get_phase(self, mock_runtime):
        """测试获取阶段"""
        mock_runtime._state_machine.get_phase.return_value = SessionPhase.IDLE

        coordinator = SessionStateCoordinator(mock_runtime)
        phase = coordinator.get_phase("test:1")

        assert phase == SessionPhase.IDLE
        assert coordinator._stats.phase_reads == 1

    def test_get_phase_multiple(self, mock_runtime):
        """测试多次获取阶段"""
        mock_runtime._state_machine.get_phase.return_value = SessionPhase.RUNNING

        coordinator = SessionStateCoordinator(mock_runtime)
        coordinator.get_phase("test:1")
        coordinator.get_phase("test:2")

        assert coordinator._stats.phase_reads == 2

    def test_get_state(self, mock_runtime):
        """测试获取状态"""
        mock_state = MagicMock()
        mock_runtime._state_machine.get_state.return_value = mock_state

        coordinator = SessionStateCoordinator(mock_runtime)
        state = coordinator.get_state("test:1")

        assert state is mock_state

    def test_has_session_true(self, mock_runtime):
        """测试会话存在"""
        mock_runtime._state_machine._states = {"test:1": MagicMock()}

        coordinator = SessionStateCoordinator(mock_runtime)
        assert coordinator.has_session("test:1") is True

    def test_has_session_false(self, mock_runtime):
        """测试会话不存在"""
        mock_runtime._state_machine._states = {}

        coordinator = SessionStateCoordinator(mock_runtime)
        assert coordinator.has_session("test:1") is False


class TestSessionStateCoordinatorTransition:
    """测试状态转换"""

    def test_transition_success(self, mock_runtime):
        """测试成功转换"""
        mock_runtime._state_machine.get_phase.return_value = SessionPhase.IDLE
        mock_runtime._state_machine.transition.return_value = True

        coordinator = SessionStateCoordinator(mock_runtime)
        result = coordinator.transition(
            "test:1", SessionPhase.RUNNING, reason="start"
        )

        assert result is True
        assert coordinator._stats.phase_transitions == 1
        mock_runtime._state_machine.transition.assert_called_once()

    def test_transition_failure(self, mock_runtime):
        """测试转换失败"""
        mock_runtime._state_machine.get_phase.return_value = SessionPhase.IDLE
        mock_runtime._state_machine.transition.return_value = False

        coordinator = SessionStateCoordinator(mock_runtime)
        result = coordinator.transition(
            "test:1", SessionPhase.WAITING_PERMISSION, reason="test"
        )

        assert result is False
        assert coordinator._stats.phase_transitions == 0

    def test_force_transition(self, mock_runtime):
        """测试强制转换"""
        mock_runtime._state_machine.get_phase.return_value = SessionPhase.ERROR
        mock_runtime._state_machine.force_transition.return_value = True

        coordinator = SessionStateCoordinator(mock_runtime)
        result = coordinator.force_transition(
            "test:1", SessionPhase.IDLE, reason="recovery"
        )

        assert result is True
        assert coordinator._stats.phase_transitions == 1


class TestSessionStateCoordinatorTasks:
    """测试任务管理"""

    def test_register_task(self, mock_runtime):
        """测试注册任务"""
        mock_runtime._active_tasks = {}

        coordinator = SessionStateCoordinator(mock_runtime)

        task = MagicMock(spec=asyncio.Task)
        task.get_name.return_value = "test-task"

        coordinator.register_task("test:1", task)

        assert "test:1" in mock_runtime._active_tasks
        assert task in mock_runtime._active_tasks["test:1"]
        assert coordinator._stats.tasks_created == 1

    def test_unregister_task(self, mock_runtime):
        """测试注销任务"""
        task = MagicMock(spec=asyncio.Task)
        mock_runtime._active_tasks = {"test:1": [task]}

        coordinator = SessionStateCoordinator(mock_runtime)
        coordinator.unregister_task("test:1", task)

        assert task not in mock_runtime._active_tasks["test:1"]
        assert coordinator._stats.tasks_completed == 1

    def test_get_active_tasks(self, mock_runtime):
        """测试获取活跃任务"""
        done_task = MagicMock(spec=asyncio.Task)
        done_task.done.return_value = True

        active_task = MagicMock(spec=asyncio.Task)
        active_task.done.return_value = False

        mock_runtime._active_tasks = {"test:1": [done_task, active_task]}

        coordinator = SessionStateCoordinator(mock_runtime)
        tasks = coordinator.get_active_tasks("test:1")

        assert len(tasks) == 1
        assert active_task in tasks

    def test_get_active_tasks_empty(self, mock_runtime):
        """测试获取活跃任务 - 空的"""
        mock_runtime._active_tasks = {}

        coordinator = SessionStateCoordinator(mock_runtime)
        tasks = coordinator.get_active_tasks("test:1")

        assert tasks == []

    def test_has_active_tasks_true(self, mock_runtime):
        """测试有活跃任务"""
        active_task = MagicMock(spec=asyncio.Task)
        active_task.done.return_value = False
        mock_runtime._active_tasks = {"test:1": [active_task]}

        coordinator = SessionStateCoordinator(mock_runtime)
        assert coordinator.has_active_tasks("test:1") is True

    def test_has_active_tasks_false(self, mock_runtime):
        """测试无活跃任务"""
        mock_runtime._active_tasks = {}

        coordinator = SessionStateCoordinator(mock_runtime)
        assert coordinator.has_active_tasks("test:1") is False

    def test_cancel_active_tasks(self, mock_runtime):
        """测试取消活跃任务"""
        task1 = MagicMock(spec=asyncio.Task)
        task1.done.return_value = False
        task1.cancel = MagicMock()

        task2 = MagicMock(spec=asyncio.Task)
        task2.done.return_value = True

        mock_runtime._active_tasks = {"test:1": [task1, task2]}

        coordinator = SessionStateCoordinator(mock_runtime)
        cancelled = coordinator.cancel_active_tasks("test:1")

        assert cancelled == 1
        task1.cancel.assert_called_once()
        assert "test:1" not in mock_runtime._active_tasks

    def test_cancel_active_tasks_no_tasks(self, mock_runtime):
        """测试取消活跃任务 - 无任务"""
        mock_runtime._active_tasks = {}

        coordinator = SessionStateCoordinator(mock_runtime)
        cancelled = coordinator.cancel_active_tasks("test:1")

        assert cancelled == 0


class TestSessionStateCoordinatorLocks:
    """测试锁管理"""

    def test_get_lock_create(self, mock_runtime):
        """测试创建锁"""
        mock_runtime._session_locks = {}

        coordinator = SessionStateCoordinator(mock_runtime)
        lock = coordinator.get_lock("test:1")

        assert lock is not None
        assert "test:1" in mock_runtime._session_locks
        assert coordinator._stats.locks_created == 1

    def test_get_lock_existing(self, mock_runtime):
        """测试获取已存在的锁"""
        existing_lock = asyncio.Lock()
        mock_runtime._session_locks = {"test:1": existing_lock}

        coordinator = SessionStateCoordinator(mock_runtime)
        lock = coordinator.get_lock("test:1")

        assert lock is existing_lock
        assert coordinator._stats.locks_created == 0

    def test_release_lock(self, mock_runtime):
        """测试释放锁"""
        mock_runtime._session_locks = {"test:1": asyncio.Lock()}

        coordinator = SessionStateCoordinator(mock_runtime)
        result = coordinator.release_lock("test:1")

        assert result is True
        assert "test:1" not in mock_runtime._session_locks
        assert coordinator._stats.locks_released == 1

    def test_release_lock_not_exists(self, mock_runtime):
        """测试释放不存在的锁"""
        mock_runtime._session_locks = {}

        coordinator = SessionStateCoordinator(mock_runtime)
        result = coordinator.release_lock("test:1")

        assert result is False

    def test_has_lock_true(self, mock_runtime):
        """测试锁存在"""
        mock_runtime._session_locks = {"test:1": asyncio.Lock()}

        coordinator = SessionStateCoordinator(mock_runtime)
        assert coordinator.has_lock("test:1") is True

    def test_has_lock_false(self, mock_runtime):
        """测试锁不存在"""
        mock_runtime._session_locks = {}

        coordinator = SessionStateCoordinator(mock_runtime)
        assert coordinator.has_lock("test:1") is False


class TestSessionStateCoordinatorCleanup:
    """测试会话清理"""

    def test_cleanup_session(self, mock_runtime):
        """测试清理会话"""
        mock_runtime._state_machine._states = {"test:1": MagicMock()}

        task = MagicMock(spec=asyncio.Task)
        task.done.return_value = False
        task.cancel = MagicMock()
        mock_runtime._active_tasks = {"test:1": [task]}

        mock_runtime._session_locks = {"test:1": asyncio.Lock()}

        coordinator = SessionStateCoordinator(mock_runtime)
        result = coordinator.cleanup_session("test:1")

        assert result["tasks_cancelled"] == 1
        assert result["lock_released"] is True
        assert result["state_cleared"] is True
        assert "test:1" not in mock_runtime._state_machine._states
        assert "test:1" not in mock_runtime._active_tasks
        assert "test:1" not in mock_runtime._session_locks


class TestSessionStateCoordinatorStats:
    """测试统计功能"""

    def test_get_stats(self, mock_runtime):
        """测试获取统计"""
        coordinator = SessionStateCoordinator(mock_runtime)
        coordinator._stats.phase_transitions = 5

        stats = coordinator.get_stats()

        assert stats.phase_transitions == 5

    def test_reset_stats(self, mock_runtime):
        """测试重置统计"""
        coordinator = SessionStateCoordinator(mock_runtime)
        coordinator._stats.phase_transitions = 10

        coordinator.reset_stats()

        assert coordinator._stats.phase_transitions == 0


class TestSessionStateCoordinatorConsistency:
    """测试一致性检查"""

    def test_check_consistency_ok(self, mock_runtime):
        """测试一致性检查 - 正常"""
        snapshot = MagicMock()
        snapshot.is_consistent.return_value = True
        snapshot.inconsistencies = []
        mock_runtime._state_checker.check_session.return_value = snapshot

        coordinator = SessionStateCoordinator(mock_runtime)
        is_consistent, issues = coordinator.check_consistency("test:1")

        assert is_consistent is True
        assert issues == []

    def test_check_consistency_issues(self, mock_runtime):
        """测试一致性检查 - 有问题"""
        snapshot = MagicMock()
        snapshot.is_consistent.return_value = False
        snapshot.inconsistencies = ["Issue 1", "Issue 2"]
        mock_runtime._state_checker.check_session.return_value = snapshot

        coordinator = SessionStateCoordinator(mock_runtime)
        is_consistent, issues = coordinator.check_consistency("test:1")

        assert is_consistent is False
        assert issues == ["Issue 1", "Issue 2"]


class TestSessionStateCoordinatorExport:
    """测试导出功能"""

    def test_export_state(self, mock_runtime):
        """测试导出状态"""
        snapshot = MagicMock()
        snapshot.to_dict.return_value = {"phase": "idle"}
        mock_runtime._state_checker.check_session.return_value = snapshot

        coordinator = SessionStateCoordinator(mock_runtime)
        result = coordinator.export_state("test:1")

        assert result == {"phase": "idle"}


# === Fixtures ===

@pytest.fixture
def mock_runtime():
    """创建模拟的 AgentRuntime"""
    runtime = MagicMock()

    state_machine = MagicMock()
    state_machine._states = {}
    state_machine.get_phase = MagicMock()
    state_machine.get_state = MagicMock()
    state_machine.transition = MagicMock()
    state_machine.force_transition = MagicMock()
    runtime._state_machine = state_machine

    runtime._active_tasks = {}
    runtime._session_locks = {}

    state_checker = MagicMock()
    state_checker.check_session = MagicMock()
    runtime._state_checker = state_checker

    return runtime


class TestSessionStateCoordinatorAtomicOps:
    """测试原子操作"""

    @pytest.mark.asyncio
    async def test_atomic_start_dispatch(self, mock_runtime_with_state_machine):
        """测试原子性开始 dispatch"""
        coordinator = SessionStateCoordinator(mock_runtime_with_state_machine)

        task = MagicMock(spec=asyncio.Task)
        task.get_name.return_value = "test-task"

        result = await coordinator.atomic_start_dispatch("test:1", task)

        assert result is True
        phase = coordinator.get_phase("test:1")
        assert phase == SessionPhase.RUNNING

    @pytest.mark.asyncio
    async def test_atomic_end_dispatch(self, mock_runtime_with_state_machine):
        """测试原子性结束 dispatch"""
        coordinator = SessionStateCoordinator(mock_runtime_with_state_machine)

        task = MagicMock(spec=asyncio.Task)
        task.get_name.return_value = "test-task"
        task.done.return_value = True

        result = await coordinator.atomic_end_dispatch("test:1", task)

        assert result is True

    @pytest.mark.asyncio
    async def test_atomic_cleanup_session(self, mock_runtime_with_state_machine):
        """测试原子性清理会话"""
        coordinator = SessionStateCoordinator(mock_runtime_with_state_machine)

        coordinator.force_transition("test:1", SessionPhase.RUNNING, reason="test")
        coordinator.get_lock("test:1")

        result = await coordinator.atomic_cleanup_session("test:1")

        assert result["lock_released"] is True
        assert coordinator.get_phase("test:1") == SessionPhase.IDLE

    @pytest.mark.asyncio
    async def test_atomic_wait_permission(self, mock_runtime_with_state_machine):
        """测试原子性等待权限"""
        coordinator = SessionStateCoordinator(mock_runtime_with_state_machine)

        result = await coordinator.atomic_wait_permission("test:1", "perm-123")

        assert result is True
        assert coordinator.get_phase("test:1") == SessionPhase.WAITING_PERMISSION

    @pytest.mark.asyncio
    async def test_atomic_wait_interaction(self, mock_runtime_with_state_machine):
        """测试原子性等待交互"""
        coordinator = SessionStateCoordinator(mock_runtime_with_state_machine)

        result = await coordinator.atomic_wait_interaction("test:1", "inter-456")

        assert result is True
        assert coordinator.get_phase("test:1") == SessionPhase.WAITING_INTERACTION

    @pytest.mark.asyncio
    async def test_atomic_resume_from_wait(self, mock_runtime_with_state_machine):
        """测试原子性从等待恢复"""
        coordinator = SessionStateCoordinator(mock_runtime_with_state_machine)

        coordinator.force_transition("test:1", SessionPhase.WAITING_PERMISSION, reason="test")

        result = await coordinator.atomic_resume_from_wait("test:1")

        assert result is True
        assert coordinator.get_phase("test:1") == SessionPhase.RUNNING


# === Additional Fixtures ===

@pytest.fixture
def mock_runtime_with_state_machine():
    """创建带有真实状态机的 mock runtime"""
    runtime = MagicMock()

    runtime._state_machine = SessionStateMachine()
    runtime._active_tasks = {}
    runtime._session_locks = {}

    state_checker = MagicMock()
    snapshot = MagicMock()
    snapshot.is_consistent.return_value = True
    snapshot.inconsistencies = []
    state_checker.check_session.return_value = snapshot
    runtime._state_checker = state_checker

    return runtime