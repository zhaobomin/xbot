"""FastAPI application for the xbot WebUI adapter."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import shutil
import unicodedata
import zipfile
from collections import OrderedDict
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import (
    FastAPI,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from xbot import __version__
from xbot.interfaces.gateway.auth import (
    AuthManager,
    UserStore,
    ensure_password_file,
    get_or_create_jwt_secret,
    print_password_banner,
)
from xbot.interfaces.gateway.services import ServiceContainer
from xbot.interfaces.gateway.session_keys import (
    runtime_route_from_session_key,
    to_internal_session_key,
)
from xbot.platform.config.schema import MCPServerConfig, ProviderConfig
from xbot.platform.logging.core import get_logger
from xbot.runtime.system.cron.types import CronPayload, CronSchedule

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Security: Name validation for skills and MCP servers
# ---------------------------------------------------------------------------

# Valid name pattern: alphanumeric, dashes, underscores. Must start with letter/number.
# Max 64 characters.
_VALID_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_WS_CHAT_MAX_CONTENT_CHARS = 1_000_000


def validate_safe_name(name: str, field_name: str = "name", *, allow_dots: bool = False) -> str:
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
    pattern = _VALID_NAME_PATTERN if not allow_dots else re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
    if not pattern.match(normalized):
        allowed = "letters, numbers, dashes, and underscores"
        if allow_dots:
            allowed = "letters, numbers, dots, dashes, and underscores"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid {field_name}: use only {allowed}. "
                f"Must start with a letter or number. Max 64 characters. "
                f"Example: my-skill-name"
            ),
        )

    return normalized

# ---------------------------------------------------------------------------
# Security: Login rate limiting
# ---------------------------------------------------------------------------

_LOGIN_ATTEMPTS: OrderedDict[str, list[datetime]] = OrderedDict()
_MAX_ATTEMPTS_PER_IP = 5
_ATTEMPT_WINDOW_SECONDS = 60
_MAX_TRACKED_IPS = 10_000


def _active_login_attempts(client_ip: str) -> list[datetime]:
    now = datetime.now(UTC)
    attempts = _LOGIN_ATTEMPTS.get(client_ip, [])
    return [a for a in attempts if now - a < timedelta(seconds=_ATTEMPT_WINDOW_SECONDS)]


def _check_login_rate_limit(client_ip: str) -> None:
    """Check if IP has exceeded failed-login rate limit."""
    attempts = _active_login_attempts(client_ip)
    if len(attempts) >= _MAX_ATTEMPTS_PER_IP:
        _LOGIN_ATTEMPTS[client_ip] = attempts
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many login attempts. Try again in {_ATTEMPT_WINDOW_SECONDS} seconds.",
        )


def _record_failed_login(client_ip: str) -> None:
    """Record one failed login attempt for rate limiting."""
    attempts = _active_login_attempts(client_ip)
    attempts.append(datetime.now(UTC))
    _LOGIN_ATTEMPTS[client_ip] = attempts
    _LOGIN_ATTEMPTS.move_to_end(client_ip)

    while len(_LOGIN_ATTEMPTS) > _MAX_TRACKED_IPS:
        _LOGIN_ATTEMPTS.popitem(last=False)


def _clear_failed_logins(client_ip: str) -> None:
    """Clear failed login attempts after successful authentication."""
    _LOGIN_ATTEMPTS.pop(client_ip, None)


def _clear_login_rate_limit() -> None:
    """Clear all rate limit state. For testing only."""
    _LOGIN_ATTEMPTS.clear()

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
    max_tokens: int | None = None
    temperature: float | None = None
    max_iterations: int | None = None
    context_window_tokens: int | None = None
    reasoning_effort: str | None = None
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
    models: list[str] | None = None


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


def _is_masked_secret_value(value: str) -> bool:
    """True if `value` looks like a redacted placeholder produced by GET /api/config/raw.

    The raw config GET serializes SecretStr via ``model_dump(mode="json")`` to
    ``"**********"``, then ``_sanitize_public_config`` masks sensitive fields to
    ``"••••"`` + last4. Both forms indicate "secret was redacted for display".
    """
    return value == "**********" or value.startswith("••••")


def _restore_masked_secrets(old: Any, new: Any) -> None:
    """Restore SecretStr values redacted by the raw config round-trip.

    GET /api/config/raw masks SecretStr fields for safety. When the edited
    config is PUT back, those placeholders would otherwise clobber the real
    secrets (e.g. api keys, tokens). This walks ``new`` (a pydantic model) in
    parallel with ``old`` and, for every SecretStr field whose current value is
    a mask, copies the real value from ``old``. A user-supplied new (non-mask)
    value is preserved; an empty value clears the secret as requested.
    """
    from pydantic import BaseModel, SecretStr

    if not isinstance(new, BaseModel) or not isinstance(old, BaseModel):
        return
    for field_name in type(new).model_fields:
        new_val = getattr(new, field_name, None)
        old_val = getattr(old, field_name, None)
        if isinstance(new_val, SecretStr):
            if _is_masked_secret_value(new_val.get_secret_value()) and isinstance(old_val, SecretStr):
                setattr(new, field_name, SecretStr(old_val.get_secret_value()))
        elif isinstance(new_val, BaseModel) and isinstance(old_val, BaseModel):
            _restore_masked_secrets(old_val, new_val)
        elif isinstance(new_val, dict) and isinstance(old_val, dict):
            for key, value in new_val.items():
                if isinstance(value, BaseModel) and isinstance(old_val.get(key), BaseModel):
                    _restore_masked_secrets(old_val[key], value)


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


def _serialize_skill_info(container: ServiceContainer, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item["name"],
        "source": item.get("source", "builtin"),
        "path": item.get("path", ""),
        "description": item.get("description", item["name"]),
        "available": item.get("available", True),
        "enabled": item.get("enabled", True),
        "unavailable_reason": item.get("unavailable_reason"),
        "type": item.get("type", "builtin"),
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
    sensitive_markers = ("token", "secret", "password", "key", "authorization")
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


def _should_include_workspace_path(workspace_path: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(workspace_path)
    except ValueError:
        return False
    parts = relative.parts
    if len(parts) >= 2 and parts[0] == ".webui" and parts[1] == "backups":
        return False
    return not any(part.startswith(".workspace-import-") for part in parts)


def _write_workspace_zip(zf: zipfile.ZipFile, workspace_path: Path) -> None:
    for path in sorted(workspace_path.rglob("*")):
        if path.is_file() and _should_include_workspace_path(workspace_path, path):
            zf.write(path, path.relative_to(workspace_path))


def _validate_workspace_zip_member(member: str, extracted_path: Path) -> None:
    normalized = member.replace("\\", "/")
    member_path = PurePosixPath(normalized)
    if not normalized or member_path.is_absolute() or ".." in member_path.parts:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid path in archive: {member}")
    target = (extracted_path / member_path).resolve()
    try:
        target.relative_to(extracted_path.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid path in archive: {member}") from exc


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
    skip_lifecycle: bool = False,
    health_router: Any = None,
) -> FastAPI:
    app = FastAPI(title="xbot WebUI", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://tauri.localhost",
            "https://tauri.localhost",
            "tauri://localhost",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    resolved_data_dir = (data_dir or container.data_dir or (container.config.workspace_path / ".webui")).resolve()
    resolved_data_dir.mkdir(parents=True, exist_ok=True)
    container.data_dir = resolved_data_dir
    app.state.services = container
    app.state.user_store = UserStore(resolved_data_dir / "users.json")

    # Ensure password is initialized (generates on first run)
    generated_password = ensure_password_file()
    if generated_password:
        print_password_banner(generated_password)

    app.state.auth = AuthManager(get_or_create_jwt_secret())
    app.state.runtime_started = False
    app.state.channel_start_task = None
    app.state.webui_active_tasks = {}
    if frontend_dir is not None:
        resolved_frontend_dir = frontend_dir
    else:
        # Frontend static files are in the webui/ directory (sibling to gateway/)
        webui_dir = Path(__file__).parent.parent / "webui"
        frontend_candidates = (
            webui_dir / "frontend" / "dist",
            webui_dir / "frontend" / "dev-dist",
        )
        resolved_frontend_dir = next(
            (path for path in frontend_candidates if path.exists()),
            frontend_candidates[0],
        )
    app.state.frontend_dir = resolved_frontend_dir
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

    def _session_namespace(session_key: str) -> str:
        return (session_key or "").split(":", 1)[0]

    def _ensure_writable_client_session(session_key: str, user_id: str) -> str:
        internal_session_key = to_internal_session_key(session_key)
        namespace, sep, rest = internal_session_key.partition(":")
        if namespace not in {"web", "app"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This session is read-only from WebUI/App",
            )
        owner, owner_sep, _session_id = rest.partition(":")
        if not sep or not owner_sep or owner != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot modify another client's session",
            )
        return internal_session_key

    async def _send_read_only_session_error(websocket: WebSocket, session_key: str) -> None:
        await _safe_websocket_send_json(websocket, {
            "type": "error",
            "error": "This session is read-only from WebUI/App",
            "session_key": session_key,
        })

    def _frontend_index_response() -> HTMLResponse | None:
        resolved_frontend = Path(app.state.frontend_dir)
        index_file = resolved_frontend / "index.html"
        if index_file.exists():
            return HTMLResponse(index_file.read_text(encoding="utf-8"))
        return None

    def _resolve_heartbeat_target() -> tuple[str, str] | None:
        heartbeat_cfg = container.config.gateway.heartbeat
        enabled_channels = set()
        manager = container.metadata.get("channel_manager")
        if manager is not None and hasattr(manager, "enabled_channels"):
            enabled_channels = set(getattr(manager, "enabled_channels", []) or [])

        explicit_channel = (heartbeat_cfg.channel or "").strip()
        explicit_chat_id = (heartbeat_cfg.chat_id or "").strip()
        if explicit_channel and explicit_chat_id and explicit_channel in enabled_channels:
            return explicit_channel, explicit_chat_id

        if not hasattr(container.conversation_store, "list_sessions"):
            return None

        for item in container.conversation_store.list_sessions():
            key = str(item.get("key") or "")
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel == "im":
                provider, sep, provider_chat_id = chat_id.partition(":")
                if sep and provider in enabled_channels and provider_chat_id:
                    return provider, provider_chat_id
                continue
            if channel in {"cli", "system", "heartbeat"}:
                continue
            if channel.startswith("cron"):
                continue
            if channel in enabled_channels and chat_id:
                return channel, chat_id
        return None

    @app.on_event("startup")
    async def _startup_runtime() -> None:
        if skip_lifecycle:
            # Gateway manages lifecycle externally
            return
        if app.state.runtime_started:
            return

        if hasattr(container.agent, "initialize"):
            await container.agent.initialize()

        # Wire cron callback to real agent execution.
        if hasattr(container.cron, "on_job"):
            from xbot.platform.bus.events import OutboundMessage
            from xbot.platform.utils.evaluator import evaluate_response
            from xbot.tools.cron import CronTool
            from xbot.tools.message import MessageTool

            async def on_cron_job(job: Any) -> str | None:
                reminder_note = (
                    "[Scheduled Task] Timer finished.\n\n"
                    f"Task '{job.name}' has been triggered.\n"
                    f"Scheduled instruction: {job.payload.message}"
                )

                message_tool = container.agent.tools.get("message") if hasattr(container.agent, "tools") else None
                if isinstance(message_tool, MessageTool):
                    message_tool.start_turn()

                cron_tool = container.agent.tools.get("cron") if hasattr(container.agent, "tools") else None
                cron_token = None
                if isinstance(cron_tool, CronTool):
                    cron_token = cron_tool.set_cron_context(True)
                try:
                    response = await container.agent.process_managed_direct(
                        reminder_note,
                        session_key=f"cron:{job.id}",
                        channel=job.payload.channel or "cli",
                        chat_id=job.payload.to or "direct",
                    )
                finally:
                    if isinstance(cron_tool, CronTool) and cron_token is not None:
                        cron_tool.reset_cron_context(cron_token)

                if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
                    return response

                if job.payload.deliver and job.payload.to and response:
                    should_notify = await evaluate_response(
                        response, job.payload.message, container.agent.backend.call_for_structured,
                    )
                    if should_notify:
                        await container.bus.publish_outbound(OutboundMessage(
                            channel=job.payload.channel or "cli",
                            chat_id=job.payload.to,
                            content=response,
                        ))
                return response

            container.cron.on_job = on_cron_job

        if hasattr(container.cron, "start"):
            await container.cron.start()

        # Wire heartbeat callbacks to runtime backend.
        async def _heartbeat_llm_call(*args, **kwargs):
            return await container.agent.backend.call_for_structured(*args, **kwargs)

        async def _heartbeat_execute(tasks: str) -> str:
            channel, chat_id = _resolve_heartbeat_target() or ("cli", "direct")

            async def _silent(*_args, **_kwargs):
                return None

            return await container.agent.process_managed_direct(
                tasks,
                session_key="heartbeat",
                channel=channel,
                chat_id=chat_id,
                on_progress=_silent,
            )

        async def _heartbeat_notify(response: str) -> None:
            from xbot.platform.bus.events import OutboundMessage

            target = _resolve_heartbeat_target()
            if target is None:
                return
            channel, chat_id = target
            await container.bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

        configure_heartbeat = getattr(container.heartbeat, "configure_callbacks", None)
        if callable(configure_heartbeat):
            configure_heartbeat(
                llm_call=_heartbeat_llm_call,
                on_execute=_heartbeat_execute,
                on_notify=_heartbeat_notify,
            )
        else:
            container.heartbeat._llm_call = _heartbeat_llm_call
            container.heartbeat.on_execute = _heartbeat_execute
            container.heartbeat.on_notify = _heartbeat_notify

        if hasattr(container.heartbeat, "start"):
            await container.heartbeat.start()

        # The gateway owns long-lived external channel connections. Starting
        # them from WebUI creates duplicate Feishu/Telegram consumers and can
        # split inbound events across processes.
        app.state.channel_start_task = None

        app.state.runtime_started = True

    @app.on_event("shutdown")
    async def _shutdown_runtime() -> None:
        if skip_lifecycle:
            # Gateway manages lifecycle externally
            return
        if not app.state.runtime_started:
            return

        task = app.state.channel_start_task
        if task is not None:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            else:
                with suppress(Exception):
                    await task
        app.state.channel_start_task = None

        heartbeat_shutdown = getattr(container.heartbeat, "shutdown", None)
        if callable(heartbeat_shutdown):
            with suppress(Exception):
                await heartbeat_shutdown()
        elif hasattr(container.heartbeat, "stop"):
            with suppress(Exception):
                container.heartbeat.stop()

        cron_shutdown = getattr(container.cron, "shutdown", None)
        if callable(cron_shutdown):
            with suppress(Exception):
                await cron_shutdown()
        elif hasattr(container.cron, "stop"):
            with suppress(Exception):
                container.cron.stop()

        if hasattr(container.agent, "close_mcp"):
            with suppress(Exception):
                await container.agent.close_mcp()
        if hasattr(container.agent, "shutdown"):
            with suppress(Exception):
                await container.agent.shutdown()
        if hasattr(container.agent, "stop"):
            with suppress(Exception):
                container.agent.stop()

        app.state.runtime_started = False

    # Include health router BEFORE static file routes to ensure health endpoints take priority
    if health_router is not None:
        app.include_router(health_router)

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
    async def login(body: LoginRequest, request: Request) -> dict[str, Any]:
        # Check rate limit
        client_ip = request.client.host if request.client else "unknown"
        _check_login_rate_limit(client_ip)

        try:
            user = app.state.user_store.authenticate(body.username, body.password)
        except HTTPException:
            _record_failed_login(client_ip)
            raise
        _clear_failed_logins(client_ip)
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
        sessions = container.conversation_store.list_sessions()
        channels = _channels_payload()
        cron_status = container.cron.status() if hasattr(container.cron, "status") else {"jobs": 0}
        return {
            "runtime": container.runtime_status(),
            "counts": {
                "sessions": len(sessions),
                "channels": len(channels["channels"]),
                "cron_jobs": int(cron_status.get("jobs", 0)),
            },
            "heartbeat": container.heartbeat_status(),
        }

    @app.get("/api/desktop/ping")
    async def desktop_ping() -> dict[str, Any]:
        return {
            "ok": True,
            "name": "xbot",
            "version": __version__,
        }

    @app.get("/api/sessions")
    async def list_sessions(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
        _get_user_from_auth_header(authorization)
        return container.conversation_store.list_sessions()

    @app.get("/api/sessions/{session_key:path}/messages")
    async def get_session_messages(
        session_key: str,
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        _get_user_from_auth_header(authorization)
        session = container.conversation_store.get(session_key)
        if session is None:
            return []
        return session.messages

    @app.get("/api/sessions/{session_key:path}/memory")
    async def get_session_memory(
        session_key: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, str]:
        _get_user_from_auth_header(authorization)
        memory_dir = container.config.workspace_path / "memory"
        memory_file = memory_dir / "MEMORY.md"
        history_file = memory_dir / "HISTORY.md"
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

        user = _get_user_from_auth_header(authorization)
        internal_session_key = _ensure_writable_client_session(session_key, str(user["id"]))
        session = container.conversation_store.get_or_create(internal_session_key)
        if index < 0 or index >= len(session.messages):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message index")
        del session.messages[index]
        session.updated_at = datetime.now()
        container.conversation_store.save(session)
        return {"removed": 1}

    @app.delete("/api/sessions/{session_key:path}")
    async def delete_session(session_key: str, authorization: str | None = Header(default=None)) -> dict[str, bool]:
        user = _get_user_from_auth_header(authorization)
        internal_session_key = _ensure_writable_client_session(session_key, str(user["id"]))
        removed = container.conversation_store.delete(internal_session_key)
        try:
            await container.agent.reset_session(internal_session_key, drop_sdk_context=True)
        except Exception:
            # Keep delete API best-effort for runtime cleanup.
            pass
        return {"ok": removed}

    @app.get("/api/providers")
    async def providers(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
        _get_user_from_auth_header(authorization)
        provider_list = []

        # Legacy WebUI compatibility: expose the historical `custom` provider
        # first, while storing it in the new custom_providers map.
        _ = container.config.providers.custom  # ensure legacy 'custom' provider is registered

        for name, value in container.config.providers.custom_providers.items():
            raw_key = value.api_key.get_secret_value() if hasattr(value.api_key, "get_secret_value") else ""
            provider_list.append({
                "name": name,
                "api_key_masked": _mask_secret(value.api_key),
                "api_base": value.api_base,
                "extra_headers": _sanitize_public_config(value.extra_headers),
                "has_key": bool(raw_key),
                "models": list(value.models) if value.models else [],
                "is_custom": True,
            })

        # 1. 固定供应商 (只有 anthropic)
        fixed_names = ["anthropic"]
        for name in fixed_names:
            value = getattr(container.config.providers, name)
            raw_key = value.api_key.get_secret_value() if hasattr(value.api_key, "get_secret_value") else ""
            provider_list.append({
                "name": name,
                "api_key_masked": _mask_secret(value.api_key),
                "api_base": value.api_base,
                "extra_headers": _sanitize_public_config(value.extra_headers),
                "has_key": bool(raw_key),
                "models": list(value.models) if value.models else [],
                "is_custom": False,
            })

        return provider_list

    @app.post("/api/providers")
    async def create_provider(
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        name = body.get("name", "").strip()
        if not name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provider name required")

        # 验证名称
        safe_name = validate_safe_name(name, "provider name")

        # 检查是否已存在
        fixed_names = {"anthropic"}
        if safe_name in fixed_names or safe_name in container.config.providers.custom_providers:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Provider already exists")

        # 创建
        container.config.providers.custom_providers[safe_name] = ProviderConfig(
            api_key=SecretStr(body.get("api_key", "")),
            api_base=body.get("api_base"),
            extra_headers=body.get("extra_headers"),
            models=body.get("models", []),
        )
        container.persist_config()
        return {"name": safe_name}

    @app.delete("/api/providers/{provider_name}")
    async def delete_provider(
        provider_name: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _get_user_from_auth_header(authorization)
        safe_name = validate_safe_name(provider_name, "provider name")

        if safe_name not in container.config.providers.custom_providers:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Custom provider not found")

        del container.config.providers.custom_providers[safe_name]
        container.persist_config()
        return {"ok": True}

    @app.patch("/api/providers/{provider_name}")
    async def patch_provider(
        provider_name: str,
        body: ProviderPatch,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        safe_name = validate_safe_name(provider_name, "provider name")

        # 查找供应商
        fixed_names = {"anthropic"}
        _ = container.config.providers.custom  # ensure legacy 'custom' provider is registered
        if safe_name in fixed_names:
            provider = getattr(container.config.providers, safe_name)
        elif safe_name in container.config.providers.custom_providers:
            provider = container.config.providers.custom_providers[safe_name]
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")

        if body.api_key is not None:
            provider.api_key = SecretStr(body.api_key)
        if body.api_base is not None:
            provider.api_base = body.api_base
        if body.extra_headers is not None:
            provider.extra_headers = body.extra_headers
        if body.models is not None:
            provider.models = body.models
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
            safe_name = validate_safe_name(name, "channel name")
            setattr(container.config.channels, safe_name, channel_config)
        container.persist_config()
        return await channels(authorization=authorization)

    @app.patch("/api/channels/{channel_name}")
    async def patch_channel(
        channel_name: str,
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        safe_name = validate_safe_name(channel_name, "channel name")
        existing = getattr(container.config.channels, safe_name, {})
        merged = dict(existing or {})
        merged.update(body)
        setattr(container.config.channels, safe_name, merged)
        container.persist_config()
        return {"name": safe_name, "config": _sanitize_public_config(merged)}

    @app.post("/api/channels/{channel_name}/reload")
    async def reload_channel(
        channel_name: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _get_user_from_auth_header(authorization)
        try:
            result = await container.reload_channel(channel_name)
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
            return await container.reload_all_channels()
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
        server_name = validate_safe_name(server_name, "server name")
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
        server_name = validate_safe_name(server_name, "server name")
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
            await container.heartbeat.set_enabled(body.enabled)
        if body.interval_s is not None:
            if body.interval_s < 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="heartbeat interval_s must be >= 1",
                )
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
            "max_tokens": defaults.max_tokens,
            "temperature": defaults.temperature,
            "max_iterations": defaults.max_tool_iterations,
            "context_window_tokens": defaults.context_window_tokens,
            "reasoning_effort": defaults.reasoning_effort,
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
        if body.max_tokens is not None:
            defaults.max_tokens = body.max_tokens
        if body.temperature is not None:
            defaults.temperature = body.temperature
        if body.max_iterations is not None:
            defaults.max_tool_iterations = body.max_iterations
        if body.context_window_tokens is not None:
            defaults.context_window_tokens = body.context_window_tokens
        if body.reasoning_effort is not None:
            defaults.reasoning_effort = body.reasoning_effort
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
            await container.heartbeat.set_enabled(body.heartbeat_enabled)
        if body.heartbeat_interval_s is not None:
            if body.heartbeat_interval_s < 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="heartbeat interval_s must be >= 1",
                )
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
        name = validate_safe_name(name, "workspace file name", allow_dots=True)
        path = container.config.workspace_path / name
        return {"name": name, "content": path.read_text(encoding="utf-8") if path.exists() else ""}

    @app.put("/api/config/workspace-file/{name}")
    async def put_workspace_file(
        name: str,
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, str]:
        _get_user_from_auth_header(authorization)
        name = validate_safe_name(name, "workspace file name", allow_dots=True)
        path = container.config.workspace_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        content = str(body.get("content", ""))
        path.write_text(content, encoding="utf-8")
        return {"name": name, "content": content}

    @app.get("/api/config/raw")
    async def get_raw_config(authorization: str | None = Header(default=None)) -> dict[str, str]:
        _get_user_from_auth_header(authorization)
        # mode="json" serializes SecretStr -> "**********" so secrets are never
        # exposed via the _json_default fallback; _sanitize_public_config adds
        # a second layer for any plain-string sensitive fields (e.g. headers).
        return {
            "content": json.dumps(
                _sanitize_public_config(
                    container.config.model_dump(by_alias=True, mode="json")
                ),
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
        from xbot.platform.config.schema import Config

        _get_user_from_auth_header(authorization)
        content = str(body.get("content", ""))
        try:
            config_data = json.loads(content)
            new_config = Config.model_validate(config_data)
        except (json.JSONDecodeError, ValueError) as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
        # Restore secrets that GET /api/config/raw redacted for display, so
        # editing a non-secret field and saving doesn't clobber api keys/tokens.
        _restore_masked_secrets(container.config, new_config)
        container.config = new_config
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
            _write_workspace_zip(zf, container.config.workspace_path)
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

        # Guards against zip-bomb / resource-exhaustion DoS: cap upload size,
        # entry count, and total uncompressed size before extracting.
        max_zip_size = 50 * 1024 * 1024  # 50 MB compressed
        max_entries = 10_000
        max_uncompressed = 100 * 1024 * 1024  # 100 MB decompressed

        contents = b""
        while True:
            chunk = await file.read(64 * 1024)
            if not chunk:
                break
            contents += chunk
            if len(contents) > max_zip_size:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Zip file exceeds 50MB limit",
                )
        data = contents
        backups_dir = (container.data_dir or container.config.workspace_path) / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backups_dir / f"workspace-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as backup_zip:
            _write_workspace_zip(backup_zip, container.config.workspace_path)
        workspace_path = container.config.workspace_path
        workspace_parent = workspace_path.parent
        extracted_path = workspace_parent / f".workspace-import-{secrets.token_hex(8)}"
        staged_old_path = workspace_parent / f".workspace-import-old-{secrets.token_hex(8)}"
        with zipfile.ZipFile(BytesIO(data), "r") as zf:
            entries = zf.infolist()
            if len(entries) > max_entries:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Too many zip entries: {len(entries)} > {max_entries}",
                )
            total_uncompressed = sum(info.file_size for info in entries)
            if total_uncompressed > max_uncompressed:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Uncompressed size exceeds 100MB limit",
                )
            for member in zf.namelist():
                _validate_workspace_zip_member(member, extracted_path)
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
        skill_file = container.primary_skill_root() / safe_name / "SKILL.md"
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
        skill_name = validate_safe_name(skill_name, "skill name")
        skill_file = container.primary_skill_root() / skill_name / "SKILL.md"
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
        skill_name = validate_safe_name(skill_name, "skill name")
        skill_dir = container.primary_skill_root() / skill_name
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
        try:
            payload = app.state.auth.decode_token(token)
        except HTTPException:
            await websocket.close(code=1008)
            return
        user_id = str(payload.get("sub") or "admin")
        session_key = websocket.query_params.get("session") or f"web:{user_id}:default"
        await websocket.accept()
        if not await _safe_websocket_send_json(websocket, {"type": "session_info", "session_key": session_key}):
            return

        active_tasks: dict[str, asyncio.Task] = app.state.webui_active_tasks
        owned_task_keys: set[str] = set()

        async def _run_agent_turn(active_session_key: str, content: str) -> None:
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
                    "session_key": active_session_key,
                })

            internal_session_key = to_internal_session_key(active_session_key)
            runtime_channel, runtime_chat_id = runtime_route_from_session_key(
                internal_session_key,
                user_id,
            )
            before_session = container.conversation_store.get(internal_session_key)
            before_count = len(before_session.messages) if before_session is not None else 0

            try:
                response = await container.agent.process_managed_direct(
                    content=content,
                    session_key=internal_session_key,
                    channel=runtime_channel,
                    chat_id=runtime_chat_id,
                    on_progress=_on_progress,
                )
                after_session = container.conversation_store.get(internal_session_key)
                after_count = len(after_session.messages) if after_session is not None else 0
                if after_count <= before_count:
                    session = container.conversation_store.get_or_create(internal_session_key)
                    session.add_message("user", content)
                    if response:
                        session.add_message("assistant", response)
                    container.conversation_store.save(session)
                elif response and after_session is not None:
                    last_role = after_session.messages[-1].get("role") if after_session.messages else None
                    if last_role == "user":
                        after_session.add_message("assistant", response)
                        container.conversation_store.save(after_session)
                await _safe_websocket_send_json(websocket, {
                    "type": "done",
                    "content": response,
                    "session_key": active_session_key,
                })
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("WebUI chat task failed for %s", active_session_key)
                await _safe_websocket_send_json(websocket, {
                    "type": "error",
                    "error": str(exc),
                    "session_key": active_session_key,
                })
            finally:
                active_tasks.pop(active_session_key, None)
                owned_task_keys.discard(active_session_key)

        try:
            while True:
                message = await websocket.receive_json()
                message_type = message.get("type")
                message_session_key = message.get("session_key")
                active_session_key = (
                    message_session_key
                    if isinstance(message_session_key, str) and message_session_key
                    else session_key
                )
                if message_type == "cancel":
                    task = active_tasks.pop(active_session_key, None)
                    if task is not None and not task.done():
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
                    await _safe_websocket_send_json(websocket, {
                        "type": "cancel_ok",
                        "session_key": active_session_key,
                    })
                    continue
                if message_type == "revoke":
                    index = message.get("index")
                    if not isinstance(index, int):
                        await _safe_websocket_send_json(websocket, {
                            "type": "error",
                            "error": "Message index must be an integer",
                            "session_key": active_session_key,
                        })
                        continue
                    try:
                        internal_session_key = _ensure_writable_client_session(active_session_key, user_id)
                    except HTTPException:
                        await _send_read_only_session_error(websocket, active_session_key)
                        continue
                    session = container.conversation_store.get_or_create(internal_session_key)
                    if index < 0 or index >= len(session.messages):
                        await _safe_websocket_send_json(websocket, {
                            "type": "error",
                            "error": "Invalid message index",
                            "session_key": active_session_key,
                        })
                        continue
                    del session.messages[index]
                    session.updated_at = datetime.now()
                    container.conversation_store.save(session)
                    await _safe_websocket_send_json(websocket, {
                        "type": "revoke_ok",
                        "index": index,
                        "session_key": active_session_key,
                    })
                    continue
                if message_type != "message":
                    continue
                try:
                    _ensure_writable_client_session(active_session_key, user_id)
                except HTTPException:
                    await _send_read_only_session_error(websocket, active_session_key)
                    continue
                content = message.get("content", "")
                if not isinstance(content, str):
                    await _safe_websocket_send_json(websocket, {
                        "type": "error",
                        "error": "Message content must be a string",
                        "session_key": active_session_key,
                    })
                    return
                if len(content) > _WS_CHAT_MAX_CONTENT_CHARS:
                    await _safe_websocket_send_json(websocket, {
                        "type": "error",
                        "error": f"Message too large; max {_WS_CHAT_MAX_CONTENT_CHARS} characters",
                        "session_key": active_session_key,
                    })
                    return

                task = active_tasks.get(active_session_key)
                if task is not None and not task.done():
                    await _safe_websocket_send_json(websocket, {
                        "type": "error",
                        "error": "A message is already running for this session",
                        "session_key": active_session_key,
                    })
                    continue
                active_tasks[active_session_key] = asyncio.create_task(
                    _run_agent_turn(active_session_key, content)
                )
                owned_task_keys.add(active_session_key)
        except WebSocketDisconnect:
            pass
        finally:
            # Cancel any in-flight agent turns on every exit path — client
            # disconnect, oversized/malformed message return, or error — so
            # the session slot is released and a reconnect isn't blocked by
            # an "already running" ghost task.
            for key in list(owned_task_keys):
                task = active_tasks.pop(key, None)
                if task is not None and not task.done():
                    task.cancel()

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def spa_fallback(full_path: str) -> str:
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        frontend = _frontend_index_response()
        if frontend is not None:
            return frontend.body.decode("utf-8")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return app
