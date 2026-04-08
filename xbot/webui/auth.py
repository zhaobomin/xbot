"""Compatibility module alias for webui auth."""

import sys

from xbot.interfaces.webui import auth as _impl

sys.modules[__name__] = _impl
