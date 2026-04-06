//! Agent 连接集成测试

use std::time::Duration;

use tokio::time::timeout;
use tokio_util::compat::{TokioAsyncReadCompatExt, TokioAsyncWriteCompatExt};
use once_cell::sync::Lazy;
use std::sync::Arc;
use tokio::sync::Mutex;

use acp_client::acp::{self, Agent, Client};

/// 测试 ACP Agent 是否能正确启动和初始化
#[tokio::test]
async fn test_agent_startup_and_initialize() {
    // 检查 ANTHROPIC_API_KEY 是否设置
    if std::env::var("ANTHROPIC_API_KEY").is_err() {
        eprintln!("Skipping test: ANTHROPIC_API_KEY not set");
        return;
    }

    // 使用 npx 启动 ACP agent
    let mut child = tokio::process::Command::new("npx")
        .arg("@agentclientprotocol/claude-agent-acp")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .expect("Failed to start agent");

    let stdin = child.stdin.take().expect("No stdin");
    let stdout = child.stdout.take().expect("No stdout");

    // 使用 LocalSet 运行 !Send futures
    let local_set = tokio::task::LocalSet::new();

    let result = local_set.run_until(async move {
        // 创建 ACP 连接
        let (conn, io_task) = acp::ClientSideConnection::new(
            TestClient,
            stdin.compat_write(),
            stdout.compat(),
            |fut| {
                tokio::task::spawn_local(fut);
            },
        );

        tokio::task::spawn_local(io_task);

        // 初始化连接，设置 30 秒超时
        let init_result = timeout(Duration::from_secs(30), async {
            conn.initialize(
                acp::InitializeRequest::new(acp::ProtocolVersion::V1)
                    .client_info(acp::Implementation::new("test-client", "0.1.0").title("Test Client"))
            ).await
        }).await;

        match init_result {
            Ok(Ok(response)) => {
                println!("Agent initialized successfully!");
                if let Some(info) = response.agent_info {
                    println!("Agent: {} v{}", info.name, info.version);
                }
                Ok(())
            }
            Ok(Err(e)) => {
                eprintln!("Initialize error: {:?}", e);
                Err(anyhow::anyhow!("Initialize error: {:?}", e))
            }
            Err(_) => {
                eprintln!("Initialize timeout after 30s");
                Err(anyhow::anyhow!("Initialize timeout"))
            }
        }
    }).await;

    assert!(result.is_ok(), "Agent initialization should succeed");
}

/// 测试 Agent 创建 Session
#[tokio::test]
async fn test_agent_new_session() {
    if std::env::var("ANTHROPIC_API_KEY").is_err() {
        eprintln!("Skipping test: ANTHROPIC_API_KEY not set");
        return;
    }

    let mut child = tokio::process::Command::new("npx")
        .arg("@agentclientprotocol/claude-agent-acp")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .expect("Failed to start agent");

    let stdin = child.stdin.take().expect("No stdin");
    let stdout = child.stdout.take().expect("No stdout");

    let local_set = tokio::task::LocalSet::new();

    let result = local_set.run_until(async move {
        let (conn, io_task) = acp::ClientSideConnection::new(
            TestClient,
            stdin.compat_write(),
            stdout.compat(),
            |fut| {
                tokio::task::spawn_local(fut);
            },
        );

        tokio::task::spawn_local(io_task);

        // 初始化
        conn.initialize(
            acp::InitializeRequest::new(acp::ProtocolVersion::V1)
                .client_info(acp::Implementation::new("test-client", "0.1.0").title("Test Client"))
        ).await?;

        // 创建 session
        let cwd = std::env::current_dir()?;
        let session = conn.new_session(acp::NewSessionRequest::new(cwd)).await?;

        println!("Session created: {:?}", session.session_id);
        Ok::<_, anyhow::Error>(session.session_id)
    }).await;

    assert!(result.is_ok(), "Session creation should succeed");
    let session_id = result.unwrap();
    assert!(!session_id.0.is_empty(), "Session ID should not be empty");
}

/// 测试 Agent 发送 Prompt 并接收响应
#[tokio::test]
async fn test_agent_prompt_and_response() {
    if std::env::var("ANTHROPIC_API_KEY").is_err() {
        eprintln!("Skipping test: ANTHROPIC_API_KEY not set");
        return;
    }

    let mut child = tokio::process::Command::new("npx")
        .arg("@agentclientprotocol/claude-agent-acp")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .expect("Failed to start agent");

    let stdin = child.stdin.take().expect("No stdin");
    let stdout = child.stdout.take().expect("No stdout");

    let local_set = tokio::task::LocalSet::new();

    let result: Result<String, anyhow::Error> = local_set.run_until(async move {
        let (conn, io_task) = acp::ClientSideConnection::new(
            TestClientWithOutput,
            stdin.compat_write(),
            stdout.compat(),
            |fut| {
                tokio::task::spawn_local(fut);
            },
        );

        tokio::task::spawn_local(io_task);

        // 初始化
        conn.initialize(
            acp::InitializeRequest::new(acp::ProtocolVersion::V1)
                .client_info(acp::Implementation::new("test-client", "0.1.0").title("Test Client"))
        ).await?;

        // 创建 session
        let cwd = std::env::current_dir()?;
        let session = conn.new_session(acp::NewSessionRequest::new(cwd)).await?;

        // 发送 prompt
        conn.prompt(
            acp::PromptRequest::new(
                session.session_id.clone(),
                vec!["Say 'Hello Test' and nothing else".into()]
            )
        ).await?;

        // 等待响应收集
        tokio::time::sleep(Duration::from_secs(10)).await;

        Ok(TestClientWithOutput::get_output().await)
    }).await;

    assert!(result.is_ok(), "Prompt should succeed");
    let output = result.unwrap();
    println!("Agent response: {}", output);
    // 响应应该包含一些内容（Agent 会回复）
    assert!(!output.is_empty(), "Agent should respond with content");
}

/// 简单的测试 Client
struct TestClient;

#[async_trait::async_trait(?Send)]
impl Client for TestClient {
    async fn request_permission(
        &self,
        args: acp::RequestPermissionRequest,
    ) -> acp::Result<acp::RequestPermissionResponse> {
        // 自动批准第一个选项
        if let Some(option) = args.options.first() {
            Ok(acp::RequestPermissionResponse::new(
                acp::RequestPermissionOutcome::Selected(
                    acp::SelectedPermissionOutcome::new(option.option_id.clone())
                )
            ))
        } else {
            Ok(acp::RequestPermissionResponse::new(
                acp::RequestPermissionOutcome::Cancelled
            ))
        }
    }

    async fn session_notification(
        &self,
        _args: acp::SessionNotification,
    ) -> acp::Result<()> {
        // 忽略通知
        Ok(())
    }
}

/// 收集输出的测试 Client
struct TestClientWithOutput;

static OUTPUT: Lazy<Arc<Mutex<String>>> = Lazy::new(|| {
    Arc::new(Mutex::new(String::new()))
});

impl TestClientWithOutput {
    async fn get_output() -> String {
        OUTPUT.lock().await.clone()
    }
}

#[async_trait::async_trait(?Send)]
impl Client for TestClientWithOutput {
    async fn request_permission(
        &self,
        args: acp::RequestPermissionRequest,
    ) -> acp::Result<acp::RequestPermissionResponse> {
        if let Some(option) = args.options.first() {
            Ok(acp::RequestPermissionResponse::new(
                acp::RequestPermissionOutcome::Selected(
                    acp::SelectedPermissionOutcome::new(option.option_id.clone())
                )
            ))
        } else {
            Ok(acp::RequestPermissionResponse::new(
                acp::RequestPermissionOutcome::Cancelled
            ))
        }
    }

    async fn session_notification(
        &self,
        args: acp::SessionNotification,
    ) -> acp::Result<()> {
        match &args.update {
            acp::SessionUpdate::AgentMessageChunk(chunk) => {
                if let acp::ContentBlock::Text(text) = &chunk.content {
                    let mut output = OUTPUT.lock().await;
                    output.push_str(&text.text);
                }
            }
            _ => {}
        }
        Ok(())
    }
}