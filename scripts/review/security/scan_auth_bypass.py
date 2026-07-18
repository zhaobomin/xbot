from __future__ import annotations

import ast
import hashlib

from scripts.review.common import Category, Finding, make_sig_key

# (attribute, methods) tuples for web-framework route decorators.
_ROUTE_ATTRS: set[str] = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
# Names that mark a decorator as auth-gated even without `dependencies=`.
_AUTH_HINTS: set[str] = {
    "requires_auth",
    "login_required",
    "require_auth",
    "requires_login",
    "authenticate",
}


def _route_decorator(node: ast.AST) -> ast.Call | None:
    """Return the route-decorator call on *node* if any, else None.

    Recognises ``@app.get(...)`` / ``@router.post(...)`` style decorators whose
    attribute name is a known HTTP method. Auth-only decorators are skipped.
    """
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if dec.func.attr in _ROUTE_ATTRS:
                return dec
    return None


def _has_auth_dependency(call: ast.Call) -> bool:
    """True when the route call passes a non-empty ``dependencies=`` kwarg."""
    for kw in call.keywords:
        if kw.arg == "dependencies" and isinstance(kw.value, ast.List):
            return bool(kw.value.elts)
    return False


def _has_auth_decorator(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when an auth-related decorator (e.g. ``@requires_auth``) is present."""
    for dec in func.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id in _AUTH_HINTS:
            return True
        if isinstance(target, ast.Attribute) and target.attr in _AUTH_HINTS:
            return True
    return False


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    findings: list[Finding] = []
    title = "web route missing authentication dependency"
    for func in ast.walk(tree):
        call = _route_decorator(func)
        if call is None:
            continue
        if _has_auth_decorator(func) or _has_auth_dependency(call):
            continue
        func_name = func.name
        detail = f"func: {func_name}\nroute decorator without auth dependency"
        fid = hashlib.md5(f"{path}:{func.lineno}:{func_name}".encode()).hexdigest()[:8]
        findings.append(
            Finding(
                id=f"auth_bypass:{fid}",
                sig_key=make_sig_key("auth_bypass", func_name, title),
                severity="P0",
                file=path,
                line=func.lineno,
                category=Category.AUTH_BYPASS.value,
                title=title,
                detail=detail,
                suggestion="add `dependencies=[Depends(verify)]` or an auth decorator",
                confidence="low",
                scanner="scan_auth_bypass",
            )
        )
    return findings
