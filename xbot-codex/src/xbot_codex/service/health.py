from __future__ import annotations

from aiohttp import web

from xbot_codex.runtime import CodexRuntime


def create_health_app(runtime: CodexRuntime) -> web.Application:
    app = web.Application()

    async def live(_: web.Request) -> web.Response:
        return web.json_response({"status": "alive"})

    async def ready(_: web.Request) -> web.Response:
        return web.json_response({"ready": True})

    async def status(_: web.Request) -> web.Response:
        return web.json_response(runtime.status())

    app.router.add_get("/health/live", live)
    app.router.add_get("/health/ready", ready)
    app.router.add_get("/status", status)
    return app
