from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class ServiceSection(BaseModel):
    name: str = "xbot-codex"


class TelegramConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    group_policy: str = "mention"


class FeishuConfig(BaseModel):
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    allow_from: list[str] = Field(default_factory=list)
    react_emoji: str = "THUMBSUP"
    group_policy: str = "mention"
    reply_to_message: bool = False


class ChannelsConfig(BaseModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)


class GatewayConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 18791


class CodexConfig(BaseModel):
    binary_path: str = "codex"
    profile: str | None = None
    default_model: str | None = None
    default_mode: str | None = None
    home: str | None = None
    proxy: str | None = None
    no_proxy: str | None = None
    workdir_root: str = "/tmp/xbot-codex"
    allowed_models: list[str] = Field(default_factory=list)
    allowed_modes: list[str] = Field(default_factory=lambda: ["suggest", "auto", "full-auto", "dangerous"])


class RuntimeConfig(BaseModel):
    idle_timeout_seconds: int = 3600


class LoggingConfig(BaseModel):
    level: str = "INFO"


class ServiceConfig(BaseModel):
    service_name: str = "xbot-codex"
    service: ServiceSection = Field(default_factory=ServiceSection)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    codex: CodexConfig = Field(default_factory=CodexConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_config(path: str | Path | None = None) -> ServiceConfig:
    raw: dict = {}
    if path is not None:
        config_path = Path(path)
        if config_path.exists():
            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    service = raw.get("service", {})
    if "name" in service and "service_name" not in raw:
        raw["service_name"] = service["name"]

    config = ServiceConfig.model_validate(raw)

    env_telegram_token = os.getenv("XBOT_CODEX_TELEGRAM_TOKEN")
    if env_telegram_token:
        config.channels.telegram.token = env_telegram_token
    env_feishu_app_id = os.getenv("XBOT_CODEX_FEISHU_APP_ID")
    if env_feishu_app_id:
        config.channels.feishu.app_id = env_feishu_app_id
    env_feishu_app_secret = os.getenv("XBOT_CODEX_FEISHU_APP_SECRET")
    if env_feishu_app_secret:
        config.channels.feishu.app_secret = env_feishu_app_secret
    env_codex_binary = os.getenv("XBOT_CODEX_CODEX_BINARY")
    if env_codex_binary:
        config.codex.binary_path = env_codex_binary
    env_codex_profile = os.getenv("XBOT_CODEX_CODEX_PROFILE")
    if env_codex_profile:
        config.codex.profile = env_codex_profile
    env_codex_home = os.getenv("XBOT_CODEX_CODEX_HOME")
    if env_codex_home:
        config.codex.home = env_codex_home
    env_codex_proxy = os.getenv("XBOT_CODEX_HTTP_PROXY")
    if env_codex_proxy:
        config.codex.proxy = env_codex_proxy
    env_codex_no_proxy = os.getenv("XBOT_CODEX_NO_PROXY")
    if env_codex_no_proxy:
        config.codex.no_proxy = env_codex_no_proxy
    return config
