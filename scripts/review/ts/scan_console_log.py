from __future__ import annotations

import hashlib
import re

from scripts.review.common import Category, Finding, make_sig_key

# Matches ``console.log(...)`` calls. Deliberately does not match
# ``logger.info`` / ``logger.debug`` so the clean pattern is ignored.
_CONSOLE_RE = re.compile(r"\bconsole\s*\.\s*log\s*\(")

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
    title = "console.log() used in source"
    for i, line in enumerate(lines, start=1):
        if not _CONSOLE_RE.search(line):
            continue
        func_name = _enclosing_func(lines, i - 1)
        detail = f"func: {func_name}\nconsole.log() at line {i}"
        fid = hashlib.md5(f"{path}:{i}:console_log".encode()).hexdigest()[:8]
        findings.append(
            Finding(
                id=f"console_log:{fid}",
                sig_key=make_sig_key("console_log", func_name, title),
                severity="P2",
                file=path,
                line=i,
                category=Category.CONSOLE_LOG.value,
                title=title,
                detail=detail,
                suggestion="use the project logger (e.g. logger.info) instead of console.log",
                confidence="high",
                scanner="scan_console_log",
            )
        )
    return findings
