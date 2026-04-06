//! 消息路由

pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

/// 消息路由器
pub struct Router;

impl Router {
    pub async fn run(&mut self) -> Result<()> {
        // TODO: 实现消息路由逻辑
        tracing::info!("Router running...");
        Ok(())
    }
}