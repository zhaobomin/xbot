"""Agent pool: manages independent ClaudeSDKBackend instances per crew role."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from xbot.crew.models import AgentRole, CrewConfig
from xbot.runtime.core.protocol import AgentContext
from xbot.platform.config.schema import AgentsConfig, Config
from xbot.platform.logging.core import get_logger

logger = get_logger(__name__)
@dataclass
class TaskProgress:
    """Progress event from task execution."""

    delta_content: str = ""  # New content since last event
    total_content: str = ""  # All content so far
    is_final: bool = False  # True when task completes


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
        self._failed_roles: dict[str, str] = {}  # role_name -> error message

    async def initialize(self, only_roles: set[str] | None = None) -> None:
        """Create and initialise a backend for each role.

        Args:
            only_roles: If provided, only initialise these roles (for resume).

        Raises:
            RuntimeError: If no backends could be initialised.
        """
        from xbot.runtime.core.service import AgentService
        from xbot.runtime.core.types import AgentConfig

        roles = self.crew_config.agents
        if only_roles is not None:
            roles = {k: v for k, v in roles.items() if k in only_roles}

        workspace = Path(self.crew_config.workspace).expanduser().resolve()

        for role_name, role in roles.items():
            try:
                agents_config = self._build_role_config(role)

                # 更新 shared_resources 中的 config，确保 model 设置生效
                from copy import deepcopy
                updated_config = deepcopy(self.xbot_config)
                if role.model and role.model != "inherit":
                    updated_config.agents.defaults.model = role.model

                shared_resources: dict[str, Any] = {
                    "workspace": str(workspace),
                    "config": updated_config,
                    "tools_config": self.xbot_config.tools,
                    "permission_handler": self.permission_handler,
                    "bus": None,
                    "session_manager": None,
                }

                # Convert AgentsConfig to AgentConfig for AgentService
                agent_config = AgentConfig(
                    model=agents_config.defaults.model,
                    system_prompt="",  # System prompt is built dynamically by ContextBuilder
                    mcp_servers=getattr(agents_config.defaults, "mcp_servers", {}),
                    agents=getattr(agents_config.defaults, "agents", []),
                )

                service = AgentService(agent_config, shared_resources)
                await service.initialize()
                self._backends[role_name] = service
                logger.info(f"[crew-pool] Initialised backend for role '{role_name}' with model={role.model if role.model != 'inherit' else updated_config.agents.defaults.model}")
            except Exception as e:
                error_msg = str(e)
                self._failed_roles[role_name] = error_msg
                logger.exception(f"[crew-pool] Failed to initialise backend for role '{role_name}'")

        if not self._backends:
            failed_info = ", ".join(f"'{r}': {e}" for r, e in self._failed_roles.items())
            raise RuntimeError(
                f"All backend initialisations failed — cannot run crew. "
                f"Failed roles: {failed_info}"
            )

        # Warn if some roles failed but we can continue
        if self._failed_roles:
            failed_names = ", ".join(self._failed_roles.keys())
            logger.warning(
                f"[crew-pool] Some roles failed to initialise: {failed_names}. "
                f"Tasks using these roles will fail."
            )

    async def run_task(
        self, role_name: str, prompt: str, session_key: str, media: list[str] | None = None
    ) -> str:
        """Execute a prompt with the specified role's backend and collect the full response.

        Args:
            role_name: The crew role to use.
            prompt: Full prompt text.
            session_key: Unique session key for this invocation.
            media: Optional list of media file paths (images, etc.) to include.

        Returns:
            The concatenated response text.

        Raises:
            KeyError: If the role was not initialised or initialisation failed.
        """
        content = ""
        async for progress in self.run_task_streaming(role_name, prompt, session_key, media):
            content = progress.total_content
        return content

    async def run_task_streaming(
        self, role_name: str, prompt: str, session_key: str, media: list[str] | None = None
    ) -> AsyncIterator[TaskProgress]:
        """Execute a prompt and yield progress events.

        This method allows callers to monitor task progress for soft timeout
        detection. Any content output indicates the task is making progress.

        Args:
            role_name: The crew role to use.
            prompt: Full prompt text.
            session_key: Unique session key for this invocation.
            media: Optional list of media file paths (images, etc.) to include.

        Yields:
            TaskProgress events with delta and total content.

        Raises:
            KeyError: If the role was not initialised or initialisation failed.
        """
        backend = self._backends.get(role_name)
        if backend is None:
            if role_name in self._failed_roles:
                raise KeyError(
                    f"Backend for role '{role_name}' failed to initialise: "
                    f"{self._failed_roles[role_name]}"
                )
            raise KeyError(
                f"No backend for role '{role_name}' (not initialised). "
                f"Available roles: {list(self._backends.keys())}"
            )

        context = AgentContext(
            session_key=session_key,
            prompt=prompt,
            channel="crew",
            chat_id=role_name,
            media=media,
        )

        total_content = ""
        async for response in backend.process(context):
            delta = ""
            if response.is_delta:
                delta = response.delta_content
                total_content += delta
            else:
                # Non-delta response replaces content
                new_content = response.content or total_content
                delta = new_content[len(total_content):] if new_content.startswith(total_content) else new_content
                total_content = new_content

            if delta or response.is_delta:
                yield TaskProgress(
                    delta_content=delta,
                    total_content=total_content,
                    is_final=False,
                )

        # Final event
        yield TaskProgress(
            delta_content="",
            total_content=total_content,
            is_final=True,
        )

    async def shutdown(self) -> None:
        """Shutdown all managed backends.

        Handles CancelledError gracefully: completes shutdown of all backends
        before re-raising the cancellation to preserve cleanup semantics.
        """
        cancelled_error: asyncio.CancelledError | None = None
        for role_name, backend in self._backends.items():
            try:
                await backend.shutdown()
                logger.debug(f"[crew-pool] Shut down backend for '{role_name}'")
            except asyncio.CancelledError as e:
                # Store the first CancelledError, continue shutting down others
                if cancelled_error is None:
                    cancelled_error = e
                logger.warning(f"[crew-pool] Backend '{role_name}' shutdown cancelled")
            except Exception:
                logger.exception(f"[crew-pool] Error shutting down backend '{role_name}'")
        self._backends.clear()

        # Re-raise CancelledError after all backends are shut down
        if cancelled_error is not None:
            raise cancelled_error

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

    def get_failed_roles(self) -> dict[str, str]:
        """Return role names that failed to initialise with their error messages."""
        return dict(self._failed_roles)
