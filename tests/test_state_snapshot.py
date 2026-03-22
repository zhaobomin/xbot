"""测试状态快照数据结构。"""

import pytest
import time

from xbot.agent.state_snapshot import StateSnapshot


class TestStateSnapshotCreation:
    """测试 StateSnapshot 创建"""

    def test_create_with_session_key_only(self):
        """测试只用 session_key 创建"""
        snapshot = StateSnapshot(session_key="telegram:123")

        assert snapshot.session_key == "telegram:123"
        assert snapshot.runtime_phase == "idle"
        assert snapshot.runtime_active_tasks == 0
        assert snapshot.backend_has_client == False
        assert len(snapshot.inconsistencies) == 0

    def test_create_with_all_fields(self):
        """测试用所有字段创建"""
        now = time.time()
        snapshot = StateSnapshot(
            session_key="telegram:456",
            timestamp=now,
            runtime_phase="running",
            runtime_phase_reason="dispatch_started",
            runtime_active_tasks=2,
            runtime_has_lock=True,
            backend_has_client=True,
            backend_task_id="task-789",
            backend_last_used=now,
            bus_pending_permission=False,
            bus_pending_interaction=True,
            bus_pending_interaction_id="req-001",
            inconsistencies=["test issue"],
        )

        assert snapshot.session_key == "telegram:456"
        assert snapshot.timestamp == now
        assert snapshot.runtime_phase == "running"
        assert snapshot.runtime_active_tasks == 2
        assert snapshot.backend_has_client == True
        assert snapshot.backend_task_id == "task-789"
        assert "test issue" in snapshot.inconsistencies

    def test_create_empty_factory(self):
        """测试工厂方法创建空快照"""
        snapshot = StateSnapshot.create_empty("feishu:abc")

        assert snapshot.session_key == "feishu:abc"
        assert snapshot.runtime_phase == "idle"
        assert snapshot.is_consistent() == True


class TestStateSnapshotConsistency:
    """测试状态一致性检查"""

    def test_is_consistent_when_no_issues(self):
        """测试无不一致问题时返回 True"""
        snapshot = StateSnapshot(
            session_key="test:1",
            inconsistencies=[],
        )

        assert snapshot.is_consistent() == True

    def test_is_consistent_when_has_issues(self):
        """测试有不一致问题时返回 False"""
        snapshot = StateSnapshot(
            session_key="test:1",
            inconsistencies=["RUNNING but no backend client"],
        )

        assert snapshot.is_consistent() == False

    def test_multiple_issues(self):
        """测试多个不一致问题"""
        snapshot = StateSnapshot(
            session_key="test:1",
            inconsistencies=[
                "RUNNING but no backend client",
                "IDLE but has active tasks",
            ],
        )

        assert snapshot.is_consistent() == False
        assert len(snapshot.inconsistencies) == 2


class TestStateSnapshotSerialization:
    """测试序列化"""

    def test_to_dict_contains_all_sections(self):
        """测试 to_dict 包含所有部分"""
        snapshot = StateSnapshot(
            session_key="test:1",
            runtime_phase="running",
            backend_has_client=True,
        )

        d = snapshot.to_dict()

        assert "session_key" in d
        assert "timestamp" in d
        assert "runtime" in d
        assert "backend" in d
        assert "bus" in d
        assert "inconsistencies" in d
        assert "is_consistent" in d

    def test_to_dict_runtime_section(self):
        """测试 to_dict runtime 部分"""
        snapshot = StateSnapshot(
            session_key="test:1",
            runtime_phase="waiting_permission",
            runtime_phase_reason="tool_permission",
            runtime_active_tasks=1,
            runtime_has_lock=True,
        )

        d = snapshot.to_dict()

        assert d["runtime"]["phase"] == "waiting_permission"
        assert d["runtime"]["phase_reason"] == "tool_permission"
        assert d["runtime"]["active_tasks"] == 1
        assert d["runtime"]["has_lock"] == True

    def test_to_dict_backend_section(self):
        """测试 to_dict backend 部分"""
        snapshot = StateSnapshot(
            session_key="test:1",
            backend_has_client=True,
            backend_task_id="task-123",
        )

        d = snapshot.to_dict()

        assert d["backend"]["has_client"] == True
        assert d["backend"]["task_id"] == "task-123"

    def test_to_dict_bus_section(self):
        """测试 to_dict bus 部分"""
        snapshot = StateSnapshot(
            session_key="test:1",
            bus_pending_permission=True,
            bus_pending_permission_id="req-456",
            bus_pending_interaction=True,
            bus_pending_interaction_id="int-789",
        )

        d = snapshot.to_dict()

        assert d["bus"]["pending_permission"] == True
        assert d["bus"]["pending_permission_id"] == "req-456"
        assert d["bus"]["pending_interaction"] == True
        assert d["bus"]["pending_interaction_id"] == "int-789"


class TestStateSnapshotSummary:
    """测试摘要生成"""

    def test_summary_basic(self):
        """测试基本摘要"""
        snapshot = StateSnapshot(
            session_key="telegram:123",
            runtime_phase="idle",
            runtime_active_tasks=0,
            backend_has_client=False,
        )

        summary = snapshot.summary()

        assert "telegram:123" in summary
        assert "idle" in summary
        assert "0tasks" in summary
        assert "no_client" in summary

    def test_summary_with_client(self):
        """测试有 client 的摘要"""
        snapshot = StateSnapshot(
            session_key="telegram:123",
            runtime_phase="running",
            runtime_active_tasks=1,
            backend_has_client=True,
        )

        summary = snapshot.summary()

        assert "has_client" in summary

    def test_summary_with_issues(self):
        """测试有问题的摘要"""
        snapshot = StateSnapshot(
            session_key="telegram:123",
            runtime_phase="running",
            runtime_active_tasks=0,
            backend_has_client=False,
            inconsistencies=["RUNNING but no backend client"],
        )

        summary = snapshot.summary()

        assert "1issues" in summary


class TestStateSnapshotDefaults:
    """测试默认值"""

    def test_default_timestamp_is_recent(self):
        """测试默认时间戳是当前时间"""
        before = time.time()
        snapshot = StateSnapshot(session_key="test:1")
        after = time.time()

        assert before <= snapshot.timestamp <= after

    def test_default_phase_is_idle(self):
        """测试默认阶段是 idle"""
        snapshot = StateSnapshot(session_key="test:1")

        assert snapshot.runtime_phase == "idle"

    def test_default_no_tasks(self):
        """测试默认没有活跃任务"""
        snapshot = StateSnapshot(session_key="test:1")

        assert snapshot.runtime_active_tasks == 0

    def test_default_no_client(self):
        """测试默认没有 client"""
        snapshot = StateSnapshot(session_key="test:1")

        assert snapshot.backend_has_client == False
        assert snapshot.backend_task_id is None

    def test_default_no_pending_requests(self):
        """测试默认没有待处理请求"""
        snapshot = StateSnapshot(session_key="test:1")

        assert snapshot.bus_pending_permission == False
        assert snapshot.bus_pending_interaction == False