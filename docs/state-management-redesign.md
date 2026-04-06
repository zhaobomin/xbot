# 状态管理简化设计

## 背景

经过 SDK 能力测试，发现：
- SDK **不支持并发请求保护**（消息会混淆）
- SDK **不发送 Task 状态消息**
- SDK **完整支持 Session CRUD**（list/fork/delete/rename/tag）
- SDK **支持 Context 查询**

## 当前架构问题

```
当前有 5 层状态管理：

Runtime
  ├── _state_machine: SessionStateMachine     # Phase 状态
  ├── _session_store: SessionStore            # 统一存储
  └── _state_coordinator: SessionStateCoordinator  # 协调器

Backend
  ├── _state_adapter: SessionStateAdapter     # 适配层 (dual-write)
  └── 11 个 legacy dicts:
        _clients, _client_models, _sdk_session_ids,
        _session_contexts, _client_last_used,
        _active_task_ids, _active_request_ids,
        _session_commands, _client_skills_versions,
        _long_running_turns, _client_creation_futures

问题：
- 每次写操作要同时更新多个 dict
- Adapter 的 dual-write 逻辑复杂
- 并发需要 epoch 机制防止 stale adapter
- 代码难以理解和维护
```

## 新架构设计

### 核心原则

1. **单一数据源** - SessionStore 是唯一状态存储
2. **无 dual-write** - 每个字段只写一处
3. **最小状态集** - 只保留 SDK 不管理的状态
4. **明确职责** - Runtime 管状态，Backend 管连接

### 新的数据结构

```python
@dataclass
class SessionState:
    """单个 session 的完整状态 - 最小字段集"""

    # 标识
    session_key: str                    # xbot 的 session ID (如 "slack:C12345")
    sdk_session_id: str | None = None   # SDK 的 session ID (UUID)

    # 回复路由 (必需，SDK 不管理)
    channel: str = ""                   # 渠道类型
    chat_id: str = ""                   # 渠道内的聊天 ID

    # 连接管理 (必需，SDK 不管理)
    client: ClaudeSDKClient | None = None  # 活跃的 SDK 连接
    last_active: float = field(default_factory=time.time)

    # 并发保护 (必需，SDK 不支持)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    phase: SessionPhase = SessionPhase.IDLE

    # 任务跟踪 (必需，SDK 不发送 task 消息)
    tasks: list[asyncio.Task] = field(default_factory=list)


class SessionManager:
    """简化的 Session 管理器 - 替代 SessionStore + Coordinator + Adapter"""

    def __init__(self):
        self._sessions: dict[str, SessionState] = {}
        self._sdk_index: dict[str, str] = {}  # sdk_session_id -> session_key
        self._global_lock = asyncio.Lock()

    # === 基本操作 ===

    def get(self, session_key: str) -> SessionState | None:
        return self._sessions.get(session_key)

    def get_by_sdk_id(self, sdk_session_id: str) -> SessionState | None:
        key = self._sdk_index.get(sdk_session_id)
        return self._sessions.get(key) if key else None

    def get_or_create(self, session_key: str) -> SessionState:
        if session_key not in self._sessions:
            self._sessions[session_key] = SessionState(session_key=session_key)
        return self._sessions[session_key]

    # === SDK Session ID 管理 ===

    def set_sdk_session_id(self, session_key: str, sdk_id: str | None) -> None:
        state = self._sessions.get(session_key)
        if not state:
            return

        # 清理旧索引
        if state.sdk_session_id:
            self._sdk_index.pop(state.sdk_session_id, None)

        state.sdk_session_id = sdk_id
        if sdk_id:
            self._sdk_index[sdk_id] = session_key

    # === Phase 管理 (并发保护) ===

    def can_start_request(self, session_key: str) -> bool:
        """检查是否可以开始新请求"""
        state = self._sessions.get(session_key)
        return state is not None and state.phase == SessionPhase.IDLE

    def start_request(self, session_key: str) -> bool:
        """尝试进入 RUNNING 状态，返回是否成功"""
        state = self.get_or_create(session_key)
        if state.phase != SessionPhase.IDLE:
            return False
        state.phase = SessionPhase.RUNNING
        state.last_active = time.time()
        return True

    def end_request(self, session_key: str, phase: SessionPhase = SessionPhase.IDLE) -> None:
        """结束请求，恢复到指定状态"""
        state = self._sessions.get(session_key)
        if state:
            state.phase = phase
            state.last_active = time.time()

    # === Task 管理 ===

    def register_task(self, session_key: str, task: asyncio.Task) -> None:
        state = self.get_or_create(session_key)
        state.tasks.append(task)

    def get_active_tasks(self, session_key: str) -> list[asyncio.Task]:
        state = self._sessions.get(session_key)
        if not state:
            return []
        return [t for t in state.tasks if not t.done()]

    async def cancel_all_tasks(self, session_key: str) -> int:
        state = self._sessions.get(session_key)
        if not state:
            return 0
        cancelled = 0
        for task in state.tasks:
            if not task.done():
                task.cancel()
                cancelled += 1
        state.tasks.clear()
        return cancelled

    # === 清理 ===

    async def cleanup_session(self, session_key: str) -> None:
        """清理 session，断开连接"""
        state = self._sessions.pop(session_key, None)
        if not state:
            return

        # 清理 SDK 索引
        if state.sdk_session_id:
            self._sdk_index.pop(state.sdk_session_id, None)

        # 断开连接
        if state.client:
            try:
                await state.client.disconnect()
            except Exception:
                pass

        # 取消任务
        for task in state.tasks:
            if not task.done():
                task.cancel()

    def list_stale_sessions(self, ttl_seconds: float) -> list[str]:
        """返回超时的 session keys"""
        cutoff = time.time() - ttl_seconds
        return [
            key for key, state in self._sessions.items()
            if state.last_active < cutoff and state.phase == SessionPhase.IDLE
        ]
```

### 新架构层级

```
Runtime
  ├── session_manager: SessionManager      # 唯一状态管理器
  └── backend: ClaudeSDKBackend

Backend
  └── _clients_lock: asyncio.Lock          # 仅用于 client 创建保护
  └── 直接操作 session_manager
```

### 文件变更

```
删除:
  xbot/agent/state/session_state_adapter.py  (~480 行)
  xbot/agent/state/coordinator.py            (~500 行)
  xbot/agent/state/transaction.py            (~200 行)
  xbot/agent/state/checker.py                (~300 行) - 简化后不需要
  xbot/agent/state/context_mapping.py        (如果存在)
  xbot/agent/state/snapshot.py               (如果存在)

重写:
  xbot/agent/state/store.py                  -> session_manager.py (~200 行)
  xbot/agent/state/machine.py                -> 合并到 session_manager.py
  xbot/agent/backends/claude_sdk_backend.py  # 移除 11 个 dicts

简化:
  xbot/agent/runtime.py                      # 直接使用 session_manager
```

## 迁移风险评估

### 高风险点

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| **Notification 回复路由失败** | 中 | 高 | 保留 `sdk_session_id -> (channel, chat_id)` 映射，充分测试 |
| **并发请求导致 SDK 流混乱** | 低 | 高 | Phase 状态机必须正确实现，在入口处检查 |
| **Session 持久化丢失** | 低 | 中 | SDK 自己管理持久化，xbot 不需要额外持久化 |
| **测试覆盖不足** | 中 | 高 | 迁移前补充测试，迁移后全量回归 |

### 中风险点

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| **Backend 初始化时序变化** | 中 | 中 | 确保 `initialize()` 正确设置 session_manager 引用 |
| **Task 取消逻辑变化** | 低 | 中 | 保留 `register_task` 和 `cancel_all_tasks` 语义 |
| **Lock 行为变化** | 低 | 低 | 每个 session 仍然有自己的 lock |

### 低风险点

| 风险 | 概率 | 影响 |
|------|------|------|
| **model/skills_version 不再跟踪** | 低 | 低 | SDK options 已包含这些信息 |
| **commands 不再跟踪** | 低 | 低 | SDK 自己管理 |
| **统计信息格式变化** | 低 | 低 | 非核心功能 |

## 迁移计划

### Phase 1: 准备 (1-2 天)

1. **补充测试**
   - 为 `session_state_adapter.py` 的关键路径补充测试
   - 为 `coordinator.py` 补充测试
   - 确保当前功能有测试覆盖

2. **创建新组件**
   - 创建 `session_manager.py`
   - 编写单元测试

### Phase 2: 并行运行 (2-3 天)

1. **Feature Flag**
   - 添加 `use_new_session_manager` 配置项
   - 新旧实现并行运行，仅新实现写入，旧实现只读

2. **逐步迁移**
   - 先迁移 Backend 的 client 管理
   - 再迁移 Runtime 的 phase 管理
   - 最后移除 Adapter

### Phase 3: 切换 (1 天)

1. **全量切换**
   - 移除 feature flag
   - 删除旧代码

2. **回归测试**
   - 运行所有测试
   - 手动测试关键场景

### Phase 4: 清理 (1 天)

1. **删除废弃文件**
2. **更新文档**
3. **代码审查**

## 回滚方案

如果迁移后发现问题：

1. **快速回滚**: Git revert 到迁移前的 commit
2. **数据兼容**: 由于 SDK 管理持久化，无数据兼容问题
3. **配置回滚**: Feature flag 允许快速切换回旧实现

## 预估工作量

| 阶段 | 工作量 | 风险 |
|------|--------|------|
| Phase 1 准备 | 1-2 天 | 低 |
| Phase 2 并行运行 | 2-3 天 | 中 |
| Phase 3 切换 | 1 天 | 中 |
| Phase 4 清理 | 1 天 | 低 |
| **总计** | **5-7 天** | |

## 代码行数变化预估

```
删除: ~1500 行
  - session_state_adapter.py: ~480 行
  - coordinator.py: ~500 行
  - transaction.py: ~200 行
  - checker.py: ~300 行 (简化后)
  - backend 中的冗余代码: ~100 行

新增: ~300 行
  - session_manager.py: ~200 行
  - 测试: ~100 行

净减少: ~1200 行
```

## 建议

1. **先做小规模测试** - 在开发环境用 feature flag 验证新实现
2. **保留旧代码一段时间** - 不要立即删除，等新实现稳定后再删
3. **关注 notification 回复路由** - 这是最容易出错的地方
4. **监控并发场景** - 多用户同时发送消息的场景需要重点测试