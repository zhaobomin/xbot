//! 文件操作边界检查

use std::path::{Path, PathBuf};

use crate::{AppError, Result};

/// 文件操作类型
#[derive(Debug, Clone, Copy)]
pub enum FileOp {
    Read,
    Write,
    Delete,
}

/// 文件边界配置
pub struct FileBoundary {
    /// 阻止的系统关键路径
    blocked_paths: Vec<PathBuf>,
}

impl FileBoundary {
    /// 创建默认边界配置
    pub fn new() -> Self {
        Self {
            blocked_paths: vec![
                PathBuf::from("/etc"),
                PathBuf::from("/System"),
                PathBuf::from("/usr"),
                PathBuf::from("/bin"),
                PathBuf::from("/sbin"),
            ],
        }
    }

    /// 验证路径是否允许访问
    pub fn validate_path(&self, path: &Path, _op: FileOp) -> Result<()> {
        // canonicalize 获取真实路径
        let canonical = path
            .canonicalize()
            .or_else(|_| Ok::<PathBuf, std::io::Error>(path.to_path_buf()))?;

        // 检查是否为系统关键路径
        for blocked in &self.blocked_paths {
            if canonical == *blocked || canonical.starts_with(blocked) {
                return Err(AppError::ProtocolError {
                    message: format!("Path blocked: {}", path.display()),
                });
            }
        }
        Ok(())
    }
}

impl Default for FileBoundary {
    fn default() -> Self {
        Self::new()
    }
}