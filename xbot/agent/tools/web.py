"""Compatibility module alias for agent web tools.

Preferred location: ``xbot.tools.web``.
"""

import sys

from xbot.tools import web as _impl

sys.modules[__name__] = _impl
