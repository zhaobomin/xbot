# xbot 全模块 Bug Review 报告（含二次验证）

> 范围：`xbot/` Python 包（151 文件）+ `bridge/src`（5 个 TS 文件）。
> 方法：逐文件 review（只读不改），随后对每个高危发现回到源码 / 实测做二次验证（真 bug / 误报 / 触发条件苛刻）。
> 验证列含义：✅ 已确认真实存在｜⚠️ 存在但触发条件苛刻或有缓解｜❌ 误报/不成立。

---

## 一、Critical（必须立即修）

### C1. WebUI 静态路由任意文件读取（未授权）✅ 已复现确认
- **位置**：`xbot/interfaces/gateway/app.py:786`
- **问题**：`_serve_static(_path: Path = static_path)` 中 `_path` 不在 URL 路径模板（`/{static_file.name}`）里，被 FastAPI 当作 **query 参数**。
- **复核（实测复现）**：用相同模式起了真实 FastAPI 服务，`curl "/favicon.ico?_path=/etc/hosts"` 实际返回了 `/etc/hosts` 内容，**任意文件读取复现成功**。该路由无 auth 依赖。
- **⚠️ 复核更正（重要）**：原报告建议的修复写法 `async def _serve_static(_path=static_path)`（去掉类型注解、用闭包默认值）**同样被攻破**——实测发现只要参数出现在函数签名里且有默认值，FastAPI 就当 query 参数处理，**与有无类型注解无关**。
- **正确修复方向**：`_path` 绝不能出现在签名里。用工厂闭包把路径绑进函数体：
  ```python
  def _make_static_handler(p: Path):
      async def handler() -> FileResponse:
          return FileResponse(p)
      return handler
  app.get(route_path, include_in_schema=False)(_make_static_handler(static_file))
  ```
  已实测验证此写法对 `?_path=` 攻击免疫。

### C2. WhatsApp 重连不清旧 socket 监听器 → 对象/内存泄漏 ✅ 存在（已降级）
- **位置**：`bridge/src/whatsapp.ts:56-175`（`connect()`）
- **问题**：重连时 `this.sock = makeWASocket({...})` 直接覆盖旧实例，未 `ev.removeAllListeners()` 也未 `sock.end()`。旧 socket 的 `ev` 上挂的 3 个监听器闭包引用 `this`，使旧 `sock` 对象无法被 GC，每重连一次泄漏一组监听器+socket 对象。
- **复核修正**：原报告"消息重复投递"的说法过重——旧 socket 的 WS 在连接断开后已 close，正常不会再 emit `messages.upsert`，重复投递大概率不发生。真实问题是**内存/监听器泄漏**（长期运行缓慢增长），故由 critical 降为 major。修复方向不变：重连前 `removeAllListeners` + `end()`。

---

## 二、Major（应尽快修）

### M1. SSRF 防护被 IPv4-mapped IPv6 绕过 ✅ 已确认（实测）
- **位置**：`xbot/platform/security/network.py:11-22`（`_BLOCKED_NETWORKS` + `_is_private`）
- **问题**：黑名单未覆盖 IPv4-mapped IPv6（`::ffff:x.x.x.x`）。`_is_private` 只做网段包含判断，`::ffff:127.0.0.1` 不属于任何被禁 v4 网段。
- **验证**：用项目 venv 实测 `::ffff:127.0.0.1`、`::ffff:10.0.0.1`、`::ffff:192.168.1.1`、`::ffff:169.254.169.254`（云元数据）全部 `blocked=False`；`getaddrinfo` 对该字面量原样返回。`http://[::ffff:127.0.0.1]/` 可完整绕过。
- **修复方向**：`_is_private` 里加 `if isinstance(addr, IPv6Address) and addr.ipv4_mapped: return _is_private(addr.ipv4_mapped)`。

### M2. 会话消息删除（revoke）不落盘，重启后复活 ✅ 已确认
- **位置**：`xbot/interfaces/gateway/app.py:903-905` 及 WS revoke（1848-1850）+ `xbot/runtime/session/conversation_store.py:367-399`
- **问题**：`del session.messages[index]` 后调 `save(session)`，但 `save()` 只在 `_metadata_dirty=True` 或 `_new_messages` 非空时才写盘。直接删列表元素两者都不触发 → 落进 399 行 "nothing to persist"。删除不落盘，重启后被删消息复活。且删除 `last_consolidated` 之前的消息后未回退该偏移，`get_history()` 切片错位。
- **验证**：读 `save()` 全部分支确认无其他落盘路径。
- **修复方向**：删除后调 `session.mark_metadata_dirty()`，并按需回退 `last_consolidated`。

### M3. 技能开关接口是纯 stub，完全不生效 ✅ 已确认
- **位置**：`xbot/interfaces/gateway/app.py:1639-1646`
- **问题**：`POST /api/skills/{name}/toggle` 只做鉴权后原样回显 `{"enabled": body.enabled}`，不写配置、不改文件、不动内存。前端技能开关"成功"但什么都没发生，刷新即丢。
- **修复方向**：接入真实的技能启用/禁用持久化逻辑。

### M4. bcrypt 超长密码触发 500 ✅ 已确认（实测，触发需已知用户名）
- **位置**：`xbot/interfaces/gateway/auth.py:33,37` + `app.py:808-812`
- **问题**：`bcrypt.hashpw/checkpw` 对 >72 字节密码抛 `ValueError`，login/change-password 未捕获 → 500。
- **验证**：实测 100 字符、甚至 25 个汉字（75 字节）都触发 `ValueError`。
- **复核修正（触发条件）**：`authenticate` 是 `username != user["username"] or not verify_password(...)`，`or` 短路——用户名不对时 bcrypt 不执行。故触发需先知道正确用户名（默认 `admin`，易得）。未授权 DoS 仍成立，但门槛略高于原报告所述。login 只捕获 `HTTPException`，`ValueError` 会冒泡成 500。
- **修复方向**：入参先 `password.encode()[:72]` 截断，或捕获 ValueError 返回 400。

### M5. split config 加载时全量覆盖同名 provider ✅ 已确认
- **位置**：`xbot/platform/config/loader.py:117-127`
- **问题**：`data["providers"][provider_name] = {"apiKey":..., "apiBase":...}` 直接赋值，不是合并。`config.json` 里该 provider 已配的 `extraHeaders`/`models`/`timeout` 等字段，加载 `providers/default.json` 后全部丢失。
- **修复方向**：改为 `setdefault(...).update(...)` 或深合并。

### M6. `ProvidersConfig.custom` property 读取有写副作用 ✅ 存在（影响已修正）
- **位置**：`xbot/platform/config/schema.py:225`
- **问题**：getter `return self.custom_providers.setdefault("custom", ProviderConfig())`——仅读取 `.custom` 就往字典里插一个空 provider。
- **复核修正（影响）**：原报告称"下游 auto-detect 误当有效项"——实测 `_auto_detect_provider`（loader.py:266-267）会跳过 `apiBase`/`apiKey` 均空的 provider，故不会被 auto-detect 误用。真实影响是：`model_dump()` 序列化会带上这个幽灵空 provider，若随后 `save_config` 持久化，会把用户从未配置的空 provider 写进配置文件，造成配置污染。
- **修复方向**：getter 改为 `self.custom_providers.get("custom") or ProviderConfig()`（不写入）。

### M7. MP3 带 ID3 头识别失败 ✅ 已确认（实测）
- **位置**：`xbot/platform/utils/helpers.py:54-56`（`detect_audio_mime`）
- **问题**：只查 MPEG 帧同步 `0xFF 0xE0+`，不识别 `ID3` 头。真实世界绝大多数 MP3 都带 ID3v2。
- **验证**：实测带 ID3 头的 MP3 返回 `None`（错分为普通文件），裸帧 MP3 正常返回 `audio/mp3`。
- **修复方向**：开头加 `if data[:3] == b"ID3": return "audio/mp3"`（或跳过 ID3 块再查帧同步）。

### M8. WhatsApp 非 Boom 错误 + 多设备互踢 → 无限重连 ✅ 已确认
- **位置**：`bridge/src/whatsapp.ts:96-97`
- **问题**：`(lastDisconnect?.error as Boom)?.output?.statusCode`——error 非 Boom 时 `statusCode=undefined`，`undefined !== loggedOut(401)` 为 true → `shouldReconnect=true`。包括 `connectionReplaced(440)`（另一设备登录同账号）也会无限重连互踢。
- **验证**：读源码确认逻辑链。
- **修复方向**：显式排除 `loggedOut`/`connectionReplaced`/`badSession`，非 Boom 错误按不可重连或退避处理。

### M9. `disconnect()` 后 5 秒自动重连，软断开失效 ✅ 已确认
- **位置**：`bridge/src/whatsapp.ts:249-254` + 95-114
- **问题**：`disconnect()` 调 `sock.end()` 后 `connection.update` 仍 emit 'close'，`statusCode` 非 loggedOut → 走重连分支 5 秒后 `connect()` 重建。任何"软断开"需求被违背（stop() 靠 process.exit 掩盖）。
- **修复方向**：加 `_manualDisconnect` 标志，disconnect 时置位并在 close 分支跳过重连。

### M10. WhatsApp 批量消息处理中一条失败丢整批 ✅ 已确认
- **位置**：`bridge/src/whatsapp.ts:130-174`
- **问题**：`messages.upsert` async handler 内串行 `await this.downloadMedia(...)`：单条大文件下载阻塞后续消息；且 Baileys 的 EventEmitter 不捕获 async listener 的 rejection，handler 内未捕获异常 → unhandledRejection 并中断当批循环，剩余消息丢失。
- **修复方向**：handler 内 try/catch 每条消息，媒体下载并发化或限流。

### M11. `ClientPool.disconnect()` 返回值语义与 docstring 矛盾 ⚠️ 已降级为 minor
- **位置**：`xbot/runtime/core/client_pool.py:220-244`
- **复核修正**：原报告"幽灵连接泄漏"言重。disconnect 失败时会调 `_best_effort_force_disconnect` 兜底（terminate/kill/close），且 `record.state="disconnected"`，底层连接有兜底断开，并非纯泄漏。
- **真实问题**：docstring 说 "False if not found"，但"找到了、已移除并尽力断开、仅 graceful 失败"也返回 `False`，语义矛盾。`prune_idle`/`disconnect_all` 用返回值统计成功数会少计；调用方若按 "False=仍在池中" 处理会误判。由 major 降为 minor。

### M12. 权限关键词 "ok"/"确认" 可能误授权 ⚠️ 存在（有缓解）
- **位置**：`xbot/interaction/response_parser.py:9` + `response_handlers.py:117-138`
- **问题**：ALLOW 关键词含 `ok`/`确认`，pending 高危权限请求期间用户随口回 "ok" 会被解析为 allow 放行危险工具（写文件/执行命令）。
- **缓解**：仅在确实有 pending 请求时生效；权限提示本身在问"允许吗"。真正风险是用户回 "ok" 想表达别的意思。
- **修复方向**：高危操作收紧关键词（必须"允许"/"同意"），或要求引用回复原权限消息。

### M13. bus 跨 event loop 用 asyncio.Lock ⚠️ 存在（触发苛刻）
- **位置**：`xbot/platform/bus/queue.py:723-735`（`clear_session_requests`）
- **问题**：非 loop 线程走 `asyncio.run()` 创建新 loop，新 loop 首次 await `self._permission_lock` 会把锁绑到新 loop，之后原 loop 协程再用同一把锁报 "attached to a different loop"。
- **缓解**：已标 deprecated 且在 loop 内调用会直接 raise；需"先同步调用、后原 loop 用锁"的特定时序才触发。
- **修复方向**：删除 sync 版本，统一走 `aclear_session_requests`。

### M14. workspace 导出打包明文 S3 凭据 ⚠️ 待确认
- **位置**：`xbot/interfaces/gateway/app.py:1434-1445` + 1427-1431
- **问题**：export 仅排除 `.webui/backups` 和 `.workspace-import-*`，`.webui/s3.json`（明文 `secret_access_key`）会被打进 zip。管理员导出即得到未加密凭据包。
- **修复方向**：export 排除 `.webui/s3.json` 等含密钥文件。

### M15. memory 消息 timestamp=None 导致 consolidate 异常逃逸 ⚠️ 待确认（触发面窄）
- **位置**：`xbot/memory/store.py:179` + `reme.py:364`
- **问题**：`message.get('timestamp', '?')[:16]`——显式 `"timestamp": None` 时 `None[:16]` 抛 TypeError；`_raw_archive` 再次调用又抛 → 异常逃出 consolidate，该批消息既未归档也未标记失败。
- **缓解**：正常 `add_message` 路径不会产生 None timestamp，仅外部直接传 dict 触发。

---

## 三、Minor（择期修，共性问题归类）

**安全/注入类**
- `platform/config/paths.py:21` `get_media_dir(channel)` 未 sanitize channel，含 `..`/`/` 可逃逸 media 目录。
- `crew/planner/utils.py:134` `parse_string_list` 文本 fallback 用 `line.split(":")[0]`，URL/时间会被误当条目名。
- `crew/process.py:709` manager plan 里 LLM 幻觉出的假任务名被静默跳过，无告警。

**可用性/正确性**
- `config/loader.py:75-77` config.json 解析失败静默回退默认配置，用户无感（可能用错 provider）。
- `config/loader.py:308` `save_config` 写文件非原子，崩溃留半截 JSON + 上面的静默回退 → 配置无声损坏。
- `logging/core.py:41-49` handler 挂到 root logger，污染三方库日志输出。
- `state/machine.py:103-104` 非法状态转换静默 `return False`，无日志，排障难。
- `channels/telegram.py:490` `draft_id = 毫秒 % 2^31`，同毫秒内多消息 draft_id 碰撞互相覆盖草稿。
- `tools` shell/filesystem/web 路径与 SSRF 防护均到位，未发现实质问题。

**bridge TS 协议类**
- `server.ts:74-83` send 命令无请求 id，并发时 'sent'/'error' 无法配对（协议可靠性缺陷）。
- `server.ts:76` `as SendCommand` 断言后不校验字段，malformed 命令报错难懂。
- `server.ts:129` `stop()` 无强制退出兜底，某客户端不响应 close 帧时进程不退。

---

## 四、修复优先级建议（复核后更新）

| 优先级 | 项 | 理由 |
|---|---|---|
| P0 | C1 静态路由文件读（已复现）、M1 SSRF 绕过（已复现） | 可直接利用的安全漏洞，均已实测复现 |
| P1 | C2 WhatsApp 监听器/对象泄漏、M2 删除不落盘、M4 bcrypt 500、M3 技能开关 stub、M8/M9/M10 WhatsApp 重连系列 | 功能错误 / 内存泄漏 / 易被触发 |
| P2 | M5 配置覆盖、M7 MP3 识别、M6 property 副作用（配置污染） | 数据正确性，特定场景触发 |
| P3 | M11 返回值语义、M12 关键词、M13 跨 loop、M14/M15 及其余 minor | 择期清理 |

## 五、二次复核结论（本轮新增）

对每个高危发现做了"能跑代码就不推演"的复核，结论如下：

- **实测复现**：C1（起真实 FastAPI 服务 curl 攻击成功）、M1（全链路 SSRF 校验放行 v4-mapped v6）。
- **严重度修正**：
  - C2 由 critical → major（旧 WS 已 close，"消息重复投递"大概率不发生，真实问题是内存/监听器泄漏）。
  - M11 由 major → minor（有 force-disconnect 兜底，非纯泄漏；真实问题是返回值语义与 docstring 矛盾）。
  - M4 维持 major，但触发需已知用户名（`or` 短路），门槛略高于原述。
  - M6 维持 major 偏 minor，影响从"auto-detect 误用"修正为"save 时配置污染"（auto-detect 会跳过空 provider）。
- **修复建议修正**：C1 的修复方案必须完全不把 `_path` 放进函数签名（实测带默认值的闭包参数同样被攻破），须用工厂闭包绑定路径。
- **复核后仍确认成立的 major**：M2、M3、M5、M7、M8、M9、M10 均重读源码确认触发链完整。

---

## 五、验证方式说明

- **实测执行**：SSRF（C 级 M1）、MP3 ID3（M7）、bcrypt 超长（M4）均用项目 venv 真实跑代码确认行为。
- **源码推演**：其余项逐行读相关代码路径，确认触发链完整（如 C1 的 FastAPI 参数解析规则、M2 的 save() 落盘分支、C2 的监听器挂载点）。
- 标 ⚠️/待验证 的项建议在修复前先补一个复现测试。
