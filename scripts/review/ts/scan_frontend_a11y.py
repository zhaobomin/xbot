from __future__ import annotations

import hashlib
import re

from scripts.review.common import Category, Finding, make_sig_key

# Matches a self-closing or open ``<img ...>`` tag, including attributes that
# may span newlines. Captures the whole tag text in group 0.
_IMG_RE = re.compile(r"<img\b[^>]*?/?>", re.DOTALL)

# Matches an ``alt`` attribute (with or without a value).
_ALT_RE = re.compile(r"\balt\b")

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


def _line_of_index(src: str, idx: int) -> int:
    """1-based line number of character offset *idx* in *src*."""
    return src.count("\n", 0, idx) + 1


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    lines = src.splitlines()

    findings: list[Finding] = []
    title = "<img> tag missing alt attribute"
    for m in _IMG_RE.finditer(src):
        tag = m.group(0)
        if _ALT_RE.search(tag):
            continue
        line = _line_of_index(src, m.start())
        func_name = _enclosing_func(lines, line - 1)
        detail = f"func: {func_name}\n<img> at line {line} has no alt attribute"
        fid = hashlib.md5(f"{path}:{line}:img_alt".encode()).hexdigest()[:8]
        findings.append(
            Finding(
                id=f"frontend_a11y:{fid}",
                sig_key=make_sig_key("frontend_a11y", func_name, title),
                severity="P2",
                file=path,
                line=line,
                category=Category.FRONTEND_A11Y.value,
                title=title,
                detail=detail,
                suggestion="add a descriptive alt attribute (alt='' for decorative images)",
                confidence="medium",
                scanner="scan_frontend_a11y",
            )
        )
    return findings
