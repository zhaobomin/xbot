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


def _imported_name(alias: ast.alias) -> str:
    """Return the name bound in the local namespace by *alias*."""
    if alias.asname:
        return alias.asname
    # ``import a.b`` binds ``a``; ``from m import n`` binds ``n``.
    return alias.name.split(".", 1)[0]


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
    used_names: set[str] = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}

    findings: list[Finding] = []

    unused_title = "imported name is never referenced"
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for alias in node.names:
            bound = _imported_name(alias)
            if bound in used_names:
                continue
            func_name = _enclosing_func(node, parent)
            detail = f"func: {func_name}\nimport '{bound}' is never referenced"
            fid = hashlib.md5(f"{path}:{node.lineno}:{bound}:unused".encode()).hexdigest()[:8]
            findings.append(
                Finding(
                    id=f"dead_code:{fid}",
                    sig_key=make_sig_key("dead_code", func_name, unused_title),
                    severity="P1",
                    file=path,
                    line=node.lineno,
                    category=Category.DEAD_CODE.value,
                    title=unused_title,
                    detail=detail,
                    suggestion="remove the unused import or reference it",
                    confidence="high",
                    scanner="scan_dead_code",
                )
            )

    task_title = "task result is discarded (GC risk)"
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
        fid = hashlib.md5(f"{path}:{node.lineno}:{func_name}:task".encode()).hexdigest()[:8]
        findings.append(
            Finding(
                id=f"dead_code:{fid}",
                sig_key=make_sig_key("dead_code", func_name, task_title),
                severity="P1",
                file=path,
                line=node.lineno,
                category=Category.DEAD_CODE.value,
                title=task_title,
                detail=detail,
                suggestion="assign the task to a variable so it is not garbage-collected",
                confidence="high",
                scanner="scan_dead_code",
            )
        )
    return findings
