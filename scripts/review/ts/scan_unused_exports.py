from __future__ import annotations

import hashlib
import re

from scripts.review.common import IGNORED_DIRS, IGNORED_FILES, Category, Finding, make_sig_key

# Captures ``export const <name>`` / ``export function <name>``.
_EXPORT_RE = re.compile(
    r"\bexport\s+(?:const|let|var|function|async\s+function)\s+(\w+)\b"
)

# Captures named bindings from ``import { a, b } from "..."``. Default imports
# (``import x from``) and namespace imports (``import * as x from``) do not
# reference a specific export name, so they are not counted.
_IMPORT_BIND_RE = re.compile(r"\bimport\s*\{([^}]*)\}")

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


def _collect_ts_files(path: str) -> list[str]:
    """Return absolute paths to every ``*.ts``/``*.tsx`` file under *path*.

    Vendored and build directories (``node_modules``, ``.venv``, ``dist`` ...)
    are pruned so their type-declaration files do not drown real findings.
    """
    import os

    if os.path.isdir(path):
        out: list[str] = []
        for root, _dirs, names in os.walk(path):
            _dirs[:] = [d for d in _dirs if d not in IGNORED_DIRS]
            for name in names:
                if name in IGNORED_FILES:
                    continue
                if name.endswith((".ts", ".tsx")):
                    out.append(os.path.abspath(os.path.join(root, name)))
        return sorted(out)
    if os.path.isfile(path):
        return [os.path.abspath(path)]
    return []


def _parse_imports(text: str) -> set[str]:
    names: set[str] = set()
    for m in _IMPORT_BIND_RE.finditer(text):
        for part in m.group(1).split(","):
            ident = part.strip().split(" as ")[0].strip()
            if ident:
                names.add(ident)
    return names


def scan(path: str) -> list[Finding]:
    files = _collect_ts_files(path)
    if not files:
        return []

    # Pass 1: collect every imported name across the directory tree.
    imported: set[str] = set()
    file_lines: dict[str, list[str]] = {}
    for fpath in files:
        with open(fpath, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        file_lines[fpath] = lines
        imported |= _parse_imports("\n".join(lines))

    # Pass 2: flag exports whose name is never imported elsewhere in the tree.
    # ``.d.ts`` files contain ambient module declarations (``declare module``)
    # whose ``export`` keywords describe third-party APIs, not xbot's own
    # exports — skip them to avoid flagging type declarations.
    findings: list[Finding] = []
    title = "exported symbol is never imported within the module tree"
    for fpath, lines in file_lines.items():
        if fpath.endswith(".d.ts"):
            continue
        for i, line in enumerate(lines, start=1):
            m = _EXPORT_RE.search(line)
            if not m:
                continue
            name = m.group(1)
            if name in imported:
                continue
            func_name = _enclosing_func(lines, i - 1)
            detail = f"func: {func_name}\nexport `{name}` at line {i} is not imported anywhere"
            fid = hashlib.md5(f"{fpath}:{i}:{name}:unused_exports".encode()).hexdigest()[:8]
            findings.append(
                Finding(
                    id=f"unused_exports:{fid}",
                    sig_key=make_sig_key("unused_exports", func_name, title),
                    severity="P2",
                    file=fpath,
                    line=i,
                    category=Category.UNUSED_EXPORTS.value,
                    title=title,
                    detail=detail,
                    suggestion="remove the export or import it where it is needed",
                    confidence="high",
                    scanner="scan_unused_exports",
                )
            )
    return findings
