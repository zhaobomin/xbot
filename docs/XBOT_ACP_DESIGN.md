# xbot-acp 项目设计文档

> **项目类型**: 新项目（独立于 xbot）
> **设计日期**: 2026-04-06
> **目标**: 基于 ACP 协议实现多平台消息机器人，连接多种 AI Agent

---

## 一、项目概述

### 1.1 背景

ACP (Agent Client Protocol) 是一个用于标准化代码编辑器和 AI 编码代理之间通信的协议。本项目旨在：

1. 实现一个 ACP Client，作为飞书等平台的机器人
2. 通过 ACP 协议连接 Claude Code、Codex CLI 等 Agent
3. 支持权限审批、文件操作、终端执行等功能
4. 保留扩展其他 Channel 和 Agent 的能力

### 1.2 核心价值

- **标准化**: 通过 ACP 协议统一 Agent 接口
- **可扩展**: 模块化设计，易于添加新 Channel 和 Agent
- **用户友好**: 飞书交互卡片审批，流式响应展示
- **可靠性**: 完善的状态机、异常处理、边界保护

### 1.3 已确认需求

| 需求项 | 确认值 |
|--------|--------|
| 项目类型 | 新项目，不修改 xbot |
| 架构 | 纯 ACP 协议 |
| 飞书角色 | Channel + 本地 ACP Client |
| Agent 连接 | stdio 子进程 |
| 技术栈 | Rust |
| 飞书特性 | 交互卡片审批、终端展示、流式响应 |
| 范围 | 全功能版本，分阶段实现 |

---

## 二、架构设计

### 2.1 整体架构

```
xbot-acp/
├── Cargo.toml                     # workspace 定义
│
├── crates/
│   ├── acp-client/                # ACP Client 实现
│   │   ├── src/
│   │   │   ├── lib.rs
│   │   │   ├── client.rs          # Client trait 实现
│   │   │   ├── connection.rs      # Agent 连接管理（stdio）
│   │   │   ├── session.rs         # 会话状态管理
│   │   │   ├── permission.rs      # 权限审批处理
│   │   │   ├── state.rs           # 状态机定义
│   │   │   ├── agent/             # Agent 启动器
│   │   │   │   ├── claude.rs      # Claude Code
│   │   │   │   ├── codex.rs       # Codex CLI (codex-acp)
│   │   │   │   └── custom.rs      # 自定义 Agent
│   │   │   ├── boundary/          # 边界处理
│   │   │   │   ├── file.rs
│   │   │   │   └── terminal.rs
│   │   │   └── types.rs           # 类型定义
│   │
│   ├── feishu-channel/            # 飞书 Channel
│   │   ├── src/
│   │   │   ├── lib.rs
│   │   │   ├── channel.rs         # Channel trait 实现
│   │   │   ├── websocket.rs       # WebSocket 连接
│   │   │   ├── message.rs         # 消息收发
│   │   │   ├── card.rs            # 交互卡片
│   │   │   ├── stream.rs          # 流式响应
│   │   │   ├── terminal.rs        # 终端展示
│   │   │   ├── api.rs             # 飞书 API
│   │   │   └── state.rs           # 飞书连接状态机
│   │
│   └── app/                       # 应用入口
│       ├── src/
│       │   ├── main.rs            # CLI 入口
│       │   ├── config.rs          # 配置加载
│       │   ├── router.rs          # 消息路由
│       │   ├── error_handler.rs   # 异常处理中心
│       │   └── shutdown.rs        # 关闭处理
│
├── config/                        # 配置文件
│   ├── default.toml               # 默认配置
│   └── feishu.toml                # 飞书配置
│
└── docs/                          # 文档
```

### 2.2 依赖关系

```
                    ┌─────────────────────────────────────┐
                    │              app                     │
                    │  (CLI, Router, ErrorHandler)        │
                    └─────────────────┬───────────────────┘
                                      │
                    ┌─────────────────┴─────────────────┐
                    │                                   │
                    ▼                                   ▼
          ┌───────────────────┐           ┌───────────────────┐
          │   feishu-channel  │           │    acp-client     │
          │ (Channel trait)   │           │ (Client impl)     │
          └───────────────────┘           └───────────────────┘
                                                  │
                                                  ▼
                                  ┌───────────────────────────┐
                                  │ agent-client-protocol     │
                                  │ (外部 crate, v0.10.2)     │
                                  └───────────────────────────┘
```

### 2.3 设计理念

参考 zed-editor 的实现，采用简化架构：

- **acp-client**: 合并原 `acp-core` + `agent-connector` + `session-manager`，统一管理 ACP 协议、Agent 连接和会话
- **feishu-channel**: 合并原 `channel-core` + `channels/feishu`，飞书专用实现，Channel trait 内置
- **app**: 轻量入口，只做配置加载和消息路由

---

## 三、核心组件设计

### 3.1 acp-client - ACP Client 核心

**职责**: 实现 ACP Client trait，管理 Agent 连接和会话状态。

#### 关键模块

| 模块 | 功能 |
|------|------|
| `client.rs` | 实现 `acp::Client` trait，响应 Agent 的请求 |
| `connection.rs` | Agent 进程启动和 stdio 连接管理 |
| `session.rs` | 会话状态管理（session_id 映射） |
| `permission.rs` | 权限审批请求处理 |
| `state.rs` | 状态机定义 |
| `agent/claude.rs` | Claude Code 启动器 |
| `agent/codex.rs` | Codex CLI (codex-acp) 启动器 |
| `agent/custom.rs` | 自定义 Agent 启动器 |
| `boundary/file.rs` | 文件操作边界检查 |
| `boundary/terminal.rs` | 终端执行边界检查 |
| `channel.rs` | Channel trait 定义（供多平台复用） |

#### Client 实现

```rust
use agent_client_protocol as acp;
use std::sync::Arc;

pub struct AcpClient<C: Channel> {
    channel: Arc<C>,                // Channel 实现（Arc 包装以支持多线程）
    file_boundary: FileBoundary,
    terminal_boundary: TerminalBoundary,
}

// 注意：移除 ?Send 以支持 tokio 多线程运行时
// Channel trait 需要 Send + Sync
#[async_trait::async_trait]
impl<C: Channel + Send + Sync> acp::Client for AcpClient<C> {
    async fn request_permission(&self, req: acp::RequestPermissionRequest) 
        -> acp::Result<acp::RequestPermissionResponse>;
    
    async fn write_text_file(&self, req: acp::WriteTextFileRequest) 
        -> acp::Result<acp::WriteTextFileResponse>;
    
    async fn read_text_file(&self, req: acp::ReadTextFileRequest) 
        -> acp::Result<acp::ReadTextFileResponse>;
    
    async fn create_terminal(&self, req: acp::CreateTerminalRequest) 
        -> acp::Result<acp::CreateTerminalResponse>;
    
    async fn terminal_output(&self, req: acp::TerminalOutputRequest) 
        -> acp::Result<acp::TerminalOutputResponse>;
    
    async fn release_terminal(&self, req: acp::ReleaseTerminalRequest) 
        -> acp::Result<acp::ReleaseTerminalResponse>;
    
    async fn session_notification(&self, args: acp::SessionNotification) 
        -> acp::Result<()> {
        // 处理 Agent 的流式消息，转发到 Channel
        self.channel.handle_session_update(args.update);
    }
}
```

#### Connection 管理

```rust
pub struct AgentConnection {
    child: tokio::process::Child,           // Agent 子进程
    connection: Arc<acp::ClientSideConnection>, // Arc 替代 Rc，支持多线程
    sessions: HashMap<acp::SessionId, AcpSession>,
    agent_capabilities: acp::AgentCapabilities,
}

impl AgentConnection {
    pub async fn launch(command: Vec<String>) -> Result<Self>;
    pub async fn initialize(&self) -> Result<acp::AgentCapabilities>;
    pub async fn new_session(&self) -> Result<acp::SessionId>;
    pub async fn load_session(&self, session_id: &acp::SessionId) -> Result<bool>;
    pub async fn prompt(&self, session_id: &acp::SessionId, content: String) 
        -> Result<acp::PromptResponse>;
}
```

#### Session 状态

```rust
pub struct AcpSession {
    session_id: acp::SessionId,
    chat_id: String,               // 飞书 chat_id
    created_at: Instant,
    last_activity: Instant,
}

pub struct SessionManager {
    // chat_id -> session 映射
    sessions: HashMap<String, AcpSession>,
    // Agent 连接
    agent: AgentConnection,
}

impl SessionManager {
    pub async fn get_or_create(&mut self, chat_id: &str) -> Result<&AcpSession>;
    
    // 重启恢复：尝试 load_session，不支持则新建
    pub async fn restore(&mut self, chat_id: &str, prev_session_id: Option<&str>) 
        -> Result<&AcpSession>;
}
```

---

### 3.2 Channel Trait

**职责**: 定义消息平台的统一接口，支持多平台扩展。

```rust
pub trait Channel: Send + Sync {
    fn name(&self) -> &str;
    
    // 启动/停止
    async fn start(&self) -> Result<()>;
    async fn stop(&self) -> Result<()>;
    
    // 消息发送
    async fn send_message(&self, chat_id: &str, content: &str) -> Result<()>;
    async fn send_stream(&self, chat_id: &str, content: &str) -> Result<()>;
    async fn finalize_stream(&self, chat_id: &str) -> Result<()>;
    
    // 权限审批
    async fn send_permission_card(&self, chat_id: &str, card: PermissionCard) 
        -> Result<PermissionDecision>;
    
    // 终端输出
    async fn send_terminal_output(&self, chat_id: &str, terminal_id: &str, output: &str) 
        -> Result<()>;
    
    // 消息接收流
    fn message_stream(&self) -> Receiver<ChannelMessage>;
}

pub struct ChannelMessage {
    chat_id: String,
    content: String,
    media: Vec<MediaContent>,
}

pub struct PermissionCard {
    permission_id: String,
    description: String,
    options: Vec<String>,  // ["Allow", "Deny", "AllowAll"]
}

pub enum PermissionDecision {
    Allow,
    Deny,
    AllowAll,
}
```

---

### 3.3 feishu-channel - 飞书实现

**职责**: 实现飞书 WebSocket 连接和消息处理。

#### 关键模块

| 模块 | 功能 |
|------|------|
| `channel.rs` | 实现 Channel trait |
| `websocket.rs` | 飞书 WebSocket 长连接 |
| `message.rs` | 消息收发 |
| `card.rs` | 交互卡片（审批） |
| `stream.rs` | 流式消息展示 |
| `terminal.rs` | 终端输出展示 |
| `api.rs` | 飞书 REST API |

#### FeishuChannel 实现

```rust
pub struct FeishuChannel {
    config: FeishuConfig,
    websocket: FeishuWebSocket,
    api: FeishuApi,
    stream_manager: FeishuStreamManager,
    message_tx: Sender<ChannelMessage>,
    pending_permissions: HashMap<String, oneshot::Sender<PermissionDecision>>,
}

impl Channel for FeishuChannel {
    fn name(&self) -> &str { "feishu" }
    
    async fn start(&self) -> Result<()> {
        self.websocket.connect(&self.config).await?;
    }
    
    async fn send_stream(&self, chat_id: &str, content: &str) -> Result<()> {
        self.stream_manager.update(chat_id, content).await?;
    }
    
    async fn send_permission_card(&self, chat_id: &str, card: PermissionCard) 
        -> Result<PermissionDecision> {
        let (tx, rx) = oneshot::channel();
        self.pending_permissions.insert(card.permission_id.clone(), tx);
        self.api.send_card(chat_id, card).await?;
        rx.await?
    }
}
```

#### 飞书 Card 实现

```rust
pub struct FeishuCardBuilder;

impl FeishuCardBuilder {
    pub fn permission_card(permission: &PermissionCard) -> CardJson {
        // 飞书交互卡片 JSON
        // 按钮 callback_id: "perm:{id}:allow" / "perm:{id}:deny"
    }
    
    pub fn terminal_card(terminal_id: &str, output: &str) -> CardJson;
}

pub async fn handle_card_callback(event: CardEvent) -> Result<PermissionDecision> {
    // 解析 callback_id -> permission_id + decision
}
```

---

## 四、数据流设计

### 4.1 用户消息处理流程

```
用户发送消息
    │
    ▼
feishu-channel WebSocket 接收事件
    │
    ▼
FeishuChannel → ChannelMessage { chat_id, content }
    │
    ▼
app/router.rs
    ├── 查找或创建 Session
    │   ├── SessionManager::get_or_create(chat_id)
    │   ├── 无会话 → AgentConnection::launch()
    │   │       → initialize() → new_session()
    │   └── 有会话 → 使用现有 session_id
    │
    ▼
AgentConnection::prompt(session_id, content)
    │
    ▼
Agent 进程处理（通过 acp::ClientSideConnection）
    │
    ▼  （流式返回 SessionNotification）
AcpClient::session_notification()
    │   ├── AgentMessageChunk → FeishuChannel::send_stream()
    │   ├── RequestPermission → FeishuChannel::send_permission_card()
    │   │       │
    │   │       ▼
    │   │   用户点击 → oneshot::Sender 发送决策
    │   │       │
    │   │       ▼
    │   │   返回 RequestPermissionResponse 给 Agent
    │   │
    │   ├── ToolCall → 记录日志
    │   ├── ToolCallUpdate → 更新状态
    │   │
    ▼
PromptResponse → FeishuChannel::finalize_stream()
```

### 4.2 权限审批流程

```
Agent → RequestPermissionRequest
    │
    ▼
AcpClient::request_permission()
    ├── 构建 PermissionCard
    │   permission_id: UUID
    │   description: 操作描述
    │
    ▼
FeishuChannel::send_permission_card(chat_id, card)
    ├── oneshot::channel 创建
    ├── pending_permissions.insert(permission_id, tx)
    ├── 飞书 API 发送交互卡片
    │
    ▼
等待用户响应（30s timeout）
    │
    ├─→ 用户点击按钮 → 飞书卡片回调
    │       │
    │       ▼
    │   FeishuChannel::handle_card_callback()
    │       ├── 解析 callback_id → permission_id + decision
    │       ├── pending_permissions.get(permission_id) → tx.send(decision)
    │       │
    │       ▼
    │   AcpClient::request_permission()
    │       ├── rx.await? 收到决策
    │       ├── 返回 RequestPermissionResponse { decision }
    │
    └─→ 30s timeout → 默认 Deny
            │
            ▼
        AcpClient::request_permission()
            ├── cleanup_pending_permission(permission_id)
            ├── 返回 RequestPermissionResponse { decision: Deny }
            ├── 飞书卡片更新为 "已超时，默认拒绝"
```

**超时实现**:
```rust
// 使用 tokio::time::timeout 包装 oneshot receive
let decision = tokio::time::timeout(
    Duration::from_secs(30),
    rx.await
).await.unwrap_or(Ok(PermissionDecision::Deny)).unwrap_or(PermissionDecision::Deny);
```

---

## 五、状态机设计

### 5.1 AgentConnection 状态机

```
[Disconnected] → launch() → [Connecting]
[Connecting] → initialize() success → [Ready]
[Connecting] → initialize() failed → [Failed]
[Ready] → new_session() → [Active]
[Active] → prompt() → [Processing]
[Processing] → SessionUpdate → [Processing] (loop)
[Processing] → PromptResponse → [Active]
[Active] → child.exit → [Disconnected]
[Failed] → retry → [Connecting]
```

```rust
pub enum AgentConnectionState {
    Disconnected,
    Connecting,
    Ready,
    Active,
    Processing,
    Failed,
}
```

### 5.2 FeishuChannel 状态机

```
[Disconnected] → start() → [Connecting]
[Connecting] → WebSocket connected → [Connected]
[Connecting] → timeout/error → [Reconnecting]
[Reconnecting] → exponential backoff → [Connecting]
[Connected] → WebSocket close → [Reconnecting]
[Connected] → stop() → [Stopped]
```

```rust
pub enum FeishuChannelState {
    Disconnected,
    Connecting,
    Connected,
    Reconnecting,
    Stopped,
}

pub struct FeishuChannelStateMachine {
    state: FeishuChannelState,
    reconnect_attempts: usize,
}
```

### 5.3 权限审批（带超时和 TTL 清理）

权限审批是同步阻塞操作，通过 oneshot channel 实现，需要超时和清理机制：

```rust
// 在 FeishuChannel 中
pending_permissions: HashMap<String, PendingPermission>,
pending_lock: Mutex<()>,

pub struct PendingPermission {
    sender: oneshot::Sender<PermissionDecision>,
    created_at: Instant,
}

// 超时处理（在 request_permission 中）
const PERMISSION_TIMEOUT: Duration = Duration::from_secs(30);

// TTL 清理（后台任务，每 60s 检查）
async fn cleanup_stale_permissions(&mut self) {
    let now = Instant::now();
    self.pending_permissions.retain(|id, pending| {
        now.duration_since(pending.created_at) < PERMISSION_TIMEOUT * 2
    });
}

// 流程
1. Agent 发起 RequestPermissionRequest
2. AcpClient 创建 oneshot channel (tx, rx)
3. tx 存入 pending_permissions（带时间戳），发送飞书卡片
4. tokio::time::timeout(30s, rx.await)
   - 成功：用户决策
   - 超时：默认 Deny，更新飞书卡片状态
5. cleanup_stale_permissions 定期清理过期条目（防止泄漏）
```

---

## 六、异常处理设计

### 6.1 异常分类与处理

| 异常类型 | 处理策略 | 用户通知 |
|----------|----------|----------|
| Agent 进程崩溃 | 重启 Agent，新建 session | 飞书消息通知 |
| Agent 响应超时 | 取消当前操作 | 飞书消息通知 |
| Agent 协议错误 | 记录日志，返回错误给 Agent | 无 |
| 飞书 WebSocket 断开 | 自动重连（指数退避） | 无 |
| 飞书认证失败 | 停止服务，记录日志 | 飞书消息通知 |
| 权限审批超时 | 默认 Deny | 无 |
| 文件/终端操作失败 | 返回错误给 Agent | 无 |

### 6.2 简化的错误处理

```rust
pub enum AppError {
    AgentCrashed { reason: String },
    AgentTimeout,
    ChannelDisconnected,
    PermissionTimeout,
    IoError { source: std::io::Error },
}

impl AppError {
    pub fn should_notify_user(&self) -> bool {
        matches!(self, 
            AppError::AgentCrashed { .. } | 
            AppError::AgentTimeout |
            AppError::ChannelDisconnected
        )
    }
}
```

**处理原则**：
- Agent 层错误：重启 Agent，通知用户
- Channel 层错误：自动重连，失败后通知用户
- 操作层错误：返回给 Agent，让 Agent 决定下一步

---

## 七、边界处理设计

**原则**: 最小化边界限制，只做安全兜底，让 Agent 能最大化发挥能力。

### 7.1 文件操作边界

```rust
pub struct FileBoundary {
    // 仅阻止系统关键路径（注意：不能用 "/"，否则阻止所有绝对路径）
    blocked_paths: Vec<PathBuf>,  // ["/etc", "/System", "/usr", "/bin", "/sbin"]
    // 工作目录（可选，用于日志记录）
    working_dir: Option<PathBuf>,
}

impl FileBoundary {
    pub fn validate_path(&self, path: &Path, op: FileOp) -> Result<()> {
        // canonicalize 获取真实路径（处理 symlinks、..）
        let canonical = path.canonicalize()
            .or_else(|_| Ok(path.to_path_buf()))?;  // 路径不存在时使用原路径
        
        // 检查是否为系统关键路径（精确匹配或前缀匹配）
        for blocked in &self.blocked_paths {
            if canonical == *blocked || canonical.starts_with(blocked) {
                return Err(Error::path_blocked(path));
            }
        }
        Ok(())
    }
}
```

**策略**:
- 不限制 `allowed_paths`，Agent 可访问用户任意目录
- 只阻止系统关键路径（/etc, /System, /usr, /bin, /sbin 等）
- **注意**: 不能用 "/" 作为 blocked_path，否则会阻止所有绝对路径
- 使用 canonicalize 处理 symlinks 和路径逃逸（如 `/home/user/../etc`）
- 不限制文件大小和扩展名
- 校验失败返回错误给 Agent，让 Agent 知道原因并调整策略

### 7.2 终端执行边界

```rust
pub struct TerminalBoundary {
    // 仅阻止极端危险命令
    blocked_patterns: Vec<Regex>,  // ["rm -rf /", "dd if=/dev/zero", ":(){ :|:& };:"]
    max_execution_time: Duration,  // 30min（允许长时间任务）
}

impl TerminalBoundary {
    pub fn validate_command(&self, cmd: &str) -> Result<()> {
        // 只检查极端危险命令
        if self.blocked_patterns.iter().any(|p| p.is_match(cmd)) {
            return Err(Error::command_blocked(cmd));
        }
        Ok(())
    }
}
```

**策略**:
- 不用 `allowed_commands` 白名单，Agent 可执行任意命令
- 只阻止极端危险命令（rm -rf /、fork bomb、dd 破坏磁盘）
- 不阻止 sudo，Agent 可能需要安装依赖
- 不做 sandbox，信任 Agent 操作
- 校验失败返回错误给 Agent

### 7.3 无边界项（信任 Agent）

| 项目 | 策略 | 原因 |
|------|------|------|
| 文件大小 | 不限制 | Agent 需处理大型文件（如日志、数据文件） |
| 文件扩展名 | 不限制 | Agent 需写 .sh、.py 等脚本执行任务 |
| 命令白名单 | 不使用 | Agent 需执行各种命令完成任务 |
| sudo | 允许 | Agent 可能需要安装系统依赖 |
| 网络访问 | 不限制 | Agent 需访问外部 API、下载资源 |

---

## 八、日志跟踪设计

**方案**: 使用 `tracing` 库，无需自定义 LogEvent。

### 8.1 日志配置

```rust
// app/src/main.rs
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

fn init_logging() {
    tracing_subscriber::registry()
        .with(tracing_subscriber::fmt::layer().json())
        .with(tracing_subscriber::EnvFilter::from_default_env())
        .init();
}
```

### 8.2 关键日志点

| 位置 | 日志内容 | 级别 |
|------|----------|------|
| Agent 启动 | `agent_launched` + command | INFO |
| Agent 初始化 | `agent_initialized` + capabilities | INFO |
| Session 创建 | `session_created` + session_id | INFO |
| Prompt 开始 | `prompt_started` + chat_id | DEBUG |
| Prompt 结束 | `prompt_completed` + stop_reason | DEBUG |
| 权限审批 | `permission_{requested,approved,denied}` | INFO |
| 错误 | `error` + reason | ERROR |

### 8.3 日志示例

```rust
// Agent 启动
tracing::info!(
    agent = "claude-code",
    command = ?command,
    "agent_launched"
);

// Session 创建
tracing::info!(
    session_id = %session_id,
    chat_id = %chat_id,
    "session_created"
);

// 权限审批
tracing::info!(
    permission_id = %permission_id,
    description = %description,
    "permission_requested"
);
```

### 8.4 日志输出格式

```json
{"timestamp":"2026-04-06T10:30:00Z","level":"INFO","target":"xbot_acp","fields":{"agent":"claude-code","message":"agent_launched"}}
```

---

## 九、配置设计

### 9.1 配置文件结构

```toml
# config/default.toml
[app]
name = "xbot-acp"
log_level = "info"
default_agent = "claude-code"  # 固定 Agent，可选: "claude-code" 或 "codex"

[agent]
# Agent 启动配置（根据 default_agent 选择）
command = "claude"              # Claude Code: "claude", Codex: "npx @zed-industries/codex-acp"

# 边界配置（与 Section 7 对齐）
[file]
# 只阻止系统关键路径，不限制 allowed_paths
blocked_paths = ["${HOME}/../etc", "/etc", "/System", "/usr", "/bin", "/sbin"]
# 注意：不能用 "/" 否则阻止所有绝对路径

[terminal]
# 只阻止极端危险命令，不用白名单
blocked_patterns = ["rm -rf /", "dd if=/dev/zero", ":\\(\\)\\{ :\\|:& \\};:"]
max_execution_time = 1800      # 30min（允许长时间任务）
# 注意：不阻止 sudo，Agent 可能需要安装依赖

[permission]
timeout = 30                    # 权限审批超时（秒）
cleanup_interval = 60           # TTL 清理间隔（秒）

# config/feishu.toml
[feishu]
enabled = true
app_id = "${FEISHU_APP_ID}"
app_secret = "${FEISHU_APP_SECRET}"
encrypt_key = "${FEISHU_ENCRYPT_KEY}"
verification_token = "${FEISHU_VERIFICATION_TOKEN}"
bot_open_id = "${FEISHU_BOT_OPEN_ID}"
group_policy = "mention"        # 群聊触发策略: "mention" | "all" | "none"
```

**配置说明**:
- 文件边界：只配置 `blocked_paths`，不限制用户目录访问
- 终端边界：只配置 `blocked_patterns`（极端危险命令），不阻止 sudo
- 权限边界：配置超时和清理参数
- 与 Section 7 边界设计原则一致：最小化限制，信任 Agent

### 9.2 环境变量

| 变量 | 说明 |
|------|------|
| `ANTHROPIC_API_KEY` | Claude Code API Key（使用 claude-code 时） |
| `OPENAI_API_KEY` | Codex API Key（使用 codex 时） |
| `FEISHU_APP_ID` | 飞书应用 ID |
| `FEISHU_APP_SECRET` | 飞书应用 Secret |

---

## 十、分阶段实现计划

### Phase 1: ACP Client 核心（约 1 周）

| 任务 | Crate | 内容 |
|------|-------|------|
| P1-1 | acp-client | 基础类型定义、Channel trait |
| P1-2 | acp-client | AgentConnection（启动进程、stdio 连接） |
| P1-3 | acp-client | AcpClient 实现（acp::Client trait） |
| P1-4 | acp-client | SessionManager（会话映射） |
| P1-5 | acp-client | Claude Code 启动器 |
| P1-6 | acp-client | 边界处理（文件、终端） |
| P1-7 | app | CLI 入口、配置加载 |
| P1-8 | 测试 | 单元测试 + 与 Claude Code 集成测试 |

**交付物**: 可以启动 Claude Code Agent，通过 CLI 发送消息并接收响应。

### Phase 2: 飞书 Channel（约 1 周）

| 任务 | Crate | 内容 |
|------|-------|------|
| P2-1 | feishu-channel | WebSocket 连接 |
| P2-2 | feishu-channel | 消息收发 |
| P2-3 | feishu-channel | 交互卡片（权限审批） |
| P2-4 | feishu-channel | 流式响应 |
| P2-5 | feishu-channel | 终端输出展示 |
| P2-6 | app | Router 集成飞书 Channel |
| P2-7 | 测试 | 飞书集成测试 |

**交付物**: 完整的飞书 Channel，可以在飞书中使用 Claude Code Agent。

### Phase 3: Codex Agent（约 1 周）

| 任务 | Crate | 内容 |
|------|-------|------|
| P3-1 | acp-client | Codex 启动器（codex-acp） |
| P3-2 | app | 配置支持切换 Agent |
| P3-3 | 测试 | Codex 集成测试 |

**交付物**: 支持 Claude Code 和 Codex CLI 两种 Agent，通过配置切换。

---

## 十一、测试策略

### 11.1 单元测试

```rust
// acp-client 测试
#[tokio::test]
async fn test_agent_connection_launch() {
    let conn = AgentConnection::launch(vec!["echo".into()]).await.unwrap();
    assert!(conn.initialize().await.is_ok());
}

// 边界测试
#[test]
fn test_file_boundary_blocks_system_path() {
    let boundary = FileBoundary::default();
    assert!(boundary.validate_path(&Path::new("/etc/passwd"), FileOp::Read).is_err());
    assert!(boundary.validate_path(&Path::new("/home/user/project"), FileOp::Read).is_ok());
}
```

### 11.2 集成测试

```bash
# Phase 1: CLI 测试
RUST_LOG=debug cargo run -- chat "hello"

# Phase 2: 飞书集成测试
# 1. 启动服务
cargo run
# 2. 在飞书中发送消息测试

# Phase 3: 切换 Agent 测试
# 修改 config/default.toml 中 default_agent = "codex"
cargo run
```

### 11.3 Mock Agent（开发调试）

```rust
// 使用官方 agent-client-protocol 示例
// cargo run --example agent

// 或创建简单 mock
pub struct MockAgent;

#[async_trait::async_trait(?Send)]
impl acp::Agent for MockAgent {
    async fn prompt(&self, args: acp::PromptRequest) -> acp::Result<acp::PromptResponse> {
        // 固定返回
        Ok(acp::PromptResponse::new(acp::StopReason::EndTurn))
    }
}
```

---

## 十二、关键决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 架构 | 3 crate（acp-client, feishu-channel, app） | 简化架构，个人自用足够 |
| ACP SDK | agent-client-protocol (v0.10.2) | 官方 Rust SDK，类型安全 |
| Agent 连接 | stdio 子进程 | 与 zed 一致，简单可靠 |
| 权限审批 | 飞书交互卡片 + oneshot channel | 用户友好，同步阻塞实现简单 |
| 会话持久化 | Agent 管理，不本地存储 | 个人自用，飞书有历史 |
| Agent 选择 | 配置固定，运行时不切换 | 简化实现，个人自用足够 |
| 边界处理 | 最小化限制 | 让 Agent 最大化发挥能力 |
| 日志 | tracing 库 | Rust 标准方案，无需自定义 |
| 目标用户 | 个人开发者自用 | 简化设计，不追求企业级功能 |

---

## 十三、风险与待定事项

<!-- AUTONOMOUS DECISION LOG -->
## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|----------|----------|
| 1 | CEO | 用户挑战：MCP vs ACP 协议选型 | User Challenge | P6 (用户主权) | 用户已考虑，保持原方向 | MCP、Web Terminal、扩展 xbot |
| 2 | CEO | 用户挑战：用户痛点验证 | User Challenge | P6 (用户主权) | 用户已考虑，保持原方向 | — |
| 3 | CEO | 用户挑战：安全策略矛盾 | User Challenge | P6 (用户主权) | 用户已考虑，保持原方向 | 白名单模式 |

### 13.1 风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Agent API Key 泄露 | 安全问题 | 环境变量注入，不存储在配置文件 |
| 飞书卡片回调延迟 | 权限审批超时 | 设置合理超时（30s），默认 Deny |
| Agent 进程资源泄漏 | 内存/CPU 占用 | 进程监控、定期清理、限制并发数 |
| codex-acp 版本更新 | 兼容性问题 | 锁定版本，关注 upstream 更新 |

### 13.2 待定事项

| 待定 | 说明 |
|------|------|
| 飞书卡片样式设计 | 审批卡片、终端卡片的具体 UI 设计 |
| 多 Agent 会话策略 | 用户如何选择/切换 Agent |
| 会话持久化 | 是否需要持久化会话历史到磁盘 |

### 13.3 已确认事项（技术调研结果）

| 项目 | 状态 | 说明 |
|------|------|------|
| Claude Code ACP | ✅ 原生支持 | `claude` 命令直接支持 ACP 协议 |
| Codex CLI ACP | ✅ 适配器支持 | 使用 `codex-acp` (zed-industries 维护) |
| agent-client-protocol | ✅ 官方 Rust SDK | crates.io 版本 0.10.2，含 client/agent 示例 |