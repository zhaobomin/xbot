from __future__ import annotations

import ast
import hashlib

from scripts.review.common import Category, Finding, make_sig_key

# (module, attr) sleep entrypoints.
_SLEEP_ATTRS: set[tuple[str, str]] = {
    ("time", "sleep"),
    ("asyncio", "sleep"),
}


def _is_sleep_call(call: ast.Call) -> bool:
    """True when *call* targets ``time.sleep``/``asyncio.sleep``."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return (func.value.id, func.attr) in _SLEEP_ATTRS
    return False


def _is_constant(node: ast.AST) -> bool:
    """True when *node* is a fixed constant literal (no jitter/backoff expr)."""
    if isinstance(node, ast.Constant):
        return True
    # A unary minus on a constant (e.g. -1) is still a fixed constant.
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return _is_constant(node.operand)
    return False


def _enclosing_retry_loop(func: ast.AST) -> dict[int, bool]:
    """Map each ``For`` node id (in *func*) to whether it is a retry-style loop.

    A retry-style loop is a ``for ... in range(...)`` whose body contains a
    ``try/except`` — the shape of a bounded retry attempt loop that retries on
    failure. Plain ``for`` loops without ``try/except`` (e.g. streaming,
    batching, pagination) are excluded so the scanner does not flag streaming
    intervals or pacing sleeps.
    """
    retry: dict[int, bool] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.For):
            continue
        it = node.iter
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id in {"range", "xrange"}:
            # A retry loop retries on failure: its body must contain try/except.
            has_try = any(
                isinstance(child, ast.Try) for child in ast.walk(node)
                if child is not node
            )
            if has_try:
                retry[id(node)] = True
    return retry


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    findings: list[Finding] = []
    title = "fixed delay in retry loop lacks backoff/jitter"
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        func_name = func.name
        retry_loops = _enclosing_retry_loop(func)
        if not retry_loops:
            continue
        # Track which For nodes enclose each call via a parent map.
        parent: dict[int, ast.AST] = {}
        for node in ast.walk(func):
            for child in ast.iter_child_nodes(node):
                parent[id(child)] = node
        for node in ast.walk(func):
            if not isinstance(node, ast.Call) or not _is_sleep_call(node):
                continue
            if not node.args:
                continue
            if not _is_constant(node.args[0]):
                continue
            # Walk up parents to see if this sleep sits inside a retry For loop.
            inside_retry = False
            cur = node
            while cur is not None:
                cur = parent.get(id(cur))
                if isinstance(cur, ast.For) and retry_loops.get(id(cur)):
                    inside_retry = True
                    break
            if not inside_retry:
                continue
            detail = f"func: {func_name}\nfixed sleep() in retry loop (no backoff/jitter)"
            fid = hashlib.md5(f"{path}:{node.lineno}:{func_name}".encode()).hexdigest()[:8]
            findings.append(
                Finding(
                    id=f"retry_jitter:{fid}",
                    sig_key=make_sig_key("retry_jitter", func_name, title),
                    severity="P2",
                    file=path,
                    line=node.lineno,
                    category=Category.RETRY_JITTER.value,
                    title=title,
                    detail=detail,
                    suggestion="use exponential backoff with jitter (e.g. 2**attempt + random())",
                    confidence="medium",
                    scanner="scan_retry_jitter",
                )
            )
    return findings
