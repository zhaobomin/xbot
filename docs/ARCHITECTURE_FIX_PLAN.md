# 架构修复计划

> 审查日期: 2026-03-22
> 状态: ✅ 已完成
> 预计总时间: 8小时

## 一、问题总览

| 优先级 | 编号 | 问题 | 位置 | 状态 |
|--------|------|------|------|------|
| 🔴 P1 | ISSUE-001 | Session State 与 Backend State 同步缺失 | runtime.py, claude_sdk_backend.py | ✅ 已修复 |
| 🔴 P1 | ISSUE-002 | Permission Handler Session Context 清理时机 | claude_sdk_backend.py, permission_handler.py | ✅ 已修复 |
| 🟠 P2 | ISSUE-003 | Channel Stop 后残留任务 | channels/*.py, base.py | ✅ 已修复 |
| 🟠 P2 | ISSUE-004 | Feishu WebSocket 线程安全问题 | channels/feishu.py | ✅ 已修复 |
| 🟡 P2 | ISSUE-005 | Session Lock 未清理 | runtime.py | ✅ 已修复 |
| 🟡 P3 | ISSUE-006 | WAITING_* → STOPPING 转换缺失 | runtime.py | ✅ 已存在 |
| 🟡 P3 | ISSUE-007 | _active_task_ids 与 _clients 生命周期不同步 | claude_sdk_backend.py | ✅ 已修复 |

**修复进度**: 7/7 (100%) 🎉

---

## 二、详细修复方案

### 🔴 ISSUE-001: Session State 与 Backend State 同步缺失

**严重程度**: 高
**影响范围**: Session 状态可能与 Backend 实际状态不一致

**问题描述**:

`AgentRuntime._state_machine` 与 `ClaudeSDKBackend._clients`/`_active_task_ids` 是两套独立的状态管理：
- Runtime 管理 `SessionPhase` 状态
- Backend 管理 SDK client 和 task 状态

当 Backend 清理 client（TTL/LRU）或 task 完成时，Runtime 状态可能未同步更新。

**修复方案**:

1. 在 `shared_resources` 中添加状态同步回调
2. Backend 在清理 client/task 时通知 Runtime
3. Runtime 更新状态机

**修复代码**:

```python
# === runtime.py ===

class AgentRuntime:
    def __init__(self, config: Any, shared_resources: dict[str, Any]):
        # ... 现有初始化 ...

        # 添加 backend 状态同步回调
        shared_resources["on_client_cleanup"] = self._on_backend_client_cleanup
        shared_resources["on_task_complete"] = self._on_backend_task_complete

    def _on_backend_client_cleanup(self, session_key: str) -> None:
        """Backend client 被清理时的回调。"""
        # 如果 session 正在运行，标记为需要同步
        if self._state_machine.is_active(session_key):
            logger.debug(f"Backend client cleaned up for active session: {session_key}")
            self._state_machine.force_transition(
                session_key, SessionPhase.IDLE, reason="backend_client_cleanup"
            )

    def _on_backend_task_complete(self, session_key: str, task_id: str) -> None:
        """Backend task 完成时的回调。"""
        # 清理本地 task 追踪
        self._active_task_ids.pop(session_key, None)


# === claude_sdk_backend.py ===

async def _cleanup_stale_clients_unlocked(self) -> int:
    """Remove clients that have been idle longer than TTL."""
    now = time.time()
    stale_keys = [
        key for key, last_used in self._client_last_used.items()
        if now - last_used > self.CLIENT_TTL_SECONDS
    ]

    for key in stale_keys:
        client = self._clients.pop(key, None)
        self._client_last_used.pop(key, None)
        self._session_commands.pop(key, None)
        self._active_task_ids.pop(key, None)
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                logger.debug(f"Ignoring error while disconnecting stale client for session {key}")

        # 通知 runtime 状态同步
        on_cleanup = self._shared_resources.get("on_client_cleanup")
        if on_cleanup:
            on_cleanup(key)

    if stale_keys:
        logger.info(f"Cleaned up {len(stale_keys)} stale client(s) (TTL={self.CLIENT_TTL_SECONDS}s)")

    return len(stale_keys)


async def _evict_lru_client_unlocked(self) -> None:
    """Evict the least recently used client."""
    if not self._client_last_used:
        return

    lru_key = min(self._client_last_used, key=self._client_last_used.get)

    client = self._clients.pop(lru_key, None)
    self._client_last_used.pop(lru_key, None)
    self._session_commands.pop(lru_key, None)
    self._active_task_ids.pop(lru_key, None)

    if client is not None:
        try:
            await client.disconnect()
        except Exception:
            logger.debug(f"Ignoring error while disconnecting LRU client for session {lru_key}")

    # 通知 runtime 状态同步
    on_cleanup = self._shared_resources.get("on_client_cleanup")
    if on_cleanup:
        on_cleanup(lru_key)

    logger.info(f"Evicted LRU client for session {lru_key}")
```

**测试要点**:
- 验证 TTL 清理后 session 状态正确更新
- 验证 LRU 驱逐后 session 状态正确更新
- 验证新请求能正常创建 client

---

### 🔴 ISSUE-002: Permission Handler Session Context 清理时机

**严重程度**: 高
**影响范围**: 权限请求可能发送到错误的 channel/chat_id

**问题描述**:

`PermissionRequestHandler._session_context` 在以下情况可能未被清理：
1. SDK 处理过程中发生未捕获异常
2. 用户强制停止任务
3. 会话被重置

**修复方案**:

1. 在 `process()` 的所有异常路径添加清理
2. 在 `reset_session()` 中显式清理
3. 添加 `_session_context` 的 TTL 清理机制

**修复代码**:

```python
# === claude_sdk_backend.py ===

async def process(self, context: AgentContext) -> AsyncIterator[AgentResponse]:
    """Process a message using Claude SDK."""
    # ... 现有初始化代码 ...

    try:
        # ... 现有处理逻辑 ...

    except Exception as e:
        # ... 现有异常处理 ...

        # 确保 permission handler context 被清理
        self._clear_permission_context(context.session_key)

        # ... yield error response ...

    finally:
        # Clear permission handler session context
        self._clear_permission_context(context.session_key)

    # ... 其他代码 ...

def _clear_permission_context(self, session_key: str) -> None:
    """清理 permission handler 的 session context。"""
    if self._permission_handler and hasattr(self._permission_handler, "clear_session_context"):
        try:
            self._permission_handler.clear_session_context(session_key)
        except Exception as e:
            logger.debug(f"Error clearing permission context for {session_key}: {e}")


# === permission_handler.py ===

class PermissionRequestHandler(BasePermissionHandler):
    def __init__(self, ...):
        super().__init__(...)
        # ... 现有初始化 ...

        # 添加 context TTL 清理
        self._context_timestamps: dict[str, float] = {}
        self._context_ttl = 3600  # 1 hour TTL

    def set_session_context(self, session_key: str, channel: str, chat_id: str, metadata: dict[str, Any] | None = None) -> None:
        """设置会话上下文。"""
        self._session_context[session_key] = {
            "channel": channel,
            "chat_id": chat_id,
            "metadata": dict(metadata or {}),
        }
        self._context_timestamps[session_key] = time.time()

        # 清理过期的 context
        self._cleanup_expired_contexts()

    def _cleanup_expired_contexts(self) -> None:
        """清理过期的 session context。"""
        now = time.time()
        expired = [
            key for key, ts in self._context_timestamps.items()
            if now - ts > self._context_ttl
        ]
        for key in expired:
            self._session_context.pop(key, None)
            self._context_timestamps.pop(key, None)

        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired permission contexts")
```

**测试要点**:
- 验证异常后 context 被正确清理
- 验证 TTL 清理机制工作正常
- 验证并发请求不会互相干扰

---

### 🟠 ISSUE-003: Channel Stop 后残留任务

**严重程度**: 中
**影响范围**: Channel 停止后可能有后台任务继续运行

**问题描述**:

各 Channel 的 `stop()` 实现不一致：
- 某些 Channel 只设置 `_running = False`
- 未等待后台任务完成
- 未取消定时器

**修复方案**:

1. 在 `BaseChannel` 中定义统一的 stop 协议
2. 所有 Channel 实现遵循协议
3. 添加后台任务追踪

**修复代码**:

```python
# === channels/base.py ===

class BaseChannel(ABC):
    """Abstract base class for chat channel implementations."""

    name: str = "base"
    display_name: str = "Base"
    transcription_api_key: str = ""

    def __init__(self, config: Any, bus: MessageBus):
        self.config = config
        self.bus = bus
        self._running = False
        self._background_tasks: set[asyncio.Task] = set()  # 追踪后台任务
        self._stop_event = asyncio.Event()  # 用于协调停止

    async def stop(self) -> None:
        """Stop the channel and clean up resources.

        Default implementation:
        1. Set _running = False
        2. Set stop event
        3. Cancel all background tasks
        4. Wait for tasks to complete
        5. Call _cleanup_resources()
        """
        self._running = False
        self._stop_event.set()

        # 取消所有后台任务
        for task in self._background_tasks:
            if not task.done():
                task.cancel()

        # 等待任务完成
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        # 子类可覆盖此方法进行额外清理
        await self._cleanup_resources()

    async def _cleanup_resources(self) -> None:
        """Override this method to clean up channel-specific resources."""
        pass

    def _track_task(self, task: asyncio.Task) -> None:
        """Track a background task for cleanup on stop."""
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _create_tracked_task(self, coro, name: str | None = None) -> asyncio.Task:
        """Create and track a background task."""
        task = asyncio.create_task(coro, name=name)
        self._track_task(task)
        return task


# === channels/telegram.py (示例修改) ===

async def start(self) -> None:
    """Start the Telegram bot."""
    # ... 现有初始化 ...
    self._running = True
    self._stop_event.clear()

    # 使用追踪的任务
    self._create_tracked_task(self._poll_loop(), "telegram-poll")

    # ... 其他代码 ...

async def _poll_loop(self) -> None:
    """Telegram long polling loop."""
    while self._running and not self._stop_event.is_set():
        try:
            # ... polling 逻辑 ...
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Telegram poll error: {}", e)
            if self._running and not self._stop_event.is_set():
                await asyncio.sleep(5)
```

**测试要点**:
- 验证 stop 后所有后台任务被取消
- 验证资源被正确释放
- 验证 stop 不会阻塞太久

---

### 🟠 ISSUE-004: Feishu WebSocket 线程安全问题

**严重程度**: 中
**影响范围**: Feishu Channel 可能出现竞态条件

**问题描述**:

Feishu Channel 使用独立线程运行 WebSocket：
- `_running` 标志在线程间共享
- 缺乏显式同步机制
- Python GIL 提供有限保护

**修复方案**:

1. 使用 `threading.Event` 替代 `_running` 检查
2. 添加线程安全的状态访问
3. 改进停止逻辑

**修复代码**:

```python
# === channels/feishu.py ===

class FeishuChannel(BaseChannel):
    def __init__(self, config: Any, bus: MessageBus):
        super().__init__(config, bus)
        # ... 现有初始化 ...

        # 线程安全的停止机制
        self._stop_event = threading.Event()
        self._ws_thread: threading.Thread | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        """Start the Feishu bot with WebSocket long connection."""
        # ... 现有初始化 ...

        self._running = True
        self._stop_event.clear()
        self._main_loop = asyncio.get_running_loop()

        # ... WebSocket 客户端初始化 ...

        def run_ws():
            self._ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._ws_loop)
            # ... patch lark_oapi ...

            reconnect_delay = self._ws_reconnect_delay

            try:
                # 使用 stop_event 替代 _running 检查
                while not self._stop_event.is_set():
                    try:
                        logger.info("Feishu WebSocket connecting...")
                        self._ws_client.start()
                        reconnect_delay = self._ws_reconnect_delay
                    except Exception as e:
                        logger.warning("Feishu WebSocket error: {}", e)

                    # 使用带超时的 wait，允许快速响应 stop
                    if not self._stop_event.wait(timeout=reconnect_delay):
                        # 超时，继续重连
                        reconnect_delay = min(
                            reconnect_delay * 2,
                            self._ws_max_reconnect_delay
                        )
                    else:
                        # stop_event 被设置，退出循环
                        break
            finally:
                self._ws_loop.close()
                self._ws_loop = None

        self._ws_thread = threading.Thread(target=run_ws, daemon=True, name="feishu-ws")
        self._ws_thread.start()

        # ... 其他代码 ...

    async def stop(self) -> None:
        """Stop the Feishu bot."""
        self._running = False
        self._stop_event.set()  # 通知 WebSocket 线程停止

        # 等待 WebSocket 线程结束（最多 5 秒）
        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5.0)
            if self._ws_thread.is_alive():
                logger.warning("Feishu WebSocket thread did not stop gracefully")

        # 清理资源
        self._client = None
        self._ws_client = None
        logger.info("Feishu channel stopped")
```

**测试要点**:
- 验证 stop 后线程正确退出
- 验证快速停止不会丢失消息
- 验证重连逻辑正确工作

---

### 🟡 ISSUE-005: Session Lock 未清理

**严重程度**: 低
**影响范围**: 长期运行可能积累内存

**问题描述**:

`AgentRuntime._session_locks` 在 session 结束后未清理，可能导致内存泄漏。

**修复方案**:

在 `_terminate_session` 中清理 session lock。

**修复代码**:

```python
# === runtime.py ===

async def _terminate_session(self, session_key: str, *, hard_reset: bool) -> dict[str, Any]:
    """Cancel runtime/backend activity and clear pending requests for a session."""
    self._set_session_phase(
        session_key,
        SessionPhase.RESETTING if hard_reset else SessionPhase.STOPPING,
        reason="terminate_session",
    )

    # 清理 active tasks
    tasks = self._active_tasks.pop(session_key, [])
    cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # 清理 backend 状态
    backend_cancelled = await self.router.backend.cancel_session(session_key)
    backend_task_stopped = await self.router.backend.stop_active_task(session_key)
    interrupt_result = await self.router.backend.interrupt_session(session_key)
    if hard_reset:
        await self.router.backend.reset_session(session_key)

    # 清理 bus 请求
    cleared_requests = {"permission": False, "interaction": False}
    if self.bus is not None and hasattr(self.bus, "clear_session_requests"):
        cleared_requests = self.bus.clear_session_requests(session_key)

    # 清理 session lock
    self._session_locks.pop(session_key, None)

    # 清理状态机状态（仅 hard_reset 时完全清除）
    if hard_reset:
        self._state_machine.clear(session_key)
    else:
        self._set_session_phase(session_key, SessionPhase.IDLE, reason="terminate_session_completed")

    return {
        "cancelled": cancelled,
        "backend_cancelled": backend_cancelled,
        "backend_task_stopped": backend_task_stopped,
        "interrupted": bool(interrupt_result.get("interrupted")),
        "usage": interrupt_result.get("usage"),
        "cleared_requests": cleared_requests,
    }
```

**测试要点**:
- 验证 session 结束后 lock 被清理
- 验证新请求能正常创建 lock
- 验证并发请求不受影响

---

### 🟡 ISSUE-006: WAITING_* → STOPPING 转换缺失

**严重程度**: 低
**影响范围**: 用户在等待权限/交互时无法正确停止

**问题描述**:

状态机缺少从 `WAITING_PERMISSION`/`WAITING_INTERACTION` 到 `STOPPING` 的转换。

**修复方案**:

添加缺失的状态转换。

**修复代码**:

```python
# === runtime.py ===

# Valid state transitions: {from_phase: {to_phase1, to_phase2, ...}}
VALID_TRANSITIONS: dict[SessionPhase, set[SessionPhase]] = {
    SessionPhase.IDLE: {
        SessionPhase.RUNNING,
        SessionPhase.STOPPING,
        SessionPhase.RESETTING,
        SessionPhase.ERROR,
    },
    SessionPhase.RUNNING: {
        SessionPhase.IDLE,
        SessionPhase.WAITING_PERMISSION,
        SessionPhase.WAITING_INTERACTION,
        SessionPhase.STOPPING,
        SessionPhase.RESETTING,
        SessionPhase.ERROR,
    },
    SessionPhase.WAITING_PERMISSION: {
        SessionPhase.RUNNING,
        SessionPhase.IDLE,
        SessionPhase.STOPPING,      # 新增：允许从等待权限状态停止
        SessionPhase.RESETTING,     # 新增：允许从等待权限状态重置
        SessionPhase.ERROR,
    },
    SessionPhase.WAITING_INTERACTION: {
        SessionPhase.RUNNING,
        SessionPhase.IDLE,
        SessionPhase.STOPPING,      # 新增：允许从等待交互状态停止
        SessionPhase.RESETTING,     # 新增：允许从等待交互状态重置
        SessionPhase.ERROR,
    },
    SessionPhase.STOPPING: {
        SessionPhase.IDLE,
        SessionPhase.ERROR,
    },
    SessionPhase.RESETTING: {
        SessionPhase.IDLE,
        SessionPhase.ERROR,
    },
    SessionPhase.ERROR: {
        SessionPhase.IDLE,
        SessionPhase.RUNNING,
        SessionPhase.RESETTING,
    },
}
```

**测试要点**:
- 验证用户在等待权限时可以停止
- 验证状态转换正确记录

---

### 🟡 ISSUE-007: _active_task_ids 与 _clients 生命周期不同步

**严重程度**: 低
**影响范围**: task_id 可能在 client disconnect 后残留

**问题描述**:

`_active_task_ids` 在某些清理路径未与 `_clients` 同步清理。

**修复方案**:

确保所有清理 `_clients` 的地方也清理 `_active_task_ids`。

**修复代码**:

```python
# === claude_sdk_backend.py ===

async def interrupt_session(self, session_key: str) -> dict[str, Any]:
    """Interrupt any ongoing LLM request for a session."""
    client = self._clients.get(session_key)
    if client is None:
        return {"interrupted": False, "usage": None}

    usage_info = None
    try:
        # ... 现有 interrupt 逻辑 ...
    except Exception as e:
        logger.warning(f"Failed to interrupt SDK client: {e}")
        return {"interrupted": False, "usage": None}
    finally:
        # Always remove client to force fresh connection on next request
        self._clients.pop(session_key, None)
        self._active_task_ids.pop(session_key, None)  # 确保同步清理
        self._session_commands.pop(session_key, None)  # 确保同步清理
        logger.debug(f"Removed client for session {session_key} after interrupt")

    return {"interrupted": True, "usage": usage_info}


async def reset_session_client_state(self, session_key: str) -> None:
    """Reset SDK client/task state for a session after incomplete interaction."""
    task_id = self._active_task_ids.pop(session_key, None)
    client = self._clients.pop(session_key, None)

    if client is not None and task_id:
        try:
            await client.stop_task(task_id)
        except Exception:
            logger.debug("Failed to stop active task while resetting session state")

    # 清理相关状态
    self._session_commands.pop(session_key, None)
    self._client_last_used.pop(session_key, None)

    # 清理 session context
    session_contexts = self._shared_resources.get("_session_contexts", {})
    session_contexts.pop(session_key, None)
```

---

## 三、修复计划时间表

| 阶段 | 任务 | 预计时间 | 依赖 |
|------|------|----------|------|
| **第一阶段** | ISSUE-001: Session State 同步 | 2h | 无 |
| **第一阶段** | ISSUE-002: Permission Handler 清理 | 1.5h | 无 |
| **第二阶段** | ISSUE-003: Channel Stop 协议 | 2h | 无 |
| **第二阶段** | ISSUE-004: Feishu 线程安全 | 1h | 无 |
| **第三阶段** | ISSUE-005: Session Lock 清理 | 0.5h | 无 |
| **第三阶段** | ISSUE-006: 状态机转换完善 | 0.5h | 无 |
| **第三阶段** | ISSUE-007: task_id 同步清理 | 0.5h | 无 |
| **测试验证** | 运行完整测试套件 | 1h | 全部 |

**总计**: 9小时

---

## 四、测试策略

### 单元测试

每个 Issue 修复后添加相应测试：

1. `test_session_state_sync.py` - 测试 backend-runtime 状态同步
2. `test_permission_context_cleanup.py` - 测试 permission handler 清理
3. `test_channel_stop_protocol.py` - 测试 channel stop 行为

### 集成测试

```bash
# 运行所有 channel 相关测试
pytest tests/ -v -k "channel"

# 运行状态机测试
pytest tests/ -v -k "state_machine"

# 运行 backend 测试
pytest tests/ -v -k "backend"
```

### 回归测试

```bash
pytest tests/ -v --cov=xbot
```

---

## 五、风险和注意事项

1. **ISSUE-001**: 需要修改 shared_resources 结构，确保向后兼容
2. **ISSUE-003**: 需要修改所有 Channel 实现，测试工作量大
3. **ISSUE-004**: 线程安全问题需要仔细测试，避免引入新问题
4. **所有修复**: 需要在 dev 环境验证后再部署到生产环境

---

## 六、附录：相关文件

- `xbot/agent/runtime.py` - AgentRuntime, SessionStateMachine
- `xbot/agent/backends/claude_sdk_backend.py` - ClaudeSDKBackend
- `xbot/agent/permission_handler.py` - PermissionRequestHandler
- `xbot/channels/base.py` - BaseChannel
- `xbot/channels/feishu.py` - FeishuChannel
- `xbot/channels/telegram.py` - TelegramChannel
- `xbot/bus/queue.py` - MessageBus