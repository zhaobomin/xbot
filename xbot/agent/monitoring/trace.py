"""Compatibility module alias for agent monitoring trace.

Preferred location: ``xbot.runtime.system.monitoring.trace``.
"""

import sys

from xbot.runtime.system.monitoring import trace as _impl

sys.modules[__name__] = _impl
