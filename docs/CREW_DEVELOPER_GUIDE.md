# Crew 模块开发文档

## 架构概览

```
┌─────────────────────────────────────────────────────────────────────┐
│                    CrewOrchestrator                                 │
│  (入口：组装所有组件，执行工作流)                                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐  ┌─────────────────┐  ┌───────────────────────┐   │
│  │ AgentPool   │  │ CrewStateManager │  │ CrewExecutionContext │   │
│  │ (Agent实例池)│  │ (二层状态机)     │  │ (上下文/检查点)      │   │
│  └─────────────┘  └─────────────────┘  └───────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Process (执行策略)                        │   │
│  │  ├── SequentialProcess (顺序执行)                           │   │
│  │  └── HierarchicalProcess (层级执行)                         │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Templates (模板系统)                      │   │
│  │  ├── code-review / doc-generator / data-pipeline           │   │
│  │  └── bug-hunter / test-writer                               │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## 核心模块

### 1. CrewOrchestrator (`orchestrator.py`)

主入口类，负责组装和执行 Crew。

```python
class CrewOrchestrator:
    def __init__(
        self,
        crew_config: CrewConfig,
        xbot_config: Config,
        permission_handler: BasePermissionHandler,
        config_path: str = "",
        on_progress: Callable | None = None,
    ): ...

    async def run(self, checkpoint_path: Path | None = None) -> CrewResult:
        """执行 Crew，可选择从检查点恢复。"""
```

### 2. CrewStateManager (`state.py`)

二层状态机管理器，协调 Crew 和 Task 的状态。

```python
class CrewStateManager:
    def __init__(
        self,
        task_names: list[str],
        task_definitions: list[TaskDefinition],
        strict_invariants: bool = False,
    ): ...

    def transition_crew(self, phase: CrewPhase, reason: str = "") -> None: ...
    def transition_task(self, task_name: str, phase: TaskPhase, reason: str = "") -> None: ...
    def _sync_crew_phase(self) -> None: ...
    def _check_invariants(self) -> None: ...
```

### 3. AgentPool (`agent_pool.py`)

管理 Agent 实例池，为每个角色创建独立的 SDK Client。

```python
class AgentPool:
    async def initialize(self, only_roles: set[str] | None = None) -> None: ...
    async def get_agent(self, role: str) -> ClaudeSDKBackend: ...
    async def shutdown(self) -> None: ...
```

### 4. SequentialProcess (`process.py`)

顺序执行策略实现。

```python
class SequentialProcess:
    async def execute(self, tasks: list[TaskDefinition]) -> list[TaskResult]: ...
    async def _do_human_review(self, task: TaskDefinition, result: TaskResult) -> TaskResult: ...
```

### 5. Templates (`templates.py`)

模板加载和项目管理。

```python
def list_templates() -> list[CrewTemplate]: ...
def get_template(name: str) -> CrewTemplate | None: ...
def init_project(project_dir: Path, template_name: str | None = None) -> Path: ...
```

---

## 二层状态机设计

### 状态定义

#### CrewPhase

```python
class CrewPhase(str, Enum):
    CREATED = "created"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETING = "completing"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTING = "aborting"
    ABORTED = "aborted"
```

#### TaskPhase

```python
class TaskPhase(str, Enum):
    PENDING = "pending"
    BLOCKED = "blocked"
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    REJECTED = "rejected"
```

### 状态转换图

#### Crew Phase 转换

```
CREATED ──► INITIALIZING ──► RUNNING ◄──► PAUSED
                                 │           │
                                 ▼           │
                           COMPLETING        │
                                 │           │
                                 ▼           │
                           COMPLETED         │
                                 ▲           │
                                 │           │
                            FAILED ◄─────────┘
                                 ▲
                                 │
                            ABORTING ──► ABORTED
```

#### Task Phase 转换

```
PENDING ──► BLOCKED ──► QUEUED ──► RUNNING ──► COMPLETED
   │           │                      │
   │           │                      ▼
   │           │              AWAITING_REVIEW
   │           │                    │
   │           │         ┌──────────┼──────────┐
   │           │         ▼          ▼          ▼
   │           │    RETRYING   REJECTED    (CONTINUE)
   │           │         │          │
   │           │         │          ▼
   │           │         │      SKIPPED
   │           │         │
   └───────────┴─────────┴──────► FAILED
                                        ▲
                                        │
                                   SKIPPED ◄─── (upstream failed)
```

### 转换规则

```python
CREW_VALID_TRANSITIONS: dict[CrewPhase, set[CrewPhase]] = {
    CrewPhase.CREATED: {CrewPhase.INITIALIZING, CrewPhase.FAILED},
    CrewPhase.INITIALIZING: {CrewPhase.RUNNING, CrewPhase.FAILED},
    CrewPhase.RUNNING: {CrewPhase.PAUSED, CrewPhase.COMPLETING, CrewPhase.FAILED, CrewPhase.ABORTING},
    CrewPhase.PAUSED: {CrewPhase.RUNNING, CrewPhase.COMPLETING, CrewPhase.FAILED, CrewPhase.ABORTING},
    CrewPhase.COMPLETING: {CrewPhase.COMPLETED, CrewPhase.FAILED},
    CrewPhase.ABORTING: {CrewPhase.ABORTED, CrewPhase.FAILED},
    CrewPhase.COMPLETED: set(),
    CrewPhase.FAILED: set(),
    CrewPhase.ABORTED: set(),
}

TASK_VALID_TRANSITIONS: dict[TaskPhase, set[TaskPhase]] = {
    TaskPhase.PENDING: {TaskPhase.BLOCKED, TaskPhase.QUEUED, TaskPhase.SKIPPED},
    TaskPhase.BLOCKED: {TaskPhase.QUEUED, TaskPhase.SKIPPED},
    TaskPhase.QUEUED: {TaskPhase.RUNNING, TaskPhase.SKIPPED},
    TaskPhase.RUNNING: {TaskPhase.COMPLETED, TaskPhase.AWAITING_REVIEW, TaskPhase.FAILED},
    TaskPhase.AWAITING_REVIEW: {TaskPhase.COMPLETED, TaskPhase.REJECTED, TaskPhase.RETRYING, TaskPhase.SKIPPED, TaskPhase.FAILED},
    TaskPhase.RETRYING: {TaskPhase.RUNNING},
    TaskPhase.REJECTED: {TaskPhase.SKIPPED},
    TaskPhase.COMPLETED: set(),
    TaskPhase.FAILED: set(),
    TaskPhase.SKIPPED: set(),
}
```

### 状态不变量 (Invariants)

| ID | 不变量 | 级别 | 说明 |
|----|--------|------|------|
| I1 | Crew RUNNING → 至少一个活跃任务 | Warning | RUNNING 状态必须有执行中的任务 |
| I2 | Crew PAUSED → 有 AWAITING_REVIEW，无 RUNNING | Warning | PAUSED 状态语义正确 |
| I3 | Task RUNNING/QUEUED → 所有依赖 COMPLETED | Critical | 保证数据完整性 |
| I4 | Crew COMPLETING → 所有任务终态 | Warning | 状态一致性 |
| I5 | Crew ABORTING → 无 RUNNING 任务 | Warning | 安全中止 |

### 自动同步机制

Task 状态变化时，自动推导 Crew 状态：

```python
def _sync_crew_phase(self) -> None:
    """Task 状态变化时自动同步 Crew 状态。"""
    # 不覆盖终态
    if crew_phase in {ABORTING, ABORTED, FAILED}:
        return

    task_phases = get_all_task_phases()

    # 优先级1: 有活跃任务 -> RUNNING
    if any(p in {RUNNING, QUEUED, RETRYING} for p in task_phases):
        crew -> RUNNING

    # 优先级2: 有任务等待 Review -> PAUSED
    elif AWAITING_REVIEW in task_phases:
        crew -> PAUSED

    # 优先级3: 所有任务终态 -> COMPLETING
    elif all(p in {COMPLETED, SKIPPED, FAILED, REJECTED} for p in task_phases):
        crew -> COMPLETING
```

---

## 扩展开发

### 添加新模板

1. 创建模板目录：

```bash
mkdir -p xbot/agent/crew/templates/my-template
```

2. 创建配置文件 `crew_config.yaml`：

```yaml
name: my_template_crew
description: 模板描述
process: sequential
workspace: .

agents:
  worker:
    description: 工作者
    goal: 完成任务
    max_iterations: 30

tasks:
  - name: main_task
    description: 主任务
    agent: worker
    timeout: 300
```

3. 创建说明文件 `README.md`

4. 在 `templates.py` 中注册：

```python
BUILTIN_TEMPLATES: dict[str, str] = {
    # ...
    "my-template": "我的自定义模板描述",
}
```

### 添加新的 Process 类型

1. 继承 `BaseProcess`：

```python
class MyCustomProcess(BaseProcess):
    async def execute(self, tasks: list[TaskDefinition]) -> list[TaskResult]:
        # 实现自定义执行逻辑
        pass
```

2. 在 `orchestrator.py` 中注册：

```python
process_cls = {
    ProcessType.sequential: SequentialProcess,
    ProcessType.hierarchical: HierarchicalProcess,
    ProcessType.my_custom: MyCustomProcess,
}[self.crew_config.process]
```

### 添加新的 Task Phase

1. 在 `state.py` 中添加枚举值：

```python
class TaskPhase(str, Enum):
    # ...
    MY_NEW_PHASE = "my_new_phase"
```

2. 更新转换规则：

```python
TASK_VALID_TRANSITIONS: dict[TaskPhase, set[TaskPhase]] = {
    # ...
    TaskPhase.SOME_PHASE: {..., TaskPhase.MY_NEW_PHASE},
    TaskPhase.MY_NEW_PHASE: {...},
}
```

3. 更新同步逻辑（如需要）

---

## 测试

### 运行测试

```bash
# 运行所有 Crew 测试
pytest tests/agent/crew/ -v

# 运行特定测试
pytest tests/agent/crew/test_state.py -v
pytest tests/agent/crew/test_templates.py -v
```

### 测试文件结构

```
tests/agent/crew/
├── test_state.py      # 状态机测试 (95 个)
├── test_models.py     # 数据模型测试 (31 个)
├── test_context.py    # 上下文测试 (15 个)
├── test_process.py    # 执行流程测试 (16 个)
└── test_templates.py  # 模板测试 (35 个)
```

---

## 调试

### 启用详细日志

```bash
xbot crew run crew_config.yaml -v
```

### 检查状态

```python
# 在代码中
state_manager = CrewStateManager(...)
print(state_manager.crew_phase)
print(state_manager.get_task_phase("task_name"))
print(state_manager.get_all_task_phases())
```

### 检查点分析

```python
from xbot.agent.crew.context import load_checkpoint

checkpoint = load_checkpoint("checkpoint.json")
print(checkpoint["crew_phase"])
print([t["name"] for t in checkpoint["completed_tasks"]])
```