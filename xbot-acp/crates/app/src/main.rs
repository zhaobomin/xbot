//! xbot-acp - 基于 ACP 协议的多平台消息机器人

use std::sync::Arc;

use acp_client::acp::{self, Agent, Client};
use acp_client::{AgentType, AcpClient, Channel, create_launcher};
use feishu_channel::{FeishuChannel, FeishuConfig};
use tokio_util::compat::{TokioAsyncReadCompatExt, TokioAsyncWriteCompatExt};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

mod config;
mod router;

fn main() -> anyhow::Result<()> {
    // 初始化日志
    tracing_subscriber::registry()
        .with(tracing_subscriber::fmt::layer())
        .with(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    tracing::info!("xbot-acp starting...");

    let args: Vec<String> = std::env::args().collect();

    match args.get(1).map(|s| s.as_str()) {
        Some("chat") => {
            let prompt = args.get(2).cloned().unwrap_or_else(|| "Hello".to_string());
            let agent_type = args.get(3)
                .map(|s| s.parse().unwrap_or(AgentType::ClaudeCode))
                .unwrap_or(AgentType::ClaudeCode);
            // 使用 tokio runtime
            let rt = tokio::runtime::Runtime::new()?;
            rt.block_on(run_chat(prompt, agent_type))?;
        }
        Some("test") | Some("agent-test") => {
            let rt = tokio::runtime::Runtime::new()?;
            rt.block_on(test_agent_connection())?;
        }
        Some("feishu") => {
            let rt = tokio::runtime::Runtime::new()?;
            rt.block_on(run_feishu())?;
        }
        _ => {
            println!("xbot-acp - ACP Client for AI Agents");
            println!();
            println!("Usage:");
            println!("  xbot-acp chat <message> [agent]  - Send a message");
            println!("  xbot-acp test                    - Test agent connection");
            println!("  xbot-acp feishu                  - Start Feishu bot");
            println!();
            println!("Agents:");
            println!("  claude-code  - Claude Code (default)");
        }
    }

    Ok(())
}

/// 用于测试的 Client
struct TestClient;

#[async_trait::async_trait(?Send)]
impl Client for TestClient {
    async fn request_permission(
        &self,
        args: acp::RequestPermissionRequest,
    ) -> acp::Result<acp::RequestPermissionResponse> {
        if let Some(option) = args.options.first() {
            Ok(acp::RequestPermissionResponse::new(
                acp::RequestPermissionOutcome::Selected(acp::SelectedPermissionOutcome::new(
                    option.option_id.clone(),
                )),
            ))
        } else {
            Ok(acp::RequestPermissionResponse::new(
                acp::RequestPermissionOutcome::Cancelled,
            ))
        }
    }

    async fn session_notification(&self, args: acp::SessionNotification) -> acp::Result<()> {
        match &args.update {
            acp::SessionUpdate::AgentMessageChunk(chunk) => {
                if let acp::ContentBlock::Text(text) = &chunk.content {
                    print!("{}", text.text);
                }
            }
            acp::SessionUpdate::ToolCall(tool_call) => {
                eprintln!("\n[Tool: {}]", tool_call.title);
            }
            _ => {}
        }
        Ok(())
    }
}

/// 直接测试 Agent 连接
async fn test_agent_connection() -> anyhow::Result<()> {
    tracing::info!("Testing agent connection...");

    let launcher = create_launcher(AgentType::ClaudeCode);

    // 检查环境变量（会尝试从 xbot 配置加载）
    for env_var in launcher.required_env_vars() {
        if std::env::var(env_var).is_err() {
            anyhow::bail!("Environment variable {} is required", env_var);
        }
    }

    let command = launcher.command();

    tracing::info!("Launching: {:?}", command);

    // 使用 tokio process 启动
    let mut child = tokio::process::Command::new(&command[0])
        .args(&command[1..])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()?;

    tracing::info!("Process started, PID: {:?}", child.id());

    let stdin = child.stdin.take().ok_or_else(|| anyhow::anyhow!("No stdin"))?;
    let stdout = child.stdout.take().ok_or_else(|| anyhow::anyhow!("No stdout"))?;

    tracing::info!("Creating ACP connection...");

    // 使用 LocalSet 来运行 !Send 的 future
    let local_set = tokio::task::LocalSet::new();

    local_set.run_until(async move {
        // 使用 tokio_util::compat 转换 I/O
        let (conn, io_task) = acp::ClientSideConnection::new(
            TestClient,
            stdin.compat_write(),
            stdout.compat(),
            |fut| {
                tokio::task::spawn_local(fut);
            },
        );

        // 在后台处理 I/O
        tokio::task::spawn_local(io_task);

        tracing::info!("Initializing...");

        let init_request = acp::InitializeRequest::new(acp::ProtocolVersion::V1)
            .client_info(acp::Implementation::new("xbot-acp", "0.1.0").title("XBot ACP"));

        let response = conn.initialize(init_request).await?;

        tracing::info!("Agent initialized!");
        if let Some(info) = response.agent_info {
            tracing::info!("Agent: {} v{}", info.name, info.version);
        }

        let cwd = std::env::current_dir()?;
        let session = conn.new_session(acp::NewSessionRequest::new(cwd)).await?;

        tracing::info!("Session created: {:?}", session.session_id);

        // 发送一个简单的 prompt
        println!("\nSending prompt: 'Hello'");
        let prompt_request = acp::PromptRequest::new(session.session_id, vec!["Hello".into()]);
        conn.prompt(prompt_request).await?;

        println!("\n");
        tracing::info!("Test PASSED!");

        Ok::<_, anyhow::Error>(())
    }).await
}

/// CLI chat 测试
async fn run_chat(prompt: String, agent_type: AgentType) -> anyhow::Result<()> {
    let launcher = create_launcher(agent_type.clone());
    tracing::info!("Starting chat with {} (agent: {})", prompt, launcher.name());

    for env_var in launcher.required_env_vars() {
        if std::env::var(env_var).is_err() {
            anyhow::bail!("Environment variable {} is required", env_var);
        }
    }

    let command = launcher.command();
    tracing::info!("Launching: {:?}", command);

    let mut child = tokio::process::Command::new(&command[0])
        .args(&command[1..])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()?;

    let stdin = child.stdin.take().unwrap();
    let stdout = child.stdout.take().unwrap();

    let local_set = tokio::task::LocalSet::new();

    local_set.run_until(async move {
        let (conn, io_task) = acp::ClientSideConnection::new(
            TestClient,
            stdin.compat_write(),
            stdout.compat(),
            |fut| {
                tokio::task::spawn_local(fut);
            },
        );

        tokio::task::spawn_local(io_task);

        let init_request = acp::InitializeRequest::new(acp::ProtocolVersion::V1)
            .client_info(acp::Implementation::new("xbot-acp", "0.1.0").title("XBot ACP"));
        conn.initialize(init_request).await?;

        tracing::info!("Agent initialized");

        let cwd = std::env::current_dir()?;
        let session = conn.new_session(acp::NewSessionRequest::new(cwd)).await?;

        tracing::info!("Session created: {:?}", session.session_id);
        println!();

        let prompt_request = acp::PromptRequest::new(session.session_id, vec![prompt.into()]);
        conn.prompt(prompt_request).await?;

        println!();
        tracing::info!("Chat completed");

        Ok::<_, anyhow::Error>(())
    }).await
}

/// 飞书 Bot 运行
async fn run_feishu() -> anyhow::Result<()> {
    tracing::info!("Starting Feishu bot...");

    if std::env::var("ANTHROPIC_API_KEY").is_err() {
        anyhow::bail!("ANTHROPIC_API_KEY is required for Claude Code");
    }

    // 飞书配置
    let config = FeishuConfig {
        app_id: "cli_a9488bffb5f99cef".to_string(),
        app_secret: "m5Mvwd2NQhqHmYJ9Ob3fQer1V0NVSZMn".to_string(),
        encrypt_key: "".to_string(),
        verification_token: "".to_string(),
        bot_open_id: "".to_string(),
    };

    let channel = Arc::new(FeishuChannel::new(config));

    // 启动 Channel
    channel.start().await?;
    tracing::info!("Feishu bot started. Press Ctrl+C to stop.");

    // 获取消息流
    let mut message_rx = channel.message_stream();

    // 处理消息
    while let Some(msg) = futures::StreamExt::next(&mut message_rx).await {
        let chat_id = msg.chat_id.clone();
        let content = msg.content.clone();
        let message_id = msg.message_id.clone();

        tracing::info!("Message from {}: {}", chat_id, content);

        // 点赞
        if let Some(mid) = &message_id {
            let _ = channel.add_reaction(mid, "THUMBSUP").await;
        }

        // 处理 Agent (使用 LocalSet)
        let local_set = tokio::task::LocalSet::new();
        if let Err(e) = local_set.run_until(run_agent_for_message(Arc::clone(&channel), chat_id, content)).await {
            tracing::error!("Agent error: {:?}", e);
        }
    }

    Ok(())
}

/// 为单条消息运行 Agent
async fn run_agent_for_message(
    channel: Arc<FeishuChannel>,
    chat_id: String,
    prompt: String,
) -> anyhow::Result<()> {
    let launcher = create_launcher(AgentType::ClaudeCode);
    let command = launcher.command();

    tracing::info!("Launching agent: {:?}", command);

    let mut child = tokio::process::Command::new(&command[0])
        .args(&command[1..])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()?;

    let stdin = child.stdin.take().unwrap();
    let stdout = child.stdout.take().unwrap();

    let acp_client = AcpClient::new(Arc::clone(&channel));
    acp_client.set_chat_id(chat_id.clone()).await;

    let (conn, io_task) = acp::ClientSideConnection::new(
        acp_client,
        stdin.compat_write(),
        stdout.compat(),
        |fut| {
            tokio::task::spawn_local(fut);
        },
    );

    tokio::task::spawn_local(io_task);

    let init_request = acp::InitializeRequest::new(acp::ProtocolVersion::V1)
        .client_info(acp::Implementation::new("xbot-acp", "0.1.0").title("XBot ACP"));
    conn.initialize(init_request).await?;

    let cwd = std::env::current_dir()?;
    let session = conn.new_session(acp::NewSessionRequest::new(cwd)).await?;

    let prompt_request = acp::PromptRequest::new(session.session_id, vec![prompt.into()]);
    conn.prompt(prompt_request).await?;

    channel.finalize_stream(&chat_id).await?;

    Ok(())
}