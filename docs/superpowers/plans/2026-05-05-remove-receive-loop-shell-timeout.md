# Remove Receive Loop and Shell Timeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the 300s idle_timeout from the agent service receive loop and the 600s _MAX_TIMEOUT from the shell tool, simplifying both subsystems.

**Architecture:** Replace `asyncio.wait_for` timeout wrappers with direct awaits. The SDK stream manages its own connection lifecycle (disconnects naturally raise exceptions), and subprocesses exit on their own. Remove the multi-layer exception classification and recovery-branch logic that the timeout layer required.

**Tech Stack:** Python, asyncio, pytest

---

### Task 1: Simplify service.py receive loop

**Files:**
- Modify: `xbot/runtime/core/service.py:346-436`
- Modify: `tests/unit/runtime/test_agent_service_v2_integration.py:237-365`

- [ ] **Step 1: Rewrite the receive loop to remove timeout wrappers**

Replace lines 346-408 in service.py with the simplified version. The current code at lines 346-436 is:

```python
# ACP-style turn boundary: consume stream until explicit idle state event.
msg_count = 0
idle_timeout = 300.0
saw_idle_boundary = False
ended_due_to_pending_wait = False

stream_iter = client.receive_messages().__aiter__()

class _SdkStreamTimeoutError(Exception):
    """Timeout surfaced from SDK stream internals."""

while True:
    try:
        async def _read_next_message():
            try:
                return await stream_iter.__anext__()
            except TimeoutError as e:
                raise _SdkStreamTimeoutError(str(e)) from e

        message = await asyncio.wait_for(_read_next_message(), timeout=idle_timeout)
    except StopAsyncIteration:
        ...  # RuntimeError
    except _SdkStreamTimeoutError as e:
        ...  # RuntimeError + STREAM_TIMEOUT dispatch
    except TimeoutError:
        ...  # pending_user_wait check or RuntimeError + STREAM_TIMEOUT dispatch
```

Replace with:

```python
# ACP-style turn boundary: consume stream until explicit idle state event.
msg_count = 0
saw_idle_boundary = False

stream_iter = client.receive_messages().__aiter__()

while True:
    try:
        message = await stream_iter.__anext__()
    except StopAsyncIteration:
        if sm:
            self._dispatch_state_event(
                context.session_key,
                SessionEvent.STREAM_ENDED_UNEXPECTEDLY,
                reason="stream_ended_unexpectedly",
            )
        raise RuntimeError(
            f"SDK stream ended before idle boundary for session {context.session_key}"
        )
```

Also update the post-loop check at line 435 from:

```python
if not saw_idle_boundary and not ended_due_to_pending_wait:
    raise RuntimeError(f"Missing idle boundary for {context.session_key}")
```

to:

```python
if not saw_idle_boundary:
    raise RuntimeError(f"Missing idle boundary for {context.session_key}")
```

- [ ] **Step 2: Remove `_is_recoverable_stream_error_text` method**

Remove the `_is_recoverable_stream_error_text` static method at lines 500-512. Also remove the call to it at line 456 in the outer `except Exception` block. The `except Exception` block at lines 454-465 becomes:

```python
except Exception as e:
    logger.error(f"[AgentService] Error processing: {e}")
    yield AgentResponse(
        content=f"Error: {e}",
        finish_reason="error",
    )
```

The `_is_recoverable_stream_error_text` method is also used at lines 2572 and 2753 in the direct-stream processing paths. Change those calls to just check for the remaining relevant markers. Replace the method body to remove "receive loop idle timeout":

```python
@staticmethod
def _is_recoverable_stream_error_text(error_text: str) -> bool:
    """Whether an error string indicates recoverable stream boundary corruption."""
    text = str(error_text).lower()
    return any(
        marker in text
        for marker in (
            "missing idle boundary",
            "stream ended before idle boundary",
            "stream timeout error before idle boundary",
            "sdk stream timeout error before idle boundary",
        )
    )
```

- [ ] **Step 3: Remove `_has_pending_user_wait` usage from receive loop only**

The `_has_pending_user_wait` method at lines 482-497 is still used by `_dispatch_terminal_state`. Keep the method but remove the `ended_due_to_pending_wait` variable and its usage in the receive loop. The method itself stays untouched.

- [ ] **Step 4: Update test — remove `test_process_wait_timeout_without_pending_returns_error`**

This test at lines 332-365 in `test_agent_service_v2_integration.py` tests the `TimeoutError` + no-pending-wait → RuntimeError path. Since we removed that path entirely, this test is no longer valid. Delete the entire `test_process_wait_timeout_without_pending_returns_error` function.

- [ ] **Step 5: Update tests — rewrite the two pending-wait timeout tests**

The tests `test_process_wait_timeout_with_pending_permission_goes_waiting_permission` (lines ~237-282) and `test_process_wait_timeout_with_pending_interaction_goes_waiting_interaction` (lines ~285-329) both monkeypatch `asyncio.wait_for` to inject a `TimeoutError` at `timeout == 300.0`. Since the receive loop no longer uses `wait_for`, these tests need a different approach.

Replace both tests with versions that test the pending-wait path via the `_dispatch_terminal_state` method instead. The pending-wait behavior is now handled at the terminal state dispatch level, not in the receive loop.

For `test_process_wait_timeout_with_pending_permission_goes_waiting_permission`, rewrite as:

```python
@pytest.mark.asyncio
async def test_process_pending_permission_goes_waiting_permission(tmp_path) -> None:
    """When a permission request is pending when the stream ends with idle boundary,
    the session should transition to WAITING_PERMISSION."""
    service, registry = _make_service(tmp_path)
    bus = MessageBus()
    service._shared_resources["bus"] = bus
    context = _make_context("feishu:c-pending-perm")
    client = FakeClient(events=[SystemMessage(state="idle")])

    # Publish a permission request before starting process
    req = PermissionRequest(
        request_id="perm-1",
        session_key="feishu:c-pending-perm",
        channel="feishu",
        chat_id="c1",
        tool_name="Bash",
        tool_input={},
        message="allow?",
    )
    await bus.publish_permission_request(req)

    service._get_or_create_client = lambda sk: client  # type: ignore[method-assign]
    service._refresh_session_commands_from_client = lambda sk, c: None  # type: ignore[method-assign]

    responses = [r async for r in service.process(context)]
    assert responses == []
    assert registry.get_phase("feishu:c-pending-perm") == SessionPhase.WAITING_PERMISSION
```

For `test_process_wait_timeout_with_pending_interaction_goes_waiting_interaction`, rewrite similarly:

```python
@pytest.mark.asyncio
async def test_process_pending_interaction_goes_waiting_interaction(tmp_path) -> None:
    """When an interaction request is pending when the stream ends with idle boundary,
    the session should transition to WAITING_INTERACTION."""
    service, registry = _make_service(tmp_path)
    bus = MessageBus()
    service._shared_resources["bus"] = bus
    context = _make_context("feishu:c-pending-int")
    client = FakeClient(events=[SystemMessage(state="idle")])

    req = InteractionRequest(
        request_id="int-1",
        session_key="feishu:c-pending-int",
        channel="feishu",
        chat_id="c1",
        kind="question",
        prompt="continue?",
    )
    await bus.publish_interaction_request(req)

    service._get_or_create_client = lambda sk: client  # type: ignore[method-assign]
    service._refresh_session_commands_from_client = lambda sk, c: None  # type: ignore[method-assign]

    responses = [r async for r in service.process(context)]
    assert responses == []
    assert registry.get_phase("feishu:c-pending-int") == SessionPhase.WAITING_INTERACTION
```

Both tests no longer need `monkeypatch` since the receive loop doesn't use `asyncio.wait_for`. The pending-wait behavior is now tested by the `_dispatch_terminal_state` path, which already checks for pending permission/interaction requests after the idle boundary.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/unit/runtime/test_agent_service_v2_integration.py -v`
Expected: All tests PASS. The three timeout-related tests are gone or rewritten.

- [ ] **Step 7: Commit**

```bash
git add xbot/runtime/core/service.py tests/unit/runtime/test_agent_service_v2_integration.py
git commit -m "refactor(service): remove idle_timeout from receive loop

Remove the 300s idle_timeout and associated timeout wrapper layer from
the agent service receive loop. The SDK stream manages its own connection
lifecycle - no asyncio.wait_for needed on our side.

Removed: _SdkStreamTimeoutError, TimeoutError branch with
pending_user_wait check, idle_timeout constant.

Simplified _is_recoverable_stream_error_text to remove the
'receive loop idle timeout' marker.

Updated tests: removed timeout-injection tests, rewrote pending-wait
tests to test via idle boundary path instead."
```

---

### Task 2: Remove timeout from shell.py ExecTool

**Files:**
- Modify: `xbot/tools/shell.py`
- Modify: `tests/test_exec_security.py:46`

- [ ] **Step 1: Remove timeout from ExecTool.__init__**

Remove the `timeout` parameter, `TimeoutsConfig` import, and `self.timeout` initialization. Current `__init__`:

```python
def __init__(
    self,
    timeout: int | None = None,
    working_dir: str | Path | None = None,
    ...
):
    from xbot.platform.config.schema import TimeoutsConfig
    self.timeout = timeout or int(TimeoutsConfig().shell_exec)
    ...
```

Change to:

```python
def __init__(
    self,
    working_dir: str | Path | None = None,
    ...
):
    ...
```

Remove the `from xbot.platform.config.schema import TimeoutsConfig` line entirely.

- [ ] **Step 2: Remove _MAX_TIMEOUT and timeout from parameters schema**

Remove `_MAX_TIMEOUT = 600` class constant. Remove the `timeout` entry from the `parameters` dict. Current parameters schema includes:

```python
"timeout": {
    "type": "integer",
    "description": (
        "Timeout in seconds. Increase for long-running commands "
        "like compilation or installation (default 60, max 600)."
    ),
    "minimum": 1,
    "maximum": 600,
},
```

Remove this entire entry. The `required` array stays `["command"]` only.

- [ ] **Step 3: Remove timeout from execute method**

Remove the `timeout` parameter from `execute()` and the `effective_timeout` calculation + `asyncio.wait_for` wrapper. Current execute:

```python
async def execute(
    self, command: str, working_dir: str | Path | None = None,
    timeout: int | None = None, **kwargs: Any,
) -> str:
    ...
    effective_timeout = min(timeout or self.timeout, self._MAX_TIMEOUT)
    ...
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=effective_timeout,
        )
    except asyncio.TimeoutError:
        process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        return f"Error: Command timed out after {effective_timeout} seconds"
```

Change to:

```python
async def execute(
    self, command: str, working_dir: str | Path | None = None, **kwargs: Any,
) -> str:
    ...
    stdout, stderr = await process.communicate()
```

The entire `try/except asyncio.TimeoutError` block with `process.kill()` cleanup is removed. The outer `try/except Exception` block stays unchanged.

- [ ] **Step 4: Update test_exec_security.py**

Change `ExecTool(timeout=5)` at line 46 to `ExecTool()`. The timeout parameter no longer exists.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_exec_security.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add xbot/tools/shell.py tests/test_exec_security.py
git commit -m "refactor(shell): remove timeout enforcement from ExecTool

Remove _MAX_TIMEOUT=600, timeout parameter, and asyncio.wait_for
wrapper from ExecTool. Child processes manage their own lifecycle
and exit naturally - no timeout enforcement needed on our side."
```

---

### Task 3: Verify no other references and run full test suite

**Files:** None (verification only)

- [ ] **Step 1: Grep for stale references**

```bash
grep -rn "idle_timeout = 300\|_MAX_TIMEOUT = 600\|effective_timeout" xbot/ tests/ --include="*.py"
```

Expected: No matches.

```bash
grep -rn "asyncio.wait_for.*timeout.*300\|asyncio.wait_for.*communicate.*timeout" xbot/ tests/ --include="*.py"
```

Expected: No matches in service.py or shell.py.

- [ ] **Step 2: Run full test suite**

```bash
pytest tests/ -v --timeout=120 -x
```

Expected: All tests PASS. If any failures, investigate and fix before proceeding.

- [ ] **Step 3: Commit the spec and plan docs**

```bash
git add docs/superpowers/specs/2026-05-05-remove-receive-loop-shell-timeout-design.md docs/superpowers/plans/2026-05-05-remove-receive-loop-shell-timeout.md
git commit -m "docs: add design spec and implementation plan for timeout removal"
```