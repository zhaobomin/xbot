"""Unified stdlib logging bootstrap for xbot."""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from threading import RLock
from typing import IO

_LOCK = RLock()
_HANDLER: logging.Handler | None = None
_FILTER: logging.Filter | None = None
_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_FORMAT_WITH_CID = "%(asctime)s | %(levelname)s | %(name)s | [%(correlation_id)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")

# ---------------------------------------------------------------------------
# Context variables for request-scoped correlation
# ---------------------------------------------------------------------------
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
session_key_var: ContextVar[str] = ContextVar("session_key", default="")


class CorrelationFilter(logging.Filter):
    """Inject correlation_id and session_key from ContextVar into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get("")  # type: ignore[attr-defined]
        record.session_key = session_key_var.get("")  # type: ignore[attr-defined]
        return True


class StructuredFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        entry: dict[str, object] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        cid = getattr(record, "correlation_id", "")
        if cid:
            entry["correlation_id"] = cid
        sk = getattr(record, "session_key", "")
        if sk:
            entry["session_key"] = sk
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    """Return a stdlib logger for xbot modules."""
    return logging.getLogger(name)


def configure_logging(
    *,
    level: int | str = logging.INFO,
    stream: IO[str] | None = None,
    structured: bool | None = None,
) -> None:
    """Configure root logging for xbot.

    Repeated calls replace only the xbot-managed stream handler and keep
    external handlers like pytest's caplog attached to root.

    Args:
        level: Log level (int or string like ``"DEBUG"``).
        stream: Output stream (defaults to ``sys.stderr``).
        structured: If ``True`` use JSON output. If ``None`` (default),
            auto-detect from ``XBOT_LOG_FORMAT`` env var.
    """
    normalized_level = _normalize_level(level)
    output = stream if stream is not None else sys.stderr

    if structured is None:
        structured = os.environ.get("XBOT_LOG_FORMAT", "").lower() == "json"

    with _LOCK:
        root_logger = logging.getLogger()
        root_logger.setLevel(normalized_level)
        package_logger = logging.getLogger("xbot")
        package_logger.disabled = False
        package_logger.setLevel(logging.NOTSET)

        global _HANDLER, _FILTER
        if _HANDLER is not None:
            root_logger.removeHandler(_HANDLER)
        if _FILTER is not None:
            root_logger.removeFilter(_FILTER)

        handler = logging.StreamHandler(output)
        handler.setLevel(normalized_level)

        if structured:
            handler.setFormatter(StructuredFormatter(datefmt=_DATEFMT))
        else:
            handler.setFormatter(logging.Formatter(_FORMAT_WITH_CID, _DATEFMT))

        corr_filter = CorrelationFilter()
        handler.addFilter(corr_filter)

        root_logger.addHandler(handler)
        _HANDLER = handler
        _FILTER = corr_filter


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
