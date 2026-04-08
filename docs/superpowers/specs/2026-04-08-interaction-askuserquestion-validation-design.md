# AskUserQuestion Validation Unification Design

Date: 2026-04-08  
Scope: `xbot/agent/interaction` (channel runtime path + permission AskUserQuestion path)

## 1. Context

Current `AskUserQuestion` handling has split validation logic:

- Runtime inbound response path validates in `response_handlers.py`.
- Answer parsing path validates again in `permission.py::_parse_answers`.

This split caused behavior drift and bugs:

- Retry counter does not accumulate on the real `service.run()` path because handler default argument starts at `0` for each call.
- `strict` behavior differs by path (exact match in runtime validation vs prefix match in parsing).
- Mode compatibility is narrow (no alias or graceful fallback policy).

## 2. Goals and Non-Goals

### Goals

1. Fix known bugs without large-scope refactor.
2. Unify option matching logic so runtime validation and answer parsing behave consistently.
3. Preserve current UX constraints:
   - multi-question uses comma-like separators only (`，`, `,`, `、`)
   - `strict` cancels after 3 invalid attempts
4. Improve compatibility for open input mode.

### Non-Goals

1. Do not introduce richer input syntaxes (numbered answers/newline forms).
2. Do not redesign MessageBus or SessionManager protocols.
3. Do not change external request/response schema.

## 3. Chosen Approach

Selected: **minimal patch with shared validation utilities**.

We introduce a shared validation module inside `xbot/agent/interaction` and migrate both call sites to use it.

Why this approach:

- Fixes consistency issues at medium cost.
- Limits blast radius versus a full object-model rewrite.
- Keeps public behavior and interfaces stable.

## 4. Validation Model

### 4.1 Modes

Canonical modes:

- `strict`
- `suggested` (default open mode)

Compatibility aliases:

- `open` -> `suggested`
- `loose` -> `suggested`

Unknown mode handling:

- fallback to `suggested`
- emit warning log with original mode value

### 4.2 Matching Rules

Unified matcher rules:

1. Case-insensitive exact match first.
2. Prefix match allowed (for both `strict` and `suggested`) based on confirmed requirement.
3. If multiple options share same prefix, treat as ambiguous and return no match (avoid silent wrong normalization).

### 4.3 Splitting Rules

Input split for multi-question answers remains:

- separators: `，`, `,`, `、`
- no whitespace-based split
- no numbered syntax support

## 5. Runtime Flow Changes

`RuntimeResponseHandlers.handle_interaction_response()` will:

1. Resolve pending interaction request.
2. Delegate answer normalization/validation to shared validator.
3. Read and update retry count from persistent per-request storage.
4. On invalid strict answer:
   - increment retry
   - send standardized error prompt
   - if retry >= 3: cancel interaction and clear pending state
5. On valid answer or suggested free-text:
   - submit normalized response
   - clear retry state

### 5.1 Retry Counter Storage

Current issue: retry count passed as method argument is not persisted across inbound turns in `service.run()`.

New rule:

- source of truth is internal dict keyed by `retry_key = "{session_key}:{request_id}"`
- ignore external `retry_count` for progression logic (kept only for backward compatibility)

This prevents:

- reset-to-1 bug on every invalid reply
- stale count reuse across different request ids

## 6. Permission Path Changes

`BasePermissionHandler._parse_answers()` will use shared matching helpers instead of its own independent matching implementation.

Effects:

- single-question and multi-question normalization now follows same matcher behavior as runtime validation
- prefix support behaves consistently in all paths
- fewer future drift points

## 7. Error Handling and User Messaging

Standardized strict invalid response message includes:

- flattened valid options list
- original user input
- retry progress (`N/3`)

On max retries:

- notify cancellation
- clear pending interaction request via async-safe bus cleanup
- transition phase to `IDLE`

Expired/missing request handling remains unchanged:

- "request expired/cancelled" guidance preserved

## 8. Testing Strategy

Add/adjust tests to cover real behavior gaps:

1. Runtime strict + prefix match accepted (`北京` -> `北京市`).
2. Retry accumulation in real run-like invocation sequence (`1/3`, `2/3`, `3/3`).
3. Retry key isolation across request replacement.
4. Mode alias mapping (`open`, `loose`) and unknown fallback warning.
5. Suggested mode unmatched answer passthrough.
6. Multi-question comma-only split behavior retained.
7. 3-fail cancellation clears pending interaction and phase path remains valid.

## 9. File-Level Change Plan

Primary files:

- `xbot/agent/interaction/response_handlers.py`
- `xbot/agent/interaction/permission.py`
- `xbot/agent/interaction/response_parser.py` (or new dedicated validation helper module)

Tests:

- `tests/test_interaction_bug_fixes.py`
- `tests/test_multi_question_ask_user.py`
- add focused tests for mode alias/fallback and request-scoped retry keying

## 10. Risks and Mitigations

1. Behavior shift for strict prefix matching conflict cases.
   - Mitigation: explicit ambiguity handling and tests.
2. Retry storage key migration side effects.
   - Mitigation: keep backward-compatible cleanup by session-key where needed during transition.
3. User-facing wording changes causing snapshot/test churn.
   - Mitigation: stabilize one canonical message template and update tests once.

## 11. Rollout

1. Implement shared validator utilities.
2. Migrate runtime handler and parser paths.
3. Add/adjust tests.
4. Run targeted suite first, then broader agent tests.
5. If regressions appear, gate by preserving old behavior behind fallback path only for uncovered edge cases.

## 12. Acceptance Criteria

1. Strict mode retries increment across inbound turns and cancel exactly at 3 invalid attempts.
2. Prefix match behavior is identical in runtime validation and parse_answers normalization.
3. Suggested/open/loose inputs support passthrough when unmatched.
4. Multi-question split remains comma-only.
5. Existing permission/interaction integration tests continue to pass after updates.
