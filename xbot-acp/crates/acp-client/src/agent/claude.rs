//! Claude Code 启动器

use crate::agent::AgentLauncher;
use std::path::Path;
use serde::Deserialize;

/// xbot 配置结构（简化版）
#[derive(Debug, Deserialize)]
struct XbotConfig {
    agents: AgentsConfig,
    providers: ProvidersConfig,
}

#[derive(Debug, Deserialize)]
struct AgentsConfig {
    defaults: AgentDefaults,
}

#[derive(Debug, Deserialize)]
struct AgentDefaults {
    model: String,
    provider: String,
}

#[derive(Debug, Deserialize)]
struct ProvidersConfig {
    #[serde(default)]
    anthropic: ProviderInfo,
    #[serde(default)]
    #[serde(rename = "aliyunCodingPlan")]
    aliyun_coding_plan: ProviderInfo,
    #[serde(default)]
    custom: ProviderInfo,
    // 其他 provider...
}

#[derive(Debug, Deserialize, Default)]
struct ProviderInfo {
    #[serde(rename = "apiKey", default)]
    api_key: Option<String>,
    #[serde(rename = "apiBase", default)]
    api_base: Option<String>,
}

/// 从 xbot 配置加载 API 凭证
fn load_xbot_config() -> Option<(String, Option<String>)> {
    let config_path = Path::new(&std::env::var("HOME").unwrap_or_else(|_| ".".to_string()))
        .join(".xbot/config.json");

    if !config_path.exists() {
        tracing::debug!("xbot config not found at {:?}", config_path);
        return None;
    }

    let content = match std::fs::read_to_string(&config_path) {
        Ok(c) => c,
        Err(e) => {
            tracing::warn!("Failed to read xbot config: {}", e);
            return None;
        }
    };

    let config: XbotConfig = match serde_json::from_str(&content) {
        Ok(c) => c,
        Err(e) => {
            tracing::warn!("Failed to parse xbot config: {}", e);
            return None;
        }
    };

    let provider_name = config.agents.defaults.provider;
    let model = config.agents.defaults.model;

    tracing::info!("Loaded xbot config: provider={}, model={}", provider_name, model);

    // 获取对应 provider 的配置
    let provider = match provider_name.as_str() {
        "aliyun_coding_plan" => &config.providers.aliyun_coding_plan,
        "anthropic" => &config.providers.anthropic,
        "custom" => &config.providers.custom,
        _ => &config.providers.custom,
    };

    let api_key = match provider.api_key.clone() {
        Some(k) if !k.is_empty() => k,
        _ => {
            tracing::warn!("No API key found for provider {}", provider_name);
            return None;
        }
    };
    let api_base = provider.api_base.clone();

    tracing::info!("Using API base: {:?}", api_base);

    // 设置模型环境变量
    std::env::set_var("XBOT_MODEL", &model);

    Some((api_key, api_base))
}

/// Claude Code 启动器
pub struct ClaudeCodeLauncher;

impl AgentLauncher for ClaudeCodeLauncher {
    fn command(&self) -> Vec<String> {
        // 使用 ACP wrapper 来启动 Claude Code agent
        // 参考: https://github.com/agentclientprotocol/claude-agent-acp
        vec![
            "npx".to_string(),
            "@agentclientprotocol/claude-agent-acp".to_string(),
        ]
    }

    fn name(&self) -> &str {
        "Claude Code (ACP)"
    }

    fn required_env_vars(&self) -> Vec<&'static str> {
        // 尝试从 xbot 配置加载
        if let Some((api_key, api_base)) = load_xbot_config() {
            tracing::info!("Setting ANTHROPIC_API_KEY from xbot config");
            // 设置环境变量供 ACP agent 使用
            std::env::set_var("ANTHROPIC_API_KEY", &api_key);
            if let Some(base) = api_base {
                tracing::info!("Setting ANTHROPIC_BASE_URL={}", base);
                // Claude SDK 使用 ANTHROPIC_BASE_URL
                std::env::set_var("ANTHROPIC_BASE_URL", &base);
            }
            return vec![];
        }

        tracing::info!("No xbot config found, checking ANTHROPIC_API_KEY env var");

        // 回退到直接检查环境变量
        vec!["ANTHROPIC_API_KEY"]
    }
}