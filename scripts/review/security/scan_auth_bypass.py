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

# Function-name substrings that identify imperative auth checks performed inside
# the route body (e.g. ``_get_user_from_auth_header(authorization)``). When the
# route body calls one of these, the route is guarded even though the decorator
# lacks ``dependencies=[Depends(...)]``.
_AUTH_CALL_HINTS: tuple[str, ...] = (
    "auth_header",
    "verify_token",
    "verify_auth",
    "check_auth",
    "require_auth",
    "authenticate",
    "current_user",
    "get_user_from_auth",
    "authorize",
)

# Route function names that are *expected* to be unauthenticated: login,
# static asset serving, liveness/health probes, and desktop ping.
_NO_AUTH_FUNC_NAMES: frozenset[str] = frozenset({
    "login",
    "logout",
    "register",
    "signup",
    "index",
    "_serve_static",
    "serve_static",
    "static",
    "ping",
    "desktop_ping",
    "health",
    "healthz",
    "health_check",
    "ready",
    "readiness",
    "liveness",
    "spa_fallback",
    "detailed_status",
    "status",
    "fingerprint",
    "metrics",
})


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


def _has_auth_param(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when *func* declares an ``authorization`` parameter.

    FastAPI routes that accept ``authorization: str | None = Header(...)``
    almost always pass it to an auth-check function — even if the scanner
    cannot trace the indirect call, the presence of the parameter is strong
    evidence the route is guarded.
    """
    for arg in func.args.args:
        if arg.arg == "authorization":
            return True
    return False


def _has_imperative_auth(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when the route body calls an imperative auth function.

    Many codebases guard routes by calling ``_get_user_from_auth_header(...)``
    as the first statement inside the handler body rather than via FastAPI
    ``dependencies=[Depends(verify)]``. Recognise that pattern so we do not
    flag a route that is actually guarded.
    """
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = ""
        if isinstance(target, ast.Name):
            name = target.id
        elif isinstance(target, ast.Attribute):
            name = target.attr
        if name and any(hint in name.lower() for hint in _AUTH_CALL_HINTS):
            return True
    return False


def _is_no_auth_route(func_name: str) -> bool:
    """True for routes that are *expected* to be unauthenticated."""
    lower = func_name.lower()
    if func_name in _NO_AUTH_FUNC_NAMES:
        return True
    return any(hint in lower for hint in ("login", "logout", "ping", "health", "static"))


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
        if _has_imperative_auth(func):
            continue
        if _has_auth_param(func):
            continue
        func_name = func.name
        if _is_no_auth_route(func_name):
            continue
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
