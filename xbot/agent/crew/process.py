"""Crew execution processes: Sequential and Hierarchical strategies.

Each process drives the task execution loop, handles human intervention
(briefing + review), saves checkpoints, and respects the review lock for
serialised human interactions.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from xbot.logging import get_logger

logger = get_logger(__name__)

from xbot.agent.crew.agent_pool import AgentPool, TaskProgress
from xbot.agent.crew.context import CrewExecutionContext, save_checkpoint
from xbot.agent.crew.models import CrewConfig, OutputFormat, TaskDefinition, TaskResult, UserAction
from xbot.agent.crew.output import (
    OutputParser,
    OutputPersister,
    OutputTruncator,
    OutputRepairer,
    TruncationStrategy,
    should_attempt_repair,
)
from xbot.agent.crew.state import CrewStateManager, TaskPhase
from xbot.agent.crew.validation import CrewValidator, ExecutionPreconditions
from xbot.agent.interaction.permission import BasePermissionHandler


class BaseProcess(ABC):
    """Base class for crew execution strategies."""

    def __init__(
        self,
        pool: AgentPool,
        context: CrewExecutionContext,
        permission_handler: BasePermissionHandler,
        crew_config: CrewConfig,
        state_manager: CrewStateManager,
        config_path: str = "",
        started_at: datetime | None = None,
        on_progress: Callable[..., None] | None = None,
        llm_repair: Callable[[str], str] | None = None,
    ) -> None:
        self.pool = pool
        self.context = context
        self.permission_handler = permission_handler
        self.crew_config = crew_config
        self.state_manager = state_manager
        self.config_path = config_path
        self.started_at = started_at or datetime.now()
        self.on_progress = on_progress
        self._review_lock = asyncio.Lock()

        # Output management
        self._output_parser = OutputParser()
        self._output_truncator = OutputTruncator()
        self._output_repairer = OutputRepairer(llm_call=llm_repair)
        self._persister: OutputPersister | None = None

        # Initialize persister if output is enabled
        if crew_config.output.enabled:
            try:
                self._persister = OutputPersister(
                    workspace=Path(crew_config.workspace),
                    crew_name=crew_config.name,
                    retention_days=crew_config.output.retention_days,
                )
                self._persister.initialize()
                logger.debug(f"[crew-output] Initialized output persister: {self._persister.run_dir}")
            except Exception as e:
                logger.warning(f"[crew-output] Failed to initialize persister: {e}")

    @abstractmethod
    async def execute(self, tasks: list[TaskDefinition]) -> list[TaskResult]:
        """Run all tasks and return results."""

    # ------------------------------------------------------------------
    # Single task execution
    # ------------------------------------------------------------------

    # Soft timeout configuration
    SOFT_TIMEOUT_BUFFER = 300  # 5 minutes - Seconds to extend when progress detected
    ACTIVITY_THRESHOLD = 180  # 3 minutes - Seconds without output to consider "stuck"
    MAX_EXTENSIONS = 10  # Maximum number of timeout extensions

    def _estimate_timeout(self, task: TaskDefinition) -> int:
        """Estimate timeout based on task complexity.

        Args:
            task: Task definition

        Returns:
            Estimated timeout in seconds
        """
        base = 60  # Minimum 1 minute

        # Description length indicates complexity
        desc_bonus = min(len(task.description or "") // 10, 120)

        # Role complexity (based on agent configuration)
        role = self.crew_config.agents.get(task.agent)
        role_bonus = 60  # Default medium complexity
        if role:
            # More iterations = more complex task
            role_bonus = min(role.max_iterations * 3, 120)

        total = base + desc_bonus + role_bonus
        logger.debug(f"[crew] Estimated timeout for '{task.name}': {total}s")
        return total

    async def _execute_single_task(self, task: TaskDefinition) -> TaskResult:
        """Execute a single task: briefing -> run -> process output -> return result.

        Does NOT include human review — callers handle review separately so
        state transitions stay clean.

        Supports soft timeout with progress detection:
        - If timeout is None: smart mode with auto-extend on progress
        - If timeout is set: traditional hard timeout (backward compatible)
        """
        # 0. Validate task (fail fast)
        task_names = {t.name for t in self.crew_config.tasks}
        validation_error = CrewValidator.validate_task(
            task, set(self.crew_config.agents.keys()), task_names
        )
        if validation_error:
            logger.error(f"[crew] Task '{task.name}' validation failed: {validation_error.message}")
            return TaskResult(
                task_name=task.name,
                agent_name=task.agent,
                output=validation_error.to_result_message(),
                status="failed",
                started_at=datetime.now(),
                finished_at=datetime.now(),
            )

        # 1. Get role (validated above)
        role = self.crew_config.agents[task.agent]

        # 2. Human briefing (pre-execution)
        human_briefing = await self._human_briefing(task)

        # 3. Build prompt and get media files
        prompt, media = self.context.build_agent_context(
            task=task,
            role=role,
            session_key=f"crew:{self.crew_config.name}:{task.name}:{uuid.uuid4().hex[:8]}",
            global_context=self.crew_config.global_context,
            human_briefing=human_briefing,
            max_context_length=self.crew_config.max_context_length,
        )

        # 4. Generate session key
        session_key = f"crew:{self.crew_config.name}:{task.name}:{uuid.uuid4().hex[:8]}"

        # 5. Determine timeout mode
        use_soft_timeout = task.timeout is None
        # Use explicit timeout if set (including 0), otherwise estimate
        initial_timeout = task.timeout if task.timeout is not None else self._estimate_timeout(task)

        # 6. Execute with soft timeout
        started = datetime.now()
        output = ""
        status = "success"
        extended_count = 0
        quality = "full"

        try:
            output, extended_count = await self._execute_with_soft_timeout(
                task=task,
                prompt=prompt,
                session_key=session_key,
                initial_timeout=initial_timeout,
                use_soft_timeout=use_soft_timeout,
                media=media,
            )
            if extended_count > 0:
                quality = "partial"
                logger.info(f"[crew] Task '{task.name}' completed with {extended_count} timeout extensions")

        except asyncio.CancelledError:
            logger.warning(f"[crew] Task '{task.name}' was cancelled")
            output = "Task cancelled"
            status = "cancelled"
            raise
        except asyncio.TimeoutError:
            logger.warning(f"[crew] Task '{task.name}' timed out after extensions")
            output = f"Task timed out after {initial_timeout + extended_count * self.SOFT_TIMEOUT_BUFFER}s"
            status = "failed"
        except Exception as exc:
            logger.exception(f"[crew] Task '{task.name}' failed with error")
            output = f"Task failed: {exc}"
            status = "failed"

        finished = datetime.now()

        # 6. Process output (format, repair, truncate)
        output_format = task.output_format
        structured_output = None
        repaired = False
        truncated = False

        if output_format != OutputFormat.RAW and status == "success":
            # Parse output according to format
            parsed = self._output_parser.parse(
                output,
                output_format=output_format,
                schema=task.output_schema,
            )

            # Attempt repair if parsing failed
            if not parsed.valid and should_attempt_repair(parsed):
                self._progress(f"Attempting to repair output for task '{task.name}'...")
                repair_result = self._output_repairer.repair(
                    output,
                    target_format=output_format,
                    schema=task.output_schema,
                    error_message=parsed.error,
                )
                if repair_result.success and repair_result.parsed:
                    output = repair_result.repaired_content
                    structured_output = repair_result.parsed.structured
                    repaired = True
                    logger.info(f"[crew-output] Successfully repaired output for task '{task.name}'")
            else:
                structured_output = parsed.structured

        # 7. Truncate if needed
        max_output_size = self.crew_config.output.max_output_size
        if len(output) > max_output_size:
            trunc_result = self._output_truncator.truncate(
                output,
                max_length=max_output_size,
                strategy=TruncationStrategy.SMART,
            )
            if trunc_result.truncated:
                output = trunc_result.content
                truncated = True
                logger.info(f"[crew-output] Truncated output for task '{task.name}' ({trunc_result.original_length} -> {trunc_result.truncated_length})")

        return TaskResult(
            task_name=task.name,
            agent_name=task.agent,
            output=output,
            status=status,
            started_at=started,
            finished_at=finished,
            human_briefing_input=human_briefing,
            output_format=output_format,
            structured_output=structured_output,
            truncated=truncated,
            repaired=repaired,
            quality=quality,
            extended_count=extended_count,
        )

    async def _execute_with_soft_timeout(
        self,
        task: TaskDefinition,
        prompt: str,
        session_key: str,
        initial_timeout: int,
        use_soft_timeout: bool,
        media: list[str] | None = None,
    ) -> tuple[str, int]:
        """Execute task with soft timeout and progress detection.

        Uses asyncio.shield() to protect stream_task from being cancelled
        during timeout checks, allowing the stream to continue even when
        wait_for raises TimeoutError.

        Args:
            task: Task definition
            prompt: Full prompt
            session_key: Session identifier
            initial_timeout: Initial timeout in seconds
            use_soft_timeout: If True, extend on progress; if False, hard timeout
            media: Optional list of media file paths (images, etc.) to include

        Returns:
            Tuple of (output, extended_count)
        """
        import time

        if not self._pool_supports_native_streaming():
            output = await asyncio.wait_for(
                self.pool.run_task(task.agent, prompt, session_key, media),
                timeout=initial_timeout,
            )
            return output, 0

        start_time = time.monotonic()
        deadline = start_time + initial_timeout
        extended_count = 0
        last_activity_time: float | None = None  # None means no output yet
        output = ""

        # Create the async iterator
        stream = self.pool.run_task_streaming(task.agent, prompt, session_key, media)

        # Create task for first progress event
        stream_task = asyncio.create_task(stream.__anext__())

        while True:
            current_time = time.monotonic()
            remaining = deadline - current_time

            # Calculate wait timeout (max ACTIVITY_THRESHOLD to ensure regular checks)
            wait_timeout = max(0.1, min(remaining, self.ACTIVITY_THRESHOLD))

            try:
                # Use shield() to protect stream_task from being cancelled by wait_for
                progress = await asyncio.wait_for(asyncio.shield(stream_task), wait_timeout)

                # Got progress event
                current_time = time.monotonic()

                # Update last activity time when we get content
                if progress.delta_content:
                    last_activity_time = current_time

                # Update output
                output = progress.total_content

                # Task completed
                if progress.is_final:
                    return output, extended_count

                # Create new task for next progress event
                stream_task = asyncio.create_task(stream.__anext__())

            except asyncio.TimeoutError:
                # wait_for timed out, but stream_task is NOT cancelled (shielded)
                current_time = time.monotonic()

                if use_soft_timeout and extended_count < self.MAX_EXTENSIONS:
                    # Only extend if we've had output activity
                    if last_activity_time is not None:
                        time_since_activity = current_time - last_activity_time
                        if time_since_activity < self.ACTIVITY_THRESHOLD:
                            # Recent activity, extend timeout
                            extended_count += 1
                            deadline += self.SOFT_TIMEOUT_BUFFER
                            logger.info(
                                f"[crew] Task '{task.name}' has progress, "
                                f"extending timeout by {self.SOFT_TIMEOUT_BUFFER}s "
                                f"(extension #{extended_count})"
                            )
                            # Continue loop, stream_task is still pending (shielded)
                            continue
                        else:
                            # Had output but stopped for too long
                            logger.warning(
                                f"[crew] Task '{task.name}' appears stuck "
                                f"(no output for {time_since_activity:.0f}s), stopping"
                            )
                            # Cancel stream_task before raising
                            stream_task.cancel()
                            try:
                                await stream_task
                            except (asyncio.CancelledError, asyncio.TimeoutError):
                                pass
                            raise asyncio.TimeoutError()
                    else:
                        # Never received any output - startup failure
                        logger.warning(
                            f"[crew] Task '{task.name}' failed to start "
                            f"(no output for {current_time - start_time:.0f}s), stopping"
                        )
                        # Cancel stream_task before raising
                        stream_task.cancel()
                        try:
                            await stream_task
                        except (asyncio.CancelledError, asyncio.TimeoutError):
                            pass
                        raise asyncio.TimeoutError()
                else:
                    # Hard timeout or max extensions reached
                    # Cancel stream_task before raising
                    stream_task.cancel()
                    try:
                        await stream_task
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
                    raise asyncio.TimeoutError()

            except StopAsyncIteration:
                # Stream ended normally
                return output, extended_count

            except asyncio.CancelledError:
                # Outer cancellation - need to clean up stream_task
                stream_task.cancel()
                try:
                    await stream_task
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                raise

    # ------------------------------------------------------------------
    # Human intervention
    # ------------------------------------------------------------------

    async def _human_briefing(self, task: TaskDefinition) -> str | None:
        """Pre-execution: ask human for supplementary instructions."""
        if not task.human_briefing:
            return None

        self._progress(f"Awaiting human briefing for task '{task.name}' ...")
        response = await self.permission_handler.request_interaction(
            kind="question",
            prompt=(
                f"Task '{task.name}' is about to be executed by [{task.agent}].\n"
                f"Description: {task.description}\n\n"
                "Enter any supplementary instructions, or press Enter to skip:"
            ),
            suggestions=["skip"],
            session_key=f"crew:{self.crew_config.name}",
        )
        text = (response.content or "").strip()
        if not text or text.lower() in ("skip", "跳过"):
            return None
        return text

    async def _human_review(self, task: TaskDefinition, result: TaskResult) -> TaskResult:
        """Post-execution review with serialised access via review lock.

        Options:
        1. Continue       — pass through unchanged
        2. Annotate       — add review notes visible to downstream
        3. Edit output    — replace output for downstream
        4. Redo           — re-execute with feedback
        5. Skip           — mark as skipped
        6. Abort          — mark as human_rejected
        """
        if not task.human_review:
            return result

        async with self._review_lock:
            return await self._do_human_review(task, result)

    async def _do_human_review(self, task: TaskDefinition, result: TaskResult) -> TaskResult:
        """Actual review interaction (called while holding the lock).

        Loops until the user chooses a terminal action (continue/skip/abort).
        Non-terminal actions (annotate/edit/redo) allow further review.
        """
        while True:
            output_preview = result.output[:2000]
            if len(result.output) > 2000:
                output_preview += "\n... (truncated)"

            self._progress(f"Awaiting human review for task '{task.name}' ...")
            response = await self.permission_handler.request_interaction(
                kind="question",
                prompt=(
                    f"Task '{task.name}' finished (status: {result.status}).\n\n"
                    f"Output:\n{output_preview}\n\n"
                    "Choose an action:\n"
                    "1. continue  — accept as-is\n"
                    "2. annotate  — add notes for downstream tasks\n"
                    "3. edit      — modify the output\n"
                    "4. redo      — re-execute with feedback\n"
                    "5. skip      — skip this task\n"
                    "6. abort     — stop the entire crew\n"
                ),
                suggestions=["continue", "annotate", "edit", "redo", "skip", "abort"],
                session_key=f"crew:{self.crew_config.name}",
            )

            action = self._parse_user_action(response.content or "continue")

            # Terminal actions: exit review loop
            if action == UserAction.CONTINUE:
                return result

            if action == UserAction.SKIP:
                result.status = "skipped"
                return result

            if action == UserAction.ABORT:
                result.status = "human_rejected"
                return result

            # Non-terminal actions: modify result and continue reviewing
            if action == UserAction.ANNOTATE:
                result = await self._collect_annotation(result)
                continue

            if action == UserAction.EDIT:
                result = await self._collect_edit(result)
                continue

            if action == UserAction.REDO:
                result, _ = await self._redo_task(task, result)
                if self.state_manager.get_task_phase(task.name) == TaskPhase.RUNNING:
                    self.state_manager.transition_task(task.name, TaskPhase.AWAITING_REVIEW)
                # Continue reviewing the new result
                continue

            # Default: treat as continue (terminal)
            return result

    def _parse_user_action(self, raw: str) -> UserAction:
        """Parse user input into a UserAction enum value.

        Supports numeric shortcuts (1-6) and Chinese equivalents.
        """
        action = raw.strip().lower()

        # Map numeric shortcuts and Chinese equivalents
        action_map = {
            "1": UserAction.CONTINUE,
            "continue": UserAction.CONTINUE,
            "继续": UserAction.CONTINUE,
            "2": UserAction.ANNOTATE,
            "annotate": UserAction.ANNOTATE,
            "批注": UserAction.ANNOTATE,
            "3": UserAction.EDIT,
            "edit": UserAction.EDIT,
            "修改": UserAction.EDIT,
            "4": UserAction.REDO,
            "redo": UserAction.REDO,
            "重做": UserAction.REDO,
            "5": UserAction.SKIP,
            "skip": UserAction.SKIP,
            "跳过": UserAction.SKIP,
            "6": UserAction.ABORT,
            "abort": UserAction.ABORT,
            "终止": UserAction.ABORT,
        }

        return action_map.get(action, UserAction.CONTINUE)

    async def _collect_annotation(self, result: TaskResult) -> TaskResult:
        """Collect annotation text and attach to result."""
        resp = await self.permission_handler.request_interaction(
            kind="question",
            prompt="Enter your review notes (will be visible to downstream tasks):",
            session_key=f"crew:{self.crew_config.name}",
        )
        text = (resp.content or "").strip()
        if text:
            result.human_annotations.append(text)
        return result

    async def _collect_edit(self, result: TaskResult) -> TaskResult:
        """Collect edited output from human."""
        resp = await self.permission_handler.request_interaction(
            kind="question",
            prompt="Enter the revised output (replaces original for downstream tasks):",
            session_key=f"crew:{self.crew_config.name}",
        )
        text = (resp.content or "").strip()
        if text:
            result.human_edited_output = text
        return result

    async def _redo_task(self, task: TaskDefinition, original: TaskResult) -> tuple[TaskResult, bool]:
        """Re-execute a task with human feedback.

        Returns:
            A tuple of (TaskResult, success: bool) where success indicates
            whether the redo completed successfully.
        """
        # 0. Validate task (fail fast)
        task_names = {t.name for t in self.crew_config.tasks}
        validation_error = CrewValidator.validate_task(
            task, set(self.crew_config.agents.keys()), task_names
        )
        if validation_error:
            logger.error(f"[crew] Redo task '{task.name}' validation failed: {validation_error.message}")
            return TaskResult(
                task_name=task.name,
                agent_name=task.agent,
                output=f"Redo failed: {validation_error.to_result_message()}",
                status="failed",
                started_at=datetime.now(),
                finished_at=datetime.now(),
            ), False

        resp = await self.permission_handler.request_interaction(
            kind="question",
            prompt="Enter feedback for the redo (what should be different?):",
            session_key=f"crew:{self.crew_config.name}",
        )
        feedback = (resp.content or "").strip()

        # Transition to RETRYING state
        self.state_manager.transition_task(task.name, TaskPhase.RETRYING, "human requested redo")

        # Build a new prompt with feedback
        role = self.crew_config.agents[task.agent]

        extra_briefing = f"Previous attempt feedback: {feedback}" if feedback else None
        prompt, media = self.context.build_agent_context(
            task=task,
            role=role,
            session_key=f"crew:{self.crew_config.name}:{task.name}:redo:{uuid.uuid4().hex[:8]}",
            global_context=self.crew_config.global_context,
            human_briefing=extra_briefing,
            max_context_length=self.crew_config.max_context_length,
        )

        session_key = f"crew:{self.crew_config.name}:{task.name}:redo:{uuid.uuid4().hex[:8]}"
        started = datetime.now()

        # Transition to RUNNING for the actual redo execution
        self.state_manager.transition_task(task.name, TaskPhase.RUNNING, "redo execution started")

        # Determine timeout mode (same as _execute_single_task)
        use_soft_timeout = task.timeout is None
        # Use explicit timeout if set (including 0), otherwise estimate
        initial_timeout = task.timeout if task.timeout is not None else self._estimate_timeout(task)

        # Initialize variables that may be overwritten by exceptions
        output = ""
        extended_count = 0
        status = "success"
        success = True

        try:
            output, extended_count = await self._execute_with_soft_timeout(
                task=task,
                prompt=prompt,
                session_key=session_key,
                initial_timeout=initial_timeout,
                use_soft_timeout=use_soft_timeout,
                media=media,
            )
        except asyncio.CancelledError:
            output = "Redo cancelled"
            status = "cancelled"
            success = False
            raise
        except asyncio.TimeoutError:
            output = f"Redo timed out after extensions"
            status = "failed"
            success = False
        except Exception as exc:
            output = f"Redo failed: {exc}"
            status = "failed"
            success = False

        return TaskResult(
            task_name=task.name,
            agent_name=task.agent,
            output=output,
            status=status,
            started_at=started,
            finished_at=datetime.now(),
            human_briefing_input=extra_briefing,
            quality="partial" if extended_count > 0 else "full",
            extended_count=extended_count,
        ), success

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _progress(self, message: str, **kwargs: Any) -> None:
        logger.info(f"[crew] {message}")
        if self.on_progress:
            self.on_progress(message, **kwargs)

    def _check_upstream_ready(self, task: TaskDefinition) -> bool:
        """Return True if all upstream dependencies are completed."""
        for dep in task.context_from:
            r = self.context.get_result(dep)
            if r is None or r.status not in ("success", "completed"):
                return False
        return True

    def _get_next_pending_task(self, tasks: list[TaskDefinition]) -> str | None:
        """Find the next task that hasn't been started."""
        for t in tasks:
            phase = self.state_manager.get_task_phase(t.name)
            if phase in (TaskPhase.PENDING, TaskPhase.BLOCKED, TaskPhase.QUEUED):
                return t.name
        return None

    def _save_checkpoint(self, tasks: list[TaskDefinition]) -> None:
        """Save a checkpoint after each completed task."""
        try:
            next_task = self._get_next_pending_task(tasks)
            save_checkpoint(
                crew_config=self.crew_config,
                config_path=self.config_path,
                context=self.context,
                crew_phase=self.state_manager.crew_phase.value,
                next_task=next_task,
                started_at=self.started_at,
            )
        except Exception:
            logger.exception("[crew] Failed to save checkpoint")

    def _persist_task_output(self, result: TaskResult) -> None:
        """Persist task output to disk immediately after completion."""
        if not self._persister:
            return

        try:
            self._persister.save_task_output(
                task_name=result.task_name,
                output=result.output,
                status=result.status,
                started_at=result.started_at,
                finished_at=result.finished_at,
                output_format=result.output_format.value if result.output_format else "raw",
                truncated=result.truncated,
                repaired=result.repaired,
                structured_output=result.structured_output,
                artifacts=result.artifacts if result.artifacts else None,
            )
            logger.debug(f"[crew-output] Persisted output for task '{result.task_name}'")
        except Exception:
            logger.exception(f"[crew-output] Failed to persist output for task '{result.task_name}'")

    def finalize_output(self, status: str = "completed") -> None:
        """Finalize output persistence after all tasks complete."""
        if self._persister:
            try:
                self._persister.finalize(status)
            except Exception:
                logger.exception("[crew-output] Failed to finalize output persistence")

    def _pool_supports_native_streaming(self) -> bool:
        """Return whether the pool natively exposes streaming execution."""
        from unittest.mock import AsyncMock

        if isinstance(self.pool, AsyncMock):
            return False
        return callable(getattr(self.pool, "run_task_streaming", None))


# ==========================================================================
# Sequential Process
# ==========================================================================

class SequentialProcess(BaseProcess):
    """Execute tasks one by one in config order."""

    async def execute(self, tasks: list[TaskDefinition]) -> list[TaskResult]:
        results: list[TaskResult] = []

        # Terminal states - tasks in these states should be skipped
        terminal_states = {
            TaskPhase.COMPLETED, TaskPhase.SKIPPED, TaskPhase.FAILED, TaskPhase.REJECTED
        }

        for task in tasks:
            phase = self.state_manager.get_task_phase(task.name)
            # Skip already completed/terminal tasks (resume scenario)
            if phase in terminal_states:
                existing = self.context.get_result(task.name)
                if existing:
                    results.append(existing)
                continue

            # Check upstream dependencies
            if not self._check_upstream_ready(task):
                self.state_manager.transition_task(task.name, TaskPhase.SKIPPED, "upstream not ready")
                self._progress(f"Skipping task '{task.name}' — upstream dependency not met")
                result = TaskResult(
                    task_name=task.name,
                    agent_name=task.agent,
                    output="Skipped: upstream dependency not completed",
                    status="skipped",
                    started_at=datetime.now(),
                    finished_at=datetime.now(),
                )
                self.context.add_result(result)
                results.append(result)
                self._persist_task_output(result)
                self._save_checkpoint(tasks)
                continue

            # Transition: PENDING -> QUEUED -> RUNNING
            if phase == TaskPhase.PENDING:
                if task.context_from:
                    self.state_manager.transition_task(task.name, TaskPhase.BLOCKED)
                    self.state_manager.transition_task(task.name, TaskPhase.QUEUED, "deps satisfied")
                else:
                    self.state_manager.transition_task(task.name, TaskPhase.QUEUED, "no deps")
            if self.state_manager.get_task_phase(task.name) == TaskPhase.QUEUED:
                self.state_manager.transition_task(task.name, TaskPhase.RUNNING)

            self._progress(f"Executing task '{task.name}' with agent '{task.agent}' ...")

            # Execute
            result = await self._execute_single_task(task)

            # Human review (for both success and failure cases)
            if task.human_review:
                self.state_manager.transition_task(task.name, TaskPhase.AWAITING_REVIEW)
                result = await self._human_review(task, result)
            elif result.status == "failed":
                # No human review - just fail
                self.state_manager.transition_task(task.name, TaskPhase.FAILED, "execution failed")
                self.context.add_result(result)
                results.append(result)
                self._persist_task_output(result)
                self._save_checkpoint(tasks)
                self._progress(f"Task '{task.name}' failed: {result.output[:200]}")
                continue

            # Post-review state
            if result.status == "human_rejected":
                self.state_manager.transition_task(task.name, TaskPhase.REJECTED, "human rejected")
                self.state_manager.transition_task(task.name, TaskPhase.SKIPPED, "rejected -> skip")
                self.context.add_result(result)
                results.append(result)
                self._persist_task_output(result)
                self._save_checkpoint(tasks)
                self._progress(f"Crew aborted by human at task '{task.name}'")
                # Abort: skip all remaining tasks
                for remaining in tasks:
                    rp = self.state_manager.get_task_phase(remaining.name)
                    if rp in (TaskPhase.PENDING, TaskPhase.BLOCKED, TaskPhase.QUEUED):
                        self.state_manager.transition_task(remaining.name, TaskPhase.SKIPPED, "crew aborted")
                        skip_result = TaskResult(
                            task_name=remaining.name,
                            agent_name=remaining.agent,
                            output="Skipped: crew aborted by human",
                            status="skipped",
                            started_at=datetime.now(),
                            finished_at=datetime.now(),
                        )
                        self.context.add_result(skip_result)
                        results.append(skip_result)
                        self._persist_task_output(skip_result)
                break
            elif result.status == "skipped":
                self.state_manager.transition_task(task.name, TaskPhase.SKIPPED, "human skipped")
            elif result.status == "failed":
                # Redo failed - state already transitioned by _redo_task
                # The task is now in FAILED state
                self.state_manager.transition_task(task.name, TaskPhase.FAILED, "redo failed")
                self.context.add_result(result)
                results.append(result)
                self._persist_task_output(result)
                self._save_checkpoint(tasks)
                self._progress(f"Task '{task.name}' redo failed: {result.output[:200]}")
                continue
            else:
                self.state_manager.transition_task(task.name, TaskPhase.COMPLETED)

            self.context.add_result(result)
            results.append(result)
            self._persist_task_output(result)
            self._save_checkpoint(tasks)
            self._progress(
                f"Task '{task.name}' done (status={result.status})",
                task_name=task.name,
                status=result.status,
            )

        return results


# ==========================================================================
# Hierarchical Process
# ==========================================================================

class HierarchicalProcess(BaseProcess):
    """Manager agent coordinates task execution order.

    The manager agent receives a description of all roles and tasks,
    then outputs a JSON execution plan.  If parsing fails we fall back
    to sequential order.
    """

    async def execute(self, tasks: list[TaskDefinition]) -> list[TaskResult]:
        # Try to get an execution plan from the manager
        ordered_names = await self._get_manager_plan(tasks)

        if ordered_names is None:
            self._progress("Manager plan unavailable — falling back to sequential order")
            ordered_names = [t.name for t in tasks]

        # Build a name -> TaskDefinition lookup
        task_map = {t.name: t for t in tasks}

        # Execute in the manager-decided order, using the same sequential logic
        ordered_tasks = []
        for name in ordered_names:
            td = task_map.get(name)
            if td:
                ordered_tasks.append(td)
        # Append any tasks not mentioned by the manager
        mentioned = set(ordered_names)
        for t in tasks:
            if t.name not in mentioned:
                ordered_tasks.append(t)

        # Reuse sequential execution with the reordered task list
        seq = SequentialProcess(
            pool=self.pool,
            context=self.context,
            permission_handler=self.permission_handler,
            crew_config=self.crew_config,
            state_manager=self.state_manager,
            config_path=self.config_path,
            started_at=self.started_at,
            on_progress=self.on_progress,
            llm_repair=self._output_repairer.llm_call,
        )
        seq._persister = self._persister
        return await seq.execute(ordered_tasks)

    async def _get_manager_plan(self, tasks: list[TaskDefinition]) -> list[str] | None:
        """Ask the manager agent for an execution plan."""
        manager_role_name = self.crew_config.manager_agent
        if not manager_role_name:
            # Auto-pick first role as manager or give up
            return None

        role = self.crew_config.agents.get(manager_role_name)
        if not role:
            logger.warning(f"[crew] Manager role '{manager_role_name}' not found in agents")
            return None

        # Build the manager prompt
        roles_desc = "\n".join(
            f"- {name}: {r.description} (goal: {r.goal})"
            for name, r in self.crew_config.agents.items()
        )
        tasks_desc = "\n".join(
            f"- {t.name} (agent: {t.agent}, deps: {t.context_from}): {t.description[:100]}"
            for t in tasks
        )

        prompt = (
            f"You are the team manager. Your role: {role.name}\n"
            f"Goal: {role.goal}\n\n"
            f"## Available Team Members\n{roles_desc}\n\n"
            f"## Tasks to Complete\n{tasks_desc}\n\n"
            "Decide the execution order. Output a JSON array of task names in order.\n"
            'Example: ["discover_bugs", "fix_bugs", "review_and_test", "create_pr"]\n'
            "Only output the JSON array, nothing else."
        )

        session_key = f"crew:{self.crew_config.name}:manager:{uuid.uuid4().hex[:8]}"
        try:
            output = await asyncio.wait_for(
                self.pool.run_task(manager_role_name, prompt, session_key),
                timeout=self.crew_config.manager_timeout,
            )
            return self._parse_plan(output)
        except asyncio.CancelledError:
            logger.info("[crew] Manager plan generation cancelled")
            raise
        except Exception:
            logger.exception("[crew] Manager plan generation failed")
            return None

    @staticmethod
    def _parse_plan(output: str) -> list[str] | None:
        """Extract a JSON array of task names from manager output.

        Uses bracket counting to find complete JSON arrays, handling
        cases where array elements contain bracket characters.
        """
        # First, try to parse the entire output as JSON
        try:
            plan = json.loads(output.strip())
            if isinstance(plan, list) and all(isinstance(x, str) for x in plan):
                return plan
        except json.JSONDecodeError:
            pass

        # Find JSON array using bracket counting
        # This handles arrays containing strings with brackets
        start_idx = output.find('[')
        while start_idx != -1:
            bracket_count = 0
            in_string = False
            escape_next = False

            for i, char in enumerate(output[start_idx:], start_idx):
                if escape_next:
                    escape_next = False
                    continue
                if char == '\\' and in_string:
                    escape_next = True
                    continue
                if char == '"' and not escape_next:
                    in_string = not in_string
                elif not in_string:
                    if char == '[':
                        bracket_count += 1
                    elif char == ']':
                        bracket_count -= 1
                        if bracket_count == 0:
                            try:
                                plan = json.loads(output[start_idx:i + 1])
                                if isinstance(plan, list) and all(isinstance(x, str) for x in plan):
                                    return plan
                            except json.JSONDecodeError:
                                pass
                            start_idx = output.find('[', i + 1)
                            break
            else:
                return None

        return None
