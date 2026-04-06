"""FastAPI application for the xbot WebUI adapter."""

from __future__ import annotations

import json
import re
import secrets
import shutil
import unicodedata
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    File,
    Header,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from xbot.config.schema import MCPServerConfig
from xbot.cron.types import CronPayload, CronSchedule

# ---------------------------------------------------------------------------
# Security: Name validation for skills and MCP servers
# ---------------------------------------------------------------------------

# Valid name pattern: alphanumeric, dashes, underscores. Must start with letter/number.
# Max 64 characters.
_VALID_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


def validate_safe_name(name: str, field_name: str = "name") -> str:
    """Validate that a name is safe for filesystem use.

    Security: Prevents path traversal attacks via:
    - Unicode normalization (handles ..%2f, fullwidth slashes, etc.)
    - Rejecting path separators (/ \\)
    - Rejecting path traversal sequences (..)
    - Enforcing alphanumeric + dash/underscore only

    Args:
        name: The name to validate
        field_name: Field name for error messages

    Returns:
        The validated, normalized name

    Raises:
        HTTPException: If the name is invalid
    """
    # Unicode normalization (NFKC) to prevent encoding attacks
    normalized = unicodedata.normalize("NFKC", name)

    # Check for path separators
    if "/" in normalized or "\\" in normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name}: cannot contain path separators",
        )

    # Check for path traversal
    if ".." in normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name}: cannot contain '..' sequences",
        )

    # Check against pattern
    if not _VALID_NAME_PATTERN.match(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid {field_name}: use only letters, numbers, dashes, and underscores. "
                f"Must start with a letter or number. Max 64 characters. "
                f"Example: my-skill-name"
            ),
        )

    return normalized
from xbot.webui.auth import AuthManager, UserStore, ensure_password_file, print_password_banner
from xbot.webui.services import ServiceContainer
from xbot.webui.session_keys import to_internal_session_key


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AgentConfigPatch(BaseModel):
    model: str | None = None
    provider: str | None = None
    workspace: str | None = None
    send_progress: bool | None = None
    send_tool_hints: bool | None = None


class CronPayloadModel(BaseModel):
    kind: str = "agent_turn"
    message: str
    deliver: bool = False
    channel: str | None = None
    to: str | None = None


class CronScheduleModel(BaseModel):
    kind: str
    at_ms: int | None = Field(default=None, alias="at_ms")
    every_ms: int | None = Field(default=None, alias="every_ms")
    expr: str | None = None
    tz: str | None = None


class CronJobCreate(BaseModel):
    name: str
    enabled: bool = True
    schedule: CronScheduleModel
    payload: CronPayloadModel
    delete_after_run: bool = False


class SkillCreate(BaseModel):
    name: str
    content: str


class SkillUpdate(BaseModel):
    content: str


class GatewayConfigPatch(BaseModel):
    host: str | None = None
    port: int | None = None
    heartbeat_enabled: bool | None = None
    heartbeat_interval_s: int | None = None


class ChannelsPatch(BaseModel):
    send_progress: bool | None = None
    send_tool_hints: bool | None = None
    channels: dict[str, dict[str, Any]] = Field(default_factory=dict)


class MCPPatch(BaseModel):
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    type: str | None = None
    tool_timeout: int = 30
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"])


class HeartbeatPatch(BaseModel):
    enabled: bool | None = None
    interval_s: int | None = None


class ProviderPatch(BaseModel):
    api_key: str | None = None
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None


class EnabledPatch(BaseModel):
    enabled: bool


class S3ConfigPatch(BaseModel):
    enabled: bool = False
    endpoint_url: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    bucket: str = ""
    region: str = ""
    public_base_url: str = ""


def _mask_secret(value: Any) -> str:
    raw = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value or "")
    if not raw:
        return ""
    return "••••" if len(raw) <= 4 else f"••••{raw[-4:]}"


def _serialize_cron_job(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "name": job.name,
        "enabled": job.enabled,
        "schedule": {
            "kind": job.schedule.kind,
            "at_ms": job.schedule.at_ms,
            "every_ms": job.schedule.every_ms,
            "expr": job.schedule.expr,
            "tz": job.schedule.tz,
        },
        "payload": {
            "kind": job.payload.kind,
            "message": job.payload.message,
            "deliver": job.payload.deliver,
            "channel": job.payload.channel,
            "to": job.payload.to,
        },
        "state": {
            "next_run_at_ms": job.state.next_run_at_ms,
            "last_run_at_ms": job.state.last_run_at_ms,
            "last_status": job.state.last_status,
            "last_error": job.state.last_error,
        },
    }


def _serialize_skill(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item["name"],
        "path": item["path"],
        "source": item["source"],
        "type": item["type"],
    }


def _serialize_skill_info(container: ServiceContainer, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item["name"],
        "source": item["source"],
        "path": item["path"],
        "description": item["name"],
        "available": True,
        "enabled": True,
        "unavailable_reason": None,
        "type": item["type"],
    }


def _mcp_config_dict(config: Any) -> dict[str, Any]:
    if hasattr(config, "model_dump"):
        return config.model_dump(by_alias=True)
    if isinstance(config, dict):
        return dict(config)
    return {}


def _json_default(value: Any) -> str:
    if hasattr(value, "get_secret_value"):
        return value.get_secret_value()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _sanitize_public_config(value: Any, *, field_name: str = "") -> Any:
    sensitive_markers = ("token", "secret", "password", "key")
    if isinstance(value, dict):
        return {
            key: _sanitize_public_config(item, field_name=key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_public_config(item, field_name=field_name) for item in value]
    if isinstance(value, str) and any(marker in field_name.lower() for marker in sensitive_markers):
        return _mask_secret(value)
    return value


async def _safe_websocket_send_json(websocket: WebSocket, payload: dict[str, Any]) -> bool:
    try:
        await websocket.send_json(payload)
        return True
    except WebSocketDisconnect:
        return False
    except RuntimeError as exc:
        if "close message has been sent" in str(exc):
            return False
        raise


def create_app(
    container: ServiceContainer,
    *,
    data_dir: Path | None = None,
    frontend_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="xbot WebUI", version="0.1.0")

    resolved_data_dir = (data_dir or container.data_dir or (container.config.workspace_path / ".webui")).resolve()
    resolved_data_dir.mkdir(parents=True, exist_ok=True)
    container.data_dir = resolved_data_dir
    app.state.services = container
    app.state.user_store = UserStore(resolved_data_dir / "users.json")

    # Ensure password is initialized (generates on first run)
    generated_password = ensure_password_file()
    if generated_password:
        print_password_banner(generated_password)

    app.state.auth = AuthManager(secrets.token_hex(32))
    app.state.frontend_dir = frontend_dir or (Path(__file__).parent / "frontend" / "dist")
    app.state.s3_config_path = resolved_data_dir / "s3.json"
    default_s3_config = {
        "enabled": False,
        "endpoint_url": "",
        "access_key_id": "",
        "secret_access_key": "",
        "bucket": "",
        "region": "",
        "public_base_url": "",
    }
    if app.state.s3_config_path.exists():
        try:
            app.state.s3_config = {
                **default_s3_config,
                **json.loads(app.state.s3_config_path.read_text(encoding="utf-8")),
            }
        except (json.JSONDecodeError, OSError):
            app.state.s3_config = dict(default_s3_config)
    else:
        app.state.s3_config = dict(default_s3_config)

    def _get_user_from_auth_header(authorization: str | None) -> dict[str, Any]:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
        token = authorization.removeprefix("Bearer ").strip()
        payload = app.state.auth.decode_token(token)
        return {
            "id": payload["sub"],
            "username": payload["username"],
            "role": payload["role"],
        }

    async def _current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return _get_user_from_auth_header(authorization)

    def _frontend_index_response() -> HTMLResponse | None:
        resolved_frontend = Path(app.state.frontend_dir)
        index_file = resolved_frontend / "index.html"
        if index_file.exists():
            return HTMLResponse(index_file.read_text(encoding="utf-8"))
        return None

    resolved_frontend = Path(app.state.frontend_dir)
    assets_dir = resolved_frontend / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    if resolved_frontend.exists():
        app.mount("/dist", StaticFiles(directory=str(resolved_frontend)), name="dist")
        for static_file in resolved_frontend.iterdir():
            if static_file.is_file() and static_file.name != "index.html":
                route_path = f"/{static_file.name}"
                static_path = static_file

                @app.get(route_path, include_in_schema=False)
                async def _serve_static(_path: Path = static_path) -> FileResponse:
                    return FileResponse(_path)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        frontend = _frontend_index_response()
        if frontend is not None:
            return frontend.body.decode("utf-8")
        return """
        <!doctype html>
        <html lang="en">
          <head><meta charset="utf-8"><title>xbot WebUI</title></head>
          <body><main><h1>xbot WebUI</h1><p>Adapter service is running.</p></main></body>
        </html>
        """

    @app.post("/api/auth/login")
    async def login(body: LoginRequest) -> dict[str, Any]:
        user = app.state.user_store.authenticate(body.username, body.password)
        return {
            "access_token": app.state.auth.issue_token(user),
            "token_type": "bearer",
            "user": user,
        }

    @app.post("/api/auth/change-password")
    async def change_password(
        body: ChangePasswordRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _get_user_from_auth_header(authorization)
        app.state.user_store.change_password(body.current_password, body.new_password)
        return {"ok": True}

    @app.put("/api/auth/password")
    async def change_password_compat(
        body: ChangePasswordRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        return await change_password(body=body, authorization=authorization)

    @app.get("/api/dashboard")
    async def dashboard(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        sessions = container.session_manager.list_sessions()
        skills = container.list_skills()
        channels = _channels_payload()
        cron_status = container.cron.status() if hasattr(container.cron, "status") else {"jobs": 0}
        return {
            "runtime": container.runtime_status(),
            "counts": {
                "sessions": len(sessions),
                "skills": len(skills),
                "channels": len(channels["channels"]),
                "cron_jobs": int(cron_status.get("jobs", 0)),
            },
            "heartbeat": container.heartbeat_status(),
        }

    @app.get("/api/sessions")
    async def list_sessions(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
        _get_user_from_auth_header(authorization)
        return container.session_manager.list_sessions()

    @app.get("/api/sessions/{session_key:path}/messages")
    async def get_session_messages(
        session_key: str,
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        _get_user_from_auth_header(authorization)
        session = container.session_manager.get_or_create(session_key)
        return session.messages

    @app.get("/api/sessions/{session_key:path}/memory")
    async def get_session_memory(
        session_key: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, str]:
        _get_user_from_auth_header(authorization)
        session_key  # reserved for future per-session memory lookup
        workspace = container.config.workspace_path
        memory_file = workspace / "MEMORY.md"
        history_file = workspace / "HISTORY.md"
        return {
            "memory": memory_file.read_text(encoding="utf-8") if memory_file.exists() else "",
            "history": history_file.read_text(encoding="utf-8") if history_file.exists() else "",
        }

    @app.delete("/api/sessions/{session_key:path}/messages/{index}")
    async def revoke_session_message(
        session_key: str,
        index: int,
        authorization: str | None = Header(default=None),
    ) -> dict[str, int]:
        from datetime import datetime

        _get_user_from_auth_header(authorization)
        session = container.session_manager.get_or_create(session_key)
        if index < 0 or index >= len(session.messages):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message index")
        del session.messages[index]
        session.updated_at = datetime.now()
        container.session_manager.save(session)
        return {"removed": 1}

    @app.delete("/api/sessions/{session_key:path}")
    async def delete_session(session_key: str, authorization: str | None = Header(default=None)) -> dict[str, bool]:
        _get_user_from_auth_header(authorization)
        return {"ok": container.session_manager.delete(session_key)}

    @app.get("/api/providers")
    async def providers(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
        _get_user_from_auth_header(authorization)
        provider_list = []
        for name in type(container.config.providers).model_fields:
            value = getattr(container.config.providers, name)
            raw_key = value.api_key.get_secret_value() if hasattr(value.api_key, "get_secret_value") else ""
            provider_list.append({
                "name": name,
                "api_key_masked": _mask_secret(value.api_key),
                "api_base": value.api_base,
                "extra_headers": value.extra_headers,
                "has_key": bool(raw_key),
                "models": [],
                "is_custom": name == "custom",
            })
        return provider_list

    @app.patch("/api/providers/{provider_name}")
    async def patch_provider(
        provider_name: str,
        body: ProviderPatch,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        provider = getattr(container.config.providers, provider_name, None)
        if provider is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
        if body.api_key is not None:
            provider.api_key = SecretStr(body.api_key)
        if body.api_base is not None:
            provider.api_base = body.api_base
        if body.extra_headers is not None:
            provider.extra_headers = body.extra_headers
        container.persist_config()
        return {"ok": True}

    def _channels_payload() -> dict[str, Any]:
        raw = container.config.channels.model_dump(by_alias=True)
        base_keys = {"sendProgress", "sendToolHints", "sendUsageSummary"}
        channels = {k: v for k, v in raw.items() if k not in base_keys}
        extras = container.config.channels.model_extra or {}
        channels.update(extras)
        return {
            "channels": channels,
            "settings": {
                "send_progress": container.config.channels.send_progress,
                "send_tool_hints": container.config.channels.send_tool_hints,
                "send_usage_summary": container.config.channels.send_usage_summary,
            },
        }

    @app.get("/api/channels")
    async def channels(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
        _get_user_from_auth_header(authorization)
        payload = _channels_payload()
        runtime_status = container.channel_runtime_status()
        return [
            {
                "name": name,
                "enabled": bool(runtime_status.get(name, {}).get("enabled", config.get("enabled", False))) if isinstance(config, dict) else bool(runtime_status.get(name, {}).get("enabled", False)),
                "running": bool(runtime_status.get(name, {}).get("running", False)),
                "error": runtime_status.get(name, {}).get("error"),
                "config": _sanitize_public_config(config),
            }
            for name, config in payload["channels"].items()
        ]

    @app.patch("/api/channels")
    async def patch_channels(
        body: ChannelsPatch,
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        _get_user_from_auth_header(authorization)
        if body.send_progress is not None:
            container.config.channels.send_progress = body.send_progress
        if body.send_tool_hints is not None:
            container.config.channels.send_tool_hints = body.send_tool_hints
        for name, channel_config in body.channels.items():
            setattr(container.config.channels, name, channel_config)
        container.persist_config()
        return await channels(authorization=authorization)

    @app.patch("/api/channels/{channel_name}")
    async def patch_channel(
        channel_name: str,
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        existing = getattr(container.config.channels, channel_name, {})
        merged = dict(existing or {})
        merged.update(body)
        setattr(container.config.channels, channel_name, merged)
        container.persist_config()
        return {"name": channel_name, "config": merged}

    @app.post("/api/channels/{channel_name}/reload")
    async def reload_channel(
        channel_name: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        try:
            result = container.reload_channel(channel_name)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"name": channel_name, "error": str(exc)},
            ) from exc
        return result

    @app.post("/api/channels/reload-all")
    async def reload_all_channels(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        try:
            return container.reload_all_channels()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"name": "*", "error": str(exc)},
            ) from exc

    @app.get("/api/mcp")
    async def mcp_servers(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        return {
            "servers": {
                name: _mcp_config_dict(config)
                for name, config in container.config.tools.mcp_servers.items()
            }
        }

    @app.get("/api/mcp/servers")
    async def list_mcp_servers(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
        _get_user_from_auth_header(authorization)
        return [
            {"name": name, **_mcp_config_dict(config)}
            for name, config in container.config.tools.mcp_servers.items()
        ]

    @app.post("/api/mcp/servers/{server_name}")
    async def create_mcp_server(
        server_name: str,
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        # Security: Validate server name to prevent injection attacks
        safe_name = validate_safe_name(server_name, "server name")
        data = dict(body)
        data.pop("name", None)
        container.config.tools.mcp_servers[safe_name] = MCPServerConfig.model_validate(data)
        container.persist_config()
        return {"name": safe_name}

    @app.put("/api/mcp/servers/{server_name}")
    async def update_mcp_server(
        server_name: str,
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        # Security: Validate server name
        safe_name = validate_safe_name(server_name, "server name")
        existing = _mcp_config_dict(container.config.tools.mcp_servers.get(safe_name, {}))
        existing.update(body)
        existing.pop("name", None)
        container.config.tools.mcp_servers[safe_name] = MCPServerConfig.model_validate(existing)
        container.persist_config()
        return {"name": safe_name}

    @app.delete("/api/mcp/servers/{server_name}")
    async def delete_mcp_server(
        server_name: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _get_user_from_auth_header(authorization)
        # Security: Validate server name
        safe_name = validate_safe_name(server_name, "server name")
        container.config.tools.mcp_servers.pop(safe_name, None)
        container.persist_config()
        return {"ok": True}

    @app.get("/api/mcp/servers/runtime")
    async def list_mcp_runtime(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
        _get_user_from_auth_header(authorization)
        runtime_status = container.mcp_runtime_status()
        return [
            {
                "name": name,
                "running": bool(runtime_status.get(name, {}).get("running", False)),
                "enabled": bool(runtime_status.get(name, {}).get("enabled", _mcp_config_dict(container.config.tools.mcp_servers[name]).get("enabled", True))),
                "tools": list(runtime_status.get(name, {}).get("tools", [])),
                "tool_count": int(runtime_status.get(name, {}).get("tool_count", 0)),
                "transport": str(runtime_status.get(name, {}).get("transport", "unknown")),
                "error": runtime_status.get(name, {}).get("error"),
            }
            for name in container.config.tools.mcp_servers
        ]

    @app.patch("/api/mcp/servers/{server_name}/enabled")
    async def toggle_mcp_server(
        server_name: str,
        body: EnabledPatch,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        existing = _mcp_config_dict(container.config.tools.mcp_servers.get(server_name, {}))
        existing["enabled"] = body.enabled
        container.config.tools.mcp_servers[server_name] = MCPServerConfig.model_validate(existing)
        container.persist_config()
        return {"name": server_name, "enabled": body.enabled}

    @app.patch("/api/mcp/{server_name}")
    async def patch_mcp_server(
        server_name: str,
        body: MCPPatch,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        container.config.tools.mcp_servers[server_name] = MCPServerConfig.model_validate(body.model_dump())
        container.persist_config()
        server = container.config.tools.mcp_servers[server_name]
        return {"name": server_name, "config": server.model_dump(by_alias=True)}

    @app.get("/api/heartbeat")
    async def heartbeat(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        return container.heartbeat_status()

    @app.patch("/api/heartbeat")
    async def patch_heartbeat(
        body: HeartbeatPatch,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        if body.enabled is not None:
            container.config.gateway.heartbeat.enabled = body.enabled
            container.heartbeat.enabled = body.enabled
        if body.interval_s is not None:
            container.config.gateway.heartbeat.interval_s = body.interval_s
            container.heartbeat.interval_s = body.interval_s
        container.persist_config()
        return container.heartbeat_status()

    @app.get("/api/config/agent")
    async def get_agent_config(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        defaults = container.config.agents.defaults
        return {
            "model": defaults.model,
            "provider": defaults.provider,
            "workspace": defaults.workspace,
            "send_progress": container.config.channels.send_progress,
            "send_tool_hints": container.config.channels.send_tool_hints,
        }

    @app.patch("/api/config/agent")
    async def patch_agent_config(
        body: AgentConfigPatch,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        defaults = container.config.agents.defaults
        if body.model is not None:
            defaults.model = body.model
            if hasattr(container.agent, "model"):
                container.agent.model = body.model
        if body.provider is not None:
            defaults.provider = body.provider
        if body.workspace is not None:
            defaults.workspace = body.workspace
        if body.send_progress is not None:
            container.config.channels.send_progress = body.send_progress
        if body.send_tool_hints is not None:
            container.config.channels.send_tool_hints = body.send_tool_hints
        container.persist_config()
        return await get_agent_config(authorization=authorization)

    @app.get("/api/config/gateway")
    async def get_gateway_config(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        gateway = container.config.gateway
        return {
            "host": gateway.host,
            "port": gateway.port,
            "heartbeat_enabled": gateway.heartbeat.enabled,
            "heartbeat_interval_s": gateway.heartbeat.interval_s,
        }

    @app.patch("/api/config/gateway")
    async def patch_gateway_config(
        body: GatewayConfigPatch,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        gateway = container.config.gateway
        if body.host is not None:
            gateway.host = body.host
        if body.port is not None:
            gateway.port = body.port
        if body.heartbeat_enabled is not None:
            gateway.heartbeat.enabled = body.heartbeat_enabled
            container.heartbeat.enabled = body.heartbeat_enabled
        if body.heartbeat_interval_s is not None:
            gateway.heartbeat.interval_s = body.heartbeat_interval_s
            container.heartbeat.interval_s = body.heartbeat_interval_s
        container.persist_config()
        return await get_gateway_config(authorization=authorization)

    @app.get("/api/config/workspace-file/{name}")
    async def get_workspace_file(
        name: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, str]:
        _get_user_from_auth_header(authorization)
        path = container.config.workspace_path / name
        return {"name": name, "content": path.read_text(encoding="utf-8") if path.exists() else ""}

    @app.put("/api/config/workspace-file/{name}")
    async def put_workspace_file(
        name: str,
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, str]:
        _get_user_from_auth_header(authorization)
        path = container.config.workspace_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        content = str(body.get("content", ""))
        path.write_text(content, encoding="utf-8")
        return {"name": name, "content": content}

    @app.get("/api/config/raw")
    async def get_raw_config(authorization: str | None = Header(default=None)) -> dict[str, str]:
        _get_user_from_auth_header(authorization)
        return {
            "content": json.dumps(
                container.config.model_dump(by_alias=True),
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            )
        }

    @app.put("/api/config/raw")
    async def put_raw_config(
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        from xbot.config.schema import Config

        _get_user_from_auth_header(authorization)
        content = str(body.get("content", ""))
        container.config = Config.model_validate(json.loads(content))
        container.persist_config()
        return {"ok": True}

    @app.get("/api/config/logs")
    async def get_logs(
        authorization: str | None = Header(default=None),
        lines: int = 500,
        keyword: str = "",
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        logs_dir = (container.data_dir or container.config.workspace_path) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_files = sorted(
            [path for path in logs_dir.iterdir() if path.is_file()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not log_files:
            return {"content": "", "path": str(logs_dir)}

        selected_file = log_files[0]
        all_lines = selected_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        if keyword:
            lowered = keyword.lower()
            all_lines = [line for line in all_lines if lowered in line.lower()]
        if lines > 0:
            all_lines = all_lines[-lines:]
        return {"content": "\n".join(all_lines), "path": str(selected_file)}

    @app.get("/api/config/s3")
    async def get_s3_config(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        return dict(app.state.s3_config)

    @app.put("/api/config/s3")
    async def put_s3_config(
        body: S3ConfigPatch,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        app.state.s3_config = body.model_dump()
        app.state.s3_config_path.write_text(
            json.dumps(app.state.s3_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return dict(app.state.s3_config)

    @app.get("/api/config/workspace/export")
    async def export_workspace(authorization: str | None = Header(default=None)) -> StreamingResponse:
        _get_user_from_auth_header(authorization)
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(container.config.workspace_path.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(container.config.workspace_path))
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="xbot-workspace.zip"'},
        )

    @app.post("/api/config/workspace/import")
    async def import_workspace(
        file: UploadFile = File(...),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        if not (file.filename or "").endswith(".zip"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .zip files are accepted")
        data = await file.read()
        backups_dir = (container.data_dir or container.config.workspace_path) / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backups_dir / f"workspace-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as backup_zip:
            for path in sorted(container.config.workspace_path.rglob("*")):
                if path.is_file():
                    backup_zip.write(path, path.relative_to(container.config.workspace_path))
        workspace_path = container.config.workspace_path
        workspace_parent = workspace_path.parent
        extracted_path = workspace_parent / f".workspace-import-{secrets.token_hex(8)}"
        staged_old_path = workspace_parent / f".workspace-import-old-{secrets.token_hex(8)}"
        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            for member in zf.namelist():
                target = (workspace_path / member).resolve()
                if not str(target).startswith(str(workspace_path.resolve())):
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid path in archive: {member}")
            extracted_path.mkdir(parents=True, exist_ok=True)
            try:
                zf.extractall(extracted_path)
                webui_dir = workspace_path / ".webui"
                extracted_webui_dir = extracted_path / ".webui"
                if webui_dir.exists() and not extracted_webui_dir.exists():
                    shutil.copytree(webui_dir, extracted_webui_dir)

                workspace_path.rename(staged_old_path)
                extracted_path.rename(workspace_path)
            except Exception:
                if staged_old_path.exists() and not workspace_path.exists():
                    staged_old_path.rename(workspace_path)
                if extracted_path.exists():
                    shutil.rmtree(extracted_path, ignore_errors=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to import workspace",
                )
            else:
                if staged_old_path.exists():
                    shutil.rmtree(staged_old_path, ignore_errors=True)
        return {"ok": True, "backup": str(backup_path)}

    @app.get("/api/cron/jobs")
    async def list_cron_jobs(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
        _get_user_from_auth_header(authorization)
        jobs = container.cron.list_jobs() if hasattr(container.cron, "list_jobs") else []
        return [_serialize_cron_job(job) for job in jobs]

    @app.post("/api/cron/jobs", status_code=201)
    async def create_cron_job(
        body: CronJobCreate,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        job = container.cron.add_job(
            name=body.name,
            schedule=CronSchedule(
                kind=body.schedule.kind,
                at_ms=body.schedule.at_ms,
                every_ms=body.schedule.every_ms,
                expr=body.schedule.expr,
                tz=body.schedule.tz,
            ),
            message=body.payload.message,
            deliver=body.payload.deliver,
            channel=body.payload.channel,
            to=body.payload.to,
            delete_after_run=body.delete_after_run,
        )
        if not body.enabled and hasattr(container.cron, "enable_job"):
            maybe_job = container.cron.enable_job(job.id, enabled=False)
            if maybe_job is not None:
                job = maybe_job
        return _serialize_cron_job(job)

    @app.put("/api/cron/jobs/{job_id}")
    async def update_cron_job(
        job_id: str,
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        existing = container.cron.get_job(job_id)
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        updates = {}
        if "name" in body:
            updates["name"] = body["name"]
        if "enabled" in body:
            updates["enabled"] = body["enabled"]
        if "schedule" in body:
            schedule = body["schedule"]
            updates["schedule"] = CronSchedule(
                kind=schedule["kind"],
                at_ms=schedule.get("at_ms"),
                every_ms=schedule.get("every_ms"),
                expr=schedule.get("expr"),
                tz=schedule.get("tz"),
            )
        if "payload" in body:
            payload = body["payload"]
            updates["payload"] = CronPayload(
                kind=payload.get("kind", "agent_turn"),
                message=payload.get("message", ""),
                deliver=payload.get("deliver", False),
                channel=payload.get("channel"),
                to=payload.get("to"),
            )
        job = container.cron.update_job(job_id, **updates)
        return _serialize_cron_job(job)

    @app.delete("/api/cron/jobs/{job_id}")
    async def delete_cron_job(
        job_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _get_user_from_auth_header(authorization)
        return {"ok": container.cron.delete_job(job_id)}

    @app.patch("/api/cron/jobs/{job_id}/enabled")
    async def toggle_cron_job(
        job_id: str,
        body: EnabledPatch,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        job = container.cron.enable_job(job_id, enabled=body.enabled)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        return _serialize_cron_job(job)

    @app.get("/api/skills")
    async def list_skills(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
        _get_user_from_auth_header(authorization)
        return [_serialize_skill_info(container, item) for item in container.list_skills()]

    @app.post("/api/skills", status_code=201)
    async def create_skill(
        body: SkillCreate,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        # Security: Validate skill name to prevent path traversal
        safe_name = validate_safe_name(body.name, "skill name")
        skill_file = container.config.workspace_path / "skills" / safe_name / "SKILL.md"
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(body.content, encoding="utf-8")
        return {"name": safe_name, "content": body.content}

    @app.get("/api/skills/{skill_name}")
    async def get_skill(skill_name: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        loader = container.list_skills()
        item = next((entry for entry in loader if entry["name"] == skill_name), None)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
        skill_file = Path(item["path"])
        return {"name": skill_name, "content": skill_file.read_text(encoding="utf-8")}

    @app.post("/api/skills/{skill_name}/toggle")
    async def toggle_skill(
        skill_name: str,
        body: EnabledPatch,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        return {"name": skill_name, "enabled": body.enabled}

    @app.put("/api/skills/{skill_name}")
    async def update_skill(
        skill_name: str,
        body: SkillUpdate,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        skill_file = container.config.workspace_path / "skills" / skill_name / "SKILL.md"
        if not skill_file.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
        skill_file.write_text(body.content, encoding="utf-8")
        return {"name": skill_name, "content": body.content}

    @app.delete("/api/skills/{skill_name}")
    async def delete_skill(
        skill_name: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        import shutil

        _get_user_from_auth_header(authorization)
        skill_dir = container.config.workspace_path / "skills" / skill_name
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        return {"ok": True}

    @app.get("/api/users")
    async def list_users(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
        user = _get_user_from_auth_header(authorization)
        return [user]

    @app.post("/api/users")
    async def create_user(
        authorization: str | None = Header(default=None),
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        del body
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Single-admin mode only")

    @app.websocket("/ws/chat")
    async def ws_chat(websocket: WebSocket) -> None:
        token = websocket.query_params.get("token", "")
        payload = app.state.auth.decode_token(token)
        session_key = websocket.query_params.get("session") or f"web:{payload['sub']}:default"
        await websocket.accept()
        if not await _safe_websocket_send_json(websocket, {"type": "session_info", "session_key": session_key}):
            return

        try:
            while True:
                message = await websocket.receive_json()
                if message.get("type") != "message":
                    continue
                content = message.get("content", "")

                async def _on_progress(
                    text: str,
                    *,
                    tool_hint: bool = False,
                    event_type: str = "progress",
                    event_data: dict[str, Any] | None = None,
                ) -> None:
                    await _safe_websocket_send_json(websocket, {
                        "type": "progress",
                        "content": text,
                        "tool_hint": tool_hint,
                        "event_type": event_type,
                        "event_data": event_data,
                        "session_key": session_key,
                    })

                response = await container.agent.process_managed_direct(
                    content=content,
                    session_key=to_internal_session_key(session_key),
                    channel="web",
                    chat_id=payload["sub"],
                    on_progress=_on_progress,
                )
                if not await _safe_websocket_send_json(websocket, {
                    "type": "done",
                    "content": response,
                    "session_key": session_key,
                }):
                    return
        except WebSocketDisconnect:
            return

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def spa_fallback(full_path: str) -> str:
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        frontend = _frontend_index_response()
        if frontend is not None:
            return frontend.body.decode("utf-8")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return app
