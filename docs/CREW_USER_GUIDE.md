# Crew 多 Agent 协作指南

## 概述

Crew 是 xbot 的多 Agent 协作编排模块，支持多个 AI Agent 角色协同完成复杂任务。

### 核心特性

- **多 Agent 协作**: 定义不同角色的 Agent，各司其职
- **任务依赖**: 支持任务间的依赖关系和上下文传递
- **状态机管理**: 自动管理任务和 Crew 的状态转换
- **断点恢复**: 执行中断后可从检查点恢复
- **人工审核**: 支持任务完成后的人工审核干预

---

## 快速开始

### 1. 查看可用模板

```bash
xbot crew templates
```

输出示例：
```
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┓
┃ Name          ┃ Description                           ┃ Agents ┃ Tasks ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━┩
│ code-review   │ Code quality review and improvement   │ 3      │ 3     │
│ doc-generator │ Generate documentation from code      │ 3      │ 3     │
│ data-pipeline │ Design and implement data processing  │ 3      │ 4     │
│ bug-hunter    │ Find bugs and suggest fixes           │ 3      │ 3     │
│ test-writer   │ Write comprehensive tests for code    │ 3      │ 4     │
└───────────────┴───────────────────────────────────────┴────────┴───────┘
```

### 2. 创建项目

```bash
# 使用模板创建
xbot crew init my-project --template code-review

# 不使用模板（创建最小配置）
xbot crew init my-project
```

### 3. 验证配置

```bash
cd my-project
xbot crew validate crew_config.yaml --dry-run
```

输出示例：
```
Validating: crew_config.yaml

✓ YAML syntax valid
✓ 3 agent(s) defined
✓ 3 task(s) defined
✓ All task names unique
✓ All agent references valid
✓ All task dependencies valid
✓ No circular dependencies

✓ Validation passed!

Execution Plan:
  1. review_code → reviewer
  2. analyze_issues → analyzer (depends on: review_code)
  3. suggest_fixes → fixer (depends on: review_code, analyze_issues)

Estimated time: ~6-12 minutes
```

### 4. 执行 Crew

```bash
xbot crew run crew_config.yaml
```

---

## CLI 命令详解

### `xbot crew init`

初始化一个新的 Crew 项目。

```bash
xbot crew init <project_name> [OPTIONS]

Arguments:
  <project_name>  项目名称

Options:
  -t, --template TEXT   使用的模板名称
  -p, --path TEXT       项目父目录（默认：当前目录）

Examples:
  xbot crew init my-review --template code-review
  xbot crew init my-docs --template doc-generator -p /projects
```

### `xbot crew templates`

列出所有可用的内置模板。

```bash
xbot crew templates
```

### `xbot crew validate`

验证 Crew 配置文件。

```bash
xbot crew validate <config_file> [OPTIONS]

Arguments:
  <config_file>  配置文件路径

Options:
  --strict    启用严格验证模式
  --dry-run   模拟执行，显示执行计划

Examples:
  xbot crew validate crew_config.yaml
  xbot crew validate crew_config.yaml --dry-run
```

### `xbot crew show`

显示配置文件详情。

```bash
xbot crew show <config_file>
```

### `xbot crew run`

执行 Crew。

```bash
xbot crew run <config_file> [OPTIONS]

Arguments:
  <config_file>  配置文件路径

Options:
  -w, --workspace TEXT  覆盖工作目录
  -c, --config TEXT     xbot 配置文件
  -v, --verbose         详细输出
  --resume TEXT         从检查点恢复
  --progress/--no-progress  显示进度（默认开启）

Examples:
  xbot crew run crew_config.yaml
  xbot crew run crew_config.yaml -v
  xbot crew run crew_config.yaml --resume checkpoint.json
```

### `xbot crew checkpoints`

列出项目的检查点。

```bash
xbot crew checkpoints <project_dir> [OPTIONS]

Arguments:
  <project_dir>  项目目录（默认：当前目录）

Options:
  -n, --limit INT  显示数量限制（默认：10）

Examples:
  xbot crew checkpoints .
  xbot crew checkpoints /path/to/project -n 20
```

### `xbot crew resume`

从检查点恢复执行。

```bash
xbot crew resume <project_dir> [OPTIONS]

Arguments:
  <project_dir>  项目目录（默认：当前目录）

Options:
  -c, --checkpoint TEXT  检查点名称（默认：最新）
  -v, --verbose          详细输出

Examples:
  xbot crew resume .
  xbot crew resume . --checkpoint my_crew_20240325.json
```

### `xbot crew history`

查看执行历史。

```bash
xbot crew history <project_dir> [OPTIONS]

Arguments:
  <project_dir>  项目目录（默认：当前目录）

Options:
  -n, --limit INT  显示数量限制（默认：10）
```

### `xbot crew graph`

生成任务依赖图。

```bash
xbot crew graph <config_file> [OPTIONS]

Arguments:
  <config_file>  配置文件路径

Options:
  -o, --output TEXT  输出文件（默认：标准输出）
  --mermaid          输出 Mermaid 格式

Examples:
  xbot crew graph crew_config.yaml
  xbot crew graph crew_config.yaml --mermaid -o graph.mmd
```

---

## 配置文件格式

### 基本结构

```yaml
# Crew 基本信息
name: my_crew
description: Crew 描述
process: sequential  # sequential 或 hierarchical
workspace: .         # 工作目录

# 全局上下文（注入所有 Agent）
global_context: |
  你是一个团队的一员...

# Agent 定义
agents:
  agent_name:
    description: Agent 描述
    goal: Agent 目标
    backstory: 背景故事（可选）
    model: inherit  # inherit 或指定模型
    tools: null     # null 表示所有工具
    max_iterations: 30

# Task 定义
tasks:
  - name: task_name
    description: 任务描述
    agent: agent_name
    context_from: []      # 依赖的上游任务
    human_review: false   # 是否需要人工审核
    human_briefing: false # 是否允许人工前置指导
    timeout: 600          # 超时秒数
    expected_output: 期望输出描述
```

### 完整配置示例

```yaml
name: code_review_crew
description: 代码审查和改进建议

process: sequential
workspace: .

global_context: |
  你是一个代码审查团队的成员。
  关注代码质量，提供可操作的建议。

agents:
  reviewer:
    description: 代码审查员
    goal: 审查代码质量，识别问题
    backstory: |
      你是一位经验丰富的软件工程师，熟悉最佳实践。
    max_iterations: 30

  fixer:
    description: 代码修复专家
    goal: 提供具体的修复建议
    max_iterations: 35

tasks:
  - name: review_code
    description: |
      审查工作区中的代码。
      关注：
      1. 代码结构和组织
      2. 潜在的 bug
      3. 性能问题
    agent: reviewer
    timeout: 300

  - name: suggest_fixes
    description: |
      基于审查结果，提供具体的修复建议。
    agent: fixer
    context_from:
      - review_code
    timeout: 300
```

---

## 内置模板

### code-review（代码审查）

**用途**: 代码质量审查和改进建议

**Agent**: reviewer, analyzer, fixer

**任务流程**:
1. `review_code` - 全面代码审查
2. `analyze_issues` - 深入分析关键问题
3. `suggest_fixes` - 提供修复建议

```bash
xbot crew init my-review --template code-review
```

### doc-generator（文档生成）

**用途**: 从代码自动生成文档

**Agent**: analyst, writer, reviewer

**任务流程**:
1. `analyze_structure` - 分析代码结构
2. `generate_docs` - 生成文档
3. `review_docs` - 审核和完善

```bash
xbot crew init my-docs --template doc-generator
```

### data-pipeline（数据流水线）

**用途**: 设计和实现数据处理流水线

**Agent**: analyst, developer, validator

**任务流程**:
1. `analyze_data` - 分析数据
2. `design_pipeline` - 设计流水线
3. `implement_pipeline` - 实现代码
4. `create_tests` - 创建测试

```bash
xbot crew init my-pipeline --template data-pipeline
```

### bug-hunter（Bug 查找）

**用途**: 查找代码中的 bug 并提供修复建议

**Agent**: detector, investigator, fixer

**任务流程**:
1. `detect_bugs` - 检测潜在 bug
2. `investigate_bugs` - 深入调查
3. `fix_bugs` - 提供修复

```bash
xbot crew init bug-scan --template bug-hunter
```

### test-writer（测试编写）

**用途**: 为代码自动编写测试

**Agent**: analyst, unit_tester, integration_tester

**任务流程**:
1. `analyze_code` - 分析代码
2. `write_unit_tests` - 编写单元测试
3. `write_integration_tests` - 编写集成测试
4. `create_test_summary` - 生成测试摘要

```bash
xbot crew init my-tests --template test-writer
```

---

## 任务依赖

### 定义依赖

使用 `context_from` 字段定义任务依赖：

```yaml
tasks:
  - name: task_a
    agent: worker

  - name: task_b
    agent: worker
    context_from:
      - task_a        # 依赖 task_a 的输出

  - name: task_c
    agent: worker
    context_from:
      - task_a
      - task_b        # 可以依赖多个任务
```

### 依赖规则

1. **上游输出传递**: 上游任务的输出会作为上下文传递给下游任务
2. **执行顺序**: 依赖任务会在被依赖任务完成后执行
3. **失败传播**: 上游任务失败时，下游任务会被跳过

### 依赖图可视化

```bash
# ASCII 格式
xbot crew graph crew_config.yaml

# Mermaid 格式（可渲染为图片）
xbot crew graph crew_config.yaml --mermaid -o deps.mmd
```

---

## 状态机

### Crew 状态

| 状态 | 说明 |
|------|------|
| CREATED | 已创建，等待初始化 |
| INITIALIZING | 正在初始化 Agent Pool |
| RUNNING | 正在执行任务 |
| PAUSED | 等待人工审核 |
| COMPLETING | 任务完成，生成摘要 |
| COMPLETED | 正常完成 |
| FAILED | 执行失败 |
| ABORTING | 用户请求中止 |
| ABORTED | 已中止 |

### Task 状态

| 状态 | 说明 |
|------|------|
| PENDING | 等待执行 |
| BLOCKED | 上游依赖未完成 |
| QUEUED | 依赖已满足，等待执行 |
| RUNNING | 正在执行 |
| AWAITING_REVIEW | 等待人工审核 |
| RETRYING | 准备重试 |
| COMPLETED | 成功完成 |
| FAILED | 执行失败 |
| SKIPPED | 被跳过 |
| REJECTED | 人工拒绝 |

---

## 检查点和恢复

### 检查点位置

检查点保存在项目的 `.xbot/crew_checkpoints/` 目录下。

### 查看检查点

```bash
xbot crew checkpoints .

┏━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┓
┃ #   ┃ Checkpoint             ┃ Time     ┃ Status     ┃ Tasks                 ┃
┡━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1   │ my_crew_20240325.json   │ 14:30:15 │ completed  │ 5 done                │
│ 2   │ my_crew_20240325.json   │ 12:20:30 │ running    │ 3 done, next: task_4  │
└─────┴────────────────────────┴──────────┴────────────┴───────────────────────┘
```

### 恢复执行

```bash
# 自动使用最新检查点
xbot crew resume .

# 指定检查点
xbot crew resume . --checkpoint my_crew_20240325_143015.json
```

---

## 项目结构

使用 `xbot crew init` 创建的项目结构：

```
my-project/
├── crew_config.yaml      # Crew 配置文件
├── README.md             # 项目说明
├── workspace/            # 工作目录（任务执行目录）
└── .xbot/
    └── crew_checkpoints/ # 检查点目录
```

---

## 高级用法

### 人工审核

启用任务的人工审核：

```yaml
tasks:
  - name: critical_task
    agent: reviewer
    human_review: true  # 任务完成后暂停等待审核
```

支持的审核操作：
- `continue` - 继续执行
- `annotate` - 添加注释
- `edit` - 编辑输出
- `redo` - 重试任务
- `skip` - 跳过任务
- `abort` - 中止 Crew

### 层级模式

使用 Manager Agent 协调任务：

```yaml
process: hierarchical
manager_agent: manager

agents:
  manager:
    description: 团队经理
    goal: 协调团队完成任务
    max_iterations: 50

  worker:
    description: 执行者
    goal: 完成分配的任务
```

### 自定义超时

```yaml
tasks:
  - name: quick_task
    agent: worker
    timeout: 60    # 1 分钟

  - name: long_task
    agent: worker
    timeout: 1800  # 30 分钟
```

---

## 故障排除

### 常见问题

**Q: 任务执行超时怎么办？**

A: 增加 `timeout` 配置值，或简化任务描述。

**Q: 如何查看详细日志？**

A: 使用 `-v` 参数：`xbot crew run crew_config.yaml -v`

**Q: 依赖检查失败？**

A: 使用 `xbot crew validate --dry-run` 检查执行计划。

**Q: 如何恢复中断的执行？**

A: 使用 `xbot crew resume .` 自动恢复。

---

## 最佳实践

1. **使用模板**: 从内置模板开始，逐步定制
2. **验证配置**: 执行前先用 `validate --dry-run` 检查
3. **合理设置超时**: 根据任务复杂度设置合适的超时
4. **利用依赖图**: 使用 `graph` 命令可视化任务关系
5. **定期检查历史**: 使用 `history` 命令回顾执行情况