from __future__ import annotations

import warnings
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.claude_sdk_loop import create_agent
from nanobot.config.provider_registry import (
    PROVIDER_REGISTRY,
    get_all_provider_names,
    get_provider_spec,
    get_sdk_compatible_providers,
    is_provider_sdk_compatible,
)
from nanobot.config.schema import Config
from nanobot.providers.registry import PROVIDERS


def test_config_provider_registry_uses_core_provider_names_in_order() -> None:
    assert get_all_provider_names() == [spec.name for spec in PROVIDERS]
    assert list(PROVIDER_REGISTRY.keys()) == [spec.name for spec in PROVIDERS]


def test_config_provider_registry_derives_defaults_from_core_registry() -> None:
    anthropic = get_provider_spec("anthropic")
    aliyun = get_provider_spec("aliyun_coding_plan")
    openrouter = get_provider_spec("openrouter")

    assert anthropic is not None
    assert anthropic.default_base_url == "https://api.anthropic.com"
    assert anthropic.supported_by_sdk is True

    assert aliyun is not None
    assert aliyun.default_base_url == "https://coding.dashscope.aliyuncs.com/apps/anthropic"
    assert aliyun.supported_by_sdk is True

    assert openrouter is not None
    assert openrouter.default_base_url == "https://openrouter.ai/api/v1"
    assert openrouter.supported_by_sdk is False


def test_sdk_compatible_provider_list_is_derived_from_registry() -> None:
    assert get_sdk_compatible_providers() == [
        "aliyun_coding_plan",
        "alrun",
        "anthropic",
    ]
    assert is_provider_sdk_compatible("anthropic") is True
    assert is_provider_sdk_compatible("openrouter") is False


def test_create_agent_emits_deprecation_warning_for_legacy_factory() -> None:
    config = Config()
    bus = MagicMock()
    workspace = Path("/tmp/nanobot-test")
    provider = MagicMock()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        create_agent(
            bus=bus,
            config=config,
            workspace=workspace,
            provider=provider,
        )

    assert any(issubclass(item.category, DeprecationWarning) for item in caught)
