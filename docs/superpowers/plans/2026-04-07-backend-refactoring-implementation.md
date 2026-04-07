# Claude SDK Backend Refactoring v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor claude_sdk_backend.py from 2559 lines to ~800 lines by extracting code into focused modules, each under 1000 lines.

**Architecture:** Extract client lifecycle management into ClientPool, process execution into ProcessExecutor, and session operations into SessionManager. The backend becomes a thin coordinator that delegates to specialized modules.

**Tech Stack:** Python 3.12, asyncio, pytest

---

## File Structure After Refactoring

```
xbot/agent/backends/
├── __init__.py                    (~30 lines)
├── claude_sdk_backend.py          (~800 lines)  ← Main coordinator
├── client_pool.py                 (~500 lines)  ← Client creation
├── client_cleanup.py              (~300 lines)  ← NEW: Client cleanup
├── process_executor.py            (~400 lines)  ← NEW: Process logic
├── session_manager.py             (~350 lines)  ← NEW: Session ops
├── client_lifecycle.py            (~240 lines)  ← Unchanged
├── message_converter.py           (~340 lines)  ← Unchanged
├── options_builder.py             (~700 lines)  ← Unchanged
├── multimodal.py                  (~380 lines)  ← Unchanged
├── error_recovery.py              (~300 lines)  ← Unchanged
├── auxiliary_llm.py               (~340 lines)  ← Unchanged
├── sdk_session_ops.py             (~300 lines)  ← Unchanged
└── session_state_adapter.py       (~130 lines)  ← Unchanged
```

---

## Task 1: Delete Legacy Methods

**Goal:** Remove duplicate `_legacy_*` methods that exist alongside ClientPool delegation.

**Files:**
- Modify: `xbot/agent/backends/claude_sdk_backend.py`

**Context:** The `_legacy_release_client` (147 lines) and `_legacy_get_or_create_client` (72 lines) are duplicates kept for backward compatibility. Tests should use ClientPool directly.

- [ ] **Step 1: Identify legacy methods to delete**

Run:
```bash
grep -n "async def _legacy_release_client\|async def _legacy_get_or_create_client" xbot/agent/backends/claude_sdk_backend.py
```

Expected output:
```
669:    async def _legacy_release_client(
1262:    async def _legacy_get_or_create_client(self, session_key: str) -> "ClaudeSDKClient":
```

- [ ] **Step 2: Find the end of _legacy_release_client**

Read lines 669-820 to find where `_legacy_release_client` ends (next method starts).

- [ ] **Step 3: Delete _legacy_release_client method**

Delete lines 669 to the line before the next method definition (approximately lines 669-816).

- [ ] **Step 4: Find and delete _legacy_get_or_create_client**

Read lines 1260-1340 to find the method boundaries, then delete it.

- [ ] **Step 5: Update release_client to remove legacy fallback**

Change the `release_client` method to not call `_legacy_release_client`:

```python
async def release_client(
    self,
    session_key: str,
    *,
    reason: str,
    preserve_sdk_context: bool | None = None,
) -> bool:
    """Release a client from the pool."""
    if self._client_pool is None:
        raise RuntimeError("Backend not initialized - client pool not available")

    return await self._client_pool.release_client(
        session_key,
        reason=reason,
        preserve_sdk_context=preserve_sdk_context,
        should_preserve_callback=self._should_preserve_sdk_context,
        on_cleanup_callback=self._shared_resources.get("on_backend_client_cleanup"),
    )
```

- [ ] **Step 6: Update _get_or_create_client to remove legacy fallback**

Change `_get_or_create_client` to not call `_legacy_get_or_create_client`:

```python
async def _get_or_create_client(self, session_key: str) -> "ClaudeSDKClient":
    """Get or create a Claude SDK client for the session."""
    if self._client_pool is None:
        raise RuntimeError("Backend not initialized - client pool not available")

    current_model = self._options_builder._get_model_name() if self._options_builder else None
    return await self._client_pool.get_or_create_client(session_key, current_model)
```

- [ ] **Step 7: Run tests to check what breaks**

Run:
```bash
pytest tests/test_claude_sdk_backend.py -v --tb=short 2>&1 | grep -E "PASSED|FAILED|ERROR" | head -30
```

Expected: Some tests may fail because they don't initialize ClientPool.

- [ ] **Step 8: Fix failing tests by initializing ClientPool**

For tests that fail, ensure they either:
1. Call `backend.initialize()` to set up ClientPool
2. Or mock `backend._client_pool` directly

- [ ] **Step 9: Verify tests pass**

Run:
```bash
pytest tests/test_claude_sdk_backend.py -v --tb=short
```

Expected: All tests pass.

- [ ] **Step 10: Commit**

```bash
git add xbot/agent/backends/claude_sdk_backend.py
git commit -m "refactor: remove legacy client methods

- Delete _legacy_release_client (-147 lines)
- Delete _legacy_get_or_create_client (-72 lines)
- Backend now requires ClientPool to be initialized"
```

---

## Task 2: Create ProcessExecutor Module

**Goal:** Extract process() message handling logic into a dedicated module with low coupling.

**Files:**
- Create: `xbot/agent/backends/process_executor.py`
- Modify: `xbot/agent/backends/claude_sdk_backend.py`
- Modify: `xbot/agent/backends/__init__.py`

- [ ] **Step 1: Identify code to extract**

Find the helper methods and message loop:
```bash
grep -n "async def _handle_sdk\|async def _receive_with_boundary\|async def _setup_process_context\|def _handle_session_recovery" xbot/agent/backends/claude_sdk_backend.py
```

- [ ] **Step 2: Create process_executor.py skeleton**

Create the file with the class structure:

```python
"""Process executor for Claude SDK backend.

This module handles the message processing loop, extracting the
complex logic from ClaudeSDKBackend for better maintainability.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator

from xbot.agent.backends.error_recovery import ErrorRecoveryHandler
from xbot.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from claude_agent_sdk.types import (
        ResultMessage,
        SystemMessage,
        TaskStartedMessage,
        TaskNotificationMessage,
        TaskProgressMessage,
        StreamEvent,
    )
    from xbot.agent.protocol import AgentContext, AgentResponse


@dataclass
class ProcessContext:
    """Context for process execution."""
    session_key: str
    channel: str
    chat_id: str
    session: Any
    prompt: str
    media: list[str] | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class ProcessResult:
    """Result from process execution."""
    final_content: str
    received_result: bool
    needs_compact_retry: bool
    is_payload_error: bool


class ProcessExecutor:
    """Executes the message processing loop for Claude SDK.

    This class is designed to be stateless - it receives all needed
    context through parameters and returns results for the caller to handle.
    """

    # Constants
    _MAX_STALE_DISCARD = 50

    def __init__(
        self,
        state_manager: Any,
        sessions: Any,
        message_converter: Any,
        multimodal_builder: Any,
    ):
        """Initialize the process executor.

        Args:
            state_manager: Session state manager
            sessions: Session manager
            message_converter: Message converter instance
            multimodal_builder: Multimodal builder instance
        """
        self._state_manager = state_manager
        self._sessions = sessions
        self._message_converter = message_converter
        self._multimodal_builder = multimodal_builder
```

- [ ] **Step 3: Add execute method skeleton**

```python
    async def execute(
        self,
        client: Any,
        context: ProcessContext,
        query_sent_at: float,
        payload_compact_attempted: bool = False,
    ) -> AsyncIterator[AgentResponse]:
        """Execute the message processing loop.

        Args:
            client: Claude SDK client
            context: Process context with session info
            query_sent_at: Timestamp when query was sent
            payload_compact_attempted: Whether compact was already attempted

        Yields:
            AgentResponse objects
        """
        # To be implemented in next steps
        pass
```

- [ ] **Step 4: Move _receive_with_boundary method**

Read lines 1614-1696 from `claude_sdk_backend.py` (the `_receive_with_boundary` method) and add it to `ProcessExecutor`.

- [ ] **Step 5: Move _handle_sdk_* methods**

Move the four helper methods:
- `_handle_sdk_init_message` (lines 1661-1693)
- `_handle_sdk_task_started` (lines 1695-1707)
- `_handle_sdk_terminal_notification` (lines 1709-1738)
- `_handle_sdk_result_message` (lines 1740-1764)

- [ ] **Step 6: Implement execute method with message loop**

Add the main loop logic:

```python
    async def execute(
        self,
        client: Any,
        context: ProcessContext,
        query_sent_at: float,
        payload_compact_attempted: bool = False,
    ) -> AsyncIterator[AgentResponse]:
        """Execute the message processing loop."""
        final_content = ""
        received_result = False
        stale_count = 0

        async for message in self._receive_with_boundary(client, context.session_key):
            is_terminal_result = isinstance(message, ResultMessage)

            # Handle each message type
            if isinstance(message, SystemMessage) and message.subtype == "init":
                async for resp in self._handle_init(message, context):
                    yield resp

            elif isinstance(message, TaskStartedMessage) and message.task_id:
                self._handle_task_started(message, context.session_key)

            elif isinstance(message, TaskProgressMessage):
                self._state_manager.touch(context.session_key)

            elif isinstance(message, TaskNotificationMessage) and message.status in {"completed", "failed", "stopped"}:
                should_skip = self._handle_terminal_notification(message, context.session_key)
                if should_skip:
                    continue

            elif is_terminal_result:
                self._handle_result_message(message, context)

            # Handle request-too-large
            if is_terminal_result and getattr(message, "is_error", False):
                if ErrorRecoveryHandler.is_request_too_large(str(message.result or "")):
                    if not payload_compact_attempted:
                        yield AgentResponse(
                            content="",
                            progress_texts=["📦 请求过大，正在压缩历史上下文后重试..."],
                        )
                        # Signal retry needed
                        return
                    else:
                        yield AgentResponse(
                            content="⚠️ 当前消息中的图片数据过大。",
                            finish_reason="error",
                        )
                        return

            # Convert and yield
            if self._message_converter:
                response = self._message_converter.convert(message)
                if response:
                    if response.is_delta and response.delta_content:
                        final_content += response.delta_content
                    elif response.content:
                        final_content = response.content
                    yield response

            if is_terminal_result:
                received_result = True
                break

        self._final_content = final_content
        self._received_result = received_result
```

- [ ] **Step 7: Update backend to use ProcessExecutor**

In `claude_sdk_backend.py`, add initialization:

```python
def __init__(self):
    # ... existing code ...
    self._process_executor: ProcessExecutor | None = None
```

And in `initialize()`:

```python
# Initialize process executor
self._process_executor = ProcessExecutor(
    state_manager=self._state_manager,
    sessions=self.sessions,
    message_converter=self._message_converter,
    multimodal_builder=self._multimodal_builder,
)
```

- [ ] **Step 8: Update process() to use executor**

Replace the inline message loop with:

```python
async for response in self._process_executor.execute(
    client=client,
    context=ProcessContext(
        session_key=context.session_key,
        channel=context.channel,
        chat_id=context.chat_id,
        session=session,
        prompt=prompt,
        media=context.media,
        metadata=context.metadata,
    ),
    query_sent_at=query_sent_at,
    payload_compact_attempted=payload_compact_attempted,
):
    yield response
```

- [ ] **Step 9: Update __init__.py**

Add `ProcessExecutor` to exports:

```python
from xbot.agent.backends.process_executor import ProcessExecutor, ProcessContext, ProcessResult

__all__ = [
    # ... existing exports ...
    "ProcessExecutor",
    "ProcessContext",
    "ProcessResult",
]
```

- [ ] **Step 10: Run tests**

```bash
pytest tests/test_claude_sdk_backend.py -v --tb=short
```

Expected: All tests pass.

- [ ] **Step 11: Commit**

```bash
git add xbot/agent/backends/
git commit -m "refactor: create ProcessExecutor for message handling

- Extract message processing loop into ProcessExecutor
- Move _receive_with_boundary and helper methods
- Reduce process() method complexity"
```

---

## Task 3: Create ClientCleanup Module

**Goal:** Extract client cleanup and disconnect logic from backend into a dedicated module.

**Files:**
- Create: `xbot/agent/backends/client_cleanup.py`
- Modify: `xbot/agent/backends/claude_sdk_backend.py`
- Modify: `xbot/agent/backends/__init__.py`

- [ ] **Step 1: Identify methods to move**

```bash
grep -n "async def _attempt_disconnect\|async def _disconnect_client\|async def _force_kill_process\|async def _cleanup_stale\|async def _evict_lru\|def _remove_client_state\|async def _finalize_detached" xbot/agent/backends/claude_sdk_backend.py
```

- [ ] **Step 2: Create client_cleanup.py**

```python
"""Client cleanup utilities for Claude SDK backend.

This module handles client disconnection, force kill, and cleanup
operations extracted from the main backend class.
"""

from __future__ import annotations

import asyncio
import signal
import time
from typing import TYPE_CHECKING, Any

from xbot.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient


class ClientCleanup:
    """Handles client cleanup operations.

    This class is responsible for:
    - Disconnecting clients with retries
    - Force killing client processes
    - Cleaning up stale clients
    """

    def __init__(
        self,
        disconnect_retries: int = 2,
        disconnect_timeout: float = 10.0,
        force_kill_enabled: bool = True,
    ):
        """Initialize the cleanup handler.

        Args:
            disconnect_retries: Number of disconnect retry attempts
            disconnect_timeout: Timeout for disconnect operation
            force_kill_enabled: Whether to force kill on disconnect failure
        """
        self._disconnect_retries = disconnect_retries
        self._disconnect_timeout = disconnect_timeout
        self._force_kill_enabled = force_kill_enabled

    async def disconnect_with_retry(
        self,
        client: "ClaudeSDKClient",
        session_key: str,
        reason: str = "",
    ) -> tuple[bool, int, str]:
        """Attempt to disconnect a client with retries.

        Args:
            client: Client to disconnect
            session_key: Session identifier for logging
            reason: Reason for disconnect (for logging)

        Returns:
            Tuple of (success, attempts, error_summary)
        """
        attempts = 0
        last_error = ""

        for attempt in range(self._disconnect_retries + 1):
            attempts += 1
            try:
                await asyncio.wait_for(
                    client.disconnect(),
                    timeout=self._disconnect_timeout,
                )
                return True, attempts, ""
            except asyncio.TimeoutError:
                last_error = "timeout"
                logger.warning(
                    f"[ClientCleanup] Disconnect timeout (attempt {attempt + 1}) for {session_key}"
                )
            except Exception as e:
                last_error = str(e)[:100]
                logger.warning(
                    f"[ClientCleanup] Disconnect error (attempt {attempt + 1}) for {session_key}: {e}"
                )

        return False, attempts, last_error

    async def force_kill_process(
        self,
        session_key: str,
        client: "ClaudeSDKClient | None" = None,
        pid: int | None = None,
    ) -> bool:
        """Force kill a client process.

        Args:
            session_key: Session identifier for logging
            client: Client to kill (extracts pid if available)
            pid: Process ID to kill (alternative to client)

        Returns:
            True if kill succeeded, False otherwise
        """
        if pid is None and client is not None:
            pid = getattr(client, "_pid", None)

        if pid is None:
            return False

        try:
            import os
            try:
                os.kill(pid, signal.SIGTERM)
                await asyncio.sleep(0.5)
                os.kill(pid, 0)  # Check if still alive
                os.kill(pid, signal.SIGKILL)  # Kill if still alive
            except ProcessLookupError:
                pass  # Already dead

            logger.info(f"[ClientCleanup] Force killed process {pid} for {session_key}")
            return True
        except Exception as e:
            logger.warning(f"[ClientCleanup] Force kill failed for {session_key}: {e}")
            return False

    async def cleanup_client(
        self,
        client: "ClaudeSDKClient",
        session_key: str,
        reason: str = "",
    ) -> str:
        """Clean up a client (disconnect or force kill).

        Args:
            client: Client to clean up
            session_key: Session identifier
            reason: Reason for cleanup

        Returns:
            Outcome: "disconnected", "killed", or "leaked"
        """
        success, _, _ = await self.disconnect_with_retry(client, session_key, reason)
        if success:
            return "disconnected"

        if self._force_kill_enabled:
            if await self.force_kill_process(session_key, client):
                return "killed"

        return "leaked"
```

- [ ] **Step 3: Move cleanup methods from backend**

Read and remove the following methods from `claude_sdk_backend.py`:
- `_attempt_disconnect_client`
- `_disconnect_client_with_timeout`
- `_force_kill_process`

- [ ] **Step 4: Update backend to use ClientCleanup**

In `__init__`:
```python
from xbot.agent.backends.client_cleanup import ClientCleanup
# ...
self._client_cleanup = ClientCleanup(
    disconnect_retries=self.disconnect_retries,
    disconnect_timeout=self.client_disconnect_timeout_seconds,
    force_kill_enabled=self.client_force_kill_enabled,
)
```

- [ ] **Step 5: Update __init__.py**

```python
from xbot.agent.backends.client_cleanup import ClientCleanup

__all__ = [
    # ... existing exports ...
    "ClientCleanup",
]
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_claude_sdk_backend.py -v --tb=short
```

- [ ] **Step 7: Commit**

```bash
git add xbot/agent/backends/
git commit -m "refactor: create ClientCleanup for disconnect operations

- Extract disconnect retry logic
- Extract force kill logic
- Reduce backend complexity"
```

---

## Task 4: Create SessionManager Module

**Goal:** Extract session management methods into a dedicated module.

**Files:**
- Create: `xbot/agent/backends/session_manager.py`
- Modify: `xbot/agent/backends/claude_sdk_backend.py`
- Modify: `xbot/agent/backends/__init__.py`

- [ ] **Step 1: Identify methods to move**

```bash
grep -n "async def compact_session\|async def reset_session\|async def interrupt_session\|async def delete_sdk_session\|async def fork_sdk_session\|async def list_sdk_sessions\|async def get_session_commands\|def _handle_session_recovery\|def _extract_slash_commands" xbot/agent/backends/claude_sdk_backend.py
```

- [ ] **Step 2: Create session_manager.py skeleton**

```python
"""Session management for Claude SDK backend.

This module handles session operations including:
- Session reset and interruption
- Context compaction
- SDK session management (delete, fork, list)
- Slash command discovery
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from xbot.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from xbot.session.manager import SessionManager as SessionStore
    from xbot.agent.state.session_manager import SessionManager as StateSessionManager


class SessionManager:
    """Manages session operations for Claude SDK backend.

    This class handles all session-related operations, extracting
    them from the main backend class for better organization.
    """

    def __init__(
        self,
        state_manager: StateSessionManager,
        sessions: SessionStore | None,
        memory_consolidator: Any = None,
    ):
        """Initialize the session manager.

        Args:
            state_manager: Session state manager
            sessions: Session store
            memory_consolidator: Optional memory consolidator
        """
        self._state_manager = state_manager
        self._sessions = sessions
        self._memory_consolidator = memory_consolidator
```

- [ ] **Step 3: Move session methods**

Read and move the following methods from `claude_sdk_backend.py`:
- `compact_session` (lines ~2298-2386)
- `reset_session` (lines ~2189-2246)
- `interrupt_session` (lines ~2248-2296)
- `_handle_session_recovery` (lines ~1727-1775)
- `_extract_slash_commands` (lines ~1471-1510)

- [ ] **Step 4: Add SDK session operations**

Move `delete_sdk_session`, `fork_sdk_session`, `list_sdk_sessions` to the new module.

- [ ] **Step 5: Update backend to use SessionManager**

```python
from xbot.agent.backends.session_manager import SessionManager

# In __init__:
self._session_manager: SessionManager | None = None

# In initialize():
self._session_manager = SessionManager(
    state_manager=self._state_manager,
    sessions=self.sessions,
    memory_consolidator=self.memory_consolidator,
)
```

- [ ] **Step 6: Create delegation methods in backend**

Replace the moved methods with thin delegation:

```python
async def compact_session(self, session_key: str) -> dict[str, Any]:
    """Compact session context."""
    if self._session_manager is None:
        raise RuntimeError("Session manager not initialized")
    return await self._session_manager.compact_session(session_key)

async def reset_session(self, session_key: str) -> None:
    """Reset a session."""
    if self._session_manager is None:
        raise RuntimeError("Session manager not initialized")
    await self._session_manager.reset_session(session_key)
```

- [ ] **Step 7: Update __init__.py**

```python
from xbot.agent.backends.session_manager import SessionManager

__all__ = [
    # ... existing exports ...
    "SessionManager",
]
```

- [ ] **Step 8: Run tests**

```bash
pytest tests/test_claude_sdk_backend.py -v --tb=short
```

- [ ] **Step 9: Commit**

```bash
git add xbot/agent/backends/
git commit -m "refactor: create SessionManager for session operations

- Move compact_session, reset_session, interrupt_session
- Move SDK session operations (delete, fork, list)
- Reduce backend complexity"
```

---

## Task 5: Final Verification and Line Count

**Goal:** Verify all modules are under 1000 lines and tests pass.

**Files:**
- Verify: `xbot/agent/backends/*.py`

- [ ] **Step 1: Count lines in all modules**

```bash
wc -l xbot/agent/backends/*.py | sort -n
```

Expected: All files < 1000 lines.

- [ ] **Step 2: Run full test suite**

```bash
pytest tests/test_claude_sdk_backend.py -v
```

Expected: All tests pass.

- [ ] **Step 3: Check for any remaining large methods**

```bash
awk '/^[[:space:]]*async def |^[[:space:]]*def / {
    if (current_func && NR - start_line > 50) {
        print current_func ": " NR - start_line " lines"
    }
    current_func = $0
    gsub(/^[[:space:]]+/, "", current_func)
    gsub(/\(.*/, "", current_func)
    start_line = NR
}
END {
    if (current_func && NR - start_line > 50) {
        print current_func ": " NR - start_line " lines"
    }
}' xbot/agent/backends/claude_sdk_backend.py | sort -t: -k2 -rn
```

Expected: No methods over 100 lines.

- [ ] **Step 4: Create summary**

```bash
echo "=== Final Line Counts ===" && wc -l xbot/agent/backends/*.py | sort -n
```

- [ ] **Step 5: Final commit**

```bash
git add xbot/agent/backends/
git commit -m "refactor: complete backend module restructuring

Final line counts:
- claude_sdk_backend.py: ~800 lines
- client_pool.py: ~500 lines
- client_cleanup.py: ~300 lines (new)
- process_executor.py: ~400 lines (new)
- session_manager.py: ~350 lines (new)

All modules under 1000 lines."
```

---

## Self-Review Checklist

- [ ] **Spec coverage:** All 5 tasks address the core goal (each module < 1000 lines)
- [ ] **No placeholders:** All steps have specific commands or code
- [ ] **Type consistency:** Method signatures match between files
- [ ] **Test coverage:** Tests updated to use new modules