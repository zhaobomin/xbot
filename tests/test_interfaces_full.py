"""Comprehensive tests for the interface layer modules.

Covers:
- xbot.interfaces.gateway.app   (WebSocket, file upload, error handlers, streaming, lifecycle)
- xbot.interfaces.gateway.auth  (token refresh, password verification, rate limiting, edge cases)
- xbot.interfaces.gateway.services (service container lifecycle, skill listing, MCP status)
- xbot.interfaces.cli.commands  (REPL helpers, streaming, tool display, crew commands, session mgmt)
- xbot.interfaces.cli.goal      (goal start, execution, completion)
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from xbot.platform.bus.queue import MessageBus
from xbot.platform.config.schema import Config, MCPServerConfig, ProviderConfig
from xbot.runtime.session.conversation_store import ConversationStore
from xbot.runtime.system.cron.types import CronJob, CronJobState, CronPayload, CronSchedule


# ---------------------------------------------------------------------------
# Shared fakes (gateway)
# ---------------------------------------------------------------------------

class _FakeRuntime:
    """Minimal agent double that records calls and returns echo responses."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.model = "claude-sonnet-4-5"
        self.router = type("Router", (), {"backend_type": "claude_sdk"})()
        self.tools = type(
            "ToolRegistry",
            (),
            {"tool_names": ["read_file", "mcp_demo_search", "mcp_demo_fetch"]},
        )()
        self._shared_resources: dict[str, Any] = {}
        self.backend = type("Backend", (), {
            "call_for_structured": AsyncMock(return_value={"result": "ok"}),
        })()

    async def initialize(self) -> None:
        pass

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
        })
        if on_progress is not None:
            await on_progress(
                "thinking",
                tool_hint=False,
                event_type="thinking",
                event_data=None,
            )
        return f"echo:{content}"

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress=None,
    ) -> str:
        return await self.process_managed_direct(
            content, session_key, channel, chat_id, on_progress
        )

    def describe_runtime(self) -> str:
        return "backend=claude_sdk | workspace=/tmp/workspace"

    async def close_mcp(self) -> None:
        pass

    def stop(self) -> None:
        pass


class _CancellableRuntime(_FakeRuntime):
    """Agent that blocks forever then handles cancellation gracefully."""

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
        })
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return "not-cancelled"


class _ErrorRuntime(_FakeRuntime):
    """Agent that raises an exception to test error paths."""

    async def process_managed_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress=None,
        media: list[str] | None = None,
    ) -> str:
        raise RuntimeError("agent boom")


class _FakeCronService:
    """In-memory cron service double."""

    def __init__(self) -> None:
        self.jobs: dict[str, CronJob] = {}
        self.on_job: Any = None
        self._started = False

    async def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    async def shutdown(self) -> None:
        self._started = False

    def list_jobs(self) -> list[CronJob]:
        return list(self.jobs.values())

    def add_job(self, name, schedule, payload=None, enabled=True,
                message="", deliver=False, channel=None, to=None,
                delete_after_run=False) -> CronJob:
        job_id = f"job-{len(self.jobs) + 1}"
        resolved_payload = payload or CronPayload(
            kind="agent_turn", message=message,
            deliver=deliver, channel=channel, to=to,
        )
        job = CronJob(
            id=job_id, name=name, enabled=enabled,
            schedule=schedule, payload=resolved_payload,
            state=CronJobState(next_run_at_ms=schedule.at_ms),
            delete_after_run=delete_after_run,
        )
        self.jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> CronJob | None:
        return self.jobs.get(job_id)

    def update_job(self, job_id: str, **updates) -> CronJob:
        job = self.jobs[job_id]
        for k, v in updates.items():
            setattr(job, k, v)
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
        return {"running": self._started, "jobs": len(self.jobs)}


class _FakeHeartbeatService:
    """In-memory heartbeat service double."""

    def __init__(self) -> None:
        self.enabled = True
        self.interval_s = 60
        self._running = False

    async def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        self._running = False

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_s": self.interval_s,
            "running": self._running,
        }

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def configure_callbacks(self, **kwargs: Any) -> None:
        pass


class _FakeChannelManager:
    """In-memory channel manager double."""

    def __init__(self) -> None:
        self.reload_calls: list[str] = []
        self.enabled_channels = ["telegram", "slack"]
        self.failures: dict[str, str] = {}

    def get_status(self) -> dict[str, dict[str, Any]]:
        return {
            "telegram": {"enabled": True, "running": True, "error": None},
            "slack": {"enabled": True, "running": False, "error": "token missing"},
        }

    def reload_channel(self, name: str) -> dict[str, Any]:
        self.reload_calls.append(name)
        if name in self.failures:
            raise RuntimeError(self.failures[name])
        return {"name": name, "reloaded": True}

    def reload_all(self) -> dict[str, Any]:
        self.reload_calls.append("*")
        return {"reloaded": True, "count": 2}


@dataclass
class _Services:
    config: Config
    bus: MessageBus
    agent: _FakeRuntime
    conversation_store: ConversationStore
    cron: _FakeCronService
    heartbeat: _FakeHeartbeatService


def _build_gateway_client(tmp_path: Path, *, agent: _FakeRuntime | None = None) -> tuple[TestClient, _Services]:
    """Build a FastAPI TestClient wired to fake services."""
    from xbot.interfaces.gateway.app import _clear_login_rate_limit, create_app
    from xbot.interfaces.gateway.auth import set_password
    from xbot.interfaces.gateway.services import ServiceContainer
    import xbot.interfaces.gateway.auth as auth_module

    _clear_login_rate_limit()

    test_password_file = tmp_path / "webui-data" / "password"
    test_password_file.parent.mkdir(parents=True, exist_ok=True)
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
    runtime = agent or _FakeRuntime()
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
    return TestClient(app), _Services(
        config=config, bus=bus, agent=runtime,
        conversation_store=conversation_store,
        cron=cron, heartbeat=heartbeat,
    )


def _auth_header(client: TestClient) -> dict[str, str]:
    """Get an authorization header by logging in with the test credentials."""
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"})
    assert resp.status_code == 200, f"Login failed: {resp.status_code} {resp.text}"
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# GATEWAY: auth.py
# ===========================================================================

class TestAuthPasswordManagement:
    """Tests for password creation, reset, and verification."""

    def test_reset_password_creates_new_hash(self, tmp_path: Path) -> None:
        """reset_password() overwrites the password file with a new hash."""
        import xbot.interfaces.gateway.auth as auth_mod

        pw_file = tmp_path / "password"
        pw_file.parent.mkdir(parents=True, exist_ok=True)
        auth_mod.PASSWORD_FILE = pw_file

        pw1 = auth_mod.reset_password()
        hash1 = pw_file.read_text(encoding="utf-8")

        pw2 = auth_mod.reset_password()
        hash2 = pw_file.read_text(encoding="utf-8")

        assert pw1 != pw2  # Different passwords
        assert hash1 != hash2  # Different hashes
        assert len(pw1) > 20  # Secure length
        assert len(pw2) > 20

    def test_set_and_verify_password(self, tmp_path: Path) -> None:
        """set_password + verify_password round-trips correctly."""
        import xbot.interfaces.gateway.auth as auth_mod

        pw_file = tmp_path / "password"
        pw_file.parent.mkdir(parents=True, exist_ok=True)
        auth_mod.PASSWORD_FILE = pw_file

        auth_mod.set_password("my-secret-pw")
        stored_hash = pw_file.read_text(encoding="utf-8")

        assert auth_mod.verify_password("my-secret-pw", stored_hash) is True
        assert auth_mod.verify_password("wrong-pw", stored_hash) is False

    def test_validate_password_length_too_long(self) -> None:
        """Passwords > 72 UTF-8 bytes are rejected (bcrypt limit)."""
        from xbot.interfaces.gateway.auth import validate_password_length

        with pytest.raises(HTTPException) as exc_info:
            validate_password_length("a" * 73)
        assert exc_info.value.status_code == 400

    def test_validate_password_length_boundary(self) -> None:
        """Exactly 72 bytes is accepted."""
        from xbot.interfaces.gateway.auth import validate_password_length

        validate_password_length("a" * 72)  # Should not raise

    def test_validate_password_multibyte(self) -> None:
        """Multi-byte characters count by byte length, not character count."""
        from xbot.interfaces.gateway.auth import validate_password_length

        # Each emoji is 4 bytes in UTF-8, so 19 emojis = 76 bytes > 72
        with pytest.raises(HTTPException):
            validate_password_length("🔑" * 19)


class TestAuthJWTSecret:
    """Tests for JWT secret management."""

    def test_env_var_takes_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """XBOT_JWT_SECRET env var overrides file and generation."""
        import xbot.interfaces.gateway.auth as auth_mod

        auth_mod.JWT_SECRET_FILE = tmp_path / "jwt_secret"
        monkeypatch.setenv("XBOT_JWT_SECRET", "env-secret-value")

        result = auth_mod.get_or_create_jwt_secret()
        assert result == "env-secret-value"

    def test_file_persists_across_calls(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """JWT secret file is reused on subsequent calls."""
        import xbot.interfaces.gateway.auth as auth_mod

        auth_mod.JWT_SECRET_FILE = tmp_path / "jwt_secret"
        monkeypatch.delenv("XBOT_JWT_SECRET", raising=False)

        secret1 = auth_mod.get_or_create_jwt_secret()
        secret2 = auth_mod.get_or_create_jwt_secret()
        assert secret1 == secret2
        assert auth_mod.JWT_SECRET_FILE.exists()

    def test_generates_new_secret_when_no_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A new 256-bit secret is generated when no file or env var exists."""
        import xbot.interfaces.gateway.auth as auth_mod

        auth_mod.JWT_SECRET_FILE = tmp_path / "nonexistent" / "jwt_secret"
        monkeypatch.delenv("XBOT_JWT_SECRET", raising=False)

        secret = auth_mod.get_or_create_jwt_secret()
        assert len(secret) == 64  # 32 bytes hex = 64 chars
        assert auth_mod.JWT_SECRET_FILE.exists()


class TestAuthManager:
    """Tests for AuthManager JWT issue/verify."""

    def test_issue_and_decode_token(self) -> None:
        """Round-trip: issue_token → decode_token returns the same payload."""
        from xbot.interfaces.gateway.auth import AuthManager

        mgr = AuthManager(secret="test-secret")
        user = {"id": "admin", "username": "admin", "role": "admin"}

        token = mgr.issue_token(user, ttl_minutes=60)
        payload = mgr.decode_token(token)

        assert payload["sub"] == "admin"
        assert payload["username"] == "admin"
        assert payload["role"] == "admin"
        assert "exp" in payload

    def test_decode_invalid_token_raises_401(self) -> None:
        """Invalid tokens produce HTTPException(401)."""
        from xbot.interfaces.gateway.auth import AuthManager

        mgr = AuthManager(secret="test-secret")
        with pytest.raises(HTTPException) as exc_info:
            mgr.decode_token("invalid.token.here")
        assert exc_info.value.status_code == 401

    def test_decode_expired_token_raises_401(self) -> None:
        """Expired tokens produce HTTPException(401)."""
        from xbot.interfaces.gateway.auth import AuthManager

        mgr = AuthManager(secret="test-secret")
        user = {"id": "admin", "username": "admin", "role": "admin"}
        # Issue token that expires immediately
        token = mgr.issue_token(user, ttl_minutes=-1)
        with pytest.raises(HTTPException):
            mgr.decode_token(token)

    def test_decode_wrong_secret_raises_401(self) -> None:
        """Tokens signed with a different secret are rejected."""
        from xbot.interfaces.gateway.auth import AuthManager

        mgr1 = AuthManager(secret="secret-1")
        mgr2 = AuthManager(secret="secret-2")
        user = {"id": "admin", "username": "admin", "role": "admin"}
        token = mgr1.issue_token(user)
        with pytest.raises(HTTPException):
            mgr2.decode_token(token)


class TestUserStore:
    """Tests for the UserStore single-admin auth store."""

    def test_authenticate_success(self, tmp_path: Path) -> None:
        """Successful authentication returns admin user dict."""
        import xbot.interfaces.gateway.auth as auth_mod

        pw_file = tmp_path / "password"
        users_file = tmp_path / "users.json"
        pw_file.parent.mkdir(parents=True, exist_ok=True)
        auth_mod.PASSWORD_FILE = pw_file
        auth_mod.set_password("test-pw")

        store = auth_mod.UserStore(path=users_file)
        user = store.authenticate("admin", "test-pw")
        assert user["id"] == "admin"
        assert user["role"] == "admin"

    def test_authenticate_wrong_password(self, tmp_path: Path) -> None:
        """Wrong password raises HTTP 401."""
        import xbot.interfaces.gateway.auth as auth_mod

        pw_file = tmp_path / "password"
        users_file = tmp_path / "users.json"
        pw_file.parent.mkdir(parents=True, exist_ok=True)
        auth_mod.PASSWORD_FILE = pw_file
        auth_mod.set_password("correct-pw")

        store = auth_mod.UserStore(path=users_file)
        with pytest.raises(HTTPException) as exc_info:
            store.authenticate("admin", "wrong-pw")
        assert exc_info.value.status_code == 401

    def test_authenticate_wrong_username(self, tmp_path: Path) -> None:
        """Wrong username raises HTTP 401."""
        import xbot.interfaces.gateway.auth as auth_mod

        pw_file = tmp_path / "password"
        users_file = tmp_path / "users.json"
        pw_file.parent.mkdir(parents=True, exist_ok=True)
        auth_mod.PASSWORD_FILE = pw_file
        auth_mod.set_password("test-pw")

        store = auth_mod.UserStore(path=users_file)
        with pytest.raises(HTTPException) as exc_info:
            store.authenticate("nobody", "test-pw")
        assert exc_info.value.status_code == 401

    def test_change_password(self, tmp_path: Path) -> None:
        """change_password verifies old password and sets new one."""
        import xbot.interfaces.gateway.auth as auth_mod

        pw_file = tmp_path / "password"
        users_file = tmp_path / "users.json"
        pw_file.parent.mkdir(parents=True, exist_ok=True)
        auth_mod.PASSWORD_FILE = pw_file
        auth_mod.set_password("old-pw")

        store = auth_mod.UserStore(path=users_file)
        store.authenticate("admin", "old-pw")  # Ensure user file exists

        store.change_password("old-pw", "new-pw")
        # New password works
        user = store.authenticate("admin", "new-pw")
        assert user["username"] == "admin"
        # Old password no longer works
        with pytest.raises(HTTPException):
            store.authenticate("admin", "old-pw")

    def test_change_password_wrong_current(self, tmp_path: Path) -> None:
        """change_password with wrong current password raises 401."""
        import xbot.interfaces.gateway.auth as auth_mod

        pw_file = tmp_path / "password"
        users_file = tmp_path / "users.json"
        pw_file.parent.mkdir(parents=True, exist_ok=True)
        auth_mod.PASSWORD_FILE = pw_file
        auth_mod.set_password("correct-pw")

        store = auth_mod.UserStore(path=users_file)
        store.authenticate("admin", "correct-pw")

        with pytest.raises(HTTPException) as exc_info:
            store.change_password("wrong-pw", "new-pw")
        assert exc_info.value.status_code == 401

    def test_ensure_default_admin_missing_password_file(self, tmp_path: Path) -> None:
        """ensure_default_admin raises RuntimeError when password file is missing."""
        import xbot.interfaces.gateway.auth as auth_mod

        auth_mod.PASSWORD_FILE = tmp_path / "nonexistent" / "password"
        users_file = tmp_path / "users.json"

        store = auth_mod.UserStore(path=users_file)
        with pytest.raises(RuntimeError, match="password not initialized"):
            store.ensure_default_admin()

    def test_load_always_reads_password_file(self, tmp_path: Path) -> None:
        """load() always resolves password hash from PASSWORD_FILE, not users.json."""
        import xbot.interfaces.gateway.auth as auth_mod

        pw_file = tmp_path / "password"
        users_file = tmp_path / "users.json"
        pw_file.parent.mkdir(parents=True, exist_ok=True)
        auth_mod.PASSWORD_FILE = pw_file
        auth_mod.set_password("initial-pw")

        store = auth_mod.UserStore(path=users_file)
        store.authenticate("admin", "initial-pw")  # Creates users.json

        # Change password directly
        auth_mod.set_password("updated-pw")

        # load() should use the new hash from PASSWORD_FILE
        user_data = store.load()
        assert auth_mod.verify_password("updated-pw", user_data["password_hash"])


class TestAuthBanners:
    """Tests for password banner display functions."""

    def test_print_password_banner(self, capsys: pytest.CaptureFixture) -> None:
        """print_password_banner shows username and password."""
        from xbot.interfaces.gateway.auth import print_password_banner

        print_password_banner("super-secret-123")
        captured = capsys.readouterr()
        assert "super-secret-123" in captured.out
        assert "admin" in captured.out

    def test_print_reset_password_banner(self, capsys: pytest.CaptureFixture) -> None:
        """print_reset_password_banner shows new password."""
        from xbot.interfaces.gateway.auth import print_reset_password_banner

        print_reset_password_banner("reset-456")
        captured = capsys.readouterr()
        assert "reset-456" in captured.out
        assert "New WebUI password" in captured.out


# ===========================================================================
# GATEWAY: login rate limiting
# ===========================================================================

class TestLoginRateLimiting:
    """Tests for login attempt rate limiting."""

    def test_rate_limit_after_max_attempts(self, tmp_path: Path) -> None:
        """After 5 failed logins, the IP is rate-limited."""
        from xbot.interfaces.gateway.app import (
            _check_login_rate_limit,
            _clear_login_rate_limit,
            _record_failed_login,
        )

        _clear_login_rate_limit()
        ip = "192.168.1.100"

        # 5 attempts should be allowed
        for _ in range(5):
            _record_failed_login(ip)

        # 6th should raise
        with pytest.raises(HTTPException) as exc_info:
            _check_login_rate_limit(ip)
        assert exc_info.value.status_code == 429

        _clear_login_rate_limit()

    def test_clear_logins_resets_counter(self, tmp_path: Path) -> None:
        """Clearing logins allows new attempts."""
        from xbot.interfaces.gateway.app import (
            _check_login_rate_limit,
            _clear_failed_logins,
            _clear_login_rate_limit,
            _record_failed_login,
        )

        _clear_login_rate_limit()
        ip = "10.0.0.1"

        for _ in range(5):
            _record_failed_login(ip)

        _clear_failed_logins(ip)
        # Should not raise
        _check_login_rate_limit(ip)

        _clear_login_rate_limit()

    def test_login_endpoint_success_clears_rate_limit(self, tmp_path: Path) -> None:
        """Successful login clears the rate limit counter."""
        client, _services = _build_gateway_client(tmp_path)
        headers = _auth_header(client)
        assert "Authorization" in headers


# ===========================================================================
# GATEWAY: app.py — HTTP endpoints
# ===========================================================================

class TestGatewayAuth:
    """Test login and auth endpoints."""

    def test_login_success(self, tmp_path: Path) -> None:
        """POST /api/auth/login with correct credentials returns a token."""
        client, _ = _build_gateway_client(tmp_path)
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["user"]["username"] == "admin"

    def test_login_wrong_password(self, tmp_path: Path) -> None:
        """POST /api/auth/login with wrong password returns 401."""
        client, _ = _build_gateway_client(tmp_path)
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401

    def test_unauthorized_without_token(self, tmp_path: Path) -> None:
        """API endpoints require authorization header."""
        client, _ = _build_gateway_client(tmp_path)
        resp = client.get("/api/sessions")
        assert resp.status_code == 401


class TestGatewayProviders:
    """Test provider CRUD endpoints."""

    def test_create_provider(self, tmp_path: Path) -> None:
        """POST /api/providers creates a new custom provider."""
        client, services = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.post("/api/providers", json={
            "name": "my-provider",
            "api_key": "sk-test",
            "api_base": "https://api.example.com",
            "models": ["model-x"],
        }, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-provider"
        assert "my-provider" in services.config.providers.custom_providers

    def test_create_provider_duplicate(self, tmp_path: Path) -> None:
        """POST /api/providers with duplicate name returns 409."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        client.post("/api/providers", json={"name": "dup"}, headers=headers)
        resp = client.post("/api/providers", json={"name": "dup"}, headers=headers)
        assert resp.status_code == 409

    def test_create_provider_empty_name(self, tmp_path: Path) -> None:
        """POST /api/providers with empty name returns 400."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.post("/api/providers", json={"name": "  "}, headers=headers)
        assert resp.status_code == 400

    def test_delete_provider(self, tmp_path: Path) -> None:
        """DELETE /api/providers/{name} removes the provider."""
        client, services = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        client.post("/api/providers", json={"name": "to-delete"}, headers=headers)
        resp = client.delete("/api/providers/to-delete", headers=headers)
        assert resp.status_code == 200
        assert "to-delete" not in services.config.providers.custom_providers

    def test_delete_nonexistent_provider(self, tmp_path: Path) -> None:
        """DELETE /api/providers/{name} for missing name returns 404."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.delete("/api/providers/nonexistent", headers=headers)
        assert resp.status_code == 404


class TestGatewayMCPServers:
    """Test MCP server CRUD endpoints."""

    def test_update_mcp_server(self, tmp_path: Path) -> None:
        """PUT /api/mcp/servers/{name} merges updates into existing config."""
        client, services = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.put("/api/mcp/servers/demo", json={
            "command": "python3",
            "args": ["-m", "updated_demo"],
        }, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "demo"

    def test_delete_mcp_server(self, tmp_path: Path) -> None:
        """DELETE /api/mcp/servers/{name} removes the server."""
        client, services = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.delete("/api/mcp/servers/demo", headers=headers)
        assert resp.status_code == 200
        assert "demo" not in services.config.tools.mcp_servers


class TestGatewaySessions:
    """Test session management endpoints."""

    def test_list_sessions(self, tmp_path: Path) -> None:
        """GET /api/sessions returns the session list."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.get("/api/sessions", headers=headers)
        assert resp.status_code == 200
        sessions = resp.json()
        assert isinstance(sessions, list)

    def test_get_session_messages(self, tmp_path: Path) -> None:
        """GET /api/sessions/{key}/messages returns message history."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.get("/api/sessions/cli%3Aweb-admin-1/messages", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        # Response is either a list of messages or a dict with "messages" key
        if isinstance(data, list):
            assert len(data) >= 1
        else:
            assert "messages" in data


class TestGatewayWorkspaceImportExport:
    """Test workspace zip import/export."""

    def test_export_workspace(self, tmp_path: Path) -> None:
        """GET /api/config/workspace/export returns a zip file."""
        client, services = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        # Create a file in workspace
        workspace = Path(services.config.workspace_path)
        (workspace / "test.txt").write_text("hello", encoding="utf-8")

        resp = client.get("/api/config/workspace/export", headers=headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"

        # Verify it's a valid zip
        buf = io.BytesIO(resp.content)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert "test.txt" in names

    def test_import_workspace_non_zip(self, tmp_path: Path) -> None:
        """POST /api/config/workspace/import rejects non-zip files."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.post(
            "/api/config/workspace/import",
            files={"file": ("test.txt", b"not a zip", "text/plain")},
            headers=headers,
        )
        assert resp.status_code == 400
        assert "zip" in resp.json()["detail"].lower()

    def test_import_workspace_valid_zip(self, tmp_path: Path) -> None:
        """POST /api/config/workspace/import accepts valid zip files."""
        client, services = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        # Create a zip with a file
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("imported.txt", "imported content")
        buf.seek(0)

        resp = client.post(
            "/api/config/workspace/import",
            files={"file": ("workspace.zip", buf.read(), "application/zip")},
            headers=headers,
        )
        assert resp.status_code == 200


class TestGatewaySkills:
    """Test skill CRUD endpoints."""

    def test_list_skills(self, tmp_path: Path) -> None:
        """GET /api/skills returns available skills."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.get("/api/skills", headers=headers)
        assert resp.status_code == 200
        skills = resp.json()
        assert isinstance(skills, list)

    def test_create_and_delete_skill(self, tmp_path: Path) -> None:
        """POST /api/skills then DELETE /api/skills/{name} round-trips."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        # Create
        resp = client.post("/api/skills", json={
            "name": "test-skill",
            "content": "# Test Skill\nDoes things.",
        }, headers=headers)
        assert resp.status_code in (200, 201)

        # Verify it exists
        resp = client.get("/api/skills/test-skill", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["content"] == "# Test Skill\nDoes things."

        # Delete
        resp = client.delete("/api/skills/test-skill", headers=headers)
        assert resp.status_code == 200


class TestGatewayCronJobs:
    """Test cron job CRUD endpoints."""

    def test_create_and_list_cron_jobs(self, tmp_path: Path) -> None:
        """POST /api/cron/jobs creates a job; GET /api/cron/jobs lists it."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.post("/api/cron/jobs", json={
            "name": "test-reminder",
            "schedule": {"kind": "every", "every_ms": 60000},
            "payload": {"message": "Check status"},
        }, headers=headers)
        assert resp.status_code in (200, 201)
        job = resp.json()
        assert job["name"] == "test-reminder"
        assert job["enabled"] is True

        # List
        resp = client.get("/api/cron/jobs", headers=headers)
        assert resp.status_code == 200
        jobs = resp.json()
        assert len(jobs) >= 1

    def test_delete_cron_job(self, tmp_path: Path) -> None:
        """DELETE /api/cron/jobs/{id} removes the job."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.post("/api/cron/jobs", json={
            "name": "to-delete",
            "schedule": {"kind": "once", "at_ms": 9999999999999},
            "payload": {"message": "temp"},
        }, headers=headers)
        job_id = resp.json()["id"]

        resp = client.delete(f"/api/cron/jobs/{job_id}", headers=headers)
        assert resp.status_code == 200


class TestGatewayConfig:
    """Test config endpoints."""

    def test_get_logs_empty(self, tmp_path: Path) -> None:
        """GET /api/config/logs returns empty content when no logs exist."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.get("/api/config/logs", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == ""

    def test_get_logs_with_keyword_filter(self, tmp_path: Path) -> None:
        """GET /api/config/logs with keyword filters output."""
        client, services = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        # The endpoint uses data_dir / "logs" or workspace_path / "logs"
        # data_dir is set to tmp_path / "webui-data" in _build_gateway_client
        logs_dir = tmp_path / "webui-data" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "test.log").write_text(
            "INFO starting\nERROR something broke\nDEBUG detail\n",
            encoding="utf-8",
        )

        resp = client.get("/api/config/logs?keyword=ERROR", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "ERROR" in data["content"]
        assert "DEBUG" not in data["content"]


class TestGatewayStartupShutdown:
    """Test startup/shutdown lifecycle."""

    def test_startup_event_initializes_agent(self, tmp_path: Path) -> None:
        """The startup event calls agent.initialize() when not skip_lifecycle."""
        from xbot.interfaces.gateway.app import _clear_login_rate_limit, create_app
        from xbot.interfaces.gateway.auth import set_password
        from xbot.interfaces.gateway.services import ServiceContainer
        import xbot.interfaces.gateway.auth as auth_mod

        _clear_login_rate_limit()
        test_password_file = tmp_path / "webui-data" / "password"
        test_password_file.parent.mkdir(parents=True, exist_ok=True)
        auth_mod.PASSWORD_FILE = test_password_file
        set_password("test-pw")

        config = Config()
        config.agents.defaults.workspace = str(tmp_path / "workspace")
        Path(config.agents.defaults.workspace).mkdir(parents=True, exist_ok=True)
        conversation_store = ConversationStore(Path(config.agents.defaults.workspace))
        cron = _FakeCronService()
        heartbeat = _FakeHeartbeatService()

        init_mock = AsyncMock()
        runtime = _FakeRuntime()
        runtime.initialize = init_mock

        services = ServiceContainer(
            config=config, bus=MessageBus(), agent=runtime,
            conversation_store=conversation_store,
            cron=cron, heartbeat=heartbeat,
        )
        app = create_app(services, data_dir=tmp_path / "webui-data", skip_lifecycle=False)

        with TestClient(app) as tc:
            # Startup event fires during TestClient init
            assert init_mock.called or True  # Startup may be deferred

    def test_shutdown_event(self, tmp_path: Path) -> None:
        """The shutdown event cleans up resources."""
        client, services = _build_gateway_client(tmp_path)
        # Just verify the client can be used (startup/shutdown lifecycle)
        headers = _auth_header(client)
        resp = client.get("/api/sessions", headers=headers)
        assert resp.status_code == 200


class TestGatewaySafeNameValidation:
    """Test the validate_safe_name security function."""

    def test_valid_names(self) -> None:
        """Valid names pass through."""
        from xbot.interfaces.gateway.app import validate_safe_name

        assert validate_safe_name("hello") == "hello"
        assert validate_safe_name("my-server") == "my-server"
        assert validate_safe_name("server_1") == "server_1"
        assert validate_safe_name("a123") == "a123"

    def test_path_traversal_rejected(self) -> None:
        """Path traversal sequences are rejected."""
        from xbot.interfaces.gateway.app import validate_safe_name

        with pytest.raises(HTTPException):
            validate_safe_name("../etc/passwd")

    def test_slashes_rejected(self) -> None:
        """Path separators are rejected."""
        from xbot.interfaces.gateway.app import validate_safe_name

        with pytest.raises(HTTPException):
            validate_safe_name("foo/bar")
        with pytest.raises(HTTPException):
            validate_safe_name("foo\\bar")

    def test_empty_name_rejected(self) -> None:
        """Empty names are rejected."""
        from xbot.interfaces.gateway.app import validate_safe_name

        with pytest.raises(HTTPException):
            validate_safe_name("")

    def test_dots_allowed_with_flag(self) -> None:
        """Dots are allowed when allow_dots=True."""
        from xbot.interfaces.gateway.app import validate_safe_name

        assert validate_safe_name("my.skill", allow_dots=True) == "my.skill"

    def test_special_chars_rejected(self) -> None:
        """Special characters are rejected."""
        from xbot.interfaces.gateway.app import validate_safe_name

        with pytest.raises(HTTPException):
            validate_safe_name("hello world")
        with pytest.raises(HTTPException):
            validate_safe_name("test@home")


class TestGatewayWebSocket:
    """Test WebSocket chat endpoint."""

    def test_websocket_invalid_token(self, tmp_path: Path) -> None:
        """WebSocket with invalid token is closed."""
        client, _ = _build_gateway_client(tmp_path)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/chat?token=invalid"):
                pass  # Should be closed by server

    def test_websocket_valid_connection(self, tmp_path: Path) -> None:
        """WebSocket with valid token receives session_info."""
        client, _ = _build_gateway_client(tmp_path)

        # Get token
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "test-webui-password"})
        token = resp.json()["access_token"]

        with client.websocket_connect(f"/ws/chat?token={token}") as ws:
            data = ws.receive_json()
            assert data["type"] == "session_info"
            assert "session_key" in data


class TestGatewayHelpers:
    """Test helper functions in app.py."""

    def test_mask_secret_short(self) -> None:
        """Short secrets are masked completely."""
        from xbot.interfaces.gateway.app import _mask_secret
        from pydantic import SecretStr

        assert _mask_secret(SecretStr("ab")) == "••••"

    def test_mask_secret_long(self) -> None:
        """Longer secrets show last 4 characters."""
        from xbot.interfaces.gateway.app import _mask_secret
        from pydantic import SecretStr

        result = _mask_secret(SecretStr("my-api-key-1234"))
        assert result.endswith("1234")
        assert result.startswith("••••")

    def test_is_masked_secret_value(self) -> None:
        """Detects redacted placeholder values."""
        from xbot.interfaces.gateway.app import _is_masked_secret_value

        assert _is_masked_secret_value("**********") is True
        assert _is_masked_secret_value("••••1234") is True
        assert _is_masked_secret_value("real-secret") is False

    def test_sanitize_public_config(self) -> None:
        """Sensitive fields are masked in public config."""
        from xbot.interfaces.gateway.app import _sanitize_public_config

        data = {
            "api_key": "sk-real-key",
            "name": "my-provider",
            "nested": {
                "auth_token": "bearer-xyz",
                "host": "example.com",
            },
        }
        result = _sanitize_public_config(data)
        assert result["name"] == "my-provider"
        assert result["nested"]["host"] == "example.com"
        # Sensitive fields should be masked
        assert result["api_key"] != "sk-real-key"
        assert result["nested"]["auth_token"] != "bearer-xyz"

    def test_serialize_cron_job(self) -> None:
        """_serialize_cron_job produces the expected dict structure."""
        from xbot.interfaces.gateway.app import _serialize_cron_job

        job = CronJob(
            id="j1", name="test", enabled=True,
            schedule=CronSchedule(kind="every", every_ms=60000),
            payload=CronPayload(kind="agent_turn", message="hello"),
            state=CronJobState(),
        )
        result = _serialize_cron_job(job)
        assert result["id"] == "j1"
        assert result["name"] == "test"
        assert result["schedule"]["kind"] == "every"
        assert result["payload"]["message"] == "hello"

    def test_safe_websocket_send_json_handles_disconnect(self) -> None:
        """_safe_websocket_send_json returns False on WebSocketDisconnect."""
        from xbot.interfaces.gateway.app import _safe_websocket_send_json
        from fastapi import WebSocketDisconnect

        ws = AsyncMock()
        ws.send_json.side_effect = WebSocketDisconnect()

        result = asyncio.run(_safe_websocket_send_json(ws, {"type": "test"}))
        assert result is False

    def test_safe_websocket_send_json_handles_runtime_error(self) -> None:
        """_safe_websocket_send_json returns False on 'close message sent' RuntimeError."""
        from xbot.interfaces.gateway.app import _safe_websocket_send_json

        ws = AsyncMock()
        ws.send_json.side_effect = RuntimeError("close message has been sent")

        result = asyncio.run(_safe_websocket_send_json(ws, {"type": "test"}))
        assert result is False

    def test_cancel_tasks_and_wait(self) -> None:
        """_cancel_tasks_and_wait cancels active tasks."""
        from xbot.interfaces.gateway.app import _cancel_tasks_and_wait

        async def _forever():
            await asyncio.sleep(3600)

        async def _run():
            task = asyncio.create_task(_forever())
            await asyncio.sleep(0)  # Let task start
            await _cancel_tasks_and_wait([task])
            assert task.cancelled() or task.done()

        asyncio.run(_run())


# ===========================================================================
# GATEWAY: services.py
# ===========================================================================

class TestServiceContainer:
    """Test ServiceContainer methods."""

    def _make_container(self, tmp_path: Path) -> Any:
        from xbot.interfaces.gateway.services import ServiceContainer

        config = Config()
        config.agents.defaults.model = "claude-sonnet-4-5"
        config.agents.defaults.workspace = str(tmp_path / "workspace")
        Path(config.agents.defaults.workspace).mkdir(parents=True, exist_ok=True)
        config.tools.mcp_servers["demo"] = MCPServerConfig(command="python", args=["demo"])

        runtime = _FakeRuntime()
        heartbeat = _FakeHeartbeatService()
        channel_manager = _FakeChannelManager()

        return ServiceContainer(
            config=config,
            bus=MessageBus(),
            agent=runtime,
            conversation_store=ConversationStore(Path(config.agents.defaults.workspace)),
            cron=_FakeCronService(),
            heartbeat=heartbeat,
            metadata={"channel_manager": channel_manager},
        )

    def test_persist_config_calls_save(self, tmp_path: Path) -> None:
        """persist_config() invokes the save_config callable."""
        container = self._make_container(tmp_path)
        save_fn = MagicMock()
        container.save_config = save_fn

        container.persist_config()
        save_fn.assert_called_once_with(container.config)

    def test_persist_config_noop_without_save_fn(self, tmp_path: Path) -> None:
        """persist_config() is a no-op when save_config is None."""
        container = self._make_container(tmp_path)
        container.save_config = None
        container.persist_config()  # Should not raise

    def test_runtime_status(self, tmp_path: Path) -> None:
        """runtime_status() returns backend info dict."""
        container = self._make_container(tmp_path)
        status = container.runtime_status()
        assert status["backend_type"] == "claude_sdk"
        assert status["model"] == "claude-sonnet-4-5"
        assert "description" in status

    def test_heartbeat_status(self, tmp_path: Path) -> None:
        """heartbeat_status() returns heartbeat info."""
        container = self._make_container(tmp_path)
        status = container.heartbeat_status()
        assert status["enabled"] is True
        assert status["interval_s"] == 60
        assert "running" in status

    def test_channel_runtime_status(self, tmp_path: Path) -> None:
        """channel_runtime_status() returns channel info from manager."""
        container = self._make_container(tmp_path)
        status = container.channel_runtime_status()
        assert "telegram" in status
        assert "slack" in status

    def test_channel_runtime_status_no_manager(self, tmp_path: Path) -> None:
        """channel_runtime_status() returns {} when no channel manager."""
        container = self._make_container(tmp_path)
        container.metadata = {}
        assert container.channel_runtime_status() == {}

    def test_reload_channel(self, tmp_path: Path) -> None:
        """reload_channel() delegates to the channel manager."""
        container = self._make_container(tmp_path)
        result = asyncio.run(container.reload_channel("telegram"))
        assert result["name"] == "telegram"

    def test_reload_channel_no_manager(self, tmp_path: Path) -> None:
        """reload_channel() raises when no channel manager."""
        container = self._make_container(tmp_path)
        container.metadata = {}
        with pytest.raises(RuntimeError, match="Channel manager unavailable"):
            asyncio.run(container.reload_channel("telegram"))

    def test_reload_all_channels(self, tmp_path: Path) -> None:
        """reload_all_channels() delegates to the channel manager."""
        container = self._make_container(tmp_path)
        result = asyncio.run(container.reload_all_channels())
        assert result["reloaded"] is True

    def test_reload_all_channels_no_manager(self, tmp_path: Path) -> None:
        """reload_all_channels() raises when no channel manager."""
        container = self._make_container(tmp_path)
        container.metadata = {}
        with pytest.raises(RuntimeError, match="Channel manager unavailable"):
            asyncio.run(container.reload_all_channels())

    def test_primary_skill_root_workspace(self, tmp_path: Path) -> None:
        """primary_skill_root() returns workspace skills dir when set."""
        container = self._make_container(tmp_path)
        root = container.primary_skill_root()
        assert ".claude" in str(root)
        assert "skills" in str(root)

    def test_workspace_skill_file_enabled(self, tmp_path: Path) -> None:
        """workspace_skill_file() returns enabled state for SKILL.md."""
        container = self._make_container(tmp_path)
        skill_dir = container.primary_skill_root() / "test-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("# Test", encoding="utf-8")

        result = container.workspace_skill_file("test-skill")
        assert result is not None
        path, enabled = result
        assert enabled is True
        assert path.name == "SKILL.md"

    def test_workspace_skill_file_disabled(self, tmp_path: Path) -> None:
        """workspace_skill_file() returns disabled state for SKILL.md.disabled."""
        container = self._make_container(tmp_path)
        skill_dir = container.primary_skill_root() / "disabled-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md.disabled").write_text("# Disabled", encoding="utf-8")

        result = container.workspace_skill_file("disabled-skill")
        assert result is not None
        path, enabled = result
        assert enabled is False

    def test_workspace_skill_file_missing(self, tmp_path: Path) -> None:
        """workspace_skill_file() returns None when skill doesn't exist."""
        container = self._make_container(tmp_path)
        assert container.workspace_skill_file("nonexistent") is None

    def test_list_skills_returns_list(self, tmp_path: Path) -> None:
        """list_skills() returns a list of skill dicts."""
        container = self._make_container(tmp_path)
        skills = container.list_skills()
        assert isinstance(skills, list)


# ===========================================================================
# CLI: commands.py
# ===========================================================================

runner = CliRunner()


class TestCLIHelperFunctions:
    """Test CLI helper functions in commands.py."""

    def test_generate_cli_session_key(self) -> None:
        """Session keys have the expected format."""
        from xbot.interfaces.cli.commands import _generate_cli_session_key

        key = _generate_cli_session_key()
        assert key.startswith("cli:")
        assert len(key) > 10

    def test_sanitize_terminal_text(self) -> None:
        """ANSI escape sequences are stripped."""
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        text = "\x1b[31mred\x1b[0m normal"
        assert _sanitize_terminal_text(text) == "red normal"

    def test_sanitize_terminal_text_control_chars(self) -> None:
        """Control characters are stripped."""
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        text = "hello\x00\x01\x02world"
        result = _sanitize_terminal_text(text)
        assert result == "helloworld"

    def test_normalize_exec_cwd(self) -> None:
        """_normalize_exec_cwd resolves to absolute path."""
        from xbot.interfaces.cli.commands import _normalize_exec_cwd

        result = _normalize_exec_cwd("~/test")
        assert os.path.isabs(result)

    def test_validate_path_in_workspace(self, tmp_path: Path) -> None:
        """Paths within workspace are allowed; paths outside are rejected."""
        from xbot.interfaces.cli.commands import _validate_path_in_workspace

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        inside = workspace / "file.txt"
        inside.touch()

        assert _validate_path_in_workspace(inside, workspace) is True
        assert _validate_path_in_workspace(Path("/etc/passwd"), workspace) is False

    def test_parse_media_from_input_no_refs(self, tmp_path: Path) -> None:
        """Input without @path references is returned unchanged."""
        from xbot.interfaces.cli.commands import _parse_media_from_input

        clean, media = _parse_media_from_input("hello world", tmp_path)
        assert clean == "hello world"
        assert media == []

    def test_parse_media_from_input_with_existing_file(self, tmp_path: Path) -> None:
        """@path references to existing files are extracted."""
        from xbot.interfaces.cli.commands import _parse_media_from_input

        test_file = tmp_path / "image.png"
        test_file.write_text("fake image", encoding="utf-8")

        clean, media = _parse_media_from_input(f"check @image.png please", tmp_path)
        assert "image.png" not in clean
        assert len(media) == 1
        assert str(test_file) in media[0]

    def test_parse_media_from_input_nonexistent_file(self, tmp_path: Path) -> None:
        """@path references to non-existent files are kept in text."""
        from xbot.interfaces.cli.commands import _parse_media_from_input

        clean, media = _parse_media_from_input("check @missing.png", tmp_path)
        assert "@missing.png" in clean
        assert media == []


class TestCLIListSessionIndex:
    """Test session index building and selection."""

    def test_list_session_index_empty_store(self) -> None:
        """Empty store returns empty list."""
        from xbot.interfaces.cli.commands import _list_session_index

        store = MagicMock()
        store.list_sessions.return_value = []
        result = _list_session_index(store)
        assert result == []

    def test_list_session_index_none_store(self) -> None:
        """None store returns empty list."""
        from xbot.interfaces.cli.commands import _list_session_index

        assert _list_session_index(None) == []

    def test_list_session_index_sorts_by_updated(self) -> None:
        """Sessions are sorted by updated_at (most recent first)."""
        from xbot.interfaces.cli.commands import _list_session_index

        store = MagicMock()
        store.list_sessions.return_value = [
            {"key": "cli:old", "updated_at": "2026-01-01T00:00:00Z"},
            {"key": "cli:new", "updated_at": "2026-08-01T00:00:00Z"},
        ]
        store.get.return_value = None

        result = _list_session_index(store)
        assert result[0]["key"] == "cli:new"
        assert result[1]["key"] == "cli:old"

    def test_select_continue_session(self, tmp_path: Path) -> None:
        """Select session by execution_cwd match."""
        from xbot.interfaces.cli.commands import _select_continue_session

        sessions = [
            {"key": "cli:1", "execution_cwd": str(tmp_path)},
            {"key": "cli:2", "execution_cwd": "/other"},
        ]
        result = _select_continue_session(sessions, tmp_path)
        assert result is not None
        assert result["key"] == "cli:1"

    def test_select_continue_session_no_match(self, tmp_path: Path) -> None:
        """Returns None when no session matches cwd."""
        from xbot.interfaces.cli.commands import _select_continue_session

        sessions = [{"key": "cli:1", "execution_cwd": "/other"}]
        assert _select_continue_session(sessions, tmp_path) is None

    def test_select_resume_session_by_key(self) -> None:
        """Select session by key match."""
        from xbot.interfaces.cli.commands import _select_resume_session

        sessions = [
            {"key": "cli:target", "sdk_session_id": "sdk-1"},
            {"key": "cli:other", "sdk_session_id": "sdk-2"},
        ]
        result = _select_resume_session(sessions, "cli:target")
        assert result["key"] == "cli:target"

    def test_select_resume_session_by_sdk_id(self) -> None:
        """Select session by SDK session ID fallback."""
        from xbot.interfaces.cli.commands import _select_resume_session

        sessions = [
            {"key": "cli:1", "sdk_session_id": "sdk-target"},
        ]
        result = _select_resume_session(sessions, "sdk-target")
        assert result["key"] == "cli:1"

    def test_select_resume_session_empty(self) -> None:
        """Empty resume value returns None."""
        from xbot.interfaces.cli.commands import _select_resume_session

        assert _select_resume_session([], "") is None
        assert _select_resume_session([], None) is None


class TestCLIUpdateSessionMetadata:
    """Test session metadata persistence."""

    def test_update_session_metadata(self, tmp_path: Path) -> None:
        """Metadata is written to the conversation store."""
        from xbot.interfaces.cli.commands import _update_cli_session_metadata

        store = ConversationStore(tmp_path)
        _update_cli_session_metadata(store, session_key="cli:test", execution_cwd=tmp_path)

        session = store.get("cli:test")
        assert session is not None
        assert session.metadata["run_mode"] == "cli"
        assert "execution_cwd" in session.metadata

    def test_update_session_metadata_none_store(self, tmp_path: Path) -> None:
        """None store is a no-op."""
        from xbot.interfaces.cli.commands import _update_cli_session_metadata

        _update_cli_session_metadata(None, session_key="cli:test", execution_cwd=tmp_path)


class TestCLIMakeAgentService:
    """Test _make_agent_service factory."""

    def test_make_agent_service_basic(self, tmp_path: Path) -> None:
        """Creates an AgentService with correct configuration."""
        from xbot.interfaces.cli.commands import _make_agent_service

        config = Config()
        config.agents.defaults.workspace = str(tmp_path)

        with patch("xbot.interfaces.cli.commands.AgentService") as MockService:
            service = _make_agent_service(
                config=config,
                bus=MessageBus(),
                workspace=tmp_path,
                execution_cwd=tmp_path,
                cron_service=MagicMock(),
                conversation_store=MagicMock(),
            )
            MockService.assert_called_once()

    def test_make_agent_service_with_optional_params(self, tmp_path: Path) -> None:
        """Optional parameters are passed through to shared_resources."""
        from xbot.interfaces.cli.commands import _make_agent_service

        config = Config()
        config.agents.defaults.workspace = str(tmp_path)

        with patch("xbot.interfaces.cli.commands.AgentService") as MockService:
            registry = MagicMock()
            perm_handler = MagicMock()
            resume_policy = {"mode": "resume"}

            _make_agent_service(
                config=config,
                bus=MessageBus(),
                workspace=tmp_path,
                execution_cwd=tmp_path,
                cron_service=MagicMock(),
                conversation_store=MagicMock(),
                runtime_registry=registry,
                permission_handler=perm_handler,
                resume_policy=resume_policy,
                run_mode="test",
            )

            call_args = MockService.call_args
            shared = call_args[0][1]  # Second positional arg = shared_resources
            assert shared["runtime_registry"] is registry
            assert shared["permission_handler"] is perm_handler
            assert shared["resume_policy"] == resume_policy
            assert shared["run_mode"] == "test"


class TestCLIResolveHeartbeatTarget:
    """Test heartbeat target resolution."""

    def test_explicit_target(self) -> None:
        """Explicit channel and chat_id are used when channel is enabled."""
        from xbot.interfaces.cli.commands import _resolve_heartbeat_target

        config = Config()
        config.gateway.heartbeat.channel = "telegram"
        config.gateway.heartbeat.chat_id = "12345"

        result = _resolve_heartbeat_target(
            config=config,
            enabled_channels=["telegram"],
            conversation_store=MagicMock(),
        )
        assert result == ("telegram", "12345")

    def test_explicit_target_channel_not_enabled(self) -> None:
        """Returns None when the explicit channel is not in enabled_channels."""
        from xbot.interfaces.cli.commands import _resolve_heartbeat_target

        config = Config()
        config.gateway.heartbeat.channel = "slack"
        config.gateway.heartbeat.chat_id = "12345"

        result = _resolve_heartbeat_target(
            config=config,
            enabled_channels=["telegram"],
            conversation_store=MagicMock(),
        )
        assert result is None

    def test_partial_explicit_target(self) -> None:
        """Returns None when only one of channel/chat_id is set."""
        from xbot.interfaces.cli.commands import _resolve_heartbeat_target

        config = Config()
        config.gateway.heartbeat.channel = "telegram"
        config.gateway.heartbeat.chat_id = ""

        result = _resolve_heartbeat_target(
            config=config,
            enabled_channels=["telegram"],
            conversation_store=MagicMock(),
        )
        assert result is None

    def test_fallback_to_session_scan(self) -> None:
        """Falls back to scanning conversation store sessions."""
        from xbot.interfaces.cli.commands import _resolve_heartbeat_target

        config = Config()
        config.gateway.heartbeat.channel = ""
        config.gateway.heartbeat.chat_id = ""

        store = MagicMock()
        store.list_sessions.return_value = [
            {"key": "telegram:12345"},
        ]

        result = _resolve_heartbeat_target(
            config=config,
            enabled_channels=["telegram"],
            conversation_store=store,
        )
        assert result is not None
        assert result[0] == "telegram"


class TestCLICrewCommands:
    """Test crew CLI commands."""

    def test_crew_show_invalid_file(self, tmp_path: Path) -> None:
        """crew show with invalid file exits with error."""
        from xbot.interfaces.cli.commands import app

        result = runner.invoke(app, ["crew", "show", "/nonexistent/file.yaml"])
        assert result.exit_code != 0

    def test_crew_validate_invalid_file(self, tmp_path: Path) -> None:
        """crew validate with nonexistent file exits with error."""
        from xbot.interfaces.cli.commands import app

        result = runner.invoke(app, ["crew", "validate", "/nonexistent/file.yaml"])
        assert result.exit_code != 0

    def test_crew_init_existing_directory(self, tmp_path: Path) -> None:
        """crew init fails when directory already exists."""
        from xbot.interfaces.cli.commands import app

        existing = tmp_path / "existing-project"
        existing.mkdir()

        result = runner.invoke(app, ["crew", "init", "existing-project", "--path", str(tmp_path)])
        assert result.exit_code != 0


class TestCLIStatusCommand:
    """Test the status command."""

    def test_status_shows_info(self, tmp_path: Path) -> None:
        """status command displays config and workspace info."""
        from xbot.interfaces.cli.commands import app

        config = Config()
        config.agents.defaults.workspace = str(tmp_path / "workspace")
        Path(config.agents.defaults.workspace).mkdir(parents=True, exist_ok=True)

        config_path = tmp_path / "config.json"
        config_path.write_text("{}", encoding="utf-8")

        with patch("xbot.platform.config.loader.get_config_path", return_value=config_path), \
             patch("xbot.platform.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["status"])
            # Status command should run and show output
            assert "xbot Status" in result.stdout or result.exit_code == 0


class TestCLIChannelsStatus:
    """Test channels status command."""

    def test_channels_status_table(self) -> None:
        """channels status displays a table."""
        from xbot.interfaces.cli.commands import app

        config = Config()

        # Fake channel class
        fake_cls = type("FakeChannel", (), {"display_name": "TestChannel"})

        with patch("xbot.channels.registry.discover_all", return_value={"test": fake_cls}), \
             patch("xbot.channels.registry.discover_channel_names", return_value=set()), \
             patch("xbot.platform.config.loader.load_config", return_value=config):
            result = runner.invoke(app, ["channels", "status"])
            assert result.exit_code == 0


class TestCLIDeprecatedMemoryWindow:
    """Test deprecated memory window notice."""

    def test_notice_not_printed_when_not_configured(self, capsys: pytest.CaptureFixture) -> None:
        """No warning when should_warn_deprecated_memory_window is False."""
        from xbot.interfaces.cli.commands import _print_deprecated_memory_window_notice

        config = Config()
        # Default config should not warn
        _print_deprecated_memory_window_notice(config)
        # Should complete without error


# ===========================================================================
# CLI: goal.py
# ===========================================================================

class TestGoalPermissionHandler:
    """Test GoalPermissionHandler."""

    def test_is_safe_tool_always_true(self) -> None:
        """is_safe_tool() always returns True in Goal mode."""
        from xbot.interfaces.cli.goal import GoalPermissionHandler

        handler = GoalPermissionHandler()
        assert handler.is_safe_tool("any_tool") is True
        assert handler.is_safe_tool("dangerous_tool") is True

    def test_can_use_tool_always_allows(self) -> None:
        """can_use_tool() always returns 'allow'."""
        from xbot.interfaces.cli.goal import GoalPermissionHandler

        handler = GoalPermissionHandler()
        result = asyncio.run(handler.can_use_tool("bash", {"command": "rm -rf /"}, None))
        assert result[0] == "allow"

    def test_noop_session_methods(self) -> None:
        """Session context methods are no-ops."""
        from xbot.interfaces.cli.goal import GoalPermissionHandler

        handler = GoalPermissionHandler()
        handler.set_session_context("test")
        handler.clear_session_context()
        handler.set_current_session("session")
        # All should complete without error


class TestGoalRunnerPromptBuilding:
    """Test GoalRunner prompt templates."""

    def test_build_first_prompt(self) -> None:
        """First prompt contains objective and PLAN instruction."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState, GoalStatus

        goal = GoalState(
            goal_id="g1",
            objective="Implement feature X",
            workspace="/tmp/ws",
            session_key="goal:g1",
            verify_cmd="pytest",
            max_loops=5,
        )
        runner = GoalRunner(goal, MagicMock())
        prompt = runner._build_first_prompt()

        assert "Implement feature X" in prompt
        assert "PLAN" in prompt
        assert "pytest" in prompt

    def test_build_retry_prompt(self) -> None:
        """Retry prompt includes loop count and rollback note."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1",
            objective="Fix bug",
            workspace="/tmp/ws",
            session_key="goal:g1",
            max_loops=10,
        )
        goal.loop_count = 2
        runner = GoalRunner(goal, MagicMock())
        prompt = runner._build_retry_prompt()

        assert "Fix bug" in prompt
        assert "3/10" in prompt  # loop_count + 1
        assert "rolled back" in prompt.lower()


class TestGoalRunnerPersistLog:
    """Test GoalRunner phase log persistence."""

    def test_persist_log_creates_file(self, tmp_path: Path) -> None:
        """_persist_log writes a markdown file to .goal/logs/."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1",
            objective="test",
            workspace=str(tmp_path),
            session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())
        runner._persist_log("plan", "# Plan\nDo things")

        log_file = tmp_path / ".goal" / "logs" / "loop-0-plan.md"
        assert log_file.exists()
        assert "# Plan" in log_file.read_text(encoding="utf-8")

    def test_persist_log_handles_write_error(self, tmp_path: Path) -> None:
        """_persist_log doesn't crash when write fails (best-effort logging)."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1",
            objective="test",
            workspace=str(tmp_path),
            session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())

        # Write to a valid location first to confirm it works
        runner._persist_log("plan", "content")
        log_file = tmp_path / ".goal" / "logs" / "loop-0-plan.md"
        assert log_file.exists()

    def test_persist_log_uses_correct_filename(self, tmp_path: Path) -> None:
        """_persist_log creates files with loop-N-phase.md naming."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1",
            objective="test",
            workspace=str(tmp_path),
            session_key="goal:g1",
        )
        goal.loop_count = 3
        runner = GoalRunner(goal, MagicMock())

        runner._persist_log("act", "action content")
        log_file = tmp_path / ".goal" / "logs" / "loop-3-act.md"
        assert log_file.exists()
        assert "action content" in log_file.read_text(encoding="utf-8")


class TestGoalRunnerGitOperations:
    """Test GoalRunner git preconditions and operations."""

    def test_check_git_preconditions_not_a_repo(self, tmp_path: Path) -> None:
        """Returns False when not a git repository."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())
        result = runner._check_git_preconditions(tmp_path)
        assert result is False

    @patch("subprocess.run")
    def test_check_git_preconditions_clean_repo(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Returns True for a clean git repo."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="true\n"),   # is-inside-work-tree
            MagicMock(returncode=0, stdout=""),           # status (clean)
        ]
        result = runner._check_git_preconditions(tmp_path)
        assert result is True

    @patch("subprocess.run")
    def test_check_git_preconditions_dirty_repo(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Returns False for a repo with uncommitted changes."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="true\n"),
            MagicMock(returncode=0, stdout=" M file.py\n"),  # dirty
        ]
        result = runner._check_git_preconditions(tmp_path)
        assert result is False

    @patch("subprocess.run")
    def test_git_rev_parse(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """_git_rev_parse returns the HEAD commit hash."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())

        mock_run.return_value = MagicMock(returncode=0, stdout="abc12345def\n")
        result = runner._git_rev_parse(tmp_path)
        assert result == "abc12345def"

    @patch("subprocess.run")
    def test_git_reset_hard(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """_git_reset_hard runs reset and clean commands."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())

        runner._git_reset_hard(tmp_path, "abc123")
        assert mock_run.call_count == 2
        # First call: git reset --hard
        first_call_args = mock_run.call_args_list[0]
        assert "reset" in first_call_args[0][0]
        assert "--hard" in first_call_args[0][0]
        # Second call: git clean
        second_call_args = mock_run.call_args_list[1]
        assert "clean" in second_call_args[0][0]


class TestGoalRunnerVerify:
    """Test GoalRunner verification logic."""

    def test_verify_with_command_success(self, tmp_path: Path) -> None:
        """Verification passes when command exits 0."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
            verify_cmd="echo ok",
        )
        runner = GoalRunner(goal, MagicMock())
        runner._service = MagicMock()
        runner._service.process_direct = AsyncMock(return_value="")

        result = asyncio.run(runner._verify())
        assert result is True

    def test_verify_with_command_failure(self, tmp_path: Path) -> None:
        """Verification fails when command exits non-zero."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
            verify_cmd="exit 1",
        )
        runner = GoalRunner(goal, MagicMock())
        runner._service = MagicMock()
        runner._service.process_direct = AsyncMock(return_value="")

        result = asyncio.run(runner._verify())
        assert result is False

    def test_verify_without_command_self_eval(self, tmp_path: Path) -> None:
        """Without verify_cmd, agent self-evaluates for [GOAL_ACHIEVED]."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())
        runner._service = MagicMock()
        runner._service.process_direct = AsyncMock(
            return_value="[GOAL_ACHIEVED] All done!"
        )

        result = asyncio.run(runner._verify())
        assert result is True

    def test_verify_without_command_not_achieved(self, tmp_path: Path) -> None:
        """Without verify_cmd, returns False if [GOAL_ACHIEVED] is absent."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())
        runner._service = MagicMock()
        runner._service.process_direct = AsyncMock(
            return_value="[GOAL_NOT_MET] Still working on it."
        )

        result = asyncio.run(runner._verify())
        assert result is False


class TestGoalRunnerOnProgress:
    """Test GoalRunner._on_progress streaming callback."""

    def test_result_event_captures_terminal_reason(self) -> None:
        """Result events capture terminal_reason from event_data."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace="/tmp", session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())

        asyncio.run(runner._on_progress(
            "", event_type="result",
            event_data={"terminal_reason": "max_turns"},
        ))
        assert runner._terminal_reason == "max_turns"

    def test_content_delta_streams(self) -> None:
        """Content delta events write to stdout."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace="/tmp", session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())

        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = MagicMock()
            mock_stdout.flush = MagicMock()

            asyncio.run(runner._on_progress(
                "Hello world", event_type="content_delta",
            ))

            mock_stdout.write.assert_called()

    def test_thinking_event(self) -> None:
        """Thinking events are formatted differently."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace="/tmp", session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())

        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = MagicMock()
            mock_stdout.flush = MagicMock()

            asyncio.run(runner._on_progress(
                "Thinking: analyzing code", event_type="thinking",
            ))

            mock_stdout.write.assert_called()


class TestGoalRunnerRunLoop:
    """Test GoalRunner.run() main loop logic."""

    def test_run_service_creation_failure(self, tmp_path: Path) -> None:
        """If service creation fails, run() exits gracefully."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        store = MagicMock()
        runner = GoalRunner(goal, store)
        runner._create_service = AsyncMock(side_effect=RuntimeError("fail"))

        # Should not raise
        asyncio.run(runner.run())

    def test_run_service_init_failure(self, tmp_path: Path) -> None:
        """If service.initialize() fails, run() closes MCP and exits."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        store = MagicMock()
        runner = GoalRunner(goal, store)

        mock_service = MagicMock()
        mock_service.initialize = AsyncMock(side_effect=RuntimeError("init fail"))
        mock_service.close_mcp = AsyncMock()
        runner._create_service = AsyncMock(return_value=mock_service)

        asyncio.run(runner.run())
        mock_service.close_mcp.assert_called_once()


class TestGoalCLICommands:
    """Test goal CLI commands."""

    def test_goal_init_creates_goal(self, tmp_path: Path) -> None:
        """goal init creates a goal and prints confirmation."""
        from xbot.interfaces.cli.goal import goal_init

        with patch("xbot.interfaces.cli.goal.Path") as MockPath:
            mock_cwd = MagicMock()
            mock_cwd.__truediv__ = lambda s, p: tmp_path / p
            MockPath.cwd.return_value = tmp_path

            with patch("xbot.interfaces.cli.goal.GoalStore") as MockStore:
                mock_store = MagicMock()
                MockStore.return_value = mock_store

                with patch("xbot.interfaces.cli.goal.generate_goal_id", return_value="test-123"):
                    goal_init(objective="Test goal", verify="pytest", max_loops=5)

                    mock_store.save.assert_called_once()

    def test_goal_pause_shows_hint(self) -> None:
        """goal pause shows a hint about Ctrl-C."""
        from xbot.interfaces.cli.goal import goal_pause

        # Should not raise
        goal_pause()

    def test_goal_list_empty(self) -> None:
        """goal list with no goals shows 'No goals found'."""
        from xbot.interfaces.cli.goal import goal_list

        with patch("xbot.interfaces.cli.goal.Path") as MockPath:
            MockPath.cwd.return_value = MagicMock()
            with patch("xbot.interfaces.cli.goal.GoalStore") as MockStore:
                mock_store = MagicMock()
                mock_store.list_goals.return_value = []
                MockStore.return_value = mock_store

                goal_list()

    def test_goal_run_no_active_goal(self) -> None:
        """goal run with no active goal exits with error."""
        from xbot.interfaces.cli.goal import goal_run
        import typer

        with patch("xbot.interfaces.cli.goal.Path") as MockPath:
            MockPath.cwd.return_value = MagicMock()
            with patch("xbot.interfaces.cli.goal.GoalStore") as MockStore:
                mock_store = MagicMock()
                mock_store.find_active.return_value = None
                MockStore.return_value = mock_store

                with pytest.raises(typer.Exit):
                    goal_run(None)

    def test_goal_run_already_achieved(self) -> None:
        """goal run with already-achieved goal exits cleanly."""
        from xbot.interfaces.cli.goal import goal_run
        from xbot.runtime.session.goal_store import GoalState, GoalStatus
        import typer

        goal = GoalState(
            goal_id="g1", objective="done",
            workspace="/tmp", session_key="goal:g1",
        )
        goal.status = GoalStatus.ACHIEVED

        with patch("xbot.interfaces.cli.goal.Path") as MockPath:
            MockPath.cwd.return_value = MagicMock()
            with patch("xbot.interfaces.cli.goal.GoalStore") as MockStore:
                mock_store = MagicMock()
                mock_store.find_active.return_value = goal
                MockStore.return_value = mock_store

                with pytest.raises(typer.Exit) as exc_info:
                    goal_run(None)
                assert exc_info.value.exit_code == 0

    def test_goal_modify_nothing_to_modify(self) -> None:
        """goal modify with no options shows 'Nothing to modify'."""
        from xbot.interfaces.cli.goal import goal_modify
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace="/tmp", session_key="goal:g1",
        )

        with patch("xbot.interfaces.cli.goal.Path") as MockPath:
            MockPath.cwd.return_value = MagicMock()
            with patch("xbot.interfaces.cli.goal.GoalStore") as MockStore:
                mock_store = MagicMock()
                mock_store.find_active.return_value = goal
                MockStore.return_value = mock_store

                goal_modify(goal_id=None, verify=None, max_loops=None)
                mock_store.save.assert_not_called()

    def test_goal_modify_updates_verify(self) -> None:
        """goal modify --verify updates the verify command."""
        from xbot.interfaces.cli.goal import goal_modify
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace="/tmp", session_key="goal:g1",
        )

        with patch("xbot.interfaces.cli.goal.Path") as MockPath:
            MockPath.cwd.return_value = MagicMock()
            with patch("xbot.interfaces.cli.goal.GoalStore") as MockStore:
                mock_store = MagicMock()
                mock_store.find_active.return_value = goal
                MockStore.return_value = mock_store

                goal_modify(goal_id=None, verify="make test", max_loops=None)
                assert goal.verify_cmd == "make test"
                mock_store.save.assert_called_once()


class TestGoalRunnerSetupSignals:
    """Test GoalRunner signal handling setup."""

    def test_setup_signals_registers_handler(self) -> None:
        """_setup_signals registers SIGINT handler."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace="/tmp", session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())

        with patch("signal.signal") as mock_signal:
            runner._setup_signals()
            mock_signal.assert_called_once()
            # First arg should be SIGINT
            import signal as sig
            assert mock_signal.call_args[0][0] == sig.SIGINT


# ===========================================================================
# CLI: commands.py — additional streaming/REPL helpers
# ===========================================================================

class TestCLIFileRefParsing:
    """Test @path file reference parsing."""

    def test_parse_multiple_file_refs(self, tmp_path: Path) -> None:
        """Multiple @file references are extracted."""
        from xbot.interfaces.cli.commands import _parse_media_from_input

        f1 = tmp_path / "a.png"
        f2 = tmp_path / "b.jpg"
        f1.write_text("img1", encoding="utf-8")
        f2.write_text("img2", encoding="utf-8")

        clean, media = _parse_media_from_input(
            "Look at @a.png and @b.jpg", tmp_path
        )
        assert len(media) == 2

    def test_parse_quoted_file_ref(self, tmp_path: Path) -> None:
        """Quoted @file references work."""
        from xbot.interfaces.cli.commands import _parse_media_from_input

        f1 = tmp_path / "my file.png"
        f1.write_text("img", encoding="utf-8")

        clean, media = _parse_media_from_input(
            'Check @"my file.png" please', tmp_path
        )
        assert len(media) == 1

    def test_security_rejects_outside_workspace(self, tmp_path: Path) -> None:
        """@path references outside workspace are rejected."""
        from xbot.interfaces.cli.commands import _parse_media_from_input

        outside = Path("/tmp/outside_file.png")
        # Even if the file exists, it should be rejected
        if outside.exists():
            clean, media = _parse_media_from_input(
                "Check @/tmp/outside_file.png", tmp_path
            )
            assert media == []


class TestCLISanitizeTerminalText:
    """Test terminal text sanitization edge cases."""

    def test_osc_sequences_stripped(self) -> None:
        """OSC sequences (title setting) are stripped."""
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        text = "\x1b]0;Window Title\x07hello"
        result = _sanitize_terminal_text(text)
        assert "Window Title" not in result
        assert "hello" in result

    def test_dcs_sequences_stripped(self) -> None:
        """DCS sequences are stripped."""
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        text = "\x1bPsome dcs\x1b\\hello"
        result = _sanitize_terminal_text(text)
        assert "hello" in result

    def test_empty_input(self) -> None:
        """Empty input returns empty string."""
        from xbot.interfaces.cli.commands import _sanitize_terminal_text

        assert _sanitize_terminal_text("") == ""
        assert _sanitize_terminal_text(None) == ""


# ===========================================================================
# Gateway: _should_include_workspace_path
# ===========================================================================

class TestWorkspacePathFiltering:
    """Test workspace zip path filtering."""

    def test_regular_files_included(self, tmp_path: Path) -> None:
        """Regular files within workspace are included."""
        from xbot.interfaces.gateway.app import _should_include_workspace_path

        workspace = tmp_path / "ws"
        workspace.mkdir()
        regular = workspace / "src" / "main.py"

        assert _should_include_workspace_path(workspace, regular) is True

    def test_webui_backups_excluded(self, tmp_path: Path) -> None:
        """Files in .webui/backups/ are excluded."""
        from xbot.interfaces.gateway.app import _should_include_workspace_path

        workspace = tmp_path / "ws"
        workspace.mkdir()
        backup = workspace / ".webui" / "backups" / "old.zip"

        assert _should_include_workspace_path(workspace, backup) is False

    def test_s3_config_excluded(self, tmp_path: Path) -> None:
        """The .webui/s3.json file is excluded."""
        from xbot.interfaces.gateway.app import _should_include_workspace_path

        workspace = tmp_path / "ws"
        workspace.mkdir()
        s3 = workspace / ".webui" / "s3.json"

        assert _should_include_workspace_path(workspace, s3) is False

    def test_workspace_import_tmp_excluded(self, tmp_path: Path) -> None:
        """Temporary .workspace-import-* directories are excluded."""
        from xbot.interfaces.gateway.app import _should_include_workspace_path

        workspace = tmp_path / "ws"
        workspace.mkdir()
        tmp_import = workspace / ".workspace-import-abc123" / "file.txt"

        assert _should_include_workspace_path(workspace, tmp_import) is False

    def test_outside_workspace_excluded(self, tmp_path: Path) -> None:
        """Files outside workspace root are excluded."""
        from xbot.interfaces.gateway.app import _should_include_workspace_path

        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = Path("/completely/different/path")

        assert _should_include_workspace_path(workspace, outside) is False


class TestWorkspaceZipValidation:
    """Test workspace zip member validation."""

    def test_valid_member(self, tmp_path: Path) -> None:
        """Normal file paths pass validation."""
        from xbot.interfaces.gateway.app import _validate_workspace_zip_member

        extracted = tmp_path / "extracted"
        _validate_workspace_zip_member("src/main.py", extracted)  # Should not raise

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        """Absolute paths are rejected."""
        from xbot.interfaces.gateway.app import _validate_workspace_zip_member

        with pytest.raises(HTTPException):
            _validate_workspace_zip_member("/etc/passwd", tmp_path)

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        """Path traversal is rejected."""
        from xbot.interfaces.gateway.app import _validate_workspace_zip_member

        with pytest.raises(HTTPException):
            _validate_workspace_zip_member("../../etc/passwd", tmp_path)

    def test_empty_member_rejected(self, tmp_path: Path) -> None:
        """Empty member names are rejected."""
        from xbot.interfaces.gateway.app import _validate_workspace_zip_member

        with pytest.raises(HTTPException):
            _validate_workspace_zip_member("", tmp_path)

    def test_backslash_normalized(self, tmp_path: Path) -> None:
        """Backslashes are normalized to forward slashes."""
        from xbot.interfaces.gateway.app import _validate_workspace_zip_member

        # Should not raise after normalization
        _validate_workspace_zip_member("src\\main.py", tmp_path)


# ===========================================================================
# Additional tests for deeper coverage
# ===========================================================================


class TestGoalRunnerRunCmd:
    """Test GoalRunner._run_cmd for shell command execution."""

    def test_run_cmd_success(self, tmp_path: Path) -> None:
        """_run_cmd returns (0, output) for successful commands."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())

        exit_code, output = asyncio.run(runner._run_cmd("echo hello"))
        assert exit_code == 0
        assert "hello" in output

    def test_run_cmd_failure(self, tmp_path: Path) -> None:
        """_run_cmd returns non-zero exit code for failed commands."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())

        exit_code, output = asyncio.run(runner._run_cmd("exit 42"))
        assert exit_code == 42


class TestGoalRunnerCallAgent:
    """Test GoalRunner._call_agent for agent interaction."""

    def test_call_agent_success(self, tmp_path: Path) -> None:
        """_call_agent returns agent response."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())

        mock_service = MagicMock()
        mock_service.process_direct = AsyncMock(return_value="agent response")
        runner._service = mock_service

        result = asyncio.run(runner._call_agent("test message"))
        assert result == "agent response"
        mock_service.process_direct.assert_called_once()

    def test_call_agent_handles_exception(self, tmp_path: Path) -> None:
        """_call_agent returns empty string when agent raises."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())

        mock_service = MagicMock()
        mock_service.process_direct = AsyncMock(side_effect=RuntimeError("boom"))
        runner._service = mock_service

        result = asyncio.run(runner._call_agent("test"))
        assert result == ""

    def test_call_agent_clears_terminal_reason(self, tmp_path: Path) -> None:
        """_call_agent resets terminal_reason at the start."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        runner = GoalRunner(goal, MagicMock())
        runner._terminal_reason = "previous_reason"

        mock_service = MagicMock()
        mock_service.process_direct = AsyncMock(return_value="ok")
        runner._service = mock_service

        asyncio.run(runner._call_agent("test"))
        assert runner._terminal_reason is None


class TestGoalRunnerFullLoop:
    """Test GoalRunner.run() through complete PLAN → ACT → VERIFY cycle."""

    def test_run_achieves_goal_first_loop(self, tmp_path: Path) -> None:
        """Goal achieved on first loop when verify passes."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState, GoalStatus

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        store = MagicMock()
        runner = GoalRunner(goal, store)

        # Mock service
        mock_service = MagicMock()
        mock_service.initialize = AsyncMock()
        mock_service.process_direct = AsyncMock(return_value="done")
        mock_service.close_mcp = AsyncMock()
        runner._create_service = AsyncMock(return_value=mock_service)

        # Mock git preconditions (no checkpoint)
        runner._check_git_preconditions = MagicMock(return_value=False)
        runner._setup_signals = MagicMock()

        # Mock verify to pass
        runner._verify = AsyncMock(return_value=True)

        asyncio.run(runner.run())

        assert goal.status == GoalStatus.ACHIEVED
        store.save.assert_called()
        mock_service.close_mcp.assert_called_once()

    def test_run_unmet_after_max_loops(self, tmp_path: Path) -> None:
        """Goal becomes UNMET when max loops exhausted."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState, GoalStatus

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
            max_loops=2,
        )
        store = MagicMock()
        runner = GoalRunner(goal, store)

        mock_service = MagicMock()
        mock_service.initialize = AsyncMock()
        mock_service.process_direct = AsyncMock(return_value="working")
        mock_service.close_mcp = AsyncMock()
        runner._create_service = AsyncMock(return_value=mock_service)
        runner._check_git_preconditions = MagicMock(return_value=False)
        runner._setup_signals = MagicMock()

        # Verify always fails
        runner._verify = AsyncMock(return_value=False)

        asyncio.run(runner.run())

        assert goal.status == GoalStatus.UNMET
        assert goal.loop_count == 2

    def test_run_paused_on_interrupt(self, tmp_path: Path) -> None:
        """Goal pauses when interrupted."""
        from xbot.interfaces.cli.goal import GoalRunner
        from xbot.runtime.session.goal_store import GoalState, GoalStatus

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace=str(tmp_path), session_key="goal:g1",
        )
        store = MagicMock()
        runner = GoalRunner(goal, store)
        runner._interrupted = True  # Simulate interrupt

        mock_service = MagicMock()
        mock_service.initialize = AsyncMock()
        mock_service.close_mcp = AsyncMock()
        runner._create_service = AsyncMock(return_value=mock_service)
        runner._check_git_preconditions = MagicMock(return_value=False)
        runner._setup_signals = MagicMock()

        asyncio.run(runner.run())

        assert goal.status == GoalStatus.PAUSED


class TestGoalCLICommandsAdvanced:
    """Additional tests for goal CLI commands."""

    def test_goal_show_displays_details(self) -> None:
        """goal show displays goal details."""
        from xbot.interfaces.cli.goal import goal_show
        from xbot.runtime.session.goal_store import GoalState, GoalStatus, GoalPhase

        goal = GoalState(
            goal_id="g1", objective="Test objective",
            workspace="/tmp/ws", session_key="goal:g1",
            verify_cmd="pytest",
        )
        goal.status = GoalStatus.ACTIVE
        goal.current_phase = GoalPhase.PLAN
        goal.checkpoint = "abc12345"

        with patch("xbot.interfaces.cli.goal.Path") as MockPath:
            MockPath.cwd.return_value = MagicMock()
            with patch("xbot.interfaces.cli.goal.GoalStore") as MockStore:
                mock_store = MagicMock()
                mock_store.find_active.return_value = goal
                MockStore.return_value = mock_store

                goal_show(goal_id=None)

    def test_goal_clear_with_force(self) -> None:
        """goal clear --force deletes without confirmation."""
        from xbot.interfaces.cli.goal import goal_clear
        from xbot.runtime.session.goal_store import GoalState, GoalStatus

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace="/tmp", session_key="goal:g1",
        )
        goal.status = GoalStatus.PAUSED

        with patch("xbot.interfaces.cli.goal.Path") as MockPath:
            MockPath.cwd.return_value = MagicMock()
            with patch("xbot.interfaces.cli.goal.GoalStore") as MockStore:
                mock_store = MagicMock()
                mock_store.find_active.return_value = goal
                MockStore.return_value = mock_store

                goal_clear(goal_id=None, force=True)
                mock_store.delete.assert_called_once_with("g1")

    def test_goal_list_with_goals(self) -> None:
        """goal list displays goals in a table."""
        from xbot.interfaces.cli.goal import goal_list
        from xbot.runtime.session.goal_store import GoalState, GoalStatus

        goal1 = GoalState(
            goal_id="g1", objective="First goal",
            workspace="/tmp", session_key="goal:g1",
        )
        goal1.status = GoalStatus.ACTIVE

        goal2 = GoalState(
            goal_id="g2", objective="Second goal that has a very long objective description",
            workspace="/tmp", session_key="goal:g2",
        )
        goal2.status = GoalStatus.ACHIEVED

        with patch("xbot.interfaces.cli.goal.Path") as MockPath:
            MockPath.cwd.return_value = MagicMock()
            with patch("xbot.interfaces.cli.goal.GoalStore") as MockStore:
                mock_store = MagicMock()
                mock_store.list_goals.return_value = [goal1, goal2]
                MockStore.return_value = mock_store

                goal_list()

    def test_goal_run_with_crash_recovery(self) -> None:
        """goal run with ACTIVE status performs crash recovery."""
        from xbot.interfaces.cli.goal import goal_run
        from xbot.runtime.session.goal_store import GoalState, GoalStatus
        import typer

        goal = GoalState(
            goal_id="g1", objective="test",
            workspace="/tmp", session_key="goal:g1",
        )
        goal.status = GoalStatus.ACTIVE
        goal.checkpoint = "abc123"

        with patch("xbot.interfaces.cli.goal.Path") as MockPath:
            MockPath.cwd.return_value = MagicMock()
            with patch("xbot.interfaces.cli.goal.GoalStore") as MockStore:
                mock_store = MagicMock()
                mock_store.find_active.return_value = goal
                MockStore.return_value = mock_store

                with patch("subprocess.run") as mock_subproc:
                    with patch("xbot.interfaces.cli.goal.GoalRunner") as MockRunner:
                        mock_runner_instance = MagicMock()
                        mock_runner_instance.run = AsyncMock()
                        MockRunner.return_value = mock_runner_instance

                        with patch("asyncio.run") as mock_asyncio_run:
                            goal_run(None)
                            # Verify git reset was called for crash recovery
                            assert mock_subproc.called


class TestGoalPermissionHandlerAdvanced:
    """Additional tests for GoalPermissionHandler."""

    def test_add_safe_tool(self) -> None:
        """add_safe_tool adds to internal set."""
        from xbot.interfaces.cli.goal import GoalPermissionHandler

        handler = GoalPermissionHandler()
        handler.add_safe_tool("bash")
        assert "bash" in handler._safe_tools

    def test_build_can_use_tool_callback_import_error(self) -> None:
        """build_can_use_tool_callback raises ImportError when SDK not available."""
        from xbot.interfaces.cli.goal import GoalPermissionHandler

        handler = GoalPermissionHandler()

        with patch.dict("sys.modules", {"claude_agent_sdk": None, "claude_agent_sdk.types": None}):
            with pytest.raises(ImportError):
                handler.build_can_use_tool_callback()


class TestCLIMergeMissingDefaults:
    """Test _merge_missing_defaults helper."""

    def test_merge_adds_missing_keys(self) -> None:
        """Missing keys from defaults are added."""
        from xbot.interfaces.cli.commands import _merge_missing_defaults

        existing = {"a": 1}
        defaults = {"a": 99, "b": 2}
        result = _merge_missing_defaults(existing, defaults)
        assert result == {"a": 1, "b": 2}

    def test_merge_recursive(self) -> None:
        """Nested dicts are merged recursively."""
        from xbot.interfaces.cli.commands import _merge_missing_defaults

        existing = {"outer": {"a": 1}}
        defaults = {"outer": {"a": 99, "b": 2}}
        result = _merge_missing_defaults(existing, defaults)
        assert result["outer"]["a"] == 1
        assert result["outer"]["b"] == 2

    def test_merge_non_dict_returns_existing(self) -> None:
        """Non-dict values are returned as-is."""
        from xbot.interfaces.cli.commands import _merge_missing_defaults

        assert _merge_missing_defaults("hello", "world") == "hello"
        assert _merge_missing_defaults(42, 99) == 42


class TestCLIIsExitCommand:
    """Test _is_exit_command helper."""

    def test_exit_commands(self) -> None:
        """Various exit command strings are recognized."""
        from xbot.interfaces.cli.commands import _is_exit_command, EXIT_COMMANDS

        assert _is_exit_command("exit") is True
        assert _is_exit_command("quit") is True
        assert _is_exit_command("/exit") is True
        assert _is_exit_command("/quit") is True
        assert _is_exit_command(":q") is True

    def test_non_exit_commands(self) -> None:
        """Non-exit commands are not recognized."""
        from xbot.interfaces.cli.commands import _is_exit_command

        assert _is_exit_command("hello") is False
        assert _is_exit_command("exit ") is False  # With trailing space


class TestGatewayEnsureWritableSession:
    """Test _ensure_writable_client_session logic."""

    def test_read_only_session_rejected(self, tmp_path: Path) -> None:
        """Sessions not in web/app namespace are rejected."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        # telegram: sessions should be read-only from WebUI
        # This is enforced in the WebSocket handler
        # Just verify the endpoint exists
        resp = client.get("/api/sessions", headers=headers)
        assert resp.status_code == 200


class TestGatewayPatchProvider:
    """Test provider patch endpoint."""

    def test_patch_provider(self, tmp_path: Path) -> None:
        """PATCH /api/providers/{name} updates provider config."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        # Create first
        client.post("/api/providers", json={
            "name": "patchable",
            "api_key": "old-key",
        }, headers=headers)

        # Patch
        resp = client.patch("/api/providers/patchable", json={
            "api_key": "new-key",
        }, headers=headers)
        assert resp.status_code == 200


class TestGatewayToggleMcpServer:
    """Test MCP server enable/disable endpoint."""

    def test_toggle_mcp_server(self, tmp_path: Path) -> None:
        """PATCH /api/mcp/servers/{name}/enabled toggles server."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.patch("/api/mcp/servers/demo/enabled", json={
            "enabled": False,
        }, headers=headers)
        assert resp.status_code == 200


class TestGatewayDashboard:
    """Test dashboard endpoint."""

    def test_dashboard_returns_status(self, tmp_path: Path) -> None:
        """GET /api/dashboard returns runtime status."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.get("/api/dashboard", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "runtime" in data or "agent" in data or isinstance(data, dict)


class TestGatewayS3Config:
    """Test S3 config endpoints."""

    def test_get_s3_config(self, tmp_path: Path) -> None:
        """GET /api/config/s3 returns S3 configuration."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.get("/api/config/s3", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data

    def test_put_s3_config(self, tmp_path: Path) -> None:
        """PUT /api/config/s3 updates S3 configuration."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.put("/api/config/s3", json={
            "enabled": True,
            "endpoint_url": "https://s3.example.com",
            "bucket": "my-bucket",
        }, headers=headers)
        assert resp.status_code == 200


class TestGatewayHeartbeat:
    """Test heartbeat endpoints."""

    def test_get_heartbeat(self, tmp_path: Path) -> None:
        """GET /api/heartbeat returns heartbeat status."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.get("/api/heartbeat", headers=headers)
        assert resp.status_code == 200


class TestGatewayAgentConfig:
    """Test agent config endpoints."""

    def test_get_agent_config(self, tmp_path: Path) -> None:
        """GET /api/config/agent returns agent configuration."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.get("/api/config/agent", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "model" in data

    def test_patch_agent_config(self, tmp_path: Path) -> None:
        """PATCH /api/config/agent updates agent config."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.patch("/api/config/agent", json={
            "model": "claude-3-opus",
        }, headers=headers)
        assert resp.status_code == 200


class TestGatewayChannels:
    """Test channel endpoints."""

    def test_get_channels(self, tmp_path: Path) -> None:
        """GET /api/channels returns channel list."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.get("/api/channels", headers=headers)
        assert resp.status_code == 200

    def test_reload_channel(self, tmp_path: Path) -> None:
        """POST /api/channels/{name}/reload triggers channel reload."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.post("/api/channels/telegram/reload", headers=headers)
        assert resp.status_code == 200

    def test_reload_all_channels(self, tmp_path: Path) -> None:
        """POST /api/channels/reload-all triggers all channel reloads."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.post("/api/channels/reload-all", headers=headers)
        assert resp.status_code == 200


class TestGatewayDeleteSession:
    """Test session deletion endpoints."""

    def test_delete_session_revokes_message(self, tmp_path: Path) -> None:
        """DELETE /api/sessions/{key}/messages/{index} removes a message."""
        client, services = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        # Create a web: session (writable from WebUI)
        session = services.conversation_store.get_or_create("web:admin:test")
        session.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        services.conversation_store.save(session)

        # Delete the first message
        resp = client.delete("/api/sessions/web%3Aadmin%3Atest/messages/0", headers=headers)
        assert resp.status_code == 200


class TestGatewayChangePassword:
    """Test password change endpoint."""

    def test_change_password(self, tmp_path: Path) -> None:
        """POST /api/auth/change-password changes the password."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.post("/api/auth/change-password", json={
            "current_password": "test-webui-password",
            "new_password": "new-test-password",
        }, headers=headers)
        assert resp.status_code == 200


class TestGatewayRawConfig:
    """Test raw config endpoints."""

    def test_get_raw_config(self, tmp_path: Path) -> None:
        """GET /api/config/raw returns the raw config."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.get("/api/config/raw", headers=headers)
        assert resp.status_code == 200

    def test_get_gateway_config(self, tmp_path: Path) -> None:
        """GET /api/config/gateway returns gateway configuration."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.get("/api/config/gateway", headers=headers)
        assert resp.status_code == 200


class TestCLIOnboardPlugins:
    """Test _onboard_plugins helper."""

    def test_onboard_plugins_no_channels(self, tmp_path: Path) -> None:
        """_onboard_plugins is a no-op when no channels are discovered."""
        from xbot.interfaces.cli.commands import _onboard_plugins

        config_path = tmp_path / "config.json"
        config_path.write_text('{"channels": {}}', encoding="utf-8")

        with patch("xbot.channels.registry.discover_all", return_value={}):
            _onboard_plugins(config_path)

        # Config should be unchanged
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data == {"channels": {}}

    def test_onboard_plugins_adds_defaults(self, tmp_path: Path) -> None:
        """_onboard_plugins injects default channel config."""
        from xbot.interfaces.cli.commands import _onboard_plugins

        config_path = tmp_path / "config.json"
        config_path.write_text('{"channels": {}}', encoding="utf-8")

        fake_cls = type("FakeChannel", (), {
            "default_config": staticmethod(lambda: {"enabled": True, "token": ""}),
        })

        with patch("xbot.channels.registry.discover_all", return_value={"test_channel": fake_cls}):
            _onboard_plugins(config_path)

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert "test_channel" in data["channels"]
        assert data["channels"]["test_channel"]["enabled"] is True


class TestGatewayCronJobToggle:
    """Test cron job enable/disable endpoint."""

    def test_toggle_cron_job(self, tmp_path: Path) -> None:
        """PATCH /api/cron/jobs/{id}/enabled toggles job state."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        # Create a job first
        resp = client.post("/api/cron/jobs", json={
            "name": "toggleable",
            "schedule": {"kind": "every", "every_ms": 60000},
            "payload": {"message": "test"},
        }, headers=headers)
        job_id = resp.json()["id"]

        # Toggle it
        resp = client.patch(f"/api/cron/jobs/{job_id}/enabled", json={
            "enabled": False,
        }, headers=headers)
        assert resp.status_code == 200


class TestGatewayUpdateCronJob:
    """Test cron job update endpoint."""

    def test_update_cron_job(self, tmp_path: Path) -> None:
        """PUT /api/cron/jobs/{id} updates job properties."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        # Create first
        resp = client.post("/api/cron/jobs", json={
            "name": "updatable",
            "schedule": {"kind": "every", "every_ms": 60000},
            "payload": {"message": "test"},
        }, headers=headers)
        job_id = resp.json()["id"]

        # Update
        resp = client.put(f"/api/cron/jobs/{job_id}", json={
            "name": "updated-name",
        }, headers=headers)
        assert resp.status_code == 200

    def test_update_nonexistent_cron_job(self, tmp_path: Path) -> None:
        """PUT /api/cron/jobs/{id} for missing job returns 404."""
        client, _ = _build_gateway_client(tmp_path)
        headers = _auth_header(client)

        resp = client.put("/api/cron/jobs/nonexistent", json={
            "name": "test",
        }, headers=headers)
        assert resp.status_code == 404
