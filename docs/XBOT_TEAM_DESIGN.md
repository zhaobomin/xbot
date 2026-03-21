# xbot-team 设计文档

> 多 Agent 协作框架 - 无侵入式外围协作层

## 1. 概述

### 1.1 目标

为 xbot 提供多 Agent 协作能力，实现：
- 统一的 Team Leader Agent 负责任务分解和协调
- 多个 Worker Agent 并行执行任务
- 任务依赖链自动管理
- Gate 机制强制人工介入
- 实时监控窗口显示进度

### 1.2 设计原则

| 原则 | 说明 |
|------|------|
| **无侵入** | 不修改 xbot 核心代码，通过 CLI 和环境变量交互 |
| **CLI 驱动** | Agent 通过 CLI 命令创建和管理其他 Agent |
| **文件系统通信** | 使用文件系统实现消息队列和状态存储，无需 Redis |
| **Session 保持** | Gate 等待时保持 Worker Session 活跃，保留完整上下文 |

### 1.3 项目信息

- **名称**: xbot-team
- **位置**: `/home/xbot/projects/xbot-team` (独立项目)
- **依赖**: xbot (通过 CLI 调用)

---

## 2. 整体架构

### 2.1 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│                    xbot-team (外围协作层)                    │
│   ┌─────────────────────────────────────────────────────┐  │
│   │                                                     │  │
│   │   Team Leader Agent                                 │  │
│   │   ├── 任务分解 (调用 xbot LLM)                      │  │
│   │   ├── Worker 创建 (调用 xbot agent CLI)             │  │
│   │   ├── 消息路由                                      │  │
│   │   ├── Gate 协调                                     │  │
│   │   └── 结果汇总                                      │  │
│   │                                                     │  │
│   │   文件系统通信层                                     │  │
│   │   └── ~/.xteam/{team}/                              │  │
│   │       ├── tasks/          # 任务状态                 │  │
│   │       ├── inboxes/        # 消息队列                 │  │
│   │       └── outputs/        # 产出物                   │  │
│   │                                                     │  │
│   └─────────────────────────────────────────────────────┘  │
│                          │                                  │
│                          │ CLI 调用                         │
│                          ▼                                  │
│   ┌─────────────────────────────────────────────────────┐  │
│   │                                                     │  │
│   │   xbot (现有代码不改动)                              │  │
│   │   ├── xbot agent          # Worker Agent           │  │
│   │   └── 配置文件            # 复用现有 LLM 配置        │  │
│   │                                                     │  │
│   └─────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 tmux 窗口布局

```
┌─────────────────────────────────────────────────────────────┐
│  tmux session: xteam-{team_name}                            │
├──────────────────────────┬──────────────────────────────────┤
│  窗口 0: board           │  窗口 1: worker_0                │
│  (实时监控，自动刷新)      │  (执行任务/Gate等待)             │
├──────────────────────────┼──────────────────────────────────┤
│  窗口 2: worker_1        │  窗口 3: worker_2                │
│  ...                     │  ...                             │
└──────────────────────────┴──────────────────────────────────┘

用户操作:
- Ctrl+b N     → 切换到窗口 N
- Ctrl+b d     → 分离 tmux (后台运行)
- xteam attach → 重新连接到 tmux
```

---

## 3. 核心数据模型

### 3.1 任务模型

```python
# xteam/models/task.py

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid


class TaskStatus(str, Enum):
    pending = "pending"              # 等待执行
    in_progress = "in_progress"      # 执行中
    blocked = "blocked"              # 被阻塞
    waiting_approval = "waiting_approval"  # Gate 等待确认
    completed = "completed"          # 已完成
    failed = "failed"                # 失败


class TaskDefinition(BaseModel):
    """任务定义"""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    subject: str = Field(description="任务标题")
    description: str = Field(default="", description="任务描述")
    owner: str = Field(default="", description="负责的 Worker")

    # 依赖
    blocked_by: list[str] = Field(default_factory=list, description="依赖的任务ID")

    # Gate 相关
    requires_approval: bool = Field(default=False, description="是否需要人工确认")

    # 状态
    status: TaskStatus = Field(default=TaskStatus.pending)

    # 时间
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str = Field(default="")
    completed_at: str = Field(default="")

    # 产出物
    output_path: str = Field(default="", description="产出物路径")

    # 元数据
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskPlan(BaseModel):
    """任务计划"""
    goal: str = Field(description="用户原始目标")
    tasks: list[TaskDefinition] = Field(default_factory=list)
    execution_order: list[str] = Field(default_factory=list, description="拓扑排序后的执行顺序")
```

### 3.2 消息模型

```python
# xteam/models/message.py

from enum import Enum
from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid


class MessageType(str, Enum):
    # 任务相关
    task_assigned = "task_assigned"        # 任务分配
    task_completed = "task_completed"      # 任务完成
    task_failed = "task_failed"            # 任务失败

    # Gate 相关
    gate_waiting = "gate_waiting"          # Gate 等待确认
    gate_approved = "gate_approved"        # Gate 已确认
    gate_rejected = "gate_rejected"        # Gate 已拒绝

    # 状态相关
    idle = "idle"                          # Worker 空闲
    progress = "progress"                  # 进度更新

    # 通用
    message = "message"                    # 普通消息


class TeamMessage(BaseModel):
    """团队消息"""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    type: MessageType = Field(default=MessageType.message)
    from_agent: str = Field(description="发送者")
    to: str = Field(description="接收者")
    content: str = Field(default="", description="消息内容")
    task_id: str = Field(default="", description="关联的任务ID")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # 附加数据
    data: dict[str, Any] = Field(default_factory=dict)
```

### 3.3 团队模型

```python
# xteam/models/team.py

from pydantic import BaseModel, Field
from datetime import datetime, timezone
import uuid


class WorkerInfo(BaseModel):
    """Worker 信息"""
    name: str
    role: str = "worker"
    status: str = "idle"  # idle, working, waiting_approval
    current_task: str = ""
    tmux_window: int = 0


class TeamConfig(BaseModel):
    """团队配置"""
    name: str
    goal: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    workers: list[WorkerInfo] = Field(default_factory=list)
    leader_window: int = 0  # Leader 所在的 tmux 窗口
    board_window: int = 0   # Board 所在的 tmux 窗口
```

---

## 4. 核心组件设计

### 4.1 文件存储层

```
~/.xteam/
├── teams/
│   └── {team_name}/
│       ├── config.json          # 团队配置
│       ├── tasks/
│       │   └── task-{id}.json   # 任务状态文件
│       ├── inboxes/
│       │   ├── leader/
│       │   │   └── msg-{ts}-{id}.json
│       │   ├── worker_0/
│       │   └── worker_1/
│       └── outputs/
│           └── task-{id}/       # 任务产出物
└── sessions/
    └── {team_name}/
        └── {worker_name}.json   # Worker session 状态
```

### 4.2 TaskStore - 任务存储

```python
# xteam/store/tasks.py

import json
import fcntl
from pathlib import Path
from typing import Any
from contextlib import contextmanager

from xteam.models.task import TaskDefinition, TaskStatus


class TaskStore:
    """文件系统任务存储"""

    def __init__(self, team_name: str, data_dir: Path | None = None):
        self.team_name = team_name
        self.data_dir = data_dir or Path.home() / ".xteam"
        self.tasks_dir = self.data_dir / "teams" / team_name / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _write_lock(self):
        lock_path = self.tasks_dir / ".lock"
        with lock_path.open("a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def create(self, task: TaskDefinition) -> TaskDefinition:
        """创建任务"""
        with self._write_lock():
            self._save(task)
        return task

    def get(self, task_id: str) -> TaskDefinition | None:
        """获取任务"""
        path = self.tasks_dir / f"task-{task_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return TaskDefinition.model_validate(data)

    def update(
        self,
        task_id: str,
        status: TaskStatus | None = None,
        owner: str | None = None,
        output_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskDefinition | None:
        """更新任务"""
        with self._write_lock():
            task = self.get(task_id)
            if not task:
                return None

            if status is not None:
                task.status = status
                if status == TaskStatus.in_progress:
                    task.started_at = datetime.now(timezone.utc).isoformat()
                elif status == TaskStatus.completed:
                    task.completed_at = datetime.now(timezone.utc).isoformat()

            if owner is not None:
                task.owner = owner
            if output_path is not None:
                task.output_path = output_path
            if metadata is not None:
                task.metadata.update(metadata)

            self._save(task)

            # 如果任务完成，解除依赖它的任务的阻塞
            if status == TaskStatus.completed:
                self._resolve_dependents(task_id)

            return task

    def list_tasks(
        self,
        status: TaskStatus | None = None,
        owner: str | None = None,
    ) -> list[TaskDefinition]:
        """列出任务"""
        tasks = []
        for path in sorted(self.tasks_dir.glob("task-*.json")):
            try:
                task = TaskDefinition.model_validate(json.loads(path.read_text()))
                if status and task.status != status:
                    continue
                if owner and task.owner != owner:
                    continue
                tasks.append(task)
            except Exception:
                continue
        return tasks

    def get_ready_tasks(self) -> list[TaskDefinition]:
        """获取可以执行的任务（无依赖或依赖已完成）"""
        all_tasks = self.list_tasks()
        completed_ids = {t.id for t in all_tasks if t.status == TaskStatus.completed}

        ready = []
        for task in all_tasks:
            if task.status != TaskStatus.pending:
                continue
            # 检查依赖是否都已完成
            if all(dep_id in completed_ids for dep_id in task.blocked_by):
                ready.append(task)
        return ready

    def _save(self, task: TaskDefinition):
        path = self.tasks_dir / f"task-{task.id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(task.model_dump_json(indent=2))
        tmp.rename(path)

    def _resolve_dependents(self, completed_task_id: str):
        """解除依赖已完成任务的任务的阻塞"""
        for task in self.list_tasks(status=TaskStatus.blocked):
            if completed_task_id in task.blocked_by:
                task.blocked_by.remove(completed_task_id)
                if not task.blocked_by:
                    task.status = TaskStatus.pending
                self._save(task)
```

### 4.3 InboxManager - 消息通信

```python
# xteam/store/inbox.py

import json
import uuid
from pathlib import Path
from datetime import datetime, timezone

from xteam.models.message import TeamMessage, MessageType


class InboxManager:
    """基于文件系统的消息队列"""

    def __init__(self, team_name: str, data_dir: Path | None = None):
        self.team_name = team_name
        self.data_dir = data_dir or Path.home() / ".xteam"
        self.inboxes_dir = self.data_dir / "teams" / team_name / "inboxes"
        self.inboxes_dir.mkdir(parents=True, exist_ok=True)

    def send(
        self,
        from_agent: str,
        to: str,
        content: str,
        msg_type: MessageType = MessageType.message,
        task_id: str = "",
        data: dict | None = None,
    ) -> TeamMessage:
        """发送消息"""
        inbox = self.inboxes_dir / to
        inbox.mkdir(parents=True, exist_ok=True)

        timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
        msg = TeamMessage(
            id=uuid.uuid4().hex[:8],
            type=msg_type,
            from_agent=from_agent,
            to=to,
            content=content,
            task_id=task_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            data=data or {},
        )

        # 原子写入
        msg_path = inbox / f"msg-{timestamp}-{msg.id}.json"
        tmp_path = msg_path.with_suffix(".tmp")
        tmp_path.write_text(msg.model_dump_json(indent=2))
        tmp_path.rename(msg_path)

        return msg

    def receive(self, agent_name: str, limit: int = 10) -> list[TeamMessage]:
        """接收消息（消费）"""
        inbox = self.inboxes_dir / agent_name
        if not inbox.exists():
            return []

        messages = []
        files = sorted(inbox.glob("msg-*.json"))[:limit]

        for f in files:
            try:
                msg = TeamMessage.model_validate(json.loads(f.read_text()))
                messages.append(msg)
                f.unlink()  # 消费后删除
            except Exception:
                pass

        return messages

    def peek(self, agent_name: str) -> list[TeamMessage]:
        """查看消息（不消费）"""
        inbox = self.inboxes_dir / agent_name
        if not inbox.exists():
            return []

        messages = []
        for f in sorted(inbox.glob("msg-*.json")):
            try:
                messages.append(TeamMessage.model_validate(json.loads(f.read_text())))
            except Exception:
                pass
        return messages

    def count(self, agent_name: str) -> int:
        """消息数量"""
        inbox = self.inboxes_dir / agent_name
        if not inbox.exists():
            return 0
        return len(list(inbox.glob("msg-*.json")))
```

### 4.4 TeamLeader - 协调器

```python
# xteam/leader.py

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from xteam.models.task import TaskDefinition, TaskStatus, TaskPlan
from xteam.models.message import MessageType
from xteam.store.tasks import TaskStore
from xteam.store.inbox import InboxManager
from xteam.spawn import spawn_worker
from xteam.board import BoardMonitor


class TeamLeader:
    """Team Leader Agent - 任务协调器"""

    def __init__(self, team_name: str):
        self.team_name = team_name
        self.tasks = TaskStore(team_name)
        self.inbox = InboxManager(team_name)
        self.workers: dict[str, dict] = {}
        self.board: BoardMonitor | None = None

    def start(self, goal: str, max_workers: int = 3):
        """启动团队"""
        print(f"🚀 启动团队: {self.team_name}")
        print(f"🎯 目标: {goal}")

        # 1. 分解任务
        print("📋 正在分解任务...")
        plan = self._plan_tasks(goal)

        # 2. 创建任务
        for task in plan.tasks:
            self.tasks.create(task)
            print(f"   ✓ {task.id}: {task.subject}")

        # 3. 启动 Board 监控窗口
        self.board = BoardMonitor(self.team_name)
        self.board.start()

        # 4. 创建 Workers
        print(f"👷 创建 {max_workers} 个 Worker...")
        for i in range(max_workers):
            worker_name = f"worker_{i}"
            spawn_worker(
                team_name=self.team_name,
                agent_name=worker_name,
                role="worker",
            )
            self.workers[worker_name] = {
                "status": "idle",
                "current_task": None,
                "window": i + 1,  # 窗口0是 board
            }

        # 5. 进入协调循环
        print("🔄 开始协调...")
        self._coordinate_loop()

    def _plan_tasks(self, goal: str) -> TaskPlan:
        """使用 LLM 分解任务"""
        prompt = self._build_planning_prompt(goal)

        # 调用 xbot 进行任务分解
        result = subprocess.run(
            ["xbot", "agent", "-m", prompt],
            capture_output=True,
            text=True,
        )

        return self._parse_plan(result.stdout, goal)

    def _build_planning_prompt(self, goal: str) -> str:
        return f"""请将以下目标分解为具体的任务列表，以 JSON 格式输出：

目标: {goal}

输出格式:
```json
{{
  "tasks": [
    {{
      "subject": "任务标题",
      "description": "任务详细描述",
      "requires_approval": false,
      "blocked_by": []
    }}
  ]
}}
```

要求:
1. 任务粒度适中，每个任务可以由一个 Agent 独立完成
2. 明确任务间的依赖关系 (blocked_by)
3. 关键节点（如需求确认、测试验收）设置 requires_approval: true
4. 输出纯 JSON，不要有其他内容
"""

    def _parse_plan(self, response: str, goal: str) -> TaskPlan:
        """解析 LLM 返回的任务计划"""
        # 提取 JSON
        json_str = response
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0]
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0]

        data = json.loads(json_str.strip())

        tasks = []
        for i, t in enumerate(data.get("tasks", [])):
            task = TaskDefinition(
                id=f"task_{i}",
                subject=t.get("subject", ""),
                description=t.get("description", ""),
                requires_approval=t.get("requires_approval", False),
                blocked_by=t.get("blocked_by", []),
            )
            if task.blocked_by:
                task.status = TaskStatus.blocked
            tasks.append(task)

        return TaskPlan(goal=goal, tasks=tasks)

    def _coordinate_loop(self):
        """协调循环"""
        while True:
            # 1. 分配任务
            self._assign_tasks()

            # 2. 处理消息
            self._process_messages()

            # 3. 检查是否完成
            if self._is_all_completed():
                self._summarize()
                break

            time.sleep(2)

    def _assign_tasks(self):
        """分配任务给空闲 Worker"""
        ready_tasks = self.tasks.get_ready_tasks()
        idle_workers = [
            name for name, info in self.workers.items()
            if info["status"] == "idle"
        ]

        for task in ready_tasks:
            if not idle_workers:
                break

            worker = idle_workers.pop(0)
            self._assign_task(worker, task)

    def _assign_task(self, worker: str, task: TaskDefinition):
        """分配任务"""
        # 更新任务状态
        self.tasks.update(task.id, status=TaskStatus.in_progress, owner=worker)

        # 发送消息给 Worker
        self.inbox.send(
            from_agent="leader",
            to=worker,
            content=json.dumps({
                "task_id": task.id,
                "subject": task.subject,
                "description": task.description,
                "requires_approval": task.requires_approval,
            }),
            msg_type=MessageType.task_assigned,
            task_id=task.id,
        )

        # 更新 Worker 状态
        self.workers[worker]["status"] = "working"
        self.workers[worker]["current_task"] = task.id

        print(f"📤 分配任务: {task.id} -> {worker}")

    def _process_messages(self):
        """处理消息"""
        messages = self.inbox.receive("leader")

        for msg in messages:
            if msg.type == MessageType.task_completed:
                self._handle_task_completed(msg)
            elif msg.type == MessageType.gate_waiting:
                self._handle_gate_waiting(msg)
            elif msg.type == MessageType.gate_approved:
                self._handle_gate_approved(msg)
            elif msg.type == MessageType.idle:
                self._handle_worker_idle(msg)

    def _handle_task_completed(self, msg: TeamMessage):
        """处理任务完成"""
        task_id = msg.task_id
        self.tasks.update(task_id, status=TaskStatus.completed)

        # 更新 Worker 状态
        for name, info in self.workers.items():
            if info.get("current_task") == task_id:
                info["status"] = "idle"
                info["current_task"] = None
                break

        print(f"✅ 任务完成: {task_id}")

    def _handle_gate_waiting(self, msg: TeamMessage):
        """处理 Gate 等待 - 自动跳转到 Worker 窗口"""
        task_id = msg.task_id
        worker = msg.from_agent

        # 更新任务状态
        self.tasks.update(task_id, status=TaskStatus.waiting_approval)

        # 更新 Worker 状态
        if worker in self.workers:
            self.workers[worker]["status"] = "waiting_approval"

        # 自动切换到 Worker 窗口
        window = self.workers[worker]["window"]
        subprocess.run([
            "tmux", "select-window", "-t",
            f"xteam-{self.team_name}:{window}"
        ])

        print(f"🚧 Gate 等待: {task_id} (已跳转到 {worker})")

    def _handle_gate_approved(self, msg: TeamMessage):
        """处理 Gate 确认"""
        task_id = msg.task_id
        worker = msg.from_agent

        # 更新任务状态
        self.tasks.update(task_id, status=TaskStatus.completed)

        # 更新 Worker 状态
        if worker in self.workers:
            self.workers[worker]["status"] = "idle"
            self.workers[worker]["current_task"] = None

        # 跳回 Board 窗口
        subprocess.run([
            "tmux", "select-window", "-t",
            f"xteam-{self.team_name}:0"
        ])

        print(f"✅ Gate 确认: {task_id}")

    def _handle_worker_idle(self, msg: TeamMessage):
        """处理 Worker 空闲"""
        worker = msg.from_agent
        if worker in self.workers:
            self.workers[worker]["status"] = "idle"

    def _is_all_completed(self) -> bool:
        """检查是否全部完成"""
        tasks = self.tasks.list_tasks()
        return all(t.status == TaskStatus.completed for t in tasks)

    def _summarize(self):
        """汇总结果"""
        print("\n" + "="*50)
        print("🎉 所有任务完成！")
        print("="*50)

        tasks = self.tasks.list_tasks()
        for task in tasks:
            print(f"  ✓ {task.subject}")
            if task.output_path:
                print(f"    产出: {task.output_path}")
```

### 4.5 BoardMonitor - 监控窗口

```python
# xteam/board.py

import subprocess
import time
from pathlib import Path

from xteam.models.task import TaskStatus
from xteam.store.tasks import TaskStore


class BoardMonitor:
    """实时监控窗口"""

    def __init__(self, team_name: str):
        self.team_name = team_name
        self.tasks = TaskStore(team_name)
        self.running = False
        self.interval = 2  # 刷新间隔(秒)

    def start(self):
        """启动监控窗口"""
        session_name = f"xteam-{self.team_name}"

        # 创建 tmux session，窗口 0 是 board
        subprocess.run([
            "tmux", "new-session", "-d",
            "-s", session_name,
            "-n", "board",
            "-x", "200", "-y", "50",
        ])

        # 在 board 窗口启动监控循环
        subprocess.run([
            "tmux", "send-keys", "-t", f"{session_name}:board",
            f"python -m xteam.board_loop {self.team_name}",
            "Enter"
        ])

    def render(self) -> str:
        """渲染监控界面"""
        tasks = self.tasks.list_tasks()

        # 统计
        total = len(tasks)
        completed = sum(1 for t in tasks if t.status == TaskStatus.completed)
        in_progress = sum(1 for t in tasks if t.status == TaskStatus.in_progress)
        waiting = sum(1 for t in tasks if t.status == TaskStatus.waiting_approval)
        blocked = sum(1 for t in tasks if t.status == TaskStatus.blocked)

        # 进度条
        if total > 0:
            percent = completed / total
            bar_len = 40
            filled = int(bar_len * percent)
            bar = "█" * filled + "░" * (bar_len - filled)
        else:
            bar = "░" * 40
            percent = 0

        # 构建输出
        lines = []
        lines.append("")
        lines.append(f"  Team: {self.team_name}")
        lines.append(f"  {'━'*50}")
        lines.append(f"  {bar} {percent*100:.0f}% ({completed}/{total})")
        lines.append(f"  {'━'*50}")
        lines.append("")
        lines.append(f"  {'ID':<15} {'状态':<12} {'Owner':<12} {'任务'}")
        lines.append(f"  {'-'*60}")

        status_icons = {
            TaskStatus.pending: "⏳ 等待",
            TaskStatus.in_progress: "🔄 进行",
            TaskStatus.blocked: "⏸️ 阻塞",
            TaskStatus.waiting_approval: "🚧 确认",
            TaskStatus.completed: "✅ 完成",
            TaskStatus.failed: "❌ 失败",
        }

        for task in tasks:
            status = status_icons.get(task.status, task.status.value)
            lines.append(f"  {task.id:<15} {status:<12} {task.owner:<12} {task.subject}")

        lines.append("")
        lines.append(f"  统计: 完成 {completed} | 进行中 {in_progress} | 等待确认 {waiting} | 阻塞 {blocked}")
        lines.append("")
        lines.append(f"  更新: {time.strftime('%H:%M:%S')}")
        lines.append("")

        return "\n".join(lines)


def run_board_loop(team_name: str):
    """运行监控循环（在 tmux 中执行）"""
    board = BoardMonitor(team_name)

    # 清屏函数
    def clear_screen():
        print("\033[2J\033[H", end="")

    try:
        while True:
            clear_screen()
            print(board.render())
            time.sleep(board.interval)
    except KeyboardInterrupt:
        print("\n监控已停止")
```

### 4.6 spawn_worker - 创建 Worker

```python
# xteam/spawn.py

import os
import subprocess
from pathlib import Path


WORKER_PROMPT = """
## 团队协作上下文

你是 **{team_name}** 团队的 Worker Agent（名称: {agent_name}）。

### 协作命令
- 发送消息: `xteam inbox send --team {team_name} --from {agent_name} --to leader --message "..."`
- 发送消息(type): `xteam inbox send --team {team_name} --from {agent_name} --to leader --type TASK_COMPLETED --task-id xxx`
- 查看任务: `xteam task list --team {team_name} --owner {agent_name}`

### Gate 确认机制

当任务标记为 `requires_approval: true` 时：

1. 完成任务后，不要结束对话，进入等待状态
2. 告知用户已完成，等待确认
3. 系统会自动将用户带到你的窗口

### 用户确认触发词

当用户表达确认意图时（如以下任意表达），执行确认流程：
- "确认"、"通过"、"OK"、"可以"、"没问题"
- "approve"、"done"、"完成"
- "继续"、"下一步"

### 收到确认后执行

1. 发送 gate_approved 消息:
```
xteam inbox send --team {team_name} --from {agent_name} --to leader --type gate_approved --task-id {task_id}
```

2. 更新任务状态:
```
xteam task update --team {team_name} {task_id} --status completed
```

### 工作流程
1. 启动后检查收件箱获取任务
2. 执行任务，必要时与 Leader 通信
3. 完成后发送完成消息或等待 Gate 确认
"""


def spawn_worker(
    team_name: str,
    agent_name: str,
    role: str,
    task: str = "等待任务分配",
    worktree: str | None = None,
) -> str:
    """创建 Worker Agent"""

    session_name = f"xteam-{team_name}"

    # 检查 session 是否存在
    check = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )

    # 构建协作提示词
    collaboration_prompt = WORKER_PROMPT.format(
        team_name=team_name,
        agent_name=agent_name,
    )

    # 环境变量
    env = os.environ.copy()
    env.update({
        "XTEAM_TEAM_NAME": team_name,
        "XTEAM_AGENT_NAME": agent_name,
        "XTEAM_AGENT_ROLE": role,
        "XTEAM_COLLABORATION_PROMPT": collaboration_prompt,
    })

    # 构建 xbot agent 命令
    cmd = ["xbot", "agent", "--session", f"team:{agent_name}"]
    if worktree:
        cmd.extend(["--workspace", worktree])

    # 获取下一个窗口编号
    if check.returncode != 0:
        # 创建新 session
        subprocess.run([
            "tmux", "new-session", "-d",
            "-s", session_name,
            "-n", agent_name,
            *cmd
        ], env=env)
        window_num = 0
    else:
        # 在现有 session 中创建新窗口
        result = subprocess.run(
            ["tmux", "list-windows", "-t", session_name, "-F", "#{window_index}"],
            capture_output=True, text=True,
        )
        window_num = len(result.stdout.strip().splitlines())

        subprocess.run([
            "tmux", "new-window",
            "-t", session_name,
            "-n", agent_name,
            *cmd
        ], env=env)

    return f"Worker '{agent_name}' spawned in window {window_num}"


def is_agent_alive(team_name: str, agent_name: str) -> bool | None:
    """检查 Agent 是否存活

    Returns:
        True: 存活
        False: 已终止
        None: 不确定
    """
    session_name = f"xteam-{team_name}"

    # 检查窗口是否存在
    result = subprocess.run(
        ["tmux", "list-windows", "-t", session_name, "-F", "#{window_name}"],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        return False

    windows = result.stdout.strip().splitlines()
    return agent_name in windows
```

---

## 5. CLI 接口设计

### 5.1 主命令

```python
# xteam/cli.py

import typer
from typing import Optional

app = typer.Typer(
    name="xteam",
    help="🦞 xbot-team - Multi-Agent Collaboration Framework",
)


@app.command()
def start(
    goal: str = typer.Argument(..., help="目标描述"),
    team: str = typer.Option("default", "--team", "-t", help="团队名称"),
    workers: int = typer.Option(3, "--workers", "-w", help="Worker 数量"),
):
    """启动一个团队来完成目标"""
    from xteam.leader import TeamLeader

    leader = TeamLeader(team_name=team)
    leader.start(goal, max_workers=workers)


@app.command()
def attach(
    team: str = typer.Option("default", "--team", "-t", help="团队名称"),
):
    """连接到团队的 tmux session"""
    import subprocess

    session_name = f"xteam-{team}"
    subprocess.run(["tmux", "attach-session", "-t", session_name])


@app.command()
def status(
    team: str = typer.Option("default", "--team", "-t", help="团队名称"),
):
    """查看团队状态"""
    from xteam.board import BoardMonitor

    board = BoardMonitor(team)
    print(board.render())


# 任务管理
task_app = typer.Typer(name="task", help="任务管理")
app.add_typer(task_app, name="task")


@task_app.command("list")
def task_list(
    team: str = typer.Option(..., "--team", "-t"),
    owner: str = typer.Option(None, "--owner", "-o"),
):
    """列出任务"""
    from xteam.store.tasks import TaskStore

    store = TaskStore(team)
    tasks = store.list_tasks(owner=owner)

    for task in tasks:
        print(f"{task.id}\t{task.status.value}\t{task.subject}")


@task_app.command("update")
def task_update(
    team: str = typer.Option(..., "--team", "-t"),
    task_id: str = typer.Argument(...),
    status: str = typer.Option(None, "--status", "-s"),
):
    """更新任务状态"""
    from xteam.store.tasks import TaskStore
    from xteam.models.task import TaskStatus

    store = TaskStore(team)
    task_status = TaskStatus(status) if status else None
    store.update(task_id, status=task_status)
    print(f"Updated task {task_id}")


# 消息管理
inbox_app = typer.Typer(name="inbox", help="消息管理")
app.add_typer(inbox_app, name="inbox")


@inbox_app.command("send")
def inbox_send(
    team: str = typer.Option(..., "--team", "-t"),
    from_agent: str = typer.Option(..., "--from"),
    to: str = typer.Option(..., "--to"),
    message: str = typer.Option("", "--message", "-m"),
    type: str = typer.Option("message", "--type"),
    task_id: str = typer.Option("", "--task-id"),
):
    """发送消息"""
    from xteam.store.inbox import InboxManager
    from xteam.models.message import MessageType

    inbox = InboxManager(team)
    msg_type = MessageType(type)

    inbox.send(
        from_agent=from_agent,
        to=to,
        content=message,
        msg_type=msg_type,
        task_id=task_id,
    )
    print(f"Message sent to {to}")


@inbox_app.command("list")
def inbox_list(
    team: str = typer.Option(..., "--team", "-t"),
    agent: str = typer.Option(..., "--agent", "-a"),
):
    """查看消息"""
    from xteam.store.inbox import InboxManager

    inbox = InboxManager(team)
    messages = inbox.peek(agent)

    for msg in messages:
        print(f"[{msg.type.value}] {msg.from_agent}: {msg.content[:50]}")


if __name__ == "__main__":
    app()
```

### 5.2 使用示例

```bash
# 启动团队
xteam start "开发一个待办事项 CLI 工具" --team todo-app --workers 3

# 连接到 tmux 查看
xteam attach --team todo-app

# 查看状态（不进入 tmux）
xteam status --team todo-app

# 任务操作
xteam task list --team todo-app
xteam task update --team todo-app task_0 --status completed

# 消息操作
xteam inbox send --team todo-app --from worker_0 --to leader --type task_completed --task-id task_0
```

---

## 6. Gate 交互流程

### 6.1 完整流程

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  1. Worker 完成需要确认的任务                                 │
│     └── requires_approval = True                            │
│     └── Worker 发送 gate_waiting 消息                        │
│                                                             │
│  2. Leader 收到消息                                          │
│     └── 更新任务状态: waiting_approval                        │
│     └── 自动跳转到 Worker 窗口                               │
│                                                             │
│  3. 用户在 Worker 窗口交互                                   │
│     ├── 查看/修改产出物                                      │
│     ├── 与 Worker 对话                                      │
│     └── 确认: "OK" / "确认" / "继续"                         │
│                                                             │
│  4. Worker 收到确认                                          │
│     └── 发送 gate_approved 消息                              │
│     └── 更新任务状态: completed                              │
│     └── 进入空闲状态                                         │
│                                                             │
│  5. Leader 收到确认                                          │
│     └── 解除后续任务阻塞                                     │
│     └── 自动跳回 Board 窗口                                  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 tmux 界面示意

```
Gate 触发时:

┌─────────────────────────────────────────────────────────────┐
│  [自动跳转] worker_0 窗口                                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Worker: 需求文档已完成，主要内容包括：                       │
│          1. 用户登录/注册                                    │
│          2. 待办事项 CRUD                                    │
│          3. 数据持久化                                       │
│                                                             │
│          文档路径: outputs/requirement.md                    │
│                                                             │
│          请确认是否可以进入开发阶段？                         │
│                                                             │
│  用户: _                                                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘

用户确认后:

┌─────────────────────────────────────────────────────────────┐
│  [自动跳转] board 窗口                                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Team: todo-app                                             │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   │
│  ████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  25% (1/4)      │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   │
│                                                             │
│  ID               状态          Owner        任务           │
│  ---------------------------------------------------------- │
│  task_0           ✅ 完成       worker_0     编写需求文档    │
│  task_1           🔄 进行       worker_1     开发API        │
│  task_2           🔄 进行       worker_2     开发CLI        │
│  task_3           ⏸️ 阻塞       -            集成测试        │
│                                                             │
│  更新: 14:35:22                                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 7. xbot 集成（可选增强）

### 7.1 环境变量检测

在 xbot 中可选检测 `XTEAM_*` 环境变量：

```python
# xbot/agent/runtime.py (可选修改)

def _get_system_prompt() -> str:
    """获取系统提示词"""
    base_prompt = "..."

    # 检测 xteam 环境
    collab_prompt = os.environ.get("XTEAM_COLLABORATION_PROMPT")
    if collab_prompt:
        base_prompt += f"\n\n{collab_prompt}"

    return base_prompt
```

### 7.2 不修改 xbot 的替代方案

如果不修改 xbot，Worker 启动时将协作提示词作为第一条消息发送：

```python
# xteam/spawn.py

def spawn_worker(...):
    # ...

    # 启动后自动注入协作提示词
    subprocess.run([
        "tmux", "send-keys", "-t", f"{session_name}:{agent_name}",
        collaboration_prompt,
        "Enter"
    ])
```

---

## 8. 项目结构

```
/home/xbot/projects/xbot-team/
├── xteam/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                 # CLI 入口
│   ├── leader.py              # Team Leader 协调器
│   ├── board.py               # Board 监控窗口
│   ├── board_loop.py          # 监控循环入口
│   ├── spawn.py               # Worker 创建
│   ├── models/
│   │   ├── __init__.py
│   │   ├── task.py            # 任务模型
│   │   ├── message.py         # 消息模型
│   │   └── team.py            # 团队模型
│   └── store/
│       ├── __init__.py
│       ├── tasks.py           # 任务存储
│       └── inbox.py           # 消息存储
├── tests/
│   └── ...
├── pyproject.toml
└── README.md
```

---

## 9. 后续扩展

### Phase 1 (当前)
- [x] 基本任务分解
- [x] Worker 创建和协调
- [x] 任务依赖链
- [x] Gate 机制
- [x] Board 监控窗口

### Phase 2
- [ ] Leader 交互窗口（可对话查询状态）
- [ ] 任务产出物管理
- [ ] Worker 重启/恢复

### Phase 3
- [ ] 团队模板（TOML 定义）
- [ ] 多种 Worker 角色（coder, tester, writer 等）
- [ ] Web UI 监控面板

---

## 10. 总结

| 特性 | 实现方式 |
|------|---------|
| **无侵入** | 独立项目，通过 CLI 调用 xbot |
| **任务协调** | Leader Agent 集中调度 |
| **消息通信** | 文件系统 Inbox |
| **Gate 机制** | Worker Session 保持 + 自动窗口跳转 |
| **用户介入** | 交互式对话，自然语言确认 |
| **进度监控** | Board 窗口实时刷新 |