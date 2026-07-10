# Changelog

## v2.0.36 (2026-07-10)

- 放宽 Claude SDK 最大轮次的有效上限至 1000，兼容现有部署配置并恢复网关启动。

## v2.0.35 (2026-07-10)

- WebUI 的“最大轮次”现在映射到 Claude SDK `max_turns`；配置变更会在下一轮对话重建客户端生效。
- 上下文窗口设置会同步至内存归并器，并为 API 与配置文件增加有效范围校验。
- 删除 Claude SDK 不支持的最大输出、温度和推理强度设置入口，避免保存但不生效的伪配置。

## v2.0.29 (2026-06-25)

- Upgraded `claude-agent-sdk` from `0.2.103` to `0.2.110`, pulling in bundled Claude Code CLI updates from `2.1.179` to `2.1.191`.

## v2.0.28 (2026-06-18)

- 修复 LLM repair 重试时重复初始化并关闭 `AgentService` 的问题，改为同一次 crew 执行内复用长生命周期 repair runner。
- 搜索 API provider（Brave / Tavily / SearXNG / Jina）统一使用 URL 校验与 DNS pinning transport。
- 修复多项稳定性问题：conversation compact 元数据 dirty 标记、旧 sdk session 索引清理、heartbeat tool call 参数防御、health readiness 状态兼容、memory 消息格式化容错。
- 优化 `ClientPool.disconnect()`，避免在持锁期间执行网络断开 I/O。
- 增加相关回归测试并更新本地工作目录忽略规则。

## v2.0.22 (2026-06-16)

- 清理项目根目录：删除 MagicMock/ 测试残留、.codex-webui.log、.qoder/、.workbuddy/、扫描报告等无关文件。
- 移除已废弃的 .superset/ 和空 package.json。
- 日志路径从源码目录迁移至 ~/.xbot/logs/，更新 LaunchAgent 配置。
- .gitignore 新增 .ruff_cache/ 规则。

## v2.0.16 (2026-06-03)

- Upgraded `claude-agent-sdk` from `0.2.82` to `0.2.88`.
- Pulled in bundled Claude Code CLI updates from `2.1.142` to `2.1.161` via the SDK upgrade for newer model/runtime compatibility.
- Included the upstream Trio `session_store` compatibility fix so store-backed SDK flows no longer crash under Trio runtimes.

## v2.0.0 (2026-04-14)

- Runtime session state machine fully replaced with `SessionCoordinator` as the only write path.
- Removed legacy state interfaces and compatibility paths: `SessionStateMachine`, `force_transition`, `transition`, `transaction`.
- Removed legacy phases: `RUNNING`, `STOPPING`, `RESETTING`, `ERROR`.
- Added unified recovery policy: session-level recovery + one automatic retry; clear `sdk_session_id` only after 3 consecutive recovery failures.
- Added v2 migration doc: `docs/SESSION_STATE_V2_MIGRATION.md`.

## v0.3.48 (2026-04-10)

- 优化 Tool Calls 展示：不再仅在“首个参数为字符串”时显示参数，统一支持命名参数摘要。
- Tool hint 现展示最多前 3 个参数，并对长文本/复杂结构做紧凑截断，兼顾信息量与可读性。
- 增加 `AgentService._format_tool_hint` 单测，覆盖 `Edit` 与 `TodoWrite` 的非字符串参数场景。

## v0.3.47 (2026-04-10)

- 修复 Claude SDK 持久流串轮残留：引入 Result 后 `quiet_window + drain_cap` 收口机制，并用 task ledger 吸收晚到的 `TaskNotification`，降低下一轮污染。
- 最终用户回复严格只使用 `ResultMessage.result`，不再拼接 task/progress 文本。
- 新增配置项：`post_result_quiet_window_ms`、`post_result_drain_cap_ms`、`task_terminal_statuses`。
- 增强会话与客户端清理：新增 idle client prune、disconnect 失败 force-kill fallback、`!stop` 主动断开会话 client。
- WebUI 删除会话时同步触发 runtime reset，避免残留会话状态。

## v0.3.46 (2026-04-09)

- 全面清理 xbot 对 Skill 调用语义的定制逻辑，恢复为 Claude Code SDK 原生机制（仅保留 SDK init 能力快照用于观测）。
- 删除 WebUI `/api/skills*` 管理接口与前端技能页入口，避免形成第二条 Skill 管理链路。
- CLI `init/onboard` 移除 skill-pack 安装能力与参数，默认 pack 仅保留 command pack。
- 权限与能力目录移除 skill 专项分支（如 `mcp__xbot__load_skill_content` 白名单、`skill_` 分类/统计路径）。
- 模板与系统提示去策略化，不再注入 skill 调度规则。

## v0.3.44 (2026-04-09)

- 接入 Claude SDK Memory 配置链路：支持 `memory_integration.mode`、`setting_sources`、`sdk_settings` 注入到 SDK options。
- 新增系统提示策略：`system_prompt_strategy.preset`（`xbot`/`claude_code`）与 `append_xbot_prompt`。
- 恢复 ReMe 主链路：`AgentService` 重新接入 `ContextBuilder + MemoryConsolidator`，并按 `memory_consolidation_mode` 触发 `off/sync/async`。
- 统一 memory store 注入，`MemoryTool` 与主链路共享同一实例，避免分叉状态。
- 增加配置、SDK options 与 consolidation 路径回归测试。
