from __future__ import annotations

import ast
import hashlib

from scripts.review.common import Category, Finding, make_sig_key

# asyncio calls that schedule a coroutine into a Task; discarding the result
# lets the runtime garbage-collect the task before it completes.
_TASK_CALLS: set[tuple[str, str]] = {
    ("asyncio", "ensure_future"),
    ("asyncio", "create_task"),
}


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    return parent


def _enclosing_func(node: ast.AST, parent: dict[ast.AST, ast.AST]) -> str:
    cur: ast.AST = node
    while cur in parent:
        cur = parent[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
    return "<module>"


def _is_task_call(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and (func.value.id, func.attr) in _TASK_CALLS
    )


def _is_discarded(call: ast.Call, parent: dict[ast.AST, ast.AST]) -> bool:
    """True when *call*'s result is thrown away (bare expression statement)."""
    owner = parent.get(call)
    return isinstance(owner, ast.Expr)


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    parent = _build_parent_map(tree)

    findings: list[Finding] = []
    title = "task scheduled without keeping a reference (GC risk)"
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_task_call(node):
            continue
        if not _is_discarded(node, parent):
            continue
        func_name = _enclosing_func(node, parent)
        call_name = node.func.attr  # type: ignore[union-attr]
        detail = f"func: {func_name}\n{call_name}() result not assigned (GC risk)"
        fid = hashlib.md5(f"{path}:{node.lineno}:{func_name}".encode()).hexdigest()[:8]
        findings.append(
            Finding(
                id=f"task_lifecycle:{fid}",
                sig_key=make_sig_key("task_lifecycle", func_name, title),
                severity="P1",
                file=path,
                line=node.lineno,
                category=Category.TASK_LIFECYCLE.value,
                title=title,
                detail=detail,
                suggestion="assign the task to a variable so it is not garbage-collected",
                confidence="medium",
                scanner="scan_task_lifecycle",
            )
        )
    return findings
