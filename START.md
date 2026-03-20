# xbot Start Guide

这份文档面向日常使用者，重点说明 xbot 默认的 `~/.xbot` 目录、`config.json`、自定义 `skills`、MCP 配置，以及它们之间的关系。

## 1. 默认目录结构

执行一次 `xbot onboard` 后，默认会生成一套本地目录：

```text
~/.xbot/
├── config.json
├── workspace/
│   ├── AGENTS.md
│   ├── SOUL.md
│   ├── USER.md
│   ├── TOOLS.md
│   ├── HEARTBEAT.md
│   ├── memory/
│   │   ├── MEMORY.md
│   │   └── HISTORY.md
│   └── skills/
├── cron/
├── logs/
├── media/
└── sessions/
```

最重要的有 2 个位置：

- `~/.xbot/config.json`
  xbot 的主配置文件，模型、provider、channel、MCP、工具开关都在这里。
- `~/.xbot/workspace/`
  agent 的工作区。记忆、提示模板、自定义 skills、周期任务文件都在这里。

## 2. 配置与 Workspace 的分工

可以先记住一个简单原则：

- `config.json` 管“运行参数”
  比如模型、API Key、MCP server、端口、channel 配置、工具限制。
- `workspace/` 管“Agent 知识和行为素材”
  比如 memory、AGENTS.md、SOUL.md、自定义 skills、HEARTBEAT.md。

也就是说：

- 想换模型、加 provider、接 MCP：改 `config.json`
- 想加技能、写长期记忆、改 agent 风格：改 `workspace/`

## 3. `config.json` 最小可用示例

一个最小可用配置通常至少包含默认模型和 provider API Key：

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.xbot/workspace",
      "model": "anthropic/claude-opus-4-5",
      "provider": "auto"
    }
  },
  "providers": {
    "anthropic": {
      "apiKey": "YOUR_API_KEY"
    }
  }
}
```

说明：

- `workspace` 默认就是 `~/.xbot/workspace`
- `provider` 设成 `auto` 时，xbot 会按模型和已配置的 key 自动匹配 provider
- JSON 配置里通常使用 camelCase，例如 `apiKey`、`apiBase`、`mcpServers`

## 4. `config.json` 里最常用的几块

### 4.1 `agents.defaults`

这部分控制 agent 的默认运行行为。

常用字段：

- `workspace`: 默认工作区路径
- `model`: 默认模型
- `provider`: 指定 provider，或用 `auto`
- `maxTokens`: 单次输出上限
- `contextWindowTokens`: 上下文窗口
- `temperature`: 生成温度
- `maxToolIterations`: 单轮最多工具调用次数
- `reasoningEffort`: 推理强度，可选 `low` / `medium` / `high`

示例：

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.xbot/workspace",
      "model": "openai/gpt-5",
      "provider": "openai",
      "maxTokens": 8192,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  }
}
```

### 4.2 `providers`

这里放各家模型服务的凭证与地址。

常见字段：

- `apiKey`
- `apiBase`
- `extraHeaders`

示例：

```json
{
  "providers": {
    "openai": {
      "apiKey": "YOUR_OPENAI_KEY"
    },
    "openrouter": {
      "apiKey": "YOUR_OPENROUTER_KEY"
    },
    "custom": {
      "apiKey": "YOUR_KEY",
      "apiBase": "https://your-openai-compatible-endpoint/v1"
    }
  }
}
```

### 4.3 `channels`

这里放各个聊天渠道的配置。不同 channel 会读取各自字段，xbot 不强制统一每个平台的细节字段。

通用字段：

- `sendProgress`: 是否把 agent 的过程性文本实时发到 channel
- `sendToolHints`: 是否把工具调用提示也发到 channel

不同平台自己的 token、appId、secret、allowFrom 等，也会写在 `channels.<name>` 下。

### 4.4 `gateway`

控制网关监听地址和 heartbeat。

常用字段：

- `host`
- `port`
- `heartbeat.enabled`
- `heartbeat.intervalS`

### 4.5 `tools`

控制工具行为，包括 web、shell 和 MCP。

常用字段：

- `restrictToWorkspace`
- `exec.timeout`
- `exec.pathAppend`
- `web.proxy`
- `web.search.provider`
- `mcpServers`

推荐先打开这个安全开关：

```json
{
  "tools": {
    "restrictToWorkspace": true
  }
}
```

它会把 agent 的工具访问限制在 workspace 范围内。

## 5. `workspace/` 里这些文件分别做什么

### 5.1 `AGENTS.md`

给 agent 的补充指令。适合写：

- 你的团队约定
- 回复风格
- 特定流程要求
- 定时任务处理方式

### 5.2 `SOUL.md`

更偏人格和长期行为风格。不是必须改，但可以用来定义 bot 的语气、定位和偏好。

### 5.3 `USER.md`

放用户背景、长期上下文、使用偏好等。

### 5.4 `TOOLS.md`

补充工具使用约束，适合写：

- 哪些工具优先使用
- 哪些目录不要碰
- 哪些外部系统需要谨慎调用

### 5.5 `memory/MEMORY.md`

长期记忆文件，适合存：

- 用户偏好
- 项目常识
- 稳定联系人关系
- 长期有效的规则

### 5.6 `memory/HISTORY.md`

历史摘要日志，便于 grep 检索，一般不用手动维护。

### 5.7 `HEARTBEAT.md`

周期任务清单。`xbot gateway` 运行时会按 heartbeat 周期读取它，并把待处理任务发给 agent 执行。

## 6. Skills 怎么用

### 6.1 内置 skills

xbot 自带一批内置 skills，比如：

- `github`
- `weather`
- `summarize`
- `tmux`
- `memory`
- `cron`
- `clawhub`

内置 skill 不需要你自己创建，运行时会自动发现。

### 6.2 自定义 skills 放哪里

推荐放在：

```text
~/.xbot/workspace/skills/<skill-name>/SKILL.md
```

例如：

```text
~/.xbot/workspace/skills/my-helper/SKILL.md
```

这是最标准、最稳定的自定义 skill 目录。

高级用法里，xbot 也支持：

```text
~/.xbot/workspace/.xbot/skills/<skill-name>/SKILL.md
```

但对普通用户来说，优先使用 `workspace/skills/` 即可。

### 6.3 一个最小 skill 例子

```md
---
name: my-helper
description: Help with my team's internal workflow
---

# My Helper

When the user asks about release notes:
- summarize the latest changes
- keep the reply short
- list risks first
```

### 6.4 skills 的加载优先级

用户可简单理解为：

1. `workspace/skills/` 里的自定义 skills 优先
2. `workspace/.xbot/skills/` 其次
3. xbot 自带内置 skills 最后

所以如果你定义了同名 skill，通常会覆盖内置 skill。

### 6.5 如何安装公共 skills

如果你使用内置的 `clawhub` skill，公共 skills 会安装到：

```text
~/.xbot/workspace/skills/
```

例如：

```bash
npx --yes clawhub@latest install <slug> --workdir ~/.xbot/workspace
```

`--workdir ~/.xbot/workspace` 很重要，否则 skill 可能被装到错误目录。

## 7. MCP 怎么配

MCP 让 xbot 连接外部工具服务器，并把它们当成 agent 的原生工具来使用。

MCP 配置写在：

```text
~/.xbot/config.json
```

路径位置是：

```json
{
  "tools": {
    "mcpServers": {
    }
  }
}
```

### 7.1 本地 stdio MCP 示例

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
      }
    }
  }
}
```

适用于本机启动一个 MCP 进程，比如通过 `npx` 或 `uvx`。

### 7.2 远程 HTTP/SSE MCP 示例

```json
{
  "tools": {
    "mcpServers": {
      "myRemoteMcp": {
        "url": "https://example.com/mcp/",
        "headers": {
          "Authorization": "Bearer YOUR_TOKEN"
        }
      }
    }
  }
}
```

适用于远程 MCP 服务。

`type` 可以省略，xbot 会自动判断：

- 有 `command` 时，通常按 `stdio` 处理
- 有 `url` 且以 `/sse` 结尾时，通常按 `sse` 处理
- 其他 `url` 默认按 `streamableHttp` 处理

### 7.3 `enabledTools` 用法

如果一个 MCP server 提供很多工具，你可以只注册其中一部分：

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
        "enabledTools": ["read_file", "mcp_filesystem_write_file"]
      }
    }
  }
}
```

规则如下：

- 不写 `enabledTools`，等于全部启用
- `["*"]`，等于全部启用
- `[]`，等于一个都不注册
- 写成具体数组，只注册那几个工具

它既支持原始 MCP 工具名，也支持 xbot 包装后的工具名：

- 原始名：`read_file`
- 包装名：`mcp_filesystem_write_file`

### 7.4 `toolTimeout`

如果某个 MCP server 很慢，可以单独提高超时：

```json
{
  "tools": {
    "mcpServers": {
      "slowServer": {
        "url": "https://example.com/mcp/",
        "toolTimeout": 120
      }
    }
  }
}
```

## 8. 多实例怎么理解

默认实例使用：

- 配置：`~/.xbot/config.json`
- 工作区：`~/.xbot/workspace`

如果你想做多实例，可以换一套配置路径：

```bash
xbot onboard --config ~/.xbot-telegram/config.json --workspace ~/.xbot-telegram/workspace
xbot gateway --config ~/.xbot-telegram/config.json
```

这时：

- `~/.xbot-telegram/config.json` 是该实例配置
- `~/.xbot-telegram/workspace/` 是该实例工作区
- `cron/`、`media/`、`logs/` 等运行数据也会跟着这个 config 目录走

适合：

- 区分测试和生产
- 一个实例接 Telegram，一个接 Discord
- 不同团队使用不同模型和 skills

## 9. 推荐的实际管理方式

如果你是普通用户，建议这样用：

1. 先只维护一个主实例：`~/.xbot/`
2. 只在 `config.json` 里配置 provider、channel、MCP
3. 只在 `workspace/skills/` 里放自定义 skills
4. 只在 `workspace/memory/` 里维护长期记忆
5. 需要定时任务时改 `HEARTBEAT.md`

这样结构最清晰，也最不容易混乱。

## 10. 一个推荐起步配置

下面这份配置适合作为大多数用户的起点：

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.xbot/workspace",
      "model": "anthropic/claude-opus-4-5",
      "provider": "auto",
      "maxTokens": 8192,
      "temperature": 0.1
    }
  },
  "providers": {
    "anthropic": {
      "apiKey": "YOUR_API_KEY"
    }
  },
  "tools": {
    "restrictToWorkspace": true,
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/.xbot/workspace"]
      }
    }
  },
  "gateway": {
    "host": "0.0.0.0",
    "port": 18790
  }
}
```

## 11. 快速检查清单

如果你发现 xbot 没按预期工作，可以先检查：

- `~/.xbot/config.json` 是否是合法 JSON
- `agents.defaults.workspace` 路径是否存在
- `providers.<name>.apiKey` 是否已填写
- 自定义 skill 是否放在 `workspace/skills/<name>/SKILL.md`
- `mcpServers` 里的命令或 URL 是否可用
- 是否把 `enabledTools` 误写成了不存在的工具名
- 是否把真正想改的内容写到了错误实例的 config 里

## 12. 一句话总结

把 xbot 理解成两层就够了：

- `config.json` 决定“它怎么运行”
- `workspace/` 决定“它知道什么、怎么做事”

其中：

- 自定义技能放 `workspace/skills/`
- MCP 配在 `config.json` 的 `tools.mcpServers`
- 周期任务放 `workspace/HEARTBEAT.md`
- 长期记忆放 `workspace/memory/MEMORY.md`
