//! Agent 启动器

mod claude;
mod codex;

pub use claude::ClaudeCodeLauncher;
pub use codex::CodexLauncher;

/// Agent 类型
#[derive(Debug, Clone)]
pub enum AgentType {
    ClaudeCode,
    Codex,
}

impl std::fmt::Display for AgentType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AgentType::ClaudeCode => write!(f, "claude-code"),
            AgentType::Codex => write!(f, "codex"),
        }
    }
}

impl std::str::FromStr for AgentType {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "claude-code" | "claude" => Ok(AgentType::ClaudeCode),
            "codex" => Ok(AgentType::Codex),
            _ => Err(format!("Unknown agent type: {}", s)),
        }
    }
}

/// Agent 启动器 trait
pub trait AgentLauncher {
    /// 获取启动命令
    fn command(&self) -> Vec<String>;

    /// Agent 名称
    fn name(&self) -> &str;

    /// 环境变量要求
    fn required_env_vars(&self) -> Vec<&'static str>;
}

/// 创建 Agent 启动器
pub fn create_launcher(agent_type: AgentType) -> Box<dyn AgentLauncher> {
    match agent_type {
        AgentType::ClaudeCode => Box::new(ClaudeCodeLauncher),
        AgentType::Codex => Box::new(CodexLauncher),
    }
}