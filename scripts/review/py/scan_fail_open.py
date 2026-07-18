from __future__ import annotations

import ast
import hashlib

from scripts.review.common import Category, Finding, make_sig_key


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


def _is_not_in_test(test: ast.AST) -> bool:
    """True when *test* is a membership check shaped ``name not in known``."""
    if not isinstance(test, ast.Compare):
        return False
    return any(isinstance(op, ast.NotIn) for op in test.ops)


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
            if not _is_not_in_test(node.test):
                continue
            # The "not in known" branch must reject; a branch that proceeds
            # without raising lets an unknown name through (fail-open).
            if _branch_rejects(node.body):
                continue
            detail = f"func: {func_name}\nif <name> not in <known>: branch admits without raise"
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
                    suggestion="raise/reject unknown names in the not-in-known branch",
                    confidence="high",
                    scanner="scan_fail_open",
                )
            )
    return findings
