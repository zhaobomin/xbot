"""Compatibility module alias for agent protocol."""

import sys

from xbot.runtime.core import protocol as _impl

sys.modules[__name__] = _impl
