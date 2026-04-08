"""Compatibility module alias for agent client_pool."""

import sys

from xbot.runtime.core import client_pool as _impl

sys.modules[__name__] = _impl
