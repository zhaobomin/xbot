# xbot 代码审查工具链 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable code-review toolchain that runs static scanners (Python + TS + security/concurrency), dynamic verification, and parallel agent deep-dives over the xbot codebase, producing a structured bug report with baseline diffing.

**Architecture:** Two-layer three-track: deterministic scanner scripts (`scripts/review/`) producing structured `Finding` JSON; a Codex skill (`.codex/skills/xbot-review/`) orchestrating parallel module agents, dynamic verification, and baseline diff. Scanners are read-only (AST + codegraph.db), verify layer is exempt (imports xbot for pytest). Output: `docs/reviews/auto/<date>_review.md` + `findings.json`.

**Tech Stack:** Python 3.11, `ast`, `dataclasses`/`enum`, Jinja2 templates, pytest/pytest-asyncio, ruff, TypeScript tsc, eslint, SQLite (codegraph.db read-only).

**Spec:** `docs/superpowers/specs/2026-07-15-xbot-code-review-toolchain-design.md`

---

## Phase 1: Scanner Layer Foundation

### Task 1: shared Finding contract + Category enum

**Files:**
- Create: `scripts/review/common.py`
- Create: `tests/review/__init__.py`
- Create: `tests/review/conftest.py`
- Create: `tests/review/test_finding_format.py`

- [ ] **Step 1: Write failing tests for Finding serialization + sig_key**

```python
from scripts.review.common import Finding, Category, make_sig_key

def test_finding_serializes_to_json():
    f = Finding(id="async_block:a3f2", sig_key="x", severity="P0",
                file="xbot/x.py", line=1, category="async_block",
                title="t", detail="func: foo", suggestion="s",
                confidence="high", scanner="test")
    d = f.to_dict()
    assert d["category"] == "async_block"
    assert Finding.from_dict(d) == f

def test_sig_key_is_stable_across_line_changes():
    base = make_sig_key("async_block", "xbot.x.foo", "session_id_bug")
    assert base == "async_block:xbot.x.foo:session_id_bug"

def test_category_enum_covers_all_tracks():
    vals = {c.value for c in Category}
    assert "async_block" in vals and "toolchain_error" in vals
    assert "event_loop_block" not in vals
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/review/test_finding_format.py -v`
Expected: FAIL (ModuleNotFoundError: scripts.review.common)

- [ ] **Step 3: Implement common.py**

```python
from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import Enum

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
    id: str
    sig_key: str
    severity: str
    file: str
    line: int
    category: str
    title: str
    detail: str
    suggestion: str
    confidence: str
    scanner: str
    verdict: str = "inconclusive"
    verify_note: str = ""
    diff_status: str = ""
    def to_dict(self): return asdict(self)
    @staticmethod
    def from_dict(d): return Finding(**d)

def make_sig_key(category, symbol, title_slug):
    return f"{category}:{symbol}:{title_slug}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/review/test_finding_format.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/review/common.py tests/review/
git commit -m "feat(review): shared Finding contract + Category enum"
```

---

### Task 2: Python scanner base + scan_async_blocks

**Files:**
- Create: `scripts/review/py/__init__.py`
- Create: `scripts/review/py/scan_async_blocks.py`
- Create: `tests/review/fixtures/async_block_sample.py`
- Create: `tests/review/test_py_scanners.py`

- [ ] **Step 1: Write seed sample + failing test**

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/review/test_py_scanners.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement scan_async_blocks (AST-based)**

Parse with `ast`, find `async def` bodies, flag calls to sync-blocking funcs (`httpx.get`, `httpx.post`, `requests.get`, `time.sleep`, `asyncio.sleep`) NOT preceded by `await`. Emit `Category.ASYNC_BLOCK`, severity medium (high if hot-path heuristic matches).

- [ ] **Step 4: Run test to verify it passes**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/review/py/ tests/review/fixtures/ tests/review/test_py_scanners.py
git commit -m "feat(review): scan_async_blocks AST scanner"
```

---

### Task 3: Remaining Python pattern scanners

Each scanner follows Task 2 TDD pattern. One commit per scanner.

- `scan_private_api`: flag `getattr(obj,"_waiters")`, `obj._waiters` on stdlib types. confidence=high.
- `scan_fail_open`: `if name not in known: allowed.append(name)` without else-reject. confidence=high.
- `scan_dead_code`: unused imports, unassigned task results. confidence=high.
- `scan_task_lifecycle`: `ensure_future`/`create_task` not assigned. confidence=medium.
- `scan_mutable_defaults`: `[]`/`{}`/`set()` as defaults. confidence=high.
- `scan_naming_remnants`: grep "Nanobot". confidence=high.

- [ ] **Final step: Run all scanner tests**

Run: `.venv/bin/python -m pytest tests/review/test_py_scanners.py -v`
Expected: ALL PASS

### Task 4: Python semantic scanners + codegraph reachability

- `scan_ssrf` (shallow, low): param names in `httpx.get(f"...{param}...")`. No data-flow.
- `scan_retry_jitter`: `sleep(constant)` in retry loops. confidence=medium.
- `scan_codegraph_reachability`: read `.codegraph/codegraph.db` reverse call-reach. confidence=low. Missing/stale DB -> `toolchain_error` finding, no crash.

### Task 5: Python runner + ruff wrapper + dedup

**Files:** `scripts/review/py/runner.py`, `scripts/review/py/lint_ruff.py`, `tests/review/test_dedup.py`

- [ ] **Step 1: Write dedup test** (keep highest confidence on `(file,line,category)` match)
- [ ] **Step 2-5**: TDD implement `dedup` (single rule), `lint_ruff` (ruff json -> Findings), `runner.py` (all scanners + dedup -> `findings_py.json`)
- [ ] **Step 6: Commit**

---

### Task 6: TS scanners + runner

**Files:** `scripts/review/ts/runner.sh`, `scripts/review/ts/build_tsc.py`, 7 `scan_*.py` scanners, `tests/review/test_ts_scanners.py`

TS pattern scanners use regex on `.ts`/`.tsx`. `build_tsc.py` shells `tsc --noEmit`. `runner.sh` merges -> `findings_ts.json`. All import `scripts/review/common.py`.

- `scan_console_log`: `console.log(` in non-dev. confidence=high.
- `scan_reconnect_race`: setTimeout reconnect without clear. confidence=medium.
- `scan_any_type`: `: any` annotations. confidence=high.
- `scan_unhandled_promise`: `.then()` without `.catch()`. confidence=medium.
- `scan_unused_exports`: exported but not imported anywhere. confidence=high.
- `scan_frontend_a11y`: `<img>` without `alt`, `onClick` without `role`. confidence=medium.

Note: eslint currently broken (migration issue). `runner.sh` must emit `toolchain_error`, not crash.

### Task 7: Security/concurrency scanners + runner

**Files:** `scripts/review/security/runner.py`, 7 `scan_*.py` scanners, `tests/review/test_security_scanners.py`

All shallow, low confidence. `scan_event_loop_block` emits `async_block` category.

- `scan_auth_bypass`: routes missing auth decorators. low.
- `scan_ssrf`: param names in outbound URL. low.
- `scan_injection`: `subprocess`/`shell` with string concat. low.
- `scan_secrets`: high-entropy strings / known patterns. high.
- `scan_async_race`: shared dict no-lock. low.
- `scan_deadlock`: lock order. low.
- `scan_event_loop_block`: async + sync IO. emits `async_block`.

---

## Phase 2: Dynamic Verification Layer

### Task 8: baseline_tests + coverage_gaps

**Files:** `scripts/review/verify/__init__.py`, `scripts/review/verify/baseline_tests.py`, `scripts/review/verify/coverage_gaps.py`, `tests/review/test_preflight.py`

- [ ] **Step 1: Test baseline returns pass count + failure nodeids**
- [ ] **Step 2-5**: TDD `run_baseline()` -> `.venv/bin/python -m pytest -q --tb=no`, parse failure nodeids. `coverage_gaps.py`: preflight-check `pytest-cov`; missing -> skip-marker; else run cov + map file->coverage%.

### Task 9: gen_regression (Jinja2 templates)

**Files:** `scripts/review/verify/gen_regression.py`, 6 `.j2` templates, 12 `fixtures_dynamic/*.py`, `tests/review/test_gen_regression.py`

- [ ] **Step 1: Write confirm/refute golden test** (async_block confirm generates failing test)
- [ ] **Step 2-5**: TDD `generate_test(finding)`: load Jinja2 template by category, render with module_path/function_name/sample_args/id. Write to `tests/review_temp/test_<id>.py`. Templates assert correct behavior (bug -> assertion fails -> confirmed). async_block template: `try/except TimeoutError: pytest.fail(...)`.
- [ ] **Step 6: Add all 6 confirm/refute pairs** (async_block, fail_open, ssrf, task_lifecycle, injection, auth_bypass)
- [ ] **Step 7: Commit**

### Task 10: run_regression + confidence_updater + verify runner

**Files:** `scripts/review/verify/run_regression.py`, `scripts/review/verify/confidence_updater.py`, `scripts/review/verify/runner.py`, `tests/review/test_confidence_updater.py`

- [ ] **Step 1: Write verdict-mapping test** (failed->confirmed, passed->refuted, static-confirm for dead_code)
- [ ] **Step 2-5**: TDD `run_regression`: run `pytest tests/review_temp/`, parse pass/fail/error -> verdict. `confidence_updater`: dynamic verdicts + static rule (no-template + high -> confirmed + verify_note="static-confirmed"). `verify/runner.py`: baseline->coverage->gen->run->update -> `findings_verified.json`.

---

## Phase 3: Orchestration + Skill + Baseline Diff

### Task 11: agent emitter + baseline diff

**Files:** add `validate_agent_finding` to `common.py`, `scripts/review/orchestrate.py` (merge+diff), `tests/review/test_agent_emitter.py`, `tests/review/test_baseline_diff.py`

- [ ] **Step 1: Test invalid category dropped + valid finding normalized** (scanner=`agent:<module>`, verdict=inconclusive)
- [ ] **Step 2-5**: TDD `validate_agent_finding`.
- [ ] **Step 6: Test recurring + regression** (sig_key in baseline findings -> recurring; sig_key in fixed_history -> regression)
- [ ] **Step 7**: TDD `apply_diff`: match by sig_key vs baseline findings + fixed_history. Emit new/recurring/fixed/regression. Write fixed -> fixed_history TTL=4.

### Task 12: orchestrator + report renderer

**Files:** `scripts/review/orchestrate.py` (full), `tests/review/test_preflight.py` (extend)

- [ ] **Step 1: Test preflight skips missing pytest-cov**
- [ ] **Step 2-5**: TDD `orchestrate.py`: preflight (ruff/pytest/tsc/eslint/pytest-cov/codegraph freshness; missing -> toolchain_error or skip) -> 3 tracks parallel -> merge+dedup -> verify -> spawn agents per module_map -> validate agent findings -> apply baseline diff -> render report markdown + findings_final.json (with fixed_history + baseline_failures).

### Task 13: Codex skill + references

**Files:** `.codex/skills/xbot-review/SKILL.md`, `references/bug_patterns.md`, `references/module_map.md`

- [ ] **Step 1: Write module_map.md** (11 boundaries incl task_supervisor, web_http_transport, platform/utils, registry)
- [ ] **Step 2: Write bug_patterns.md** (11 historical patterns)
- [ ] **Step 3: Write SKILL.md** (triggers, flow, output)
- [ ] **Step 4: Smoke test** (`python scripts/review/orchestrate.py`)
- [ ] **Step 5: Commit**

### Task 14: golden bug fixtures

**Files:** 6 `tests/review/fixtures/known_bugs/*.py`, extend `test_py_scanners.py`

- [ ] **Step 1: Extract pre-fix code via `git show <fix-commit>^:<file>`**, minimize to smallest snippet
- [ ] **Step 2: Test scanner hits golden fixture**
- [ ] **Step 3: Run golden tests** -> PASS
- [ ] **Step 4: Commit**

### Task 15: gitignore + final integration

- [ ] **Step 1: Add gitignore** (`docs/reviews/auto/*_findings.json`, `tests/review_temp/`, intermediates)
- [ ] **Step 2: Full smoke run** (`python scripts/review/orchestrate.py` -> produces report, no crashes)
- [ ] **Step 3: Full test suite** (`.venv/bin/python -m pytest tests/review/ -v` -> ALL PASS)
- [ ] **Step 4: Commit**

---

## Notes

- TDD throughout: seed samples + golden cases before implementation.
- One commit per scanner in Phase 1.
- verify/ layer may import xbot (exempt from scanner no-import rule).
- No network/gateway deps: SSRF tests use `httpx.MockTransport`; baseline runs pytest only.
- codegraph freshness: preflight checks; stale -> skip with toolchain_error.
- eslint known-broken: runner emits toolchain_error, not crash.
