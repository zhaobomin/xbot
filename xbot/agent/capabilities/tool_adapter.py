"""Compatibility module alias for agent tool adapter.

Preferred location: ``xbot.capabilities.tool_adapter``.
"""

import sys

from xbot.capabilities import tool_adapter as _impl

sys.modules[__name__] = _impl
