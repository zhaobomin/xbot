# xbot 状态管理演进 - 任务拆分

> 版本: 1.0
> 创建日期: 2026-03-22
> 预计总工时: 40-50 小时 (约 2-3 周)

---

## 任务概览

```
┌─────────────────────────────────────────────────────────────────────┐
│  任务依赖关系图                                                      │
│                                                                     │
│  T1 ──▶ T2 ──▶ T3                                                  │
│  │      │                                                          │
│  │      └──▶ T4 ──▶ T5                                             │
│  │                    │                                             │
│  │                    └──▶ T6 ──▶ T7                                │
│  │                                  │                               │
│  │                                  └──▶ T8 ──▶ T9                  │
│  │                                                │                  │
│  │                                                └──▶ T10           │
│  │                                                      │            │
│  └──▶ T11 ─────────────────────────────────────────────┘            │
│                                                                     │
│  T1-T3:  Phase 0 (检查器)    - 风险: 🟢 极低                        │
│  T4-T5:  Phase 4 (监控)      - 风险: 🟢 极低                        │
│  T6-T7:  Phase 1 (Coordinator) - 风险: 🟡 中                        │
│  T8-T9:  Phase 3 (事务)      - 风险: 🟡 中                          │
│  T10:    Phase 2 (SDK适配)   - 风险: 🟡 中                          │
│  T11:    清理旧代码          - 风险: 🟢 低                           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 第一阶段：状态检查器 (风险：🟢 极低)

### T1: 实现状态快照数据结构

**目标**: 定义状态快照的数据结构，用于一致性检查

**文件**: `xbot/agent/state_snapshot.py` (新建)

**代码框架**:
```python
"""状态快照数据结构，用于一致性检查和日志记录。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import time


@dataclass
class StateSnapshot:
    """某一时刻的完整状态快照"""

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
        """检查状态是否一致"""
        return len(self.inconsistencies) == 0

    def to_dict(self) -> dict[str, Any]:
        """转换为字典，用于日志和序列化"""
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
        }
```

**预计时间**: 1 小时

**风险**: 🟢 极低 (只添加数据结构，不影响现有代码)

**验收标准**:
- [ ] 文件创建成功
- [ ] 数据结构可以正常实例化
- [ ] `to_dict()` 方法正确工作
- [ ] 类型检查通过

**测试代码**: `tests/test_state_snapshot.py`
```python
def test_state_snapshot_creation():
    snapshot = StateSnapshot(
        session_key="test:session",
        runtime_phase="running",
    )
    assert snapshot.session_key == "test:session"
    assert snapshot.runtime_phase == "running"
    assert snapshot.is_consistent() == True

def test_state_snapshot_to_dict():
    snapshot = StateSnapshot(session_key="test:session")
    d = snapshot.to_dict()
    assert "session_key" in d
    assert "runtime" in d
    assert "backend" in d
    assert "bus" in d
```

---

### T2: 实现状态一致性检查器

**目标**: 实现检查状态一致性的逻辑

**文件**: `xbot/agent/state_checker.py` (新建)

**代码框架**:
```python
"""状态一致性检查器。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from loguru import logger

from xbot.agent.state_snapshot import StateSnapshot

if TYPE_CHECKING:
    from xbot.agent.runtime import AgentRuntime


# 状态一致性规则定义
CONSISTENCY_RULES = [
    {
        "id": "running_requires_client",
        "condition": lambda s: s.runtime_phase == "running",
        "requirement": lambda s: s.backend_has_client,
        "message": "RUNNING but no backend client",
    },
    {
        "id": "waiting_permission_requires_request",
        "condition": lambda s: s.runtime_phase == "waiting_permission",
        "requirement": lambda s: s.bus_pending_permission,
        "message": "WAITING_PERMISSION but no pending request",
    },
    {
        "id": "waiting_interaction_requires_request",
        "condition": lambda s: s.runtime_phase == "waiting_interaction",
        "requirement": lambda s: s.bus_pending_interaction,
        "message": "WAITING_INTERACTION but no pending request",
    },
    {
        "id": "idle_no_active_tasks",
        "condition": lambda s: s.runtime_phase == "idle",
        "requirement": lambda s: s.runtime_active_tasks == 0,
        "message": "IDLE but has active tasks",
    },
    {
        "id": "idle_no_backend_task",
        "condition": lambda s: s.runtime_phase == "idle",
        "requirement": lambda s: s.backend_task_id is None,
        "message": "IDLE but has backend task_id",
    },
    {
        "id": "client_requires_lock",
        "condition": lambda s: s.backend_has_client,
        "requirement": lambda s: s.runtime_has_lock,
        "message": "Has client but no session lock",
    },
]


class StateConsistencyChecker:
    """状态一致性检查器"""

    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime

    def check_session(self, session_key: str) -> StateSnapshot:
        """检查单个 session 的状态一致性"""
        snapshot = self._capture_snapshot(session_key)
        self._detect_inconsistencies(snapshot)
        return snapshot

    def _capture_snapshot(self, session_key: str) -> StateSnapshot:
        """捕获当前状态快照"""
        # 获取 Runtime 状态
        phase = self._runtime._state_machine.get_phase(session_key)
        tasks = self._runtime._active_tasks.get(session_key, [])
        has_lock = session_key in self._runtime._session_locks

        # 获取 Backend 状态
        backend = self._runtime.router._backend
        has_client = session_key in backend._clients if backend else False
        task_id = backend._active_task_ids.get(session_key) if backend else None
        last_used = backend._client_last_used.get(session_key) if backend else None

        # 获取 Bus 状态
        bus = self._runtime.bus
        pending_permission = bool(bus and bus.get_pending_request_for_session(session_key))
        pending_interaction = bool(bus and bus.get_pending_interaction_for_session(session_key))

        return StateSnapshot(
            session_key=session_key,
            runtime_phase=phase.value,
            runtime_active_tasks=len([t for t in tasks if not t.done()]),
            runtime_has_lock=has_lock,
            backend_has_client=has_client,
            backend_task_id=task_id,
            backend_last_used=last_used,
            bus_pending_permission=pending_permission,
            bus_pending_interaction=pending_interaction,
        )

    def _detect_inconsistencies(self, snapshot: StateSnapshot) -> None:
        """检测状态不一致"""
        for rule in CONSISTENCY_RULES:
            try:
                if rule["condition"](snapshot) and not rule["requirement"](snapshot):
                    snapshot.inconsistencies.append(rule["message"])
            except Exception as e:
                logger.debug(f"Rule {rule['id']} check failed: {e}")

    def check_all_sessions(self) -> list[StateSnapshot]:
        """检查所有 session"""
        snapshots = []
        for session_key in self._get_all_session_keys():
            snapshot = self.check_session(session_key)
            if not snapshot.is_consistent():
                snapshots.append(snapshot)
        return snapshots

    def _get_all_session_keys(self) -> set[str]:
        """获取所有已知的 session key"""
        keys = set()

        # 从状态机获取
        keys.update(self._runtime._state_machine._states.keys())

        # 从活跃任务获取
        keys.update(self._runtime._active_tasks.keys())

        # 从 session locks 获取
        keys.update(self._runtime._session_locks.keys())

        # 从 backend 获取
        backend = self._runtime.router._backend
        if backend:
            keys.update(backend._clients.keys())

        return keys
```

**预计时间**: 2 小时

**风险**: 🟢 极低 (只读操作，不修改状态)

**依赖**: T1

**验收标准**:
- [ ] 检查器可以正确捕获状态快照
- [ ] 所有规则都能正确执行
- [ ] 不一致情况能被正确检测
- [ ] 不影响现有功能

**测试代码**: `tests/test_state_checker.py`
```python
import pytest
from xbot.agent.state_checker import StateConsistencyChecker, CONSISTENCY_RULES

def test_consistency_rules_defined():
    """验证所有规则都有必要字段"""
    for rule in CONSISTENCY_RULES:
        assert "id" in rule
        assert "condition" in rule
        assert "requirement" in rule
        assert "message" in rule

async def test_checker_detects_inconsistency(runtime, session_key):
    """测试检测到不一致"""
    # 模拟不一致状态：RUNNING 但没有 client
    runtime._state_machine.force_transition(session_key, SessionPhase.RUNNING)

    checker = StateConsistencyChecker(runtime)
    snapshot = checker.check_session(session_key)

    assert not snapshot.is_consistent()
    assert "RUNNING but no backend client" in snapshot.inconsistencies

async def test_checker_accepts_consistent_state(runtime, session_key):
    """测试一致状态通过检查"""
    # 初始 IDLE 状态应该是一致的
    checker = StateConsistencyChecker(runtime)
    snapshot = checker.check_session(session_key)

    assert snapshot.is_consistent()
```

---

### T3: 集成检查器到 Runtime

**目标**: 在 Runtime 中集成检查器，添加状态变更日志

**文件**: `xbot/agent/runtime.py` (修改)

**改动点**:

1. 导入检查器
2. 在 `__init__` 中创建检查器实例
3. 添加 `_log_state_snapshot` 方法
4. 在关键操作后调用检查

**代码改动**:
```python
# 在文件顶部添加导入
from xbot.agent.state_checker import StateConsistencyChecker
from xbot.agent.state_snapshot import StateSnapshot

class AgentRuntime:
    def __init__(self, config: Any, shared_resources: dict[str, Any]):
        # ... 现有初始化 ...

        # 新增: 状态一致性检查器
        self._state_checker = StateConsistencyChecker(self)
        self._state_check_enabled = True  # 功能开关

    async def _log_state_snapshot(self, session_key: str, event: str) -> None:
        """记录状态快照到 trace"""
        if not self._state_check_enabled:
            return

        snapshot = self._state_checker.check_session(session_key)

        append_session_trace(
            self.sessions,
            session_key,
            f"state_snapshot_{event}",
            snapshot.to_dict(),
        )

        # 如果检测到不一致，记录警告
        if not snapshot.is_consistent():
            logger.warning(
                f"State inconsistency detected for {session_key}: "
                f"{snapshot.inconsistencies}"
            )

    # 在 _dispatch 中添加检查
    async def _dispatch(self, msg: InboundMessage) -> None:
        # ... 开始时检查 ...
        await self._log_state_snapshot(msg.session_key, "dispatch_start")

        try:
            # ... 现有逻辑 ...
        finally:
            # ... 结束时检查 ...
            await self._log_state_snapshot(msg.session_key, "dispatch_end")
            self._sync_session_phase(msg.session_key)
```

**预计时间**: 1.5 小时

**风险**: 🟢 极低 (只添加日志，不影响逻辑)

**依赖**: T2

**验收标准**:
- [ ] Runtime 可以正常初始化检查器
- [ ] 状态快照正确记录到 trace
- [ ] 不一致情况有警告日志
- [ ] 所有现有测试通过

**测试**: 运行现有测试套件，确保无回归
```bash
pytest tests/ -v
# 预期: 929 passed
```

---

## 第二阶段：监控指标 (风险：🟢 极低)

### T4: 实现状态指标收集器

**目标**: 收集和暴露状态相关的指标

**文件**: `xbot/agent/state_metrics.py` (新建)

**代码框架**:
```python
"""状态指标收集器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
import time

if TYPE_CHECKING:
    from xbot.agent.runtime import AgentRuntime


@dataclass
class StateMetrics:
    """状态指标"""

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
        """转换为 Prometheus 格式"""
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


class StateMetricsCollector:
    """状态指标收集器"""

    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime
        self._last_metrics: StateMetrics | None = None

    def collect(self) -> StateMetrics:
        """收集当前指标"""
        # 初始化 phase 计数
        from xbot.agent.runtime import SessionPhase
        sessions_by_phase = {phase.value: 0 for phase in SessionPhase}

        total_sessions = 0
        sessions_with_inconsistencies = 0
        active_backend_clients = 0
        pending_permissions = 0
        pending_interactions = 0

        # 统计各 session
        for session_key in self._get_all_session_keys():
            total_sessions += 1

            snapshot = self._runtime._state_checker.check_session(session_key)
            sessions_by_phase[snapshot.runtime_phase] = sessions_by_phase.get(snapshot.runtime_phase, 0) + 1

            if not snapshot.is_consistent():
                sessions_with_inconsistencies += 1

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
        """获取所有 session key"""
        return self._runtime._state_checker._get_all_session_keys()

    def get_last_metrics(self) -> StateMetrics | None:
        """获取上次收集的指标"""
        return self._last_metrics
```

**预计时间**: 1.5 小时

**风险**: 🟢 极低 (只读操作)

**依赖**: T3

**验收标准**:
- [ ] 指标可以正确收集
- [ ] Prometheus 格式输出正确
- [ ] 不影响性能

**测试**: `tests/test_state_metrics.py`
```python
async def test_metrics_collector(runtime):
    """测试指标收集"""
    collector = StateMetricsCollector(runtime)
    metrics = collector.collect()

    assert metrics.total_sessions >= 0
    assert "idle" in metrics.sessions_by_phase
    assert metrics.to_prometheus_format().startswith("xbot_sessions_total")
```

---

### T5: 添加健康检查服务

**目标**: 定期检查状态健康并告警

**文件**: `xbot/agent/state_health.py` (新建)

**代码框架**:
```python
"""状态健康检查服务。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from xbot.agent.runtime import AgentRuntime
    from xbot.agent.state_metrics import StateMetricsCollector


class StateHealthCheckService:
    """状态健康检查服务"""

    def __init__(
        self,
        runtime: AgentRuntime,
        metrics_collector: StateMetricsCollector,
        check_interval: float = 60.0,
        alert_threshold: int = 3,
    ):
        self._runtime = runtime
        self._metrics_collector = metrics_collector
        self._check_interval = check_interval
        self._alert_threshold = alert_threshold

        self._running = False
        self._task: asyncio.Task | None = None
        self._consecutive_issues = 0
        self._last_check_result: dict | None = None

    async def start(self) -> None:
        """启动健康检查"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._check_loop())
        logger.info(f"State health check service started (interval={self._check_interval}s)")

    async def stop(self) -> None:
        """停止健康检查"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("State health check service stopped")

    async def _check_loop(self) -> None:
        """检查循环"""
        while self._running:
            try:
                await asyncio.sleep(self._check_interval)
                await self._perform_check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check failed: {e}")

    async def _perform_check(self) -> dict:
        """执行检查"""
        # 收集指标
        metrics = self._metrics_collector.collect()

        # 检查不一致
        inconsistent_sessions = self._runtime._state_checker.check_all_sessions()

        result = {
            "total_sessions": metrics.total_sessions,
            "inconsistent_count": len(inconsistent_sessions),
            "inconsistent_sessions": [
                {"session_key": s.session_key, "issues": s.inconsistencies}
                for s in inconsistent_sessions[:10]  # 最多返回 10 个
            ],
            "metrics": metrics,
            "timestamp": time.time(),
        }

        self._last_check_result = result

        # 告警逻辑
        if inconsistent_sessions:
            self._consecutive_issues += 1
            if self._consecutive_issues >= self._alert_threshold:
                logger.error(
                    f"State health alert: {len(inconsistent_sessions)} sessions inconsistent "
                    f"(consecutive: {self._consecutive_issues})"
                )
                # TODO: 发送告警通知
        else:
            self._consecutive_issues = 0

        return result

    def get_last_result(self) -> dict | None:
        """获取上次检查结果"""
        return self._last_check_result
```

**预计时间**: 1.5 小时

**风险**: 🟢 极低 (独立服务，不修改核心逻辑)

**依赖**: T4

**验收标准**:
- [ ] 服务可以正常启动和停止
- [ ] 定期执行检查
- [ ] 不一致情况有告警日志
- [ ] 不影响主流程性能

**测试**: `tests/test_state_health.py`
```python
async def test_health_check_service(runtime):
    """测试健康检查服务"""
    from xbot.agent.state_metrics import StateMetricsCollector

    collector = StateMetricsCollector(runtime)
    service = StateHealthCheckService(runtime, collector, check_interval=1.0)

    await service.start()
    await asyncio.sleep(2)  # 等待检查执行

    result = service.get_last_result()
    assert result is not None
    assert "total_sessions" in result

    await service.stop()
```

---

## 第三阶段：统一状态协调器 (风险：🟡 中)

### T6: 实现会话状态数据结构

**目标**: 定义统一的会话状态结构

**文件**: `xbot/agent/session_state.py` (新建)

**代码框架**:
```python
"""统一的会话状态数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import time

from xbot.agent.runtime import SessionPhase


@dataclass
class RuntimeState:
    """Runtime 组件状态"""
    phase: SessionPhase = SessionPhase.IDLE
    phase_reason: str = ""
    active_tasks: int = 0
    has_lock: bool = False


@dataclass
class BackendState:
    """Backend 组件状态"""
    has_client: bool = False
    task_id: str | None = None
    last_used: float | None = None
    reconnect_pending: bool = False
    last_error: str | None = None


@dataclass
class BusState:
    """Bus 组件状态"""
    pending_permission_request: str | None = None
    pending_interaction_request: str | None = None


@dataclass
class SessionState:
    """统一的会话状态"""

    # 基本信息
    session_key: str

    # 各组件状态
    runtime: RuntimeState = field(default_factory=RuntimeState)
    backend: BackendState = field(default_factory=BackendState)
    bus: BusState = field(default_factory=BusState)

    # 元数据
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    version: int = 0

    # SDK 信息
    sdk_session_id: str | None = None

    def increment_version(self) -> None:
        """增加版本号"""
        self.version += 1
        self.updated_at = time.time()

    def check_inconsistencies(self) -> list[str]:
        """检查状态不一致"""
        issues = []

        # 规则 1: RUNNING 状态必须有 client
        if self.runtime.phase == SessionPhase.RUNNING and not self.backend.has_client:
            issues.append("RUNNING but no backend client")

        # 规则 2: WAITING_PERMISSION 必须有 pending request
        if self.runtime.phase == SessionPhase.WAITING_PERMISSION:
            if not self.bus.pending_permission_request:
                issues.append("WAITING_PERMISSION but no pending request")

        # 规则 3: WAITING_INTERACTION 必须有 pending request
        if self.runtime.phase == SessionPhase.WAITING_INTERACTION:
            if not self.bus.pending_interaction_request:
                issues.append("WAITING_INTERACTION but no pending request")

        # 规则 4: IDLE 状态不应该有活跃任务
        if self.runtime.phase == SessionPhase.IDLE:
            if self.runtime.active_tasks > 0:
                issues.append("IDLE but has active tasks")
            if self.backend.task_id:
                issues.append("IDLE but has backend task_id")

        # 规则 5: 有 client 就应该有锁
        if self.backend.has_client and not self.runtime.has_lock:
            issues.append("Has client but no session lock")

        return issues

    def is_consistent(self) -> bool:
        """状态是否一致"""
        return len(self.check_inconsistencies()) == 0

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "session_key": self.session_key,
            "runtime": {
                "phase": self.runtime.phase.value,
                "phase_reason": self.runtime.phase_reason,
                "active_tasks": self.runtime.active_tasks,
                "has_lock": self.runtime.has_lock,
            },
            "backend": {
                "has_client": self.backend.has_client,
                "task_id": self.backend.task_id,
                "last_used": self.backend.last_used,
                "reconnect_pending": self.backend.reconnect_pending,
                "last_error": self.backend.last_error,
            },
            "bus": {
                "pending_permission_request": self.bus.pending_permission_request,
                "pending_interaction_request": self.bus.pending_interaction_request,
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "sdk_session_id": self.sdk_session_id,
        }
```

**预计时间**: 1.5 小时

**风险**: 🟢 极低 (只添加数据结构)

**验收标准**:
- [ ] 数据结构可以正常实例化
- [ ] 一致性检查规则正确
- [ ] 序列化/反序列化正确

**测试**: `tests/test_session_state.py`
```python
def test_session_state_creation():
    state = SessionState(session_key="test:session")
    assert state.runtime.phase == SessionPhase.IDLE
    assert state.is_consistent()

def test_session_state_inconsistency_detection():
    state = SessionState(
        session_key="test:session",
        runtime=RuntimeState(phase=SessionPhase.RUNNING),
    )
    issues = state.check_inconsistencies()
    assert "RUNNING but no backend client" in issues
    assert not state.is_consistent()

def test_session_state_version_increment():
    state = SessionState(session_key="test:session")
    v1 = state.version
    state.increment_version()
    assert state.version == v1 + 1
```

---

### T7: 实现 SessionStateCoordinator

**目标**: 实现统一的状态协调器

**文件**: `xbot/agent/session_coordinator.py` (新建)

**代码框架**:
```python
"""会话状态协调器。"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Any
from loguru import logger

from xbot.agent.session_state import (
    SessionState,
    RuntimeState,
    BackendState,
    BusState,
)
from xbot.agent.runtime import SessionPhase


@dataclass
class StateCheckpoint:
    """状态快照"""
    checkpoint_id: str
    session_key: str
    state: SessionState
    created_at: float


class SessionStateCoordinator:
    """
    会话状态协调器

    职责:
    1. 统一状态存储和访问
    2. 状态变更通知
    3. Checkpoint 管理
    4. 一致性检查
    """

    MAX_CHECKPOINTS_PER_SESSION = 10

    def __init__(self):
        self._states: dict[str, SessionState] = {}
        self._checkpoints: dict[str, list[StateCheckpoint]] = {}
        self._lock = asyncio.Lock()

        # 状态变更回调
        self._on_state_change: Callable[[str, SessionState, SessionState], None] | None = None

    # === 基础操作 ===

    async def get_or_create(self, session_key: str) -> SessionState:
        """获取或创建会话状态"""
        async with self._lock:
            if session_key not in self._states:
                self._states[session_key] = SessionState(session_key=session_key)
            return self._states[session_key]

    async def get(self, session_key: str) -> SessionState | None:
        """获取会话状态（不创建）"""
        async with self._lock:
            return self._states.get(session_key)

    async def update(
        self,
        session_key: str,
        *,
        runtime: RuntimeState | None = None,
        backend: BackendState | None = None,
        bus: BusState | None = None,
        reason: str = "",
    ) -> SessionState:
        """原子性更新会话状态"""
        async with self._lock:
            state = await self._get_or_create_unlocked(session_key)

            # 保存旧状态用于回调
            old_state = self._clone_state(state)

            # 应用更新
            if runtime is not None:
                state.runtime = runtime
            if backend is not None:
                state.backend = backend
            if bus is not None:
                state.bus = bus

            if reason:
                state.runtime.phase_reason = reason

            state.increment_version()

            # 检查一致性
            issues = state.check_inconsistencies()
            if issues:
                logger.warning(
                    f"State inconsistency after update for {session_key}: {issues}"
                )

            # 通知变更
            if self._on_state_change:
                try:
                    self._on_state_change(session_key, old_state, state)
                except Exception as e:
                    logger.debug(f"State change callback error: {e}")

            return state

    async def clear_session(self, session_key: str) -> None:
        """清除会话状态"""
        async with self._lock:
            self._states.pop(session_key, None)
            self._checkpoints.pop(session_key, None)

    # === 便捷方法 ===

    async def set_phase(
        self,
        session_key: str,
        phase: SessionPhase,
        reason: str = "",
    ) -> SessionState:
        """设置会话阶段"""
        state = await self.get_or_create(session_key)

        new_runtime = RuntimeState(
            phase=phase,
            phase_reason=reason,
            active_tasks=state.runtime.active_tasks,
            has_lock=state.runtime.has_lock,
        )

        return await self.update(session_key, runtime=new_runtime, reason=reason)

    async def set_backend_client(
        self,
        session_key: str,
        has_client: bool,
        task_id: str | None = None,
    ) -> SessionState:
        """设置 backend 客户端状态"""
        state = await self.get_or_create(session_key)

        new_backend = BackendState(
            has_client=has_client,
            task_id=task_id,
            last_used=time.time() if has_client else state.backend.last_used,
            reconnect_pending=state.backend.reconnect_pending,
            last_error=state.backend.last_error,
        )

        return await self.update(session_key, backend=new_backend)

    async def set_pending_permission(
        self,
        session_key: str,
        request_id: str | None,
    ) -> SessionState:
        """设置 pending 权限请求"""
        state = await self.get_or_create(session_key)

        new_bus = BusState(
            pending_permission_request=request_id,
            pending_interaction_request=state.bus.pending_interaction_request,
        )

        return await self.update(session_key, bus=new_bus)

    # === Checkpoint 管理 ===

    async def save_checkpoint(self, session_key: str) -> str | None:
        """保存状态快照"""
        async with self._lock:
            state = self._states.get(session_key)
            if state is None:
                return None

            checkpoint = StateCheckpoint(
                checkpoint_id=str(uuid.uuid4()),
                session_key=session_key,
                state=self._clone_state(state),
                created_at=time.time(),
            )

            if session_key not in self._checkpoints:
                self._checkpoints[session_key] = []

            self._checkpoints[session_key].append(checkpoint)

            # 限制数量
            if len(self._checkpoints[session_key]) > self.MAX_CHECKPOINTS_PER_SESSION:
                self._checkpoints[session_key].pop(0)

            return checkpoint.checkpoint_id

    async def restore_checkpoint(self, session_key: str, checkpoint_id: str) -> bool:
        """恢复到指定快照"""
        async with self._lock:
            checkpoints = self._checkpoints.get(session_key, [])
            for cp in checkpoints:
                if cp.checkpoint_id == checkpoint_id:
                    restored = self._clone_state(cp.state)
                    restored.increment_version()
                    self._states[session_key] = restored
                    return True
            return False

    async def list_checkpoints(self, session_key: str) -> list[StateCheckpoint]:
        """列出所有快照"""
        return self._checkpoints.get(session_key, [])

    # === 内部方法 ===

    async def _get_or_create_unlocked(self, session_key: str) -> SessionState:
        """不加锁的获取或创建"""
        if session_key not in self._states:
            self._states[session_key] = SessionState(session_key=session_key)
        return self._states[session_key]

    def _clone_state(self, state: SessionState) -> SessionState:
        """克隆状态"""
        return SessionState(
            session_key=state.session_key,
            runtime=RuntimeState(
                phase=state.runtime.phase,
                phase_reason=state.runtime.phase_reason,
                active_tasks=state.runtime.active_tasks,
                has_lock=state.runtime.has_lock,
            ),
            backend=BackendState(
                has_client=state.backend.has_client,
                task_id=state.backend.task_id,
                last_used=state.backend.last_used,
                reconnect_pending=state.backend.reconnect_pending,
                last_error=state.backend.last_error,
            ),
            bus=BusState(
                pending_permission_request=state.bus.pending_permission_request,
                pending_interaction_request=state.bus.pending_interaction_request,
            ),
            created_at=state.created_at,
            updated_at=state.updated_at,
            version=state.version,
            sdk_session_id=state.sdk_session_id,
        )
```

**预计时间**: 3 小时

**风险**: 🟡 中 (核心组件)

**依赖**: T6

**验收标准**:
- [ ] 所有操作线程安全
- [ ] 状态更新原子性
- [ ] Checkpoint 保存/恢复正确
- [ ] 一致性检查有效

**测试**: `tests/test_session_coordinator.py` (详细测试用例见文档)

---

### T7.1: 集成 Coordinator 到 Runtime (影子模式)

**目标**: 在 Runtime 中集成 Coordinator，但使用影子模式

**文件**: `xbot/agent/runtime.py` (修改)

**改动点**:

```python
# 添加导入
from xbot.agent.session_coordinator import SessionStateCoordinator

class AgentRuntime:
    def __init__(self, config: Any, shared_resources: dict[str, Any]):
        # ... 现有初始化 ...

        # 新增: 状态协调器 (影子模式)
        self._coordinator = SessionStateCoordinator()
        self._coordinator_shadow_mode = True  # 影子模式开关

        # 设置状态变更回调
        self._coordinator._on_state_change = self._on_coordinator_state_change

    def _on_coordinator_state_change(
        self,
        session_key: str,
        old_state: SessionState,
        new_state: SessionState,
    ) -> None:
        """Coordinator 状态变更回调"""
        # 记录到 trace
        append_session_trace(
            self.sessions,
            session_key,
            "coordinator_state_change",
            {
                "old_phase": old_state.runtime.phase.value,
                "new_phase": new_state.runtime.phase.value,
                "version": new_state.version,
            },
        )

        # 影子模式下对比差异
        if self._coordinator_shadow_mode:
            current_phase = self._state_machine.get_phase(session_key)
            if current_phase != new_state.runtime.phase:
                logger.warning(
                    f"Shadow mode phase mismatch for {session_key}: "
                    f"state_machine={current_phase.value}, coordinator={new_state.runtime.phase.value}"
                )

    async def _set_session_phase(self, session_key: str, phase: SessionPhase, *, reason: str = "") -> None:
        """设置会话阶段"""
        # 现有逻辑 (生产路径)
        self._state_machine.transition(session_key, phase, reason=reason, force=True)

        # 新逻辑 (影子路径)
        if self._coordinator is not None:
            try:
                await self._coordinator.set_phase(session_key, phase, reason)
            except Exception as e:
                if not self._coordinator_shadow_mode:
                    raise
                logger.debug(f"Coordinator shadow mode error (ignored): {e}")
```

**预计时间**: 2 小时

**风险**: 🟡 中 (但影子模式降低风险)

**依赖**: T7

**验收标准**:
- [ ] Runtime 正常初始化
- [ ] 影子模式不影响生产
- [ ] 状态差异被记录
- [ ] 所有测试通过

---

## 第四阶段：事务支持 (风险：🟡 中)

### T8: 实现状态事务

**目标**: 实现事务性的状态更新

**文件**: `xbot/agent/state_transaction.py` (新建)

**代码框架**: 见之前文档

**预计时间**: 2.5 小时

**风险**: 🟡 中

**依赖**: T7.1

**验收标准**:
- [ ] 事务可以正常提交
- [ ] 异常时自动回滚
- [ ] 检查点恢复正确
- [ ] 无死锁

---

### T9: 使用事务重构 _terminate_session

**目标**: 使用事务重构关键操作

**文件**: `xbot/agent/runtime.py` (修改)

**预计时间**: 2 小时

**风险**: 🟡 中

**依赖**: T8

**验收标准**:
- [ ] 终止流程正确
- [ ] 异常时状态回滚
- [ ] 所有测试通过

---

## 第五阶段：清理 (风险：🟢 低)

### T10: 移除旧的状态机代码

**目标**: 稳定运行后移除旧代码

**前置条件**: T7.1 影子模式运行 7 天无问题

**预计时间**: 2 小时

**风险**: 🟢 低 (已验证)

**验收标准**:
- [ ] 所有功能正常
- [ ] 代码更简洁
- [ ] 测试通过

---

### T11: 更新文档

**目标**: 更新相关文档

**文件**:
- `docs/ARCHITECTURE_FIX_PLAN.md`
- `docs/STATE_EVOLUTION_PLAN.md`
- `README.md`

**预计时间**: 1 小时

---

## 任务执行顺序

```
Week 1:
├── T1: 状态快照数据结构 (1h) 🟢
├── T2: 状态一致性检查器 (2h) 🟢
├── T3: 集成检查器到 Runtime (1.5h) 🟢
├── T4: 状态指标收集器 (1.5h) 🟢
└── T5: 健康检查服务 (1.5h) 🟢

Week 2:
├── T6: 会话状态数据结构 (1.5h) 🟢
├── T7: SessionStateCoordinator (3h) 🟡
└── T7.1: 集成到 Runtime 影子模式 (2h) 🟡

Week 3 (可选，验证稳定后):
├── T8: 状态事务 (2.5h) 🟡
├── T9: 重构 _terminate_session (2h) 🟡
├── T10: 移除旧代码 (2h) 🟢
└── T11: 更新文档 (1h) 🟢
```

---

## 验证检查点

每个任务完成后执行:

```bash
# 1. 单元测试
pytest tests/test_state_*.py -v

# 2. 全量测试
pytest tests/ -v

# 3. 类型检查
mypy xbot/agent/state_*.py

# 4. 集成验证
python -c "from xbot.agent.runtime import AgentRuntime; print('OK')"
```

---

## 回滚方案

任何阶段出现问题:

1. **功能开关关闭**: 设置相应的 `_xxx_enabled = False`
2. **重启服务**: 旧逻辑继续运行
3. **代码回滚**: 如需要，git revert 到上一个稳定版本