"""Compatibility module alias for webui app."""

import sys

from xbot.interfaces.webui import app as _impl

sys.modules[__name__] = _impl
