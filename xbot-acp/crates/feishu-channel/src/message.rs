//! 消息解析

use acp_client::ChannelMessage;
use serde::Deserialize;

/// 消息事件
#[derive(Debug, Deserialize)]
pub struct MessageEvent {
    pub sender: MessageSender,
    pub message: MessageContent,
}

#[derive(Debug, Deserialize)]
pub struct MessageSender {
    pub sender_id: SenderId,
}

#[derive(Debug, Deserialize)]
pub struct SenderId {
    pub open_id: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct MessageContent {
    pub chat_id: String,
    pub content: String,
    pub message_id: String,
}

/// 解析飞书消息事件
pub fn parse_message_event(payload: &serde_json::Value) -> Option<ChannelMessage> {
    // 飞书事件格式: {"header":{"event_type":"xxx"}, "event":{"sender":..., "message":...}}
    let event = payload.get("event")?;

    let message: MessageEvent = serde_json::from_value(event.clone()).ok()?;

    // 解析文本内容
    #[derive(Deserialize)]
    struct TextContent {
        text: String,
    }

    let text_content: TextContent = serde_json::from_str(&message.message.content).ok()?;

    Some(ChannelMessage {
        chat_id: message.message.chat_id,
        content: text_content.text,
        media: Vec::new(),
        message_id: Some(message.message.message_id),
    })
}