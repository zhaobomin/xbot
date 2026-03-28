"""Crew orchestrator: top-level entry point for crew execution and recovery."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from xbot.agent.crew.agent_pool import AgentPool
from xbot.agent.crew.context import CrewExecutionContext, load_checkpoint
from xbot.agent.crew.models import CrewConfig, CrewResult, ProcessType, TaskResult
from xbot.agent.crew.process import HierarchicalProcess, SequentialProcess
from xbot.agent.crew.state import CrewPhase, CrewStateManager, TaskPhase
from xbot.agent.permission_handler import BasePermissionHandler
from xbot.config.schema import Config


class CrewOrchestrator:
    """Assembles pool, context, state manager, and process to run a crew.

    Usage::

        orch = CrewOrchestrator(crew_config, xbot_config, permission_handler)
        result = await orch.run()
    """

    def __init__(
        self,
        crew_config: CrewConfig,
        xbot_config: Config,
        permission_handler: BasePermissionHandler,
        config_path: str = "",
        on_progress: Callable[..., None] | None = None,
    ) -> None:
        self.crew_config = crew_config
        self.xbot_config = xbot_config
        self.permission_handler = permission_handler
        self.config_path = config_path
        self.on_progress = on_progress

    async def run(self, checkpoint_path: Path | None = None) -> CrewResult:
        """Main execution flow.

        Args:
            checkpoint_path: Optional path to a checkpoint JSON for resume.

        Returns:
            CrewResult summarising the execution.
        """
        started_at = datetime.now()
        wall_start = time.perf_counter()

        # Initialise state manager
        task_names = [t.name for t in self.crew_config.tasks]
        state_manager = CrewStateManager(
            task_names=task_names,
            task_definitions=self.crew_config.tasks,
        )

        context = CrewExecutionContext()

        # Handle resume from checkpoint
        only_roles: set[str] | None = None
        if checkpoint_path:
            try:
                cp = load_checkpoint(checkpoint_path)
                self._apply_checkpoint(cp, context, state_manager)
                # Only initialise backends for remaining tasks
                # Check all terminal states, not just COMPLETED
                terminal_phases = {
                    TaskPhase.COMPLETED, TaskPhase.SKIPPED, TaskPhase.FAILED, TaskPhase.REJECTED
                }
                remaining_agents = {
                    t.agent for t in self.crew_config.tasks
                    if state_manager.get_task_phase(t.name) not in terminal_phases
                }
                only_roles = remaining_agents or None
                self._progress(
                    f"Resumed from checkpoint: "
                    f"{len(cp.get('completed_tasks', []))} completed tasks, "
                    f"next: {cp.get('next_task', '?')}"
                )
            except Exception:
                logger.exception("[crew] Failed to load checkpoint — starting fresh")

        # Initialise agent pool
        state_manager.transition_crew(CrewPhase.INITIALIZING)
        pool = AgentPool(self.crew_config, self.xbot_config, self.permission_handler)
        try:
            await pool.initialize(only_roles=only_roles)
        except Exception as exc:
            state_manager.transition_crew(CrewPhase.FAILED, str(exc))
            return CrewResult(
                crew_name=self.crew_config.name,
                task_results=[],
                status="failed",
                total_time=time.perf_counter() - wall_start,
                summary=f"Failed to initialise agent pool: {exc}",
            )

        state_manager.transition_crew(CrewPhase.RUNNING)

        # Select process
        process_cls = (
            HierarchicalProcess
            if self.crew_config.process == ProcessType.hierarchical
            else SequentialProcess
        )
        process = process_cls(
            pool=pool,
            context=context,
            permission_handler=self.permission_handler,
            crew_config=self.crew_config,
            state_manager=state_manager,
            config_path=self.config_path,
            started_at=started_at,
            on_progress=self.on_progress,
            llm_repair=self._get_llm_repair_callable(),
        )

        # Execute
        results: list[TaskResult] = []
        final_status = "completed"  # Default
        cancelled_error = None
        try:
            results = await process.execute(self.crew_config.tasks)
        except KeyboardInterrupt:
            logger.info("[crew] Interrupted by user (Ctrl+C)")
            state_manager.transition_crew(CrewPhase.ABORTING, "KeyboardInterrupt")
            state_manager.transition_crew(CrewPhase.ABORTED)
            final_status = "aborted"
        except asyncio.CancelledError as e:
            logger.info("[crew] Cancelled by async cancellation")
            state_manager.transition_crew(CrewPhase.ABORTING, "CancelledError")
            state_manager.transition_crew(CrewPhase.ABORTED)
            final_status = "aborted"
            # Store the error to re-raise after cleanup
            cancelled_error = e
        except Exception as exc:
            logger.exception("[crew] Unhandled exception during execution")
            try:
                state_manager.transition_crew(CrewPhase.FAILED, str(exc))
            except Exception:
                pass
            final_status = "failed"
        finally:
            await pool.shutdown()

        # Determine final status
        total_time = time.perf_counter() - wall_start

        if state_manager.crew_phase in (CrewPhase.ABORTED,):
            status = "aborted"
        elif state_manager.crew_phase == CrewPhase.FAILED:
            status = "failed"
        else:
            # Try to reach COMPLETED through valid transitions
            if state_manager.crew_phase == CrewPhase.RUNNING:
                try:
                    state_manager.transition_crew(CrewPhase.COMPLETING)
                except Exception:
                    pass
            if state_manager.crew_phase == CrewPhase.COMPLETING:
                try:
                    state_manager.transition_crew(CrewPhase.COMPLETED)
                except Exception:
                    pass
            # Determine status from task results
            if any(r.status == "failed" for r in results):
                status = "failed"
            elif any(r.status == "human_rejected" for r in results):
                status = "aborted"
            else:
                status = "completed"

        # Finalize output persistence
        process.finalize_output(status)

        # Re-raise CancelledError after cleanup is complete
        if cancelled_error is not None:
            raise cancelled_error

        summary = self._build_summary(results, total_time)

        return CrewResult(
            crew_name=self.crew_config.name,
            task_results=results,
            status=status,
            total_time=total_time,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Checkpoint resume
    # ------------------------------------------------------------------

    def _apply_checkpoint(
        self,
        cp: dict[str, Any],
        context: CrewExecutionContext,
        state_manager: CrewStateManager,
    ) -> None:
        """Inject completed tasks from a checkpoint into context and state.

        Only tasks with status 'success' or 'completed' are restored.
        Failed/skipped tasks are logged and left as PENDING for re-execution.
        """
        completed_count = 0
        retry_count = 0

        for completed in cp.get("completed_tasks", []):
            status = completed.get("status", "success")

            # Only restore successful tasks
            if status not in ("success", "completed"):
                retry_count += 1
                logger.info(
                    f"[crew-checkpoint] Task '{completed.get('name')}' had status "
                    f"'{status}' — will be re-executed"
                )
                continue

            result = TaskResult(
                task_name=completed["name"],
                agent_name=completed["agent"],
                output=completed.get("output", ""),
                status=completed.get("status", "success"),
                started_at=datetime.fromisoformat(completed["started_at"]),
                finished_at=datetime.fromisoformat(completed["finished_at"]),
                human_edited_output=completed.get("human_edited_output"),
                human_annotations=completed.get("human_annotations", []),
                human_briefing_input=completed.get("human_briefing_input"),
            )
            context.add_result(result)
            state_manager.force_task_phase(completed["name"], TaskPhase.COMPLETED)
            completed_count += 1

        if retry_count > 0:
            logger.warning(
                f"[crew-checkpoint] {retry_count} task(s) with failed/skipped status "
                f"will be re-executed from scratch"
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_summary(self, results: list[TaskResult], total_time: float) -> str:
        lines = [f"Crew '{self.crew_config.name}' finished in {total_time:.1f}s"]
        for r in results:
            marker = {"success": "+", "completed": "+", "failed": "x", "skipped": "-", "human_rejected": "!"}
            m = marker.get(r.status, "?")
            lines.append(f"  [{m}] {r.task_name} ({r.agent_name}) — {r.status}")
        return "\n".join(lines)

    def _progress(self, message: str, **kwargs: Any) -> None:
        logger.info(f"[crew] {message}")
        if self.on_progress:
            self.on_progress(message, **kwargs)

    def _get_llm_repair_callable(self) -> Callable[[str], str] | None:
        """Get an LLM callable for output repair.

        Returns None if no simple LLM call mechanism is available,
        in which case repair will gracefully fail.

        Returns:
            Callable that takes a prompt and returns LLM response, or None.
        """
        # For now, return None - repair will gracefully fail.
        # This can be enhanced later to use a dedicated repair LLM client.
        # The orchestrator context doesn't have a simple sync LLM call method,
        # and the agent backends are async-based.
        return None
