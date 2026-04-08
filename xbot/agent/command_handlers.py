"""Compatibility module alias for agent command_handlers."""

import sys

from xbot.runtime.core import command_handlers as _impl

sys.modules[__name__] = _impl
