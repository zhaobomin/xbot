//! 流式响应管理

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use tokio::sync::RwLock;

/// 流式消息更新间隔
const STREAM_UPDATE_INTERVAL: Duration = Duration::from_millis(500);

/// 流式消息内容
struct StreamContent {
    content: String,
    message_id: Option<String>,
    last_update: Instant,
}

/// 流式响应管理器
pub struct FeishuStreamManager {
    streams: Arc<RwLock<HashMap<String, StreamContent>>>,
}

impl FeishuStreamManager {
    pub fn new() -> Self {
        Self {
            streams: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// 开始新的流式消息
    pub async fn start(&self, chat_id: &str) {
        let mut streams = self.streams.write().await;
        streams.insert(
            chat_id.to_string(),
            StreamContent {
                content: String::new(),
                message_id: None,
                last_update: Instant::now(),
            },
        );
    }

    /// 追加内容
    pub async fn append(&self, chat_id: &str, content: &str) {
        let mut streams = self.streams.write().await;
        if let Some(stream) = streams.get_mut(chat_id) {
            stream.content.push_str(content);
        }
    }

    /// 获取当前内容
    pub async fn get_content(&self, chat_id: &str) -> Option<String> {
        let streams = self.streams.read().await;
        streams.get(chat_id).map(|s| s.content.clone())
    }

    /// 设置消息 ID
    pub async fn set_message_id(&self, chat_id: &str, message_id: String) {
        let mut streams = self.streams.write().await;
        if let Some(stream) = streams.get_mut(chat_id) {
            stream.message_id = Some(message_id);
        }
    }

    /// 获取消息 ID
    pub async fn get_message_id(&self, chat_id: &str) -> Option<String> {
        let streams = self.streams.read().await;
        streams.get(chat_id).and_then(|s| s.message_id.clone())
    }

    /// 检查是否应该更新（第一次总是返回 true）
    pub async fn should_update(&self, chat_id: &str) -> bool {
        let streams = self.streams.read().await;
        if let Some(stream) = streams.get(chat_id) {
            // 第一次发送或间隔足够长
            stream.message_id.is_none() || stream.last_update.elapsed() >= STREAM_UPDATE_INTERVAL
        } else {
            false
        }
    }

    /// 标记已更新
    pub async fn mark_updated(&self, chat_id: &str) {
        let mut streams = self.streams.write().await;
        if let Some(stream) = streams.get_mut(chat_id) {
            stream.last_update = Instant::now();
        }
    }

    /// 结束流式消息
    pub async fn end(&self, chat_id: &str) -> Option<(String, Option<String>)> {
        let mut streams = self.streams.write().await;
        streams.remove(chat_id).map(|s| (s.content, s.message_id))
    }
}

impl Default for FeishuStreamManager {
    fn default() -> Self {
        Self::new()
    }
}