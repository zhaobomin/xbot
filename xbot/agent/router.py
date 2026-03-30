"""Agent router for managing the Claude SDK backend.

Provides a unified interface for message processing through the Claude SDK.
"""

from typing import Any, AsyncIterator, Type

from xbot.logging import get_logger

logger = get_logger(__name__)

from xbot.agent.protocol import AgentBackend, AgentContext, AgentResponse
from xbot.config.schema import AgentsConfig


class AgentRouter:
    """Router for managing the Claude SDK Agent backend.

    The router:
    - Initializes the Claude SDK backend
    - Manages backend lifecycle
    - Provides unified interface for message processing
    """

    # Backend registry - maps type names to backend classes
    _backends: dict[str, Type[AgentBackend]] = {}

    def __init__(self, config: AgentsConfig, shared_resources: dict[str, Any]):
        """Initialize the router.

        Args:
            config: Agent configuration
            shared_resources: Shared resources (bus, providers, workspace, etc.)
        """
        self.config = config
        self.shared_resources = shared_resources
        self._backends = dict(type(self)._backends)
        self._backend: AgentBackend | None = None
        self._initialized = False

    @property
    def backend_type(self) -> str:
        """Current backend type (always 'claude_sdk')."""
        return "claude_sdk"

    @property
    def backend(self) -> AgentBackend:
        """Get current backend instance."""
        if self._backend is None:
            raise RuntimeError("Backend not initialized. Call initialize() first.")
        return self._backend

    async def initialize(self) -> None:
        """Initialize the Claude SDK backend.

        Raises:
            RuntimeError: If backend initialization fails
        """
        if self._initialized:
            return

        # Claude SDK is the only supported backend
        backend_class = self._backends.get("claude_sdk")
        if not backend_class:
            raise RuntimeError("Claude SDK backend not registered")

        logger.info("Initializing agent backend: claude_sdk")
        self._backend = backend_class()
        await self._backend.initialize(self.config, self.shared_resources)
        self._initialized = True
        logger.info(f"Agent backend initialized: {self._backend.name}")

    async def process(self, context: AgentContext) -> AsyncIterator[AgentResponse]:
        """Process a message using the selected backend.

        Args:
            context: Processing context

        Yields:
            AgentResponse objects
        """
        logger.info(f"[Router] Starting process for session={context.session_key}, backend={self._backend.name if self._backend else None}")

        if not self._initialized:
            await self.initialize()

        logger.info(f"[Router] Calling backend.process for session={context.session_key}")
        async for response in self._backend.process(context):
            logger.debug(f"[Router] Yielding response for session={context.session_key}")
            yield response

    async def shutdown(self) -> None:
        """Shutdown the current backend."""
        if self._backend:
            logger.info(f"Shutting down backend: {self._backend.name}")
            await self._backend.shutdown()
            self._backend = None
            self._initialized = False

    @classmethod
    def register_backend(cls, name: str, backend_class: Type[AgentBackend]) -> None:
        """Register a new backend type.

        Args:
            name: Backend type name
            backend_class: Backend class
        """
        cls._backends[name] = backend_class
        logger.debug(f"Registered agent backend: {name}")

    @classmethod
    def get_available_backends(cls) -> list[str]:
        """Get list of available backend types.

        Returns:
            List of backend type names
        """
        return list(cls._backends.keys())


def register_default_backends() -> None:
    """Register default backend implementations.

    This function should be called at module import time
    to ensure all backends are registered.

    Note: This function is idempotent - it will not overwrite
    existing registrations (useful for testing).
    """
    # Don't overwrite existing registrations (supports testing with mocks)
    if "claude_sdk" in AgentRouter._backends:
        return

    # Claude SDK backend is the only supported backend
    from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

    AgentRouter.register_backend("claude_sdk", ClaudeSDKBackend)
