"""Lightweight secret pattern scanner for memory content."""
from __future__ import annotations

import re

from xbot.logging import get_logger

logger = get_logger(__name__)

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    (
        "SSH Private Key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    ("GitHub Token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}")),
    ("Slack Token", re.compile(r"xox[bpas]-[a-zA-Z0-9\-]{10,}")),
    (
        "JWT Token",
        re.compile(
            r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"
        ),
    ),
    (
        "Generic API Key",
        re.compile(
            r"(?:api[_-]?key|api[_-]?secret|api[_-]?token|secret[_-]?key)"
            r"\s*[=:]\s*['\"]?[A-Za-z0-9_\-/.+]{20,}",
            re.IGNORECASE,
        ),
    ),
    (
        "Generic Password",
        re.compile(
            r"(?:password|passwd|pwd)\s*[=:]\s*['\"]?[^\s'\"]{8,}",
            re.IGNORECASE,
        ),
    ),
]


def scan_for_secrets(content: str) -> list[str]:
    """Return list of detected secret type labels. Empty list means clean."""
    found: list[str] = []
    for label, pattern in _PATTERNS:
        if pattern.search(content):
            found.append(label)
    return found
