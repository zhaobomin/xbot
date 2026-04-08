"""Compatibility module alias for agent context model_manager."""

import sys

from xbot.runtime.core.context import model_manager as _impl

sys.modules[__name__] = _impl
