# AskUserQuestion Validation Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify AskUserQuestion validation behavior across runtime handling and answer parsing while fixing retry accumulation and preserving comma-only multi-answer input.

**Architecture:** Introduce a shared validation utility in `xbot/agent/interaction` as the single source of truth for mode normalization, option matching, and answer splitting. Route both runtime interaction handling and permission-side answer parsing through that utility. Keep existing public request/response structures unchanged and constrain changes to interaction modules plus targeted tests.

**Tech Stack:** Python 3.11, asyncio, pytest, xbot MessageBus/SessionManager.

---

## File Structure and Responsibilities

- Create: `xbot/agent/interaction/ask_user_validation.py`
- Modify: `xbot/agent/interaction/response_handlers.py`
- Modify: `xbot/agent/interaction/permission.py`
- Modify: `xbot/agent/interaction/response_parser.py`
- Create: `tests/test_ask_user_validation.py`
- Modify: `tests/test_interaction_bug_fixes.py`
- Modify: `tests/test_multi_question_ask_user.py`

Responsibility split:

- `ask_user_validation.py`: canonical mode normalization, splitting, option matching, and structured validation result.
- `response_handlers.py`: orchestration only (state transitions, bus submit/cancel, retry lifecycle keyed by request).
- `permission.py`: convert validated results into AskUserQuestion `answers` format without duplicating matching logic.
- `response_parser.py`: shared keyword parsing + mode canonicalization helpers used by runtime.
- Tests: isolated unit coverage for utility + integration-like coverage for runtime flow.

---

### Task 1: Build Shared Validation Utility (Single Source of Truth)

**Files:**
- Create: `xbot/agent/interaction/ask_user_validation.py`
- Create: `tests/test_ask_user_validation.py`

- [ ] **Step 1: Write failing tests for mode normalization, split, and matching**

```python
# tests/test_ask_user_validation.py

def test_normalize_validation_mode_aliases_to_suggested():
    assert normalize_validation_mode("open") == "suggested"
    assert normalize_validation_mode("loose") == "suggested"


def test_split_answers_comma_only():
    assert split_answers("A, B，C、D") == ["A", "B", "C", "D"]
    assert split_answers("A B") == ["A B"]


def test_match_option_prefix_unique_match():
    assert match_option("北京", ["北京市", "上海市"]) == "北京市"


def test_match_option_prefix_ambiguous_returns_none():
    assert match_option("A", ["Alpha", "Alpine"]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ask_user_validation.py -v`
Expected: FAIL with import errors / missing functions.

- [ ] **Step 3: Implement utility module minimally**

```python
# xbot/agent/interaction/ask_user_validation.py
VALIDATION_MODE_ALIASES = {"open": "suggested", "loose": "suggested"}
CANONICAL_MODES = {"strict", "suggested"}


def normalize_validation_mode(mode: str | None) -> str:
    # unknown -> suggested
    ...


def split_answers(raw: str) -> list[str]:
    # split only by ， , 、
    ...


def match_option(candidate: str, options: list[str]) -> str | None:
    # exact case-insensitive first, then unique prefix
    ...
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_ask_user_validation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_ask_user_validation.py xbot/agent/interaction/ask_user_validation.py
git commit -m "feat: add shared AskUserQuestion validation utilities"
```

---

### Task 2: Unify Runtime Validation and Fix Retry Accumulation Bug

**Files:**
- Modify: `xbot/agent/interaction/response_handlers.py`
- Modify: `tests/test_interaction_bug_fixes.py`

- [ ] **Step 1: Write failing tests for request-scoped retry progression**

```python
# tests/test_interaction_bug_fixes.py

@pytest.mark.asyncio
async def test_retry_count_accumulates_without_external_retry_arg(...):
    # call handler twice with invalid input and default retry_count
    # assert second message shows 第 2/3 次尝试

@pytest.mark.asyncio
async def test_retry_key_isolated_by_request_id(...):
    # replace pending interaction request, ensure old retry does not leak
```

- [ ] **Step 2: Run targeted tests to verify failures**

Run: `pytest tests/test_interaction_bug_fixes.py -k "accumulates or request_id" -v`
Expected: FAIL due current retry reset behavior.

- [ ] **Step 3: Refactor runtime handler to use shared validator and request-scoped retry key**

```python
# response_handlers.py sketch
request_id = self._bus.get_pending_interaction_for_session(msg.session_key)
retry_key = f"{msg.session_key}:{request_id}"
retry_count = self._interaction_retry_counts.get(retry_key, 0)

mode = normalize_validation_mode(req.metadata.get("validation_mode"))
result = validate_interaction_input(...)

if result.invalid_strict:
    retry_count += 1
    self._interaction_retry_counts[retry_key] = retry_count
```

- [ ] **Step 4: Ensure cleanup on all terminal paths**

- success: clear `retry_key`
- max retries: clear `retry_key`, clear pending request
- expired/cancelled/state-mismatch: clear any session+request stale retry entries

- [ ] **Step 5: Run tests to verify pass**

Run: `pytest tests/test_interaction_bug_fixes.py -v`
Expected: PASS with new progression assertions.

- [ ] **Step 6: Commit**

```bash
git add xbot/agent/interaction/response_handlers.py tests/test_interaction_bug_fixes.py
git commit -m "fix: make AskUserQuestion retries request-scoped and cumulative"
```

---

### Task 3: Unify Permission-Side Parsing with Shared Validator

**Files:**
- Modify: `xbot/agent/interaction/permission.py`
- Modify: `xbot/agent/interaction/response_parser.py`
- Modify: `tests/test_multi_question_ask_user.py`

- [ ] **Step 1: Add failing tests for parser/handler consistency**

```python
# tests/test_multi_question_ask_user.py

def test_parse_answers_uses_same_prefix_logic_as_runtime():
    answers = handler._parse_answers("北京", [{"question": "城市"}], [["北京市", "上海市"]])
    assert answers[0]["answer"] == "北京市"


def test_unknown_validation_mode_falls_back_to_suggested():
    assert normalize_validation_mode("weird-mode") == "suggested"
```

- [ ] **Step 2: Run tests to verify failures (if behavior not aligned yet)**

Run: `pytest tests/test_multi_question_ask_user.py -v`
Expected: FAIL on new consistency checks before implementation.

- [ ] **Step 3: Migrate `_parse_answers` to shared functions**

```python
# permission.py sketch
parts = split_answers(user_response)
matched = match_option(candidate, valid_options)
```

Also add mode canonicalization helper usage in interaction-request metadata preparation path (where mode is read).

- [ ] **Step 4: Keep backward compatibility in `response_parser.py`**

Add/export small helper wrappers if needed so call sites avoid duplicate constants and mode strings.

- [ ] **Step 5: Run relevant tests**

Run: `pytest tests/test_multi_question_ask_user.py tests/test_ask_user_validation.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add xbot/agent/interaction/permission.py xbot/agent/interaction/response_parser.py tests/test_multi_question_ask_user.py
git commit -m "refactor: share AskUserQuestion matching between runtime and parser"
```

---

### Task 4: Full Regression Run and Final Cleanup

**Files:**
- Modify: `tests/test_interaction_bug_fixes.py` (if assertion messages need stabilization)
- Modify: `tests/test_progress_coalescer.py` (only if side-effects are introduced)

- [ ] **Step 1: Run full targeted suite for interaction subsystem**

Run:
`pytest tests/test_ask_user_validation.py tests/test_interaction_bug_fixes.py tests/test_multi_question_ask_user.py tests/test_progress_coalescer.py -v`

Expected: all PASS.

- [ ] **Step 2: Run broader agent regression subset**

Run:
`pytest tests/test_integration_real.py -k "RuntimeResponseHandlers or permission" -v`

Expected: PASS (or unrelated pre-existing failures explicitly documented).

- [ ] **Step 3: Resolve lint/type regressions in touched files**

Run:
`ruff check xbot/agent/interaction tests/test_ask_user_validation.py tests/test_interaction_bug_fixes.py tests/test_multi_question_ask_user.py`

Expected: no new violations in changed files.

- [ ] **Step 4: Final commit for test stabilization only (if needed)**

```bash
git add tests/ xbot/agent/interaction/
git commit -m "test: add regression coverage for AskUserQuestion validation unification"
```

---

## Execution Notes

- Follow `@superpowers/test-driven-development` discipline for each task: fail -> implement -> pass.
- Keep commits small and functional, one logical change per commit.
- Do not expand scope into non-AskUserQuestion permission flows.
- If ambiguity behavior (prefix matching multiple options) triggers product disagreement, pause and confirm before changing fallback semantics.

## Done Criteria

- Shared validation utility is the only place defining mode normalization + matching rules.
- Runtime strict mode shows `1/3`, `2/3`, `3/3` correctly on sequential invalid replies.
- `open`/`loose`/unknown mode behavior is compatible and tested.
- Multi-question split remains comma-only and documented by tests.
- No regressions in targeted interaction and permission tests.
