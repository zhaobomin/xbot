//! Channel Trait - 多平台统一接口定义

use async_trait::async_trait;
use futures::channel::mpsc::Receiver;
use serde::{Deserialize, Serialize};
use std::fmt;

use crate::Result;

/// Channel trait - 定义消息平台的统一接口
#[async_trait]
pub trait Channel: Send + Sync {
    /// Channel 名称
    fn name(&self) -> &str;

    /// 启动 Channel
    async fn start(&self) -> Result<()>;

    /// 停止 Channel
    async fn stop(&self) -> Result<()>;

    /// 发送消息
    async fn send_message(&self, chat_id: &str, content: &str) -> Result<()>;

    /// 发送流式消息（追加）
    async fn send_stream(&self, chat_id: &str, content: &str) -> Result<()>;

    /// 结束流式消息
    async fn finalize_stream(&self, chat_id: &str) -> Result<()>;

    /// 发送权限审批卡片
    async fn send_permission_card(
        &self,
        chat_id: &str,
        card: PermissionCard,
    ) -> Result<PermissionDecision>;

    /// 发送终端输出
    async fn send_terminal_output(
        &self,
        chat_id: &str,
        terminal_id: &str,
        output: &str,
    ) -> Result<()>;

    /// 获取消息接收流
    fn message_stream(&self) -> Receiver<ChannelMessage>;

    /// 添加消息表情回应（如点赞）
    async fn add_reaction(&self, _message_id: &str, _emoji_type: &str) -> Result<()> {
        // 默认实现：不支持
        Ok(())
    }

    /// 发送工具调用提示卡片
    async fn send_tool_hint(&self, chat_id: &str, tool_hint: &str) -> Result<()> {
        // 默认实现：发送为普通消息
        self.send_message(chat_id, &format!("🔧 Tool: {}", tool_hint)).await
    }
}

/// Channel 消息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChannelMessage {
    pub chat_id: String,
    pub content: String,
    pub media: Vec<MediaContent>,
    /// 原始消息 ID（用于回复、点赞等操作）
    #[serde(default)]
    pub message_id: Option<String>,
}

/// 媒体内容
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MediaContent {
    pub media_type: MediaType,
    pub data: String, // URL 或 base64
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum MediaType {
    Image,
    File,
    Audio,
}

/// 权限审批卡片
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PermissionCard {
    pub permission_id: String,
    pub description: String,
    pub options: Vec<String>, // ["Allow", "Deny", "AllowAll"]
}

/// 权限决策
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PermissionDecision {
    Allow,
    Deny,
    AllowAll,
}

impl fmt::Display for PermissionDecision {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PermissionDecision::Allow => write!(f, "allow"),
            PermissionDecision::Deny => write!(f, "deny"),
            PermissionDecision::AllowAll => write!(f, "allow_all"),
        }
    }
}