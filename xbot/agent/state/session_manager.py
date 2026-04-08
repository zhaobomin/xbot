"""Compatibility module alias for agent state session manager.

Preferred location: ``xbot.runtime.state.session_manager``.
"""

import sys

from xbot.runtime.state import session_manager as _impl

sys.modules[__name__] = _impl
