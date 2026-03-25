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
from typing import Any, Callable

from loguru import logger

from xbot.agent.crew.agent_pool import AgentPool
from xbot.agent.crew.context import CrewExecutionContext, save_checkpoint
from xbot.agent.crew.models import CrewConfig, TaskDefinition, TaskResult
from xbot.agent.crew.state import CrewStateManager, TaskPhase
from xbot.agent.permission_handler import BasePermissionHandler


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

    @abstractmethod
    async def execute(self, tasks: list[TaskDefinition]) -> list[TaskResult]:
        """Run all tasks and return results."""

    # ------------------------------------------------------------------
    # Single task execution
    # ------------------------------------------------------------------

    async def _execute_single_task(self, task: TaskDefinition) -> TaskResult:
        """Execute a single task: briefing -> run -> return result.

        Does NOT include human review — callers handle review separately so
        state transitions stay clean.
        """
        role = self.crew_config.agents[task.agent]

        # 1. Human briefing (pre-execution)
        human_briefing = await self._human_briefing(task)

        # 2. Build prompt
        prompt = self.context.build_task_prompt(
            task, role,
            global_context=self.crew_config.global_context,
            human_briefing=human_briefing,
        )

        # 3. Generate session key
        session_key = f"crew:{self.crew_config.name}:{task.name}:{uuid.uuid4().hex[:8]}"

        # 4. Execute with timeout
        started = datetime.now()
        try:
            output = await asyncio.wait_for(
                self.pool.run_task(task.agent, prompt, session_key),
                timeout=task.timeout,
            )
            status = "success"
        except asyncio.TimeoutError:
            logger.warning(f"[crew] Task '{task.name}' timed out after {task.timeout}s")
            output = f"Task timed out after {task.timeout} seconds"
            status = "failed"
        except Exception as exc:
            logger.exception(f"[crew] Task '{task.name}' failed with error")
            output = f"Task failed: {exc}"
            status = "failed"

        finished = datetime.now()
        return TaskResult(
            task_name=task.name,
            agent_name=task.agent,
            output=output,
            status=status,
            started_at=started,
            finished_at=finished,
            human_briefing_input=human_briefing,
        )

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
        """Actual review interaction (called while holding the lock)."""
        output_preview = result.output[:2000]
        if len(result.output) > 2000:
            output_preview += "\n... (truncated)"

        self._progress(f"Awaiting human review for task '{task.name}' ...")
        response = await self.permission_handler.request_interaction(
            kind="question",
            prompt=(
                f"Task '{task.name}' completed (status: {result.status}).\n\n"
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

        action = (response.content or "continue").strip().lower()

        if action in ("1", "continue", "继续"):
            return result

        if action in ("2", "annotate", "批注"):
            return await self._collect_annotation(result)

        if action in ("3", "edit", "修改"):
            return await self._collect_edit(result)

        if action in ("4", "redo", "重做"):
            return await self._redo_task(task, result)

        if action in ("5", "skip", "跳过"):
            result.status = "skipped"
            return result

        if action in ("6", "abort", "终止"):
            result.status = "human_rejected"
            return result

        # Default: treat as continue
        return result

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

    async def _redo_task(self, task: TaskDefinition, original: TaskResult) -> TaskResult:
        """Re-execute a task with human feedback."""
        resp = await self.permission_handler.request_interaction(
            kind="question",
            prompt="Enter feedback for the redo (what should be different?):",
            session_key=f"crew:{self.crew_config.name}",
        )
        feedback = (resp.content or "").strip()

        # Build a new prompt with feedback
        role = self.crew_config.agents[task.agent]
        extra_briefing = f"Previous attempt feedback: {feedback}" if feedback else None
        prompt = self.context.build_task_prompt(
            task, role,
            global_context=self.crew_config.global_context,
            human_briefing=extra_briefing,
        )

        session_key = f"crew:{self.crew_config.name}:{task.name}:redo:{uuid.uuid4().hex[:8]}"
        started = datetime.now()
        try:
            output = await asyncio.wait_for(
                self.pool.run_task(task.agent, prompt, session_key),
                timeout=task.timeout,
            )
            status = "success"
        except asyncio.TimeoutError:
            output = f"Redo timed out after {task.timeout}s"
            status = "failed"
        except Exception as exc:
            output = f"Redo failed: {exc}"
            status = "failed"

        return TaskResult(
            task_name=task.name,
            agent_name=task.agent,
            output=output,
            status=status,
            started_at=started,
            finished_at=datetime.now(),
            human_briefing_input=extra_briefing,
        )

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


# ==========================================================================
# Sequential Process
# ==========================================================================

class SequentialProcess(BaseProcess):
    """Execute tasks one by one in config order."""

    async def execute(self, tasks: list[TaskDefinition]) -> list[TaskResult]:
        results: list[TaskResult] = []

        for task in tasks:
            phase = self.state_manager.get_task_phase(task.name)
            # Skip already completed tasks (resume scenario)
            if phase == TaskPhase.COMPLETED:
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

            # Handle failure
            if result.status == "failed":
                self.state_manager.transition_task(task.name, TaskPhase.FAILED, "execution failed")
                self.context.add_result(result)
                results.append(result)
                self._save_checkpoint(tasks)
                self._progress(f"Task '{task.name}' failed: {result.output[:200]}")
                continue

            # Human review
            if task.human_review:
                self.state_manager.transition_task(task.name, TaskPhase.AWAITING_REVIEW)
                result = await self._human_review(task, result)

            # Post-review state
            if result.status == "human_rejected":
                self.state_manager.transition_task(task.name, TaskPhase.REJECTED, "human rejected")
                self.state_manager.transition_task(task.name, TaskPhase.SKIPPED, "rejected -> skip")
                self.context.add_result(result)
                results.append(result)
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
                break
            elif result.status == "skipped":
                self.state_manager.transition_task(task.name, TaskPhase.SKIPPED, "human skipped")
            elif result.status == "failed":
                # Redo returned a failure — treat as task failure
                self.state_manager.transition_task(task.name, TaskPhase.RETRYING, "redo requested")
                self.state_manager.transition_task(task.name, TaskPhase.RUNNING, "redo running")
                self.state_manager.transition_task(task.name, TaskPhase.FAILED, "redo failed")
                self.context.add_result(result)
                results.append(result)
                self._save_checkpoint(tasks)
                self._progress(f"Task '{task.name}' redo failed: {result.output[:200]}")
                continue
            else:
                self.state_manager.transition_task(task.name, TaskPhase.COMPLETED)

            self.context.add_result(result)
            results.append(result)
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
        )
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
                timeout=120,
            )
            return self._parse_plan(output)
        except Exception:
            logger.exception("[crew] Manager plan generation failed")
            return None

    @staticmethod
    def _parse_plan(output: str) -> list[str] | None:
        """Extract a JSON array of task names from manager output."""
        # Try to find a JSON array in the output
        match = re.search(r'\[.*?\]', output, re.DOTALL)
        if not match:
            return None
        try:
            plan = json.loads(match.group())
            if isinstance(plan, list) and all(isinstance(x, str) for x in plan):
                return plan
        except json.JSONDecodeError:
            pass
        return None
