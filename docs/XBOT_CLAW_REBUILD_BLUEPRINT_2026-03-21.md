# XBOT 重写蓝图（唯一源版本）

更新时间：2026-03-21  
目标：页面结构和视觉风格对齐 `xbot-claw`，但系统模型严格对齐当前 `xbot`；采用“唯一真源”，避免 DB/文件双写漂移。

## 1. 核心决策

- 前端重写：保留 `xbot-claw` 的导航结构和视觉层次，不复用旧业务代码。
- 后端重写：按当前 `xbot` 实际域重建：`config/skills/mcp/cron/channels(telegram,feishu)`。
- 存储策略：**全文件真源（Single Source of Truth = Filesystem）**。
- Gateway 与 API 同端口：`:8080`。

不采用：

- DB + 文件双真源
- 文件监听反向同步数据库
- 多入口直接改配置文件

## 2. 目标范围（MVP）

1. `Config`（`~/.xbot/config.json` 全量配置）
2. `Skills`（workspace + builtin skills）
3. `MCP`（server 配置、测试缓存）
4. `Cron`（任务与执行日志）
5. `Telegram Channel`（账号、路由、消息日志）
6. `Feishu Channel`（账号、路由、消息日志）

## 3. 部署与模块

- 单端口分流：
  - `/`、`/assets/*` -> 前端
  - `/api/v1/*` -> 后端
- 运行模块：
  - `gateway`：鉴权、统一错误、写入口管控
  - `domains`：config/skill/mcp/cron/channels
  - `runtime`：cron worker、channel worker、agent runtime
  - `store`：文件读写、锁、原子提交、快照恢复

## 4. 唯一真源设计（文件系统）

## 4.1 目录结构（建议）

```text
~/.xbot/
  config.json
  state/
    skills/
      <skill_key>/SKILL.md
      <skill_key>/meta.json
    mcp/
      servers.json
      health_logs.jsonl
    cron/
      jobs.json
      runs/
        <job_id>.jsonl
    channels/
      telegram.json
      feishu.json
      conversations.jsonl
      messages.jsonl
  locks/
    config.lock
    mcp.lock
    cron.lock
    channels.lock
  snapshots/
    yyyyMMdd-HHmmss/
```

与当前 `xbot` 对齐说明：

- `config.json`：主配置真源（当前系统已有）
- `*.json`：域内状态文件（可覆盖）
- `*.jsonl`：事件/日志追加写（审计与回放）
- `skills/`：目录即资源

当前代码已确认的真实路径/机制：

- 配置文件默认：`~/.xbot/config.json`（`xbot/config/loader.py`）
- Cron 存储：`<config_dir>/cron/jobs.json`（`xbot/config/paths.py` + `xbot/cron/service.py`）
- Skill 加载顺序：`workspace/skills` -> `workspace/.xbot/skills` -> `xbot/skills`（`xbot/agent/skills.py`）
- MCP 来源：`config.tools.mcp_servers`（`xbot/config/schema.py`）
- Channel 来源：`config.channels.<channel>`（`xbot/channels/manager.py`）

## 4.2 读写规则（关键）

1. 所有写操作只能通过 Gateway/API（单写入口）。  
2. 写流程固定：`加锁 -> 写临时文件 -> fsync -> rename -> 解锁`。  
3. 运行时读取“内存快照”，由文件变更事件触发热重载。  
4. 禁止运行时人工编辑核心文件；需要运维改动时走 `xbotctl` 或 API。  
5. 失败恢复：启动时加载最近可用快照，坏文件进入 `*.corrupt`。

## 4.3 一致性与并发

- 无分布式前提下，用 `fcntl/flock` 文件锁即可。
- 每个域单独锁文件，避免全局大锁。
- 操作幂等：每次变更带 `op_id`，写入 jsonl，重复请求可去重。

## 5. “本地 skill 新增但系统未知”怎么处理

采用“文件即真源”后：

- `skills/<key>/SKILL.md` 新增后即被扫描并纳入运行时快照。
- 无需 DB 映射，不存在“文件有但 DB 没有”的漂移。
- 冲突规则：
  - 同 key 已存在：按目录优先，记录警告到 `state/skills_warnings.jsonl`
  - 格式错误：跳过加载并标记 invalid

## 6. API 设计（文件真源版，按当前 xbot 概念）

统一前缀：`/api/v1`

### 6.1 Config

- `GET /config`（读取整份 `config.json`）
- `PUT /config`（整份覆盖，版本号校验）
- `PATCH /config`（按 JSON Patch 局部更新）
- `POST /config/validate`

### 6.2 Skills（与 `SkillsLoader` 一致）

- `GET /skills`
- `GET /skills/{key}`
- `PUT /skills/{key}`（写入/覆盖 `SKILL.md`）
- `DELETE /skills/{key}`
- `POST /skills/import-zip`
- `POST /skills/reload`
- `GET /skills/sources`（返回 workspace/scoped_workspace/builtin 三层来源）

### 6.3 MCP

- `GET /mcp/servers`（来自 `config.tools.mcp_servers`）
- `PUT /mcp/servers`（写回 `config.json` 的该节点）
- `POST /mcp/servers/test`
- `POST /mcp/servers/discover`
- `GET /mcp/health-logs`

### 6.4 Cron

- `GET /cron/jobs`
- `PUT /cron/jobs`（整包更新）
- `POST /cron/jobs/{jobId}/run`
- `POST /cron/jobs/{jobId}/enable`
- `POST /cron/jobs/{jobId}/disable`
- `GET /cron/jobs/{jobId}/runs`

### 6.5 Channels（Telegram / 飞书）

- `GET /channels/telegram`（来自 `config.channels.telegram`）
- `PUT /channels/telegram`（写回 `config.json`）
- `GET /channels/feishu`（来自 `config.channels.feishu`）
- `PUT /channels/feishu`（写回 `config.json`）
- `POST /channels/{type}/test`
- `GET /channels/{type}/messages`
- `POST /channels/{type}/send`
- `POST /channels/telegram/webhook/{accountKey}`（可选）
- `POST /channels/feishu/webhook/{accountKey}`

## 7. 前端结构（复刻 xbot-claw）

## 7.1 页面导航

- Dashboard
- Config
  - Agent Defaults
  - Providers
  - Tools
  - Gateway
- Assets
  - Skills
- MCP Servers
- Cron
- Channels
  - Telegram
  - Feishu

## 7.2 前端模块

- `app/`（路由、Shell、全局 Provider）
- `modules/config/`
- `modules/skills/`
- `modules/mcp/`
- `modules/cron/`
- `modules/channels/telegram/`
- `modules/channels/feishu/`
- `shared/ui/`
- `shared/api/`
- `shared/types/`

## 8. 同步机制（不是双向同步）

这是“唯一源 + 运行时快照”机制：

1. API 写 `config.json`/`jobs.json`/skills 文件（原子提交）  
2. 变更事件推送给 runtime（in-process bus）  
3. runtime 重建对应域快照  
4. Agent/Channel/Cron 在下一次调度使用新快照  

没有 DB，因此也没有 DB<->文件同步问题。

## 9. 风险与约束

- 优点：简单、可解释、无双写漂移。
- 风险：复杂查询和跨域统计能力弱于 DB。
- 应对：
  - 读多维统计时，基于 jsonl 构建内存索引缓存
  - 必要时后续加“只读索引库”（非真源）

## 10. 实施计划（3 期）

### P1（1 周）

- 文件存储层（锁、原子写、快照、恢复）
- Config/Skill/MCP 基础 API
- 前端壳子和核心页面

### P2（1 周）

- Cron 全链路（执行器 + runs 日志）
- Telegram/飞书接入（配置、测试、消息收发）
- 运行时热重载机制

### P3（0.5-1 周）

- 导入迁移脚本（从旧项目导入到文件真源）
- 端到端回归和压测
- 运维工具 `xbotctl`（导出/校验/修复）

## 11. 验收标准

- `config/skill/mcp/cron/telegram/feishu` 全部由文件真源驱动。
- 概念与当前 `xbot` 一致：不引入 `profiles/workspaces/rules` 新实体。
- 任意配置改动经过 API 后，10 秒内可在运行时生效。
- 无 DB 依赖也可完整启动并运行。
- 前端结构与 `xbot-claw` 主导航层级一致。
