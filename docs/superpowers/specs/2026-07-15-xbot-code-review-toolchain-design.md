# xbot 代码审查工具链设计

**日期**: 2026-07-15(初稿) / 2026-07-15(spec review iteration 2 修订)
**项目**: xbot (v2.0.39, ~41k 行 Python + TS bridge/frontend)
**状态**: 已确认设计,待实施

**修订记录**: 经 spec-document-reviewer 审查(iteration 1),修复 1 blocker + 9 major + 6 minor。
主要修订: codegraph 污点传播改为调用可达性、统一 dedup 规则、定义 category enum + 稳定签名 key、
agent→Finding 输出契约、gen_regression 模板机制、confidence/severity 赋值表、错误处理与 preflight、
黄金 fixture 重建方法、confirm/refute 动态黄金用例、module_map 补全、三轨共享 common.py。

---

## 1. 目标与决策

设计一个可重复执行的审查工具链,每次发版或大改动后能一键跑出全量 bug 报告(静态分析 + 动态验证 + 并行 agent 分区),产出结构化报告,不触碰代码。

四项核心决策:

1. **交付物形态**: 可重复执行的审查工具链(Codex skill + 可执行扫描器脚本)
2. **覆盖范围**: Python 核心(`xbot/`) + TypeScript(`bridge/src` + `frontend/src`)
3. **分析深度**: 静态分析 + 动态测试验证 + 安全/并发专项(最全深度)
4. **修复策略**: 默认仅报告,不自动修代码;可选 `--fix-confirmed` 开关用于低风险已坐实 bug

### `--fix-confirmed` 开关契约(仅在显式传参时生效)

**eligible(可自动修)类别 allowlist**: `dead_code`(删除未使用 import/死函数)、`naming_remnants`(重命名历史遗留)。这两类 verdict=confirmed 且 confidence=high 时,可自动修,修法是确定性删除/重命名,零语义风险。

**ineligible(不可自动修)类别**: 所有其他类别(含 verdict=confirmed 的真 bug)。真 bug 的修复涉及语义判断,不属于"低风险"范畴,只能人审后手工修。

修法机制:eligible 类别的 finding 生成补丁 → 跑相关单测验证不破坏 → 输出"已自动修复"清单。任何一步失败则回滚,降级为报告项。

默认行为(不传 `--fix-confirmed`)仍是仅报告。

**eligible 条件可达性说明**: dead_code 和 naming_remnants 属于无模板 category(5.3 表中"不生成测试"),不经过动态验证,故无动态 verdict。confidence_updater(5.5)对此类 category 设有**静态确认规则**: 无模板且默认 confidence=high → verdict=confirmed(verify_note="static-confirmed", 静态确认路径)。因此 `--fix-confirmed` 的 eligible 条件对这两类等价于 `confidence=high`(经静态确认),是可达的,而非依赖不可达的动态 confirmed。

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
                                                    agent 并行深潜(追加语义 finding,按 emitter 契约格式化)
```

关键设计原则: 扫描器只产出 finding(不修代码),skill 只编排(不直接改代码),动态验证只坐实/证伪(生成的测试是临时的,不进主测试套件除非人审后决定)。"仅报告"语义在架构层强制。

---

## 3. 扫描器层 — Python 轨

扫描器放 `scripts/review/py/`,每个扫描器是独立 `.py` 脚本,输入是文件路径或目录,输出是 finding 列表(JSON)。可单独跑、可组合。

### 文件清单

```
scripts/review/
├── common.py                   # Finding 数据类 + Category enum + 共享工具(三轨唯一来源,import 它)
├── py/                         # Python 扫描器轨
│   ├── __init__.py
│   ├── runner.py               # 组合所有扫描器,统一入口
│   ├── lint_ruff.py            # ruff 封装,把 ruff 输出转 finding
│   ├── scan_async_blocks.py    # async 函数内同步调用检测
│   ├── scan_private_api.py     # 标准库私有 API 访问检测
│   ├── scan_fail_open.py       # 权限/白名单 fail-open 模式
│   ├── scan_dead_code.py       # 死代码/未使用 import/残留文件
│   ├── scan_task_lifecycle.py  # 后台任务 GC 风险(ensure_future 无引用)
│   ├── scan_ssrf.py            # SSRF / 请求目标未校验(浅层模式匹配)
│   ├── scan_retry_jitter.py    # 重试无 jitter / 固定间隔
│   ├── scan_mutable_defaults.py # 可变默认参数
│   ├── scan_codegraph_reachability.py  # 复用 codegraph.db 做调用可达性/引用分析
│   └── scan_naming_remnants.py # 残留命名(Nanobot 等历史遗留)
├── ...                         # ts/ security/ verify/ 见下文
```

### 共享 Finding 契约(common.py)

三轨所有扫描器、agent emitter、verify 层共用 `scripts/review/common.py` 的数据类。**不允许各轨自定义 Finding 类型**,统一 import common.py。

```python
@dataclass
class Finding:
    id: str               # 扫描器名 + 内容哈希,如 "async_block:a3f2"
    sig_key: str          # 稳定签名 key(见 6.3),独立于 id 和行号
    severity: str        # P0 / P1 / P2(赋值表见 3.4)
    file: str             # 相对仓库根
    line: int
    category: str        # 必须是 Category enum 成员的 value(见 3.2)
    title: str            # 一句话标题
    detail: str           # 具体描述 + 代码片段
    suggestion: str       # 建议修法
    confidence: str       # high / medium / low(赋值表见 3.4)
    scanner: str          # 产出该 finding 的扫描器/agent 名
```

动态验证后追加字段: `verdict`(`confirmed` / `refuted` / `inconclusive`)、`verify_note`(验证说明)。
基线 diff 后追加字段: `diff_status`(`new` / `recurring` / `fixed` / `regression`)。

### Category enum(冻结词汇表)

去重、diff、报告分类全部依赖 category 字符串完全匹配。三轨扫描器和 agent 必须从以下 enum 取 value:

```python
class Category(str, Enum):
    ASYNC_BLOCK = "async_block"
    ASYNC_RACE = "async_race"
    DEADLOCK = "deadlock"
    PRIVATE_API = "private_api"
    FAIL_OPEN = "fail_open"
    DEAD_CODE = "dead_code"
    TASK_LIFECYCLE = "task_lifecycle"
    SSRF = "ssrf"
    RETRY_JITTER = "retry_jitter"
    MUTABLE_DEFAULTS = "mutable_defaults"
    NAMING_REMNANTS = "naming_remnants"
    AUTH_BYPASS = "auth_bypass"
    INJECTION = "injection"
    SECRETS = "secrets"
    CONSOLE_LOG = "console_log"
    RECONNECT_RACE = "reconnect_race"
    ANY_TYPE = "any_type"
    UNHANDLED_PROMISE = "unhandled_promise"
    UNUSED_EXPORTS = "unused_exports"
    FRONTEND_A11Y = "frontend_a11y"
    CODEGRAPH_REACHABILITY = "codegraph_reachability"
    TOOLCHAIN_ERROR = "toolchain_error"
```

去重规则要求 `(file, line, category)` 三元组精确匹配才合并。跨轨重叠(如 py/scan_async_blocks 与 security/scan_event_loop_block)必须用相同 category value 才会被去重——见 3.3 和 4.2 的"跨轨 category 映射"。

### 跨轨 category 映射

有意的语义重叠通过共享 category 归一:
- py/scan_async_blocks 与 security/scan_event_loop_block 均产出 `async_block`(它们检测同一反模式的不同侧面:前者扫同步网络调用,后者扫同步 IO,但归一为同一 category)。
- py/scan_ssrf 与 security/scan_ssrf 均产出 `ssrf`。

这样去重按 `(file, line, "ssrf")` 即可合并跨轨同位 finding。

### 扫描器分类

**模式扫描器**(纯 AST/grep,误报率低): `scan_async_blocks`、`scan_private_api`、`scan_fail_open`、`scan_dead_code`、`scan_task_lifecycle`、`scan_mutable_defaults`。用 `ast` 模块遍历,识别已知反模式。例如 `scan_async_blocks` 找 `async def` 体内未 `await` 的同步网络调用;`scan_task_lifecycle` 找 `asyncio.ensure_future` / `create_task` 调用未赋值给变量的情况。这些扫描器对结构化模式误报率低,默认 confidence 起点见 3.4。

**浅层语义扫描器**(模式匹配,强制 low/medium confidence): `scan_ssrf`(py 轨)、`scan_retry_jitter`、`scan_naming_remnants`、`scan_async_race`、`scan_deadlock`、`scan_event_loop_block`(security 轨)、`scan_codegraph_reachability`。

这些扫描器**不声称零误报**,而是做浅层模式匹配 + 诚实标注:
- `scan_ssrf`(py 轨):匹配"用户输入参数流到 httpx/aiohttp 请求 URL 字面量"的浅层模式(参数名出现在 URL 表达式中),不做完整过程内数据流。confidence 默认 low。
- `scan_async_race`、`scan_deadlock`、`scan_event_loop_block`:匹配已知反模式(共享 dict 无锁读写、锁获取顺序、async 内 time.sleep/requests.get)。**不**做并发模型推理。confidence 默认 low。
- `scan_codegraph_reachability`:**不是污点传播**。codegraph.db 只有调用/包含/导入边(`contains/calls/imports/instantiates/references/extends`),无数据流边。此扫描器做"调用可达性":从已知 sink(如 httpx.get)反向找可达的函数,标注"该函数可到达网络 sink",作为 SSRF/注入深潜的线索而非定论。confidence 默认 low。

### scan_codegraph_reachability 的诚实定位与 codegraph 新鲜度

`.codegraph/codegraph.db` 是静态快照(当前为 2026-06-17 生成)。审查工具链**不依赖它做数据流/污点分析**,只用于:
1. 反向调用可达性(从 sink 找入口函数,作为 agent 深潜线索)
2. 死函数检测(无入边的函数,但需交叉验证:可能被反射/动态调用)
3. 被引用但已删除的模块检测

**新鲜度要求**:编排层 preflight 检查 codegraph.db 的生成时间(查 `.codegraph/` 下元数据或文件 mtime)。若与 HEAD commit 日期相差超过 2 周或无对应元数据,则跳过 `scan_codegraph_reachability`(标注 `toolchain_error: codegraph stale`),其余扫描器继续。不自动重新生成 codegraph(那是 codegraph 工具的职责,不在本工具链范围)。

### confidence 与 severity 赋值表(3.4)

每个 category 有默认 severity 和 confidence 起点。扫描器可基于上下文(如发现真实用户输入流到 sink)升级 confidence,但不能无依据跳级。

| category | 默认 severity | 默认 confidence | 升级条件 |
|----------|------------|---------------|---------|
| async_block | P0 | medium | 函数在请求热路径(被 channel/agent 调用) → high |
| task_lifecycle | P1 | medium | 在网关主循环 → high |
| private_api | P1 | high | — |
| fail_open | P0 | high | — |
| dead_code | P1 | high | — |
| ssrf | P0 | low | 浅层匹配 + 用户输入参数名命中 → medium |
| retry_jitter | P2 | medium | — |
| mutable_defaults | P1 | high | — |
| naming_remnants | P2 | high | — |
| auth_bypass | P0 | low | 路由无任何鉴权装饰器 → medium |
| injection | P0 | low | 拼接含用户输入参数名 → medium |
| secrets | P0 | high | — |
| async_race | P1 | low | — |
| deadlock | P1 | low | — |
| console_log | P2 | high | — |
| reconnect_race | P1 | medium | — |
| any_type | P2 | high | — |
| unhandled_promise | P1 | medium | — |
| unused_exports | P2 | high | — |
| frontend_a11y | P2 | medium | — |
| codegraph_reachability | P2 | low | — |
| toolchain_error | — | — | 扫描器自身失败(见 9) |

动态验证(verdict)可进一步调整:confirmed → confidence 至少 medium(若原 low 则升 medium);refuted → confidence 降 low;inconclusive → 保持。

### 入口

`runner.py` 跑全部扫描器并合并去重,输出 `findings_py.json`。单独跑某个扫描器: `python scripts/review/py/scan_async_blocks.py xbot/runtime/`。

---

## 4. 扫描器层 — TS 轨 + 安全/并发专项轨

### TS 轨

扫 `bridge/src`(WhatsApp bridge, TypeScript + Baileys)和 `frontend/src`(React 19 + Vite)。扫描器放 `scripts/review/ts/`。

```
scripts/review/ts/
├── runner.sh                  # 组合入口
├── lint_eslint.py             # eslint 封装 → finding(frontend 有 eslint,bridge 无)
├── build_tsc.py               # tsc --noEmit 编译检查 → finding
├── scan_console_log.py        # console.log 滥用
├── scan_reconnect_race.py     # 重连定时器竞态
├── scan_any_type.py           # any 类型滥用
├── scan_unhandled_promise.py  # 未 catch 的 Promise rejection
├── scan_unused_exports.py     # 死导出
└── scan_frontend_a11y.py      # 前端 a11y 基础检查(aria/alt 缺失)
```

TS 轨扫描器 import `scripts/review/common.py` 获取 Finding 和 Category(无独立 base.py)。

TS 轨没有自动化测试框架(bridge 无测试,frontend 无 vitest),动态层降级为 `build_tsc.py`(`tsc --noEmit`)编译检查 + `lint_eslint.py`(`eslint src`)。语义问题靠 agent 深潜抓。

`runner.sh` 依次跑扫描器,合并输出 `findings_ts.json`。

### 安全/并发专项轨

横切 Python 和 TS,放 `scripts/review/security/`。

```
scripts/review/security/
├── runner.py
├── scan_auth_bypass.py        # 鉴权绕过: gateway/webui 路由是否校验 session(浅层:查装饰器缺失)
├── scan_ssrf.py                # SSRF: 用户输入到出站请求 URL(浅层模式)
├── scan_injection.py          # 注入: shell command / SQL / template 拼接(浅层:查拼接含用户输入参数名)
├── scan_secrets.py            # 硬编码密钥 / .env 泄露
├── scan_async_race.py         # async 竞态: 共享状态无锁访问(浅层模式,low confidence)
├── scan_deadlock.py           # 死锁: 锁获取顺序(浅层模式,low confidence)
└── scan_event_loop_block.py   # 事件循环阻塞: async 内同步 IO(归一为 async_block category)
```

注意: `scan_codegraph_taint.py` 已移除。codegraph 相关分析归入 py 轨的 `scan_codegraph_reachability`(诚实定位见 3.4),因为 codegraph.db 无数据流边,做不了污点传播。

### 去重规则(统一,跨 4 与 6 节一致)

三轨合并去重规则(单一规则,消除原 4/6 节矛盾):

1. 按 `(file, line, category)` 三元组精确匹配才合并。category 必须是 enum value(3.2),确保跨轨同义 finding 归一。
2. 合并时保留 confidence 最高版本;confidence 相同时保留 severity 更高版本;两者都相同时保留 scanner 名字典序更小的(确定性)。
3. **不再有"保留深度版本"规则**(原 4 节措辞已删)。深度差异通过 confidence/severity 体现,而非单独规则。

---

## 5. 动态验证层

把静态扫描器的"可疑 finding"变成"已坐实的真 bug"或"已证伪的误报"。放 `scripts/review/verify/`。

```
scripts/review/verify/
├── __init__.py
├── runner.py                  # 动态验证编排入口
├── baseline_tests.py          # 跑现有 pytest 基线,记录通过/失败/跳过 + 失败 nodeid 快照
├── coverage_gaps.py           # 覆盖率缺口分析
├── gen_regression.py          # 为可疑 finding 自动生成回归测试(模板机制,见 5.3)
├── run_regression.py          # 跑生成的回归测试,判定坐实/证伪
└── confidence_updater.py      # 根据验证结果更新 finding 的 confidence
```

### verify 层的 import 边界

扫描器层禁止 import xbot 运行时(约束 9.1)。**verify 层不受此约束**:baseline_tests.py 通过 pytest 运行 xbot 测试(自然 import xbot);gen_regression.py 生成的测试需要 import xbot 目标模块来调用函数。verify 层是动态执行层,import 是其工作方式。

### baseline_tests.py

跑 `.venv/bin/python -m pytest -q` 全量(当前 2491 个),记录基线状态。**失败 nodeid 快照**:把所有失败/错误测试的 nodeid(如 `tests/test_agent_service.py::test_xxx`)存入基线 artifact(`findings_baseline.json` 的 `baseline_failures` 字段)。本次运行的失败与基线失败对比:基线已有 → "既有失败";基线无 → "新增失败",可能由本次审查发现的相关变更引入(但工具链不改代码,所以"新增失败"意味着环境/依赖漂移,标注供人查)。

### coverage_gaps.py

跑 `pytest --cov=xbot --cov-report=json` 拿覆盖率。**依赖 pytest-cov**:preflight 检查(见 9.6)是否安装;未安装则跳过覆盖率分析(不影响其他步骤),在报告中标注"覆盖率分析跳过:pytest-cov 未安装"。

对每个 finding 所在的文件/函数查覆盖率。低覆盖 + 高风险 category(如 ssrf、fail_open)的 finding 自动提升优先级,标记"建议补测试"。输出是给 finding 附加"这条路径有没有被现有测试覆盖到"的元数据,不是修覆盖率。

### gen_regression.py(核心)— 模板机制

**生成机制: 模板-per-category,非 LLM,非输入合成**。每个 category 有一个 Jinja2 模板,输入是 finding 的结构化字段(file, line, 函数名, category),输出是可执行 pytest 测试文件。模板是确定性、可审计的,保证两次相同 finding 生成相同测试。

模板契约:

```python
# scripts/review/verify/templates/async_block.py.j2
import asyncio
import pytest
from {{ finding.module_path }} import {{ finding.function_name }}

@pytest.mark.asyncio
async def test_{{ finding.id }}(monkeypatch):
    # 断言正确行为: 函数在超时内完成(不阻塞事件循环)
    # 真 bug(阻塞)→ wait_for 抛 TimeoutError 被捕获并 pytest.fail → 测试失败 → confirmed
    # 干净(不阻塞)→ 函数完成 → 测试通过 → refuted
    try:
        await asyncio.wait_for(
            {{ finding.function_name }}({{ finding.sample_args }}),
            timeout=0.1,
        )
    except asyncio.TimeoutError:
        pytest.fail("event loop blocked by synchronous call")
```

每个 category 的模板:

| category | 模板策略 | 断言 |
|----------|---------|------|
| async_block | 调用目标 async 函数,用 `asyncio.wait_for` 超时检测阻塞 | 超时则坐实(真阻塞) |
|  | (verdict 语义: 阻塞→TimeoutError→测试失败→confirmed;不阻塞→完成→测试通过→refuted) |
| fail_open | 构造非法权限输入(模板从 finding.detail 提取被绕过的权限名) | 非法输入被接受则坐实 |
| ssrf | 用 `httpx.MockTransport` 拦截出站请求,传入内网 URL | 请求未被拦截(泄露)则坐实 |
| task_lifecycle | 触发后台任务,GC 后(`gc.collect()`)断言任务完成 | 任务未完成则坐实 |
| injection | 构造含 shell 元字符的输入,调用目标函数 | 命令被执行则坐实 |
| auth_bypass | 直接请求受保护路由,不传 session | 返回 200 而非 401 则坐实 |
| dead_code | 不生成测试(静态扫描即可坐实) | — |
| 其他无模板 category | 不生成测试,保持 inconclusive | — |

**finding → 模板输入的映射**: gen_regression.py 从 finding 提取:
- `module_path`: 从 finding.file 转换(`xbot/runtime/core/service.py` → `xbot.runtime.core.service`)
- `function_name`: 从 finding.detail 解析(扫描器在 detail 中以 `func: name` 前缀标注目标函数)
- `sample_args`: 模板默认用 `None`/空值;若扫描器在 detail 中标注了参数构造(`args: ...`),则用之

扫描器**必须在 finding.detail 中标注 `func:` 和可选 `args:`**,否则 gen_regression 跳过该 finding(标 inconclusive,note: "无法提取目标函数")。

生成的测试放 `tests/review_temp/`(gitignored),文件名带 finding id,如 `test_async_block_a3f2.py`。

### run_regression.py

跑 `tests/review_temp/` 下的测试,按结果更新 finding:

- 测试失败(符合预期:如超时、非法被接受) → bug 坐实,confidence 至少 medium,verdict=confirmed,severity 保持或上调
- 测试通过(不符合预期) → 证伪或当前不可触发,confidence 降 low,verdict=refuted,标注"动态未复现"
- 测试报错(import 失败等) → 无法判定,verdict=inconclusive,保持原 confidence,标注"验证失败"

### confidence_updater.py

把动态验证结果回写 `findings.json`,更新每个 finding 的 `confidence`、`verdict`、`verify_note`。编排层最终报告只把 `confirmed` 和 `inconclusive` 列入正式 bug 清单,`refuted` 归入"已排除误报"附录。

边界: 动态验证层不修代码,不删 finding,只更新 verdict/confidence/verify_note。坐实/证伪判定全部基于测试运行结果,不做推理猜测。

**静态确认规则(无模板 category)**: dead_code 等"不生成测试"的 category(5.3 表)不经过动态验证。confidence_updater 对这类 category 设: 若默认 confidence=high → verdict=confirmed(verify_note="static-confirmed", 静态确认路径);否则 verdict=inconclusive。这使 `--fix-confirmed` 的 eligible allowlist(dead_code、naming_remnants)可达。

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

1. **preflight**: 检查依赖(ruff/pytest/tsc/eslint/pytest-cov 是否可用)、codegraph.db 新鲜度、`.venv` 存在性。缺失项按 9.6 处理。加载上次基线 `findings_baseline.json`(首次运行则空基线)。
2. **三轨扫描并行启动**: Python 轨、TS 轨、安全/并发专项轨同时跑各自的 `runner`。产出 `findings_py.json` / `findings_ts.json` / `findings_security.json`。
3. **合并去重**: 三个 json 汇入统一 `findings_raw.json`,按 4.3 去重规则(单一规则:`(file, line, category)` 精确匹配,保留 confidence 最高版本)。
4. **动态验证**: `findings_raw.json` 喂给 `verify/runner.py`,跑基线测试 + 覆盖率分析 + 生成回归测试 + 运行 + 更新 verdict,产出 `findings_verified.json`。
5. **agent 并行深潜**: 按 `module_map.md` 划分的模块边界分派并行 agent,每个 agent 审一个模块。agent 按 emitter 契约(6.4)产出 Finding。产出追加到 `findings_verified.json`。agent findings 默认 verdict=inconclusive(动态验证已在 step 4 完成,agent findings 不再跑动态验证)。
6. **基线 diff**: 对比 `findings_verified.json` 与 `findings_baseline.json`,按**稳定签名 key(sig_key,6.3)**而非 id 匹配,标注每条 finding 状态。
7. **报告生成**: 渲染 `docs/reviews/auto/<date>_review.md` + `findings_final.json`;把 `findings_final.json` 存为新基线(含 `baseline_failures` 失败 nodeid 快照)。

### 稳定签名 key(sig_key,6.3)

`Finding.id` 是"扫描器名+内容哈希",dedup 会改变存活 id,行号会因编辑移动,都不能稳定跨 run 匹配同一个 bug。

`sig_key` 定义为: `category + ":" + qualified_symbol + ":" + title_slug`,其中:
- `category`: enum value
- `qualified_symbol`: 目标函数/类的限定名(如 `xbot.runtime.core.service.AgentService.call_for_auxiliary`)。对非符号级 finding(如文件级死代码),用文件路径。
- `title_slug`: title 的归一化 slug(小写、去标点、空格转下划线)

**独立于行号和 id**。baseline diff 按 sig_key 匹配:
- 当前有、基线无 → `new`
- 当前有、基线有且 diff_status 非 fixed → `recurring`
- 当前无、基线有 → `fixed`
- 当前有、且基线 `fixed_history` 命中(4 轮 TTL 内)→ `regression`

行号变化不影响匹配(只要符号和 title 相同)。这解决了"行号移动导致 recurring 被误判为 fixed+new"的问题。

**fixed finding 的基线保留(使 regression 可检测)**: `fixed` 的 finding 按定义在当前代码中不存在,而 `findings_final.json` 由当前 finding 构成,故默认不含 fixed 条目。为使 `regression` 可检测,`findings_final.json` 需**保留 fixed finding 作为历史条目**: 当前轮消失的 finding 以 `diff_status=fixed` + `fixed_at=<本轮日期>` 存入基线的 `fixed_history` 列表,跨基线保留(带 TTL: 保留最近 4 轮的 fixed 条目,更早的清除)。这样下一轮该 sig_key 复现时,基线 `fixed_history` 命中 → `regression`。

### agent→Finding emitter 契约(6.4)

agent 深潜产出自由文本,但必须含一个 JSON 代码块,格式如下,否则被 emitter 校验器丢弃:

```json
[
  {
    "category": "async_block",
    "file": "xbot/runtime/core/service.py",
    "line": 354,
    "severity": "P0",
    "confidence": "medium",
    "title": "session_id 不一致 in multimodal query path",
    "detail": "func: _handle_multimodal\\n...具体描述...",
    "suggestion": "统一 session_id 来源"
  }
]
```

emitter 校验器(`common.py` 的 `validate_agent_finding`):
- `category` 必须是 Category enum value(否则丢弃)
- `file`、`line`、`title`、`detail` 必填(否则丢弃,记录"agent finding 校验失败")
- `severity`/`confidence` 必须在赋值表范围内(否则用该 category 默认值)
- `id`/`sig_key` 由 emitter 自动生成(agent 不提供)
- `scanner` 字段设为 `agent:<module_name>`

agent findings 经校验后与扫描器 findings 一起参与去重(按 4.3 规则)。agent findings 默认 `verdict=inconclusive`(动态验证已在 step 4 完成)。

### 模块划分(module_map.md,补充安全/并发相关文件)

基于 xbot 实际结构,给并行 agent 分区。已补充 reviewer 指出的遗漏文件:

- runtime 核心: `xbot/runtime/core/`(service、command_handlers、client_pool、context、**task_supervisor**、protocol、hooks、types)
- 状态机: `xbot/runtime/state/` + `xbot/runtime/session/`(含 conversation_store)
- 系统服务: `xbot/runtime/system/`(cron、heartbeat、monitoring:alerting/health/trace)
- channels: `xbot/channels/`(11 个渠道 + manager + registry + feishu_content + feishu_ws_worker)
- 平台层: `xbot/platform/`(config、bus:queue/events、security:network、providers、logging、**utils:retry/helpers/evaluator/file_reader**)
- tools: `xbot/tools/`(shell、web、**web_http_transport**(SSRF sink)、filesystem、memory、cron、message、registry、base)
- capabilities: `xbot/capabilities/`(catalog、policy、handoff、tool_adapter)
- crew: `xbot/crew/`(orchestrator、planner 全套、process、state、resource_manager)
- interfaces: `xbot/interfaces/`(cli:commands、gateway:app/auth/services/session_keys、webui:app/auth/services)
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
**preflight 状态**: ruff✓ pytest✓ tsc✓ eslint✓ pytest-cov✗(跳过覆盖率分析) codegraph:stale(跳过 reachability)

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
- **sig_key**: async_block:xbot.runtime.core.service.AgentService._handle_multimodal:session_id_inconsistency
- **confidence**: high (动态验证: test_async_block_a3f2 失败,坐实)
- **verdict**: confirmed
- **详情**: ...
- **建议**: ...

## P1 — 应清理
...

## P2 — 建议改进
...

## 已排除误报(动态证伪)
| finding_id | sig_key | category | 原因 |
...

## 已修复(基线对比)
| sig_key | 原描述 |
...

## 工具链错误(扫描器/依赖失败)
| scanner | 错误 |
...
```

每条 finding 带 `[NEW]` / `[RECURRING]` / `[FIXED]` / `[REGRESSION]` 标签,以及 confidence、verdict 和 sig_key。`refuted` 归入底部附录。工具链错误单列。

### 机读输出(json)

路径: `docs/reviews/auto/<date>_findings.json` + `docs/reviews/auto/findings_baseline.json`(滚动基线,含 `baseline_failures` 失败 nodeid 快照)

`findings.json` 含全部 finding + verdict + diff_status + sig_key,供下次 diff 对比用。

---

## 8. 测试方案

工具链本身也需要被验证可靠。测试方案分三部分: 工具链自测 + 已知 bug 黄金探测集 + 动态验证正确性黄金用例。

### 工具链自测

放 `tests/review/`:

```
tests/review/
├── conftest.py                    # 提供 xbot 仓库 fixture + 临时 findings 比较
├── test_finding_format.py         # Finding 序列化/sig_key 生成/校验正确性
├── test_category_enum.py         # Category enum 完整性 + 跨轨映射
├── test_py_scanners.py            # 每个扫描器用种子样本验证(命中反模式、不误报干净代码)
├── test_ts_scanners.py
├── test_security_scanners.py
├── test_dedup.py                  # 三轨去重逻辑(confidence 高版本存活、跨轨 category 归一)
├── test_baseline_diff.py          # sig_key 匹配 + new/recurring/fixed/regression 判定(行号移动不影响)
├── test_agent_emitter.py          # agent→Finding 校验器(格式错误丢弃、category 非法丢弃)
├── test_gen_regression.py         # 模板生成器输出可执行 + 正确性(见 8.3)
├── test_confidence_updater.py     # verdict 更新逻辑(confirmed→medium+、refuted→low)
└── test_preflight.py             # 依赖检查 + codegraph 新鲜度 + 降级行为
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

### 已知 bug 黄金探测集(8.2)— fixture 重建方法

6 个黄金 bug 是已修复的过去 bug,原始 bug 代码已不在 repo。fixture 重建方法:

| 扫描器 | 黄金 bug | fixture 重建方法 |
|--------|---------|----------------|
| scan_task_lifecycle | Fix-2: ensure_future 无引用 | 最小化复现:从 git 历史(git show <fix commit>)提取修复前代码,裁剪到最小可触发片段 |
| scan_private_api | Fix-4: _waiters 私有访问 | 最小化复现:同上,从 fix commit 反取 |
| scan_fail_open | Fix-8: CapabilityPolicy fail-open | 最小化复现:同上 |
| scan_async_blocks | P0-1: mcp async 阻塞 | 最小化复现:mcp/ 已删,从历史 commit 取 get_tasks() 同步调用片段 |
| scan_ssrf | v2.0.27 SSRF guard | 最小化复现:从 v2.0.27 fix commit 取修复前片段 |
| scan_naming_remnants | Nanobot 命名遗留 | verbatim 快照:从 dingtalk.py 历史 commit 取含 Nanobot 的片段 |

重建方法分两种:
- **最小化复现**(5 个):从 `git show <fix-commit>^:<file>` 取修复前版本,裁剪到只含反模式 + 最小上下文。验证扫描器命中,且裁剪掉的干净代码不被误报。
- **verbatim 快照**(1 个,naming_remnants):直接取历史版本的命名片段。

fixture 放 `tests/review/fixtures/known_bugs/<scanner>_<bug_id>.py`,每个有对应的 `test_known_bug_<bug_id>` 断言扫描器命中。

**剩余扫描器覆盖目标**: 无黄金 bug 的扫描器(全部 TS 扫描器、scan_auth_bypass/scan_injection/scan_secrets/scan_async_race/scan_deadlock)用合成种子样本(构造含反模式的文件)覆盖。覆盖目标:每个扫描器至少 1 个命中用例 + 1 个不误报用例。

### 动态验证正确性黄金用例(8.3)

`test_gen_regression.py` 不只测"生成测试可执行",还测 verdict 正确性。每个有模板的 category 提供两个黄金用例:

| category | confirm 用例(真 bug,生成的测试应失败→confirmed) | refute 用例(误报,生成的测试应通过→refuted) |
|----------|-------------------|--------------------|
| async_block | 构造一个真阻塞的 async 函数 | 构造一个其实有 await 的函数(扫描器误报) |
| fail_open | 构造一个真 fail-open 的权限检查 | 构造一个其实正确拒绝的检查 |
| ssrf | 构造一个真泄露内网 URL 的请求 | 构造一个其实有 SSRF guard 的请求 |
| task_lifecycle | 构造一个真会被 GC 的后台任务 | 构造一个其实被正确持有的任务 |
| injection | 构造一个真执行 shell 元字符的命令 | 构造一个其实有转义的命令 |
| auth_bypass | 构造一个真无鉴权的路由 | 构造一个其实有鉴权的路由 |

每个用例断言: gen_regression 生成测试 → run_regression 执行 → verdict 符合预期(confirmed/refuted)。这是工具链 headline 能力(坐实/证伪)的正确性证明。

---

## 9. 错误处理与实施约束

### 9.1 扫描器只读
所有扫描器不允许 import xbot 运行时模块(避免 import 副作用污染审查环境),只用 `ast` 解析源码或读 `codegraph.db`。**verify 层不受此约束**(见 5.1)。

### 9.2 临时测试隔离
`tests/review_temp/` 和 `tests/review/fixtures/` 不进 pytest 默认收集路径,`pyproject.toml` 的 `testpaths` 保持 `["tests"]` 不变。工具链自测单独跑 `pytest tests/review/`。

### 9.3 不依赖网络
扫描器、动态验证、回归测试生成都不发真实网络请求。SSRF 类验证用 `httpx.MockTransport`。

### 9.4 不依赖运行中的 gateway
动态验证跑的是 pytest 单测,不碰 launchd 管理的 gateway 进程。基线测试用 `.venv/bin/python -m pytest`,和已有验证基线一致。

### 9.5 渐进交付
实施时先交付扫描器层(能独立跑出 finding),再加动态验证层,最后加 skill 编排层。每层交付后可独立验证。

### 9.6 错误处理契约 + preflight

**扫描器失败**: 单个扫描器抛异常 → 不影响其他扫描器。失败被捕获,产出一条 `category=toolchain_error` 的 finding(scanner 字段为失败扫描器名,detail 为异常摘要),该扫描器的其他 finding 标注"部分扫描可能不完整"。run 不中断。

**外部工具缺失**: preflight 检查 ruff/pytest/tsc/eslint/pytest-cov 是否在 PATH 可调用:
- ruff/pytest 缺失 → blocker,中止 run 并报错(核心依赖)
- tsc/eslint 缺失 → 跳过对应 TS 扫描器,报告标注
- pytest-cov 缺失 → 跳过覆盖率分析,报告标注

**codegraph.db 缺失或过期**: 缺失或生成时间与 HEAD 相差 >2 周 → 跳过 scan_codegraph_reachability,产出 toolchain_error finding 标注原因,其余继续。

**生成的回归测试 import 失败**: run_regression.py 捕获 ImportError/ModuleNotFoundError → 该 finding verdict=inconclusive,verify_note="生成测试无法导入目标模块",不影响其他 finding。

**基线文件缺失**: 首次运行无 findings_baseline.json → 所有 finding diff_status=new,正常继续。

### 9.7 依赖声明
工具链运行需要: ruff、pytest、pytest-asyncio(已在 dev deps)、pytest-cov(需新增到 dev deps 或 preflight 检查)、Jinja2(gen_regression 模板,需新增或确认已安装)。在 `scripts/review/pyproject_extras.txt` 或注释中声明,不在主 `pyproject.toml` 强制加(避免污染 xbot 运行时依赖)。

---

## 10. 完整目录结构

```
scripts/review/
├── common.py                   # Finding + Category enum + 共享工具(三轨唯一来源)
├── orchestrate.py              # 编排入口
├── py/                         # Python 扫描器轨
│   ├── __init__.py
│   ├── runner.py
│   ├── lint_ruff.py
│   ├── scan_async_blocks.py
│   ├── scan_private_api.py
│   ├── scan_fail_open.py
│   ├── scan_dead_code.py
│   ├── scan_task_lifecycle.py
│   ├── scan_ssrf.py
│   ├── scan_retry_jitter.py
│   ├── scan_mutable_defaults.py
│   ├── scan_codegraph_reachability.py
│   └── scan_naming_remnants.py
├── ts/                         # TS 扫描器轨(无 base.py,import common.py)
│   ├── runner.sh
│   ├── lint_eslint.py
│   ├── build_tsc.py
│   ├── scan_console_log.py
│   ├── scan_reconnect_race.py
│   ├── scan_any_type.py
│   ├── scan_unhandled_promise.py
│   ├── scan_unused_exports.py
│   └── scan_frontend_a11y.py
├── security/                   # 安全/并发专项轨(无 base.py,无 codegraph_taint)
│   ├── runner.py
│   ├── scan_auth_bypass.py
│   ├── scan_ssrf.py
│   ├── scan_injection.py
│   ├── scan_secrets.py
│   ├── scan_async_race.py
│   ├── scan_deadlock.py
│   └── scan_event_loop_block.py
├── verify/                     # 动态验证层
│   ├── __init__.py
│   ├── runner.py
│   ├── baseline_tests.py
│   ├── coverage_gaps.py
│   ├── gen_regression.py
│   ├── run_regression.py
│   ├── confidence_updater.py
│   └── templates/              # Jinja2 模板(per-category)
│       ├── async_block.py.j2
│       ├── fail_open.py.j2
│       ├── ssrf.py.j2
│       ├── task_lifecycle.py.j2
│       ├── injection.py.j2
│       └── auth_bypass.py.j2

.codex/skills/xbot-review/
├── SKILL.md
├── scripts/
│   └── orchestrate.py
└── references/
    ├── bug_patterns.md
    └── module_map.md

docs/reviews/auto/                # 输出目录
├── <date>_review.md
├── <date>_findings.json
└── findings_baseline.json        # 含 baseline_failures 失败 nodeid 快照

tests/review/                     # 工具链自测
├── conftest.py
├── fixtures/
│   ├── async_block_sample.py
│   ├── private_api_sample.py
│   └── known_bugs/              # 黄金探测集(最小化复现/verbatim 快照)
│       ├── task_lifecycle_fix2.py
│       ├── private_api_fix4.py
│       ├── fail_open_fix8.py
│       ├── async_blocks_p0_1.py
│       ├── ssrf_v2027.py
│       └── naming_remnants_nanobot.py
├── fixtures_dynamic/            # 动态验证黄金用例(confirm/refute)
│   ├── async_block_confirm.py
│   ├── async_block_refute.py
│   ├── fail_open_confirm.py
│   ├── fail_open_refute.py
│   ├── ssrf_confirm.py
│   ├── ssrf_refute.py
│   ├── task_lifecycle_confirm.py
│   ├── task_lifecycle_refute.py
│   ├── injection_confirm.py
│   ├── injection_refute.py
│   ├── auth_bypass_confirm.py
│   └── auth_bypass_refute.py
├── test_finding_format.py
├── test_category_enum.py
├── test_py_scanners.py
├── test_ts_scanners.py
├── test_security_scanners.py
├── test_dedup.py
├── test_baseline_diff.py
├── test_agent_emitter.py
├── test_gen_regression.py
├── test_confidence_updater.py
└── test_preflight.py

tests/review_temp/                # 动态验证临时测试(gitignored)
```

`.gitignore` 追加: `docs/reviews/auto/*_findings.json`、`tests/review_temp/`、中间产物(`findings_raw.json` 等)。基线 json 和最终报告可选提交。
