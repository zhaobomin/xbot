# xbot 代码审查工具链设计

**日期**: 2026-07-15
**项目**: xbot (v2.0.39, ~41k 行 Python + TS bridge/frontend)
**状态**: 已确认设计,待实施

---

## 1. 目标与决策

设计一个可重复执行的审查工具链,每次发版或大改动后能一键跑出全量 bug 报告(静态分析 + 动态验证 + 并行 agent 分区),产出结构化报告,不触碰代码。

四项核心决策:

1. **交付物形态**: 可重复执行的审查工具链(Codex skill + 可执行扫描器脚本)
2. **覆盖范围**: Python 核心(`xbot/`) + TypeScript(`bridge/src` + `frontend/src`)
3. **分析深度**: 静态分析 + 动态测试验证 + 安全/并发专项(最全深度)
4. **修复策略**: 默认仅报告,不自动修代码;可选 `--fix-confirmed` 开关用于低风险已坐实 bug

---

## 2. 整体架构

工具链分两层三轨:

- **编排层(Codex skill)**: 入口,负责初始化基线、调度三轨、汇总报告。不直接扫代码,只协调。
- **三轨扫描器**: Python 轨扫 `xbot/` 包; TS 轨扫 `bridge/src` + `frontend/src`; 安全/并发专项轨横切两套代码。三轨可并行。
- **动态验证层**: 对静态发现的可疑点跑现有 pytest + 自动生成针对性回归测试坐实/证伪。
- **基线 diff**: 存上次 `findings.json` 作基线,本次产出对比,标注新增/消失/回归。
- **输出**: 人读 markdown + 机读 json,放 `docs/reviews/auto/`。

数据流:

```
三轨扫描器 → findings_raw.json(合并去重) → 动态验证 → findings_verified.json → 基线 diff → 报告 + findings_final.json(存为新基线)
                                                          ↑
                                                    agent 并行深潜(追加语义 finding)
```

关键设计原则: 扫描器只产出 finding(不修代码),skill 只编排(不直接改代码),动态验证只坐实/证伪(生成的测试是临时的,不进主测试套件除非人审后决定)。"仅报告"语义在架构层强制。

---

## 3. 扫描器层 — Python 轨

扫描器放 `scripts/review/py/`,每个扫描器是独立 `.py` 脚本,输入是文件路径或目录,输出是 finding 列表(JSON)。可单独跑、可组合。

### 文件清单

```
scripts/review/py/
├── __init__.py
├── base.py                     # Finding 数据类 + 扫描器基类
├── runner.py                   # 组合所有扫描器,统一入口
├── lint_ruff.py                # ruff 封装,把 ruff 输出转 finding
├── scan_async_blocks.py        # async 函数内同步调用检测
├── scan_private_api.py         # 标准库私有 API 访问检测
├── scan_fail_open.py           # 权限/白名单 fail-open 模式
├── scan_dead_code.py           # 死代码/未使用 import/残留文件
├── scan_task_lifecycle.py      # 后台任务 GC 风险(ensure_future 无引用)
├── scan_ssrf.py                # SSRF / 请求目标未校验
├── scan_retry_jitter.py        # 重试无 jitter / 固定间隔
├── scan_mutable_defaults.py    # 可变默认参数
├── scan_codegraph_refs.py      # 复用 codegraph.db 做调用链/引用分析
└── scan_naming_remnants.py     # 残留命名(Nanobot 等历史遗留)
```

### Finding 数据类

所有扫描器(三轨共用)的产出统一为以下格式,由 `py/base.py` 约束:

```python
@dataclass
class Finding:
    id: str               # 扫描器名 + 哈希,如 "async_block:a3f2"
    severity: str         # P0 / P1 / P2
    file: str             # 相对仓库根
    line: int
    category: str         # async / dead_code / ssrf / ...
    title: str            # 一句话标题
    detail: str           # 具体描述 + 代码片段
    suggestion: str       # 建议修法
    confidence: str       # high / medium / low
```

动态验证后追加字段: `verdict`(`confirmed` / `refuted` / `inconclusive`)。

### 扫描器分类

**模式扫描器**(纯 AST/grep,零误报目标): `scan_async_blocks`、`scan_private_api`、`scan_fail_open`、`scan_dead_code`、`scan_task_lifecycle`、`scan_mutable_defaults`。用 `ast` 模块遍历,识别已知反模式。例如 `scan_async_blocks` 找 `async def` 体内未 `await` 的同步网络调用;`scan_task_lifecycle` 找 `asyncio.ensure_future` / `create_task` 调用未赋值给变量的情况。

**语义扫描器**(需要上下文,可能有误报,`confidence` 标记): `scan_ssrf`、`scan_retry_jitter`、`scan_codegraph_refs`、`scan_naming_remnants`。`scan_ssrf` 追踪用户输入到 `httpx`/`aiohttp` 请求 URL 的路径;`scan_codegraph_refs` 查 `codegraph.db` 找"被引用但已删除的模块""死函数(无入边)"等。

每个扫描器针对的 bug 类别来自 xbot 前几轮 review 实际抓到的真实 bug 模式: `scan_task_lifecycle` 对应 Fix-2(后台任务 GC),`scan_private_api` 对应 Fix-4(`_waiters` 私有访问),`scan_fail_open` 对应 Fix-8(CapabilityPolicy fail-open)。

### 入口

`runner.py` 跑全部扫描器并合并去重,输出 `findings_py.json`。单独跑某个扫描器: `python scripts/review/py/scan_async_blocks.py xbot/runtime/`。

---

## 4. 扫描器层 — TS 轨 + 安全/并发专项轨

### TS 轨

扫 `bridge/src`(WhatsApp bridge, TypeScript + Baileys)和 `frontend/src`(React 19 + Vite)。扫描器放 `scripts/review/ts/`。

```
scripts/review/ts/
├── runner.sh                  # 组合入口
├── base.py                    # 复用 py/base.py 的 Finding 格式
├── lint_eslint.py             # eslint 封装 → finding(frontend 有 eslint,bridge 无)
├── build_tsc.py               # tsc --noEmit 编译检查 → finding
├── scan_console_log.py        # console.log 滥用
├── scan_reconnect_race.py     # 重连定时器竞态
├── scan_any_type.py           # any 类型滥用
├── scan_unhandled_promise.py  # 未 catch 的 Promise rejection
├── scan_unused_exports.py     # 死导出
└── scan_frontend_a11y.py      # 前端 a11y 基础检查(aria/alt 缺失)
```

TS 轨没有自动化测试框架(bridge 无测试,frontend 无 vitest),动态层降级为 `build_tsc.py`(`tsc --noEmit`)编译检查 + `lint_eslint.py`(`eslint src`)。语义问题靠 agent 深潜抓。

`runner.sh` 依次跑扫描器,合并输出 `findings_ts.json`,Finding 格式与 Python 轨完全一致(`base.py` 共享)。

### 安全/并发专项轨

横切 Python 和 TS,放 `scripts/review/security/`。

```
scripts/review/security/
├── runner.py
├── base.py                    # 复用 Finding 格式
├── scan_auth_bypass.py        # 鉴权绕过: gateway/webui 路由是否校验 session
├── scan_ssrf.py                # SSRF: 用户输入到出站请求 URL
├── scan_injection.py          # 注入: shell command / SQL / template 拼接
├── scan_secrets.py            # 硬编码密钥 / .env 泄露
├── scan_async_race.py         # async 竞态: 共享状态无锁访问
├── scan_deadlock.py           # 死锁: 锁获取顺序 / await 链推理
├── scan_event_loop_block.py   # 事件循环阻塞: async 内同步 IO
└── scan_codegraph_taint.py    # 复用 codegraph.db 做污点传播
```

专项轨与 Python 轨有少量有意重叠(如 `scan_ssrf`)。Python 轨的 `scan_ssrf` 是快速模式扫描,专项轨的 `scan_ssrf` + `scan_codegraph_taint` 做深度调用链追踪。`runner.py` 在汇总时用 `(file, line, category)` 三元组去重,保留深度版本。

安全专项参考前几轮真实发现: SSRF guard 是 v2.0.27 review 修复项,`scan_auth_bypass` 对应 gateway session 校验,`scan_event_loop_block` 对应 P0-1(mcp async 阻塞)。并发专项覆盖 Fix-2(后台任务 GC)和 Fix-4(私有 API 锁访问)同源的竞态类问题。

所有扫描器产出统一 Finding 格式,汇入 `findings_security.json`。

---

## 5. 动态验证层

把静态扫描器的"可疑 finding"变成"已坐实的真 bug"或"已证伪的误报"。放 `scripts/review/verify/`。

```
scripts/review/verify/
├── __init__.py
├── runner.py                  # 动态验证编排入口
├── baseline_tests.py          # 跑现有 pytest 基线,记录通过/失败/跳过
├── coverage_gaps.py           # 覆盖率缺口分析
├── gen_regression.py          # 为可疑 finding 自动生成回归测试
├── run_regression.py          # 跑生成的回归测试,判定坐实/证伪
└── confidence_updater.py      # 根据验证结果更新 finding 的 confidence
```

### baseline_tests.py

跑 `.venv/bin/python -m pytest -q` 全量(当前 2491 个),记录基线状态。已存在的失败标记为"既有失败",不与本次审查的发现混淆。

### coverage_gaps.py

跑 `pytest --cov=xbot --cov-report=json` 拿覆盖率,对每个 finding 所在的文件/函数查覆盖率。低覆盖 + 高风险 category(如 ssrf、fail_open)的 finding 自动提升优先级,标记"建议补测试"。输出是给 finding 附加"这条路径有没有被现有测试覆盖到"的元数据,不是修覆盖率。

### gen_regression.py(核心)

对 confidence 为 medium/low 的 finding,根据 category 生成针对性回归测试:

- `async_block` 类: 调用该 async 函数,断言不阻塞事件循环(`asyncio.wait_for` 超时检测)
- `fail_open` 类: 构造非法权限输入,断言被拒绝
- `ssrf` 类: 传入内网 URL,断言被拦截
- `task_lifecycle` 类: 触发后台任务,GC 后断言任务完成
- `dead_code` 类: 不生成测试(死代码靠静态扫描即可坐实)

生成的测试放 `tests/review_temp/`(gitignored),文件名带 finding id,如 `test_async_block_a3f2.py`。

### run_regression.py

跑 `tests/review_temp/` 下的测试,按结果更新 finding:

- 测试失败(符合预期) → bug 坐实,confidence → high,severity 保持或上调
- 测试通过(不符合预期) → 证伪或当前不可触发,confidence → low,标注"动态未复现"
- 测试报错 → 无法判定,保持原 confidence,标注"验证失败"

### confidence_updater.py

把动态验证结果回写 `findings.json`,更新每个 finding 的 `confidence` 和新增 `verdict` 字段。编排层最终报告只把 `confirmed` 和 `inconclusive` 列入正式 bug 清单,`refuted` 归入"已排除误报"附录。

边界: 动态验证层不修代码,不删 finding,只更新 verdict 和 confidence。坐实/证伪判定全部基于测试运行结果,不做推理猜测。

---

## 6. 编排层(Codex skill + 基线 diff)

以 Codex skill 形式实现,放 `.codex/skills/xbot-review/`。

```
.codex/skills/xbot-review/
├── SKILL.md                   # skill 入口,描述触发条件和执行流程
├── scripts/
│   └── orchestrate.py         # skill 触发入口,调 scripts/review/orchestrate.py
└── references/
    ├── bug_patterns.md        # xbot 历史 bug 模式库(给 agent 深潜参考)
    └── module_map.md         # xbot 模块边界划分(给并行 agent 分区)
```

### 触发

用户说"review xbot"、"审查代码"、"跑 bug 扫描"时触发。也支持显式 `python scripts/review/orchestrate.py` 直接跑。

### 编排流程

1. **初始化**: 确认仓库根、确认 `.venv` 存在、加载上次基线 `findings_baseline.json`(首次运行则空基线)
2. **三轨扫描并行启动**: Python 轨、TS 轨、安全/并发专项轨同时跑各自的 `runner`。TS 轨的 eslint/tsc 是外部进程,Python 扫描器是进程内调用。产出 `findings_py.json` / `findings_ts.json` / `findings_security.json`
3. **合并去重**: 三个 json 汇入统一 `findings_raw.json`,按 `(file, line, category)` 去重,同位 finding 保留最高 confidence 版本
4. **动态验证**: `findings_raw.json` 喂给 `verify/runner.py`,跑基线测试 + 覆盖率分析 + 生成回归测试 + 运行 + 更新 verdict,产出 `findings_verified.json`
5. **agent 并行深潜**: 按 `module_map.md` 划分的模块边界分派并行 agent,每个 agent 审一个模块。agent 拿到该模块代码 + 已有静态/动态 finding 作线索,负责找静态扫描器漏掉的语义 bug。产出追加到 `findings_verified.json`
6. **基线 diff**: 对比 `findings_verified.json` 与 `findings_baseline.json`,标注每条 finding 状态: `new`(基线无) / `recurring`(基线有,未修) / `fixed`(基线有,本次消失) / `regression`(基线已修,本次复现)
7. **报告生成**: 渲染 `docs/reviews/auto/<date>_review.md` + `findings_final.json`;把 `findings_final.json` 存为新基线

### 模块划分(module_map.md)

基于 xbot 实际结构,给并行 agent 分区:

- runtime 核心: `xbot/runtime/core/`(service、command_handlers、client_pool、context)
- 状态机: `xbot/runtime/state/` + `xbot/runtime/session/`
- 系统服务: `xbot/runtime/system/`(cron、heartbeat、monitoring)
- channels: `xbot/channels/`(11 个渠道 + manager + registry)
- 平台层: `xbot/platform/`(config、bus、security、providers、logging)
- tools: `xbot/tools/`(shell、web、filesystem、memory、cron、message)
- capabilities: `xbot/capabilities/`
- crew: `xbot/crew/`(多 agent 编排 + planner)
- interfaces: `xbot/interfaces/`(cli、gateway、webui)
- bridge: `bridge/src/`(TS)
- frontend: `xbot/interfaces/webui/frontend/src`(TS)

### bug_patterns.md

从前几轮 review 提炼的真实 bug 模式库,给 agent 当 checklist: async 阻塞、私有 API 访问、fail-open 权限、后台任务 GC、SSRF 未校验、重试无 jitter、可变默认参数、死代码/残留文件、命名遗留(Nanobot)、session_id 不一致、property 副作用。agent 照着逐条查自己负责的模块。

---

## 7. 输出格式

### 报告(markdown,人读)

路径: `docs/reviews/auto/<date>_review.md`

结构:

```markdown
# xbot 自动审查报告 — 2026-07-15

**基线版本**: v2.0.39 (a528e17d)
**基线对比**: 上次审查 2026-07-09
**扫描范围**: xbot/(151 文件) + bridge/src + frontend/src

## 摘要

| 严重程度 | 新增 | 复现 | 已修复 | 未修 |
|---------|------|------|--------|------|
| P0      | 1    | 0    | 2      | 0    |
| P1      | 3    | 1    | 4      | 2    |
| P2      | 5    | 0    | 8      | 1    |

## P0 — Bug(会导致运行错误)

### [NEW] async 阻塞: xxx
- **文件**: xbot/runtime/core/service.py:354
- **category**: async_block
- **confidence**: high (动态验证: test_async_block_a3f2 失败,坐实)
- **详情**: ...
- **建议**: ...

## P1 — 应清理
...

## P2 — 建议改进
...

## 已排除误报(动态证伪)
| finding_id | category | 原因 |
...

## 已修复(基线对比)
| finding_id | 原描述 |
...
```

每条 finding 带 `[NEW]` / `[RECURRING]` / `[FIXED]` / `[REGRESSION]` 标签,以及 confidence 和动态验证结果。`refuted` 归入底部附录。

### 机读输出(json)

路径: `docs/reviews/auto/<date>_findings.json` + `docs/reviews/auto/findings_baseline.json`(滚动基线)

`findings.json` 含全部 finding + verdict + diff 状态,供下次 diff 对比用。

---

## 8. 测试方案

工具链本身也需要被验证可靠。测试方案分两部分: 工具链自测 + 已知 bug 探测验证。

### 工具链自测

放 `tests/review/`:

```
tests/review/
├── conftest.py                    # 提供 xbot 仓库 fixture + 临时 findings 比较
├── test_finding_format.py         # Finding 序列化/去重/合并正确性
├── test_py_scanners.py            # 每个扫描器用构造的反模式样本验证
├── test_ts_scanners.py
├── test_security_scanners.py
├── test_dedup.py                  # 三轨去重逻辑
├── test_baseline_diff.py          # new/recurring/fixed/regression 判定
├── test_gen_regression.py         # 回归测试生成器输出可执行
└── test_confidence_updater.py     # verdict 更新逻辑
```

核心思路: **种子样本**。每个扫描器配一个小型 Python/TS 文件,内含已知反模式 + 干净代码,断言扫描器只命中反模式行、不误报干净代码。

```python
# tests/review/fixtures/async_block_sample.py
async def good():
    await httpx.get(url)          # 干净: 有 await

async def bad():
    httpx.get(url)                # 反模式: 同步调用未 await
    asyncio.sleep(1)              # 反模式: 阻塞 sleep
```

`test_py_scanners.py::test_async_blocks` 断言 `scan_async_blocks` 命中 `bad()` 两行、不命中 `good()`。

### 已知 bug 回归验证(黄金测试集)

用前几轮 review 已坐实的真 bug 当黄金测试集,放 `tests/review/fixtures/known_bugs/`,断言对应扫描器能检出:

| 扫描器 | 黄金 bug 来源 | 断言 |
|--------|-------------|------|
| `scan_task_lifecycle` | Fix-2: `ensure_future` 无引用 | 命中 |
| `scan_private_api` | Fix-4: `_waiters` 私有访问 | 命中 |
| `scan_fail_open` | Fix-8: CapabilityPolicy fail-open | 命中 |
| `scan_async_blocks` | P0-1: mcp async 阻塞 | 命中 |
| `scan_ssrf` | v2.0.27 SSRF guard | 命中 |
| `scan_naming_remnants` | Nanobot 命名遗留 | 命中 |

如果某个扫描器改坏了,黄金测试立刻失败,而不是等到真实审查漏掉 bug 才发现。

---

## 9. 实施约束

1. **扫描器只读**: 所有扫描器不允许 import xbot 运行时模块(避免 import 副作用污染审查环境),只用 `ast` 解析源码或读 `codegraph.db`。
2. **临时测试隔离**: `tests/review_temp/` 和 `tests/review/fixtures/` 不进 pytest 默认收集路径,`pyproject.toml` 的 `testpaths` 保持 `["tests"]` 不变。工具链自测单独跑 `pytest tests/review/`。
3. **不依赖网络**: 扫描器、动态验证、回归测试生成都不发真实网络请求。SSRF 类验证用 mock server 或 `httpx.MockTransport`。
4. **不依赖运行中的 gateway**: 动态验证跑的是 pytest 单测,不碰 launchd 管理的 gateway 进程。基线测试用 `.venv/bin/python -m pytest`,和已有验证基线一致。
5. **渐进交付**: 实施时先交付扫描器层(能独立跑出 finding),再加动态验证层,最后加 skill 编排层。每层交付后可独立验证,不是一锤子工程。

---

## 10. 完整目录结构

```
scripts/review/
├── py/                          # Python 扫描器轨
│   ├── __init__.py
│   ├── base.py                  # Finding 数据类(共享)
│   ├── runner.py                # 组合入口
│   ├── lint_ruff.py
│   ├── scan_async_blocks.py
│   ├── scan_private_api.py
│   ├── scan_fail_open.py
│   ├── scan_dead_code.py
│   ├── scan_task_lifecycle.py
│   ├── scan_ssrf.py
│   ├── scan_retry_jitter.py
│   ├── scan_mutable_defaults.py
│   ├── scan_codegraph_refs.py
│   └── scan_naming_remnants.py
├── ts/                          # TS 扫描器轨
│   ├── base.py                  # 复用 py/base.py
│   ├── runner.sh
│   ├── lint_eslint.py
│   ├── build_tsc.py
│   ├── scan_console_log.py
│   ├── scan_reconnect_race.py
│   ├── scan_any_type.py
│   ├── scan_unhandled_promise.py
│   ├── scan_unused_exports.py
│   └── scan_frontend_a11y.py
├── security/                    # 安全/并发专项轨
│   ├── base.py
│   ├── runner.py
│   ├── scan_auth_bypass.py
│   ├── scan_ssrf.py
│   ├── scan_injection.py
│   ├── scan_secrets.py
│   ├── scan_async_race.py
│   ├── scan_deadlock.py
│   ├── scan_event_loop_block.py
│   └── scan_codegraph_taint.py
├── verify/                      # 动态验证层
│   ├── __init__.py
│   ├── runner.py
│   ├── baseline_tests.py
│   ├── coverage_gaps.py
│   ├── gen_regression.py
│   ├── run_regression.py
│   └── confidence_updater.py
└── orchestrate.py               # 编排入口

.codex/skills/xbot-review/
├── SKILL.md
├── scripts/
│   └── orchestrate.py           # skill 触发入口
└── references/
    ├── bug_patterns.md           # 历史 bug 模式库
    └── module_map.md             # 模块边界划分

docs/reviews/auto/                # 输出目录
├── <date>_review.md
├── <date>_findings.json
└── findings_baseline.json

tests/review/                     # 工具链自测
├── conftest.py
├── fixtures/
│   ├── async_block_sample.py
│   ├── private_api_sample.py
│   └── known_bugs/
├── test_finding_format.py
├── test_py_scanners.py
├── test_ts_scanners.py
├── test_security_scanners.py
├── test_dedup.py
├── test_baseline_diff.py
├── test_gen_regression.py
└── test_confidence_updater.py

tests/review_temp/                # 动态验证临时测试(gitignored)
```

`.gitignore` 追加: `docs/reviews/auto/*_findings.json`、`tests/review_temp/`、中间产物(`findings_raw.json` 等)。基线 json 和最终报告可选提交。
