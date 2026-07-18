from __future__ import annotations

import hashlib
import re

from scripts.review.common import Category, Finding, make_sig_key

# Matches a ``.then(`` chain step. We flag a chain when it has a ``.then(``
# but no ``.catch(`` anywhere in the same statement.
_THEN_RE = re.compile(r"\.then\s*\(")
_CATCH_RE = re.compile(r"\.catch\s*\(")

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


def _statement_block(lines: list[str], start: int) -> tuple[str, int]:
    """Join lines from *start* until parens balance and the statement ends.

    Returns the joined statement text and the index of the last line it
    consumed. A statement ends when its paren depth returns to zero at or
    after the first ``.then(`` / ``.catch(`` opening, or at a statement
    terminator (``;``) at depth 0.
    """
    depth = 0
    buf: list[str] = []
    for i in range(start, len(lines)):
        buf.append(lines[i])
        depth += lines[i].count("(") - lines[i].count(")")
        if depth <= 0 and (";" in lines[i] or i > start):
            # Statement closes once parens balance; also stop on a bare ``;``.
            if depth <= 0:
                return "\n".join(buf), i
    return "\n".join(buf), len(lines) - 1


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    lines = src.splitlines()

    findings: list[Finding] = []
    title = "promise chain has .then() without .catch()"
    i = 0
    while i < len(lines):
        line = lines[i]
        if not _THEN_RE.search(line):
            i += 1
            continue
        block, end = _statement_block(lines, i)
        if _CATCH_RE.search(block):
            i = end + 1
            continue
        func_name = _enclosing_func(lines, i)
        detail = f"func: {func_name}\n.then() at line {i + 1} has no .catch()"
        fid = hashlib.md5(f"{path}:{i + 1}:unhandled_promise".encode()).hexdigest()[:8]
        findings.append(
            Finding(
                id=f"unhandled_promise:{fid}",
                sig_key=make_sig_key("unhandled_promise", func_name, title),
                severity="P1",
                file=path,
                line=i + 1,
                category=Category.UNHANDLED_PROMISE.value,
                title=title,
                detail=detail,
                suggestion="append .catch() to handle the rejected promise",
                confidence="medium",
                scanner="scan_unhandled_promise",
            )
        )
        i = end + 1
    return findings
