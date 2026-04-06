//! 会话管理 - chat_id 与 session_id 映射

use std::collections::HashMap;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use agent_client_protocol::SessionId;

/// 会话超时时间（无活动后清理）
const SESSION_TIMEOUT: Duration = Duration::from_secs(3600); // 1 hour

/// 会话信息
#[derive(Debug)]
pub struct AcpSession {
    /// Agent 会话 ID
    pub session_id: SessionId,
    /// Channel 会话 ID（如飞书 chat_id）
    pub chat_id: String,
    /// 创建时间
    pub created_at: Instant,
    /// 最后活动时间
    pub last_activity: Instant,
}

impl AcpSession {
    /// 更新最后活动时间
    pub fn touch(&mut self) {
        self.last_activity = Instant::now();
    }

    /// 检查会话是否超时
    pub fn is_expired(&self) -> bool {
        self.last_activity.elapsed() > SESSION_TIMEOUT
    }
}

/// 会话管理器
pub struct SessionManager {
    /// chat_id -> AcpSession 映射
    sessions: HashMap<String, AcpSession>,
    /// 工作目录
    cwd: PathBuf,
}

impl SessionManager {
    /// 创建新的会话管理器
    pub fn new(cwd: PathBuf) -> Self {
        Self {
            sessions: HashMap::new(),
            cwd,
        }
    }

    /// 注册新会话
    pub fn register(&mut self, chat_id: String, session_id: SessionId) {
        let session = AcpSession {
            session_id,
            chat_id: chat_id.clone(),
            created_at: Instant::now(),
            last_activity: Instant::now(),
        };
        self.sessions.insert(chat_id, session);
        tracing::debug!("Session registered, total: {}", self.sessions.len());
    }

    /// 获取会话
    pub fn get(&self, chat_id: &str) -> Option<&AcpSession> {
        self.sessions.get(chat_id)
    }

    /// 获取可变会话
    pub fn get_mut(&mut self, chat_id: &str) -> Option<&mut AcpSession> {
        self.sessions.get_mut(chat_id)
    }

    /// 更新会话活动时间
    pub fn touch(&mut self, chat_id: &str) {
        if let Some(session) = self.sessions.get_mut(chat_id) {
            session.touch();
        }
    }

    /// 移除会话
    pub fn remove(&mut self, chat_id: &str) -> Option<AcpSession> {
        let session = self.sessions.remove(chat_id);
        if session.is_some() {
            tracing::debug!("Session removed, total: {}", self.sessions.len());
        }
        session
    }

    /// 清理过期会话
    pub fn cleanup_expired(&mut self) -> usize {
        let before = self.sessions.len();
        self.sessions.retain(|_, session| !session.is_expired());
        let removed = before - self.sessions.len();
        if removed > 0 {
            tracing::info!("Cleaned up {} expired sessions", removed);
        }
        removed
    }

    /// 获取会话数量
    pub fn len(&self) -> usize {
        self.sessions.len()
    }

    /// 检查是否为空
    pub fn is_empty(&self) -> bool {
        self.sessions.is_empty()
    }

    /// 获取工作目录
    pub fn cwd(&self) -> &PathBuf {
        &self.cwd
    }

    /// 遍历所有会话
    pub fn iter(&self) -> impl Iterator<Item = (&String, &AcpSession)> {
        self.sessions.iter()
    }
}