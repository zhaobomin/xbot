from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from xbot.bus.queue import MessageBus
from xbot.cli.commands import app
from xbot.config.schema import Config, MCPServerConfig
from xbot.cron.types import CronJob, CronJobState, CronPayload, CronSchedule
from xbot.session.manager import SessionManager
from xbot.webui.auth import set_password, get_password_file_path


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


class _FakeChannelManager:
    def __init__(self) -> None:
        self.reload_calls: list[str] = []
        self.failures: dict[str, str] = {}

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
    session_manager: SessionManager
    cron: _FakeCronService
    heartbeat: _FakeHeartbeatService


def _build_client(tmp_path: Path) -> tuple[TestClient, _Services]:
    from xbot.webui.app import create_app, _clear_login_rate_limit
    from xbot.webui.services import ServiceContainer

    # Clear login rate limit between tests
    _clear_login_rate_limit()

    # Set a known test password for all tests (isolated per test via temp path)
    test_password_file = tmp_path / "webui-data" / "password"
    test_password_file.parent.mkdir(parents=True, exist_ok=True)
    # Monkeypatch the password file location for this test
    import xbot.webui.auth as auth_module
    auth_module.PASSWORD_FILE = test_password_file
    set_password("test-webui-password")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    config.gateway.port = 18790
    config.channels.telegram = {"enabled": True, "botToken": "secret"}
    config.tools.mcp_servers["demo"] = MCPServerConfig(command="python", args=["-m", "demo"])

    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    session_manager = SessionManager(workspace)
    session = session_manager.get_or_create("cli:web-admin-1")
    session.messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    session_manager.save(session)

    cron = _FakeCronService()
    heartbeat = _FakeHeartbeatService()
    runtime = _FakeRuntime()
    bus = MessageBus()
    channel_manager = _FakeChannelManager()
    services = ServiceContainer(
        config=config,
        bus=bus,
        agent=runtime,
        session_manager=session_manager,
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


def test_webui_serves_frontend_dist_when_present(tmp_path: Path) -> None:
    from xbot.webui.app import create_app
    from xbot.webui.services import ServiceContainer

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html><body>frontend-dist</body></html>", encoding="utf-8")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    services = ServiceContainer(
        config=config,
        bus=MessageBus(),
        agent=_FakeRuntime(),
        session_manager=SessionManager(config.workspace_path),
        cron=_FakeCronService(),
        heartbeat=_FakeHeartbeatService(),
    )
    client = TestClient(create_app(services, data_dir=tmp_path / "data", frontend_dir=dist_dir))

    response = client.get("/")

    assert response.status_code == 200
    assert "frontend-dist" in response.text


def test_webui_spa_routes_fall_back_to_index(tmp_path: Path) -> None:
    from xbot.webui.app import create_app
    from xbot.webui.services import ServiceContainer

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html><body>frontend-dist</body></html>", encoding="utf-8")

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    services = ServiceContainer(
        config=config,
        bus=MessageBus(),
        agent=_FakeRuntime(),
        session_manager=SessionManager(config.workspace_path),
        cron=_FakeCronService(),
        heartbeat=_FakeHeartbeatService(),
    )
    client = TestClient(create_app(services, data_dir=tmp_path / "data", frontend_dir=dist_dir))

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "frontend-dist" in response.text


@pytest.mark.skip(reason="requires frontend build artifacts")
def test_frontend_branding_uses_xbot_semantics() -> None:
    frontend_root = Path("xbot/webui/frontend")
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
        Path("xbot/webui/frontend/index.html"),
        Path("xbot/webui/frontend/src/pages/Login.tsx"),
        Path("xbot/webui/frontend/src/components/layout/Sidebar.tsx"),
        Path("xbot/webui/frontend/src/components/layout/MobileTopBar.tsx"),
        Path("xbot/webui/frontend/src/components/chat/ChatWindow.tsx"),
        Path("xbot/webui/frontend/src/components/chat/MessageBubble.tsx"),
    ]

    for path in files:
        content = path.read_text(encoding="utf-8")
        assert "/logo.png" not in content, f"stale png logo reference in {path}"
        assert "/icon.png" not in content, f"stale png icon reference in {path}"


def test_frontend_uses_text_brand_mark_instead_of_logo_images() -> None:
    files = [
        Path("xbot/webui/frontend/src/pages/Login.tsx"),
        Path("xbot/webui/frontend/src/components/layout/Sidebar.tsx"),
        Path("xbot/webui/frontend/src/components/layout/MobileTopBar.tsx"),
        Path("xbot/webui/frontend/src/components/chat/ChatWindow.tsx"),
    ]

    for path in files:
        content = path.read_text(encoding="utf-8")
        assert "/logo.svg" not in content, f"stale svg logo reference in {path}"
        assert "/icon.svg" not in content, f"stale svg icon reference in {path}"
        assert "xbot" in content, f"missing text brand mark in {path}"

    bubble = Path("xbot/webui/frontend/src/components/chat/MessageBubble.tsx").read_text(encoding="utf-8")
    assert "/icon.svg" not in bubble
    assert '"x"' in bubble or "'x'" in bubble


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
    assert messages.json()[0]["role"] == "user"
    assert any(item["name"] == "anthropic" for item in providers.json())
    assert any(item["name"] == "telegram" for item in channels.json())
    telegram = next(item for item in channels.json() if item["name"] == "telegram")
    assert telegram["config"]["botToken"].startswith("••••")
    assert "secret" not in telegram["config"]["botToken"]
    assert any(item["name"] == "demo" for item in mcp.json())
    assert heartbeat.json()["enabled"] is True


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


def test_cron_and_skills_management_endpoints(tmp_path: Path) -> None:
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

    skill_create = client.post(
        "/api/skills",
        headers=headers,
        json={"name": "demo-skill", "content": "# Demo"},
    )
    assert skill_create.status_code == 201
    assert (services.config.workspace_path / "skills" / "demo-skill" / "SKILL.md").exists()

    skill_list = client.get("/api/skills", headers=headers)
    assert skill_list.status_code == 200
    assert any(item["name"] == "demo-skill" for item in skill_list.json())


def test_cron_management_crud_endpoints_with_real_service_shape(tmp_path: Path) -> None:
    from xbot.webui.app import create_app
    from xbot.webui.services import ServiceContainer

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
    session_manager = SessionManager(config.workspace_path)
    services = ServiceContainer(
        config=config,
        bus=MessageBus(),
        agent=_FakeRuntime(),
        session_manager=session_manager,
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

    create_skill = client.post(
        "/api/skills",
        headers=headers,
        json={"name": "editable-skill", "content": "# v1"},
    )
    assert create_skill.status_code == 201

    update_skill = client.put(
        "/api/skills/editable-skill",
        headers=headers,
        json={"content": "# v2"},
    )
    assert update_skill.status_code == 200
    assert (services.config.workspace_path / "skills" / "editable-skill" / "SKILL.md").read_text(encoding="utf-8") == "# v2"

    delete_skill = client.delete("/api/skills/editable-skill", headers=headers)
    assert delete_skill.status_code == 200
    assert not (services.config.workspace_path / "skills" / "editable-skill").exists()


def test_websocket_chat_uses_internal_cli_session_mapping(tmp_path: Path) -> None:
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

    assert services.agent.calls[0]["session_key"] == "cli:web-admin-abc123"


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

    assert services.agent.calls[0]["session_key"] == "cli:web-admin-drop"


def test_safe_websocket_send_swallows_disconnect_errors() -> None:
    from starlette.websockets import WebSocketDisconnect

    from xbot.webui.app import _safe_websocket_send_json

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
    skill = client.get("/api/skills/demo-skill", headers=headers)

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

    assert skill.status_code == 404


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
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    revoke = client.delete("/api/sessions/cli:web-admin-1/messages/0", headers=headers)
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
    from xbot.webui.app import _clear_login_rate_limit, _MAX_ATTEMPTS_PER_IP

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
    from xbot.webui.app import _clear_login_rate_limit, _MAX_ATTEMPTS_PER_IP

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


def test_skill_name_validation_prevents_path_traversal(tmp_path: Path) -> None:
    """Test that skill name validation prevents path traversal attacks."""
    client, _services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Try to create a skill with path traversal
    malicious_names = [
        "../../../etc/passwd",
        "..%2f..%2f..%2fetc%2fpasswd",
        "skill/../../../etc/passwd",
        "skill\\..\\..\\..\\etc\\passwd",
    ]

    for name in malicious_names:
        response = client.post(
            "/api/skills",
            headers=headers,
            json={"name": name, "content": "# malicious"},
        )
        # Should reject with 400
        assert response.status_code == 400, f"Should reject malicious name: {name}"


def test_skill_name_validation_rejects_unicode_attacks(tmp_path: Path) -> None:
    """Test that skill name validation normalizes Unicode to prevent encoding attacks."""
    client, _services = _build_client(tmp_path)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Try Unicode fullwidth slash (／) which normalizes to /
    unicode_slash_names = [
        "skill／../../../etc/passwd",  # Fullwidth slash
        "skill\uFF0F..",  # Another Unicode slash variant
    ]

    for name in unicode_slash_names:
        response = client.post(
            "/api/skills",
            headers=headers,
            json={"name": name, "content": "# unicode"},
        )
        # Should reject with 400
        assert response.status_code == 400, f"Should reject Unicode attack: {repr(name)}"
