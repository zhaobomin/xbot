# Claude SDK Backend 重构方案 v2

## 目标
每个模块不超过 1000 行，职责清晰，低耦合。

## 当前状态

```
claude_sdk_backend.py: 2559 行 ❌ 太大
options_builder.py:     699 行 ✅
client_pool.py:         487 行 ✅
multimodal.py:          377 行 ✅
message_converter.py:   337 行 ✅
auxiliary_llm.py:       333 行 ✅
sdk_session_ops.py:     299 行 ✅
error_recovery.py:      292 行 ✅
client_lifecycle.py:    238 行 ✅
```

## 问题分析

### 1. 客户端管理代码重复
- `release_client` (134 行) + `_legacy_release_client` (147 行) = 281 行
- `_get_or_create_client` (38 行) + `_legacy_get_or_create_client` (72 行) = 110 行
- **应该只保留一份，而不是 legacy + delegation**

### 2. process() 方法过大
- 284 行，包含消息处理循环
- 已经拆分了 4 个 helper 方法，但逻辑仍在一个方法中

### 3. 很多小方法可以归类
- `__init__` 属性和 properties (~100 行)
- scavenger 相关 (~60 行)
- 日志和诊断方法 (~50 行)

---

## 新模块结构设计

```
xbot/agent/backends/
├── __init__.py                    (~30 行)
├── claude_sdk_backend.py          (~800 行)  ← 主入口，协调各模块
├── client_pool.py                 (~600 行)  ← 增强客户端管理
├── client_lifecycle.py            (~240 行)  ← 不变
├── process_executor.py            (~400 行)  ← 新建：process() 逻辑
├── session_manager.py             (~350 行)  ← 新建：会话操作
├── message_converter.py           (~340 行)  ← 不变
├── options_builder.py             (~700 行)  ← 不变
├── multimodal.py                  (~380 行)  ← 不变
├── error_recovery.py              (~300 行)  ← 不变
├── auxiliary_llm.py               (~340 行)  ← 不变
├── sdk_session_ops.py             (~300 行)  ← 不变
└── session_state_adapter.py       (~130 行)  ← 不变
```

---

## 详细拆分计划

### 模块 1: `claude_sdk_backend.py` (~800 行)

**保留内容：**
- 类定义和 `__init__` (~100 行)
- 配置 properties (~80 行)
- `initialize()` (~180 行) - 拆分子方法但保留在此
- `shutdown()` (~30 行)
- 简单的 delegation 方法 (~100 行)
- `process()` 入口 (~50 行) - 调用 ProcessExecutor
- 杂项小方法 (~100 行)
- imports 和类型定义 (~60 行)

**删除内容：**
- `_legacy_*` 方法 (-219 行)
- `_get_or_create_client` 实现 (只保留 delegation)
- `release_client` 实现 (只保留 delegation)
- 所有 disconnect/evict/cleanup 方法
- `process()` 消息处理循环

### 模块 2: `client_pool.py` (增强到 ~600 行)

**移入内容：**
- `_get_or_create_client` 完整实现 (~80 行)
- `release_client` 完整实现 (~140 行)
- `_attempt_disconnect_client` (~30 行)
- `_disconnect_client_with_timeout` (~20 行)
- `_force_kill_process` (~30 行)
- `_remove_client_state` (~50 行)
- `_cleanup_stale_clients_unlocked` (~30 行)
- `_evict_lru_client_unlocked` (~30 行)
- scavenger 方法 (~60 行)

**当前 client_pool.py**: 487 行
**增加**: ~470 行
**最终**: ~960 行 → 可能需要进一步拆分

**备选方案**：拆分成两个文件：
- `client_pool.py` (~500 行) - 创建和管理
- `client_cleanup.py` (~300 行) - 释放和清理

### 模块 3: `process_executor.py` (新建 ~400 行)

**内容：**
- `ProcessExecutor` 类
- `execute()` - 主流程 (~150 行)
- `_handle_init_message()` (~35 行)
- `_handle_task_started()` (~25 行)
- `_handle_terminal_notification()` (~35 行)
- `_handle_result_message()` (~25 行)
- `_receive_with_boundary()` (~80 行)
- 重试逻辑 (~50 行)

**关键设计**：
- 不持有 backend 引用
- 通过参数接收需要的数据
- 返回结果让 backend 处理

### 模块 4: `session_manager.py` (新建 ~350 行)

**内容：**
- `compact_session()` (~90 行)
- `reset_session()` (~40 行)
- `interrupt_session()` (~50 行)
- `delete_sdk_session()` (~30 行)
- `fork_sdk_session()` (~40 行)
- `list_sdk_sessions()` (~30 行)
- `get_session_commands()` (~30 行)
- `_handle_session_recovery()` (~50 行)

---

## 实施步骤

### Step 1: 清理 legacy 方法
- 删除 `_legacy_release_client` (-147 行)
- 删除 `_legacy_get_or_create_client` (-72 行)
- 修改测试直接测试 ClientPool

**预期：2559 → 2340 行**

### Step 2: 创建 ProcessExecutor
- 提取 `process()` 消息处理逻辑
- 提取 `_receive_with_boundary()`
- 提取 4 个 `_handle_sdk_*` 方法
- 设计低耦合接口

**预期：2340 → 1940 行**

### Step 3: 增强 ClientPool
- 移入客户端管理方法
- 考虑是否拆分为 client_pool.py + client_cleanup.py

**预期：1940 → 1500 行** (如果拆分 cleanup 则更低)

### Step 4: 创建 SessionManager
- 移入会话管理方法

**预期：1500 → 1150 行**

### Step 5: 最终优化
- 确保 claude_sdk_backend.py < 1000 行
- 确保其他模块都 < 1000 行

---

## 预期结果

| 文件 | 最终行数 |
|------|---------|
| `claude_sdk_backend.py` | ~800 行 |
| `client_pool.py` | ~500 行 |
| `client_cleanup.py` | ~300 行 (新) |
| `process_executor.py` | ~400 行 (新) |
| `session_manager.py` | ~350 行 (新) |
| 其他模块 | 保持不变 |

**所有模块 < 1000 行 ✅**

---

## 风险评估

### 高风险
- 测试需要大量修改（测试 ClientPool 而不是 backend）
- ProcessExecutor 的接口设计需要仔细考虑

### 中风险
- 迁移过程中的回归
- 模块间依赖关系需要理清

### 低风险
- 代码移动而非重写
- 保持功能不变