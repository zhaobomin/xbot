from __future__ import annotations

import ast
import hashlib

from scripts.review.common import Category, Finding, make_sig_key


def _lock_name(item: ast.withitem) -> str | None:
    """Return the acquired lock name for *item*, when it is a bare name."""
    ctx = item.context_expr
    # `async with lock_a:` or `async with asyncio.Lock():`
    if isinstance(ctx, ast.Name):
        return ctx.id
    if isinstance(ctx, ast.Call):
        func = ctx.func
        if isinstance(func, ast.Attribute) and func.attr == "Lock":
            return "asyncio.Lock"
        if isinstance(func, ast.Name) and func.id == "Lock":
            return "Lock"
    return None


def _acquisition_order(func: ast.AST) -> list[str]:
    """Lock names acquired by *func* in source order (non-nested scope)."""
    order: list[str] = []
    for node in _iter_non_nested(func):
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                name = _lock_name(item)
                if name:
                    order.append(name)
    return order


def _with_orders(func: ast.AST) -> list[tuple[ast.AsyncWith, list[str]]]:
    """Per-``async with`` lock sequences inside *func* (non-nested scope)."""
    out: list[tuple[ast.AsyncWith, list[str]]] = []
    for node in _iter_non_nested(func):
        if isinstance(node, ast.AsyncWith):
            names = [n for n in (_lock_name(i) for i in node.items) if n]
            if len(names) >= 2:
                out.append((node, names))
    return out


def _iter_non_nested(node: ast.AST):
    """Yield *node* and descendants without descending into nested scopes."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield from _iter_non_nested(child)


def _order_edges(order: list[str]) -> set[tuple[str, str]]:
    """``(a, b)`` meaning *a* is acquired before *b*; skips repeats within a func."""
    edges: set[tuple[str, str]] = set()
    seen: set[str] = set()
    for lock in order:
        for prev in seen:
            if prev != lock:
                edges.add((prev, lock))
        seen.add(lock)
    return edges


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    # Per-``async with`` acquisition units, in source order.
    units: list[tuple[str, int, list[str]]] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for with_node, names in _with_orders(func):
            units.append((func.name, with_node.lineno, names))

    findings: list[Finding] = []
    title = "inconsistent lock acquisition order across functions"
    # Edge -> set of functions that first established it.
    established: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for func_name, lineno, order in units:
        local_edges = _order_edges(order)
        for edge in local_edges:
            reverse = (edge[1], edge[0])
            if reverse in established:
                # This function reverses an order already established elsewhere.
                detail = (
                    f"{func_name} acquires {edge[0]} before {edge[1]}, but "
                    f"{established[reverse][0][0]} acquires the reverse order"
                )
                fid = hashlib.md5(
                    f"{path}:{lineno}:{func_name}:{edge[0]}:{edge[1]}".encode()
                ).hexdigest()[:8]
                findings.append(
                    Finding(
                        id=f"deadlock:{fid}",
                        sig_key=make_sig_key("deadlock", func_name, title),
                        severity="P1",
                        file=path,
                        line=lineno,
                        category=Category.DEADLOCK.value,
                        title=title,
                        detail=detail,
                        suggestion="acquire locks in a single fixed order everywhere",
                        confidence="low",
                        scanner="scan_deadlock",
                    )
                )
                break
        for edge in local_edges:
            established.setdefault(edge, []).append((func_name, lineno))
    return findings
