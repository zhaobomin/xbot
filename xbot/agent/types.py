"""Compatibility module alias for agent types."""

import sys

from xbot.runtime.core import types as _impl

sys.modules[__name__] = _impl
