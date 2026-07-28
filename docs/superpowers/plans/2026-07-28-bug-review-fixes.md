# Bug Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the reproducible defects from the 2026-07-28 bug review with minimal, test-backed changes and no architectural refactor.

**Architecture:** Keep all existing subsystem boundaries. Add small helpers only where an existing class already owns the behavior: URL validation in `network.py`, session deletion in `ConversationStore`, skill-file state in `ServiceContainer`, and reconnect classification in the bridge client. Preserve existing public APIs unless the defect is in that API.

**Tech Stack:** Python 3.11+, pytest, FastAPI, Pydantic, asyncio, TypeScript, Baileys, `tsc`, Ruff.

---

## File Map

- `xbot/interfaces/gateway/app.py`: static routes, auth input errors, revoke API, export exclusions, skill endpoints.
- `xbot/interfaces/gateway/services.py`: workspace skill discovery and enabled state.
- `xbot/platform/security/network.py`: mapped IPv6 normalization.
- `xbot/runtime/session/conversation_store.py`: persistent indexed deletion.
- `xbot/interfaces/gateway/auth.py`: bcrypt byte-length validation.
- `xbot/platform/config/loader.py`: split-provider merge and atomic save.
- `xbot/platform/config/schema.py`: side-effect-free legacy provider getter.
- `xbot/platform/config/paths.py`: safe media namespace validation.
- `xbot/platform/utils/helpers.py`: ID3 detection.
- `xbot/memory/store.py`, `xbot/memory/reme.py`: robust timestamp formatting.
- `xbot/interaction/response_parser.py`: unambiguous permission keywords.
- `xbot/crew/planner/utils.py`, `xbot/crew/process.py`: fallback parsing and unknown-task diagnostics.
- `xbot/channels/telegram.py`: collision-resistant draft IDs.
- `bridge/src/whatsapp.ts`, `bridge/src/server.ts`: reconnect, manual disconnect, batch isolation, validation, bounded shutdown.
- Existing focused test modules plus `tests/test_bug_review_2026_07_28.py`: regression coverage.

### Task 1: Gateway file-read, SSRF, password, and export security

**Files:**
- Modify: `xbot/interfaces/gateway/app.py`
- Modify: `xbot/platform/security/network.py`
- Modify: `xbot/interfaces/gateway/auth.py`
- Test: `tests/test_webui_adapter.py`
- Test: `tests/test_network_security.py`

- [ ] **Step 1: Write failing tests**

Add tests proving:

```python
def test_static_route_does_not_accept_path_override(...):
    response = client.get("/favicon.ico?_path=/etc/hosts")
    assert response.content == expected_favicon

def test_ipv4_mapped_ipv6_is_private():
    assert _is_private(ipaddress.ip_address("::ffff:127.0.0.1"))

def test_login_rejects_password_over_72_utf8_bytes(...):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "汉" * 25})
    assert response.status_code == 400

def test_change_password_rejects_overlong_current_and_new_passwords(...):
    for route in ("/api/auth/change-password", "/api/auth/password"):
        assert request_change(route, current="汉" * 25, new="valid").status_code == 400
        assert request_change(route, current="valid", new="汉" * 25).status_code == 400

def test_workspace_export_excludes_s3_credentials(...):
    assert ".webui/s3.json" not in exported_names
```

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/pytest -q \
  tests/test_webui_adapter.py::test_static_route_does_not_accept_path_override \
  tests/test_webui_adapter.py::test_login_rejects_password_over_72_utf8_bytes \
  tests/test_webui_adapter.py::test_change_password_rejects_overlong_current_and_new_passwords \
  tests/test_webui_adapter.py::test_workspace_export_excludes_s3_credentials \
  tests/test_network_security.py::test_ipv4_mapped_ipv6_is_private
```

Expected: all new tests fail on the reported behavior.

- [ ] **Step 3: Implement minimal fixes**

Use a factory returning a zero-argument static handler. Normalize
`IPv6Address.ipv4_mapped` before network matching. Add a shared auth validator
that rejects encoded passwords longer than 72 bytes; translate it to HTTP 400.
Extend `_should_include_workspace_path()` to exclude `.webui/s3.json`.

- [ ] **Step 4: Verify GREEN**

Run the command from Step 2 and expect all tests to pass.

### Task 2: Persistent message deletion

**Files:**
- Modify: `xbot/runtime/session/conversation_store.py`
- Modify: `xbot/interfaces/gateway/app.py`
- Test: `tests/test_conversation_store.py`
- Test: `tests/test_webui_adapter.py`

- [ ] **Step 1: Write failing store tests**

```python
def test_delete_message_rewrites_disk_and_adjusts_consolidated_offset(tmp_path):
    store = ConversationStore(tmp_path)
    session = store.get_or_create("web:admin:test")
    # persist three messages with last_consolidated=2
    assert store.delete_message(session, 0) is True
    reloaded = ConversationStore(tmp_path).get(session.key)
    assert [m["content"] for m in reloaded.messages] == ["m1", "m2"]
    assert reloaded.last_consolidated == 1
```

Also test deleting an unconsolidated index leaves the offset unchanged and an
invalid index returns `False`.

- [ ] **Step 2: Verify RED**

Run the new store tests; expect failure because `delete_message` is absent.

- [ ] **Step 3: Implement and route through the store**

Add `ConversationStore.delete_message(session, index) -> bool`. Delete the
entry, decrement `last_consolidated` iff `index < last_consolidated`, update
the timestamp, mark dirty, and call `save()`. Replace both HTTP and WebSocket
inline deletion blocks with this method.

- [ ] **Step 4: Verify GREEN**

Run the focused conversation-store and WebUI revoke tests.

### Task 3: Real workspace skill toggle

**Files:**
- Modify: `xbot/interfaces/gateway/services.py`
- Modify: `xbot/interfaces/gateway/app.py`
- Test: `tests/test_webui_adapter.py`

- [ ] **Step 1: Write failing endpoint tests**

Test that the primary root is `$workspace/.claude/skills`, disabling renames
`SKILL.md` to `SKILL.md.disabled`, list reports `enabled=False`, get/update work
while disabled, re-enabling restores `SKILL.md`, and builtin/missing skills
return 400/404 without modifying package files.

- [ ] **Step 2: Verify RED**

Run the new skill endpoint tests; expect the current echo-only endpoint and
legacy `$workspace/skills` path assertions to fail.

- [ ] **Step 3: Implement minimal file-state behavior**

Change `primary_skill_root()` to `.claude/skills`. Teach `list_skills()` to
recognize `SKILL.md.disabled`. Add a service helper that resolves an enabled or
disabled workspace skill file. Toggle with `Path.replace()` and route
get/update/delete through the resolver. Do not introduce a new persistence
schema.

- [ ] **Step 4: Verify GREEN**

Run `tests/test_webui_adapter.py` skill tests.

### Task 4: Configuration correctness and safe persistence

**Files:**
- Modify: `xbot/platform/config/loader.py`
- Modify: `xbot/platform/config/schema.py`
- Test: `tests/test_config_loader.py`

- [ ] **Step 1: Write failing tests**

Cover preservation of existing `models` and `extraHeaders`, absence of getter
mutation, and successful atomic replacement with no leftover temp file.
Patch `os.replace` to fail and assert the old valid configuration remains
unchanged.

- [ ] **Step 2: Verify RED**

Run the new config tests; expect replacement, mutation, and partial-write
assertions to fail.

- [ ] **Step 3: Implement minimal fixes**

Merge only `apiKey` and `apiBase` into the existing provider mapping. Make the
legacy getter use `get()` without insertion and explicitly create the legacy
entry only in the WebUI compatibility path that needs it. Serialize into a
same-directory `NamedTemporaryFile`, flush/fsync/chmod it, then `os.replace`;
unlink the temporary file on failure.

- [ ] **Step 4: Verify GREEN**

Run `tests/test_config_loader.py` and provider-related WebUI tests.

### Task 5: Small correctness and robustness fixes

**Files:**
- Modify: `xbot/platform/utils/helpers.py`
- Modify: `xbot/platform/config/paths.py`
- Modify: `xbot/memory/store.py`
- Modify: `xbot/memory/reme.py`
- Modify: `xbot/interaction/response_parser.py`
- Modify: `xbot/crew/planner/utils.py`
- Modify: `xbot/crew/process.py`
- Modify: `xbot/channels/telegram.py`
- Test: `tests/test_bug_review_2026_07_28.py`
- Test: `tests/test_response_parser.py`

- [ ] **Step 1: Write failing tests**

Cover ID3 MIME detection, rejecting `../escape`, absolute paths, `/` and `\`
separators, `.` and `..` as media namespaces before any directory is created,
safe `timestamp=None` formatting, `ok`/`确认` not authorizing a permission request,
colon-containing fallback entries remaining intact, an unknown manager task
emitting a warning, and two same-millisecond Telegram sends using distinct
draft IDs.

- [ ] **Step 2: Verify RED**

Run only the new tests and confirm each fails for its intended assertion.

- [ ] **Step 3: Implement minimal fixes**

Add direct guards or local helpers only. Use a per-channel monotonic counter
combined with the millisecond value for Telegram draft IDs; do not change
streaming behavior.

- [ ] **Step 4: Verify GREEN**

Run the focused helper, parser, planner, memory, and Telegram tests.

### Task 6: WhatsApp reconnect and batch handling

**Files:**
- Modify: `bridge/src/whatsapp.ts`
- Modify: `bridge/src/server.ts`
- Create: `bridge/test/whatsapp-lifecycle.test.mjs`
- Test: `tests/test_whatsapp_channel.py`

- [ ] **Step 1: Add failing bridge behavior tests**

Export the small pure predicates `isRetryableDisconnect()` and
`validateSendCommand()` from their existing modules. Use Node's built-in
`node:test` runner and lightweight fake sockets/servers to verify:

- every enumerated retryable and non-retryable status, plus `undefined`;
- a second `connect()` rejects before auth/version network calls when a socket
  reference already exists;
- `disconnect()` awaits `end()`, cancels a pending timer, clears the socket,
  and marks the close as manual;
- the connection-update handler schedules no timer for a manual close or a
  non-retryable reason;
- a first malformed inbound message throwing does not prevent the second
  message from reaching `onMessage`;
- invalid send commands are rejected before `sendMessage()`;
- `BridgeServer.stop()` resolves within its bound and terminates clients when
  the close callback never arrives.

Small private-method extraction inside the existing classes is allowed only
where required to call the current handler logic from these tests. Do not add
dependency-injection frameworks or new lifecycle classes.

- [ ] **Step 2: Verify RED**

Run:

```bash
cd bridge && npm run build && node --test test/whatsapp-lifecycle.test.mjs
```

Expected: new behavior tests fail on the current reconnect/disconnect and
shutdown behavior.

- [ ] **Step 3: Implement minimal bridge behavior**

Add a local `isRetryableDisconnect(statusCode)` function with the enumerated
status codes. Track `_manualDisconnect`; clear the socket reference on remote
close; clear timers and await `end()` on manual disconnect. Guard `connect()`
before any async setup and reject when `this.sock` is non-null. Wrap each loop
iteration in its own `try/catch`. Validate `type`, `to`, and `text`. Race the
WebSocket-server close completion against a bounded timeout that terminates
remaining local clients.

- [ ] **Step 4: Verify GREEN and compile**

Run:

```bash
.venv/bin/pytest -q tests/test_whatsapp_channel.py
cd bridge && npm run build && npx eslint src && node --test test/whatsapp-lifecycle.test.mjs
```

Expected: tests pass and both TypeScript commands exit 0.

### Task 7: Regression and full verification

**Files:**
- Modify only if verification reveals an in-scope regression.

- [ ] **Step 1: Run focused Python suites**

```bash
.venv/bin/pytest -q \
  tests/test_webui_adapter.py \
  tests/test_config_loader.py \
  tests/test_conversation_store.py \
  tests/test_response_parser.py \
  tests/test_whatsapp_channel.py \
  tests/test_bug_review_2026_07_28.py
```

- [ ] **Step 2: Run full Python quality gates**

```bash
.venv/bin/ruff check xbot tests
.venv/bin/pytest -q
```

- [ ] **Step 3: Run frontend and bridge gates**

```bash
cd xbot/interfaces/webui/frontend && npm run lint && npm run build
cd bridge && npx eslint src && npm run build && node --test test/whatsapp-lifecycle.test.mjs
```

- [ ] **Step 4: Run the review toolchain**

```bash
.venv/bin/python -m scripts.review.orchestrate
```

Inspect the generated review and machine-readable findings. Confirm no
collection/baseline failure is hidden behind a zero-finding count.

- [ ] **Step 5: Correct ClientPool return-value documentation**

Update `ClientPool.disconnect()` and aggregate-count docstrings to describe the
already-tested behavior: missing sessions count as already disconnected, while
a graceful disconnect failure returns `False` after best-effort cleanup. Do not
change the implementation or its tests.

- [ ] **Step 6: Inspect the final diff**

Confirm every production change maps to an observed failing regression test,
no unrelated user files changed, and no refactor entered the patch.
