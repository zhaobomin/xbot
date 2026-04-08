"""Compatibility module alias for agent context builder."""

import sys

from xbot.runtime.core.context import builder as _impl

sys.modules[__name__] = _impl
