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


def _interpolated_param_names(arg: ast.AST) -> list[str]:
    """Return bare ``Name`` identifiers interpolated into an f-string URL arg."""
    names: list[str] = []
    if isinstance(arg, ast.JoinedStr):
        for part in arg.values:
            if isinstance(part, ast.FormattedValue):
                expr = part.value
                if isinstance(expr, ast.Name):
                    names.append(expr.id)
    return names


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    findings: list[Finding] = []
    title = "user-controlled value interpolated into request URL"
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        func_name = func.name
        param_names = {a.arg for a in func.args.args}
        if not param_names:
            continue
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            if not _is_net_call(node):
                continue
            if not node.args:
                continue
            url_arg = node.args[0]
            matched = [n for n in _interpolated_param_names(url_arg) if n in param_names]
            if not matched:
                continue
            detail = f"func: {func_name}\nparam {matched[0]} interpolated into request URL"
            fid = hashlib.md5(
                f"{path}:{node.lineno}:{func_name}:{matched[0]}".encode()
            ).hexdigest()[:8]
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
                    suggestion="validate/allow-list the value before interpolating into a URL",
                    confidence="low",
                    scanner="scan_ssrf",
                )
            )
    return findings
