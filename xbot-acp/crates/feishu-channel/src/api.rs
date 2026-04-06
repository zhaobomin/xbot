//! 飞书 API 客户端

use std::sync::Arc;
use std::time::{Duration, Instant};

use reqwest::Client;
use serde::Deserialize;

use crate::channel::FeishuConfig;

/// access_token 缓存时间
const TOKEN_EXPIRE_MARGIN: Duration = Duration::from_secs(300);

/// Access Token 响应（飞书 API 直接返回在根级别）
#[derive(Debug, Deserialize)]
pub struct TokenResponse {
    pub code: i32,
    pub msg: String,
    pub app_access_token: String,
    pub expire: i32,
}

/// WebSocket Endpoint 响应（lark_oapi SDK 格式）
#[derive(Debug, Deserialize)]
pub struct WsEndpointResponse {
    pub code: i32,
    #[serde(default)]
    pub msg: Option<String>,
    pub data: Option<WsEndpointData>,
}

#[derive(Debug, Deserialize)]
pub struct WsEndpointData {
    #[serde(rename = "URL")]
    pub URL: String,
}

/// WebSocket URL 响应（旧格式，保留）
#[derive(Debug, Deserialize)]
pub struct WsUrlResponse {
    pub code: i32,
    pub msg: String,
    pub data: Option<WsUrlData>,
}

#[derive(Debug, Deserialize)]
pub struct WsUrlData {
    pub url: String,
}

/// 发送消息响应
#[derive(Debug, Deserialize)]
pub struct SendMessageResponse {
    pub code: i32,
    pub msg: String,
    pub data: Option<SendMessageData>,
}

#[derive(Debug, Deserialize)]
pub struct SendMessageData {
    pub message_id: String,
}

/// 飞书 API 客户端
pub struct FeishuApi {
    config: Arc<FeishuConfig>,
    client: Client,
    access_token: Option<String>,
    token_expire_at: Option<Instant>,
}

impl FeishuApi {
    pub fn new(config: Arc<FeishuConfig>) -> Self {
        Self {
            config,
            client: Client::new(),
            access_token: None,
            token_expire_at: None,
        }
    }

    /// 获取 access_token
    pub async fn get_access_token(&mut self) -> Result<String, String> {
        // 检查缓存
        if let (Some(token), Some(expire_at)) = (&self.access_token, self.token_expire_at) {
            if expire_at > Instant::now() {
                return Ok(token.clone());
            }
        }

        let url = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal";
        let body = serde_json::json!({
            "app_id": self.config.app_id,
            "app_secret": self.config.app_secret,
        });

        let resp = self.client
            .post(url)
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("Request failed: {}", e))?;

        let result: TokenResponse = resp
            .json()
            .await
            .map_err(|e| format!("Parse failed: {}", e))?;

        if result.code != 0 {
            return Err(format!("API error: {} - {}", result.code, result.msg));
        }

        self.access_token = Some(result.app_access_token.clone());
        self.token_expire_at = Some(
            Instant::now() + Duration::from_secs(result.expire as u64) - TOKEN_EXPIRE_MARGIN,
        );

        tracing::debug!("Obtained new access token");
        Ok(result.app_access_token)
    }

    /// 获取 WebSocket URL（使用 lark_oapi SDK 的 endpoint）
    pub async fn get_ws_url(&mut self) -> Result<String, String> {
        // 直接使用 AppID/AppSecret 获取 WebSocket URL
        // 参考 lark_oapi SDK: POST /callback/ws/endpoint
        let url = "https://open.feishu.cn/callback/ws/endpoint";
        let body = serde_json::json!({
            "AppID": self.config.app_id,
            "AppSecret": self.config.app_secret,
        });

        let resp = self.client
            .post(url)
            .header("locale", "zh")
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("Request failed: {}", e))?;

        // 先获取原始响应文本用于调试
        let resp_text = resp.text().await.map_err(|e| format!("Read response failed: {}", e))?;
        tracing::info!("WS endpoint API response: {}", resp_text);

        // lark_oapi 返回格式: {"code":0,"data":{"URL":"wss://..."}}
        let result: WsEndpointResponse = serde_json::from_str(&resp_text)
            .map_err(|e| format!("Parse failed: {} (response: {})", e, resp_text))?;

        if result.code != 0 {
            return Err(format!("API error: {}", result.code));
        }

        let data = result.data.ok_or("No endpoint data")?;
        Ok(data.URL)
    }

    /// 发送文本消息
    pub async fn send_text_message(&mut self, chat_id: &str, text: &str) -> Result<String, String> {
        let token = self.get_access_token().await?;

        let url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id";
        let body = serde_json::json!({
            "receive_id": chat_id,
            "msg_type": "text",
            "content": serde_json::to_string(&serde_json::json!({"text": text})).unwrap(),
        });

        let resp = self.client
            .post(url)
            .header("Authorization", format!("Bearer {}", token))
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("Request failed: {}", e))?;

        let result: SendMessageResponse = resp
            .json()
            .await
            .map_err(|e| format!("Parse failed: {}", e))?;

        if result.code != 0 {
            return Err(format!("API error: {} - {}", result.code, result.msg));
        }

        Ok(result.data.map(|d| d.message_id).unwrap_or_default())
    }

    /// 发送卡片消息
    pub async fn send_card_message(&mut self, chat_id: &str, card: serde_json::Value) -> Result<String, String> {
        let token = self.get_access_token().await?;

        let url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id";
        let body = serde_json::json!({
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": serde_json::to_string(&card).unwrap(),
        });

        let resp = self.client
            .post(url)
            .header("Authorization", format!("Bearer {}", token))
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("Request failed: {}", e))?;

        let result: SendMessageResponse = resp
            .json()
            .await
            .map_err(|e| format!("Parse failed: {}", e))?;

        if result.code != 0 {
            return Err(format!("API error: {} - {}", result.code, result.msg));
        }

        Ok(result.data.map(|d| d.message_id).unwrap_or_default())
    }

    /// 更新消息
    pub async fn update_message(&mut self, message_id: &str, content: serde_json::Value) -> Result<(), String> {
        let token = self.get_access_token().await?;

        let url = format!(
            "https://open.feishu.cn/open-apis/im/v1/messages/{}",
            message_id
        );

        let resp = self.client
            .patch(&url)
            .header("Authorization", format!("Bearer {}", token))
            .json(&serde_json::json!({"content": serde_json::to_string(&content).unwrap()}))
            .send()
            .await
            .map_err(|e| format!("Request failed: {}", e))?;

        let result: SendMessageResponse = resp
            .json()
            .await
            .map_err(|e| format!("Parse failed: {}", e))?;

        if result.code != 0 {
            return Err(format!("API error: {} - {}", result.code, result.msg));
        }

        Ok(())
    }

    /// 添加消息表情回应（点赞）
    pub async fn add_reaction(&mut self, message_id: &str, emoji_type: &str) -> Result<(), String> {
        let token = self.get_access_token().await?;

        let url = format!(
            "https://open.feishu.cn/open-apis/im/v1/messages/{}/reactions",
            message_id
        );

        let body = serde_json::json!({
            "reaction_type": {
                "emoji_type": emoji_type
            }
        });

        let resp = self.client
            .post(&url)
            .header("Authorization", format!("Bearer {}", token))
            .json(&body)
            .send()
            .await
            .map_err(|e| format!("Request failed: {}", e))?;

        let resp_text = resp.text().await.map_err(|e| format!("Read response failed: {}", e))?;
        tracing::debug!("Reaction API response: {}", resp_text);

        // 简单解析，只要 code == 0 就算成功
        if resp_text.contains("\"code\":0") {
            tracing::info!("Added {} reaction to message {}", emoji_type, message_id);
            return Ok(());
        }

        // 尝试解析错误
        if let Ok(result) = serde_json::from_str::<serde_json::Value>(&resp_text) {
            if let Some(code) = result.get("code").and_then(|c| c.as_i64()) {
                if code == 0 {
                    tracing::info!("Added {} reaction to message {}", emoji_type, message_id);
                    return Ok(());
                }
                return Err(format!("API error: {} - {}", code, result.get("msg").and_then(|m| m.as_str()).unwrap_or("unknown")));
            }
        }

        Err(format!("Unexpected response: {}", resp_text))
    }
}