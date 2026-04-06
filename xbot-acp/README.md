# xbot-acp

基于 ACP (Agent Client Protocol) 协议的多平台消息机器人，支持 Claude Code 和 Codex CLI。

## 功能特性

- **多 Agent 支持**: Claude Code、Codex CLI
- **多平台扩展**: 飞书（已实现）、其他平台可扩展
- **权限审批**: 通过飞书交互卡片进行权限审批
- **流式响应**: 实时显示 AI 响应
- **边界保护**: 文件和终端操作的安全边界

## 快速开始

### 安装

```bash
# 克隆项目
git clone https://github.com/your-repo/xbot-acp.git
cd xbot-acp

# 构建
cargo build --release
```

### CLI 使用

```bash
# 测试 Claude Code 连接
export ANTHROPIC_API_KEY=your_key
cargo run --release -- test

# 与 Claude Code 对话
cargo run --release -- chat "hello"

# 使用 Codex
export OPENAI_API_KEY=your_key
cargo run --release -- chat "hello" codex
cargo run --release -- test codex
```

## 配置

### 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `ANTHROPIC_API_KEY` | Claude Code API Key | 使用 Claude Code 时 |
| `OPENAI_API_KEY` | Codex API Key | 使用 Codex 时 |
| `FEISHU_APP_ID` | 飞书应用 ID | 使用飞书时 |
| `FEISHU_APP_SECRET` | 飞书应用 Secret | 使用飞书时 |
| `FEISHU_ENCRYPT_KEY` | 飞书加密 Key | 使用飞书时 |
| `FEISHU_VERIFICATION_TOKEN` | 飞书验证 Token | 使用飞书时 |
| `FEISHU_BOT_OPEN_ID` | 飞书机器人 Open ID | 使用飞书时 |

### 配置文件

配置文件位于 `config/default.toml`:

```toml
[app]
name = "xbot-acp"
log_level = "info"
default_agent = "claude-code"

[agent]
command = "claude"

[file]
blocked_paths = ["/etc", "/System", "/usr", "/bin", "/sbin"]

[terminal]
blocked_patterns = ["rm -rf /", "dd if=/dev/zero"]
max_execution_time = 1800

[permission]
timeout = 30
cleanup_interval = 60
```

## Docker 部署

```bash
# 构建镜像
docker build -t xbot-acp .

# 使用 docker-compose
docker-compose up -d
```

## 项目结构

```
xbot-acp/
├── crates/
│   ├── acp-client/        # ACP Client 核心
│   │   ├── client.rs      # acp::Client trait 实现
│   │   ├── connection.rs  # Agent 进程管理
│   │   ├── session.rs     # 会话管理
│   │   ├── boundary/      # 边界检查
│   │   └── agent/         # Agent 启动器
│   ├── feishu-channel/    # 飞书 Channel
│   │   ├── api.rs         # 飞书 API
│   │   ├── websocket.rs   # WebSocket 连接
│   │   ├── card.rs        # 交互卡片
│   │   └── channel.rs     # FeishuChannel
│   └── app/               # 应用入口
│       └── main.rs        # CLI
├── config/                # 配置文件
├── Dockerfile
├── docker-compose.yml
└── Cargo.toml
```

## 支持的 Agent

| Agent | 命令 | 协议 | 说明 |
|-------|------|------|------|
| Claude Code | `claude` | ACP 原生 | Anthropic 官方 CLI |
| Codex CLI | `npx @zed-industries/codex-acp` | codex-acp 适配器 | OpenAI Codex |

## 开发

```bash
# 运行测试
cargo test

# 代码检查
cargo clippy

# 格式化
cargo fmt
```

## 许可证

MIT