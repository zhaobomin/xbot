# xbot 升级回归审查报告：v2.0.14 → v2.0.29

**日期**：2026-07-09
**审查范围**：`git diff v2.0.14..HEAD`（115 个 Python 文件变更，+6659/−3084 行）+ 工作区未提交改动 `xbot/runtime/core/service.py`
**审查方式**：5 个并行子代理分模块 diff 审查 + 主代理对 P0/P1 逐项实证复核（含 pydantic 实测复现）
**结论**：升级整体质量可控（旧审查报告的 3 个 P0/P1 已被 v2.0.25/27 修复），但**新引入 1 个 P0 安全漏洞 + 3 个 P1 回归 + 多个 P2 健壮性问题**。

---

## 一、P0 — 致命安全漏洞（必须修）

### Bug #1 `/api/config/raw` 明文泄露所有密钥
- **文件**：`xbot/interfaces/gateway/app.py:1263-1273`（端点）、`:321-324`（`_json_default`）
- **实证复现**（项目 venv，pydantic v2）：
  ```python
  json.dumps(C(k="supersecret").model_dump(by_alias=True), default=_json_default)
  # -> {"k": "supersecret"}   ← 明文！
  ```
- **原因**：`model_dump()` 返回 `SecretStr` 包装对象，json 无法原生序列化 → 触发 `default=_json_default` → `get_secret_value()` 还原明文。其它配置端点都做了 `_sanitize_public_config`/`_mask_secret`，唯独此端点漏了脱敏。
- **影响**：任何已认证调用方 `GET /api/config/raw` 即可拿到 anthropic / custom provider 的 `api_key`、MCP token 等全部明文密钥。
- **修复建议**：`get_raw_config` 下发的 `container.config` 先过一遍 `_sanitize_public_config`（与 `providers()` 等端点一致），或 `model_dump(mode="json")` 后再脱敏，绝不直接对含 `SecretStr` 的对象用 `default=get_secret_value`。

---

## 二、P1 — 功能 / 数据回归（建议尽快修）

### Bug #2 旧 `providers.custom` 配置在迁移中静默丢失
- **文件**：`xbot/platform/config/loader.py:399`（`_migrate_provider_fields`）、`xbot/platform/config/schema.py:222-229`（`ProvidersConfig.custom`）
- **代码**：
  ```python
  # loader.py
  fixed_provider_keys = {"anthropic", "custom", "customProviders", "custom_providers"}
  for raw_name in list(providers.keys()):
      if raw_name in fixed_provider_keys:
          continue   # "custom" 被跳过，留在顶层 providers.custom
  # schema.py
  @property
  def custom(self) -> ProviderConfig:
      return self.custom_providers.setdefault("custom", ProviderConfig())  # 读 custom_providers，非顶层
  ```
- **原因**：迁移函数把 `"custom"` 列入 `fixed_provider_keys` 而**不**移入 `customProviders`；`ProvidersConfig.custom` 改为读 `custom_providers` 的 property。由于 pydantic 默认 `extra="ignore"`，顶层孤儿键 `custom` 在 `model_validate` 时被丢弃；`pc.custom.api_key == ''`、`api_base is None`。
- **影响**：使用 `providers.custom`（OpenAI 兼容网关）的用户升级后 api_key/api_base 全部丢失，无法鉴权连接。**数据丢失型回归**。
- **修复建议**：将 `fixed_provider_keys` 改为 `{"anthropic", "customProviders", "custom_providers"}`，使旧 `providers.custom` 被并入 `customProviders.custom`（或迁移中特判 `"custom"` 显式归入）。

### Bug #3 `ModelManager` 供应商名归一化与迁移不一致（camelCase 供应商失效）
- **文件**：`xbot/runtime/core/context/model_manager.py:82,91,151`、`xbot/platform/config/loader.py:355`（`_provider_name_to_snake`）
- **代码**：
  ```python
  # model_manager.py
  provider_attr = self._provider_name.replace("-", "_")   # "aliyunCodingPlan" 不变
  provider_config = getattr(self._config.providers, provider_attr, None)
  # loader.py 写入键
  config_provider_name = _provider_name_to_snake(provider_name)  # "aliyunCodingPlan" -> "aliyun_coding_plan"
  providers["customProviders"][config_provider_name]["models"] = available_models
  ```
- **原因**：`ModelManager` 用 `.replace("-","_")` 取 camelCase 键（如 `aliyunCodingPlan`），而 loader 迁移把模型列表写到 snake_case 键（`aliyun_coding_plan`）。二者对含大写字母的供应商名不一致 → `_get_provider_models()` 返回 `[]` → `available_models` 回退硬编码 `["claude-sonnet-4-5"]`，`_get_base_url` 也取错。
- **影响**：`$model` 命令、模型切换、`available_models` 与 `current_model` 一致性对 `aliyunCodingPlan` 这类 camelCase 供应商全部失效。
- **修复建议**：`ModelManager` 与 loader 统一使用同一归一化函数（都改用 `_provider_name_to_snake` 或都 `replace("-","_")`）。

### Bug #4 `import_workspace` 上传无大小上限（Zip Bomb DoS）
- **文件**：`xbot/interfaces/gateway/app.py:1357`
- **代码**：`data = await file.read()` —— `UploadFile` 未设 `max_size`，整包读入内存；随后 `zf.extractall(...)` 解压到 `workspace_parent/.workspace-import-*`。
- **说明**：zip 成员路径已用 `_validate_workspace_zip_member` 做了路径穿越校验（这部分是对的），但**整体大小 / 条目数 / 解压后体积无限**。
- **影响**：攻击者上传高压缩比 zip 可耗尽内存/磁盘，造成 DoS（需认证，危害相对受限但仍为真实风险）。
- **修复建议**：`UploadFile` 加 `max_size` 或先流式读入带长度上限的 `SpooledTemporaryFile`；`extractall` 前校验总解压体积与条目数（`zf.infolist()` 累加 `file_size`）。

---

## 三、P2 — 健壮性问题（建议修）

| # | 模块 | 位置 | 问题 | 复核 |
|---|------|------|------|------|
| 5 | runtime/client_pool | `client_pool.py:193-217` | `disconnect()` 失败时 `except` 分支 `return True`（v2.0.14 返回 `False`），`prune_idle`/`disconnect_all` 成功计数被高估，调用方无法感知真实失败 | 主代理复核确认 |
| 6 | channels/feishu | `feishu.py:215-227` | `_kill_stale_ws_workers` 用 `pgrep -f "spawn_main.*feishu"` 后仅排除 `os.getpid()`，**误杀同机其它 xbot 实例**的 WS worker，导致对方消息投递中断 | 主代理复核确认 |
| 7 | tools/registry | `registry.py:26` | `register` 对重名工具 `raise ValueError`（v2.0.14 静默覆盖）；MCP 重连若未先 `unregister` 即再次 `register` 同名 wrapper，会抛异常中止重连 | 子代理报告，代码已确认存在 |
| 8 | runtime/core/service（未提交改动） | `service.py:1513` | `_resolve_cli_path` 候选 `home/.local/share/claude/versions/latest` 是**目录**，`p.is_file()` 恒 False，此候选永不命中（死代码，不影响功能）。`cli_path=` 本身对 SDK 0.2.113 合法 | 主代理复核确认（非 bug，仅无效候选） |
| 9 | runtime/session | `conversation_store.py:502` | `list_sessions` 逐行 `json.loads`，任一行损坏且无 `try/except` → 外层 `except: continue` 跳过整个文件，整会话从列表消失（v2.0.14 仅解析首行 metadata 不受影响；与 `_load()` 已加容错不一致） | 子代理报告 |
| 10 | runtime/system/heartbeat | `heartbeat/service.py:100-103` | `configure_callbacks` 仅判断 `_UNSET`、**未排除 `None`**；显式 `configure_callbacks(on_execute=None)` 会把既有回调置空，后续 tick 调 `self.on_execute(...)` 抛 `TypeError`（`llm_call` 有正确 `is not None` 保护，此处不一致） | 子代理报告 |
| 11 | tools/web | `web.py:121` | `_validate_and_pin_url` 仅钉 `resolved[0]`（首个解析 IP）；多 A 记录域名若首 IP 不可达，`PinnedAsyncHTTPTransport` 直接失败，搜索报错（钉 IP 防护的可靠性回归） | 子代理报告 |
| 12 | channels/feishu | `feishu.py:563` | `_largest_fitting_prefix` 单字符即超限时返回 `0`（v2.0.14 返回 `len(text)`），退化配置下消息正文被静默丢弃 | 子代理报告 |

---

## 四、已确认修复 / 无回归（放心项）

- **旧审查 Bug#1/#2（machine.py 内存泄漏、`resolve_sdk_session_id` 查错字典）**：当前 `_set_sdk_session_id_impl` 已先 `pop(old)`，`resolve_sdk_session_id` 用 `session_key` 取 state 后返回 `state.sdk_session_id`，**已修复**。
- **旧审查 Bug#3（conversation_store `compact()` 未重置 `_metadata_dirty`）**：`:473` 已加 `session._metadata_dirty = False`，**已修复**。
- **SSRF 守卫**（`security/network.py` `validate_resolved_url`）：现对解析异常/非 http/https/缺失 hostname 失败关闭（v2.0.14 放行），属加固，未被绕过。
- **消息总线权限 / 超时清理**（`bus/queue.py`）：新增 `has_waiters` 检测与三处字典清理，正确。
- **cron 时区**（v2.0.25 修复）：`tz_name = schedule.tz or default_tz` 等逻辑正确，无回归。
- **exec 安全**（`tools/shell.py`/`filesystem.py`）：超时 `asyncio.wait_for` + `process.kill()`、路径穿越 `restrict_to_workspace` 严格，无命令注入/穿越回归。
- **登录 / session 统一**（v2.0.18 网关拆分）：认证仅 `gateway/auth.py` 一份实现（JWT + bcrypt + 限流），无 open-redirect；写操作均经 `_ensure_writable_client_session`，拆分后行为一致。

> 注：`runtime/system/monitoring/health.py` 的 FastAPI `readiness` 缺少 aiohttp 版对 dict 型 agent 状态的归一化——经核实当前所有 `update_status("agent", ...)` 传的都是字符串（`"running"`/`"initializing"`），**该不一致目前不会被触发**，降为低优先级防御性改进，不计入活跃 bug。

---

## 五、修复优先级建议

1. **P0 Bug#1**：`get_raw_config` 加脱敏（最高优先级，密钥泄露）。
2. **P1 Bug#2 / #3**：修复 `providers.custom` 迁移丢失 + 供应商名归一化统一（影响真实用户连通性）。
3. **P1 Bug#4**：`import_workspace` 加大小/体积上限。
4. **P2**：按上表逐条处理，#5/#6 较易触发可优先。

> 本报告仅供审查，未做任何代码修改。
