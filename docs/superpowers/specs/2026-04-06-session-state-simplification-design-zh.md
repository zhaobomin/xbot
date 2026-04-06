# Session 状态管理简化设计

**日期**: 2026-04-06  
**状态**: 草稿  
**作者**: Claude

## 概要

将 xbot 的 session 状态管理从 5 层简化为 1 层，移除约 1200 行复杂的 dual-write 代码。SDK 自己管理会话历史，xbot 只需要跟踪 SDK 不管理的状态：连接池、请求路由、并发保护、任务生命周期。

## 问题陈述

### 当前架构问题

1. **5 层状态管理**：StateMachine → Store → Adapter → legacy dicts → Coordinator
2. **Dual-write 复杂性**：每个 setter 都要同时写 SessionStore 和 legacy dicts
3. **Backend 中有 11 个 legacy dicts**：`_clients`、`_client_models`、`_sdk_session_ids`、`_session_contexts`、`_client_last_used`、`_active_task_ids`、`_active_request_ids`、`_session_commands`、`_client_skills_versions`、`_long_running_turns`、`_client_creation_futures`
4. **并发复杂性**：需要 `_adapter_epoch` 机制来检测过时的 adapter
5. **难以维护**：新贡献者难以理解状态流转

### SDK 能力测试结果

测试 `claude-agent-sdk` v0.1.56 后：

| 能力 | SDK 支持 | xbot 需要 |
|------|----------|-----------|
| 并发请求保护 | ❌ 不支持 | 需要 - Phase 状态机 |
| `interrupt()` | ✅ 支持停止请求 | 用于用户取消 |
| `stop_task(task_id)` | ❌ 无法使用 | 不需要 - SDK 不发送 TaskStartedMessage |
| Task 状态消息 | ❌ 不发送 | 需要 - 跟踪 asyncio.Tasks |
| Session CRUD | ✅ 完整支持 | 不需要 - 使用 SDK API |
| Context 使用查询 | ✅ 支持 | 不需要 - 使用 `get_context_usage()` |
| Model/Skills 跟踪 | ✅ 在 options 中 | 不需要 - SDK 处理 |
| 会话历史 | ✅ 已管理 | 不需要 - SDK 持久化 |

#### interrupt() vs stop_task() 测试结果

```
TEST 1: Basic Interrupt
  - interrupt() 发送后，请求立即停止
  - 返回 ResultMessage，cost=$0.0000

TEST 2: Interrupt During Tool Use  
  - 工具使用过程中可以中断
  - 已发出的工具调用可能继续执行（SDK 行为）

TEST 3-4: TaskStartedMessage Detection
  - SDK 不发送 TaskStartedMessage（至少在常规查询中）
  - 因此 stop_task() 无法使用（没有 task_id）

结论：
- interrupt() 可以停止当前请求
- stop_task() 不需要实现（SDK 不提供必要的 task_id）
```

#### asyncio.Task 跟踪仍然需要的原因

虽然 `interrupt()` 可以停止 SDK 请求，但 asyncio.Task 跟踪仍然需要：

| 场景 | 说明 |
|------|------|
| **并发保护** | 用户发新消息时，需要取消前一个 asyncio.Task |
| **优雅关闭** | 服务器关闭时，需要取消所有活跃的 asyncio.Tasks |
| **超时强制** | 请求超时时，需要取消 asyncio.Task 并调用 interrupt() |
| **异常断开** | 客户端断开时，需要清理 asyncio.Tasks |

**注意**：asyncio.Task 跟踪的是 xbot 自己的消息处理任务，不是 SDK 的内部任务。

## 解决方案

### 核心原则：单一数据源

用 1 个 `SessionManager` 类替代 5 层架构，只存储 SDK 不管理的状态。

### 新的数据结构

```python
@dataclass
class SessionState:
    """最小化 session 状态 - 只保留 SDK 不管理的"""
    
    # 标识
    session_key: str                    # xbot 的 session ID (如 "slack:C12345")
    sdk_session_id: str | None = None   # SDK 的 session ID (UUID)
    
    # 路由 (必需 - SDK 不知道 channel/chat_id)
    channel: str = ""                   # 渠道类型
    chat_id: str = ""                   # 渠道内的聊天 ID
    
    # 连接 (必需 - SDK 不池化客户端)
    client: ClaudeSDKClient | None = None  # 活跃的 SDK 连接
    last_active: float = field(default_factory=time.time)
    
    # 并发 (必需 - SDK 不支持并发保护)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    phase: SessionPhase = SessionPhase.IDLE
    
    # 任务 (必需 - SDK 不发送 TaskStarted 消息)
    tasks: list[asyncio.Task] = field(default_factory=list)
```

### 新架构层级

```
之前 (5 层):

Runtime
  ├── _state_machine: SessionStateMachine
  ├── _session_store: SessionStore
  └── _state_coordinator: SessionStateCoordinator

Backend
  ├── _state_adapter: SessionStateAdapter (dual-write)
  └── 11 个 legacy dicts

之后 (1 层):

Runtime
  └── session_manager: SessionManager

Backend
  └── (直接访问 session_manager)
```

### SessionManager API

```python
class SessionManager:
    """统一的 session 状态管理器"""
    
    def __init__(self):
        self._sessions: dict[str, SessionState] = {}
        self._sdk_index: dict[str, str] = {}  # sdk_session_id -> session_key
        self._global_lock = asyncio.Lock()
    
    # === 生命周期 ===
    def get(self, session_key: str) -> SessionState | None
    def get_or_create(self, session_key: str) -> SessionState
    def get_by_sdk_id(self, sdk_session_id: str) -> SessionState | None
    
    # === SDK Session ID ===
    def set_sdk_session_id(self, session_key: str, sdk_id: str | None) -> None
    
    # === 路由 ===
    def set_routing(self, session_key: str, channel: str, chat_id: str) -> None
    def get_routing(self, session_key: str) -> tuple[str, str] | None
    def resolve_routing(self, identifier: str) -> tuple[str, str, str] | None
        # 返回 (session_key, channel, chat_id)，接受 session_key 或 sdk_session_id
    
    # === 并发 ===
    def can_start_request(self, session_key: str) -> bool
    def start_request(self, session_key: str) -> bool
    def end_request(self, session_key: str, phase: SessionPhase = IDLE) -> None
    
    # === 连接 ===
    def set_client(self, session_key: str, client: ClaudeSDKClient) -> None
    def get_client(self, session_key: str) -> ClaudeSDKClient | None
    def has_client(self, session_key: str) -> bool
    def list_client_sessions(self) -> list[str]
    
    # === 任务 ===
    def register_task(self, session_key: str, task: asyncio.Task) -> None
    def get_active_tasks(self, session_key: str) -> list[asyncio.Task]
    async def cancel_all_tasks(self, session_key: str) -> int
    
    # === 清理 ===
    async def cleanup_session(self, session_key: str) -> None
    def list_stale_sessions(self, ttl_seconds: float) -> list[str]
```

### 文件变更

#### 删除的文件（约 1500 行）

| 文件 | 行数 | 原因 |
|------|------|------|
| `xbot/agent/state/session_state_adapter.py` | ~480 | Dual-write adapter 已移除 |
| `xbot/agent/state/coordinator.py` | ~500 | Coordinator 已移除 |
| `xbot/agent/state/transaction.py` | ~200 | Transaction 支持已移除 |
| `xbot/agent/state/checker.py` | ~300 | 简化后不再需要 |

#### 新增的文件（约 300 行）

| 文件 | 行数 | 描述 |
|------|------|------|
| `xbot/agent/state/session_manager.py` | ~200 | 新的统一管理器 |
| `tests/test_session_manager.py` | ~100 | 单元测试 |

#### 修改的文件

| 文件 | 变更 |
|------|------|
| `xbot/agent/backends/claude_sdk_backend.py` | 移除 11 个 dicts，直接使用 `session_manager` |
| `xbot/agent/runtime.py` | 使用 `session_manager` 替代 `_session_store` + `_state_coordinator` |
| `xbot/agent/state/__init__.py` | 导出 `SessionManager`、`SessionState`、`SessionPhase` |
| `xbot/agent/state/machine.py` | 合并到 `session_manager.py` 或保持最小化 |

## 迁移策略

### Phase 1：准备（1-2 天）

1. 为当前 `SessionStateAdapter` 的关键路径补充测试
2. 为 `SessionStateCoordinator` 补充测试
3. 创建 `xbot/agent/state/session_manager.py` 新实现
4. 为 `SessionManager` 编写单元测试

### Phase 2：Feature Flag（2-3 天）

1. 在配置中添加 `use_new_session_manager: bool` 开关
2. 修改 Runtime 根据开关使用新旧实现
3. 部署时开关关闭（旧实现生效）
4. 在 staging 环境启用开关
5. 监控问题

### Phase 3：切换（1 天）

1. 在生产环境启用开关
2. 监控 24 小时
3. 如果发现问题，禁用开关（即时回滚）
4. 如果稳定，进入 Phase 4

### Phase 4：清理（1 天）

1. 移除 feature flag
2. 删除旧文件：
   - `session_state_adapter.py`
   - `coordinator.py`
   - `transaction.py`
   - `checker.py`
3. 从 Backend 移除 11 个 legacy dicts
4. 更新文档

### 回滚方案

如果迁移后发现问题：

1. **即时回滚**：禁用 feature flag（一个配置修改）
2. **无数据迁移**：SDK 管理持久化，无数据兼容问题
3. **Git revert**：如需要，回退到迁移前的 commit

## 风险评估

### 高风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Notification 回复路由失败 | 中 | 高 | 充分测试 `resolve_routing()`，同时支持 session_key 和 sdk_session_id |
| 并发请求导致消息混乱 | 低 | 高 | 在请求入口进行 Phase 检查，RUNNING 时拒绝新请求 |
| 测试覆盖不足 | 中 | 高 | 迁移前补充测试 |

### 中风险

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Backend 初始化时序变化 | 中 | 中 | 确保 `initialize()` 正确设置 session_manager 引用 |
| Task 取消逻辑变化 | 低 | 中 | 保留 `register_task` 和 `cancel_all_tasks` 语义 |
| Client 池行为变化 | 低 | 中 | 测试 TTL 清理和 LRU 驱逐 |

### 低风险

| 风险 | 概率 | 影响 |
|------|------|------|
| Model/skills_version 不再跟踪 | 低 | 低 | SDK options 已包含这些信息 |
| Commands 不再跟踪 | 低 | 低 | SDK 自己管理 |
| 统计信息格式变化 | 低 | 低 | 非核心功能 |

## 测试要求

### 单元测试

- `SessionManager.get_or_create()` 正确创建和获取
- `SessionManager.set_sdk_session_id()` 同时更新 session 和索引
- `SessionManager.resolve_routing()` 支持 session_key 和 sdk_session_id
- `SessionManager.start_request()` 非 IDLE 时拒绝
- `SessionManager.end_request()` 设置正确的 phase
- `SessionManager.cancel_all_tasks()` 取消活跃任务
- `SessionManager.list_stale_sessions()` 返回正确的超时 sessions

### 集成测试

- 完整消息流：接收 → 处理 → 回复
- 并发消息处理（第一条运行时第二条被拒绝）
- SDK notification → 回复路由
- Session 超时清理
- Client 池驱逐

### 手动测试

- Slack：发送消息，收到回复
- 飞书：发送消息，收到回复
- Telegram：发送消息，收到回复
- 多用户：多个用户同时发送消息
- 长时间运行任务：使用 interrupt 取消
- 通过 SDK API fork session

## 成功标准

1. **功能对等**：所有现有功能工作相同
2. **无消息路由失败**：所有 SDK notifications 到达正确目标
3. **并发安全**：无消息交错
4. **代码减少**：移除约 1200 行
5. **测试覆盖**：`SessionManager` 覆盖率 > 90%
6. **无回归**：所有现有测试通过

## 时间线

| 阶段 | 持续时间 | 累计 |
|------|----------|------|
| Phase 1：准备 | 1-2 天 | 1-2 天 |
| Phase 2：Feature Flag | 2-3 天 | 3-5 天 |
| Phase 3：切换 | 1 天 | 4-6 天 |
| Phase 4：清理 | 1 天 | 5-7 天 |

**预估总工作量**：5-7 天

## 参考

- SDK 能力测试结果（见 2026-04-06 测试输出）
- 当前架构：`xbot/agent/state/` 目录
- SDK 文档：`claude-agent-sdk` Python 包

## 附录：移除字段理由

| 字段 | 移除原因 |
|------|----------|
| `model` | SDK options 已跟踪 model，可通过 `get_server_info()` 查询 |
| `skills_version` | SDK options 跟踪 skills，无需单独跟踪 |
| `commands` | SDK 内部管理命令状态 |
| `persistent_session` | SDK 通过 JSONL 文件处理持久化 |
| `request_id` | SDK 管理请求/响应关联 |
| `previous_phase` | 简化的状态机不需要回滚 |
| `transition_count` | 仅用于调试，核心功能不需要 |