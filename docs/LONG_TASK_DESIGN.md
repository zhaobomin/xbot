# 长任务能力设计方案

> 版本: 1.0
> 日期: 2026-03-21
> 状态: 设计中

---

## 一、背景与目标

### 1.1 背景

xbot 当前通过 `spawn` 工具支持后台任务执行，但存在以下限制：

1. **无状态持久化** - 服务重启后任务丢失
2. **无检查点机制** - 长任务无法暂停/恢复
3. **无进度追踪** - 用户无法查看任务进度
4. **迭代次数限制** - 固定 15 次，复杂任务可能无法完成

### 1.2 目标

- ✅ 任务状态持久化，支持跨会话恢复
- ✅ 检查点机制，支持暂停/恢复
- ✅ 进度追踪和通知
- ✅ 最小改动，复用现有 spawn 架构

### 1.3 非目标

- ❌ 不新建独立的 LongTaskAgent
- ❌ 不改动 ClaudeSDKBackend 入口
- ❌ 不引入新的外部依赖

---

## 二、架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    ClaudeSDKBackend                          │
│                    (入口不变)                                 │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          │ spawn 工具
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   SubagentManager (增强)                     │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                 现有功能                              │    │
│  │  - spawn() 创建后台任务                               │    │
│  │  - _run_subagent() 执行任务                           │    │
│  │  - cancel_by_session() 取消任务                       │    │
│  └─────────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                 新增功能                              │    │
│  │  - resume() 恢复任务                                  │    │
│  │  - pause() 暂停任务                                   │    │
│  │  - get_progress() 获取进度                            │    │
│  │  - _save_checkpoint() 保存检查点                      │    │
│  │  - _load_checkpoint() 加载检查点                      │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                      TaskStore (新增)                        │
│  - 任务状态持久化                                            │
│  - 检查点存储                                                │
│  - 进度记录                                                  │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 模块职责

| 模块 | 职责 | 改动 |
|------|------|------|
| SubagentManager | 后台任务执行管理 | 增强 |
| TaskStore | 任务状态持久化 | 新增 |
| SpawnTool | 用户调用入口 | 小改 |
| ClaudeSDKBackend | 统一入口 | 不变 |

---

## 三、数据模型

### 3.1 TaskState

```python
@dataclass
class TaskState:
    """任务状态"""
    task_id: str
    session_key: str
    label: str
    task: str              # 任务描述
    status: TaskStatus     # pending, running, paused, completed, failed
    created_at: datetime
    updated_at: datetime
    
    # 进度信息
    progress: float        # 0.0 - 1.0
    progress_message: str
    
    # 执行信息
    iteration: int
    max_iterations: int
    tokens_used: int
    
    # 结果
    result: str | None
    error: str | None
    
    # 来源信息
    origin_channel: str
    origin_chat_id: str
    
    # 检查点引用
    checkpoint_id: str | None


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

### 3.2 Checkpoint

```python
@dataclass
class Checkpoint:
    """任务检查点"""
    checkpoint_id: str
    task_id: str
    created_at: datetime
    
    # 执行状态
    messages: list[dict]    # 当前对话历史
    iteration: int
    
    # 工具状态
    tools_used: list[str]
    last_tool_result: str | None
    
    # 进度
    progress: float
    progress_message: str
```

### 3.3 存储结构

```
workspace/
└── .tasks/
    ├── tasks.json              # 任务索引
    ├── {task_id}/
    │   ├── state.json          # 任务状态
    │   └── checkpoints/
    │       ├── cp_001.json     # 检查点 1
    │       ├── cp_002.json     # 检查点 2
    │       └── ...
    └── ...
```

---

## 四、核心流程

### 4.1 创建任务流程

```
用户: /spawn 分析代码库并生成文档
                │
                ▼
        ┌───────────────┐
        │  SpawnTool    │
        │  .execute()   │
        └───────┬───────┘
                │
                ▼
        ┌───────────────┐
        │SubagentManager│
        │  .spawn()     │
        └───────┬───────┘
                │
    ┌───────────┴───────────┐
    │                       │
    ▼                       ▼
┌─────────┐          ┌─────────────┐
│TaskStore│          │ async Task  │
│.create()│          │ execution   │
└─────────┘          └─────────────┘
    │                       │
    ▼                       ▼
 返回 task_id          执行中...
```

### 4.2 检查点保存流程

```
SubagentManager._run_subagent()
                │
                ▼
        ┌───────────────┐
        │ iteration++   │
        └───────┬───────┘
                │
                ▼
        ┌───────────────┐
        │ 工具执行       │
        └───────┬───────┘
                │
                ▼
        ┌───────────────┐
        │should_checkpoint?│
        └───────┬───────┘
                │
        ┌───────┴───────┐
        │ Yes           │ No
        ▼               ▼
┌───────────────┐   继续
│_save_checkpoint│
└───────┬───────┘
        │
        ▼
┌───────────────┐
│  TaskStore    │
│.save_checkpoint│
└───────────────┘
```

### 4.3 任务恢复流程

```
用户: /resume task_abc123
                │
                ▼
        ┌───────────────┐
        │ SubagentManager│
        │   .resume()    │
        └───────┬───────┘
                │
                ▼
        ┌───────────────┐
        │   TaskStore   │
        │ .load_checkpoint│
        └───────┬───────┘
                │
                ▼
        ┌───────────────┐
        │ 恢复 messages │
        │ 恢复 iteration│
        └───────┬───────┘
                │
                ▼
        ┌───────────────┐
        │ 继续执行任务   │
        └───────────────┘
```

---

## 五、检查点策略

### 5.1 触发条件

| 条件 | 默认值 | 说明 |
|------|--------|------|
| 迭代次数 | 每 5 次 | 每 N 次迭代保存一次 |
| 时间间隔 | 每 60 秒 | 防止长时间无检查点 |
| 关键节点 | 工具调用后 | 重要操作后保存 |
| 手动触发 | /checkpoint | 用户主动保存 |

### 5.2 配置项

```json
{
  "agents": {
    "defaults": {
      "checkpoint_interval": 5,      // 每 N 次迭代
      "checkpoint_time_interval": 60, // 每 N 秒
      "max_checkpoints": 10          // 最多保留 N 个检查点
    }
  }
}
```

### 5.3 检查点内容

```python
{
    "checkpoint_id": "cp_001",
    "task_id": "task_abc123",
    "created_at": "2026-03-21T02:00:00",
    "iteration": 5,
    "messages": [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "...", "tool_calls": [...]},
        {"role": "tool", "content": "..."},
        ...
    ],
    "progress": 0.35,
    "progress_message": "正在分析 src/components/ 目录",
    "tools_used": ["read_file", "exec"],
    "tokens_used": 5000
}
```

---

## 六、进度追踪

### 6.1 进度计算

```python
def calculate_progress(
    iteration: int,
    max_iterations: int,
    tokens_used: int,
    estimated_tokens: int,
) -> float:
    """计算任务进度"""
    # 基于迭代次数
    iteration_progress = iteration / max_iterations
    
    # 基于 token 使用
    token_progress = tokens_used / estimated_tokens if estimated_tokens > 0 else 0
    
    # 加权平均
    return min(0.9, (iteration_progress * 0.6 + token_progress * 0.4))
```

### 6.2 进度通知策略

| 触发条件 | 行为 |
|----------|------|
| 进度变化 >= 10% | 发送进度通知 |
| 时间间隔 >= 5 分钟 | 发送心跳通知 |
| 任务完成 | 发送完成通知 |
| 任务失败 | 发送错误通知 |

### 6.3 通知消息格式

```
[任务进度] task_abc123

任务: 分析代码库并生成文档
进度: 35% ████████░░░░░░░░░░░
状态: 正在分析 src/components/ 目录

命令: /status task_abc123 查看详情
```

---

## 七、命令设计

### 7.1 新增命令

| 命令 | 功能 | 示例 |
|------|------|------|
| `/spawn <任务>` | 启动后台任务 | `/spawn 分析代码库` |
| `/status [task_id]` | 查看任务状态 | `/status task_abc123` |
| `/tasks` | 列出所有任务 | `/tasks` |
| `/pause <task_id>` | 暂停任务 | `/pause task_abc123` |
| `/resume <task_id>` | 恢复任务 | `/resume task_abc123` |
| `/cancel <task_id>` | 取消任务 | `/cancel task_abc123` |

### 7.2 命令处理

```python
# 在 AgentRuntime 或 Loop 中处理
async def _handle_message(self, msg: InboundMessage) -> OutboundMessage | None:
    cmd = msg.content.strip().lower()
    
    # 任务管理命令
    if cmd.startswith("/spawn "):
        return await self._handle_spawn(msg)
    elif cmd.startswith("/status"):
        return await self._handle_status(msg)
    elif cmd.startswith("/tasks"):
        return await self._handle_list_tasks(msg)
    elif cmd.startswith("/pause "):
        return await self._handle_pause(msg)
    elif cmd.startswith("/resume "):
        return await self._handle_resume(msg)
    elif cmd.startswith("/cancel "):
        return await self._handle_cancel(msg)
    ...
```

---

## 八、文件改动清单

### 8.1 新增文件

| 文件 | 说明 | 行数估计 |
|------|------|----------|
| `xbot/agent/task_store.py` | 任务状态持久化 | ~150 行 |
| `xbot/agent/task_types.py` | 任务相关类型定义 | ~50 行 |

### 8.2 修改文件

| 文件 | 改动说明 | 行数估计 |
|------|----------|----------|
| `xbot/agent/subagent.py` | 增强任务管理能力 | +100 行 |
| `xbot/agent/tools/spawn.py` | 支持更多参数 | +20 行 |
| `xbot/agent/runtime.py` | 添加任务命令处理 | +50 行 |
| `xbot/config/schema.py` | 添加检查点配置 | +10 行 |

### 8.3 总改动量

| 类型 | 数量 |
|------|------|
| 新增文件 | 2 个 |
| 修改文件 | 4 个 |
| 新增代码 | ~350 行 |
| 修改代码 | ~180 行 |

---

## 九、测试计划

### 9.1 单元测试

| 测试文件 | 测试内容 |
|----------|----------|
| `tests/test_task_store.py` | 任务创建、保存、加载、检查点 |
| `tests/test_subagent_checkpoint.py` | 检查点保存和恢复 |
| `tests/test_task_commands.py` | 命令处理逻辑 |

### 9.2 集成测试

| 测试场景 | 验证点 |
|----------|--------|
| 长任务执行 | 任务能正常完成 |
| 任务暂停恢复 | 检查点正确恢复 |
| 服务重启 | 任务状态持久化正确 |
| 并发任务 | 多任务并行执行 |

---

## 十、实施计划

### Phase 1: 基础能力 (1-2 天)

- [ ] 实现 TaskStore
- [ ] 实现 TaskState 数据模型
- [ ] 修改 SubagentManager 支持状态保存

### Phase 2: 检查点机制 (2-3 天)

- [ ] 实现检查点保存逻辑
- [ ] 实现检查点恢复逻辑
- [ ] 添加配置项

### Phase 3: 命令支持 (1 天)

- [ ] 实现 /status, /tasks, /pause, /resume, /cancel 命令
- [ ] 添加进度通知

### Phase 4: 测试和文档 (1 天)

- [ ] 单元测试
- [ ] 集成测试
- [ ] 更新文档

---

## 十一、风险和缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 检查点文件过大 | 磁盘占用 | 限制检查点数量，压缩存储 |
| 恢复后状态不一致 | 任务失败 | 完善检查点数据完整性校验 |
| 并发写入冲突 | 数据损坏 | 使用文件锁或异步队列 |

---

## 十二、Session 与上下文隔离

### 12.1 独立 Session 设计

**子任务使用独立 Session**，与主会话隔离。

```
主会话 (session_key: telegram:7743853836)
    │
    ├── message 1: "你好"
    ├── message 2: "分析代码库"
    │
    └── spawn 子任务
            │
            ▼
    子任务 Session (session_key: task:abc123)
        │
        ├── message 1: [系统提示]
        ├── message 2: "分析代码库..."
        ├── message 3: [工具结果]
        └── ...
```

### 12.2 上下文隔离规则

| 数据 | 主会话 | 子任务 | 隔离策略 |
|------|--------|--------|----------|
| 对话历史 | ✅ 独立 | ✅ 独立 | **完全隔离**，不继承 |
| Memory (MEMORY.md) | ✅ 读写 | ✅ 只读 | 子任务可读取，不修改 |
| 文件系统 | ✅ 访问 | ✅ 访问 | 共享 workspace |
| 工具权限 | ✅ 完整 | ⚠️ 受限 | 无 message/spawn 工具 |

### 12.3 Session Key 规则

```python
# 主会话
session_key = "telegram:7743853836"

# 子任务 Session
task_session_key = f"task:{task_id}"  # 如 "task:abc123"
```

### 12.4 实现代码

```python
class SubagentManager:
    async def spawn(self, task: str, ...) -> str:
        task_id = str(uuid.uuid4())[:8]
        
        # 子任务使用独立 session
        task_session_key = f"task:{task_id}"
        
        # 创建独立 session
        task_session = self.sessions.get_or_create(task_session_key)
        task_session.metadata["parent_session"] = session_key  # 记录父会话
        task_session.metadata["task_id"] = task_id
        
        ...
```

---

## 十三、任务依赖（预留扩展）

### 13.1 当前决策

**Phase 1-4 不实现任务依赖**，但预留扩展接口。

### 13.2 未来设计

```python
@dataclass
class TaskDependency:
    """任务依赖关系"""
    depends_on: list[str]      # 依赖的任务 ID
    condition: str             # "success" | "always" | "failure"
    timeout: float | None      # 等待超时

@dataclass 
class TaskState:
    # ... 现有字段
    
    # 预留依赖字段
    dependencies: list[TaskDependency] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)  # 当前阻塞的任务
```

### 13.3 使用示例（未来）

```python
# 任务 A: 分析代码
task_a = await spawn("分析代码库结构")

# 任务 B: 依赖 A 完成后生成文档
task_b = await spawn(
    "生成 API 文档",
    depends_on=[task_a],
    condition="success"
)

# 任务 C: 无论 A 结果如何都执行
task_c = await spawn(
    "生成 README",
    depends_on=[task_a],
    condition="always"
)
```

---

## 十四、后续扩展

完成基础能力后，可以考虑：

1. **任务优先级** - 支持任务优先级队列
2. **任务依赖** - 支持任务间依赖关系（见第十三章）
3. **分布式执行** - 支持多机任务分发
4. **进度 UI** - 提供 Web 进度查看界面