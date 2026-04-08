"""Compatibility module alias for agent service.

Preferred location: ``xbot.runtime.core.service``.
"""

import sys

from xbot.runtime.core import service as _impl

sys.modules[__name__] = _impl
