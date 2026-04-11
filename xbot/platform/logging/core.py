"""Unified stdlib logging bootstrap for xbot."""

from __future__ import annotations

import logging
import sys
from threading import RLock
from typing import IO

_LOCK = RLock()
_HANDLER: logging.Handler | None = None
_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")

def get_logger(name: str) -> logging.Logger:
    """Return a stdlib logger for xbot modules."""
    return logging.getLogger(name)


def configure_logging(
    *,
    level: int | str = logging.INFO,
    stream: IO[str] | None = None,
) -> None:
    """Configure xbot logging without mutating global root logger level.

    Repeated calls replace only the xbot-managed stream handler and keep
    external handlers like pytest's caplog attached to root.
    """
    normalized_level = _normalize_level(level)
    output = stream if stream is not None else sys.stderr

    with _LOCK:
        root_logger = logging.getLogger()
        package_logger = logging.getLogger("xbot")
        package_logger.disabled = False
        package_logger.setLevel(normalized_level)

        global _HANDLER
        if _HANDLER is not None:
            root_logger.removeHandler(_HANDLER)

        handler = logging.StreamHandler(output)
        handler.setLevel(normalized_level)
        handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root_logger.addHandler(handler)
        _HANDLER = handler


def set_package_logging_enabled(
    enabled: bool,
    *,
    package: str = "xbot",
    enabled_level: int | str = logging.DEBUG,
) -> None:
    """Enable or suppress xbot package logs without affecting third-party logs."""
    package_logger = logging.getLogger(package)
    package_logger.disabled = False
    if enabled:
        package_logger.setLevel(_normalize_level(enabled_level))
    else:
        package_logger.setLevel(logging.CRITICAL + 1)


def _normalize_level(level: int | str) -> int:
    if isinstance(level, str):
        resolved = logging.getLevelName(level.upper())
        if isinstance(resolved, int):
            return resolved
        raise ValueError(f"Unknown log level: {level}")
    return int(level)
