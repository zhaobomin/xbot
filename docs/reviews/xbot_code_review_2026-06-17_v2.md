# xbot 全量代码审查报告 (v2)

**日期**: 2026-06-17
**审查范围**: xbot/ 下全部 Python 源文件 (153 文件, ~41,500 行)
**审查方式**: 逐文件人工审查 + codegraph 知识图谱辅助

---

## P0 — 致命 Bug（安全漏洞 / 数据损坏）

### Bug #1 — `_set_sdk_session_id_impl` 内存泄漏
- **文件**: `xbot/runtime/state/machine.py:170-174`
- **描述**: 覆盖 `sdk_session_id` 时，从不清理 `_state_meta` 字典中旧的 sdk_id 键。每次 session 重新获取 SDK session，旧条目永远残留，`_state_meta` 字典无界增长，长生命周期服务中最终导致内存泄漏。
- **对比**: `runtime_registry.py:52-58` 的同名方法正确地先 `pop(old, None)` 再写入，`machine.py` 遗漏了这一步。
- **问题代码**:
```python
def _set_sdk_session_id_impl(self, session_key: str, sdk_id: str | None) -> None:
    state = self._get_or_create_state(session_key)
    state.sdk_session_id = sdk_id
    if sdk_id:
        self._get_state_meta()[sdk_id] = state  # 旧 sdk_id 未清理
```
- **修复**:
```python
def _set_sdk_session_id_impl(self, session_key: str, sdk_id: str | None) -> None:
    state = self._get_or_create_state(session_key)
    old = state.sdk_session_id
    if old:
        self._get_state_meta().pop(old, None)
    state.sdk_session_id = sdk_id
    if sdk_id:
        self._get_state_meta()[sdk_id] = state
```

---

### Bug #2 — `resolve_sdk_session_id` 查错字典
- **文件**: `xbot/runtime/state/machine.py:176-178`
- **描述**: `resolve_sdk_session_id` 用 `session_key` 去查 `_state_meta`（以 sdk_id 为键），但应该返回的是 state 对象中存储的 `sdk_session_id`。此方法始终返回 `None`（除非 session_key 恰好等于某个 sdk_id），导致所有依赖它的 `drop_sdk_context` 路径失效。
- **问题代码**:
```python
def resolve_sdk_session_id(self, session_key: str) -> str | None:
    state = self._get_state_meta().get(session_key)  # 用 session_key 查 sdk_id 索引
    return state.sdk_session_id if state else None
```
- **修复**:
```python
def resolve_sdk_session_id(self, session_key: str) -> str | None:
    state = self._sessions  # 应查 sessions dict
    # 正确方式：先从 _get_state_meta 以 session_key 取 state，再返回 state.sdk_session_id
    meta = self._get_state_meta()
    state = meta.get(session_key)
    return state.sdk_session_id if state else None
```

---

## P1 — 功能错误（可能导致崩溃 / 静默失败）

### Bug #3 — `compact()` 未重置 `_metadata_dirty`
- **文件**: `xbot/runtime/session/conversation_store.py:456-475`
- **描述**: `compact()` 调用 `_save_full()` 做全量重写后，没有重置 `session._metadata_dirty = False`。`save()` 在 :372 正确清除了，但 `compact()` 遗漏。后果：compact 后的每次 `save()` 都会误判为"元数据变更"，触发不必要的全量文件重写而非追加，长会话中造成频繁 I/O。
- **问题代码**:
```python
def compact(self, session):
    with self._file_lock(path, exclusive=True):
        self._save_full(session, path)
        session._new_messages.clear()
        # 缺少：session._metadata_dirty = False
```
- **修复**: 在 `session._new_messages.clear()` 后添加 `session._metadata_dirty = False`

---

### Bug #4 — `delete()` 静默忽略 `delete_sdk_file` 参数
- **文件**: `xbot/runtime/state/runtime_registry.py:52-58`
- **描述**: `delete()` 接受 `delete_sdk_file: bool` 参数但立即丢弃（`_ = delete_sdk_file`），不做任何 SDK session 文件清理。调用方传入 `delete_sdk_file=True` 时误以为 SDK 侧资源也被回收，实际上只有内存索引被清理。
- **修复**: 实现 SDK 文件清理逻辑，或移除参数并在文档中说明。

---

### Bug #5 — heartbeat `tool_calls[0]` 未做长度检查
- **文件**: `xbot/runtime/system/heartbeat/service.py:150-156`
- **描述**: `has_tool_calls` 为 `True` 后直接访问 `response.tool_calls[0].arguments`。若 provider 实现 `has_tool_calls` 返回 True 但 `tool_calls` 为空列表（不同 LLM provider 行为不一致），会抛 `IndexError`。外层 `_tick` 的 `except Exception` 会捕获，但导致该心跳周期静默失败。
- **问题代码**:
```python
if response is None or not getattr(response, "has_tool_calls", False):
    return "skip", ""
args = response.tool_calls[0].arguments  # 可能 IndexError
```
- **修复**:
```python
tool_calls = getattr(response, "tool_calls", None) or []
if not tool_calls:
    return "skip", ""
args = tool_calls[0].arguments
```

---

### Bug #6 — readiness 探针对 dict 类型状态值误判
- **文件**: `xbot/runtime/system/monitoring/health.py:159-176`
- **描述**: `_handle_ready` 用 `agent_status == "running"` 判断就绪状态。但 `update_status()` 签名为 `status: dict[str, Any] | str`——如果调用方传入 dict，字符串比较恒为 False，就绪探针永远返回 503，K8s/负载均衡器永不路由流量。
- **修复**: 比较前归一化：`isinstance(agent_status, dict)` 时取 `agent_status.get("state")`

---

### Bug #7 — `client_pool.py` `disconnect()` 锁内做网络 I/O
- **文件**: `xbot/runtime/core/client_pool.py:202-217`
- **描述**: `disconnect()` 方法在 `async with self._lock:` 内执行 `await asyncio.wait_for(record.client.disconnect(), timeout=10.0)`。这个网络 I/O 操作可能耗时 10 秒，期间所有其他协程（包括 `get_or_create`）无法获取锁，导致整个 session 假死。
- **修复**: 先在锁内取出 record 并从 dict 删除，在锁外执行 disconnect：
```python
async def disconnect(self, session_key):
    async with self._lock:
        record = self._clients.pop(session_key, None)
    if record is None:
        return True
    try:
        await asyncio.wait_for(record.client.disconnect(), timeout=10.0)
    except Exception:
        ...
    return True
```

---

## P2 — 潜在问题 / 代码质量

### Bug #8 — `dispatch()` 每次调用重建静态字典
- **文件**: `xbot/runtime/state/machine.py:101-120`
- **描述**: `event_targets` 字典内容不变，但每次 `dispatch` 调用都在函数体内重新分配。`coordinator.py` 中已提取为模块级常量。
- **修复**: 提升为模块级常量 `_LEGACY_EVENT_TARGET`

---

### Bug #9 — `AlertRule.cooldown_seconds` 死代码
- **文件**: `xbot/runtime/system/monitoring/alerting.py:22-29`
- **描述**: `AlertRule` 数据类定义了 `cooldown_seconds` 字段暗示 per-rule 冷却时间，但 `_reserve_alert_slot` 只使用全局 `self.config.cooldown_seconds`。`AlertRule` 从未被实例化。
- **修复**: 移除 `AlertRule` 或实现规则注册机制

---

### Bug #10 — `memory/store.py` `_format_messages` 中 `content` 可能为非字符串
- **文件**: `xbot/memory/store.py:154-178`
- **描述**: 第 172 行 `content = has_content if has_content else ...`——`has_content` 来自 `message.get("content")`，可能是 list/dict（多模态消息），直接 `f"...{content}"` 会产生不可读的输出。不会崩溃但日志/grep 不可用。
- **修复**: 对非字符串 content 做截断处理：
```python
if isinstance(content, (list, dict)):
    content = f"({type(content).__name__}: {len(str(content))} chars)"
```

---

### Bug #11 — `crew/orchestrator.py` LLM repair 使用同步线程 + `asyncio.run`
- **文件**: `xbot/crew/orchestrator.py:335-363`
- **描述**: `repair_callable` 在子线程中调用 `loop.run_until_complete(_init_and_call(prompt))`，创建全新 `AgentService` 实例。每次 repair 都重新初始化整个 service（包括 ClientPool、ContextBuilder、MemoryConsolidator），开销巨大。且线程 `join(timeout=120)` 超时后线程仍在后台运行，不会真正停止。
- **修复**: 考虑复用外层 service 实例，或在 `repair_callable` 中使用 `asyncio.to_thread` 替代手动线程管理。

---

### Bug #12 — `_LOGIN_ATTEMPTS` 无时间清理
- **文件**: `xbot/interfaces/gateway/app.py:121-162`
- **描述**: `_LOGIN_ATTEMPTS` 是模块级全局 `OrderedDict`，只在登录失败时追加、在 `_MAX_TRACKED_IPS` 溢出时按 LRU 淘汰。没有定时清理机制——成功登录的 IP 条目永远残留。长期运行的公网 gateway 可能积累大量过期条目（虽然有 `_MAX_TRACKED_IPS=10000` 上限，但会占用不必要的内存）。
- **修复**: 添加定期清理，或在 `_active_login_attempts` 返回空列表时自动清理。

---

### Bug #13 — `service.py` `_async_consolidation_tasks` / `_async_registry_tasks` 集合无限增长
- **文件**: `xbot/runtime/core/service.py:154-155`
- **描述**: `_async_consolidation_tasks` 和 `_async_registry_tasks` 是 `set[asyncio.Task]`，只在 `shutdown()` 时清理。运行期间，完成的 task 对象永远留在集合中（虽然 task 完成后 Python GC 可能回收 task 对象，但 set 中的引用阻止了回收）。长生命周期服务中可能累积数百个已完成 task 引用。
- **修复**: 给 task 添加 done callback 自动从集合移除：
```python
task = asyncio.create_task(coro)
self._async_consolidation_tasks.add(task)
task.add_done_callback(self._async_consolidation_tasks.discard)
```

---

### Bug #14 — `tools/web.py` Brave/Tavily/SearXNG search 未使用 SSRF 防护的 transport
- **文件**: `xbot/tools/web.py:209-269`
- **描述**: `_search_brave`、`_search_tavily`、`_search_searxng` 使用 `httpx.AsyncClient(proxy=self.proxy)` 直连 API，没有使用 `_PinnedAsyncHTTPTransport` 做 DNS pinning。如果代理配置不当或 DNS 被劫持，可能将 API Key 发送到恶意服务器。`_fetch_readability` 正确使用了 pinned transport。
- **修复**: 对搜索 API 调用也使用 `_PinnedAsyncHTTPTransport` 或至少验证 `validate_url_safe()`。

---

## 审查通过（代码质量良好）的模块

| 模块 | 说明 |
|---|---|
| `runtime/core/protocol.py` | 纯数据类，无逻辑 |
| `runtime/core/types.py` | 纯类型定义 |
| `runtime/core/mcp_config.py` | 环境变量展开逻辑正确 |
| `runtime/core/task_supervisor.py` | 后台任务管理完善 |
| `runtime/core/hooks.py` | hook 处理逻辑清晰 |
| `runtime/core/context/builder.py` | ContextBuilder 逻辑完善 |
| `runtime/core/context/commands.py` | 命令加载有路径遍历防护 |
| `platform/bus/queue.py` | MessageBus 请求/响应流程完整，超时清理正确 |
| `platform/security/network.py` | SSRF 防护完整，包含 IPv4-mapped IPv6 检测 |
| `tools/shell.py` | 命令安全守卫完善（obfuscation 检测、路径遍历检测） |
| `tools/filesystem.py` | 文件操作安全（路径限制、大小限制） |
| `interfaces/gateway/auth.py` | 认证安全（bcrypt、JWT、速率限制、密码自动生成） |
| `interfaces/gateway/app.py` | 名称验证防注入、ZIP 路径遍历防护、WebSocket 认证 |
| `channels/manager.py` | Channel 管理逻辑清晰 |
| `channels/base.py` | BaseChannel 抽象合理 |

---

## Bug 汇总与修复状态

| # | 严重度 | 文件 | 标题 | 状态 |
|---|--------|------|------|------|
| 1 | **P0** | `runtime/state/machine.py:170` | `_set_sdk_session_id_impl` 内存泄漏 | ✅ 已修复 |
| 2 | ~~P0~~ | `runtime/state/machine.py:176` | `resolve_sdk_session_id` | ❌ 误判（`_state_meta` 用 session_key 和 sdk_id 双键索引，逻辑正确） |
| 3 | **P1** | `runtime/session/conversation_store.py:456` | `compact()` 未重置 `_metadata_dirty` | ✅ 已修复 |
| 4 | P1 | `runtime/state/runtime_registry.py:52` | `delete()` 静默忽略 `delete_sdk_file` | ⏸️ 需要设计决策，暂不修复 |
| 5 | **P1** | `runtime/system/heartbeat/service.py:150` | heartbeat `tool_calls[0]` 未做长度检查 | ✅ 已修复 |
| 6 | **P1** | `runtime/system/monitoring/health.py:159` | readiness 探针对 dict 状态误判 | ✅ 已修复 |
| 7 | **P1** | `runtime/core/client_pool.py:202` | `disconnect()` 锁内做网络 I/O 导致假死 | ✅ 已修复 |
| 8 | P2 | `runtime/state/machine.py:101` | `dispatch()` 每次重建静态字典 | ✅ 已修复（提升为模块级常量） |
| 9 | P2 | `runtime/system/monitoring/alerting.py:22` | `AlertRule.cooldown_seconds` 死代码 | ⏸️ 预留扩展，暂不修复 |
| 10 | P2 | `memory/store.py:172` | `_format_messages` content 可能为非字符串 | ✅ 已修复 |
| 11 | P2 | `crew/orchestrator.py:335` | LLM repair 每次重建整个 AgentService | ⏸️ 需要重构，暂不修复 |
| 12 | P2 | `interfaces/gateway/app.py:121` | `_LOGIN_ATTEMPTS` 无定期清理 | ⏸️ 已有 LRU 淘汰机制，暂不修复 |
| 13 | ~~P2~~ | `runtime/core/service.py:154` | 异步 task 集合无限增长 | ❌ 误判（line 3245/3373 已有 done callback 自动清理） |
| 14 | P2 | `tools/web.py:209-269` | 搜索 API 未使用 DNS pinning transport | ⏸️ 需要重构，暂不修复 |

## 修复统计

- **实际 bug**: 12 个（排除 2 个误判）
- **已修复**: 7 个（P0×1、P1×4、P2×2）
- **需要设计决策/重构**: 5 个（暂不修复）
