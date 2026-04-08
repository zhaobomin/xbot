"""Compatibility module alias for agent context commands."""

import sys

from xbot.runtime.core.context import commands as _impl

sys.modules[__name__] = _impl
