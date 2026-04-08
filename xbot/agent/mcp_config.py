"""Compatibility module alias for agent mcp_config."""

import sys

from xbot.runtime.core import mcp_config as _impl

sys.modules[__name__] = _impl
