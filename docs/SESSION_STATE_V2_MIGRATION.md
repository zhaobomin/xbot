# Session State v2 Migration (Breaking)

## Scope

This document describes the runtime state-machine breaking changes introduced in `v2.0.0`.

## What Changed

`SessionCoordinator` is now the only runtime state machine.

- Single write path: `RuntimeSessionRegistry.dispatch(session_key, event, ...)`
- Explicit event-driven transition table in `xbot/runtime/state/coordinator.py`
- `RuntimeSessionRegistry` is state snapshot/index holder and metadata accessor

## Removed APIs

The following APIs were removed and are no longer available:

- `SessionStateMachine`
- `RuntimeSessionRegistry.force_transition(...)`
- `RuntimeSessionRegistry.transition(...)`
- `RuntimeSessionRegistry.transaction(...)`
- `AgentService.transaction(...)`

## Removed Phases

These phases are removed:

- `RUNNING`
- `STOPPING`
- `RESETTING`
- `ERROR`

## New Canonical Phases

- `IDLE`
- `ACQUIRING_CLIENT`
- `SENDING_QUERY`
- `RECEIVING_STREAM`
- `WAITING_PERMISSION`
- `WAITING_INTERACTION`
- `DRAINING`
- `RELEASING_CLIENT`
- `BROKEN`

## Recovery Policy

- Stream corruption/idle-boundary errors are handled at session scope.
- Auto recovery retries once for the current request.
- `sdk_session_id` is preserved for resume by default.
- `sdk_session_id` is cleared only after 3 consecutive recovery failures.
- Busy-spin recovery is session-level client recycle/rebuild; gateway restart is not required.

## Required Caller Updates

1. Replace direct phase writes with event dispatch:

```python
runtime_registry.dispatch(session_key, SessionEvent.USER_MESSAGE, strict=False)
```

2. Update all phase checks to new enum names.

3. Remove dependencies on removed APIs/phases above.

## Compatibility

No compatibility shim is provided in `v2.0.0`.
Callers must upgrade in one step.
