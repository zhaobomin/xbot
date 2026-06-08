"""LLM provider module.

Data types (LLMResponse, ToolCallRequest, GenerationSettings) are in base.py.
Provider registry and metadata are in registry.py.

Note: LLMProvider abstract class has been removed. xbot now uses
Claude SDK directly for all LLM interactions via Anthropic Messages API.
"""

from __future__ import annotations

from xbot.platform.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from xbot.platform.providers.registry import (
    PROVIDERS,
    ProviderSpec,
    find_by_model,
    find_by_name,
    find_gateway,
)

__all__ = [
    # Data types
    "GenerationSettings",
    "LLMResponse",
    "ToolCallRequest",
    # Registry
    "PROVIDERS",
    "ProviderSpec",
    "find_by_model",
    "find_by_name",
    "find_gateway",
]


