# xbot Module Boundaries

Module ownership boundaries for parallel agent deep-dives. Each agent
should own exactly one boundary to avoid overlapping edits.

- **runtime 核心** — `xbot/runtime/core/`
  service, command_handlers, client_pool, context, task_supervisor,
  protocol, hooks, types

- **状态机** — `xbot/runtime/state/` + `xbot/runtime/session/`
  state transitions, session lifecycle

- **系统服务** — `xbot/runtime/system/`
  cron, heartbeat, monitoring

- **channels** — `xbot/channels/`
  11 channels + manager + registry

- **平台层** — `xbot/platform/`
  config, bus, security, providers, logging, utils

- **tools** — `xbot/tools/`
  shell, web, web_http_transport, filesystem, memory, cron, message, registry

- **capabilities** — `xbot/capabilities/`

- **crew** — `xbot/crew/`

- **interfaces** — `xbot/interfaces/`
  cli, gateway, webui

- **bridge** — `bridge/src/`

- **frontend** — `xbot/interfaces/webui/frontend/src`
