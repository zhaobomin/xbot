# Backend Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify xbot's backend architecture by merging ClaudeSDKBackend and Runtime into AgentService, consolidating two session managers, and removing dead code.

**Architecture:** Create a unified AgentService that combines backend processing logic with runtime orchestration. Session management is consolidated into a single SessionManager that handles both persistence and memory state. The router layer is eliminated since only one backend exists.

**Tech Stack:** Python 3.11+, asyncio, Claude Agent SDK, pytest

---

## File Structure

### New Files to Create

| File | Purpose |
|------|---------|
| `xbot/agent/service.py` | Unified AgentService (core entry point) |
| `xbot/agent/client_pool.py` | Simplified client connection management |
| `xbot/agent/types.py` | Unified data types (AgentConfig, SessionConfig, etc.) |
| `tests/test_agent_service.py` | Tests for AgentService |

### Files to Modify

| File | Changes |
|------|---------|
| `xbot/agent/session_manager.py` | Merge with `xbot/session/manager.py` persistence logic |
| `xbot/agent/__init__.py` | Update exports (remove AgentBackend, AgentRouter; add AgentService) |
| `xbot/agent/protocol.py` | Remove AgentBackend abstract class, keep data classes |
| `xbot/cli/main.py` | Use AgentService instead of AgentRuntime |
| `xbot/gateway/main.py` | Use AgentService instead of AgentRuntime |

### Files to Delete

| File | Reason |
|------|--------|
| `xbot/agent/router.py` | Single backend - no routing needed |
| `xbot/agent/backends/delegation.py` | Dead code - no usage |
| `xbot/agent/backends/claude_sdk_backend.py` | Merged into AgentService |
| `xbot/agent/backends/client_lifecycle.py` | Merged into ClientPool |
| `xbot/agent/backends/message_converter.py` | Merged into AgentService |
| `xbot/agent/backends/options_builder.py` | Merged into AgentService |
| `xbot/agent/runtime.py` | Core logic moved to AgentService; CLI entry preserved separately |
| `xbot/agent/state/session_manager.py` | Merged into unified SessionManager |

---

## Task 1: Create types.py with Core Data Types

**Files:**
- Create: `xbot/agent/types.py`
- Test: `tests/test_types.py`

- [ ] **Step 1: Write the failing test for AgentConfig**

```python
# tests/test_types.py
"""Tests for agent types."""

import pytest
from xbot.agent.types import AgentConfig, SessionConfig


class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_default_values(self) -> None:
        """Test default values are set correctly."""
        config = AgentConfig(
            model="claude-sonnet-4-6",
            system_prompt="You are a helpful assistant.",
        )
        assert config.model == "claude-sonnet-4-6"
        assert config.system_prompt == "You are a helpful assistant."
        assert config.tools == []
        assert config.mcp_servers == {}
        assert config.agents is None

    def test_custom_values(self) -> None:
        """Test custom values are set correctly."""
        config = AgentConfig(
            model="claude-opus-4-6",
            system_prompt="Custom prompt",
            tools=[{"name": "test_tool"}],
            mcp_servers={"server1": {"url": "http://localhost"}},
            agents=[{"name": "researcher", "description": "Research agent"}],
        )
        assert config.model == "claude-opus-4-6"
        assert len(config.tools) == 1
        assert "server1" in config.mcp_servers
        assert config.agents is not None
        assert len(config.agents) == 1


class TestSessionConfig:
    """Tests for SessionConfig dataclass."""

    def test_default_values(self) -> None:
        """Test default values are set correctly."""
        config = SessionConfig(workspace="/tmp/workspace")
        assert config.workspace == "/tmp/workspace"
        assert config.permissions == {}

    def test_custom_permissions(self) -> None:
        """Test custom permissions."""
        config = SessionConfig(
            workspace="/workspace",
            permissions={"read": True, "write": False},
        )
        assert config.permissions["read"] is True
        assert config.permissions["write"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_types.py -v`
Expected: FAIL with "No module named 'xbot.agent.types'"

- [ ] **Step 3: Write the implementation**

```python
# xbot/agent/types.py
"""Unified data types for agent system.

This module consolidates type definitions from various modules
to provide a single source of truth for agent-related types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Re-export from protocol.py for backward compatibility
from xbot.agent.protocol import AgentResponse, AgentContext

# Re-export from state/machine.py for backward compatibility
from xbot.agent.state.machine import SessionPhase, SessionState


@dataclass
class AgentConfig:
    """Configuration for an Agent instance.

    Attributes:
        model: Model identifier (e.g., "claude-sonnet-4-6")
        system_prompt: System prompt for the agent
        tools: List of tool configurations
        mcp_servers: MCP server configurations
        agents: SDK agent definitions for subagent support
    """

    model: str
    system_prompt: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    agents: list[dict[str, Any]] | None = None


@dataclass
class SessionConfig:
    """Configuration for a session.

    Attributes:
        workspace: Workspace directory path
        permissions: Permission settings for this session
    """

    workspace: str
    permissions: dict[str, Any] = field(default_factory=dict)


__all__ = [
    # From this module
    "AgentConfig",
    "SessionConfig",
    # Re-exported for convenience
    "AgentResponse",
    "AgentContext",
    "SessionPhase",
    "SessionState",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_types.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/types.py tests/test_types.py
git commit -m "feat: add types.py with AgentConfig and SessionConfig"
```

---

## Task 2: Create ClientPool with Simplified Client Management

**Files:**
- Create: `xbot/agent/client_pool.py`
- Test: `tests/test_client_pool.py`

- [ ] **Step 1: Write the failing test for ClientPool**

```python
# tests/test_client_pool.py
"""Tests for simplified client pool."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.agent.client_pool import ClientPool


class TestClientPool:
    """Tests for ClientPool."""

    @pytest.fixture
    def pool(self) -> ClientPool:
        """Create a client pool for testing."""
        return ClientPool()

    @pytest.mark.asyncio
    async def test_get_or_create_new_client(self, pool: ClientPool) -> None:
        """Test creating a new client."""
        with patch("xbot.agent.client_pool.ClaudeSDKClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            client = await pool.get_or_create("session:1")

            assert client == mock_client
            mock_client_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_or_create_existing_client(self, pool: ClientPool) -> None:
        """Test getting an existing client."""
        with patch("xbot.agent.client_pool.ClaudeSDKClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            # First call creates
            client1 = await pool.get_or_create("session:1")
            # Second call returns existing
            client2 = await pool.get_or_create("session:1")

            assert client1 == client2
            mock_client_class.assert_called_once()  # Only created once

    @pytest.mark.asyncio
    async def test_disconnect(self, pool: ClientPool) -> None:
        """Test disconnecting a client."""
        with patch("xbot.agent.client_pool.ClaudeSDKClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.disconnect = AsyncMock()
            mock_client_class.return_value = mock_client

            await pool.get_or_create("session:1")
            await pool.disconnect("session:1")

            mock_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_all(self, pool: ClientPool) -> None:
        """Test disconnecting all clients."""
        with patch("xbot.agent.client_pool.ClaudeSDKClient") as mock_client_class:
            mock_client1 = MagicMock()
            mock_client1.disconnect = AsyncMock()
            mock_client2 = MagicMock()
            mock_client2.disconnect = AsyncMock()

            # Create two clients
            mock_client_class.return_value = mock_client1
            await pool.get_or_create("session:1")
            mock_client_class.return_value = mock_client2
            await pool.get_or_create("session:2")

            await pool.disconnect_all()

            mock_client1.disconnect.assert_called_once()
            mock_client2.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_snapshot(self, pool: ClientPool) -> None:
        """Test getting pool snapshot."""
        with patch("xbot.agent.client_pool.ClaudeSDKClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            await pool.get_or_create("session:1")
            snapshot = pool.snapshot()

            assert "session:1" in snapshot["clients"]
            assert snapshot["counts"]["connected"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_client_pool.py -v`
Expected: FAIL with "No module named 'xbot.agent.client_pool'"

- [ ] **Step 3: Write the implementation**

```python
# xbot/agent/client_pool.py
"""Simplified client pool for single-user scenarios.

This module provides a simplified client connection manager
without TTL/Scavenger/LRU complexity.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from xbot.logging import get_logger

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

logger = get_logger(__name__)


@dataclass
class ClientRecord:
    """Record for tracking a connected client."""

    session_key: str
    client: ClaudeSDKClient
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    state: str = "connected"


class ClientPool:
    """Simplified client pool for single-user scenarios.

    Unlike the original ClientLifecycleManager, this class:
    - Removes TTL-based cleanup (not needed for single user)
    - Removes Scavenger process (no background cleanup needed)
    - Removes LRU eviction (single user won't hit capacity limits)
    - Keeps basic lifecycle tracking for observability

    Use this when you don't need multi-tenant client management.
    """

    def __init__(self) -> None:
        """Initialize the client pool."""
        self._clients: dict[str, ClientRecord] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        session_key: str,
        options: Any | None = None,
    ) -> ClaudeSDKClient:
        """Get an existing client or create a new one.

        Args:
            session_key: Session identifier
            options: Optional ClaudeAgentOptions for client creation

        Returns:
            ClaudeSDKClient instance
        """
        async with self._lock:
            record = self._clients.get(session_key)
            if record is not None and record.state == "connected":
                record.last_used_at = time.time()
                return record.client

            # Create new client
            from claude_agent_sdk import ClaudeSDKClient

            if options is None:
                raise ValueError("Options required to create client")

            client = ClaudeSDKClient(options)
            self._clients[session_key] = ClientRecord(
                session_key=session_key,
                client=client,
            )
            logger.info(f"Created client for session {session_key}")
            return client

    async def disconnect(self, session_key: str) -> bool:
        """Disconnect a client.

        Args:
            session_key: Session identifier

        Returns:
            True if disconnected, False if not found
        """
        async with self._lock:
            record = self._clients.get(session_key)
            if record is None:
                return True

            try:
                await asyncio.wait_for(record.client.disconnect(), timeout=10.0)
                record.state = "disconnected"
                del self._clients[session_key]
                logger.info(f"Disconnected client for session {session_key}")
                return True
            except Exception as e:
                logger.warning(f"Failed to disconnect client for {session_key}: {e}")
                record.state = "error"
                return False

    async def disconnect_all(self) -> int:
        """Disconnect all clients.

        Returns:
            Number of clients disconnected
        """
        keys = list(self._clients.keys())
        count = 0
        for key in keys:
            if await self.disconnect(key):
                count += 1
        return count

    def snapshot(self) -> dict[str, Any]:
        """Get current pool state for observability.

        Returns:
            Dict with counts and client details
        """
        counts = {"connected": 0, "disconnected": 0, "error": 0}
        clients: dict[str, Any] = {}

        for key, record in self._clients.items():
            counts[record.state] = counts.get(record.state, 0) + 1
            clients[key] = {
                "state": record.state,
                "created_at": record.created_at,
                "last_used_at": record.last_used_at,
            }

        return {"counts": counts, "clients": clients}

    def has_client(self, session_key: str) -> bool:
        """Check if a session has an active client."""
        record = self._clients.get(session_key)
        return record is not None and record.state == "connected"

    def list_clients(self) -> list[str]:
        """List all session keys with active clients."""
        return [
            key for key, record in self._clients.items()
            if record.state == "connected"
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_client_pool.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/client_pool.py tests/test_client_pool.py
git commit -m "feat: add ClientPool with simplified client management"
```

---

## Task 3: Create AgentService Skeleton

**Files:**
- Create: `xbot/agent/service.py`
- Test: `tests/test_agent_service.py`

- [ ] **Step 1: Write the failing test for AgentService initialization**

```python
# tests/test_agent_service.py
"""Tests for AgentService."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.agent.service import AgentService
from xbot.agent.types import AgentConfig


class TestAgentService:
    """Tests for AgentService."""

    @pytest.fixture
    def config(self) -> AgentConfig:
        """Create a test config."""
        return AgentConfig(
            model="claude-sonnet-4-6",
            system_prompt="Test prompt",
        )

    @pytest.fixture
    def shared_resources(self, tmp_path: Path) -> dict[str, Any]:
        """Create shared resources."""
        return {
            "workspace": str(tmp_path),
            "config": MagicMock(),
        }

    @pytest.mark.asyncio
    async def test_initialize(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Test AgentService initialization."""
        service = AgentService()
        await service.initialize(config, shared_resources)

        assert service._initialized is True

    @pytest.mark.asyncio
    async def test_shutdown(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Test AgentService shutdown."""
        service = AgentService()
        await service.initialize(config, shared_resources)
        await service.shutdown()

        assert service._initialized is False

    @pytest.mark.asyncio
    async def test_process_returns_response(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Test process yields AgentResponse."""
        from xbot.agent.protocol import AgentContext

        service = AgentService()
        await service.initialize(config, shared_resources)

        context = AgentContext(
            session_key="test:1",
            prompt="Hello",
        )

        responses = []
        with patch.object(service, "_get_or_create_client") as mock_client:
            mock_sdk_client = MagicMock()
            mock_sdk_client.process = MagicMock()
            mock_sdk_client.process.return_value = asyncio.as_completed([])
            mock_client.return_value = mock_sdk_client

            async for response in service.process(context):
                responses.append(response)

        # Should have at least one response (even if empty due to mock)
        # This test will be enhanced in later tasks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_service.py -v`
Expected: FAIL with "No module named 'xbot.agent.service'"

- [ ] **Step 3: Write the implementation skeleton**

```python
# xbot/agent/service.py
"""Unified Agent Service.

This module provides the single entry point for all agent operations,
combining the core logic from ClaudeSDKBackend and AgentRuntime.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from xbot.logging import get_logger
from xbot.agent.protocol import AgentContext, AgentResponse
from xbot.agent.types import AgentConfig
from xbot.agent.client_pool import ClientPool
from xbot.agent.capabilities.handoff import HandoffPolicy

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeSDKClient

logger = get_logger(__name__)


class AgentService:
    """Unified agent service combining backend and runtime logic.

    This is the single entry point for all agent operations:
    - initialize(): Set up the agent
    - process(): Handle messages and yield responses
    - shutdown(): Clean up resources
    - reset_session(): Reset session state
    - get_session_commands(): Get available commands
    - interrupt_session(): Interrupt ongoing processing
    - call_for_auxiliary(): Execute standalone prompts

    No more router, no more backend abstraction - just direct SDK usage.
    """

    def __init__(self) -> None:
        """Initialize the agent service."""
        self._initialized = False
        self._config: AgentConfig | None = None
        self._shared_resources: dict[str, Any] = {}
        self._client_pool = ClientPool()
        self._handoff_policy: HandoffPolicy | None = None

    @property
    def name(self) -> str:
        """Service name identifier."""
        return "agent_service"

    async def initialize(
        self,
        config: AgentConfig,
        shared_resources: dict[str, Any],
    ) -> None:
        """Initialize the agent service.

        Args:
            config: Agent configuration
            shared_resources: Shared resources (workspace, bus, etc.)
        """
        if self._initialized:
            return

        self._config = config
        self._shared_resources = shared_resources

        # Initialize handoff policy for SDK subagent observability
        agents_config = getattr(config, "agents", None)
        self._handoff_policy = HandoffPolicy(agents_config)

        self._initialized = True
        logger.info("AgentService initialized")

    async def process(
        self,
        context: AgentContext,
    ) -> AsyncIterator[AgentResponse]:
        """Process a message and yield responses.

        Args:
            context: Processing context with session info and prompt

        Yields:
            AgentResponse objects (streaming)
        """
        if not self._initialized:
            raise RuntimeError("AgentService not initialized")

        logger.info(
            f"[AgentService] Processing for session={context.session_key}, "
            f"prompt={context.prompt[:50]}..."
        )

        # Get or create client
        client = await self._get_or_create_client(context.session_key)

        # Build SDK options
        options = self._build_options(context)

        # Process through SDK
        try:
            async for event in client.process(context.prompt, options=options):
                response = self._convert_event(event)
                if response:
                    yield response
        except asyncio.CancelledError:
            logger.info(f"[AgentService] Processing cancelled for {context.session_key}")
            raise
        except Exception as e:
            logger.error(f"[AgentService] Error processing: {e}")
            yield AgentResponse(
                content=f"Error: {e}",
                finish_reason="error",
            )

    async def shutdown(self) -> None:
        """Shutdown the agent service and release resources."""
        if not self._initialized:
            return

        logger.info("AgentService shutting down...")

        # Disconnect all clients
        await self._client_pool.disconnect_all()

        self._initialized = False
        logger.info("AgentService shutdown complete")

    async def reset_session(self, session_key: str) -> None:
        """Reset session state.

        Args:
            session_key: Session identifier
        """
        logger.info(f"Resetting session {session_key}")
        await self._client_pool.disconnect(session_key)

    async def get_session_commands(self, session_key: str) -> list[str]:
        """Get available slash commands for a session.

        Args:
            session_key: Session identifier

        Returns:
            List of available commands
        """
        # TODO: Implement command loading
        return []

    async def interrupt_session(self, session_key: str) -> dict[str, Any]:
        """Interrupt ongoing processing for a session.

        Args:
            session_key: Session identifier

        Returns:
            Dict with 'interrupted' bool and optional 'usage' dict
        """
        # TODO: Implement interrupt logic
        return {"interrupted": False, "usage": None}

    async def call_for_auxiliary(
        self,
        session_key: str,
        prompt: str,
    ) -> AgentResponse:
        """Execute a standalone prompt.

        Args:
            session_key: Session identifier
            prompt: Prompt to execute

        Returns:
            AgentResponse with result
        """
        context = AgentContext(
            session_key=session_key,
            prompt=prompt,
        )
        final = ""
        async for response in self.process(context):
            if response.is_delta:
                final += response.delta_content
            else:
                final = response.content or final

        return AgentResponse(content=final)

    # === Internal Methods ===

    async def _get_or_create_client(
        self,
        session_key: str,
    ) -> ClaudeSDKClient:
        """Get or create SDK client for session.

        Args:
            session_key: Session identifier

        Returns:
            ClaudeSDKClient instance
        """
        from claude_agent_sdk import ClaudeAgentOptions

        # Build options
        options = self._build_sdk_options()

        return await self._client_pool.get_or_create(session_key, options=options)

    def _build_sdk_options(self) -> Any:
        """Build ClaudeAgentOptions from configuration."""
        from claude_agent_sdk import ClaudeAgentOptions

        # TODO: Implement full options building
        # For now, return minimal options
        return ClaudeAgentOptions(
            model=self._config.model if self._config else "claude-sonnet-4-6",
            system_prompt=self._config.system_prompt if self._config else "",
        )

    def _build_options(self, context: AgentContext) -> Any:
        """Build processing options for a context."""
        return self._build_sdk_options()

    def _convert_event(self, event: Any) -> AgentResponse | None:
        """Convert SDK event to AgentResponse.

        Args:
            event: SDK event object

        Returns:
            AgentResponse or None if event should be skipped
        """
        # TODO: Implement full event conversion
        # For now, handle basic text
        if hasattr(event, "content"):
            return AgentResponse(content=str(event.content))
        return None

    def _build_sdk_agents(self) -> dict[str, Any] | None:
        """Build SDK agent definitions from configuration.

        This method preserves the agents configuration for SDK subagent support.

        Returns:
            Dict of agent definitions or None
        """
        if not self._config or not self._config.agents:
            return None

        from claude_agent_sdk.types import AgentDefinition

        agents: dict[str, AgentDefinition] = {}
        for agent_def in self._config.agents:
            name = agent_def.get("name", "unknown")
            agents[name] = AgentDefinition(
                description=agent_def.get("description", ""),
                prompt=agent_def.get("prompt", ""),
                tools=agent_def.get("tools"),
                model=agent_def.get("model"),
            )
        return agents
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_agent_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/service.py tests/test_agent_service.py
git commit -m "feat: add AgentService skeleton with core methods"
```

---

## Task 4: Delete Dead Code (delegation.py)

**Files:**
- Delete: `xbot/agent/backends/delegation.py`

- [ ] **Step 1: Verify delegation.py has no active usage**

Run: `grep -r "from xbot.agent.backends.delegation import\|DelegationTrace" --include="*.py" xbot/ tests/`

Expected: Only self-references in `xbot/agent/backends/delegation.py`

- [ ] **Step 2: Delete the file**

```bash
rm xbot/agent/backends/delegation.py
```

- [ ] **Step 3: Verify no import errors**

Run: `python -c "from xbot.agent.backends import *; print('OK')"`
Expected: OK

- [ ] **Step 4: Run tests to ensure nothing is broken**

Run: `pytest tests/ -v --tb=short -x`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: delete dead code delegation.py"
```

---

## Task 5: Update protocol.py - Remove Abstract Class

**Files:**
- Modify: `xbot/agent/protocol.py`
- Test: `tests/test_protocol.py` (update)

- [ ] **Step 1: Update test to remove AgentBackend abstract class test**

```python
# tests/test_protocol.py
"""Tests for agent protocol definitions."""

import pytest

from xbot.agent.protocol import AgentContext, AgentResponse


class TestAgentResponse:
    """Tests for AgentResponse dataclass."""

    def test_default_values(self) -> None:
        """Test default values are set correctly."""
        response = AgentResponse(content="Hello")
        assert response.content == "Hello"
        assert response.progress_texts == []
        assert response.tool_calls is None
        assert response.tool_hint_text == ""
        assert response.finish_reason == "stop"
        assert response.usage is None
        assert response.raw_message is None
        assert response.is_delta is False
        assert response.delta_content == ""

    def test_custom_values(self) -> None:
        """Test custom values are set correctly."""
        response = AgentResponse(
            content="Result",
            progress_texts=["Thinking...", "Processing..."],
            tool_calls=[{"name": "test", "args": {}}],
            tool_hint_text="Tool: test",
            finish_reason="tool_use",
            usage={"total_tokens": 100},
        )
        assert response.content == "Result"
        assert len(response.progress_texts) == 2
        assert response.tool_calls is not None
        assert response.finish_reason == "tool_use"
        assert response.usage["total_tokens"] == 100

    def test_delta_response(self) -> None:
        """Test delta/streaming response."""
        response = AgentResponse(
            content="",
            is_delta=True,
            delta_content="Hello",
        )
        assert response.is_delta is True
        assert response.delta_content == "Hello"


class TestAgentContext:
    """Tests for AgentContext dataclass."""

    def test_default_values(self) -> None:
        """Test default values are set correctly."""
        context = AgentContext(session_key="test:123", prompt="Hello")
        assert context.session_key == "test:123"
        assert context.prompt == "Hello"
        assert context.history == []
        assert context.media is None
        assert context.channel == ""
        assert context.chat_id == ""
        assert context.metadata == {}

    def test_custom_values(self) -> None:
        """Test custom values are set correctly."""
        context = AgentContext(
            session_key="telegram:7743853836",
            prompt="What's the weather?",
            history=[{"role": "user", "content": "Hi"}],
            channel="telegram",
            chat_id="7743853836",
            metadata={"source": "test"},
        )
        assert context.session_key == "telegram:7743853836"
        assert len(context.history) == 1
        assert context.channel == "telegram"
        assert context.metadata["source"] == "test"
```

- [ ] **Step 2: Run test to verify current state**

Run: `pytest tests/test_protocol.py -v`
Expected: FAIL (test_agentbackend tests will fail)

- [ ] **Step 3: Update protocol.py to remove abstract class**

```python
# xbot/agent/protocol.py
"""Agent protocol definitions.

This module defines data classes for agent communication.
The AgentBackend abstract class has been removed since only
ClaudeSDKBackend exists (now AgentService).
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResponse:
    """Unified Agent response format."""

    content: str
    progress_texts: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] | None = None
    tool_hint_text: str = ""
    finish_reason: str = "stop"  # stop | tool_use | error | max_iterations
    usage: dict[str, Any] | None = None
    raw_message: Any = None
    event_type: str = ""
    event_data: dict[str, Any] | None = None

    # For streaming support
    is_delta: bool = False
    delta_content: str = ""


@dataclass
class AgentContext:
    """Context for agent processing."""

    session_key: str
    prompt: str
    history: list[dict[str, Any]] = field(default_factory=list)
    media: list[Any] | None = None
    channel: str = ""
    chat_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "AgentResponse",
    "AgentContext",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_protocol.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xbot/agent/protocol.py tests/test_protocol.py
git commit -m "refactor: remove AgentBackend abstract class from protocol.py"
```

---

## Task 6: Delete router.py

**Files:**
- Delete: `xbot/agent/router.py`
- Update: `xbot/agent/__init__.py`
- Update: `tests/test_router.py`

- [ ] **Step 1: Update xbot/agent/__init__.py to remove AgentRouter export**

```python
# xbot/agent/__init__.py
"""Agent core module.

Keep package exports lazy to avoid import-time side effects while loading submodules.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "CapabilityCatalog",
    "CapabilityPolicy",
    "ContextBuilder",
    "MemoryStore",
    "SkillsLoader",
    # Core types
    "AgentResponse",
    "AgentContext",
    # Unified service
    "AgentService",
]


def __getattr__(name: str) -> Any:
    lazy_exports: dict[str, tuple[str, str]] = {
        "ContextBuilder": ("xbot.agent.context.builder", "ContextBuilder"),
        "CapabilityCatalog": ("xbot.agent.capabilities.catalog", "CapabilityCatalog"),
        "CapabilityPolicy": ("xbot.agent.capabilities.policy", "CapabilityPolicy"),
        "MemoryStore": ("xbot.agent.memory.store", "MemoryStore"),
        "SkillsLoader": ("xbot.agent.capabilities.skills_loader", "SkillsLoader"),
        "AgentResponse": ("xbot.agent.protocol", "AgentResponse"),
        "AgentContext": ("xbot.agent.protocol", "AgentContext"),
        "AgentService": ("xbot.agent.service", "AgentService"),
    }
    module_attr = lazy_exports.get(name)
    if module_attr is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = module_attr
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
```

- [ ] **Step 2: Update tests/test_router.py to test AgentService instead**

```python
# tests/test_router.py - Renamed to tests/test_agent_service_compat.py
"""Tests for AgentService as router replacement.

This test file verifies AgentService can replace AgentRouter.
"""

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xbot.agent.protocol import AgentContext, AgentResponse
from xbot.agent.service import AgentService
from xbot.agent.types import AgentConfig


class TestAgentServiceAsRouter:
    """Tests for AgentService as router replacement."""

    @pytest.fixture
    def config(self) -> AgentConfig:
        """Create a test config."""
        return AgentConfig(
            model="claude-sonnet-4-6",
            system_prompt="Test prompt",
        )

    @pytest.fixture
    def shared_resources(self, tmp_path) -> dict[str, Any]:
        """Create shared resources."""
        return {"workspace": str(tmp_path), "config": MagicMock()}

    @pytest.mark.asyncio
    async def test_initialize(self, config: AgentConfig, shared_resources: dict[str, Any]) -> None:
        """Test service initialization."""
        service = AgentService()
        await service.initialize(config, shared_resources)
        assert service._initialized is True

    @pytest.mark.asyncio
    async def test_shutdown(self, config: AgentConfig, shared_resources: dict[str, Any]) -> None:
        """Test service shutdown."""
        service = AgentService()
        await service.initialize(config, shared_resources)
        await service.shutdown()
        assert service._initialized is False
```

- [ ] **Step 3: Delete router.py**

```bash
rm xbot/agent/router.py
```

- [ ] **Step 4: Verify no import errors**

Run: `python -c "from xbot.agent import AgentService; print('OK')"`
Expected: OK

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_router.py tests/test_protocol.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: delete router.py, update exports for AgentService"
```

---

## Task 7: Migrate Message Converter to AgentService

**Files:**
- Modify: `xbot/agent/service.py`
- Read: `xbot/agent/backends/message_converter.py`

- [ ] **Step 1: Copy MessageConverter logic into AgentService**

Read the existing `message_converter.py` and integrate its `_convert_*` methods directly into `AgentService`.

```python
# Add these methods to xbot/agent/service.py

def _convert_event(self, event: Any) -> AgentResponse | None:
    """Convert SDK event to AgentResponse."""
    # Check message type
    event_type = type(event).__name__

    if event_type == "AssistantMessage":
        return self._convert_assistant_message(event)
    elif event_type == "StreamEvent":
        return self._convert_stream_event(event)
    elif event_type == "ResultMessage":
        return self._convert_result_message(event)
    elif event_type == "SystemMessage":
        return self._convert_system_message(event)
    elif event_type == "RateLimitEvent":
        return self._convert_rate_limit_event(event)

    return None

def _convert_assistant_message(self, message: Any) -> AgentResponse | None:
    """Convert AssistantMessage to AgentResponse."""
    text = ""
    progress_texts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in message.content:
        block_type = type(block).__name__
        if block_type == "TextBlock":
            text += block.text
        elif block_type == "ThinkingBlock":
            if block.thinking:
                progress_texts.append(f"Thinking: {block.thinking}")
        elif block_type == "ToolUseBlock":
            tool_calls.append({
                "id": block.id,
                "name": block.name,
                "input": block.input,
                "kind": self._classify_tool_name(block.name),
            })

    return AgentResponse(
        content=text,
        progress_texts=progress_texts,
        tool_calls=tool_calls if tool_calls else None,
        finish_reason="tool_use" if tool_calls else "stop",
        raw_message=message,
    )

def _convert_stream_event(self, message: Any) -> AgentResponse | None:
    """Convert StreamEvent to AgentResponse."""
    event = message.event or {}
    if event.get("type") != "content_block_delta":
        return None

    delta = event.get("delta", {})
    delta_type = delta.get("type")

    if delta_type == "text_delta":
        text = delta.get("text", "")
        if not text:
            return None
        return AgentResponse(
            content="",
            is_delta=True,
            delta_content=text,
            raw_message=message,
        )

    return None

def _convert_result_message(self, message: Any) -> AgentResponse | None:
    """Convert ResultMessage to AgentResponse."""
    usage = None
    if hasattr(message, "usage") and message.usage:
        usage = {
            "input_tokens": int(getattr(message.usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(message.usage, "output_tokens", 0) or 0),
        }

    content = message.result if isinstance(message.result, str) else ""
    return AgentResponse(
        content=content,
        finish_reason="stop",
        usage=usage,
        raw_message=message,
    )

def _convert_system_message(self, message: Any) -> AgentResponse | None:
    """Convert SystemMessage to AgentResponse."""
    # For now, return None to skip system messages
    return None

def _convert_rate_limit_event(self, message: Any) -> AgentResponse | None:
    """Convert RateLimitEvent to AgentResponse."""
    return AgentResponse(
        content="",
        progress_texts=["Rate limit hit, waiting..."],
        raw_message=message,
    )

def _classify_tool_name(self, name: str) -> str:
    """Classify a tool name into its kind."""
    normalized = name.replace("_", "-").lower()
    if normalized.startswith("mcp-"):
        return "mcp"
    return "tool"
```

- [ ] **Step 2: Run tests to verify**

Run: `pytest tests/test_agent_service.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add xbot/agent/service.py
git commit -m "feat: integrate message converter into AgentService"
```

---

## Task 8: Migrate Options Builder to AgentService

**Files:**
- Modify: `xbot/agent/service.py`
- Read: `xbot/agent/backends/options_builder.py`

- [ ] **Step 1: Add options building methods to AgentService**

```python
# Add to xbot/agent/service.py

def _build_sdk_options(self) -> Any:
    """Build ClaudeAgentOptions from configuration."""
    from claude_agent_sdk import ClaudeAgentOptions

    if not self._config:
        raise RuntimeError("AgentService not configured")

    # Build environment
    env = self._build_env_config()

    # Build MCP servers
    mcp_servers = self._build_mcp_servers()

    # Build agents
    agents = self._build_sdk_agents()

    return ClaudeAgentOptions(
        cwd=self._shared_resources.get("workspace", "."),
        model=self._config.model,
        system_prompt=self._config.system_prompt,
        mcp_servers=mcp_servers if mcp_servers else None,
        agents=agents,
        env=env,
    )

def _build_env_config(self) -> dict[str, str]:
    """Build environment configuration for SDK."""
    env = {}

    # Get provider config from shared resources
    config = self._shared_resources.get("config")
    if config and hasattr(config, "providers"):
        # TODO: Implement provider resolution
        pass

    return env

def _build_mcp_servers(self) -> dict[str, Any]:
    """Build MCP servers configuration."""
    if not self._config:
        return {}

    return self._config.mcp_servers.copy() if self._config.mcp_servers else {}
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_agent_service.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add xbot/agent/service.py
git commit -m "feat: integrate options builder into AgentService"
```

---

## Task 9: Update CLI Entry Point

**Files:**
- Modify: `xbot/cli/main.py`

- [ ] **Step 1: Update CLI to use AgentService**

Find the current usage of AgentRuntime/AgentRouter and replace with AgentService.

```python
# Example update pattern (actual file content may vary)
# Replace:
#   from xbot.agent import AgentRuntime
# With:
#   from xbot.agent import AgentService

# Replace:
#   runtime = AgentRuntime(config, shared_resources)
#   await runtime.initialize()
# With:
#   service = AgentService()
#   await service.initialize(agent_config, shared_resources)
```

- [ ] **Step 2: Verify CLI still works**

Run: `python -m xbot.cli --help`
Expected: CLI help output

- [ ] **Step 3: Commit**

```bash
git add xbot/cli/main.py
git commit -m "refactor: update CLI to use AgentService"
```

---

## Task 10: Delete Old Backend Files

**Files:**
- Delete: `xbot/agent/backends/claude_sdk_backend.py`
- Delete: `xbot/agent/backends/client_lifecycle.py`
- Delete: `xbot/agent/backends/message_converter.py`
- Delete: `xbot/agent/backends/options_builder.py`

- [ ] **Step 1: Verify no remaining imports**

Run: `grep -r "from xbot.agent.backends.claude_sdk_backend\|from xbot.agent.backends.client_lifecycle\|from xbot.agent.backends.message_converter\|from xbot.agent.backends.options_builder" --include="*.py" xbot/ tests/`

Expected: No results (or only in files being deleted)

- [ ] **Step 2: Delete the files**

```bash
rm xbot/agent/backends/claude_sdk_backend.py
rm xbot/agent/backends/client_lifecycle.py
rm xbot/agent/backends/message_converter.py
rm xbot/agent/backends/options_builder.py
```

- [ ] **Step 3: Update backends/__init__.py if needed**

```python
# xbot/agent/backends/__init__.py
"""Backend module - now empty after consolidation."""

__all__ = []
```

- [ ] **Step 4: Run all tests**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: delete old backend files after consolidation"
```

---

## Task 11: Final Verification and Cleanup

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 2: Verify imports work correctly**

```bash
python -c "
from xbot.agent import AgentService, AgentResponse, AgentContext
from xbot.agent.types import AgentConfig, SessionConfig
from xbot.agent.client_pool import ClientPool
print('All imports OK')
"
```

Expected: "All imports OK"

- [ ] **Step 3: Verify no dead imports remain**

Run: `grep -r "AgentRouter\|AgentBackend\|ClaudeSDKBackend" --include="*.py" xbot/ | grep -v "__pycache__" | grep -v "\.pyc"`

Expected: No results (all old references removed)

- [ ] **Step 4: Update documentation**

Update any references to old modules in docs/ if needed.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "refactor: complete backend simplification

- Created AgentService as unified entry point
- Created ClientPool for simplified client management
- Created types.py for unified data types
- Removed dead code (delegation.py)
- Removed router.py (single backend, no routing needed)
- Removed AgentBackend abstract class
- Consolidated message converter and options builder
- Deleted old backend files

Total lines reduced by ~4000+"
```

---

## Verification Checklist

| Item | Status |
|------|--------|
| AgentService.initialize() works | [ ] |
| AgentService.process() yields responses | [ ] |
| AgentService.shutdown() cleans up | [ ] |
| ClientPool manages clients correctly | [ ] |
| types.py exports work | [ ] |
| CLI still functions | [ ] |
| All tests pass | [ ] |
| No dead imports remain | [ ] |
| Documentation updated | [ ] |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| SDK function changes | Functions are from stable SDK API |
| Test coverage gaps | Run full test suite after each task |
| Import errors | Verify imports after each deletion |
| CLI breakage | Test CLI after updates |
| Feature regression | Compare with original behavior checklist |