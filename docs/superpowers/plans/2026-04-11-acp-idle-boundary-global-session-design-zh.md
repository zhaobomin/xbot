# xbot 会话边界升级设计（中文）

日期：2026-04-11  
作者：Codex（基于当前代码与测试现状）

---

## 1. 背景与问题

当前 `AgentService.process()` 以“读到 `ResultMessage` 即可结束请求”为主逻辑，并在结果后做短暂 drain（quiet window / drain cap）。该模型存在三个根本问题：

1. 一轮 request 内可能出现多个 `ResultMessage`，提前结束会丢内容。  
2. `ResultMessage` 后仍可能有异步 task/subagent 事件，提前结束会造成跨轮污染。  
3. 结束条件依赖启发式时间窗口，不是协议级明确边界。

ACP/Claude SDK 的可用边界语义是：开启会话状态事件后，以 `SystemMessage(subtype=session_state_changed, state=idle)` 作为一轮结束。

---

## 2. 本次设计目标

- 一步升级为 ACP 风格轮次边界：**idle 收口**。  
- **不增加全局状态管理复杂度**。  
- 不破坏既有会话/状态机制（phase、pending permission/interaction、reset/shutdown、sdk_session_id 映射）。  
- 保证多 `result` 全量可见、无跨轮串扰。

---

## 3. 非目标（明确不做）

1. 不新增第二套会话状态机（如在 `AgentService` 内再维护独立 phase）。  
2. 不引入“兼容双模式”（旧 result-drain + 新 idle 边界并存）。  
3. 不将 SDK 的 `running/idle` 直接映射为 runtime phase（避免覆盖 `WAITING_PERMISSION/WAITING_INTERACTION` 语义）。

---

## 4. 现状全局状态管理（必须保持的真源）

### 4.1 真源与职责

- `RuntimeSessionRegistry`：会话状态唯一真源（phase、routing、sdk_session_id、tasks、metadata 快照）。  
- `SessionPhase`：业务并发/交互状态机（`RUNNING/WAITING_PERMISSION/WAITING_INTERACTION/IDLE/...`）。  
- `_dispatch`/`RuntimeResponseHandlers`：负责 phase 进出与交互态切换。  
- `ClientPool`：SDK client 生命周期。  
- `conversation_store + sdk_session_id`：跨重启恢复。

### 4.2 风险点（从当前实现观察）

- `process()` 仍是旧 result-drain 语义，和目标 idle 收口冲突。  
- `_dispatch()` 当前仅首个 `result` 对用户可见，天然吞后续 `result`。  
- 若引入 `prompt_running/pending_prompts` 等新状态，容易与 `runtime_registry.phase + _active_tasks` 重叠，导致状态分叉。

---

## 5. 目标方案（最小入侵）

### 5.1 核心规则

1. 每轮结束条件从“首个 result”改为“收到 `session_state_changed: idle`”。  
2. `ResultMessage` 仅作为内容/stop 元数据，不作为边界。  
3. 维持现有 `runtime_registry` phase 机制不变；`process()` 只负责事件流消费与转换。

### 5.2 事件处理语义

- `Assistant/Task/System/Result`：均按到达顺序转换并对外输出（不早退）。  
- 检测到 `SystemMessage(subtype=session_state_changed, state=idle)`：结束当前轮读取。  
- 若流结束或异常且未收到 idle：按错误路径返回（防死等）。

### 5.3 多 result 输出策略

必须修正 `_dispatch` 的最终输出策略，避免“仅首条 result 出站”。

本方案**强制采用唯一策略**（不再保留 A/B 二选一）：

- 每个 `event_type=result` 都按顺序出站（全量可见）。  
- 会话持久化默认记录最后一个 result 作为 assistant 最终答复（可追溯、与现状兼容）。

### 5.4 全局状态保护约束

- 不改 `RuntimeSessionRegistry` 结构字段。  
- 不改 `SessionPhase` 转换图。  
- 不新增 service 内长期状态镜像（禁止 phase 镜像、禁止重复 queue 状态）。  
- `sdk_session_id` 同步逻辑继续沿用 `_sync_sdk_session_mapping()`。
- 禁止把 SDK `session_state_changed(running/idle)` 直接写入 runtime phase（避免覆盖 `WAITING_PERMISSION/WAITING_INTERACTION`）。

---

## 6. 前置条件

必须在 SDK 环境开启：

- `CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1`

否则 `session_state_changed` 不稳定/缺失，idle 边界无法成立。

### 6.1 缺失 idle 的统一兜底（可执行约束）

- 读取超时：沿用当前 `process()` 主循环 `idle_timeout=300s`，若未收到 idle 则按 error 路径收口。  
- 流提前结束（`StopAsyncIteration`）且未收到 idle：按 error 路径收口。  
- 本次升级不新增新的超时配置项，避免配置复杂度上升。

---

## 7. 迁移步骤（代码层面执行顺序）

1. 替换 `process()` 轮次收口逻辑为 idle 边界。  
2. 删除 result-drain 相关逻辑与配置读取分支（quiet window / drain cap / task terminal statuses）。  
3. 修正 `_dispatch` 多 result 出站策略。  
4. 保持 reset/shutdown/interrupt 的既有行为，仅确保不会因新收口逻辑残留读循环。  
5. 更新文档与配置说明，移除旧参数语义。

---

## 8. 测试与验收标准

### 8.1 必过场景

1. 多 result + task 事件 + idle 收口：全部事件可见，idle 前不结束。  
2. result 后仍有事件：无早退。  
3. 跨轮隔离：上一轮尾部事件不污染下一轮。  
4. stopReason 与结束边界解耦：stopReason 来自 result，结束来自 idle。  
5. 缺失 idle：可超时/异常收口，不死等。  
6. `sdk_session_id` 映射持续正确。  
7. `CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1` 被强制注入。

### 8.2 回归范围

- `tests/test_agent_service.py`（AcpStyleTurnBoundary + RunDispatch）  
- `tests/test_interaction_bug_fixes.py`（确保交互状态机不受影响）

### 8.3 端到端实测证据（真实 SDK，非 fake）

在 2026-04-11 实测（工作目录 `/Users/zhaobomin/Documents/projects/thirdpart/xbot`）：

1. 开启 `CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1`，运行真实 `claude_agent_sdk.query()` 3 次：  
每次都观测到 `SystemMessage(session_state_changed, state=running)` 与 `SystemMessage(session_state_changed, state=idle)`，结果为 `3/3`。  

2. 不开启该 env，运行真实 `claude_agent_sdk.query()` 3 次：  
均未观测到 `session_state_changed`，结果为 `0/3`。  

3. 复杂场景（触发 sub-agent/task）实测 1 次：  
观测到完整序列：`TaskStartedMessage -> TaskNotificationMessage(completed) -> ResultMessage -> session_state_changed(idle)`。  
说明复杂事件流下，idle 仍可作为稳定轮次收口边界。

结论：idle 边界方案在真实 SDK 上可行，且依赖该 env 为成立前提。

---

## 9. 风险评估

### 9.1 复杂度

- 总体复杂度：**中低**（主要集中在 `process` + `_dispatch`）。

### 9.2 主要风险与应对

1. **无 idle 事件导致阻塞**  
应对：强制 env + 缺失 idle 统一兜底（300s/流结束->error）。

2. **多 result 输出行为变化引发下游展示差异**  
应对：设计中固定为“逐条出站”唯一策略，并补 dispatch 级断言。

3. **局部新增状态造成全局状态分叉**  
应对：硬约束“只改边界语义，不加第二套状态”。

4. **与 WAITING_* 交互态冲突**  
应对：不把 SDK running/idle 写入 runtime phase；继续由 `_dispatch finally` + `response_handlers` 管理 phase。

---

## 10. 回滚策略

若线上发现不可接受行为：

1. 回滚到升级前 `process`/`_dispatch` 提交。  
2. 保留测试套件，标记失败场景用于下一轮修复。  
3. 不回滚 `session_state_events` 环境变量注入（可保留，无破坏性）。

---

## 11. 最终决策建议

建议采用该方案，原因：

- 协议边界清晰（idle），替代启发式 drain。  
- 不触碰全局状态真源，避免会话管理复杂度上升。  
- 可通过现有测试体系快速验证，迁移风险可控。
