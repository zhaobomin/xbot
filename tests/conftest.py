"""Pytest session bootstrap helpers."""

from __future__ import annotations

import importlib
import sys

import pytest


def _remove_beartype_path_hook() -> None:
    """Remove beartype claw path hook when preloaded by host shell/profile.

    Some environments pre-install beartype import hooks globally, which can
    trigger circular imports during test module loading. Tests do not rely on
    these hooks, so we strip them for deterministic imports.
    """
    original_len = len(sys.path_hooks)
    sys.path_hooks[:] = [
        hook for hook in sys.path_hooks if not getattr(hook, "__beartype_is_path_hook__", False)
    ]
    if len(sys.path_hooks) != original_len:
        sys.path_importer_cache.clear()
        importlib.invalidate_caches()


_remove_beartype_path_hook()
