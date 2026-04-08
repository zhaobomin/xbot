"""Compatibility facade for cron service.

Preferred location: ``xbot.runtime.system.cron.service``.
"""

from xbot.runtime.system.cron.service import *  # noqa: F403
from xbot.runtime.system.cron.service import _compute_next_run, _now_ms  # noqa: F401
