from __future__ import annotations

import ast
import hashlib

from scripts.review.common import Category, Finding, make_sig_key

# (module, attr) pairs that block the event loop when called without await.
_BLOCKING_ATTRS: set[tuple[str, str]] = {
    ("httpx", "delete"),
    ("httpx", "get"),
    ("httpx", "post"),
    ("httpx", "put"),
    ("httpx", "request"),
    ("requests", "delete"),
    ("requests", "get"),
    ("requests", "head"),
    ("requests", "patch"),
    ("requests", "post"),
    ("requests", "put"),
    ("asyncio", "sleep"),
    ("time", "sleep"),
}


def _blocking_call_name(call: ast.Call) -> str | None:
    """Return ``module.attr`` when *call* targets a known blocking function."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        key = (func.value.id, func.attr)
        if key in _BLOCKING_ATTRS:
            return f"{key[0]}.{key[1]}"
    return None


def _collect(node: ast.AST, blocking: list[tuple[ast.Call, str]], awaited: set[int]) -> None:
    """Walk *node*'s children, recording blocking calls and awaited call ids.

    Nested function/lambda definitions are not descended into, so calls that
    belong to a different scope are excluded from the enclosing async function.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(child, ast.Await) and isinstance(child.value, ast.Call):
            awaited.add(id(child.value))
        if isinstance(child, ast.Call):
            name = _blocking_call_name(child)
            if name is not None:
                blocking.append((child, name))
        _collect(child, blocking, awaited)


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    findings: list[Finding] = []
    title = "sync call not awaited in async function"
    for func in ast.walk(tree):
        if not isinstance(func, ast.AsyncFunctionDef):
            continue
        blocking: list[tuple[ast.Call, str]] = []
        awaited: set[int] = set()
        for stmt in func.body:
            _collect(stmt, blocking, awaited)
        func_name = func.name
        for call, name in blocking:
            if id(call) in awaited:
                continue
            detail = f"func: {func_name}\nasync def calls {name} without await"
            fid = hashlib.md5(f"{path}:{call.lineno}:{func_name}:{name}".encode()).hexdigest()[:8]
            findings.append(
                Finding(
                    id=f"async_block:{fid}",
                    sig_key=make_sig_key("async_block", func_name, title),
                    severity="medium",
                    file=path,
                    line=call.lineno,
                    category=Category.ASYNC_BLOCK.value,
                    title=title,
                    detail=detail,
                    suggestion="await the call or run in executor",
                    confidence="medium",
                    scanner="scan_async_blocks",
                )
            )
    return findings
