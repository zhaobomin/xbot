# Changelog

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
