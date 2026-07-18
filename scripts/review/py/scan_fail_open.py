"""Detect fail-open permission checks: ``if name not in known: admit``.

A fail-open anti-pattern admits an unknown name into a permission boundary
instead of rejecting it. The scanner matches the AST shape

    if <expr> not in <boundary>:
        <boundary_or_allowed>.append(<expr>)   # admit

and requires the RHS of ``not in`` to read like a permission/admission set
(``allowed``, ``admitted``, ``whitelist``, ``known``, ``available`` ...). This
keeps the scanner off ordinary deduplication patterns such as

    if item not in seen: seen.append(item)

where ``seen`` is just the output list being built.
"""

from __future__ import annotations

import ast
import hashlib
import re

from scripts.review.common import Category, Finding, make_sig_key

# Identifiers whose name reads like a permission/admission boundary. Matching
# is case-insensitive and only looks at the trailing identifier, so prefixes
# such as ``_`` or ``self.`` or ``this.`` do not matter.
_BOUNDARY_NAME_RE = re.compile(
    r"(?:admit|admitt?(?:ed|tance)|allow(?:ed|list)?|permit(?:ted)?|"
    r"permission|whitelist|known|authoriz(?:ed)?|approv(?:ed)?|"
    r"grant(?:ed)?|available|enabled|builtin|register(?:ed)?)",
    re.IGNORECASE,
)


def _walk_non_nested(node: ast.AST):
    """Yield *node* and descendants without descending into nested scopes."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield from _walk_non_nested(child)


def _branch_rejects(body: list[ast.stmt]) -> bool:
    """True when *body* rejects (raises) rather than admitting the caller."""
    for stmt in body:
        for n in _walk_non_nested(stmt):
            if isinstance(n, ast.Raise):
                return True
    return False


def _admit_target(body: list[ast.stmt]) -> str | None:
    """Return the collection name an unknown value is admitted into, if any.

    Only ``.append``/``.add``/``.insert`` calls count as the admit action; a
    bare ``continue`` or ``return`` is not admission, it is just early exit.
    """
    for stmt in body:
        for n in _walk_non_nested(stmt):
            if not isinstance(n, ast.Call):
                continue
            fn = n.func
            if isinstance(fn, ast.Attribute) and fn.attr in {"append", "add", "insert"}:
                if isinstance(fn.value, ast.Name):
                    return fn.value.id
                if isinstance(fn.value, ast.Attribute):
                    return fn.value.attr
    return None


def _not_in_test(node_test: ast.AST) -> tuple[str, str] | None:
    """Return ``(lhs_expr, rhs_name)`` when *node_test* is ``x not in y``.

    Only membership tests whose RHS reads like a permission boundary are kept;
    ordinary dedup collections (``seen``, ``result``, ``normalized`` ...) are
    filtered out here so the scanner stops firing on them.
    """
    if not isinstance(node_test, ast.Compare):
        return None
    if not any(isinstance(op, ast.NotIn) for op in node_test.ops):
        return None
    lhs = ast.unparse(node_test.left)
    rhs_node = node_test.comparators[-1]
    rhs_name = rhs_node.id if isinstance(rhs_node, ast.Name) else (
        rhs_node.attr if isinstance(rhs_node, ast.Attribute) else ast.unparse(rhs_node)
    )
    if not _BOUNDARY_NAME_RE.search(rhs_name):
        return None
    return lhs, rhs_name


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    findings: list[Finding] = []
    title = "permission check admits unknown name (fail-open)"
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        func_name = func.name
        for node in _walk_non_nested(func):
            if not isinstance(node, ast.If):
                continue
            pair = _not_in_test(node.test)
            if pair is None:
                continue
            lhs, rhs = pair
            # The "not in known" branch must reject; a branch that proceeds
            # without raising lets an unknown name through (fail-open).
            if _branch_rejects(node.body):
                continue
            admit_target = _admit_target(node.body)
            if admit_target is None:
                continue
            detail = (
                f"func: {func_name}\n"
                f"if {lhs} not in {rhs}: branch admits via "
                f"{admit_target}.append/.add"
            )
            fid = hashlib.md5(f"{path}:{node.lineno}:{func_name}".encode()).hexdigest()[:8]
            findings.append(
                Finding(
                    id=f"fail_open:{fid}",
                    sig_key=make_sig_key("fail_open", func_name, title),
                    severity="P0",
                    file=path,
                    line=node.lineno,
                    category=Category.FAIL_OPEN.value,
                    title=title,
                    detail=detail,
                    suggestion=f"raise/reject unknown {lhs} instead of admitting into {admit_target}",
                    confidence="high",
                    scanner="scan_fail_open",
                )
            )
    return findings
