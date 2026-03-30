"""CLI commands for dynamic crew planning.

This module provides commands for:
- Planning crew configurations from goals
- Running crews dynamically without pre-defined config files
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from xbot.agent.crew.planner import (
    CrewPlanner,
    CrewPlan,
    RolePoolConfig,
    RoleTier,
)

app = typer.Typer(help="Dynamic crew planning commands")
console = Console()


@app.command("plan")
def crew_plan(
    goal: str = typer.Argument(..., help="Goal description for the crew"),
    workspace: str = typer.Option(
        ".",
        "--workspace", "-w",
        help="Workspace path",
    ),
    tier: str = typer.Option(
        "core",
        "--tier", "-t",
        help="Role tier to use: core, extended, specialist, all",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output", "-o",
        help="Output file path for the generated config",
    ),
    preview: bool = typer.Option(
        False,
        "--preview", "-p",
        help="Show a preview of the plan",
    ),
    save: bool = typer.Option(
        False,
        "--save",
        help="Save the generated config to a temp file",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """Generate a crew configuration from a goal description.

    This command analyzes the goal and automatically selects appropriate
    roles and creates a task plan.

    Example:
        xbot crew plan "Analyze code quality and fix bugs"
        xbot crew plan "Write tests for the API" --tier extended --save
    """
    # Validate goal length
    if len(goal) > 10000:
        console.print("[red]Error: Goal description is too long (max 10000 characters)[/red]")
        raise typer.Exit(1)

    if not goal.strip():
        console.print("[red]Error: Goal description cannot be empty[/red]")
        raise typer.Exit(1)

    # Validate workspace
    workspace_path = Path(workspace).resolve()
    if not workspace_path.exists():
        console.print(f"[red]Error: Workspace path does not exist: {workspace_path}[/red]")
        raise typer.Exit(1)

    # Determine enabled tiers
    if tier == "all":
        enabled_tiers = [RoleTier.CORE, RoleTier.EXTENDED, RoleTier.SPECIALIST]
    else:
        try:
            enabled_tiers = [RoleTier(tier.lower())]
        except ValueError:
            valid = ["core", "extended", "specialist", "all"]
            console.print(f"[red]Invalid tier '{tier}'. Valid options: {valid}[/red]")
            raise typer.Exit(1)

    # Create planner
    config = RolePoolConfig(enabled_tiers=enabled_tiers)
    planner = CrewPlanner(role_pool_config=config)

    # Build context
    context = {
        "workspace": str(workspace_path),
    }

    console.print(f"[dim]Planning crew for:[/dim] {goal}")
    console.print(f"[dim]Tier: {tier}[/dim]\n")

    # Generate plan
    try:
        plan = planner.plan(goal, context)
    except Exception as e:
        console.print(f"[red]Planning failed: {e}[/red]")
        raise typer.Exit(1)

    # Output results
    if json_output:
        import json
        console.print_json(json.dumps(plan.to_dict(), indent=2))
        return

    # Show preview
    if preview:
        preview_str = planner.preview(plan)
        console.print(Panel(preview_str, title="Crew Plan Preview", border_style="cyan"))
        console.print()

    # Generate YAML
    yaml_content = planner.generate_config(plan)

    # Save to file
    if output:
        output_path = Path(output)
        planner.save_config(plan, output_path)
        console.print(f"[green]✓ Saved config to: {output_path}[/green]")

    elif save:
        # Save to temp file
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix="_crew.yaml",
            delete=False,
            prefix=f"{plan.name}_",
        ) as f:
            f.write(yaml_content)
            console.print(f"[green]✓ Saved config to: {f.name}[/green]")

    else:
        # Print YAML
        syntax = Syntax(yaml_content, "yaml", theme="monokai")
        console.print(Panel(syntax, title="Generated Crew Config", border_style="green"))

    # Show summary
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Crew Name: {plan.name}")
    console.print(f"  Process: {plan.process}")
    console.print(f"  Roles: {len(plan.roles)}")
    console.print(f"  Tasks: {len(plan.tasks)}")
    console.print(f"  Confidence: {plan.confidence:.0%}")
    console.print(f"  Planning Time: {plan.planning_time:.2f}s")


@app.command("run-dynamic")
def crew_run_dynamic(
    goal: str = typer.Argument(..., help="Goal description for the crew"),
    workspace: str = typer.Option(
        ".",
        "--workspace", "-w",
        help="Workspace path",
    ),
    tier: str = typer.Option(
        "core",
        "--tier", "-t",
        help="Role tier to use: core, extended, specialist, all",
    ),
    config: Optional[str] = typer.Option(
        None,
        "--config", "-c",
        help="xbot config file",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose", "-v",
        help="Verbose output",
    ),
    save_config: bool = typer.Option(
        False,
        "--save-config",
        help="Save the generated config file",
    ),
    preview: bool = typer.Option(
        False,
        "--preview", "-p",
        help="Show plan preview before running",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Generate plan without executing",
    ),
):
    """Plan and run a crew dynamically from a goal description.

    This command combines planning and execution:
    1. Analyzes the goal
    2. Selects appropriate roles
    3. Creates task plan
    4. Executes the crew

    Example:
        xbot crew run-dynamic "Analyze and fix bugs in the codebase"
        xbot crew run-dynamic "Write documentation" --tier extended --save-config
    """
    from xbot.agent.crew import CrewOrchestrator, load_crew_config
    from xbot.agent.crew.models import parse_crew_config
    from xbot.agent.crew.config import CrewConfigLoader
    from xbot.cli.commands import _load_runtime_config, InteractivePermissionHandler

    # Validate goal length
    if len(goal) > 10000:
        console.print("[red]Error: Goal description is too long (max 10000 characters)[/red]")
        raise typer.Exit(1)

    if not goal.strip():
        console.print("[red]Error: Goal description cannot be empty[/red]")
        raise typer.Exit(1)

    # Validate workspace
    workspace_path = Path(workspace).resolve()
    if not workspace_path.exists():
        console.print(f"[red]Error: Workspace path does not exist: {workspace_path}[/red]")
        raise typer.Exit(1)

    # Determine enabled tiers
    if tier == "all":
        enabled_tiers = [RoleTier.CORE, RoleTier.EXTENDED, RoleTier.SPECIALIST]
    else:
        try:
            enabled_tiers = [RoleTier(tier.lower())]
        except ValueError:
            valid = ["core", "extended", "specialist", "all"]
            console.print(f"[red]Invalid tier '{tier}'. Valid options: {valid}[/red]")
            raise typer.Exit(1)

    # Create planner
    pool_config = RolePoolConfig(enabled_tiers=enabled_tiers)
    planner = CrewPlanner(role_pool_config=pool_config)
    context = {
        "workspace": str(workspace_path),
    }

    console.print(f"[bold cyan]Planning crew for:[/bold cyan] {goal}")

    # Step 1: Generate plan
    try:
        plan = planner.plan(goal, context)
    except Exception as e:
        console.print(f"[red]Planning failed: {e}[/red]")
        raise typer.Exit(1)

    # Show plan summary
    console.print(f"\n[green]✓ Plan generated[/green]")
    console.print(f"  Roles: {', '.join(r.name for r in plan.roles)}")
    console.print(f"  Tasks: {len(plan.tasks)}")
    console.print(f"  Confidence: {plan.confidence:.0%}")

    # Show preview if requested
    if preview:
        preview_str = planner.preview(plan)
        console.print(Panel(preview_str, title="Plan Preview", border_style="cyan"))

    # Dry run - stop here
    if dry_run:
        yaml_content = planner.generate_config(plan)
        syntax = Syntax(yaml_content, "yaml", theme="monokai")
        console.print(Panel(syntax, title="Generated Config (Dry Run)", border_style="yellow"))
        return

    # Step 2: Generate temp config file
    yaml_content = planner.generate_config(plan)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix="_crew.yaml",
        delete=not save_config,
        prefix=f"{plan.name}_",
    ) as temp_file:
        temp_file.write(yaml_content)
        temp_file.flush()
        temp_file.seek(0)
        config_path = Path(temp_file.name)

        if save_config:
            console.print(f"[dim]Config saved to: {config_path}[/dim]")

        # Step 3: Load and execute
        console.print(f"\n[bold cyan]Executing crew...[/bold cyan]\n")

        # Load xbot config
        xbot_config = _load_runtime_config(config, workspace)

        # Load crew config from temp file
        try:
            loader = CrewConfigLoader()
            config_dict = loader.load(config_path)
            crew_config = parse_crew_config(config_dict, config_path)
        except Exception as e:
            console.print(f"[red]Failed to load generated config: {e}[/red]")
            raise typer.Exit(1)

        # Apply settings
        crew_config.workspace = str(workspace_path)
        if verbose:
            crew_config.verbose = True

        # Permission handler - with defensive access
        try:
            perm_config = xbot_config.agents.claude_sdk.permission
            permission_handler = InteractivePermissionHandler(
                auto_approve_safe_tools=getattr(perm_config, 'auto_approve_safe_tools', True),
                safe_tools=set(getattr(perm_config, 'safe_tools', [])),
            )
        except AttributeError:
            # Fallback to defaults if config structure is different
            permission_handler = InteractivePermissionHandler(
                auto_approve_safe_tools=True,
                safe_tools=set(),
            )

        # Progress callback
        task_count = len(crew_config.tasks)
        completed_count = [0]

        def on_progress(message: str, **kwargs: Any) -> None:
            if "done" in message.lower() or "completed" in message.lower():
                completed_count[0] += 1

            bar_width = 20
            filled = int(bar_width * completed_count[0] / task_count) if task_count > 0 else 0
            bar = "█" * filled + "░" * (bar_width - filled)

            task_name = kwargs.get("task_name") or ""
            if task_name:
                # Ensure task_name is a string and handle None
                task_name_str = str(task_name)[:30] if task_name else ""
                console.print(f"\r[dim][crew][/dim] [{bar}] {completed_count[0]}/{task_count} | {task_name_str:<30}", end="")
            elif verbose:
                console.print(f"[dim][crew][/dim] {message}")

        # Execute
        async def _run() -> None:
            orch = CrewOrchestrator(
                crew_config=crew_config,
                xbot_config=xbot_config,
                permission_handler=permission_handler,
                config_path=str(config_path),
                on_progress=on_progress,
            )
            result = await orch.run()
            console.print()  # Clear progress line
            _print_crew_result(result)

        asyncio.run(_run())


def _print_crew_result(result) -> None:
    """Print crew execution result."""
    from rich.panel import Panel

    success = getattr(result, "status", "") == "completed"
    total_time = float(getattr(result, "total_time", 0.0) or 0.0)
    summary = getattr(result, "summary", "") or ""
    task_results = list(getattr(result, "task_results", []) or [])

    if success:
        console.print(Panel(
            f"[green]✓ Crew execution completed successfully[/green]\n\n"
            f"Tasks completed: {len(task_results)}\n"
            f"Total time: {total_time:.1f}s"
            + (f"\nSummary: {summary}" if summary else ""),
            title="Result",
            border_style="green",
        ))

        # Show task results
        if task_results:
            table = Table(title="Task Results")
            table.add_column("Task", style="cyan")
            table.add_column("Status", style="green")
            table.add_column("Time")

            for task_result in task_results:
                task_status = getattr(task_result, "status", "unknown")
                ok_statuses = {"success", "completed", "partial", "skipped"}
                status = "✓" if task_status in ok_statuses else "✗"
                status_style = "green" if task_status in ok_statuses else "red"
                started_at = getattr(task_result, "started_at", None)
                finished_at = getattr(task_result, "finished_at", None)
                elapsed = 0.0
                if started_at is not None and finished_at is not None:
                    elapsed = max(0.0, (finished_at - started_at).total_seconds())
                table.add_row(
                    task_result.task_name[:40],
                    f"[{status_style}]{status}[/{status_style}] {task_status}",
                    f"{elapsed:.1f}s",
                )

            console.print(table)

    else:
        console.print(Panel(
            f"[red]✗ Crew execution failed[/red]\n\n"
            f"Summary: {summary or 'Unknown error'}\n"
            f"Tasks completed: {len(task_results)}",
            title="Result",
            border_style="red",
        ))


if __name__ == "__main__":
    app()
