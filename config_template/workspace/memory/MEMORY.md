# Long-term Memory

This file stores important information that should persist across sessions.

## User Information

- **Name**: 阿六
- **Role**: 老板

## Preferences

(User preferences learned over time)

## Project Context

(Information about ongoing projects)

## Servers

### 阿里云 ECS 服务器

| 项目 | 信息 |
|------|------|
| **域名** | llm.alrun.cn |
| **SSH 地址** | root@llm.alrun.cn |
| **系统** | Alibaba Cloud Linux 3 (OpenAnolis Edition) |
| **架构** | x86_64 |
| **内存** | 1.8GB |
| **磁盘** | 40GB |
| **Swap** | 2GB |

**运行的服务：**
- xbot (AI 机器人) - 用户 xbot, 端口 18790
- Clash Meta (VPN 代理) - 端口 7890 (HTTP), 7891 (SOCKS), 9090 (管理面板)
- LLM Gateway - 端口 5003
- Caddy - 反向代理
- PostgreSQL, Redis

**关键目录：**
- xbot 代码: `/opt/xbot`
- xbot 配置: `/home/xbot/.xbot`
- Clash 配置: `/opt/clash`

**SSH 连接方式：**
```bash
ssh root@llm.alrun.cn
```

## Important Notes

(Things to remember)

---

*This file is automatically updated by xbot when important information should be remembered.*
