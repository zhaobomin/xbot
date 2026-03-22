# Bug 修复计划

> 审查日期: 2026-03-22
> 审查人: xbot dev
> 最后更新: 2026-03-22
> 状态: 部分完成

## 一、问题总览

| 级别 | 编号 | 问题 | 位置 | 状态 |
|------|------|------|------|------|
| 🔴 P0 | BUG-001 | Compact 通知竞态条件 | `claude_sdk_backend.py` | ✅ 已修复 |
| 🟠 P1 | BUG-002 | SDK Client 重连后上下文丢失 | `claude_sdk_backend.py` | ✅ 已修复 |
| 🟡 P2 | BUG-003 | Session Lock 潜在死锁风险 | `runtime.py` | ✅ 已修复 |
| 🟡 P2 | BUG-004 | Permission Request 超时未通知用户 | `bus/queue.py` | ✅ 已修复 |
| 🟡 P2 | BUG-005 | Outbound Dispatcher 异常处理不完整 | `channels/manager.py` | ✅ 已修复 |
| 🟡 P2 | BUG-006 | Session Context 内存泄漏风险 | `claude_sdk_backend.py` | ✅ 已修复 |
| 🟡 P2 | BUG-007 | Heartbeat 任务堆积风险 | `heartbeat/service.py` | ✅ 已修复 |

**修复进度**: 7/7 (100%) 🎉

---

## 二、已修复问题详情

### ✅ BUG-001: Compact 通知竞态条件

**修复内容**:
- 通过 `message_callback` 机制在 `CompactHookHandler` 中正确发送通知
- 使用 `asyncio.ensure_future()` 替代 `loop.create_task()` 提高兼容性
- 添加多层降级策略处理无 event loop 的情况

**修复文件**: `xbot/agent/backends/claude_sdk_backend.py:431-482`

---

### ✅ BUG-003: Session Lock 潜在死锁风险

**修复内容**:
- 新增 `SessionStateMachine` 类实现状态转换验证
- 定义 `VALID_TRANSITIONS` 字典限制合法状态转换
- 添加 `transition()` 方法验证状态转换合法性

**修复文件**: `xbot/agent/runtime.py:25-201`

---

### ✅ BUG-004: Permission Request 超时未通知用户

**修复内容**:
- 在 `_handle_permission_response` 中增加用户反馈
- 当用户回复时没有待处理的请求，发送友好提示
- 超时或取消时通知用户

**修复文件**: `xbot/agent/runtime.py:284-354`

---

### ✅ BUG-005: Outbound Dispatcher 异常处理不完整

**修复内容**:
- 添加 `except Exception` 捕获通用异常
- 使用 `logger.exception` 记录完整堆栈
- 继续运行 dispatcher，不退出

**修复文件**: `xbot/channels/manager.py:114-152`

---

### ✅ BUG-006: Session Context 内存泄漏风险

**修复内容**:
- 实现 TTL-based 清理机制 (`CLIENT_TTL_SECONDS = 3600`)
- 实现 LRU 驱逐策略 (`MAX_CLIENTS = 100`)
- 添加 `_cleanup_stale_clients_unlocked()` 方法
- 在 `reset_session` 和 `shutdown` 中清理 session contexts

**修复文件**: `xbot/agent/backends/claude_sdk_backend.py:723-997`

---

### ✅ BUG-007: Heartbeat 任务堆积风险

**修复内容**:
- 添加 `_running_tick` 属性追踪当前 tick 任务
- 在 `_run_loop` 中检查上一个任务是否仍在运行
- 若任务仍在运行，跳过本次迭代并记录警告

**修复文件**: `xbot/heartbeat/service.py:65-82, 150-171`

---

### ✅ BUG-002: SDK Client 重连后上下文丢失

**修复内容**:
- 异常时不立即清理 `sdk_session_id`，而是标记会话为"待恢复"状态
- 保存错误信息（`_last_error`、`_error_timestamp`、`_fallback_error`）供调试
- 下次请求时检查 `_reconnect_pending` 标记，发送恢复提示
- 向用户发送友好的错误提示，告知可以继续对话

**修复文件**: `xbot/agent/backends/claude_sdk_backend.py:1316-1430`

**修复代码要点**:
```python
# 异常处理中 - 不清理 sdk_session_id
if session is not None:
    session.metadata["_reconnect_pending"] = True
    session.metadata["_last_error"] = str(e)[:500]
    session.metadata["_error_timestamp"] = datetime.now().isoformat()
    self.sessions.save(session)

# 下次请求时 - 检查并处理恢复状态
if session is not None and session.metadata.pop("_reconnect_pending", None):
    if session.metadata.get("sdk_session_id"):
        reconnect_hint = "🔄 正在尝试恢复之前的会话上下文..."
```

---

## 四、修复历史

| 日期 | 修复内容 | 提交 |
|------|----------|------|
| 2026-03-22 | Client Pool 内存泄漏、Permission 响应竞态、Session 状态机、统一异常、Prometheus 指标 | b1f4dca |
| 2026-03-22 | Outbound Dispatcher 异常处理、Heartbeat 任务跳过、Compact 通知增强 | (本次修复) |
| 2026-03-22 | SDK Client 重连后上下文恢复、Session State Machine reason 更新 | (本次修复) |

---

## 五、测试策略

### 单元测试

- ✅ `test_outbound_dispatcher_error_recovery.py` - 测试 dispatcher 异常恢复
- ✅ `test_heartbeat_skip_mechanism.py` - 测试 heartbeat 跳过机制（通过其他测试覆盖）
- ✅ `test_claude_sdk_backend.py` - 测试 SDK backend 各种场景

### 集成测试

```bash
pytest tests/ -v --cov=xbot -k "dispatcher or heartbeat or compact or reconnect"
```

### 回归测试

```bash
pytest tests/ -v --cov=xbot
```

### 测试结果

```
============================== 929 passed, 2 warnings in 27.78s ==============================
```