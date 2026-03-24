"""CLI commands for xbot."""

import asyncio
from contextlib import contextmanager, nullcontext
import os
import select
import signal
import sys
from pathlib import Path
from typing import Any, Callable

# Force UTF-8 encoding for Windows console
if sys.platform == "win32":
    if sys.stdout.encoding != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        # Re-open stdout/stderr with UTF-8 encoding
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import typer
from prompt_toolkit import print_formatted_text
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI, HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.application import run_in_terminal
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from xbot import __logo__, __version__
from xbot.agent.runtime import AgentRuntime
from xbot.agent.progress_coalescer import ProgressCoalescer
from xbot.config.paths import get_workspace_path
from xbot.config.schema import Config
from xbot.utils.helpers import (
    sync_workspace_command_pack,
    sync_workspace_skill_pack,
    sync_workspace_templates,
)
from xbot.agent.permission_handler import CLIPermissionHandler, InteractivePermissionHandler

app = typer.Typer(
    name="xbot",
    help=f"{__logo__} xbot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ---------------------------------------------------------------------------
# File reference parsing for @path syntax
# ---------------------------------------------------------------------------

import re as _re

_FILE_REF_RE = _re.compile(
    r"""(?:^|(?<=\s))@(?:"([^"@]+?\.[a-zA-Z0-9]+)"|'([^'@]+?\.[a-zA-Z0-9]+)'|([^\s"'@]+?\.[a-zA-Z0-9]+))""",
    _re.IGNORECASE | _re.MULTILINE,
)


def _parse_media_from_input(user_input: str) -> tuple[str, list[str]]:
    """Extract ``@path`` file references from user input.

    Returns ``(clean_text, media_paths)`` where *clean_text* has matched
    references removed and *media_paths* contains resolved absolute paths
    for files that exist on disk.
    """
    media_paths: list[str] = []

    def _replace(m: _re.Match) -> str:
        raw_path = m.group(1) or m.group(2) or m.group(3)
        p = Path(raw_path).expanduser().resolve()
        if p.is_file():
            media_paths.append(str(p))
            return ""
        # File does not exist – keep original text so user sees it
        return m.group(0)

    clean = _FILE_REF_RE.sub(_replace, user_input).strip()
    return clean or "请处理这些文件", media_paths

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
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

    from xbot.config.paths import get_cli_history_path

    history_file = get_cli_history_path()
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,   # Enter submits (single line mode)
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
    pass


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
    from xbot.config.loader import get_config_path, load_config, save_config, set_config_path
    from xbot.config.schema import Config

    if config:
        config_path = Path(config).expanduser().resolve()
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
            config = _apply_workspace_override(Config())
            save_config(config, config_path)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            config = _apply_workspace_override(load_config(config_path))
            save_config(config, config_path)
            console.print(f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)")
    else:
        config = _apply_workspace_override(Config())
        save_config(config, config_path)
        console.print(f"[green]✓[/green] Created config at {config_path}")
    console.print("[dim]Config template now uses `maxTokens` + `contextWindowTokens`; `memoryWindow` is no longer a runtime setting.[/dim]")

    _onboard_plugins(config_path)

    # Create workspace, preferring the configured workspace path.
    workspace_path = get_workspace_path(config.workspace_path)
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
    if config:
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
    from xbot.config.loader import load_config, set_config_path
    from xbot.config.validator import ConfigurationError, validate_config

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


def _make_agent_runtime(
    *,
    config: Config,
    bus,
    workspace: Path,
    cron_service,
    session_manager,
    permission_handler=None,
):
    """Create the unified router-backed runtime."""
    shared_resources = {
        "bus": bus,
        "workspace": workspace,
        "cron_service": cron_service,
        "session_manager": session_manager,
        "config": config,
        "tools_config": config.tools,
    }
    if permission_handler is not None:
        shared_resources["permission_handler"] = permission_handler
    return AgentRuntime(
        config=config,
        shared_resources=shared_resources,
    )


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
    from xbot.agent.health import HealthCheckService
    from xbot.agent.permission_handler import PermissionRequestHandler
    from xbot.bus.queue import MessageBus
    from xbot.channels.manager import ChannelManager
    from xbot.config.paths import get_cron_dir
    from xbot.cron.service import CronService
    from xbot.cron.types import CronJob
    from xbot.heartbeat.service import HeartbeatService
    from xbot.session.manager import SessionManager

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    config = _load_runtime_config(config, workspace)
    _print_deprecated_memory_window_notice(config)
    port = port if port is not None else config.gateway.port
    health_port = health_port if health_port is not None else (port - 710)

    console.print(f"{__logo__} Starting xbot gateway version {__version__} on port {port}...")
    console.print(f"[dim]Agent type: claude_sdk[/dim]")
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    session_manager = SessionManager(config.workspace_path)

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

    # Create agent runtime
    agent = _make_agent_runtime(
        config=config,
        bus=bus,
        workspace=config.workspace_path,
        cron_service=cron,
        session_manager=session_manager,
        permission_handler=permission_handler,
    )

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        from xbot.agent.tools.cron import CronTool
        from xbot.agent.tools.message import MessageTool
        from xbot.utils.evaluator import evaluate_response

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
            response = await agent.process_direct(
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
            llm_call = agent.backend.call_for_auxiliary
            should_notify = await evaluate_response(
                response, job.payload.message, llm_call,
            )
            if should_notify:
                from xbot.bus.events import OutboundMessage
                await bus.publish_outbound(OutboundMessage(
                    channel=job.payload.channel or "cli",
                    chat_id=job.payload.to,
                    content=response,
                ))
        return response
    cron.on_job = on_cron_job

    # Create channel manager
    channels = ChannelManager(config, bus)

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        enabled = set(channels.enabled_channels)
        # Prefer the most recently updated non-internal session on an enabled channel.
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        # Fallback keeps prior behavior but remains explicit.
        return "cli", "direct"

    # Create heartbeat service
    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_heartbeat_target()

        async def _silent(*_args, **_kwargs):
            pass

        return await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from xbot.bus.events import OutboundMessage
        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return  # No external channel available to deliver to
        await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

    async def _heartbeat_llm_call(*args, **kwargs):
        """Defer backend access until runtime (after agent.initialize())."""
        return await agent.backend.call_for_auxiliary(*args, **kwargs)

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        llm_call=_heartbeat_llm_call,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
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
        from xbot.agent.alerting import AlertConfig, init_alert_service

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
        except Exception as e:
            import traceback
            console.print("\n[red]Error: Gateway crashed unexpectedly[/red]")
            console.print(traceback.format_exc())
            # Send critical alert
            await alert.alert_critical(e, "Gateway crashed unexpectedly")
        finally:
            await agent.close_mcp()
            heartbeat.stop()
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
    from loguru import logger

    from xbot.bus.queue import MessageBus
    from xbot.config.paths import get_cron_dir
    from xbot.cron.service import CronService

    config = _load_runtime_config(config, workspace)
    _print_deprecated_memory_window_notice(config)
    sync_workspace_templates(config.workspace_path)

    bus = MessageBus()

    # Create cron service for tool usage (no callback needed for CLI unless running)
    cron_store_path = get_cron_dir() / "jobs.json"
    cron = CronService(cron_store_path)

    if logs:
        logger.enable("xbot")
    else:
        logger.disable("xbot")

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

    agent_loop = _make_agent_runtime(
        config=config,
        bus=bus,
        workspace=config.workspace_path,
        cron_service=cron,
        session_manager=None,
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
                    message,
                    session_id,
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
        from xbot.bus.events import InboundMessage
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
            # Set spinner reference on permission handler for this session
            if isinstance(_permission_handler, InteractivePermissionHandler):
                _thinking_ref = _ThinkingSpinner(enabled=not logs)
                _permission_handler.set_thinking_spinner(_thinking_ref)

            bus_task = asyncio.create_task(agent_loop.run())
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

            outbound_task = asyncio.create_task(_consume_outbound())

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

                        clean_text, media_paths = _parse_media_from_input(user_input)
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
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
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
    from xbot.config.loader import load_config

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
    from xbot.config.paths import get_bridge_install_dir

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

    from xbot.config.loader import load_config
    from xbot.config.paths import get_runtime_subdir

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
    from xbot.config.loader import load_config

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
    from xbot.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} xbot Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        from xbot.providers.registry import PROVIDERS

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
# OAuth Login
# ============================================================================

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


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
    from xbot.providers.registry import PROVIDERS

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
