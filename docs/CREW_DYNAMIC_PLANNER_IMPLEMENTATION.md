# Crew 动态规划模块 - 分阶段实现计划

## 现有代码结构分析

```
xbot/agent/crew/
├── __init__.py              # 公开 API: AgentRole, CrewConfig, CrewOrchestrator 等
├── models.py                # 核心数据模型 (Pydantic)
├── orchestrator.py          # 执行引擎
├── agent_pool.py            # Agent 池管理
├── context.py               # 执行上下文
├── state.py                 # 状态管理
├── process.py               # 执行流程
├── templates.py             # 模板管理
├── config/                  # 配置加载
│   ├── loader.py
│   ├── merger.py
│   ├── variables.py
│   └── validator.py
└── output/                  # 输出处理
    ├── format.py
    ├── persist.py
    ├── repair.py
    └── truncate.py

xbot/cli/commands.py         # CLI 命令 (typer)
└── crew_app                 # crew 子命令组
    ├── run
    ├── show
    ├── init
    ├── templates
    ├── validate
    ├── checkpoints
    ├── resume
    ├── history
    ├── graph
    └── export
```

---

## Phase 1: 角色池基础 (P0)

### 1.1 目标

建立角色池管理基础设施，支持预定义角色的加载和查询。

### 1.2 新增文件

```
xbot/agent/crew/
├── planner/                     # 新增目录
│   ├── __init__.py
│   ├── models.py                # 新增: RoleDefinition, RolePool, Capability 等
│   └── role_pool.py             # 新增: RolePoolManager
│
└── role_pool/                   # 新增目录
    ├── core/                    # 核心角色
    │   ├── researcher.yaml
    │   ├── coder.yaml
    │   ├── reviewer.yaml
    │   └── tester.yaml
    └── extended/                # 扩展角色
        └── doc_writer.yaml
```

### 1.3 修改文件

| 文件 | 修改内容 | 侵入性 |
|------|---------|--------|
| `xbot/agent/crew/__init__.py` | 添加 planner 模块的导出 | 🟢 无侵入 |
| 无 | 本阶段不修改任何现有文件 | |

### 1.4 实现内容

```python
# planner/models.py
class Capability(str, Enum): ...
class RoleTier(str, Enum): ...
class RoleDefinition: ...  # 与现有 AgentRole 的关系: 可转换为 AgentRole
class RolePoolConfig: ...
class RolePool: ...

# planner/role_pool.py
class RolePoolManager:
    def load(self) -> None: ...
    def get_pool(self) -> RolePool: ...
```

### 1.5 与现有代码的关系

```
新模块: RoleDefinition ──(转换)──> 现有: AgentRole
                              │
                              └─ role.to_agent_role()
```

### 1.6 侵入性评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 代码修改 | 🟢 无 | 纯新增，不修改现有代码 |
| 接口变更 | 🟢 无 | 不影响现有 API |
| 测试影响 | 🟢 无 | 新模块有独立测试 |
| 部署影响 | 🟢 无 | 可独立部署 |

**结论**: 🟢 **零侵入** - 完全独立的新模块

---

## Phase 2: 角色管理 CLI (P0.5)

### 2.1 目标

提供独立的角色管理命令，支持查看、创建、验证角色。

### 2.2 新增文件

```
xbot/agent/crew/
├── planner/
│   ├── role_creator.py          # 新增: 角色创建器
│   └── prompts.py               # 新增: LLM Prompt
│
└── cli/                         # 新增目录
    ├── __init__.py
    └── role_cmd.py              # 新增: 角色管理 CLI 命令
```

### 2.3 修改文件

| 文件 | 修改内容 | 侵入性 |
|------|---------|--------|
| `xbot/cli/commands.py` | 添加 roles 子命令组 | 🟡 低侵入 |

### 2.4 修改详情

```python
# xbot/cli/commands.py
# 在文件末尾添加 (~10 行)

# === 新增 ===
roles_app = typer.Typer(help="Role pool management")
crew_app.add_typer(roles_app, name="roles")

# 导入命令
from xbot.agent.crew.cli.role_cmd import (
    roles_list, roles_show, roles_create, roles_validate
)

roles_app.command("list")(roles_list)
roles_app.command("show")(roles_show)
roles_app.command("create")(roles_create)
roles_app.command("validate")(roles_validate)
```

### 2.5 实现内容

```python
# cli/role_cmd.py
def roles_list(...): ...      # xbot crew roles list
def roles_show(...): ...      # xbot crew roles show <name>
def roles_create(...): ...    # xbot crew roles create [交互式]
def roles_validate(...): ...  # xbot crew roles validate <file>

# planner/role_creator.py
class RoleCreator:
    async def create_role(...) -> RoleCreationResult: ...
    def _validate_role(...) -> list[str]: ...
    def _save_role(...) -> Path: ...
```

### 2.6 侵入性评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 代码修改 | 🟡 低 | 仅在 commands.py 末尾添加 ~10 行 |
| 接口变更 | 🟢 无 | 新增命令，不影响现有命令 |
| 测试影响 | 🟢 无 | 新命令有独立测试 |
| 部署影响 | 🟢 无 | 可独立部署 |

**结论**: 🟡 **低侵入** - 仅在 CLI 入口添加注册代码

---

## Phase 3: 角色选择与任务规划 (P0)

### 3.1 目标

实现动态规划核心功能：目标分析 → 角色选择 → 任务规划 → 配置生成。

### 3.2 新增文件

```
xbot/agent/crew/planner/
├── role_selector.py             # 新增: 角色选择器
├── task_planner.py              # 新增: 任务规划器
├── config_generator.py          # 新增: 配置生成器
└── crew_planner.py              # 新增: 主入口
```

### 3.3 修改文件

| 文件 | 修改内容 | 侵入性 |
|------|---------|--------|
| `xbot/cli/commands.py` | 添加 plan 子命令 | 🟡 低侵入 |

### 3.4 修改详情

```python
# xbot/cli/commands.py
# 在 crew_app 下添加 plan 命令 (~30 行)

@crew_app.command("plan")
def crew_plan(
    goal: str = typer.Argument(..., help="Goal description"),
    workspace: str = typer.Option(".", "--workspace", "-w"),
    output: str = typer.Option(None, "--output", "-o"),
    tier: str = typer.Option("core", "--tier"),
    allow_create_roles: bool = typer.Option(False, "--allow-create-roles"),
    preview: bool = typer.Option(False, "--preview"),
):
    """Plan a crew dynamically based on goal."""
    # ... 调用 CrewPlanner
```

### 3.5 实现内容

```python
# planner/crew_planner.py
class CrewPlanner:
    async def plan(self, goal: str, context: dict) -> CrewPlan: ...
    async def generate_config(self, plan: CrewPlan) -> str: ...
    async def plan_and_generate(self, goal: str) -> tuple[CrewPlan, str]: ...

# planner/role_selector.py
class RoleSelector:
    async def select(self, analysis: GoalAnalysis, role_pool: RolePool) -> RoleSelection: ...

# planner/task_planner.py
class TaskPlanner:
    async def plan(self, goal: str, analysis: GoalAnalysis, role_selection: RoleSelection) -> list[TaskPlan]: ...

# planner/config_generator.py
class ConfigGenerator:
    def generate_yaml(self, plan: CrewPlan) -> str: ...
```

### 3.6 与现有代码的集成点

```
CrewPlanner
    │
    ├── 输出: CrewPlan
    │       │
    │       └── roles: list[RoleDefinition]
    │                │
    │                └── to_agent_role() ──> AgentRole (现有)
    │
    └── ConfigGenerator.generate_yaml()
            │
            └── 输出: crew_config.yaml
                        │
                        └── load_crew_config() ──> CrewConfig (现有)
                                                    │
                                                    └── CrewOrchestrator.run() (现有)
```

### 3.7 侵入性评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 代码修改 | 🟡 低 | 仅在 commands.py 添加 plan 命令 |
| 接口变更 | 🟢 无 | 新增命令，复用现有加载流程 |
| 测试影响 | 🟢 无 | 新模块有独立测试 |
| 部署影响 | 🟢 无 | 可独立部署 |

**结论**: 🟡 **低侵入** - 仅添加新命令，复用现有执行流程

---

## Phase 4: 动态执行集成 (P1)

### 4.1 目标

实现 `xbot crew run-dynamic` 命令，直接执行动态规划的配置。

### 4.2 新增文件

```
xbot/agent/crew/cli/
└── plan_cmd.py                  # 新增: 规划相关命令
```

### 4.3 修改文件

| 文件 | 修改内容 | 侵入性 |
|------|---------|--------|
| `xbot/cli/commands.py` | 添加 run-dynamic 命令 | 🟡 低侵入 |

### 4.4 修改详情

```python
# xbot/cli/commands.py
# 在 crew_app 下添加 run-dynamic 命令 (~50 行)

@crew_app.command("run-dynamic")
def crew_run_dynamic(
    goal: str = typer.Argument(..., help="Goal description"),
    workspace: str = typer.Option(".", "--workspace", "-w"),
    tier: str = typer.Option("core", "--tier"),
    allow_create_roles: bool = typer.Option(False, "--allow-create-roles"),
    save_config: bool = typer.Option(False, "--save-config"),
):
    """Plan and run a crew dynamically based on goal."""
    # 1. 调用 CrewPlanner.plan()
    # 2. 生成临时配置文件
    # 3. 调用现有 CrewOrchestrator.run()
```

### 4.5 执行流程

```
用户输入: xbot crew run-dynamic "分析代码质量"

Step 1: CrewPlanner.plan(goal)
        ├── GoalAnalysis
        ├── RoleSelection
        └── TaskPlan[]

Step 2: ConfigGenerator.generate_yaml(plan)
        └── 临时文件: /tmp/dynamic_crew_xxx.yaml

Step 3: load_crew_config(temp_file)  ← 现有函数
        └── CrewConfig

Step 4: CrewOrchestrator.run(config)  ← 现有执行器
        └── CrewResult
```

### 4.6 侵入性评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 代码修改 | 🟡 低 | 仅添加新命令 |
| 接口变更 | 🟢 无 | 完全复用现有执行流程 |
| 测试影响 | 🟢 无 | 新命令有独立测试 |
| 部署影响 | 🟢 无 | 可独立部署 |

**结论**: 🟡 **低侵入** - 复用现有执行引擎

---

## Phase 5: 角色池扩展与优化 (P2)

### 5.1 目标

完善角色池功能：更多预定义角色、角色覆盖、全局角色目录。

### 5.2 新增文件

```
xbot/agent/crew/role_pool/
├── specialist/                  # 新增目录
│   ├── security_auditor.yaml
│   └── ml_engineer.yaml
└── pool.yaml                    # 新增: 角色池配置

~/.xbot/roles/                   # 新增: 用户全局角色目录
```

### 5.3 修改文件

| 文件 | 修改内容 | 侵入性 |
|------|---------|--------|
| 无 | 本阶段纯新增 | 🟢 无侵入 |

### 5.4 实现内容

```python
# RolePoolManager 扩展
class RolePoolManager:
    def load(self) -> None:
        # 增加全局目录加载
        self._load_from_dir(Path.home() / ".xbot" / "roles", RoleTier.EXTENDED)

    def _apply_overrides(self) -> None:
        # 支持角色配置覆盖
        ...
```

### 5.5 侵入性评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 代码修改 | 🟢 无 | 仅扩展现有模块 |
| 接口变更 | 🟢 无 | 向后兼容 |
| 测试影响 | 🟢 无 | 新增测试 |
| 部署影响 | 🟢 无 | 可独立部署 |

**结论**: 🟢 **零侵入** - 仅扩展新模块

---

## 总体侵入性评估

### 各阶段汇总

| Phase | 功能 | 侵入性 | 修改文件 |
|-------|------|--------|---------|
| 1 | 角色池基础 | 🟢 零侵入 | 无 |
| 2 | 角色管理 CLI | 🟡 低侵入 | commands.py (~10行) |
| 3 | 角色选择与任务规划 | 🟡 低侵入 | commands.py (~30行) |
| 4 | 动态执行集成 | 🟡 低侵入 | commands.py (~50行) |
| 5 | 角色池扩展 | 🟢 零侵入 | 无 |

### 风险分析

| 风险 | 级别 | 缓解措施 |
|------|------|---------|
| CLI 命令冲突 | 低 | 使用独立子命令组 `crew roles` |
| 数据模型不一致 | 低 | 提供 `to_agent_role()` 转换方法 |
| 执行流程变更 | 无 | 完全复用现有 Orchestrator |

### 架构关系图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           新模块 (planner)                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │ RolePool     │  │ RoleCreator  │  │ CrewPlanner  │  │ ConfigGen  │  │
│  │ Manager      │  │              │  │              │  │            │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘  │
│         │                 │                 │                 │         │
│         │                 │                 │                 │         │
└─────────┼─────────────────┼─────────────────┼─────────────────┼─────────┘
          │                 │                 │                 │
          │                 │                 │                 │
          ▼                 ▼                 ▼                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          现有模块 (不修改)                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌──────────────┐                                           ┌─────────┐│
│  │ AgentRole    │ <─── to_agent_role() ─── RoleDefinition  │ CLI     ││
│  │ (Pydantic)   │                                           │ commands││
│  └──────┬───────┘                                           └────┬────┘│
│         │                                                        │     │
│         ▼                                                        │     │
│  ┌──────────────┐      ┌──────────────┐                          │     │
│  │ CrewConfig   │ <─── │ load_crew_   │ <─── YAML 文件 ──────────┘     │
│  │ (Pydantic)   │      │ config()     │                                │
│  └──────┬───────┘      └──────────────┘                                │
│         │                                                              │
│         ▼                                                              │
│  ┌──────────────┐                                                      │
│  │ CrewOrchestr│  ──> 执行 crew                                        │
│  │ ator        │                                                      │
│  └──────────────┘                                                      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

图例:
  ───> 调用/依赖关系
  ...> 转换关系
```

---

## 实施建议

### 开发顺序

```
Week 1: Phase 1 (角色池基础)
├── 数据模型定义
├── RolePoolManager 实现
├── 核心 YAML 角色定义
└── 单元测试

Week 2: Phase 2 (角色管理 CLI)
├── RoleCreator 实现
├── CLI 命令实现
├── 交互式创建向导
└── 单元测试

Week 3: Phase 3 (核心规划)
├── RoleSelector 实现
├── TaskPlanner 实现
├── CrewPlanner 实现
├── ConfigGenerator 实现
└── 单元测试

Week 4: Phase 4 (动态执行)
├── run-dynamic 命令
├── 集成测试
└── 文档更新

Week 5+: Phase 5 (扩展优化)
├── 更多预定义角色
├── 全局角色目录
└── 性能优化
```

### 测试策略

```
单元测试:
├── tests/agent/crew/planner/
│   ├── test_models.py          # 数据模型测试
│   ├── test_role_pool.py       # 角色池测试
│   ├── test_role_creator.py    # 角色创建测试
│   ├── test_role_selector.py   # 角色选择测试
│   ├── test_task_planner.py    # 任务规划测试
│   ├── test_config_generator.py # 配置生成测试
│   └── test_crew_planner.py    # 集成测试

集成测试:
├── tests/agent/crew/test_dynamic_planning.py
└── tests/cli/test_crew_commands.py
```

### 回滚策略

每个 Phase 都可以独立回滚:

| Phase | 回滚方式 |
|-------|---------|
| 1 | 删除 `planner/` 和 `role_pool/` 目录 |
| 2 | 删除 `cli/role_cmd.py`，移除 commands.py 中的注册代码 |
| 3 | 删除 `planner/` 中的规划模块，移除 commands.py 中的 plan 命令 |
| 4 | 移除 commands.py 中的 run-dynamic 命令 |
| 5 | 删除 `role_pool/specialist/` 目录 |

---

## 总结

本设计方案采用 **插件化架构**，新功能作为独立模块实现，与现有代码通过明确的接口集成:

1. **零侵入**: Phase 1 和 Phase 5 完全不修改现有代码
2. **低侵入**: Phase 2-4 仅在 CLI 入口添加注册代码
3. **可回滚**: 每个 Phase 都可独立回滚
4. **可测试**: 新模块有独立的单元测试和集成测试
5. **可扩展**: 架构支持未来添加更多角色和规划能力

**推荐实施顺序**: Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5