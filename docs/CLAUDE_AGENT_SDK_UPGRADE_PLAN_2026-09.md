# Claude Agent SDK 升级方案：0.2.131 → 0.2.152

> 状态：✅ 已执行完毕（2026-09-05）
> 更新日期：2026-09-05
> 核对方式：逐一比对 0.2.152 wheel 源码 / AST 符号表 / 项目引用点

---

## 1. 现状盘点

| 项 | 值 | 说明 |
|---|---|---|
| `pyproject.toml` 声明 | `claude-agent-sdk==0.2.131`（L54） | 上上次升级（commit 491c965f7） |
| `uv.lock` 实际锁定 | `0.2.128` | **lock 与声明不一致**，需重新 lock |
| `.venv` 实际安装 | `0.2.128` | 运行时真实版本 |
| 最新可用版本 | **`0.2.152`**（2026-09-03 发布） | 目标版本 |
| 目标版本要求 | Python `>=3.10`、`mcp>=1.23,<3.0` | 项目 Python>=3.11、mcp 1.26 → **无冲突** |

**结论：当前运行的是 0.2.128，本次升级实质跨度 0.2.128 → 0.2.152（24 个版本）。**

---

## 2. 版本差异详解（0.2.128 → 0.2.152）

24 个版本中仅 5 个含功能变更，其余均为内置 CLI bump：

| 版本 | 类型 | 变更内容 | 对 xbot 影响 |
|---|---|---|---|
| 0.2.126 | 功能 | `ResultMessage` 新增 `terminal_reason`；`model_usage` 类型化 `dict[str, ModelUsage]` | 无（字段带默认值） |
| 0.2.127 | 修复 | `query()` 不再在首个 result 帧后过早关闭 stdin（后台任务 MCP 工具可用） | 利好（无改动） |
| **0.2.129** | **⚠️ Breaking** | `ClaudeAgentOptions.skills` 名称校验：含 `*`、`:*`、括号、逗号等非法名抛 `ValueError`；原 `skills=["*"]` 须改 `skills="all"`；同时修复 `--allowedTools` 注入漏洞 | **不触发**（xbot 未用 `skills` 参数，用 `add_dirs`+`plugins`） |
| 0.2.137 | 功能 | 新增 `ConversationResetMessage` 消息类型（Message 联合拓宽）；`UserMessage`/`ResultMessage` 新增 `origin` 字段；`ClaudeAgentOptions` 新增 `resume_session_at`/`resume_drops_turn`；修复 SessionStore 恢复时缺失 settings.json 导致 "Not logged in" | 见 §3.2 |
| 0.2.140 | 功能 | MCP 依赖放宽 `<3.0`（支持进程内 MCP 2.x server）；新增 `forward_subagent_text`；CLI 异常退出抛 **`ResultError`**（带 `subtype`/`errors`/`result`）；`can_use_tool` 支持字符串提示 | 见 §3.3 |
| 0.2.141~0.2.152 | 内部 | 仅内置 CLI 2.1.236 → 2.1.259 | 无 |

> 中间 0.2.130~0.2.136、0.2.138/0.2.139 等均为纯 CLI bump，无 SDK API 变化。

---

## 3. 代码级兼容性核对（已逐一验证）

### 3.1 项目使用的 API 在 0.2.152 全部存在 ✅

对 0.2.152 wheel 做了 AST 符号表扫描，xbot 引用的每个符号都可用：

| 引用文件 | 使用的 API | 0.2.152 状态 |
|---|---|---|
| `xbot/runtime/core/client_pool.py` | `ClaudeSDKClient` | ✅ |
| `xbot/runtime/core/service.py` | `ClaudeSDKClient` / `ClaudeAgentOptions` / `delete_session` / `HookMatcher` / `AgentDefinition` | ✅ |
| `xbot/runtime/core/hooks.py` | `HookContext` / `PreCompactHookInput` / `PreToolUseHookInput` | ✅ |
| `xbot/capabilities/tool_adapter.py` | `create_sdk_mcp_server` / `tool` | ✅（仍为顶层导出） |
| `xbot/interaction/permission.py` | `PermissionResultAllow` / `PermissionResultDeny` / `ToolPermissionContext` | ✅ |
| `xbot/interfaces/cli/goal.py` | `PermissionResultAllow` / `ToolPermissionContext` | ✅ |
| `tests/...` | `ResultMessage` / `_internal.message_parser.parse_message`（私有） | ✅ 仍在 |

`ClaudeAgentOptions` 字段数 47 → 50，新增 `forward_subagent_text`、`resume_session_at`、`resume_drops_turn`，**均为可选**，不影响现有构造。

### 3.2 消息分派不受 Message 联合拓宽影响（但有一个可选项）

xbot 的 `_convert_event()`（service.py L2257）用 **`type(event).__name__` 字符串比对**分派，并非 `assert_never`/穷举 match，因此：

- `ConversationResetMessage`（会话被 `/clear` 等重置时发出）**不会导致崩溃**，但目前会落入 `return None` 被静默忽略。
- 若需在会话重置时清理累计统计（如 `total_cost_usd`）、刷新会话 ID，可在 `_convert_event` 加一个分支（**可选增强**，非必须）。
- `UserMessage`/`ResultMessage` 新增的 `origin: MessageOrigin | None = None` **带默认值**，现有 dataclass 构造（含测试）不会炸。

### 3.3 ResultError：建议显式捕获（推荐的小改动）

0.2.140 起，CLI 以错误退出时 SDK 抛 `ResultError`（含 `subtype`/`errors`/`result` 结构化负载），此前是裸 "exit code 1"。

xbot 的 `service.py` 现有 `except Exception` 已能兜住（不会崩溃），但会丢失结构化错误明细。**推荐**在 query 调用处增加：

```python
from claude_agent_sdk import ResultError
...
try:
    await asyncio.wait_for(client.query(...), timeout=query_timeout)
except ResultError as e:
    logger.error("[AgentService] SDK ResultError subtype=%s errors=%s",
                 e.subtype, e.errors)
    # 仍按原路径上报 finish_reason="error"
except asyncio.TimeoutError as e:
    ...
```

### 3.4 依赖约束无冲突 ✅

| 包 | xbot 锁定 | SDK 0.2.152 要求 | 结论 |
|---|---|---|---|
| mcp | `>=1.26.0,<2.0.0` | `>=1.23.0,<3.0.0` | 兼容，无需改动 |
| python | `>=3.11` | `>=3.10` | 兼容 |

---

## 4. 执行步骤（按序）

```bash
# ① 修改依赖声明
#    pyproject.toml L54：==0.2.131 → ==0.2.152
#    （如需吸收未来 patch，可放宽为 >=0.2.152,<0.3.0）

# ② 同步 lock 与 venv（同时修复当前 lock 停留在 0.2.128 的问题）
cd /Users/zhaobomin/Documents/projects/thirdpart/xbot
uv lock
uv sync

# ③ 冒烟验证 SDK 可导入、版本正确
.venv/bin/python -c "import claude_agent_sdk; print(claude_agent_sdk.__version__)"

# ④ 跑受影响测试
.venv/bin/python -m pytest tests/test_agent_service.py \
  tests/test_client_pool.py \
  tests/integration/test_client_resume_failure.py \
  tests/integration/test_client_pool_lifecycle.py \
  tests/test_service_round3_part4.py \
  tests/test_result_none_bug_diagnostic.py -x -q
```

### 4.1 建议同步做的代码改动

| 文件 | 改动 | 必要性 |
|---|---|---|
| `xbot/runtime/core/service.py` query 异常区 | 增加 `except ResultError` 结构化日志 | 推荐 |
| `xbot/runtime/core/service.py` `_convert_event` | （可选）`ConversationResetMessage` 分支：清会话统计 / 刷新 session_id | 可选 |

### 4.2 验证清单

- [ ] `uv sync` 无依赖冲突报错
- [ ] `.venv/bin/python -c "import claude_agent_sdk"` 输出 0.2.152
- [ ] 上述 pytest 全部通过
- [ ] 本地启动 xbot 冒烟：发起一次普通对话 + 一次工具调用（验证 `create_sdk_mcp_server` 链路）
- [ ] 验证一次会话恢复（resume）流程

---

## 5. 回滚方案

```bash
# pyproject.toml 改回 0.2.131（或 git checkout 该文件）
uv lock && uv sync
# 代码改动（如有）用 git revert 或手动还原
```

---

## 7. 执行记录（2026-09-05）

### 已完成

| 项 | 结果 |
|---|---|
| `pyproject.toml` L54 | `claude-agent-sdk==0.2.131` → `==0.2.152` |
| `uv lock` | `claude-agent-sdk v0.2.128 → v0.2.152`（219 包解析成功） |
| `uv sync --extra dev` | venv 更新到 0.2.152，dev 测试依赖补齐 |
| 版本验证 | `.venv/bin/python` 确认 `__version__ == 0.2.152`，uv.lock 同步 |

### 代码改动

1. **`xbot/runtime/core/service.py`** 两处 `except Exception` 增加 `ResultError` 结构化日志分支（主 process 路径 + SessionWorker 路径），记录 `subtype`/`errors`/`result`/`exit_code`，普通异常保持原 `logger.exception` 行为。
2. **删除 `process()` 内 cmd 分支两行冗余局部 import**（`InboundMessage`/`AgentResponse` 模块顶部已导入）——修复了**隐藏 bug**：局部 import 使 `AgentResponse` 在函数作用域内被视为局部变量，非 cmd 路径走进 `except` 分支 yield `AgentResponse` 时报 `UnboundLocalError`（升级前的 7 个测试失败根因）。

### 测试结果

| 测试集 | 结果 |
|---|---|
| `tests/test_agent_service.py` | 55 passed |
| client_pool / service_round3 / result_none / integration(resume+lifecycle) | 54 passed |
| tool_adapter / permission ×2 / hooks / sdk_resolver / goal_mode | 147 passed |
| **合计** | **256 passed，0 failed** |

> 注：升级后首轮出现 7 个失败，经 `git stash` 基线对比确认全部源于上述隐藏 bug（v2.1.9 引入的局部 import 遮蔽），与 SDK 升级无关；删除冗余 import 后全绿。

---

## 6. 风险汇总

| 风险 | 等级 | 缓解 |
|---|---|---|
| lock 停留在 0.2.128 与声明不一致 | 低 | 步骤 ② 一并修复 |
| 0.2.129 skills 校验 Breaking | 低 | 已确认 xbot 不用 `skills` 参数，不触发 |
| `ConversationResetMessage` 被静默忽略 | 低 | 不影响稳定性；可选加分支增强 |
| 测试引用 SDK 私有 API `_internal.message_parser` | 低 | 0.2.152 仍在；长期建议改为公开 API 或 mock |
| CLI 2.1.259 行为差异（如权限提示文案） | 低 | 冒烟回归覆盖 |
## 7. 执行记录（2026-09-05）

### 已落地改动

| 文件 | 改动 | 说明 |
|---|---|---|
| `pyproject.toml` | `==0.2.131` → `==0.2.152` | + `uv lock && uv sync --extra dev`，lock 0.2.128→0.2.152 |
| `xbot/runtime/core/service.py` | process 主路径 + SessionWorker 路径 `except ResultError` 结构化日志 | 0.2.140+ 结构化错误，记录 subtype/errors/result/exit_code |
| `xbot/runtime/core/service.py` | 删除 cmd 分支冗余局部 import | 修复 UnboundLocalError 隐藏 bug（升级前已存在，7 个测试因此红） |
| `xbot/runtime/core/service.py` | `_convert_event` 新增 `ConversationResetMessage` 分支 → system 事件 | 不再静默丢弃 /clear 重置事件 |
| `xbot/runtime/core/service.py` | `_sync_sdk_session_mapping` 遇 reset 清空失效映射 + `_clear_cached_sdk_session_id` | 防止下轮 resume 死会话 |
| `tests/test_result_none_bug_diagnostic.py` | 删除 2 个 SDK 私有 API 测试 | `_internal.message_parser` 依赖移除（同类意图已被公开 API 测试覆盖） |
| `tests/test_agent_service.py` | +2 个 ConversationReset 覆盖测试 | 转换 + 清映射 |

### 验证结果

- 相关测试：**393 passed / 0 failed**（agent_service / result_none / service_round3 / service_deep / round3_part4 / resume_failure / client_pool_lifecycle）
- 真实冒烟（alrun/DashScope + CLI 2.1.259）：**SMOKE PASS** — 新会话 `SMOKE_OK` + resume 会话 `RESUME_OK`，`is_error=False`

### 升级后注意

- SDK 0.2.152 顶层 `query()` 的 `prompt` 已为 **keyword-only**（`query(prompt=..., options=...)`）；xbot 走 `ClaudeSDKClient.query(prompt)` 实例方法，签名不受影响。
- 冒烟时 CLI 报 `claude-code:unrecognized_model`（qwen3.7-plus 为自定义模型名），但 DashScope 兼容端点正常响应，属预期噪音。
