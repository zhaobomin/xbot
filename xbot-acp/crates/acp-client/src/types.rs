//! 基础类型定义

use thiserror::Error;

/// 应用错误类型
#[derive(Error, Debug)]
pub enum AppError {
    #[error("Agent crashed: {reason}")]
    AgentCrashed { reason: String },

    #[error("Agent timeout")]
    AgentTimeout,

    #[error("Channel disconnected")]
    ChannelDisconnected,

    #[error("Permission timeout")]
    PermissionTimeout,

    #[error("IO error: {source}")]
    IoError {
        #[from]
        source: std::io::Error,
    },

    #[error("Protocol error: {message}")]
    ProtocolError { message: String },

    #[error("Config error: {message}")]
    ConfigError { message: String },
}

/// Result 类型别名
pub type Result<T> = std::result::Result<T, AppError>;

/// Agent 连接状态
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentConnectionState {
    Disconnected,
    Connecting,
    Ready,
    Active,
    Processing,
    Failed,
}

/// 飞书连接状态
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FeishuChannelState {
    Disconnected,
    Connecting,
    Connected,
    Reconnecting,
    Stopped,
}