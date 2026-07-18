from __future__ import annotations

import hashlib
import re

from scripts.review.common import Category, Finding, make_sig_key

# Matches ``setTimeout(<callback>, <delay>)`` where the callback is a reconnect
# or retry routine. Calls with any other callback are ignored.
_SET_TIMEOUT_RE = re.compile(r"setTimeout\s*\(\s*(reconnect|retry)\b")

# Matches a ``clearTimeout(...)`` call. We only care about presence within the
# same function body preceding the new ``setTimeout``.
_CLEAR_TIMEOUT_RE = re.compile(r"clearTimeout\s*\(")

_FUNC_RE = re.compile(r"\b(?:function|export\s+function)\s+(\w+)\s*\(")
_ARROW_RE = re.compile(
    r"\b(?:const|let|var)\s+(\w+)\s*(?:<[^>]*>)?\s*=\s*(?:async\s*)?\(?[^=]*=>"
)


def _enclosing_func(lines: list[str], idx: int) -> str:
    """Best-effort name of the function enclosing line *idx* by scanning up."""
    for i in range(idx, -1, -1):
        m = _FUNC_RE.search(lines[i])
        if m:
            return m.group(1)
        m = _ARROW_RE.search(lines[i])
        if m:
            return m.group(1)
    return "<module>"


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    lines = src.splitlines()

    findings: list[Finding] = []
    title = "setTimeout(reconnect/retry) without clearing the previous timer"
    for i, line in enumerate(lines, start=1):
        m = _SET_TIMEOUT_RE.search(line)
        if not m:
            continue
        func_name = _enclosing_func(lines, i - 1)
        # Look backwards for a clearTimeout call within the same function body.
        # Stop if we cross into a previous function definition.
        cleared = False
        for j in range(i - 2, -1, -1):
            prev = lines[j]
            if _FUNC_RE.search(prev) or _ARROW_RE.search(prev):
                break
            if _CLEAR_TIMEOUT_RE.search(prev):
                cleared = True
                break
        if cleared:
            continue
        cb = m.group(1)
        detail = f"func: {func_name}\nsetTimeout({cb}, ...) at line {i} without preceding clearTimeout"
        fid = hashlib.md5(f"{path}:{i}:reconnect_race".encode()).hexdigest()[:8]
        findings.append(
            Finding(
                id=f"reconnect_race:{fid}",
                sig_key=make_sig_key("reconnect_race", func_name, title),
                severity="P1",
                file=path,
                line=i,
                category=Category.RECONNECT_RACE.value,
                title=title,
                detail=detail,
                suggestion="clearTimeout(timer) before scheduling a new reconnect/retry timer",
                confidence="medium",
                scanner="scan_reconnect_race",
            )
        )
    return findings
