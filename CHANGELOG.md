# Changelog

## v0.3.44 (2026-04-09)

- 接入 Claude SDK Memory 配置链路：支持 `memory_integration.mode`、`setting_sources`、`sdk_settings` 注入到 SDK options。
- 新增系统提示策略：`system_prompt_strategy.preset`（`xbot`/`claude_code`）与 `append_xbot_prompt`。
- 恢复 ReMe 主链路：`AgentService` 重新接入 `ContextBuilder + MemoryConsolidator`，并按 `memory_consolidation_mode` 触发 `off/sync/async`。
- 统一 memory store 注入，`MemoryTool` 与主链路共享同一实例，避免分叉状态。
- 增加配置、SDK options 与 consolidation 路径回归测试。

