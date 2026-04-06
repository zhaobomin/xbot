//! 边界处理 - 文件和终端操作限制

mod file;
mod terminal;

pub use file::{FileBoundary, FileOp};
pub use terminal::{TerminalBoundary};