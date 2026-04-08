"""Compatibility module alias for CLI commands.

Preferred location: ``xbot.interfaces.cli.commands``.
"""

import sys

from xbot.interfaces.cli import commands as _impl

sys.modules[__name__] = _impl
