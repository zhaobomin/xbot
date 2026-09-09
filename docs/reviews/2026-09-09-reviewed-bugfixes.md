# 已确认问题的修复与验证

实施分支：`codex/fix-reviewed-bugs`，基于 `0e46fbdfb`。修复在隔离工作区完成验证后合并至主干；本次代码集成不包含 Gateway 部署。

## 问题清单

以下是前轮从当前执行路径验证的 8 项问题，以及自动整理关联的 1 项持久化问题。优先级 P1 表示可能误删数据或错误执行任务，P2 表示功能失效或状态不一致。

| 编号 | 优先级 | 真实问题 | 本次修复 | 主要回归证据 |
| --- | --- | --- | --- | --- |
| 1 | P1 | 连续撤回使用过期索引，可能误删别的消息 | 历史 GET 返回 ETag；REST/WS 撤回携带版本；文件锁内检查内存与磁盘版本；前端串行撤回并重取历史，保留隐藏消息的原始索引 | `test_revoke_requires_revision_and_rejects_stale_index`、`test_revoke_detects_another_store_writer`；前端历史缩短/隐藏行用例 |
| 2 | P1 | Cron CRUD 会取消或重复执行在途任务 | 定时唤醒和执行任务解耦；同任务占用去重；执行快照和配置代次控制完成写回 | `test_crud_and_later_deadline_do_not_cancel_or_block_callback`、`test_completion_preserves_changes_and_callback_snapshot` |
| 3 | P2 | 断线发送丢草稿、等待状态无法结束 | WebSocket.send 返回提交结果；仅成功时清草稿并设置 waiting；断开后结束 waiting，提示检查执行结果；连接幂等 | 前端真实发送方法和输入回调回归 |
| 4 | P2 | 上传接口缺失，附件只作为 Markdown 文本传递 | 实现受控本地上传、拥有者校验、签名预览、可选 S3 镜像；结构化 attachment_ids 解析为本地 media 传给运行时 | `test_upload_is_forwarded_as_real_image_to_runtime` 检查真实 PNG 最终成为 SDK image block；上传边界和 S3 Stubber |
| 5 | P2 | 配置保存后失效的缓存 key 与读取 key 不同 | 网关绑定 HTTP 客户端及失效 key；工作区保存结果先写准确缓存；编辑器按网关/文件区分，保护保存期间的新输入 | 前端 QueryClient 回归；TypeScript 与生产构建 |
| 6 | P2 | 慢 Cron 阻塞后来到期的其他任务 | 不再在唤醒路径等待全部回调；默认最多 8 项并发；容量释放后重新唤醒；停机取消并等待在途任务 | 容量、未来到期、停机和保存失败回归 |
| 7 | P2 | Crew task.timeout 配置没有生效 | 初次/redo 共用单任务 deadline；超时关闭流并有界停止指定 session；外部取消继续传播；None 保持无显式 deadline | `test_07_explicit_timeout_is_enforced_through_orchestrator`、`test_external_crew_cancellation_closes_stream_and_only_stops_exact_session` |
| 8 | P2 | 自动记忆整理的 sync/async 配置没有实际触发入口 | 成功回复后触发；worker 等 idle 后后台调度；每会话去重，sync 等待仅约束该会话；reset/shutdown 取消并等待 | direct/worker、off/sync/async、取消与错误回归 |
| 9 | P2 | last_consolidated 只更新内存，重启后进度丢失 | 自动及手动整理两处标记 metadata dirty 后保存 | `test_08_archive_offset_survives_restart_when_trigger_is_restored`、`test_force_archive_offset_survives_restart` |

## 修复复审中进一步处理的缺陷

- P1：外部修改不相关 Cron 配置时，不能抹除未变更的一次性任务代次，否则完成后可能再次执行。新增外部修改回归。
- P1：撤回前取消并等待后台整理；过期缓存不能复活已删除会话；运行中的会话删除返回 409，删除清理期间禁止新消息进入。
- P2：JSON 上传内容与元数据曾使用同名文件；元数据改为独立 `.meta` 文件，验证字节往返一致。
- P2：附件引用与未发送附件清理互斥，防止刚准备引用就被清理；超过 8 个附件前端拦截并保留草稿。
- P2：附件上传批次固定网关和认证客户端；切换网关后禁止提交其他网关的附件，保留草稿供切回或移除附件。
- P2：取消 direct 的同步整理等待时释放进度回调；其他会话的 worker 入队不等待当前会话归档。

## 行为变化及残余边界

1. REST 撤回需要历史 GET 返回的 ETag，通过 `If-Match` 提交；WS revoke 需要 `revision`。缺少版本返回 428（REST），过期返回 409；旧外部客户端需要更新。当前前端已同步改动。
2. 聊天运行中不能撤回或删除会话，先停止或等待完成。已发出帧后断线无法判断工具是否执行，因此不自动重发，也不承诺工具副作用可回滚。
3. Cron 最大并发默认 8；同任务运行中再次手动触发会拒绝，force 只绕过 enabled 条件。配置变更不会取消已经发出的工具操作。这里保证的是当前服务进程内不重复调度，不是跨进程或崩溃后的 exactly-once。
4. Crew deadline 后可能增加有限清理时间；已经完成的外部操作不能撤销。真实 SDK 子进程及在线模型未做现场端到端试验，测试使用真实运行时路径配合 Fake SDK 验证停止调用与生成器退出。
5. 自动整理失败不改变已完成聊天回复。归档内存文件与会话进度并非跨文件事务，进程崩溃仍可能重复归档；没有宣称 exactly-once。
6. 附件单文件最多 20MB，每条消息最多 8 个；预览签名有效 24 小时。未引用文件超过 24 小时在后续上传触发的清理中删除；已引用文件保留，会持续占用本地磁盘。开启 S3 时仍保留本地副本供运行时读取，S3 是镜像。预览链接属于持有链接即可访问的短期凭证。
7. 新增 boto3 依赖。S3 使用真实客户端序列化与 Stubber 验证请求，未用真实凭据上传；未在用户当前虚拟环境安装依赖或重启 Gateway。

## 本轮验证

合并前将以下后端范围合并为一次运行：**1354 passed、2 skipped**（41.17 秒）；前端 6 项回归与生产构建再次通过。

- 核心修复及现有 Gateway/Cron/AgentService/Session/Crew process 测试：247 passed、1 skipped。
- 扩展 Crew、Memory、Runtime、Session、Cron chaos/property 测试：1128 passed、1 skipped。与上一组有少量重叠，不能相加作为独立用例数量。
- Gateway 最终细节调整后：11 passed。
- 前端 Node 回归：6 passed；直接执行真实 TS/TSX 方法和回调，覆盖 WebSocket、草稿保留、附件上限、历史缩短与网关切换。不是浏览器交互端到端测试。
- 最终 `npm run build`（含 TypeScript）通过，仓库跟踪的 dist 已重建。
- `git diff --check` 通过。受影响已有 Python 文件 Ruff 基线 30 项、当前 29 项，无新增检查项；新附件模块 Ruff 通过。没有把遗留 lint 问题描述成全绿。
- 主工作区保持干净。此次没有重新运行全仓库约 20 分钟的完整套件，也不宣称全项目不存在其他 bug。

复跑时在本修复 worktree 使用 `PYTHONPATH="$PWD" .venv/bin/python -m pytest ...`，避免 editable install 导入主工作区代码。S3 测试需要安装 pyproject 中的 boto3；本次隔离安装在 `/tmp/xbot-reviewed-s3-deps`，运行时将该目录加入 PYTHONPATH。

新增测试文件：`tests/test_reviewed_gateway_fixes.py`、`tests/test_reviewed_cron_regressions.py`、`tests/test_reviewed_runtime_regressions.py`、`xbot/interfaces/webui/frontend/tests/reviewed-fixes.test.mjs`。
