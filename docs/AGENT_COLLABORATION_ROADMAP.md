# Agent 协作能力增强路线图

> 创建时间：2026-03-21
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

## 二、Agent 协作的 4 个方向

### 2.1 方向对比

| 方向 | 价值 | 工作量 | 复杂度 | 推荐优先级 |
|------|------|--------|--------|------------|
| 任务编排增强 | ⭐⭐⭐⭐⭐ | 中等 | 中等 | **P0** |
| 专家代理生态 | ⭐⭐⭐⭐ | 中等 | 低 | **P1** |
| 代理间通信 | ⭐⭐⭐⭐ | 中等 | 中等 | P2 |
| 自主规划能力 | ⭐⭐⭐ | 较高 | 高 | P3 |

---

### 2.2 方向 1：任务编排增强（P0）

#### 目标

让主代理能够分解复杂任务，协调多个子代理并行/串行执行。

#### 场景示例

```
用户: "帮我分析这个项目，生成报告，并发送到飞书"

系统分解:
  Task 1 [并行]: 代码分析 Agent → 分析项目结构
  Task 2 [并行]: 依赖检查 Agent → 检查依赖状态
  Task 3 [串行]: 报告生成 Agent → 汇总 Task 1, 2 结果
  Task 4 [串行]: 飞书发送 Agent → 发送报告

最终: 返回执行结果
```

#### 核心组件

| 组件 | 职责 |
|------|------|
| `TaskPlanner` | LLM 驱动的任务分解器 |
| `TaskExecutor` | 并行/串行执行引擎 |
| `ResultAggregator` | 结果聚合器 |
| `ProgressTracker` | 进度追踪器 |

#### 实施计划

**Phase 1（2周）- 基础编排**
- [ ] 实现 `TaskPlanner`：任务分解提示词 + 解析逻辑
- [ ] 实现 `TaskExecutor`：支持串行/并行执行
- [ ] 实现 `ResultAggregator`：汇总子任务结果
- [ ] 单元测试

**Phase 2（2周）- 增强能力**
- [ ] 进度追踪：实时展示子任务状态
- [ ] 错误处理：子任务失败重试/跳过策略
- [ ] 超时控制：限制单个子任务时间
- [ ] 集成测试

**Phase 3（持续）- 优化迭代**
- [ ] 优化任务分解提示词
- [ ] 支持用户自定义编排规则
- [ ] 性能优化

---

### 2.3 方向 2：专家代理生态（P1）

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

#### 专家代理配置

```yaml
# agents/research.yaml
name: research
display_name: 研究助手
description: 搜索和分析网络信息
model: sonnet
tools:
  - web_search
  - web_fetch
  - read_file
  - write_file
prompt: |
  你是一个研究助手，擅长搜索和分析信息。
  你的任务是收集、整理、分析用户需要的信息。
when:
  - 用户需要搜索信息
  - 用户需要调研某个主题
  - 用户需要收集资料
```

#### 实施计划

- [ ] 定义专家代理配置格式
- [ ] 实现 3-5 个预置专家代理
- [ ] 支持从文件加载代理配置
- [ ] 代理市场（后续）

---

### 2.4 方向 3：代理间通信（P2）

#### 目标

让代理之间能互相通信、协作。

#### 架构设计

```
┌──────────────────────────────────────┐
│           Main Agent                  │
│   (协调者，分配任务，汇总结果)          │
└─────────┬───────────────┬─────────────┘
          │               │
    ┌─────▼─────┐   ┌─────▼─────┐
    │ Research  │◄──►│   Code    │
    │  Agent    │   │  Agent    │
    └───────────┘   └───────────┘
          │               │
          └───────┬───────┘
                  ▼
          共享工作区/消息队列
```

#### 核心功能

| 功能 | 说明 |
|------|------|
| 消息总线 | 代理间发送消息 |
| 共享状态 | 多代理访问同一工作区 |
| 请求/响应 | 代理A请求代理B帮助 |

#### 实施计划

- [ ] 设计代理消息协议
- [ ] 实现共享工作区
- [ ] 实现代理间消息传递
- [ ] 集成测试

---

### 2.5 方向 4：自主规划能力（P3）

#### 目标

Agent 能自主规划、反思、调整策略。

#### 流程设计

```
┌─────────────────────────────────────┐
│            Goal: "完成项目部署"       │
└─────────────────┬───────────────────┘
                  ▼
┌─────────────────────────────────────┐
│  Planning: 规划执行步骤              │
│  1. 检查环境 2. 安装依赖 3. 配置...  │
└─────────────────┬───────────────────┘
                  ▼
┌─────────────────────────────────────┐
│  Execution: 执行步骤                 │
│  Step 1 → Step 2 → Step 3           │
└─────────────────┬───────────────────┘
                  ▼
┌─────────────────────────────────────┐
│  Reflection: 检查结果，调整计划       │
│  "Step 2 失败，尝试备选方案"          │
└─────────────────────────────────────┘
```

#### 实施计划

- [ ] 设计规划-执行-反思循环
- [ ] 实现状态持久化
- [ ] 支持计划恢复
- [ ] 集成测试

---

## 三、技术方案（任务编排增强）

### 3.1 核心接口设计

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

### 3.2 任务分解提示词

```markdown
你是一个任务规划器。用户会给你一个请求，你需要：

1. 判断是否需要分解为多个子任务
2. 如果需要，输出 JSON 格式的任务计划

可用的专家代理：
{agent_list}

用户请求：{user_request}

输出格式（如果需要分解）：
```json
{
  "needs_decomposition": true,
  "reason": "这是一个复杂任务，需要多个步骤",
  "subtasks": [
    {
      "id": "task_1",
      "description": "分析项目结构",
      "agent": "code",
      "dependencies": []
    },
    {
      "id": "task_2", 
      "description": "生成分析报告",
      "agent": "writing",
      "dependencies": ["task_1"]
    }
  ]
}
```

如果不需要分解：
```json
{
  "needs_decomposition": false,
  "reason": "这是一个简单任务，主代理可以直接处理"
}
```
```

### 3.3 执行流程

```
1. 用户发送请求
2. TaskPlanner 分析请求，生成 TaskPlan
3. TaskExecutor 按 DAG 顺序执行子任务
   - 无依赖的任务并行执行
   - 有依赖的任务串行执行
4. ResultAggregator 汇总结果
5. 返回最终回复给用户
```

---

## 四、讨论要点

### 4.1 待确认问题

1. **任务粒度**：如何判断何时需要分解任务？阈值是什么？
2. **并行限制**：最多允许多少个子任务并行执行？
3. **错误策略**：子任务失败时，是继续执行还是全部回滚？
4. **进度展示**：如何向用户展示多任务进度？
5. **资源控制**：如何防止任务编排消耗过多 token？

### 4.2 备选方案

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

## 五、下一步

- [ ] 确认优先级和方向
- [ ] 细化技术方案
- [ ] 开始 Phase 1 实现

---

*文档维护者：xbot team*