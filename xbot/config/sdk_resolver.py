"""Claude SDK model/provider resolution utilities.

This module centralizes how xbot resolves provider + model for the
Claude SDK backend so config validation and runtime use identical rules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from xbot.config.provider_registry import SDK_COMPATIBLE_PROVIDER_NAMES

if TYPE_CHECKING:
    from xbot.config.schema import Config


_SDK_PROVIDER_PRIORITY = ("anthropic", "aliyun_coding_plan", "alrun")
_LEGACY_MODEL_PREFIXES = {
    "anthropic",
    "aliyun_coding_plan",
    "alrun",
    "openrouter",
}


def detect_provider_from_model(model: str) -> str:
    """Infer SDK provider from model naming cues."""
    model_lower = model.strip().lower()
    if model_lower.startswith("alrun-"):
        return "alrun"
    if "claude" in model_lower:
        return "anthropic"
    if "qwen" in model_lower or "glm" in model_lower:
        return "aliyun_coding_plan"
    return "anthropic"


def normalize_sdk_model_name(model: str, provider: str) -> str:
    """Normalize historical model naming formats for Claude SDK."""
    out = model.strip()
    if provider == "alrun" and out.startswith("alrun-"):
        out = out[len("alrun-"):]

    if "/" in out:
        head, tail = out.split("/", 1)
        normalized_head = head.lower().replace("-", "_")
        if (
            normalized_head in SDK_COMPATIBLE_PROVIDER_NAMES
            or normalized_head in _LEGACY_MODEL_PREFIXES
        ):
            out = tail
    return out


def resolve_sdk_provider_and_model(
    config: "Config",
    *,
    require_api_key: bool = False,
) -> tuple[str, str]:
    """Resolve final provider+model for Claude SDK backend.

    Args:
        config: Root xbot config
        require_api_key: If True, fail when resolved provider has no api_key
    """
    configured_provider = config.agents.defaults.provider
    configured_model = (config.agents.defaults.model or "").strip()
    if not configured_model:
        raise ValueError("agents.defaults.model must not be empty")

    if configured_provider == "auto":
        detected = detect_provider_from_model(configured_model)
        if _has_api_key(config, detected):
            provider_name = detected
        else:
            provider_name = next(
                (name for name in _SDK_PROVIDER_PRIORITY if _has_api_key(config, name)),
                detected,
            )
    else:
        provider_name = configured_provider

    if provider_name not in SDK_COMPATIBLE_PROVIDER_NAMES:
        compatible = ", ".join(sorted(SDK_COMPATIBLE_PROVIDER_NAMES))
        raise ValueError(
            f"Provider '{provider_name}' is not compatible with Claude SDK. "
            f"Compatible providers: {compatible}"
        )

    if require_api_key and not _has_api_key(config, provider_name):
        raise ValueError(
            f"API key not configured for provider '{provider_name}'. "
            f"Please set providers.{provider_name}.api_key in config.json"
        )

    return provider_name, normalize_sdk_model_name(configured_model, provider_name)


def _has_api_key(config: "Config", provider_name: str) -> bool:
    provider_attr = provider_name.replace("-", "_")
    provider_config = getattr(config.providers, provider_attr, None)
    return bool(provider_config and provider_config.api_key)
