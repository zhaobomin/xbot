# xbot-acp 实施计划

> **基于**: docs/XBOT_ACP_DESIGN.md
> **目标**: 分阶段落地 xbot-acp 项目，从骨架到完整功能

---

## Phase 0: 项目骨架（Day 1）

### P0-1: Cargo Workspace 初始化

**目标**: 建立 3 crate 结构，验证依赖关系

**输入**: 设计文档架构图

**输出**:
```
xbot-acp/
├── Cargo.toml              # workspace 定义
├── crates/
│   ├── acp-client/
│   │   ├── Cargo.toml      # 依赖 agent-client-protocol
│   │   └── src/lib.rs
│   ├── feishu-channel/
│   │   ├── Cargo.toml      # 依赖 acp-client
│   │   └── src/lib.rs
│   └── app/
│   │   ├── Cargo.toml      # 依赖 acp-client, feishu-channel
│   │   └── src/main.rs
├── config/
│   └── default.toml
└── .gitignore
```

**验收**: `cargo build --workspace` 编译通过

**依赖**:
- agent-client-protocol = "0.10.2"
- tokio = { version = "1", features = ["full"] }
- async-trait = "0.1"
- tracing = "0.1"
- thiserror = "1.0"
- serde = { version = "1.0", features = ["derive"] }
- toml = "0.8"

---

### P0-2: 基础类型定义（acp-client/src/types.rs）

**目标**: 定义核心错误类型、状态枚举

**输出**:
```rust
// Error types
pub enum AppError {
    AgentCrashed { reason: String },
    AgentTimeout,
    ChannelDisconnected,
    PermissionTimeout,
    IoError { source: std::io::Error },
}

// State enums
pub enum AgentConnectionState { Disconnected, Connecting, Ready, Active, Processing, Failed }
pub enum FeishuChannelState { Disconnected, Connecting, Connected, Reconnecting, Stopped }
```

**验收**: `cargo build` 编译通过

---

### P0-3: Channel Trait 定义（acp-client/src/channel.rs）

**目标**: 定义多平台统一接口

**输出**:
```rust
pub trait Channel: Send + Sync {
    fn name(&self) -> &str;
    async fn start(&self) -> Result<()>;
    async fn stop(&self) -> Result<()>;
    async fn send_message(&self, chat_id: &str, content: &str) -> Result<()>;
    async fn send_stream(&self, chat_id: &str, content: &str) -> Result<()>;
    async fn finalize_stream(&self, chat_id: &str) -> Result<()>;
    async fn send_permission_card(&self, chat_id: &str, card: PermissionCard) -> Result<PermissionDecision>;
    async fn send_terminal_output(&self, chat_id: &str, terminal_id: &str, output: &str) -> Result<()>;
    fn message_stream(&self) -> Receiver<ChannelMessage>;
}

pub struct ChannelMessage { chat_id: String, content: String, media: Vec<MediaContent> }
pub struct PermissionCard { permission_id: String, description: String, options: Vec<String> }
pub enum PermissionDecision { Allow, Deny, AllowAll }
```

**验收**: trait 定义完整，编译通过

---

## Phase 1: ACP Client 核心（Days 2-4）

### P1-1: AgentConnection（acp-client/src/connection.rs）

**目标**: 实现 Agent 进程启动和 stdio 连接

**输入**: agent-client-protocol SDK 文档

**输出**:
```rust
pub struct AgentConnection {
    child: tokio::process::Child,
    connection: Arc<acp::ClientSideConnection>,
    sessions: HashMap<acp::SessionId, AcpSession>,
    agent_capabilities: acp::AgentCapabilities,
    state: AgentConnectionState,
}

impl AgentConnection {
    pub async fn launch(command: Vec<String>) -> Result<Self>;
    pub async fn initialize(&mut self) -> Result<acp::AgentCapabilities>;
    pub async fn new_session(&mut self) -> Result<acp::SessionId>;
    pub async fn prompt(&self, session_id: &acp::SessionId, content: String) -> Result<()>;
}
```

**验收**:
- 可以启动子进程
- stdio 连接建立
- `cargo test connection::test_launch` 通过

**关键挑战**:
- stdin/stdout pipe 管理
- 进程生命周期监控
- 错误处理（进程崩溃）

---

### P1-2: AcpClient 实现（acp-client/src/client.rs）

**目标**: 实现 `acp::Client` trait

**输入**: AgentConnection + Channel trait

**输出**:
```rust
pub struct AcpClient<C: Channel> {
    channel: Arc<C>,
    file_boundary: FileBoundary,
    terminal_boundary: TerminalBoundary,
}

#[async_trait::async_trait]
impl<C: Channel + Send + Sync> acp::Client for AcpClient<C> {
    async fn request_permission(&self, req: acp::RequestPermissionRequest) -> acp::Result<...>;
    async fn write_text_file(&self, req: acp::WriteTextFileRequest) -> acp::Result<...>;
    async fn read_text_file(&self, req: acp::ReadTextFileRequest) -> acp::Result<...>;
    async fn create_terminal(&self, req: acp::CreateTerminalRequest) -> acp::Result<...>;
    async fn terminal_output(&self, req: acp::TerminalOutputRequest) -> acp::Result<...>;
    async fn release_terminal(&self, req: acp::ReleaseTerminalRequest) -> acp::Result<...>;
    async fn session_notification(&self, args: acp::SessionNotification) -> acp::Result<()>;
}
```

**验收**:
- 所有 trait 方法实现
- session_notification 能转发到 Channel
- `cargo test client::test_session_notification` 通过

---

### P1-3: SessionManager（acp-client/src/session.rs）

**目标**: chat_id ↔ session_id 映射管理

**输出**:
```rust
pub struct SessionManager {
    sessions: HashMap<String, AcpSession>,
    agent: AgentConnection,
}

impl SessionManager {
    pub async fn get_or_create(&mut self, chat_id: &str) -> Result<&AcpSession>;
    pub async fn restore(&mut self, chat_id: &str, prev_session_id: Option<&str>) -> Result<&AcpSession>;
}

pub struct AcpSession {
    session_id: acp::SessionId,
    chat_id: String,
    created_at: Instant,
    last_activity: Instant,
}
```

**验收**: 会话映射逻辑正确，测试通过

---

### P1-4: 边界处理（acp-client/src/boundary/）

**目标**: 文件/终端操作边界检查

**输出**:
```rust
// boundary/file.rs
pub struct FileBoundary {
    blocked_paths: Vec<PathBuf>,
}

impl FileBoundary {
    pub fn validate_path(&self, path: &Path, op: FileOp) -> Result<()>;
}

// boundary/terminal.rs
pub struct TerminalBoundary {
    blocked_patterns: Vec<Regex>,
    max_execution_time: Duration,
}

impl TerminalBoundary {
    pub fn validate_command(&self, cmd: &str) -> Result<()>;
}
```

**验收**:
- 系统路径被正确阻止
- 危险命令被正确阻止
- `cargo test boundary::` 全部通过

---

### P1-5: Claude Code 启动器（acp-client/src/agent/claude.rs）

**目标**: Claude Code Agent 启动配置

**输出**:
```rust
pub struct ClaudeCodeLauncher {
    command: String,  // "claude"
}

impl ClaudeCodeLauncher {
    pub fn command_args() -> Vec<String>;
}
```

**验收**: 启动参数正确，能与真实 Claude Code 通信

---

### P1-6: Mock Channel（acp-client/src/mock_channel.rs）

**目标**: 用于测试的 Channel 实现

**输出**:
```rust
pub struct MockChannel {
    messages_tx: Sender<ChannelMessage>,
    messages_rx: Receiver<ChannelMessage>,
    permissions: HashMap<String, PermissionDecision>,
}

impl Channel for MockChannel { ... }
```

**验收**: 能模拟消息发送和权限审批

---

### P1-7: CLI 入口（app/src/main.rs + config.rs）

**目标**: 基本命令行接口

**输出**:
```rust
// main.rs
#[tokio::main]
async fn main() -> Result<()> {
    let config = Config::load("config/default.toml")?;
    // 启动 AgentConnection
    // 使用 MockChannel 测试
    // CLI 交互循环
}

// Commands: cargo run -- chat "hello"
```

**验收**:
- CLI 能启动
- 能发送消息给 Agent
- 能收到响应

---

### P1-8: 真实 Agent 集成测试

**目标**: 与 Claude Code 真实通信

**验收**:
- `ANTHROPIC_API_KEY` 设置正确
- `cargo run -- chat "hello"` 能得到真实响应
- 流式消息正确显示
- 权限审批流程工作

**里程碑**: **Phase 1 完成 - CLI 可用**

---

## Phase 2: 飞书 Channel（Days 5-7）

### P2-1: 飞书 WebSocket 连接（feishu-channel/src/websocket.rs）

**目标**: 长连接建立和心跳

**输出**:
```rust
pub struct FeishuWebSocket {
    url: String,
    ws: Option<WebSocketStream>,
    state: FeishuChannelState,
    reconnect_attempts: usize,
}

impl FeishuWebSocket {
    pub async fn connect(&mut self, config: &FeishuConfig) -> Result<()>;
    pub async fn reconnect(&mut self) -> Result<()>;  // exponential backoff
    pub async fn heartbeat(&self) -> Result<()>;
}
```

**验收**: WebSocket 连接稳定，断线自动重连

**依赖**: 飞书开放平台文档

---

### P2-2: 飞书消息收发（feishu-channel/src/message.rs + api.rs）

**目标**: 消息解析和发送

**输出**:
```rust
pub struct FeishuApi {
    app_id: String,
    app_secret: String,
    access_token: Option<String>,
}

impl FeishuApi {
    pub async fn get_access_token(&mut self) -> Result<String>;
    pub async fn send_message(&self, chat_id: &str, content: &str) -> Result<()>;
}

pub struct FeishuMessageParser;

impl FeishuMessageParser {
    pub fn parse_event(event: Value) -> Result<ChannelMessage>;
}
```

**验收**: 能解析飞书消息事件，能发送文本消息

---

### P2-3: 交互卡片（feishu-channel/src/card.rs）

**目标**: 权限审批卡片和回调处理

**输出**:
```rust
pub struct FeishuCardBuilder;

impl FeishuCardBuilder {
    pub fn permission_card(permission: &PermissionCard) -> CardJson;
    pub fn terminal_card(terminal_id: &str, output: &str) -> CardJson;
}

pub async fn handle_card_callback(event: CardEvent) -> Result<PermissionDecision>;
```

**验收**: 卡片 JSON 正确，回调解析正确

---

### P2-4: 流式响应（feishu-channel/src/stream.rs）

**目标**: 实时更新消息内容

**输出**:
```rust
pub struct FeishuStreamManager {
    streams: HashMap<String, String>,  // chat_id -> accumulated content
}

impl FeishuStreamManager {
    pub async fn update(&mut self, chat_id: &str, content: &str) -> Result<()>;
    pub async fn finalize(&mut self, chat_id: &str) -> Result<()>;
}
```

**验收**: 流式消息实时更新，最终消息完整

---

### P2-5: FeishuChannel 实现（feishu-channel/src/channel.rs）

**目标**: Channel trait 完整实现

**输出**:
```rust
pub struct FeishuChannel {
    config: FeishuConfig,
    websocket: FeishuWebSocket,
    api: FeishuApi,
    stream_manager: FeishuStreamManager,
    message_tx: Sender<ChannelMessage>,
    pending_permissions: HashMap<String, PendingPermission>,
}

impl Channel for FeishuChannel { ... }
```

**验收**: 所有 trait 方法实现，含超时和 TTL 清理

---

### P2-6: Router 集成（app/src/router.rs）

**目标**: 飞书消息路由到 Agent

**输出**:
```rust
pub struct Router {
    channel: Arc<FeishuChannel>,
    session_manager: SessionManager,
}

impl Router {
    pub async fn run(&mut self) -> Result<()>;
    async fn handle_message(&mut self, msg: ChannelMessage) -> Result<()>;
}
```

**验收**: 飞书消息 → Agent → 飞书响应完整流程

---

### P2-7: 飞书集成测试

**验收**:
- 飞书 Bot 配置正确
- WebSocket 连接稳定
- 群聊 @机器人 能触发
- 权限审批卡片正常工作

**里程碑**: **Phase 2 完成 - 飞书可用**

---

## Phase 3: Codex Agent（Days 8-9）

### P3-1: Codex 启动器（acp-client/src/agent/codex.rs）

**目标**: codex-acp 启动配置

**输出**:
```rust
pub struct CodexLauncher {
    command: String,  // "npx @zed-industries/codex-acp"
}

impl CodexLauncher {
    pub fn command_args() -> Vec<String>;
}
```

**验收**: 能启动 codex-acp，能通信

---

### P3-2: Agent 切换配置（app/src/config.rs）

**目标**: 配置支持多 Agent

**输出**:
```rust
pub enum AgentType { ClaudeCode, Codex }

impl Config {
    pub fn agent_launcher(&self) -> Box<dyn AgentLauncher>;
}
```

**验收**: 配置切换 Agent，都能正常工作

---

### P3-3: Codex 集成测试

**验收**:
- `OPENAI_API_KEY` 设置正确
- Codex 能正常响应
- 飞书能使用 Codex

**里程碑**: **Phase 3 完成 - 多 Agent 支持**

---

## Phase 4: 完善（Day 10）

### P4-1: 错误处理完善

**目标**: 全链路错误处理

**内容**:
- Agent 崩溃重启
- Channel 断线重连
- 超时处理统一
- 用户通知优化

---

### P4-2: 日志和监控

**目标**: tracing 日志完善

**内容**:
- 关键节点日志
- JSON 格式输出
- 环境变量控制级别

---

### P4-3: 文档和部署

**目标**: 使用文档和部署说明

**内容**:
- README 使用说明
- 飞书 Bot 配置教程
- Docker 部署脚本

---

## 验收标准总览

| Phase | 验收命令 | 期望结果 |
|-------|----------|----------|
| P0 | `cargo build --workspace` | 编译通过 |
| P1 | `cargo run -- chat "hello"` | Agent 响应（CLI） |
| P2 | 飞书 @机器人 发消息 | Agent 响应（飞书） |
| P3 | 切换 Codex 配置 | Codex 响应 |
| P4 | Docker 部署 | 服务稳定运行 |

---

## 依赖和风险

**外部依赖**:
- agent-client-protocol crate 稳定性
- Claude Code / Codex API 可用性
- 飞书 WebSocket 稳定性

**风险缓解**:
- P1-6 Mock Channel 降低 Agent 依赖
- 分阶段验收，早发现问题
- 配置文件模板降低部署难度

---

## 开始条件

1. `claude` 命令可用（Claude Code 已安装）
2. `ANTHROPIC_API_KEY` 已设置
3. Rust 工具链已安装（cargo, rustc）

**下一步**: 确认开始条件后，执行 P0-1（Cargo Workspace 初始化）