"""Compatibility module alias for webui session_keys."""

import sys

from xbot.interfaces.webui import session_keys as _impl

sys.modules[__name__] = _impl
