# xbot 配置模板

本目录包含 xbot 的配置模板文件。

## 目录结构

```
config_template/
├── config.json.example          # 主配置文件模板
├── README.md                    # 本文件
└── workspace/                   # 工作空间模板
    ├── AGENTS.md               # Agent 行为配置
    ├── HEARTBEAT.md            # 心跳任务
    ├── SOUL.md                 # Agent 人格设定
    ├── TOOLS.md                # 工具使用说明
    ├── USER.md                 # 用户配置
    └── memory/
        ├── MEMORY.md           # 长期记忆
        └── HISTORY.md          # 历史日志
```

## 使用方法

### 1. 复制配置文件

```bash
# 创建配置目录
mkdir -p ~/.xbot/workspace/memory

# 复制主配置
cp config_template/config.json.example ~/.xbot/config.json

# 复制 workspace 模板
cp -r config_template/workspace/* ~/.xbot/workspace/
```

### 2. 修改配置

编辑 `~/.xbot/config.json`，填入您的 API Key 和 Telegram Token：

```json
{
  "providers": {
    "aliyun_coding_plan": {
      "api_key": "YOUR_ACTUAL_API_KEY"
    }
  },
  "channels": {
    "telegram": {
      "token": "YOUR_TELEGRAM_BOT_TOKEN"
    }
  }
}
```

### 3. 获取 Telegram Bot Token

1. 在 Telegram 中找到 @BotFather
2. 发送 `/newbot` 创建新机器人
3. 按提示设置名称，获取 Token

## 配置说明

### agents 配置

| 字段 | 说明 |
|------|------|
| `type` | 后端类型：`claude_sdk` 或 `litellm` |
| `model` | 模型名称 |
| `provider` | 提供商名称 |

### channels 配置

| 字段 | 说明 |
|------|------|
| `telegram.enabled` | 是否启用 Telegram 通道 |
| `telegram.token` | Bot Token |
| `telegram.proxy` | 代理地址（如需要） |

### tools 配置

| 字段 | 说明 |
|------|------|
| `web.proxy` | 网络请求代理 |
| `exec.timeout` | 命令执行超时时间 |

## 自定义

### 修改 Agent 人格

编辑 `~/.xbot/workspace/SOUL.md` 来自定义 Agent 的性格和行为。

### 修改用户信息

编辑 `~/.xbot/workspace/USER.md` 来设置您的偏好和上下文信息。
