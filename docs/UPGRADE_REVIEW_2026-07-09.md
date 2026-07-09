# 升级代码审查报告：v2.0.14 → v2.0.29

> 审查日期：2026-07-09
> 审查范围：`git diff v2.0.14..HEAD`（24 个提交，509 文件变更）
> 审查方式：只读审查，未修改任何代码
> 审查覆盖：后端运行时（runtime core）、平台（platform）、工具（tools）、系统与网关（systems + gateway）。前端重写、package-lock、Tauri、docs 的 churn 已排除。

## 概览

整体看，这次升级在安全方面是**净改进**：SSRF DNS 重绑定 TOCTOU 修复、固定 IP 传输（`_PinnedAsyncHTTPTransport`）、exec 超时、MCP 结果截断、命令路径提取修正。真正的回归集中在两类重构：

1. **会话键命名空间统一**（`{channel}:{chat_id}` → `im:{channel}:{chat_id}`）—— 破坏向后兼容。
2. **锁粒度优化**（client_pool 把健康检查/断开移出池锁）—— 引入并发竞态。

发现按严重度分级，共 20 项，其中高 1 项、中 6 项、低 12 项、范围外备注 1 项。

---

## 🔴 高严重性

### 1. 会话键加 `im:` 前缀，旧 IM 对话升级后孤立

- **位置：** `xbot/platform/bus/events.py:21-36`（`to_canonical_session_key`）、`xbot/runtime/session/conversation_store.py:177-216`（`_session_paths_for_read`）
- **已验证：** ✅
- **问题：** v2.0.14 用 `{channel}:{chat_id}`（如 `slack:123`）经 `sha256` 存盘；HEAD 改为 `im:{channel}:{chat_id}`（如 `im:slack:123`）。`_session_paths_for_read` 的 4 个候选路径**都派生自同一个新 key**——"legacy" 指目录位置和文件名格式（hashed vs safe），**并未**回退尝试去掉 `im:` 前缀的旧 key。
- **失败场景：** 升级后，任何已有 Slack/Telegram/Discord/Feishu 用户下一条消息时，找不到旧会话文件 → 开新空会话，进行中的对话上下文全部丢失（数据没删，但无法寻址）。`list_sessions` 里旧文件还在，但再也解析不回原聊天。
- **影响：** 本次升级影响最大的回归，上线即触发，波及所有现有 IM 用户。

#### 解决方案

**方案 A（推荐，一次性迁移 + 读取回退）：**

1. 在 `ConversationStore` 增加 `to_legacy_session_key(channel, chat_id) -> str`，返回旧的 `{channel}:{chat_id}` 形式。
2. 扩展 `_session_paths_for_read(key, *, include_legacy_key=False)`：当 `include_legacy_key=True` 时，除了新 key 的 4 个路径，再追加由旧 key（去掉 `im:` 前缀）派生的 4 个路径。
3. 在 `get_or_create` / `_load` 的读取路径上调用 `include_legacy_key=True`，找到旧文件后：
   - 读取内容写入新 key 路径（`_get_session_path(key)`），
   - 删除旧 key 文件（或保留作为备份，加 `.bak` 后缀），
   - 记一次 `logger.info("Migrated session %s -> %s", old_key, new_key)`。
4. 加一个 `migrate_legacy_sessions()` 幂等方法，遍历 `list_sessions()` 扫描出的孤立旧文件，批量重键化；可在网关启动时调用一次。

**方案 B（最小改动，仅读取回退，不搬文件）：**

- 只做上述第 2、3 步的"读取时回退查询"，不主动迁移文件。
- 优点：改动小、零风险；缺点：旧文件长期共存，`list_sessions` 仍会列出重复项，需在列表层按内容去重。

> 建议 A：迁移后状态干净，`list_sessions` 不会出现无法解析的孤立条目。

---

## 🟠 中严重性

### 2. client_pool `get_or_create` 断开后使用竞态

- **位置：** `xbot/runtime/core/client_pool.py:70-89`
- **已验证：** ✅
- **问题：** 重构把健康检查和断开操作移出池锁。`_disconnect_record`（84 行，长达 3s 的 await）期间记录仍以 `state="connected"` 留在 `_clients` 中，直到 87 行才置 `disconnected` 并删除。并发调用者 B 可在此窗口内取到同一记录、健康检查通过、在锁下二次确认 `current is record and state=="connected"` 为真 → 返回客户端；随后 A 完成断开并删除记录，**B 持有一个已断开的客户端**，后续 `query()`/`receive_messages()` 失败。
- **失败场景：** 两个并发请求命中同一不健康会话；B 取到正在被 A 回收的客户端，后续调用报错。
- **窗口：** 较窄（需健康检查在断开期间返回 True），但属真实并发回归。

#### 解决方案

在标记"正在回收"状态时**先于**断开操作置位，使并发调用者能观察到：

```python
# 伪代码：进入回收分支时，先在锁内把状态置为 disconnecting
async with self._lock:
    current = self._clients.get(session_key)
    if current is record and current.state == "connected":
        current.state = "disconnecting"   # 立即可见，阻止 B 取用
    else:
        continue  # 已被他人处理
await self._disconnect_record(record, timeout=3.0)
async with self._lock:
    if self._clients.get(session_key) is record:
        record.state = "disconnected"
        del self._clients[session_key]
continue
```

- 在 `ClientRecord` 增加一个 `disconnecting` 状态枚举值。
- 74 行的快速路径 `record.state == "connected"` 自然会跳过 `disconnecting` 记录，B 会 `continue` 进入下一轮循环并新建客户端。
- 同步检查 `_is_client_healthy` 是否在断开期间仍可能返回 True；若健康检查本身有副作用，考虑在 `disconnecting` 状态下直接跳过健康检查。

---

### 3. 心跳 enabled 标志运行时无效（API 切换不生效）

- **位置：** `xbot/interfaces/gateway/app.py:1155-1162`、`1220-1235` ↔ `xbot/runtime/system/heartbeat/service.py:186-208`（`_run_loop`）
- **问题：** 两个 PATCH 端点设置 `container.heartbeat.enabled`，但 `_run_loop` 只检查 `self._running`，循环中从不重新读 `self.enabled`。
- **失败场景：**
  - WebUI 禁用心跳 → `heartbeat_status` 报 `enabled:false`，但循环仍每 30 分钟唤醒代理（产生 LLM 调用/通知）。
  - 启动时禁用、之后通过 API 启用 → 循环永远不会启动，直到网关重启。

#### 解决方案

让 `_run_loop` 在每个 tick 重新读取 enabled，并支持运行时启停：

1. `_run_loop` 在 `while self._running:` 内、每次 `await asyncio.sleep(interval)` 前后检查 `self.enabled`；若 `not self.enabled`，则 `continue`（跳过本 tick 的唤醒）。
2. `patch_heartbeat` / `patch_gateway_config` 在改 `enabled` 后：
   - 从 False→True 且循环未运行时，调用 `heartbeat.start()`（幂等：内部用 `_running` 守卫）。
   - 从 True→False 不需要停循环（循环自己会跳过），但可选地 `heartbeat.stop()` 以释放协程。
3. 给 `HeartbeatService` 加 `set_enabled(bool)` 方法，原子地更新 `self.enabled` 并在需要时 start/stop，避免 API 层直接 `setattr`。

---

### 4. `patch_gateway_config` 接受 0/负数心跳间隔

- **位置：** `xbot/interfaces/gateway/app.py:1233-1235`
- **问题：** `patch_heartbeat` 校验 `interval_s >= 1`（1164 行），但 `patch_gateway_config` 设置 `gateway.heartbeat.interval_s` 和 `container.heartbeat.interval_s` 时无边界检查。
- **失败场景：** 管理员 PATCH `heartbeat_interval_s: 0` → `asyncio.sleep(0)` 立即 tick → CPU 空转 + 告警风暴。负数被 asyncio 视为 0。

#### 解决方案

- 在 `patch_gateway_config` 设置 interval 前复用同一校验：`if interval_s is not None and interval_s < 1: raise HTTPException(400, ...)`。
- 抽一个 `_validate_heartbeat_interval(v)` 共享给两个端点，避免校验逻辑漂移。
- 可选：在 `HeartbeatService` / schema 层加 `interval_s: float = Field(ge=1)` 做模型级硬约束，从根上杜绝。

---

### 5. shell 超时只杀 shell，未杀进程组

- **位置：** `xbot/tools/shell.py:88`（`create_subprocess_shell` 未设 `start_new_session=True`）、`102`（超时 `process.kill()`）
- **问题：** `process.kill()` 只信号 shell PID，管道/后台子进程变孤儿继续运行。
- **失败场景：** `cat /dev/urandom | base64 | head` 超 60s → `kill()` 终止 `sh`，但 `cat`/`base64` 仍耗 CPU；调用方收到"已终止"的虚假保证。恶意/失控管道可借此耗尽资源。

#### 解决方案

```python
# 创建时（POSIX）
proc = await asyncio.create_subprocess_shell(
    cmd, cwd=cwd, env=env,
    start_new_session=True,  # 子进程成为新进程组首领
    ...
)
# 超时分支
try:
    pgid = os.getpgid(proc.pid)
    os.killpg(pgid, signal.SIGKILL)
except ProcessLookupError:
    pass  # 已退出
```

- 注意竞态：先读 `proc.pid` 再 `getpgid`，进程可能已退出，用 `ProcessLookupError` 兜底。
- Windows 无进程组语义，需 `# pragma: no cover` 分支或用 `taskkill /T /F /PID`。
- 配合 `_best_effort_force_disconnect` 的现有模式，把进程组清理放进超时处理路径。

---

### 6. 全局 `_store_lock` 把所有内存归并串行在 LLM 调用之后

- **位置：** `xbot/memory/store.py:353`、`368-371`（`MemoryConsolidator.consolidate_messages`）
- **问题：** `consolidate_messages` 在整个 `store.consolidate()`（一次 10–30s 的 LLM 请求）期间持有进程级 `_store_lock`。`maybe_consolidate_by_tokens` 在此期间还持有会话级归并锁。v2.0.14 无此全局锁，不同会话归并可并发。
- **失败场景：** `memory_consolidation_mode=="sync"`（`service.py:3267-3269` 内联 `await`）下，会话 A 的消息处理被会话 B 的归并 LLM 调用阻塞，造成面向用户的延迟。回归。

#### 解决方案

- **缩小锁粒度：** `_store_lock` 只保护 `_clients`/`_messages` 字典的读写临界区（毫秒级），**不**覆盖 `store.consolidate()` 的 LLM 调用。在锁内取出需要归并的快照，释放锁后再做 LLM 调用，最后用短锁写回结果。
- **改会话级锁：** 用 `session_key` 粒度的 `asyncio.Lock`（`dict[str, Lock]` + 懒创建）替代全局锁，使不同会话的归并真正并发。
- **sync 模式解耦：** 评估 sync 模式下是否真的需要内联等待归并；可改为"归并提交任务 + 等待完成"的细粒度 future，而非持锁等待。
- 确认锁顺序（会话级 → 全局）不变，避免引入死锁。

---

### 7. 三处 `split(":", 1)` 未适配 `im:` 前缀

- **位置：**
  - `xbot/memory/store.py:420`
  - `xbot/runtime/session/conversation_store.py:515`
  - `xbot/interfaces/cli/commands.py:124`、`1245`
- **问题：** 网关（`gateway/app.py:521-526`）已适配 `im:` 前缀，但这三处仍按旧 `{channel}:{chat_id}` 在第一个冒号处拆分。
- **失败场景：** 对 `im:slack:123`，`store.py:420` 产出 `channel="im"`、`chat_id="slack:123"` 喂给归并 token 探测（上下文取错渠道）；`conversation_store.py:515` 在 WebUI 会话列表把渠道显示为 `im`；CLI 无法匹配/恢复 IM 会话。是 #1 的连锁副作用。

#### 解决方案

- 抽一个共享解析函数 `parse_session_key(key: str) -> tuple[str, str]`，统一剥离前导 `im:` 前缀后按 `channel:chat_id` 拆分（与 `to_canonical_session_key` 对称）。
- 三处 `split(":", 1)` 替换为调用该函数。
- 加单测覆盖 `im:slack:123`、`slack:123`（无前缀兼容）、`im:telegram:chat_id:with:colons`（chat_id 含冒号）三种形态。
- 与 #1 的迁移一并验证。

---

## 🟡 低严重性

### 8. cron 单个卡死任务停摆所有调度

- **位置：** `xbot/runtime/system/cron/service.py:327-362`（`_execute_job` 等待 `self.on_job(job)` 无超时）、`_on_timer:307`（仅任务执行完才重新 `_arm_timer`）
- **问题：** 一个挂起的 MCP 工具让所有 cron（含 `at` 提醒）在重启前不再触发。
- **解决方案：** 给 `on_job` 调用包 `asyncio.wait_for(..., timeout=job_timeout)`（可配置，默认如 300s），超时则取消并记错误日志；`_on_timer` 用 `asyncio.gather` 或 fire-and-forget + 单独 `_arm_timer`，使一个任务的卡死不阻塞下一次定时。可选：每个 job 在独立 `create_task` 中执行，主定时器只负责按时触发。

### 9. `list_sessions` 遇一行坏 JSONL 跳过整条会话

- **位置：** `xbot/runtime/session/conversation_store.py:498-512`
- **问题：** 外层 `except Exception: continue`（525 行）吞掉整条会话；`_load` 已逐行跳过（322-330），这里不一致。
- **解决方案：** 把 `json.loads(line)` 包进内层 `try/except JSONDecodeError: continue`，跳过坏行而非整条会话；与 `_load` 的逐行跳过保持一致。

### 10. `disconnect` 异常路径返回值由 False 改 True

- **位置：** `xbot/runtime/core/client_pool.py:193-217`
- **问题：** `prune_idle`/`disconnect_all` 计数永远等于找到的记录数，掩盖断开失败趋势。
- **解决方案：** 异常路径仍返回 `False`；或在返回计数外另返回 `failed` 计数。可观测层依赖该计数做告警，需同步更新消费方。

### 11. `query_directly` 仅成功时持久化用户消息

- **位置：** `xbot/runtime/core/service.py:2735-2737`
- **问题：** `_persist_user_message` 在 `try` 末尾、`return` 前调用，异常时用户输入丢失，与 workflow 模式（2858 行，处理前持久化）不一致。
- **解决方案：** 把 `_persist_user_message` 移到 `self.process()` 之前（与 workflow 对齐），用 `try/finally` 保证即使处理抛异常也先落盘。

### 12. `ws_chat` 收到超大/畸形消息后 `return` 关闭 socket 但不取消在途任务

- **位置：** `xbot/interfaces/gateway/app.py:1709-1722`
- **问题：** 处理器 `return` 结束 websocket 协程，但只有 `except WebSocketDisconnect`（1736-1741）会取消 `owned_task_keys`。在途 `_run_agent_turn` 继续占用 `active_tasks` 会话槽。
- **失败场景：** 用户轮次进行中发送超大消息 → socket 关闭、代理轮次仍在跑、占会话锁 → 重连客户端收到 "already running" 直到卡死轮次完成，结果被静默丢弃。
- **解决方案：** 在 `return` 路径上同样调用 `owned_task_keys` 的取消逻辑；或把取消移到 `finally` 块，保证任何退出路径都清理在途任务。

### 13. FastAPI 健康路由就绪逻辑与 aiohttp 不一致

- **位置：** `xbot/runtime/system/monitoring/health.py:275-285`（`create_health_router.readiness`）vs `159-178`（`_handle_ready`）
- **问题：** aiohttp 版解包 `{"state":...}`，FastAPI 版不解包；未来用字典状态会误返 503。当前调用方传字符串，暂不触发。
- **解决方案：** 抽一个共享 `_is_ready(agent_status) -> bool` 函数，两个路由都调用，统一字典/字符串两种形态的处理。

### 14. `/health/ready` 启动期从 200 变 503

- **位置：** `xbot/runtime/system/monitoring/health.py:167-170`、`279`
- **问题：** `agent_status` 从 `in ("running","unknown")` 收紧为 `== "running"`。`"initializing"` 期间返 503。更正确但属行为回归。
- **解决方案：** 若编排器/负载均衡器依赖启动期 200，可改为 `agent_status in ("running", "initializing")` 返 200，或在文档中明确"启动期 503"为新契约并通知下游。建议后者（语义更清晰），但需同步告警/部署脚本。

### 15. `patch_channel`（单数）未校验 channel 名

- **位置：** `xbot/interfaces/gateway/app.py:1005-1022`
- **问题：** 直接 `setattr(container.config.channels, channel_name, merged)`，未走 `validate_safe_name`（复数 `patch_channels:1004` 校验了每个名称）。
- **解决方案：** 单数端点同样调用 `validate_safe_name(channel_name)` 后再 `setattr`；或直接复用复数端点的校验路径。

### 16. shell shlex 失败现返回 `[]` 跳过相对路径提取

- **位置：** `xbot/tools/shell.py:248-251`
- **问题：** 旧代码回退 `command.split()`；新代码返回 `[]`，`restrict_to_workspace` 模式下不提取/检查相对路径。残余风险小（多数 shlex 失败也是 shell 语法错误）。
- **解决方案：** 恢复 `command.split()` 回退；或在 shlex 失败时直接拒绝执行该命令（更保守），但要与现有行为对齐评估。

### 17. registry 重复注册现抛异常，无注销路径

- **位置：** `xbot/tools/registry.py:25-26` + `xbot/tools/mcp.py:244`（`_connect_single_mcp_server`）
- **问题：** 旧版重连/热重载流程未先 `unregister` 就重注册，受影响服务器被标记"失败"。主路径已用 SDK 管理的 MCP，影响有限。
- **解决方案：** `connect_mcp_servers` 重连前先 `unregister` 旧工具；或给 `ToolRegistry.register` 加 `replace=True` 选项，显式覆盖。

### 18. filesystem 模糊 `replace_all` 顺序 replace 可能被子串破坏

- **位置：** `xbot/tools/filesystem.py:293-295`
- **问题：** 非精确分支遍历 `dict.fromkeys(_find_fuzzy_match_fragments(...))` 顺序 `new_content.replace(fragment, norm_new)`；片段互为子串时较早替换可能破坏较晚匹配。
- **解决方案：** 按片段长度降序排序后再 replace；或对内容做单次扫描、收集所有匹配区间后一次性拼接替换，避免顺序覆盖。

### 19. `resolve_sdk_provider_and_model` 空模型抛异常，与 schema 默认矛盾

- **位置：** `xbot/platform/config/sdk_resolver.py:67-69` vs `xbot/platform/config/schema.py:34`（`model: str = ""`）
- **问题：** schema 注释"使用供应商的第一个模型"，但 resolver 空模型抛 `ValueError`。目前休眠（无调用方），潜在不一致。
- **解决方案：** resolver 在空模型时改为"取 provider 的第一个可用模型"并返回，与 schema 默认契约对齐；或删除该函数（确认无未来调用方）。

### 20. ContextVar 配置路径不传播到已存活的后台任务

- **位置：** `xbot/platform/config/loader.py:213-230`（`_current_config_path` 改为 `ContextVar`）
- **问题：** `asyncio.create_task` 复制当前上下文，任务生成后 `set_config_path` 对该任务不可见（旧 `global` 可见）。
- **失败场景：** 仅在后台任务已启动后再 `set_config_path`（如配置热重载/多租户切换）时触发。若仅启动时调用一次则无影响。
- **解决方案：** 若需要运行时切换配置路径，让后台任务显式从单一配置源（如 `ConfigManager` 单例）读取，而非依赖 ContextVar；或文档明确"配置路径仅在启动时设置一次"为契约。

---

## ℹ️ 范围外备注

### 附. 未提交的 `service.py` 改动（`_resolve_cli_path`）

- **位置：** 工作区 `git status` 显示 `M xbot/runtime/core/service.py`，+38 行，未提交。
- **内容：** 解析原生架构 `claude` CLI 路径，规避 ARM Mac 上 Rosetta 2 缺 AVX 导致 SDK 自带 x86_64 CLI 挂起 60s 超时。
- **说明：** 不在 `v2.0.14..HEAD` 提交范围内，逻辑自洽。属于新增功能，建议单独验证：候选路径里 `~/.local/share/claude/versions/latest` 是否为可执行文件需确认（`is_file() and os.access(X_OK)` 已覆盖，但 `latest` 是否为符号链接/可执行需实测）。

---

## 处理优先级建议

| 优先级 | 项 | 理由 |
|---|---|---|
| P0 | #1 会话键迁移 + #7 split 适配 | 影响所有现有 IM 用户，上线即触发 |
| P1 | #2 client_pool 竞态、#3 心跳 enabled 无效、#5 shell 进程组 | 真实正确性 bug，偶发失效 |
| P2 | #4 间隔校验、#6 全局锁、#8 cron 卡死、#12 ws_chat 任务清理 | 边界条件下稳定性问题 |
| P3 | #9–#11、#13–#20 | 健壮性/一致性/可观测性，后续清理批次 |

---

## 验证清单（修复后回归用）

- [ ] 现有 IM 会话升级后对话连续性保留（#1）
- [ ] `im:` 前缀键在三处 split 点解析正确（#7）
- [ ] 并发命中不健康会话不取到已断开客户端（#2）
- [ ] WebUI 启停心跳立即生效（#3）
- [ ] `heartbeat_interval_s=0` 被 400 拒绝（#4）
- [ ] 管道命令超时后无孤儿进程（#5）
- [ ] sync 归并模式下不同会话不互相阻塞（#6）
- [ ] 单个 cron 卡死不阻塞其他调度（#8）
- [ ] `list_sessions` 遇坏行不丢整条会话（#9）
- [ ] `query_directly` 异常时用户消息已落盘（#11）
- [ ] ws 超大消息退出路径取消在途任务（#12）
