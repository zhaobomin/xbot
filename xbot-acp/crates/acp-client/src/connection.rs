//! Agent 连接管理 - 进程启动和 stdio 连接

use std::process::Stdio;
use std::sync::Arc;

use agent_client_protocol::{self as acp, Agent};
use tokio::process::{Child, Command};
use tokio_util::compat::{TokioAsyncReadCompatExt, TokioAsyncWriteCompatExt};

use crate::types::AgentConnectionState;
use crate::{AppError, Result};

/// Agent 连接
pub struct AgentConnection {
    /// Agent 子进程
    child: Option<Child>,
    /// ACP 连接
    connection: Arc<acp::ClientSideConnection>,
    /// Agent 能力信息
    capabilities: Option<acp::AgentCapabilities>,
    /// 连接状态
    state: AgentConnectionState,
}

impl AgentConnection {
    /// 启动 Agent 进程并建立连接
    ///
    /// 注意：此函数需要在 `LocalSet` 中运行，因为 SDK 使用 `?Send` async_trait
    pub async fn launch(command: Vec<String>) -> Result<Self> {
        if command.is_empty() {
            return Err(AppError::ConfigError {
                message: "Agent command is empty".to_string(),
            });
        }

        tracing::info!("Launching agent with command: {:?}", command);

        // 启动子进程
        let mut child = Command::new(&command[0])
            .args(&command[1..])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .map_err(|e| AppError::AgentCrashed {
                reason: format!("Failed to spawn agent: {}", e),
            })?;

        // 获取 stdin/stdout pipe 并转换为 futures 兼容类型
        let stdin = child.stdin.take().expect("stdin not available").compat_write();
        let stdout = child.stdout.take().expect("stdout not available").compat();

        // 创建 ClientSideConnection
        // 注意：SDK 使用 ?Send，需要使用 tokio::task::spawn_local
        let (connection, io_task) = acp::ClientSideConnection::new(
            NullClient, // TODO: 替换为真实的 Client 实现
            stdin,
            stdout,
            |fut| {
                tokio::task::spawn_local(fut);
            },
        );

        // Spawn IO task
        tokio::task::spawn_local(io_task);

        tracing::info!("Agent process spawned, connection established");

        Ok(Self {
            child: Some(child),
            connection: Arc::new(connection),
            capabilities: None,
            state: AgentConnectionState::Ready,
        })
    }

    /// 初始化连接，获取 Agent 能力
    pub async fn initialize(&self) -> Result<acp::AgentCapabilities> {
        tracing::info!("Initializing agent connection...");

        let request = acp::InitializeRequest::new(acp::ProtocolVersion::V1)
            .client_info(acp::Implementation::new("xbot-acp", "0.1.0").title("XBot ACP Client"));

        let response = self
            .connection
            .initialize(request)
            .await
            .map_err(|e| AppError::ProtocolError {
                message: format!("Initialize failed: {}", e),
            })?;

        tracing::info!(
            "Agent initialized: {}",
            response.agent_info
                .as_ref()
                .map(|i| format!("{} v{}", i.name, i.version))
                .unwrap_or_else(|| "unknown".to_string())
        );

        Ok(response.agent_capabilities)
    }

    /// 创建新会话
    pub async fn new_session(&self, cwd: std::path::PathBuf) -> Result<acp::SessionId> {
        tracing::info!("Creating new session...");

        let request = acp::NewSessionRequest::new(cwd);

        let response = self
            .connection
            .new_session(request)
            .await
            .map_err(|e| AppError::ProtocolError {
                message: format!("New session failed: {}", e),
            })?;

        tracing::info!("Session created: {:?}", response.session_id);

        Ok(response.session_id)
    }

    /// 发送消息
    pub async fn prompt(&self, session_id: &acp::SessionId, content: String) -> Result<()> {
        tracing::info!("Sending prompt to session: {:?}", session_id);

        let request = acp::PromptRequest::new(session_id.clone(), vec![content.into()]);

        // prompt 返回 PromptResponse
        let _response = self
            .connection
            .prompt(request)
            .await
            .map_err(|e| AppError::ProtocolError {
                message: format!("Prompt failed: {}", e),
            })?;

        tracing::info!("Prompt completed");
        Ok(())
    }

    /// 获取 StreamReceiver 用于接收流式更新
    pub fn subscribe(&self) -> acp::StreamReceiver {
        self.connection.subscribe()
    }

    /// 检查 Agent 进程状态
    pub async fn check_alive(&mut self) -> bool {
        if let Some(child) = &mut self.child {
            match child.try_wait() {
                Ok(Some(_)) => {
                    tracing::warn!("Agent process exited");
                    self.state = AgentConnectionState::Disconnected;
                    false
                }
                Ok(None) => true,
                Err(e) => {
                    tracing::error!("Failed to check agent status: {}", e);
                    false
                }
            }
        } else {
            false
        }
    }
}

/// Null Client - 空实现，用于测试
struct NullClient;

#[async_trait::async_trait(?Send)]
impl acp::Client for NullClient {
    async fn request_permission(
        &self,
        _args: acp::RequestPermissionRequest,
    ) -> acp::Result<acp::RequestPermissionResponse> {
        Err(acp::Error::method_not_found())
    }

    async fn write_text_file(
        &self,
        _args: acp::WriteTextFileRequest,
    ) -> acp::Result<acp::WriteTextFileResponse> {
        Err(acp::Error::method_not_found())
    }

    async fn read_text_file(
        &self,
        _args: acp::ReadTextFileRequest,
    ) -> acp::Result<acp::ReadTextFileResponse> {
        Err(acp::Error::method_not_found())
    }

    async fn create_terminal(
        &self,
        _args: acp::CreateTerminalRequest,
    ) -> acp::Result<acp::CreateTerminalResponse> {
        Err(acp::Error::method_not_found())
    }

    async fn terminal_output(
        &self,
        _args: acp::TerminalOutputRequest,
    ) -> acp::Result<acp::TerminalOutputResponse> {
        Err(acp::Error::method_not_found())
    }

    async fn release_terminal(
        &self,
        _args: acp::ReleaseTerminalRequest,
    ) -> acp::Result<acp::ReleaseTerminalResponse> {
        Err(acp::Error::method_not_found())
    }

    async fn wait_for_terminal_exit(
        &self,
        _args: acp::WaitForTerminalExitRequest,
    ) -> acp::Result<acp::WaitForTerminalExitResponse> {
        Err(acp::Error::method_not_found())
    }

    async fn kill_terminal(
        &self,
        _args: acp::KillTerminalRequest,
    ) -> acp::Result<acp::KillTerminalResponse> {
        Err(acp::Error::method_not_found())
    }

    async fn session_notification(
        &self,
        args: acp::SessionNotification,
    ) -> acp::Result<()> {
        tracing::debug!("Session notification: {:?}", args.update);
        Ok(())
    }

    async fn ext_method(&self, _args: acp::ExtRequest) -> acp::Result<acp::ExtResponse> {
        Err(acp::Error::method_not_found())
    }

    async fn ext_notification(&self, _args: acp::ExtNotification) -> acp::Result<()> {
        Err(acp::Error::method_not_found())
    }
}