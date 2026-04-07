# Claude SDK Backend & Runtime 简化设计

**日期**: 2026-04-07
**状态**: 待实施

## 问题分析

### 现状
- `claude_sdk_backend.py` (2930 行) - 过度封装，33 个 delegation 方法仅转发调用
- `runtime.py` (2098 行) - 与 backend 边界模糊，紧耦合
- 两套 Session 管理系统重复：`SessionManager`（持久化）+ `StateSessionManager`（内存状态）
- `router.py` 只有单个 backend，不需要路由
- `protocol.py` 抽象类只有一个实现，不需要抽象层
- `delegation.py` (44 行) - 死代码，无任何使用

### 核心问题
1. **过度封装**: SDK 已提供 `delete_session`, `fork_session`, `list_sessions`, `get_session_info`，无需再封装
2. **过度设计的错误处理**: process() 方法包含复杂的 retry loop、stale detection，单用户场景不需要
3. **复杂的 client 管理**: TTL、LRU、Scavenger 对单用户场景过度
4. **重复的 session 管理**: 两套系统职责重叠

## 设计方案

### 新模块结构

```
xbot/agent/
├── service.py          # 核心 Agent 服务（合并 backend + runtime 核心逻辑）
├── session_manager.py  # 合并后的 Session 管理
├── client_pool.py      # 简化的 Client 管理
├── types.py            # 统一数据类型
├── handoff.py          # 保留 - 观测 SDK subagent
├── router.py           # 删除 - 单 backend 不需要
├── protocol.py         # 删除抽象层，保留数据类 AgentResponse, AgentContext
└── backends/
    ├── claude_sdk_backend.py   # 删除
    ├── client_lifecycle.py     # 合并到 client_pool.py
    ├── message_converter.py    # 合并到 service.py
    ├── options_builder.py      # 合并到 service.py
    └── delegation.py           # 删除 - 死代码
```

### 模块设计

#### 1. AgentService (service.py)

**职责**: 提供 Agent 的核心操作接口，是唯一的入口点

**核心方法** (必须保留):
```python
class AgentService:
    async def initialize(self, config: AgentConfig) -> None
    async def process(self, session_id: str, message: str) -> AsyncIterator[AgentResponse]
    async def shutdown(self) -> None
    async def reset_session(self, session_id: str) -> None
    async def get_session_commands(self, session_id: str) -> list[str]
    async def interrupt_session(self, session_id: str) -> None
    async def call_for_auxiliary(self, session_id: str, prompt: str) -> AgentResponse

    # SDK agents 配置（保留）
    def _build_sdk_agents(self) -> list[dict] | None
```

**内部依赖**:
- SessionManager (session 状态管理)
- ClientPool (client 连接管理)
- HandoffPolicy (观测 SDK subagent)
- SDK session 函数（直接调用，不封装）

**process() 简化**:
```python
async def process(self, session_id: str, message: str) -> AsyncIterator[AgentResponse]:
    # 1. 获取/创建 client
    client = await self._client_pool.get_or_create(session_id)
    
    # 2. 直接调用 SDK process（移除复杂 retry loop）
    async for event in client.process(message, session_id=session_id):
        response = self._convert_event(event)
        if response:
            yield response
    
    # 3. 更新 session 状态
    await self._session_manager.touch(session_id)
```

#### 2. SessionManager (session_manager.py)

**职责**: 合合两套 session 管理，统一持久化和内存状态

**接口**:
```python
class SessionManager:
    async def create(self, session_id: str, config: SessionConfig) -> Session
    async def get(self, session_id: str) -> Session | None
    async def touch(self, session_id: str) -> None
    async def reset(self, session_id: str) -> None
    async def delete(self, session_id: str) -> None
    
    # 使用 SDK 函数，不封装
    # delete_session, fork_session, list_sessions, get_session_info 直接调用
```

**合并来源**:
- `xbot/session/manager.py` - 持久化逻辑
- `xbot/agent/state/session_manager.py` - 内存状态管理

**数据结构**:
```python
@dataclass
class Session:
    id: str
    created_at: float
    last_used_at: float
    config: SessionConfig
    history: list[dict]  # 对话历史
    phase: SessionPhase   # 状态机（保留简化版）
    tasks: dict[str, TaskInfo]  # 任务管理（保留）
```

#### 3. ClientPool (client_pool.py)

**职责**: 简化的 client 连接管理（单用户场景）

**接口**:
```python
class ClientPool:
    async def get_or_create(self, session_id: str) -> ClaudeSDKClient
    async def disconnect(self, session_id: str) -> None
    async def disconnect_all(self) -> None
    async def snapshot(self) -> dict  # 状态观测
```

**简化逻辑**:
- 移除 TTL/Scavenger（单用户不需要自动清理）
- 移除 LRU（单用户场景容量足够）
- 保留基本的生命周期追踪（connected/disconnected 状态）

**合并来源**:
- `xbot/agent/backends/client_lifecycle.py`

#### 4. types.py

**职责**: 统一数据类型定义

**内容**:
```python
from xbot.agent.protocol import AgentResponse, AgentContext  # 从 protocol.py 移入
from xbot.agent.state.session_manager import SessionPhase, TaskInfo  # 从 session_manager.py 移入

@dataclass
class AgentConfig:
    model: str
    system_prompt: str
    tools: list[ToolConfig]
    mcp_servers: dict[str, MCPConfig]
    agents: list[dict] | None  # SDK agents 配置

@dataclass  
class SessionConfig:
    workspace: str
    permissions: dict
```

### 删除清单

| 文件 | 原因 |
|------|------|
| `xbot/agent/router.py` | 只有单个 backend，不需要路由 |
| `xbot/agent/protocol.py` (抽象类部分) | AgentBackend 抽象类无意义，保留数据类移到 types.py |
| `xbot/agent/backends/delegation.py` | 死代码，无任何使用 |
| `xbot/agent/backends/claude_sdk_backend.py` | 合并到 AgentService |
| `xbot/agent/state/session_manager.py` | 合并到 SessionManager |

### 保留清单

| 文件/功能 | 原因 |
|----------|------|
| `handoff.py` | 观测 SDK subagent 执行，独立功能 |
| `agents 配置` | SDK 的 subagent 功能，通过 `_build_sdk_agents()` 配置 |
| `SessionPhase 状态机` | 简化版保留，用于会话状态管理 |
| `TaskInfo` | 任务管理需要保留 |

## 实施阶段

### Phase 1: 创建新模块骨架
- 创建 `service.py`, `session_manager.py`, `client_pool.py`, `types.py`
- 定义接口和数据结构

### Phase 2: 迁移 SessionManager
- 合合 `xbot/session/manager.py` 和 `xbot/agent/state/session_manager.py`
- 确保持久化和内存状态统一管理

### Phase 3: 迁移 ClientPool
- 从 `client_lifecycle.py` 迁移简化版逻辑
- 移除 TTL/Scavenger/LRU

### Phase 4: 迁移 AgentService
- 从 `claude_sdk_backend.py` 迁移核心方法
- 从 `runtime.py` 迁移路由逻辑
- 直接使用 SDK session 函数

### Phase 5: 删除旧模块
- 删除 router.py, protocol.py(抽象类), delegation.py
- 删除旧 backend 文件
- 更新所有导入路径

### Phase 6: 验证测试
- 运行现有测试套件
- 手动测试关键功能：initialize, process, shutdown, reset_session, interrupt_session

## 功能完整性检查

| 功能 | 验证方式 |
|------|---------|
| Agent 初始化 | 测试 `initialize()` 可创建 client 和 session |
| 消息处理 | 测试 `process()` 可处理对话，返回 AgentResponse |
| Agent 关闭 | 测试 `shutdown()` 可清理所有 client 和 session |
| 会话重置 | 测试 `reset_session()` 可重置状态和历史 |
| 命令获取 | 测试 `get_session_commands()` 可返回可用命令 |
| 会话中断 | 测试 `interrupt_session()` 可中断正在进行的处理 |
| 辅助调用 | 测试 `call_for_auxiliary()` 可执行独立调用 |
| Session 持久化 | 测试 session 数据可保存和恢复 |
| Client 管理 | 测试 client 创建、连接、断开正常 |
| Handoff 观测 | 测试 handoff.py 可正常观测 subagent |
| Agents 配置 | 测试 `_build_sdk_agents()` 可正确配置 |

## 测试策略

1. **单元测试**: 为新模块编写单元测试
2. **集成测试**: 使用现有测试套件验证功能
3. **手动测试**: 关键功能的端到端测试
4. **回归测试**: 确保无功能降级

## 风险点

| 风险 | 缓解措施 |
|------|---------|
| SDK 函数调用失败 | SDK 函数已验证可用，直接调用无风险 |
| Session 持久化丢失 | 合合时保留原有持久化逻辑 |
| Client 连接问题 | 简化但保留基本生命周期追踪 |
| handoff 功能影响 | handoff.py 独立保留，不涉及重构 |

## 预期成果

- `claude_sdk_backend.py` (2930 行) → `service.py` (~500-800 行)
- `runtime.py` (2098 行) → 删除或精简为 CLI 入口 (~100-200 行)
- 两套 Session 管理 → 单一 `SessionManager`
- 复杂 client 管理 → 简化 `ClientPool`
- 总代码量预计减少 50%+

**功能不降级，架构更清晰，维护成本更低。**