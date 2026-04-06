//! Feishu Channel 实现

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use acp_client::{AppError, Channel, ChannelMessage, PermissionCard, PermissionDecision};
use async_trait::async_trait;
use futures::channel::mpsc::{channel, Receiver, Sender};
use tokio::sync::{broadcast, mpsc as tokio_mpsc, RwLock};

use crate::api::FeishuApi;
use crate::card;
use crate::message::parse_message_event;
use crate::stream::FeishuStreamManager;
use crate::websocket::FeishuWebSocket;

/// 权限审批超时
const PERMISSION_TIMEOUT: Duration = Duration::from_secs(30);

/// 飞书配置
#[derive(Debug, Clone)]
pub struct FeishuConfig {
    pub app_id: String,
    pub app_secret: String,
    pub encrypt_key: String,
    pub verification_token: String,
    pub bot_open_id: String,
}

/// 待处理的权限请求
struct PendingPermission {
    sender: tokio::sync::oneshot::Sender<PermissionDecision>,
    created_at: Instant,
}

/// 飞书 Channel 实现
pub struct FeishuChannel {
    config: Arc<FeishuConfig>,
    api: RwLock<FeishuApi>,
    stream_manager: FeishuStreamManager,
    // 使用 broadcast channel 支持多个订阅者
    message_broadcast: broadcast::Sender<ChannelMessage>,
    pending_permissions: RwLock<HashMap<String, PendingPermission>>,
}

impl FeishuChannel {
    /// 创建飞书 Channel
    pub fn new(config: FeishuConfig) -> Self {
        let config = Arc::new(config);
        let api = FeishuApi::new(Arc::clone(&config));
        let (tx, _rx) = broadcast::channel::<ChannelMessage>(100);

        Self {
            config,
            api: RwLock::new(api),
            stream_manager: FeishuStreamManager::new(),
            message_broadcast: tx,
            pending_permissions: RwLock::new(HashMap::new()),
        }
    }
}

#[async_trait]
impl Channel for FeishuChannel {
    fn name(&self) -> &str {
        "feishu"
    }

    async fn start(&self) -> Result<(), AppError> {
        tracing::info!("Starting Feishu channel...");

        // 获取 WebSocket URL
        let wss_url = self
            .api
            .write()
            .await
            .get_ws_url()
            .await
            .map_err(|e| AppError::ProtocolError {
                message: format!("Failed to get WS URL: {}", e),
            })?;

        // 创建事件通道
        let (event_tx, mut event_rx) = tokio_mpsc::channel(100);

        // 启动 WebSocket
        let ws = FeishuWebSocket::new(Arc::clone(&self.config));
        ws.start(event_tx, wss_url)
            .await
            .map_err(|e| AppError::ProtocolError {
                message: format!("Failed to start WebSocket: {}", e),
            })?;

        // 处理事件
        let message_broadcast = self.message_broadcast.clone();
        tokio::spawn(async move {
            while let Some(event) = event_rx.recv().await {
                tracing::info!("Event type: {}, payload: {}", event.event_type, event.payload);

                if event.event_type == "im.message.receive_v1" {
                    if let Some(msg) = parse_message_event(&event.payload) {
                        tracing::info!("Parsed message: chat_id={}, content={}", msg.chat_id, msg.content);
                        if message_broadcast.send(msg).is_err() {
                            tracing::warn!("Failed to forward message");
                        }
                    } else {
                        tracing::warn!("Failed to parse message event");
                    }
                }
            }
        });

        tracing::info!("Feishu channel started");
        Ok(())
    }

    async fn stop(&self) -> Result<(), AppError> {
        tracing::info!("Stopping Feishu channel...");
        Ok(())
    }

    async fn send_message(&self, chat_id: &str, content: &str) -> Result<(), AppError> {
        self.api
            .write()
            .await
            .send_text_message(chat_id, content)
            .await
            .map_err(|e| AppError::ProtocolError {
                message: format!("Failed to send message: {}", e),
            })?;
        Ok(())
    }

    async fn send_stream(&self, chat_id: &str, content: &str) -> Result<(), AppError> {
        // 确保流已启动
        self.stream_manager.start(chat_id).await;
        self.stream_manager.append(chat_id, content).await;

        let full_content = self
            .stream_manager
            .get_content(chat_id)
            .await
            .unwrap_or_default();

        if let Some(message_id) = self.stream_manager.get_message_id(chat_id).await {
            let card = card::stream_card(&full_content);
            self.api
                .write()
                .await
                .update_message(&message_id, card)
                .await
                .map_err(|e| AppError::ProtocolError {
                    message: format!("Failed to update: {}", e),
                })?;
        } else if self.stream_manager.should_update(chat_id).await {
            let card = card::stream_card(&full_content);
            let message_id = self
                .api
                .write()
                .await
                .send_card_message(chat_id, card)
                .await
                .map_err(|e| AppError::ProtocolError {
                    message: format!("Failed to send: {}", e),
                })?;

            self.stream_manager
                .set_message_id(chat_id, message_id)
                .await;
            self.stream_manager.mark_updated(chat_id).await;
        }

        Ok(())
    }

    async fn finalize_stream(&self, chat_id: &str) -> Result<(), AppError> {
        if let Some((content, message_id)) = self.stream_manager.end(chat_id).await {
            if let Some(msg_id) = message_id {
                let card = card::stream_card(&content);
                self.api
                    .write()
                    .await
                    .update_message(&msg_id, card)
                    .await
                    .map_err(|e| AppError::ProtocolError {
                        message: format!("Failed to finalize: {}", e),
                    })?;
            } else {
                self.send_message(chat_id, &content).await?;
            }
        }
        Ok(())
    }

    async fn send_permission_card(
        &self,
        chat_id: &str,
        card: PermissionCard,
    ) -> Result<PermissionDecision, AppError> {
        let (tx, rx) = tokio::sync::oneshot::channel();

        {
            let mut pending = self.pending_permissions.write().await;
            pending.insert(
                card.permission_id.clone(),
                PendingPermission {
                    sender: tx,
                    created_at: Instant::now(),
                },
            );
        }

        let feishu_card = card::permission_card(&card.permission_id, &card.description, &card.options);

        self.api
            .write()
            .await
            .send_card_message(chat_id, feishu_card)
            .await
            .map_err(|e| AppError::ProtocolError {
                message: format!("Failed to send permission card: {}", e),
            })?;

        let decision = tokio::time::timeout(PERMISSION_TIMEOUT, rx)
            .await
            .unwrap_or(Ok(PermissionDecision::Deny))
            .unwrap_or(PermissionDecision::Deny);

        {
            let mut pending = self.pending_permissions.write().await;
            pending.remove(&card.permission_id);
        }

        Ok(decision)
    }

    async fn send_terminal_output(
        &self,
        chat_id: &str,
        _terminal_id: &str,
        output: &str,
    ) -> Result<(), AppError> {
        self.send_message(chat_id, &format!("```\n{}\n```", output))
            .await
    }

    fn message_stream(&self) -> Receiver<ChannelMessage> {
        // 创建 mpsc channel 用于返回
        let (tx, rx) = channel(100);
        let mut broadcast_rx = self.message_broadcast.subscribe();

        // 启动任务将 broadcast 消息转发到 mpsc
        tokio::spawn(async move {
            while let Ok(msg) = broadcast_rx.recv().await {
                if tx.clone().try_send(msg).is_err() {
                    break;
                }
            }
        });

        rx
    }

    async fn add_reaction(&self, message_id: &str, emoji_type: &str) -> Result<(), AppError> {
        self.api
            .write()
            .await
            .add_reaction(message_id, emoji_type)
            .await
            .map_err(|e| AppError::ProtocolError {
                message: format!("Failed to add reaction: {}", e),
            })
    }

    async fn send_tool_hint(&self, chat_id: &str, tool_hint: &str) -> Result<(), AppError> {
        // 发送工具调用提示卡片
        let card = serde_json::json!({
            "config": {"wide_screen_mode": true},
            "elements": [
                {
                    "tag": "markdown",
                    "content": format!("**Tool Calls**\n\n```text\n{}\n```", tool_hint)
                }
            ]
        });

        self.api
            .write()
            .await
            .send_card_message(chat_id, card)
            .await
            .map_err(|e| AppError::ProtocolError {
                message: format!("Failed to send tool hint: {}", e),
            })?;

        Ok(())
    }
}