# xbot 2026-07-09 Code Review 修复日志

**日期**：2026-07-13
**范围**：延续 `docs/reviews/xbot_upgrade_review_v2.0.14_to_v2.0.29.md` 与 `docs/CODE_REVIEW_2026-07-09.md` 的评审结论，逐项落地或关闭
**版本基线**：v2.0.37（当前 HEAD）
**方法**：现场核实（Read/Grep）+ 修补 + 单测验证；无 CI 集成回归

> ⚠️ 本文件是**修复过程日志**，不是发布说明。已修项、未修项、关闭项都如实记录。审查中我曾对 Fix-3 与 Fix-9 有过误分类，也一并记录在下方，避免下一轮 review 重蹈覆辙。

---

## 一、已修复（4 项）

### Fix-1：`call_for_auxiliary` 的 `model` 参数被吞掉
- **位置**：`xbot/runtime/core/service.py::call_for_auxiliary`
- **症状**：签名带 `model: str | None = None`，函数体未使用 → CLI/heartbeat 传参不生效
- **修法**：在 `AgentContext` 增加 `model` 字段，`process()` 内检测到 `context.model` 时临时覆盖 SDK options，不污染 `_effective_model` 全局缓存
- **验证**：新增 `test_call_for_auxiliary_respects_model_override`
- **兼容**：符合 memory「项目单模型部署约束」——参数保留但透传路径完整

### Fix-2：`_build_hooks` 后台任务 GC 风险
- **位置**：`xbot/runtime/core/service.py` L1867 / L1948 附近的 `asyncio.ensure_future(_send(), loop=loop)`
- **症状**：未持有 Task 引用 → 可能被 GC，运行时偶发 `Task was destroyed but it is pending!`
- **修法**：新增 `_spawn_background(coro, name=...)` helper，用 `self._background_tasks: set[asyncio.Task]` + `add_done_callback(discard)` 自清理；替换两处 `ensure_future`
- **验证**：Fix-7 补充覆盖测试（见下）
- **规范来源**：memory「后台任务生命周期管理规范」

### Fix-4：消除 `asyncio.Event._waiters` / `Lock._waiters` 私有 API 访问
- **位置**：`xbot/platform/bus/queue.py`、`xbot/memory/store.py`
- **症状**：读 `getattr(event, "_waiters", ...)` 判定是否有等待者——依赖 CPython 内部字段，PyPy/未来版本可能失效
- **修法**：改为自维护 waiter 计数
  - `queue.py` (+34/−4)：`_permission_waiter_counts` / `_interaction_waiter_counts`，入口 `+1`、清理块 `-1`
  - `store.py` (+19/−3)：`_lock_waiter_counts`，`get_lock` 与 `_cleanup_lock_if_idle` 配对增减
- **验证**：`test_message_bus_permission` + `test_request_cleanup_bugs` + `test_request_pool_limits` + `test_edge_case_coverage` + `test_memory_consolidation_edge_cases`（含 `test_lock_created_on_demand`）全绿
- **规范来源**：memory「禁止访问标准库私有API规范」

### Fix-8：`CapabilityPolicy` 在有 MCP 场景下白名单失效
- **位置**：`xbot/capabilities/policy.py::resolve_agent_tools`
- **原逻辑**：
  ```python
  if canonical.startswith("mcp_") or (has_mcp and canonical not in builtin_tool_names()):
      allowed.append(canonical)   # ← fail-open
  ```
- **症状**：只要配了任一 MCP server，拼写错误的 builtin 名（如 `read_fille`）会被误判为 MCP 工具放行，白名单形同虚设
- **修法**：删除 `has_mcp` 变量与 fail-open 分支，仅用 `mcp_` 前缀识别 MCP 工具，加 4 行注释锁死意图
- **验证**：新增 `test_resolve_agent_tools_misspelled_builtin_dropped_even_with_mcp`；`tests/test_capability_policy.py` + `tests/test_integration_real.py::TestCapabilityCatalogPolicyIntegration` 共 19 tests 全绿
- **兼容**：Grep 全部 agent yaml 配置，`tools` 字段全用 canonical 名，无人依赖 fail-open 分支

---

## 二、补齐测试（1 项，不改生产代码）

### Fix-7：GC-based fire-and-forget 任务追踪的测试缺口
- **位置**：`tests/test_agent_service.py` (+115 行)
- **覆盖**：
  1. `test_dispatch_memory_consolidation_async_removes_task_on_done`
  2. `test_track_hook_notification_task_success_cleanup`
  3. `test_track_hook_notification_task_exception_consumed`
  4. `test_track_hook_notification_task_no_running_loop`（无 loop 时 `coro.close()`，避免 `coroutine was never awaited`）
- **说明**：Fix-2 修好后缺失覆盖，补齐后一并锁定契约

---

## 三、已由更早版本修复（1 项）

### Fix-5：`ClientPool` 断连期间的竞态
- **位置**：`xbot/runtime/core/client_pool.py`
- **状态**：v2.0.27 (`c6b3e647`) / v2.0.31 (`07a9757b`) 已按 `docs/UPGRADE_REVIEW_2026-07-09.md #2` 建议吸收
- **当前实现**：所有可能触发 disconnect 的分支都符合「lock-in mark+pop → lock-out disconnect」契约（fingerprint mismatch、unhealthy 回收、容量驱逐、健康检查通过路径），并显式注释了不变量
- **本轮无改动**

---

## 四、关闭为非 bug（3 项）

### Fix-3：`_dispatch` 并非死代码
- **原始定义**：清理 `xbot/runtime/core/service.py::_dispatch`（~159 行）为死代码
- **重新评估结论**：**不是死代码，也不是 bug**——本文档作者原始 review 时误分类
- **依据**：`_dispatch` 生产侧无 caller（`run()` 只走 `_enqueue_worker_message`），但仍承载 2 条**生产路径未覆盖**的能力：
  1. **Recoverable stream-error auto-retry**（`max_attempts=2` + `_attempt_broken_session_recovery`）
  2. **`ResultMessage.result=None` fallback**（CLI ≥2.1.128 特定问题，用 last content 兜底）
- **回归测试证据**：`tests/test_result_none_bug_diagnostic.py`（文件名即说明是真实用户 bug 的防线）+ `tests/test_agent_service.py` + `tests/unit/runtime/test_agent_service_v2_integration.py`，合计 19 处 `service._dispatch(msg, bus)` 直接调用
- **处理**：不改代码。**下一轮 reviewer 若想删除，请先把 auto-retry 与 result fallback 迁移到 `_run_session_worker` / `_publish_worker_response`，再迁移 19 处测试**
- **建议 TODO**（非本轮）：在 `_dispatch` docstring 顶部标注 legacy 用途 —— 若同意可另起一轮低风险 PR

### Fix-6：`SubagentModelCompat` 分类默认值歧义
- **状态**：用户跳过（评估后判定生产上无影响）
- **本轮无改动**

### Fix-9：`_publish_worker_response` 分支复杂度
- **原始定义**：分支复杂度重构
- **重新评估结论**：**误分类**——方法是 6 个平行独立的 if，没有嵌套、fall-through、共享状态，不是真复杂度 bug
- **顺路发现**：`AgentResponse.tool_hint_text` 是遗留 dead 字段，production 侧从未赋值，3 处消费（`service.py` L2722 / L3008 / L3169）全是不可达分支
- **处理**：清理死字段属于低价值可维护性微整，本轮**不做**。若下一轮愿意清理，改动范围：
  - `xbot/runtime/core/protocol.py::AgentResponse` 移除字段
  - `service.py` 删 3 处死分支
  - `tests/test_protocol.py` 删 2 处相关断言

---

## 五、审查中的判断错误（供后续 reviewer 校对）

1. **Fix-3 原始定义有误**：把「有独有能力的 legacy 兼容路径」当成「死代码」——若按原方案执行会引入 CLI 层非确定性防御的 regression（auto-retry、result=None fallback 消失）
2. **Fix-3 测试面低估**：初次评估 9 处 `_dispatch` 直接调用，实际 19 处
3. **Fix-9 分类过度**：把「顺序 side-effect 泵 + 遗留字段的死分支」标签为「分支复杂度 bug」

原则修正：**「无生产 caller」≠「死代码」**，必须核实测试是否在锁定独有行为、生产替代路径是否已覆盖全部原有能力。

---

## 附：验证测试清单

| 修复 | 测试文件 | 数量 |
|---|---|---|
| Fix-1 | `tests/test_agent_service.py::test_call_for_auxiliary_respects_model_override` | 1 |
| Fix-2 | 由 Fix-7 覆盖 | — |
| Fix-4 | `test_message_bus_permission` + `test_request_cleanup_bugs` + `test_request_pool_limits` + `test_edge_case_coverage` + `test_memory_consolidation_edge_cases` | 73 + 38 |
| Fix-7 | `tests/test_agent_service.py` 新增 4 项 | 4 |
| Fix-8 | `tests/test_capability_policy.py` + `tests/test_integration_real.py::TestCapabilityCatalogPolicyIntegration` | 19 |
