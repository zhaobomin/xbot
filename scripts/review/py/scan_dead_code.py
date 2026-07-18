from __future__ import annotations

import ast
import hashlib
import re

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


def _extract_all_names(tree: ast.Module) -> set[str]:
    """Return every string literal listed in a module-level ``__all__``.

    ``__all__`` re-exports make an imported name part of the public API even
    though the name is never *referenced* inside the module body. Without this
    check every re-export in every ``__init__.py`` would be a false positive.
    """
    out: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                val = node.value
                if isinstance(val, (ast.List, ast.Tuple)):
                    for elt in val.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            out.add(elt.value)
    return out


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


def _has_noqa_f401(node: ast.Import | ast.ImportFrom, source_lines: list[str]) -> bool:
    """True when *node* carries a ``# noqa: F401`` suppression comment.

    ``# noqa: F401`` marks an import as intentionally unused (conditional
    availability check or re-export), so the scanner should skip it.
    """
    line = source_lines[node.lineno - 1] if node.lineno <= len(source_lines) else ""
    return "noqa" in line and "F401" in line


def _is_type_checking_import(
    node: ast.Import | ast.ImportFrom,
    parent: dict[ast.AST, ast.AST],
) -> bool:
    """True when *node* sits inside an ``if TYPE_CHECKING:`` guard."""
    owner = parent.get(node)
    while owner is not None:
        if isinstance(owner, ast.If):
            test = owner.test
            name = test.id if isinstance(test, ast.Name) else ""
            if name == "TYPE_CHECKING":
                return True
            # ``from typing import TYPE_CHECKING`` also binds as attribute.
            if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
                return True
        owner = parent.get(owner)
    return False


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
        source_lines = source.splitlines()

    tree = ast.parse(source, filename=path)

    parent = _build_parent_map(tree)
    used_names: set[str] = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    all_names = _extract_all_names(tree)

    # PEP 563 / ``from __future__ import annotations`` stores type hints as
    # string literals (e.g. ``"Callable[[], None]"``). Collect every word-like
    # token from string constants so imports used only in string annotations
    # are not flagged as dead.
    _string_name_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            used_names.update(_string_name_re.findall(node.value))

    findings: list[Finding] = []

    unused_title = "imported name is never referenced"
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        # ``from __future__ import annotations`` and friends are interpreter
        # directives, not runtime imports that need to be referenced.
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        # Imports inside ``if TYPE_CHECKING:`` are type-checker-only; they are
        # never referenced at runtime by design.
        if _is_type_checking_import(node, parent):
            continue
        # ``from m import *`` re-exports everything; skip wildcard imports.
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            continue
        if _has_noqa_f401(node, source_lines):
            continue
        for alias in node.names:
            bound = _imported_name(alias)
            if bound in used_names:
                continue
            # Re-exported via __all__: the name is part of the public API.
            if bound in all_names:
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
