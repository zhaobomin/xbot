"""
Provider Registry — single source of truth for LLM provider metadata.

SDK Mode Only:
  This registry only contains providers compatible with Claude SDK Agent.
  All providers here use the Anthropic Messages API protocol.

Adding a new SDK-compatible provider:
  1. Add a ProviderSpec to PROVIDERS below.
  2. Add the provider name to SDK_COMPATIBLE_PROVIDER_NAMES in config/provider_registry.py.
  Done. The provider will be available for SDK mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider's metadata. See PROVIDERS below for real examples.

    Placeholders in env_extras values:
      {api_key}  — the user's API key
      {api_base} — api_base from config, or this spec's default_api_base
    """

    # identity
    name: str  # config field name, e.g. "anthropic"
    keywords: tuple[str, ...]  # model-name keywords for matching (lowercase)
    env_key: str  # API key env var, e.g. "ANTHROPIC_API_KEY"
    display_name: str = ""  # shown in `xbot status`

    # model prefixing (for display/routing)
    model_prefix: str = ""  # "anthropic" → model becomes "anthropic/{model}"
    skip_prefixes: tuple[str, ...] = ()  # don't prefix if model already starts with these

    # extra env vars, e.g. (("ZHIPUAI_API_KEY", "{api_key}"),)
    env_extras: tuple[tuple[str, str], ...] = ()

    # gateway detection
    is_gateway: bool = False  # routes any model (Aliyun Coding Plan, Alrun)
    is_local: bool = False  # local deployment (always False for SDK providers)
    is_oauth: bool = False  # OAuth-based auth (always False for SDK providers)
    detect_by_key_prefix: str = ""  # match api_key prefix
    detect_by_base_keyword: str = ""  # match substring in api_base URL
    default_api_base: str = ""  # fallback base URL

    # gateway behavior
    strip_model_prefix: bool = False  # strip "provider/" before API call
    provider_kwargs: dict[str, Any] = field(default_factory=dict)  # reserved provider-specific options

    # per-model param overrides, e.g. (("kimi-k2.5", {"temperature": 1.0}),)
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = ()

    # Provider supports cache_control on content blocks (Anthropic prompt caching)
    supports_prompt_caching: bool = False

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


# ---------------------------------------------------------------------------
# PROVIDERS — the registry. Order = priority. Copy any entry as template.
# ---------------------------------------------------------------------------

PROVIDERS: tuple[ProviderSpec, ...] = (
    # === Anthropic (native SDK support) ===================================
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        model_prefix="",
        skip_prefixes=(),
        env_extras=(),
        is_gateway=False,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="https://api.anthropic.com",
        strip_model_prefix=False,
        model_overrides=(),
        supports_prompt_caching=True,
    ),

    # === Aliyun Coding Plan: Anthropic Messages API compatible gateway =====
    # Supports models like glm-5, qwen-max, etc.
    ProviderSpec(
        name="aliyun_coding_plan",
        keywords=("qwen", "glm"),  # Match qwen-* and glm-* models
        env_key="ANTHROPIC_API_KEY",
        display_name="Aliyun Coding Plan",
        model_prefix="anthropic",  # Route through anthropic provider
        skip_prefixes=(),
        env_extras=(),
        is_gateway=True,
        detect_by_key_prefix="",
        detect_by_base_keyword="dashscope.aliyuncs.com",
        default_api_base="https://coding.dashscope.aliyuncs.com/apps/anthropic",
        strip_model_prefix=False,
        model_overrides=(),
        supports_prompt_caching=False,
    ),

    # === Alrun: Anthropic Messages API compatible gateway ==================
    ProviderSpec(
        name="alrun",
        keywords=(),  # No keywords - must be explicitly configured
        env_key="ANTHROPIC_API_KEY",
        display_name="Alrun",
        model_prefix="anthropic",  # Route through anthropic provider
        skip_prefixes=(),
        env_extras=(),
        is_gateway=True,
        detect_by_key_prefix="",
        detect_by_base_keyword="",
        default_api_base="",
        strip_model_prefix=True,  # Strip alrun-* prefix before API call
        model_overrides=(),
        supports_prompt_caching=False,
    ),
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def find_by_model(model: str) -> ProviderSpec | None:
    """Match a provider by model-name keyword (case-insensitive)."""
    model_lower = model.lower()
    model_normalized = model_lower.replace("-", "_")
    model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
    normalized_prefix = model_prefix.replace("-", "_")

    # Prefer explicit provider prefix
    for spec in PROVIDERS:
        if model_prefix and normalized_prefix == spec.name:
            return spec

    for spec in PROVIDERS:
        if any(
            kw in model_lower or kw.replace("-", "_") in model_normalized for kw in spec.keywords
        ):
            return spec
    return None


def find_gateway(
    provider_name: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
) -> ProviderSpec | None:
    """Detect gateway provider.

    Priority:
      1. provider_name — if it maps to a gateway spec, use it directly.
      2. api_key prefix — match by key prefix.
      3. api_base keyword — match substring in URL.
    """
    # 1. Direct match by config key
    if provider_name:
        spec = find_by_name(provider_name)
        if spec and spec.is_gateway:
            return spec

    # 2. Auto-detect by api_key prefix / api_base keyword
    for spec in PROVIDERS:
        if spec.detect_by_key_prefix and api_key and api_key.startswith(spec.detect_by_key_prefix):
            return spec
        if spec.detect_by_base_keyword and api_base and spec.detect_by_base_keyword in api_base:
            return spec

    return None


def find_by_name(name: str) -> ProviderSpec | None:
    """Find a provider spec by config field name, e.g. "anthropic"."""
    for spec in PROVIDERS:
        if spec.name == name:
            return spec
    return None
