"""Crew execution context: task-to-task data propagation, prompt building, checkpoints."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from xbot.logging import get_logger

logger = get_logger(__name__)

from xbot.agent.crew.models import AgentRole, CrewConfig, TaskDefinition, TaskResult


class CrewExecutionContext:
    """Manages completed task results and builds downstream prompts.

    Human-intervention content (edited outputs, annotations, briefing inputs)
    is naturally embedded into prompts via ``build_task_prompt()``.
    """

    def __init__(self) -> None:
        self._results: dict[str, TaskResult] = {}

    def add_result(self, result: TaskResult) -> None:
        self._results[result.task_name] = result

    def get_result(self, task_name: str) -> TaskResult | None:
        return self._results.get(task_name)

    def get_upstream_results(self, task: TaskDefinition) -> dict[str, TaskResult]:
        """Return completed upstream results referenced by ``context_from``."""
        return {
            dep: self._results[dep]
            for dep in task.context_from
            if dep in self._results
        }

    def get_all_results(self) -> list[TaskResult]:
        return list(self._results.values())

    def build_task_prompt(
        self,
        task: TaskDefinition,
        role: AgentRole,
        global_context: str = "",
        human_briefing: str | None = None,
        max_context_length: int = 4000,
    ) -> str:
        """Build the full prompt for a task, embedding human inputs naturally.

        Sections:
        - Role identity and goal
        - Global context
        - Task description
        - Additional instructions from team lead (human_briefing)
        - Context from upstream tasks (with effective_output and annotations)
        - Expected output format

        Args:
            task: Task definition.
            role: Agent role executing the task.
            global_context: Global project context.
            human_briefing: Pre-execution human instructions.
            max_context_length: Max chars for upstream output (default 4000).
        """
        parts: list[str] = []

        # --- Role identity ---
        parts.append(
            f"You are working as part of a team. Your role: **{role.name}**\n"
            f"Goal: {role.goal}"
        )
        if role.backstory:
            parts.append(f"Background: {role.backstory}")

        # --- Global context ---
        if global_context:
            parts.append(f"## Project Context\n{global_context}")

        # --- Task description ---
        parts.append(f"## Your Task\n{task.description}")

        # --- Human briefing (pre-execution instructions) ---
        if human_briefing:
            parts.append(f"## Additional Instructions from Team Lead\n{human_briefing}")

        # --- Upstream context ---
        upstream = self.get_upstream_results(task)
        if upstream:
            ctx_lines = ["## Context from Previous Tasks"]
            for dep_name, dep_result in upstream.items():
                output = dep_result.effective_output
                # Truncate very long outputs to avoid prompt overflow
                if len(output) > max_context_length:
                    output = output[:max_context_length] + "\n\n... (output truncated)"
                ctx_lines.append(
                    f"### Task: {dep_name} (by {dep_result.agent_name})\n{output}"
                )
                # Include human annotations if present
                if dep_result.human_annotations:
                    ctx_lines.append("#### Team Lead Review Notes")
                    for ann in dep_result.human_annotations:
                        ctx_lines.append(f"- {ann}")
            parts.append("\n\n".join(ctx_lines))

        # --- Expected output ---
        if task.expected_output:
            parts.append(f"## Expected Output\n{task.expected_output}")

        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Checkpoint persistence
# ---------------------------------------------------------------------------

def save_checkpoint(
    crew_config: CrewConfig,
    config_path: str,
    context: CrewExecutionContext,
    crew_phase: str,
    next_task: str | None,
    started_at: datetime,
) -> Path:
    """Persist a checkpoint JSON after each completed task.

    Returns the path to the written checkpoint file.
    """
    workspace = Path(crew_config.workspace)
    checkpoint_dir = workspace / ".xbot" / "crew_checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    completed = []
    for r in context.get_all_results():
        completed.append({
            "name": r.task_name,
            "agent": r.agent_name,
            "status": r.status,
            "output": r.output,
            "human_edited_output": r.human_edited_output,
            "human_annotations": r.human_annotations,
            "human_briefing_input": r.human_briefing_input,
            "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat(),
        })

    data: dict[str, Any] = {
        "version": 1,
        "crew_config_path": config_path,
        "crew_name": crew_config.name,
        "crew_phase": crew_phase,
        "started_at": started_at.isoformat(),
        "checkpoint_at": datetime.now().isoformat(),
        "completed_tasks": completed,
        "next_task": next_task,
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{crew_config.name}_{ts}.json"
    target = checkpoint_dir / filename

    # Atomic write: write to temp then rename
    fd, tmp_path = tempfile.mkstemp(dir=str(checkpoint_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        Path(tmp_path).replace(target)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise

    logger.debug(f"[crew-checkpoint] Saved: {target}")
    return target


def load_checkpoint(path: Path) -> dict[str, Any]:
    """Load a checkpoint JSON. Returns the parsed dict.

    Raises:
        FileNotFoundError: If checkpoint file does not exist.
        json.JSONDecodeError: If JSON is malformed.
    """
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Checkpoint not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)
