//! WebSocket 连接管理
//!
//! 飞书 WebSocket 使用 protobuf 协议，参考 lark_oapi SDK 实现

use std::sync::Arc;

use futures::{SinkExt, StreamExt};
use prost::Message;
use tokio::sync::mpsc;
use tokio_tungstenite::{connect_async, tungstenite::Message as WsMessage};

use crate::channel::FeishuConfig;

/// FrameType - 消息类型
const FRAME_TYPE_CONTROL: i32 = 1;
const FRAME_TYPE_DATA: i32 = 2;

/// MessageType - header 中的 type 值
const MESSAGE_TYPE_EVENT: &str = "event";
const MESSAGE_TYPE_PING: &str = "ping";
const MESSAGE_TYPE_PONG: &str = "pong";

/// Header keys
const HEADER_TYPE: &str = "type";

// ========== Protobuf 定义 ==========

/// Header - key-value pair
#[derive(Clone, PartialEq, Message)]
pub struct Header {
    #[prost(string, tag = "1")]
    pub key: String,
    #[prost(string, tag = "2")]
    pub value: String,
}

/// Frame - 飞书 WebSocket 消息帧
#[derive(Clone, PartialEq, Message)]
pub struct Frame {
    #[prost(uint64, tag = "1")]
    pub seq_id: u64,
    #[prost(uint64, tag = "2")]
    pub log_id: u64,
    #[prost(int32, tag = "3")]
    pub service: i32,
    #[prost(int32, tag = "4")]
    pub method: i32,
    #[prost(message, repeated, tag = "5")]
    pub headers: Vec<Header>,
    #[prost(string, optional, tag = "6")]
    pub payload_encoding: Option<String>,
    #[prost(string, optional, tag = "7")]
    pub payload_type: Option<String>,
    #[prost(bytes, optional, tag = "8")]
    pub payload: Option<Vec<u8>>,
}

/// 飞书事件（解析后的 JSON payload）
#[derive(Debug, Clone)]
pub struct FeishuEvent {
    pub event_type: String,
    pub payload: serde_json::Value,
}

/// 飞书 WebSocket 连接管理器
pub struct FeishuWebSocket {
    #[allow(dead_code)]
    config: Arc<FeishuConfig>,
}

impl FeishuWebSocket {
    pub fn new(config: Arc<FeishuConfig>) -> Self {
        Self { config }
    }

    /// 启动 WebSocket 连接
    pub async fn start(
        &self,
        event_tx: mpsc::Sender<FeishuEvent>,
        wss_url: String,
    ) -> Result<(), String> {
        tracing::info!("Connecting to Feishu WebSocket...");

        // 连接 WebSocket
        let (ws_stream, _) = connect_async(&wss_url)
            .await
            .map_err(|e| format!("WebSocket connect failed: {}", e))?;

        tracing::info!("WebSocket connected");

        let (mut write, mut read) = ws_stream.split();

        // 从 URL 解析 service_id 用于 ping
        let service_id = Self::parse_service_id(&wss_url);

        // 心跳任务
        let heartbeat_task = async move {
            let mut interval = tokio::time::interval(std::time::Duration::from_secs(90));
            loop {
                interval.tick().await;
                let ping_frame = Self::create_ping_frame(service_id);
                if write
                    .send(WsMessage::Binary(ping_frame.encode_to_vec()))
                    .await
                    .is_err()
                {
                    tracing::error!("Failed to send ping");
                    break;
                }
                tracing::debug!("Sent ping frame");
            }
        };

        // 接收任务
        let read_task = async move {
            while let Some(msg) = read.next().await {
                match msg {
                    Ok(WsMessage::Binary(data)) => {
                        if let Some(event) = Self::handle_frame(&data) {
                            tracing::info!("Received event: {}", event.event_type);
                            if event_tx.send(event).await.is_err() {
                                tracing::error!("Failed to forward event");
                                break;
                            }
                        }
                    }
                    Ok(WsMessage::Close(_)) => {
                        tracing::warn!("WebSocket closed");
                        break;
                    }
                    Err(e) => {
                        tracing::error!("WebSocket error: {}", e);
                        break;
                    }
                    _ => {}
                }
            }
        };

        // 并行运行
        tokio::spawn(async move {
            tokio::select! {
                _ = heartbeat_task => {}
                _ = read_task => {}
            }
        });

        Ok(())
    }

    /// 从 URL 解析 service_id
    fn parse_service_id(url: &str) -> i32 {
        // URL 格式: wss://xxx?service_id=xxx
        if let Some(pos) = url.find("service_id=") {
            let start = pos + "service_id=".len();
            let end = url[start..].find('&').unwrap_or(url[start..].len());
            url[start..start + end].parse().unwrap_or(0)
        } else {
            0
        }
    }

    /// 创建 ping frame
    fn create_ping_frame(service_id: i32) -> Frame {
        Frame {
            seq_id: 0,
            log_id: 0,
            service: service_id,
            method: FRAME_TYPE_CONTROL,
            headers: vec![Header {
                key: HEADER_TYPE.to_string(),
                value: MESSAGE_TYPE_PING.to_string(),
            }],
            payload_encoding: None,
            payload_type: None,
            payload: None,
        }
    }

    /// 处理接收到的 frame
    fn handle_frame(data: &[u8]) -> Option<FeishuEvent> {
        let frame = Frame::decode(data).ok()?;
        tracing::debug!(
            "Received frame: method={}, headers={}",
            frame.method,
            frame.headers.len()
        );

        // 获取 header 中的 type
        let msg_type = frame
            .headers
            .iter()
            .find(|h| h.key == HEADER_TYPE)
            .map(|h| h.value.as_str());

        match msg_type {
            Some(MESSAGE_TYPE_PONG) => {
                tracing::debug!("Received pong");
                None
            }
            Some(MESSAGE_TYPE_EVENT) => {
                // 解析 payload
                if let Some(payload) = frame.payload {
                    let payload_str = String::from_utf8(payload).ok()?;
                    let json: serde_json::Value = serde_json::from_str(&payload_str).ok()?;

                    // 飞书事件格式: {"header":{"event_type":"xxx"}, "event":{...}}
                    let event_type = json
                        .get("header")
                        .and_then(|h: &serde_json::Value| h.get("event_type"))
                        .and_then(|t: &serde_json::Value| t.as_str())
                        .unwrap_or("unknown");

                    Some(FeishuEvent {
                        event_type: event_type.to_string(),
                        payload: json,
                    })
                } else {
                    None
                }
            }
            Some(MESSAGE_TYPE_PING) => {
                // 忽略 ping
                None
            }
            _ => {
                tracing::debug!("Unknown message type: {:?}", msg_type);
                None
            }
        }
    }
}