//! 配置加载

use serde::Deserialize;
use std::path::Path;

/// 应用配置
#[derive(Debug, Deserialize)]
pub struct Config {
    pub app: AppConfig,
    pub agent: AgentConfig,
    pub file: FileConfig,
    pub terminal: TerminalConfig,
    pub permission: PermissionConfig,
}

#[derive(Debug, Deserialize)]
pub struct AppConfig {
    pub name: String,
    pub log_level: String,
    pub default_agent: String,
}

#[derive(Debug, Deserialize)]
pub struct AgentConfig {
    pub command: String,
}

#[derive(Debug, Deserialize)]
pub struct FileConfig {
    pub blocked_paths: Vec<String>,
}

#[derive(Debug, Deserialize)]
pub struct TerminalConfig {
    pub blocked_patterns: Vec<String>,
    pub max_execution_time: u64,
}

#[derive(Debug, Deserialize)]
pub struct PermissionConfig {
    pub timeout: u64,
    pub cleanup_interval: u64,
}

#[derive(Debug)]
pub enum AppError {
    ConfigError(String),
    IoError(std::io::Error),
}

impl std::fmt::Display for AppError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AppError::ConfigError(msg) => write!(f, "Config error: {}", msg),
            AppError::IoError(e) => write!(f, "IO error: {}", e),
        }
    }
}

impl std::error::Error for AppError {}

pub type Result<T> = std::result::Result<T, AppError>;

impl Config {
    pub fn load(path: &Path) -> Result<Self> {
        let content = std::fs::read_to_string(path)
            .map_err(|e| AppError::IoError(e))?;
        let config: Config = toml::from_str(&content)
            .map_err(|e| AppError::ConfigError(e.to_string()))?;
        Ok(config)
    }
}