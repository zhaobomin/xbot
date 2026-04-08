"""CLI commands for xbot."""

import asyncio
import json
import os
import re as _re
import select
import signal
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Callable

if sys.platform == "win32":
    if sys.stdout.encoding != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        # Re-open stdout/stderr with UTF-8 encoding
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # OSError: handle invalid (redirected/piped)
            # ValueError: reconfigure not supported
            pass

import typer
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.formatted_text import ANSI, HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from xbot import __logo__, __version__
from xbot.runtime.core.service import AgentService
from xbot.crew.cli.plan_cmd import crew_plan, crew_run_dynamic
from xbot.crew.cli.role_cmd import app as roles_app
from xbot.interaction.permission import CLIPermissionHandler, InteractivePermissionHandler
from xbot.interaction.progress_coalescer import ProgressCoalescer
from xbot.runtime.core.task_supervisor import ServiceTaskRegistry
from xbot.platform.config.paths import get_data_dir, get_workspace_path
from xbot.platform.config.schema import Config
from xbot.platform.logging.core import configure_logging, get_logger, set_package_logging_enabled
from xbot.platform.utils.helpers import (
    sync_workspace_command_pack,
    sync_workspace_skill_pack,
    sync_workspace_templates,
)
from xbot.interfaces.webui.cli import webui_app

# Force UTF-8 encoding for Windows console
logger = get_logger(__name__)

app = typer.Typer(
    name="xbot",
    help=f"{__logo__} xbot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}


def _resolve_heartbeat_target(
    *,
    config: Config,
    enabled_channels: list[str],
    session_manager: Any,
) -> tuple[str, str] | None:
    """Resolve a stable heartbeat delivery target at gateway startup."""
    heartbeat_cfg = config.gateway.heartbeat
    enabled = set(enabled_channels)

    explicit_channel = (heartbeat_cfg.channel or "").strip()
    explicit_chat_id = (heartbeat_cfg.chat_id or "").strip()
    if explicit_channel or explicit_chat_id:
        if not explicit_channel or not explicit_chat_id:
            logger.warning(
                "Heartbeat target ignored: both channel and chat_id are required; "
                "got channel=%r chat_id=%r",
                explicit_channel,
                explicit_chat_id,
            )
            return None
        if explicit_channel not in enabled:
            logger.warning(
                "Heartbeat target ignored: channel %r is not enabled. Enabled channels: %s",
                explicit_channel,
                sorted(enabled),
            )
            return None
        return explicit_channel, explicit_chat_id

    if session_manager is None or not hasattr(session_manager, "list_sessions"):
        return None

    for item in session_manager.list_sessions():
        key = item.get("key") or ""
        if ":" not in key:
            continue
        channel, chat_id = key.split(":", 1)
        if channel in {"cli", "system", "heartbeat"}:
            continue
        if channel.startswith("cron"):
            continue
        if channel in enabled and chat_id:
            return channel, chat_id

    return None

# ---------------------------------------------------------------------------
# File reference parsing for @path syntax
# ---------------------------------------------------------------------------

_FILE_REF_RE = _re.compile(
    r"""(?:^|(?<=\s))@(?:"([^"@]+?\.[a-zA-Z0-9]+)"|'([^'@]+?\.[a-zA-Z0-9]+)'|([^\s"'@]+?\.[a-zA-Z0-9]+))""",
    _re.IGNORECASE | _re.MULTILINE,
)


def _validate_path_in_workspace(path: Path, workspace: Path) -> bool:
    """Allowlist: only paths within workspace are permitted.

    Security: This prevents path traversal attacks where users could read
    arbitrary files on the system using @path syntax.
    """
    try:
        resolved = path.resolve()  # Follows symlinks
        workspace_resolved = workspace.resolve()
        return resolved.is_relative_to(workspace_resolved)
    except (OSError, ValueError):
        return False


def _parse_media_from_input(
    user_input: str, workspace: Path | None = None
) -> tuple[str, list[str]]:
    """Extract ``@path`` file references from user input.

    Returns ``(clean_text, media_paths)`` where *clean_text* has matched
    references removed and *media_paths* contains resolved absolute paths
    for files that exist on disk AND are within the workspace directory.

    Security: Paths outside the workspace are rejected with an error message.
    """
    from xbot.platform.config.paths import get_workspace_path

    # Use provided workspace or fall back to configured workspace
    effective_workspace = workspace or get_workspace_path()

    media_paths: list[str] = []
    errors: list[str] = []

    def _replace(m: _re.Match) -> str:
        raw_path = m.group(1) or m.group(2) or m.group(3)
        p = Path(raw_path).expanduser().resolve()

        if not p.is_file():
            # File does not exist – keep original text so user sees it
            return m.group(0)

        # Security check: path must be within workspace
        if not _validate_path_in_workspace(p, effective_workspace):
            errors.append(
                f"Error: '@path' can only reference files within the workspace directory.\n"
                f"  Workspace: {effective_workspace}\n"
                f"  Attempted: {p}"
            )
            return m.group(0)  # Keep original so user sees the error

        media_paths.append(str(p))
        return ""

    clean = _FILE_REF_RE.sub(_replace, user_input).strip()

    # Print any security errors
    for error in errors:
        print(f"\n[red]{error}[/red]\n")

    return clean or "请处理这些文件", media_paths

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit
_LOCAL_COMMAND_SETTINGS = "local_command_settings.json"


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except (OSError, ValueError):
        # stdin not available or not a valid file descriptor
        return

    try:
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except (ImportError, OSError, termios.error):
        # ImportError: termios not available (Windows)
        # OSError/termios.error: device doesn't support the operation
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except (OSError, ValueError, BlockingIOError):
        # Fallback failed, but it's not critical
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios
        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    from xbot.platform.config.paths import get_cli_history_path

    history_file = get_cli_history_path()
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,   # Enter submits (single line mode)
    )
    _PROMPT_SESSION.editing_mode = _load_cli_editing_mode()


def _load_cli_editing_mode() -> EditingMode:
    settings_path = get_data_dir() / _LOCAL_COMMAND_SETTINGS
    try:
        if settings_path.exists():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            mode = str(data.get("editorMode", "normal")).lower()
            if mode == "vim":
                return EditingMode.VI
    except Exception:
        pass
    return EditingMode.EMACS


def set_cli_editing_mode(mode: str) -> None:
    if _PROMPT_SESSION is None:
        return
    normalized = mode.lower()
    _PROMPT_SESSION.editing_mode = (
        EditingMode.VI if normalized == "vim" else EditingMode.EMACS
    )


def _make_console() -> Console:
    return Console(file=sys.stdout)


def _render_interactive_ansi(render_fn) -> str:
    """Render Rich output to ANSI so prompt_toolkit can print it safely."""
    ansi_console = Console(
        force_terminal=True,
        color_system=console.color_system or "standard",
        width=console.width,
    )
    with ansi_console.capture() as capture:
        render_fn(ansi_console)
    return capture.get()


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with consistent terminal styling."""
    console = _make_console()
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(f"[cyan]{__logo__} xbot[/cyan]")
    console.print(body)
    console.print()


async def _print_interactive_line(text: str) -> None:
    """Print async interactive updates with prompt_toolkit-safe Rich styling."""
    def _write() -> None:
        ansi = _render_interactive_ansi(
            lambda c: c.print(f"  [dim]↳ {text}[/dim]")
        )
        print_formatted_text(ANSI(ansi), end="")

    await run_in_terminal(_write)


async def _print_interactive_response(response: str, render_markdown: bool) -> None:
    """Print async interactive replies with prompt_toolkit-safe Rich styling."""
    def _write() -> None:
        content = response or ""
        ansi = _render_interactive_ansi(
            lambda c: (
                c.print(),
                c.print(f"[cyan]{__logo__} xbot[/cyan]"),
                c.print(Markdown(content) if render_markdown else Text(content)),
                c.print(),
            )
        )
        print_formatted_text(ANSI(ansi), end="")

    await run_in_terminal(_write)


class _ThinkingSpinner:
    """Spinner wrapper with pause support for clean progress output."""

    def __init__(self, enabled: bool):
        self._spinner = console.status(
            "[dim]xbot is thinking...[/dim]", spinner="dots"
        ) if enabled else None
        self._active = False

    def __enter__(self):
        if self._spinner:
            self._spinner.start()
        self._active = True
        return self

    def __exit__(self, *exc):
        self._active = False
        if self._spinner:
            self._spinner.stop()
        return False

    @contextmanager
    def pause(self):
        """Temporarily stop spinner while printing progress."""
        if self._spinner and self._active:
            self._spinner.stop()
        try:
            yield
        finally:
            if self._spinner and self._active:
                self._spinner.start()


def _print_cli_progress_line(text: str, thinking: _ThinkingSpinner | None) -> None:
    """Print a CLI progress line, pausing the spinner if needed."""
    with thinking.pause() if thinking else nullcontext():
        console.print(f"  [dim]↳ {text}[/dim]")


async def _print_interactive_progress_line(text: str, thinking: _ThinkingSpinner | None) -> None:
    """Print an interactive progress line, pausing the spinner if needed."""
    with thinking.pause() if thinking else nullcontext():
        await _print_interactive_line(text)


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc



def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} xbot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """xbot - Personal AI Assistant."""
    configure_logging()


# ============================================================================
# Onboard / Setup
# ============================================================================


def _run_init(
    *,
    workspace: str | None = None,
    config: str | None = None,
    skill_pack: str = "default",
    command_pack: str = "default",
    install_skill_pack: bool = False,
    install_command_pack: bool = False,
) -> None:
    """Initialize xbot configuration and workspace."""
    from xbot.platform.config.loader import get_config_path, load_config, save_config, set_config_path
    from xbot.platform.config.schema import Config

    config_arg = config

    if config_arg:
        config_path = Path(config_arg).expanduser().resolve()
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")
    else:
        config_path = get_config_path()

    def _apply_workspace_override(loaded: Config) -> Config:
        if workspace:
            loaded.agents.defaults.workspace = workspace
        return loaded

    # Create or update config
    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print("  [bold]N[/bold] = refresh config, keeping existing values and adding new fields")
        if typer.confirm("Overwrite?"):
            loaded_config = _apply_workspace_override(Config())
            save_config(loaded_config, config_path)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            loaded_config = _apply_workspace_override(load_config(config_path))
            save_config(loaded_config, config_path)
            console.print(f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)")
    else:
        loaded_config = _apply_workspace_override(Config())
        save_config(loaded_config, config_path)
        console.print(f"[green]✓[/green] Created config at {config_path}")
    console.print("[dim]Config template now uses `maxTokens` + `contextWindowTokens`; `memoryWindow` is no longer a runtime setting.[/dim]")

    _onboard_plugins(config_path)

    # Create workspace, preferring the configured workspace path.
    workspace_path = get_workspace_path(loaded_config.workspace_path)
    if not workspace_path.exists():
        workspace_path.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace_path}")

    sync_workspace_templates(workspace_path)

    if install_skill_pack:
        try:
            installed_skills = sync_workspace_skill_pack(workspace_path, skill_pack)
        except FileNotFoundError:
            console.print(f"[red]Error: skill pack not found: {skill_pack}[/red]")
            raise typer.Exit(1)
        if installed_skills:
            console.print(
                f"[green]✓[/green] Installed skill pack '{skill_pack}' "
                f"({len(installed_skills)} item(s))"
            )

    if install_command_pack:
        try:
            installed_commands = sync_workspace_command_pack(workspace_path, command_pack)
        except FileNotFoundError:
            console.print(f"[red]Error: command pack not found: {command_pack}[/red]")
            raise typer.Exit(1)
        if installed_commands:
            console.print(
                f"[green]✓[/green] Installed command pack '{command_pack}' "
                f"({len(installed_commands)} item(s))"
            )

    agent_cmd = 'xbot agent -m "Hello!"'
    if config_arg:
        agent_cmd += f" --config {config_path}"

    console.print(f"\n{__logo__} xbot is ready!")
    console.print("\nNext steps:")
    console.print(f"  1. Add your API key to [cyan]{config_path}[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print(f"  2. Chat: [cyan]{agent_cmd}[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/xbot#-chat-apps[/dim]")


@app.command()
def onboard(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Initialize xbot configuration and workspace (legacy alias)."""
    _run_init(
        workspace=workspace,
        config=config,
        install_skill_pack=True,
        install_command_pack=True,
    )


@app.command("init")
def init_cmd(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    skill_pack: str = typer.Option("default", "--skill-pack", help="Skill pack to install"),
    command_pack: str = typer.Option("default", "--command-pack", help="Command pack to install"),
    no_skill_pack: bool = typer.Option(False, "--no-skill-pack", help="Skip skill pack installation"),
    no_command_pack: bool = typer.Option(False, "--no-command-pack", help="Skip command pack installation"),
):
    """Initialize xbot environment (config, workspace, default packs)."""
    _run_init(
        workspace=workspace,
        config=config,
        skill_pack=skill_pack,
        command_pack=command_pack,
        install_skill_pack=not no_skill_pack,
        install_command_pack=not no_command_pack,
    )


def _merge_missing_defaults(existing: Any, defaults: Any) -> Any:
    """Recursively fill in missing values from defaults without overwriting user config."""
    if not isinstance(existing, dict) or not isinstance(defaults, dict):
        return existing

    merged = dict(existing)
    for key, value in defaults.items():
        if key not in merged:
            merged[key] = value
        else:
            merged[key] = _merge_missing_defaults(merged[key], value)
    return merged


def _onboard_plugins(config_path: Path) -> None:
    """Inject default config for all discovered channels (built-in + plugins)."""
    import json

    from xbot.channels.registry import discover_all

    all_channels = discover_all()
    if not all_channels:
        return

    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    channels = data.setdefault("channels", {})
    for name, cls in all_channels.items():
        if name not in channels:
            channels[name] = cls.default_config()
        else:
            channels[name] = _merge_missing_defaults(channels[name], cls.default_config())

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _load_runtime_config(config: str | None = None, workspace: str | None = None) -> Config:
    """Load config and optionally override the active workspace."""
    from xbot.platform.config.loader import load_config, set_config_path
    from xbot.platform.config.validator import ConfigurationError, validate_config

    config_path = None
    if config:
        config_path = Path(config).expanduser().resolve()
        if not config_path.exists():
            console.print(f"[red]Error: Config file not found: {config_path}[/red]")
            raise typer.Exit(1)
        set_config_path(config_path)
        console.print(f"[dim]Using config: {config_path}[/dim]")

    loaded = load_config(config_path)
    if workspace:
        loaded.agents.defaults.workspace = workspace
    try:
        validate_config(loaded)
    except ConfigurationError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)
    return loaded


def _make_agent_service(
    *,
    config: Config,
    bus,
    workspace: Path,
    cron_service,
    session_manager,
    state_manager=None,
    permission_handler=None,
):
    """Create the unified agent service."""
    from xbot.runtime.core.types import AgentConfig

    agent_config = AgentConfig(
        model=config.agents.defaults.model,
        system_prompt="",  # System prompt is built dynamically by ContextBuilder
        mcp_servers=getattr(config.tools, "mcp_servers", {}) or {},
        agents=list(config.agents.claude_sdk.agents.values()) if config.agents.claude_sdk.agents else None,
    )

    shared_resources = {
        "bus": bus,
        "workspace": workspace,
        "cron_service": cron_service,
        "session_manager": session_manager,
        "config": config,
        "tools_config": config.tools,
    }
    if state_manager is not None:
        shared_resources["state_manager"] = state_manager
    if permission_handler is not None:
        shared_resources["permission_handler"] = permission_handler

    service = AgentService(agent_config, shared_resources)
    return service


def _print_deprecated_memory_window_notice(config: Config) -> None:
    """Warn when running with old memoryWindow-only config."""
    if config.agents.defaults.should_warn_deprecated_memory_window:
        console.print(
            "[yellow]Hint:[/yellow] Detected deprecated `memoryWindow` without "
            "`contextWindowTokens`. `memoryWindow` is ignored; run "
            "[cyan]xbot onboard[/cyan] to refresh your config template."
        )


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int | None = typer.Option(None, "--port", "-p", help="Gateway port"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    health_port: int | None = typer.Option(None, "--health-port", help="Health check HTTP port (default: gateway_port - 710)"),
):
    """Start the xbot gateway."""
    from xbot.interaction.permission import PermissionRequestHandler
    from xbot.runtime.system.monitoring.health import HealthCheckService
    from xbot.runtime.state import SessionManager as StateManager
    from xbot.platform.bus.queue import MessageBus
    from xbot.channels.manager import ChannelManager
    from xbot.platform.config.paths import get_cron_dir
    from xbot.runtime.system.cron.service import CronService
    from xbot.runtime.system.cron.types import CronJob
    from xbot.runtime.system.heartbeat.service import HeartbeatService
    from xbot.runtime.session.manager import SessionManager

    configure_logging(level="DEBUG" if verbose else "INFO")

    config = _load_runtime_config(config, workspace)
    _print_deprecated_memory_window_notice(config)
    port = port if port is not None else config.gateway.port
    health_port = health_port if health_port is not None else (port - 710)

    console.print(f"{__logo__} Starting xbot gateway version {__version__} on port {port}...")
    console.print("[dim]Agent type: claude_sdk[/dim]")
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    session_manager = SessionManager(config.workspace_path)
    state_manager = StateManager()

    # Create health check service
    health = HealthCheckService(port=health_port, host=config.gateway.host)

    # Create cron service first (callback set after agent creation)
    cron_store_path = get_cron_dir() / "jobs.json"
    cron = CronService(cron_store_path)

    # Create permission handler for channel mode (gateway)
    perm_config = config.agents.claude_sdk.permission
    permission_handler = PermissionRequestHandler(
        bus=bus,
        timeout=perm_config.timeout,
        auto_approve_safe_tools=perm_config.auto_approve_safe_tools,
        safe_tools=set(perm_config.safe_tools),
    )

    # Create agent service
    agent = _make_agent_service(
        config=config,
        bus=bus,
        workspace=config.workspace_path,
        cron_service=cron,
        session_manager=session_manager,
        state_manager=state_manager,
        permission_handler=permission_handler,
    )

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        from xbot.tools.cron import CronTool
        from xbot.tools.message import MessageTool
        from xbot.platform.utils.evaluator import evaluate_response

        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )

        cron_tool = agent.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)
        try:
            response = await agent.process_managed_direct(
                reminder_note,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
            )
        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return response

        if job.payload.deliver and job.payload.to and response:
            llm_call = agent.backend.call_for_structured
            should_notify = await evaluate_response(
                response, job.payload.message, llm_call,
            )
            if should_notify:
                from xbot.platform.bus.events import OutboundMessage
                await bus.publish_outbound(OutboundMessage(
                    channel=job.payload.channel or "cli",
                    chat_id=job.payload.to,
                    content=response,
                ))
        return response
    cron.on_job = on_cron_job

    # Create channel manager
    channels = ChannelManager(config, bus)

    heartbeat_target = _resolve_heartbeat_target(
        config=config,
        enabled_channels=channels.enabled_channels,
        session_manager=session_manager,
    )
    if heartbeat_target is None:
        logger.info("Heartbeat target unresolved at startup; execute-only mode enabled")
    else:
        logger.info(
            "Heartbeat target resolved at startup: %s:%s",
            heartbeat_target[0],
            heartbeat_target[1],
        )

    # Create heartbeat service
    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = heartbeat_target or ("cli", "direct")

        async def _silent(*_args, **_kwargs):
            pass

        return await agent.process_managed_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from xbot.platform.bus.events import OutboundMessage
        if heartbeat_target is None:
            return  # No external channel available to deliver to
        channel, chat_id = heartbeat_target
        await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

    async def _heartbeat_llm_call(*args, **kwargs):
        """Defer backend access until runtime (after agent.initialize())."""
        return await agent.backend.call_for_structured(*args, **kwargs)

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        llm_call=_heartbeat_llm_call,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
        on_channel_health=channels.check_channels_health,
    )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")
    console.print(f"[green]✓[/green] Health check: http://{config.gateway.host}:{health_port}/health")

    async def run():
        from xbot.runtime.system.monitoring.alerting import AlertConfig, init_alert_service

        # Initialize alert service
        alert_config = AlertConfig(
            enabled=bool(channels.enabled_channels),
            channel=channels.enabled_channels[0] if channels.enabled_channels else "telegram",
            chat_id="",  # Will be determined from sessions
        )
        alert = init_alert_service(bus, alert_config)

        try:
            # Start health check service
            health.update_status("agent", "initializing")
            health.update_status("channels", channels.enabled_channels)
            await health.start()

            await agent.initialize()
            health.update_status("agent", "running")

            await cron.start()
            health.update_status("cron", cron.status())

            await heartbeat.start()
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        except asyncio.CancelledError:
            console.print("\nShutting down (cancelled)...")
        except Exception as e:
            import traceback
            console.print("\n[red]Error: Gateway crashed unexpectedly[/red]")
            console.print(traceback.format_exc())
            # Send critical alert
            await alert.alert_critical(e, "Gateway crashed unexpectedly")
        finally:
            await agent.close_mcp()
            heartbeat_shutdown = getattr(heartbeat, "shutdown", None)
            if callable(heartbeat_shutdown):
                await heartbeat_shutdown()
            else:
                heartbeat.stop()
            cron_shutdown = getattr(cron, "shutdown", None)
            if callable(cron_shutdown):
                await cron_shutdown()
            else:
                cron.stop()
            agent.stop()
            await channels.stop_all()
            await health.stop()

    asyncio.run(run())




# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show xbot runtime logs during chat"),
):
    """Interact with the agent directly."""
    from xbot.runtime.state import SessionManager as StateManager
    from xbot.platform.bus.queue import MessageBus
    from xbot.platform.config.paths import get_cron_dir
    from xbot.runtime.system.cron.service import CronService

    config = _load_runtime_config(config, workspace)
    _print_deprecated_memory_window_notice(config)
    sync_workspace_templates(config.workspace_path)

    bus = MessageBus()
    state_manager = StateManager()

    # Create cron service for tool usage (no callback needed for CLI unless running)
    cron_store_path = get_cron_dir() / "jobs.json"
    cron = CronService(cron_store_path)

    configure_logging(level="DEBUG" if logs else "INFO")
    set_package_logging_enabled(logs, enabled_level="DEBUG")

    # Shared reference for progress callbacks and permission handler
    _thinking: _ThinkingSpinner | None = None

    # Get permission config
    perm_config = config.agents.claude_sdk.permission

    if message:
        # Single message mode — non-interactive CLI permission handler
        _permission_handler = CLIPermissionHandler(
            auto_approve_safe_tools=perm_config.auto_approve_safe_tools,
            interactive=False,  # Non-interactive mode
            safe_tools=set(perm_config.safe_tools),
        )
    else:
        # Interactive mode — interactive permission handler with spinner support
        _permission_handler = InteractivePermissionHandler(
            auto_approve_safe_tools=perm_config.auto_approve_safe_tools,
            safe_tools=set(perm_config.safe_tools),
        )

    agent_loop = _make_agent_service(
        config=config,
        bus=bus,
        workspace=config.workspace_path,
        cron_service=cron,
        session_manager=None,
        state_manager=state_manager,
        permission_handler=_permission_handler,
    )

    # For interactive mode, set spinner reference on permission handler
    if not message and isinstance(_permission_handler, InteractivePermissionHandler):
        # Spinner will be set when _thinking is created in run_interactive
        pass

    def _should_emit_cli_progress(tool_hint: bool, event_type: str) -> bool:
        ch = agent_loop.channels_config
        if ch is None:
            ch = config.channels
        if ch and tool_hint and not ch.send_tool_hints:
            return False
        if ch and not tool_hint and event_type == "usage" and not ch.send_usage_summary:
            return False
        if ch and not tool_hint and event_type != "usage" and not ch.send_progress:
            return False
        return True

    async def _cli_progress(
        content: str,
        *,
        tool_hint: bool = False,
        event_type: str = "progress",
        event_data: dict[str, Any] | None = None,
    ) -> None:
        _ = event_data
        if not _should_emit_cli_progress(tool_hint, event_type):
            return
        _print_cli_progress_line(content, _thinking)

    if ":" in session_id:
        cli_channel, cli_chat_id = session_id.split(":", 1)
    else:
        cli_channel, cli_chat_id = "cli", session_id

    if message:
        # Single message mode — direct call, no bus needed
        async def run_once():
            nonlocal _thinking
            progress_coalescer = ProgressCoalescer()
            _thinking = _ThinkingSpinner(enabled=not logs)

            async def _coalesced_cli_progress(
                content: str,
                *,
                tool_hint: bool = False,
                event_type: str = "progress",
                event_data: dict[str, Any] | None = None,
            ) -> None:
                _ = event_data
                if not _should_emit_cli_progress(tool_hint, event_type):
                    return
                key = (cli_channel, cli_chat_id, event_type)
                ready = progress_coalescer.push(
                    key=key,
                    text=content,
                    event_type=event_type,
                    tool_hint=tool_hint,
                )
                for item in ready:
                    _print_cli_progress_line(item.text, _thinking)

            with _thinking:
                response = await agent_loop.process_direct(
                    content=message,
                    session_key=session_id,
                    channel=cli_channel,
                    chat_id=cli_chat_id,
                    on_progress=_coalesced_cli_progress,
                )
                for item in progress_coalescer.flush_all():
                    _print_cli_progress_line(item.text, _thinking)
            _thinking = None
            _print_agent_response(response, render_markdown=markdown)
            await agent_loop.close_mcp()

        asyncio.run(run_once())
    else:
        # Interactive mode — route through bus like other channels
        from xbot.platform.bus.events import InboundMessage
        _init_prompt_session()
        console.print(f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n")

        def _handle_signal(signum, frame):
            sig_name = signal.Signals(signum).name
            _restore_terminal()
            console.print(f"\nReceived {sig_name}, goodbye!")
            sys.exit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        # SIGHUP is not available on Windows
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, _handle_signal)
        # Ignore SIGPIPE to prevent silent process termination when writing to closed pipes
        # SIGPIPE is not available on Windows
        if hasattr(signal, 'SIGPIPE'):
            signal.signal(signal.SIGPIPE, signal.SIG_IGN)

        async def run_interactive():
            task_registry = ServiceTaskRegistry()
            # Set spinner reference on permission handler for this session
            if isinstance(_permission_handler, InteractivePermissionHandler):
                _thinking_ref = _ThinkingSpinner(enabled=not logs)
                _permission_handler.set_thinking_spinner(_thinking_ref)

            task_registry.spawn("interactive-cli", agent_loop.run(), name="agent-loop")
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[str] = []
            progress_coalescer = ProgressCoalescer()

            async def _consume_outbound():
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                        if msg.metadata.get("_progress"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            event_type = msg.metadata.get("_event_type", "progress")
                            if _should_emit_cli_progress(bool(is_tool_hint), str(event_type)):
                                key = (msg.channel, msg.chat_id, event_type)
                                ready = progress_coalescer.push(
                                    key=key,
                                    text=msg.content,
                                    event_type=str(event_type),
                                    tool_hint=bool(is_tool_hint),
                                )
                                for item in ready:
                                    await _print_interactive_progress_line(item.text, _thinking)

                        elif not turn_done.is_set():
                            for item in progress_coalescer.flush_all():
                                await _print_interactive_progress_line(item.text, _thinking)
                            if msg.content:
                                turn_response.append(msg.content)
                            turn_done.set()
                        elif msg.content:
                            for item in progress_coalescer.flush_all():
                                await _print_interactive_progress_line(item.text, _thinking)
                            await _print_interactive_response(msg.content, render_markdown=markdown)

                    except asyncio.TimeoutError:
                        for item in progress_coalescer.flush_due():
                            await _print_interactive_progress_line(item.text, _thinking)
                        continue
                    except asyncio.CancelledError:
                        break

            task_registry.spawn(
                "interactive-cli",
                _consume_outbound(),
                name="outbound-consumer",
            )

            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        user_input = await _read_interactive_input_async()
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        turn_done.clear()
                        turn_response.clear()

                        clean_text, media_paths = _parse_media_from_input(user_input, workspace=config.workspace_path)
                        if media_paths:
                            console.print(f"[dim]Attached {len(media_paths)} file(s)[/dim]")

                        await bus.publish_inbound(InboundMessage(
                            channel=cli_channel,
                            sender_id="user",
                            chat_id=cli_chat_id,
                            content=clean_text,
                            media=media_paths,
                        ))

                        nonlocal _thinking
                        _thinking = _ThinkingSpinner(enabled=not logs)
                        # Update spinner reference on permission handler
                        if isinstance(_permission_handler, InteractivePermissionHandler):
                            _permission_handler.set_thinking_spinner(_thinking)
                        with _thinking:
                            await turn_done.wait()
                        _thinking = None

                        if turn_response:
                            _print_agent_response(turn_response[0], render_markdown=markdown)
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                agent_loop.stop()
                await task_registry.cancel_owner("interactive-cli")
                await agent_loop.close_mcp()

        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from xbot.channels.registry import discover_all
    from xbot.platform.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")

    for name, cls in sorted(discover_all().items()):
        section = getattr(config.channels, name, None)
        if section is None:
            enabled = False
        elif isinstance(section, dict):
            enabled = section.get("enabled", False)
        else:
            enabled = getattr(section, "enabled", False)
        table.add_row(
            cls.display_name,
            "[green]\u2713[/green]" if enabled else "[dim]\u2717[/dim]",
        )

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # User's bridge location
    from xbot.platform.config.paths import get_bridge_install_dir

    user_bridge = get_bridge_install_dir()

    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Check for npm
    npm_path = shutil.which("npm")
    if not npm_path:
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # xbot/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall xbot")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run([npm_path, "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run([npm_path, "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import shutil
    import subprocess

    from xbot.platform.config.loader import load_config
    from xbot.platform.config.paths import get_runtime_subdir

    config = load_config()
    bridge_dir = _get_bridge_dir()

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    env = {**os.environ}
    wa_cfg = getattr(config.channels, "whatsapp", None) or {}
    bridge_token = wa_cfg.get("bridgeToken", "") if isinstance(wa_cfg, dict) else getattr(wa_cfg, "bridge_token", "")
    if bridge_token:
        env["BRIDGE_TOKEN"] = bridge_token
    env["AUTH_DIR"] = str(get_runtime_subdir("whatsapp-auth"))

    npm_path = shutil.which("npm")
    if not npm_path:
        console.print("[red]npm not found. Please install Node.js.[/red]")
        raise typer.Exit(1)

    try:
        subprocess.run([npm_path, "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")


# ============================================================================
# Plugin Commands
# ============================================================================

plugins_app = typer.Typer(help="Manage channel plugins")
app.add_typer(plugins_app, name="plugins")


@plugins_app.command("list")
def plugins_list():
    """List all discovered channels (built-in and plugins)."""
    from xbot.channels.registry import discover_all, discover_channel_names
    from xbot.platform.config.loader import load_config

    config = load_config()
    builtin_names = set(discover_channel_names())
    all_channels = discover_all()

    table = Table(title="Channel Plugins")
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="magenta")
    table.add_column("Enabled", style="green")

    for name in sorted(all_channels):
        cls = all_channels[name]
        source = "builtin" if name in builtin_names else "plugin"
        section = getattr(config.channels, name, None)
        if section is None:
            enabled = False
        elif isinstance(section, dict):
            enabled = section.get("enabled", False)
        else:
            enabled = getattr(section, "enabled", False)
        table.add_row(
            cls.display_name,
            source,
            "[green]yes[/green]" if enabled else "[dim]no[/dim]",
        )

    console.print(table)


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show xbot status."""
    from xbot.platform.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} xbot Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        from xbot.platform.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
            elif spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")


# ============================================================================
# Crew Orchestration
# ============================================================================

crew_app = typer.Typer(help="Multi-agent crew orchestration")
app.add_typer(crew_app, name="crew")

crew_app.add_typer(roles_app, name="roles", help="Role pool management")

# Add dynamic planning commands
crew_app.command("plan")(crew_plan)
crew_app.command("run-dynamic")(crew_run_dynamic)


@crew_app.command("run")
def crew_run(
    config_file: str = typer.Argument(..., help="Path to crew YAML config"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Override workspace path"),
    config: str | None = typer.Option(None, "--config", "-c", help="xbot config file"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    resume: str | None = typer.Option(None, "--resume", help="Resume from checkpoint JSON"),
    progress: bool = typer.Option(True, "--progress/--no-progress", help="Show progress display"),
    var: list[str] = typer.Option([], "--var", help="Set variable (name=value), can be used multiple times"),
):
    """Run a multi-agent crew from a YAML config file.

    Variables can be set with --var name=value and used in the config as ${name}.
    """
    from pathlib import Path

    from xbot.crew import CrewOrchestrator, load_crew_config
    from xbot.crew.config import CrewConfigLoader
    from xbot.crew.models import parse_crew_config

    # 1. Load xbot global config
    xbot_config = _load_runtime_config(config, workspace)

    # 2. Parse CLI variables
    cli_vars = {}
    for v in var:
        if "=" in v:
            name, value = v.split("=", 1)
            cli_vars[name.strip()] = value
        else:
            console.print(f"[yellow]Warning: Invalid --var format '{v}', expected 'name=value'[/yellow]")

    # 3. Load crew YAML with variable resolution and inheritance support
    config_path = Path(config_file).expanduser().resolve()

    # Use CrewConfigLoader for proper variable resolution and inheritance
    loader = CrewConfigLoader(cli_vars=cli_vars)
    try:
        config_dict = loader.load(config_path)
        crew_config = parse_crew_config(config_dict, config_path)
    except Exception as e:
        # Fallback: try simple load without variable resolution
        try:
            crew_config = load_crew_config(config_path)
        except Exception:
            raise e

    if workspace:
        crew_config.workspace = str(Path(workspace).expanduser().resolve())
    if verbose:
        crew_config.verbose = True

    # 3. Permission handler for human review
    perm_config = xbot_config.agents.claude_sdk.permission
    permission_handler = InteractivePermissionHandler(
        auto_approve_safe_tools=perm_config.auto_approve_safe_tools,
        safe_tools=set(perm_config.safe_tools),
    )

    # 4. Progress callback with enhanced display
    task_count = len(crew_config.tasks)
    completed_count = [0]  # Use list for mutable closure

    def on_progress(message: str, **kwargs: Any) -> None:
        if progress:
            # Check for task completion
            if "done" in message.lower() or "completed" in message.lower():
                completed_count[0] += 1

            # Show progress bar
            bar_width = 20
            filled = int(bar_width * completed_count[0] / task_count) if task_count > 0 else 0
            bar = "█" * filled + "░" * (bar_width - filled)

            task_name = kwargs.get("task_name", "")

            if task_name:
                console.print(f"\r[dim][crew][/dim] [{bar}] {completed_count[0]}/{task_count} | {task_name[:30]:<30}", end="")
            else:
                console.print(f"\r[dim][crew][/dim] [{bar}] {completed_count[0]}/{task_count} | {message[:30]:<30}", end="")
        elif verbose:
            console.print(f"[dim][crew][/dim] {message}")

    # 5. Execute
    async def _run() -> None:
        orch = CrewOrchestrator(
            crew_config=crew_config,
            xbot_config=xbot_config,
            permission_handler=permission_handler,
            config_path=str(Path(config_file).expanduser().resolve()),
            on_progress=on_progress,
        )
        checkpoint_path = Path(resume) if resume else None
        result = await orch.run(checkpoint_path=checkpoint_path)
        if progress:
            console.print()  # Clear progress line
        _print_crew_result(result)

    asyncio.run(_run())


@crew_app.command("show")
def crew_show(
    config_file: str = typer.Argument(..., help="Path to crew YAML config"),
):
    """Display and validate a crew config file."""
    from xbot.crew import load_crew_config

    try:
        crew_config = load_crew_config(Path(config_file))
    except Exception as exc:
        console.print(f"[red]Error loading crew config: {exc}[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold]{crew_config.name}[/bold]")
    if crew_config.description:
        console.print(f"  {crew_config.description}")
    console.print(f"  Process: {crew_config.process.value}")
    console.print(f"  Workspace: {crew_config.workspace}")
    console.print()

    # Agents table
    table = Table(title="Agents")
    table.add_column("Name", style="cyan")
    table.add_column("Goal")
    table.add_column("Model")
    for name, role in crew_config.agents.items():
        table.add_row(name, role.goal[:60], role.model)
    console.print(table)
    console.print()

    # Tasks table
    table = Table(title="Tasks")
    table.add_column("Name", style="cyan")
    table.add_column("Agent")
    table.add_column("Deps")
    table.add_column("Review")
    table.add_column("Briefing")
    for task in crew_config.tasks:
        table.add_row(
            task.name,
            task.agent,
            ", ".join(task.context_from) or "-",
            "yes" if task.human_review else "-",
            "yes" if task.human_briefing else "-",
        )
    console.print(table)


@crew_app.command("init")
def crew_init(
    project_name: str = typer.Argument(..., help="Name of the project to create"),
    template: str | None = typer.Option(None, "--template", "-t", help="Template to use"),
    path: str | None = typer.Option(None, "--path", "-p", help="Parent directory (default: current dir)"),
):
    """Initialize a new crew project with a template."""
    from xbot.crew.templates import get_template, init_project, list_templates

    # Validate template if specified
    if template:
        t = get_template(template)
        if not t:
            console.print(f"[red]Unknown template: {template}[/red]")
            console.print("\nAvailable templates:")
            for tmpl in list_templates():
                console.print(f"  - {tmpl.name}: {tmpl.description}")
            raise typer.Exit(1)

    # Determine project directory
    parent_dir = Path(path) if path else Path.cwd()
    project_dir = parent_dir / project_name

    if project_dir.exists():
        console.print(f"[red]Directory already exists: {project_dir}[/red]")
        raise typer.Exit(1)

    # Create project
    try:
        config_path = init_project(
            project_dir=project_dir,
            template_name=template,
            project_name=project_name,
        )
        console.print(f"\n[green]✓[/green] Created crew project: [bold]{project_name}[/bold]")
        console.print(f"  Directory: {project_dir}")
        console.print(f"  Config:    {config_path}")
        if template:
            console.print(f"  Template:  {template}")
        console.print("\nNext steps:")
        console.print(f"  cd {project_name}")
        console.print("  xbot crew run crew_config.yaml")
    except Exception as exc:
        console.print(f"[red]Error creating project: {exc}[/red]")
        raise typer.Exit(1)


@crew_app.command("templates")
def crew_templates():
    """List available crew templates."""
    from xbot.crew.templates import list_templates

    templates = list_templates()

    console.print("\n[bold]Available Crew Templates[/bold]\n")

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Agents")
    table.add_column("Tasks")

    for t in templates:
        config = t.load_config()
        agents_count = len(config.get("agents", {}))
        tasks_count = len(config.get("tasks", []))
        table.add_row(t.name, t.description, str(agents_count), str(tasks_count))

    console.print(table)
    console.print("\nUsage: [dim]xbot crew init my-project --template <name>[/dim]")


@crew_app.command("validate")
def crew_validate(
    config_file: str = typer.Argument(..., help="Path to crew YAML config"),
    strict: bool = typer.Option(False, "--strict", help="Enable strict validation"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate execution without running"),
):
    """Validate a crew configuration file."""
    from xbot.crew import load_crew_config

    console.print(f"\n[bold]Validating:[/bold] {config_file}\n")

    errors = []
    # 1. Load and parse YAML
    try:
        crew_config = load_crew_config(Path(config_file))
        console.print("[green]✓[/green] YAML syntax valid")
    except FileNotFoundError:
        console.print("[red]✗[/red] File not found")
        raise typer.Exit(1)
    except Exception as e:
        errors.append(f"Configuration error: {e}")
        console.print(f"[red]✗[/red] Configuration error: {e}")

    if errors:
        console.print(f"\n[red]Validation failed with {len(errors)} error(s)[/red]")
        raise typer.Exit(1)

    # 2. Validate agents
    console.print(f"[green]✓[/green] {len(crew_config.agents)} agent(s) defined")

    # Check for duplicate agent names
    agent_names = list(crew_config.agents.keys())
    if len(agent_names) != len(set(agent_names)):
        errors.append("Duplicate agent names found")

    # 3. Validate tasks
    console.print(f"[green]✓[/green] {len(crew_config.tasks)} task(s) defined")

    task_names = {t.name for t in crew_config.tasks}

    # Check for duplicate task names
    if len(crew_config.tasks) != len(task_names):
        errors.append("Duplicate task names found")
        console.print("[red]✗[/red] Duplicate task names found")
    else:
        console.print("[green]✓[/green] All task names unique")

    # 4. Validate agent references
    for task in crew_config.tasks:
        if task.agent not in crew_config.agents:
            errors.append(f"Task '{task.name}' references unknown agent '{task.agent}'")
            console.print(f"[red]✗[/red] Task '{task.name}' references unknown agent '{task.agent}'")

    if not any(e.startswith("Task") and "unknown agent" in e for e in errors):
        console.print("[green]✓[/green] All agent references valid")

    # 5. Validate dependencies
    dep_errors = False
    for task in crew_config.tasks:
        for dep in task.context_from:
            if dep not in task_names:
                errors.append(f"Task '{task.name}' has invalid dependency '{dep}'")
                console.print(f"[red]✗[/red] Task '{task.name}' has invalid dependency '{dep}'")
                dep_errors = True

    if not dep_errors:
        console.print("[green]✓[/green] All task dependencies valid")

    # 6. Check for circular dependencies
    if not dep_errors:
        visited = set()
        rec_stack = set()

        def has_cycle(task_name: str) -> bool:
            visited.add(task_name)
            rec_stack.add(task_name)
            task = next((t for t in crew_config.tasks if t.name == task_name), None)
            if task:
                for dep in task.context_from:
                    if dep not in visited:
                        if has_cycle(dep):
                            return True
                    elif dep in rec_stack:
                        return True
            rec_stack.remove(task_name)
            return False

        for task in crew_config.tasks:
            if task.name not in visited:
                if has_cycle(task.name):
                    errors.append("Circular dependency detected")
                    console.print("[red]✗[/red] Circular dependency detected")
                    break
        else:
            console.print("[green]✓[/green] No circular dependencies")

    # 7. Summary
    console.print()
    if errors:
        console.print(f"[red]✗ Validation failed with {len(errors)} error(s)[/red]")
        raise typer.Exit(1)
    else:
        console.print("[green]✓ Validation passed![/green]")

        if dry_run:
            console.print("\n[bold]Execution Plan:[/bold]")
            for i, task in enumerate(crew_config.tasks, 1):
                deps = f" (depends on: {', '.join(task.context_from)})" if task.context_from else ""
                console.print(f"  {i}. {task.name} → {task.agent}{deps}")

            console.print(f"\n[dim]Estimated time: ~{len(crew_config.tasks) * 2}-{len(crew_config.tasks) * 4} minutes[/dim]")

        console.print(f"\nRun with: [dim]xbot crew run {config_file}[/dim]")


@crew_app.command("checkpoints")
def crew_checkpoints(
    project_dir: str = typer.Argument(".", help="Project directory"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max checkpoints to show"),
):
    """List available checkpoints for a crew project."""
    project_path = Path(project_dir).resolve()
    checkpoint_dir = project_path / ".xbot" / "crew_checkpoints"

    if not checkpoint_dir.exists():
        console.print(f"[dim]No checkpoints found in {project_path}[/dim]")
        return

    checkpoints = sorted(
        checkpoint_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]

    if not checkpoints:
        console.print(f"[dim]No checkpoints found in {project_path}[/dim]")
        return

    console.print(f"\n[bold]Checkpoints in {project_path.name}[/bold]\n")

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=3)
    table.add_column("Checkpoint", style="cyan")
    table.add_column("Time")
    table.add_column("Status")
    table.add_column("Tasks")

    for i, cp in enumerate(checkpoints, 1):
        try:
            import json
            with open(cp, encoding="utf-8") as f:
                data = json.load(f)

            cp_time = data.get("checkpoint_at", "")[11:19] or "unknown"
            cp_status = data.get("crew_phase", "unknown")
            completed = len(data.get("completed_tasks", []))
            next_task = data.get("next_task", "-")

            status_style = {
                "completed": "[green]completed[/green]",
                "running": "[yellow]running[/yellow]",
                "failed": "[red]failed[/red]",
            }

            table.add_row(
                str(i),
                cp.name,
                cp_time,
                status_style.get(cp_status, cp_status),
                f"{completed} done, next: {next_task}" if next_task else f"{completed} done",
            )
        except Exception:
            table.add_row(str(i), cp.name, "?", "?", "?")

    console.print(table)
    console.print(f"\nResume: [dim]xbot crew resume {project_dir} --checkpoint <name>[/dim]")


@crew_app.command("resume")
def crew_resume(
    project_dir: str = typer.Argument(".", help="Project directory"),
    checkpoint: str | None = typer.Option(None, "--checkpoint", "-c", help="Checkpoint name (default: latest)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Resume a crew from a checkpoint."""
    from xbot.crew import CrewOrchestrator, load_crew_config
    from xbot.crew.context import load_checkpoint

    project_path = Path(project_dir).resolve()
    checkpoint_dir = project_path / ".xbot" / "crew_checkpoints"
    config_path = project_path / "crew_config.yaml"

    # Find config
    if not config_path.exists():
        console.print(f"[red]No crew_config.yaml found in {project_path}[/red]")
        raise typer.Exit(1)

    # Find checkpoint
    if checkpoint:
        checkpoint_path = checkpoint_dir / checkpoint
        if not checkpoint_path.exists():
            console.print(f"[red]Checkpoint not found: {checkpoint}[/red]")
            raise typer.Exit(1)
    else:
        # Find latest
        checkpoints = sorted(
            checkpoint_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not checkpoints:
            console.print(f"[red]No checkpoints found in {project_path}[/red]")
            raise typer.Exit(1)
        checkpoint_path = checkpoints[0]

    console.print(f"\n[bold]Resuming from:[/bold] {checkpoint_path.name}")

    # Load checkpoint to show status
    try:
        cp_data = load_checkpoint(checkpoint_path)
        completed = len(cp_data.get("completed_tasks", []))
        next_task = cp_data.get("next_task")
        console.print(f"  Completed tasks: {completed}")
        if next_task:
            console.print(f"  Next task: {next_task}")
    except Exception as e:
        console.print(f"[red]Error loading checkpoint: {e}[/red]")
        raise typer.Exit(1)

    # Load configs
    xbot_config = _load_runtime_config(None, None)
    crew_config = load_crew_config(config_path)

    # Permission handler
    perm_config = xbot_config.agents.claude_sdk.permission
    permission_handler = InteractivePermissionHandler(
        auto_approve_safe_tools=perm_config.auto_approve_safe_tools,
        safe_tools=set(perm_config.safe_tools),
    )

    # Progress callback
    def on_progress(message: str, **kwargs: Any) -> None:
        if verbose:
            console.print(f"[dim][crew][/dim] {message}")

    # Execute
    async def _run() -> None:
        orch = CrewOrchestrator(
            crew_config=crew_config,
            xbot_config=xbot_config,
            permission_handler=permission_handler,
            config_path=str(config_path),
            on_progress=on_progress,
        )
        result = await orch.run(checkpoint_path=checkpoint_path)
        _print_crew_result(result)

    asyncio.run(_run())


@crew_app.command("history")
def crew_history(
    project_dir: str = typer.Argument(".", help="Project directory"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max entries to show"),
):
    """Show execution history for a crew project."""
    project_path = Path(project_dir).resolve()
    checkpoint_dir = project_path / ".xbot" / "crew_checkpoints"

    if not checkpoint_dir.exists():
        console.print(f"[dim]No execution history in {project_path}[/dim]")
        return

    checkpoints = sorted(
        checkpoint_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]

    if not checkpoints:
        console.print(f"[dim]No execution history in {project_path}[/dim]")
        return

    console.print(f"\n[bold]Execution History: {project_path.name}[/bold]\n")

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Time", width=19)
    table.add_column("Crew", style="cyan")
    table.add_column("Status")
    table.add_column("Tasks")
    table.add_column("Duration")

    for cp in checkpoints:
        try:
            import json
            from datetime import datetime

            with open(cp, encoding="utf-8") as f:
                data = json.load(f)

            started = data.get("started_at", "")
            try:
                dt = datetime.fromisoformat(started)
                time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                time_str = started[:19] if started else "unknown"

            crew_name = data.get("crew_name", "unknown")
            crew_phase = data.get("crew_phase", "unknown")
            completed = len(data.get("completed_tasks", []))
            # Calculate duration if possible
            started_at = data.get("started_at")
            checkpoint_at = data.get("checkpoint_at")
            duration = "-"
            if started_at and checkpoint_at:
                try:
                    start = datetime.fromisoformat(started_at)
                    end = datetime.fromisoformat(checkpoint_at)
                    secs = (end - start).total_seconds()
                    if secs >= 60:
                        duration = f"{int(secs // 60)}m {int(secs % 60)}s"
                    else:
                        duration = f"{int(secs)}s"
                except (ValueError, TypeError):
                    pass

            status_style = {
                "completed": "[green]completed[/green]",
                "completing": "[green]completing[/green]",
                "running": "[yellow]running[/yellow]",
                "failed": "[red]failed[/red]",
                "aborted": "[red]aborted[/red]",
            }

            table.add_row(
                time_str,
                crew_name,
                status_style.get(crew_phase, crew_phase),
                str(completed),
                duration,
            )
        except Exception:
            pass

    console.print(table)


@crew_app.command("graph")
def crew_graph(
    config_file: str = typer.Argument(..., help="Path to crew YAML config"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file (default: stdout)"),
    mermaid: bool = typer.Option(False, "--mermaid", help="Output Mermaid diagram format"),
):
    """Generate a task dependency graph."""
    from xbot.crew import load_crew_config

    try:
        crew_config = load_crew_config(Path(config_file))
    except Exception as exc:
        console.print(f"[red]Error loading config: {exc}[/red]")
        raise typer.Exit(1)

    tasks = crew_config.tasks
    task_names = {t.name for t in tasks}

    # Build graph
    if mermaid:
        lines = ["graph TD"]
        lines.append(f"    title[{crew_config.name}]")

        for task in tasks:
            # Node label
            label = f"{task.name}\\n({task.agent})"
            lines.append(f'    {task.name}["{label}"]')

            # Edges
            for dep in task.context_from:
                lines.append(f"    {dep} --> {task.name}")

        # Style completed/running nodes if we had that info
        lines.append("")
        lines.append("    classDef task fill:#e1f5fe,stroke:#01579b")
        lines.append(f"    class {','.join(task_names)} task")

        output_text = "\n".join(lines)
    else:
        # ASCII art format
        lines = []
        lines.append(f"\n[bold]Task Dependency Graph: {crew_config.name}[/bold]\n")

        # Find tasks with no dependencies (roots)
        roots = [t for t in tasks if not t.context_from]

        # BFS to build graph
        visited = set()
        levels = []
        current_level = roots

        while current_level:
            levels.append(current_level)
            visited.update(t.name for t in current_level)
            next_level = []
            for task in tasks:
                if task.name not in visited:
                    if all(dep in visited for dep in task.context_from):
                        next_level.append(task)
            current_level = next_level

        for i, level in enumerate(levels):
            prefix = "  " * i
            connector = "└─ " if i > 0 else ""
            task_names_str = " → ".join(t.name for t in level)
            lines.append(f"{prefix}{connector}[cyan]{task_names_str}[/cyan]")
            for t in level:
                if t.context_from:
                    deps = ", ".join(t.context_from)
                    lines.append(f"{prefix}   [dim]← {deps}[/dim]")

        output_text = "\n".join(lines)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(output_text)
        console.print(f"[green]✓[/green] Graph written to {output}")
    else:
        console.print(output_text)


@crew_app.command("export")
def crew_export(
    project_dir: str = typer.Argument(".", help="Project directory"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file (default: stdout)"),
    format: str = typer.Option("markdown", "--format", "-f", help="Output format: markdown, json, html"),
    run_id: str | None = typer.Option(None, "--run", "-r", help="Specific run ID to export"),
):
    """Export crew execution results as a report.

    Generates a report from the latest or specified run.

    Examples:
        xbot crew export . -f markdown -o report.md
        xbot crew export . -f json
        xbot crew export . --run code_review_20240325
    """
    import json
    from pathlib import Path

    project_path = Path(project_dir).expanduser().resolve()
    runs_dir = project_path / ".xbot" / "crew_runs"

    if not runs_dir.exists():
        console.print(f"[yellow]No crew runs found in {project_path}[/yellow]")
        console.print("[dim]Run 'xbot crew run <config.yaml>' first[/dim]")
        raise typer.Exit(1)

    # Find the run to export
    if run_id:
        run_dir = runs_dir / run_id
        if not run_dir.exists():
            console.print(f"[red]Run not found: {run_id}[/red]")
            raise typer.Exit(1)
    else:
        # Get the latest run
        run_dirs = sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        if not run_dirs:
            console.print(f"[yellow]No crew runs found in {project_path}[/yellow]")
            raise typer.Exit(1)
        run_dir = run_dirs[0]
        run_id = run_dir.name

    # Load manifest
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        console.print(f"[red]Manifest not found for run: {run_id}[/red]")
        raise typer.Exit(1)

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    # Load task outputs
    tasks_dir = run_dir / "tasks"
    task_outputs = []
    if tasks_dir.exists():
        for task_file in sorted(tasks_dir.iterdir()):
            if task_file.suffix in (".json", ".md", ".txt"):
                try:
                    content = task_file.read_text()
                    if task_file.suffix == ".json":
                        try:
                            content = json.dumps(json.loads(content), indent=2)
                        except (json.JSONDecodeError, ValueError):
                            pass
                    task_outputs.append({
                        "file": task_file.name,
                        "content": content,
                    })
                except Exception as e:
                    console.print(f"[yellow]Warning: Could not read {task_file.name}: {e}[/yellow]")

    # Generate report
    if format == "json":
        report = json.dumps({
            "manifest": manifest,
            "tasks": task_outputs,
        }, indent=2)
    elif format == "html":
        report = _generate_html_report(manifest, task_outputs)
    else:  # markdown
        report = _generate_markdown_report(manifest, task_outputs)

    # Output
    if output:
        Path(output).write_text(report)
        console.print(f"[green]✓[/green] Report written to {output}")
    else:
        console.print(report)


def _generate_markdown_report(manifest: dict, task_outputs: list) -> str:
    """Generate a Markdown report from run data."""
    lines = []
    lines.append(f"# Crew Execution Report: {manifest.get('crew_name', 'Unknown')}")
    lines.append("")
    lines.append(f"**Run ID:** {manifest.get('run_id', 'N/A')}")
    lines.append(f"**Status:** {manifest.get('status', 'N/A')}")
    lines.append(f"**Started:** {manifest.get('started_at', 'N/A')}")
    lines.append(f"**Finished:** {manifest.get('finished_at', 'N/A')}")
    lines.append(f"**Total Time:** {manifest.get('total_time', 0):.1f}s")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Task | Status |")
    lines.append("|------|--------|")
    for task in manifest.get("tasks", []):
        lines.append(f"| {task.get('task_name', 'N/A')} | {task.get('status', 'N/A')} |")
    lines.append("")

    # Task outputs
    lines.append("## Task Outputs")
    lines.append("")
    for i, task_output in enumerate(task_outputs, 1):
        lines.append(f"### {task_output['file']}")
        lines.append("")
        lines.append("```")
        lines.append(task_output["content"][:2000])  # Limit output size
        if len(task_output["content"]) > 2000:
            lines.append("... (truncated)")
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def _generate_html_report(manifest: dict, task_outputs: list) -> str:
    """Generate an HTML report from run data."""
    crew_name = manifest.get('crew_name', 'Unknown')
    status = manifest.get('status', 'N/A')
    total_time = manifest.get('total_time', 0)

    tasks_html = ""
    for task in manifest.get("tasks", []):
        task_status = task.get('status', 'N/A')
        status_class = "success" if task_status == "success" else "failed" if task_status == "failed" else ""
        tasks_html += f'<tr><td>{task.get("task_name", "N/A")}</td><td class="{status_class}">{task_status}</td></tr>'

    outputs_html = ""
    for task_output in task_outputs:
        content = task_output["content"][:2000].replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        outputs_html += f'''
        <div class="task-output">
            <h3>{task_output["file"]}</h3>
            <pre>{content}</pre>
        </div>
        '''

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Crew Report: {crew_name}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 40px; }}
        h1 {{ color: #333; }}
        .summary {{ background: #f5f5f5; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
        th {{ background: #4CAF50; color: white; }}
        .success {{ color: green; }}
        .failed {{ color: red; }}
        .task-output {{ margin: 20px 0; padding: 15px; background: #fafafa; border-radius: 8px; }}
        pre {{ white-space: pre-wrap; word-wrap: break-word; }}
    </style>
</head>
<body>
    <h1>Crew Execution Report: {crew_name}</h1>
    <div class="summary">
        <p><strong>Status:</strong> {status}</p>
        <p><strong>Duration:</strong> {total_time:.1f}s</p>
    </div>
    <h2>Tasks</h2>
    <table>
        <tr><th>Task</th><th>Status</th></tr>
        {tasks_html}
    </table>
    <h2>Task Outputs</h2>
    {outputs_html}
</body>
</html>'''


def _print_crew_result(result: Any) -> None:
    """Print a crew execution result as a Rich table."""
    console.print()
    status_style = {
        "completed": "[green]completed[/green]",
        "failed": "[red]failed[/red]",
        "aborted": "[yellow]aborted[/yellow]",
    }
    console.print(f"[bold]Crew:[/bold] {result.crew_name}")
    console.print(f"[bold]Status:[/bold] {status_style.get(result.status, result.status)}")
    console.print(f"[bold]Time:[/bold] {result.total_time:.1f}s")
    console.print()

    table = Table(title="Task Results")
    table.add_column("Task", style="cyan")
    table.add_column("Agent")
    table.add_column("Status")
    table.add_column("Duration")
    for r in result.task_results:
        dur = (r.finished_at - r.started_at).total_seconds()
        s = r.status
        if s in ("success", "completed"):
            s = f"[green]{s}[/green]"
        elif s == "failed":
            s = f"[red]{s}[/red]"
        elif s in ("skipped", "human_rejected"):
            s = f"[yellow]{s}[/yellow]"
        table.add_row(r.task_name, r.agent_name, s, f"{dur:.1f}s")
    console.print(table)
    console.print()
    console.print(result.summary)
    console.print()


# ============================================================================
# OAuth Login
# ============================================================================

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")

app.add_typer(webui_app, name="webui")


_LOGIN_HANDLERS: dict[str, Callable] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn
    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"),
):
    """Authenticate with an OAuth provider."""
    from xbot.platform.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive
        token = None
        try:
            token = get_token()
        except Exception:
            pass
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda s: console.print(s),
                prompt_fn=lambda s: typer.prompt(s),
            )
        if not (token and token.access):
            console.print("[red]✗ Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]")
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
