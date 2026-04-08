"""Compatibility module alias for webui cli."""

import sys

from xbot.interfaces.webui import cli as _impl

sys.modules[__name__] = _impl
