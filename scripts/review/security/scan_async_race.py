from __future__ import annotations

import ast
import hashlib

from scripts.review.common import Category, Finding, make_sig_key

# Method calls that mutate a list/dict/set in place.
_MUTATING_METHODS: set[str] = {
    "append",
    "extend",
    "insert",
    "remove",
    "pop",
    "clear",
    "popitem",
    "setdefault",
    "update",
    "add",
    "discard",
}


def _bound_names(func: ast.AsyncFunctionDef) -> set[str]:
    """Names locally bound in *func* (params + plain-name assignments).

    Non-nested scope only: assignments inside inner functions/lambdas are
    excluded so a free/global name is still classified as shared.
    """
    bound: set[str] = {a.arg for a in func.args.args}
    if func.args.vararg:
        bound.add(func.args.vararg.arg)
    if func.args.kwarg:
        bound.add(func.args.kwarg.arg)
    for node in _iter_non_nested(func):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    bound.add(t.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(
            node.target, ast.Name
        ):
            bound.add(node.target.id)
    return bound


def _iter_non_nested(node: ast.AST):
    """Yield *node* and descendants without descending into nested scopes."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield from _iter_non_nested(child)


def _mutated_name(node: ast.AST) -> str | None:
    """Return the shared ``Name`` mutated by *node*, if any.

    Covers subscript assignment (``cache[k] = v``) and in-place mutating method
    calls (``cache.append(v)``). Plain ``name = v`` rebinds a local, not a race.
    """
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                return t.value.id
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Subscript):
        if isinstance(node.target.value, ast.Name):
            return node.target.value.id
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in _MUTATING_METHODS and isinstance(node.func.value, ast.Name):
            return node.func.value.id
    return None


def _walk_ancestors(root: ast.AST):
    """Yield ``(node, ancestors)`` for every non-nested descendant of *root*.

    *ancestors* is the chain from *root* down to (excluding) the node, so a
    caller can test whether a mutation sits under an ``async with`` guard.
    """
    stack: list[tuple[ast.AST, list[ast.AST]]] = [(root, [])]
    while stack:
        cur, ancestors = stack.pop()
        for child in ast.iter_child_nodes(cur):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            child_ancestors = ancestors + [cur]
            yield child, child_ancestors
            stack.append((child, child_ancestors))


def _is_lock_guarded(ancestors: list[ast.AST]) -> bool:
    """True when some ancestor is an ``async with`` (lock or similar guard)."""
    return any(isinstance(a, ast.AsyncWith) for a in ancestors)


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    findings: list[Finding] = []
    title = "shared mutable state written in async function without a lock"
    for func in ast.walk(tree):
        if not isinstance(func, ast.AsyncFunctionDef):
            continue
        func_name = func.name
        bound = _bound_names(func)
        for node, ancestors in _walk_ancestors(func):
            name = _mutated_name(node)
            if name is None or name in bound:
                continue
            if _is_lock_guarded(ancestors):
                continue
            detail = f"shared `{name}` mutated without a lock in async function"
            fid = hashlib.md5(f"{path}:{node.lineno}:{func_name}:{name}".encode()).hexdigest()[:8]
            findings.append(
                Finding(
                    id=f"async_race:{fid}",
                    sig_key=make_sig_key("async_race", func_name, title),
                    severity="P1",
                    file=path,
                    line=node.lineno,
                    category=Category.ASYNC_RACE.value,
                    title=title,
                    detail=detail,
                    suggestion="guard the read-modify-write with `async with asyncio.Lock()`",
                    confidence="low",
                    scanner="scan_async_race",
                )
            )
    return findings
