"""Crew resource manager: unified cleanup flow with async context manager.

This module implements the mid-term solution from IMPROVEMENT_PLAN.md,
ensuring all cleanup code runs in finally blocks and CancelledError
is handled gracefully.

Key principles:
1. All cleanup in __aexit__ (guaranteed to run)
2. CancelledError is captured and re-raised after cleanup
3. State transitions are handled consistently
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from xbot.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    from xbot.agent.crew.agent_pool import AgentPool
    from xbot.agent.crew.models import CrewConfig, TaskResult
    from xbot.agent.crew.process import BaseProcess
    from xbot.agent.crew.state import CrewStateManager
    from xbot.agent.interaction.permission import BasePermissionHandler
    from xbot.config.schema import Config


class CrewResourceManager:
    """Async context manager for crew execution resources.

    This class ensures that cleanup always runs, even when:
    - CancelledError is raised (task cancellation)
    - Exceptions occur during execution
    - Normal completion

    Usage::

        async with CrewResourceManager(...) as manager:
            manager.initialize_pool()
            process = create_process(manager.pool, ...)
            manager.set_process(process)
            results = await process.execute(tasks)

        # Cleanup is guaranteed after exiting the block
        final_status = manager.final_status

    The manager captures CancelledError so cleanup can complete,
    then re-raises it after finalize_output() runs.
    """

    def __init__(
        self,
        crew_config: "CrewConfig",
        xbot_config: "Config",
        permission_handler: "BasePermissionHandler",
        state_manager: "CrewStateManager",
        started_at: datetime,
    ) -> None:
        self.crew_config = crew_config
        self.xbot_config = xbot_config
        self.permission_handler = permission_handler
        self.state_manager = state_manager
        self.started_at = started_at

        # Resources managed by this context
        self.pool: "AgentPool | None" = None
        self.process: "BaseProcess | None" = None

        # Execution outcome tracking
        self.final_status: str = "completed"
        self.cancelled_error: asyncio.CancelledError | None = None
        self.results: list["TaskResult"] = []

        # Track if we entered the context
        self._entered: bool = False
        self._pool_initialized: bool = False

    async def __aenter__(self) -> "CrewResourceManager":
        """Enter the context - no resource initialization here.

        Resources are initialized lazily via initialize_pool()
        to allow for checkpoint resume scenarios where we may
        want to skip certain agents.
        """
        self._entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> bool:
        """Exit the context - guaranteed cleanup.

        This method:
        1. Sets final state based on exception type
        2. Shuts down agent pool
        3. Finalizes output persistence
        4. Stores CancelledError for later re-raise

        Returns False to NOT suppress exceptions, allowing them
        to propagate after cleanup completes.

        Args:
            exc_type: Exception type if an exception was raised
            exc_val: Exception value
            exc_tb: Exception traceback

        Returns:
            False (never suppress exceptions)
        """
        if not self._entered:
            # Never entered context, nothing to clean up
            return False

        try:
            # 1. Determine and set final state
            self._set_final_state(exc_type, exc_val)

            # 2. Shutdown agent pool (always attempt)
            if self.pool is not None and self._pool_initialized:
                try:
                    await self.pool.shutdown()
                    logger.debug("[crew-resource] Pool shutdown complete")
                except Exception:
                    logger.exception("[crew-resource] Error during pool shutdown")

            # 3. Finalize output persistence
            if self.process is not None:
                try:
                    self.process.finalize_output(self.final_status)
                    logger.debug(f"[crew-resource] Output finalized with status: {self.final_status}")
                except Exception:
                    logger.exception("[crew-resource] Error during output finalization")

        except Exception:
            # Never let cleanup exceptions mask the original exception
            logger.exception("[crew-resource] Error during cleanup sequence")

        # Store CancelledError for orchestrator to re-raise
        if exc_type is asyncio.CancelledError and exc_val is not None:
            self.cancelled_error = exc_val
            logger.info("[crew-resource] CancelledError captured, will re-raise after cleanup")

        # Return False to NOT suppress the exception
        # This allows cleanup to complete, then the exception propagates
        return False

    def _set_final_state(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
    ) -> None:
        """Set final execution state based on exception type.

        Args:
            exc_type: Exception type
            exc_val: Exception value
        """
        from xbot.agent.crew.state import CrewPhase

        if exc_type is asyncio.CancelledError:
            # Cancelled - transition to ABORTING -> ABORTED
            self.final_status = "aborted"
            try:
                self.state_manager.transition_crew(CrewPhase.ABORTING, "CancelledError")
                self.state_manager.transition_crew(CrewPhase.ABORTED)
            except Exception:
                # State transition may fail, but cleanup continues
                logger.warning("[crew-resource] State transition failed during cancellation")
            logger.info("[crew-resource] Crew execution aborted due to cancellation")

        elif exc_type is KeyboardInterrupt:
            # Keyboard interrupt - similar to cancellation
            self.final_status = "aborted"
            try:
                self.state_manager.transition_crew(CrewPhase.ABORTING, "KeyboardInterrupt")
                self.state_manager.transition_crew(CrewPhase.ABORTED)
            except Exception:
                logger.warning("[crew-resource] State transition failed during keyboard interrupt")
            logger.info("[crew-resource] Crew execution aborted by keyboard interrupt")

        elif exc_type is not None:
            # Other exception - transition to FAILED
            self.final_status = "failed"
            try:
                error_msg = str(exc_val) if exc_val else "Unknown error"
                self.state_manager.transition_crew(CrewPhase.FAILED, error_msg)
            except Exception:
                logger.warning("[crew-resource] State transition failed during error handling")
            logger.error(f"[crew-resource] Crew execution failed: {exc_val}")

        else:
            # No exception - determine status from results
            self.final_status = self._determine_success_status()

    def _determine_success_status(self) -> str:
        """Determine final status when no exception occurred.

        Returns:
            "completed" if all tasks succeeded, "failed" otherwise
        """
        from xbot.agent.crew.state import CrewPhase

        # Check state manager's final phase
        current_phase = self.state_manager.crew_phase

        if current_phase in (CrewPhase.ABORTED,):
            return "aborted"
        elif current_phase == CrewPhase.FAILED:
            return "failed"

        # Try to reach COMPLETED through valid transitions
        try:
            if current_phase == CrewPhase.RUNNING:
                self.state_manager.transition_crew(CrewPhase.COMPLETING)
        except Exception:
            pass

        try:
            if self.state_manager.crew_phase == CrewPhase.COMPLETING:
                self.state_manager.transition_crew(CrewPhase.COMPLETED)
        except Exception:
            pass

        # Check results for failures
        if any(r.status == "failed" for r in self.results):
            return "failed"
        elif any(r.status == "human_rejected" for r in self.results):
            return "aborted"

        return "completed"

    async def initialize_pool(self, only_roles: set[str] | None = None) -> None:
        """Initialize the agent pool.

        This is called after entering the context, allowing for
        checkpoint resume scenarios.

        Args:
            only_roles: Optional set of roles to initialize (for resume)
        """
        from xbot.agent.crew.agent_pool import AgentPool
        from xbot.agent.crew.state import CrewPhase

        self.pool = AgentPool(
            self.crew_config,
            self.xbot_config,
            self.permission_handler,
        )
        await self.pool.initialize(only_roles=only_roles)
        self._pool_initialized = True

        self.state_manager.transition_crew(CrewPhase.RUNNING)
        logger.debug("[crew-resource] Agent pool initialized")

    def set_process(self, process: "BaseProcess") -> None:
        """Set the process for output finalization.

        Args:
            process: The execution process
        """
        self.process = process

    def set_results(self, results: list["TaskResult"]) -> None:
        """Set the execution results for status determination.

        Args:
            results: List of task execution results
        """
        self.results = results

    def get_cancelled_error(self) -> asyncio.CancelledError | None:
        """Get the captured CancelledError for re-raising.

        Returns:
            The captured CancelledError, or None if not cancelled
        """
        return self.cancelled_error

    def should_re_raise_cancelled(self) -> bool:
        """Check if CancelledError should be re-raised.

        Returns:
            True if a CancelledError was captured during cleanup
        """
        return self.cancelled_error is not None
