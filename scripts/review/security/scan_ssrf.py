from __future__ import annotations

import ast
import hashlib

from scripts.review.common import Category, Finding, make_sig_key

# (module, attr) HTTP-style request entrypoints whose first positional arg is a URL.
_NET_ATTRS: set[tuple[str, str]] = {
    ("httpx", "get"),
    ("httpx", "post"),
    ("httpx", "put"),
    ("httpx", "patch"),
    ("httpx", "delete"),
    ("httpx", "head"),
    ("httpx", "request"),
    ("requests", "get"),
    ("requests", "post"),
    ("requests", "put"),
    ("requests", "patch"),
    ("requests", "delete"),
    ("requests", "head"),
    ("requests", "request"),
    ("aiohttp", "get"),
    ("aiohttp", "post"),
    ("aiohttp", "put"),
    ("aiohttp", "patch"),
    ("aiohttp", "delete"),
    ("aiohttp", "head"),
    ("aiohttp", "request"),
}


def _is_net_call(call: ast.Call) -> bool:
    """True when *call* targets a known ``module.attr`` HTTP request function."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return (func.value.id, func.attr) in _NET_ATTRS
    return False


def _url_param_name(arg: ast.AST) -> str | None:
    """Return the bare ``Name`` used directly as the request URL, if any.

    The shallow SSRF detector flags a user-controlled variable passed verbatim
    as the URL (``httpx.get(user_url)``). String literals and f-strings that
    interpolate other values are left to the deeper py-track scanner.
    """
    if isinstance(arg, ast.Name):
        return arg.id
    return None


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    findings: list[Finding] = []
    title = "user-controlled value passed as request URL"
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        func_name = func.name
        param_names = {a.arg for a in func.args.args}
        for node in ast.walk(func):
            if not isinstance(node, ast.Call) or not _is_net_call(node):
                continue
            if not node.args:
                continue
            name = _url_param_name(node.args[0])
            if name is None:
                continue
            # Flag when the URL name matches a function param (user-controlled);
            # bare variables of unknown origin are out of scope for the shallow pass.
            if name not in param_names:
                continue
            detail = f"func: {func_name}\nparam {name} passed directly as request URL"
            fid = hashlib.md5(f"{path}:{node.lineno}:{func_name}:{name}".encode()).hexdigest()[:8]
            findings.append(
                Finding(
                    id=f"ssrf:{fid}",
                    sig_key=make_sig_key("ssrf", func_name, title),
                    severity="P0",
                    file=path,
                    line=node.lineno,
                    category=Category.SSRF.value,
                    title=title,
                    detail=detail,
                    suggestion="validate/allow-list the URL before making the request",
                    confidence="low",
                    scanner="scan_ssrf",
                )
            )
    return findings
