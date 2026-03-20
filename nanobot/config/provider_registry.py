"""Compatibility view over the canonical provider registry.

`nanobot.providers.registry` remains the single source of truth for provider
metadata used across routing, matching, and LiteLLM behavior. This module
projects that registry into the smaller config-oriented shape needed by config
validation and Claude SDK compatibility checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nanobot.providers.registry import PROVIDERS, find_by_name

SDK_COMPATIBLE_PROVIDER_NAMES = frozenset(
    {
        "anthropic",
        "aliyun_coding_plan",
        "alrun",
    }
)

SDK_BASE_URL_OVERRIDES = {
    "anthropic": "https://api.anthropic.com",
}


@dataclass(frozen=True)
class ProviderSpec:
    """Config-facing provider metadata derived from the canonical registry."""

    name: str
    display_name: str
    protocol: Literal["anthropic", "litellm"]
    default_base_url: str
    supported_by_sdk: bool


def _protocol_for(name: str) -> Literal["anthropic", "litellm"]:
    return "anthropic" if name in SDK_COMPATIBLE_PROVIDER_NAMES else "litellm"


def _default_base_url_for(name: str, default_api_base: str) -> str:
    return SDK_BASE_URL_OVERRIDES.get(name, default_api_base)


PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    spec.name: ProviderSpec(
        name=spec.name,
        display_name=spec.display_name,
        protocol=_protocol_for(spec.name),
        default_base_url=_default_base_url_for(spec.name, spec.default_api_base),
        supported_by_sdk=spec.name in SDK_COMPATIBLE_PROVIDER_NAMES,
    )
    for spec in PROVIDERS
}


def get_provider_spec(name: str) -> ProviderSpec | None:
    """Get provider specification by name."""
    return PROVIDER_REGISTRY.get(name)


def get_sdk_compatible_providers() -> list[str]:
    """Get SDK-compatible providers in canonical registry order."""
    return [spec.name for spec in PROVIDERS if spec.name in SDK_COMPATIBLE_PROVIDER_NAMES]


def is_provider_sdk_compatible(name: str) -> bool:
    """Check if a provider is compatible with Claude SDK Agent."""
    return name in SDK_COMPATIBLE_PROVIDER_NAMES and find_by_name(name) is not None


def get_all_provider_names() -> list[str]:
    """Get all provider names in canonical registry order."""
    return [spec.name for spec in PROVIDERS]
