//! ACP Client - Agent Client Protocol 实现
//!
//! 提供与 Claude Code、Codex CLI 等 Agent 的通信能力

pub mod types;
pub mod channel;
pub mod client;
pub mod connection;
pub mod session;
pub mod boundary;
pub mod agent;

// Re-exports
pub use agent_client_protocol as acp;
pub use types::{AppError, Result};
pub use channel::{Channel, ChannelMessage, PermissionCard, PermissionDecision};
pub use client::AcpClient;
pub use connection::AgentConnection;
pub use session::SessionManager;
pub use boundary::{FileBoundary, TerminalBoundary};
pub use agent::{AgentLauncher, AgentType, create_launcher};