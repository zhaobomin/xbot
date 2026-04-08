"""Capabilities and skills package."""

from xbot.capabilities.catalog import CapabilityCatalog, canonical_tool_name
from xbot.capabilities.handoff import HandoffPolicy
from xbot.capabilities.policy import CapabilityPolicy, CapabilityResolution
from xbot.capabilities.tool_adapter import ToolAdapter

__all__ = [
    "CapabilityCatalog",
    "canonical_tool_name",
    "HandoffPolicy",
    "CapabilityPolicy",
    "CapabilityResolution",
    "ToolAdapter",
]
