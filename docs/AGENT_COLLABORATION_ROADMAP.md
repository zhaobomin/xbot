# Agent 协作能力增强路线图

> 创建时间：2026-03-21
> 更新时间：2026-03-21
> 状态：规划讨论中

## 一、当前 Agent 架构

### 1.1 架构概览

```
┌─────────────────────────────────────────┐
│              Main Agent                  │
│  (接收用户请求，决定如何处理)              │
└─────────────┬───────────────────────────┘
              │
    ┌─────────┼─────────┐
    ▼         ▼         ▼
┌───────┐ ┌───────┐ ┌───────────┐
│Handoff│ │Spawn  │ │ 直接处理   │
│专家代理│ │后台代理│ │ (Tools)   │
└───────┘ └───────┘ └───────────┘
```

### 1.2 已有能力

| 能力 | 说明 | 代码位置 |
|------|------|----------|
| 主代理 + 子代理配置 | 支持定义多个专家代理 | `config/schema.py:AgentDefinition` |
| Handoff 决策 | 自动判断是否转交专家代理 | `agent/handoff_policy.py` |
| Spawn 后台任务 | 异步执行长时任务 | `agent/subagent.py` |
| 工具调用 | 文件操作、Shell、Web 等 | `agent/tools/` |

### 1.3 配置示例

```json
{
  "agents": {
    "type": "claude_sdk",
    "claude_sdk": {
      "agents": {
        "research": {
          "description": "搜索和分析网络信息",
          "prompt": "你是一个研究助手...",
          "when": "用户需要搜索、调研、收集信息",
          "model": "sonnet"
        },
        "code": {
          "description": "编写和调试代码",
          "prompt": "你是一个代码专家...",
          "when": "用户需要编写、修改、调试代码",
          "model": "sonnet"
        }
      }
    }
  }
}
```

---

## 二、架构局限性分析

### 2.1 当前 Channel 模式的限制

当前基于 Channel 的同步请求-响应模式：

```
用户 ──► Channel (Telegram/飞书/Discord) ──► Agent ──► 回复
            ↓
      同步请求-响应模式
      等待时间有限制
```

**限制点**：

| 需求 | 当前架构 | 问题 |
|------|----------|------|
| 长时间执行（几分钟+） | 同步等待 | IM 平台 webhook 超时（5-30秒） |
| 任务分解、多 Agent 协作 | 单次请求处理 | 状态管理复杂 |
| 进度追踪 | 无状态 | 用户不知道执行到哪了 |
| 暂停/恢复/取消 | 无持久化 | 不支持 |
| 多轮协作 | 独立会话 | Agent 间通信困难 |

**结论**：当前基于 Channel 的同步模式，不太适合复杂任务编排。

---

## 三、Agent 任务调度架构

### 3.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              用户请求入口                                │
│                                                                         │
│   Telegram ──┐                                                          │
│   飞书 ──────┼──► Channel Manager ──► Main Agent (Claude SDK)           │
│   Discord ───┘                                      │                   │
│   CLI ──────────────────────────────────────────────┘                   │
│                                                     │                   │
└─────────────────────────────────────────────────────┼───────────────────┘
                                                      │
                                                      ▼
                            ┌─────────────────────────────────────────┐
                            │         Main Agent 决策中心              │
                            │                                         │
                            │   1. 分析请求复杂度                       │
                            │   2. 判断是否需要分解                     │
                            │   3. 选择执行方式                        │
                            └─────────────────┬───────────────────────┘
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    │                         │                         │
                    ▼                         ▼                         ▼
          ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
          │   直接处理       │      │   Spawn 后台    │      │   CLI 进程      │
          │   (同步)        │      │   (同进程)      │      │   (独立进程)    │
          │                 │      │                 │      │                 │
          │  < 30 秒        │      │  30s - 2min    │      │  > 2 分钟       │
          │  简单问答       │      │  中等任务       │      │  复杂任务       │
          └────────┬────────┘      └────────┬────────┘      └────────┬────────┘
                   │                        │                        │
                   │                        │                        │
                   ▼                        ▼                        ▼
          ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
          │  Tools 执行     │      │ SubagentManager │      │  TaskRunner     │
          │                 │      │                 │      │                 │
          │ • 文件读写      │      │ asyncio.Task    │      │ subprocess      │
          │ • Shell 命令    │      │ 共享 Provider   │      │ 独立进程        │
          │ • Web 搜索      │      │                 │      │ 独立 Provider   │
          │ • MCP 工具      │      │                 │      │                 │
          └────────┬────────┘      └────────┬────────┘      └────────┬────────┘
                   │                        │                        │
                   │                        │                        │
                   ▼                        ▼                        ▼
          ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
          │   同步返回      │      │  后台执行完成   │      │  持久化存储     │
          │   直接回复用户   │      │  通知 Channel   │      │  可恢复/取消    │
          │                 │      │                 │      │  通知 Channel   │
          └─────────────────┘      └─────────────────┘      └─────────────────┘
```

### 3.2 执行方式对比

| 执行方式 | 适用场景 | 进程 | 持久化 | 超时限制 | 通知方式 |
|----------|----------|------|--------|----------|----------|
| **直接处理** | < 30s 简单任务 | 同进程 | 否 | Channel 限制 | 同步返回 |
| **Spawn** | 30s-2min 中等任务 | 同进程 | 否 | 无 | 后台通知 |
| **CLI 进程** | > 2min 复杂任务 | 独立 | 是 | 无 | 任务状态 + 通知 |

---

## 四、执行流程详解

### 4.1 场景 1：简单任务（直接处理）

```
用户 ──► Channel ──► Main Agent ──► Tools ──► 结果 ──► 回复用户
                                   (同步)
                                   < 30s
```

### 4.2 场景 2：中等任务（Spawn 后台）

```
用户 ──► Channel ──► Main Agent ──► Spawn ──► "已启动后台任务"
                      │
                      │         ┌──────────────────────┐
                      │         │  asyncio.Task (后台)  │
                      │         │                      │
                      └─────────┼──► SubagentManager   │
                                │         │            │
                                │         ▼            │
                                │    Claude SDK Agent  │
                                │         │            │
                                │         ▼            │
                                │      完成通知        │
                                └─────────┬────────────┘
                                          │
                                          ▼
                                    Channel 推送结果
```

### 4.3 场景 3：复杂任务（CLI 进程）

```
用户 ──► Channel ──► Main Agent ──► TaskRunner ──► "任务已启动 ID: xxx"
                      │
                      │         ┌──────────────────────────────────┐
                      │         │        CLI Agent 进程             │
                      │         │   (独立运行，可跨会话恢复)         │
                      │         │                                  │
                      │         │   ┌────────────────────────────┐  │
                      │         │   │  task_xxx.json (状态文件)  │  │
                      │         │   │  status: running           │  │
                      │         │   │  progress: 30%             │  │
                      │         │   └────────────────────────────┘  │
                      │         │               │                   │
                      └─────────┼───────────────┼───────────────────┘
                                │               │
                                │               ▼
                                │      Claude SDK Agent
                                │               │
                                │               ▼
                                │          任务执行
                                │               │
                                │        ┌──────┴──────┐
                                │        ▼             ▼
                                │    成功通知      失败记录
                                │        │
                                ▼        ▼
                          Channel 推送结果
                          
用户随时查询: /task xxx
```

---

## 五、CLI Agent 独立进程方案

### 5.1 设计思路

复杂任务通过独立的 CLI Agent 进程执行，解决 Channel 同步模式限制：

**优点**：
- **解耦**：Channel 不需要等待，无超时问题
- **简单**：不需要大改现有架构
- **灵活**：CLI Agent 可以运行任意长时间
- **可控**：可以暂停/恢复/监控进度
- **可靠**：任务状态持久化，支持恢复

### 5.2 CLI 命令设计

```bash
# 启动子任务
xbot task run \
  --task "分析项目结构，生成报告" \
  --agent research \
  --task-id task_abc123 \
  --notify feishu:ou_xxx \
  --workspace /path/to/project

# 查看任务状态
xbot task status task_abc123

# 列出所有任务
xbot task list

# 取消任务
xbot task cancel task_abc123

# 恢复中断的任务
xbot task resume task_abc123
```

### 5.3 任务状态文件

```json
// ~/.xbot/tasks/task_abc123.json
{
  "id": "task_abc123",
  "status": "running",
  "created_at": "2026-03-21T14:00:00",
  "request": "分析项目结构",
  "agent": "research",
  "notify": {
    "channel": "feishu",
    "chat_id": "ou_xxx"
  },
  "progress": [
    {"step": "扫描文件", "status": "completed"},
    {"step": "分析代码", "status": "running"},
    {"step": "生成报告", "status": "pending"}
  ],
  "result": null
}
```

### 5.4 核心代码框架

```python
# xbot/cli/task_commands.py

import asyncio
import subprocess
from pathlib import Path

class TaskRunner:
    """启动和管理 CLI Agent 任务"""
    
    def __init__(self, task_dir: Path):
        self.task_dir = task_dir
        self.task_dir.mkdir(parents=True, exist_ok=True)
    
    async def run_task(
        self,
        task: str,
        agent: str = "default",
        notify: str | None = None,
        workspace: Path | None = None,
    ) -> str:
        """启动一个 CLI Agent 任务，返回任务 ID"""
        task_id = generate_task_id()
        
        # 创建任务状态文件
        task_file = self.task_dir / f"{task_id}.json"
        task_file.write_text(json.dumps({
            "id": task_id,
            "status": "pending",
            "task": task,
            "agent": agent,
            "notify": notify,
            "workspace": str(workspace) if workspace else None,
            "created_at": datetime.now().isoformat(),
            "progress": [],
            "result": None,
        }))
        
        # 启动 CLI 子进程
        cmd = [
            "xbot", "agent", "run",
            "--task-id", task_id,
            "--task", task,
        ]
        if agent != "default":
            cmd.extend(["--agent", agent])
        if notify:
            cmd.extend(["--notify", notify])
        
        # 后台运行
        subprocess.Popen(cmd, start_new_session=True)
        
        return task_id
    
    def get_status(self, task_id: str) -> dict:
        """获取任务状态"""
        task_file = self.task_dir / f"{task_id}.json"
        if not task_file.exists():
            raise ValueError(f"Task {task_id} not found")
        return json.loads(task_file.read_text())
    
    def list_tasks(self) -> list[dict]:
        """列出所有任务"""
        tasks = []
        for f in self.task_dir.glob("*.json"):
            tasks.append(json.loads(f.read_text()))
        return sorted(tasks, key=lambda t: t["created_at"], reverse=True)


class CLIAgentRunner:
    """CLI Agent 实际执行任务"""
    
    async def run(self, task_id: str):
        """执行指定任务"""
        task = self._load_task(task_id)
        self._update_status(task_id, "running")
        
        try:
            # 创建 Claude SDK Agent
            agent = await self._create_agent(task["agent"])
            
            # 执行任务
            result = await agent.run(task["task"])
            
            # 保存结果
            self._save_result(task_id, result)
            
            # 通知
            if task.get("notify"):
                await self._notify(task["notify"], result)
                
        except Exception as e:
            self._update_status(task_id, "failed", error=str(e))
```

---

## 六、任务编排场景

### 6.1 多 Agent 协作流程

```
用户: "分析项目，生成报告，发到飞书"
                          │
                          ▼
              ┌───────────────────────┐
              │     TaskPlanner       │
              │     (任务分解)         │
              └───────────┬───────────┘
                          │
              分解为:
              ┌───────────┼───────────┐
              │           │           │
              ▼           ▼           ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Task 1   │ │ Task 2   │ │ Task 3   │
        │ 代码分析 │ │ 报告生成 │ │ 飞书发送 │
        │ (并行)   │ │ (串行)   │ │ (串行)   │
        └────┬─────┘ └────┬─────┘ └────┬─────┘
             │            │            │
             │   ┌────────┘            │
             │   │ 依赖 Task 1 结果    │
             │   │                     │
             ▼   ▼                     ▼
        CLI Agent 1              CLI Agent 3
        (独立进程)               (独立进程)
             │                        │
             ▼                        │
        CLI Agent 2 ─────────────────┘
        (独立进程)
             │
             ▼
        结果聚合 ──► 通知用户
```

---

## 七、数据存储架构

### 7.1 数据流图

```
┌──────────────────────────────────────────────────────────────────────┐
│                           数据存储层                                  │
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │ Session     │  │ Memory      │  │ Task State  │  │ Workspace   │ │
│  │ (会话状态)   │  │ (长期记忆)   │  │ (任务状态)   │  │ (工作目录)   │ │
│  │             │  │             │  │             │  │             │ │
│  │ .xbot/      │  │ MEMORY.md   │  │ tasks/      │  │ workspace/  │ │
│  │ sessions/   │  │ HISTORY.md  │  │ task_*.json │  │             │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘ │
│         ▲                ▲                ▲                ▲        │
└─────────┼────────────────┼────────────────┼────────────────┼────────┘
          │                │                │                │
          │                │                │                │
┌─────────┴────────────────┴────────────────┴────────────────┴────────┐
│                           Agent 层                                   │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                     Main Agent                               │    │
│  │                   (Claude SDK)                               │    │
│  └───────────────────────────┬─────────────────────────────────┘    │
│                              │                                       │
│        ┌─────────────────────┼─────────────────────┐                │
│        │                     │                     │                │
│        ▼                     ▼                     ▼                │
│  ┌───────────┐         ┌───────────┐         ┌───────────┐         │
│  │  Tools    │         │  Spawn    │         │ CLI Task  │         │
│  │  直接调用  │         │  后台任务  │         │ 独立进程  │         │
│  └───────────┘         └───────────┘         └───────────┘         │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 八、Agent 协作的 4 个方向

### 8.1 方向对比

| 方向 | 价值 | 工作量 | 复杂度 | 推荐优先级 |
|------|------|--------|--------|------------|
| 任务编排增强 | ⭐⭐⭐⭐⭐ | 中等 | 中等 | **P0** |
| 专家代理生态 | ⭐⭐⭐⭐ | 中等 | 低 | **P1** |
| 代理间通信 | ⭐⭐⭐⭐ | 中等 | 中等 | P2 |
| 自主规划能力 | ⭐⭐⭐ | 较高 | 高 | P3 |

---

### 8.2 方向 1：任务编排增强（P0）

#### 目标

让主代理能够分解复杂任务，协调多个子代理并行/串行执行。

#### 核心组件

| 组件 | 职责 |
|------|------|
| `TaskPlanner` | LLM 驱动的任务分解器 |
| `TaskExecutor` | 并行/串行执行引擎 |
| `ResultAggregator` | 结果聚合器 |
| `ProgressTracker` | 进度追踪器 |

#### 实施计划

**Phase 1（2周）- CLI 任务管理基础**
- [ ] 实现 `TaskRunner`：启动/管理 CLI Agent 任务
- [ ] 实现任务持久化（JSON 文件存储）
- [ ] 实现任务命令：`xbot task run/list/status/cancel`
- [ ] 单元测试

**Phase 2（2周）- Channel 集成**
- [ ] 实现复杂任务判断逻辑
- [ ] 实现 Main Agent 触发 CLI Agent
- [ ] 实现完成通知回调
- [ ] 集成测试

**Phase 3（2周）- 任务编排**
- [ ] 实现 `TaskPlanner`：任务分解提示词 + 解析逻辑
- [ ] 实现 `TaskExecutor`：支持串行/并行执行
- [ ] 实现 `ResultAggregator`：汇总子任务结果
- [ ] 进度追踪

---

### 8.3 方向 2：专家代理生态（P1）

#### 目标

预置常用专家代理，支持用户安装/创建新代理。

#### 预置专家代理

```
预置专家代理:
├── 🔍 Research Agent    (网络搜索、资料收集)
├── 💻 Code Agent        (代码编写、调试)
├── 📊 Analysis Agent    (数据分析、可视化)
├── 📝 Writing Agent     (文档撰写、翻译)
└── 🔧 DevOps Agent      (部署、运维)
```

#### 实施计划

- [ ] 定义专家代理配置格式
- [ ] 实现 3-5 个预置专家代理
- [ ] 支持从文件加载代理配置
- [ ] 代理市场（后续）

---

### 8.4 方向 3：代理间通信（P2）

#### 目标

让代理之间能互相通信、协作。

#### 核心功能

| 功能 | 说明 |
|------|------|
| 消息总线 | 代理间发送消息 |
| 共享状态 | 多代理访问同一工作区 |
| 请求/响应 | 代理A请求代理B帮助 |

---

### 8.5 方向 4：自主规划能力（P3）

#### 目标

Agent 能自主规划、反思、调整策略。

---

## 九、技术方案（任务编排增强）

### 9.1 核心接口设计

```python
from dataclasses import dataclass
from enum import Enum
from typing import Any

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class SubTask:
    id: str
    description: str
    agent: str  # 使用的代理
    dependencies: list[str]  # 依赖的任务 ID
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str | None = None

@dataclass
class TaskPlan:
    task_id: str
    original_request: str
    subtasks: list[SubTask]
    parallel_groups: list[list[str]]  # 可并行执行的任务组

class TaskPlanner:
    """LLM 驱动的任务分解器"""
    
    async def plan(self, request: str, available_agents: list[str]) -> TaskPlan:
        """将用户请求分解为子任务"""
        ...

class TaskExecutor:
    """任务执行引擎"""
    
    async def execute(self, plan: TaskPlan) -> dict[str, Any]:
        """执行任务计划，返回各子任务结果"""
        ...

class ResultAggregator:
    """结果聚合器"""
    
    def aggregate(self, results: dict[str, Any]) -> str:
        """聚合子任务结果，生成最终回复"""
        ...
```

---

## 十、讨论要点

### 10.1 待确认问题

1. **任务粒度**：如何判断何时需要分解任务？阈值是什么？
2. **并行限制**：最多允许多少个子任务并行执行？
3. **错误策略**：子任务失败时，是继续执行还是全部回滚？
4. **进度展示**：如何向用户展示多任务进度？
5. **资源控制**：如何防止任务编排消耗过多 token？

### 10.2 备选方案

**方案 A：LLM 驱动分解**
- 优点：灵活，能处理各种场景
- 缺点：不可控，可能分解不合理

**方案 B：规则驱动分解**
- 优点：可控，可预测
- 缺点：不够灵活，需要预设规则

**方案 C：混合模式（推荐）**
- 简单请求：规则判断，不分解
- 复杂请求：LLM 分解，人工确认

---

## 十一、实施路线图

### 阶段 1：CLI 任务管理基础（1周）

```
目标：实现基础的 CLI 任务管理能力

任务：
├── TaskRunner 类
│   ├── run_task()    启动任务
│   ├── get_status()  查询状态
│   ├── list_tasks()  列出任务
│   └── cancel_task() 取消任务
│
├── CLIAgentRunner 类
│   ├── run()         执行任务
│   └── notify()      完成通知
│
└── CLI 命令
    ├── xbot task run
    ├── xbot task list
    ├── xbot task status
    └── xbot task cancel
```

### 阶段 2：Channel 集成（1周）

```
目标：让 Channel Agent 能触发和监控 CLI 任务

任务：
├── 复杂任务判断逻辑
├── 触发 CLI Agent
├── 接收完成通知
└── 用户命令: /task, /tasks
```

### 阶段 3：任务编排（2周）

```
目标：实现任务分解和多 Agent 协作

任务：
├── TaskPlanner (任务分解)
├── TaskExecutor (并行/串行执行)
├── ResultAggregator (结果聚合)
└── ProgressTracker (进度追踪)
```

---

## 十二、下一步

- [ ] 确认 CLI Agent 方案
- [ ] 开始 Phase 1 实现
- [ ] 设计任务状态文件格式

---

*文档维护者：xbot team*