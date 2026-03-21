# 多 Agent 协作框架调研报告

> 创建时间：2026-03-21
> 状态：调研分析

## 一、框架概览

| 框架 | Stars | 定位 | 核心特点 |
|------|-------|------|----------|
| **OpenAI Swarm** | 21.2k | 教育性框架 | 轻量级、手写编排 |
| **ClawTeam** | 2.2k | Agent 自编排 | Agent 自己 spawn Agent |
| **Swarms** | 5.9k | 企业级框架 | 多种编排架构 |
| **CrewAI** | 30k+ | 角色扮演协作 | Crew + Flows 双模式 |
| **Claude-Flow** | - | Claude Code 专用 | 60+ 预置 Agent |

---

## 二、详细分析

### 2.1 OpenAI Swarm（OpenAI 官方）

**仓库**: https://github.com/openai/swarm

#### 设计理念

轻量级、教育性、手工编排

#### 架构

```
┌─────────────────────────────────────────┐
│              用户代码                    │
│   (Python 代码手写编排逻辑)              │
└─────────────────┬───────────────────────┘
                  │
    ┌─────────────┼─────────────┐
    ▼             ▼             ▼
┌───────┐   ┌───────┐   ┌───────┐
│Agent A│   │Agent B│   │Agent C│
│       │   │       │   │       │
│handoff│──►│handoff│──►│result │
└───────┘   └───────┘   └───────┘
```

#### 核心概念

```python
from swarm import Agent

def transfer_to_agent_b():
    return agent_b

agent_a = Agent(
    name="Agent A",
    instructions="You are a helpful agent.",
    functions=[transfer_to_agent_b],
)

agent_b = Agent(
    name="Agent B",
    instructions="You are another helpful agent.",
)
```

#### 特点

| 特点 | 说明 |
|------|------|
| Agent 切换 | 通过 `handoff` 函数切换 |
| 无状态 | 每次调用独立 |
| 代码驱动 | 需要写编排代码 |
| 轻量 | 核心代码很少 |

#### 优点与缺点

| 优点 | 缺点 |
|------|------|
| 简单、轻量、可控 | 需要手写编排逻辑 |
| 学习门槛低 | 不够智能 |
| 官方出品 | 无持久化状态 |

---

### 2.2 ClawTeam（HKUDS）

**仓库**: https://github.com/HKUDS/ClawTeam

#### 设计理念

Agent 自编排：Agent 自己 spawn Agent，而不是人类写编排代码

#### 架构

```
┌─────────────────────────────────────────┐
│           Leader Agent                   │
│         (Claude Code)                    │
│                                         │
│   自己决定调用 CLI:                      │
│   clawteam spawn --task "..."           │
│   clawteam task update ...              │
└─────────────────┬───────────────────────┘
                  │
    ┌─────────────┼─────────────┐
    ▼             ▼             ▼
┌───────┐   ┌───────┐   ┌───────┐
│Worker1│   │Worker2│   │Worker3│
│       │   │       │   │       │
│worktree│  │worktree│  │worktree│
│tmux   │   │tmux   │   │tmux   │
└───────┘   └───────┘   └───────┘
```

#### 核心 CLI 命令

```bash
# 创建团队
clawteam team spawn-team my-team -d "Build auth module"

# Spawn worker（自动创建 worktree + tmux）
clawteam spawn --team my-team --agent-name alice --task "Implement OAuth2"

# 任务管理
clawteam task list my-team
clawteam task update my-team <task-id> --status completed

# Agent 间通信
clawteam inbox send my-team leader "Auth done"
clawteam inbox receive my-team

# 可视化
clawteam board attach my-team  # tmux 分屏查看
```

#### 关键设计

| 设计 | 说明 |
|------|------|
| **Git Worktree** | 每个 worker 有独立的 git worktree，真正隔离 |
| **任务依赖** | 支持 `--blocked-by` 实现任务依赖链 |
| **Inbox 通信** | Agent 通过 CLI 收发消息 |
| **tmux 可视化** | 并行展示所有 agent，人类可观察 |
| **TOML 模板** | 预定义团队配置，一键启动 |

#### 优点与缺点

| 优点 | 缺点 |
|------|------|
| Agent 自己决定如何编排 | 依赖 Claude Code CLI |
| Git worktree 真正隔离 | 需要学习 CLI 命令 |
| 任务依赖链支持 | - |
| 可视化直观 | - |

#### 使用场景示例

```
用户: "分析项目，生成报告，发到飞书"

Leader Agent 自动执行:
├── 🏗️ clawteam team spawn-team analysis
├── 📋 创建任务:
│   ├── T1: "扫描项目结构" → worker1
│   ├── T2: "分析代码质量" → worker2
│   ├── T3: "生成报告" --blocked-by T1,T2 → worker3
│   └── T4: "发送飞书" --blocked-by T3 → worker4
├── 🚀 Spawn 4 个 worker，各自独立 worktree
├── 🔄 自动等待依赖完成
└── ✅ 结果聚合，通知用户
```

---

### 2.3 Swarms（企业级）

**仓库**: https://github.com/kyegomez/swarms

#### 设计理念

多种编排架构，开箱即用，企业级

#### 架构选择

```
┌─────────────────────────────────────────────────────────┐
│                     Swarms 架构                          │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │Sequential    │  │Concurrent    │  │GraphWorkflow │  │
│  │Workflow      │  │Workflow      │  │(DAG)         │  │
│  │              │  │              │  │              │  │
│  │ A → B → C   │  │ A B C 并行   │  │ 复杂依赖图   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │AgentRearrange│  │MixtureOfAgent│  │Hierarchical  │  │
│  │动态路由      │  │MoA 集成      │  │Swarm 层级   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
```

#### 编排架构对比

| 架构 | 说明 | 适用场景 |
|------|------|----------|
| SequentialWorkflow | 串行执行，A → B → C | 数据处理管道、报告生成 |
| ConcurrentWorkflow | 并行执行，A B C 同时运行 | 批量处理、并行分析 |
| GraphWorkflow | DAG 图，复杂依赖 | 软件构建、复杂流程 |
| AgentRearrange | 动态路由 `a -> b, c` | 灵活工作流 |
| MixtureOfAgents | MoA 架构，多模型集成 | 需要多模型协作 |
| HierarchicalSwarm | 层级架构，Queen-Worker | 大规模任务分解 |

#### AutoSwarmBuilder

```python
from swarms.structs.auto_swarm_builder import AutoSwarmBuilder

swarm = AutoSwarmBuilder(
    name="Accounting Team",
    description="Analyze crypto transactions",
    model_name="gpt-4",
)

# 自动生成 5 个专业 Agent 及其工作流
result = swarm.run(
    task="Create an accounting team with 5 agents"
)
```

#### 优点与缺点

| 优点 | 缺点 |
|------|------|
| 架构丰富，选择多 | 较重，学习曲线陡 |
| AutoSwarmBuilder 自动生成 | 企业级特性需要付费 |
| MCP 集成 | - |
| 生产就绪 | - |

---

### 2.4 CrewAI（角色扮演）

**仓库**: https://github.com/crewAIInc/crewAI

#### 设计理念

Crew（团队）+ Flows（流程）双模式，角色扮演概念

#### 架构

```
┌─────────────────────────────────────────────────────────┐
│                       CrewAI                             │
│                                                         │
│   ┌─────────────────────────────────────────────────┐   │
│   │                   Crew                           │   │
│   │   (角色扮演的 Agent 团队)                        │   │
│   │                                                 │   │
│   │   ┌─────────┐  ┌─────────┐  ┌─────────┐       │   │
│   │   │Researcher│  │ Writer  │  │ Editor  │       │   │
│   │   │角色：研究 │  │角色：写作│  │角色：编辑│       │   │
│   │   │目标：...  │  │目标：... │  │目标：... │       │   │
│   │   │背景：...  │  │背景：... │  │背景：... │       │   │
│   │   └────┬────┘  └────┬────┘  └────┬────┘       │   │
│   │        │            │            │              │   │
│   │        └────────────┼────────────┘              │   │
│   │                     │                           │   │
│   │              自然协作/委托                       │   │
│   └─────────────────────────────────────────────────┘   │
│                                                         │
│   ┌─────────────────────────────────────────────────┐   │
│   │                   Flow                           │   │
│   │   (事件驱动的生产流程)                           │   │
│   │                                                 │   │
│   │   Task1 ──► Task2 ──► Task3 ──► Task4          │   │
│   │            条件分支、状态管理                    │   │
│   └─────────────────────────────────────────────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

#### Agent 定义（YAML）

```yaml
# agents.yaml
researcher:
  role: >
    Senior Research Analyst
  goal: >
    Uncover cutting-edge developments in AI and data science
  backstory: >
    You're a seasoned researcher with a knack for uncovering the latest
    developments in AI and data science. Known for your ability to find the most relevant
    information and present it in a clear and concise manner.
  verbose: true

writer:
  role: >
    Tech Content Strategist
  goal: >
    Create compelling content about AI and data science
  backstory: >
    You are a famous content strategist, known for your ability to
    create compelling content about AI and data science.
```

#### Crew 定义（Python）

```python
from crewai import Agent, Task, Crew

researcher = Agent(
    role="Senior Research Analyst",
    goal="Uncover cutting-edge developments",
    backstory="You're a seasoned researcher...",
)

writer = Agent(
    role="Tech Content Strategist",
    goal="Create compelling content",
    backstory="You are a famous content strategist...",
)

crew = Crew(
    agents=[researcher, writer],
    tasks=[research_task, write_task],
    verbose=True,
)

result = crew.kickoff()
```

#### 优点与缺点

| 优点 | 缺点 |
|------|------|
| 角色扮演概念直观 | 主要是代码编排，不是 Agent 自编排 |
| YAML 配置简单 | 不支持 Agent 自己 spawn Agent |
| Crews + Flows 双模式 | - |
| 社区大（10万+ 认证开发者） | - |
| 不依赖 LangChain | - |

---

### 2.5 Claude-Flow

**仓库**: https://github.com/Backgetters/claude-flow

#### 设计理念

Claude Code 专用，60+ 预置 Agent，自学习

#### 架构

```
┌─────────────────────────────────────────────────────────┐
│                    Claude-Flow                          │
│                                                         │
│   60+ 预置 Agent:                                       │
│   ├── Coder, Architect, Tester                         │
│   ├── Security, DevOps, Documentation                   │
│   └── ...                                               │
│                                                         │
│   Queen-Worker 层级：                                   │
│   ┌─────────────────────────────────────────────────┐   │
│   │                 Queen                            │   │
│   │              (协调者)                            │   │
│   └────────────────────┬────────────────────────────┘   │
│                        │                                │
│         ┌──────────────┼──────────────┐                │
│         ▼              ▼              ▼                │
│    ┌─────────┐   ┌─────────┐   ┌─────────┐            │
│    │ Worker1 │   │ Worker2 │   │ Worker3 │            │
│    └─────────┘   └─────────┘   └─────────┘            │
│                                                        │
└─────────────────────────────────────────────────────────┘
```

#### 核心特性

| 特性 | 说明 |
|------|------|
| **自学习 (SONA)** | 从工作流中学习，自动优化 |
| **共识算法** | Raft, Byzantine, Gossip 等 5 种 |
| **向量记忆** | HNSW 向量数据库，150x 更快检索 |
| **MCP 集成** | 175+ MCP 工具 |
| **智能路由** | 基于学习模式，89% 准确率 |

#### 快速开始

```bash
# 安装
curl -fsSL https://cdn.jsdelivr.net/gh/ruvnet/claude-flow@main/scripts/install.sh | bash

# 初始化
npx ruflo@alpha init

# 运行任务
npx ruflo@alpha --agent coder --task "Implement user authentication"

# MCP 集成
claude mcp add ruflo -- npx -y ruflo@latest mcp start
```

#### 优点与缺点

| 优点 | 缺点 |
|------|------|
| 预置 Agent 丰富（60+） | 依赖 Claude Code 生态 |
| 自学习能力 | 较重 |
| 企业级安全 | - |
| Claude Code 深度集成 | - |

---

## 三、核心模式对比

### 3.1 编排方式对比

| 模式 | 代表框架 | 谁来编排 | 灵活性 | 复杂度 |
|------|----------|----------|--------|--------|
| **代码编排** | Swarm, Swarms | 人类写代码 | 低 | 低 |
| **配置编排** | CrewAI | YAML 配置 | 中 | 中 |
| **Agent 自编排** | ClawTeam | Agent 自己 | 高 | 高 |
| **模板驱动** | Claude-Flow | 预置模板 | 中 | 低 |

### 3.2 功能对比

| 功能 | Swarm | ClawTeam | Swarms | CrewAI | Claude-Flow |
|------|-------|----------|--------|--------|-------------|
| Agent 自 spawn | ❌ | ✅ | ❌ | ❌ | ✅ |
| Git Worktree | ❌ | ✅ | ❌ | ❌ | ❌ |
| 任务依赖 | ❌ | ✅ | ✅ | ✅ | ✅ |
| Agent 间通信 | ❌ | ✅ (Inbox) | ✅ | ✅ | ✅ |
| 可视化 | ❌ | ✅ (tmux) | ❌ | ❌ | ✅ |
| 自学习 | ❌ | ❌ | ❌ | ❌ | ✅ |
| 多种编排架构 | ❌ | ❌ | ✅ (6+) | ✅ (2) | ✅ |
| 预置 Agent | ❌ | ❌ | ❌ | ❌ | ✅ (60+) |
| MCP 集成 | ❌ | ❌ | ✅ | ❌ | ✅ |

### 3.3 适用场景

| 场景 | 推荐框架 | 理由 |
|------|----------|------|
| 学习多 Agent 概念 | Swarm | 最简单，官方出品 |
| 复杂开发任务 | ClawTeam | Agent 自编排，worktree 隔离 |
| 企业级生产 | Swarms | 架构丰富，生产就绪 |
| 内容创作/研究 | CrewAI | 角色扮演直观，配置简单 |
| Claude Code 用户 | Claude-Flow | 深度集成，预置 Agent 多 |

---

## 四、对 xbot 的启发

### 4.1 可借鉴的设计

| 框架 | 可借鉴点 | 具体应用 |
|------|----------|----------|
| **ClawTeam** | Agent 自编排 | Leader Agent 自己决定 spawn 多少 worker |
| **ClawTeam** | Git worktree | 每个 worker 独立 worktree，真正隔离 |
| **ClawTeam** | Inbox 通信 | 简单的文件消息机制 |
| **ClawTeam** | tmux 可视化 | 并行展示所有 worker |
| **Swarms** | 多种编排架构 | 支持串行/并行/DAG 等模式 |
| **Swarms** | AutoSwarmBuilder | 自动生成 Agent 配置 |
| **CrewAI** | 角色定义 | Agent 有角色、目标、背景 |
| **CrewAI** | YAML 配置 | 简化 Agent 定义 |
| **Claude-Flow** | Queen-Worker 层级 | 适合大规模任务分解 |
| **Claude-Flow** | 自学习 | 从历史中学习优化 |

### 4.2 建议 xbot 的混合方案

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         xbot 混合架构                                    │
│                                                                         │
│   入口 1: Channel（简单任务）                                            │
│   └──► 同步处理，直接返回                                                │
│       保持现有架构不变                                                    │
│                                                                         │
│   入口 2: CLI（复杂任务）                                                │
│   └──► Agent 自编排（借鉴 ClawTeam）                                     │
│       │                                                                 │
│       ├── Leader Agent 自己决定 spawn 多少 worker                       │
│       │   - 借鉴 CrewAI：Worker 有角色、目标                             │
│       │   - 借鉴 Swarms：支持多种编排模式                                │
│       │                                                                 │
│       ├── Worker 隔离                                                   │
│       │   - 借鉴 ClawTeam：Git worktree 隔离                             │
│       │                                                                 │
│       ├── Worker 通信                                                   │
│       │   - 借鉴 ClawTeam：Inbox 文件消息                                │
│       │                                                                 │
│       └── 可视化                                                        │
│           - 借鉴 ClawTeam：tmux 分屏                                     │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.3 核心 CLI 命令设计

```bash
# 启动 Leader Agent（自动编排）
xbot run "<goal>"                   

# 手动 spawn worker（如果需要）
xbot worker spawn --name alice --task "Implement OAuth2"
xbot worker spawn --name bob --task "Write tests" --blocked-by alice

# Worker 管理
xbot worker list                      # 列出所有 worker
xbot worker status alice              # 查看状态
xbot worker kill alice                # 停止 worker

# Worker 通信
xbot inbox send leader "Task done"   # 发送消息
xbot inbox receive                   # 接收消息

# 可视化
xbot board attach                    # tmux 分屏查看所有 worker
```

### 4.4 关键设计选择

| 选择 | 推荐 | 理由 |
|------|------|------|
| **编排方式** | Agent 自编排 | 更智能、更灵活 |
| **Worker 隔离** | Git worktree | 真正隔离，可并行开发 |
| **Agent 定义** | 角色 + 目标 | 借鉴 CrewAI，配置简单 |
| **编排模式** | 多种可选 | 借鉴 Swarms，适应不同场景 |
| **通信方式** | Inbox 文件 | 简单可靠 |
| **可视化** | tmux 分屏 | 借鉴 ClawTeam，直观 |

### 4.5 实施路线

| 阶段 | 内容 | 借鉴来源 |
|------|------|----------|
| **Phase 1** | CLI Worker 基础（spawn/list/kill） | ClawTeam |
| **Phase 2** | Git Worktree 隔离 | ClawTeam |
| **Phase 3** | Inbox 通信机制 | ClawTeam |
| **Phase 4** | 任务依赖链（blocked-by） | ClawTeam |
| **Phase 5** | tmux 可视化 | ClawTeam |
| **Phase 6** | Agent 角色定义（YAML） | CrewAI |
| **Phase 7** | 多种编排模式 | Swarms |

---

## 五、参考链接

- OpenAI Swarm: https://github.com/openai/swarm
- ClawTeam: https://github.com/HKUDS/ClawTeam
- Swarms: https://github.com/kyegomez/swarms
- CrewAI: https://github.com/crewAIInc/crewAI
- Claude-Flow: https://github.com/Backgetters/claude-flow

---

*文档维护者：xbot team*