"""Goal mode for xbot CLI.

Implements an autonomous PLAN → ACT → VERIFY loop that pursues a high-level
objective until achieved or max iterations reached.

Usage:
    xbot goal init "implement user registration"
    xbot goal run
    xbot goal pause
    xbot goal resume
    xbot goal list
"""

from __future__ import annotations

import asyncio
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from xbot.runtime.session.goal_store import (
    GoalPhase,
    GoalState,
    GoalStatus,
    GoalStore,
    generate_goal_id,
)

console = Console()
goal_app = typer.Typer(help="Goal mode — autonomous objective pursuit")


# ---------------------------------------------------------------------------
# Permission handler: auto-approve everything
# ---------------------------------------------------------------------------


class GoalPermissionHandler:
    """Permission handler that auto-approves all tools for Goal mode."""

    def __init__(self) -> None:
        self.auto_approve_safe_tools = True
        self._safe_tools: set[str] = set()

    def is_safe_tool(self, tool_name: str) -> bool:  # noqa: ARG002
        return True

    def add_safe_tool(self, tool_name: str) -> None:
        self._safe_tools.add(tool_name)

    async def can_use_tool(
        self,
        tool_name: str,  # noqa: ARG002
        tool_input: dict[str, Any],
        context: Any,  # noqa: ARG002
    ) -> tuple[str, dict | str]:
        """Always allow."""
        return "allow", tool_input

    def build_can_use_tool_callback(self):
        """Build SDK-compatible callback."""
        try:
            from claude_agent_sdk.types import (
                PermissionResultAllow,
                ToolPermissionContext,
            )
        except ImportError:
            raise ImportError("claude-agent-sdk is not installed.")

        async def callback(
            tool_name: str,
            tool_input: dict[str, Any],
            context: ToolPermissionContext | None = None,
        ) -> PermissionResultAllow:
            return PermissionResultAllow()

        return callback

    # Required interface methods (no-op for Goal mode)
    def set_session_context(self, *args: Any, **kwargs: Any) -> None:
        pass

    def clear_session_context(self, *args: Any, **kwargs: Any) -> None:
        pass

    def set_current_session(self, *args: Any, **kwargs: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# GoalRunner — the core loop
# ---------------------------------------------------------------------------


class GoalRunner:
    """Drives the PLAN → ACT → VERIFY loop."""

    def __init__(self, goal: GoalState, store: GoalStore) -> None:
        self.goal = goal
        self.store = store
        self._terminal_reason: str | None = None
        self._interrupted = False
        self._service: Any = None

    async def run(self) -> None:
        """Execute the goal loop."""
        workspace = Path(self.goal.workspace)

        # Git precondition check
        use_checkpoint = self._check_git_preconditions(workspace)

        # Setup signal handling
        self._setup_signals()

        # Create agent service
        try:
            self._service = await self._create_service(workspace)
        except Exception as e:
            console.print(f"[red]Error: failed to create agent service: {e}[/red]")
            return

        try:
            await self._service.initialize()
        except Exception as e:
            console.print(f"[red]Error: failed to initialize agent: {e}[/red]")
            await self._service.close_mcp()
            return

        try:
            plan_msg = self._build_first_prompt()

            while self.goal.loop_count < self.goal.max_loops:
                if self._interrupted:
                    self.goal.status = GoalStatus.PAUSED
                    self.store.save(self.goal)
                    console.print("\n[yellow]Goal paused.[/yellow]")
                    break

                self.goal.status = GoalStatus.ACTIVE

                # === PLAN ===
                self.goal.current_phase = GoalPhase.PLAN
                self.store.save(self.goal)
                console.print(f"\n[bold cyan]━━━ PLAN (loop {self.goal.loop_count + 1}/{self.goal.max_loops}) ━━━[/bold cyan]")

                await self._call_agent(plan_msg)

                if self._interrupted:
                    self.goal.status = GoalStatus.PAUSED
                    self.store.save(self.goal)
                    console.print("\n[yellow]Goal paused.[/yellow]")
                    break

                # === CHECKPOINT ===
                if use_checkpoint:
                    self.goal.checkpoint = self._git_rev_parse(workspace)
                    self.store.save(self.goal)

                # === ACT ===
                self.goal.current_phase = GoalPhase.ACT
                self.store.save(self.goal)
                console.print("\n[bold green]━━━ ACT ━━━[/bold green]")

                act_msg = "[ACT] Execute your plan now."
                continuation_count = 0
                while True:
                    if self._interrupted:
                        break
                    await self._call_agent(act_msg)
                    # Only continue if explicitly hit max_turns (agent was cut off).
                    # All other terminal states (completed, None, etc.) mean the
                    # agent chose to stop — respect that.
                    if self._terminal_reason != "max_turns":
                        break
                    continuation_count += 1
                    if continuation_count % 20 == 0:
                        console.print(f"[dim]  (ACT continuation #{continuation_count})[/dim]")
                    act_msg = "Continue executing."

                if self._interrupted:
                    self.goal.status = GoalStatus.PAUSED
                    self.store.save(self.goal)
                    console.print("\n[yellow]Goal paused.[/yellow]")
                    break

                # === VERIFY ===
                self.goal.current_phase = GoalPhase.VERIFY
                self.store.save(self.goal)
                console.print("\n[bold magenta]━━━ VERIFY ━━━[/bold magenta]")

                passed = await self._verify()

                if passed:
                    self.goal.status = GoalStatus.ACHIEVED
                    self.store.save(self.goal)
                    console.print("\n[bold green]✓ Goal achieved![/bold green]")
                    break

                # VERIFY failed → rollback → next loop
                console.print("[yellow]  Verification failed. Rolling back and retrying...[/yellow]")
                if use_checkpoint and self.goal.checkpoint:
                    self._git_reset_hard(workspace, self.goal.checkpoint)

                self.goal.loop_count += 1
                self.store.save(self.goal)
                plan_msg = self._build_retry_prompt()

            else:
                # max_loops exhausted
                self.goal.status = GoalStatus.UNMET
                self.store.save(self.goal)
                console.print(f"\n[red]✗ Goal unmet after {self.goal.max_loops} attempts.[/red]")

        finally:
            await self._service.close_mcp()

    # ------------------------------------------------------------------
    # Agent interaction
    # ------------------------------------------------------------------

    async def _call_agent(self, message: str) -> str:
        """Call process_direct and capture terminal_reason."""
        self._terminal_reason = None
        try:
            result = await self._service.process_direct(
                content=message,
                session_key=self.goal.session_key,
                channel="goal",
                chat_id=self.goal.goal_id,
                on_progress=self._on_progress,
            )
        except Exception as e:
            console.print(f"[dim]  (agent call failed: {e}, retrying...)[/dim]")
            return ""
        return result or ""

    async def _on_progress(
        self,
        content: str,
        *,
        tool_hint: bool = False,
        event_type: str = "progress",
        event_data: dict[str, Any] | None = None,
    ) -> None:
        """Capture terminal_reason from result events and print progress."""
        if event_type == "result" and event_data:
            self._terminal_reason = event_data.get("terminal_reason")
        # Print tool hints for visibility
        if tool_hint and content:
            console.print(f"[dim]  {content}[/dim]")

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    async def _verify(self) -> bool:
        """Run verification. Returns True if goal achieved."""
        if self.goal.verify_cmd:
            try:
                # Set XBOT_VERIFY_PHASE=1 so scripts can distinguish orchestrator
                # verification from agent-initiated test runs.
                verify_cmd = f"XBOT_VERIFY_PHASE=1 {self.goal.verify_cmd}"
                exit_code, output = await self._run_cmd(verify_cmd)
            except Exception as e:
                exit_code, output = -1, f"Command error: {e}"
            if exit_code == 0:
                await self._call_agent(f"[VERIFY PASSED]\n```\n{output}\n```")
                return True
            else:
                await self._call_agent(f"[VERIFY FAILED]\n```\n{output}\n```")
                return False
        else:
            result = await self._call_agent(
                "[VERIFY] Check if the objective is achieved. "
                "If yes, output [GOAL_ACHIEVED]. If not, output [GOAL_NOT_MET] with explanation."
            )
            return "[GOAL_ACHIEVED]" in result

    async def _run_cmd(self, cmd: str) -> tuple[int, str]:
        """Run a shell command and return (exit_code, output)."""
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.goal.workspace,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        return proc.returncode or 0, output

    # ------------------------------------------------------------------
    # Prompt templates
    # ------------------------------------------------------------------

    def _build_first_prompt(self) -> str:
        parts = [f"[GOAL MODE]\nObjective: {self.goal.objective}"]
        if self.goal.verify_cmd:
            parts.append(f"Validation command: `{self.goal.verify_cmd}`")
        parts.append(
            "Current phase: PLAN. Analyze the codebase and create your execution plan. "
            "Do not modify any files in this phase."
        )
        return "\n".join(parts)

    def _build_retry_prompt(self) -> str:
        parts = [
            f"[PLAN] Loop {self.goal.loop_count + 1}/{self.goal.max_loops}",
            f"Objective: {self.goal.objective}",
        ]
        if self.goal.verify_cmd:
            parts.append(f"Validation command: `{self.goal.verify_cmd}`")
        parts.append(
            "Previous attempt did not pass verification. The workspace has been rolled back. "
            "Analyze what went wrong and revise your approach."
        )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Git operations
    # ------------------------------------------------------------------

    def _check_git_preconditions(self, workspace: Path) -> bool:
        """Check git repo status. Returns True if checkpoint is available."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=workspace,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print(
                    "[yellow]Warning: Not a git repository. "
                    "Running without checkpoint/rollback support.[/yellow]"
                )
                return False
        except FileNotFoundError:
            console.print("[yellow]Warning: git not found. Running without checkpoint support.[/yellow]")
            return False

        # Check for uncommitted changes (exclude .goal/ which we create)
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", ".", ":!.goal"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            console.print(
                "[yellow]Warning: Uncommitted changes detected. "
                "Running without checkpoint/rollback support.[/yellow]\n"
                "[dim]  Tip: commit or stash changes before running goals for full rollback support.[/dim]"
            )
            return False

        return True

    def _git_rev_parse(self, workspace: Path) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _git_reset_hard(self, workspace: Path, commit: str) -> None:
        subprocess.run(
            ["git", "reset", "--hard", commit],
            cwd=workspace,
            capture_output=True,
        )
        # Also clean untracked files created during ACT (but preserve .goal/)
        subprocess.run(
            ["git", "clean", "-fd", "-e", ".goal"],
            cwd=workspace,
            capture_output=True,
        )

    # ------------------------------------------------------------------
    # Service creation
    # ------------------------------------------------------------------

    async def _create_service(self, workspace: Path):
        """Create an AgentService configured for Goal mode."""
        from xbot.platform.bus.queue import MessageBus
        from xbot.platform.config.loader import load_config
        from xbot.platform.config.paths import get_cron_dir
        from xbot.runtime.core.service import AgentService
        from xbot.runtime.core.types import AgentConfig
        from xbot.runtime.session.conversation_store import ConversationStore
        from xbot.runtime.state import RuntimeSessionRegistry
        from xbot.runtime.system.cron.service import CronService

        config = load_config()

        agent_config = AgentConfig(
            model=config.agents.defaults.model,
            system_prompt="",
            mcp_servers=getattr(config.tools, "mcp_servers", {}) or {},
            agents=(
                list(config.agents.claude_sdk.agents.values())
                if config.agents.claude_sdk.agents
                else None
            ),
        )

        bus = MessageBus()
        runtime_registry = RuntimeSessionRegistry()
        conversation_store = ConversationStore(config.workspace_path)
        cron_store_path = get_cron_dir() / "jobs.json"
        cron = CronService(cron_store_path)

        permission_handler = GoalPermissionHandler()

        shared_resources: dict[str, Any] = {
            "bus": bus,
            "workspace": config.workspace_path,
            "execution_cwd": str(workspace),
            "cron_service": cron,
            "conversation_store": conversation_store,
            "config": config,
            "tools_config": config.tools,
            "run_mode": "goal",
            "runtime_registry": runtime_registry,
            "permission_handler": permission_handler,
        }

        # Register session in runtime registry
        if hasattr(runtime_registry, "get_or_create"):
            runtime_registry.get_or_create(self.goal.session_key)
        if hasattr(runtime_registry, "set_execution_cwd"):
            runtime_registry.set_execution_cwd(self.goal.session_key, str(workspace))
        if hasattr(runtime_registry, "set_workspace_dir"):
            runtime_registry.set_workspace_dir(self.goal.session_key, str(config.workspace_path))

        service = AgentService(agent_config, shared_resources)
        return service

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _setup_signals(self) -> None:
        """Register SIGINT handler for graceful pause."""
        def _handler(signum, frame):
            if self._interrupted:
                # Second Ctrl-C — force exit
                console.print("\n[red]Force quit.[/red]")
                sys.exit(1)
            self._interrupted = True
            console.print("\n[yellow]Interrupt received. Pausing after current turn...[/yellow]")

        signal.signal(signal.SIGINT, _handler)


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------


@goal_app.command("init")
def goal_init(
    objective: str = typer.Argument(..., help="The goal objective"),
    verify: str | None = typer.Option(None, "--verify", "-v", help="Validation command (shell)"),
    max_loops: int = typer.Option(10, "--max-loops", "-n", help="Maximum loop iterations"),
) -> None:
    """Create a new goal."""
    workspace = Path.cwd()
    store = GoalStore(workspace)

    goal_id = generate_goal_id()
    goal = GoalState(
        goal_id=goal_id,
        objective=objective,
        workspace=str(workspace),
        session_key=f"goal:{goal_id}",
        verify_cmd=verify,
        max_loops=max_loops,
    )
    store.save(goal)
    console.print(f"[green]Goal created:[/green] {goal_id}")
    console.print(f"  Objective: {objective}")
    if verify:
        console.print(f"  Verify: {verify}")
    console.print(f"  Max loops: {max_loops}")
    console.print(f"\nRun [bold]xbot goal run[/bold] to start.")


@goal_app.command("run")
def goal_run(
    goal_id: str | None = typer.Argument(None, help="Goal ID (default: most recent non-terminal goal)"),
) -> None:
    """Start or resume a goal."""
    workspace = Path.cwd()
    store = GoalStore(workspace)

    if goal_id:
        goal = store.load(goal_id)
    else:
        goal = store.find_active()

    if not goal:
        console.print("[red]No active goal found. Run `xbot goal init` first.[/red]")
        raise typer.Exit(1)

    if goal.status == GoalStatus.ACHIEVED:
        console.print(f"[green]Goal already achieved:[/green] {goal.goal_id}")
        raise typer.Exit(0)

    if goal.status == GoalStatus.UNMET and goal.loop_count >= goal.max_loops:
        console.print(
            f"[red]Goal exhausted all {goal.max_loops} attempts.[/red]\n"
            "[dim]  Use `xbot goal modify --max-loops <N>` to increase and retry.[/dim]"
        )
        raise typer.Exit(1)

    if goal.status == GoalStatus.ACTIVE:
        # Process died without proper cleanup — treat as crash recovery
        console.print(f"[yellow]Goal was interrupted abnormally. Resuming...[/yellow]")
        if goal.checkpoint:
            _workspace = Path(goal.workspace)
            subprocess.run(
                ["git", "reset", "--hard", goal.checkpoint],
                cwd=_workspace,
                capture_output=True,
            )
            subprocess.run(["git", "clean", "-fd", "-e", ".goal"], cwd=_workspace, capture_output=True)
            console.print("[dim]  Rolled back to last checkpoint.[/dim]")

    # Resume logic
    if goal.status == GoalStatus.PAUSED:
        console.print(f"[cyan]Resuming goal:[/cyan] {goal.goal_id}")
        # If paused during ACT, rollback first
        if goal.current_phase == GoalPhase.ACT and goal.checkpoint:
            _workspace = Path(goal.workspace)
            subprocess.run(
                ["git", "reset", "--hard", goal.checkpoint],
                cwd=_workspace,
                capture_output=True,
            )
            subprocess.run(["git", "clean", "-fd", "-e", ".goal"], cwd=_workspace, capture_output=True)
            console.print("[dim]  Rolled back to checkpoint before resuming.[/dim]")

    console.print(f"[bold]Goal:[/bold] {goal.objective}")
    console.print(f"[bold]ID:[/bold] {goal.goal_id}")
    if goal.verify_cmd:
        console.print(f"[bold]Verify:[/bold] {goal.verify_cmd}")
    console.print()

    runner = GoalRunner(goal, store)
    asyncio.run(runner.run())


@goal_app.command("pause")
def goal_pause() -> None:
    """Pause the current goal (sends interrupt signal)."""
    # In practice, pause is done via Ctrl-C during `goal run`.
    # This command is for future use with background/daemon mode.
    console.print("[yellow]Use Ctrl-C during `xbot goal run` to pause.[/yellow]")


@goal_app.command("resume")
def goal_resume(
    goal_id: str | None = typer.Argument(None, help="Goal ID"),
) -> None:
    """Resume a paused goal (alias for `goal run`)."""
    goal_run(goal_id)


@goal_app.command("clear")
def goal_clear(
    goal_id: str | None = typer.Argument(None, help="Goal ID to clear"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Clear/delete a goal."""
    workspace = Path.cwd()
    store = GoalStore(workspace)

    if goal_id:
        goal = store.load(goal_id)
    else:
        goal = store.find_active()

    if not goal:
        console.print("[red]No goal found.[/red]")
        raise typer.Exit(1)

    if goal.status == GoalStatus.ACTIVE and not force:
        console.print("[red]Goal is active. Use --force to clear, or pause first.[/red]")
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(f"Clear goal '{goal.objective}'?")
        if not confirm:
            raise typer.Exit(0)

    store.delete(goal.goal_id)
    console.print(f"[green]Goal cleared:[/green] {goal.goal_id}")


@goal_app.command("list")
def goal_list() -> None:
    """List all goals in the current workspace."""
    workspace = Path.cwd()
    store = GoalStore(workspace)
    goals = store.list_goals()

    if not goals:
        console.print("[dim]No goals found.[/dim]")
        return

    table = Table(title="Goals")
    table.add_column("ID", style="cyan")
    table.add_column("Status")
    table.add_column("Loop")
    table.add_column("Objective")

    status_styles = {
        GoalStatus.INITIALIZED: "dim",
        GoalStatus.ACTIVE: "bold green",
        GoalStatus.PAUSED: "yellow",
        GoalStatus.ACHIEVED: "green",
        GoalStatus.UNMET: "red",
    }

    for goal in goals:
        style = status_styles.get(goal.status, "")
        table.add_row(
            goal.goal_id,
            f"[{style}]{goal.status.value}[/{style}]",
            f"{goal.loop_count}/{goal.max_loops}",
            goal.objective[:50] + ("..." if len(goal.objective) > 50 else ""),
        )

    console.print(table)


@goal_app.command("show")
def goal_show(
    goal_id: str | None = typer.Argument(None, help="Goal ID"),
) -> None:
    """Show details of a goal."""
    workspace = Path.cwd()
    store = GoalStore(workspace)

    if goal_id:
        goal = store.load(goal_id)
    else:
        goal = store.find_active()

    if not goal:
        console.print("[red]No goal found.[/red]")
        raise typer.Exit(1)

    verified_note = ""
    if goal.status == GoalStatus.ACHIEVED and not goal.verify_cmd:
        verified_note = " (self-verified)"

    console.print(f"[bold]ID:[/bold]        {goal.goal_id}")
    console.print(f"[bold]Objective:[/bold] {goal.objective}")
    console.print(f"[bold]Status:[/bold]    {goal.status.value}{verified_note}")
    console.print(f"[bold]Loop:[/bold]      {goal.loop_count}/{goal.max_loops}")
    console.print(f"[bold]Phase:[/bold]     {goal.current_phase.value if goal.current_phase else '-'}")
    console.print(f"[bold]Verify:[/bold]    {goal.verify_cmd or '(agent self-check)'}")
    console.print(f"[bold]Workspace:[/bold] {goal.workspace}")
    console.print(f"[bold]Session:[/bold]   {goal.session_key}")
    if goal.checkpoint:
        console.print(f"[bold]Checkpoint:[/bold] {goal.checkpoint[:8]}")
    console.print(f"[bold]Created:[/bold]   {goal.created_at}")
    console.print(f"[bold]Updated:[/bold]   {goal.updated_at}")


@goal_app.command("modify")
def goal_modify(
    goal_id: str | None = typer.Argument(None, help="Goal ID"),
    verify: str | None = typer.Option(None, "--verify", "-v", help="New validation command"),
    max_loops: int | None = typer.Option(None, "--max-loops", "-n", help="New max loops"),
) -> None:
    """Modify goal configuration."""
    workspace = Path.cwd()
    store = GoalStore(workspace)

    if goal_id:
        goal = store.load(goal_id)
    else:
        goal = store.find_active()

    if not goal:
        console.print("[red]No goal found.[/red]")
        raise typer.Exit(1)

    changed = False
    if verify is not None:
        goal.verify_cmd = verify
        changed = True
        console.print(f"  verify_cmd → {verify}")

    if max_loops is not None:
        goal.max_loops = max_loops
        changed = True
        console.print(f"  max_loops → {max_loops}")

    if changed:
        store.save(goal)
        console.print("[green]Goal updated.[/green]")
    else:
        console.print("[dim]Nothing to modify. Use --verify or --max-loops.[/dim]")
