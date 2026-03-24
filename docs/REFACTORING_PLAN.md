# 大文件重构方案

**版本**: v1.0  
**日期**: 2026-03-24  
**目标**: 低风险渐进式拆分三个核心大文件，提升代码可维护性

---

## 目录

1. [重构目标](#重构目标)
2. [当前问题](#当前问题)
3. [整体策略](#整体策略)
4. [Phase 1: claude_sdk_backend.py 拆分](#phase-1-claude_sdk_backendpy-拆分)
5. [Phase 2: runtime.py 拆分](#phase-2-runtimepy-拆分)
6. [Phase 3: feishu.py 拆分](#phase-3-feishupy-拆分)
7. [风险评估矩阵](#风险评估矩阵)
8. [执行顺序建议](#执行顺序建议)

---

## 重构目标

- **可维护性**: 每个文件 < 500 行，单一职责
- **低风险**: 保持所有测试通过，渐进式执行
- **清晰边界**: 模块间依赖明确，减少循环依赖

---

## 当前问题

| 文件 | 行数 | 问题 |
|------|------|------|
| `agent/backends/claude_sdk_backend.py` | 2298 | 消息转换、选项构建、后端实现混杂 |
| `agent/runtime.py` | 1436 | 状态机、调度、会话管理、核心运行时混杂 |
| `channels/feishu.py` | 1312 | 内容提取逻辑与 Channel 实现混杂 |

---

## 整体策略

```
重构原则:
1. 先拆分独立模块（无依赖或依赖少）
2. 每次只拆一个类/一组函数
3. 拆分后立即运行测试验证
4. 保持 public API 不变
```

---

## Phase 1: claude_sdk_backend.py 拆分

**风险等级**: 🟢 低风险

### 文件结构分析

```
claude_sdk_backend.py (2298 行)
├── DelegationTrace (22 行, 76-97)      ← 独立数据类
├── MessageConverter (248 行, 98-345)   ← 独立，无外部依赖
├── OptionsBuilder (406 行, 346-751)    ← 依赖 config, tools
└── ClaudeSDKBackend (1546 行, 752-end) ← 核心后端，依赖上述所有
```

### 拆分计划

#### Step 1.1: 提取 MessageConverter (最安全)

**目标文件**: `agent/backends/message_converter.py`

**迁移内容**:
- `MessageConverter` 类 (第 98-345 行, 248 行)
- 相关 import

**预计行数**: ~260 行

**依赖分析**:
- 输入: `list[ContentBlock]` (来自 SDK)
- 输出: `list[dict]` (转换为 xbot 格式)
- 无外部模块依赖，纯工具类

**导入变更**:
```python
# claude_sdk_backend.py 顶部添加
from .message_converter import MessageConverter

# message_converter.py 需要的 import
from typing import Any
from loguru import logger
```

**测试影响**: 无需新增测试，现有测试继续通过

**执行验证**:
```bash
pytest tests/test_claude_sdk_backend.py -v
```

---

#### Step 1.2: 提取 OptionsBuilder

**目标文件**: `agent/backends/options_builder.py`

**迁移内容**:
- `OptionsBuilder` 类 (第 346-751 行, 406 行)
- 相关 import

**预计行数**: ~420 行

**依赖分析**:
- 依赖 `xbot.config` 获取模型配置
- 依赖 `xbot.agent.tools` 获取工具定义
- 依赖 `DelegationTrace` (保留在同文件)

**导入变更**:
```python
# claude_sdk_backend.py 顶部添加
from .options_builder import OptionsBuilder

# options_builder.py 需要的 import
from dataclasses import dataclass
from typing import Any
from xbot.config import get_config
from xbot.agent.tools import ToolRegistry
```

**测试影响**: 无需新增测试

**执行验证**:
```bash
pytest tests/test_claude_sdk_backend.py -v
```

---

#### Step 1.3: 提取 DelegationTrace

**目标文件**: `agent/backends/delegation.py`

**迁移内容**:
- `DelegationTrace` 类 (第 76-97 行, 22 行)

**预计行数**: ~40 行 (包含 docstring 和 import)

**依赖分析**:
- 纯数据类，无依赖

**导入变更**:
```python
# claude_sdk_backend.py 顶部添加
from .delegation import DelegationTrace
```

**执行验证**:
```bash
pytest tests/test_claude_sdk_backend.py -v
```

---

### Phase 1 完成后结构

```
agent/backends/
├── __init__.py
├── base.py              # AgentBackend 基类
├── claude_sdk_backend.py  # ~1560 行 (核心后端逻辑)
├── delegation.py        # ~40 行 (DelegationTrace)
├── message_converter.py # ~260 行 (MessageConverter)
├── options_builder.py   # ~420 行 (OptionsBuilder)
└── litellm_backend.py   # 现有
```

---

## Phase 2: runtime.py 拆分

**风险等级**: 🟡 中等风险 (核心模块)

### 文件结构分析

```
runtime.py (1436 行)
├── SessionPhase (60 行, 28-87)       ← 状态枚举
├── SessionState (7 行, 88-94)        ← 状态数据类
├── SessionStateMachine (131 行, 95-225) ← 状态机
└── AgentRuntime (1210 行, 226-end)   ← 核心运行时
```

### AgentRuntime 内部分析

```
AgentRuntime (~1210 行)
├── 初始化与配置 (~100 行)
├── 会话生命周期管理 (~150 行)
├── 消息处理入口 (_handle_message, ~50 行)
├── 消息调度逻辑 (_dispatch_*, ~200 行)
├── Agent 执行流程 (_run_agent_*, ~300 行)
├── 流式响应处理 (_handle_stream_*, ~150 行)
├── 进度回调与中断 (~100 行)
└── 清理与关闭 (~60 行)
```

### 拆分计划

#### Step 2.1: 提取状态相关类 (已有基础)

**现状**: 已有 `state_machine.py`, `state_coordinator.py`, `state_transaction.py`

**剩余工作**:
- `SessionPhase` 枚举已部分重复定义
- `SessionState` 数据类可移至 `state_machine.py`

**目标**:
- 移除 `runtime.py` 中的 `SessionPhase` 定义，统一使用 `state_machine.py` 中的
- 移除 `runtime.py` 中的 `SessionState` 定义，移至 `state_machine.py`

**预计减少**: ~70 行

**风险**: 🟡 中等 - 需要检查所有导入

**执行验证**:
```bash
pytest tests/ -v --tb=short
```

---

#### Step 2.2: 提取消息调度逻辑

**目标文件**: `agent/dispatcher.py`

**迁移内容**:
- `_dispatch_*` 系列方法 (~200 行)
- `_should_skip_message` 等辅助方法

**预计行数**: ~250 行

**设计方案**:
```python
# agent/dispatcher.py
class MessageDispatcher:
    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime
    
    async def dispatch(self, msg: ChannelMessage) -> None:
        """消息调度入口"""
        ...
    
    async def _dispatch_new_session(self, msg: ChannelMessage) -> None:
        ...
    
    async def _dispatch_continue_session(self, msg: ChannelMessage) -> None:
        ...
```

**依赖分析**:
- 依赖 `AgentRuntime` 的状态查询方法
- 依赖 `SessionPhase` 枚举

**导入变更**:
```python
# runtime.py
from .dispatcher import MessageDispatcher

class AgentRuntime:
    def __init__(self, ...):
        self._dispatcher = MessageDispatcher(self)
    
    async def _handle_message(self, msg: ChannelMessage) -> None:
        await self._dispatcher.dispatch(msg)
```

**风险**: 🟡 中等 - 需要重构方法签名

**执行验证**:
```bash
pytest tests/test_runtime.py tests/test_integration.py -v
```

---

#### Step 2.3: 提取流式响应处理

**目标文件**: `agent/stream_handler.py`

**迁移内容**:
- `_handle_stream_*` 系列方法 (~150 行)
- SSE 事件处理逻辑

**预计行数**: ~200 行

**设计方案**:
```python
# agent/stream_handler.py
class StreamHandler:
    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime
    
    async def handle_stream_event(self, event: dict, session_key: str) -> None:
        """处理单个流式事件"""
        ...
```

**风险**: 🟡 中等

**执行验证**:
```bash
pytest tests/test_runtime.py -v
```

---

### Phase 2 完成后结构

```
agent/
├── __init__.py
├── runtime.py           # ~800 行 (核心初始化、会话管理、Agent 执行)
├── dispatcher.py        # ~250 行 (消息调度)
├── stream_handler.py    # ~200 行 (流式响应处理)
├── state_machine.py     # 现有
├── state_coordinator.py # 现有
├── state_transaction.py # 现有
└── ...
```

---

## Phase 3: feishu.py 拆分

**风险等级**: 🟢 低风险

### 文件结构分析

```
feishu.py (1312 行)
├── _extract_* 辅助函数 (205 行, 35-239) ← 内容提取逻辑
├── FeishuConfig (14 行, 240-253)
└── FeishuChannel (1058 行, 254-end)
```

### 拆分计划

#### Step 3.1: 提取内容提取函数

**目标文件**: `channels/feishu_content.py`

**迁移内容**:
- `_extract_share_card_content` (20 行)
- `_extract_interactive_content` (41 行)
- `_extract_element_content` (73 行)
- `_extract_post_content` (62 行)
- `_extract_post_text` (9 行)

**预计行数**: ~250 行 (含 docstring)

**依赖分析**:
- 纯函数，无外部依赖
- 输入: `dict` (飞书消息内容 JSON)
- 输出: `str` 或 `list[str]`

**导入变更**:
```python
# feishu.py 顶部添加
from .feishu_content import (
    _extract_share_card_content,
    _extract_interactive_content,
    _extract_post_content,
)
```

**测试影响**:
- 可新增 `tests/test_feishu_content.py` 单元测试
- 现有测试继续通过

**执行验证**:
```bash
pytest tests/test_feishu.py -v
```

---

### Phase 3 完成后结构

```
channels/
├── feishu.py           # ~1060 行 (Channel 实现)
├── feishu_content.py   # ~250 行 (内容提取辅助函数)
├── telegram.py
├── matrix.py
└── ...
```

---

## 风险评估矩阵

| Step | 文件 | 风险 | 影响范围 | 测试覆盖 | 回滚难度 |
|------|------|------|----------|----------|----------|
| 1.1 | MessageConverter | 🟢 低 | 单文件 | 高 | 简单 |
| 1.2 | OptionsBuilder | 🟢 低 | 单文件 | 高 | 简单 |
| 1.3 | DelegationTrace | 🟢 低 | 单文件 | 高 | 简单 |
| 2.1 | 状态类整理 | 🟡 中 | 多文件 | 高 | 中等 |
| 2.2 | MessageDispatcher | 🟡 中 | runtime | 高 | 中等 |
| 2.3 | StreamHandler | 🟡 中 | runtime | 中 | 中等 |
| 3.1 | feishu_content | 🟢 低 | 单文件 | 中 | 简单 |

---

## 执行顺序建议

```
Week 1 (低风险热身):
  Day 1-2: Step 1.3 (DelegationTrace)
  Day 3-4: Step 1.1 (MessageConverter)
  Day 5:   Step 1.2 (OptionsBuilder)

Week 2 (飞书拆分):
  Day 1-2: Step 3.1 (feishu_content)

Week 3 (Runtime 拆分):
  Day 1:   Step 2.1 (状态类整理)
  Day 2-4: Step 2.2 (MessageDispatcher)
  Day 5:   Step 2.3 (StreamHandler)

每周执行:
  1. 只做 1-2 个 Step
  2. 每个 Step 后运行全量测试
  3. 确认无回归后再做下一个
```

---

## 检查清单

每个 Step 执行前:
- [ ] 创建新分支
- [ ] 确认当前测试全部通过

每个 Step 执行后:
- [ ] 运行 `pytest tests/ -v`
- [ ] 检查 import 是否正确
- [ ] 确认无循环依赖
- [ ] 更新相关 `__init__.py`

每个 Phase 完成后:
- [ ] 运行全量测试
- [ ] 检查代码风格 (`ruff check`)
- [ ] 更新文档（如有）

---

## 预期收益

| 指标 | 重构前 | 重构后 |
|------|--------|--------|
| 最大文件行数 | 2298 | ~1560 |
| 平均文件行数 | ~1000 | ~400 |
| 职责清晰度 | 混杂 | 单一 |
| 新人理解成本 | 高 | 低 |
| 测试覆盖难度 | 高 | 低 |

---

## 附录: 目录结构预览

```
xbot/
├── agent/
│   ├── backends/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── claude_sdk_backend.py  # ~1560 行
│   │   ├── delegation.py          # 新: ~40 行
│   │   ├── message_converter.py   # 新: ~260 行
│   │   ├── options_builder.py     # 新: ~420 行
│   │   └── litellm_backend.py
│   ├── __init__.py
│   ├── runtime.py                 # ~800 行
│   ├── dispatcher.py              # 新: ~250 行
│   ├── stream_handler.py          # 新: ~200 行
│   ├── state_machine.py
│   ├── state_coordinator.py
│   ├── state_transaction.py
│   └── ...
├── channels/
│   ├── feishu.py                  # ~1060 行
│   ├── feishu_content.py          # 新: ~250 行
│   └── ...
└── ...
```