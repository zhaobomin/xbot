"""Heartbeat service - periodic agent wake-up to check for tasks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Coroutine

from xbot.logging import get_logger

logger = get_logger(__name__)
from xbot.agent.task_supervisor import ServiceTaskRegistry

_HEARTBEAT_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "heartbeat",
            "description": "Report heartbeat decision after reviewing tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["skip", "run"],
                        "description": "skip = nothing to do, run = has active tasks",
                    },
                    "tasks": {
                        "type": "string",
                        "description": "Natural-language summary of active tasks (required for run)",
                    },
                },
                "required": ["action"],
            },
        },
    }
]

_TRANSIENT_ERROR_MARKERS = (
    "429",
    "rate limit",
    "500",
    "502",
    "503",
    "504",
    "overloaded",
    "timeout",
    "timed out",
    "connection",
    "server error",
    "temporarily unavailable",
)


class HeartbeatService:
    """
    Periodic heartbeat service that wakes the agent to check for tasks.

    Phase 1 (decision): reads HEARTBEAT.md and asks the LLM — via a virtual
    tool call — whether there are active tasks.  This avoids free-text parsing
    and the unreliable HEARTBEAT_OK token.

    Phase 2 (execution): only triggered when Phase 1 returns ``run``.  The
    ``on_execute`` callback runs the task through the full agent loop and
    returns the result to deliver.
    """

    def __init__(
        self,
        workspace: Path,
        llm_call: Callable[..., Awaitable[Any]],
        on_execute: Callable[[str], Coroutine[Any, Any, str]] | None = None,
        on_notify: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        interval_s: int = 30 * 60,
        enabled: bool = True,
        on_channel_health: Callable[[], dict[str, tuple[bool, str]]] | None = None,
    ):
        self.workspace = workspace
        self._llm_call = llm_call
        self.on_execute = on_execute
        self.on_notify = on_notify
        self.interval_s = interval_s
        self.enabled = enabled
        self._on_channel_health = on_channel_health
        self._running = False
        self._task: asyncio.Task | None = None
        self._running_tick: asyncio.Task | None = None  # Track current tick task
        self._task_registry = ServiceTaskRegistry(error_reporter=self._report_task_error)

    @staticmethod
    def _report_task_error(owner: str, task_name: str, exc: BaseException) -> None:
        logger.error("Heartbeat task failed for owner=%s task=%s: %s", owner, task_name, exc)

    @property
    def heartbeat_file(self) -> Path:
        return self.workspace / "HEARTBEAT.md"

    def _read_heartbeat_file(self) -> str | None:
        if self.heartbeat_file.exists():
            try:
                return self.heartbeat_file.read_text(encoding="utf-8")
            except Exception:
                return None
        return None

    async def _decide(self, content: str) -> tuple[str, str]:
        """Phase 1: ask LLM to decide skip/run via virtual tool call.

        Returns (action, tasks) where action is 'skip' or 'run'.
        """
        from xbot.utils.helpers import current_time_str

        response = None
        for attempt, delay in enumerate((0, 1, 2, 4)):
            if delay:
                await asyncio.sleep(delay)
            response = await self._llm_call(
                messages=[
                    {"role": "system", "content": "You are a heartbeat agent. Call the heartbeat tool to report your decision."},
                    {"role": "user", "content": (
                        f"Current Time: {current_time_str()}\n\n"
                        "Review the following HEARTBEAT.md and decide whether there are active tasks.\n\n"
                        f"{content}"
                    )},
                ],
                tools=_HEARTBEAT_TOOL,
                tool_choice="auto",
                max_tokens=256,
            )
            is_error = getattr(response, "finish_reason", "") == "error"
            err_text = (getattr(response, "content", "") or "").lower()
            if not is_error or not any(marker in err_text for marker in _TRANSIENT_ERROR_MARKERS):
                break

        if response is None or not getattr(response, "has_tool_calls", False):
            return "skip", ""

        args = response.tool_calls[0].arguments
        return args.get("action", "skip"), args.get("tasks", "")

    async def start(self) -> None:
        """Start the heartbeat service."""
        if not self.enabled:
            logger.info("Heartbeat disabled")
            return
        if self._running:
            logger.warning("Heartbeat already running")
            return

        self._running = True
        self._task = self._task_registry.spawn("heartbeat-service", self._run_loop(), name="heartbeat-loop")
        logger.info("Heartbeat started (every %ss)", self.interval_s)

    def stop(self) -> None:
        """Stop the heartbeat service."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def shutdown(self) -> None:
        """Stop the heartbeat service and wait for owned tasks to finish."""
        self.stop()
        await self._task_registry.cancel_all()

    async def _run_loop(self) -> None:
        """Main heartbeat loop with task skip mechanism."""
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if not self._running:
                    break

                # 检查上一个任务是否仍在运行
                if self._running_tick is not None and not self._running_tick.done():
                    logger.warning("Heartbeat task still running, skipping this iteration")
                    continue

                # 创建新的 tick 任务
                self._running_tick = self._task_registry.spawn(
                    "heartbeat-service:tick",
                    self._tick(),
                    name="heartbeat-tick",
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat error: %s", e)

    async def _tick(self) -> None:
        """Execute a single heartbeat tick."""
        from xbot.utils.evaluator import evaluate_response

        content = self._read_heartbeat_file()
        if not content:
            logger.debug("Heartbeat: HEARTBEAT.md missing or empty")
            return

        # Channel connectivity check
        if self._on_channel_health:
            try:
                health = self._on_channel_health()
                for ch_name, (healthy, detail) in health.items():
                    if not healthy:
                        logger.warning("Heartbeat: channel %s unhealthy: %s", ch_name, detail)
                    else:
                        logger.debug("Heartbeat: channel %s healthy", ch_name)
            except Exception as e:
                logger.warning("Heartbeat: channel health check error: %s", e)

        logger.info("Heartbeat: checking for tasks...")

        try:
            action, tasks = await self._decide(content)

            if action != "run":
                logger.info("Heartbeat: OK (nothing to report)")
                return

            logger.info("Heartbeat: tasks found, executing...")
            if self.on_execute:
                response = await self.on_execute(tasks)

                if response:
                    should_notify = await evaluate_response(
                        response, tasks, self._llm_call,
                    )
                    if should_notify and self.on_notify:
                        logger.info("Heartbeat: completed, delivering response")
                        await self.on_notify(response)
                    else:
                        logger.info("Heartbeat: silenced by post-run evaluation")
        except Exception:
            logger.exception("Heartbeat execution failed")

    async def trigger_now(self) -> str | None:
        """Manually trigger a heartbeat."""
        content = self._read_heartbeat_file()
        if not content:
            return None
        action, tasks = await self._decide(content)
        if action != "run" or not self.on_execute:
            return None
        return await self.on_execute(tasks)
