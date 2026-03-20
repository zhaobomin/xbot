"""Agent router for selecting and managing Agent backends.

The router selects the appropriate backend based on configuration
and provides a unified interface for message processing.
"""

import logging
from typing import Any, AsyncIterator, Type

from nanobot.agent.protocol import AgentBackend, AgentContext, AgentResponse
from nanobot.config.schema import AgentsConfig

logger = logging.getLogger(__name__)


class AgentRouter:
    """Router for selecting and managing Agent backends.

    The router:
    - Selects backend based on config.agents.type
    - Manages backend lifecycle
    - Provides unified interface for message processing
    - Supports dynamic backend switching
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
        self._backend: AgentBackend | None = None
        self._initialized = False

    @property
    def backend_type(self) -> str:
        """Current backend type."""
        return self.config.type

    @property
    def backend(self) -> AgentBackend:
        """Get current backend instance."""
        if self._backend is None:
            raise RuntimeError("Backend not initialized. Call initialize() first.")
        return self._backend

    async def initialize(self) -> None:
        """Initialize the selected backend.

        Raises:
            ValueError: If backend type is unknown
        """
        if self._initialized:
            return

        backend_class = self._backends.get(self.config.type)
        if not backend_class:
            available = ", ".join(self._backends.keys())
            raise ValueError(
                f"Unknown agent backend type: '{self.config.type}'. "
                f"Available backends: {available}"
            )

        logger.info(f"Initializing agent backend: {self.config.type}")
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
        if not self._initialized:
            await self.initialize()

        async for response in self._backend.process(context):
            yield response

    async def switch_backend(self, new_type: str) -> None:
        """Switch to a different backend type.

        Args:
            new_type: New backend type name

        Raises:
            ValueError: If backend type is unknown
        """
        if new_type == self.config.type:
            logger.info(f"Already using backend type: {new_type}")
            return

        logger.info(f"Switching backend from {self.config.type} to {new_type}")

        # Shutdown current backend
        if self._backend:
            await self._backend.shutdown()

        # Update config and reset
        self.config.type = new_type
        self._backend = None
        self._initialized = False

        # Initialize new backend
        await self.initialize()

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
    """
    # Import here to avoid circular imports
    from nanobot.agent.backends.litellm_backend import LiteLLMBackend

    AgentRouter.register_backend("litellm", LiteLLMBackend)

    # Claude SDK backend is optional - only register if SDK is available
    try:
        from nanobot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        AgentRouter.register_backend("claude_sdk", ClaudeSDKBackend)
    except ImportError:
        logger.warning(
            "Claude SDK backend not available. "
            "Install claude-agent-sdk to enable it."
        )