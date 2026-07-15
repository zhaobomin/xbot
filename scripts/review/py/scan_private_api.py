from __future__ import annotations

import ast
import hashlib

from scripts.review.common import Category, Finding, make_sig_key

# Attributes that are CPython/asyncio private implementation details. Touching
# them couples code to a specific interpreter and breaks across versions.
_PRIVATE_ATTRS: set[str] = {
    "_waiters",
    "_value",
    "_state",
    "_cond",
    "_lock",
    "_waiter_count",
    "_source_traceback",
}


def _walk_non_nested(node: ast.AST):
    """Yield *node* and descendants without descending into nested scopes.

    Nested function/lambda bodies belong to a different function, so their
    nodes are excluded to avoid attributing them to the enclosing function.
    """
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield from _walk_non_nested(child)


def _getattr_private_attr(call: ast.Call) -> str | None:
    """Return the private attr name when *call* is ``getattr(obj, "<private>")``."""
    func = call.func
    if isinstance(func, ast.Name) and func.id == "getattr":
        if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
            attr = call.args[1].value
            if isinstance(attr, str) and attr in _PRIVATE_ATTRS:
                return attr
    return None


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    findings: list[Finding] = []
    title = "access to stdlib-private attribute"
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        func_name = func.name
        for node in _walk_non_nested(func):
            if isinstance(node, ast.Attribute) and node.attr in _PRIVATE_ATTRS:
                detail = f"func: {func_name}\naccesses private attribute .{node.attr}"
                fid = hashlib.md5(
                    f"{path}:{node.lineno}:{func_name}:{node.attr}".encode()
                ).hexdigest()[:8]
                findings.append(
                    Finding(
                        id=f"private_api:{fid}",
                        sig_key=make_sig_key("private_api", func_name, title),
                        severity="P1",
                        file=path,
                        line=node.lineno,
                        category=Category.PRIVATE_API.value,
                        title=title,
                        detail=detail,
                        suggestion="use the public API instead of CPython/asyncio internals",
                        confidence="high",
                        scanner="scan_private_api",
                    )
                )
            elif isinstance(node, ast.Call):
                attr = _getattr_private_attr(node)
                if attr is not None:
                    detail = (
                        f"func: {func_name}\n"
                        f"accesses private attribute .{attr} via getattr"
                    )
                    fid = hashlib.md5(
                        f"{path}:{node.lineno}:{func_name}:{attr}".encode()
                    ).hexdigest()[:8]
                    findings.append(
                        Finding(
                            id=f"private_api:{fid}",
                            sig_key=make_sig_key("private_api", func_name, title),
                            severity="P1",
                            file=path,
                            line=node.lineno,
                            category=Category.PRIVATE_API.value,
                            title=title,
                            detail=detail,
                            suggestion="use the public API instead of getattr on internals",
                            confidence="high",
                            scanner="scan_private_api",
                        ),
                    )
    return findings
