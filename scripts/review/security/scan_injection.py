from __future__ import annotations

import ast
import hashlib

from scripts.review.common import Category, Finding, make_sig_key

# (module, attr) pairs that spawn a subprocess and accept a shell command.
_SHELL_ATTRS: set[tuple[str, str]] = {
    ("subprocess", "run"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("subprocess", "Popen"),
    ("os", "system"),
    ("os", "popen"),
}


def _is_shell_call(call: ast.Call) -> bool:
    """True when *call* targets a known subprocess/shell function."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return (func.value.id, func.attr) in _SHELL_ATTRS
    return False


def _shell_string_arg(arg: ast.AST) -> bool:
    """True when *arg* is a string-ish shell command built from user input.

    Flags f-strings (``ast.JoinedStr``) and string concatenations that mix a
    ``BinOp`` of strings with interpolated values. A bare string literal is a
    fixed command (no injection surface); a list is the safe argv form.
    """
    if isinstance(arg, ast.Constant):
        return False
    if isinstance(arg, ast.List):
        return False
    if isinstance(arg, ast.JoinedStr):
        return bool(arg.values)
    # ``"echo " + user_input`` style concatenation.
    if isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
        return not _is_pure_constant(arg)
    return True


def _is_pure_constant(node: ast.AST) -> bool:
    """True when *node* is a constant or a sum of constants only."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_pure_constant(node.left) and _is_pure_constant(node.right)
    return False


def scan(path: str) -> list[Finding]:
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    findings: list[Finding] = []
    title = "user input interpolated into shell command string"
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        func_name = func.name
        for node in ast.walk(func):
            if not isinstance(node, ast.Call) or not _is_shell_call(node):
                continue
            if not node.args:
                continue
            if not _shell_string_arg(node.args[0]):
                continue
            detail = f"func: {func_name}\nshell command built from interpolated value"
            fid = hashlib.md5(f"{path}:{node.lineno}:{func_name}".encode()).hexdigest()[:8]
            findings.append(
                Finding(
                    id=f"injection:{fid}",
                    sig_key=make_sig_key("injection", func_name, title),
                    severity="P0",
                    file=path,
                    line=node.lineno,
                    category=Category.INJECTION.value,
                    title=title,
                    detail=detail,
                    suggestion="pass argv as a list and drop shell=True",
                    confidence="low",
                    scanner="scan_injection",
                )
            )
    return findings
