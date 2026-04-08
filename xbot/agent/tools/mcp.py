"""Compatibility module alias for agent MCP tools.

Preferred location: ``xbot.tools.mcp``.
"""

import sys

from xbot.tools import mcp as _impl

sys.modules[__name__] = _impl
