"""CLI helpers for xbot webui."""

from __future__ import annotations

import typer

from xbot.interfaces.webui.app import create_app
from xbot.interfaces.webui.auth import (
    print_reset_password_banner,
    reset_password,
    set_password,
)
from xbot.interfaces.webui.bootstrap import build_services
from xbot.platform.logging.core import configure_logging

webui_app = typer.Typer(help="Run the standalone WebUI adapter")


@webui_app.command("serve")
def serve(
    port: int = typer.Option(18780, "--port", "-p", help="WebUI port"),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
) -> None:
    """Start the standalone WebUI adapter."""
    import uvicorn

    from xbot.interfaces.cli.commands import _load_runtime_config, _make_agent_service

    configure_logging(level="DEBUG" if verbose else "INFO")
    loaded = _load_runtime_config(config, workspace)
    services = build_services(loaded, make_runtime=_make_agent_service)
    app = create_app(services)
    uvicorn.run(app, host=host, port=port)


@webui_app.command("set-password")
def set_password_cmd(
    password: str = typer.Option(
        ..., "--password", "-p", prompt="New password", hide_input=True,
        help="New password for WebUI admin user",
    ),
) -> None:
    """Set a new password for the WebUI admin user.

    Example:
        xbot webui set-password -p my-new-secure-password
    """
    set_password(password)
    print("Password updated successfully.")
    print("Use the new password when logging into the WebUI.")


@webui_app.command("reset-password")
def reset_password_cmd() -> None:
    """Generate a new random password for the WebUI admin user.

    This will invalidate the old password. The new password will be
    printed to the console.

    Example:
        xbot webui reset-password
    """
    password = reset_password()
    print_reset_password_banner(password)
