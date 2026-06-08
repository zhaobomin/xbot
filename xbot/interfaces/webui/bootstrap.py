"""Bootstrap helpers for starting the WebUI adapter."""

from __future__ import annotations

from pydantic import SecretStr

from xbot.channels.manager import ChannelManager
from xbot.interfaces.webui.services import ServiceContainer
from xbot.platform.bus.queue import MessageBus
from xbot.platform.config.loader import save_config
from xbot.platform.config.schema import ProviderConfig
from xbot.runtime.session.conversation_store import ConversationStore
from xbot.runtime.system.cron.service import CronService
from xbot.runtime.system.heartbeat.service import HeartbeatService


async def _heartbeat_llm_call(*_args, **_kwargs):
    raise RuntimeError("Heartbeat LLM call unavailable until runtime backend is initialized")


def _migrate_legacy_providers(config, save: bool = True) -> bool:
    """迁移旧版固定供应商配置到 custom_providers。

    将 aliyun_coding_plan 和 alrun 从配置顶层字段迁移到 custom_providers 字典。
    返回是否进行了迁移。
    """
    migrated = False

    # 检查是否有旧版配置需要迁移
    legacy_names = ["aliyun_coding_plan", "alrun"]

    for name in legacy_names:
        # 尝试从配置对象中获取旧字段
        legacy_config = getattr(config.providers, name, None)
        if legacy_config is None:
            continue

        # 检查是否有实际配置（有 API key 或 api_base）
        has_key = False
        if hasattr(legacy_config, "api_key"):
            key = legacy_config.api_key
            if hasattr(key, "get_secret_value"):
                has_key = bool(key.get_secret_value())
            else:
                has_key = bool(key)

        has_base = hasattr(legacy_config, "api_base") and legacy_config.api_base

        if has_key or has_base:
            # 迁移到 custom_providers
            if name not in config.providers.custom_providers:
                # 创建新的 ProviderConfig
                new_config = ProviderConfig(
                    api_key=legacy_config.api_key if hasattr(legacy_config, "api_key") else SecretStr(""),
                    api_base=legacy_config.api_base if hasattr(legacy_config, "api_base") else None,
                    extra_headers=legacy_config.extra_headers if hasattr(legacy_config, "extra_headers") else None,
                    models=legacy_config.models if hasattr(legacy_config, "models") else [],
                )
                config.providers.custom_providers[name] = new_config
                migrated = True

        # 删除旧字段（如果存在）
        try:
            delattr(config.providers, name)
        except (AttributeError, ValueError):
            pass

    if migrated and save:
        save_config(config)

    return migrated


def build_services(config, *, make_runtime) -> ServiceContainer:
    """Create the minimal service graph used by the WebUI adapter."""
    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)

    # 迁移旧版供应商配置
    _migrate_legacy_providers(config)

    bus = MessageBus()
    conversation_store = ConversationStore(workspace)
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
        execution_cwd=workspace,
        cron_service=cron,
        conversation_store=conversation_store,
        permission_handler=None,
        run_mode="webui",
    )
    return ServiceContainer(
        config=config,
        bus=bus,
        agent=agent,
        conversation_store=conversation_store,
        cron=cron,
        heartbeat=heartbeat,
        save_config=save_config,
        data_dir=workspace / ".webui",
        metadata={"channel_manager": channel_manager},
    )
