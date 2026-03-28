"""CLI commands for role management.

This module provides commands for:
- Listing available roles
- Showing role details
- Creating new roles
- Validating role files
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

from xbot.agent.crew.planner import (
    Capability,
    RoleDefinition,
    RolePoolConfig,
    RolePoolManager,
    RoleTier,
)
from xbot.agent.crew.planner.role_creator import RoleCreator, validate_role_file

app = typer.Typer(help="Role pool management commands")
console = Console()


@app.command("list")
def roles_list(
    tier: str = typer.Option(
        "all",
        "--tier", "-t",
        help="Filter by tier: core, extended, specialist, all",
    ),
    custom_dir: Optional[str] = typer.Option(
        None,
        "--custom-dir", "-c",
        help="Directory containing custom roles",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON",
    ),
):
    """List all available roles."""
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

    # Configure and load pool
    config = RolePoolConfig(
        enabled_tiers=enabled_tiers,
        custom_roles_dir=custom_dir,
    )
    manager = RolePoolManager(config)
    pool = manager.get_pool()

    roles = pool.get_available_roles()

    if json_output:
        import json
        data = [r.to_dict() for r in roles]
        console.print_json(json.dumps(data, indent=2))
        return

    if not roles:
        console.print("[yellow]No roles found[/yellow]")
        return

    # Create table
    table = Table(title="Available Roles")
    table.add_column("Name", style="cyan")
    table.add_column("Display Name", style="green")
    table.add_column("Tier", style="yellow")
    table.add_column("Capabilities", style="magenta")
    table.add_column("Description")

    for role in sorted(roles, key=lambda r: (r.tier.value, r.name)):
        caps = ", ".join(c.value for c in role.capabilities[:3])
        if len(role.capabilities) > 3:
            caps += f" +{len(role.capabilities) - 3}"
        table.add_row(
            role.name,
            role.display_name,
            role.tier.value,
            caps,
            role.description[:50] + "..." if len(role.description) > 50 else role.description,
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(roles)} role(s)[/dim]")


@app.command("show")
def roles_show(
    name: str = typer.Argument(..., help="Role name to show"),
    custom_dir: Optional[str] = typer.Option(
        None,
        "--custom-dir", "-c",
        help="Directory containing custom roles",
    ),
    yaml_output: bool = typer.Option(
        False,
        "--yaml",
        help="Output as YAML",
    ),
):
    """Show details of a specific role."""
    config = RolePoolConfig(
        enabled_tiers=[RoleTier.CORE, RoleTier.EXTENDED, RoleTier.SPECIALIST],
        custom_roles_dir=custom_dir,
    )
    manager = RolePoolManager(config)
    pool = manager.get_pool()

    role = pool.get_role(name)
    if role is None:
        console.print(f"[red]Role '{name}' not found[/red]")
        available = [r.name for r in pool.get_available_roles()]
        console.print(f"[dim]Available roles: {', '.join(available[:10])}{'...' if len(available) > 10 else ''}[/dim]")
        raise typer.Exit(1)

    if yaml_output:
        import yaml
        yaml_str = yaml.dump(role.to_dict(), default_flow_style=False, allow_unicode=True)
        syntax = Syntax(yaml_str, "yaml", theme="monokai")
        console.print(syntax)
        return

    # Display role details
    caps = "\n".join(f"  • {c.value}" for c in role.capabilities)
    tools = ", ".join(role.tools) if role.tools else "all available"
    examples = "\n".join(f"  • {e}" for e in role.examples) if role.examples else "  None"

    content = f"""
[bold cyan]Name:[/bold cyan] {role.name}
[bold cyan]Display Name:[/bold cyan] {role.display_name}
[bold cyan]Tier:[/bold cyan] {role.tier.value}
[bold cyan]Description:[/bold cyan] {role.description}
[bold cyan]Goal:[/bold cyan] {role.goal}
[bold cyan]Backstory:[/bold cyan] {role.backstory[:100]}{'...' if len(role.backstory) > 100 else ''}

[bold cyan]Capabilities:[/bold cyan]
{caps}

[bold cyan]Tools:[/bold cyan] {tools}
[bold cyan]Max Iterations:[/bold cyan] {role.max_iterations}
[bold cyan]Timeout Multiplier:[/bold cyan] {role.timeout_multiplier}

[bold cyan]Tags:[/bold cyan] {', '.join(role.tags) if role.tags else 'None'}
[bold cyan]Examples:[/bold cyan]
{examples}
"""
    console.print(Panel(content, title=f"Role: {role.name}", border_style="cyan"))


@app.command("create")
def roles_create(
    name: Optional[str] = typer.Option(
        None,
        "--name", "-n",
        help="Role name (identifier)",
    ),
    display_name: Optional[str] = typer.Option(
        None,
        "--display-name", "-d",
        help="Human-readable name",
    ),
    description: Optional[str] = typer.Option(
        None,
        "--description",
        help="Role description",
    ),
    goal: Optional[str] = typer.Option(
        None,
        "--goal",
        help="Role goal",
    ),
    capabilities: Optional[str] = typer.Option(
        None,
        "--capabilities", "-c",
        help="Comma-separated capabilities",
    ),
    tools: Optional[str] = typer.Option(
        None,
        "--tools", "-t",
        help="Comma-separated tools",
    ),
    max_iterations: int = typer.Option(
        30,
        "--max-iterations",
        help="Maximum iterations",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output", "-o",
        help="Output directory for the role file",
    ),
    from_file: Optional[str] = typer.Option(
        None,
        "--from-file",
        help="Load role from a YAML file",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help="Use interactive mode for missing fields",
    ),
):
    """Create a new role.

    Can be used in interactive mode (default) or with command-line arguments.
    """
    creator = RoleCreator(
        custom_roles_dir=Path(output) if output else None,
        require_confirmation=True,
    )

    # Load from file if specified
    if from_file:
        role = creator.load_role_from_file(Path(from_file))
        if role is None:
            console.print(f"[red]Failed to load role from {from_file}[/red]")
            raise typer.Exit(1)
        console.print(f"[green]Loaded role from {from_file}[/green]")

        # Validate
        errors = creator.validate_role(role)
        if errors:
            console.print("[red]Validation errors:[/red]")
            for err in errors:
                console.print(f"  • {err}")
            raise typer.Exit(1)

        # Save
        if output:
            save_path = creator.save_role(role, Path(output) / f"{role.name}.yaml")
            console.print(f"[green]Saved role to {save_path}[/green]")
        return

    # Interactive mode for missing fields
    if interactive and not all([name, description, goal, capabilities]):
        console.print("[bold]Role Creation Wizard[/bold]\n")

        if not name:
            name = typer.prompt("Role name (identifier, e.g., my_analyst)")

        if not display_name:
            display_name = typer.prompt("Display name", default=name.replace("_", " ").title())

        if not description:
            description = typer.prompt("Description")

        if not goal:
            goal = typer.prompt("Goal")

        if not capabilities:
            # Show available capabilities
            cap_list = [c.value for c in Capability]
            console.print(f"\nAvailable capabilities: {', '.join(cap_list)}")
            capabilities = typer.prompt("Capabilities (comma-separated)")

    # Validate required fields
    if not name:
        console.print("[red]Error: Role name is required[/red]")
        raise typer.Exit(1)

    if not capabilities:
        console.print("[red]Error: Capabilities are required[/red]")
        raise typer.Exit(1)

    # Parse capabilities
    cap_list = []
    for cap_str in capabilities.split(","):
        cap_str = cap_str.strip()
        try:
            cap_list.append(Capability(cap_str))
        except ValueError:
            console.print(f"[yellow]Warning: Unknown capability '{cap_str}', skipping[/yellow]")

    if not cap_list:
        console.print("[red]Error: No valid capabilities specified[/red]")
        raise typer.Exit(1)

    # Parse tools
    tool_list = None
    if tools:
        tool_list = [t.strip() for t in tools.split(",")]

    # Create role
    result = creator.create_role_from_definition(
        name=name,
        display_name=display_name or name.replace("_", " ").title(),
        description=description or f"Custom role: {name}",
        goal=goal or f"Provide {', '.join(c.value for c in cap_list)} capabilities",
        backstory="",
        capabilities=cap_list,
        tools=tool_list,
        max_iterations=max_iterations,
    )

    if not result.success:
        console.print("[red]Failed to create role:[/red]")
        for err in result.errors:
            console.print(f"  • {err}")
        raise typer.Exit(1)

    # Show warnings
    for warn in result.warnings:
        console.print(f"[yellow]Warning: {warn}[/yellow]")

    # Show role details
    role = result.role
    console.print(f"\n[green]✓ Role created successfully![/green]")
    console.print(f"\n[bold]Role: {role.display_name} ({role.name})[/bold]")
    console.print(f"  Description: {role.description}")
    console.print(f"  Capabilities: {', '.join(c.value for c in role.capabilities)}")
    console.print(f"  Tools: {', '.join(role.tools) if role.tools else 'all available'}")

    # Save if output specified
    if output:
        save_path = creator.save_role(role)
        console.print(f"\n[green]Saved to: {save_path}[/green]")
    else:
        console.print(f"\n[dim]Use --output to save the role to a file[/dim]")


@app.command("validate")
def roles_validate(
    file: str = typer.Argument(..., help="Path to role YAML file"),
    verbose: bool = typer.Option(
        False,
        "--verbose", "-v",
        help="Show detailed information",
    ),
):
    """Validate a role definition file."""
    path = Path(file)

    if not path.exists():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    is_valid, errors, role = validate_role_file(path)

    if is_valid:
        console.print(f"[green]✓ Role '{role.name}' is valid[/green]")

        if verbose and role:
            console.print(f"\n[dim]Name:[/dim] {role.name}")
            console.print(f"[dim]Display Name:[/dim] {role.display_name}")
            console.print(f"[dim]Tier:[/dim] {role.tier.value}")
            console.print(f"[dim]Capabilities:[/dim] {', '.join(c.value for c in role.capabilities)}")
            console.print(f"[dim]Tools:[/dim] {', '.join(role.tools) if role.tools else 'all'}")
    else:
        console.print(f"[red]✗ Role validation failed[/red]")
        for err in errors:
            console.print(f"  • {err}")
        raise typer.Exit(1)


@app.command("copy")
def roles_copy(
    source: str = typer.Argument(..., help="Source role name"),
    target: str = typer.Argument(..., help="Target role name"),
    custom_dir: Optional[str] = typer.Option(
        None,
        "--custom-dir", "-c",
        help="Directory containing custom roles",
    ),
    output: Optional[str] = typer.Option(
        None,
        "--output", "-o",
        help="Output directory for the new role",
    ),
):
    """Copy a role to a new name."""
    # Load source role
    config = RolePoolConfig(
        enabled_tiers=[RoleTier.CORE, RoleTier.EXTENDED, RoleTier.SPECIALIST],
        custom_roles_dir=custom_dir,
    )
    manager = RolePoolManager(config)
    pool = manager.get_pool()

    source_role = pool.get_role(source)
    if source_role is None:
        console.print(f"[red]Source role '{source}' not found[/red]")
        raise typer.Exit(1)

    # Create copy
    creator = RoleCreator(custom_roles_dir=Path(output) if output else None)

    new_role = RoleDefinition(
        name=target,
        display_name=source_role.display_name,
        description=source_role.description,
        goal=source_role.goal,
        backstory=source_role.backstory,
        tier=source_role.tier,
        capabilities=source_role.capabilities.copy(),
        tools=source_role.tools.copy() if source_role.tools else None,
        tool_restrictions=source_role.tool_restrictions.copy() if source_role.tool_restrictions else None,
        max_iterations=source_role.max_iterations,
        timeout_multiplier=source_role.timeout_multiplier,
        tags=source_role.tags.copy() + ["copy"],
        examples=source_role.examples.copy(),
    )

    # Save
    if output:
        save_path = creator.save_role(new_role)
        console.print(f"[green]✓ Copied role '{source}' to '{target}'[/green]")
        console.print(f"[dim]Saved to: {save_path}[/dim]")
    else:
        console.print("[yellow]Warning: No output directory specified. Role not saved.[/yellow]")
        console.print("Use --output to specify where to save the new role.")


@app.command("delete")
def roles_delete(
    name: str = typer.Argument(..., help="Role name to delete"),
    custom_dir: str = typer.Option(
        ...,
        "--custom-dir", "-c",
        help="Directory containing the custom role",
    ),
    force: bool = typer.Option(
        False,
        "--force", "-f",
        help="Skip confirmation",
    ),
):
    """Delete a custom role file."""
    # Security: Validate role name to prevent path traversal
    if not name or "/" in name or "\\" in name or ".." in name:
        console.print(f"[red]Invalid role name: '{name}'[/red]")
        console.print("[dim]Role name cannot contain path separators or '..'[/dim]")
        raise typer.Exit(1)

    # Validate name pattern
    import re
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", name):
        console.print(f"[red]Invalid role name format: '{name}'[/red]")
        console.print("[dim]Name must start with a letter and contain only letters, numbers, underscores, and hyphens[/dim]")
        raise typer.Exit(1)

    custom_path = Path(custom_dir).resolve()
    role_path = custom_path / f"{name}.yaml"

    # Security: Ensure resolved path is within custom_dir
    try:
        role_path.resolve().relative_to(custom_path)
    except ValueError:
        console.print(f"[red]Security error: Invalid path[/red]")
        raise typer.Exit(1)

    if not role_path.exists():
        console.print(f"[red]Role file not found: {role_path}[/red]")
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(f"Delete role '{name}' from {role_path}?")
        if not confirm:
            console.print("[yellow]Cancelled[/yellow]")
            raise typer.Exit(0)

    role_path.unlink()
    console.print(f"[green]✓ Deleted role '{name}'[/green]")


@app.command("export")
def roles_export(
    name: str = typer.Argument(..., help="Role name to export"),
    output: str = typer.Option(
        ...,
        "--output", "-o",
        help="Output file path",
    ),
    custom_dir: Optional[str] = typer.Option(
        None,
        "--custom-dir", "-c",
        help="Directory containing custom roles",
    ),
):
    """Export a role to a YAML file."""
    config = RolePoolConfig(
        enabled_tiers=[RoleTier.CORE, RoleTier.EXTENDED, RoleTier.SPECIALIST],
        custom_roles_dir=custom_dir,
    )
    manager = RolePoolManager(config)
    pool = manager.get_pool()

    role = pool.get_role(name)
    if role is None:
        console.print(f"[red]Role '{name}' not found[/red]")
        raise typer.Exit(1)

    creator = RoleCreator()
    save_path = creator.save_role(role, Path(output))
    console.print(f"[green]✓ Exported role '{name}' to {save_path}[/green]")


@app.command("import")
def roles_import(
    file: str = typer.Argument(..., help="Path to role YAML file"),
    output: Optional[str] = typer.Option(
        None,
        "--output", "-o",
        help="Output directory for the imported role",
    ),
):
    """Import a role from a YAML file."""
    source_path = Path(file)

    if not source_path.exists():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    creator = RoleCreator(custom_roles_dir=Path(output) if output else None)
    role = creator.load_role_from_file(source_path)

    if role is None:
        console.print(f"[red]Failed to load role from {file}[/red]")
        raise typer.Exit(1)

    # Validate
    errors = creator.validate_role(role)
    if errors:
        console.print("[red]Validation errors:[/red]")
        for err in errors:
            console.print(f"  • {err}")
        raise typer.Exit(1)

    # Save
    if output:
        save_path = creator.save_role(role)
        console.print(f"[green]✓ Imported role '{role.name}' to {save_path}[/green]")
    else:
        console.print(f"[green]✓ Role '{role.name}' validated successfully[/green]")
        console.print("[dim]Use --output to save the imported role[/dim]")