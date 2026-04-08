"""Compatibility view over the canonical provider registry.

`xbot.providers.registry` is the single source of truth for provider
metadata. This module projects that registry into the config-oriented shape
needed by config validation and Claude SDK compatibility checks.

All providers in the registry are SDK-compatible (use Anthropic Messages API).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from xbot.platform.providers.registry import PROVIDERS, find_by_name

# Set of SDK-compatible provider names (all providers in the slimmed-down registry)
SDK_COMPATIBLE_PROVIDER_NAMES = frozenset(spec.name for spec in PROVIDERS)


@dataclass(frozen=True)
class ProviderSpec:
    """Config-facing provider metadata derived from the canonical registry."""

    name: str
    display_name: str
    protocol: Literal["anthropic"]
    default_base_url: str
    supported_by_sdk: bool  # Always True for all providers in the registry


def _default_base_url_for(name: str, default_api_base: str) -> str:
    return default_api_base


PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    spec.name: ProviderSpec(
        name=spec.name,
        display_name=spec.display_name,
        protocol="anthropic",  # All providers in registry use Anthropic protocol
        default_base_url=_default_base_url_for(spec.name, spec.default_api_base),
        supported_by_sdk=True,  # All providers in registry are SDK-compatible
    )
    for spec in PROVIDERS
}


def get_provider_spec(name: str) -> ProviderSpec | None:
    """Get provider specification by name."""
    return PROVIDER_REGISTRY.get(name)


def get_sdk_compatible_providers() -> list[str]:
    """Get SDK-compatible providers in canonical registry order."""
    return [spec.name for spec in PROVIDERS]


def is_provider_sdk_compatible(name: str) -> bool:
    """Check if a provider is compatible with Claude SDK Agent."""
    return find_by_name(name) is not None


def get_all_provider_names() -> list[str]:
    """Get all provider names in canonical registry order."""
    return [spec.name for spec in PROVIDERS]
