from __future__ import annotations

import hashlib
import re

from scripts.review.common import Category, Finding, make_sig_key

# Matches a ``: any`` type annotation. The leading colon is required so we do
# not flag identifiers that merely contain "any" (e.g. ``many``). ``as any``
# casts are also covered by allowing an optional leading ``as``.
_ANY_RE = re.compile(r"(?:\bas\b\s*|\s*:\s*)any\b")

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
    title = "`any` type annotation escapes type checking"
    for i, line in enumerate(lines, start=1):
        if not _ANY_RE.search(line):
            continue
        func_name = _enclosing_func(lines, i - 1)
        detail = f"func: {func_name}\n`any` annotation at line {i}"
        fid = hashlib.md5(f"{path}:{i}:any_type".encode()).hexdigest()[:8]
        findings.append(
            Finding(
                id=f"any_type:{fid}",
                sig_key=make_sig_key("any_type", func_name, title),
                severity="P2",
                file=path,
                line=i,
                category=Category.ANY_TYPE.value,
                title=title,
                detail=detail,
                suggestion="replace `any` with a concrete type or `unknown` and narrow it",
                confidence="high",
                scanner="scan_any_type",
            )
        )
    return findings
