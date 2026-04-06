//! AcpClient - 实现 acp::Client trait，连接 Agent 和 Channel

use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use agent_client_protocol as acp;
use async_trait::async_trait;
use tokio::sync::RwLock;
use tokio::time::timeout;

use crate::boundary::{FileBoundary, FileOp, TerminalBoundary};
use crate::channel::{Channel, PermissionCard, PermissionDecision};
use crate::{AppError, Result};

/// 权限审批超时时间
const PERMISSION_TIMEOUT: Duration = Duration::from_secs(30);

/// AcpClient - 实现 Client trait，连接 Agent 和 Channel
pub struct AcpClient<C: Channel> {
    /// Channel 实现
    channel: Arc<C>,
    /// 文件操作边界
    file_boundary: FileBoundary,
    /// 终端操作边界
    terminal_boundary: TerminalBoundary,
    /// chat_id（用于发送消息到正确的会话）
    chat_id: RwLock<Option<String>>,
}

impl<C: Channel> AcpClient<C> {
    /// 创建新的 AcpClient
    pub fn new(channel: Arc<C>) -> Self {
        Self {
            channel,
            file_boundary: FileBoundary::new(),
            terminal_boundary: TerminalBoundary::new(),
            chat_id: RwLock::new(None),
        }
    }

    /// 设置当前 chat_id
    pub async fn set_chat_id(&self, chat_id: String) {
        let mut guard = self.chat_id.write().await;
        *guard = Some(chat_id);
    }

    /// 获取当前 chat_id
    async fn get_chat_id(&self) -> Result<String> {
        let guard = self.chat_id.read().await;
        guard.clone().ok_or_else(|| AppError::ProtocolError {
            message: "chat_id not set".to_string(),
        })
    }

    /// 通过 Channel 请求权限审批
    async fn request_permission_via_channel(
        &self,
        args: acp::RequestPermissionRequest,
    ) -> acp::Result<acp::RequestPermissionResponse> {
        let chat_id = self.get_chat_id().await.map_err(|_| {
            acp::Error::invalid_params()
        })?;

        // 构建权限卡片
        let description = format_permission_description(&args);
        let options: Vec<String> = args
            .options
            .iter()
            .map(|o| o.name.clone())
            .collect();

        let card = PermissionCard {
            permission_id: uuid::Uuid::new_v4().to_string(),
            description,
            options,
        };

        tracing::info!(
            "Requesting permission for session {:?}: {}",
            args.session_id,
            card.description
        );

        // 发送权限审批请求，带超时
        let decision = timeout(
            PERMISSION_TIMEOUT,
            self.channel.send_permission_card(&chat_id, card.clone()),
        )
        .await
        .map_err(|_| acp::Error::invalid_params())?
        .map_err(|_| acp::Error::invalid_params())?;

        tracing::info!("Permission decision: {:?}", decision);

        // 转换决策
        let outcome = match decision {
            PermissionDecision::Allow => {
                // 选择第一个选项（通常是 Allow）
                if let Some(option) = args.options.first() {
                    acp::RequestPermissionOutcome::Selected(acp::SelectedPermissionOutcome::new(
                        option.option_id.clone(),
                    ))
                } else {
                    acp::RequestPermissionOutcome::Cancelled
                }
            }
            PermissionDecision::Deny => acp::RequestPermissionOutcome::Cancelled,
            PermissionDecision::AllowAll => {
                // 选择第一个选项
                if let Some(option) = args.options.first() {
                    acp::RequestPermissionOutcome::Selected(acp::SelectedPermissionOutcome::new(
                        option.option_id.clone(),
                    ))
                } else {
                    acp::RequestPermissionOutcome::Cancelled
                }
            }
        };

        Ok(acp::RequestPermissionResponse::new(outcome))
    }
}

#[async_trait(?Send)]
impl<C: Channel> acp::Client for AcpClient<C> {
    async fn request_permission(
        &self,
        args: acp::RequestPermissionRequest,
    ) -> acp::Result<acp::RequestPermissionResponse> {
        self.request_permission_via_channel(args).await
    }

    async fn write_text_file(
        &self,
        args: acp::WriteTextFileRequest,
    ) -> acp::Result<acp::WriteTextFileResponse> {
        let path = Path::new(&args.path);

        // 边界检查
        self.file_boundary
            .validate_path(path, FileOp::Write)
            .map_err(|_| acp::Error::invalid_params())?;

        tracing::info!("Writing file: {:?}", path);

        // 执行文件写入
        tokio::fs::write(path, &args.content)
            .await
            .map_err(|_| acp::Error::invalid_params())?;

        Ok(acp::WriteTextFileResponse::default())
    }

    async fn read_text_file(
        &self,
        args: acp::ReadTextFileRequest,
    ) -> acp::Result<acp::ReadTextFileResponse> {
        let path = Path::new(&args.path);

        // 边界检查
        self.file_boundary
            .validate_path(path, FileOp::Read)
            .map_err(|_| acp::Error::invalid_params())?;

        tracing::info!("Reading file: {:?}", path);

        // 执行文件读取
        let content = tokio::fs::read_to_string(path)
            .await
            .map_err(|_| acp::Error::invalid_params())?;

        Ok(acp::ReadTextFileResponse::new(content))
    }

    async fn create_terminal(
        &self,
        args: acp::CreateTerminalRequest,
    ) -> acp::Result<acp::CreateTerminalResponse> {
        // 边界检查命令（command 是必需的 String）
        self.terminal_boundary
            .validate_command(&args.command)
            .map_err(|_| acp::Error::invalid_params())?;

        tracing::info!("Creating terminal: {} {:?}", args.command, args.args);

        // TODO: 实现真实的终端创建
        // 目前返回一个假的 terminal id
        Ok(acp::CreateTerminalResponse::new(acp::TerminalId::new(
            uuid::Uuid::new_v4().to_string(),
        )))
    }

    async fn terminal_output(
        &self,
        args: acp::TerminalOutputRequest,
    ) -> acp::Result<acp::TerminalOutputResponse> {
        tracing::info!("Terminal output request: {:?}", args.terminal_id);

        // TODO: 实现真实的终端输出获取
        Ok(acp::TerminalOutputResponse::new(String::new(), false))
    }

    async fn release_terminal(
        &self,
        args: acp::ReleaseTerminalRequest,
    ) -> acp::Result<acp::ReleaseTerminalResponse> {
        tracing::info!("Releasing terminal: {:?}", args.terminal_id);

        // TODO: 实现真实的终端释放
        Ok(acp::ReleaseTerminalResponse::default())
    }

    async fn wait_for_terminal_exit(
        &self,
        args: acp::WaitForTerminalExitRequest,
    ) -> acp::Result<acp::WaitForTerminalExitResponse> {
        tracing::info!("Waiting for terminal exit: {:?}", args.terminal_id);

        // TODO: 实现真实的等待
        Ok(acp::WaitForTerminalExitResponse::new(
            acp::TerminalExitStatus::default(),
        ))
    }

    async fn kill_terminal(
        &self,
        args: acp::KillTerminalRequest,
    ) -> acp::Result<acp::KillTerminalResponse> {
        tracing::info!("Killing terminal: {:?}", args.terminal_id);

        // TODO: 实现真实的终端终止
        Ok(acp::KillTerminalResponse::default())
    }

    async fn session_notification(
        &self,
        args: acp::SessionNotification,
    ) -> acp::Result<()> {
        let chat_id = self.get_chat_id().await.map_err(|_| {
            acp::Error::invalid_params()
        })?;

        match &args.update {
            acp::SessionUpdate::AgentMessageChunk(chunk) => {
                // 流式消息 - 发送到 Channel
                if let acp::ContentBlock::Text(text) = &chunk.content {
                    self.channel
                        .send_stream(&chat_id, &text.text)
                        .await
                        .map_err(|_| acp::Error::invalid_params())?;
                }
            }
            acp::SessionUpdate::ToolCall(tool_call) => {
                // 工具调用 - 发送提示到 Channel
                tracing::info!("Tool call: {}", tool_call.title);
                let tool_hint = tool_call.title.clone();
                if let Err(e) = self.channel.send_tool_hint(&chat_id, &tool_hint).await {
                    tracing::warn!("Failed to send tool hint: {:?}", e);
                }
            }
            acp::SessionUpdate::ToolCallUpdate(update) => {
                tracing::debug!("Tool call update: {:?}", update.tool_call_id);
            }
            acp::SessionUpdate::Plan(plan) => {
                tracing::debug!("Plan update: {} entries", plan.entries.len());
            }
            other => {
                tracing::debug!("Session update: {:?}", other);
            }
        }

        Ok(())
    }

    async fn ext_method(&self, args: acp::ExtRequest) -> acp::Result<acp::ExtResponse> {
        tracing::warn!("Unsupported ext method: {}", args.method);
        Err(acp::Error::method_not_found())
    }

    async fn ext_notification(&self, args: acp::ExtNotification) -> acp::Result<()> {
        tracing::warn!("Unsupported ext notification: {}", args.method);
        Err(acp::Error::method_not_found())
    }
}

/// 格式化权限请求描述
fn format_permission_description(args: &acp::RequestPermissionRequest) -> String {
    // tool_call 是 ToolCallUpdate 类型
    if let Some(title) = &args.tool_call.fields.title {
        return format!("Tool: {}", title);
    }

    // 尝试从选项中获取描述
    if let Some(option) = args.options.first() {
        format!("Permission: {}", option.name)
    } else {
        "Permission request".to_string()
    }
}