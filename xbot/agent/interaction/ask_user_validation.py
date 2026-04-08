"""Shared validation helpers for AskUserQuestion."""

from __future__ import annotations

import re

VALIDATION_MODE_ALIASES = {
    "open": "suggested",
    "loose": "suggested",
}

CANONICAL_MODES = {"strict", "suggested"}


def normalize_validation_mode(mode: str | None) -> str:
    """Return the canonical AskUserQuestion validation mode."""
    normalized = str(mode or "").strip().lower()
    if normalized in CANONICAL_MODES:
        return normalized
    return VALIDATION_MODE_ALIASES.get(normalized, "suggested")


def split_answers(raw: str | None) -> list[str]:
    """Split answers using comma-like separators only."""
    if not raw:
        return []
    parts = re.split(r"[，,、]+", raw.strip())
    return [part.strip() for part in parts if part.strip()]


def match_option(candidate: str | None, options: list[str]) -> str | None:
    """Match a candidate against options using exact match, then unique prefix."""
    normalized = str(candidate or "").strip().lower()
    if not normalized:
        return None

    for option in options:
        if normalized == str(option).strip().lower():
            return option

    matches = [
        option
        for option in options
        if str(option).strip().lower().startswith(normalized)
    ]
    if len(matches) == 1:
        return matches[0]
    return None
