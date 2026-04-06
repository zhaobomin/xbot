# Session State Management Simplification

**Date**: 2026-04-06  
**Status**: Draft  
**Author**: Claude

## Executive Summary

Simplify xbot's session state management from 5 layers to 1 layer, removing ~1200 lines of complex dual-write code. The SDK manages conversation history; xbot only needs to track what SDK doesn't: connection pooling, request routing, concurrency protection, and task lifecycle.

## Problem Statement

### Current Architecture Issues

1. **5 layers of state management**: StateMachine → Store → Adapter → legacy dicts → Coordinator
2. **Dual-write complexity**: Every setter writes to both SessionStore and legacy dicts
3. **11 legacy dicts** in Backend: `_clients`, `_client_models`, `_sdk_session_ids`, `_session_contexts`, `_client_last_used`, `_active_task_ids`, `_active_request_ids`, `_session_commands`, `_client_skills_versions`, `_long_running_turns`, `_client_creation_futures`
4. **Concurrency complexity**: Requires `_adapter_epoch` mechanism to detect stale adapters
5. **Difficult to maintain**: New contributors struggle to understand the state flow

### SDK Capability Testing Results

After testing `claude-agent-sdk` v0.1.56:

| Capability | SDK Support | xbot Needs |
|------------|-------------|------------|
| Concurrent request protection | ❌ No | Yes - Phase state machine |
| Task cancellation | ✅ `interrupt()` | No - Use SDK API |
| `stop_task(task_id)` | ✅ Available | No - `interrupt()` sufficient |
| Session CRUD | ✅ Full | No - Use SDK APIs |
| Context usage query | ✅ Yes | No - Use `get_context_usage()` |
| Model/skills tracking | ✅ In options | No - SDK handles |
| Conversation history | ✅ Managed | No - SDK persists |

## Proposed Solution

### Core Principle: Single Source of Truth

Replace 5 layers with 1 `SessionManager` class that stores only what SDK doesn't manage.

### New Data Structure

```python
@dataclass
class SessionState:
    """Minimal session state - only what SDK doesn't manage."""
    
    # Identity
    session_key: str                    # xbot's session ID (e.g., "slack:C12345")
    sdk_session_id: str | None = None   # SDK's session UUID
    
    # Routing (required - SDK doesn't know channel/chat_id)
    channel: str = ""                   # Channel type (slack, feishu, telegram, etc.)
    chat_id: str = ""                   # Chat ID within channel
    
    # Connection (required - SDK doesn't pool clients)
    client: ClaudeSDKClient | None = None
    last_active: float = field(default_factory=time.time)
    
    # Process tracking (required - for force kill orphan processes)
    client_pid: int | None = None       # PID of SDK subprocess
    process_handle: Any | None = None   # Process handle for force kill
    
    # Concurrency (required - SDK doesn't prevent concurrent queries)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    phase: SessionPhase = SessionPhase.IDLE
    
    # Tasks (required - for asyncio task cancellation on session terminate)
    tasks: list[asyncio.Task] = field(default_factory=list)
```

### Key Design Decisions (from discussion)

1. **process_handle retained**: Force kill is a safety net for orphan processes when disconnect fails. Low frequency but high impact if not handled.

2. **current_task_id removed**: Use `client.interrupt()` instead of `stop_task(task_id)`. The `interrupt()` method stops the entire request without needing a task_id.

3. **model/skills_version removed**: Not worth the complexity. Client recreation is acceptable.

4. **commands removed**: SDK manages command state internally.

### New Architecture

```
Before (5 layers):

Runtime
  ├── _state_machine: SessionStateMachine
  ├── _session_store: SessionStore
  └── _state_coordinator: SessionStateCoordinator

Backend
  ├── _state_adapter: SessionStateAdapter (dual-write)
  └── 11 legacy dicts

After (1 layer):

Runtime
  └── session_manager: SessionManager

Backend
  └── (direct access to session_manager)
```

### SessionManager API

```python
class SessionManager:
    """Unified session state management."""
    
    def __init__(self):
        self._sessions: dict[str, SessionState] = {}
        self._sdk_index: dict[str, str] = {}  # sdk_session_id -> session_key
        self._global_lock = asyncio.Lock()
    
    # === Lifecycle ===
    def get(self, session_key: str) -> SessionState | None
    def get_or_create(self, session_key: str) -> SessionState
    def get_by_sdk_id(self, sdk_session_id: str) -> SessionState | None
    
    # === SDK Session ID ===
    def set_sdk_session_id(self, session_key: str, sdk_id: str | None) -> None
    
    # === Routing ===
    def set_routing(self, session_key: str, channel: str, chat_id: str) -> None
    def get_routing(self, session_key: str) -> tuple[str, str] | None
    def resolve_routing(self, identifier: str) -> tuple[str, str, str] | None
        # Returns (session_key, channel, chat_id), accepts either session_key or sdk_session_id
    
    # === Concurrency ===
    def can_start_request(self, session_key: str) -> bool
    def start_request(self, session_key: str) -> bool
    def end_request(self, session_key: str, phase: SessionPhase = IDLE) -> None
    
    # === Connection ===
    def set_client(self, session_key: str, client: ClaudeSDKClient) -> None
    def get_client(self, session_key: str) -> ClaudeSDKClient | None
    def has_client(self, session_key: str) -> bool
    def list_client_sessions(self) -> list[str]
    
    # === Tasks ===
    def register_task(self, session_key: str, task: asyncio.Task) -> None
    def get_active_tasks(self, session_key: str) -> list[asyncio.Task]
    async def cancel_all_tasks(self, session_key: str) -> int
    
    # === Cleanup ===
    async def cleanup_session(self, session_key: str) -> None
    def list_stale_sessions(self, ttl_seconds: float) -> list[str]
```

### Phase State Machine (Simplified)

Keep the existing `SessionPhase` enum but simplify transitions:

```python
class SessionPhase(str, Enum):
    IDLE = "idle"               # Ready for new requests
    RUNNING = "running"         # Processing a request
    WAITING_PERMISSION = "waiting_permission"
    WAITING_INTERACTION = "waiting_interaction"
    STOPPING = "stopping"
    RESETTING = "resetting"
    ERROR = "error"
```

Transition rules remain in `SessionManager.start_request()` and `end_request()`.

### interrupt() vs stop_task()

SDK provides two cancellation APIs:

```python
# interrupt() - stops entire request, no task_id needed
await client.interrupt()

# stop_task() - stops specific subtask, requires task_id
await client.stop_task(task_id)
```

**Decision**: Use `interrupt()` exclusively. It stops the entire request without needing to track `task_id`.

**Migration**:
- `interrupt_session()` → unchanged (uses `interrupt()`)
- `reset_session()` → change from `stop_task(task_id)` to `interrupt()`

### File Changes

#### Deleted Files (~1500 lines total)

| File | Lines | Reason |
|------|-------|--------|
| `xbot/agent/state/session_state_adapter.py` | ~480 | Dual-write adapter removed |
| `xbot/agent/state/coordinator.py` | ~500 | Coordinator removed |
| `xbot/agent/state/transaction.py` | ~200 | Transaction support removed |
| `xbot/agent/state/checker.py` | ~300 | Simplified consistency checking |

#### New Files (~300 lines)

| File | Lines | Description |
|------|-------|-------------|
| `xbot/agent/state/session_manager.py` | ~200 | New unified manager |
| `tests/test_session_manager.py` | ~100 | Unit tests |

#### Modified Files

| File | Changes |
|------|---------|
| `xbot/agent/backends/claude_sdk_backend.py` | Remove 11 dicts, use `session_manager` directly |
| `xbot/agent/runtime.py` | Use `session_manager` instead of `_session_store` + `_state_coordinator` |
| `xbot/agent/state/__init__.py` | Export `SessionManager`, `SessionState`, `SessionPhase` |
| `xbot/agent/state/machine.py` | Merge into `session_manager.py` or keep minimal |

## Migration Strategy

### Phase 1: Preparation (1-2 days)

1. Add unit tests for current `SessionStateAdapter` critical paths
2. Add unit tests for `SessionStateCoordinator`
3. Create `xbot/agent/state/session_manager.py` with new implementation
4. Write unit tests for `SessionManager`

### Phase 2: Feature Flag (2-3 days)

1. Add `use_new_session_manager: bool` to config
2. Modify Runtime to use either old or new implementation based on flag
3. Deploy with flag disabled (old implementation active)
4. Enable flag in staging environment
5. Monitor for issues

### Phase 3: Switch Over (1 day)

1. Enable flag in production
2. Monitor for 24 hours
3. If issues detected, disable flag (instant rollback)
4. If stable, proceed to Phase 4

### Phase 4: Cleanup (1 day)

1. Remove feature flag
2. Delete old files:
   - `session_state_adapter.py`
   - `coordinator.py`
   - `transaction.py`
   - `checker.py`
3. Remove 11 legacy dicts from Backend
4. Update documentation

### Rollback Plan

1. **Instant rollback**: Disable feature flag (one config change)
2. **No data migration**: SDK manages persistence, no data compatibility issues
3. **Git revert**: If needed, revert to pre-migration commit

## Risk Assessment

### High Risk

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Notification routing fails | Medium | High | Thoroughly test `resolve_routing()` with both session_key and sdk_session_id |
| Concurrent requests mix messages | Low | High | Phase check at request entry; reject if not IDLE |
| Missing test coverage | Medium | High | Add tests before migration |

### Medium Risk

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Backend initialization timing | Medium | Medium | Ensure `initialize()` sets session_manager reference before use |
| Task cancellation behavior changes | Low | Medium | Preserve `register_task` / `cancel_all_tasks` semantics |
| Client pool behavior changes | Low | Medium | Test TTL cleanup and LRU eviction |

### Low Risk

| Risk | Probability | Impact |
|------|-------------|--------|
| Model/skills_version no longer tracked | Low | Low | Already in SDK options |
| Commands no longer tracked | Low | Low | SDK manages internally |
| Stats format changes | Low | Low | Non-critical feature |

## Testing Requirements

### Unit Tests

- `SessionManager.get_or_create()` creates and retrieves correctly
- `SessionManager.set_sdk_session_id()` updates both session and index
- `SessionManager.resolve_routing()` works with session_key and sdk_session_id
- `SessionManager.start_request()` rejects when not IDLE
- `SessionManager.end_request()` sets correct phase
- `SessionManager.cancel_all_tasks()` cancels active tasks
- `SessionManager.list_stale_sessions()` returns correct sessions

### Integration Tests

- Full message flow: receive → process → respond
- Concurrent message handling (second message rejected when first is running)
- SDK notification → reply routing
- Session cleanup on timeout
- Client pool eviction

### Manual Tests

- Slack: Send message, receive reply
- Feishu: Send message, receive reply
- Telegram: Send message, receive reply
- Multi-user: Multiple users send messages simultaneously
- Long-running task: Cancel with interrupt
- Session fork via SDK API

## Success Criteria

1. **Functional parity**: All existing features work identically
2. **No message routing failures**: All SDK notifications reach correct targets
3. **Concurrency safe**: No message interleaving
4. **Code reduction**: ~1200 lines removed
5. **Test coverage**: >90% coverage on `SessionManager`
6. **No regressions**: All existing tests pass

## Timeline

| Phase | Duration | Cumulative |
|-------|----------|------------|
| Phase 1: Preparation | 1-2 days | 1-2 days |
| Phase 2: Feature Flag | 2-3 days | 3-5 days |
| Phase 3: Switch Over | 1 day | 4-6 days |
| Phase 4: Cleanup | 1 day | 5-7 days |

**Total estimated effort**: 5-7 days

## References

- SDK capability test results (see test output from 2026-04-06)
- Current architecture: `xbot/agent/state/` directory
- SDK documentation: `claude-agent-sdk` Python package

## Appendix: Removed Fields Justification

| Field | Reason for Removal |
|-------|-------------------|
| `model` | SDK options already track model; client recreation is acceptable |
| `skills_version` | SDK options track skills; no need for separate tracking |
| `commands` | SDK manages command state internally |
| `persistent_session` | SDK handles persistence via JSONL files |
| `current_task_id` | Use `client.interrupt()` instead of `stop_task(task_id)` |
| `current_request_id` | SDK manages request/response correlation |
| `previous_phase` | Simplified state machine doesn't need rollback |
| `transition_count` | Debug-only, not needed for core functionality |

## Appendix: Retained Fields Justification

| Field | Reason for Retention |
|-------|---------------------|
| `process_handle` / `client_pid` | Safety net for force kill when disconnect fails; prevents orphan processes |
| `tasks` (asyncio.Tasks) | Needed for session termination (cancel active asyncio tasks) |
| `lock` (per-session) | Concurrent request protection per session |
| `phase` | State machine for application-level flow control |