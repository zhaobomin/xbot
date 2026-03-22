# xbot 状态管理演进计划

> 制定日期: 2026-03-22
> 目标: 解决架构演进债务、SDK 假设不匹配、缺乏原子性保证三大核心问题
> 预计周期: 4-6 周

---

## 一、问题根因分析

### 问题 1: 架构演进债务

**现状**: 5 层状态叠加，缺乏统一协调

```
Layer 1: Channel 状态 (_running)           ← 最早
Layer 2: SDK Client 状态 (_clients pool)   ← SDK 集成
Layer 3: Session 状态 (session.metadata)   ← 多轮对话
Layer 4: Runtime 状态机 (SessionPhase)      ← 最近添加
Layer 5: Bus 请求状态 (_pending_requests)  ← 权限系统
```

**影响**: 各层状态可能不一致，清理路径复杂

### 问题 2: SDK 设计假设不匹配

**SDK 假设**:
- 单线程 CLI 使用
- 短期会话
- 同步权限回调

**xbot 需求**:
- 多 Channel 并发
- 长会话持久化
- 异步权限处理（用户可能几分钟才回复）

**影响**: 大量适配代码，每个适配点都可能引入状态不一致

### 问题 3: 缺乏原子性保证

**现状**: 状态更新分散，无事务概念

```python
# 典型的问题模式
self._clients.pop(session_key, None)        # 步骤 1
self._state_machine.force_transition(...)    # 步骤 2 - 如果失败？
self._session_locks.pop(session_key, None)   # 步骤 3 - 如果失败？
```

**影响**: 异常时状态可能不一致，难以恢复

---

## 二、演进目标

### 最终架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SessionStateCoordinator                          │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  - 统一状态入口点 (Single Source of Truth)                   │   │
│  │  - 事务性状态更新                                            │   │
│  │  - Checkpoint 快照与恢复                                     │   │
│  │  - 状态变更事件发布                                          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                │                                    │
│         ┌──────────────────────┼──────────────────────┐            │
│         ▼                      ▼                      ▼            │
│  ┌─────────────┐        ┌─────────────┐        ┌─────────────┐     │
│  │ RuntimeState│        │ BackendState│        │  BusState   │     │
│  │  - phase    │        │  - client   │        │  - requests │     │
│  │  - tasks    │        │  - task_id  │        │  - responses│     │
│  │  - lock     │        │  - last_used│        │             │     │
│  └─────────────┘        └─────────────┘        └─────────────┘     │
│         │                      │                      │            │
│         └──────────────────────┴──────────────────────┘            │
│                                │                                    │
│                                ▼                                    │
│                    ┌─────────────────────┐                          │
│                    │   StateEventLog     │                          │
│                    │   (事件溯源层)       │                          │
│                    └─────────────────────┘                          │
└─────────────────────────────────────────────────────────────────────┘
```

### 演进原则

1. **增量式改进**: 每一步都可独立部署和测试
2. **向后兼容**: 不破坏现有 API
3. **可观测性优先**: 先增加监控，再改进实现
4. **测试驱动**: 每个改进都有对应的测试用例

---

## 三、分阶段演进计划

### Phase 0: 准备工作 (Week 1)

**目标**: 建立可观测性和测试基础

#### 0.1 状态一致性检查器

```python
# 新增文件: xbot/agent/state_checker.py

@dataclass
class StateSnapshot:
    """某一时刻的完整状态快照"""
    session_key: str
    runtime_phase: SessionPhase
    runtime_tasks: int
    backend_has_client: bool
    backend_task_id: str | None
    bus_pending_permission: bool
    bus_pending_interaction: bool
    timestamp: float
    inconsistencies: list[str] = field(default_factory=list)

class StateConsistencyChecker:
    """状态一致性检查器"""

    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime

    def check_session(self, session_key: str) -> StateSnapshot:
        """检查单个 session 的状态一致性"""
        snapshot = StateSnapshot(
            session_key=session_key,
            runtime_phase=self.runtime._state_machine.get_phase(session_key),
            runtime_tasks=len([t for t in self.runtime._active_tasks.get(session_key, []) if not t.done()]),
            backend_has_client=session_key in self.runtime.backend._clients,
            backend_task_id=self.runtime.backend._active_task_ids.get(session_key),
            bus_pending_permission=bool(self.runtime.bus and self.runtime.bus.get_pending_request_for_session(session_key)),
            bus_pending_interaction=bool(self.runtime.bus and self.runtime.bus.get_pending_interaction_for_session(session_key)),
            timestamp=time.time(),
        )

        # 检查不一致
        self._detect_inconsistencies(snapshot)
        return snapshot

    def _detect_inconsistencies(self, snapshot: StateSnapshot):
        """检测状态不一致"""
        issues = []

        # 规则 1: RUNNING 状态必须有 client
        if snapshot.runtime_phase == SessionPhase.RUNNING:
            if not snapshot.backend_has_client:
                issues.append("RUNNING but no backend client")

        # 规则 2: WAITING_PERMISSION 必须有 pending request
        if snapshot.runtime_phase == SessionPhase.WAITING_PERMISSION:
            if not snapshot.bus_pending_permission:
                issues.append("WAITING_PERMISSION but no pending request")

        # 规则 3: IDLE 状态不应该有活跃任务
        if snapshot.runtime_phase == SessionPhase.IDLE:
            if snapshot.runtime_tasks > 0:
                issues.append("IDLE but has active tasks")
            if snapshot.backend_task_id:
                issues.append("IDLE but has backend task_id")

        # 规则 4: 有 client 就应该有锁
        if snapshot.backend_has_client:
            if session_key not in self.runtime._session_locks:
                issues.append("Has client but no session lock")

        snapshot.inconsistencies = issues
        return len(issues) == 0
```

#### 0.2 状态变更日志增强

```python
# 修改 runtime.py

class AgentRuntime:
    def __init__(self, ...):
        # 添加状态变更事件队列
        self._state_events: asyncio.Queue[StateChangeEvent] = asyncio.Queue()
        self._state_checker = StateConsistencyChecker(self)

    async def _log_state_change(
        self,
        session_key: str,
        event_type: str,
        details: dict[str, Any],
    ) -> None:
        """记录状态变更事件"""
        event = StateChangeEvent(
            event_id=str(uuid.uuid4()),
            session_key=session_key,
            event_type=event_type,
            details=details,
            snapshot=self._state_checker.check_session(session_key),
            timestamp=time.time(),
        )
        await self._state_events.put(event)

        # 检测到不一致时记录警告
        if event.snapshot.inconsistencies:
            logger.warning(
                f"State inconsistency detected for {session_key}: "
                f"{event.snapshot.inconsistencies}"
            )
```

#### 0.3 测试用例

```python
# 新增测试文件: tests/test_state_consistency.py

class TestStateConsistency:
    """状态一致性测试"""

    async def test_idle_state_consistency(self, runtime):
        """IDLE 状态应该没有活跃资源"""
        checker = StateConsistencyChecker(runtime)

        # 初始状态
        snapshot = checker.check_session("test:session")
        assert snapshot.runtime_phase == SessionPhase.IDLE
        assert snapshot.runtime_tasks == 0
        assert not snapshot.backend_has_client
        assert len(snapshot.inconsistencies) == 0

    async def test_running_state_requires_client(self, runtime):
        """RUNNING 状态必须有 client"""
        # 模拟进入 RUNNING 状态但不创建 client
        runtime._state_machine.force_transition(
            "test:session", SessionPhase.RUNNING
        )

        checker = StateConsistencyChecker(runtime)
        snapshot = checker.check_session("test:session")

        assert "RUNNING but no backend client" in snapshot.inconsistencies
```

**交付物**:
- [ ] `xbot/agent/state_checker.py` - 状态一致性检查器
- [ ] `tests/test_state_consistency.py` - 一致性测试
- [ ] 状态变更事件日志机制

---

### Phase 1: 统一状态入口点 (Week 2-3)

**目标**: 引入 SessionStateCoordinator，统一状态访问

#### 1.1 定义统一状态结构

```python
# 新增文件: xbot/agent/session_coordinator.py

from dataclasses import dataclass, field
from typing import Any, Callable
from enum import Enum
import asyncio
import time
import uuid

class SessionPhase(str, Enum):
    """会话阶段（从 runtime.py 迁移）"""
    IDLE = "idle"
    RUNNING = "running"
    WAITING_PERMISSION = "waiting_permission"
    WAITING_INTERACTION = "waiting_interaction"
    STOPPING = "stopping"
    RESETTING = "resetting"
    ERROR = "error"


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
    """MessageBus 组件状态"""
    pending_permission_request: str | None = None
    pending_interaction_request: str | None = None


@dataclass
class RuntimeState:
    """Runtime 组件状态"""
    phase: SessionPhase = SessionPhase.IDLE
    phase_reason: str = ""
    active_tasks: int = 0
    has_lock: bool = False


@dataclass
class SessionState:
    """统一的会话状态"""
    session_key: str
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
        self.version += 1
        self.updated_at = time.time()

    def check_inconsistencies(self) -> list[str]:
        """检查状态不一致"""
        issues = []

        # 规则定义
        rules = [
            # (条件, 错误消息)
            (
                self.runtime.phase == SessionPhase.RUNNING and not self.backend.has_client,
                "RUNNING but no backend client"
            ),
            (
                self.runtime.phase == SessionPhase.WAITING_PERMISSION and not self.bus.pending_permission_request,
                "WAITING_PERMISSION but no pending request"
            ),
            (
                self.runtime.phase == SessionPhase.WAITING_INTERACTION and not self.bus.pending_interaction_request,
                "WAITING_INTERACTION but no pending request"
            ),
            (
                self.runtime.phase == SessionPhase.IDLE and self.runtime.active_tasks > 0,
                "IDLE but has active tasks"
            ),
            (
                self.backend.has_client and not self.runtime.has_lock,
                "Has client but no session lock"
            ),
        ]

        for condition, message in rules:
            if condition:
                issues.append(message)

        return issues
```

#### 1.2 实现 SessionStateCoordinator

```python
# 继续在 session_coordinator.py 中

@dataclass
class StateCheckpoint:
    """状态快照"""
    checkpoint_id: str
    session_key: str
    state: SessionState
    created_at: float


class SessionStateCoordinator:
    """
    会话状态协调器 - 统一的状态管理入口

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
        """
        原子性更新会话状态

        所有更新在一个锁内完成，确保一致性
        """
        async with self._lock:
            state = await self._get_or_create_unlocked(session_key)
            old_state = SessionState(
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
                self._on_state_change(session_key, old_state, state)

            return state

    async def set_phase(
        self,
        session_key: str,
        phase: SessionPhase,
        reason: str = "",
    ) -> bool:
        """设置会话阶段"""
        state = await self.get_or_create(session_key)
        new_runtime = RuntimeState(
            phase=phase,
            phase_reason=reason,
            active_tasks=state.runtime.active_tasks,
            has_lock=state.runtime.has_lock,
        )
        await self.update(session_key, runtime=new_runtime, reason=reason)
        return True

    async def set_backend_client(
        self,
        session_key: str,
        has_client: bool,
        task_id: str | None = None,
    ) -> None:
        """设置 backend 客户端状态"""
        state = await self.get_or_create(session_key)
        new_backend = BackendState(
            has_client=has_client,
            task_id=task_id,
            last_used=time.time() if has_client else state.backend.last_used,
            reconnect_pending=state.backend.reconnect_pending,
            last_error=state.backend.last_error,
        )
        await self.update(session_key, backend=new_backend)

    async def set_pending_permission(
        self,
        session_key: str,
        request_id: str | None,
    ) -> None:
        """设置 pending 权限请求"""
        state = await self.get_or_create(session_key)
        new_bus = BusState(
            pending_permission_request=request_id,
            pending_interaction_request=state.bus.pending_interaction_request,
        )
        await self.update(session_key, bus=new_bus)

    async def set_pending_interaction(
        self,
        session_key: str,
        request_id: str | None,
    ) -> None:
        """设置 pending 交互请求"""
        state = await self.get_or_create(session_key)
        new_bus = BusState(
            pending_permission_request=state.bus.pending_permission_request,
            pending_interaction_request=request_id,
        )
        await self.update(session_key, bus=new_bus)

    async def clear_session(self, session_key: str) -> None:
        """清除会话状态"""
        async with self._lock:
            self._states.pop(session_key, None)
            self._checkpoints.pop(session_key, None)

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
                state=SessionState(
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
                ),
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
                    self._states[session_key] = SessionState(
                        session_key=cp.state.session_key,
                        runtime=RuntimeState(
                            phase=cp.state.runtime.phase,
                            phase_reason=cp.state.runtime.phase_reason,
                            active_tasks=cp.state.runtime.active_tasks,
                            has_lock=cp.state.runtime.has_lock,
                        ),
                        backend=BackendState(
                            has_client=cp.state.backend.has_client,
                            task_id=cp.state.backend.task_id,
                            last_used=cp.state.backend.last_used,
                            reconnect_pending=cp.state.backend.reconnect_pending,
                            last_error=cp.state.backend.last_error,
                        ),
                        bus=BusState(
                            pending_permission_request=cp.state.bus.pending_permission_request,
                            pending_interaction_request=cp.state.bus.pending_interaction_request,
                        ),
                        created_at=cp.state.created_at,
                        updated_at=time.time(),
                        version=cp.state.version + 1,
                        sdk_session_id=cp.state.sdk_session_id,
                    )
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
```

#### 1.3 迁移 Runtime 使用 Coordinator

```python
# 修改 runtime.py

class AgentRuntime:
    def __init__(self, config: Any, shared_resources: dict[str, Any]):
        # ... 现有初始化 ...

        # 新增: 统一状态协调器
        self._coordinator = SessionStateCoordinator()

        # 设置状态变更回调
        self._coordinator._on_state_change = self._on_coordinator_state_change

    def _on_coordinator_state_change(
        self,
        session_key: str,
        old_state: SessionState,
        new_state: SessionState,
    ) -> None:
        """状态变更回调"""
        # 记录到 trace
        append_session_trace(
            self.sessions,
            session_key,
            "state_change",
            {
                "old_phase": old_state.runtime.phase.value,
                "new_phase": new_state.runtime.phase.value,
                "version": new_state.version,
                "inconsistencies": new_state.check_inconsistencies(),
            },
        )

    async def _set_session_phase(
        self,
        session_key: str,
        phase: SessionPhase,
        *,
        reason: str = "",
    ) -> None:
        """设置会话阶段 - 通过 Coordinator"""
        await self._coordinator.set_phase(session_key, phase, reason)

        # 同步到旧的状态机（过渡期保留）
        self._state_machine.transition(session_key, phase, reason=reason, force=True)
```

**交付物**:
- [ ] `xbot/agent/session_coordinator.py` - 状态协调器
- [ ] `tests/test_session_coordinator.py` - 协调器测试
- [ ] Runtime 集成代码

---

### Phase 2: SDK 适配层改进 (Week 3-4)

**目标**: 解耦 SDK 假设与 xbot 需求

#### 2.1 SDK 适配器抽象

```python
# 新增文件: xbot/agent/backends/sdk_adapter.py

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator
from dataclasses import dataclass

@dataclass
class SDKSessionConfig:
    """SDK 会话配置"""
    session_key: str
    sdk_session_id: str | None = None
    permission_mode: str = "auto"
    timeout: float = 300.0


class SDKAdapter(ABC):
    """
    SDK 适配器抽象基类

    定义 xbot 与 SDK 的接口边界
    """

    @abstractmethod
    async def create_session(self, config: SDKSessionConfig) -> str:
        """创建 SDK 会话，返回 session_id"""
        pass

    @abstractmethod
    async def send_message(
        self,
        session_key: str,
        message: str,
        media: list[str] | None = None,
    ) -> AsyncIterator[Any]:
        """发送消息，返回消息流"""
        pass

    @abstractmethod
    async def interrupt_session(self, session_key: str) -> dict[str, Any]:
        """中断会话"""
        pass

    @abstractmethod
    async def close_session(self, session_key: str) -> None:
        """关闭会话"""
        pass

    @abstractmethod
    async def request_permission(
        self,
        session_key: str,
        tool_name: str,
        tool_input: dict,
    ) -> tuple[str, dict | None]:
        """
        请求权限

        Returns:
            (decision, updated_input)
            decision: "allow" | "deny"
            updated_input: 如果允许，可能修改的输入
        """
        pass


class ClaudeSDKAdapter(SDKAdapter):
    """Claude SDK 适配器实现"""

    def __init__(
        self,
        coordinator: SessionStateCoordinator,
        permission_handler: PermissionRequestHandler,
    ):
        self._coordinator = coordinator
        self._permission_handler = permission_handler
        self._clients: dict[str, Any] = {}  # SDK clients
        self._client_lock = asyncio.Lock()

    async def create_session(self, config: SDKSessionConfig) -> str:
        """创建 SDK 会话"""
        async with self._client_lock:
            # 创建 SDK client
            client = await self._create_sdk_client(config)
            self._clients[config.session_key] = client

            # 更新状态
            await self._coordinator.set_backend_client(
                config.session_key,
                has_client=True,
            )

            return config.sdk_session_id or str(uuid.uuid4())

    async def send_message(
        self,
        session_key: str,
        message: str,
        media: list[str] | None = None,
    ) -> AsyncIterator[Any]:
        """发送消息"""
        state = await self._coordinator.get(session_key)

        # 检查是否有有效的 client
        if not state or not state.backend.has_client:
            raise RuntimeError(f"No active session for {session_key}")

        client = self._clients.get(session_key)
        if not client:
            # 尝试恢复
            await self.create_session(SDKSessionConfig(
                session_key=session_key,
                sdk_session_id=state.sdk_session_id,
            ))
            client = self._clients[session_key]

        # 设置 RUNNING 状态
        await self._coordinator.set_phase(
            session_key,
            SessionPhase.RUNNING,
            reason="message_send",
        )

        try:
            async for msg in client.query(message, session_id=state.sdk_session_id):
                # 处理特殊消息
                if self._is_permission_request(msg):
                    decision, updated_input = await self.request_permission(
                        session_key,
                        msg.tool_name,
                        msg.tool_input,
                    )
                    if decision == "allow":
                        # 继续执行
                        continue
                    else:
                        # 拒绝，中断流
                        break

                yield msg

                # 检查终止消息
                if self._is_terminal_message(msg):
                    break

        except Exception as e:
            # 错误处理
            await self._coordinator.update(
                session_key,
                backend=BackendState(
                    has_client=False,
                    reconnect_pending=True,
                    last_error=str(e)[:500],
                ),
            )
            raise
        finally:
            # 清理状态
            await self._coordinator.set_phase(
                session_key,
                SessionPhase.IDLE,
                reason="message_complete",
            )

    async def request_permission(
        self,
        session_key: str,
        tool_name: str,
        tool_input: dict,
    ) -> tuple[str, dict | None]:
        """请求权限"""
        state = await self._coordinator.get(session_key)

        # 设置等待状态
        await self._coordinator.set_phase(
            session_key,
            SessionPhase.WAITING_PERMISSION,
            reason=f"tool_permission:{tool_name}",
        )

        try:
            # 调用权限处理器
            decision, result = await self._permission_handler.can_use_tool(
                tool_name,
                tool_input,
                {"session_key": session_key},
            )

            return decision, result

        finally:
            # 恢复运行状态
            await self._coordinator.set_phase(
                session_key,
                SessionPhase.RUNNING,
                reason="permission_resolved",
            )

    async def interrupt_session(self, session_key: str) -> dict[str, Any]:
        """中断会话"""
        client = self._clients.get(session_key)
        if not client:
            return {"interrupted": False, "usage": None}

        try:
            await client.interrupt()

            # 更新状态
            await self._coordinator.set_backend_client(
                session_key,
                has_client=False,
                task_id=None,
            )

            return {"interrupted": True, "usage": None}

        finally:
            # 清理 client
            async with self._client_lock:
                self._clients.pop(session_key, None)

    async def close_session(self, session_key: str) -> None:
        """关闭会话"""
        client = self._clients.get(session_key)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

            async with self._client_lock:
                self._clients.pop(session_key, None)

        # 清理状态
        await self._coordinator.set_backend_client(
            session_key,
            has_client=False,
            task_id=None,
        )
```

#### 2.2 异步权限处理改进

```python
# 修改 permission_handler.py

class PermissionRequestHandler:
    """
    改进的权限请求处理器

    支持:
    1. 异步等待（不阻塞 SDK）
    2. 超时处理
    3. 取消处理
    """

    def __init__(
        self,
        coordinator: SessionStateCoordinator,
        timeout: float = 300.0,  # 5 分钟超时
    ):
        self._coordinator = coordinator
        self._timeout = timeout
        self._pending_requests: dict[str, asyncio.Future] = {}

    async def can_use_tool(
        self,
        tool_name: str,
        tool_input: dict,
        context: dict,
    ) -> tuple[str, dict | None]:
        """
        请求权限

        使用 asyncio.Future 实现异步等待
        """
        session_key = context.get("session_key")
        request_id = str(uuid.uuid4())

        # 创建 Future
        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[str, dict | None]] = loop.create_future()
        self._pending_requests[request_id] = future

        try:
            # 发送权限请求到 Channel
            await self._send_permission_request(
                request_id,
                session_key,
                tool_name,
                tool_input,
            )

            # 更新状态
            await self._coordinator.set_pending_permission(session_key, request_id)

            # 等待响应（带超时）
            async with asyncio.timeout(self._timeout):
                decision, updated_input = await future

            return decision, updated_input

        except asyncio.TimeoutError:
            # 超时处理
            await self._handle_timeout(session_key, request_id)
            return "deny", {"reason": "timeout"}

        except asyncio.CancelledError:
            # 取消处理
            await self._handle_cancel(session_key, request_id)
            raise

        finally:
            self._pending_requests.pop(request_id, None)
            await self._coordinator.set_pending_permission(session_key, None)

    def submit_response(
        self,
        request_id: str,
        decision: str,
        updated_input: dict | None = None,
    ) -> bool:
        """提交权限响应"""
        future = self._pending_requests.get(request_id)
        if future is None or future.done():
            return False

        future.set_result((decision, updated_input))
        return True

    async def _send_permission_request(
        self,
        request_id: str,
        session_key: str,
        tool_name: str,
        tool_input: dict,
    ) -> None:
        """发送权限请求到 Channel"""
        # 通过 bus 发送
        pass

    async def _handle_timeout(self, session_key: str, request_id: str) -> None:
        """处理超时"""
        # 通知用户
        pass

    async def _handle_cancel(self, session_key: str, request_id: str) -> None:
        """处理取消"""
        # 清理状态
        pass
```

**交付物**:
- [ ] `xbot/agent/backends/sdk_adapter.py` - SDK 适配器抽象
- [ ] 改进的 `permission_handler.py`
- [ ] `tests/test_sdk_adapter.py` - 适配器测试

---

### Phase 3: 事务性状态更新 (Week 4-5)

**目标**: 引入状态事务，确保原子性

#### 3.1 状态事务实现

```python
# 新增文件: xbot/agent/state_transaction.py

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from enum import Enum
import asyncio

class TransactionState(Enum):
    PENDING = "pending"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


@dataclass
class TransactionOperation:
    """事务操作记录"""
    operation_id: str
    operation_type: str  # "update", "create", "delete"
    target: str  # 目标组件: "runtime", "backend", "bus"
    before: Any
    after: Any
    rollback_action: Callable[[], Awaitable[None]] | None = None


@dataclass
class StateTransaction:
    """
    状态更新事务

    用法:
    ```python
    async with StateTransaction(coordinator, session_key) as tx:
        tx.update_runtime(phase=SessionPhase.RUNNING)
        tx.update_backend(has_client=True)
        # 如果任何一步失败，自动回滚
    ```
    """

    coordinator: Any  # SessionStateCoordinator
    session_key: str
    operations: list[TransactionOperation] = field(default_factory=list)
    state: TransactionState = TransactionState.PENDING
    checkpoint_id: str | None = None

    async def __aenter__(self) -> "StateTransaction":
        # 保存检查点
        self.checkpoint_id = await self.coordinator.save_checkpoint(self.session_key)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            # 发生异常，回滚
            await self.rollback()
            return False  # 重新抛出异常

        # 提交事务
        await self.commit()
        return False

    def update_runtime(
        self,
        phase: SessionPhase | None = None,
        reason: str | None = None,
        active_tasks: int | None = None,
    ) -> None:
        """记录 Runtime 更新操作"""
        current = self.coordinator._states.get(self.session_key)
        if current is None:
            return

        before = RuntimeState(
            phase=current.runtime.phase,
            phase_reason=current.runtime.phase_reason,
            active_tasks=current.runtime.active_tasks,
            has_lock=current.runtime.has_lock,
        )

        after = RuntimeState(
            phase=phase if phase is not None else current.runtime.phase,
            phase_reason=reason if reason is not None else current.runtime.phase_reason,
            active_tasks=active_tasks if active_tasks is not None else current.runtime.active_tasks,
            has_lock=current.runtime.has_lock,
        )

        self.operations.append(TransactionOperation(
            operation_id=str(uuid.uuid4()),
            operation_type="update",
            target="runtime",
            before=before,
            after=after,
            rollback_action=lambda: self.coordinator.update(self.session_key, runtime=before),
        ))

    def update_backend(
        self,
        has_client: bool | None = None,
        task_id: str | None = None,
    ) -> None:
        """记录 Backend 更新操作"""
        current = self.coordinator._states.get(self.session_key)
        if current is None:
            return

        before = BackendState(
            has_client=current.backend.has_client,
            task_id=current.backend.task_id,
            last_used=current.backend.last_used,
            reconnect_pending=current.backend.reconnect_pending,
            last_error=current.backend.last_error,
        )

        after = BackendState(
            has_client=has_client if has_client is not None else current.backend.has_client,
            task_id=task_id if task_id is not None else current.backend.task_id,
            last_used=current.backend.last_used,
            reconnect_pending=current.backend.reconnect_pending,
            last_error=current.backend.last_error,
        )

        self.operations.append(TransactionOperation(
            operation_id=str(uuid.uuid4()),
            operation_type="update",
            target="backend",
            before=before,
            after=after,
            rollback_action=lambda: self.coordinator.update(self.session_key, backend=before),
        ))

    async def commit(self) -> None:
        """提交事务"""
        if self.state != TransactionState.PENDING:
            return

        # 应用所有操作
        for op in self.operations:
            if op.target == "runtime":
                await self.coordinator.update(self.session_key, runtime=op.after)
            elif op.target == "backend":
                await self.coordinator.update(self.session_key, backend=op.after)
            elif op.target == "bus":
                await self.coordinator.update(self.session_key, bus=op.after)

        self.state = TransactionState.COMMITTED

    async def rollback(self) -> None:
        """回滚事务"""
        if self.state != TransactionState.PENDING:
            return

        # 方式 1: 执行回滚操作
        for op in reversed(self.operations):
            if op.rollback_action:
                try:
                    await op.rollback_action()
                except Exception as e:
                    logger.error(f"Rollback failed for {op.operation_id}: {e}")

        # 方式 2: 恢复检查点
        if self.checkpoint_id:
            await self.coordinator.restore_checkpoint(self.session_key, self.checkpoint_id)

        self.state = TransactionState.ROLLED_BACK


# 在 SessionStateCoordinator 中添加事务支持

class SessionStateCoordinator:
    def transaction(self, session_key: str) -> StateTransaction:
        """创建状态事务"""
        return StateTransaction(
            coordinator=self,
            session_key=session_key,
        )
```

#### 3.2 使用事务重构关键操作

```python
# 修改 runtime.py 中的 _terminate_session

class AgentRuntime:
    async def _terminate_session(
        self,
        session_key: str,
        *,
        hard_reset: bool,
    ) -> dict[str, Any]:
        """终止会话 - 使用事务"""

        async with self._coordinator.transaction(session_key) as tx:
            # 记录所有操作（事务会自动回滚失败的操作）
            tx.update_runtime(
                phase=SessionPhase.STOPPING,
                reason="terminate_session",
            )

            # 取消任务
            tasks = self._active_tasks.pop(session_key, [])
            cancelled = sum(1 for t in tasks if not t.done() and t.cancel())

            for task in tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

            # Backend 操作
            backend_cancelled = await self.router.backend.cancel_session(session_key)
            backend_task_stopped = await self.router.backend.stop_active_task(session_key)
            interrupt_result = await self.router.backend.interrupt_session(session_key)

            if hard_reset:
                await self.router.backend.reset_session(session_key)

            # 更新 Backend 状态
            tx.update_backend(has_client=False, task_id=None)

            # 清理 Bus 请求
            cleared_requests = {"permission": False, "interaction": False}
            if self.bus is not None and hasattr(self.bus, "clear_session_requests"):
                cleared_requests = self.bus.clear_session_requests(session_key)

            # 清理 session lock
            self._session_locks.pop(session_key, None)

            # 最终状态
            if hard_reset:
                tx.update_runtime(phase=SessionPhase.IDLE, reason="hard_reset_complete")
                await self._coordinator.clear_session(session_key)
            else:
                tx.update_runtime(phase=SessionPhase.IDLE, reason="terminate_complete")

            return {
                "cancelled": cancelled,
                "backend_cancelled": backend_cancelled,
                "backend_task_stopped": backend_task_stopped,
                "interrupted": bool(interrupt_result.get("interrupted")),
                "usage": interrupt_result.get("usage"),
                "cleared_requests": cleared_requests,
            }
```

**交付物**:
- [ ] `xbot/agent/state_transaction.py` - 事务实现
- [ ] 重构的 `_terminate_session`
- [ ] `tests/test_state_transaction.py` - 事务测试

---

### Phase 4: 验证与监控 (Week 5-6)

**目标**: 完善监控和告警

#### 4.1 状态指标暴露

```python
# 新增文件: xbot/agent/state_metrics.py

from dataclasses import dataclass
from typing import Any
import time

@dataclass
class StateMetrics:
    """状态指标"""
    total_sessions: int = 0
    sessions_by_phase: dict[str, int] = None
    sessions_with_inconsistencies: int = 0
    active_backend_clients: int = 0
    pending_permissions: int = 0
    pending_interactions: int = 0

    # 检查点统计
    total_checkpoints: int = 0
    checkpoint_restore_attempts: int = 0
    checkpoint_restore_successes: int = 0

    # 事务统计
    total_transactions: int = 0
    transaction_commits: int = 0
    transaction_rollbacks: int = 0


class StateMetricsCollector:
    """状态指标收集器"""

    def __init__(self, coordinator: SessionStateCoordinator):
        self._coordinator = coordinator
        self._metrics = StateMetrics()

    def collect(self) -> StateMetrics:
        """收集当前指标"""
        metrics = StateMetrics(
            sessions_by_phase={phase.value: 0 for phase in SessionPhase},
        )

        for session_key, state in self._coordinator._states.items():
            metrics.total_sessions += 1
            metrics.sessions_by_phase[state.runtime.phase.value] += 1

            if state.backend.has_client:
                metrics.active_backend_clients += 1

            if state.bus.pending_permission_request:
                metrics.pending_permissions += 1

            if state.bus.pending_interaction_request:
                metrics.pending_interactions += 1

            if state.check_inconsistencies():
                metrics.sessions_with_inconsistencies += 1

        # 检查点统计
        for checkpoints in self._coordinator._checkpoints.values():
            metrics.total_checkpoints += len(checkpoints)

        return metrics

    def export_prometheus(self) -> str:
        """导出 Prometheus 格式指标"""
        metrics = self.collect()

        lines = [
            f"xbot_sessions_total {metrics.total_sessions}",
            f"xbot_sessions_inconsistent {metrics.sessions_with_inconsistencies}",
            f"xbot_backend_clients_active {metrics.active_backend_clients}",
            f"xbot_permissions_pending {metrics.pending_permissions}",
            f"xbot_interactions_pending {metrics.pending_interactions}",
            f"xbot_checkpoints_total {metrics.total_checkpoints}",
            f"xbot_transactions_total {self._metrics.total_transactions}",
            f"xbot_transactions_commits {self._metrics.transaction_commits}",
            f"xbot_transactions_rollbacks {self._metrics.transaction_rollbacks}",
        ]

        for phase, count in metrics.sessions_by_phase.items():
            lines.append(f'xbot_sessions_by_phase{{phase="{phase}"}} {count}')

        return "\n".join(lines)
```

#### 4.2 状态一致性后台检查

```python
# 新增文件: xbot/agent/state_health_check.py

class StateHealthCheckService:
    """状态健康检查服务"""

    def __init__(
        self,
        coordinator: SessionStateCoordinator,
        check_interval: float = 60.0,
    ):
        self._coordinator = coordinator
        self._check_interval = check_interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动健康检查"""
        self._running = True
        self._task = asyncio.create_task(self._check_loop())

    async def stop(self) -> None:
        """停止健康检查"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

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

    async def _perform_check(self) -> None:
        """执行检查"""
        issues = []

        for session_key, state in list(self._coordinator._states.items()):
            inconsistencies = state.check_inconsistencies()
            if inconsistencies:
                issues.append({
                    "session_key": session_key,
                    "phase": state.runtime.phase.value,
                    "issues": inconsistencies,
                })

        if issues:
            logger.warning(
                f"State health check found {len(issues)} sessions with issues"
            )
            # 可以发送告警
```

**交付物**:
- [ ] `xbot/agent/state_metrics.py` - 指标收集
- [ ] `xbot/agent/state_health_check.py` - 健康检查
- [ ] Prometheus 集成

---

## 四、迁移计划

### 兼容性策略

1. **并行运行**: 新旧状态管理并行运行一段时间
2. **功能开关**: 通过配置切换使用新旧实现
3. **渐进式迁移**: 先迁移读操作，再迁移写操作

```python
# 配置示例
class StateManagementConfig(Base):
    use_coordinator: bool = False  # 默认关闭，逐步开启
    enable_checkpoints: bool = True
    checkpoint_interval: float = 300.0  # 5 分钟
    enable_health_check: bool = True
    health_check_interval: float = 60.0
```

### 迁移步骤

```
Week 1: Phase 0 - 准备
  ├── 添加状态检查器
  ├── 添加测试用例
  └── 增强日志

Week 2-3: Phase 1 - 统一入口
  ├── 实现 SessionStateCoordinator
  ├── Runtime 集成
  └── 并行运行新旧系统

Week 3-4: Phase 2 - SDK 适配
  ├── 实现 SDKAdapter 抽象
  ├── 改进权限处理
  └── 逐步切换到新适配器

Week 4-5: Phase 3 - 事务支持
  ├── 实现状态事务
  ├── 重构关键操作
  └── 全面切换到新系统

Week 5-6: Phase 4 - 监控完善
  ├── 指标暴露
  ├── 健康检查
  └── 移除旧代码
```

---

## 五、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 新系统引入 bug | 高 | 并行运行、功能开关、充分测试 |
| 性能下降 | 中 | 性能基准测试、异步锁优化 |
| 迁移过程中状态不一致 | 高 | 事务支持、检查点恢复 |
| 配置错误 | 中 | 默认关闭、渐进式开启 |

---

## 六、验收标准

### Phase 0
- [ ] 状态一致性检查器可用
- [ ] 测试覆盖率 > 90%
- [ ] 能检测到模拟的状态不一致

### Phase 1
- [ ] SessionStateCoordinator 通过所有测试
- [ ] Runtime 正确使用 Coordinator
- [ ] 状态变更事件正确记录

### Phase 2
- [ ] SDKAdapter 抽象完整
- [ ] 权限处理支持 5 分钟超时
- [ ] 异步权限处理不阻塞 SDK

### Phase 3
- [ ] 事务支持正确回滚
- [ ] 关键操作使用事务
- [ ] 状态不一致自动恢复

### Phase 4
- [ ] Prometheus 指标可用
- [ ] 健康检查服务稳定
- [ ] 无状态不一致告警

---

## 七、附录：关键文件清单

| 文件 | 阶段 | 用途 |
|------|------|------|
| `xbot/agent/state_checker.py` | Phase 0 | 状态一致性检查 |
| `xbot/agent/session_coordinator.py` | Phase 1 | 统一状态管理 |
| `xbot/agent/backends/sdk_adapter.py` | Phase 2 | SDK 适配抽象 |
| `xbot/agent/state_transaction.py` | Phase 3 | 事务支持 |
| `xbot/agent/state_metrics.py` | Phase 4 | 指标收集 |
| `xbot/agent/state_health_check.py` | Phase 4 | 健康检查 |