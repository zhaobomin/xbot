"""Compatibility module alias for agent state machine.

Preferred location: ``xbot.runtime.state.machine``.
"""

import sys

from xbot.runtime.state import machine as _impl

sys.modules[__name__] = _impl
