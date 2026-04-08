"""Compatibility module alias for agent monitoring health.

Preferred location: ``xbot.runtime.system.monitoring.health``.
"""

import sys

from xbot.runtime.system.monitoring import health as _impl

sys.modules[__name__] = _impl
