# Changelog

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
