"""Compatibility module alias for agent hooks.

Preferred location: ``xbot.runtime.core.hooks``.
"""

import sys

from xbot.runtime.core import hooks as _impl

sys.modules[__name__] = _impl
