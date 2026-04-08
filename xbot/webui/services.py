"""Compatibility module alias for webui services."""

import sys

from xbot.interfaces.webui import services as _impl

sys.modules[__name__] = _impl
