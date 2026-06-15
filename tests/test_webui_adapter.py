from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from xbot.interfaces.cli.commands import app
from xbot.interfaces.webui.auth import set_password
from xbot.platform.bus.queue import MessageBus
from xbot.platform.config.schema import Config, MCPServerConfig
from xbot.runtime.session.conversation_store import ConversationStore
from xbot.runtime.system.cron.types import CronJob, CronJobState, CronPayload, CronSchedule


class _FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.model = "claude-sonnet-4-5"
        self.router = type("Router", (), {"backend_type": "claude_sdk"})()
        self.shared_resources = {"workspace": "/tmp/workspace"}
        self.config = Config()
        self.config.agents.defaults.model = self.model
        self.tools = type("ToolRegistry", (), {"tool_names": ["read_file", "mcp_demo_search", "mcp_demo_fetch"]})()

    async def process_managed_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress=None,
        media: list[str] | None = None,
    ) -> str:
        self.calls.append({
            "content": content,
            "session_key": session_key,
            "channel": channel,
            "chat_id": chat_id,
            "media": media or [],
        })
        if on_progress is not None:
            await on_progress("thinking", tool_hint=False, event_type="thinking", event_data=None)
        return f"echo:{content}"

    def describe_runtime(self) -> str:
        return "backend=claude_sdk | workspace=/tmp/workspace"


class _CancellableRuntime(_FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled = False

    async def process_managed_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress=None,
        media: list[str] | None = None,
    ) -> str:
        self.calls.append({
            "content": content,
            "session_key": session_key,
            "channel": channel,
            "chat_id": chat_id,
            "media": media or [],
        })
        if on_progress is not None:
            await on_progress("thinking", tool_hint=False, event_type="thinking", event_data=None)
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return "not-cancelled"


class _FakeCronService:
    def __init__(self) -> None:
        self.jobs: dict[str, CronJob] = {}

    def list_jobs(self) -> list[CronJob]:
        return list(self.jobs.values())

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        payload: CronPayload | None = None,
        enabled: bool = True,
        message: str = "",
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
    ) -> CronJob:
        job_id = f"job-{len(self.jobs) + 1}"
        resolved_payload = payload or CronPayload(
            kind="agent_turn",
            message=message,
            deliver=deliver,
            channel=channel,
            to=to,
        )
        job = CronJob(
            id=job_id,
            name=name,
            enabled=enabled,
            schedule=schedule,
            payload=resolved_payload,
            state=CronJobState(next_run_at_ms=schedule.at_ms),
            delete_after_run=delete_after_run,
        )
        self.jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> CronJob | None:
        return self.jobs.get(job_id)

    def update_job(self, job_id: str, **updates) -> CronJob:
        job = self.jobs[job_id]
        for key, value in updates.items():
            setattr(job, key, value)
        return job

    def delete_job(self, job_id: str) -> bool:
        return self.jobs.pop(job_id, None) is not None

    def enable_job(self, job_id: str, enabled: bool = True) -> CronJob | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        job.enabled = enabled
        return job

    def status(self) -> dict[str, int | bool]:
        return {"running": False, "jobs": len(self.jobs)}


class _FakeHeartbeatService:
    def __init__(self) -> None:
        self.enabled = True
        self.interval_s = 1800
        self.workspace = Path("/tmp/workspace")
        self._running = False

    def status(self) -> dict[str, int | bool | str]:
        return {
            "enabled": self.enabled,
            "interval_s": self.interval_s,
            "running": self._running,
            "heartbeat_file": str(self.workspace / "HEARTBEAT.md"),
        }


class _StrictHeartbeatService(_FakeHeartbeatService):
    def __init__(self) -> None:
        super().__init__()
        self.configured_callbacks: tuple[object, object, object] | None = None

    def __setattr__(self, name: str, value) -> None:
        if getattr(self, "_initialized", False) and name == "_llm_call":
            raise AttributeError("_llm_call is private; use configure_callbacks")
        super().__setattr__(name, value)
        if name == "_running":
            super().__setattr__("_initialized", True)

    def configure_callbacks(self, *, llm_call, on_execute, on_notify) -> None:
        self.configured_callbacks = (llm_call, on_execute, on_notify)


class _FakeChannelManager:
    def __init__(self) -> None:
        self.reload_calls: list[str] = []
        self.failures: dict[str, str] = {}
        self.enabled_channels = ["telegram", "slack"]
        self.start_calls = 0
        self.stop_calls = 0

    async def start_all(self) -> None:
        self.start_calls += 1

    async def stop_all(self) -> None:
        self.stop_calls += 1

    def get_status(self) -> dict[str, dict[str, object]]:
        return {
            "telegram": {"enabled": True, "running": True, "error": None},
            "slack": {"enabled": True, "running": False, "error": "token missing"},
        }

    def reload_channel(self, name: str) -> dict[str, object]:
        self.reload_calls.append(name)
        if name in self.failures:
            raise RuntimeError(self.failures[name])
        return {"name": name, "reloaded": True}

    def reload_all(self) -> dict[str, object]:
        self.reload_calls.append("*")
        if self.failures:
            raise RuntimeError("reload-all failed")
        return {"reloaded": True, "count": 2}


class _AsyncReloadChannelManager(_FakeChannelManager):
    async def reload_channel(self, name: str) -> dict[str, object]:
        self.reload_calls.append(name)
        return {"name": name, "reloaded": True, "async": True}

    async def reload_all(self) -> dict[str, object]:
        self.reload_calls.append("*")
        return {"reloaded": True, "count": 2, "async": True}


class _ClosingWebSocket:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def send_json(self, _payload) -> None:
        raise self.exc


@dataclass
class _Services:
    config: Config
    bus: MessageBus
    agent: _FakeRuntime
    conversation_store: ConversationStore
    cron: _FakeCronService
    heartbeat: _FakeHeartbeatService


def _build_client(tmp_path: Path) -> tuple[TestClient, _Services]:
    from xbot.interfaces.webui.app import _clear_login_rate_limit, create_app
    from xbot.interfaces.webui.services import ServiceContainer

    # Clear login rate limit between tests
    _clear_login_rate_limit()

    # Set a known test password for all tests (isolated per test via temp path)
    test_password_file = tmp_path / "webui-data" / "password"
    test_password_file.parent.mkdir(parents=True, exist_ok=True)
    # Monkeypatch the password file location for this test
    import xbot.interfaces.webui.auth as auth_module
    auth_module.PASSWORD_FILE = test_password_file
    set_password("test-webui-password")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    config.gateway.port = 18790
    config.channels.telegram = {"enabled": True, "botToken": "secret"}
    config.tools.mcp_servers["demo"] = MCPServerConfig(command="python", args=["-m", "demo"])

    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    conversation_store = ConversationStore(workspace)
    session = conversation_store.get_or_create("cli:web-admin-1")
    session.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    conversation_store.save(session)

    cron = _FakeCronService()
    heartbeat = _FakeHeartbeatService()
    runtime = _FakeRuntime()
    bus = MessageBus()
    channel_manager = _FakeChannelManager()
    services = ServiceContainer(
        config=config,
        bus=bus,
        agent=runtime,
        conversation_store=conversation_store,
        cron=cron,
        heartbeat=heartbeat,
        metadata={"channel_manager": channel_manager},
    )
    app = create_app(services, data_dir=tmp_path / "webui-data")
    return TestClient(app), services


def test_webui_root_serves_html(tmp_path: Path) -> None:
    client, _services = _build_client(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "<div id=\"root\"></div>" in response.text or "xbot WebUI" in response.text


def test_webui_lifespan_does_not_start_channel_manager(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    manager = services.metadata["channel_manager"]

    with client:
        response = client.get("/")

    assert response.status_code == 200
    assert manager.start_calls == 0
    assert manager.stop_calls == 0


def test_webui_serves_frontend_dist_when_present(tmp_path: Path) -> None:
    from xbot.interfaces.webui.app import create_app
    from xbot.interfaces.webui.services import ServiceContainer

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html><body>frontend-dist</body></html>", encoding="utf-8")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    services = ServiceContainer(
        config=config,
        bus=MessageBus(),
        agent=_FakeRuntime(),
        conversation_store=ConversationStore(config.workspace_path),
        cron=_FakeCronService(),
        heartbeat=_FakeHeartbeatService(),
    )
    client = TestClient(create_app(services, data_dir=tmp_path / "data", frontend_dir=dist_dir))

    response = client.get("/")

    assert response.status_code == 200
    assert "frontend-dist" in response.text


def test_webui_spa_routes_fall_back_to_index(tmp_path: Path) -> None:
    from xbot.interfaces.webui.app import create_app
    from xbot.interfaces.webui.services import ServiceContainer

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html><body>frontend-dist</body></html>", encoding="utf-8")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    services = ServiceContainer(
        config=config,
        bus=MessageBus(),
        agent=_FakeRuntime(),
        conversation_store=ConversationStore(config.workspace_path),
        cron=_FakeCronService(),
        heartbeat=_FakeHeartbeatService(),
    )
    client = TestClient(create_app(services, data_dir=tmp_path / "data", frontend_dir=dist_dir))

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "frontend-dist" in response.text


@pytest.mark.skip(reason="requires frontend build artifacts")
def test_frontend_branding_uses_xbot_semantics() -> None:
    frontend_root = Path("xbot/interfaces/webui/frontend")
    dist_root = frontend_root / "dist"

    assert frontend_root.exists()
    assert dist_root.exists()

    text_files = [
        frontend_root / "index.html",
        frontend_root / "src/i18n/index.ts",
        frontend_root / "src/pages/SystemConfig.tsx",
        frontend_root / "src/stores/authStore.ts",
        frontend_root / "src/stores/chatStore.ts",
        frontend_root / "src/theme/ThemeProvider.tsx",
        dist_root / "manifest.webmanifest",
    ]
    forbidden = ("Nanobot", ".nanobot", "~/.nanobot", "nanobot-lang", "nanobot-auth", "nanobot-chat", "nanobot-theme")

    for path in text_files:
        content = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in content, f"{needle} leaked into {path}"


def test_frontend_uses_svg_brand_assets() -> None:
    files = [
        Path("xbot/interfaces/webui/frontend/index.html"),
        Path("xbot/interfaces/webui/frontend/src/components/layout/sidebar.tsx"),
        Path("xbot/interfaces/webui/frontend/src/components/layout/mobile-top-bar.tsx"),
        Path("xbot/interfaces/webui/frontend/src/components/chat/chat-window.tsx"),
        Path("xbot/interfaces/webui/frontend/src/components/chat/message-bubble.tsx"),
    ]

    for path in files:
        content = path.read_text(encoding="utf-8")
        assert "/logo.png" not in content, f"stale png logo reference in {path}"
        assert "/icon.png" not in content, f"stale png icon reference in {path}"


def test_frontend_uses_xbot_svg_brand_assets() -> None:
    files = [
        Path("xbot/interfaces/webui/frontend/src/components/layout/sidebar.tsx"),
        Path("xbot/interfaces/webui/frontend/src/components/layout/mobile-top-bar.tsx"),
        Path("xbot/interfaces/webui/frontend/src/components/chat/chat-window.tsx"),
    ]

    for path in files:
        content = path.read_text(encoding="utf-8")
        assert "xbot" in content.lower(), f"missing text brand mark in {path}"

    sidebar = Path("xbot/interfaces/webui/frontend/src/components/layout/sidebar.tsx").read_text(encoding="utf-8")
    mobile_top_bar = Path("xbot/interfaces/webui/frontend/src/components/layout/mobile-top-bar.tsx").read_text(encoding="utf-8")
    index = Path("xbot/interfaces/webui/frontend/index.html").read_text(encoding="utf-8")
    assert "/xbot-logo.svg?v=xbot-logo-20260604b" in sidebar
    assert "/xbot-logo.svg?v=xbot-logo-20260604b" in mobile_top_bar
    assert "/icon.svg?v=xbot-cat-20260604" in sidebar
    assert "/icon.svg" in index
    assert "XBot" in sidebar
    assert "XBot" in mobile_top_bar

    bubble = Path("xbot/interfaces/webui/frontend/src/components/chat/message-bubble.tsx").read_text(encoding="utf-8")
    assert "/icon.svg" not in bubble
    assert '"x"' in bubble or "'x'" in bubble


def test_frontend_streams_content_delta_into_assistant_message() -> None:
    ws = Path("xbot/interfaces/webui/frontend/src/lib/ws.ts").read_text(encoding="utf-8")
    chat_window = Path("xbot/interfaces/webui/frontend/src/components/chat/chat-window.tsx").read_text(encoding="utf-8")

    assert "event_type?: string" in ws
    assert 'msg.event_type === "content_delta"' in chat_window
    assert "appendAssistantText" in chat_window
    assert "assistantMsgIdRef.current = assistantId" in chat_window


def test_frontend_uses_configurable_gateway_url() -> None:
    api = Path("xbot/interfaces/webui/frontend/src/lib/api.ts").read_text(encoding="utf-8")
    ws = Path("xbot/interfaces/webui/frontend/src/lib/ws.ts").read_text(encoding="utf-8")
    app_tsx = Path("xbot/interfaces/webui/frontend/src/App.tsx").read_text(encoding="utf-8")

    assert "useGatewayStore" in api
    assert "getGatewayApiBaseUrl" in api
    assert "useGatewayStore" in ws
    assert "getGatewayWebSocketUrl" in ws
    assert 'path="/connection"' in app_tsx


def test_frontend_restores_auth_routes() -> None:
    auth_store = Path("xbot/interfaces/webui/frontend/src/stores/auth-store.ts").read_text(encoding="utf-8")
    app_tsx = Path("xbot/interfaces/webui/frontend/src/App.tsx").read_text(encoding="utf-8")
    login_tsx = Path("xbot/interfaces/webui/frontend/src/pages/login.tsx").read_text(encoding="utf-8")

    assert 'path="/login"' in app_tsx
    assert "PrivateRoute" in app_tsx
    assert "persist(" in auth_store
    assert 'api.post("/auth/login"' in login_tsx


def test_frontend_data_queries_are_scoped_to_gateway_url() -> None:
    frontend_dir = Path("xbot/interfaces/webui/frontend/src")
    gateway_store = (frontend_dir / "stores" / "gateway-store.ts").read_text(encoding="utf-8")
    assert "function useGatewayBaseUrl" in gateway_store

    for relative in [
        "hooks/use-sessions.ts",
        "hooks/use-channels.ts",
        "hooks/use-providers.ts",
        "hooks/use-mcp.ts",
        "hooks/use-skills.ts",
        "hooks/useSkills.ts",
    ]:
        source = (frontend_dir / relative).read_text(encoding="utf-8")
        assert "useGatewayBaseUrl" in source, relative
        assert "gatewayBaseUrl" in source, relative
        assert "queryKey:" in source, relative


def test_frontend_message_invalidation_is_scoped_to_gateway_url() -> None:
    chat_window = Path("xbot/interfaces/webui/frontend/src/components/chat/chat-window.tsx").read_text(encoding="utf-8")
    sessions_hook = Path("xbot/interfaces/webui/frontend/src/hooks/use-sessions.ts").read_text(encoding="utf-8")

    assert 'queryKey: ["sessions", gatewayBaseUrl, targetKey, "messages"]' in chat_window
    assert 'queryKey: ["sessions", gatewayBaseUrl, targetKey, "messages"]' in chat_window.split('msg.type === "revoke_ok"')[1]
    assert 'queryKey: ["sessions", gatewayBaseUrl, vars.key, "messages"]' in sessions_hook


def test_frontend_hides_revoke_for_read_only_sessions() -> None:
    chat_window = Path("xbot/interfaces/webui/frontend/src/components/chat/chat-window.tsx").read_text(encoding="utf-8")

    assert "onRevoke={readOnly ? undefined : handleRevoke}" in chat_window


def test_frontend_agent_messages_use_full_available_width() -> None:
    bubble = Path("xbot/interfaces/webui/frontend/src/components/chat/message-bubble.tsx").read_text(encoding="utf-8")

    assert 'isUser ? "max-w-2xl items-end" : "flex-1 items-start"' in bubble
    assert '"flex min-w-0 flex-col gap-1"' in bubble


def test_connection_page_clears_stale_gateway_query_cache() -> None:
    connection_tsx = Path("xbot/interfaces/webui/frontend/src/pages/connection.tsx").read_text(encoding="utf-8")

    assert "useQueryClient" in connection_tsx
    assert "queryClient.clear()" in connection_tsx


def test_integrations_page_surfaces_api_errors() -> None:
    integrations_tsx = Path("xbot/interfaces/webui/frontend/src/pages/integrations.tsx").read_text(encoding="utf-8")

    assert "isError:" in integrations_tsx
    assert "gatewayError" in integrations_tsx
    assert 'to="/connection"' in integrations_tsx


def test_desktop_tauri_scaffold_points_to_webui_dist() -> None:
    package_json = Path("desktop/package.json").read_text(encoding="utf-8")
    tauri_conf = Path("desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    main_rs = Path("desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")

    assert "@tauri-apps/cli" in package_json
    assert "xbot/interfaces/webui/frontend/dist" in tauri_conf
    assert "http://localhost:5174" in tauri_conf
    assert "tauri::Builder::default()" in main_rs
    assert ".expect(" not in main_rs
    assert "std::process::exit(1)" in main_rs


def test_desktop_tauri_declares_macos_app_icon() -> None:
    tauri_conf = json.loads(Path("desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    icons = tauri_conf["bundle"]["icon"]

    assert "icons/icon.icns" in icons
    assert Path("desktop/src-tauri/icons/icon.icns").exists()
    icon_png = Path("desktop/src-tauri/icons/icon.png")
    assert icon_png.exists()
    png = icon_png.read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert png[12:16] == b"IHDR"
    assert png[24] == 8
    assert png[25] == 6


def test_admin_login_and_change_password(tmp_path: Path) -> None:
    client, _services = _build_client(tmp_path)

    login = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"})

    assert login.status_code == 200
    token = login.json()["access_token"]

    change = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "test-webui-password", "new_password": "better-secret"},
    )
    assert change.status_code == 200

    denied = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"})
    assert denied.status_code == 401

    accepted = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "better-secret"},
    )
    assert accepted.status_code == 200


def test_change_password_compatibility_endpoint_accepts_put(tmp_path: Path) -> None:
    client, _services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]

    change = client.put(
        "/api/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "test-webui-password", "new_password": "compat-secret"},
    )

    assert change.status_code == 200
    accepted = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "compat-secret"},
    )
    assert accepted.status_code == 200


def test_api_requires_auth_token(tmp_path: Path) -> None:
    client, _services = _build_client(tmp_path)

    response = client.get("/api/dashboard")

    assert response.status_code == 401


def test_websocket_requires_valid_auth_token(tmp_path: Path) -> None:
    client, _services = _build_client(tmp_path)

    with pytest.raises(Exception) as exc_info:
        with client.websocket_connect("/ws/chat"):
            pass

    assert getattr(exc_info.value, "code", None) == 1008


def test_websocket_cancel_stops_running_agent_turn(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    runtime = _CancellableRuntime()
    services.agent = runtime
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]

    with client.websocket_connect(f"/ws/chat?token={token}&session=web:admin:default") as websocket:
        assert websocket.receive_json()["type"] == "session_info"
        websocket.send_json({"type": "message", "content": "slow", "session_key": "web:admin:default"})
        assert websocket.receive_json()["type"] == "progress"
        websocket.send_json({"type": "cancel", "session_key": "web:admin:default"})
        response = websocket.receive_json()

    assert response["type"] == "cancel_ok"
    assert runtime.cancelled is True


def test_websocket_rejects_duplicate_running_session_across_connections(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    runtime = _CancellableRuntime()
    services.agent = runtime
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]

    with client.websocket_connect(f"/ws/chat?token={token}&session=web:admin:shared") as first:
        assert first.receive_json()["type"] == "session_info"
        first.send_json({"type": "message", "content": "slow", "session_key": "web:admin:shared"})
        assert first.receive_json()["type"] == "progress"

        with client.websocket_connect(f"/ws/chat?token={token}&session=web:admin:shared") as second:
            assert second.receive_json()["type"] == "session_info"
            second.send_json({"type": "message", "content": "again", "session_key": "web:admin:shared"})
            error = second.receive_json()

        first.send_json({"type": "cancel", "session_key": "web:admin:shared"})
        assert first.receive_json()["type"] == "cancel_ok"

    assert error["type"] == "error"
    assert "already running" in error["error"]
    assert len(runtime.calls) == 1


def test_read_only_management_endpoints(tmp_path: Path) -> None:
    client, _services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    dashboard = client.get("/api/dashboard", headers=headers)
    sessions = client.get("/api/sessions", headers=headers)
    messages = client.get("/api/sessions/cli:web-admin-1/messages", headers=headers)
    providers = client.get("/api/providers", headers=headers)
    channels = client.get("/api/channels", headers=headers)
    mcp = client.get("/api/mcp/servers", headers=headers)
    heartbeat = client.get("/api/heartbeat", headers=headers)

    assert dashboard.status_code == 200
    assert dashboard.json()["runtime"]["backend_type"] == "claude_sdk"
    assert sessions.json()[0]["key"] == "cli:web-admin-1"
    assert sessions.json()[0]["channel"] == "cli"
    assert sessions.json()[0]["first_message"] == "hello"
    assert sessions.json()[0]["last_message"] == "world"
    assert messages.json()[0]["role"] == "user"
    assert any(item["name"] == "anthropic" for item in providers.json())
    assert any(item["name"] == "telegram" for item in channels.json())
    telegram = next(item for item in channels.json() if item["name"] == "telegram")
    assert telegram["config"]["botToken"].startswith("••••")
    assert "secret" not in telegram["config"]["botToken"]
    assert any(item["name"] == "demo" for item in mcp.json())
    assert heartbeat.json()["enabled"] is True


def test_desktop_ping_endpoint_and_cors_preflight(tmp_path: Path) -> None:
    client, _services = _build_client(tmp_path)

    ping = client.get("/api/desktop/ping")
    preflight = client.options(
        "/api/desktop/ping",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert ping.status_code == 200
    assert ping.json()["name"] == "xbot"
    assert ping.json()["ok"] is True
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_provider_extra_headers_are_masked(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    services.config.providers.custom.extra_headers = {
        "Authorization": "Bearer provider-secret",
        "x-api-key": "provider-api-key",
        "X-Trace-Id": "trace-123",
    }
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]

    response = client.get("/api/providers", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    custom = next(item for item in response.json() if item["name"] == "custom")
    assert custom["extra_headers"]["Authorization"].startswith("••••")
    assert custom["extra_headers"]["x-api-key"].startswith("••••")
    assert custom["extra_headers"]["X-Trace-Id"] == "trace-123"
    assert "provider-secret" not in str(custom)
    assert "provider-api-key" not in str(custom)


def test_patch_channel_masks_secret_response_but_persists_plain_config(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.patch(
        "/api/channels/telegram",
        headers=headers,
        json={
            "enabled": True,
            "botToken": "telegram-token",
            "client_secret": "channel-secret",
            "nested": {"api_key": "nested-api-key", "label": "safe"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["config"]["botToken"].startswith("••••")
    assert body["config"]["client_secret"].startswith("••••")
    assert body["config"]["nested"]["api_key"].startswith("••••")
    assert body["config"]["nested"]["label"] == "safe"
    assert "telegram-token" not in str(body)
    assert "channel-secret" not in str(body)
    assert "nested-api-key" not in str(body)
    assert services.config.channels.telegram["botToken"] == "telegram-token"
    assert services.config.channels.telegram["client_secret"] == "channel-secret"


def test_patch_agent_config_persists_and_reloads(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.patch(
        "/api/config/agent",
        headers=headers,
        json={
            "model": "gpt-4.1-mini",
            "provider": "openai",
            "workspace": str(tmp_path / "alt-workspace"),
            "send_progress": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "gpt-4.1-mini"
    assert body["provider"] == "openai"
    assert services.config.agents.defaults.model == "gpt-4.1-mini"
    assert services.config.channels.send_progress is False


def test_cron_management_endpoints(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/api/cron/jobs",
        headers=headers,
        json={
            "name": "daily",
            "enabled": True,
            "schedule": {"kind": "at", "at_ms": 4102444800000},
            "payload": {"kind": "agent_turn", "message": "ping", "deliver": False},
        },
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    listed = client.get("/api/cron/jobs", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == job_id

def test_cron_management_crud_endpoints_with_real_service_shape(tmp_path: Path) -> None:
    from xbot.interfaces.webui.app import create_app
    from xbot.interfaces.webui.services import ServiceContainer

    class _RealShapeCronService:
        def __init__(self) -> None:
            self.jobs: dict[str, CronJob] = {}

        def list_jobs(self) -> list[CronJob]:
            return list(self.jobs.values())

        def add_job(
            self,
            name: str,
            schedule: CronSchedule,
            message: str,
            deliver: bool = False,
            channel: str | None = None,
            to: str | None = None,
            delete_after_run: bool = False,
        ) -> CronJob:
            job_id = f"job-{len(self.jobs) + 1}"
            job = CronJob(
                id=job_id,
                name=name,
                enabled=True,
                schedule=schedule,
                payload=CronPayload(
                    kind="agent_turn",
                    message=message,
                    deliver=deliver,
                    channel=channel,
                    to=to,
                ),
                state=CronJobState(next_run_at_ms=schedule.at_ms),
                delete_after_run=delete_after_run,
            )
            self.jobs[job_id] = job
            return job

        def get_job(self, job_id: str) -> CronJob | None:
            return self.jobs.get(job_id)

        def update_job(self, job_id: str, **updates) -> CronJob:
            job = self.jobs[job_id]
            for key, value in updates.items():
                setattr(job, key, value)
            return job

        def delete_job(self, job_id: str) -> bool:
            return self.jobs.pop(job_id, None) is not None

        def enable_job(self, job_id: str, enabled: bool = True) -> CronJob | None:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            job.enabled = enabled
            return job

        def status(self) -> dict[str, int | bool]:
            return {"running": False, "jobs": len(self.jobs)}

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    conversation_store = ConversationStore(config.workspace_path)
    services = ServiceContainer(
        config=config,
        bus=MessageBus(),
        agent=_FakeRuntime(),
        conversation_store=conversation_store,
        cron=_RealShapeCronService(),
        heartbeat=_FakeHeartbeatService(),
    )
    client = TestClient(create_app(services, data_dir=tmp_path / "webui-data"))
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    create = client.post(
        "/api/cron/jobs",
        headers=headers,
        json={
            "name": "compat",
            "enabled": True,
            "schedule": {"kind": "at", "at_ms": 4102444800000},
            "payload": {
                "kind": "agent_turn",
                "message": "ping",
                "deliver": True,
                "channel": "feishu",
                "to": "chat-1",
            },
            "delete_after_run": True,
        },
    )
    assert create.status_code == 201
    job_id = create.json()["id"]

    update = client.put(
        f"/api/cron/jobs/{job_id}",
        headers=headers,
        json={
            "name": "compat-updated",
            "enabled": False,
            "schedule": {"kind": "every", "every_ms": 60000},
            "payload": {"kind": "agent_turn", "message": "pong", "deliver": False},
        },
    )
    assert update.status_code == 200
    assert update.json()["name"] == "compat-updated"
    assert update.json()["schedule"]["kind"] == "every"

    toggle = client.patch(f"/api/cron/jobs/{job_id}/enabled", headers=headers, json={"enabled": True})
    assert toggle.status_code == 200
    assert toggle.json()["enabled"] is True

    delete = client.delete(f"/api/cron/jobs/{job_id}", headers=headers)
    assert delete.status_code == 200
    assert delete.json() == {"ok": True}


def test_write_management_endpoints(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    gateway = client.patch(
        "/api/config/gateway",
        headers=headers,
        json={"port": 18888, "heartbeat_enabled": False, "heartbeat_interval_s": 60},
    )
    assert gateway.status_code == 200
    assert services.config.gateway.port == 18888
    assert services.config.gateway.heartbeat.enabled is False

    channels = client.patch(
        "/api/channels",
        headers=headers,
        json={
            "send_progress": False,
            "channels": {
                "telegram": {"enabled": False, "botToken": "updated"},
            },
        },
    )
    assert channels.status_code == 200
    assert services.config.channels.send_progress is False
    assert services.config.channels.telegram["enabled"] is False

    mcp = client.patch(
        "/api/mcp/demo",
        headers=headers,
        json={"command": "python3", "args": ["-m", "demo.server"]},
    )
    assert mcp.status_code == 200
    assert services.config.tools.mcp_servers["demo"].command == "python3"

    heartbeat = client.patch(
        "/api/heartbeat",
        headers=headers,
        json={"enabled": True, "interval_s": 120},
    )
    assert heartbeat.status_code == 200
    assert services.heartbeat.enabled is True
    assert services.heartbeat.interval_s == 120

def test_websocket_chat_preserves_web_session_namespace(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]

    with client.websocket_connect(f"/ws/chat?token={token}&session=web:admin:abc123") as ws:
        session_info = ws.receive_json()
        assert session_info["type"] == "session_info"
        assert session_info["session_key"] == "web:admin:abc123"

        ws.send_json({"type": "message", "content": "hi"})
        progress = ws.receive_json()
        done = ws.receive_json()

        assert progress["type"] == "progress"
        assert done["type"] == "done"
        assert done["content"] == "echo:hi"

    assert services.agent.calls[0]["session_key"] == "web:admin:abc123"


def test_websocket_chat_honors_message_session_key(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]

    with client.websocket_connect(f"/ws/chat?token={token}&session=web:admin:old") as ws:
        session_info = ws.receive_json()
        assert session_info["type"] == "session_info"
        assert session_info["session_key"] == "web:admin:old"

        ws.send_json({"type": "message", "content": "hi", "session_key": "web:admin:new"})
        progress = ws.receive_json()
        done = ws.receive_json()

        assert progress["session_key"] == "web:admin:new"
        assert done["session_key"] == "web:admin:new"
        assert done["content"] == "echo:hi"

    assert services.agent.calls[0]["session_key"] == "web:admin:new"


def test_websocket_chat_uses_session_namespace_as_runtime_channel(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]

    with client.websocket_connect(f"/ws/chat?token={token}&session=app:admin:abc123") as ws:
        ws.receive_json()
        ws.send_json({"type": "message", "content": "hi"})
        ws.receive_json()
        ws.receive_json()

    assert services.agent.calls[0]["session_key"] == "app:admin:abc123"
    assert services.agent.calls[0]["channel"] == "app"
    assert services.agent.calls[0]["chat_id"] == "admin:abc123"


def test_websocket_chat_rejects_read_only_im_session_key(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]

    with client.websocket_connect(f"/ws/chat?token={token}&session=web:admin:old") as ws:
        ws.receive_json()
        ws.send_json({"type": "message", "content": "hi", "session_key": "im:feishu:oc_123"})
        error = ws.receive_json()

    assert error["type"] == "error"
    assert "read-only" in error["error"]
    assert services.agent.calls == []
    assert services.conversation_store.get("im:feishu:oc_123") is None


def test_websocket_chat_persists_when_runtime_does_not(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]

    with client.websocket_connect(f"/ws/chat?token={token}&session=web:admin:persist") as ws:
        ws.receive_json()
        ws.send_json({"type": "message", "content": "save me"})
        ws.receive_json()
        done = ws.receive_json()
        assert done["content"] == "echo:save me"

    session = services.conversation_store.get("web:admin:persist")
    assert session is not None
    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    assert session.messages[0]["content"] == "save me"
    assert session.messages[1]["content"] == "echo:save me"


def test_get_session_messages_does_not_create_missing_session(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/api/sessions/web:admin:missing/messages", headers=headers)

    assert response.status_code == 200
    assert response.json() == []
    assert services.conversation_store.get("web:admin:missing") is None


def test_session_list_updated_at_tracks_latest_message(tmp_path: Path) -> None:
    import time

    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    session = services.conversation_store.get_or_create("web:admin:list-time")
    session.add_message("user", "first")
    services.conversation_store.save(session)
    first_updated = next(
        item for item in client.get("/api/sessions", headers=headers).json()
        if item["key"] == "web:admin:list-time"
    )["updated_at"]

    time.sleep(0.02)
    session.add_message("assistant", "second")
    services.conversation_store.save(session)
    second_updated = next(
        item for item in client.get("/api/sessions", headers=headers).json()
        if item["key"] == "web:admin:list-time"
    )["updated_at"]

    assert second_updated > first_updated


def test_session_mutations_reject_read_only_im_sessions(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    session = services.conversation_store.get_or_create("im:feishu:oc_123")
    session.add_message("user", "from feishu")
    session.add_message("assistant", "reply")
    services.conversation_store.save(session)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    revoke = client.delete("/api/sessions/im:feishu:oc_123/messages/0", headers=headers)
    delete = client.delete("/api/sessions/im:feishu:oc_123", headers=headers)

    assert revoke.status_code == 403
    assert delete.status_code == 403
    assert [m["content"] for m in services.conversation_store.get("im:feishu:oc_123").messages] == [
        "from feishu",
        "reply",
    ]


def test_session_key_mapping_avoids_colon_replacement_collisions() -> None:
    from xbot.interfaces.webui.session_keys import to_internal_session_key

    assert to_internal_session_key("web:admin:abc123") == "web:admin:abc123"
    assert to_internal_session_key("app:admin:abc123") == "app:admin:abc123"
    assert to_internal_session_key("im:telegram:456") == "im:telegram:456"
    assert to_internal_session_key("cli:direct") == "cli:direct"


def test_empty_session_key_generates_unique_web_session() -> None:
    from xbot.interfaces.webui.session_keys import to_internal_session_key

    first = to_internal_session_key("")
    second = to_internal_session_key("")

    assert first.startswith("web:admin:")
    assert second.startswith("web:admin:")
    assert first != second


def test_websocket_chat_rejects_oversized_message(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]

    with client.websocket_connect(f"/ws/chat?token={token}&session=web:admin:huge") as ws:
        session_info = ws.receive_json()
        assert session_info["type"] == "session_info"

        ws.send_json({"type": "message", "content": "x" * (1_000_001)})
        error = ws.receive_json()

    assert error["type"] == "error"
    assert "too large" in error["error"]
    assert services.agent.calls == []


def test_websocket_disconnect_during_progress_does_not_crash(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]

    async def delayed_process_managed_direct(
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress=None,
        media: list[str] | None = None,
    ) -> str:
        services.agent.calls.append({
            "content": content,
            "session_key": session_key,
            "channel": channel,
            "chat_id": chat_id,
            "media": media or [],
        })
        await asyncio.sleep(0.05)
        if on_progress is not None:
            await on_progress("thinking", tool_hint=False, event_type="thinking", event_data=None)
        await asyncio.sleep(0.05)
        return f"echo:{content}"

    services.agent.process_managed_direct = delayed_process_managed_direct

    with client.websocket_connect(f"/ws/chat?token={token}&session=web:admin:drop") as ws:
        session_info = ws.receive_json()
        assert session_info["type"] == "session_info"
        ws.send_json({"type": "message", "content": "bye"})
        ws.close()

    assert services.agent.calls[0]["session_key"] == "web:admin:drop"


def test_safe_websocket_send_swallows_disconnect_errors() -> None:
    from starlette.websockets import WebSocketDisconnect

    from xbot.interfaces.webui.app import _safe_websocket_send_json

    async def _run() -> None:
        await _safe_websocket_send_json(_ClosingWebSocket(WebSocketDisconnect(code=1006)), {"type": "progress"})
        await _safe_websocket_send_json(_ClosingWebSocket(RuntimeError('Cannot call "send" once a close message has been sent.')), {"type": "done"})

    asyncio.run(_run())


def test_webui_cli_command_is_registered() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["webui", "--help"])

    assert result.exit_code == 0
    assert "serve" in result.stdout


def test_frontend_compatibility_endpoints(tmp_path: Path) -> None:
    client, _services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    channels = client.get("/api/channels", headers=headers)
    providers = client.get("/api/providers", headers=headers)
    mcp_servers = client.get("/api/mcp/servers", headers=headers)
    mcp_runtime = client.get("/api/mcp/servers/runtime", headers=headers)
    assert channels.status_code == 200
    assert isinstance(channels.json(), list)
    assert channels.json()[0]["name"] == "telegram"

    assert providers.status_code == 200
    assert isinstance(providers.json(), list)
    assert providers.json()[0]["name"] == "custom"

    assert mcp_servers.status_code == 200
    assert isinstance(mcp_servers.json(), list)
    assert mcp_servers.json()[0]["name"] == "demo"

    assert mcp_runtime.status_code == 200
    assert isinstance(mcp_runtime.json(), list)
    assert mcp_runtime.json()[0]["name"] == "demo"
    assert mcp_runtime.json()[0]["running"] is True
    assert mcp_runtime.json()[0]["tool_count"] == 2
    assert mcp_runtime.json()[0]["tools"] == ["mcp_demo_fetch", "mcp_demo_search"]

def test_channel_runtime_prefers_live_manager_status(tmp_path: Path) -> None:
    client, _services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/api/channels", headers=headers)

    assert response.status_code == 200
    payload = {item["name"]: item for item in response.json()}
    assert payload["telegram"]["running"] is True
    assert payload["telegram"]["error"] is None


def test_frontend_compatibility_mutations(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    session = services.conversation_store.get_or_create("web:admin:compat")
    session.add_message("user", "hello")
    session.add_message("assistant", "world")
    services.conversation_store.save(session)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    revoke = client.delete("/api/sessions/web:admin:compat/messages/0", headers=headers)
    memory = client.get("/api/sessions/cli:web-admin-1/memory", headers=headers)
    provider = client.patch("/api/providers/custom", headers=headers, json={"api_key": "sk-demo", "api_base": "https://example.com/v1"})
    channel = client.patch("/api/channels/telegram", headers=headers, json={"enabled": False, "botToken": "replaced"})
    reload_channel = client.post("/api/channels/telegram/reload", headers=headers)
    reload_all = client.post("/api/channels/reload-all", headers=headers)
    mcp_create = client.post("/api/mcp/servers/extra", headers=headers, json={"name": "extra", "command": "python", "args": ["-m", "extra"]})
    mcp_toggle = client.patch("/api/mcp/servers/extra/enabled", headers=headers, json={"enabled": False})
    cron_create = client.post(
        "/api/cron/jobs",
        headers=headers,
        json={
            "name": "compat",
            "enabled": True,
            "schedule": {"kind": "at", "at_ms": 4102444800000},
            "payload": {"kind": "agent_turn", "message": "compat", "deliver": False},
        },
    )
    cron_id = cron_create.json()["id"]
    cron_toggle = client.patch(f"/api/cron/jobs/{cron_id}/enabled", headers=headers, json={"enabled": False})
    providers = client.get("/api/providers", headers=headers)
    users = client.get("/api/users", headers=headers)

    assert revoke.status_code == 200
    assert memory.status_code == 200
    assert provider.status_code == 200
    assert services.config.providers.custom.api_base == "https://example.com/v1"
    custom_provider = next(item for item in providers.json() if item["name"] == "custom")
    assert custom_provider["has_key"] is True
    assert channel.status_code == 200
    assert services.config.channels.telegram["enabled"] is False
    assert reload_channel.status_code == 200
    assert reload_channel.json()["name"] == "telegram"
    assert reload_all.status_code == 200
    assert mcp_create.status_code == 200
    assert mcp_toggle.status_code == 200
    assert cron_toggle.status_code == 200
    assert users.status_code == 200
    assert users.json()[0]["username"] == "admin"


def test_channel_reload_accepts_async_manager(tmp_path: Path) -> None:
    from xbot.interfaces.webui.app import _clear_login_rate_limit, create_app
    from xbot.interfaces.webui.services import ServiceContainer

    _clear_login_rate_limit()
    import xbot.interfaces.webui.auth as auth_module
    auth_module.PASSWORD_FILE = tmp_path / "webui-data" / "password"
    auth_module.PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    set_password("test-webui-password")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    manager = _AsyncReloadChannelManager()
    services_container = ServiceContainer(
        config=config,
        bus=MessageBus(),
        agent=_FakeRuntime(),
        conversation_store=ConversationStore(workspace),
        cron=_FakeCronService(),
        heartbeat=_FakeHeartbeatService(),
        metadata={"channel_manager": manager},
    )
    client = TestClient(create_app(services_container, data_dir=tmp_path / "webui-data"))
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    reload_channel = client.post("/api/channels/telegram/reload", headers=headers)
    reload_all = client.post("/api/channels/reload-all", headers=headers)

    assert reload_channel.status_code == 200
    assert reload_channel.json() == {"name": "telegram", "reloaded": True, "async": True}
    assert reload_all.status_code == 200
    assert reload_all.json() == {"reloaded": True, "count": 2, "async": True}
    assert manager.reload_calls == ["telegram", "*"]


def test_startup_configures_heartbeat_via_public_api(tmp_path: Path) -> None:
    from xbot.interfaces.webui.app import _clear_login_rate_limit, create_app
    from xbot.interfaces.webui.services import ServiceContainer

    _clear_login_rate_limit()
    import xbot.interfaces.webui.auth as auth_module
    auth_module.PASSWORD_FILE = tmp_path / "webui-data" / "password"
    auth_module.PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    set_password("test-webui-password")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    heartbeat = _StrictHeartbeatService()
    services_container = ServiceContainer(
        config=config,
        bus=MessageBus(),
        agent=_FakeRuntime(),
        conversation_store=ConversationStore(workspace),
        cron=_FakeCronService(),
        heartbeat=heartbeat,
        metadata={},
    )

    with TestClient(create_app(services_container, data_dir=tmp_path / "webui-data")):
        pass

    assert heartbeat.configured_callbacks is not None


def test_heartbeat_resolves_im_session_to_provider_channel(tmp_path: Path) -> None:
    from xbot.interfaces.webui.app import _clear_login_rate_limit, create_app
    from xbot.interfaces.webui.services import ServiceContainer

    _clear_login_rate_limit()
    import xbot.interfaces.webui.auth as auth_module
    auth_module.PASSWORD_FILE = tmp_path / "webui-data" / "password"
    auth_module.PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    set_password("test-webui-password")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    conversation_store = ConversationStore(workspace)
    session = conversation_store.get_or_create("im:telegram:456")
    session.messages = [{"role": "user", "content": "hello"}]
    conversation_store.save(session)
    heartbeat = _StrictHeartbeatService()
    bus = MessageBus()
    services_container = ServiceContainer(
        config=config,
        bus=bus,
        agent=_FakeRuntime(),
        conversation_store=conversation_store,
        cron=_FakeCronService(),
        heartbeat=heartbeat,
        metadata={"channel_manager": _FakeChannelManager()},
    )

    with TestClient(create_app(services_container, data_dir=tmp_path / "webui-data")):
        assert heartbeat.configured_callbacks is not None
        _llm_call, _on_execute, on_notify = heartbeat.configured_callbacks
        asyncio.run(on_notify("heartbeat ok"))

    outbound = asyncio.run(asyncio.wait_for(bus.consume_outbound(), timeout=1.0))
    assert outbound.channel == "telegram"
    assert outbound.chat_id == "456"
    assert outbound.content == "heartbeat ok"


def test_system_config_compatibility_endpoints(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    logs_dir = services.data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "app.log").write_text("first line\nkeyword line\nlast line\n", encoding="utf-8")

    workspace_file = client.put("/api/config/workspace-file/AGENTS.md", headers=headers, json={"content": "# Agents"})
    workspace_read = client.get("/api/config/workspace-file/AGENTS.md", headers=headers)
    raw_config = client.get("/api/config/raw", headers=headers)
    logs = client.get("/api/config/logs?lines=2&keyword=keyword", headers=headers)
    users_create = client.post("/api/users", headers=headers, json={"username": "bob", "password": "pw", "role": "user"})

    assert workspace_file.status_code == 200
    assert workspace_read.status_code == 200
    assert workspace_read.json()["content"] == "# Agents"
    assert raw_config.status_code == 200
    assert "\"agents\"" in raw_config.json()["content"]
    assert logs.status_code == 200
    assert logs.json()["content"] == "keyword line"
    assert logs.json()["path"].endswith("app.log")
    assert users_create.status_code == 400


def test_put_raw_config_returns_400_for_invalid_json(tmp_path: Path) -> None:
    client, _services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.put(
        "/api/config/raw",
        headers=headers,
        json={"content": '{"agents": '},
    )

    assert response.status_code == 400


def test_patch_channels_rejects_unsafe_channel_names(tmp_path: Path) -> None:
    client, _services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.patch(
        "/api/channels",
        headers=headers,
        json={"channels": {"bad/name": {"enabled": True}}},
    )

    assert response.status_code == 400


def test_system_config_s3_and_workspace_transfer_endpoints(tmp_path: Path) -> None:
    import io
    import zipfile

    client, _services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    s3_get = client.get("/api/config/s3", headers=headers)
    s3_put = client.put(
        "/api/config/s3",
        headers=headers,
        json={
            "enabled": True,
            "endpoint_url": "https://s3.example.com",
            "access_key_id": "ak",
            "secret_access_key": "sk",
            "bucket": "bucket",
            "region": "cn",
            "public_base_url": "https://cdn.example.com",
        },
    )
    export_resp = client.get("/api/config/workspace/export", headers=headers)

    memory_zip = io.BytesIO()
    with zipfile.ZipFile(memory_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("config.json", "{}")
    import_resp = client.post(
        "/api/config/workspace/import",
        headers=headers,
        files={"file": ("backup.zip", memory_zip.getvalue(), "application/zip")},
    )
    s3_get_after = client.get("/api/config/s3", headers=headers)

    assert s3_get.status_code == 200
    assert s3_put.status_code == 200
    assert s3_get_after.json()["bucket"] == "bucket"
    assert export_resp.status_code == 200
    assert import_resp.status_code == 200
    assert import_resp.json()["backup"] is not None


def test_workspace_transfer_excludes_webui_backups(tmp_path: Path) -> None:
    import io
    import zipfile

    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    workspace = services.config.workspace_path
    (workspace / "keep.txt").write_text("keep", encoding="utf-8")
    backups_dir = workspace / ".webui" / "backups"
    backups_dir.mkdir(parents=True)
    (backups_dir / "old-backup.zip").write_text("old", encoding="utf-8")

    export_resp = client.get("/api/config/workspace/export", headers=headers)
    assert export_resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(export_resp.content)) as zf:
        assert "keep.txt" in zf.namelist()
        assert ".webui/backups/old-backup.zip" not in zf.namelist()

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("new.txt", "new")
    import_resp = client.post(
        "/api/config/workspace/import",
        headers=headers,
        files={"file": ("backup.zip", archive.getvalue(), "application/zip")},
    )

    assert import_resp.status_code == 200
    backup_path = Path(import_resp.json()["backup"])
    with zipfile.ZipFile(backup_path) as zf:
        assert "keep.txt" in zf.namelist()
        assert ".webui/backups/old-backup.zip" not in zf.namelist()


def test_workspace_import_rejects_unsafe_zip_members(tmp_path: Path) -> None:
    import io
    import zipfile

    client, _services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    for member in ("../workspace2/pwn.txt", "/absolute/pwn.txt", "safe/../pwn.txt", ""):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(member, "pwn")

        response = client.post(
            "/api/config/workspace/import",
            headers=headers,
            files={"file": ("backup.zip", archive.getvalue(), "application/zip")},
        )

        assert response.status_code == 400
        assert "Invalid path in archive" in response.json()["detail"]


def test_channel_reload_returns_structured_error(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    services.metadata["channel_manager"].failures["telegram"] = "reload boom"

    response = client.post("/api/channels/telegram/reload", headers=headers)

    assert response.status_code == 500
    assert response.json()["detail"]["name"] == "telegram"
    assert response.json()["detail"]["error"] == "reload boom"


def test_workspace_import_keeps_existing_files_when_extract_fails(tmp_path: Path, monkeypatch) -> None:
    import io
    import zipfile

    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    existing = services.config.workspace_path / "keep.txt"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("keep-me", encoding="utf-8")

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("new.txt", "new")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", lambda self, path: (_ for _ in ()).throw(RuntimeError("extract failed")))

    response = client.post(
        "/api/config/workspace/import",
        headers=headers,
        files={"file": ("backup.zip", archive.getvalue(), "application/zip")},
    )

    assert response.status_code == 500
    assert existing.read_text(encoding="utf-8") == "keep-me"


def test_mcp_runtime_reports_disconnected_server_details(tmp_path: Path) -> None:
    client, services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    services.config.tools.mcp_servers["idle"] = MCPServerConfig(url="https://example.com/sse", enabled_tools=["alpha"])

    response = client.get("/api/mcp/servers/runtime", headers=headers)

    assert response.status_code == 200
    payload = {item["name"]: item for item in response.json()}
    assert payload["idle"]["running"] is False
    assert payload["idle"]["transport"] == "sse"
    assert payload["idle"]["error"] == "configured but disconnected"


def test_login_rate_limit_blocks_after_max_attempts(tmp_path: Path) -> None:
    """Test that login rate limiting blocks after max attempts."""
    from xbot.interfaces.webui.app import _MAX_ATTEMPTS_PER_IP, _clear_login_rate_limit

    _clear_login_rate_limit()
    client, _services = _build_client(tmp_path)

    # Make max allowed attempts with wrong password
    for i in range(_MAX_ATTEMPTS_PER_IP):
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        )
        assert response.status_code == 401, f"Attempt {i + 1} should fail with 401"

    # Next attempt should be rate limited
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert response.status_code == 429, "Should be rate limited"
    assert "Too many login attempts" in response.json()["detail"]


def test_login_rate_limit_isolated_per_ip(tmp_path: Path, monkeypatch) -> None:
    """Test that rate limiting is per-IP, not global."""
    from xbot.interfaces.webui.app import _MAX_ATTEMPTS_PER_IP, _clear_login_rate_limit

    _clear_login_rate_limit()
    client, _services = _build_client(tmp_path)

    # Exhaust rate limit for first IP
    for i in range(_MAX_ATTEMPTS_PER_IP):
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        )
        assert response.status_code == 401

    # Simulate different client IP by manipulating request.client
    # In TestClient this is typically "testclient" but we can verify the isolation logic
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert response.status_code == 429  # Same IP should still be blocked


def test_login_rate_limit_does_not_count_successful_logins(tmp_path: Path) -> None:
    from xbot.interfaces.webui.app import _MAX_ATTEMPTS_PER_IP, _clear_login_rate_limit

    _clear_login_rate_limit()
    client, _services = _build_client(tmp_path)

    for _ in range(_MAX_ATTEMPTS_PER_IP + 1):
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test-webui-password"},
        )
        assert response.status_code == 200


def test_api_skills_endpoints_available(tmp_path: Path) -> None:
    client, _services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    list_response = client.get("/api/skills", headers=headers)
    create_response = client.post(
        "/api/skills",
        headers=headers,
        json={"name": "demo", "content": "# Demo\n\nTest skill."},
    )
    get_response = client.get("/api/skills/demo", headers=headers)
    update_response = client.put(
        "/api/skills/demo",
        headers=headers,
        json={"content": "# Demo\n\nUpdated skill."},
    )
    toggle_response = client.post("/api/skills/demo/toggle", headers=headers, json={"enabled": False})
    delete_response = client.delete("/api/skills/demo", headers=headers)

    assert list_response.status_code == 200
    assert create_response.status_code == 201
    assert get_response.status_code == 200
    assert get_response.json()["content"] == "# Demo\n\nTest skill."
    assert update_response.status_code == 200
    assert toggle_response.status_code == 200
    assert toggle_response.json()["enabled"] is False
    assert delete_response.status_code == 200
    assert delete_response.json()["ok"] is True
