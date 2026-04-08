"""Compatibility module alias for webui bootstrap."""

import sys

from xbot.interfaces.webui import bootstrap as _impl

sys.modules[__name__] = _impl
