# ACP Query Idle Boundary Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace result-drain request boundaries with ACP-style `query()` streaming and `session_state_changed: idle` turn completion to eliminate multi-result truncation and cross-turn pollution.

**Architecture:** Keep one persistent `query()` stream per `session_key`, push each new user prompt into the session input stream, and end a turn only when the stream emits `SystemMessage(subtype=session_state_changed, state=idle)`. `ResultMessage` remains content/stop-reason metadata, not the boundary. Remove legacy quiet-window/drain-cap logic and client-pool driven receive loop from the main dispatch path.

**Tech Stack:** Python 3.11+, `claude_agent_sdk.query`, asyncio queues/iterators, pytest/pytest-asyncio.

---

### Task 1: Add ACP-style Session Runtime Primitives

**Files:**
- Modify: `xbot/runtime/core/service.py`
- Test: `tests/test_agent_service.py`

- [ ] **Step 1: Write failing tests for persistent query session creation/reuse**

```python
async def test_query_session_reused_per_session_key(...):
    first = await service._get_or_create_query_session("s1")
    second = await service._get_or_create_query_session("s1")
    assert first is second
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest -q tests/test_agent_service.py -k "query_session_reused"`
Expected: FAIL (method/runtime not implemented)

- [ ] **Step 3: Add minimal runtime structs and helpers**

```python
@dataclass
class QuerySessionRuntime:
    query_iter: Any
    input_stream: Any
    prompt_running: bool = False
    pending_prompts: deque = field(default_factory=deque)
    closed: bool = False
```

- [ ] **Step 4: Implement `_get_or_create_query_session()` and `_close_query_session()`**

Run: `pytest -q tests/test_agent_service.py -k "query_session_reused"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xbot/runtime/core/service.py tests/test_agent_service.py
git commit -m "refactor: add persistent query session runtime primitives"
```

### Task 2: Refactor `process()` to Idle-Boundary Turn Loop

**Files:**
- Modify: `xbot/runtime/core/service.py`
- Test: `tests/test_agent_service.py`

- [ ] **Step 1: Write failing boundary tests first**

Use/extend `TestAcpStyleTurnBoundary` to assert:

```python
# result is not terminal
# idle is terminal
assert [r.event_type for r in responses] == ["content", "result", "task", "result"]
assert ended_by_idle is True
```

- [ ] **Step 2: Run boundary test subset and verify failures**

Run: `pytest -q tests/test_agent_service.py -k AcpStyleTurnBoundary`
Expected: FAIL on old receive_messages/result-drain behavior

- [ ] **Step 3: Replace process loop with ACP-style loop**

```python
await query_session.input_stream.push(query_prompt)
while True:
    message = await query_session.query_iter.next()
    self._sync_sdk_session_mapping(context.session_key, message)
    response = self._convert_event(message)
    if response:
        yield response
    if _is_idle_boundary(message):
        break
```

- [ ] **Step 4: Keep result semantics explicit**

- emit every `ResultMessage` response (no first-result break)
- update stop_reason metadata from result subtype
- never exit on result alone

- [ ] **Step 5: Re-run tests**

Run: `pytest -q tests/test_agent_service.py -k AcpStyleTurnBoundary`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add xbot/runtime/core/service.py tests/test_agent_service.py
git commit -m "refactor: use idle session-state boundary for process turns"
```

### Task 3: Remove Result-Drain and Legacy Boundary Config

**Files:**
- Modify: `xbot/runtime/core/service.py`
- Modify: `xbot/platform/config/schema.py`
- Test: `tests/test_agent_service.py`

- [ ] **Step 1: Write failing config expectation tests**

```python
def test_legacy_result_drain_config_removed(...):
    assert not hasattr(config.agents.claude_sdk, "post_result_quiet_window_ms")
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest -q tests/test_agent_service.py -k "ResultDrainBehavior or legacy_result_drain_config_removed"`
Expected: FAIL

- [ ] **Step 3: Remove unused result-drain code paths**

Delete:
- `_get_post_result_quiet_window_ms`
- `_get_post_result_drain_cap_ms`
- `_get_task_terminal_statuses`
- pending-task ledger and background drain branches in `process()`

- [ ] **Step 4: Remove schema fields**

Delete from `ClaudeSDKAgentConfig`:
- `post_result_quiet_window_ms`
- `post_result_drain_cap_ms`
- `task_terminal_statuses`

- [ ] **Step 5: Replace old tests with idle-boundary equivalents**

- remove/retire `TestResultDrainBehavior`
- keep ACP boundary tests as canonical behavior

- [ ] **Step 6: Run focused suite**

Run: `pytest -q tests/test_agent_service.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add xbot/runtime/core/service.py xbot/platform/config/schema.py tests/test_agent_service.py
git commit -m "refactor: remove result-drain boundary and legacy schema knobs"
```

### Task 4: Session Lifecycle and Managed Paths Cleanup

**Files:**
- Modify: `xbot/runtime/core/service.py`
- Test: `tests/test_agent_service.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
async def test_reset_session_closes_query_runtime(...):
    await service.reset_session("s1", drop_sdk_context=True)
    assert "s1" not in service._query_sessions
```

- [ ] **Step 2: Run lifecycle tests and verify failure**

Run: `pytest -q tests/test_agent_service.py -k "lifecycle_cleanup or reset_session_closes_query_runtime"`
Expected: FAIL

- [ ] **Step 3: Update lifecycle methods**

- `reset_session`: close/remove query runtime for `session_key`
- `shutdown`: close all query runtimes
- `process_managed_direct`: no client-pool release branch; rely on query runtime lifecycle

- [ ] **Step 4: Verify no leaked pending prompt waiters**

```python
assert len(query_session.pending_prompts) == 0
assert query_session.closed is True
```

- [ ] **Step 5: Re-run lifecycle tests**

Run: `pytest -q tests/test_agent_service.py -k "AcpStyleTurnBoundary or managed_direct or reset_session"
`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add xbot/runtime/core/service.py tests/test_agent_service.py
git commit -m "refactor: align session lifecycle with persistent query runtimes"
```

### Task 5: Enforce Session-State Event Preconditions

**Files:**
- Modify: `xbot/runtime/core/service.py`
- Test: `tests/test_agent_service.py`

- [ ] **Step 1: Keep/extend failing env precondition test**

```python
assert options.env.get("CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS") == "1"
```

- [ ] **Step 2: Implement env injection in `_build_env_config()`**

```python
env["CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"] = "1"
```

- [ ] **Step 3: Run targeted test**

Run: `pytest -q tests/test_agent_service.py -k environment_precondition_for_idle_events`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add xbot/runtime/core/service.py tests/test_agent_service.py
git commit -m "fix: always enable claude session state events for idle boundary"
```

### Task 6: Full Validation + Documentation Sync

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `START.md` (if boundary behavior/config docs mention old knobs)
- Test: `tests/test_agent_service.py`

- [ ] **Step 1: Add changelog entry for boundary model migration**

Include:
- ACP-style idle boundary
- multi-result behavior (emit all)
- removed drain config knobs

- [ ] **Step 2: Update user-facing docs that mention old drain controls**

Remove references to removed config fields.

- [ ] **Step 3: Run required verification commands**

Run:
- `pytest -q tests/test_agent_service.py -k AcpStyleTurnBoundary`
- `pytest -q tests/test_agent_service.py -k "RunDispatch or reset_session or managed_direct"`
- `pytest -q tests/test_agent_service.py`

Expected: All PASS

- [ ] **Step 4: Final commit**

```bash
git add CHANGELOG.md START.md tests/test_agent_service.py xbot/runtime/core/service.py xbot/platform/config/schema.py
git commit -m "refactor: migrate turn boundary to ACP idle-state model"
```

## Notes for Implementers

- Follow TDD order strictly: write/adjust failing test, then minimal implementation.
- Avoid introducing fallback dual-mode branches; this migration intentionally removes legacy paths.
- Keep commits small and reviewable (one concern per commit above).
- Prefer behavior-level assertions (`event_type`, output ordering, stop conditions) over internal private-state coupling.
