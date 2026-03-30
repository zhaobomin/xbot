import pytest
from aiohttp.test_utils import TestClient, TestServer

from xbot_codex.config import ServiceConfig
from xbot_codex.runtime import CodexRuntime
from xbot_codex.service.health import create_health_app
from xbot_codex.session.store import SessionStore


@pytest.mark.asyncio
async def test_health_endpoints_report_runtime_status() -> None:
    runtime = CodexRuntime(
        config=ServiceConfig(),
        session_store=SessionStore(default_workdir_root="/tmp/xbot-codex"),
    )
    app = create_health_app(runtime)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")
        status = await client.get("/status")
        assert live.status == 200
        assert ready.status == 200
        assert status.status == 200
        assert (await live.json())["status"] == "alive"
        assert (await ready.json())["ready"] is True
        body = await status.json()
        assert body["service"] == "xbot-codex"
        assert body["running_sessions"] == 0
    finally:
        await client.close()
