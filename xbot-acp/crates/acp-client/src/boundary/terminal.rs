//! 终端执行边界检查

use std::time::Duration;

use regex::Regex;

use crate::{AppError, Result};

/// 终端边界配置
pub struct TerminalBoundary {
    /// 阻止的危险命令模式
    blocked_patterns: Vec<Regex>,
    /// 最大执行时间
    max_execution_time: Duration,
}

impl TerminalBoundary {
    /// 创建默认边界配置
    pub fn new() -> Self {
        Self {
            blocked_patterns: vec![
                Regex::new(r"rm\s+-rf\s+/").unwrap(),
                Regex::new(r"dd\s+if=/dev/zero").unwrap(),
                Regex::new(r":\(\)\{ :\|:& ;}").unwrap(), // fork bomb
            ],
            max_execution_time: Duration::from_secs(1800), // 30min
        }
    }

    /// 验证命令是否允许执行
    pub fn validate_command(&self, cmd: &str) -> Result<()> {
        for pattern in &self.blocked_patterns {
            if pattern.is_match(cmd) {
                return Err(AppError::ProtocolError {
                    message: format!("Command blocked: {}", cmd),
                });
            }
        }
        Ok(())
    }

    /// 获取最大执行时间
    pub fn max_execution_time(&self) -> Duration {
        self.max_execution_time
    }
}

impl Default for TerminalBoundary {
    fn default() -> Self {
        Self::new()
    }
}