"""Crew orchestrator: top-level entry point for crew execution and recovery."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from xbot.agent.crew.context import CrewExecutionContext, load_checkpoint
from xbot.agent.crew.models import CrewConfig, CrewResult, ProcessType, TaskResult
from xbot.agent.crew.process import HierarchicalProcess, SequentialProcess
from xbot.agent.crew.resource_manager import CrewResourceManager
from xbot.agent.crew.state import CrewPhase, CrewStateManager, TaskPhase
from xbot.agent.crew.validation import CrewValidator
from xbot.agent.interaction.permission import BasePermissionHandler
from xbot.config.schema import Config
from xbot.logging import get_logger

logger = get_logger(__name__)
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

        # 0. Pre-flight validation (fail fast)
        # Validate crew config and log warnings
        config_warnings = CrewValidator.validate_crew_config(self.crew_config)
        CrewValidator.log_warnings(config_warnings)

        # Validate all tasks upfront
        available_agents = set(self.crew_config.agents.keys())
        task_errors = CrewValidator.validate_all_tasks(self.crew_config.tasks, available_agents)
        if task_errors:
            # Return early with validation failure
            error_messages = [e.to_result_message() for e in task_errors]
            summary = "Validation failed:\n" + "\n".join(f"  - {m}" for m in error_messages)
            logger.error(f"[crew] {summary}")
            return CrewResult(
                crew_name=self.crew_config.name,
                task_results=[
                    TaskResult(
                        task_name=e.task_name,
                        agent_name="",
                        output=e.to_result_message(),
                        status="failed",
                        started_at=started_at,
                        finished_at=datetime.now(),
                    )
                    for e in task_errors
                ],
                status="failed",
                total_time=time.perf_counter() - wall_start,
                summary=summary,
            )

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

        # Initialise state to INITIALIZING
        state_manager.transition_crew(CrewPhase.INITIALIZING)

        # Use resource manager for unified cleanup flow
        manager = CrewResourceManager(
            crew_config=self.crew_config,
            xbot_config=self.xbot_config,
            permission_handler=self.permission_handler,
            state_manager=state_manager,
            started_at=started_at,
        )

        try:
            async with manager:
                # Initialize pool within the context (allows checkpoint resume)
                try:
                    await manager.initialize_pool(only_roles=only_roles)
                except Exception as exc:
                    # Pool init failed - cleanup handled by __aexit__
                    state_manager.transition_crew(CrewPhase.FAILED, str(exc))
                    return CrewResult(
                        crew_name=self.crew_config.name,
                        task_results=[],
                        status="failed",
                        total_time=time.perf_counter() - wall_start,
                        summary=f"Failed to initialise agent pool: {exc}",
                    )

                # Select and create process
                process_cls = (
                    HierarchicalProcess
                    if self.crew_config.process == ProcessType.hierarchical
                    else SequentialProcess
                )
                process = process_cls(
                    pool=manager.pool,
                    context=context,
                    permission_handler=self.permission_handler,
                    crew_config=self.crew_config,
                    state_manager=state_manager,
                    config_path=self.config_path,
                    started_at=started_at,
                    on_progress=self.on_progress,
                    llm_repair=self._get_llm_repair_callable(),
                )
                manager.set_process(process)

                # Execute tasks
                results = await process.execute(self.crew_config.tasks)
                manager.set_results(results)

        except asyncio.CancelledError:
            # Cleanup already completed in __aexit__
            # Just return the result with aborted status
            pass
        except KeyboardInterrupt:
            # Cleanup already completed in __aexit__
            pass

        # Calculate total time after cleanup
        total_time = time.perf_counter() - wall_start

        # Re-raise CancelledError if it was captured
        if manager.should_re_raise_cancelled():
            raise manager.get_cancelled_error()

        # Build summary and return result
        results = manager.results
        summary = self._build_summary(results, total_time)

        return CrewResult(
            crew_name=self.crew_config.name,
            task_results=results,
            status=manager.final_status,
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

        Note: Uses a dedicated thread executor to run the async backend so that
        calling code can remain synchronous without nesting asyncio.run() inside
        an already-running event loop (which raises RuntimeError on Python 3.10+).

        Returns:
            Callable that takes a prompt and returns LLM response, or None.
        """
        import threading

        # Guard: only proceed if we have a real Config instance.
        # MagicMock / test stubs will fail this check and return None gracefully.
        if not isinstance(self.xbot_config, Config):
            return None

        try:
            from xbot.agent.protocol import AgentContext
            from xbot.agent.service import AgentService
            from xbot.agent.types import AgentConfig

            agents_config = self.xbot_config.agents.model_copy(deep=True)
            shared_resources = {
                "workspace": self.crew_config.workspace,
                "config": self.xbot_config,
                "tools_config": self.xbot_config.tools,
                "permission_handler": self.permission_handler,
                "bus": None,
                "session_manager": None,
            }

            # Create a lightweight service for repair
            agent_config = AgentConfig(
                model=agents_config.defaults.model,
                system_prompt="",  # System prompt is built dynamically by ContextBuilder
                mcp_servers=getattr(agents_config.defaults, "mcp_servers", {}),
                agents=getattr(agents_config.defaults, "agents", []),
            )
            service = AgentService(agent_config, shared_resources)

            async def _init_and_call(prompt: str) -> str:
                """Initialize service and run a single repair call."""
                await service.initialize()
                try:
                    session_key = f"repair_{hash(prompt) % 10000}"
                    context = AgentContext(
                        session_key=session_key,
                        prompt=prompt,
                        channel="repair",
                        chat_id="repair",
                        media=None,
                    )
                    content = ""
                    async for response in service.process(context):
                        if response.content:
                            content = response.content
                        elif response.delta_content:
                            content += response.delta_content
                    return content
                finally:
                    shutdown = getattr(service, "shutdown", None)
                    if callable(shutdown):
                        await shutdown()

            def repair_callable(prompt: str) -> str:
                """Run the async repair call in a dedicated thread with its own event loop.

                This avoids the 'asyncio.run() cannot be called when another event
                loop is running' error that occurs when called from within an async
                context on Python 3.10+.
                """
                result: list[str] = []
                exception: list[BaseException] = []

                def _thread_target() -> None:
                    import asyncio as _asyncio
                    loop = _asyncio.new_event_loop()
                    _asyncio.set_event_loop(loop)
                    try:
                        result.append(loop.run_until_complete(_init_and_call(prompt)))
                    except Exception as exc:
                        exception.append(exc)
                    finally:
                        loop.close()

                t = threading.Thread(target=_thread_target, daemon=True)
                t.start()
                t.join(timeout=120)  # 2-minute hard timeout for repair
                if exception:
                    raise exception[0]
                return result[0] if result else ""

            return repair_callable
        except Exception as e:
            logger.warning(f"Failed to create LLM repair callable: {e}")
            return None
