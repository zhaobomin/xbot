"""Agent pool: manages independent ClaudeSDKBackend instances per crew role."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from xbot.agent.crew.models import AgentRole, CrewConfig
from xbot.agent.protocol import AgentContext
from xbot.config.schema import AgentsConfig, Config


class AgentPool:
    """Creates and manages one ``ClaudeSDKBackend`` instance per crew role.

    Each role gets an independent backend with its own client pool, tools,
    and context builder.  This mirrors the spec design of mapping roles to
    isolated backend instances.
    """

    def __init__(
        self,
        crew_config: CrewConfig,
        xbot_config: Config,
        permission_handler: Any,
    ) -> None:
        self.crew_config = crew_config
        self.xbot_config = xbot_config
        self.permission_handler = permission_handler
        self._backends: dict[str, Any] = {}  # role_name -> ClaudeSDKBackend

    async def initialize(self, only_roles: set[str] | None = None) -> None:
        """Create and initialise a backend for each role.

        Args:
            only_roles: If provided, only initialise these roles (for resume).
        """
        from xbot.agent.backends.claude_sdk_backend import ClaudeSDKBackend

        roles = self.crew_config.agents
        if only_roles is not None:
            roles = {k: v for k, v in roles.items() if k in only_roles}

        workspace = Path(self.crew_config.workspace).expanduser().resolve()

        for role_name, role in roles.items():
            try:
                backend = ClaudeSDKBackend()

                agents_config = self._build_role_config(role)

                shared_resources: dict[str, Any] = {
                    "workspace": str(workspace),
                    "config": self.xbot_config,
                    "tools_config": self.xbot_config.tools,
                    "permission_handler": self.permission_handler,
                    "bus": None,
                    "session_manager": None,
                }

                await backend.initialize(agents_config, shared_resources)
                self._backends[role_name] = backend
                logger.info(f"[crew-pool] Initialised backend for role '{role_name}'")
            except Exception:
                logger.exception(f"[crew-pool] Failed to initialise backend for role '{role_name}'")

        if not self._backends:
            raise RuntimeError("All backend initialisations failed — cannot run crew")

    async def run_task(self, role_name: str, prompt: str, session_key: str) -> str:
        """Execute a prompt with the specified role's backend and collect the full response.

        Args:
            role_name: The crew role to use.
            prompt: Full prompt text.
            session_key: Unique session key for this invocation.

        Returns:
            The concatenated response text.
        """
        backend = self._backends.get(role_name)
        if backend is None:
            raise KeyError(f"No backend for role '{role_name}' (not initialised or init failed)")

        context = AgentContext(
            session_key=session_key,
            prompt=prompt,
            channel="crew",
            chat_id=role_name,
        )

        content = ""
        async for response in backend.process(context):
            if response.is_delta:
                content += response.delta_content
            else:
                content = response.content or content

        return content

    async def shutdown(self) -> None:
        """Shutdown all managed backends."""
        for role_name, backend in self._backends.items():
            try:
                await backend.shutdown()
                logger.debug(f"[crew-pool] Shut down backend for '{role_name}'")
            except Exception:
                logger.exception(f"[crew-pool] Error shutting down backend '{role_name}'")
        self._backends.clear()

    def _build_role_config(self, role: AgentRole) -> AgentsConfig:
        """Build a per-role ``AgentsConfig`` by deep-copying and overriding global settings."""
        # Deep-copy the global agents config
        base = self.xbot_config.agents.model_copy(deep=True)

        # Override model if the role specifies one
        if role.model and role.model != "inherit":
            base.defaults.model = role.model

        # Override max_turns with role's max_iterations
        base.claude_sdk.max_turns = role.max_iterations

        return base

    def get_initialised_roles(self) -> list[str]:
        """Return role names that have a live backend."""
        return list(self._backends.keys())
