"""Agent core module.

Keep package exports lazy to avoid import-time side effects while loading submodules.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "CapabilityCatalog",
    "CapabilityPolicy",
    "ContextBuilder",
    "MemoryStore",
    "SkillsLoader",
    # Core types
    "AgentResponse",
    "AgentContext",
    # Unified service
    "AgentService",
]


def __getattr__(name: str) -> Any:
    lazy_exports: dict[str, tuple[str, str]] = {
        "ContextBuilder": ("xbot.runtime.core.context.builder", "ContextBuilder"),
        "CapabilityCatalog": ("xbot.capabilities.catalog", "CapabilityCatalog"),
        "CapabilityPolicy": ("xbot.capabilities.policy", "CapabilityPolicy"),
        "MemoryStore": ("xbot.memory.store", "MemoryStore"),
        "SkillsLoader": ("xbot.capabilities.skills_loader", "SkillsLoader"),
        "AgentResponse": ("xbot.runtime.core.protocol", "AgentResponse"),
        "AgentContext": ("xbot.runtime.core.protocol", "AgentContext"),
        "AgentService": ("xbot.runtime.core.service", "AgentService"),
    }
    module_attr = lazy_exports.get(name)
    if module_attr is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = module_attr
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
