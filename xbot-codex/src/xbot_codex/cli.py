from __future__ import annotations

import typer
from aiohttp import web

from xbot_codex.channels.manager import ChannelManager
from xbot_codex.config import load_config
from xbot_codex.runtime import CodexRuntime
from xbot_codex.service.app import CodexService
from xbot_codex.service.health import create_health_app
from xbot_codex.session.store import SessionStore

app = typer.Typer(add_completion=False)


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 18791,
    config_path: str | None = None,
) -> None:
    config = load_config(config_path)
    config.gateway.host = host or config.gateway.host
    config.gateway.port = port or config.gateway.port
    runtime = CodexRuntime(
        config=config,
        session_store=SessionStore(default_workdir_root=config.codex.workdir_root),
    )
    service = CodexService(
        config=config,
        runtime=runtime,
        channel_manager=ChannelManager(config.channels, on_message=lambda msg: service.bus.publish_inbound(msg)),
    )
    app = create_health_app(runtime)

    async def on_startup(_: web.Application) -> None:
        await service.start()

    async def on_cleanup(_: web.Application) -> None:
        await service.shutdown()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host=config.gateway.host, port=config.gateway.port)


@app.command()
def version() -> None:
    from xbot_codex import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
