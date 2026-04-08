"""Compatibility module alias for task supervisor.

Preferred location: ``xbot.runtime.core.task_supervisor``.
"""

import sys

from xbot.runtime.core import task_supervisor as _impl

sys.modules[__name__] = _impl
