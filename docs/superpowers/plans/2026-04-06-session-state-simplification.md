# Session State Management Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 5-layer state management with single SessionManager class, removing ~1200 lines of code while maintaining functional parity.

**Architecture:** Single `SessionManager` class manages `SessionState` dataclass instances. Backend and Runtime use `session_manager` directly instead of `_state_coordinator` + `_session_store` + legacy dicts. Phase state machine enforces concurrent request protection.

**Tech Stack:** Python 3.11+, asyncio, dataclasses, claude-agent-sdk v0.1.56

---

## File Structure

### New Files

| File | Responsibility |
|------|----------------|
| `xbot/agent/state/session_manager.py` | Unified session state manager (single source of truth) |
| `tests/agent/state/test_session_manager.py` | Unit tests for SessionManager |

### Modified Files

| File | Changes |
|------|---------|
| `xbot/agent/backends/claude_sdk_backend.py` | Remove 11 legacy dicts, use `session_manager` |
| `xbot/agent/runtime.py` | Use `session_manager` instead of `_session_store` + `_state_coordinator` |
| `xbot/agent/state/__init__.py` | Export `SessionManager`, `SessionState` (from machine.py) |
| `xbot/agent/state/machine.py` | Keep `SessionPhase`, `SessionState` dataclass; remove `SessionStateMachine` class |

### Deleted Files (Phase 4 - after feature flag stabilization)

| File | Lines |
|------|-------|
| `xbot/agent/state/session_state_adapter.py` | ~480 |
| `xbot/agent/state/coordinator.py` | ~500 |
| `xbot/agent/state/transaction.py` | ~200 |
| `xbot/agent/state/checker.py` | ~300 |
| `xbot/agent/state/store.py` | ~615 |
| `xbot/agent/state/snapshot.py` | ~135 |
| `xbot/agent/state/context_mapping.py` | ~313 |

---

## Phase 1: Create SessionManager

### Task 1: Create SessionState Dataclass (in machine.py)

**Files:**
- Modify: `xbot/agent/state/machine.py:1-100`

- [ ] **Step 1: Review current SessionState in machine.py**

Read `xbot/agent/state/machine.py` to understand the existing `SessionState` dataclass structure.

- [ ] **Step 2: Update SessionState dataclass**

Replace the existing `SessionState` dataclass with the simplified version:

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

Add imports at top of file:
```python
import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient
```

- [ ] **Step 3: Run existing tests to verify no breaking changes**

Run: `pytest tests/test_state_machine.py -v`
Expected: PASS (SessionPhase enum unchanged, dataclass fields compatible)

- [ ] **Step 4: Commit**

```bash
git add xbot/agent/state/machine.py
git commit -m "refactor(state): simplify SessionState dataclass for new SessionManager"
```

---

### Task 2: Create SessionManager Class

**Files:**
- Create: `xbot/agent/state/session_manager.py`

- [ ] **Step 1: Write initial test for SessionManager.get_or_create**

```python
# tests/agent/state/test_session_manager.py
import pytest
from xbot.agent.state.session_manager import SessionManager
from xbot.agent.state.machine import SessionPhase

@pytest.mark.asyncio
async def test_get_or_create_creates_new_session():
    """Test that get_or_create creates a new session when it doesn't exist."""
    manager = SessionManager()
    state = manager.get_or_create("slack:C12345")

    assert state.session_key == "slack:C12345"
    assert state.phase == SessionPhase.IDLE
    assert state.channel == ""
    assert state.chat_id == ""

@pytest.mark.asyncio
async def test_get_or_create_retrieves_existing_session():
    """Test that get_or_create retrieves existing session."""
    manager = SessionManager()
    state1 = manager.get_or_create("slack:C12345")
    state1.channel = "slack"
    state1.chat_id = "C12345"

    state2 = manager.get_or_create("slack:C12345")
    assert state2 is state1
    assert state2.channel == "slack"
    assert state2.chat_id == "C12345"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent/state/test_session_manager.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'xbot.agent.state.session_manager'"

- [ ] **Step 3: Create SessionManager with lifecycle methods**

```python
# xbot/agent/state/session_manager.py
"""Unified session state management - single source of truth."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from xbot.agent.state.machine import SessionPhase, SessionState
from xbot.logging import get_logger

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

logger = get_logger(__name__)


class SessionManager:
    """Unified session state management.

    Replaces the 5-layer state management system:
    - StateMachine → Store → Adapter → legacy dicts → Coordinator

    Only manages what SDK doesn't:
    - Connection pooling (client instances)
    - Request routing (channel/chat_id)
    - Concurrency protection (phase state machine)
    - Task lifecycle (asyncio.Task tracking)
    """

    def __init__(self):
        self._sessions: dict[str, SessionState] = {}
        self._sdk_index: dict[str, str] = {}  # sdk_session_id -> session_key
        self._global_lock = asyncio.Lock()

    # === Lifecycle ===

    def get(self, session_key: str) -> SessionState | None:
        """Get session state by session_key, or None if not found."""
        return self._sessions.get(session_key)

    def get_or_create(self, session_key: str) -> SessionState:
        """Get existing session or create new one with defaults."""
        if session_key not in self._sessions:
            self._sessions[session_key] = SessionState(session_key=session_key)
        return self._sessions[session_key]

    def get_by_sdk_id(self, sdk_session_id: str) -> SessionState | None:
        """Get session state by SDK session UUID."""
        session_key = self._sdk_index.get(sdk_session_id)
        if session_key is None:
            return None
        return self._sessions.get(session_key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agent/state/test_session_manager.py::test_get_or_create_creates_new_session -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/state/session_manager.py tests/agent/state/test_session_manager.py
git commit -m "feat(state): add SessionManager with get_or_create lifecycle methods"
```

---

### Task 3: Add SDK Session ID Management

**Files:**
- Modify: `xbot/agent/state/session_manager.py`
- Modify: `tests/agent/state/test_session_manager.py`

- [ ] **Step 1: Write test for SDK session ID mapping**

```python
# tests/agent/state/test_session_manager.py (add to existing file)

@pytest.mark.asyncio
async def test_set_sdk_session_id_creates_mapping():
    """Test that set_sdk_session_id creates bidirectional mapping."""
    manager = SessionManager()
    state = manager.get_or_create("slack:C12345")

    manager.set_sdk_session_id("slack:C12345", "sdk-uuid-abc-123")

    assert state.sdk_session_id == "sdk-uuid-abc-123"
    assert manager.get_by_sdk_id("sdk-uuid-abc-123") is state

@pytest.mark.asyncio
async def test_set_sdk_session_id_updates_mapping():
    """Test that set_sdk_session_id updates existing mapping."""
    manager = SessionManager()
    state = manager.get_or_create("slack:C12345")
    manager.set_sdk_session_id("slack:C12345", "sdk-uuid-old")

    # Update to new SDK session ID
    manager.set_sdk_session_id("slack:C12345", "sdk-uuid-new")

    assert state.sdk_session_id == "sdk-uuid-new"
    assert manager.get_by_sdk_id("sdk-uuid-old") is None  # Old mapping removed
    assert manager.get_by_sdk_id("sdk-uuid-new") is state
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent/state/test_session_manager.py::test_set_sdk_session_id_creates_mapping -v`
Expected: FAIL with "AttributeError: 'SessionManager' object has no attribute 'set_sdk_session_id'"

- [ ] **Step 3: Implement SDK session ID methods**

```python
# xbot/agent/state/session_manager.py (add to SessionManager class)

    # === SDK Session ID ===

    def set_sdk_session_id(self, session_key: str, sdk_id: str | None) -> None:
        """Set SDK session UUID and update bidirectional mapping.

        - If sdk_id is None, clears the mapping
        - If session already has sdk_id, removes old mapping before adding new
        """
        state = self.get(session_key)
        if state is None:
            logger.warning(f"set_sdk_session_id: session {session_key} not found")
            return

        # Remove old mapping if exists
        if state.sdk_session_id and state.sdk_session_id in self._sdk_index:
            del self._sdk_index[state.sdk_session_id]

        # Set new mapping
        state.sdk_session_id = sdk_id
        if sdk_id:
            self._sdk_index[sdk_id] = session_key
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agent/state/test_session_manager.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/state/session_manager.py tests/agent/state/test_session_manager.py
git commit -m "feat(state): add SDK session ID mapping to SessionManager"
```

---

### Task 4: Add Routing Methods

**Files:**
- Modify: `xbot/agent/state/session_manager.py`
- Modify: `tests/agent/state/test_session_manager.py`

- [ ] **Step 1: Write test for routing**

```python
# tests/agent/state/test_session_manager.py (add to existing file)

@pytest.mark.asyncio
async def test_set_routing():
    """Test that set_routing updates channel and chat_id."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")

    manager.set_routing("slack:C12345", "slack", "C12345")

    state = manager.get("slack:C12345")
    assert state.channel == "slack"
    assert state.chat_id == "C12345"

@pytest.mark.asyncio
async def test_get_routing():
    """Test that get_routing returns channel and chat_id."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.set_routing("slack:C12345", "slack", "C12345")

    routing = manager.get_routing("slack:C12345")
    assert routing == ("slack", "C12345")

@pytest.mark.asyncio
async def test_get_routing_returns_none_for_unknown():
    """Test that get_routing returns None for unknown session."""
    manager = SessionManager()
    routing = manager.get_routing("unknown:session")
    assert routing is None

@pytest.mark.asyncio
async def test_resolve_routing_by_session_key():
    """Test resolve_routing accepts session_key."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.set_routing("slack:C12345", "slack", "C12345")

    result = manager.resolve_routing("slack:C12345")
    assert result == ("slack:C12345", "slack", "C12345")

@pytest.mark.asyncio
async def test_resolve_routing_by_sdk_session_id():
    """Test resolve_routing accepts sdk_session_id."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.set_routing("slack:C12345", "slack", "C12345")
    manager.set_sdk_session_id("slack:C12345", "sdk-uuid-abc")

    result = manager.resolve_routing("sdk-uuid-abc")
    assert result == ("slack:C12345", "slack", "C12345")

@pytest.mark.asyncio
async def test_resolve_routing_returns_none_for_unknown():
    """Test resolve_routing returns None for unknown identifier."""
    manager = SessionManager()
    result = manager.resolve_routing("unknown-id")
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent/state/test_session_manager.py::test_set_routing -v`
Expected: FAIL with "AttributeError: 'SessionManager' object has no attribute 'set_routing'"

- [ ] **Step 3: Implement routing methods**

```python
# xbot/agent/state/session_manager.py (add to SessionManager class)

    # === Routing ===

    def set_routing(self, session_key: str, channel: str, chat_id: str) -> None:
        """Set channel and chat_id for routing."""
        state = self.get_or_create(session_key)
        state.channel = channel
        state.chat_id = chat_id

    def get_routing(self, session_key: str) -> tuple[str, str] | None:
        """Get channel and chat_id, or None if session not found."""
        state = self.get(session_key)
        if state is None:
            return None
        return (state.channel, state.chat_id)

    def resolve_routing(self, identifier: str) -> tuple[str, str, str] | None:
        """Resolve routing from either session_key or sdk_session_id.

        Returns: (session_key, channel, chat_id) or None
        """
        # Try as session_key first
        state = self.get(identifier)
        if state:
            return (state.session_key, state.channel, state.chat_id)

        # Try as sdk_session_id
        state = self.get_by_sdk_id(identifier)
        if state:
            return (state.session_key, state.channel, state.chat_id)

        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agent/state/test_session_manager.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/state/session_manager.py tests/agent/state/test_session_manager.py
git commit -m "feat(state): add routing methods to SessionManager"
```

---

### Task 5: Add Concurrency (Phase) Methods

**Files:**
- Modify: `xbot/agent/state/session_manager.py`
- Modify: `tests/agent/state/test_session_manager.py`

- [ ] **Step 1: Write test for phase/concurrency control**

```python
# tests/agent/state/test_session_manager.py (add to existing file)

@pytest.mark.asyncio
async def test_can_start_request_returns_true_for_idle():
    """Test can_start_request returns True for IDLE phase."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")

    assert manager.can_start_request("slack:C12345") is True

@pytest.mark.asyncio
async def test_can_start_request_returns_false_for_running():
    """Test can_start_request returns False for RUNNING phase."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.start_request("slack:C12345")

    assert manager.can_start_request("slack:C12345") is False

@pytest.mark.asyncio
async def test_start_request_transitions_to_running():
    """Test start_request transitions phase to RUNNING."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")

    result = manager.start_request("slack:C12345")
    assert result is True

    state = manager.get("slack:C12345")
    assert state.phase == SessionPhase.RUNNING

@pytest.mark.asyncio
async def test_start_request_returns_false_when_not_idle():
    """Test start_request returns False when phase is not IDLE."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.start_request("slack:C12345")  # Phase = RUNNING

    result = manager.start_request("slack:C12345")
    assert result is False

@pytest.mark.asyncio
async def test_end_request_transitions_to_idle():
    """Test end_request transitions phase back to IDLE."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.start_request("slack:C12345")

    manager.end_request("slack:C12345")

    state = manager.get("slack:C12345")
    assert state.phase == SessionPhase.IDLE

@pytest.mark.asyncio
async def test_end_request_can_set_custom_phase():
    """Test end_request can set custom phase (e.g., ERROR)."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.start_request("slack:C12345")

    manager.end_request("slack:C12345", SessionPhase.ERROR)

    state = manager.get("slack:C12345")
    assert state.phase == SessionPhase.ERROR
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent/state/test_session_manager.py::test_can_start_request_returns_true_for_idle -v`
Expected: FAIL with "AttributeError: 'SessionManager' object has no attribute 'can_start_request'"

- [ ] **Step 3: Implement concurrency methods**

```python
# xbot/agent/state/session_manager.py (add to SessionManager class)

    # === Concurrency ===

    def can_start_request(self, session_key: str) -> bool:
        """Check if a new request can be started (phase must be IDLE)."""
        state = self.get(session_key)
        if state is None:
            return True  # New session can start
        return state.phase == SessionPhase.IDLE

    def start_request(self, session_key: str) -> bool:
        """Transition to RUNNING phase. Returns False if not IDLE."""
        state = self.get_or_create(session_key)
        if state.phase != SessionPhase.IDLE:
            logger.warning(
                f"start_request: session {session_key} not IDLE (phase={state.phase})"
            )
            return False
        state.phase = SessionPhase.RUNNING
        state.last_active = time.time()
        return True

    def end_request(self, session_key: str, phase: SessionPhase = SessionPhase.IDLE) -> None:
        """Transition to specified phase after request completes."""
        state = self.get(session_key)
        if state is None:
            logger.warning(f"end_request: session {session_key} not found")
            return
        state.phase = phase
        state.last_active = time.time()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agent/state/test_session_manager.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/state/session_manager.py tests/agent/state/test_session_manager.py
git commit -m "feat(state): add phase/concurrency control to SessionManager"
```

---

### Task 6: Add Connection Methods

**Files:**
- Modify: `xbot/agent/state/session_manager.py`
- Modify: `tests/agent/state/test_session_manager.py`

- [ ] **Step 1: Write test for connection management**

```python
# tests/agent/state/test_session_manager.py (add to existing file)
from unittest.mock import MagicMock

@pytest.mark.asyncio
async def test_set_client():
    """Test set_client stores client in session state."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")

    mock_client = MagicMock()
    manager.set_client("slack:C12345", mock_client)

    state = manager.get("slack:C12345")
    assert state.client is mock_client

@pytest.mark.asyncio
async def test_get_client():
    """Test get_client retrieves client."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")

    mock_client = MagicMock()
    manager.set_client("slack:C12345", mock_client)

    client = manager.get_client("slack:C12345")
    assert client is mock_client

@pytest.mark.asyncio
async def test_get_client_returns_none_for_unknown():
    """Test get_client returns None for unknown session."""
    manager = SessionManager()
    client = manager.get_client("unknown:session")
    assert client is None

@pytest.mark.asyncio
async def test_has_client():
    """Test has_client checks if session has client."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")

    assert manager.has_client("slack:C12345") is False

    mock_client = MagicMock()
    manager.set_client("slack:C12345", mock_client)

    assert manager.has_client("slack:C12345") is True

@pytest.mark.asyncio
async def test_list_client_sessions():
    """Test list_client_sessions returns sessions with clients."""
    manager = SessionManager()

    # Create sessions, some with clients
    manager.get_or_create("slack:C1")
    manager.get_or_create("slack:C2")
    manager.get_or_create("slack:C3")

    mock_client = MagicMock()
    manager.set_client("slack:C1", mock_client)
    manager.set_client("slack:C2", mock_client)

    sessions = manager.list_client_sessions()
    assert set(sessions) == {"slack:C1", "slack:C2"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent/state/test_session_manager.py::test_set_client -v`
Expected: FAIL with "AttributeError: 'SessionManager' object has no attribute 'set_client'"

- [ ] **Step 3: Implement connection methods**

```python
# xbot/agent/state/session_manager.py (add to SessionManager class)

    # === Connection ===

    def set_client(self, session_key: str, client: ClaudeSDKClient) -> None:
        """Set SDK client for session."""
        state = self.get_or_create(session_key)
        state.client = client
        state.last_active = time.time()

    def get_client(self, session_key: str) -> ClaudeSDKClient | None:
        """Get SDK client for session."""
        state = self.get(session_key)
        if state is None:
            return None
        return state.client

    def has_client(self, session_key: str) -> bool:
        """Check if session has an active client."""
        state = self.get(session_key)
        return state is not None and state.client is not None

    def list_client_sessions(self) -> list[str]:
        """List all sessions that have active clients."""
        return [
            key for key, state in self._sessions.items()
            if state.client is not None
        ]

    def set_process_info(
        self,
        session_key: str,
        pid: int | None,
        handle: Any | None
    ) -> None:
        """Set process tracking info for force kill capability."""
        state = self.get(session_key)
        if state is None:
            return
        state.client_pid = pid
        state.process_handle = handle
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agent/state/test_session_manager.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/state/session_manager.py tests/agent/state/test_session_manager.py
git commit -m "feat(state): add connection management to SessionManager"
```

---

### Task 7: Add Task Tracking Methods

**Files:**
- Modify: `xbot/agent/state/session_manager.py`
- Modify: `tests/agent/state/test_session_manager.py`

- [ ] **Step 1: Write test for task tracking**

```python
# tests/agent/state/test_session_manager.py (add to existing file)
import asyncio

@pytest.mark.asyncio
async def test_register_task():
    """Test register_task adds task to session."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")

    async def dummy_task():
        await asyncio.sleep(1)

    task = asyncio.create_task(dummy_task())
    manager.register_task("slack:C12345", task)

    state = manager.get("slack:C12345")
    assert task in state.tasks

    # Clean up
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

@pytest.mark.asyncio
async def test_get_active_tasks():
    """Test get_active_tasks returns session tasks."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")

    async def dummy_task():
        await asyncio.sleep(1)

    task1 = asyncio.create_task(dummy_task())
    task2 = asyncio.create_task(dummy_task())
    manager.register_task("slack:C12345", task1)
    manager.register_task("slack:C12345", task2)

    tasks = manager.get_active_tasks("slack:C12345")
    assert task1 in tasks
    assert task2 in tasks

    # Clean up
    for t in [task1, task2]:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

@pytest.mark.asyncio
async def test_cancel_all_tasks():
    """Test cancel_all_tasks cancels and clears session tasks."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")

    async def dummy_task():
        await asyncio.sleep(10)

    task1 = asyncio.create_task(dummy_task())
    task2 = asyncio.create_task(dummy_task())
    manager.register_task("slack:C12345", task1)
    manager.register_task("slack:C12345", task2)

    # Cancel all
    count = await manager.cancel_all_tasks("slack:C12345")
    assert count == 2

    # Verify tasks are cancelled
    assert task1.cancelled() or task1.done()
    assert task2.cancelled() or task2.done()

    # Verify tasks list is cleared
    state = manager.get("slack:C12345")
    assert len(state.tasks) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent/state/test_session_manager.py::test_register_task -v`
Expected: FAIL with "AttributeError: 'SessionManager' object has no attribute 'register_task'"

- [ ] **Step 3: Implement task tracking methods**

```python
# xbot/agent/state/session_manager.py (add to SessionManager class)

    # === Tasks ===

    def register_task(self, session_key: str, task: asyncio.Task) -> None:
        """Register an asyncio.Task for tracking.

        Used for session termination cleanup - cancel active tasks
        when session ends or is interrupted.
        """
        state = self.get(session_key)
        if state is None:
            logger.warning(f"register_task: session {session_key} not found")
            return
        state.tasks.append(task)

    def get_active_tasks(self, session_key: str) -> list[asyncio.Task]:
        """Get all active asyncio.Tasks for session."""
        state = self.get(session_key)
        if state is None:
            return []
        return list(state.tasks)

    async def cancel_all_tasks(self, session_key: str) -> int:
        """Cancel all active tasks for session.

        Returns: Number of tasks cancelled
        """
        state = self.get(session_key)
        if state is None:
            return 0

        count = 0
        for task in state.tasks:
            if not task.done():
                task.cancel()
                count += 1

        # Clear tasks list
        state.tasks = []
        return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agent/state/test_session_manager.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/state/session_manager.py tests/agent/state/test_session_manager.py
git commit -m "feat(state): add asyncio.Task tracking to SessionManager"
```

---

### Task 8: Add Cleanup Methods

**Files:**
- Modify: `xbot/agent/state/session_manager.py`
- Modify: `tests/agent/state/test_session_manager.py`

- [ ] **Step 1: Write test for cleanup**

```python
# tests/agent/state/test_session_manager.py (add to existing file)

@pytest.mark.asyncio
async def test_cleanup_session():
    """Test cleanup_session removes session and mappings."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")
    manager.set_sdk_session_id("slack:C12345", "sdk-uuid-abc")

    await manager.cleanup_session("slack:C12345")

    assert manager.get("slack:C12345") is None
    assert manager.get_by_sdk_id("sdk-uuid-abc") is None

@pytest.mark.asyncio
async def test_cleanup_session_cancels_tasks():
    """Test cleanup_session cancels active tasks."""
    manager = SessionManager()
    manager.get_or_create("slack:C12345")

    async def dummy_task():
        await asyncio.sleep(10)

    task = asyncio.create_task(dummy_task())
    manager.register_task("slack:C12345", task)

    await manager.cleanup_session("slack:C12345")

    assert task.cancelled() or task.done()
    assert manager.get("slack:C12345") is None

@pytest.mark.asyncio
async def test_list_stale_sessions():
    """Test list_stale_sessions returns sessions past TTL."""
    manager = SessionManager()

    # Create sessions with different last_active times
    state1 = manager.get_or_create("slack:C1")
    state2 = manager.get_or_create("slack:C2")
    state3 = manager.get_or_create("slack:C3")

    # Manually set last_active for testing
    state1.last_active = time.time() - 3600  # 1 hour ago (stale)
    state2.last_active = time.time() - 60    # 1 min ago (fresh)
    state3.last_active = time.time()         # now (fresh)

    stale = manager.list_stale_sessions(ttl_seconds=300)  # 5 min TTL
    assert stale == ["slack:C1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agent/state/test_session_manager.py::test_cleanup_session -v`
Expected: FAIL with "AttributeError: 'SessionManager' object has no attribute 'cleanup_session'"

- [ ] **Step 3: Implement cleanup methods**

```python
# xbot/agent/state/session_manager.py (add to SessionManager class)

    # === Cleanup ===

    async def cleanup_session(self, session_key: str) -> None:
        """Clean up session: cancel tasks, remove from indices, delete state."""
        state = self.get(session_key)
        if state is None:
            return

        # Cancel active tasks
        await self.cancel_all_tasks(session_key)

        # Remove SDK index mapping
        if state.sdk_session_id and state.sdk_session_id in self._sdk_index:
            del self._sdk_index[state.sdk_session_id]

        # Delete session state
        del self._sessions[session_key]

        logger.info(f"cleanup_session: removed {session_key}")

    def list_stale_sessions(self, ttl_seconds: float) -> list[str]:
        """List sessions that have been inactive longer than TTL.

        Used for TTL cleanup of idle sessions.
        """
        now = time.time()
        stale = []
        for key, state in self._sessions.items():
            if state.last_active < now - ttl_seconds:
                stale.append(key)
        return stale
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agent/state/test_session_manager.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/state/session_manager.py tests/agent/state/test_session_manager.py
git commit -m "feat(state): add cleanup methods to SessionManager"
```

---

### Task 9: Update __init__.py Exports

**Files:**
- Modify: `xbot/agent/state/__init__.py`

- [ ] **Step 1: Read current __init__.py**

Run: `cat xbot/agent/state/__init__.py`

- [ ] **Step 2: Update exports**

```python
# xbot/agent/state/__init__.py
"""Session state management.

This module provides simplified session state management using SessionManager
as the single source of truth.
"""

from xbot.agent.state.machine import SessionPhase, SessionState
from xbot.agent.state.session_manager import SessionManager

__all__ = [
    "SessionManager",
    "SessionPhase",
    "SessionState",
]
```

- [ ] **Step 3: Verify imports work**

Run: `python -c "from xbot.agent.state import SessionManager, SessionPhase, SessionState; print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add xbot/agent/state/__init__.py
git commit -m "refactor(state): update __init__.py exports for SessionManager"
```

---

## Phase 2: Feature Flag Integration

### Task 10: Add Feature Flag to Config

**Files:**
- Modify: `xbot/config/schema.py`
- Create test if needed

- [ ] **Step 1: Read config schema**

Read `xbot/config/schema.py` to find the AgentsConfig class.

- [ ] **Step 2: Add feature flag field**

Add `use_new_session_manager: bool = False` to AgentsConfig:

```python
# xbot/config/schema.py (in AgentsConfig class)
    use_new_session_manager: bool = False  # Use simplified SessionManager
```

- [ ] **Step 3: Commit**

```bash
git add xbot/config/schema.py
git commit -m "feat(config): add use_new_session_manager feature flag"
```

---

### Task 11: Integrate SessionManager into Backend

**Files:**
- Modify: `xbot/agent/backends/claude_sdk_backend.py`

- [ ] **Step 1: Add SessionManager import and initialization**

```python
# xbot/agent/backends/claude_sdk_backend.py (imports section)
from xbot.agent.state.session_manager import SessionManager
from xbot.agent.state import SessionPhase

# In ClaudeSDKBackend.__init__ (around line 145)
        self.session_manager: SessionManager | None = None  # New unified manager
        self._use_new_state: bool = False  # Feature flag
```

- [ ] **Step 2: Add initialize() method update**

Find the `initialize()` method and add:

```python
# In ClaudeSDKBackend.initialize()
        self._use_new_state = context.config.agents.use_new_session_manager
        if self._use_new_state:
            self.session_manager = SessionManager()
            logger.info("Using new SessionManager for state management")
```

- [ ] **Step 3: Run existing backend tests**

Run: `pytest tests/agent/backends/ -v -k "not integration"`
Expected: PASS (no changes to behavior yet)

- [ ] **Step 4: Commit**

```bash
git add xbot/agent/backends/claude_sdk_backend.py
git commit -m "feat(backend): add SessionManager initialization with feature flag"
```

---

### Task 12: Integrate SessionManager into Runtime

**Files:**
- Modify: `xbot/agent/runtime.py`

- [ ] **Step 1: Add SessionManager import**

```python
# xbot/agent/runtime.py (imports section)
from xbot.agent.state.session_manager import SessionManager
```

- [ ] **Step 2: Add session_manager field to AgentRuntime**

```python
# In AgentRuntime.__init__ (around line 200)
        self.session_manager: SessionManager | None = None  # New unified manager
        self._use_new_state: bool = False  # Feature flag
```

- [ ] **Step 3: Add initialization in Runtime setup**

Find where Runtime initializes and add:

```python
# In AgentRuntime initialization
        self._use_new_state = self._config.agents.use_new_session_manager
        if self._use_new_state:
            self.session_manager = SessionManager()
            # Pass to backend
            if self._router and self._router._backends:
                for backend in self._router._backends.values():
                    if hasattr(backend, 'session_manager'):
                        backend.session_manager = self.session_manager
```

- [ ] **Step 4: Run existing runtime tests**

Run: `pytest tests/agent/runtime/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/runtime.py
git commit -m "feat(runtime): add SessionManager with feature flag integration"
```

---

### Task 13: Implement Concurrent Request Protection

**Files:**
- Modify: `xbot/agent/runtime.py`

- [ ] **Step 1: Find message handling code**

Grep for where inbound messages are dispatched.

- [ ] **Step 2: Add concurrent request check**

Add phase check before dispatching message:

```python
# In Runtime message handling (dispatch method)
        if self._use_new_state:
            # Check if session can accept new request
            if not self.session_manager.can_start_request(session_key):
                await self._bus.publish_outbound(OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="⏳ 正在处理上一个请求，请稍候再试。",
                ))
                return

            # Start request (transition to RUNNING)
            self.session_manager.start_request(session_key)
```

- [ ] **Step 3: Add end_request in completion handler**

```python
# In Runtime response completion handler
        if self._use_new_state:
            self.session_manager.end_request(session_key)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/agent/runtime/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/runtime.py
git commit -m "feat(runtime): add concurrent request protection with phase check"
```

---

## Phase 3: Backend Dict Migration (Staged)

### Task 14: Replace _clients dict with SessionManager

**Files:**
- Modify: `xbot/agent/backends/claude_sdk_backend.py`

- [ ] **Step 1: Find _clients usage**

Grep for `self._clients` in backend.

- [ ] **Step 2: Add dual-mode client access**

```python
# In ClaudeSDKBackend (create wrapper methods)
    def _get_client(self, session_key: str) -> ClaudeSDKClient | None:
        """Get client - dual mode for migration."""
        if self._use_new_state:
            return self.session_manager.get_client(session_key)
        return self._clients.get(session_key)

    def _set_client(self, session_key: str, client: ClaudeSDKClient) -> None:
        """Set client - dual mode for migration."""
        if self._use_new_state:
            self.session_manager.set_client(session_key, client)
        else:
            self._clients[session_key] = client
```

- [ ] **Step 3: Replace direct _clients access with wrapper calls**

Replace `self._clients[...]` with `self._get_client(...)` and `self._set_client(...)`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/agent/backends/ -v -k "not integration"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/backends/claude_sdk_backend.py
git commit -m "refactor(backend): replace _clients with SessionManager (dual mode)"
```

---

### Task 15: Replace _sdk_session_ids dict

**Files:**
- Modify: `xbot/agent/backends/claude_sdk_backend.py`

- [ ] **Step 1: Find _sdk_session_ids usage**

Grep for `self._sdk_session_ids`.

- [ ] **Step 2: Add dual-mode SDK session ID access**

```python
# In ClaudeSDKBackend
    def _get_sdk_session_id(self, session_key: str) -> str | None:
        """Get SDK session ID - dual mode."""
        if self._use_new_state:
            state = self.session_manager.get(session_key)
            return state.sdk_session_id if state else None
        return self._sdk_session_ids.get(session_key)

    def _set_sdk_session_id(self, session_key: str, sdk_id: str | None) -> None:
        """Set SDK session ID - dual mode."""
        if self._use_new_state:
            self.session_manager.set_sdk_session_id(session_key, sdk_id)
        else:
            if sdk_id:
                self._sdk_session_ids[session_key] = sdk_id
            elif session_key in self._sdk_session_ids:
                del self._sdk_session_ids[session_key]
```

- [ ] **Step 3: Replace direct access**

- [ ] **Step 4: Run tests**

Run: `pytest tests/agent/backends/ -v -k "not integration"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/backends/claude_sdk_backend.py
git commit -m "refactor(backend): replace _sdk_session_ids with SessionManager (dual mode)"
```

---

### Task 16: Replace _client_last_used dict

**Files:**
- Modify: `xbot/agent/backends/claude_sdk_backend.py`

- [ ] **Step 1: Find _client_last_used usage**

Grep for `self._client_last_used`.

- [ ] **Step 2: Note that SessionState.last_active handles this**

The `SessionState.last_active` field is updated automatically by `set_client()`, `start_request()`, `end_request()`. No separate dict needed.

- [ ] **Step 3: Add dual-mode last_active access**

```python
# In ClaudeSDKBackend
    def _get_last_active(self, session_key: str) -> float:
        """Get last active time - dual mode."""
        if self._use_new_state:
            state = self.session_manager.get(session_key)
            return state.last_active if state else 0.0
        return self._client_last_used.get(session_key, 0.0)

    def _update_last_active(self, session_key: str) -> None:
        """Update last active time - dual mode."""
        if self._use_new_state:
            state = self.session_manager.get(session_key)
            if state:
                state.last_active = time.time()
        else:
            self._client_last_used[session_key] = time.time()
```

- [ ] **Step 4: Replace direct access**

- [ ] **Step 5: Run tests**

Run: `pytest tests/agent/backends/ -v -k "not integration"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add xbot/agent/backends/claude_sdk_backend.py
git commit -m "refactor(backend): replace _client_last_used with SessionManager (dual mode)"
```

---

### Task 17: Remove _active_request_ids (Dead Code)

**Files:**
- Modify: `xbot/agent/backends/claude_sdk_backend.py`

- [ ] **Step 1: Find _active_request_ids usage**

Grep for `self._active_request_ids`.

- [ ] **Step 2: Remove all _active_request_ids references**

Based on spec: "UserMessage not sent by default (requires extra_args), _active_request_ids filtering is effectively dead code."

Delete:
- `self._active_request_ids: dict[str, str] = {}` initialization
- All reads/writes to `_active_request_ids`

- [ ] **Step 3: Run tests**

Run: `pytest tests/agent/backends/ -v -k "not integration"`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add xbot/agent/backends/claude_sdk_backend.py
git commit -m "refactor(backend): remove _active_request_ids (dead code)"
```

---

### Task 18: Replace _active_task_ids with Task Tracking

**Files:**
- Modify: `xbot/agent/backends/claude_sdk_backend.py`

- [ ] **Step 1: Find _active_task_ids usage**

Grep for `self._active_task_ids`.

- [ ] **Step 2: Note change: now tracking asyncio.Task, not SDK task_id**

Per spec: `interrupt()` replaces `stop_task(task_id)`. Now tracking asyncio.Tasks for session termination.

- [ ] **Step 3: Add dual-mode task registration**

```python
# In ClaudeSDKBackend
    def _register_asyncio_task(self, session_key: str, task: asyncio.Task) -> None:
        """Register asyncio task for tracking - dual mode."""
        if self._use_new_state:
            self.session_manager.register_task(session_key, task)
        else:
            # Old mode: just track in a list (create if needed)
            if not hasattr(self, '_asyncio_tasks'):
                self._asyncio_tasks: dict[str, list[asyncio.Task]] = {}
            if session_key not in self._asyncio_tasks:
                self._asyncio_tasks[session_key] = []
            self._asyncio_tasks[session_key].append(task)
```

- [ ] **Step 4: Update interrupt logic**

```python
# In ClaudeSDKBackend.interrupt_session()
        if self._use_new_state:
            # Use interrupt() - no task_id needed
            client = self.session_manager.get_client(session_key)
            if client:
                await client.interrupt()
            await self.session_manager.cancel_all_tasks(session_key)
        else:
            # Old mode: stop_task(task_id)
            client = self._get_client(session_key)
            task_id = self._active_task_ids.get(session_key)
            if client and task_id:
                await client.stop_task(task_id)
            # Clean up dict
            if session_key in self._active_task_ids:
                del self._active_task_ids[session_key]
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/agent/backends/ -v -k "not integration"`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add xbot/agent/backends/claude_sdk_backend.py
git commit -m "refactor(backend): replace _active_task_ids with asyncio.Task tracking"
```

---

### Task 19: Remove Remaining Legacy Dicts

**Files:**
- Modify: `xbot/agent/backends/claude_sdk_backend.py`

- [ ] **Step 1: Identify remaining dicts to remove**

Per spec, remove:
- `_client_models` → not worth tracking (client recreation acceptable)
- `_session_commands` → SDK manages internally
- `_client_skills_versions` → SDK options track this
- `_client_creation_futures` → not needed with simpler design

- [ ] **Step 2: Remove dict declarations and usage**

Remove each dict and all references. For `_client_creation_futures`, simplify client creation without future-based locking.

- [ ] **Step 3: Run tests**

Run: `pytest tests/agent/backends/ -v -k "not integration"`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add xbot/agent/backends/claude_sdk_backend.py
git commit -m "refactor(backend): remove remaining legacy dicts (_client_models, _session_commands, etc)"
```

---

## Phase 4: Enable Feature Flag and Cleanup

### Task 20: Enable Feature Flag by Default

**Files:**
- Modify: `xbot/config/schema.py`
- Modify: `xbot/config/default.yaml` (if exists)

- [ ] **Step 1: Change default to True**

```python
# xbot/config/schema.py
    use_new_session_manager: bool = True  # Use simplified SessionManager (enabled by default)
```

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ -v --ignore=tests/integration/`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add xbot/config/schema.py
git commit -m "feat(config): enable use_new_session_manager by default"
```

---

### Task 21: Remove Old State Files

**Files:**
- Delete: `xbot/agent/state/session_state_adapter.py`
- Delete: `xbot/agent/state/coordinator.py`
- Delete: `xbot/agent/state/transaction.py`
- Delete: `xbot/agent/state/checker.py`
- Delete: `xbot/agent/state/store.py`
- Delete: `xbot/agent/state/snapshot.py`
- Delete: `xbot/agent/state/context_mapping.py`

- [ ] **Step 1: Delete files**

```bash
rm xbot/agent/state/session_state_adapter.py
rm xbot/agent/state/coordinator.py
rm xbot/agent/state/transaction.py
rm xbot/agent/state/checker.py
rm xbot/agent/state/store.py
rm xbot/agent/state/snapshot.py
rm xbot/agent/state/context_mapping.py
```

- [ ] **Step 2: Update imports in runtime.py**

Remove imports for deleted modules:

```python
# Remove these imports from xbot/agent/runtime.py:
# from xbot.agent.state.store import SessionStore
# from xbot.agent.state.checker import StateConsistencyChecker
# from xbot.agent.state.coordinator import SessionStateCoordinator
```

- [ ] **Step 3: Update imports in backend**

Remove imports:

```python
# Remove from xbot/agent/backends/claude_sdk_backend.py:
# from xbot.agent.state.session_state_adapter import SessionStateAdapter
```

- [ ] **Step 4: Run all tests**

Run: `pytest tests/ -v --ignore=tests/integration/`
Expected: PASS (some old tests may need deletion)

- [ ] **Step 5: Delete old state tests**

```bash
rm tests/test_state_snapshot.py
rm tests/test_state_transaction.py
rm tests/test_state_checker.py
rm tests/test_state_coordinator.py
rm tests/test_session_state_adapter.py
rm tests/test_session_store_race.py
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(state): remove old state management files (~1200 lines removed)"
```

---

### Task 22: Remove Feature Flag (Final Cleanup)

**Files:**
- Modify: `xbot/config/schema.py`
- Modify: `xbot/agent/backends/claude_sdk_backend.py`
- Modify: `xbot/agent/runtime.py`

- [ ] **Step 1: Remove feature flag from config**

```python
# xbot/config/schema.py - remove:
#     use_new_session_manager: bool = True
```

- [ ] **Step 2: Remove dual-mode code in backend**

Remove all `if self._use_new_state` branches, keeping only the new code path.

- [ ] **Step 3: Remove dual-mode code in runtime**

Remove all `if self._use_new_state` branches.

- [ ] **Step 4: Remove remaining legacy dicts**

Remove all remaining legacy dict declarations in backend:
- `_clients: dict[str, ClaudeSDKClient] = {}` → use session_manager
- `_client_last_used: dict[str, float] = {}` → use session_manager
- `_sdk_session_ids: dict[str, str] = {}` → use session_manager
- `_active_task_ids: dict[str, str] = {}` → removed earlier
- `_active_request_ids: dict[str, str] = {}` → removed earlier
- `_session_commands: dict[str, list[str]] = {}` → removed earlier
- `_client_skills_versions: dict[str, str | None] = {}` → removed earlier
- `_client_creation_futures: dict[str, asyncio.Future] = {}` → removed earlier

- [ ] **Step 5: Run all tests**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "refactor(state): complete migration to SessionManager, remove feature flag"
```

---

## Self-Review Checklist

After completing all tasks:

- [ ] **Spec coverage**: Verify each spec requirement has a task
- [ ] **Placeholder scan**: No TBD/TODO in plan
- [ ] **Type consistency**: SessionState fields match across all uses
- [ ] **Test coverage**: >90% coverage on SessionManager
- [ ] **Functional parity**: All features work identically
- [ ] **Concurrency safe**: No message interleaving

---

## Test Plan Summary

| Category | Tests |
|----------|-------|
| Unit | SessionManager lifecycle, SDK ID mapping, routing, concurrency, connection, tasks, cleanup |
| Integration | Full message flow, concurrent rejection, SDK notification routing |
| Manual | Slack/Feishu/Telegram messaging, multi-user concurrent, interrupt cancellation |

---

## Rollback Procedure

1. Disable feature flag: `use_new_session_manager: false`
2. Git revert to pre-migration commit
3. No data migration required (SDK manages persistence)