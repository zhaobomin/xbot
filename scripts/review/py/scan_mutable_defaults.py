from __future__ import annotations

import ast
import hashlib

from scripts.review.common import Category, Finding, make_sig_key

# Constructors that build a fresh mutable each call when invoked with no args.
_MUTABLE_CTORS: set[str] = {"set", "dict", "list"}


def _mutable_default_kind(node: ast.AST) -> str | None:
    """Return a short label when *node* is a mutable default literal."""
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return type(node).__name__.lower()
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in _MUTABLE_CTORS and not node.args and not node.keywords:
            return f"{func.id}()"
    return None


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    findings: list[Finding] = []
    title = "mutable object as default argument"
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        func_name = func.name
        defaults = list(func.args.defaults) + [
            d for d in func.args.kw_defaults if d is not None
        ]
        for default in defaults:
            kind = _mutable_default_kind(default)
            if kind is None:
                continue
            detail = f"func: {func_name}\nmutable default {kind} shared across calls"
            fid = hashlib.md5(
                f"{path}:{default.lineno}:{func_name}:{kind}".encode()
            ).hexdigest()[:8]
            findings.append(
                Finding(
                    id=f"mutable_defaults:{fid}",
                    sig_key=make_sig_key("mutable_defaults", func_name, title),
                    severity="P1",
                    file=path,
                    line=default.lineno,
                    category=Category.MUTABLE_DEFAULTS.value,
                    title=title,
                    detail=detail,
                    suggestion="use None and create the object inside the body",
                    confidence="high",
                    scanner="scan_mutable_defaults",
                )
            )
    return findings
