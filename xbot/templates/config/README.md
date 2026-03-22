# XBot 配置说明

Xbot 支持两种配置模式：**极简模式** 和 **完整模式**。

## 极简模式（推荐新手）

只需两个文件即可启动：

```
~/.xbot/
├── config.json              # 核心配置
└── providers/
    └── default.json         # API 密钥
```

### config.json
```json
{
  "agents": {
    "defaults": {
      "model": "glm-5"
    }
  }
}
```

### providers/default.json
```json
{
  "api_key": "sk-xxx",
  "base_url": "https://coding.dashscope.aliyuncs.com/apps/anthropic"
}
```

启动命令：
```bash
xbot gateway
```

## 完整模式

配置文件按功能域拆分：

```
~/.xbot/
├── config.json              # 核心配置（model、workspace 等）
├── providers/
│   └── default.json         # API 配置（api_key、base_url）
├── channels/
│   ├── telegram.json        # Telegram 完整配置
│   ├── feishu.json          # 飞书完整配置
│   ├── discord.json         # Discord 完整配置
│   ├── slack.json           # Slack 完整配置
│   ├── whatsapp.json        # WhatsApp 完整配置
│   ├── dingtalk.json        # 钉钉完整配置
│   ├── wecom.json           # 企业微信完整配置
│   ├── matrix.json          # Matrix 完整配置
│   ├── qq.json              # QQ 完整配置
│   ├── mochat.json          # Mochat 完整配置
│   └── email.json           # Email 完整配置
├── tools.json               # 工具配置（可选）
└── gateway.json             # 网关配置（可选）
```

## 环境变量（最高优先级）

可通过环境变量覆盖配置：

```bash
export XBOT_API_KEY="sk-xxx"
export XBOT_BASE_URL="https://api.anthropic.com"
export XBOT_MODEL="claude-3-opus"
```

## Provider 自动识别

| base_url 包含 | 自动识别为 |
|---------------|-----------|
| api.anthropic.com | anthropic |
| dashscope.aliyuncs.com | aliyun_coding_plan |
| alrun | alrun |
| 其他 | custom（自动适配） |

## Channel 快速启用

### Telegram
```json
{
  "enabled": true,
  "token": "YOUR_BOT_TOKEN",
  "allow_from": ["YOUR_USER_ID"]
}
```

### 飞书
```json
{
  "enabled": true,
  "app_id": "cli_xxx",
  "app_secret": "xxx"
}
```

### Discord
```json
{
  "enabled": true,
  "token": "YOUR_DISCORD_BOT_TOKEN"
}
```

### Slack
```json
{
  "enabled": true,
  "bot_token": "xoxb-xxx",
  "app_token": "xapp-xxx"
}
```

### 企业微信
```json
{
  "enabled": true,
  "bot_id": "YOUR_BOT_ID",
  "secret": "YOUR_SECRET"
}
```

### 钉钉
```json
{
  "enabled": true,
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET"
}
```

### QQ
```json
{
  "enabled": true,
  "app_id": "YOUR_APP_ID",
  "secret": "YOUR_SECRET"
}
```

## 配置优先级

1. 环境变量（最高）
2. `channels/*.json`
3. `providers/default.json`
4. `tools.json`
5. `config.json`
6. 默认值（最低）

## Git 忽略敏感配置

```gitignore
# 敏感配置
.xbot/providers/
.xbot/channels/
```

## 向后兼容

现有的完整 `config.json` 继续支持，无需修改。