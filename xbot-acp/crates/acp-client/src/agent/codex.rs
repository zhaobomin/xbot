//! Codex CLI 启动器 (通过 codex-acp 适配器)

use crate::agent::AgentLauncher;

/// Codex 启动器
pub struct CodexLauncher;

impl AgentLauncher for CodexLauncher {
    fn command(&self) -> Vec<String> {
        // 使用 codex-acp 适配器（zed-industries 维护）
        vec![
            "npx".to_string(),
            "@zed-industries/codex-acp".to_string(),
        ]
    }

    fn name(&self) -> &str {
        "Codex (codex-acp)"
    }

    fn required_env_vars(&self) -> Vec<&'static str> {
        vec!["OPENAI_API_KEY"]
    }
}