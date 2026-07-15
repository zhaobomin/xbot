# xbot 代码审查工具链 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable code-review toolchain that runs static scanners (Python + TS + security/concurrency), dynamic verification, and parallel agent deep-dives over the xbot codebase, producing a structured bug report with baseline diffing.

**Architecture:** Two-layer three-track: deterministic scanner scripts (`scripts/review/`) producing structured `Finding` JSON; a Codex skill (`.codex/skills/xbot-review/`) orchestrating parallel module agents, dynamic verification, and baseline diff. Scanners are read-only (AST + codegraph.db), verify layer is exempt (imports xbot for pytest). Output: `docs/reviews/auto/<date>_review.md` + `findings.json`.

**Tech Stack:** Python 3.11, `ast`, `dataclasses`/`enum`, Jinja2 3.1.6 (installed), pytest/pytest-asyncio, ruff 0.15.7, TypeScript tsc, eslint (currently broken — migration issue), SQLite (codegraph.db read-only). Note: `pytest-cov` NOT installed in `.venv` — coverage step will skip unless installed.

**Spec:** `docs/superpowers/specs/2026-07-15-xbot-code-review-toolchain-design.md`

**Scanner detail contract (BLOCKER — all scanners must follow):** Every scanner finding's `detail` field MUST start with `func: <qualified_name>` and optionally `args: <sample_args>`. This is how `gen_regression.py` (Task 9) extracts the target function to call. Without it, the dynamic confirm/refute pipeline is dead on real findings. Example: `detail = "func: xbot.runtime.core.service.AgentService._handle_multimodal\n..."`. Add a test asserting the detail parses `func:` for every scanner.

**Out of scope for this plan:** The `--fix-confirmed` auto-fix application path (spec §1). The static-confirmed rule (prerequisite) IS implemented in Task 10, but the actual patch→test→rollback fix-application is deferred to a follow-up plan. The `--fix-confirmed` CLI flag is accepted but no-ops with a "not yet implemented" note.

---

## Phase 1: Scanner Layer Foundation

### Task 1: shared Finding contract + Category enum + dedup + slug normalization

**Files:**
- Create: `scripts/review/common.py`
- Create: `tests/review/__init__.py`
- Create: `tests/review/conftest.py`
- Create: `tests/review/test_finding_format.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/review/test_finding_format.py
from scripts.review.common import Finding, Category, make_sig_key, slugify, dedup, validate_agent_finding

def test_finding_serializes_to_json():
    f = Finding(id="async_block:a3f2", sig_key="x", severity="P0",
                file="xbot/x.py", line=1, category="async_block",
                title="t", detail="func: foo", suggestion="s",
                confidence="high", scanner="test")
    d = f.to_dict()
    assert d["category"] == "async_block"
    assert Finding.from_dict(d) == f

def test_slugify_normalizes_title():
    assert slugify("Session ID Inconsistency!") == "session_id_inconsistency"
    assert slugify("  Multi  Word  ") == "multi_word"

def test_sig_key_uses_slugified_title():
    k = make_sig_key("async_block", "xbot.x.foo", "Session ID Bug")
    assert k == "async_block:xbot.x.foo:session_id_bug"

def test_category_enum_covers_all_tracks():
    vals = {c.value for c in Category}
    assert "async_block" in vals and "toolchain_error" in vals
    assert "event_loop_block" not in vals

def test_dedup_keeps_highest_confidence():
    a = Finding(id="s1:1", sig_key="k", severity="P0", file="x", line=1,
                category="ssrf", title="t", detail="d", suggestion="s",
                confidence="low", scanner="py")
    b = Finding(id="s2:1", sig_key="k", severity="P0", file="x", line=1,
                category="ssrf", title="t", detail="d2", suggestion="s",
                confidence="high", scanner="security")
    out = dedup([a, b])
    assert len(out) == 1 and out[0].scanner == "security"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/review/test_finding_format.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement common.py**

```python
from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import Enum
import re, hashlib

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

@dataclass
class Finding:
    id: str; sig_key: str; severity: str; file: str; line: int
    category: str; title: str; detail: str; suggestion: str
    confidence: str; scanner: str
    verdict: str = "inconclusive"; verify_note: str = ""; diff_status: str = ""
    def to_dict(self): return asdict(self)
    @staticmethod
    def from_dict(d): return Finding(**d)

def slugify(s):
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", "_", s)

def make_sig_key(category, symbol, title):
    return f"{category}:{symbol}:{slugify(title)}"

def dedup(findings):
    by_key = {}
    for f in findings:
        k = (f.file, f.line, f.category)
        if k not in by_key: by_key[k] = f
        else:
            ex = by_key[k]
            if (f.confidence, f.severity, f.scanner) > (ex.confidence, ex.severity, ex.scanner):
                by_key[k] = f
    return list(by_key.values())

def validate_agent_finding(raw, module_name):
    try:
        cat = raw["category"]
        if cat not in {c.value for c in Category}: return None
        return Finding(
            id=f"agent:{module_name}:{hashlib.md5(str(raw).encode()).hexdigest()[:8]}",
            sig_key=make_sig_key(cat, raw.get("file",""), raw.get("title","")),
            severity=raw.get("severity","P2"), file=raw["file"], line=raw["line"],
            category=cat, title=raw["title"], detail=raw.get("detail",""),
            suggestion=raw.get("suggestion",""), confidence=raw.get("confidence","low"),
            scanner=f"agent:{module_name}", verdict="inconclusive")
    except KeyError: return None
```

- [ ] **Step 4: Run test to verify it passes**
- [ ] **Step 5: Commit**

```bash
git add scripts/review/common.py tests/review/
git commit -m "feat(review): common.py (Finding, Category, dedup, sig_key, agent emitter)"
```

---

### Task 2: scan_async_blocks (Python, AST)

**Files:** `scripts/review/py/__init__.py`, `scripts/review/py/scan_async_blocks.py`, `tests/review/fixtures/async_block_sample.py`, `tests/review/test_py_scanners.py`

- [ ] **Step 1: Seed sample + failing test**

```python
# tests/review/fixtures/async_block_sample.py
import httpx, asyncio
async def good():
    await httpx.get("http://x")
async def bad():
    httpx.get("http://x")
    asyncio.sleep(1)
```
```python
# tests/review/test_py_scanners.py
from scripts.review.py.scan_async_blocks import scan
def test_async_blocks_hits_bad_not_good():
    findings = scan("tests/review/fixtures/async_block_sample.py")
    lines = {f.line for f in findings}
    assert 7 in lines and 8 in lines
    assert 5 not in lines
def test_async_block_detail_has_func_contract():
    findings = scan("tests/review/fixtures/async_block_sample.py")
    assert all(f.detail.startswith("func:") for f in findings)
```

- [ ] **Step 2: Run -> FAIL**
- [ ] **Step 3: Implement** — AST parse, find `async def` bodies, flag sync calls (`httpx.get/post`, `requests.get`, `time.sleep`, `asyncio.sleep`) NOT preceded by `await`. Emit `async_block`, severity medium. Detail MUST start with `func: <function_name>`.
- [ ] **Step 4: Run -> PASS**
- [ ] **Step 5: Commit**

---

### Task 3: Remaining Python pattern scanners (one sub-task each, one commit each)

Each scanner: write seed sample with anti-pattern + clean code → failing test (hits anti-pattern line, misses clean line, detail has `func:`) → implement AST scanner → pass → commit. **All details must start with `func:`.**

- [ ] **scan_private_api** — seed: `event = asyncio.Event(); x = event._waiters` (anti) vs `event.set()` (clean). Flag `getattr(obj,"_...")` and `obj._<name>` access on stdlib asyncio types. confidence=high.
- [ ] **scan_fail_open** — seed: `if name not in known: allowed.append(name)` (anti, no else-reject) vs `if name not in known: raise PermissionError` (clean). confidence=high.
- [ ] **scan_dead_code** — seed: `import unused_module` + `def dead(): pass` (never called) vs `import used; used()`. confidence=high.
- [ ] **scan_task_lifecycle** — seed: `asyncio.ensure_future(coro())` (unassigned, anti) vs `t = asyncio.ensure_future(coro())` (clean). confidence=medium.
- [ ] **scan_mutable_defaults** — seed: `def f(x=[]):` (anti) vs `def f(x=None):` (clean). confidence=high.
- [ ] **scan_naming_remnants** — seed: `class NanobotHandler:` + `"Nanobot Reply"` string (anti) vs `class XbotHandler:` (clean). grep-based. confidence=high.

- [ ] **Final: `.venv/bin/python -m pytest tests/review/test_py_scanners.py -v` -> ALL PASS**

### Task 4: Python semantic scanners + codegraph

- [ ] **scan_ssrf (py, shallow low)** — seed: `def handler(user_url): httpx.get(f"http://api/{user_url}")` (param name in URL expression, anti) vs `httpx.get("http://fixed")` (clean). confidence=low.
- [ ] **scan_retry_jitter** — seed: `for _ in range(3): ...; time.sleep(1)` (fixed sleep in loop, anti) vs `time.sleep(2**attempt + random()` (clean). confidence=medium.
- [ ] **scan_codegraph_reachability** — read `.codegraph/codegraph.db`, reverse call-reach from sinks. Test: missing DB → emit `toolchain_error` finding (not crash). Stale (>2wk) → same. confidence=low.

### Task 5: Python runner + ruff wrapper

**Files:** `scripts/review/py/runner.py`, `scripts/review/py/lint_ruff.py`

- [ ] TDD: `lint_ruff` runs `.venv/bin/ruff check --output-format=json`, converts to Findings. `runner.py` imports `dedup` from `common.py` (NOT py-local), runs all py scanners + ruff + dedup → `findings_py.json`.
- [ ] Commit.

---

### Task 6: TS scanners + runner (6 scanners + lint_eslint + build_tsc)

**Files:** `scripts/review/ts/runner.sh`, `scripts/review/ts/build_tsc.py`, `scripts/review/ts/lint_eslint.py`, 6 `scan_*.py`, `tests/review/test_ts_scanners.py`

Each scanner: `.ts`/`.tsx` seed sample with anti-pattern + clean code → failing test → implement (regex) → pass. Import `scripts/review/common.py`.

- [ ] **scan_console_log** — seed: `console.log("x")` (anti) vs `logger.info("x")` (clean). confidence=high.
- [ ] **scan_reconnect_race** — seed: `setTimeout(reconnect, 1000)` without `clearTimeout` on existing timer (anti) vs `clearTimeout(timer); timer = setTimeout(...)` (clean). confidence=medium.
- [ ] **scan_any_type** — seed: `let x: any` (anti) vs `let x: string` (clean). confidence=high.
- [ ] **scan_unhandled_promise** — seed: `fetch().then(...)` without `.catch()` (anti) vs `fetch().then(...).catch(...)` (clean). confidence=medium.
- [ ] **scan_unused_exports** — seed: `export const unused = 1` (never imported) vs `export const used = 1` (imported elsewhere). confidence=high.
- [ ] **scan_frontend_a11y** — seed: `<img src="x" />` without alt (anti) vs `<img src="x" alt="desc" />` (clean). confidence=medium.
- [ ] **build_tsc.py** — shells `tsc --noEmit` in `bridge/` and `frontend/`, converts errors to Findings.
- [ ] **lint_eslint.py** — shells `eslint src`. Test: eslint broken (current state) → emit `toolchain_error` finding, no crash.
- [ ] **runner.sh** — runs all + merges → `findings_ts.json`.

### Task 7: Security/concurrency scanners (7 scanners)

**Files:** `scripts/review/security/runner.py`, 7 `scan_*.py`, `tests/review/test_security_scanners.py`

All shallow, low confidence. Each: seed sample + failing test + implement + commit.

- [ ] **scan_auth_bypass** — seed: FastAPI route `@app.get("/admin")` without auth dependency (anti) vs `@app.get("/admin", dependencies=[Depends(verify)])` (clean). confidence=low.
- [ ] **scan_ssrf (security)** — seed: `httpx.get(user_input_url)` (anti) vs `httpx.get(allowlisted_url)` (clean). confidence=low. Emits same `ssrf` category as py (dedup normalizes).
- [ ] **scan_injection** — seed: `subprocess.run(f"echo {user_input}")` (anti) vs `subprocess.run(["echo", user_input])` (clean, list form). confidence=low.
- [ ] **scan_secrets** — seed: `API_KEY = "sk-abc123..."` (high-entropy string, anti) vs `API_KEY = os.environ["KEY"]` (clean). confidence=high.
- [ ] **scan_async_race** — seed: shared `dict` read+write in async without lock (anti) vs under `asyncio.Lock()` (clean). confidence=low.
- [ ] **scan_deadlock** — seed: `await lock_a; await lock_b` in one func + `await lock_b; await lock_a` in another (anti, order) vs consistent order (clean). confidence=low.
- [ ] **scan_event_loop_block** — seed: `async def f(): requests.get(url)` (sync IO in async, anti) vs `await httpx.get(url)` (clean). Emits `async_block` category.
- [ ] **runner.py** — merges → `findings_security.json`.

---

## Phase 2: Dynamic Verification Layer

### Task 8: baseline_tests + coverage_gaps + dependency declaration

**Files:** `scripts/review/verify/__init__.py`, `scripts/review/verify/baseline_tests.py`, `scripts/review/verify/coverage_gaps.py`, `scripts/review/pyproject_extras.txt`, `tests/review/test_preflight.py`

- [ ] **Step 1: Test baseline returns pass count + failure nodeids + ignores tests/review**

```python
def test_baseline_ignores_toolchain_self_tests():
    result = run_baseline()
    assert all("tests/review" not in n for n in result.failures)
```

- [ ] **Step 2-5:** TDD `run_baseline()` → `.venv/bin/python -m pytest -q --tb=no --ignore=tests/review`, parse failure nodeids. `coverage_gaps.py`: preflight-check `import pytest_cov`; missing → skip; else run cov + map. `pyproject_extras.txt`: declare `pytest-cov`, `Jinja2` (already installed).
- [ ] Commit.

### Task 9: gen_regression (6 Jinja2 templates + 12 confirm/refute fixtures)

**Files:** `scripts/review/verify/gen_regression.py`, `scripts/review/verify/templates/{async_block,ssrf,fail_open,task_lifecycle,injection,auth_bypass}.py.j2`, `tests/review/fixtures_dynamic/{async_block,ssrf,fail_open,task_lifecycle,injection,auth_bypass}_{confirm,refute}.py`, `tests/review/test_gen_regression.py`

- [ ] **Step 1: Write async_block confirm/refute test**

```python
def test_async_block_confirm_generates_failing_test():
    finding = make_finding("async_block", "tests.review.fixtures_dynamic.async_block_confirm", "blocks_forever")
    test_code = generate_test(finding)
    assert "wait_for" in test_code and "pytest.fail" in test_code
```

- [ ] **Step 2-5:** TDD `generate_test(finding)`: load Jinja2 template by `finding.category`, extract `func:`/`args:` from `finding.detail`, render. Write to `tests/review_temp/test_<id>.py`.

- [ ] **Step 6: All 6 templates + 12 fixtures.** Each confirm fixture = real bug (generated test fails → confirmed). Each refute fixture = false-positive (generated test passes → refuted):

| category | confirm fixture (bug) | refute fixture (clean) |
|----------|----------------------|----------------------|
| async_block | `async def f(): time.sleep(10)` (blocks) | `async def f(): await asyncio.sleep(0.01)` (non-blocking) |
| fail_open | `def check(p): if p not in allowed: allowed.append(p)` (admits) | `def check(p): if p not in allowed: raise PermissionError` (rejects) |
| ssrf | `def fetch(u): httpx.get(u)` (no guard, leaks intranet) | `def fetch(u): assert is_safe(u); httpx.get(u)` (guarded) |
| task_lifecycle | `asyncio.ensure_future(coro())` (GC'd) | `t = asyncio.ensure_future(coro()); await t` (held) |
| injection | `subprocess.run(f"echo {x}")` (shell injection) | `subprocess.run(["echo", x])` (list form, safe) |
| auth_bypass | `@app.get("/admin")` (no auth) | `@app.get("/admin", dependencies=[Depends(auth)])` (authed) |

- [ ] **Step 7: Commit**

### Task 10: run_regression + confidence_updater + verify runner + static-confirm

**Files:** `scripts/review/verify/run_regression.py`, `scripts/review/verify/confidence_updater.py`, `scripts/review/verify/runner.py`, `tests/review/test_confidence_updater.py`

- [ ] **Step 1: Test verdict mapping + static-confirm**

```python
def test_failed_confirms():
    assert update_verdict("failed") == ("confirmed", "medium")
def test_passed_refutes():
    assert update_verdict("passed") == ("refuted", "low")
def test_static_confirm_dead_code_high():
    v, note = static_verdict("dead_code", "high")
    assert v == "confirmed" and note == "static-confirmed"
def test_no_template_low_stays_inconclusive():
    assert static_verdict("naming_remnants", "low")[0] == "inconclusive"
```

- [ ] **Step 2-5:** TDD `run_regression`: run `pytest tests/review_temp/ --tb=line`, parse pass/fail/error → verdict. `confidence_updater`: dynamic verdicts + static rule (no-template + confidence=high → `verdict=confirmed, verify_note="static-confirmed"`; else inconclusive). `verify/runner.py`: baseline→coverage→gen→run→update → `findings_verified.json`.
- [ ] Commit.

---

## Phase 3: Orchestration + Skill + Baseline Diff

### Task 11: baseline diff (apply_diff with fixed_history)

**Files:** `scripts/review/orchestrate.py` (partial: diff), `tests/review/test_baseline_diff.py`

- [ ] **Step 1: Test recurring + regression**

```python
def test_recurring_when_sig_key_in_baseline_findings():
    current = [make_finding(sig_key="k1")]
    baseline = {"findings": [{"sig_key":"k1"}], "fixed_history": []}
    assert apply_diff(current, baseline)[0].diff_status == "recurring"
def test_regression_when_sig_key_in_fixed_history():
    current = [make_finding(sig_key="k2")]
    baseline = {"findings": [], "fixed_history": [{"sig_key":"k2","fixed_at":"2026-07-09"}]}
    assert apply_diff(current, baseline)[0].diff_status == "regression"
def test_fixed_written_to_fixed_history():
    baseline = {"findings": [{"sig_key":"k3"}], "fixed_history": []}
    result = apply_diff([], baseline)
    assert any(e["sig_key"]=="k3" for e in result["new_fixed_history"])
```

- [ ] **Step 2-5:** TDD `apply_diff`: match by sig_key vs baseline findings + fixed_history. Emit new/recurring/fixed/regression. Write fixed → fixed_history TTL=4.
- [ ] Commit.

### Task 12a: orchestrator preflight + merge

**Files:** `scripts/review/orchestrate.py` (preflight + merge), `tests/review/test_preflight.py` (extend)

- [ ] **Step 1: Test preflight skips missing pytest-cov + emits toolchain_error**

```python
def test_preflight_pytest_cov_missing_skips():
    status = preflight()
    assert status["pytest_cov"] is False  # confirmed not installed
def test_preflight_codegraph_stale_emits_toolchain_error():
    # codegraph.db mtime is Jun-17, >2wk old
    status = preflight()
    assert status["codegraph_stale"] is True
```

- [ ] **Step 2-5:** TDD preflight (ruff/pytest/tsc/eslint/pytest-cov/codegraph freshness; missing → toolchain_error finding or skip) + cross-track merge (import `dedup` from `common.py`).

### Task 12b: orchestrator full + report render

**Files:** `scripts/review/orchestrate.py` (full), `tests/review/test_orchestrate_render.py`

- [ ] **Step 1: Test report renders markdown with summary table + sections**

```python
def test_report_has_summary_and_sections():
    findings = [make_confirmed_finding("async_block","P0")]
    report = render_report(findings, baseline={}, version="v2.0.39")
    assert "# xbot 自动审查报告" in report
    assert "## 摘要" in report and "## P0" in report
    assert "[NEW]" in report
```

- [ ] **Step 2-5:** TDD full orchestrate: preflight → 3 tracks parallel → merge+dedup → verify → spawn agents per module_map → validate agent findings → apply_diff → render_report → write `findings_final.json` (with `fixed_history` + `baseline_failures`).
- [ ] Commit.

### Task 13: Codex skill + references + shim

**Files:** `.codex/skills/xbot-review/SKILL.md`, `.codex/skills/xbot-review/scripts/orchestrate.py` (thin shim calling repo-level orchestrate), `references/bug_patterns.md`, `references/module_map.md`

- [ ] **Step 1: Write module_map.md** — 11 boundaries incl `xbot/runtime/core/task_supervisor.py`, `xbot/tools/web_http_transport.py`, `xbot/platform/utils/`, `xbot/tools/registry.py`.
- [ ] **Step 2: Write bug_patterns.md** — 11 historical patterns checklist.
- [ ] **Step 3: Write SKILL.md** — triggers ("review xbot", "审查代码"), flow, output. Note `--fix-confirmed` is accepted but no-op (deferred).
- [ ] **Step 4: Write `scripts/orchestrate.py` shim** — thin wrapper calling `scripts/review/orchestrate.py`.
- [ ] **Step 5: Smoke test** — `.venv/bin/python scripts/review/orchestrate.py` produces report, no crashes.
- [ ] **Step 6: Commit**

### Task 14: golden bug fixtures

**Files:** 6 `tests/review/fixtures/known_bugs/*.py`, extend `tests/review/test_py_scanners.py`

- [ ] **Step 1: Extract pre-fix code and minimize.** Commit hashes + files:

| scanner | fix commit | file | extract method |
|---------|-----------|------|---------------|
| scan_task_lifecycle | `8ba36fbd` (Fix-2) | `xbot/runtime/core/service.py` | `git show 8ba36fbd^:xbot/runtime/core/service.py`, grep for `ensure_future` not assigned |
| scan_private_api | `8ba36fbd` (Fix-4) | `xbot/platform/bus/queue.py` | same, grep for `_waiters` getattr |
| scan_fail_open | `8ba36fbd` (Fix-8) | `xbot/capabilities/policy.py` | same, grep for `has_mcp` fail-open |
| scan_ssrf | `6e2b6396` | `xbot/platform/security/network.py` | `git show 6e2b6396^:...`, grep for unguarded URL fetch |
| scan_naming_remnants | `1e6b0e65` | `xbot/channels/dingtalk.py` | `git show 1e6b0e65^:xbot/channels/dingtalk.py`, grep for `Nanobot` |
| scan_async_blocks | (P0-1, mcp removed) | historical `mcp/todoist_resources.py` | `git show <commit-before-removal>:mcp/todoist_resources.py`, grep for sync `get_tasks()` |

- [ ] **Step 2: Write test asserting scanner hits golden fixture**

```python
def test_scan_task_lifecycle_hits_fix2_golden():
    findings = scan("tests/review/fixtures/known_bugs/task_lifecycle_fix2.py")
    assert findings  # scanner fires on real bug shape
```

- [ ] **Step 3: Run golden tests** → `.venv/bin/python -m pytest tests/review/test_py_scanners.py -k golden -v` → PASS
- [ ] **Step 4: Commit**

### Task 15: gitignore + final integration

- [ ] **Step 1: Add gitignore** — `docs/reviews/auto/*_findings.json`, `tests/review_temp/`, `findings_raw.json`, `findings_verified.json`
- [ ] **Step 2: Full smoke run** — `.venv/bin/python scripts/review/orchestrate.py` → produces `docs/reviews/auto/<date>_review.md` + `findings_final.json`, no crashes (toolchain_error OK for missing deps)
- [ ] **Step 3: Full toolchain test suite** — `.venv/bin/python -m pytest tests/review/ -v` → ALL PASS
- [ ] **Step 4: Commit**

---

## Notes

- TDD throughout: seed samples + golden cases before implementation.
- One commit per scanner in Phase 1.
- verify/ layer may import xbot (exempt from scanner no-import rule).
- Scanners MUST emit `func:` in detail (blocker contract for gen_regression).
- No network/gateway deps: SSRF tests use `httpx.MockTransport`; baseline runs pytest only.
- baseline_tests runs with `--ignore=tests/review` to avoid self-test pollution.
- codegraph freshness: preflight checks; stale → skip with toolchain_error.
- eslint known-broken: lint_eslint emits toolchain_error, not crash.
- pytest-cov NOT installed: coverage step skips unless installed.
- All commands use `.venv/bin/python`.
- `--fix-confirmed` deferred to follow-up plan; static-confirm prerequisite IS in Task 10.
