"""Compatibility module alias for agent monitoring alerting.

Preferred location: ``xbot.runtime.system.monitoring.alerting``.
"""

import sys

from xbot.runtime.system.monitoring import alerting as _impl

sys.modules[__name__] = _impl
