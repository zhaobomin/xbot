"""Bootstrap helpers for starting the WebUI adapter."""

from __future__ import annotations

from xbot.channels.manager import ChannelManager
from xbot.interfaces.webui.services import ServiceContainer
from xbot.platform.bus.queue import MessageBus
from xbot.platform.config.loader import save_config
from xbot.runtime.session.manager import SessionManager
from xbot.runtime.system.cron.service import CronService
from xbot.runtime.system.heartbeat.service import HeartbeatService


async def _heartbeat_llm_call(*_args, **_kwargs):
    raise RuntimeError("Heartbeat LLM call unavailable until runtime backend is initialized")


def build_services(config, *, make_runtime) -> ServiceContainer:
    """Create the minimal service graph used by the WebUI adapter."""
    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    bus = MessageBus()
    session_manager = SessionManager(workspace)
    cron = CronService(workspace / "cron" / "jobs.json")
    channel_manager = ChannelManager(config, bus)
    heartbeat = HeartbeatService(
        workspace=workspace,
        llm_call=_heartbeat_llm_call,
        interval_s=config.gateway.heartbeat.interval_s,
        enabled=config.gateway.heartbeat.enabled,
        on_channel_health=channel_manager.check_channels_health,
    )
    agent = make_runtime(
        config=config,
        bus=bus,
        workspace=workspace,
        cron_service=cron,
        session_manager=session_manager,
        permission_handler=None,
    )
    return ServiceContainer(
        config=config,
        bus=bus,
        agent=agent,
        session_manager=session_manager,
        cron=cron,
        heartbeat=heartbeat,
        save_config=save_config,
        data_dir=workspace / ".webui",
        metadata={"channel_manager": channel_manager},
    )
