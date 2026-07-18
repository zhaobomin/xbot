from __future__ import annotations

import ast
import hashlib
import io
import tokenize

from scripts.review.common import Category, Finding, make_sig_key

# Legacy product name that should not survive a rebrand.
_REMNANT = "Nanobot"


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    return parent


def _enclosing_scope(node: ast.AST, parent: dict[ast.AST, ast.AST]) -> str:
    """Nearest enclosing function/class name, or ``<module>``."""
    cur: ast.AST = node
    while cur in parent:
        cur = parent[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return cur.name
    return "<module>"


def _comment_lines(source: str) -> list[tuple[int, str]]:
    """Return ``(lineno, text)`` for every comment token containing the remnant."""
    out: list[tuple[int, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT and _REMNANT in tok.string:
            out.append((tok.start[0], tok.string))
    return out


def _add(findings: list[Finding], path: str, line: int, func_name: str, kind: str, snippet: str) -> None:
    title = "legacy product name remnant"
    detail = f"func: {func_name}\n{kind} contains remnant '{_REMNANT}': {snippet}"
    fid = hashlib.md5(f"{path}:{line}:{func_name}:{kind}:{snippet}".encode()).hexdigest()[:8]
    findings.append(
        Finding(
            id=f"naming_remnants:{fid}",
            sig_key=make_sig_key("naming_remnants", func_name, title),
            severity="P2",
            file=path,
            line=line,
            category=Category.NAMING_REMNANTS.value,
            title=title,
            detail=detail,
            suggestion=f"rename '{_REMNANT}' to the current product name",
            confidence="high",
            scanner="scan_naming_remnants",
        )
    )


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    tree = ast.parse(source, filename=path)

    parent = _build_parent_map(tree)
    findings: list[Finding] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if _REMNANT in node.name:
                _add(findings, path, node.lineno, _enclosing_scope(node, parent), type(node).__name__, node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # String constants cover docstrings and string literals.
            if _REMNANT in node.value:
                _add(findings, path, node.lineno, _enclosing_scope(node, parent), "string", node.value.strip())

    for line, text in _comment_lines(source):
        _add(findings, path, line, "<module>", "comment", text.strip())

    return findings
