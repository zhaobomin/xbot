"""Compatibility module alias for crew package.

Preferred location: ``xbot.crew``.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

import xbot.crew as _impl


def _alias_submodules() -> None:
    """Ensure legacy ``xbot.agent.crew.*`` imports share module identity.

    Without this bridge, importing both ``xbot.agent.crew`` and ``xbot.crew``
    can create duplicate module objects and break ``isinstance`` checks.
    """

    prefix = f"{_impl.__name__}."
    legacy_prefix = f"{__name__}."

    for module_info in pkgutil.walk_packages(_impl.__path__, prefix):
        module = importlib.import_module(module_info.name)
        legacy_name = module_info.name.replace(prefix, legacy_prefix, 1)
        sys.modules[legacy_name] = module


_alias_submodules()
sys.modules[__name__] = _impl
