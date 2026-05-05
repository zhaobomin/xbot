# Remove Timeout Mechanism from Receive Loop and Shell Tool

## Problem

The 300s hardcoded `idle_timeout` in the agent service receive loop kills
long-running operations (downloads, installations) that take more than 5
minutes without emitting a stream message. Additionally, the shell tool has
a `_MAX_TIMEOUT = 600` hard cap that similarly truncates long commands.

The timeout wrapper layer adds significant code complexity with poor
determinism: `_SdkStreamTimeoutError`, `_is_recoverable_stream_error_text`,
`_has_pending_user_wait` branching, and multi-layer exception classification
make it hard to reason about what actually happened when a stream breaks.

## Decision

Remove all timeout enforcement from both subsystems. The underlying systems
already manage their own lifecycles:

- **SDK stream**: Connection lifecycle is managed by the SDK. When the
  connection drops, the stream naturally raises `StopAsyncIteration` or an
  exception. No timeout wrapper is needed on our side.

- **Subprocess**: A child process runs until it completes. `process.communicate()`
  returns when the process exits. No timeout wrapper is needed.

## Changes

### service.py — Receive Loop

**Before** (lines 346-408):
```python
idle_timeout = 300.0
# ...
message = await asyncio.wait_for(_read_next_message(), timeout=idle_timeout)
# Three exception branches: StopAsyncIteration, _SdkStreamTimeoutError, TimeoutError
# TimeoutError further splits: pending_user_wait vs RuntimeError
```

**After**:
```python
# Direct read, no timeout wrapper
message = await stream_iter.__anext__()
# Only StopAsyncIteration remains (stream ended without idle boundary → RuntimeError)
```

Remove:
- `idle_timeout = 300.0` constant
- `_SdkStreamTimeoutError` class and its catch branch
- `TimeoutError` catch branch (including `_has_pending_user_wait` check)
- `_is_recoverable_stream_error_text()` static method
- The 5 marker strings used for "recoverable error" classification

Keep:
- `StopAsyncIteration` handling (stream ended unexpectedly → RuntimeError)
- `_has_pending_user_wait()` method (still used by `_dispatch_terminal_state`)
- `_is_idle_boundary_message()` method (unchanged)

### shell.py — ExecTool

Remove:
- `_MAX_TIMEOUT = 600` class constant
- `timeout` parameter from `__init__`, `execute`, and `parameters` schema
- `effective_timeout` calculation (`min(timeout or self.timeout, self._MAX_TIMEOUT)`)
- `asyncio.wait_for(process.communicate(), timeout=effective_timeout)` wrapper
- Timeout kill cleanup block (`process.kill()` + `wait_for(process.wait(), 5.0)`)
- `TimeoutsConfig` import and `self.timeout` initialization from it

**After**:
```python
stdout, stderr = await process.communicate()
# No timeout wrapper, process runs until it exits naturally
```

The `timeout` parameter in the `parameters` schema description that mentions
"default 60, max 600" is also removed, along with the `minimum` and `maximum`
constraints.

## Files Changed

| File | What changes |
|------|-------------|
| `xbot/runtime/core/service.py` | Simplify receive loop, remove 3 exception branches and helper methods |
| `xbot/tools/shell.py` | Remove timeout parameter, _MAX_TIMEOUT, wait_for wrapper, kill cleanup |

## Not Changed

- Permission/interaction timeouts (`PermissionConfig.timeout`, `wait_permission_response`, `wait_interaction_response`) — these wait for *user input*, not agent execution
- MCP tool timeout — MCP servers have their own timeout semantics
- Client pool connect timeout — this is a *connection* timeout, not an execution timeout
- Crew task timeout — out of scope per user direction