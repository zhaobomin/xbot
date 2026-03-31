"""CLI helpers for xbot webui."""

from __future__ import annotations

import typer

from xbot.logging import configure_logging
from xbot.webui.app import create_app
from xbot.webui.bootstrap import build_services

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

    from xbot.cli.commands import _load_runtime_config, _make_agent_runtime

    configure_logging(level="DEBUG" if verbose else "INFO")
    loaded = _load_runtime_config(config, workspace)
    services = build_services(loaded, make_runtime=_make_agent_runtime)
    app = create_app(services)
    uvicorn.run(app, host=host, port=port)
